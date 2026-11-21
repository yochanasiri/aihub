# AIHUB deployment

The base file still supports the Windows/Docker Desktop workflow with `npm run dev`. Production is opt-in. Use Docker Compose v2.24.4 or later for the overlays' reset/override syntax. Preserve your database and configuration during upgrades.

## HTTPS frontend (Coolify or another reverse proxy)

1. Deploy the source root with `infra/docker-compose.yml` and `infra/production.yml` combined. For a Compose-aware platform, supply the merged service configuration and set the source root/build contexts accordingly. Use `docker compose --env-file .env -f infra/docker-compose.yml -f infra/production.yml config --quiet` locally to validate without printing secrets.
2. Set `AUTH_URL=https://your-campus-assistant.example`, a strong `AUTH_SECRET`, and a different strong `AUTH_BRIDGE_SECRET` of at least 32 characters each. Set strong PostgreSQL and Qdrant credentials and the existing verified institute auth configuration. Keep `DEMO_LOGIN_ENABLED=false`. Register the exact Google OAuth callback `https://your-campus-assistant.example/api/auth/callback/google` if using Google.
3. Route the HTTPS domain to **frontend:3000**, preserving the public Host. Keep other containers private. Do not proxy the backend directly. Turn off SSE buffering in the reverse proxy and set its read timeout above the configured LLM timeout. If uploads are enabled, allow 20 MiB request bodies at the proxy.
4. Build and start the two files together using `up -d --build`. With the base Ollama service, pull the configured model in that container. Index crawled sources or publish a first document before expecting RAG readiness. Start with one backend worker/replica; Prometheus metrics and concurrency controls in this delivery are process-local.
5. Verify the backend readiness from inside the Compose network, then test real institute sign-in, document publish, streaming, feedback and the HTTPS microphone prompt. Configure backups for PostgreSQL and Qdrant and restrict crawler egress from private/link-local infrastructure before exposing the installation.

## External GPU model service

To use an existing private OpenAI-compatible service such as vLLM, add `-f infra/external-model.yml` **after** the production file. Set `LLM_BASE_URL` to its private `/v1` URL, `LLM_MODEL` to the served model ID, and `LLM_API_KEY` to the serving key. Enable `RAG_API_ENABLED=true` when ready. The overlay drops the backend's Ollama dependency. GPU allocation and model serving remain the responsibility of that external service.

## Refresh jobs

The optional refresh container performs one official-domain crawl, then reconciles the vector index, then waits. It reuses the existing crawler limits, allowlist and database writer locks. Set `REFRESH_INTERVAL_SECONDS` (default 21600, allowed 300–604800) and run:

```bash
docker compose --env-file .env -f infra/docker-compose.yml --profile refresh up -d --build refresh
```

For an external scheduler, run the same service with `run --rm refresh python -m backend.app.refresh --once` in place of `up`. Use one scheduler and check failures in refresh/crawler/index logs. The worker does not require the Docker socket.

## Monitoring

Set `GRAFANA_ADMIN_PASSWORD` and append `-f infra/monitoring.yml` after the other Compose files. The overlay enables backend metrics, provisions a private Prometheus scrape and a Grafana dashboard. Grafana binds to host loopback on port 3001; use a secure tunnel for remote access. Prometheus is not published. The dashboard reports RAG outcomes, latency, cache hits and scrape availability. Metrics do not label questions, users, or documents.

The deployment files were checked in source. Running them on your domain, configuring OAuth/TLS and testing the live model require your deployment host.
