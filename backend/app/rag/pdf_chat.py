"""Answer questions from request-scoped PDF passages."""
import asyncio
from types import SimpleNamespace
from urllib.parse import unquote

import numpy as np
import openai
import pymupdf
from fastapi import HTTPException

from crawler.extract import extract_pdf
from .generation import escape_markdown, verified_quotes

PDF_LIMIT = 10 * 1024 * 1024


def pdf_windows(data, embedder):
    if not data.startswith(b"%PDF-"):
        raise ValueError("Select a valid PDF")
    try:
        document = extract_pdf(data, "attachment.pdf", 100)
    except (pymupdf.FileDataError, RuntimeError, ValueError) as exc:
        raise ValueError("Use an unencrypted PDF with selectable text and at most 100 pages") from exc
    passages = []
    for page in document.metadata["pages"]:
        passages.extend((page["page"], text) for text in embedder.windows(page["text"]))
        if len(passages) > 600:
            raise ValueError("The PDF contains too much text. Upload a shorter section")
    return passages


async def answer(service, data, filename, question):
    await service.embedder.validate_query(question)
    passages = await asyncio.to_thread(pdf_windows, data, service.embedder)
    vectors = np.asarray(await service.embedder.private_passages([text for _, text in passages]))
    query = np.asarray(await service.embedder.query(question))
    ranked = np.argsort(-(vectors @ query), kind="stable")[:8]
    evidence = [
        SimpleNamespace(source_id=f"S{i}", text=passages[idx][1], prompt_text=passages[idx][1],
                        source={"id": f"S{i}", "title": filename, "url": "", "page": passages[idx][0]})
        for i, idx in enumerate(ranked, 1)
    ]
    answers, sources = [], {}
    tokens = service.llm.tokens(question, evidence)
    try:
        async for quote in verified_quotes(tokens, evidence, lambda: None):
            source = next(item.source for item in evidence if item.source_id == quote.source_id)
            sources[quote.source_id] = source
            answers.append(f"{escape_markdown(quote.answer or quote.quote)} (p. {source['page']})")
    finally:
        await tokens.aclose()
    return {"answer": "\n\n".join(answers) or "I could not find an answer supported by this PDF.",
            "sources": list(sources.values())}


async def handle_pdf(service, request):
    if request.headers.get("content-type") != "application/octet-stream":
        raise HTTPException(415, "Upload a PDF file")
    question = unquote(request.headers.get("x-aihub-question", "")).strip()
    if not 2 <= len(question) <= 2000:
        raise HTTPException(422, "Enter a question between 2 and 2000 characters")
    filename = unquote(request.headers.get("x-aihub-filename", "")).strip()[:180] or "Uploaded PDF"
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > PDF_LIMIT:
            raise HTTPException(413, "The PDF exceeds 10 MiB")
        data.extend(chunk)
    try:
        await asyncio.wait_for(service.slots.acquire(), timeout=8)
    except TimeoutError as exc:
        raise HTTPException(429, "The campus model is busy. Please retry") from exc
    try:
        async with asyncio.timeout(service.config.llm_timeout_seconds):
            return await answer(service, bytes(data), filename, question)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (TimeoutError, openai.APIError) as exc:
        raise HTTPException(503, "Local PDF answering is temporarily unavailable") from exc
    finally:
        service.slots.release()
