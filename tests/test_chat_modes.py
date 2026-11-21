"""Offline tests; no Google key, network, database, or model downloads required."""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch
import httpx
import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]

def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

# Isolate only auth dependency in these unit tests; real app retains the auth guard.
auth = ModuleType('backend.app.auth.api')
async def actor(): return 'test-user'
auth.institutional_actor = actor
with patch.dict(sys.modules, {'backend.app.auth.api':auth}):
    gemini = load('backend.app.gemini', 'backend/app/gemini.py')
conversation = load('conversation_unit', 'backend/app/rag/conversation.py')

def test_grounded_request_and_history():
    history = [gemini.Turn(role='user',content='Hello'), gemini.Turn(role='assistant',content='Hi!')]
    body = gemini.build_body('Find current information', history, True)
    assert body['tools'] == [{'google_search':{}},{'url_context':{}}]
    assert [x['role'] for x in body['contents']] == ['user','model','user']
    assert 'tools' not in gemini.build_body('hello', [], False)

def test_source_extraction_filters_untrusted_urls_and_thoughts():
    result = {'candidates':[{'content':{'parts':[{'text':'private thought','thought':True},{'text':'Answer'}]}, 'groundingMetadata':{'webSearchQueries':['query'],'groundingChunks':[{'web':{'uri':'https://iiti.ac.in/','title':'IIT Indore'}},{'web':{'uri':'javascript:alert(1)'}}]}, 'urlContextMetadata':{'urlMetadata':[{'retrievedUrl':'https://example.com/','urlRetrievalStatus':'URL_RETRIEVAL_STATUS_SUCCESS'}]}, 'finishReason':'STOP'}]}
    answer,sources,finish,searched = gemini.extract_answer(result)
    assert answer == 'Answer' and len(sources) == 2 and finish == 'STOP' and searched

def test_gemini_request_and_response_with_mock_transport():
    cfg = SimpleNamespace(gemini_api_key='fake-key',gemini_model='gemini-3.8-flash',gemini_google_search=True)
    def handler(request):
        assert request.headers['x-goog-api-key']=='fake-key'
        assert 'gemini-3.8-flash:generateContent' in str(request.url)
        assert json.loads(request.content)['tools']
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':'Hello there!'}]},'finishReason':'STOP'}]})
    out = asyncio.run(gemini.generate(cfg, gemini.build_body('Hi',[],True),transport=httpx.MockTransport(handler)))
    assert out['answer']=='Hello there!' and not out['searched']

def test_http_error_does_not_masquerade_as_connectivity():
    cfg = SimpleNamespace(gemini_api_key='fake-key',gemini_model='gemini-3.8-flash',gemini_google_search=True)
    def handler(request): return httpx.Response(404,json={'error':{'message':'Model unavailable','status':'NOT_FOUND'}})
    with pytest.raises(HTTPException) as e:
        asyncio.run(gemini.generate(cfg,{},transport=httpx.MockTransport(handler)))
    assert '404' in e.value.detail and 'Model unavailable' in e.value.detail

def test_social_reply_generated_by_model_and_followup_rewritten():
    create=AsyncMock(side_effect=[SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"route":"social","text":"I am glad to help! How are you?"}'))]), SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"route":"retrieve","text":"What is Optum eligibility?"}'))])])
    llm=SimpleNamespace(config=SimpleNamespace(llm_model='local'),client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    assert asyncio.run(conversation.route_turn(llm,'how are you',[])) == ('social','I am glad to help! How are you?')
    assert asyncio.run(conversation.route_turn(llm,'what about eligibility',[{'role':'user','content':'JD of Optum'}])) == ('retrieve','What is Optum eligibility?')
    assert create.await_count == 2

def test_long_history_proxy_has_matching_limits():
    proxy=(ROOT/'frontend/lib/proxy.ts').read_text()
    chat=(ROOT/'frontend/components/campus/chat.tsx').read_text()
    assert 'path==="gemini/chat"?256*1024' in proxy
    assert 'slice(0,3000)' in chat
    assert 'sources:data.sources||[]' in chat
