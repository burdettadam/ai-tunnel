#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
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


def parse_bool(value: str | bool | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value: {value}")


def parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [entry.strip() for entry in value.split(",") if entry.strip()]


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
    deepseek_v4_model_ids: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class ModelCatalogEntry:
    model_id: str
    backend: str
    owned_by: str
    profiles: frozenset[str] = frozenset()
    request_model_id: str = ""
    model_path: str = ""
    display_name: str = ""

    def model_response(self) -> dict[str, Any]:
        response: dict[str, Any] = {
            "id": self.model_id,
            "object": "model",
            "owned_by": self.owned_by,
        }
        if self.display_name or self.backend or self.model_path:
            response["metadata"] = {
                key: value
                for key, value in {
                    "display_name": self.display_name,
                    "backend": self.backend,
                    "model_path": self.model_path,
                }.items()
                if value
            }
        return response


@dataclass(slots=True)
class ModelCatalogPolicy:
    entries_by_id: dict[str, ModelCatalogEntry] = field(default_factory=dict)
    allowed_model_ids: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, catalog_file: str, model_profiles: list[str], extra_model_ids: list[str]) -> "ModelCatalogPolicy | None":
        if not catalog_file:
            return None

        path = Path(catalog_file)
        if not path.exists():
            return None

        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_entries = payload.get("models")
        if not isinstance(raw_entries, list):
            raise ModelRouterError(f"Model catalog {path} must contain a models array")

        entries_by_id: dict[str, ModelCatalogEntry] = {}
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, dict):
                continue
            model_id = str(raw_entry.get("id") or "").strip()
            backend = str(raw_entry.get("backend") or "").strip()
            if not model_id or not backend:
                continue

            profiles = raw_entry.get("profiles") or []
            if not isinstance(profiles, list):
                profiles = []

            entry = ModelCatalogEntry(
                model_id=model_id,
                backend=backend,
                owned_by=str(raw_entry.get("owned_by") or backend).strip() or backend,
                profiles=frozenset(str(profile).strip() for profile in profiles if str(profile).strip()),
                request_model_id=str(raw_entry.get("requestModelId") or raw_entry.get("request_model_id") or model_id).strip() or model_id,
                model_path=str(raw_entry.get("modelPath") or raw_entry.get("model_path") or "").strip(),
                display_name=str(raw_entry.get("displayName") or raw_entry.get("display_name") or "").strip(),
            )
            entries_by_id[model_id] = entry

        normalized_profiles = {profile.strip() for profile in model_profiles if profile.strip()}
        include_all = not normalized_profiles or "all" in normalized_profiles
        allowed_model_ids: set[str] = set()
        for entry in entries_by_id.values():
            if include_all or entry.profiles.intersection(normalized_profiles):
                allowed_model_ids.add(entry.model_id)

        allowed_model_ids.update(model_id for model_id in extra_model_ids if model_id)
        return cls(entries_by_id=entries_by_id, allowed_model_ids=allowed_model_ids)

    def allows(self, model_id: str) -> bool:
        return model_id in self.allowed_model_ids

    def entries_for_backend(self, backend: str) -> list[ModelCatalogEntry]:
        return [
            entry
            for entry in self.entries_by_id.values()
            if entry.backend == backend and self.allows(entry.model_id)
        ]


@dataclass(slots=True)
class ModelRouterConfig:
    ollama_base_url: str
    timeout: float = 300.0
    model_cache_ttl_secs: float = 30.0
    catalog_file: str = ""
    model_profiles: list[str] = field(default_factory=list)
    extra_model_ids: list[str] = field(default_factory=list)
    deepseek_v4_enabled: bool = False
    deepseek_v4_base_url: str = ""


@dataclass(slots=True)
class ResolvedChatUpstream:
    url: str
    payload: dict[str, Any]


class ModelRouterServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], config: ModelRouterConfig):
        self.config = config
        self.catalog_policy = ModelCatalogPolicy.load(
            config.catalog_file,
            config.model_profiles,
            config.extra_model_ids,
        )
        self._catalog_cache: ModelCatalog | None = None
        self._catalog_cache_expires_at = 0.0
        self._catalog_cache_lock = threading.Lock()
        super().__init__(server_address, ModelRouterHandler)

    def ollama_models_url(self) -> str:
        return join_url(self.config.ollama_base_url, "/v1/models")

    def ollama_chat_url(self) -> str:
        return join_url(self.config.ollama_base_url, "/v1/chat/completions")

    def deepseek_v4_models_url(self) -> str:
        return join_url(self.config.deepseek_v4_base_url, "/v1/models")

    def deepseek_v4_chat_url(self) -> str:
        return join_url(self.config.deepseek_v4_base_url, "/v1/chat/completions")

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

    def is_model_allowed(self, model_id: str) -> bool:
        if self.catalog_policy is None:
            return True
        return self.catalog_policy.allows(model_id)

    def catalog_entry(self, model_id: str) -> ModelCatalogEntry | None:
        if self.catalog_policy is None:
            return None
        return self.catalog_policy.entries_by_id.get(model_id)

    def discover_deepseek_v4_models(self) -> dict[str, dict[str, Any]]:
        if not self.config.deepseek_v4_enabled or not self.config.deepseek_v4_base_url:
            return {}
        if self.catalog_policy is None:
            return {}

        deepseek_entries = self.catalog_policy.entries_for_backend("deepseek-v4")
        if not deepseek_entries:
            return {}

        try:
            upstream_payload, _ = self.load_json(self.deepseek_v4_models_url())
        except ModelRouterUpstreamError:
            return {}

        upstream_data = upstream_payload.get("data")
        if not isinstance(upstream_data, list):
            return {}

        served_model_ids: set[str] = set()
        for upstream_entry in upstream_data:
            if not isinstance(upstream_entry, dict):
                continue
            served_id = str(upstream_entry.get("id") or "").strip()
            if served_id:
                served_model_ids.add(served_id)

        models: dict[str, dict[str, Any]] = {}
        for entry in deepseek_entries:
            candidate_ids = {entry.model_id, entry.request_model_id}
            if entry.model_path:
                candidate_ids.add(entry.model_path)
            if candidate_ids.intersection(served_model_ids):
                models[entry.model_id] = entry.model_response()
        return models

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
                if model_id and self.is_model_allowed(model_id):
                    local_models[model_id] = entry

            deepseek_v4_models = self.discover_deepseek_v4_models()
            all_models = {**local_models, **deepseek_v4_models}

            response_payload = {
                "object": "list",
                "data": [all_models[model_id] for model_id in sorted(all_models)],
            }
            catalog = ModelCatalog(
                response_payload=response_payload,
                local_model_ids=set(local_models),
                deepseek_v4_model_ids=set(deepseek_v4_models),
            )
            self._catalog_cache = catalog
            self._catalog_cache_expires_at = now + max(self.config.model_cache_ttl_secs, 0.0)
            return catalog

    def resolve_chat_upstream(self, model_id: str, payload: dict[str, Any]) -> ResolvedChatUpstream:
        catalog = self.get_model_catalog()
        if model_id in catalog.local_model_ids:
            return ResolvedChatUpstream(self.ollama_chat_url(), payload)
        if model_id in catalog.deepseek_v4_model_ids:
            return self.resolve_deepseek_v4_chat_upstream(model_id, payload)

        refreshed_catalog = self.get_model_catalog(force_refresh=True)
        if model_id in refreshed_catalog.local_model_ids:
            return ResolvedChatUpstream(self.ollama_chat_url(), payload)
        if model_id in refreshed_catalog.deepseek_v4_model_ids:
            return self.resolve_deepseek_v4_chat_upstream(model_id, payload)

        raise ModelRouterNotFoundError(f"Unknown model: {model_id}")

    def resolve_deepseek_v4_chat_upstream(self, model_id: str, payload: dict[str, Any]) -> ResolvedChatUpstream:
        entry = self.catalog_entry(model_id)
        request_model_id = entry.request_model_id if entry is not None else model_id
        rewritten_payload = dict(payload)
        rewritten_payload["model"] = request_model_id
        return ResolvedChatUpstream(self.deepseek_v4_chat_url(), rewritten_payload)


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
            resolved = self.server.resolve_chat_upstream(model_id, payload)
            if resolved.payload.get("stream"):
                self._proxy_stream(resolved.url, resolved.payload)
            else:
                self._proxy_json(resolved.url, resolved.payload)
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
        try:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _proxy_stream(self, upstream_url: str, payload: dict[str, Any]) -> None:
        upstream_response = self.server.open_stream(upstream_url, payload)
        try:
            self.send_response(200)
            self.send_header("Content-Type", upstream_response.headers.get("Content-Type", "text/event-stream"))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            while True:
                chunk = upstream_response.read(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            upstream_response.close()

    def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Route OpenAI-compatible requests to local model backends")
    parser.add_argument("--bind-host", default=os.environ.get("MODEL_ROUTER_BIND_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MODEL_ROUTER_PORT", "11436")))
    parser.add_argument("--ollama-base-url", default=os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434"))
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("MODEL_ROUTER_TIMEOUT_SECS", "300")))
    parser.add_argument("--model-cache-ttl-secs", type=float, default=float(os.environ.get("MODEL_ROUTER_CACHE_TTL_SECS", "30")))
    parser.add_argument("--catalog-file", default=os.environ.get("MODEL_ROUTER_CATALOG_FILE", ""))
    parser.add_argument("--model-profile", default=os.environ.get("MODEL_ROUTER_PROFILE", ""))
    parser.add_argument("--extra-models", default=os.environ.get("MODEL_ROUTER_EXTRA_MODELS", ""))
    parser.add_argument(
        "--deepseek-v4-enabled",
        type=parse_bool,
        default=parse_bool(os.environ.get("DEEPSEEK_V4_ENABLED"), default=False),
    )
    parser.add_argument("--deepseek-v4-base-url", default=os.environ.get("DEEPSEEK_V4_BASE_URL", ""))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = ModelRouterConfig(
        ollama_base_url=args.ollama_base_url,
        timeout=args.timeout,
        model_cache_ttl_secs=args.model_cache_ttl_secs,
        catalog_file=args.catalog_file,
        model_profiles=parse_csv(args.model_profile),
        extra_model_ids=parse_csv(args.extra_models),
        deepseek_v4_enabled=args.deepseek_v4_enabled,
        deepseek_v4_base_url=args.deepseek_v4_base_url,
    )
    server = ModelRouterServer((args.bind_host, args.port), config)
    enabled_backends = ["ollama"]
    if config.deepseek_v4_enabled:
        enabled_backends.append("deepseek-v4")
    print(f"model-router listening on {args.bind_host}:{args.port}; backends={','.join(enabled_backends)}")
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
