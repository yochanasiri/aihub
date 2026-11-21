import json
import re
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import httpx
import pytest
from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.models import Base, Document, Message, UnansweredQuery, User, now
from backend.app.auth.service import AuthService
from backend.app.rag.chunking import build_windows
from backend.app.rag.generation import (REFUSAL, exact_pdf_fee_table, select_source_excerpt,
                                        validate_line, verified_quotes)
from backend.app.rag.index import sync_index
from backend.app.rag.retrieval import Retriever
from backend.app.rag.service import RAGService
from backend.app.rag.vector import VectorStore, RetrievalFilters
from crawler.extract import Extracted
from crawler.policy import CrawlConfig
from crawler.store import save_document

pytestmark = pytest.mark.filterwarnings('ignore:Payload indexes have no effect in the local Qdrant')


def test_flattened_education_verification_fee_table():
    text = ("Education Verification Charges Within India Outside India "
            "Verification Charges Verification Charges Verification Charges Verification Charges "
            "through email (Rs.) through courier (Rs.) through email (US$) through courier (US$) "
            "1000 1200 40 60")
    source = SimpleNamespace(source_id='S1', source={'doc_type':'pdf'}, text=text)
    cases = [
        ('What are the education verification charges within India for verification by email?', 'Rs. 1000'),
        ('What are the education verification charges within India by courier?', 'Rs. 1200'),
        ('What are the education verification charges outside India by email?', 'US$ 40'),
        ('What are the education verification charges outside India by courier?', 'US$ 60'),
    ]
    for question, expected in cases:
        result = exact_pdf_fee_table(question, [source])
        assert result is not None
        assert expected in result.answer
        assert result.quote in text
    assert exact_pdf_fee_table('What documents should HR send?', [source]) is None


def test_pdf_sentence_selection_reads_past_a_truncated_chunk():
    full_page = ('2) The HR Companies/Agencies/Employers are requested to send a copy of documents\n'
                 'submitted by students for verification by IIT Indore.')
    source = SimpleNamespace(
        source_id='S1', source={'doc_type':'pdf'},
        text='2) The HR Companies/Agencies/Employers are requested to send a copy of documents',
        node=SimpleNamespace(node=SimpleNamespace(metadata={'_source_page_text':full_page})))
    result = select_source_excerpt(
        'What documents should HR companies send for education verification?', [source])
    assert result is not None
    assert 'documents submitted by students' in result.quote


def test_pdf_eligibility_question_matches_applicability_wording():
    full_page = ('The scheme has been initiated for research and innovation under PRIUS. '
                 'The scheme is applicable for the UG students enrolled at IIT Indore.')
    source = SimpleNamespace(
        source_id='S1', source={'doc_type':'pdf'}, text=full_page,
        node=SimpleNamespace(node=SimpleNamespace(metadata={'_source_page_text':full_page})))
    result = select_source_excerpt('Who is eligible for the PRIUS scheme?', [source])
    assert result is not None
    assert 'applicable for the UG students enrolled' in result.quote


def test_pdf_duration_question_matches_period_wording():
    full_page = ('For the implementation of this scheme, the student must be allotted a faculty member as a supervisor. '
                 'Such undergraduate projects should typically run for a period of at least six months.')
    source = SimpleNamespace(
        source_id='S1', source={'doc_type':'pdf'}, text=full_page,
        node=SimpleNamespace(node=SimpleNamespace(metadata={'_source_page_text':full_page})))
    result = select_source_excerpt('How long should a PRIUS undergraduate project run?', [source])
    assert result is not None
    assert 'at least six months' in result.quote


class TestEmbedder:
    __test__ = False
    dim = 4
    def tokens(self, value):
        return re.findall(r'\w+|[^\w\s]', value)
    def windows(self, value):
        words = value.split()
        return [' '.join(words[i:i+400]) for i in range(0,len(words),368)]
    def vector(self, value):
        text = value.lower()
        return [float('admission' in text),float('hostel' in text),float('sports' in text),0.01]
    async def passages(self, values):
        return [self.vector(v) for v in values]
    async def validate_query(self, value): pass
    async def query(self, value):
        return self.vector(value)


class TestLLM:
    __test__ = False
    invalid = False
    error = False
    calls = 0
    async def tokens(self, question, evidence):
        self.calls += 1
        if self.error:
            raise RuntimeError('fixture model offline')
        line = json.dumps({'source_id':evidence[0].source_id,'quote':
              'Invented statement that does not appear anywhere.' if self.invalid else evidence[0].text[-100:]})+'\n'
        for i in range(0,len(line),7):
            yield line[i:i+7]
    async def close(self): pass


@pytest.fixture
async def rig(tmp_path):
    policy_path = tmp_path/'seeds.json'
    policy_path.write_text(json.dumps({'allowed_domains':['iiti.ac.in','academic.iiti.ac.in'],
        'seeds':[{'url':'https://iiti.ac.in/'}]}))
    config = Settings(_env_file=None,crawler_config=str(policy_path),rag_api_enabled=True,
                      auth_enabled=True,auth_bridge_secret='test-bridge-'+'x'*32,retrieval_min_score=0.6)
    policy = CrawlConfig.model_validate_json(policy_path.read_text())
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine,expire_on_commit=False)
    embedder, llm = TestEmbedder(), TestLLM()
    store = VectorStore(config,embedder.dim,AsyncQdrantClient(':memory:'))
    service = RAGService(config,sessions,embedder,store,llm)
    auth_service=AuthService(config,sessions,None,None)
    async with sessions() as session:
        user=User(email="fixture@iiti.ac.in",name="Fixture User",role="Student",email_verified=True,is_demo=False)
        session.add(user)
        await session.flush()
        grant=await auth_service.grant(session,user,"password")
        await session.commit()
    yield SimpleNamespace(auth=auth_service,session_token=grant["session_token"],config=config,policy=policy,sessions=sessions,embedder=embedder,
                          store=store,service=service,llm=llm,policy_path=policy_path)
    await store.close()
    await engine.dispose()


async def add(rig, url='https://iiti.ac.in/admissions', text='Admission details in this local testing fixture. Contact the academic office for admission instructions.',
              department='Academics', doc_type='html', metadata=None):
    async with rig.sessions() as s:
        doc,_ = await save_document(s,url,Extracted('Official fixture',text,doc_type,metadata=metadata or {}),department,{})
        await s.commit()
        return doc


async def sync(rig, force=False):
    return await sync_index(rig.config,rig.sessions,rig.embedder,rig.store,rig.policy,force)


def test_strong_bridge_secret():
    with pytest.raises(ValueError): Settings(_env_file=None,auth_enabled=True,auth_bridge_secret='weak')


def test_date_filters():
    with pytest.raises(ValueError): RetrievalFilters(date_from='2026-01-01T00:00:00')
    with pytest.raises(ValueError): RetrievalFilters(date_from='2026-02-01T00:00:00Z',date_to='2026-01-01T00:00:00Z')


async def test_chunk_boundaries_and_stable_ids(rig):
    doc = await add(rig,text='# Admissions\n\n'+('Admission regulation sentence. '*450)+'\n\n# Hostels\n\nHostel information.')
    a = build_windows(doc,rig.embedder,rig.config)
    b = build_windows(doc,rig.embedder,rig.config)
    assert [w.id for w in a] == [w.id for w in b]
    assert len({w.node.id_ for w in a}) > 2
    assert all(len(rig.embedder.tokens(w.node.text)) <= 700 for w in a)
    assert {w.node.metadata['heading'] for w in a} == {'Admissions','Hostels'}
    changed = rig.config.model_copy(update={'chunk_tokens':600})
    assert changed.index_signature != rig.config.index_signature
    assert a[0].id != build_windows(doc,rig.embedder,changed)[0].id


async def test_pdf_page_boundaries(rig):
    doc = await add(rig,doc_type='pdf',metadata={'pages':[{'page':1,'text':'Admission first page text.'},
                                                                       {'page':2,'text':'Hostel second page text.'}]})
    windows = build_windows(doc,rig.embedder,rig.config)
    assert {w.node.metadata['page'] for w in windows} == {1,2}


async def test_pdf_answer_matches_event_and_refuses_missing_fee_amount(rig):
    page = ("Jan. 6, 2027 (Wednesday) Commencement of classes All students\n"
            "May 5–13, 2027 End Semester Examination (ESE) All students\n"
            "Aug. 10, 2026 Notification for remission of tuition fee for 2026 B.Tech., B.S., and B.Des. batch")
    await add(rig,doc_type='pdf',text=page,metadata={'pages':[{'page':1,'text':page}]})
    await sync(rig)
    question = 'When do classes begin in the 2027 spring semester?'
    events = [item async for item in rig.service.events('fixture',question,RetrievalFilters(),None,None,persist=False)]
    done = next(payload for name,payload in events if name == 'done')
    assert done['status'] == 'answered'
    answer = re.sub(r'\\([\\`*_{}\[\]()#+.!|>~-])',r'\1',done['answer'])
    assert 'Spring 2027 classes commence on Jan. 6, 2027.' in answer
    assert 'Commencement of classes' in done['quotes'][0]['text']

    question = 'What is the current tuition fee for B.Tech. students?'
    events = [item async for item in rig.service.events('fixture',question,RetrievalFilters(),None,None,persist=False)]
    done = next(payload for name,payload in events if name == 'done')
    assert done['status'] == 'unanswered'
    assert done['answer'] == REFUSAL


async def test_index_retrieval_and_filters(rig):
    await add(rig)
    await add(rig,url='https://iiti.ac.in/hostels',text='Hostel rules and accommodation information for the test fixture.',department='Hostels')
    result = await sync(rig)
    assert result['counts']['indexed'] == 2
    assert (await sync(rig))['counts']['unchanged'] == 2
    evidence,metrics = await rig.service.retriever.retrieve('admission instructions',RetrievalFilters())
    assert len(evidence) == 1 and evidence[0].source['department'] == 'Academics'
    assert metrics['retrieval_ms'] >= metrics['embedding_ms'] >= 0
    evidence,_ = await rig.service.retriever.retrieve('admission',RetrievalFilters(department='Hostels'))
    assert evidence == []
    evidence,_ = await rig.service.retriever.retrieve('admission',RetrievalFilters(doc_type='pdf'))
    assert evidence == []
    evidence,_ = await rig.service.retriever.retrieve('admission',RetrievalFilters(source_url='https://iiti.ac.in/admissions'))
    assert len(evidence) == 1
    evidence,_ = await rig.service.retriever.retrieve('admission',RetrievalFilters(date_from=now()+timedelta(days=1)))
    assert evidence == []
    with pytest.raises(ValueError):
        await rig.service.retriever.retrieve('admission',RetrievalFilters(source_url='https://evil.com/'))


async def test_removed_stale_and_unapproved_never_retrieved(rig):
    doc = await add(rig)
    await sync(rig)
    async with rig.sessions() as s:
        d = await s.get(Document,doc.id)
        d.active = False
        await s.commit()
    assert not (await rig.service.retriever.retrieve('admission',RetrievalFilters()))[0]
    async with rig.sessions() as s:
        d = await s.get(Document,doc.id)
        d.active = True
        d.last_crawled = now()-timedelta(days=31)
        await s.commit()
    assert not (await rig.service.retriever.retrieve('admission',RetrievalFilters()))[0]
    async with rig.sessions() as s:
        d = await s.get(Document,doc.id)
        d.last_crawled = now()
        await s.commit()
    rig.policy_path.write_text(json.dumps({'allowed_domains':['academic.iiti.ac.in'],
                                        'seeds':[{'url':'https://academic.iiti.ac.in/'}]}))
    assert not (await rig.service.retriever.retrieve('admission',RetrievalFilters()))[0]


async def test_changed_content_hides_old_index_and_replaces_points(rig):
    doc = await add(rig)
    await sync(rig)
    await add(rig,text='Admission updated regulation with a different deadline in the test fixture.')
    assert not (await rig.service.retriever.retrieve('admission',RetrievalFilters()))[0]
    await sync(rig)
    found,_ = await rig.service.retriever.retrieve('admission',RetrievalFilters())
    assert len(found) == 1 and 'updated' in found[0].text
    assert (await rig.store.client.count(rig.store.collection)).count == 1
    async with rig.sessions() as s:
        d = await s.get(Document,doc.id)
        d.active, d.needs_indexing = False,True
        await s.commit()
    await sync(rig)
    assert (await rig.store.client.count(rig.store.collection)).count == 0


async def test_failed_upsert_retries_and_no_partial_exposure(rig,monkeypatch):
    doc = await add(rig)
    original = rig.store.write
    async def fail(*args): raise RuntimeError('simulated Qdrant outage')
    monkeypatch.setattr(rig.store,'write',fail)
    assert (await sync(rig))['counts']['failed'] == 1
    async with rig.sessions() as s:
        assert (await s.get(Document,doc.id)).needs_indexing
    monkeypatch.setattr(rig.store,'write',original)
    assert (await sync(rig))['counts']['indexed'] == 1


async def test_recreated_collection_reindexes_unchanged_documents(rig):
    await add(rig)
    await sync(rig)
    await rig.store.client.delete_collection(rig.store.collection)
    assert (await sync(rig))['counts']['indexed'] == 1


async def test_forged_citations_and_quotes_rejected(rig):
    await add(rig)
    await sync(rig)
    evidence,_ = await rig.service.retriever.retrieve('admission',RetrievalFilters())
    assert validate_line(json.dumps({'source_id':'S100','quote':evidence[0].text}),evidence) is None
    assert validate_line(json.dumps({'source_id':'S1','quote':'A completely invented campus rule.'}),evidence) is None
    assert validate_line(json.dumps({'source_id':'S1','quote':evidence[0].text,'url':'https://evil.com/'}),evidence) is None
    assert validate_line(json.dumps({'source_id':'S1','quote':evidence[0].text}),evidence)


async def client_for(rig):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(rig.config,rig.service,rig.auth)),base_url='http://test')


def headers(rig): return {'Authorization':'Bearer '+rig.session_token}


def parse_sse(text):
    return [(block.splitlines()[0][7:],json.loads(block.splitlines()[1][6:]))
            for block in text.strip().split('\n\n') if block.startswith('event: ')]


async def test_api_auth_validation_and_stream_persistence(rig):
    await add(rig)
    await sync(rig)
    async with await client_for(rig) as client:
        assert (await client.post('/v1/retrieve',json={'question':'admission'})).status_code == 401
        assert (await client.post('/v1/chat/stream',headers=headers(rig),json={'question':'  '})).status_code == 422
        result = await client.post('/v1/chat/stream',headers=headers(rig),json={'question':'admission instructions'})
        assert result.status_code == 200 and result.headers['content-type'].startswith('text/event-stream')
        events = parse_sse(result.text)
        assert [e[0] for e in events] == ['start','retrieval','delta','sources','done']
        done = events[-1][1]
        assert done['status'] == 'answered' and done['sources'][0]['url'] == 'https://iiti.ac.in/admissions'
        assert done['metrics']['total_ms'] >= done['metrics']['first_visible_ms'] >= 0
        conv = done['conversation_id']
        result = await client.post('/v1/chat/stream',headers=headers(rig),json={'question':'admission details','conversation_id':conv})
        assert parse_sse(result.text)[-1][1]['conversation_id'] == conv
    async with rig.sessions() as s:
        messages = (await s.scalars(select(Message))).all()
        assert len(messages) == 4 and len([m for m in messages if m.role == 'assistant']) == 2


async def test_unanswered_logs_and_no_model_call_for_no_evidence(rig):
    await add(rig)
    await sync(rig)
    async with await client_for(rig) as client:
        result = await client.post('/v1/chat/stream',headers=headers(rig),json={'question':'sports arena'})
        done = parse_sse(result.text)[-1][1]
        assert done['answer'] == REFUSAL and done['sources'] == [] and rig.llm.calls == 0
    async with rig.sessions() as s:
        assert (await s.scalar(select(UnansweredQuery))).reason == 'no_current_evidence'


async def test_invalid_model_output_refuses_and_outage_is_error(rig):
    await add(rig)
    await sync(rig)
    async with await client_for(rig) as client:
        rig.llm.invalid = True
        result = await client.post('/v1/chat/stream',headers=headers(rig),json={'question':'admission instructions'})
        assert parse_sse(result.text)[-1][1]['status'] == 'unanswered'
        rig.llm.error = True
        result = await client.post('/v1/chat/stream',headers=headers(rig),json={'question':'admission instructions'})
        assert parse_sse(result.text)[-1][0] == 'error'
        assert parse_sse(result.text)[-1][1]['discard_partial']


async def test_disabled_api_and_conversation_ownership(rig):
    disabled = rig.config.model_copy(update={'rag_api_enabled':False})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(disabled,rig.service,rig.auth)),base_url='http://test') as c:
        assert (await c.post('/v1/retrieve',headers=headers(rig),json={'question':'admission'})).status_code == 503
    async with await client_for(rig) as c:
        result = await c.post('/v1/chat/stream',headers=headers(rig),json={
            'question':'admission','conversation_id':'00000000-0000-0000-0000-000000000001'})
        assert result.status_code == 404


async def test_bge_windows_do_not_truncate_and_long_queries_rejected(rig):
    from tokenizers import Tokenizer, models, pre_tokenizers
    from backend.app.rag.embedding import BGEEmbedder, QueryTooLong
    tokenizer = Tokenizer(models.WordLevel({'[UNK]':0,'admission':1,'rule':2},unk_token='[UNK]'))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    embedder = BGEEmbedder(rig.config)
    embedder._model, embedder._tokenizer = object(),tokenizer
    value = 'admission rule '*700
    windows = embedder.windows(value)
    assert len(windows) > 1
    assert all(len(embedder.tokens(w)) <= 460 for w in windows)
    assert sum(len(embedder.tokens(w)) for w in windows) >= len(embedder.tokens(value))
    with pytest.raises(QueryTooLong): await embedder.validate_query(value)


async def test_openai_compatible_transport_stream(rig):
    from openai import AsyncOpenAI
    from backend.app.rag.generation import LocalLLM
    await add(rig)
    await sync(rig)
    evidence,_ = await rig.service.retriever.retrieve('admission',RetrievalFilters())
    recorded = []
    async def handler(request):
        recorded.append(json.loads(request.content))
        body = ''.join('data: '+json.dumps({'id':'fixture','object':'chat.completion.chunk',
            'created':0,'model':'local-fixture','choices':[{'index':0,'delta':{'content':token},'finish_reason':None}]})+'\n\n'
            for token in ['first',' second'])+'data: [DONE]\n\n'
        return httpx.Response(200,headers={'Content-Type':'text/event-stream'},content=body)
    model = LocalLLM(rig.config)
    await model.client.close()
    model.client = AsyncOpenAI(base_url='http://local.test/v1',api_key='local',
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:
        assert ''.join([token async for token in model.tokens('admission',evidence)]) == 'first second'
        assert recorded[0]['stream'] is True and recorded[0]['model'] == rig.config.llm_model
        assert 'untrusted' in recorded[0]['messages'][0]['content']
    finally: await model.close()


async def test_sse_disconnect_does_not_deadlock_full_queue():
    import asyncio
    from backend.app.rag.api import stream_events
    closed = asyncio.Event()
    async def many_events():
        try:
            for i in range(100): yield 'delta',{'text':str(i)}
        finally: closed.set()
    stream = stream_events(many_events())
    await anext(stream)
    await asyncio.sleep(0)
    await asyncio.wait_for(stream.aclose(),timeout=1)
    assert closed.is_set()


async def test_quote_text_cannot_render_html_or_links(rig):
    from backend.app.rag.generation import escape_markdown
    value = '<script>alert(1)</script> [fake](https://evil.com)'
    escaped = escape_markdown(value)
    assert '<script>' not in escaped and '\\[fake\\]' in escaped


async def test_evaluation_metrics_require_gold_labels(rig):
    from evaluation.run import score
    retrieved={'sources':[{'url':'https://iiti.ac.in/a','parent_id':'p','text':'A real source excerpt for evaluation.'}]}
    done={'status':'answered','quotes':[{'parent_id':'p','text':'A real source excerpt for evaluation.'}]}
    result=score({'gold_source_urls':[]},retrieved,done)
    assert result['source_recall_at_k'] is None and result['quote_verbatim_rate'] == 1
    result=score({'gold_source_urls':['https://iiti.ac.in/a'],'expected_status':'answered',
                  'gold_answer_phrases':['real source']},retrieved,done)
    assert result['source_recall_at_k'] == 1 and result['answerability_correct']


async def test_tampered_vector_payload_cannot_borrow_official_source(rig):
    await add(rig)
    await sync(rig)
    points,_ = await rig.store.client.scroll(rig.store.collection)
    await rig.store.client.set_payload(rig.store.collection,
        payload={'text':'An invented admission rule inserted into a vector payload.'},points=[points[0].id])
    evidence,_ = await rig.service.retriever.retrieve('admission',RetrievalFilters())
    assert evidence == []


async def test_fresh_install_without_collection_returns_no_evidence(rig):
    assert not await rig.store.client.collection_exists(rig.store.collection)
    evidence, _ = await rig.service.retriever.retrieve("admission process", RetrievalFilters())
    assert evidence == []
