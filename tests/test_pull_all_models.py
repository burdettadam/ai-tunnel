import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "pull_all_models.py"


class PullAllModelsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.env_path = self.workspace / ".env"
        self.settings_path = self.workspace / ".vscode" / "settings.json"
        self.settings_path.parent.mkdir(parents=True)

    def run_script(self, *args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(SCRIPT_PATH),
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

    def write_env(self, extra_lines: str = "") -> None:
        self.env_path.write_text(
            textwrap.dedent(
                f"""
                OLLAMA_PORT=11434
                OLLAMA_MODEL=deepseek-v2:16b-lite-chat-q4_K_M
                OLLAMA_AGENT_MODEL=milkey/deepseek-v2.5-1210:IQ1_S
                OLLAMA_LOCAL_SMOKE_MODEL=qwen2.5:0.5b
                {extra_lines}
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

    def write_settings(self, model_ids: list[str]) -> None:
        self.settings_path.write_text(
            json.dumps(
                {
                    "github.copilot.chat.customOAIModels": {
                        model_id: {
                            "name": model_id,
                            "url": "https://ollama-api.example.com/v1",
                            "maxInputTokens": 4096,
                            "maxOutputTokens": 4096,
                            "toolCalling": False,
                            "vision": False,
                            "thinking": True,
                            "streaming": True,
                        }
                        for model_id in model_ids
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def make_fake_docker(self, log_path: Path) -> Path:
        docker_bin_dir = self.root / "bin"
        docker_bin_dir.mkdir()
        if os.name == "nt":
            docker_path = docker_bin_dir / "docker.cmd"
            docker_path.write_text(
                textwrap.dedent(
                    f"""
                    @echo off
                    echo %*>>"{log_path}"
                    echo %* | findstr /C:" ps " >nul
                    if not errorlevel 1 exit /b 0
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
                    echo "$@" >> "{log_path}"
                    if [ "$1" = "compose" ] && [ "$4" = "ps" ]; then
                      exit 0
                    fi
                    exit 0
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            docker_path.chmod(0o755)
        return docker_path

    def test_pull_all_models_deduplicates_env_and_settings_models(self) -> None:
        docker_log_path = self.root / "docker.log"
        docker_path = self.make_fake_docker(docker_log_path)
        self.write_env()
        self.write_settings(
            [
                "deepseek-v2:16b-lite-chat-q4_K_M",
                "milkey/deepseek-v2.5-1210:IQ1_S",
                "gemma4:e4b",
                "qwen2.5:3b",
                "qwen2.5:0.5b",
            ]
        )

        result = self.run_script(extra_env={"MODEL_PULL_DOCKER_COMMAND": str(docker_path)})

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Pulling 5 local models", result.stdout)
        docker_commands = docker_log_path.read_text(encoding="utf-8").splitlines()
        self.assertGreaterEqual(len(docker_commands), 7)
        self.assertIn("compose -f compose.yaml --env-file", docker_commands[0])
        self.assertIn("ps --services --status running", docker_commands[0])
        self.assertIn("compose -f compose.yaml --env-file", docker_commands[1])
        self.assertIn("up -d ollama", docker_commands[1])
        self.assertIn("exec ollama ollama pull deepseek-v2:16b-lite-chat-q4_K_M", docker_commands[2])
        self.assertIn("exec ollama ollama pull milkey/deepseek-v2.5-1210:IQ1_S", docker_commands[3])
        self.assertIn("exec ollama ollama pull qwen2.5:0.5b", docker_commands[4])
        self.assertIn("exec ollama ollama pull gemma4:e4b", docker_commands[5])
        self.assertIn("exec ollama ollama pull qwen2.5:3b", docker_commands[6])

    def test_pull_all_models_rejects_cloud_backed_ids(self) -> None:
        self.write_env()
        self.write_settings(["deepseek-v4-pro"])

        result = self.run_script()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cloud-backed Ollama model ids are not supported in this repo", result.stderr)


if __name__ == "__main__":
    unittest.main()