"""Loopback-only AIHUB rehearsal: real upload, index, Auth.js and chat; fixture models."""
import argparse
import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import tempfile
import time

import httpx

from frontend_auth_smoke import ROOT, free_port, wait_ready

ADMISSION = "Sample admission applications close on 15 June. This is fictional rehearsal data."
HOSTEL = "Sample hostel check-in opens at 9:00 a.m. This is fictional rehearsal data."
QUESTIONS = [
    ("When do sample admission applications close?", "15 June", "Rehearsal admissions.pdf"),
    ("When does sample hostel check-in open?", "9:00 a.m.", "Rehearsal hostel.txt"),
]


def serve(port):
    """Only invoked as a child of this script, against a fresh temporary database."""
    import uvicorn
    from qdrant_client import AsyncQdrantClient
    from backend.app.auth.service import AuthService
    from backend.app.config import settings
    from backend.app.db import Session
    from backend.app.main import create_app
    from backend.app.rag.service import RAGService
    from backend.app.rag.vector import VectorStore
    from test_rag import TestEmbedder, TestLLM

    if not settings.database_url.startswith("sqlite+aiosqlite:///"):
        raise RuntimeError("The rehearsal requires its isolated SQLite database")
    embedder = TestEmbedder()
    llm = TestLLM()

    class ModelList:
        async def list(self):
            from types import SimpleNamespace
            return SimpleNamespace(data=[SimpleNamespace(id=settings.llm_model)])

    from types import SimpleNamespace
    llm.client = SimpleNamespace(models=ModelList())
    store = VectorStore(settings, embedder.dim, AsyncQdrantClient(":memory:"))
    app = create_app(settings, RAGService(settings, Session, embedder, store, llm),
                     AuthService(settings, Session, None, None))
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False)


async def seed(dsn, password):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from backend.app.auth.security import hash_password
    from backend.app.models import Base, User

    engine = create_async_engine(dsn)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine)() as session:
            digest = await hash_password(password)
            for role in ("Admin", "Student"):
                key = "rehearsal_" + role.lower()
                session.add(User(email=key+"@iiti.ac.in", username=key, name="Rehearsal "+role,
                                 role=role, email_verified=True, is_demo=False, password_hash=digest))
            await session.commit()
    finally:
        await engine.dispose()


def login(client, base, role, password):
    csrf = client.get(base+"/api/auth/csrf").raise_for_status().json()["csrfToken"]
    response = client.post(base+"/api/auth/callback/credentials",
        headers={"Origin":base, "X-Auth-Return-Redirect":"1"},
        data={"csrfToken":csrf,"identifier":"rehearsal_"+role.lower(),
              "password":password,"callbackUrl":base+"/account"})
    response.raise_for_status()
    session = client.get(base+"/api/auth/session").raise_for_status().json()
    assert session.get("user", {}).get("role") == role, "Rehearsal sign-in failed"


def populate(base, password):
    from urllib.parse import quote
    import pymupdf

    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((36, 70), ADMISSION, fontsize=10)
        files = [(QUESTIONS[0][2], pdf.tobytes()), (QUESTIONS[1][2], HOSTEL.encode())]
    with httpx.Client(trust_env=False, timeout=90) as admin, httpx.Client(trust_env=False, timeout=90) as student:
        login(admin, base, "Admin", password)
        login(student, base, "Student", password)
        assert "Submission rehearsal" in student.get(base+"/account").raise_for_status().text
        for title, raw in files:
            response = admin.post(base+"/api/campus/admin/knowledge/upload",
                headers={"Origin":base,"Content-Type":"application/octet-stream","X-AIHUB-Filename":quote(title)},content=raw)
            response.raise_for_status()
            document = response.json()
            assert not document["published"]
            source = base+"/api/campus/knowledge/"+document["id"]
            assert student.get(source).status_code == 404, "Draft source was exposed"
            admin.post(base+"/api/campus/admin/knowledge/"+document["id"]+"/publish",
                       headers={"Origin":base},json={}).raise_for_status()
            assert student.get(source).status_code == 200
        for question, expected, title in QUESTIONS + [("What is the cafeteria menu?", None, None)]:
            response = student.post(base+"/api/campus/chat/stream", headers={"Origin":base}, json={"question":question})
            response.raise_for_status()
            match = next((line for line in response.text.split("\n\n") if line.startswith("event: done\n")), None)
            assert match, "Chat did not complete its stream"
            result = json.loads(match.split("\ndata: ",1)[1])
            if expected:
                assert result["status"] == "answered" and any(expected in q["text"] for q in result["quotes"]), result["answer"]
                source = next(s for s in result["sources"] if s["title"] == title)
                assert expected in student.get(base+source["url"]).raise_for_status().text
                if title.endswith(".pdf"):
                    assert source["page"] == 1
            else:
                assert result["status"] == "unanswered" and not result["sources"]
            print(f"PASS: {question} -> {expected or 'refused; no matching source'}" + (f" [{title}]" if title else ""), flush=True)


def stop_process(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def run(check):
    next_bin = ROOT/"frontend"/"node_modules"/"next"/"dist"/"bin"/"next"
    if not next_bin.is_file():
        raise SystemExit("Install the frontend dependencies first: cd frontend, then npm ci")
    with tempfile.TemporaryDirectory(prefix="aihub-rehearsal-") as temporary, ExitStack() as stack:
        folder = Path(temporary)
        backend_port, frontend_port = free_port(), free_port()
        while frontend_port == backend_port:
            frontend_port = free_port()
        base = f"http://127.0.0.1:{frontend_port}"
        backend = f"http://127.0.0.1:{backend_port}"
        dsn = "sqlite+aiosqlite:///"+(folder/"rehearsal.sqlite").as_posix()
        password = "rehearsal-"+secrets.token_urlsafe(18)
        env = {**os.environ,"PYTHONPATH":str(ROOT),"DATABASE_URL":dsn,
               "AUTH_ENABLED":"true","AUTH_BRIDGE_SECRET":secrets.token_urlsafe(40),
               "RAG_API_ENABLED":"true","RETRIEVAL_MIN_SCORE":"0.6",
               "DEMO_LOGIN_ENABLED":"false","VOICE_ENABLED":"false","ANSWER_CACHE_ENABLED":"false",
               "GOOGLE_CLIENT_ID":"","GOOGLE_CLIENT_SECRET":"","AUTH_SECRET":secrets.token_urlsafe(40),
               "AUTH_URL":base,"BACKEND_URL":backend,"NODE_ENV":"development",
               "NEXT_TELEMETRY_DISABLED":"1","NEXT_PUBLIC_AIHUB_REHEARSAL":"true"}
        asyncio.run(seed(dsn, password))
        log = stack.enter_context((folder/"servers.log").open("w"))
        processes = []
        try:
            commands = [
                ([sys.executable,str(Path(__file__).resolve()),"--serve",str(backend_port)], ROOT),
                (["node",str(next_bin),"dev","--hostname","127.0.0.1","--port",str(frontend_port)],ROOT/"frontend"),
            ]
            for command, cwd in commands:
                process = subprocess.Popen(command,cwd=cwd,env=env,stdout=log,stderr=log,
                                           start_new_session=(os.name!="nt"))
                processes.append(process)
            with httpx.Client(trust_env=False, timeout=30) as client:
                wait_ready(client,backend+"/health/live")
                wait_ready(client,base+"/login")
            populate(base,password)
            if not check:
                print(f"Open {base}/login\nTemporary usernames: rehearsal_student or rehearsal_admin\nTemporary password: {password}",flush=True)
                print("Fixture models and fictional documents only. Leave this terminal open; Ctrl+C stops it.",flush=True)
                while all(p.poll() is None for p in processes):
                    time.sleep(1)
                raise RuntimeError("A rehearsal server stopped")
        except KeyboardInterrupt:
            print("\nStopping rehearsal.")
        except Exception:
            print("Rehearsal failed. Check Node/Python dependencies and stop any other Next.js dev server for this folder.")
            raise
        finally:
            for process in reversed(processes):
                stop_process(process)
            log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Run the assertions and stop")
    parser.add_argument("--serve", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    sys.path.insert(0,str(ROOT))
    if args.serve is not None:
        serve(args.serve)
    else:
        run(args.check)
