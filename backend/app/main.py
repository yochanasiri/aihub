from contextlib import asynccontextmanager
import logging
import asyncio
from fastapi import FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from .metrics import registry
from sqlalchemy import text
from .config import settings
from .db import engine, Session
from .rag.api import router
from .gemini import router as gemini_router
from .auth.api import router as auth_router
from .campus import router as campus_router
from .knowledge import router as knowledge_router
from .review import router as review_router
from .voice import Voice, router as voice_router
from .auth.service import AuthService
from .auth.google import GoogleVerifier
from .auth.mail import SMTPMailer


def create_app(config=settings, rag_service=None, auth_service=None):
    @asynccontextmanager
    async def lifespan(app):
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        if config.rag_api_enabled and app.state.rag_service is None:
            from .rag.embedding import BGEEmbedder
            from .rag.vector import VectorStore
            from .rag.generation import LocalLLM
            from .rag.service import RAGService
            embedder = BGEEmbedder(config)
            await embedder.warmup()
            app.state.rag_service = RAGService(config,Session,embedder,VectorStore(config,embedder.dim),LocalLLM(config))
        try:
            yield
        finally:
            if app.state.rag_service:
                await app.state.rag_service.close()
            if app.state.auth_service.google:
                app.state.auth_service.google.close()
            await engine.dispose()

    app = FastAPI(title="IIT Indore AI Campus Assistant — AIHUB",version="0.5.0",
                  docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.config, app.state.rag_service = config, rag_service
    app.state.auth_service = auth_service or AuthService(config,Session,GoogleVerifier(config),SMTPMailer(config))
    app.state.gemini_slots = asyncio.Semaphore(3)
    app.state.knowledge_ingest = asyncio.Semaphore(1)
    app.state.knowledge_index = asyncio.Semaphore(1)
    app.include_router(knowledge_router)
    app.include_router(review_router)
    app.state.voice = Voice(config)
    app.include_router(voice_router)
    app.include_router(auth_router)
    app.include_router(campus_router)
    app.include_router(router)
    app.include_router(gemini_router)

    if config.metrics_enabled:
        @app.get("/metrics")
        async def metrics():
            return Response(generate_latest(registry), headers={"Content-Type":CONTENT_TYPE_LATEST})

    @app.get("/health/live")
    async def live():
        return {"status":"alive","phase":5,"rag_enabled":config.rag_api_enabled}

    @app.get("/health/ready")
    async def ready():
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            if config.rag_api_enabled:
                service = app.state.rag_service
                if service is None or not await service.store.client.collection_exists(config.collection_name):
                    raise RuntimeError("Index not initialized")
                models = await service.llm.client.models.list()
                if config.llm_model not in {model.id for model in models.data}:
                    raise RuntimeError("Configured model is not available")
            return {"status":"ready","database":"connected","rag_enabled":config.rag_api_enabled}
        except Exception:
            raise HTTPException(503,"Required database, index or model is unavailable")

    return app


app = create_app()
