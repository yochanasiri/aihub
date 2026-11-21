# RAG implementation and protocol

## Data path

1. Crawler stores validated official documents in PostgreSQL.
2. `python -m backend.app.rag.index` uses LlamaIndex SentenceSplitter and TextNode objects for heading/page-aware parent chunks, normally up to 700 BGE wordpiece tokens with 64-token overlap within a section. Short sections remain short rather than mixing unrelated headings.
3. The parent text is subdivided into <=460-token embedding windows with 32-token overlap. FastEmbed runs BGE locally; **no long parent chunk is silently truncated to BGE's 512-token limit**.
4. Qdrant stores one vector per search window, plus original parent text, source URL, document revision, heading/page and filter metadata. HNSW uses m=16 / ef_construct=100. Payload indexes are created before ingestion.
5. Query embeddings retrieve child windows. Parent chunks are deduplicated and bounded by top-K and the context budget. SQL revalidation rejects removed, stale, expired, changed, incompletely indexed or newly disallowed sources. Retrieved parent text must also occur in the stored source text (and the cited page for PDFs), preventing a corrupted payload from borrowing a legitimate document ID.
6. Local Ollama/vLLM inference selects verbatim supporting excerpts. Each JSON-line response is checked for a valid source ID and an exact quote from that source before a visible block is emitted.
7. Accepted quotes become markdown blockquotes with server-generated official source links. Sources, assistant messages and unanswered cases are persisted. SQL failure produces a stream error, not a successful completion event.

## Grounding choice

Phase 2 defaults to **verified extractive answers**. The model selects relevant text; it cannot publish a freely generated factual sentence. This deliberately prioritizes the user's official-content-only requirement. Text copied from a page is rendered as escaped quoted content, never as executable HTML, a tool instruction or an arbitrary model-generated link.

This does **not** guarantee that a selected quotation is relevant, complete, internally consistent or factually current. Similarity scores are not confidence probabilities; calibrate the threshold with the evaluator. Official pages can themselves be wrong or outdated. Review the sources and label evaluation cases before deploying to students.

Model tokens are streamed from the local OpenAI-compatible endpoint, buffered into JSON lines, verified, and sent to the frontend as completed evidence blocks. This is incremental SSE streaming, **not raw-token display**. No invented intermediate text is shown. If no quotation passes verification, the response is exactly:

> I couldn't find this in official IIT Indore sources

Only accepted quotations appear in the source list. If no usable context exists, the model is not called. Malformed JSON, invented quotes and unknown citation IDs are rejected. An inference outage is a separate error, not evidence that the corpus lacks an answer.

## Endpoints

Both require `RAG_API_ENABLED=true` and a current verified institute session. The Phase 2 shared development token has been removed. Browser clients use the same-origin Next.js `/api/campus/retrieve` and `/api/campus/chat/stream` proxy with the Auth.js HttpOnly cookie; the backend token stays server-side. Trusted operator clients can use `Authorization: Bearer <individual-session-token>`. Demo users are denied access. See `../auth/README.md` for the full login/session flow.

`POST /v1/retrieve`

```json
{"question":"What is the B.Tech admission process?","filters":{"department":"Academics","doc_type":"html"}}
```

Returns parent text, authoritative source metadata, scores, index signature and stage timings. Optional filters: `department`, `doc_type` (`html`/`pdf`), exact canonical `source_url`, timezone-aware `date_from`/`date_to`. **Date means when a content change was detected, not the source's publication date**; the crawler does not reliably extract publication dates yet.

`POST /v1/chat/stream`

```json
{"question":"What is the B.Tech admission process?","conversation_id":null,"filters":{}}
```

Omit the conversation ID to create a new conversation. Supplied IDs must belong to the authenticated actor. Questions are interpreted as standalone requests in Phase 2; previous messages are stored but not fed into inference. Contextual follow-up resolution is not implemented.

### SSE events

| Event | Meaning |
|---|---|
| `start` | Request/conversation/user-message IDs; `answer_mode=verified_excerpts` |
| `retrieval` | Current source count and embedding/retrieval timing |
| `delta` | Append this verified markdown block to the visible response |
| `sources` | Accepted official source metadata and URLs |
| `done` | Persisted assistant ID, canonical complete answer, quotes, status and final timings |
| `error` | Request failed; discard any partial answer when `discard_partial=true`; no successful `done` follows |

SSE comments (`: keepalive`) arrive while inference is busy. The frontend must use streaming `fetch` because the endpoint is POST and uses an HttpOnly session cookie through the same-origin proxy; native EventSource alone is insufficient. Stop/cancel with AbortController. Backpressure uses a bounded queue, and disconnection cancels inference and frees the slot. Partial/cancelled responses are not saved as completed assistant messages; the original user message remains.

The API does not yet expose escalation submission. `escalation_available=false` is explicit until Phase 5, although unanswered queries are already recorded. There is no model-selected recipient, notification or email action.

## Reconciliation and invalidation

The collection name includes a fingerprint of model name, chunk size/overlap, search window size/overlap and pipeline version. Changing those settings creates a separate collection and requires indexing. Old collections are retained until an operator deliberately removes them after verifying migration.

Point IDs are deterministic UUIDs for document revision + parent + child. Upserts are idempotent. New points are uploaded before obsolete revisions are deleted. The SQL `needs_indexing` flag is cleared only after a successful vector operation. Retries can repair partial writes. `--force` reconciles all documents. A newly recreated collection forces a full reindex even when database flags are clear.

Crawler and index CLI share the same PostgreSQL advisory lock. Each document is also row-locked while indexing. Unsupported deployment access patterns that bypass these jobs must preserve the same coordination.

Source aliases retain separate provenance/filter payloads. Identical search text reuses a bounded in-process embedding cache; retrieval collapses identical parent text. This uses more vector points than a single canonical point, but avoids losing department or exact-URL filters for aliases. This is **embedding reuse**, not Redis Q&A caching (Phase 5).

If you change code affecting splitting or embeddings without changing settings, bump the pipeline version in `Settings.index_signature` and reindex. Qdrant snapshots alone are not the authoritative source of current eligibility: PostgreSQL and the current allowlist are rechecked per request.

## Latency and limits

- Inference slots are bounded; chat overload returns HTTP 429 with Retry-After.
- Embedding work is offloaded from the event loop and serialized around the ONNX model.
- Empty retrieval avoids an LLM call.
- Inference has a total timeout and output/line/quote limits; errors do not reveal provider details.
- Operational logs contain request IDs, status, error type and timings, not raw questions or source text.
- `model_first_token_ms` is upstream generation time; `first_visible_ms` includes quotation validation; `retrieval_ms` includes embedding. `total_ms` includes answer persistence. These are server measurements, not a low-latency benchmark.
- Real latency depends on CPU/GPU, model quantization, prompt size and corpus. No hardware performance target has been claimed from fixture tests.
