"""Opt-in real Next.js/Auth.js + FastAPI cookie-flow smoke test. No Google or SMTP traffic."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import httpx

ROOT=Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));return s.getsockname()[1]


async def seed(dsn,password):
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from backend.app.models import Base,User
    from backend.app.auth.security import hash_password
    engine=create_async_engine(dsn)
    async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)
    sessions=async_sessionmaker(engine)
    async with sessions() as s:
        s.add(User(email='smoke@iiti.ac.in',username='smoke_user',name='Smoke Test',role='Student',
                   email_verified=True,is_demo=False,password_hash=await hash_password(password)))
        await s.commit()
    await engine.dispose()


def wait_ready(client,url):
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        try:
            response=client.get(url)
            if response.status_code==200:return
        except httpx.HTTPError: pass
        time.sleep(.3)
    raise RuntimeError('Smoke test server did not start')


def main():
    with tempfile.TemporaryDirectory(prefix='aihub-auth-smoke-') as temporary:
        folder=Path(temporary)
        backend_port,frontend_port=free_port(),free_port()
        password='smoke-'+secrets.token_urlsafe(24)
        backend=f'http://127.0.0.1:{backend_port}'
        frontend=f'http://127.0.0.1:{frontend_port}'
        dsn=f'sqlite+aiosqlite:///{folder}/test.db'
        asyncio.run(seed(dsn,password))
        environ={**os.environ,'DATABASE_URL':dsn,'AUTH_ENABLED':'true','RAG_API_ENABLED':'false',
                 'AUTH_BRIDGE_SECRET':secrets.token_urlsafe(40),'AUTH_SECRET':secrets.token_urlsafe(40),
                 'AUTH_URL':frontend,'BACKEND_URL':backend,'GOOGLE_CLIENT_ID':'','GOOGLE_CLIENT_SECRET':'',
                 'DEMO_LOGIN_ENABLED':'true','NEXT_TELEMETRY_DISABLED':'1','NODE_ENV':'development'}
        processes=[]
        with (folder/'server.log').open('w') as logs:
            try:
                processes.append(subprocess.Popen([sys.executable,'-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port',str(backend_port)],
                    cwd=ROOT,env=environ,stdout=logs,stderr=logs,start_new_session=True))
                processes.append(subprocess.Popen(['node',str(ROOT/'frontend/node_modules/next/dist/bin/next'),'dev','--hostname','127.0.0.1','--port',str(frontend_port)],
                    cwd=ROOT/'frontend',env=environ,stdout=logs,stderr=logs,start_new_session=True))
                with httpx.Client(trust_env=False,timeout=30,follow_redirects=False) as client:
                    wait_ready(client,backend+'/health/live');wait_ready(client,frontend+'/login')
                    bad_origin=client.post(frontend+'/api/account/register',headers={'Origin':'https://evil.com'},json={})
                    assert bad_origin.status_code==403
                    csrf=client.get(frontend+'/api/auth/csrf').json()['csrfToken']
                    result=client.post(frontend+'/api/auth/callback/credentials',headers={
                        'Origin':frontend,'X-Auth-Return-Redirect':'1'},data={'csrfToken':csrf,'identifier':'smoke_user',
                        'password':password,'callbackUrl':frontend+'/account'})
                    assert result.status_code==200 and 'error=' not in result.text,result.text
                    cookies=result.headers.get_list('set-cookie')
                    assert any('aihub.session=' in cookie and 'httponly' in cookie.lower() and 'samesite=lax' in cookie.lower() for cookie in cookies)
                    session=client.get(frontend+'/api/auth/session').json()
                    assert session['user']['email']=='smoke@iiti.ac.in',session
                    assert 'backendToken' not in json.dumps(session) and 'session_token' not in json.dumps(session)
                    csrf=client.get(frontend+'/api/auth/csrf').json()['csrfToken']
                    changed=client.post(frontend+'/api/auth/session',headers={'Origin':frontend},json={
                        'csrfToken':csrf,'data':{'campusUser':{'role':'Admin'},'backendToken':'forged'}})
                    assert changed.json()['user']['role']=='Student'
                    assert client.get(frontend+'/api/campus/auth/me').json()['role']=='Student'
                    assert client.get(frontend+'/api/campus/admin/access').status_code==403
                    assert client.post(frontend+'/api/campus/retrieve',headers={'Origin':'https://evil.com'},json={'question':'test question'}).status_code==403
                    # Copy the encrypted cookie to confirm server revocation defeats cookie replay.
                    copied_cookies=httpx.Cookies(client.cookies)
                    csrf=client.get(frontend+'/api/auth/csrf').json()['csrfToken']
                    result=client.post(frontend+'/api/auth/signout',headers={'Origin':frontend,'X-Auth-Return-Redirect':'1'},
                        data={'csrfToken':csrf,'callbackUrl':frontend+'/login'})
                    assert result.status_code==200
                    assert client.get(frontend+'/api/campus/auth/me').status_code==401
                    with httpx.Client(cookies=copied_cookies,trust_env=False,timeout=30) as replay:
                        assert replay.get(frontend+'/api/campus/auth/me').status_code==401
                    # Demo uses a distinct Auth.js provider and is blocked from institution-only RAG.
                    csrf=client.get(frontend+'/api/auth/csrf').json()['csrfToken']
                    client.post(frontend+'/api/auth/callback/demo',headers={'Origin':frontend,'X-Auth-Return-Redirect':'1'},
                        data={'csrfToken':csrf,'callbackUrl':frontend+'/account'})
                    assert client.get(frontend+'/api/auth/session').json()['user']['is_demo'] is True
                    assert client.post(frontend+'/api/campus/retrieve',headers={'Origin':frontend},json={'question':'admission process'}).status_code==403
                print('PASS: Auth.js cookie login, secret non-disclosure, CSRF origin checks, RBAC, logout/replay and isolated demo.')
            except Exception:
                # Do not print raw logs: providers can place credentials or session details in diagnostics.
                print('Frontend authentication smoke test failed. Temporary server logs were not printed.')
                raise
            finally:
                import signal
                for p in processes:
                    if p.poll() is None: os.killpg(p.pid,signal.SIGTERM)
                for p in processes:
                    try:p.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(p.pid,signal.SIGKILL);p.wait()


if __name__=='__main__':main()
