"""Browser UI integration. Real auth/backend; explicitly synthetic chat-stream fixture.
Run with Node 22+, npm ci, npx playwright install chromium, and Python test deps.
AIHUB_BROWSER_PATH optionally selects an already installed Chromium executable.
"""
import asyncio
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import tempfile
import httpx
from frontend_auth_smoke import ROOT, free_port, seed, wait_ready


def main():
    with tempfile.TemporaryDirectory(prefix='aihub-ui-smoke-') as temporary:
        folder=Path(temporary);backend_port,frontend_port=free_port(),free_port()
        password='smoke-'+secrets.token_urlsafe(24)
        backend=f'http://127.0.0.1:{backend_port}';frontend=f'http://127.0.0.1:{frontend_port}'
        dsn=f'sqlite+aiosqlite:///{folder}/test.db';asyncio.run(seed(dsn,password))
        environ={**os.environ,'DATABASE_URL':dsn,'AUTH_ENABLED':'true','RAG_API_ENABLED':'false',
                 'AUTH_BRIDGE_SECRET':secrets.token_urlsafe(40),'AUTH_SECRET':secrets.token_urlsafe(40),
                 'AUTH_URL':frontend,'BACKEND_URL':backend,'GOOGLE_CLIENT_ID':'','GOOGLE_CLIENT_SECRET':'',
                 'DEMO_LOGIN_ENABLED':'true','NEXT_TELEMETRY_DISABLED':'1','NODE_ENV':'development'}
        processes=[]
        with (folder/'server.log').open('w') as logs:
            try:
                processes.append(subprocess.Popen([sys.executable,'-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port',str(backend_port)],cwd=ROOT,env=environ,stdout=logs,stderr=logs,start_new_session=True))
                processes.append(subprocess.Popen(['node',str(ROOT/'frontend/node_modules/next/dist/bin/next'),'dev','--hostname','127.0.0.1','--port',str(frontend_port)],cwd=ROOT/'frontend',env=environ,stdout=logs,stderr=logs,start_new_session=True))
                with httpx.Client(trust_env=False,timeout=30) as client:
                    wait_ready(client,backend+'/health/live');wait_ready(client,frontend+'/login')
                result=subprocess.run(['node',os.environ.get('UI_SMOKE_SCRIPT','tests/ui-smoke.mjs')],cwd=ROOT/'frontend',env=environ,text=True,
                    input=json.dumps({'base':frontend,'password':password,'screenshots':str(ROOT/'docs/previews')}),capture_output=True,timeout=180)
                if result.returncode:
                    # Browser assertion messages contain only fixture content. Server auth logs are never printed.
                    print(result.stdout);print(result.stderr);raise RuntimeError('Browser smoke test failed')
                print(result.stdout.strip())
            finally:
                for p in processes:
                    if p.poll() is None:os.killpg(p.pid,signal.SIGTERM)
                for p in processes:
                    try:p.wait(timeout=10)
                    except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()

if __name__=='__main__':main()
