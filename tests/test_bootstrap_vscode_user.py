import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "bootstrap-vscode-user.py"


class BootstrapVSCodeUserTests(unittest.TestCase):
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
        self.env_path.write_text(
            textwrap.dedent(
                """
                OLLAMA_API_PUBLIC_URL=https://ollama-api.example.com/v1
                OLLAMA_MODEL=deepseek-v2:16b-lite-chat-q4_K_M
                OLLAMA_MODEL_DISPLAY_NAME=DeepSeek V2 Lite
                OLLAMA_MODEL_VSCODE_ID=deepseek-v2:16b-lite-chat-q4_K_M
                OLLAMA_CONTEXT_LENGTH=32768
                OLLAMA_MAX_OUTPUT_TOKENS=8192
                OLLAMA_MODEL_TOOL_CALLING=false
                OLLAMA_MODEL_THINKING=true
                OLLAMA_MODEL_STREAMING=true
                NGINX_API_TOKEN_FILE=../ai-tunnel-secrets/ollama-api-token
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        self.user_dir = self.root / "user"
        self.settings_path = self.user_dir / "settings.json"
        self.chat_models_path = self.user_dir / "chatLanguageModels.json"

    def run_bootstrap(self, *args: str) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(SCRIPT_PATH),
            "--env-file",
            str(self.env_path),
            "--settings-file",
            str(self.settings_path),
            "--chat-models-file",
            str(self.chat_models_path),
            *args,
        ]
        result = subprocess.run(command, capture_output=True, text=True, cwd=str(REPO_ROOT), check=False)
        if result.returncode != 0:
            self.fail(
                "bootstrap-vscode-user.py failed with exit code "
                f"{result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )
        return result

    def test_creates_user_settings_and_provider_entries(self) -> None:
        result = self.run_bootstrap()

        settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        chat_models = json.loads(self.chat_models_path.read_text(encoding="utf-8"))

        model_entry = settings["github.copilot.chat.customOAIModels"]["deepseek-v2:16b-lite-chat-q4_K_M"]
        self.assertEqual(model_entry["name"], "DeepSeek V2 Lite")
        self.assertEqual(model_entry["url"], "https://ollama-api.example.com/v1")
        self.assertEqual(model_entry["maxInputTokens"], 32768)
        self.assertEqual(model_entry["maxOutputTokens"], 8192)
        self.assertFalse(model_entry["toolCalling"])
        self.assertTrue(model_entry["thinking"])
        self.assertTrue(model_entry["streaming"])

        self.assertEqual(
            chat_models,
            [
                {
                    "name": "AI Tunnel",
                    "vendor": "openai",
                    "url": "https://ollama-api.example.com/v1",
                }
            ],
        )
        self.assertIn("Registered model 'deepseek-v2:16b-lite-chat-q4_K_M'", result.stdout)
        self.assertIn("Added provider 'AI Tunnel'", result.stdout)

    def test_updates_existing_provider_without_duplication(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(
            json.dumps(
                {
                    "task.allowAutomaticTasks": "on",
                    "github.copilot.chat.customOAIModels": {
                        "deepseek-v2:16b-lite-chat-q4_K_M": {
                            "name": "Old Name",
                            "url": "https://old.example.com/v1"
                        }
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self.chat_models_path.write_text(
            json.dumps(
                [
                    {
                        "name": "Copilot",
                        "vendor": "copilot"
                    },
                    {
                        "name": "Ollama",
                        "vendor": "ollama",
                        "url": "http://localhost:11434"
                    },
                    {
                        "name": "AI Tunnel",
                        "vendor": "openai",
                        "url": "https://old.example.com/v1"
                    }
                ],
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        self.run_bootstrap()

        settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        chat_models = json.loads(self.chat_models_path.read_text(encoding="utf-8"))

        self.assertEqual(settings["task.allowAutomaticTasks"], "on")
        self.assertEqual(
            settings["github.copilot.chat.customOAIModels"]["deepseek-v2:16b-lite-chat-q4_K_M"]["name"],
            "DeepSeek V2 Lite",
        )

        self.assertEqual(len(chat_models), 3)
        self.assertEqual(chat_models[0]["name"], "Copilot")
        self.assertEqual(chat_models[1]["name"], "Ollama")
        self.assertEqual(chat_models[2]["name"], "AI Tunnel")
        self.assertEqual(chat_models[2]["url"], "https://ollama-api.example.com/v1")

    def test_registers_optional_agent_profile(self) -> None:
        self.env_path.write_text(
            textwrap.dedent(
                """
                OLLAMA_API_PUBLIC_URL=https://ollama-api.example.com/v1
                OLLAMA_MODEL=deepseek-v2:16b-lite-chat-q4_K_M
                OLLAMA_MODEL_DISPLAY_NAME=DeepSeek V2 Lite
                OLLAMA_MODEL_VSCODE_ID=deepseek-v2:16b-lite-chat-q4_K_M
                OLLAMA_CONTEXT_LENGTH=32768
                OLLAMA_MAX_OUTPUT_TOKENS=8192
                OLLAMA_MODEL_TOOL_CALLING=false
                OLLAMA_MODEL_THINKING=true
                OLLAMA_MODEL_STREAMING=true
                OLLAMA_AGENT_MODEL=qwen2.5-coder:7b
                OLLAMA_AGENT_MODEL_DISPLAY_NAME=Qwen 2.5 Coder 7B (Agent)
                OLLAMA_AGENT_MODEL_VSCODE_ID=qwen2.5-coder:7b
                OLLAMA_AGENT_CONTEXT_LENGTH=65536
                OLLAMA_AGENT_MAX_OUTPUT_TOKENS=4096
                OLLAMA_AGENT_MODEL_TOOL_CALLING=true
                OLLAMA_AGENT_MODEL_THINKING=true
                OLLAMA_AGENT_MODEL_STREAMING=true
                NGINX_API_TOKEN_FILE=../ai-tunnel-secrets/ollama-api-token
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        result = self.run_bootstrap()

        settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        model_entries = settings["github.copilot.chat.customOAIModels"]

        self.assertIn("deepseek-v2:16b-lite-chat-q4_K_M", model_entries)
        self.assertIn("qwen2.5-coder:7b", model_entries)
        self.assertFalse(model_entries["deepseek-v2:16b-lite-chat-q4_K_M"]["toolCalling"])
        self.assertTrue(model_entries["qwen2.5-coder:7b"]["toolCalling"])
        self.assertEqual(model_entries["qwen2.5-coder:7b"]["maxInputTokens"], 65536)
        self.assertEqual(model_entries["qwen2.5-coder:7b"]["maxOutputTokens"], 4096)
        self.assertIn("Registered model 'qwen2.5-coder:7b'", result.stdout)


if __name__ == "__main__":
    unittest.main()