import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "rotate-api-token.py"


class RotateApiTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.secrets_dir = self.root / "ai-tunnel-secrets"
        self.secrets_dir.mkdir()
        self.token_path = self.secrets_dir / "ollama-api-token"
        self.token_path.write_text("original-token\n", encoding="utf-8")

        self.env_path = self.workspace / ".env"
        self.env_path.write_text(
            textwrap.dedent(
                """
                COMPOSE_PROJECT_NAME=ai-tunnel
                NGINX_API_TOKEN_FILE=../ai-tunnel-secrets/ollama-api-token
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

    def run_rotation(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(SCRIPT_PATH),
            "--env-file",
            str(self.env_path),
            *args,
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            check=False,
            env=env,
        )
        if result.returncode != 0:
            self.fail(
                "rotate-api-token.py failed with exit code "
                f"{result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )
        return result

    def test_rotates_token_without_restart_when_requested(self) -> None:
        result = self.run_rotation("--no-restart")

        rotated_token = self.token_path.read_text(encoding="utf-8").strip()
        self.assertTrue(rotated_token)
        self.assertNotEqual(rotated_token, "original-token")
        self.assertIn("Skipped nginx restart", result.stdout)

    def test_restarts_nginx_after_rotation(self) -> None:
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        docker_cmd = fake_bin / "docker.cmd"
        docker_cmd.write_text(
            "@echo off\r\n"
            "echo FAKE_DOCKER %*\r\n"
            "exit /b 0\r\n",
            encoding="utf-8",
        )

        result = self.run_rotation("--docker-command", str(docker_cmd))

        rotated_token = self.token_path.read_text(encoding="utf-8").strip()
        self.assertTrue(rotated_token)
        self.assertNotEqual(rotated_token, "original-token")
        self.assertIn("Restarting nginx", result.stdout)
        self.assertIn("Nginx restarted successfully", result.stdout)
        self.assertIn("FAKE_DOCKER compose --env-file", result.stdout)
        self.assertIn("restart nginx", result.stdout)


if __name__ == "__main__":
    unittest.main()