import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "modelctl.py"


class _ProbeRequestHandler(BaseHTTPRequestHandler):
    response_payload: dict = {}
    response_payloads: list[dict] | None = None
    requests: list[dict] = []
    docker_log_path: Path | None = None

    def do_POST(self) -> None:  # noqa: N802
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length).decode("utf-8")
        docker_log_lines = 0
        if self.__class__.docker_log_path and self.__class__.docker_log_path.exists():
            docker_log_lines = len(self.__class__.docker_log_path.read_text(encoding="utf-8").splitlines())
        self.__class__.requests.append(
            {
                "path": self.path,
                "host": self.headers.get("Host"),
                "authorization": self.headers.get("Authorization"),
                "body": json.loads(body),
                "docker_log_lines": docker_log_lines,
            }
        )
        payloads = self.__class__.response_payloads
        if payloads is not None:
            if len(self.__class__.requests) > len(payloads):
                raise AssertionError("Probe server received more requests than configured responses")
            response_payload = payloads[len(self.__class__.requests) - 1]
        else:
            response_payload = self.__class__.response_payload
        response_body = json.dumps(response_payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


class ModelCtlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.secrets_dir = self.root / "ai-tunnel-secrets"
        self.secrets_dir.mkdir()
        (self.secrets_dir / "ollama-api-token").write_text("example-secret-token\n", encoding="utf-8")
        self.env_path = self.workspace / ".env"
        self.settings_path = self.workspace / ".vscode" / "settings.json"

    def write_env(self, port: int) -> None:
        self.env_path.write_text(
            textwrap.dedent(
                f"""
                OLLAMA_API_PUBLIC_URL=https://ollama-api.example.com/v1
                OLLAMA_API_HOSTNAME=ollama-api.example.com
                NGINX_LISTEN_PORT={port}
                NGINX_API_TOKEN_FILE=../ai-tunnel-secrets/ollama-api-token
                OLLAMA_PORT=11434
                OLLAMA_MODEL=deepseek-v2:16b-lite-chat-q4_K_M
                OLLAMA_MODEL_DISPLAY_NAME=DeepSeek V2 Lite
                OLLAMA_MODEL_VSCODE_ID=deepseek-v2:16b-lite-chat-q4_K_M
                OLLAMA_CONTEXT_LENGTH=32768
                OLLAMA_MAX_OUTPUT_TOKENS=8192
                OLLAMA_MODEL_TOOL_CALLING=false
                OLLAMA_MODEL_THINKING=true
                OLLAMA_MODEL_STREAMING=true
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

    def run_modelctl(
        self,
        *args: str,
        subcommand: str = "add",
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(SCRIPT_PATH),
            subcommand,
            "--env-file",
            str(self.env_path),
            "--settings-file",
            str(self.settings_path),
            *args,
        ]
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        return subprocess.run(command, capture_output=True, text=True, cwd=str(REPO_ROOT), check=False, env=env)

    def start_probe_server(
        self,
        payload: dict | list[dict],
        *,
        docker_log_path: Path | None = None,
    ) -> tuple[ThreadingHTTPServer, threading.Thread, int]:
        if isinstance(payload, list):
            _ProbeRequestHandler.response_payloads = payload
            _ProbeRequestHandler.response_payload = {}
        else:
            _ProbeRequestHandler.response_payloads = None
            _ProbeRequestHandler.response_payload = payload
        _ProbeRequestHandler.requests = []
        _ProbeRequestHandler.docker_log_path = docker_log_path
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ProbeRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 1)
        self.addCleanup(server.shutdown)
        return server, thread, server.server_address[1]

    def test_agent_slot_requires_successful_tool_probe(self) -> None:
        payload = [
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
                            "content": "Readiness confirmed.",
                        },
                    }
                ]
            },
        ]
        _, _, port = self.start_probe_server(payload)
        self.write_env(port)

        result = self.run_modelctl(
            "--model-id",
            "qwen2.5-coder:7b",
            "--display-name",
            "Qwen 2.5 Coder 7B (Agent)",
            "--tool-calling",
            "true",
            "--env-slot",
            "agent",
            "--set-default",
            "true",
            "--pull",
            "false",
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Verified tool calling for 'qwen2.5-coder:7b'", result.stdout)
        settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        agent_entry = settings["github.copilot.chat.customOAIModels"]["qwen2.5-coder:7b"]
        self.assertTrue(agent_entry["toolCalling"])
        env_text = self.env_path.read_text(encoding="utf-8")
        self.assertIn("OLLAMA_AGENT_MODEL=qwen2.5-coder:7b", env_text)
        self.assertIn("OLLAMA_AGENT_MODEL_TOOL_CALLING=true", env_text)
        self.assertEqual(len(_ProbeRequestHandler.requests), 2)
        self.assertEqual(_ProbeRequestHandler.requests[0]["path"], "/v1/chat/completions")
        self.assertEqual(_ProbeRequestHandler.requests[0]["host"], "ollama-api.example.com")
        self.assertEqual(_ProbeRequestHandler.requests[0]["authorization"], "Bearer example-secret-token")
        second_messages = _ProbeRequestHandler.requests[1]["body"]["messages"]
        self.assertEqual(second_messages[-1], {"role": "tool", "tool_call_id": "call_1", "content": json.dumps({"status": "ok"})})

    def test_catalog_registration_keeps_existing_defaults(self) -> None:
        _, _, port = self.start_probe_server({"choices": []})
        self.write_env(port)

        result = self.run_modelctl(
            "--model-id",
            "gemma4:e4b",
            "--display-name",
            "Gemma 4 E4B (Edge)",
            "--max-input-tokens",
            "131072",
            "--max-output-tokens",
            "8192",
            "--tool-calling",
            "false",
            "--set-default",
            "false",
            "--pull",
            "false",
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        entry = settings["github.copilot.chat.customOAIModels"]["gemma4:e4b"]
        self.assertEqual(entry["name"], "Gemma 4 E4B (Edge)")
        self.assertEqual(entry["url"], "https://ollama-api.example.com/v1")
        self.assertEqual(entry["maxInputTokens"], 131072)
        self.assertEqual(entry["maxOutputTokens"], 8192)
        self.assertFalse(entry["toolCalling"])
        env_text = self.env_path.read_text(encoding="utf-8")
        self.assertIn("OLLAMA_MODEL=deepseek-v2:16b-lite-chat-q4_K_M", env_text)
        self.assertNotIn("OLLAMA_MODEL=gemma4:e4b", env_text)
        self.assertEqual(len(_ProbeRequestHandler.requests), 0)

    def test_deepseek_v4_alias_is_rejected_as_ollama_model(self) -> None:
        self.write_env(11436)

        result = self.run_modelctl(
            "--model-id",
            "deepseek-v4-pro",
            "--display-name",
            "DeepSeek V4 Pro",
            "--tool-calling",
            "true",
            "--env-slot",
            "agent",
            "--set-default",
            "true",
            "--pull",
            "false",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("served by the local DeepSeek V4 overlay, not Ollama", result.stderr)
        self.assertFalse(self.settings_path.exists())
        env_text = self.env_path.read_text(encoding="utf-8")
        self.assertNotIn("OLLAMA_AGENT_MODEL=deepseek-v4-pro", env_text)

    def test_deepseek_v4_alias_can_be_registered_as_router_model(self) -> None:
        self.write_env(11436)

        result = self.run_modelctl(
            "--model-id",
            "deepseek-v4-pro",
            "--display-name",
            "DeepSeek V4 Pro",
            "--tool-calling",
            "true",
            "--backend",
            "router",
            "--set-default",
            "false",
            "--pull",
            "false",
            "--skip-tool-verification",
            "true",
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        entry = settings["github.copilot.chat.customOAIModels"]["deepseek-v4-pro"]
        self.assertEqual(entry["name"], "DeepSeek V4 Pro")
        self.assertEqual(entry["url"], "https://ollama-api.example.com/v1")
        self.assertTrue(entry["toolCalling"])
        env_text = self.env_path.read_text(encoding="utf-8")
        self.assertNotIn("OLLAMA_AGENT_MODEL=deepseek-v4-pro", env_text)

    def test_tool_probe_failure_blocks_tool_capable_registration(self) -> None:
        payload = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "ok",
                    },
                }
            ]
        }
        _, _, port = self.start_probe_server(payload)
        self.write_env(port)

        result = self.run_modelctl(
            "--model-id",
            "qwen2.5-coder:7b",
            "--display-name",
            "Qwen 2.5 Coder 7B (Agent)",
            "--tool-calling",
            "true",
            "--env-slot",
            "agent",
            "--set-default",
            "true",
            "--pull",
            "false",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("modelctl tool-calling verification error", result.stderr)
        self.assertFalse(self.settings_path.exists())
        env_text = self.env_path.read_text(encoding="utf-8")
        self.assertNotIn("OLLAMA_AGENT_MODEL=qwen2.5-coder:7b", env_text)

    def test_roundtrip_failure_blocks_tool_capable_registration(self) -> None:
        payload = [
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
        _, _, port = self.start_probe_server(payload)
        self.write_env(port)

        result = self.run_modelctl(
            "--model-id",
            "qwen2.5-coder:7b",
            "--display-name",
            "Qwen 2.5 Coder 7B (Agent)",
            "--tool-calling",
            "true",
            "--env-slot",
            "agent",
            "--set-default",
            "true",
            "--pull",
            "false",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Tool-result round-trip did not finish with a final answer", result.stderr)
        self.assertFalse(self.settings_path.exists())

    def test_pull_happens_before_tool_probe_for_tool_capable_models(self) -> None:
        docker_bin_dir = self.root / "bin"
        docker_bin_dir.mkdir()
        docker_log_path = self.root / "docker.log"
        if os.name == "nt":
            docker_path = docker_bin_dir / "docker.cmd"
            docker_path.write_text(
                textwrap.dedent(
                    f"""
                    @echo off
                    echo %*>>"{docker_log_path}"
                    exit /b 0
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
        else:
            docker_path = docker_bin_dir / "docker"
            docker_path.write_text(
                textwrap.dedent(
                    f"""
                    #!/usr/bin/env sh
                    echo "$@" >> "{docker_log_path}"
                    exit 0
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            docker_path.chmod(0o755)

        payload = [
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
                            "content": "Readiness confirmed.",
                        },
                    }
                ]
            },
        ]
        _, _, port = self.start_probe_server(payload, docker_log_path=docker_log_path)
        self.write_env(port)

        result = self.run_modelctl(
            "--model-id",
            "milkey/deepseek-v2.5-1210:IQ1_S",
            "--display-name",
            "DeepSeek Math V2 Large (IQ1_S)",
            "--tool-calling",
            "true",
            "--env-slot",
            "agent",
            "--set-default",
            "true",
            "--pull",
            "true",
            extra_env={"MODELCTL_DOCKER_COMMAND": str(docker_path)},
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue(docker_log_path.exists())
        docker_commands = docker_log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(docker_commands), 2)
        self.assertIn("compose --env-file", docker_commands[0])
        self.assertIn("up -d ollama", docker_commands[0])
        self.assertIn("compose --env-file", docker_commands[1])
        self.assertIn("ollama pull milkey/deepseek-v2.5-1210:IQ1_S", docker_commands[1])
        self.assertIn("Starting ollama service before pull", result.stdout)
        self.assertIn("Verified tool calling for 'milkey/deepseek-v2.5-1210:IQ1_S'", result.stdout)
        self.assertEqual(len(_ProbeRequestHandler.requests), 2)
        self.assertEqual(_ProbeRequestHandler.requests[0]["docker_log_lines"], 2)


if __name__ == "__main__":
    unittest.main()
