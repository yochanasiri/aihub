# Phase 2 evaluation

`questions.json` contains 30 sample campus questions, including one deliberately out-of-scope question. They are prompts, **not a gold factual answer set**. No corpus or model quality results have been fabricated.

## Run

After crawling, indexing and starting the local RAG API:

```bash
# Run from an operator shell with AUTH_BRIDGE_SECRET exported.
# --login prompts for a verified institute account and revokes the temporary session afterward.
python evaluation/run.py --login --url http://localhost:8000 --output evaluation/results.json
# For a quick smoke test:
python evaluation/run.py --login --limit 3 --output evaluation/results-smoke.json
```

The script makes a retrieval request followed by a streaming chat request for each question. These requests create conversations owned by the authenticated institute account and unanswered-query records. They do not send messages or escalations to anyone.

Alternatively, a deployment operator can provide an already-issued `AIHUB_SESSION_TOKEN` in the environment and omit `--login`. Never place it in a URL, command-line argument or report. The old shared `RAG_DEV_TOKEN` no longer authenticates requests. Demo sessions are not permitted for corpus evaluation.

## Label the dataset

For each question, review current official source content and fill:

- `gold_source_urls`: exact canonical URLs containing the answer. Retrieval source hit/recall are computed only when these labels exist.
- `expected_status`: `answered` or `unanswered`, based on the actual corpus, not what a generic model knows.
- `gold_answer_phrases`: short factual phrases expected in relevant excerpts, if appropriate. Phrase recall is not semantic accuracy.

Leave missing labels empty/null; the corresponding scores will remain null. Dates, cutoffs, packages and helplines must be checked against official sources before labeling.

## Report interpretation

- Source hit/recall at configured K measure retrieval against supplied URL labels.
- Verbatim quote rate checks that returned quotes occur in the retrieved parent text. It measures provenance, not relevance, completeness or correct interpretation.
- Answerability compares refusal/answer behavior with labels.
- Human relevance and completeness fields (0–2) are intentionally blank for review.
- p50/p95 embedding, retrieval, model-first-token, first-visible-excerpt and total latency are reported. Retrieval includes embedding; these are not additive independent stages.
- `evaluation_wall_ms` includes both requests and network overhead. Chat metrics cover the chat request only, starting after user-message persistence.
- The first visible excerpt follows quote verification and can be later than the model's first token. No unverified draft is streamed to the client.
- Empty/failed sample sets produce null metrics rather than misleading zero latency or 100% accuracy.
- A corpus update between retrieval and chat can create unmatched quote checks; rerun against a stable corpus.

The script exits with a nonzero status if any question encounters an API or stream failure. The report still preserves successful and failed cases.
