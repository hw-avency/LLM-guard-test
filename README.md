# LLM Guard API Web Client (Cloud Run)

Kleine Flask-Webapp, um eine bestehende `llm-guard` API-Instanz über ein Browser-UI anzusprechen.

## Features

- Konfiguration via ENV:
  - `API_URL` (z. B. `https://deine-llm-guard-api.example.com`)
  - `AUTH_TOKEN` (Bearer Token)
  - `UPSTREAM_TIMEOUT_SECONDS` (optional, Default `120`)
  - `OPENAPI_SPEC_URL` (optional, Default auf gehostete OpenAPI-URL)
- Einfaches Webinterface mit Endpoint-Dropdown (aus OpenAPI geladen) und automatisch vorgeschlagenem JSON-Body je Endpoint.
- Optional einblendbare Konfigurationsansicht für `GET /debug/scanners` direkt in der UI.
- Serverseitiger Forward an LLM Guard inkl. `Authorization: Bearer <AUTH_TOKEN>`.
- Docker-ready für Google Cloud Run.
- Direkter Kompatibilitäts-Endpoint `POST /analyze/prompt` für Clients, die nicht über `/api/forward` senden.

## Lokal starten

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export API_URL="https://YOUR_LLM_GUARD_URL"
export AUTH_TOKEN="YOUR_TOKEN"
export UPSTREAM_TIMEOUT_SECONDS="120"
python app.py
```

Danach öffnen: `http://localhost:8080`

## Docker Build & Run

```bash
docker build -t llm-guard-web-client .
docker run --rm -p 8080:8080 \
  -e API_URL="https://YOUR_LLM_GUARD_URL" \
  -e AUTH_TOKEN="YOUR_TOKEN" \
  -e UPSTREAM_TIMEOUT_SECONDS="120" \
  llm-guard-web-client
```

## Deploy auf Google Cloud Run

```bash
gcloud run deploy llm-guard-web-client \
  --source . \
  --region europe-west1 \
  --allow-unauthenticated \
  --set-env-vars API_URL="https://YOUR_LLM_GUARD_URL",AUTH_TOKEN="YOUR_TOKEN",UPSTREAM_TIMEOUT_SECONDS="120"
```

> Hinweis: Für produktive Nutzung sollte der `AUTH_TOKEN` als Secret (Secret Manager) eingebunden werden.
