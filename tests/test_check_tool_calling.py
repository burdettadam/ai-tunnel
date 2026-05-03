import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_tool_calling import ToolCallingProbeError, probe_tool_calling_direct  # noqa: E402


class _DirectProbeHandler(BaseHTTPRequestHandler):
    response_payloads: list[dict] = []
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
        if len(self.__class__.requests) > len(self.__class__.response_payloads):
            raise AssertionError("Probe handler received more requests than configured responses")

        response_payload = self.__class__.response_payloads[len(self.__class__.requests) - 1]
        response_body = json.dumps(response_payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


class CheckToolCallingTests(unittest.TestCase):
    def start_server(self, payloads: list[dict]) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
        _DirectProbeHandler.response_payloads = payloads
        _DirectProbeHandler.requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _DirectProbeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 1)
        self.addCleanup(server.shutdown)
        return server, thread, f"http://127.0.0.1:{server.server_address[1]}"

    def test_direct_probe_completes_tool_roundtrip(self) -> None:
        payloads = [
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
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
                    }
                ]
            },
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "Ready.",
                        },
                    }
                ]
            },
        ]
        _, _, base_url = self.start_server(payloads)

        result = probe_tool_calling_direct(
            base_url=base_url,
            api_token="example-token",
            model_id="deepseek-v4-pro",
            timeout=5.0,
        )

        self.assertEqual(result["final_answer"], "Ready.")
        self.assertEqual(len(_DirectProbeHandler.requests), 2)
        self.assertEqual(_DirectProbeHandler.requests[0]["path"], "/chat/completions")
        self.assertEqual(_DirectProbeHandler.requests[0]["authorization"], "Bearer example-token")
        self.assertEqual(
            _DirectProbeHandler.requests[1]["body"]["messages"][-1],
            {"role": "tool", "tool_call_id": "call_1", "content": json.dumps({"status": "ok"})},
        )

    def test_direct_probe_requires_final_answer_after_tool_result(self) -> None:
        payloads = [
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
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
                    }
                ]
            },
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_2",
                                    "type": "function",
                                    "function": {
                                        "name": "report_ready",
                                        "arguments": json.dumps({"status": "ok"}),
                                    },
                                }
                            ],
                        },
                    }
                ]
            },
        ]
        _, _, base_url = self.start_server(payloads)

        with self.assertRaisesRegex(ToolCallingProbeError, "Tool-result round-trip did not finish with a final answer"):
            probe_tool_calling_direct(
                base_url=base_url,
                api_token="example-token",
                model_id="deepseek-v4-pro",
                timeout=5.0,
            )


if __name__ == "__main__":
    unittest.main()