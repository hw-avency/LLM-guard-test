# LLM Guard API Web Client (Cloud Run)

Kleine Flask-Webapp, um eine bestehende `llm-guard` API-Instanz über ein Browser-UI anzusprechen.

## Features

- Konfiguration via ENV:
  - `API_URL` (z. B. `https://deine-llm-guard-api.example.com`)
  - `AUTH_TOKEN` (Bearer Token)
- Einfaches Webinterface für:
  - Endpoint-Pfad (Default `/analyze/prompt`)
  - JSON Request Body
- Serverseitiger Forward an LLM Guard inkl. `Authorization: Bearer <AUTH_TOKEN>`.
- Docker-ready für Google Cloud Run.

## Lokal starten

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export API_URL="https://YOUR_LLM_GUARD_URL"
export AUTH_TOKEN="YOUR_TOKEN"
python app.py
```

Danach öffnen: `http://localhost:8080`

## Docker Build & Run

```bash
docker build -t llm-guard-web-client .
docker run --rm -p 8080:8080 \
  -e API_URL="https://YOUR_LLM_GUARD_URL" \
  -e AUTH_TOKEN="YOUR_TOKEN" \
  llm-guard-web-client
```

## Deploy auf Google Cloud Run

```bash
gcloud run deploy llm-guard-web-client \
  --source . \
  --region europe-west1 \
  --allow-unauthenticated \
  --set-env-vars API_URL="https://YOUR_LLM_GUARD_URL",AUTH_TOKEN="YOUR_TOKEN"
```

> Hinweis: Für produktive Nutzung sollte der `AUTH_TOKEN` als Secret (Secret Manager) eingebunden werden.
