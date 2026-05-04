#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib import error, request


class ModelRouterError(RuntimeError):
    pass


class ModelRouterRequestError(ModelRouterError):
    pass


class ModelRouterNotFoundError(ModelRouterError):
    pass


class ModelRouterUpstreamError(ModelRouterError):
    pass


GEMMA_TOOL_RESULT_FOLLOWUP_HINT = (
    "A tool result is now available. Reply normally to the user if you have enough information. "
    "Only call another tool if you truly still need one."
)


def join_url(base_url: str, path: str) -> str:
    normalized_base = base_url.strip().rstrip("/")
    normalized_path = path if path.startswith("/") else f"/{path}"
    if not normalized_base:
        raise ModelRouterError("Missing upstream base URL")
    if normalized_base.endswith(normalized_path):
        return normalized_base
    return normalized_base + normalized_path


def is_gemma_model(model_id: str) -> bool:
    return model_id.strip().lower().startswith("gemma")


def apply_chat_payload_compatibility_shims(payload: dict[str, Any]) -> dict[str, Any]:
    model_id = str(payload.get("model") or "").strip()
    if not is_gemma_model(model_id):
        return payload

    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return payload

    last_message = messages[-1]
    if not isinstance(last_message, dict) or str(last_message.get("role") or "").strip() != "tool":
        return payload

    rewritten_payload = dict(payload)
    rewritten_payload["messages"] = [
        *messages,
        {
            "role": "user",
            "content": GEMMA_TOOL_RESULT_FOLLOWUP_HINT,
        },
    ]
    return rewritten_payload


@dataclass(slots=True)
class ModelCatalog:
    response_payload: dict[str, Any]
    local_model_ids: set[str] = field(default_factory=set)


@dataclass(slots=True)
class ModelRouterConfig:
    ollama_base_url: str
    timeout: float = 300.0
    model_cache_ttl_secs: float = 30.0


class ModelRouterServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], config: ModelRouterConfig):
        self.config = config
        self._catalog_cache: ModelCatalog | None = None
        self._catalog_cache_expires_at = 0.0
        self._catalog_cache_lock = threading.Lock()
        super().__init__(server_address, ModelRouterHandler)

    def ollama_models_url(self) -> str:
        return join_url(self.config.ollama_base_url, "/v1/models")

    def ollama_chat_url(self) -> str:
        return join_url(self.config.ollama_base_url, "/v1/chat/completions")

    def load_json(self, url: str, *, payload: dict[str, Any] | None = None, timeout: float | None = None) -> tuple[dict[str, Any], str]:
        headers: dict[str, str] = {}
        body: bytes | None = None
        method = "GET"
        if payload is not None:
            method = "POST"
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = request.Request(url, data=body, method=method, headers=headers)
        try:
            with request.urlopen(req, timeout=timeout or self.config.timeout) as response:
                content_type = response.headers.get("Content-Type", "application/json")
                response_payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(response_payload, dict):
                    raise ModelRouterUpstreamError(f"Upstream response from {url} was not a JSON object")
                return response_payload, content_type
        except error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            raise ModelRouterUpstreamError(f"Upstream request to {url} failed with HTTP {exc.code}: {response_body}") from exc
        except error.URLError as exc:
            raise ModelRouterUpstreamError(f"Upstream request to {url} failed: {exc}") from exc

    def open_stream(self, url: str, payload: dict[str, Any], *, timeout: float | None = None):
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            return request.urlopen(req, timeout=timeout or self.config.timeout)
        except error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            raise ModelRouterUpstreamError(f"Upstream request to {url} failed with HTTP {exc.code}: {response_body}") from exc
        except error.URLError as exc:
            raise ModelRouterUpstreamError(f"Upstream request to {url} failed: {exc}") from exc

    def get_model_catalog(self, *, force_refresh: bool = False) -> ModelCatalog:
        now = time.monotonic()
        with self._catalog_cache_lock:
            if (
                not force_refresh
                and self._catalog_cache is not None
                and now < self._catalog_cache_expires_at
            ):
                return self._catalog_cache

            local_payload, _ = self.load_json(self.ollama_models_url())
            local_data = local_payload.get("data")
            if not isinstance(local_data, list):
                raise ModelRouterUpstreamError("Ollama /v1/models response did not contain a data array")

            local_models: dict[str, dict[str, Any]] = {}
            for entry in local_data:
                if not isinstance(entry, dict):
                    continue
                model_id = str(entry.get("id") or "").strip()
                if model_id:
                    local_models[model_id] = entry

            response_payload = {
                "object": "list",
                "data": [local_models[model_id] for model_id in sorted(local_models)],
            }
            catalog = ModelCatalog(
                response_payload=response_payload,
                local_model_ids=set(local_models),
            )
            self._catalog_cache = catalog
            self._catalog_cache_expires_at = now + max(self.config.model_cache_ttl_secs, 0.0)
            return catalog

    def resolve_chat_upstream(self, model_id: str) -> str:
        catalog = self.get_model_catalog()
        if model_id in catalog.local_model_ids:
            return self.ollama_chat_url()

        refreshed_catalog = self.get_model_catalog(force_refresh=True)
        if model_id in refreshed_catalog.local_model_ids:
            return self.ollama_chat_url()

        raise ModelRouterNotFoundError(f"Unknown model: {model_id}")


class ModelRouterHandler(BaseHTTPRequestHandler):
    server: ModelRouterServer

    def do_GET(self) -> None:  # noqa: N802
        try:
            if self.path == "/healthz":
                self._send_json(
                    200,
                    {
                        "status": "ok",
                        "service": "model-router",
                    },
                )
                return

            if self.path == "/v1/models":
                catalog = self.server.get_model_catalog()
                self._send_json(200, catalog.response_payload)
                return

            self._send_json(404, {"error": {"message": "Not found", "type": "invalid_request_error"}})
        except ModelRouterUpstreamError as exc:
            self._send_json(502, {"error": {"message": str(exc), "type": "upstream_error"}})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self._send_json(404, {"error": {"message": "Not found", "type": "invalid_request_error"}})
            return

        try:
            payload = self._read_request_json()
            model_id = str(payload.get("model") or "").strip()
            if not model_id:
                raise ModelRouterRequestError("Request body must include a non-empty model")

            payload = apply_chat_payload_compatibility_shims(payload)
            upstream_url = self.server.resolve_chat_upstream(model_id)
            if payload.get("stream"):
                self._proxy_stream(upstream_url, payload)
            else:
                self._proxy_json(upstream_url, payload)
        except ModelRouterRequestError as exc:
            self._send_json(400, {"error": {"message": str(exc), "type": "invalid_request_error"}})
        except ModelRouterNotFoundError as exc:
            self._send_json(404, {"error": {"message": str(exc), "type": "invalid_request_error"}})
        except ModelRouterUpstreamError as exc:
            self._send_json(502, {"error": {"message": str(exc), "type": "upstream_error"}})
        except Exception as exc:  # noqa: BLE001
            self._send_json(502, {"error": {"message": str(exc), "type": "router_error"}})

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _read_request_json(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ModelRouterRequestError(f"Invalid JSON request body: {exc}") from exc
        if not isinstance(payload, dict):
            raise ModelRouterRequestError("Expected request body to be a JSON object")
        return payload

    def _proxy_json(self, upstream_url: str, payload: dict[str, Any]) -> None:
        response_payload, content_type = self.server.load_json(upstream_url, payload=payload)
        body = json.dumps(response_payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy_stream(self, upstream_url: str, payload: dict[str, Any]) -> None:
        upstream_response = self.server.open_stream(upstream_url, payload)
        self.send_response(200)
        self.send_header("Content-Type", upstream_response.headers.get("Content-Type", "text/event-stream"))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            while True:
                chunk = upstream_response.read(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        finally:
            upstream_response.close()

    def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Route OpenAI-compatible requests to local Ollama")
    parser.add_argument("--bind-host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=11436)
    parser.add_argument("--ollama-base-url", default="http://ollama:11434")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--model-cache-ttl-secs", type=float, default=30.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = ModelRouterConfig(
        ollama_base_url=args.ollama_base_url,
        timeout=args.timeout,
        model_cache_ttl_secs=args.model_cache_ttl_secs,
    )
    server = ModelRouterServer((args.bind_host, args.port), config)
    print(f"model-router listening on {args.bind_host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"model-router error: {exc}", file=sys.stderr)
        raise SystemExit(1)