# AIHUB frontend · Phase 4 campus workspace

Implemented with Next.js App Router, TypeScript, Tailwind, shadcn-style components and Auth.js:

- Google OAuth with state, nonce and PKCE; backend-verifiable signed ID-token exchange.
- Username/email + password sign-in and institute email verification registration.
- Encrypted HttpOnly cookies carrying a revocable PostgreSQL-backed opaque session.
- Same-origin API proxy that never exposes backend or Google tokens to frontend JavaScript.
- Environment-controlled, isolated student demo identity.
- Navy/gold/blue login and responsive campus workspace, mobile navigation, verified streaming answers and source cards.
- Private recent conversations, paginated messages, source-based notices, campus map and contact pages.

Voice, feedback submission, escalation and source-management actions are labeled as upcoming Phase 5 features. Demo login streams labeled illustrative answers locally without accessing institutional knowledge or invoking a model. See the root README for UI screenshots and test instructions.

## Run

```bash
cp .env.example .env.local
# Configure AUTH_URL, a random AUTH_SECRET, and the backend's AUTH_BRIDGE_SECRET.
# Configure Google credentials and/or the backend's SMTP settings.
npm ci
npm run dev
```

Open http://localhost:3000/login. Backend must have `AUTH_ENABLED=true` and its database migrations applied. See `../backend/app/auth/README.md` for OAuth redirect URLs, verification, RBAC, admin bootstrap, cookie policy and deployment requirements.

`npm run typecheck` and `npm run build` verify TypeScript and the production bundle. Production runtime requires an HTTPS AUTH_URL and a proxy preserving that exact Host. `AUTH_SECRET`, `AUTH_BRIDGE_SECRET`, and Google credentials belong only in server environments; no credential uses a NEXT_PUBLIC prefix.

The `backendToken` field is present only in the encrypted cookie payload accessible to the Next.js server. It is explicitly excluded from the public session callback. The backend remains the authority for every role and session decision.
