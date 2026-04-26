#!/usr/bin/env python3

import argparse
import base64
import hashlib
import secrets
import sys
from pathlib import Path


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def resolve_env_path(env_file: Path, value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return (env_file.parent / candidate).resolve()


def require_env_key(values: dict[str, str], key: str) -> str:
    value = values.get(key, "").strip()
    if not value:
        raise KeyError(f"Missing required key in env file: {key}")
    return value


def sha_htpasswd_line(username: str, password: str) -> str:
    digest = hashlib.sha1(password.encode("utf-8")).digest()
    encoded = base64.b64encode(digest).decode("ascii")
    return f"{username}:{{SHA}}{encoded}\n"


def ensure_parent(path: Path, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)


def read_text_secret(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def write_text_secret(path: Path, content: str, force: bool, dry_run: bool) -> bool:
    if path.exists() and not force:
        return False
    ensure_parent(path, dry_run)
    if not dry_run:
        path.write_text(content, encoding="utf-8")
    return True


def write_empty_file(path: Path, force: bool, dry_run: bool) -> bool:
    if path.exists() and not force:
        return False
    ensure_parent(path, dry_run)
    if not dry_run:
        path.write_bytes(b"")
    return True


def print_result(action: str, path: Path) -> None:
    print(f"{action}: {path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create local secret placeholders and generated Nginx secrets for this stack."
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--admin-user", default="admin")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.exists():
        raise FileNotFoundError(f"Missing env file: {env_path}")

    values = read_env_file(env_path)
    secrets_dir_value = values.get("SECRETS_DIR", "").strip()
    if secrets_dir_value:
        secrets_dir = resolve_env_path(env_path, secrets_dir_value)
        if not args.dry_run:
            secrets_dir.mkdir(parents=True, exist_ok=True)
        print_result("Ensured secrets directory", secrets_dir)

    api_token_path = resolve_env_path(env_path, require_env_key(values, "NGINX_API_TOKEN_FILE"))
    htpasswd_path = resolve_env_path(env_path, require_env_key(values, "NGINX_BASIC_AUTH_FILE"))
    password_path = resolve_env_path(
        env_path,
        values.get("NGINX_BASIC_AUTH_PASSWORD_FILE", "../ai-tunnel-secrets/nginx-admin-password"),
    )
    cloudflare_token_path = resolve_env_path(env_path, require_env_key(values, "CF_TUNNEL_TOKEN_FILE"))

    api_token = secrets.token_urlsafe(48)
    api_token_written = write_text_secret(api_token_path, api_token + "\n", args.force, args.dry_run)
    print_result("Generated Nginx API token" if api_token_written else "Kept existing Nginx API token", api_token_path)

    generated_password = False
    if password_path.exists() and not args.force:
        admin_password = read_text_secret(password_path)
        if not admin_password:
            raise RuntimeError(f"Nginx admin password file is empty: {password_path}")
        print_result("Kept existing Nginx admin password file", password_path)
    else:
        admin_password = secrets.token_urlsafe(18)
        generated_password = True
        write_text_secret(password_path, admin_password + "\n", True, args.dry_run)
        print_result("Generated Nginx admin password file", password_path)

    htpasswd_line = sha_htpasswd_line(args.admin_user, admin_password)
    write_text_secret(htpasswd_path, htpasswd_line, True, args.dry_run)
    print_result("Updated Nginx basic auth file", htpasswd_path)
    if generated_password:
        print(f"Nginx admin username: {args.admin_user}")
        print(f"Nginx admin password: {admin_password}")
        print("The generated admin password is also stored in the configured password file.")
    else:
        print("Reused the existing Nginx admin password file to regenerate the htpasswd entry.")

    cloudflare_written = write_empty_file(cloudflare_token_path, args.force, args.dry_run)
    print_result(
        "Created empty Cloudflare tunnel token placeholder" if cloudflare_written else "Kept existing Cloudflare tunnel token file",
        cloudflare_token_path,
    )
    if cloudflare_written:
        print("Populate the Cloudflare tunnel token file with the real tunnel token before starting the tunnel profile.")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"bootstrap-secrets error: {exc}", file=sys.stderr)
        raise SystemExit(1)