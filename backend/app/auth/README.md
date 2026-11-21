# Phase 3 · Institute authentication and RBAC

## Architecture

The browser authenticates through Auth.js in Next.js. Google, password and optional demo sign-in all obtain an opaque session token from FastAPI. Auth.js stores this token only inside its encrypted **HttpOnly** session cookie. The public `/api/auth/session` response includes name/email/role/demo status, never the opaque backend token or Google tokens.

The browser uses same-origin `/api/campus/...` routes. Those routes decrypt the Auth.js cookie on the server, forward its opaque session token to FastAPI and stream the response. They accept a fixed path/method allowlist, not an arbitrary proxy URL. Browser-supplied authorization headers cannot override the cookie's identity. Mutating campus and registration requests require the configured exact Origin. Auth.js manages CSRF for its own login/logout endpoints.

The opaque token is stored **only as SHA-256** in PostgreSQL `auth_sessions`, with user ID, method, expiry and revocation time. High-entropy tokens are generated with Python's `secrets` module. FastAPI checks the current database user and role on every request. A role forged in a cookie/session-update request cannot grant privileges. Role changes revoke existing sessions.

Auth.js uses its encrypted JWT cookie strategy because its Credentials provider does not implement adapter-managed database sessions. The PostgreSQL opaque session is the authoritative session record; the cookie merely carries the credential securely between the browser and Next.js. Cookie renewal never extends the backend session's absolute expiry. Normal sessions last 8 hours by default; demo sessions last 1 hour.

## Configuration and startup

1. Apply the migrations: `alembic -c backend/alembic.ini upgrade head`.
2. Set backend `.env`: `AUTH_ENABLED=true` and a random `AUTH_BRIDGE_SECRET` of at least 32 characters.
3. Copy `frontend/.env.example` to `frontend/.env.local`. Set the **same** bridge secret and a **different** random `AUTH_SECRET` for cookie encryption. Never prefix either with `NEXT_PUBLIC_`.
4. Set `AUTH_URL=http://localhost:3000` for local development and `BACKEND_URL=http://127.0.0.1:8000`. In production, `AUTH_URL` must be HTTPS; requests must preserve that exact Host through your reverse proxy.
5. Run the backend and, from `frontend`, `npm ci && npm run dev`.
6. Open `http://localhost:3000/login`.

Generate each secret independently:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(40))'
```

The frontend rejects identical/short secrets. HTTPS uses `__Host-aihub.session` with Secure, HttpOnly, SameSite=Lax, Path=/ and no Domain attribute. HTTP development uses a separate `aihub.session` cookie without Secure. Production HTTP configuration is rejected. Configure HTTPS at your reverse proxy in Phase 6; no TLS certificate or public deployment is claimed here.

The backend is bound to localhost in the supplied Compose file. Keep it on a private network when connecting Next.js in a deployment. The bridge secret authenticates **server-to-server login/registration calls**, not ordinary users. Backend business endpoints require individual sessions and do not accept the bridge secret as user identity.

## Google OAuth

In the institute-approved Google Cloud project, create a Web OAuth client. Configure:

- Authorized redirect URI: `http://localhost:3000/api/auth/callback/google` locally; `https://your-host/api/auth/callback/google` in production.
- The matching `GOOGLE_CLIENT_ID` in backend and frontend environments.
- `GOOGLE_CLIENT_SECRET` only in the frontend server environment.
- An internal consent audience if the institute's Workspace administrator enables it.

Auth.js requests `openid email profile`, with PKCE, state and nonce checks. The `hd=iiti.ac.in` authorization parameter improves the account-picker experience but is **not** treated as authorization.

FastAPI independently verifies Google's signed ID token through `google-auth`: certificate signature, exact client audience, authorized-party claim when present, issuer, issue time and expiry. It then requires boolean `email_verified=true`, `hd` exactly `iiti.ac.in`, an exact normalized `@iiti.ac.in` email and a valid Google `sub`. Google certificate responses are cached and requests have a bounded timeout. A Google ID token can be exchanged only once; repeated token hashes are rejected.

Accounts are located by Google `sub`, then by verified institute email for safe linking. An unverified/different Google identity is never automatically merged. Pending password registrations cannot overwrite an account subsequently created through Google. New accounts receive **Student**; Faculty/Staff/Admin assignments require an authorized administrator. If a known Google subject's email changes, administrative review is required instead of silently moving its identity.

No Google application credentials were provided in this session. Real Google authorization/consent has therefore not been exercised; local cryptographic tests use generated RSA keys and an injected certificate response.

## Email/password registration

- Institute email domain is exact; deceptive suffixes, unapproved subdomains and malformed addresses are rejected.
- Username is normalized to lowercase and restricted to 3–40 ASCII letters/digits/underscores, starting with a letter.
- Password length is 12–128 characters. Password hashes use Argon2id (64 MiB, 3 iterations, parallelism 2), with a random per-password salt. Hashing is offloaded and concurrency-bounded.
- Registration creates a **pending record**, not an authorized user. An institute verification code is sent through your SMTP server; no paid service is required.
- Six-digit codes expire in 15 minutes, allow at most 5 failed attempts, are single-use and are stored as a keyed hash bound to the registration ID. The user must also supply the original registration password when confirming a code, protecting against unsolicited/pre-created registrations.
- After verification, the user signs in with username or email. No role field is accepted from registration input. Duplicate account registration responses do not expose whether the email already exists.
- Login performs a dummy Argon2 verification for missing accounts and checks verification/active/institute status before issuing a session.

Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, and either `SMTP_STARTTLS=true` or `SMTP_SSL=true`. TLS certificate verification remains enabled. For a self-hosted local mail-capture service only, you can use port 1025 and disable both TLS options. Do not disable TLS for real institute-mail transport. There is no stdout fallback printing real verification codes.

SMTP delivery was tested with an injected recording mailer, not a real institute mailbox. No email was sent during implementation. Password reset/recovery is not yet implemented; it was outside this phase's requested login/registration scope.

## RBAC and session lifecycle

| Endpoint | Access |
|---|---|
| `/v1/auth/register`, `/verify-email`, `/password`, `/google`, `/demo` | Trusted Next.js bridge; no ambient browser-cookie authentication |
| `/v1/auth/me` | Current active session, including a permitted demo session |
| `/v1/retrieve`, `/v1/chat/stream` | Verified active institute user; demo denied |
| `/v1/admin/access`, `/v1/admin/users/{id}/role` | Current Admin only; demo denied |
| `/v1/auth/logout` | Revokes the presented opaque session token |

`require_roles(...)` provides the reusable Faculty/Staff/Admin policy dependency for future protected routes. User/assistant conversations remain scoped to their database owner. No UI-only role check is treated as authorization.

Bootstrap the first administrator through an operator-controlled backend shell, after that person has verified/sign-in-created their institute account:

```bash
python -m backend.app.auth.manage grant-admin person@iiti.ac.in
```

This does not create an account or verify its mailbox. It writes an audit record and revokes the user's sessions; sign in again to gain Admin access. Subsequent role changes use the admin-only API. The last active Admin cannot be demoted through it. A PostgreSQL advisory transaction lock serializes role administration.

Logout clears the Auth.js cookie and attempts to revoke the backend record. A copied cookie cannot access the backend after revocation. If the backend is unavailable during logout, the cookie is cleared but server revocation is best-effort; the absolute session expiry still applies. Disabling a user (`is_active=false`) or revoking its sessions immediately prevents further backend requests. Existing response bytes cannot be recalled; authorization is checked when each request starts.

## Demo policy

`DEMO_LOGIN_ENABLED=false` by default, in **both** environments. Enable it deliberately for interface previews. Each login creates a separate Student demo identity/session, not a shared account. Demo identities are not verified institute users and cannot access institute RAG, administrator tools or other users' conversations. Turning off the backend flag invalidates already-issued demo sessions. The Phase 4 UI can use the demo identity for a labeled preview; no fabricated campus answers are supplied.

## Abuse controls and cleanup

PostgreSQL counters enforce login limits (10 attempts per identifier per 15-minute bucket), registration limits (5 per email/hour), verification limits and a bridge-wide 500-request/minute cap. These work across backend processes. They are fixed-window limits; the fronting proxy should add IP-level controls in Phase 6. Client IP headers are not blindly trusted by this implementation.

Run periodically from the deployment operator account:

```bash
python -m backend.app.auth.manage cleanup-expired
```

This removes expired sessions, pending registration records, used Google-token hashes and rate-limit buckets. It does not delete user accounts, audit history or conversations. Session issuance, mailbox verification and role changes are recorded in `auth_audit`. Neither passwords, verification codes nor raw session tokens are recorded there.

## Validation

The Python suite includes signed/forged/expired Google token tests, exact domain checks, replay prevention, Argon2 registration/login, expiring and single-use verification, demo isolation, RBAC, role-change revocation and rate limits. SQLite tests do not prove PostgreSQL row/advisory-lock behavior under concurrent production traffic.

`tests/frontend_auth_smoke.py` passed locally. It is an opt-in HTTP integration test running real Next.js/Auth.js and FastAPI against a temporary SQLite database. It checks cookie flags, server-only token handling, client role-update rejection, origin checks, RBAC, logout/cookie replay and demo isolation. It requires installed Node/Python dependencies, sends no Google or SMTP traffic, and removes its temporary database/processes afterward.
