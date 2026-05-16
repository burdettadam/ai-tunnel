import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "lock-librechat-registration.py"


class LockLibreChatRegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.secrets_dir = self.root / "ai-tunnel-secrets"
        self.secrets_dir.mkdir()
        self.env_path = self.workspace / ".env"
        self.librechat_env_path = self.secrets_dir / "librechat.env"
        self.env_path.write_text(
            "LIBRECHAT_ENV_FILE=../ai-tunnel-secrets/librechat.env\n",
            encoding="utf-8",
        )

    def run_lock(self, *args: str) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(SCRIPT_PATH),
            "--env-file",
            str(self.env_path),
            "--no-restart",
            *args,
        ]
        result = subprocess.run(command, capture_output=True, text=True, cwd=str(REPO_ROOT), check=False)
        if result.returncode != 0:
            self.fail(f"lock-librechat-registration.py failed with exit code {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        return result

    def test_disables_existing_registration_flags(self) -> None:
        self.librechat_env_path.write_text(
            textwrap.dedent(
                """
                ALLOW_EMAIL_LOGIN=true
                ALLOW_REGISTRATION=true
                ALLOW_SOCIAL_REGISTRATION=true
                CUSTOM=value
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        result = self.run_lock()
        content = self.librechat_env_path.read_text(encoding="utf-8")

        self.assertIn("ALLOW_REGISTRATION=false", content)
        self.assertIn("ALLOW_SOCIAL_REGISTRATION=false", content)
        self.assertIn("CUSTOM=value", content)
        self.assertIn("Updated LibreChat registration settings", result.stdout)
        self.assertIn("Skipped LibreChat restart", result.stdout)

    def test_adds_missing_registration_flags(self) -> None:
        self.librechat_env_path.write_text("CUSTOM=value\n", encoding="utf-8")

        self.run_lock()
        content = self.librechat_env_path.read_text(encoding="utf-8")

        self.assertIn("ALLOW_REGISTRATION=false", content)
        self.assertIn("ALLOW_SOCIAL_REGISTRATION=false", content)

    def test_dry_run_does_not_write_changes(self) -> None:
        self.librechat_env_path.write_text("ALLOW_REGISTRATION=true\n", encoding="utf-8")

        result = self.run_lock("--dry-run")

        self.assertEqual(
            self.librechat_env_path.read_text(encoding="utf-8"),
            "ALLOW_REGISTRATION=true\n",
        )
        self.assertIn("Dry run only", result.stdout)


if __name__ == "__main__":
    unittest.main()
