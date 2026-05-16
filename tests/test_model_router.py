import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, request


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from model_router import (  # noqa: E402
    GEMMA_TOOL_RESULT_FOLLOWUP_HINT,
    ModelRouterConfig,
    ModelRouterServer,
    build_parser,
)


def build_handler_class():
    class _Handler(BaseHTTPRequestHandler):
        routes: dict[tuple[str, str], list[dict]] = {}
        requests: list[dict] = []

        def do_GET(self) -> None:  # noqa: N802
            self._handle("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._handle("POST")

        def _handle(self, method: str) -> None:
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length).decode("utf-8") if content_length else ""
            payload = json.loads(body) if body else None
            self.__class__.requests.append(
                {
                    "method": method,
                    "path": self.path,
                    "body": payload,
                }
            )

            key = (method, self.path)
            responses = self.__class__.routes.get(key)
            if not responses:
                response_body = json.dumps({"error": {"message": "missing route"}}).encode("utf-8")
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response_body)))
                self.end_headers()
                self.wfile.write(response_body)
                return

            response = responses[0]
            if len(responses) > 1:
                self.__class__.routes[key] = responses[1:]

            raw_body = response.get("raw_body")
            if raw_body is None:
                raw_body = json.dumps(response.get("body", {})).encode("utf-8")
            self.send_response(response.get("status", 200))
            self.send_header("Content-Type", response.get("content_type", "application/json"))
            self.send_header("Content-Length", str(len(raw_body)))
            self.end_headers()
            self.wfile.write(raw_body)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    return _Handler


class ModelRouterTests(unittest.TestCase):
    def test_parser_uses_extended_default_timeout(self) -> None:
        args = build_parser().parse_args([])

        self.assertEqual(args.timeout, 300.0)

    def start_mock_server(self, routes: dict[tuple[str, str], list[dict]]) -> tuple[type[BaseHTTPRequestHandler], ThreadingHTTPServer, threading.Thread, str]:
        handler_class = build_handler_class()
        handler_class.routes = {key: list(value) for key, value in routes.items()}
        handler_class.requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 1)
        self.addCleanup(server.shutdown)
        return handler_class, server, thread, f"http://127.0.0.1:{server.server_address[1]}"

    def start_router(self, config: ModelRouterConfig) -> tuple[ModelRouterServer, threading.Thread, str]:
        server = ModelRouterServer(("127.0.0.1", 0), config)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 1)
        self.addCleanup(server.shutdown)
        return server, thread, f"http://127.0.0.1:{server.server_address[1]}"

    def write_catalog(self, payload: dict) -> str:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "catalog.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    def test_router_lists_local_models(self) -> None:
        _, _, _, ollama_base_url = self.start_mock_server(
            {
                ("GET", "/v1/models"): [
                    {
                        "body": {
                            "object": "list",
                            "data": [
                                {"id": "milkey/deepseek-v2.5-1210:IQ1_S", "object": "model", "owned_by": "ollama"},
                                {"id": "qwen2.5:3b", "object": "model", "owned_by": "ollama"},
                            ],
                        }
                    }
                ]
            }
        )
        config = ModelRouterConfig(
            ollama_base_url=ollama_base_url,
            model_cache_ttl_secs=30.0,
        )
        _, _, router_base_url = self.start_router(config)

        with request.urlopen(router_base_url + "/v1/models", timeout=5.0) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(
            [entry["id"] for entry in payload["data"]],
            ["milkey/deepseek-v2.5-1210:IQ1_S", "qwen2.5:3b"],
        )

    def test_router_filters_local_models_by_catalog_profile(self) -> None:
        _, _, _, ollama_base_url = self.start_mock_server(
            {
                ("GET", "/v1/models"): [
                    {
                        "body": {
                            "object": "list",
                            "data": [
                                {"id": "gemma4:31b", "object": "model", "owned_by": "ollama"},
                                {"id": "qwen2.5:3b", "object": "model", "owned_by": "ollama"},
                            ],
                        }
                    }
                ]
            }
        )
        catalog_file = self.write_catalog(
            {
                "models": [
                    {"id": "qwen2.5:3b", "backend": "ollama", "profiles": ["local-small"]},
                    {"id": "gemma4:31b", "backend": "ollama", "profiles": ["server-gpu"]},
                ]
            }
        )
        config = ModelRouterConfig(
            ollama_base_url=ollama_base_url,
            catalog_file=catalog_file,
            model_profiles=["local-small"],
        )
        _, _, router_base_url = self.start_router(config)

        with request.urlopen(router_base_url + "/v1/models", timeout=5.0) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual([entry["id"] for entry in payload["data"]], ["qwen2.5:3b"])

    def test_router_lists_and_routes_local_deepseek_v4_backend(self) -> None:
        _, _, _, ollama_base_url = self.start_mock_server(
            {
                ("GET", "/v1/models"): [
                    {
                        "body": {
                            "object": "list",
                            "data": [
                                {"id": "qwen2.5:3b", "object": "model", "owned_by": "ollama"},
                            ],
                        }
                    }
                ]
            }
        )
        deepseek_handler, _, _, deepseek_base_url = self.start_mock_server(
            {
                ("GET", "/v1/models"): [
                    {
                        "body": {
                            "object": "list",
                            "data": [
                                {"id": "deepseek-v4-flash", "object": "model", "owned_by": "sglang"},
                            ],
                        }
                    },
                    {
                        "body": {
                            "object": "list",
                            "data": [
                                {"id": "deepseek-v4-flash", "object": "model", "owned_by": "sglang"},
                            ],
                        }
                    },
                ],
                ("POST", "/v1/chat/completions"): [
                    {
                        "body": {
                            "choices": [
                                {
                                    "finish_reason": "stop",
                                    "message": {"role": "assistant", "content": "deepseek ok"},
                                }
                            ]
                        }
                    }
                ],
            }
        )
        catalog_file = self.write_catalog(
            {
                "models": [
                    {"id": "qwen2.5:3b", "backend": "ollama", "profiles": ["local-small"]},
                    {
                        "id": "deepseek-v4-flash",
                        "backend": "deepseek-v4",
                        "owned_by": "deepseek-v4-local",
                        "requestModelId": "deepseek-v4-flash",
                        "modelPath": "deepseek-ai/DeepSeek-V4-Flash",
                        "profiles": ["deepseek-v4"],
                    },
                ]
            }
        )
        config = ModelRouterConfig(
            ollama_base_url=ollama_base_url,
            catalog_file=catalog_file,
            model_profiles=["local-small"],
            extra_model_ids=["deepseek-v4-flash"],
            deepseek_v4_enabled=True,
            deepseek_v4_base_url=deepseek_base_url,
        )
        _, _, router_base_url = self.start_router(config)

        with request.urlopen(router_base_url + "/v1/models", timeout=5.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual([entry["id"] for entry in payload["data"]], ["deepseek-v4-flash", "qwen2.5:3b"])

        body = json.dumps(
            {
                "model": "deepseek-v4-flash",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            }
        ).encode("utf-8")
        req = request.Request(
            router_base_url + "/v1/chat/completions",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(req, timeout=5.0) as response:
            chat_payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(chat_payload["choices"][0]["message"]["content"], "deepseek ok")
        self.assertEqual(deepseek_handler.requests[-1]["path"], "/v1/chat/completions")
        self.assertEqual(deepseek_handler.requests[-1]["body"]["model"], "deepseek-v4-flash")

    def test_router_hides_deepseek_v4_when_backend_is_disabled(self) -> None:
        _, _, _, ollama_base_url = self.start_mock_server(
            {
                ("GET", "/v1/models"): [
                    {
                        "body": {
                            "object": "list",
                            "data": [
                                {"id": "qwen2.5:3b", "object": "model", "owned_by": "ollama"},
                            ],
                        }
                    }
                ]
            }
        )
        catalog_file = self.write_catalog(
            {
                "models": [
                    {"id": "qwen2.5:3b", "backend": "ollama", "profiles": ["local-small"]},
                    {"id": "deepseek-v4-flash", "backend": "deepseek-v4", "profiles": ["deepseek-v4"]},
                ]
            }
        )
        config = ModelRouterConfig(
            ollama_base_url=ollama_base_url,
            catalog_file=catalog_file,
            model_profiles=["local-small"],
            extra_model_ids=["deepseek-v4-flash"],
            deepseek_v4_enabled=False,
        )
        _, _, router_base_url = self.start_router(config)

        with request.urlopen(router_base_url + "/v1/models", timeout=5.0) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual([entry["id"] for entry in payload["data"]], ["qwen2.5:3b"])

    def test_router_healthz_reports_ok(self) -> None:
        _, _, _, ollama_base_url = self.start_mock_server(
            {
                ("GET", "/v1/models"): [
                    {
                        "body": {
                            "object": "list",
                            "data": [
                                {"id": "qwen2.5:3b", "object": "model", "owned_by": "ollama"},
                            ],
                        }
                    }
                ]
            }
        )
        config = ModelRouterConfig(ollama_base_url=ollama_base_url)
        _, _, router_base_url = self.start_router(config)

        with request.urlopen(router_base_url + "/healthz", timeout=5.0) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(payload, {"status": "ok", "service": "model-router"})

    def test_router_routes_local_chat_to_ollama(self) -> None:
        ollama_handler, _, _, ollama_base_url = self.start_mock_server(
            {
                ("GET", "/v1/models"): [
                    {
                        "body": {
                            "object": "list",
                            "data": [
                                {"id": "qwen2.5:3b", "object": "model", "owned_by": "ollama"},
                            ],
                        }
                    }
                ],
                ("POST", "/v1/chat/completions"): [
                    {
                        "body": {
                            "choices": [
                                {
                                    "finish_reason": "stop",
                                    "message": {"role": "assistant", "content": "local ok"},
                                }
                            ]
                        }
                    }
                ],
            }
        )
        config = ModelRouterConfig(ollama_base_url=ollama_base_url)
        _, _, router_base_url = self.start_router(config)

        body = json.dumps(
            {
                "model": "qwen2.5:3b",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            }
        ).encode("utf-8")
        req = request.Request(
            router_base_url + "/v1/chat/completions",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(req, timeout=5.0) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(payload["choices"][0]["message"]["content"], "local ok")
        self.assertEqual(ollama_handler.requests[-1]["path"], "/v1/chat/completions")
        self.assertEqual(ollama_handler.requests[-1]["body"]["model"], "qwen2.5:3b")

    def test_router_adds_gemma_followup_hint_after_tool_result(self) -> None:
        ollama_handler, _, _, ollama_base_url = self.start_mock_server(
            {
                ("GET", "/v1/models"): [
                    {
                        "body": {
                            "object": "list",
                            "data": [
                                {"id": "gemma4:e4b", "object": "model", "owned_by": "ollama"},
                            ],
                        }
                    }
                ],
                ("POST", "/v1/chat/completions"): [
                    {
                        "body": {
                            "choices": [
                                {
                                    "finish_reason": "stop",
                                    "message": {"role": "assistant", "content": "Report is ready."},
                                }
                            ]
                        }
                    }
                ],
            }
        )
        config = ModelRouterConfig(ollama_base_url=ollama_base_url)
        _, _, router_base_url = self.start_router(config)

        body = json.dumps(
            {
                "model": "gemma4:e4b",
                "messages": [
                    {"role": "system", "content": "Use tools when needed."},
                    {"role": "user", "content": "Check readiness."},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "report_ready",
                                    "arguments": json.dumps({"status": "ok"}),
                                },
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "call_1", "content": json.dumps({"status": "ok"})},
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "report_ready",
                            "parameters": {"type": "object"},
                        },
                    }
                ],
                "stream": False,
            }
        ).encode("utf-8")
        req = request.Request(
            router_base_url + "/v1/chat/completions",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        with request.urlopen(req, timeout=5.0) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(payload["choices"][0]["message"]["content"], "Report is ready.")
        upstream_messages = ollama_handler.requests[-1]["body"]["messages"]
        self.assertEqual(upstream_messages[-1], {"role": "user", "content": GEMMA_TOOL_RESULT_FOLLOWUP_HINT})
        self.assertEqual(ollama_handler.requests[-1]["body"]["tools"][0]["function"]["name"], "report_ready")

    def test_router_returns_404_for_unknown_model(self) -> None:
        _, _, _, ollama_base_url = self.start_mock_server(
            {
                ("GET", "/v1/models"): [
                    {
                        "body": {
                            "object": "list",
                            "data": [
                                {"id": "qwen2.5:3b", "object": "model", "owned_by": "ollama"},
                            ],
                        }
                    }
                ]
            }
        )
        config = ModelRouterConfig(ollama_base_url=ollama_base_url)
        _, _, router_base_url = self.start_router(config)

        body = json.dumps(
            {
                "model": "missing-model",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            }
        ).encode("utf-8")
        req = request.Request(
            router_base_url + "/v1/chat/completions",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        with self.assertRaises(error.HTTPError) as exc_info:
            request.urlopen(req, timeout=5.0)

        self.assertEqual(exc_info.exception.code, 404)
        payload = json.loads(exc_info.exception.read().decode("utf-8"))
        self.assertIn("Unknown model", payload["error"]["message"])

    def test_router_caches_model_inventory_until_ttl_expires(self) -> None:
        ollama_handler, _, _, ollama_base_url = self.start_mock_server(
            {
                ("GET", "/v1/models"): [
                    {
                        "body": {
                            "object": "list",
                            "data": [
                                {"id": "qwen2.5:3b", "object": "model", "owned_by": "ollama"},
                            ],
                        }
                    },
                    {
                        "body": {
                            "object": "list",
                            "data": [
                                {"id": "qwen2.5:3b", "object": "model", "owned_by": "ollama"},
                                {"id": "qwen2.5:0.5b", "object": "model", "owned_by": "ollama"},
                            ],
                        }
                    },
                ]
            }
        )
        config = ModelRouterConfig(
            ollama_base_url=ollama_base_url,
            model_cache_ttl_secs=60.0,
        )
        server, _, router_base_url = self.start_router(config)

        with request.urlopen(router_base_url + "/v1/models", timeout=5.0) as response:
            first_payload = json.loads(response.read().decode("utf-8"))
        with request.urlopen(router_base_url + "/v1/models", timeout=5.0) as response:
            second_payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual([entry["id"] for entry in first_payload["data"]], ["qwen2.5:3b"])
        self.assertEqual([entry["id"] for entry in second_payload["data"]], ["qwen2.5:3b"])
        self.assertEqual(len([entry for entry in ollama_handler.requests if entry["path"] == "/v1/models"]), 1)

        server._catalog_cache_expires_at = 0.0

        with request.urlopen(router_base_url + "/v1/models", timeout=5.0) as response:
            refreshed_payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(
            [entry["id"] for entry in refreshed_payload["data"]],
            ["qwen2.5:0.5b", "qwen2.5:3b"],
        )
        self.assertEqual(len([entry for entry in ollama_handler.requests if entry["path"] == "/v1/models"]), 2)


if __name__ == "__main__":
    unittest.main()
