import asyncio
import json
import logging
from time import perf_counter
from urllib.parse import quote as urlquote
from uuid import uuid4
from sqlalchemy import select
from contextlib import aclosing
from ..knowledge_ingest import PREFIX
from .. import metrics as prometheus
from ..models import Conversation, Message, UnansweredQuery
from .generation import (REFUSAL, _amount_question, escape_markdown, exact_pdf_fee_table, exact_pdf_table_rows,
                         natural_source_answer, select_source_excerpt, verified_quotes, requires_full_answer)
from .retrieval import Retriever
from .focused import focused_quotes, structured_quotes, intent, entity, evidence_matches, structured_answer_markdown
from .cache import QuoteCache
from .conversation import route_turn
from .document_answers import exhaustive_question, select_full_document, document_answer

logger = logging.getLogger("aihub.rag")


class ConversationNotFound(Exception):
    pass


class RAGService:
    def __init__(self, config, sessions, embedder, store, llm, cache=None):
        self.config, self.sessions = config, sessions
        self.embedder, self.store, self.llm = embedder, store, llm
        self.retriever = Retriever(config, sessions, embedder, store)
        self.cache = cache if cache is not None else (QuoteCache(config) if config.answer_cache_enabled and config.redis_url else None)
        self.slots = asyncio.Semaphore(config.max_concurrent_chats)

    async def begin(self, actor_id, question, conversation_id=None):
        async with self.sessions() as session:
            if conversation_id:
                conversation = await session.scalar(select(Conversation).where(
                    Conversation.id == conversation_id, Conversation.user_id == actor_id))
                if not conversation:
                    raise ConversationNotFound()
            else:
                conversation = Conversation(user_id=actor_id, title=question[:100])
                session.add(conversation)
                await session.flush()
            message = Message(conversation_id=conversation.id, role="user", content=question, sources=[])
            session.add(message)
            await session.commit()
            return conversation.id, message.id

    async def select_quotes(self, question, evidence, metrics, cached, start):
        if cached:
            for quote in cached:
                yield quote
            return
        # Preserve the existing role-aware JD/eligibility extraction. Use the
        # document-wide path for generic lists, counts and full-table requests.
        full_pages = (select_full_document(evidence)
                      if exhaustive_question(question) and not intent(question) else [])
        if full_pages:
            result = await document_answer(self.llm, question, full_pages)
            if result:
                items, complete = result
                for number, item in enumerate(items, 1):
                    heading = ''
                    if number == 1 and len(items) > 1:
                        heading = (f'**Total verified entries: {len(items)}**\n\n' if complete
                                   else f'**{len(items)} verified entries (not confirmed complete)**\n\n')
                    yield type('DocumentQuote', (), {
                        'source_id':item.source_id,
                        'quote':item.quote,
                        'answer':heading + f'{number}. {escape_markdown(item.answer)}'
                    })()
            # If no verified items exist, do not fall through to title-only
            # snippets or unrelated pages and present those as the answer.
            return
        # A whole-section request must not pass through the single-quote LLM
        # path or the field-label shortcut: serve the original full section.
        section = structured_quotes(question, evidence)
        if section:
            for item in section:
                yield item
            return
        if intent(question):
            focused = focused_quotes(question, evidence)
            if focused:
                for item in focused:
                    yield item
                return
            # For explicit entity + field requests, never answer using a different
            # organization's results or unrelated matching tokens.
            if entity(question) or not any(evidence_matches(question, e) for e in evidence):
                return
        fee_quote = exact_pdf_fee_table(question, evidence)
        if fee_quote:
            yield fee_quote
            return
        exact_rows = exact_pdf_table_rows(question, evidence)
        if exact_rows:
            for row in exact_rows:
                yield row
            return
        pdf_only = all(item.source.get("doc_type") == "pdf" for item in evidence)
        selected = select_source_excerpt(question, evidence) if pdf_only and not requires_full_answer(question) else None
        if selected is not None:
            yield selected
            return
        # Amount questions must not turn a schedule notice into a fee quote.
        if pdf_only and _amount_question(question):
            return
        if self.llm is None:
            return
        def first_token():
            if metrics["model_first_token_ms"] is None:
                metrics["model_first_token_ms"] = round((perf_counter() - start) * 1000, 2)
        async for quote in verified_quotes(self.llm.tokens(question, evidence), evidence,
                                           first_token, question):
            yield quote

    async def events(self, actor_id, question, filters, conversation_id, user_message_id, *, tuning=None, persist=True):
        start, request_id = perf_counter(), str(uuid4())
        metrics = {"embedding_ms":None, "retrieval_ms":None, "model_first_token_ms":None,
                   "first_visible_ms":None, "total_ms":None, "cache_hit":False}
        outcome, answer, used, quotes = "error", "", {}, []
        original_question = question
        yield "start", {"request_id":request_id,"conversation_id":conversation_id,"user_message_id":user_message_id,
                        "answer_mode":"verified_excerpts"}
        try:
            history = []
            if persist and conversation_id:
                async with self.sessions() as session:
                    rows = (await session.scalars(select(Message).where(
                        Message.conversation_id == conversation_id, Message.id != user_message_id,
                        Message.role.in_(["user", "assistant"]))
                        .order_by(Message.created_at.desc(), Message.id.desc()).limit(24))).all()
                    history = [{"role": row.role, "content": row.content[:900] if row.role == "user" else row.content[:320]}
                               for row in reversed(rows)]
            # Keep the routing call bounded independently of retrieval and generation.
            try:
                async with asyncio.timeout(min(60, self.config.llm_timeout_seconds)):
                    route, routed = await route_turn(self.llm, question, history) if persist else ("retrieve", question)
            except TimeoutError:
                route, routed = "retrieve", question
            if route in {"social", "clarify"}:
                casual = routed
                response_id = None
                if persist:
                    async with self.sessions() as session:
                        response = Message(conversation_id=conversation_id, role="assistant", content=casual,
                                           sources=[], latency_ms=int((perf_counter()-start)*1000))
                        session.add(response)
                        await session.flush()
                        response_id = response.id
                        await session.commit()
                outcome = "answered"
                yield "delta", {"text": casual}
                yield "sources", {"sources": []}
                metrics["total_ms"] = round((perf_counter()-start)*1000,2)
                yield "done", {"request_id":request_id,"message_id":response_id,
                               "conversation_id":conversation_id,"status":outcome,"answer":casual,
                               "sources":[],"quotes":[],"metrics":metrics,"escalation_available":False,"casual":route == "social", "clarification":route == "clarify"}
                return
            question = routed
            # The rewrite is only used for retrieval, never as an answer.
            async with asyncio.timeout(self.config.llm_timeout_seconds):
                evidence, retrieval_metrics = await self.retriever.retrieve(
                    question, filters, tuning, min_top_k=5)
                metrics.update(retrieval_metrics)
                yield "retrieval", {"source_count":len(evidence), **retrieval_metrics,
                                    **({"candidates":[{**e.source,"text":e.text} for e in evidence]} if not persist else {})}
                cache_key = None
                if evidence:
                    cached = None
                    if persist and self.cache and not intent(question) and not exhaustive_question(question):
                        cache_key = self.cache.key(actor_id, question, evidence)
                        cached = await self.cache.get(cache_key, evidence)
                    metrics["cache_hit"] = cached is not None
                    async with aclosing(self.select_quotes(question, evidence, metrics, cached, start)) as selected:
                        async for verified in selected:
                            source = next(e.source for e in evidence if e.source_id == verified.source_id)
                            used[verified.source_id] = source
                            quotes.append({"source_id":verified.source_id,"text":verified.quote,
                                           "answer":verified.answer,"parent_id":source["parent_id"]})
                            link = urlquote(source["url"], safe=":/?=&%+@;,$-_.!~*")
                            if source.get("page") and not source["url"].startswith(PREFIX):
                                link += f"#page={source['page']}"
                            prose = verified.answer or natural_source_answer(verified.quote, question)
                            if ':fullpage:' in source.get('parent_id', ''):
                                block = f"{prose} [{verified.source_id}]({link})\n\n"
                            elif intent(question):
                                block = f"{structured_answer_markdown(prose)} [{verified.source_id}]({link})\n\n"
                            else:
                                block = (f"{escape_markdown(prose)} [{verified.source_id}]({link})\n\n"
                                         f"> {escape_markdown(verified.quote)}\n\n")
                            if not answer:
                                block = "**Information from IIT Indore Sources**\n\n" + block
                            answer += block
                            if metrics["first_visible_ms"] is None:
                                metrics["first_visible_ms"] = round((perf_counter()-start)*1000,2)
                            yield "delta", {"text":block}
                if not answer:
                    answer, outcome = REFUSAL, "unanswered"
                    metrics["first_visible_ms"] = round((perf_counter()-start)*1000,2)
                    yield "delta", {"text":answer}
                else:
                    outcome = "answered"
                sources = list(used.values())
                response_id = None
                if persist:
                    async with self.sessions() as session:
                        response = Message(conversation_id=conversation_id, role="assistant", content=answer,
                                           sources=sources, latency_ms=int((perf_counter()-start)*1000))
                        session.add(response)
                        await session.flush()
                        if outcome == "unanswered":
                            session.add(UnansweredQuery(user_id=actor_id,message_id=response.id,query=original_question,
                                reason="no_current_evidence" if not evidence else "no_verified_answer_excerpt"))
                        await session.commit()
                        response_id = response.id
                if cache_key and quotes and not metrics["cache_hit"]:
                    await self.cache.put(cache_key, quotes)
                metrics["total_ms"] = round((perf_counter()-start)*1000,2)
                yield "sources", {"sources":sources}
                yield "done", {"request_id":request_id,"message_id":response_id,"conversation_id":conversation_id,
                               "status":outcome,"answer":answer,"sources":sources,"quotes":quotes,"metrics":metrics,
                               "escalation_available":persist and outcome == "unanswered"}
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except TimeoutError:
            logger.warning(json.dumps({"event":"rag_timeout","request_id":request_id,
                                       "elapsed_ms":round((perf_counter()-start)*1000)}))
            yield "error", {"code":"rag_timeout",
                            "message":"The campus model timed out. Try again shortly, or use Gemini AI chat for a general answer.",
                            "discard_partial":True,"request_id":request_id}
        except Exception as exc:
            # Log the failure type and stack on the server only, not the user's source data.
            logger.exception("rag_request_failed request_id=%s error_type=%s",request_id,type(exc).__name__)
            yield "error", {"code":"rag_unavailable","message":"The knowledge service is temporarily unavailable. Check backend logs and retry.",
                            "discard_partial":True,"request_id":request_id}
        finally:
            metrics["total_ms"] = round((perf_counter()-start)*1000,2)
            if self.config.metrics_enabled:
                mode = "chat" if persist else "preview"
                prometheus.requests.labels(outcome, mode).inc()
                prometheus.latency.labels(mode).observe(metrics["total_ms"]/1000)
                if metrics["cache_hit"]:
                    prometheus.hits.inc()
            logger.info(json.dumps({"event":"rag_request","request_id":request_id,"outcome":outcome,**metrics}))

    async def close(self):
        if self.cache:
            await self.cache.close()
        await self.store.close()
        await self.llm.close()
