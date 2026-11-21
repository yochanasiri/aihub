"""Read-side privacy and official notice provenance for Phase 4."""
from datetime import timedelta
from uuid import uuid4
import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from backend.app.models import Base, User, Conversation, Message, Document, now
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.auth.service import AuthService

@pytest.fixture
async def campus(tmp_path):
    policy=tmp_path/'seeds.json'
    policy.write_text('{"allowed_domains":["iiti.ac.in"],"seeds":[{"url":"https://iiti.ac.in/"}]}')
    config=Settings(_env_file=None,auth_enabled=True,auth_bridge_secret='x'*40,crawler_config=str(policy),demo_login_enabled=True)
    engine=create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    auth=AuthService(config,sessions,None,None)
    grants=[]
    async with sessions() as db:
        for name in ['owner','other','demo']:
            user=User(email=f'{name}@iiti.ac.in',name=name,role='Student',email_verified=True,is_demo=name=='demo')
            db.add(user);await db.flush()
            grants.append(await auth.grant(db,user,'demo' if name=='demo' else 'password'))
        conversation=Conversation(user_id=grants[0]['user']['id'],title='Private question')
        db.add(conversation);await db.flush()
        for index in range(102): db.add(Message(conversation_id=conversation.id,role='user',content=f'message {index}',sources=[],created_at=now()+timedelta(seconds=index)))
        for name,url,active,age in [('Current news','https://iiti.ac.in/news',True,0),('External news','https://evil.test/news',True,0),('Inactive news','https://iiti.ac.in/news/old',False,0),('Stale news','https://iiti.ac.in/news/stale',True,60),('Academic regulations','https://iiti.ac.in/academics',True,0)]:
            db.add(Document(title=name,url=url,hash='a'*64,text='source body',department='Institute',doc_type='html',active=active,last_crawled=now()-timedelta(days=age)))
        await db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(config,auth_service=auth)),base_url='http://test') as client:
        yield client,grants,conversation.id,policy
    await engine.dispose()


def headers(grant): return {'Authorization':'Bearer '+grant['session_token']}

async def test_history_ownership_and_pagination(campus):
    client,grants,cid,_=campus
    assert (await client.get('/v1/conversations')).status_code==401
    assert (await client.get('/v1/conversations',headers=headers(grants[2]))).status_code==403
    own=await client.get('/v1/conversations',headers=headers(grants[0]))
    assert [c['id'] for c in own.json()['items']]==[cid]
    assert (await client.get('/v1/conversations',headers=headers(grants[1]))).json()['items']==[]
    path=f'/v1/conversations/{cid}/messages'
    assert (await client.get(path,headers=headers(grants[1]))).status_code==404
    assert (await client.get(f'/v1/conversations/{uuid4()}/messages',headers=headers(grants[0]))).status_code==404
    page=(await client.get(path,headers=headers(grants[0]))).json()
    assert page['has_more'] and len(page['items'])==100
    assert page['items'][0]['content']=='message 2' and page['items'][-1]['content']=='message 101'
    older=(await client.get(path+'?offset=100',headers=headers(grants[0]))).json()
    assert not older['has_more'] and [m['content'] for m in older['items']]==['message 0','message 1']
    assert (await client.get(path+'?offset=-1',headers=headers(grants[0]))).status_code==422

async def test_notices_exact_current_sources_and_revocation(campus):
    client,grants,_,policy=campus
    response=await client.get('/v1/campus/notices',headers=headers(grants[0]))
    assert response.status_code==200
    assert [n['title'] for n in response.json()['items']]==['Current news']
    policy.write_text('{"allowed_domains":["academic.iiti.ac.in"],"seeds":[{"url":"https://academic.iiti.ac.in/"}]}')
    assert (await client.get('/v1/campus/notices',headers=headers(grants[0]))).json()['items']==[]
    assert (await client.get('/v1/campus/notices',headers=headers(grants[2]))).status_code==403

async def test_status_never_claims_disabled_rag_is_active(campus):
    client,grants,_,_=campus
    response=await client.get('/v1/campus/status',headers=headers(grants[0]))
    assert response.status_code==200 and response.json()['status']=='unavailable'
    assert (await client.get('/v1/campus/status')).status_code==401
