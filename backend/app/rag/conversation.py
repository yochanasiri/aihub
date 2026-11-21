"""Generic context resolution for grounded RAG.

The language model rewrites the user's latest turn as a *search query*, never
answers campus facts. This module deliberately contains no company-, role-,
course-, document-, or field-specific dictionaries.
"""
from __future__ import annotations

import json
import re
from typing import Any

# Linguistic hints only; no document-domain or entity-specific keywords.
_REFERENCE = re.compile(r"\b(?:it|its|they|them|their|this|that|those|these|same|previous|above|former|latter|he|she|his|her)\b", re.I)
_CONTINUATION = re.compile(
    r"^\s*(?:and\b|also\b|but\b|what\s+about\b|how\s+about\b|for\b|about\b|then\b|instead\b|the\s+other\b|can\s+you\s+(?:explain|expand|continue)\b)",
    re.I,
)
_SOCIAL = re.compile(
    r"^\s*(?:hi+|hello+|hey+|good\s+(?:morning|evening|afternoon)|how\s+are\s+you|thanks|thank\s+you|bye|goodbye)\s*[!?., ]*$",
    re.I,
)

_SYSTEM = """You are the query-understanding component of a source-grounded document assistant.
You must NOT answer factual questions or make up facts. Your only job is to decide
whether the latest user message is social conversation, a document question, or an
unresolvable ambiguous reference, and to rewrite DOCUMENT questions as
self-contained retrieval questions.

Use the recent dialogue to resolve references to a document, section, person,
organization, object, role, attribute, date, and other constraints. This works
for ANY subject and ANY document, with no special document-specific rules.
For each turn:
- Keep the conversational subject and purpose unless the user changes them.
- If the user changes only a subtopic, role, or requested attribute, retain the
  other relevant context. An explicit new subject overrides the old subject.
- When the user asks for a complete section/list/table, retain that completeness
  requirement. Do NOT select only the first matching item.
- If the current message is a self-contained new question, do NOT import prior
  context just because it is available.
- If context cannot identify the referent confidently, return `clarify` with a
  concise clarification question instead of guessing.
- Never claim a document contains any fact. Rewriting is NOT answering.
- Do not introduce named entities or constraints not supported by the user's
  current message and conversational history.

Return ONLY one JSON object:
{\"route\":\"retrieve|social|clarify\",\"text\":\"...\"}
For retrieve, text is ONE complete standalone search question (not an answer).
For social, text is a brief, natural reply to the user's current conversational
message; respond flexibly, without a fixed canned greeting.
For clarify, text is one short question asking what the user meant.
"""


def _clean_history(history: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in (history or [])[-20:]:
        role = item.get('role')
        content = item.get('content')
        if role in {'user', 'assistant'} and isinstance(content, str) and content.strip():
            # Source quotations in previous answers can overwhelm query rewriting;
            # user questions carry the authoritative context.
            limit = 700 if role == 'user' else 240
            result.append({'role': role, 'content': content.strip()[:limit]})
    return result


def is_contextual_followup(question: str, history: list[dict[str, str]] | None) -> bool:
    """Domain-agnostic signal for a query dependent on preceding context."""
    if not history or not any(turn.get('role') == 'user' for turn in history):
        return False
    q = question.strip()
    if _REFERENCE.search(q) or _CONTINUATION.search(q):
        return True
    # Short fragments without ordinary question syntax may be contextual.
    # This is deliberately conservative: the model is the primary resolver.
    return len(q.split()) <= 3 and not re.match(r'^\s*(?:who|what|where|when|why|how|which|is|are|do|does|can)\b', q, re.I)


def resolve_followup(question: str, history: list[dict[str, str]] | None) -> str:
    """Safe offline fallback if the language model is inaccessible.

    Provide the last relevant user question as context, rather than inventing a
    specific entity/field or assuming that an ambiguous follow-up has one meaning.
    """
    cleaned = _clean_history(history)
    if not is_contextual_followup(question, cleaned):
        return question
    users = [turn['content'] for turn in cleaned if turn['role'] == 'user']
    # Multiple consecutive short follow-ups need a full anchor; include several
    # recent user turns, but avoid full assistant answers and fabricated rewrites.
    window = users[-5:]
    return 'Conversation context (user questions): ' + ' | '.join(window) + '\nLatest follow-up question: ' + question


def _validate_route(output: Any, latest: str, history: list[dict[str, str]]) -> tuple[str, str] | None:
    if not isinstance(output, dict):
        return None
    route = output.get('route')
    response = output.get('text')
    if route not in {'retrieve', 'social', 'clarify'} or not isinstance(response, str):
        return None
    response = response.strip()
    if not response or len(response) > 1800:
        return None
    if route == 'retrieve':
        # Reject obvious identity rewriting for self-contained queries only if
        # it discards all of the most distinctive terms (a safe basic guard).
        # For follow-ups, history supplies terms missing from the message.
        if not history and latest.strip().casefold() != response.casefold():
            original = set(re.findall(r'[\w]{4,}', latest.casefold()))
            rewritten = set(re.findall(r'[\w]{4,}', response.casefold()))
            if original and not original.intersection(rewritten):
                return None
    return route, response


async def route_turn(llm: Any, question: str, history: list[dict[str, str]] | None):
    """Return (route, reply_or_standalone_search_question).

    This runs once before RAG retrieval, using an Ollama/OpenAI-compatible local
    endpoint. The RAG pipeline must independently verify every factual answer.
    """
    turns = _clean_history(history)
    if llm is None:
        return ('clarify', 'Could you clarify what you are referring to?') if is_contextual_followup(question, turns) else ('retrieve', question)
    payload = json.dumps({'dialogue': turns, 'latest_user_message': question}, ensure_ascii=False)
    try:
        result = await llm.client.chat.completions.create(
            model=llm.config.llm_model,
            temperature=0,
            max_tokens=300,
            response_format={'type': 'json_object'},
            messages=[{'role': 'system', 'content': _SYSTEM}, {'role': 'user', 'content': payload}],
        )
        raw = result.choices[0].message.content or '{}'
        parsed = _validate_route(json.loads(raw), question, turns)
        if parsed:
            return parsed
    except (Exception):
        # Query classification errors should not take down source-grounded RAG.
        pass
    if is_contextual_followup(question, turns):
        # This fallback adds the original user context to retrieval; it does not
        # claim a particular reconstruction is correct.
        return 'retrieve', resolve_followup(question, turns)
    # Never fabricate a scripted social reply. Ask the user to retry when the
    # local conversational model is unavailable.
    if _SOCIAL.fullmatch(question.strip()):
        return 'social', 'I’m having trouble responding conversationally right now. Please try again.'
    return 'retrieve', question
