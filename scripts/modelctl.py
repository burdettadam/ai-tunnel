#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from check_tool_calling import (
    ToolCallingProbeError,
    probe_tool_calling_direct,
    probe_tool_calling_through_nginx,
    resolve_direct_api_token,
)


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value: {value}")


def read_env_file(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return lines, values


def update_env_file(path: Path, updates: dict[str, str]) -> dict[str, str]:
    lines, values = read_env_file(path)
    indexes: dict[str, int] = {}
    for index, line in enumerate(lines):
        if "=" not in line:
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, _ = line.split("=", 1)
        indexes[key.strip()] = index

    for key, value in updates.items():
        rendered = f"{key}={value}\n"
        if key in indexes:
            lines[indexes[key]] = rendered
        else:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] = lines[-1] + "\n"
            lines.append(rendered)
        values[key] = value

    path.write_text("".join(lines), encoding="utf-8")
    return values


def load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_settings(path: Path, settings: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def build_env_updates(args: argparse.Namespace) -> dict[str, str]:
    profile_key_prefix = "OLLAMA_AGENT_" if args.env_slot == "agent" else "OLLAMA_"
    return {
        f"{profile_key_prefix}MODEL": args.model_id,
        f"{profile_key_prefix}MODEL_DISPLAY_NAME": args.display_name,
        f"{profile_key_prefix}MODEL_VSCODE_ID": args.model_id,
        f"{profile_key_prefix}CONTEXT_LENGTH": str(args.max_input_tokens),
        f"{profile_key_prefix}MAX_OUTPUT_TOKENS": str(args.max_output_tokens),
        f"{profile_key_prefix}MODEL_TOOL_CALLING": str(args.tool_calling).lower(),
        f"{profile_key_prefix}MODEL_THINKING": str(args.thinking).lower(),
        f"{profile_key_prefix}MODEL_STREAMING": str(args.streaming).lower(),
    }


def merge_model_map(existing_value: str, model_id: str, upstream_model_id: str) -> str:
    model_map: dict[str, str] = {}
    stripped = existing_value.strip()
    if stripped:
        loaded = json.loads(stripped)
        if not isinstance(loaded, dict):
            raise ValueError("Expected DEEPSEEK_MODEL_MAP_JSON to contain a JSON object")
        model_map = {str(key): str(value) for key, value in loaded.items()}
    model_map[model_id] = upstream_model_id
    return json.dumps(model_map, separators=(",", ":"))


def build_remote_deepseek_env_updates(args: argparse.Namespace, existing_values: dict[str, str]) -> dict[str, str]:
    updates = {
        "DEEPSEEK_ADAPTER_ENABLED": "true",
        "DEEPSEEK_BACKEND_BASE_URL": args.api_base_url,
        "DEEPSEEK_MODEL_MAP_JSON": merge_model_map(
            existing_values.get("DEEPSEEK_MODEL_MAP_JSON", ""),
            args.model_id,
            args.upstream_model_id or args.model_id,
        ),
    }
    if args.set_default:
        profile_key_prefix = "DEEPSEEK_AGENT_" if args.env_slot == "agent" else "DEEPSEEK_CHAT_"
        updates.update(
            {
                f"{profile_key_prefix}MODEL": args.model_id,
                f"{profile_key_prefix}MODEL_DISPLAY_NAME": args.display_name,
                f"{profile_key_prefix}MODEL_VSCODE_ID": args.model_id,
                f"{profile_key_prefix}CONTEXT_LENGTH": str(args.max_input_tokens),
                f"{profile_key_prefix}MAX_OUTPUT_TOKENS": str(args.max_output_tokens),
                f"{profile_key_prefix}MODEL_TOOL_CALLING": str(args.tool_calling).lower(),
                f"{profile_key_prefix}MODEL_VISION": str(args.vision).lower(),
                f"{profile_key_prefix}MODEL_THINKING": str(args.thinking).lower(),
                f"{profile_key_prefix}MODEL_STREAMING": str(args.streaming).lower(),
            }
        )
    return updates


def resolve_public_url(override: str | None, *env_groups: dict[str, str]) -> str:
    if override and override.strip():
        return override.strip()

    for values in env_groups:
        candidate = values.get("AI_TUNNEL_API_PUBLIC_URL", "").strip()
        if candidate:
            return candidate
        candidate = values.get("OLLAMA_API_PUBLIC_URL", "").strip()
        if candidate:
            return candidate

    for values in env_groups:
        api_hostname = values.get("OLLAMA_API_HOSTNAME", "").strip()
        if api_hostname:
            return f"https://{api_hostname}/v1"

    raise KeyError("OLLAMA_API_HOSTNAME, OLLAMA_API_PUBLIC_URL, or AI_TUNNEL_API_PUBLIC_URL is missing from the env file")


def docker_command() -> str:
    return os.environ.get("MODELCTL_DOCKER_COMMAND", "docker")


def pull_model(args: argparse.Namespace, env_path: Path, env_values_before: dict[str, str]) -> None:
    ollama_port = env_values_before.get("OLLAMA_PORT")
    if not ollama_port:
        raise KeyError("OLLAMA_PORT is missing from the env file")

    compose_up = [
        docker_command(),
        "compose",
        "--env-file",
        str(env_path),
        "up",
        "-d",
        "ollama",
    ]
    compose_pull = [
        docker_command(),
        "compose",
        "--env-file",
        str(env_path),
        "run",
        "--rm",
        "-e",
        f"OLLAMA_HOST=http://ollama:{ollama_port}",
        "ollama",
        "pull",
        args.model_id,
    ]

    print("Starting ollama service before pull")
    subprocess.run(compose_up, check=True)
    print(f"Pulling model '{args.model_id}' into Ollama")
    subprocess.run(compose_pull, check=True)


def register_model(args: argparse.Namespace) -> int:
    env_path = Path(args.env_file)
    if not env_path.exists():
        raise FileNotFoundError(f"Missing env file: {env_path}")

    env_updates = build_env_updates(args)

    _, env_values_before = read_env_file(env_path)

    if args.pull:
        pull_model(args, env_path, env_values_before)

    if args.tool_calling and not args.skip_tool_verification:
        verification = probe_tool_calling_through_nginx(env_path.resolve(), args.model_id, timeout=args.tool_verification_timeout)
        function_name = verification["tool_call"]["function"]["name"]
        print(f"Verified tool calling for '{args.model_id}' via function '{function_name}'")
    elif args.tool_calling:
        print(f"Skipping tool-calling verification for '{args.model_id}'")

    if args.set_default:
        env_values = update_env_file(env_path, env_updates)
    else:
        env_values = env_values_before

    api_public_url = os.environ.get("OLLAMA_API_PUBLIC_URL") or env_values.get("OLLAMA_API_PUBLIC_URL") or env_values_before.get("OLLAMA_API_PUBLIC_URL")
    if not api_public_url:
        api_hostname = env_values.get("OLLAMA_API_HOSTNAME") or env_values_before.get("OLLAMA_API_HOSTNAME")
        if not api_hostname:
            raise KeyError("OLLAMA_API_HOSTNAME or OLLAMA_API_PUBLIC_URL is missing from the env file")
        api_public_url = f"https://{api_hostname}/v1"

    settings_path = Path(args.settings_file)
    settings = load_settings(settings_path)
    model_entries = settings.setdefault("github.copilot.chat.customOAIModels", {})
    model_entries[args.model_id] = {
        "name": args.display_name,
        "url": api_public_url,
        "maxInputTokens": args.max_input_tokens,
        "maxOutputTokens": args.max_output_tokens,
        "toolCalling": args.tool_calling,
        "vision": args.vision,
        "thinking": args.thinking,
        "streaming": args.streaming,
    }
    save_settings(settings_path, settings)

    print(f"Registered model '{args.model_id}' in {settings_path}")
    if args.set_default:
        profile_label = "agent model" if args.env_slot == "agent" else "default model"
        print(f"Updated {profile_label} metadata in {env_path}")

    return 0


def register_remote_deepseek(args: argparse.Namespace) -> int:
    env_path = Path(args.env_file)
    if not env_path.exists():
        raise FileNotFoundError(f"Missing env file: {env_path}")

    _, env_values_before = read_env_file(env_path)
    env_updates = build_remote_deepseek_env_updates(args, env_values_before)

    if args.tool_calling and not args.skip_tool_verification:
        api_token = resolve_direct_api_token(api_token=args.api_token, api_token_file=args.api_token_file)
        verification = probe_tool_calling_direct(
            base_url=args.api_base_url,
            api_token=api_token,
            model_id=args.upstream_model_id or args.model_id,
            chat_completions_path=args.chat_completions_path,
            timeout=args.tool_verification_timeout,
        )
        function_name = verification["tool_call"]["function"]["name"]
        print(f"Verified tool calling for '{args.upstream_model_id or args.model_id}' via function '{function_name}'")
    elif args.tool_calling:
        print(f"Skipping tool-calling verification for '{args.upstream_model_id or args.model_id}'")

    env_values = update_env_file(env_path, env_updates)
    public_url = resolve_public_url(args.public_url, env_values, env_values_before)

    settings_path = Path(args.settings_file)
    settings = load_settings(settings_path)
    model_entries = settings.setdefault("github.copilot.chat.customOAIModels", {})
    model_entries[args.model_id] = {
        "name": args.display_name,
        "url": public_url,
        "maxInputTokens": args.max_input_tokens,
        "maxOutputTokens": args.max_output_tokens,
        "toolCalling": args.tool_calling,
        "vision": args.vision,
        "thinking": args.thinking,
        "streaming": args.streaming,
    }
    save_settings(settings_path, settings)

    print(f"Registered model '{args.model_id}' in {settings_path}")
    print(f"Configured remote DeepSeek backend in {env_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Ollama and VS Code model metadata for this repo")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Register a model in VS Code settings and optionally set it as default and pull it")
    add_parser.add_argument("--env-file", default=".env")
    add_parser.add_argument("--settings-file", default=".vscode/settings.json")
    add_parser.add_argument("--model-id", required=True)
    add_parser.add_argument("--display-name", required=True)
    add_parser.add_argument("--max-input-tokens", type=int, default=32768)
    add_parser.add_argument("--max-output-tokens", type=int, default=8192)
    add_parser.add_argument("--tool-calling", type=parse_bool, default=False)
    add_parser.add_argument("--vision", type=parse_bool, default=False)
    add_parser.add_argument("--thinking", type=parse_bool, default=True)
    add_parser.add_argument("--streaming", type=parse_bool, default=True)
    add_parser.add_argument("--env-slot", choices=["default", "agent"], default="default")
    add_parser.add_argument("--set-default", type=parse_bool, default=False)
    add_parser.add_argument("--skip-tool-verification", type=parse_bool, default=False)
    add_parser.add_argument("--tool-verification-timeout", type=float, default=30.0)
    add_parser.add_argument("--pull", type=parse_bool, default=False)
    add_parser.set_defaults(func=register_model)

    remote_parser = subparsers.add_parser("remote-deepseek", help="Register a remote hosted DeepSeek model behind the local adapter")
    remote_parser.add_argument("--env-file", default=".env")
    remote_parser.add_argument("--settings-file", default=".vscode/settings.json")
    remote_parser.add_argument("--model-id", required=True)
    remote_parser.add_argument("--upstream-model-id")
    remote_parser.add_argument("--display-name", required=True)
    remote_parser.add_argument("--api-base-url", required=True)
    remote_parser.add_argument("--api-token")
    remote_parser.add_argument("--api-token-file")
    remote_parser.add_argument("--public-url")
    remote_parser.add_argument("--chat-completions-path", default="/chat/completions")
    remote_parser.add_argument("--max-input-tokens", type=int, default=32768)
    remote_parser.add_argument("--max-output-tokens", type=int, default=8192)
    remote_parser.add_argument("--tool-calling", type=parse_bool, default=False)
    remote_parser.add_argument("--vision", type=parse_bool, default=False)
    remote_parser.add_argument("--thinking", type=parse_bool, default=True)
    remote_parser.add_argument("--streaming", type=parse_bool, default=True)
    remote_parser.add_argument("--env-slot", choices=["default", "agent"], default="default")
    remote_parser.add_argument("--set-default", type=parse_bool, default=False)
    remote_parser.add_argument("--skip-tool-verification", type=parse_bool, default=False)
    remote_parser.add_argument("--tool-verification-timeout", type=float, default=30.0)
    remote_parser.set_defaults(func=register_remote_deepseek)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ToolCallingProbeError as exc:
        print(f"modelctl tool-calling verification error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        print(f"modelctl error: {exc}", file=sys.stderr)
        raise SystemExit(1)
