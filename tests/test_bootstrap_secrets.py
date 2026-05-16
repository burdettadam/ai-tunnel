import base64
import hashlib
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "bootstrap-secrets.py"


def htpasswd_line(username: str, password: str) -> str:
    digest = hashlib.sha1(password.encode("utf-8")).digest()
    encoded = base64.b64encode(digest).decode("ascii")
    return f"{username}:{{SHA}}{encoded}"


class BootstrapSecretsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.secrets_dir = self.root / "ai-tunnel-secrets"
        self.env_path = self.workspace / ".env"
        self.env_path.write_text(
            textwrap.dedent(
                """
                SECRETS_DIR=../ai-tunnel-secrets
                CF_TUNNEL_TOKEN_FILE=../ai-tunnel-secrets/cloudflared-token
                NGINX_API_TOKEN_FILE=../ai-tunnel-secrets/ollama-api-token
                NGINX_BASIC_AUTH_PASSWORD_FILE=../ai-tunnel-secrets/nginx-admin-password
                NGINX_BASIC_AUTH_FILE=../ai-tunnel-secrets/nginx-htpasswd
                LIBRECHAT_ENV_FILE=../ai-tunnel-secrets/librechat.env
                PUBMED_MCP_ENV_FILE=../ai-tunnel-secrets/pubmed-mcp.env
                GITHUB_MCP_ENV_FILE=../ai-tunnel-secrets/github-mcp.env
                DEEPSEEK_V4_ENV_FILE=../ai-tunnel-secrets/deepseek-v4.env
                LIBRECHAT_PUBLIC_URL=https://librechat.example.com
                LIBRECHAT_PORT=3080
                LIBRECHAT_RAG_PORT=8000
                OLLAMA_PORT=11434
                LIBRECHAT_RAG_EMBEDDINGS_MODEL=nomic-embed-text
                GITHUB_MCP_TOOLSETS=repos,issues,pull_requests,users
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

    def run_bootstrap(self, *args: str) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(SCRIPT_PATH),
            "--env-file",
            str(self.env_path),
            *args,
        ]
        result = subprocess.run(command, capture_output=True, text=True, cwd=str(REPO_ROOT), check=False)
        if result.returncode != 0:
            self.fail(f"bootstrap-secrets.py failed with exit code {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        return result

    def test_creates_secret_files_and_derives_htpasswd(self) -> None:
        result = self.run_bootstrap("--admin-user", "alice")

        api_token = (self.secrets_dir / "ollama-api-token").read_text(encoding="utf-8").strip()
        admin_password = (self.secrets_dir / "nginx-admin-password").read_text(encoding="utf-8").strip()
        htpasswd = (self.secrets_dir / "nginx-htpasswd").read_text(encoding="utf-8").strip()
        cloudflare = self.secrets_dir / "cloudflared-token"
        librechat_env = (self.secrets_dir / "librechat.env").read_text(encoding="utf-8")
        pubmed_mcp_env = (self.secrets_dir / "pubmed-mcp.env").read_text(encoding="utf-8")
        github_mcp_env = (self.secrets_dir / "github-mcp.env").read_text(encoding="utf-8")
        deepseek_v4_env = (self.secrets_dir / "deepseek-v4.env").read_text(encoding="utf-8")

        self.assertTrue(api_token)
        self.assertTrue(admin_password)
        self.assertEqual(htpasswd, htpasswd_line("alice", admin_password))
        self.assertTrue(cloudflare.exists())
        self.assertEqual(cloudflare.read_text(encoding="utf-8"), "")
        self.assertIn("DOMAIN_CLIENT=https://librechat.example.com", librechat_env)
        self.assertIn("ALLOW_REGISTRATION=true", librechat_env)
        self.assertIn("EMBEDDINGS_MODEL=nomic-embed-text", librechat_env)
        self.assertIn("CREDS_KEY=", librechat_env)
        self.assertIn("NCBI_ADMIN_EMAIL=", pubmed_mcp_env)
        self.assertIn("NCBI_API_KEY=", pubmed_mcp_env)
        self.assertIn("UNPAYWALL_EMAIL=", pubmed_mcp_env)
        self.assertIn("GITHUB_PERSONAL_ACCESS_TOKEN=", github_mcp_env)
        self.assertIn("GITHUB_TOOLSETS=repos,issues,pull_requests,users", github_mcp_env)
        self.assertIn("HF_TOKEN=", deepseek_v4_env)
        self.assertNotIn("DEEPSEEK_API_KEY", deepseek_v4_env)
        self.assertIn("Generated LibreChat env file", result.stdout)
        self.assertIn("Generated PubMed MCP env file", result.stdout)
        self.assertIn("Generated GitHub MCP env file", result.stdout)
        self.assertIn("Generated DeepSeek V4 env file", result.stdout)
        self.assertIn("Generated Nginx admin password file", result.stdout)
        self.assertIn("Updated Nginx basic auth file", result.stdout)

    def test_reuses_existing_password_file_and_regenerates_htpasswd(self) -> None:
        self.run_bootstrap()
        original_token = (self.secrets_dir / "ollama-api-token").read_text(encoding="utf-8")
        original_password = (self.secrets_dir / "nginx-admin-password").read_text(encoding="utf-8").strip()

        (self.secrets_dir / "nginx-htpasswd").write_text("stale\n", encoding="utf-8")
        result = self.run_bootstrap()

        current_token = (self.secrets_dir / "ollama-api-token").read_text(encoding="utf-8")
        current_password = (self.secrets_dir / "nginx-admin-password").read_text(encoding="utf-8").strip()
        current_htpasswd = (self.secrets_dir / "nginx-htpasswd").read_text(encoding="utf-8").strip()

        self.assertEqual(current_token, original_token)
        self.assertEqual(current_password, original_password)
        self.assertEqual(current_htpasswd, htpasswd_line("admin", original_password))
        self.assertIn("Kept existing Nginx admin password file", result.stdout)
        self.assertIn("Reused the existing Nginx admin password file", result.stdout)

    def test_reuses_existing_librechat_env_without_force(self) -> None:
        self.run_bootstrap()
        librechat_path = self.secrets_dir / "librechat.env"
        pubmed_path = self.secrets_dir / "pubmed-mcp.env"
        github_path = self.secrets_dir / "github-mcp.env"
        deepseek_path = self.secrets_dir / "deepseek-v4.env"
        librechat_path.write_text("ALLOW_REGISTRATION=false\nCUSTOM=value\n", encoding="utf-8")
        pubmed_path.write_text("NCBI_ADMIN_EMAIL=researcher@example.com\n", encoding="utf-8")
        github_path.write_text("GITHUB_PERSONAL_ACCESS_TOKEN=existing\n", encoding="utf-8")
        deepseek_path.write_text("HF_TOKEN=existing\n", encoding="utf-8")

        result = self.run_bootstrap()

        self.assertEqual(
            librechat_path.read_text(encoding="utf-8"),
            "ALLOW_REGISTRATION=false\nCUSTOM=value\n",
        )
        self.assertEqual(
            pubmed_path.read_text(encoding="utf-8"),
            "NCBI_ADMIN_EMAIL=researcher@example.com\n",
        )
        self.assertEqual(
            github_path.read_text(encoding="utf-8"),
            "GITHUB_PERSONAL_ACCESS_TOKEN=existing\n",
        )
        self.assertEqual(
            deepseek_path.read_text(encoding="utf-8"),
            "HF_TOKEN=existing\n",
        )
        self.assertIn("Kept existing LibreChat env file", result.stdout)
        self.assertIn("Kept existing PubMed MCP env file", result.stdout)
        self.assertIn("Kept existing GitHub MCP env file", result.stdout)
        self.assertIn("Kept existing DeepSeek V4 env file", result.stdout)

    def test_force_rotates_managed_password_and_api_token(self) -> None:
        self.run_bootstrap()
        original_token = (self.secrets_dir / "ollama-api-token").read_text(encoding="utf-8").strip()
        original_password = (self.secrets_dir / "nginx-admin-password").read_text(encoding="utf-8").strip()

        self.run_bootstrap("--force")

        rotated_token = (self.secrets_dir / "ollama-api-token").read_text(encoding="utf-8").strip()
        rotated_password = (self.secrets_dir / "nginx-admin-password").read_text(encoding="utf-8").strip()
        rotated_htpasswd = (self.secrets_dir / "nginx-htpasswd").read_text(encoding="utf-8").strip()
        rotated_librechat_env = (self.secrets_dir / "librechat.env").read_text(encoding="utf-8")
        rotated_pubmed_mcp_env = (self.secrets_dir / "pubmed-mcp.env").read_text(encoding="utf-8")
        rotated_github_mcp_env = (self.secrets_dir / "github-mcp.env").read_text(encoding="utf-8")
        rotated_deepseek_v4_env = (self.secrets_dir / "deepseek-v4.env").read_text(encoding="utf-8")

        self.assertNotEqual(rotated_token, original_token)
        self.assertNotEqual(rotated_password, original_password)
        self.assertEqual(rotated_htpasswd, htpasswd_line("admin", rotated_password))
        self.assertIn("ALLOW_REGISTRATION=true", rotated_librechat_env)
        self.assertIn("NCBI_REQUEST_DELAY_MS=334", rotated_pubmed_mcp_env)
        self.assertIn("GITHUB_PERSONAL_ACCESS_TOKEN=", rotated_github_mcp_env)
        self.assertIn("HF_TOKEN=", rotated_deepseek_v4_env)


if __name__ == "__main__":
    unittest.main()
