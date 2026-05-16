#!/usr/bin/env python3

import argparse
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


def set_env_values(path: Path, updates: dict[str, str], dry_run: bool) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Missing LibreChat env file: {path}")

    lines = path.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    changed: list[str] = []
    output: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue

        key, current_value = line.split("=", 1)
        normalized_key = key.strip()
        if normalized_key in updates:
            seen.add(normalized_key)
            new_value = updates[normalized_key]
            if current_value.strip() != new_value:
                changed.append(normalized_key)
            output.append(f"{normalized_key}={new_value}")
        else:
            output.append(line)

    for key, value in updates.items():
        if key not in seen:
            changed.append(key)
            output.append(f"{key}={value}")

    if not dry_run and changed:
        path.write_text("\n".join(output) + "\n", encoding="utf-8")

    return changed


def build_restart_command(args: argparse.Namespace, env_path: Path) -> list[str]:
    command = [args.docker_command, "compose"]
    for compose_file in args.compose_file:
        command.extend(["-f", compose_file])
    command.extend(["--env-file", str(env_path), "restart", "librechat"])
    return command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Disable public LibreChat registration after the first admin account has been created."
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument(
        "--compose-file",
        action="append",
        default=["compose.yaml", "compose.librechat.yaml"],
        help="Compose file to include when restarting LibreChat. Repeat to add overlays.",
    )
    parser.add_argument("--docker-command", default="docker")
    parser.add_argument("--no-restart", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.exists():
        raise FileNotFoundError(f"Missing env file: {env_path}")

    values = read_env_file(env_path)
    librechat_env_path = resolve_env_path(env_path, require_env_key(values, "LIBRECHAT_ENV_FILE"))
    updates = {
        "ALLOW_REGISTRATION": "false",
        "ALLOW_SOCIAL_REGISTRATION": "false",
    }
    changed = set_env_values(librechat_env_path, updates, args.dry_run)

    if changed:
        print(f"Updated LibreChat registration settings in {librechat_env_path}: {', '.join(changed)}")
    else:
        print(f"LibreChat registration is already locked in {librechat_env_path}")

    if args.dry_run:
        print("Dry run only. No restart performed.")
        return 0

    if args.no_restart:
        print("Skipped LibreChat restart. Restart LibreChat before publishing it.")
        return 0

    restart_command = build_restart_command(args, env_path)
    print("Restarting LibreChat so registration settings take effect")
    subprocess.run(restart_command, check=True)
    print("LibreChat restarted successfully")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"lock-librechat-registration error: {exc}", file=sys.stderr)
        raise SystemExit(1)
