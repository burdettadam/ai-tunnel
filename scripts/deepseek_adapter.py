#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import error, request


class DeepSeekAdapterError(RuntimeError):
    pass


class DeepSeekAdapterRequestError(DeepSeekAdapterError):
    pass


class DeepSeekAdapterUpstreamError(DeepSeekAdapterError):
    pass


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value: {value}")


def resolve_upstream_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        raise DeepSeekAdapterError("Missing DeepSeek adapter base URL")
    if normalized.endswith("/chat/completions"):
        return normalized
    if normalized.endswith("/v1"):
        return normalized + "/chat/completions"
    return normalized + "/chat/completions"


def load_token(api_token: str | None = None, api_token_file: str | None = None) -> str:
    direct_token = (api_token or "").strip()
    token_file_value = (api_token_file or "").strip()
    if direct_token and token_file_value:
        raise DeepSeekAdapterError("Provide only one of api_token or api_token_file")
    if direct_token:
        return direct_token
    if token_file_value:
        token = Path(token_file_value).read_text(encoding="utf-8").strip()
        if not token:
            raise DeepSeekAdapterError(f"DeepSeek adapter token file is empty: {token_file_value}")
        return token
    raise DeepSeekAdapterError("Missing DeepSeek adapter API token")


def _normalize_message(message: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(message)
    if normalized.get("role") == "assistant" and normalized.get("tool_calls") and normalized.get("content") is None:
        normalized["content"] = ""
    if normalized.get("role") == "tool" and not isinstance(normalized.get("content"), str):
        normalized["content"] = json.dumps(normalized.get("content"))
    return normalized


def normalize_request_payload(
    payload: dict[str, Any],
    *,
    model_map: dict[str, str] | None = None,
    default_model: str | None = None,
    strip_reasoning_on_tool_turn: bool = True,
    drop_parallel_tool_calls: bool = True,
) -> dict[str, Any]:
    normalized = deepcopy(payload)

    model = str(normalized.get("model") or "").strip()
    if model_map and model in model_map:
        normalized["model"] = model_map[model]
    elif not model and default_model:
        normalized["model"] = default_model

    messages = normalized.get("messages")
    if isinstance(messages, list):
        normalized_messages = [_normalize_message(message) if isinstance(message, dict) else message for message in messages]
        normalized["messages"] = normalized_messages
        if strip_reasoning_on_tool_turn and normalized_messages:
            last_message = normalized_messages[-1]
            if isinstance(last_message, dict) and last_message.get("role") == "tool":
                normalized.pop("thinking", None)
                normalized.pop("reasoning_effort", None)

    if drop_parallel_tool_calls:
        normalized.pop("parallel_tool_calls", None)

    return normalized


def normalize_response_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(payload)
    choices = normalized.get("choices")
    if not isinstance(choices, list):
        return normalized

    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        if message.get("content") is None:
            message["content"] = ""
    return normalized


@dataclass(slots=True)
class AdapterConfig:
    upstream_url: str
    api_token: str
    timeout: float = 300.0
    default_model: str | None = None
    model_map: dict[str, str] = field(default_factory=dict)
    strip_reasoning_on_tool_turn: bool = True
    drop_parallel_tool_calls: bool = True


class DeepSeekAdapterHandler(BaseHTTPRequestHandler):
    server: "DeepSeekAdapterServer"

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/healthz":
            self._send_json(404, {"error": {"message": "Not found", "type": "invalid_request_error"}})
            return

        self._send_json(
            200,
            {
                "status": "ok",
                "service": "deepseek-adapter",
                "upstream": self.server.config.upstream_url,
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self._send_json(404, {"error": {"message": "Not found", "type": "invalid_request_error"}})
            return

        try:
            payload = self._read_request_json()
            upstream_payload = normalize_request_payload(
                payload,
                model_map=self.server.config.model_map,
                default_model=self.server.config.default_model,
                strip_reasoning_on_tool_turn=self.server.config.strip_reasoning_on_tool_turn,
                drop_parallel_tool_calls=self.server.config.drop_parallel_tool_calls,
            )
            if upstream_payload.get("stream"):
                self._proxy_stream(upstream_payload)
            else:
                self._proxy_json(upstream_payload)
        except DeepSeekAdapterRequestError as exc:
            self._send_json(400, {"error": {"message": str(exc), "type": "invalid_request_error"}})
        except DeepSeekAdapterUpstreamError as exc:
            self._send_json(502, {"error": {"message": str(exc), "type": "upstream_error"}})
        except Exception as exc:  # noqa: BLE001
            self._send_json(502, {"error": {"message": str(exc), "type": "adapter_error"}})

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _read_request_json(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise DeepSeekAdapterRequestError(f"Invalid JSON request body: {exc}") from exc
        if not isinstance(payload, dict):
            raise DeepSeekAdapterRequestError("Expected request body to be a JSON object")
        return payload

    def _proxy_json(self, payload: dict[str, Any]) -> None:
        response, content_type = self.server.forward_json(payload)
        normalized = normalize_response_payload(response)
        body = json.dumps(normalized).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy_stream(self, payload: dict[str, Any]) -> None:
        upstream_response = self.server.open_stream(payload)
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


class DeepSeekAdapterServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], config: AdapterConfig):
        super().__init__(server_address, DeepSeekAdapterHandler)
        self.config = config

    def build_request(self, payload: dict[str, Any]) -> request.Request:
        body = json.dumps(payload).encode("utf-8")
        return request.Request(
            self.config.upstream_url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.api_token}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if payload.get("stream") else "application/json",
            },
        )

    def forward_json(self, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
        req = self.build_request(payload)
        try:
            with request.urlopen(req, timeout=self.config.timeout) as response:
                content_type = response.headers.get("Content-Type", "application/json")
                response_payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            raise DeepSeekAdapterUpstreamError(
                f"Upstream DeepSeek request failed with HTTP {exc.code}: {response_body}"
            ) from exc
        except error.URLError as exc:
            raise DeepSeekAdapterUpstreamError(f"Upstream DeepSeek request could not reach the endpoint: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise DeepSeekAdapterUpstreamError("Expected upstream response to be valid JSON") from exc

        if not isinstance(response_payload, dict):
            raise DeepSeekAdapterUpstreamError("Expected upstream response to be a JSON object")
        return response_payload, content_type

    def open_stream(self, payload: dict[str, Any]):
        req = self.build_request(payload)
        try:
            return request.urlopen(req, timeout=self.config.timeout)
        except error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            raise DeepSeekAdapterUpstreamError(
                f"Upstream DeepSeek request failed with HTTP {exc.code}: {response_body}"
            ) from exc
        except error.URLError as exc:
            raise DeepSeekAdapterUpstreamError(f"Upstream DeepSeek request could not reach the endpoint: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a Copilot-facing adapter for remote hosted DeepSeek chat completions")
    parser.add_argument("--bind-host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=11435)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-token")
    parser.add_argument("--api-token-file")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--default-model")
    parser.add_argument("--model-map-json")
    parser.add_argument("--strip-reasoning-on-tool-turn", type=parse_bool, default=True)
    parser.add_argument("--drop-parallel-tool-calls", type=parse_bool, default=True)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    model_map: dict[str, str] = {}
    if args.model_map_json:
        loaded = json.loads(args.model_map_json)
        if not isinstance(loaded, dict):
            raise DeepSeekAdapterError("--model-map-json must be a JSON object")
        model_map = {str(key): str(value) for key, value in loaded.items()}

    config = AdapterConfig(
        upstream_url=resolve_upstream_url(args.base_url),
        api_token=load_token(args.api_token, args.api_token_file),
        timeout=args.timeout,
        default_model=args.default_model,
        model_map=model_map,
        strip_reasoning_on_tool_turn=args.strip_reasoning_on_tool_turn,
        drop_parallel_tool_calls=args.drop_parallel_tool_calls,
    )
    server = DeepSeekAdapterServer((args.bind_host, args.port), config)
    print(f"DeepSeek adapter listening on http://{args.bind_host}:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DeepSeekAdapterError as exc:
        print(f"deepseek-adapter error: {exc}", file=sys.stderr)
        raise SystemExit(1)