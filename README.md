# LLM Guard API Web Client (Cloud Run)

Kleine Flask-Webapp, um eine bestehende `llm-guard` API-Instanz über ein Browser-UI anzusprechen.

## Features

- Konfiguration via ENV:
  - `API_URL` (z. B. `https://deine-llm-guard-api.example.com`)
  - `AUTH_TOKEN` (Bearer Token)
  - `UPSTREAM_TIMEOUT_SECONDS` (optional, Default `120`)
  - `OPENAPI_SPEC_URL` (optional, Default auf gehostete OpenAPI-URL)
- Einfaches Webinterface mit Endpoint-Dropdown (aus OpenAPI geladen), Prompt-Feld und optionalem Output-Feld; der JSON-Body wird im Hintergrund erstellt.
- Optional einblendbare Konfigurationsansicht für `GET /debug/scanners` direkt in der UI sowie eine aufklappbare Debug-Sektion mit exakten Upstream-Requests/-Responses.
- Kein Scanner-Auswahlfeld im UI: Requests werden ohne explizite Scanner-Auswahl gesendet und zeigen Scanner-Details aus der Antwort an.
- Serverseitiger Forward an LLM Guard inkl. `Authorization: Bearer <AUTH_TOKEN>`.
- Docker-ready für Google Cloud Run.
- Direkter Kompatibilitäts-Endpoint `POST /analyze/prompt` für Clients, die nicht über `/api/forward` senden.

## API-Dokumentation: So greift die App auf LLM Guard zu

Die App arbeitet als **Backend-Proxy**: Browser/Clients sprechen nur die Flask-App an, und die Flask-App ruft dann die eigentliche LLM-Guard-API auf.

### 1) Konfiguration des Upstreams

Die Ziel-API wird ausschließlich über Umgebungsvariablen gesteuert:

- `API_URL`: Basis-URL der LLM-Guard-Instanz (Pfad wird pro Request ergänzt)
- `AUTH_TOKEN`: Bearer-Token für den Upstream
- `UPSTREAM_TIMEOUT_SECONDS`: Timeout für Upstream-Requests
- `OPENAPI_SPEC_URL`: URL der OpenAPI-Spezifikation (für Endpoint-Liste und Beispiel-Bodies)

Ohne `API_URL` oder `AUTH_TOKEN` beantwortet die App Requests mit `500`.

### 2) Request-Fluss (High Level)

1. Client sendet Request an einen lokalen Endpoint der Flask-App.
2. App validiert Payload (JSON-Objekt), ergänzt bei Bedarf Felder (`prompt`/`output`) und normalisiert Scanner-Listen.
3. App baut Ziel-URL: `API_URL.rstrip('/') + endpoint`.
4. App sendet Upstream-Request mit:
   - HTTP-Methode: `POST` (bei Analysen) oder `GET` (bei Debug/Config)
   - Header: `Authorization: Bearer <AUTH_TOKEN>`
   - `Content-Type: application/json` für POST
5. App gibt die Upstream-Antwort (Status + Response-Body) an den Client zurück.

### 3) Lokale API-Endpoints und Upstream-Mapping

#### `POST /api/forward`

Universeller Proxy-Endpoint für LLM-Guard-POST-Operationen.

**Request (lokal):**

```json
{
  "endpoint": "/analyze/prompt",
  "body": {
    "prompt": "Hallo Welt"
  },
  "input_scanners": ["prompt_injection"],
  "output_scanners": ["toxicity"]
}
```

**Wichtige Logik:**

- `endpoint` ist optional, Default: `/analyze/prompt`
- Wenn `body` fehlt, erzeugt die App automatisch einen Body aus `text` bzw. `prompt`/`output`
- `input_scanners` und `output_scanners` werden
  - in den JSON-Body geschrieben (`input_scanners`, `output_scanners`)
  - zusätzlich als Query-Parameter angehängt (mehrfach erlaubt)
- `scanners` wird bewusst entfernt, um unerwartetes Upstream-Verhalten bei leerem Scanner-Objekt zu vermeiden

**Upstream-Request (Beispiel):**

```http
POST {API_URL}/analyze/prompt?input_scanners=prompt_injection&output_scanners=toxicity
Authorization: Bearer {AUTH_TOKEN}
Content-Type: application/json

{
  "prompt": "Hallo Welt",
  "input_scanners": ["prompt_injection"],
  "output_scanners": ["toxicity"]
}
```

**Response (lokal):**

- HTTP-Status entspricht dem Upstream-Status
- JSON enthält:
  - `status_code`
  - `target_url`
  - `response` (Upstream-Body, JSON oder `raw` Text)
  - `debug.request`/`debug.response` mit Details (Authorization maskiert)

#### `POST /analyze/prompt`

Kompatibilitätsroute. Nimmt ein JSON-Objekt entgegen und leitet es 1:1 nach `{API_URL}/analyze/prompt` weiter.

#### `GET /api/config` und `GET /api/config/scanners`

Beide Routen leiten auf denselben Upstream weiter:

- `GET {API_URL}/debug/scanners`
- Header: `Authorization: Bearer <AUTH_TOKEN>`

#### `GET /api/scanners/available`

Lädt ebenfalls `{API_URL}/debug/scanners`, extrahiert daraus Scanner-Namen (`input_scanners`/`output_scanners`) und gibt eine normalisierte Liste zurück.

#### `GET /api/endpoints`

Lädt `OPENAPI_SPEC_URL`, liest daraus alle `POST`-Operationen und gibt:

- `method`
- `path`
- `summary`
- `example_body`

zurück. `example_body` wird aus `example`, `examples` oder JSON-Schema (inkl. `$ref`/`allOf`) erzeugt.

### 4) Fehlerverhalten

- Fehlende Konfiguration (`API_URL`, `AUTH_TOKEN`, ungültiges Timeout): `500`
- Upstream nicht erreichbar / Netzwerkfehler: `502`
- Ungültiger lokaler Body-Typ (kein JSON-Objekt): `400`
- Unbekannte Route: `404`

### 5) Sicherheit & Debugging

- Der Bearer-Token wird nur serverseitig verwendet.
- In Debug-Antworten wird `Authorization` maskiert (`Bearer ********`).
- Die App gibt vollständige Upstream-Daten zurück; in Produktion sollte geprüft werden, welche Debug-Informationen extern sichtbar sein sollen.

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
