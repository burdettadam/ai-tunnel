import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, request


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from deepseek_adapter import (  # noqa: E402
    AdapterConfig,
    DeepSeekAdapterServer,
    normalize_request_payload,
    normalize_response_payload,
    resolve_upstream_url,
)


class _UpstreamHandler(BaseHTTPRequestHandler):
    response_payload: dict = {}
    response_status: int = 200
    response_content_type: str = "application/json"
    raw_response_body: bytes | None = None
    requests: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length).decode("utf-8")
        self.__class__.requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "body": json.loads(body),
            }
        )
        response_body = self.__class__.raw_response_body
        if response_body is None:
            response_body = json.dumps(self.__class__.response_payload).encode("utf-8")
        self.send_response(self.__class__.response_status)
        self.send_header("Content-Type", self.__class__.response_content_type)
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


class DeepSeekAdapterTests(unittest.TestCase):
    def start_upstream(
        self,
        payload: dict,
        *,
        status: int = 200,
        content_type: str = "application/json",
        raw_body: bytes | None = None,
    ) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
        _UpstreamHandler.response_payload = payload
        _UpstreamHandler.response_status = status
        _UpstreamHandler.response_content_type = content_type
        _UpstreamHandler.raw_response_body = raw_body
        _UpstreamHandler.requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 1)
        self.addCleanup(server.shutdown)
        return server, thread, f"http://127.0.0.1:{server.server_address[1]}"

    def start_adapter(self, config: AdapterConfig) -> tuple[DeepSeekAdapterServer, threading.Thread, str]:
        server = DeepSeekAdapterServer(("127.0.0.1", 0), config)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 1)
        self.addCleanup(server.shutdown)
        return server, thread, f"http://127.0.0.1:{server.server_address[1]}"

    def test_resolve_upstream_url_uses_deepseek_default_path(self) -> None:
        self.assertEqual(resolve_upstream_url("https://api.deepseek.com"), "https://api.deepseek.com/chat/completions")
        self.assertEqual(resolve_upstream_url("https://api.deepseek.com/v1"), "https://api.deepseek.com/v1/chat/completions")

    def test_normalize_request_payload_maps_model_and_strips_tool_turn_reasoning(self) -> None:
        payload = {
            "model": "agent",
            "thinking": {"type": "enabled"},
            "reasoning_effort": "max",
            "parallel_tool_calls": True,
            "messages": [
                {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1"}]},
                {"role": "tool", "tool_call_id": "call_1", "content": {"status": "ok"}},
            ],
        }

        normalized = normalize_request_payload(
            payload,
            model_map={"agent": "deepseek-v4-pro"},
            strip_reasoning_on_tool_turn=True,
            drop_parallel_tool_calls=True,
        )

        self.assertEqual(normalized["model"], "deepseek-v4-pro")
        self.assertNotIn("thinking", normalized)
        self.assertNotIn("reasoning_effort", normalized)
        self.assertNotIn("parallel_tool_calls", normalized)
        self.assertEqual(normalized["messages"][0]["content"], "")
        self.assertEqual(normalized["messages"][1]["content"], json.dumps({"status": "ok"}))

    def test_normalize_response_payload_fills_missing_content(self) -> None:
        payload = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{"id": "call_1"}],
                    }
                }
            ]
        }
        normalized = normalize_response_payload(payload)
        self.assertEqual(normalized["choices"][0]["message"]["content"], "")

    def test_adapter_forwards_non_stream_request_with_normalized_payload(self) -> None:
        upstream_payload = {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "report_ready", "arguments": "{}"}}],
                    },
                }
            ]
        }
        _, _, upstream_base_url = self.start_upstream(upstream_payload)
        config = AdapterConfig(
            upstream_url=resolve_upstream_url(upstream_base_url),
            api_token="deepseek-token",
            model_map={"agent": "deepseek-v4-pro"},
        )
        _, _, adapter_base_url = self.start_adapter(config)

        body = json.dumps(
            {
                "model": "agent",
                "parallel_tool_calls": True,
                "messages": [
                    {"role": "user", "content": "hello"},
                ],
            }
        ).encode("utf-8")
        req = request.Request(
            adapter_base_url + "/v1/chat/completions",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(req, timeout=5.0) as response:
            response_payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(_UpstreamHandler.requests[0]["path"], "/chat/completions")
        self.assertEqual(_UpstreamHandler.requests[0]["authorization"], "Bearer deepseek-token")
        self.assertEqual(_UpstreamHandler.requests[0]["body"]["model"], "deepseek-v4-pro")
        self.assertNotIn("parallel_tool_calls", _UpstreamHandler.requests[0]["body"])
        self.assertEqual(response_payload["choices"][0]["message"]["content"], "")

    def test_adapter_healthz_reports_configured_upstream(self) -> None:
        _, _, upstream_base_url = self.start_upstream({"status": "unused"})
        config = AdapterConfig(
            upstream_url=resolve_upstream_url(upstream_base_url),
            api_token="deepseek-token",
        )
        _, _, adapter_base_url = self.start_adapter(config)

        with request.urlopen(adapter_base_url + "/healthz", timeout=5.0) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["service"], "deepseek-adapter")
        self.assertEqual(payload["upstream"], resolve_upstream_url(upstream_base_url))

    def test_adapter_stream_passthrough_preserves_event_stream(self) -> None:
        upstream_events = b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'
        _, _, upstream_base_url = self.start_upstream(
            {},
            content_type="text/event-stream",
            raw_body=upstream_events,
        )
        config = AdapterConfig(
            upstream_url=resolve_upstream_url(upstream_base_url),
            api_token="deepseek-token",
            model_map={"agent": "deepseek-v4-pro"},
        )
        _, _, adapter_base_url = self.start_adapter(config)

        body = json.dumps(
            {
                "model": "agent",
                "stream": True,
                "messages": [
                    {"role": "user", "content": "hello"},
                ],
            }
        ).encode("utf-8")
        req = request.Request(
            adapter_base_url + "/v1/chat/completions",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(req, timeout=5.0) as response:
            response_body = response.read()
            content_type = response.headers.get("Content-Type")

        self.assertIn("text/event-stream", content_type)
        self.assertEqual(response_body, upstream_events)
        self.assertEqual(_UpstreamHandler.requests[0]["body"]["model"], "deepseek-v4-pro")

    def test_adapter_returns_400_for_invalid_json(self) -> None:
        _, _, upstream_base_url = self.start_upstream({"status": "unused"})
        config = AdapterConfig(
            upstream_url=resolve_upstream_url(upstream_base_url),
            api_token="deepseek-token",
        )
        _, _, adapter_base_url = self.start_adapter(config)

        req = request.Request(
            adapter_base_url + "/v1/chat/completions",
            data=b"{",
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        with self.assertRaises(error.HTTPError) as exc_info:
            request.urlopen(req, timeout=5.0)

        self.assertEqual(exc_info.exception.code, 400)
        payload = json.loads(exc_info.exception.read().decode("utf-8"))
        self.assertEqual(payload["error"]["type"], "invalid_request_error")
        self.assertEqual(_UpstreamHandler.requests, [])

    def test_adapter_returns_502_when_upstream_returns_http_error(self) -> None:
        _, _, upstream_base_url = self.start_upstream(
            {"error": {"message": "backend exploded"}},
            status=500,
        )
        config = AdapterConfig(
            upstream_url=resolve_upstream_url(upstream_base_url),
            api_token="deepseek-token",
        )
        _, _, adapter_base_url = self.start_adapter(config)

        body = json.dumps(
            {
                "model": "deepseek-v4-pro",
                "messages": [
                    {"role": "user", "content": "hello"},
                ],
            }
        ).encode("utf-8")
        req = request.Request(
            adapter_base_url + "/v1/chat/completions",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        with self.assertRaises(error.HTTPError) as exc_info:
            request.urlopen(req, timeout=5.0)

        self.assertEqual(exc_info.exception.code, 502)
        payload = json.loads(exc_info.exception.read().decode("utf-8"))
        self.assertEqual(payload["error"]["type"], "upstream_error")


if __name__ == "__main__":
    unittest.main()