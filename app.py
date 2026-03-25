import json
import os
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)


def mask_sensitive_headers(headers: Dict[str, Any]) -> Dict[str, Any]:
    masked_headers: Dict[str, Any] = {}
    for key, value in headers.items():
        if key.lower() == "authorization" and isinstance(value, str):
            if value.lower().startswith("bearer "):
                masked_headers[key] = "Bearer ********"
            else:
                masked_headers[key] = "********"
            continue
        masked_headers[key] = value
    return masked_headers


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


def resolve_ref(spec: Dict[str, Any], ref: str) -> Optional[Dict[str, Any]]:
    if not ref.startswith("#/"):
        return None

    node: Any = spec
    for part in ref[2:].split("/"):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
        if node is None:
            return None

    return node if isinstance(node, dict) else None


def resolve_schema(spec: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    resolved = dict(schema)

    if "$ref" in resolved:
        target = resolve_ref(spec, str(resolved["$ref"]))
        if target is not None:
            ref_copy = dict(resolved)
            ref_copy.pop("$ref", None)
            resolved = {**resolve_schema(spec, target), **ref_copy}

    if "allOf" in resolved and isinstance(resolved["allOf"], list):
        merged: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}
        for part in resolved["allOf"]:
            if not isinstance(part, dict):
                continue
            part_resolved = resolve_schema(spec, part)
            for key, value in part_resolved.items():
                if key == "properties" and isinstance(value, dict):
                    merged.setdefault("properties", {}).update(value)
                elif key == "required" and isinstance(value, list):
                    merged.setdefault("required", []).extend(value)
                elif key not in {"properties", "required"}:
                    merged[key] = value
        merged["required"] = sorted(set(merged.get("required", [])))
        resolved = {**merged, **{k: v for k, v in resolved.items() if k != "allOf"}}

    return resolved


def generate_example_from_schema(spec: Dict[str, Any], schema: Dict[str, Any]) -> Any:
    schema = resolve_schema(spec, schema)
    schema_type = schema.get("type")

    if "example" in schema:
        return schema["example"]

    if "enum" in schema and isinstance(schema["enum"], list) and schema["enum"]:
        return schema["enum"][0]

    if "default" in schema:
        return schema["default"]

    if schema_type == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not properties:
            return {}

        example_object = {}
        for key, value in properties.items():
            if key in required:
                example_object[key] = generate_example_from_schema(spec, value)
        if example_object:
            return example_object

        first_key = next(iter(properties))
        return {first_key: generate_example_from_schema(spec, properties[first_key])}

    if schema_type == "array":
        item_schema = schema.get("items", {})
        if isinstance(item_schema, dict):
            return [generate_example_from_schema(spec, item_schema)]
        return []

    if schema_type == "integer":
        return 0
    if schema_type == "number":
        return 0.0
    if schema_type == "boolean":
        return False
    return ""


def extract_example_body(spec: Dict[str, Any], operation: Dict[str, Any]) -> Dict[str, Any]:
    request_body = operation.get("requestBody", {})
    if isinstance(request_body, dict) and "$ref" in request_body:
        resolved_request_body = resolve_ref(spec, str(request_body["$ref"]))
        if isinstance(resolved_request_body, dict):
            request_body = resolved_request_body

    content = request_body.get("content", {})
    json_content = content.get("application/json") or content.get("application/*+json") or {}

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
        generated = generate_example_from_schema(spec, schema)
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
                    "example_body": extract_example_body(spec, operation),
                }
            )

    endpoints.sort(key=lambda endpoint: endpoint["path"])
    return endpoints, ""


def forward_to_upstream(
    endpoint: str,
    body: Dict[str, Any],
    input_scanners: Optional[List[str]] = None,
    output_scanners: Optional[List[str]] = None,
) -> Tuple[Any, int]:
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

    input_scanners = [scanner for scanner in (input_scanners or []) if isinstance(scanner, str) and scanner.strip()]
    output_scanners = [scanner for scanner in (output_scanners or []) if isinstance(scanner, str) and scanner.strip()]

    if input_scanners:
        body["input_scanners"] = input_scanners
    else:
        body.pop("input_scanners", None)

    if output_scanners:
        body["output_scanners"] = output_scanners
    else:
        body.pop("output_scanners", None)

    # Einige Upstream-Versionen interpretieren ein leeres `scanners`-Objekt
    # als "alle Scanner aktiv". Deshalb senden wir nur explizite
    # input/output_scanners und kein zusätzliches Kombi-Feld.
    body.pop("scanners", None)

    query_params: List[Tuple[str, str]] = []
    for scanner in input_scanners:
        query_params.append(("input_scanners", scanner))
    for scanner in output_scanners:
        query_params.append(("output_scanners", scanner))

    prepared_request = requests.Request(
        "POST",
        target_url,
        headers=headers,
        json=body,
        params=query_params or None,
    ).prepare()

    try:
        response = requests.post(
            target_url,
            headers=headers,
            json=body,
            params=query_params or None,
            timeout=timeout_seconds,
        )
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
            "debug": {
                "request": {
                    "method": "POST",
                    "url": prepared_request.url,
                    "headers": mask_sensitive_headers(dict(prepared_request.headers)),
                    "json_body": body,
                },
                "response": {
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                    "text": response.text,
                },
            },
        }
    ), response.status_code


def get_upstream_config(endpoint: str) -> Tuple[Any, int]:
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

    target_url = f"{config['api_url'].rstrip('/')}{endpoint}"
    headers = {"Authorization": f"Bearer {config['auth_token']}"}

    try:
        response = requests.get(target_url, headers=headers, timeout=timeout_seconds)
    except requests.RequestException as exc:
        return jsonify({"error": "Konfiguration konnte nicht geladen werden.", "detail": str(exc)}), 502

    content_type = response.headers.get("Content-Type", "")
    if "application/json" in content_type:
        try:
            data = response.json()
        except json.JSONDecodeError:
            data = {"raw": response.text}
    else:
        data = {"raw": response.text}

    return jsonify({"status_code": response.status_code, "target_url": target_url, "response": data}), response.status_code


def load_scanner_names() -> Tuple[Dict[str, List[str]], str]:
    config = get_config()
    if not config["api_url"]:
        return {"input_scanners": [], "output_scanners": []}, "API_URL ist nicht gesetzt."
    if not config["auth_token"]:
        return {"input_scanners": [], "output_scanners": []}, "AUTH_TOKEN ist nicht gesetzt."

    try:
        timeout_seconds = int(config["upstream_timeout_seconds"])
    except ValueError:
        return {"input_scanners": [], "output_scanners": []}, "UPSTREAM_TIMEOUT_SECONDS muss eine Ganzzahl sein."

    if timeout_seconds <= 0:
        return {"input_scanners": [], "output_scanners": []}, "UPSTREAM_TIMEOUT_SECONDS muss > 0 sein."

    target_url = f"{config['api_url'].rstrip('/')}/debug/scanners"
    headers = {"Authorization": f"Bearer {config['auth_token']}"}

    try:
        response = requests.get(target_url, headers=headers, timeout=timeout_seconds)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return {"input_scanners": [], "output_scanners": []}, f"Scanner-Konfiguration konnte nicht geladen werden: {exc}"
    except ValueError as exc:
        return {"input_scanners": [], "output_scanners": []}, f"Scanner-Konfiguration ist kein gültiges JSON: {exc}"

    def extract_names(source: Any) -> List[str]:
        names: List[str] = []

        if isinstance(source, list):
            for entry in source:
                if isinstance(entry, str) and entry.strip():
                    names.append(entry.strip())
                    continue

                if isinstance(entry, dict):
                    for key in ("name", "type"):
                        value = entry.get(key)
                        if isinstance(value, str) and value.strip():
                            names.append(value.strip())
                            break

        elif isinstance(source, dict):
            if "input_scanners" in source or "output_scanners" in source:
                names.extend(extract_names(source.get("input_scanners", [])))
                names.extend(extract_names(source.get("output_scanners", [])))
            else:
                names.extend([key for key in source.keys() if isinstance(key, str) and key.strip()])

        return names

    scanners: Dict[str, List[str]] = {"input_scanners": [], "output_scanners": []}

    response_payload: Any = payload
    if isinstance(payload, dict) and isinstance(payload.get("response"), dict):
        response_payload = payload["response"]

    if isinstance(response_payload, dict):
        scanners["input_scanners"] = sorted(set(extract_names(response_payload.get("input_scanners", []))))
        scanners["output_scanners"] = sorted(set(extract_names(response_payload.get("output_scanners", []))))

        if not scanners["input_scanners"] and not scanners["output_scanners"]:
            scanners["input_scanners"] = sorted(
                set(extract_names(response_payload.get("scanners", response_payload)))
            )
    else:
        scanners["input_scanners"] = sorted(set(extract_names(response_payload)))

    return scanners, ""


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

    body_payload = payload.get("body")
    if body_payload is None:
        text = payload.get("text", "")
        normalized_text = text if isinstance(text, str) else str(text)

        prompt = payload.get("prompt", "")
        output = payload.get("output", "")

        body_key = "output" if endpoint.lower().startswith("/analyze/output") else "prompt"
        body: Dict[str, Any] = {
            "prompt": "",
            "output": "",
        }

        if payload.get("text") is not None:
            body[body_key] = normalized_text
        else:
            body["prompt"] = prompt if isinstance(prompt, str) else str(prompt)
            body["output"] = output if isinstance(output, str) else str(output)
    elif isinstance(body_payload, dict):
        body = dict(body_payload)
    else:
        return jsonify({"error": "body muss ein JSON-Objekt sein."}), 400

    input_scanners_raw = payload.get("input_scanners", [])
    output_scanners_raw = payload.get("output_scanners", [])

    input_scanners = input_scanners_raw if isinstance(input_scanners_raw, list) else []
    output_scanners = output_scanners_raw if isinstance(output_scanners_raw, list) else []

    return forward_to_upstream(
        endpoint=endpoint,
        body=body,
        input_scanners=input_scanners,
        output_scanners=output_scanners,
    )


@app.get("/api/endpoints")
def list_endpoints() -> Any:
    endpoints, error = load_available_endpoints()
    if error:
        return jsonify({"error": error}), 502
    return jsonify({"endpoints": endpoints})


@app.get("/api/config")
def get_config_root() -> Any:
    return get_upstream_config(endpoint="/debug/scanners")


@app.get("/api/config/scanners")
def get_config_scanners() -> Any:
    return get_upstream_config(endpoint="/debug/scanners")


@app.get("/api/scanners/available")
def get_available_scanners() -> Any:
    scanners, error = load_scanner_names()
    if error:
        return jsonify({"error": error}), 502
    return jsonify(scanners)


@app.post("/analyze/prompt")
def analyze_prompt() -> Any:
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "Request body muss ein JSON-Objekt sein."}), 400

    return forward_to_upstream(endpoint="/analyze/prompt", body=body)


@app.get("/api/threat-samples")
def get_threat_samples() -> Any:
    try:
        response = requests.get(
            "https://threatintel-813066616888.europe-west3.run.app/api/sample",
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        samples = (data.get("sample") or [])[:2]
        return jsonify({"samples": [{"url": s.get("url", ""), "threat": s.get("threat", "malware")} for s in samples]})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@app.errorhandler(404)
def not_found(_error: Exception) -> Any:
    return jsonify({"error": "Endpoint nicht gefunden."}), 404


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
