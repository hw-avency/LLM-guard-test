import json
import os
from typing import Any, Dict, List, Tuple

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)


def get_config() -> Dict[str, str]:
    api_url = os.getenv("API_URL", "").strip()
    auth_token = os.getenv("AUTH_TOKEN", "").strip()
    upstream_timeout_seconds = os.getenv("UPSTREAM_TIMEOUT_SECONDS", "120").strip()
    openapi_spec_url = os.getenv(
        "OPENAPI_SPEC_URL",
        "https://llm-guard-api-813066616888.europe-west3.run.app/openapi.json",
    ).strip()
    return {
        "api_url": api_url,
        "auth_token": auth_token,
        "upstream_timeout_seconds": upstream_timeout_seconds,
        "openapi_spec_url": openapi_spec_url,
    }


def generate_example_from_schema(schema: Dict[str, Any]) -> Any:
    schema_type = schema.get("type")

    if "example" in schema:
        return schema["example"]

    if schema_type == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not properties:
            return {}

        example_object = {}
        for key, value in properties.items():
            if key in required:
                example_object[key] = generate_example_from_schema(value)
        if example_object:
            return example_object

        first_key = next(iter(properties))
        return {first_key: generate_example_from_schema(properties[first_key])}

    if schema_type == "array":
        item_schema = schema.get("items", {})
        return [generate_example_from_schema(item_schema)]

    if schema_type == "integer":
        return 0
    if schema_type == "number":
        return 0.0
    if schema_type == "boolean":
        return False
    return ""


def extract_example_body(operation: Dict[str, Any]) -> Dict[str, Any]:
    request_body = operation.get("requestBody", {})
    content = request_body.get("content", {})
    json_content = content.get("application/json", {})

    if "example" in json_content and isinstance(json_content["example"], dict):
        return json_content["example"]

    examples = json_content.get("examples", {})
    if isinstance(examples, dict):
        for entry in examples.values():
            value = entry.get("value") if isinstance(entry, dict) else None
            if isinstance(value, dict):
                return value

    schema = json_content.get("schema", {})
    if isinstance(schema, dict):
        generated = generate_example_from_schema(schema)
        if isinstance(generated, dict):
            return generated

    return {}


def load_available_endpoints() -> Tuple[List[Dict[str, Any]], str]:
    config = get_config()
    openapi_url = config["openapi_spec_url"]

    if not openapi_url:
        return [], "OPENAPI_SPEC_URL ist nicht gesetzt."

    try:
        response = requests.get(openapi_url, timeout=20)
        response.raise_for_status()
        spec = response.json()
    except requests.RequestException as exc:
        return [], f"OpenAPI-Spec konnte nicht geladen werden: {exc}"
    except ValueError as exc:
        return [], f"OpenAPI-Spec ist kein gültiges JSON: {exc}"

    paths = spec.get("paths", {})
    endpoints: List[Dict[str, Any]] = []

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method_name, operation in path_item.items():
            if method_name.lower() != "post" or not isinstance(operation, dict):
                continue

            endpoints.append(
                {
                    "method": method_name.upper(),
                    "path": path,
                    "summary": operation.get("summary", ""),
                    "example_body": extract_example_body(operation),
                }
            )

    endpoints.sort(key=lambda endpoint: endpoint["path"])
    return endpoints, ""


def forward_to_upstream(endpoint: str, body: Dict[str, Any]) -> Tuple[Any, int]:
    config = get_config()
    if not config["api_url"]:
        return jsonify({"error": "API_URL ist nicht gesetzt."}), 500
    if not config["auth_token"]:
        return jsonify({"error": "AUTH_TOKEN ist nicht gesetzt."}), 500

    try:
        timeout_seconds = int(config["upstream_timeout_seconds"])
    except ValueError:
        return jsonify({"error": "UPSTREAM_TIMEOUT_SECONDS muss eine Ganzzahl sein."}), 500

    if timeout_seconds <= 0:
        return jsonify({"error": "UPSTREAM_TIMEOUT_SECONDS muss > 0 sein."}), 500

    if not endpoint.startswith("/"):
        endpoint = f"/{endpoint}"

    target_url = f"{config['api_url'].rstrip('/')}{endpoint}"
    headers = {
        "Authorization": f"Bearer {config['auth_token']}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(target_url, headers=headers, json=body, timeout=timeout_seconds)
    except requests.RequestException as exc:
        return jsonify({"error": "Anfrage an LLM Guard fehlgeschlagen.", "detail": str(exc)}), 502

    content_type = response.headers.get("Content-Type", "")
    if "application/json" in content_type:
        try:
            data = response.json()
        except json.JSONDecodeError:
            data = {"raw": response.text}
    else:
        data = {"raw": response.text}

    return jsonify(
        {
            "status_code": response.status_code,
            "target_url": target_url,
            "response": data,
        }
    ), response.status_code


@app.get("/")
def index() -> str:
    config = get_config()
    return render_template(
        "index.html",
        api_url=config["api_url"],
        auth_configured=bool(config["auth_token"]),
    )


@app.post("/api/forward")
def forward_request() -> Any:
    payload = request.get_json(silent=True) or {}
    endpoint = str(payload.get("endpoint", "/analyze/prompt")).strip()
    body = payload.get("body", {})

    if not isinstance(body, dict):
        return jsonify({"error": "body muss ein JSON-Objekt sein."}), 400

    return forward_to_upstream(endpoint=endpoint, body=body)


@app.get("/api/endpoints")
def list_endpoints() -> Any:
    endpoints, error = load_available_endpoints()
    if error:
        return jsonify({"error": error}), 502
    return jsonify({"endpoints": endpoints})


@app.post("/analyze/prompt")
def analyze_prompt() -> Any:
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "Request body muss ein JSON-Objekt sein."}), 400

    return forward_to_upstream(endpoint="/analyze/prompt", body=body)


@app.errorhandler(404)
def not_found(_error: Exception) -> Any:
    return jsonify({"error": "Endpoint nicht gefunden."}), 404


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
