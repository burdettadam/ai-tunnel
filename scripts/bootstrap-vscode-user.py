#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


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


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def parse_int(value: str, default: int) -> int:
    stripped = value.strip()
    if not stripped:
        return default
    return int(stripped)


def default_vscode_user_dir(channel: str) -> Path:
    app_name = "Code - Insiders" if channel == "insiders" else "Code"
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", "").strip()
        if not appdata:
            raise RuntimeError("APPDATA is not set")
        return Path(appdata) / app_name / "User"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name / "User"
    config_root = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if config_root:
        return Path(config_root) / app_name / "User"
    return Path.home() / ".config" / app_name / "User"


def load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return data


def load_json_array(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array in {path}")
    items: list[dict[str, Any]] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"Expected array item {index} in {path} to be a JSON object")
        items.append(item)
    return items


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def upsert_provider(entries: list[dict[str, Any]], provider: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    for index, entry in enumerate(entries):
        if entry.get("name") == provider["name"]:
            entries[index] = {**entry, **provider}
            return entries, False

    for index, entry in enumerate(entries):
        if entry.get("vendor") == provider["vendor"] and entry.get("url") == provider["url"]:
            entries[index] = {**entry, **provider}
            return entries, False

    entries.append(provider)
    return entries, True


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


def first_non_empty(values: dict[str, str], keys: list[str], default: str = "") -> str:
    for key in keys:
        value = values.get(key, "").strip()
        if value:
            return value
    return default


def build_model_entry(
    values: dict[str, str],
    *,
    model_key: str,
    model_id_key: str,
    display_name_key: str,
    context_length_keys: list[str],
    max_output_tokens_keys: list[str],
    tool_calling_keys: list[str],
    vision_keys: list[str],
    thinking_keys: list[str],
    streaming_keys: list[str],
) -> tuple[str, dict[str, Any], str]:
    model_id = values.get(model_id_key, "").strip() or require_env_key(values, model_key)
    display_name = values.get(display_name_key, "").strip() or model_id
    api_public_url = values.get("OLLAMA_API_PUBLIC_URL", "").strip()
    if not api_public_url:
        api_hostname = require_env_key(values, "OLLAMA_API_HOSTNAME")
        api_public_url = f"https://{api_hostname}/v1"

    entry = {
        "name": display_name,
        "url": api_public_url,
        "maxInputTokens": parse_int(first_non_empty(values, context_length_keys), 32768),
        "maxOutputTokens": parse_int(first_non_empty(values, max_output_tokens_keys), 8192),
        "toolCalling": parse_bool(first_non_empty(values, tool_calling_keys, "false")),
        "vision": parse_bool(first_non_empty(values, vision_keys, "false")),
        "thinking": parse_bool(first_non_empty(values, thinking_keys, "true")),
        "streaming": parse_bool(first_non_empty(values, streaming_keys, "true")),
    }
    return model_id, entry, api_public_url


def build_model_entries(values: dict[str, str]) -> tuple[list[tuple[str, dict[str, Any]]], str]:
    model_entries: list[tuple[str, dict[str, Any]]] = []

    default_model_id, default_model_entry, api_public_url = build_model_entry(
        values,
        model_key="OLLAMA_MODEL",
        model_id_key="OLLAMA_MODEL_VSCODE_ID",
        display_name_key="OLLAMA_MODEL_DISPLAY_NAME",
        context_length_keys=["OLLAMA_CONTEXT_LENGTH"],
        max_output_tokens_keys=["OLLAMA_MAX_OUTPUT_TOKENS"],
        tool_calling_keys=["OLLAMA_MODEL_TOOL_CALLING"],
        vision_keys=["OLLAMA_MODEL_VISION"],
        thinking_keys=["OLLAMA_MODEL_THINKING"],
        streaming_keys=["OLLAMA_MODEL_STREAMING"],
    )
    model_entries.append((default_model_id, default_model_entry))

    agent_model_id = first_non_empty(values, ["OLLAMA_AGENT_MODEL_VSCODE_ID", "OLLAMA_AGENT_MODEL"])
    if agent_model_id:
        agent_model_id, agent_model_entry, _ = build_model_entry(
            values,
            model_key="OLLAMA_AGENT_MODEL",
            model_id_key="OLLAMA_AGENT_MODEL_VSCODE_ID",
            display_name_key="OLLAMA_AGENT_MODEL_DISPLAY_NAME",
            context_length_keys=["OLLAMA_AGENT_CONTEXT_LENGTH", "OLLAMA_CONTEXT_LENGTH"],
            max_output_tokens_keys=["OLLAMA_AGENT_MAX_OUTPUT_TOKENS", "OLLAMA_MAX_OUTPUT_TOKENS"],
            tool_calling_keys=["OLLAMA_AGENT_MODEL_TOOL_CALLING"],
            vision_keys=["OLLAMA_AGENT_MODEL_VISION", "OLLAMA_MODEL_VISION"],
            thinking_keys=["OLLAMA_AGENT_MODEL_THINKING", "OLLAMA_MODEL_THINKING"],
            streaming_keys=["OLLAMA_AGENT_MODEL_STREAMING", "OLLAMA_MODEL_STREAMING"],
        )
        model_entries.append((agent_model_id, agent_model_entry))

    return model_entries, api_public_url


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bootstrap VS Code user-space model settings for this repo."
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--channel", choices=["insiders", "stable"], default="insiders")
    parser.add_argument("--settings-file")
    parser.add_argument("--chat-models-file")
    parser.add_argument("--provider-name", default="AI Tunnel")
    parser.add_argument("--copy-api-key", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.exists():
        raise FileNotFoundError(f"Missing env file: {env_path}")

    values = read_env_file(env_path)
    user_dir = default_vscode_user_dir(args.channel)
    settings_path = Path(args.settings_file).resolve() if args.settings_file else user_dir / "settings.json"
    chat_models_path = Path(args.chat_models_file).resolve() if args.chat_models_file else user_dir / "chatLanguageModels.json"

    model_entries, api_public_url = build_model_entries(values)
    provider_entry = {
        "name": args.provider_name,
        "vendor": "openai",
        "url": api_public_url,
    }

    settings = load_json_object(settings_path)
    settings_model_entries = settings.setdefault("github.copilot.chat.customOAIModels", {})
    if not isinstance(settings_model_entries, dict):
        raise ValueError(f"Expected github.copilot.chat.customOAIModels to be a JSON object in {settings_path}")
    for model_id, model_entry in model_entries:
        existing_model_entry = settings_model_entries.get(model_id, {})
        if not isinstance(existing_model_entry, dict):
            existing_model_entry = {}
        settings_model_entries[model_id] = {**existing_model_entry, **model_entry}
    save_json(settings_path, settings)

    providers = load_json_array(chat_models_path)
    providers, created_provider = upsert_provider(providers, provider_entry)
    save_json(chat_models_path, providers)

    api_token_path = resolve_env_path(env_path, require_env_key(values, "NGINX_API_TOKEN_FILE"))
    print(f"Updated VS Code user settings: {settings_path}")
    for model_id, _ in model_entries:
        print(f"Registered model '{model_id}' in user settings")
    print(f"Updated chat language models: {chat_models_path}")
    print(
        f"{'Added' if created_provider else 'Updated'} provider '{args.provider_name}' -> {api_public_url}"
    )
    print(f"API key source file: {api_token_path}")

    if args.copy_api_key:
        api_key = api_token_path.read_text(encoding="utf-8").strip()
        if not api_key:
            raise RuntimeError(f"API key file is empty: {api_token_path}")
        clipboard_command = copy_to_clipboard(api_key)
        print(f"Copied API key to the clipboard with {clipboard_command}")
        print("Next step: open 'Chat: Manage Language Models' in VS Code and paste the API key for the provider.")
    else:
        print("Next step: open 'Chat: Manage Language Models' in VS Code and paste the API key from the source file.")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"bootstrap-vscode-user error: {exc}", file=sys.stderr)
        raise SystemExit(1)