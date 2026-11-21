"""Run against the local development RAG API; no paid judge or synthetic accuracy score."""
import argparse
import asyncio
import json
import getpass
from urllib.parse import urlsplit
import os
from pathlib import Path
from statistics import mean
from time import perf_counter
import httpx


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round((len(ordered)-1)*fraction)]


def score(item, retrieved, done):
    sources = retrieved.get('sources',[])
    expected_urls = set(item.get('gold_source_urls') or [])
    urls = {source['url'] for source in sources}
    expected_status = item.get('expected_status')
    gold_phrases = item.get('gold_answer_phrases') or []
    text = ' '.join(q['text'] for q in done.get('quotes',[])).casefold()
    source_by_parent = {s['parent_id']:s['text'] for s in sources}
    quotes = done.get('quotes',[])
    norm = lambda value: ' '.join(value.split())
    supported = [norm(q['text']) in norm(source_by_parent[q['parent_id']])
                 for q in quotes if q['parent_id'] in source_by_parent]
    return {
        'source_recall_at_k':len(expected_urls & urls)/len(expected_urls) if expected_urls else None,
        'source_hit_at_k':bool(expected_urls & urls) if expected_urls else None,
        'answerability_correct':done.get('status') == expected_status if expected_status else None,
        'gold_phrase_recall':sum(p.casefold() in text for p in gold_phrases)/len(gold_phrases) if gold_phrases else None,
        'quote_verbatim_rate':mean(supported) if supported else None,
        'quote_checks':len(supported),
        'unmatched_quote_checks':len(quotes)-len(supported),
        'human_relevance_0_to_2':None,
        'human_completeness_0_to_2':None,
        'human_notes':None,
    }


async def evaluate_case(client, item):
    body = {'question':item['question'],'filters':item.get('filters',{})}
    started = perf_counter()
    response = await client.post('/v1/retrieve',json=body)
    response.raise_for_status()
    retrieved = response.json()
    final, error, event_name = None,None,None
    async with client.stream('POST','/v1/chat/stream',json=body) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith('event: '):
                event_name = line[7:]
            elif line.startswith('data: '):
                value = json.loads(line[6:])
                if event_name == 'done': final = value
                if event_name == 'error': error = value
    if error or final is None:
        raise RuntimeError((error or {}).get('code','stream_ended_without_done'))
    return {'id':item['id'],'question':item['question'],'status':final['status'],
            'answer':final['answer'],'sources':final['sources'],'retrieved_sources':retrieved['sources'],
            'index_signature':retrieved['index_signature'],'metrics':final['metrics'],
            'evaluation_wall_ms':round((perf_counter()-started)*1000,2),
            'scores':score(item,retrieved,final)}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://localhost:8000')
    parser.add_argument('--questions',default='evaluation/questions.json')
    parser.add_argument('--output',default='evaluation/results.json')
    parser.add_argument('--limit',type=int,default=30)
    parser.add_argument('--login',action='store_true',help='Prompt for verified institute credentials; requires AUTH_BRIDGE_SECRET in the operator environment')
    args = parser.parse_args()
    token = os.environ.get('AIHUB_SESSION_TOKEN')
    if not token and not args.login:
        parser.error('Use --login or export AIHUB_SESSION_TOKEN from a verified institute login.')
    parsed_url=urlsplit(args.url)
    if parsed_url.scheme!='https' and not (parsed_url.scheme=='http' and parsed_url.hostname in {'localhost','127.0.0.1','::1'}):
        parser.error('Remote authentication requires HTTPS; HTTP is allowed only on loopback.')
    if args.login and not os.environ.get('AUTH_BRIDGE_SECRET'):
        parser.error('--login requires AUTH_BRIDGE_SECRET in the deployment operator environment')
    questions = json.loads(Path(args.questions).read_text())[:args.limit]
    if not questions:
        parser.error('Question set is empty')
    results = []
    async with httpx.AsyncClient(base_url=args.url,timeout=180,trust_env=False) as client:
        if args.login:
            identifier=input('Verified institute username or email: ').strip()
            password=getpass.getpass('Password: ')
            login=await client.post('/v1/auth/password',headers={'X-AIHUB-Bridge':os.environ['AUTH_BRIDGE_SECRET']},
                                    json={'identifier':identifier,'password':password})
            password=None
            if not login.is_success:
                raise SystemExit('Institute login failed. No evaluation was run.')
            token=login.json()['session_token']
        client.headers['Authorization']='Bearer '+token
        try:
            for item in questions:
                try:
                    result = await evaluate_case(client,item)
                except Exception as exc:
                    result = {'id':item['id'],'question':item['question'],'status':'error','error':type(exc).__name__}
                results.append(result)
                print(item['id'],result['status'])
        finally:
            if args.login:
                await client.post('/v1/auth/logout')
    completed = [r for r in results if r['status'] != 'error']
    latency = {}
    for stage in ('embedding_ms','retrieval_ms','model_first_token_ms','first_visible_ms','total_ms'):
        values = [r['metrics'][stage] for r in completed if r['metrics'].get(stage) is not None]
        latency[stage] = {'samples':len(values),'p50':percentile(values,.5),'p95':percentile(values,.95)}
    labeled_scores = {}
    for metric in ('source_recall_at_k','source_hit_at_k','answerability_correct','gold_phrase_recall','quote_verbatim_rate'):
        values = [r['scores'][metric] for r in completed if r['scores'][metric] is not None]
        labeled_scores[metric] = {'samples':len(values),'mean':mean(values) if values else None}
    output = {'summary':{'questions':len(results),'completed':len(completed),
        'errors':len(results)-len(completed),'unanswered':sum(r['status']=='unanswered' for r in completed),
        'latency_ms':latency,'scores':labeled_scores},'results':results,
        'limitations':['Gold labels require human verification against currently crawled official pages.',
        'Verbatim quote matching measures provenance, not relevance, completeness, or correct interpretation.',
        'Two requests per question: retrieval, then chat; wall latency includes both. Stage metrics come from chat.',
        'A corpus change between retrieval and chat can produce unmatched quote checks.',
        'Conversations and unanswered records are persisted under the authenticated account. No email is sent.']}
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(output,indent=2,ensure_ascii=False))
    print(json.dumps(output['summary'],indent=2))
    if len(completed) != len(results):
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
