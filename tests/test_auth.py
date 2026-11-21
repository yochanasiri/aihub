import json
import secrets
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from google.auth import jwt, crypt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from backend.app.config import Settings
from backend.app.models import Base, User, AuthSession, PendingRegistration, AuthAudit, now
from backend.app.main import create_app
from backend.app.auth.security import institute_email, token_hash, hash_password
from backend.app.auth.service import AuthService
from backend.app.auth.google import GoogleVerifier


class RecordingMailer:
    def __init__(self): self.messages=[]
    async def send_code(self,email,code): self.messages.append((email,code))


@pytest.fixture
async def authrig():
    config=Settings(_env_file=None,auth_enabled=True,auth_bridge_secret='test-bridge-secret-'+secrets.token_hex(20),
                    google_client_id='test-client.apps.googleusercontent.com',smtp_host='unused.test')
    engine=create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    private=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    signer=crypt.RSASigner.from_string(private,key_id='test-key')
    def transport(*args,**kwargs): return SimpleNamespace(status=200,data=json.dumps({'test-key':public}).encode())
    verifier=GoogleVerifier(config,transport)
    mailer=RecordingMailer()
    service=AuthService(config,sessions,verifier,mailer)
    client=httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(config,auth_service=service)),base_url='http://test')
    yield SimpleNamespace(config=config,sessions=sessions,service=service,client=client,mailer=mailer,signer=signer,
                          bridge={'X-AIHUB-Bridge':config.auth_bridge_secret},verifier=verifier)
    await client.aclose();verifier.close();await engine.dispose()


def google_token(rig,**overrides):
    stamp=int(now().timestamp())
    claims={'sub':'google-subject-123','iss':'https://accounts.google.com','aud':rig.config.google_client_id,
            'iat':stamp,'exp':stamp+3600,'email':'verified@iiti.ac.in','email_verified':True,'hd':'iiti.ac.in','name':'Verified User'}
    claims.update(overrides)
    return jwt.encode(rig.signer,claims).decode()


@pytest.mark.parametrize('email',['person@gmail.com','person@students.iiti.ac.in','person@iiti.ac.in.evil.com',
    'person@eviliiti.ac.in','person@iiti.ac.in.','person@iiti.ac.in@evil.com','person@іiti.ac.in','person\n@iiti.ac.in'])
def test_exact_institute_email(email):
    with pytest.raises(ValueError): institute_email(email)


def test_normalized_institute_email(): assert institute_email(' Student@IITI.AC.IN ')== 'student@iiti.ac.in'


@pytest.mark.parametrize('overrides',[{'email':'x@gmail.com'},{'hd':'evil.com'},{'hd':None},{'email_verified':False},
    {'email_verified':'true'},{'aud':'other-client'},{'iss':'https://evil.com'},
    {'exp':1},{'iat':9999999999,'exp':10000000999},{'azp':'another-client'}])
async def test_google_claim_validation(authrig,overrides):
    token=google_token(authrig,**overrides)
    with pytest.raises(Exception): await authrig.verifier.verify(token)


async def test_google_signature_replay_and_secure_storage(authrig):
    token=google_token(authrig)
    claims=await authrig.verifier.verify(token)
    assert claims['sub']=='google-subject-123'
    parts=token.split('.')
    parts[1]=parts[1][:-2]+('AA' if parts[1][-2:]!='AA' else 'BB')
    with pytest.raises(Exception): await authrig.verifier.verify('.'.join(parts))
    assert (await authrig.client.post('/v1/auth/google',json={'id_token':token})).status_code==401
    response=await authrig.client.post('/v1/auth/google',headers=authrig.bridge,json={'id_token':token})
    assert response.status_code==200,response.text
    grant=response.json()
    assert grant['user']['role']=='Student' and grant['user']['email_verified']
    assert (await authrig.client.post('/v1/auth/google',headers=authrig.bridge,json={'id_token':token})).status_code==401
    async with authrig.sessions() as s:
        session=await s.scalar(select(AuthSession))
        assert session.token_hash==token_hash(grant['session_token']) and session.token_hash!=grant['session_token']


async def register(rig,email='student@iiti.ac.in',username='student_user'):
    response=await rig.client.post('/v1/auth/register',headers=rig.bridge,json={
        'email':email,'username':username,'name':'Student User','password':'long-safe-password-123'})
    assert response.status_code==200,response.text
    return response.json()['registration_id']


async def verified_user(rig):
    registration=await register(rig)
    code=rig.mailer.messages[-1][1]
    result=await rig.client.post('/v1/auth/verify-email',headers=rig.bridge,json={
        'registration_id':registration,'code':code,'password':'long-safe-password-123'})
    assert result.status_code==200,result.text
    return await rig.client.post('/v1/auth/password',headers=rig.bridge,json={
        'identifier':'student_user','password':'long-safe-password-123'})


async def test_password_requires_verification_and_code_is_single_use(authrig):
    registration=await register(authrig)
    async with authrig.sessions() as s:
        assert await s.scalar(select(User)) is None
        pending=await s.get(PendingRegistration,registration)
        assert pending.password_hash.startswith('$argon2id$')
        assert pending.code_hash!=authrig.mailer.messages[-1][1]
    denied=await authrig.client.post('/v1/auth/password',headers=authrig.bridge,json={
        'identifier':'student@iiti.ac.in','password':'long-safe-password-123'})
    assert denied.status_code==401
    code=authrig.mailer.messages[-1][1]
    body={'registration_id':registration,'code':code,'password':'long-safe-password-123'}
    assert (await authrig.client.post('/v1/auth/verify-email',headers=authrig.bridge,json=body)).status_code==200
    assert (await authrig.client.post('/v1/auth/verify-email',headers=authrig.bridge,json=body)).status_code==400
    result=await authrig.client.post('/v1/auth/password',headers=authrig.bridge,json={
        'identifier':'STUDENT@IITI.AC.IN','password':'long-safe-password-123'})
    assert result.status_code==200


async def test_registration_rejects_role_assignment(authrig):
    response=await authrig.client.post('/v1/auth/register',headers=authrig.bridge,json={
        'email':'student@iiti.ac.in','username':'student_user','name':'User','password':'long-safe-password-123','role':'Admin'})
    assert response.status_code==422 and authrig.mailer.messages==[]


async def test_wrong_code_or_password_locks_challenge(authrig):
    registration=await register(authrig)
    right_code=authrig.mailer.messages[-1][1]
    wrong_code='000000' if right_code!='000000' else '111111'
    for _ in range(5):
        result=await authrig.client.post('/v1/auth/verify-email',headers=authrig.bridge,json={
            'registration_id':registration,'code':wrong_code,'password':'long-safe-password-123'})
        assert result.status_code==400
    result=await authrig.client.post('/v1/auth/verify-email',headers=authrig.bridge,json={
        'registration_id':registration,'code':right_code,'password':'long-safe-password-123'})
    assert result.status_code==400


async def test_expired_registration_cannot_verify(authrig):
    registration=await register(authrig)
    async with authrig.sessions() as s:
        pending=await s.get(PendingRegistration,registration);pending.expires_at=now()-timedelta(seconds=1);await s.commit()
    result=await authrig.client.post('/v1/auth/verify-email',headers=authrig.bridge,json={
        'registration_id':registration,'code':authrig.mailer.messages[-1][1],'password':'long-safe-password-123'})
    assert result.status_code==400


async def test_logout_expiry_disabled_user_and_admin_gates(authrig):
    response=await verified_user(authrig)
    grant=response.json();headers={'Authorization':'Bearer '+grant['session_token']}
    assert (await authrig.client.get('/v1/auth/me',headers=headers)).status_code==200
    assert (await authrig.client.get('/v1/admin/access',headers=headers)).status_code==403
    assert (await authrig.client.post('/v1/auth/logout',headers=headers)).status_code==200
    assert (await authrig.client.get('/v1/auth/me',headers=headers)).status_code==401
    response=await authrig.client.post('/v1/auth/password',headers=authrig.bridge,json={
        'identifier':'student_user','password':'long-safe-password-123'})
    second=response.json();headers={'Authorization':'Bearer '+second['session_token']}
    async with authrig.sessions() as s:
        await s.execute(update(AuthSession).where(AuthSession.token_hash==token_hash(second['session_token'])).values(expires_at=now()-timedelta(seconds=1)))
        await s.commit()
    assert (await authrig.client.get('/v1/auth/me',headers=headers)).status_code==401
    async with authrig.sessions() as s:
        user=await s.get(User,grant['user']['id']);user.is_active=False;await s.commit()
    response=await authrig.client.post('/v1/auth/password',headers=authrig.bridge,json={
        'identifier':'student_user','password':'long-safe-password-123'})
    assert response.status_code==401


async def test_demo_is_unique_flag_controlled_and_cannot_access_rag(authrig):
    assert (await authrig.client.post('/v1/auth/demo',headers=authrig.bridge,json={})).status_code==403
    authrig.config.demo_login_enabled=True
    first=(await authrig.client.post('/v1/auth/demo',headers=authrig.bridge,json={})).json()
    second=(await authrig.client.post('/v1/auth/demo',headers=authrig.bridge,json={})).json()
    assert first['user']['id']!=second['user']['id'] and first['user']['role']=='Student'
    headers={'Authorization':'Bearer '+first['session_token']}
    assert (await authrig.client.get('/v1/auth/me',headers=headers)).status_code==200
    assert (await authrig.client.get('/v1/admin/access',headers=headers)).status_code==403
    assert (await authrig.client.post('/v1/chat/stream',headers=headers,json={'question':'admission process'})).status_code==403
    authrig.config.demo_login_enabled=False
    assert (await authrig.client.get('/v1/auth/me',headers=headers)).status_code==401


async def test_privileged_role_changes_revoke_sessions(authrig):
    grant=(await verified_user(authrig)).json()
    async with authrig.sessions() as s:
        admin=User(email='admin@iiti.ac.in',name='Admin',role='Admin',email_verified=True,is_demo=False)
        s.add(admin);await s.flush();admin_grant=await authrig.service.grant(s,admin,'google');await s.commit()
    headers={'Authorization':'Bearer '+admin_grant['session_token']}
    assert (await authrig.client.get('/v1/admin/access',headers=headers)).status_code==200
    changed=await authrig.client.patch(f"/v1/admin/users/{grant['user']['id']}/role",headers=headers,json={'role':'Faculty'})
    assert changed.status_code==200 and changed.json()['role']=='Faculty'
    assert (await authrig.client.get('/v1/auth/me',headers={'Authorization':'Bearer '+grant['session_token']})).status_code==401
    denied=await authrig.client.patch(f"/v1/admin/users/{admin_grant['user']['id']}/role",headers=headers,json={'role':'Student'})
    assert denied.status_code==409
    async with authrig.sessions() as s:
        assert await s.scalar(select(AuthAudit).where(AuthAudit.event=='role_changed'))


async def test_login_rate_limit_persists_in_database(authrig):
    for _ in range(10):
        await authrig.service.rate_limit('test-login','same-account',10)
    with pytest.raises(Exception) as exc:
        await authrig.service.rate_limit('test-login','same-account',10)
    assert exc.value.status_code==429
