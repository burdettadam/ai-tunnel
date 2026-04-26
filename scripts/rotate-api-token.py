#!/usr/bin/env python3

import argparse
import os
import secrets
import subprocess
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


def require_env_key(values: dict[str, str], key: str) -> str:
    value = values.get(key, "").strip()
    if not value:
        raise KeyError(f"Missing required key in env file: {key}")
    return value


def resolve_env_path(env_file: Path, value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return (env_file.parent / candidate).resolve()


def copy_to_clipboard(text: str) -> str:
    if os.name == "nt":
        subprocess.run(["clip"], input=text, text=True, check=True)
        return "clip"
    if sys.platform == "darwin":
        subprocess.run(["pbcopy"], input=text, text=True, check=True)
        return "pbcopy"

    clipboard_commands = [
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
    ]
    for command in clipboard_commands:
        try:
            subprocess.run(command, input=text, text=True, check=True)
            return " ".join(command)
        except FileNotFoundError:
            continue
    raise RuntimeError("No supported clipboard command was found")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rotate the Nginx API token and restart nginx so the updated secret is loaded."
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--token-length", type=int, default=48)
    parser.add_argument("--copy-to-clipboard", action="store_true")
    parser.add_argument("--no-restart", action="store_true")
    parser.add_argument("--docker-command", default="docker")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.exists():
        raise FileNotFoundError(f"Missing env file: {env_path}")

    values = read_env_file(env_path)
    project_name = values.get("COMPOSE_PROJECT_NAME", "").strip()
    if not project_name:
        raise KeyError("Missing required key in env file: COMPOSE_PROJECT_NAME")

    token_path = resolve_env_path(env_path, require_env_key(values, "NGINX_API_TOKEN_FILE"))
    token_path.parent.mkdir(parents=True, exist_ok=True)

    token = secrets.token_urlsafe(args.token_length)
    token_path.write_text(token + "\n", encoding="utf-8")
    print(f"Rotated API token file: {token_path}")

    if args.copy_to_clipboard:
        clipboard_command = copy_to_clipboard(token)
        print(f"Copied API token to the clipboard with {clipboard_command}")

    if args.no_restart:
        print("Skipped nginx restart. Restart nginx before using the rotated token.")
        return 0

    restart_command = [
        args.docker_command,
        "compose",
        "--env-file",
        str(env_path),
        "restart",
        "nginx",
    ]
    print("Restarting nginx so it reloads the rendered bearer token config")
    subprocess.run(restart_command, check=True)
    print("Nginx restarted successfully")
    print("Update any VS Code provider entry that uses this token in secure storage.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"rotate-api-token error: {exc}", file=sys.stderr)
        raise SystemExit(1)