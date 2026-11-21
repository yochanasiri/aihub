# AIHUB simulated answer demo

This update adds browser-only sample answers to Instant Student Demo Login. It demonstrates streamed text, formatted sections, example portal links and Stop Response without Google OAuth, crawling, embeddings or a local LLM. Responses are explicitly labeled simulated and are not retrieved campus facts. They are not saved to the database.

Apply to your existing extracted `aihub` folder:

1. Stop the frontend terminal with Ctrl+C.
2. Extract this update archive into that `aihub` folder, merging `frontend` and overwriting the three included source files. No environment files are included.
3. Keep `DEMO_LOGIN_ENABLED=true` in both `.env` and `frontend/.env.local`. The existing backend must be running with `AUTH_ENABLED=true`; `RAG_API_ENABLED=false` is sufficient.
4. From `aihub/frontend`, run `npm run dev`.
5. Open http://localhost:3000/login and click Instant Student Demo Login.
6. Click any suggestion, then Send. Try a hostel or placement question to see a different sample. Stop Response cancels the animation. New Conversation clears the current preview.

Only three source files change:
- frontend/lib/demo.ts (new)
- frontend/components/campus/chat.tsx
- frontend/components/campus/shell.tsx

No new npm dependencies or Docker rebuild are required. Existing `.env` and `.env.local` files are preserved. An institute login still uses the real backend chat API; only demo identities get these local sample responses.
