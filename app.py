import json
import os
from typing import Any, Dict

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)


def get_config() -> Dict[str, str]:
    api_url = os.getenv("API_URL", "").strip()
    auth_token = os.getenv("AUTH_TOKEN", "").strip()
    upstream_timeout_seconds = os.getenv("UPSTREAM_TIMEOUT_SECONDS", "120").strip()
    return {
        "api_url": api_url,
        "auth_token": auth_token,
        "upstream_timeout_seconds": upstream_timeout_seconds,
    }


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

    payload = request.get_json(silent=True) or {}
    endpoint = str(payload.get("endpoint", "/analyze/prompt")).strip()
    body = payload.get("body", {})

    if not endpoint.startswith("/"):
        endpoint = f"/{endpoint}"

    if not isinstance(body, dict):
        return jsonify({"error": "body muss ein JSON-Objekt sein."}), 400

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


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
