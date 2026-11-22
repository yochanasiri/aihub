"""Authenticated Gemini chat with actual Google Search and URL context tools."""
import asyncio
import math
import random
import logging
import re
from urllib.parse import urlsplit
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from .auth.api import institutional_actor

router = APIRouter(prefix='/v1')
log = logging.getLogger('aihub.gemini')
BASE = 'https://generativelanguage.googleapis.com/v1beta'
WEB_REQUEST = re.compile(
    r'https?://|\b(?:latest|current|today|yesterday|this week|this month|this year|'
    r'as of|right now|look up|search (?:for|the web|online|google)|'
    r'(?:use|check) (?:google|the web)|fact[- ]check|verify|cite|citations?|sources?|'
    r'admissions?|deadlines?|placements?|cutoffs?|fees?|rankings?)\b',
    re.IGNORECASE,
)
WEB_UNAVAILABLE = (
    'I could not verify that with live web sources. For IIT Indore questions, '
    'try the campus assistant for official sources.'
)

class Turn(BaseModel):
    role: str
    content: str = Field(min_length=1, max_length=3000)

class GeminiRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[Turn] = Field(default_factory=list, max_length=12)


def build_body(question, history, search=True):
    contents = []
    for turn in history:
        role = 'user' if turn.role == 'user' else 'model'
        if contents and contents[-1]['role'] == role:
            contents[-1]['parts'].append({'text': turn.content})
        else:
            contents.append({'role': role, 'parts': [{'text': turn.content}]})
    if contents and contents[0]['role'] == 'model':
        contents.pop(0)
    if contents and contents[-1]['role'] == 'user':
        contents[-1]['parts'].append({'text': question})
    else:
        contents.append({'role': 'user', 'parts': [{'text': question}]})
    body = {'systemInstruction': {'parts': [{'text': (
        'You are a friendly conversational general AI assistant. Respond naturally to greetings, small talk and follow-ups. '
        'Use the conversation to resolve references. Answer only what is asked, with complete lists when requested. '
        'For current facts, explicit searches, and verification, use Google Search when enabled. '
        'For public website URLs supplied by the user, use URL context when enabled. '
        'Never claim to have browsed unless a tool was used. If evidence is missing or a page is inaccessible, say so. '
        'State uncertainty explicitly. Never guess exact figures, dates, policies, names, links, or citations. '
        'For campus-specific facts, direct users to the campus assistant unless you can cite a verified web source. '
        'You are not the private IIT Indore knowledge base. Never invent institute records or citations.'
    )}]}, 'contents': contents, 'generationConfig': {'maxOutputTokens': 8192}}
    if search:
        body['tools'] = [{'google_search': {}}, {'url_context': {}}]
    return body


def extract_answer(result):
    candidate = (result.get('candidates') or [{}])[0]
    answer = '\n'.join(p['text'] for p in candidate.get('content', {}).get('parts', [])
                       if isinstance(p.get('text'), str) and not p.get('thought')).strip()
    metadata = candidate.get('groundingMetadata') or {}
    sources, seen = [], set()
    def add(url, title):
        if not isinstance(url, str) or len(url) > 8192 or url in seen:
            return
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                return
        except ValueError:
            return
        seen.add(url)
        sources.append({'id': f'W{len(sources)+1}', 'url': url, 'title': str(title or parsed.hostname)[:300]})
    for chunk in metadata.get('groundingChunks', []):
        web = chunk.get('web') or {}
        add(web.get('uri'), web.get('title'))
    for item in (candidate.get('urlContextMetadata') or {}).get('urlMetadata', []):
        if item.get('urlRetrievalStatus') == 'URL_RETRIEVAL_STATUS_SUCCESS':
            add(item.get('retrievedUrl'), None)
    # The client displays only actual returned source metadata, never invented links.
    return answer, sources[:20], candidate.get('finishReason'), bool(metadata.get('webSearchQueries'))

def _seconds(value):
    """Parse Google retryDelay (e.g. '4.2s') or an HTTP Retry-After number."""
    if isinstance(value, (float, int)):
        return max(0, float(value))
    if isinstance(value, str):
        value = value.strip().lower().removesuffix('s')
        try:
            return max(0, float(value))
        except ValueError:
            pass
    return None


def quota_diagnostic(response):
    """Interpret a 429 without leaking the API key or assuming a reset time.

    Rates, billing and grounding can all return 429. Details may include the
    google.rpc.QuotaFailure and google.rpc.RetryInfo structures.
    """
    try:
        error = response.json().get('error', {})
    except (ValueError, AttributeError, TypeError):
        error = {}
    message = str(error.get('message') or '').lower()
    dimensions = []
    retry_delay = _seconds(response.headers.get('Retry-After'))
    for detail in (error.get('details') or []):
        if not isinstance(detail, dict):
            continue
        kind = str(detail.get('@type') or '')
        if kind.endswith('QuotaFailure'):
            for violation in detail.get('violations', []):
                if isinstance(violation, dict):
                    dimensions.append(' '.join(str(violation.get(key) or '') for key in ('quotaId', 'quotaMetric', 'description')).lower())
        if kind.endswith('RetryInfo'):
            parsed = _seconds(detail.get('retryDelay'))
            if parsed is not None:
                retry_delay = parsed
    text = ' '.join([message, *dimensions])
    # Some 429 errors contain only a plain-English 'Please retry in ...' hint.
    if retry_delay is None:
        match = re.search(r'(?:retry|try again)\s+in\s+([0-9]+(?:\.[0-9]+)?)\s*s', text)
        if match:
            retry_delay = float(match.group(1))
    if any(x in ' '.join(dimensions or [message]) for x in ('grounding', 'google_search', 'google search', 'search quota', 'url_context', 'url context')):
        kind, detail = 'grounding', 'Gemini web-tool quota may have been reached. Check tool limits in Google AI Studio.'
    elif any(x in text for x in ('perday', 'per_day', 'requests per day', 'daily', 'per day')):
        kind, detail = 'daily', 'Gemini daily request/token quota was reached. Check the project/model limits in Google AI Studio; retrying immediately will not help.'
    elif any(x in text for x in ('billing', 'prepay', 'spend cap', 'credits', 'balance', 'tier drop')):
        kind, detail = 'billing', 'Gemini billing or spending limit may have been reached. Check your project billing and spend cap in Google AI Studio.'
    elif any(x in text for x in ('perminute', 'per_minute', 'requests per minute', 'tokens per minute', 'rpm', 'tpm', 'per minute')):
        kind, detail = 'minute', 'Gemini per-minute requests or token limit was reached. Reduce request frequency and wait before trying again.'
    else:
        kind, detail = 'unknown', 'Gemini returned HTTP 429. It may be rate limiting, model quota, search grounding quota, or a billing cap. Check Google AI Studio usage and rate limits.'
    return kind, detail, retry_delay


async def generate(config, body, *, transport=None, require_sources=False):
    if require_sources and not body.get('tools'):
        return {'answer': WEB_UNAVAILABLE, 'mode': 'gemini', 'sources': [], 'searched': False,
                'google_search_enabled': False}
    model = config.gemini_model.removeprefix('models/')
    if not re.fullmatch(r'[a-zA-Z0-9_.-]{2,100}', model):
        raise HTTPException(503, 'Invalid GEMINI_MODEL configuration')
    url = f'{BASE}/models/{model}:generateContent'
    if model == 'gemini-3.8-flash':
        body = {**body, 'generationConfig': {**body.get('generationConfig', {}),
                'thinkingConfig': {'thinkingLevel': 'LOW'}}}
    timeout = httpx.Timeout(150.0, connect=20.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        used_plain_fallback = False
        for attempt in range(3):
            try:
                response = await client.post(url, json=body, headers={'x-goog-api-key': config.gemini_api_key})
            except httpx.RequestError as exc:
                log.warning('gemini_transport_error model=%s type=%s attempt=%s', model, type(exc).__name__, attempt+1)
                if attempt == 0 and isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError)):
                    await asyncio.sleep(1)
                    continue
                if isinstance(exc, httpx.TimeoutException):
                    raise HTTPException(504, 'Gemini timed out. Search can take longer; retry or ask a shorter question.') from None
                raise HTTPException(503, f'Gemini connection failed ({type(exc).__name__}). Check Docker internet, DNS, proxy or TLS settings.') from None
            if response.status_code == 429:
                kind, reason, wait = quota_diagnostic(response)
                log.warning('gemini_quota model=%s kind=%s retry_seconds=%s', model, kind, wait)
                if require_sources:
                    return {'answer': WEB_UNAVAILABLE, 'mode': 'gemini', 'sources': [], 'searched': False,
                            'google_search_enabled': bool(body.get('tools'))}
                if body.get('tools') and kind in {'grounding', 'billing', 'unknown'}:
                    body = {key: value for key, value in body.items() if key != 'tools'}
                    used_plain_fallback = True
                    continue
                # Retry only an explicitly short, temporary, per-minute limit.
                if attempt == 0 and kind == 'minute' and wait is not None and wait <= 7:
                    await asyncio.sleep(max(0.5, wait) + random.uniform(0, 0.25))
                    continue
                headers = {'Retry-After': str(math.ceil(wait))} if wait is not None and wait > 0 else None
                if wait is not None and wait > 0:
                    reason += f' Suggested wait: about {math.ceil(wait)} seconds.'
                raise HTTPException(429, reason, headers=headers)
            if response.status_code in (502, 503, 504) and attempt == 0:
                await asyncio.sleep(1)
                continue
            if response.status_code >= 400:
                try:
                    err = response.json().get('error', {})
                    message = str(err.get('message', ''))[:500].replace(config.gemini_api_key, '[redacted]')
                    status = str(err.get('status', ''))[:80]
                except (ValueError, AttributeError):
                    message, status = '', ''
                log.warning('gemini_http_error model=%s code=%s status=%s', model, response.status_code, status)
                code = response.status_code
                if code in (401, 403):
                    raise HTTPException(503, f'Gemini credential or permission error ({code}): {message}')
                raise HTTPException(502, f'Gemini API error ({code}): {message or status or "See backend logs"}')
            try:
                answer, sources, finish, searched = extract_answer(response.json())
            except (ValueError, TypeError, AttributeError):
                raise HTTPException(502, 'Gemini returned an invalid response') from None
            if require_sources and (not answer or not sources):
                return {'answer': WEB_UNAVAILABLE, 'mode': 'gemini', 'sources': [], 'searched': searched,
                        'google_search_enabled': bool(body.get('tools'))}
            if not answer:
                raise HTTPException(502, f'Gemini returned no answer ({finish or "blocked or empty"}). Try rephrasing.')
            if finish == 'MAX_TOKENS':
                answer += '\n\n[Response reached the output limit.]'
            if used_plain_fallback:
                answer = 'Live web tools are temporarily unavailable.\n\n' + answer
            return {'answer': answer, 'mode':'gemini', 'sources':sources, 'searched':searched,
                    'google_search_enabled':bool(body.get('tools'))}

@router.post('/gemini/chat')
async def gemini_chat(data: GeminiRequest, request: Request, actor=Depends(institutional_actor)):
    config = request.app.state.config
    if not config.gemini_api_key:
        raise HTTPException(503, 'GEMINI_API_KEY is not configured in the backend')
    if any(t.role not in {'user','assistant'} for t in data.history):
        raise HTTPException(422, 'Invalid conversation roles')
    if not data.question.strip():
        raise HTTPException(422, 'Enter a message')
    question = data.question.strip()
    last_user_message = next((turn.content for turn in reversed(data.history) if turn.role == 'user'), '')
    require_sources = bool(WEB_REQUEST.search(question) or (
        re.match(r'^(?:what about|how about|and\b|where\b|when\b|why\b)', question, re.IGNORECASE)
        and WEB_REQUEST.search(last_user_message)
    ))
    body = build_body(question, data.history, config.gemini_google_search)
    semaphore = request.app.state.gemini_slots
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=20)
    except TimeoutError:
        raise HTTPException(429, 'Gemini is busy. Retry shortly.', headers={'Retry-After':'3'})
    try:
        try:
            async with asyncio.timeout(240):
                return await generate(config, body, require_sources=require_sources)
        except TimeoutError:
            raise HTTPException(504, 'Gemini request exceeded the overall time limit. Try a shorter question.') from None
    finally:
        semaphore.release()
