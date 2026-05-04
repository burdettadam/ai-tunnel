#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


BYOK_BOOTSTRAP_EXTENSION_FOLDER = "ai-tunnel.byok-bootstrap-0.0.1"
BYOK_BOOTSTRAP_EXTENSION_JS = r'''
const fs = require('fs/promises');
const path = require('path');
const vscode = require('vscode');

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function readJsonArray(filePath) {
    try {
        const raw = await fs.readFile(filePath, 'utf8');
        const data = JSON.parse(raw || '[]');
        return Array.isArray(data) ? data : [];
    } catch (error) {
        if (error && error.code === 'ENOENT') {
            return [];
        }
        throw error;
    }
}

async function writeJsonArray(filePath, entries) {
    await fs.mkdir(path.dirname(filePath), { recursive: true });
    await fs.writeFile(filePath, `${JSON.stringify(entries, null, 2)}\n`, 'utf8');
}

function isBlankOpenAICompatible(entry) {
    if (!entry || entry.vendor !== 'customoai' || entry.name !== 'OpenAI Compatible') {
        return false;
    }
    const models = Array.isArray(entry.models) ? entry.models : [];
    return models.length === 0 || models.every(model => !model || (!model.id && !model.name && !model.url));
}

function isManagedEntry(entry, config) {
    if (!entry || typeof entry !== 'object') {
        return false;
    }
    if (entry.vendor === 'openai' && entry.name === config.providerName) {
        return true;
    }
    if (entry.vendor === 'customoai' && (entry.name === config.providerName || entry.name === 'CustomOAI')) {
        return true;
    }
    return isBlankOpenAICompatible(entry);
}

async function removeManagedEntries(config) {
    const entries = await readJsonArray(config.chatModelsFile);
    const filtered = entries.filter(entry => !isManagedEntry(entry, config));
    if (filtered.length !== entries.length) {
        await writeJsonArray(config.chatModelsFile, filtered);
    }
}

async function activate(context) {
    const configPath = path.join(context.extensionPath, 'ai-tunnel-config.json');
    const config = JSON.parse(await fs.readFile(configPath, 'utf8'));
    const apiKey = (await fs.readFile(config.apiTokenPath, 'utf8')).trim();
    if (!apiKey) {
        throw new Error(`AI Tunnel API token file is empty: ${config.apiTokenPath}`);
    }

    const group = {
        name: config.providerName,
        vendor: 'customoai',
        apiKey,
        models: config.models,
    };

    let lastError;
    for (let attempt = 0; attempt < 8; attempt += 1) {
        await removeManagedEntries(config);
        await sleep(500 + attempt * 500);
        try {
            await vscode.commands.executeCommand('lm.addLanguageModelsProviderGroup', group);
            console.info('[AI Tunnel BYOK Bootstrap] configured OpenAI Compatible provider');
            return;
        } catch (error) {
            lastError = error;
            const message = String(error && error.message ? error.message : error);
            if (!message.includes('already exists') && !message.includes('Vendor customoai not found')) {
                throw error;
            }
        }
    }
    throw lastError || new Error('AI Tunnel BYOK Bootstrap failed');
}

function deactivate() {}

module.exports = { activate, deactivate };
'''.lstrip()


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


def default_vscode_extensions_dir(channel: str) -> Path:
    extensions_root = ".vscode-insiders" if channel == "insiders" else ".vscode"
    return Path.home() / extensions_root / "extensions"


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


def load_workspace_model_entries(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}

    settings = load_json_object(path)
    model_entries = settings.get("github.copilot.chat.customOAIModels", {})
    if not isinstance(model_entries, dict):
        raise ValueError(f"Expected github.copilot.chat.customOAIModels to be a JSON object in {path}")

    normalized: dict[str, dict[str, Any]] = {}
    for model_id, model_entry in model_entries.items():
        if isinstance(model_entry, dict):
            normalized[str(model_id)] = model_entry
    return normalized


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def is_secret_input(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("${input:chat.lm.secret.") and value.endswith("}")


def is_blank_openai_compatible_provider(entry: dict[str, Any]) -> bool:
    if entry.get("vendor") != "customoai" or entry.get("name") != "OpenAI Compatible":
        return False
    models = entry.get("models")
    if not isinstance(models, list) or not models:
        return True
    for model in models:
        if not isinstance(model, dict):
            return False
        if any(str(model.get(key, "")).strip() for key in ("id", "name", "url")):
            return False
    return True


def is_managed_chat_provider(entry: dict[str, Any], provider_name: str) -> bool:
    if entry.get("vendor") == "openai" and entry.get("name") == provider_name:
        return True
    if entry.get("vendor") == "customoai" and entry.get("name") in {provider_name, "CustomOAI"}:
        return True
    return is_blank_openai_compatible_provider(entry)


def build_chat_model_models(
    workspace_model_entries: dict[str, dict[str, Any]],
    model_entries: list[tuple[str, dict[str, Any]]],
    api_public_url: str,
) -> list[dict[str, Any]]:
    combined: dict[str, dict[str, Any]] = {}
    for model_id, model_entry in workspace_model_entries.items():
        combined[model_id] = {**model_entry, "url": api_public_url}
    for model_id, model_entry in model_entries:
        combined[model_id] = {**combined.get(model_id, {}), **model_entry, "url": api_public_url}

    models: list[dict[str, Any]] = []
    for model_id, model_entry in combined.items():
        models.append(
            {
                "id": model_id,
                "name": str(model_entry.get("name") or model_id),
                "url": str(model_entry.get("url") or api_public_url),
                "maxInputTokens": int(model_entry.get("maxInputTokens", 32768)),
                "maxOutputTokens": int(model_entry.get("maxOutputTokens", 8192)),
                "toolCalling": bool(model_entry.get("toolCalling", False)),
                "vision": bool(model_entry.get("vision", False)),
                "thinking": bool(model_entry.get("thinking", False)),
                "streaming": bool(model_entry.get("streaming", True)),
            }
        )
    return models


def sync_chat_language_models(
    entries: list[dict[str, Any]],
    provider_name: str,
    models: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None, int]:
    api_key_reference: str | None = None
    for entry in entries:
        if entry.get("vendor") == "customoai" and entry.get("name") in {provider_name, "CustomOAI"}:
            candidate = entry.get("apiKey")
            if is_secret_input(candidate):
                api_key_reference = candidate
                break

    cleaned: list[dict[str, Any]] = []
    removed = 0
    for entry in entries:
        if is_managed_chat_provider(entry, provider_name):
            removed += 1
            continue
        cleaned.append(entry)

    provider: dict[str, Any] = {
        "name": provider_name,
        "vendor": "customoai",
        "models": models,
    }
    if api_key_reference:
        provider["apiKey"] = api_key_reference
    cleaned.append(provider)
    return cleaned, api_key_reference, removed


def install_byok_bootstrap_extension(
    extensions_dir: Path,
    *,
    provider_name: str,
    chat_models_file: Path,
    api_token_path: Path,
    models: list[dict[str, Any]],
) -> Path:
    extension_dir = extensions_dir / BYOK_BOOTSTRAP_EXTENSION_FOLDER
    extension_dir.mkdir(parents=True, exist_ok=True)
    save_json(
        extension_dir / "package.json",
        {
            "name": "byok-bootstrap",
            "displayName": "AI Tunnel BYOK Bootstrap",
            "publisher": "ai-tunnel",
            "version": "0.0.1",
            "engines": {"vscode": "^1.104.0"},
            "categories": ["Other"],
            "activationEvents": ["onStartupFinished"],
            "extensionDependencies": ["GitHub.copilot-chat"],
            "main": "./extension.js",
        },
    )
    (extension_dir / "extension.js").write_text(BYOK_BOOTSTRAP_EXTENSION_JS, encoding="utf-8")
    save_json(
        extension_dir / "ai-tunnel-config.json",
        {
            "providerName": provider_name,
            "chatModelsFile": str(chat_models_file),
            "apiTokenPath": str(api_token_path),
            "models": models,
        },
    )
    return extension_dir


def upsert_provider(entries: list[dict[str, Any]], provider: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    for index, entry in enumerate(entries):
        if entry.get("name") == provider["name"]:
            duplicate_urls = {value for value in (entry.get("url"), provider.get("url")) if value}
            entries[index] = {**entry, **provider}
            return remove_duplicate_openai_providers(entries, provider, duplicate_urls), False

    for index, entry in enumerate(entries):
        if entry.get("vendor") == provider["vendor"] and entry.get("url") == provider["url"]:
            duplicate_urls = {value for value in (entry.get("url"), provider.get("url")) if value}
            entries[index] = {**entry, **provider}
            return remove_duplicate_openai_providers(entries, provider, duplicate_urls), False

    entries.append(provider)
    return remove_duplicate_openai_providers(entries, provider, {provider.get("url")}), True


def remove_duplicate_openai_providers(
    entries: list[dict[str, Any]],
    provider: dict[str, Any],
    duplicate_urls: set[str] | None = None,
) -> list[dict[str, Any]]:
    candidate_urls = {url for url in (duplicate_urls or set()) if url}
    normalized: list[dict[str, Any]] = []
    for entry in entries:
        if (
            entry.get("name") != provider["name"]
            and provider_urls(entry) & candidate_urls
            and entry.get("vendor") in {"openai", "customoai"}
        ):
            continue
        normalized.append(entry)
    return normalized


def provider_urls(entry: dict[str, Any]) -> set[str]:
    urls: set[str] = set()
    top_level_url = entry.get("url")
    if isinstance(top_level_url, str) and top_level_url:
        urls.add(top_level_url)

    models = entry.get("models")
    if isinstance(models, list):
        for model in models:
            if isinstance(model, dict):
                model_url = model.get("url")
                if isinstance(model_url, str) and model_url:
                    urls.add(model_url)

    return urls


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
    api_public_url: str,
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


def resolve_api_public_url(values: dict[str, str]) -> str:
    for key in ("AI_TUNNEL_API_PUBLIC_URL", "OLLAMA_API_PUBLIC_URL"):
        value = values.get(key, "").strip()
        if value:
            return value

    api_hostname = values.get("OLLAMA_API_HOSTNAME", "").strip()
    if api_hostname:
        return f"https://{api_hostname}/v1"

    raise KeyError("Missing OLLAMA_API_HOSTNAME, OLLAMA_API_PUBLIC_URL, or AI_TUNNEL_API_PUBLIC_URL in env file")


def build_model_entries(values: dict[str, str]) -> tuple[list[tuple[str, dict[str, Any]]], str]:
    model_entries: list[tuple[str, dict[str, Any]]] = []
    api_public_url = resolve_api_public_url(values)

    default_model_id, default_model_entry, api_public_url = build_model_entry(
        values,
        api_public_url=api_public_url,
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

    agent_model_key = "OLLAMA_AGENT_MODEL"
    agent_model_id = first_non_empty(values, ["OLLAMA_AGENT_MODEL_VSCODE_ID", "OLLAMA_AGENT_MODEL"])
    if agent_model_id:
        agent_model_id, agent_model_entry, _ = build_model_entry(
            values,
            api_public_url=api_public_url,
            model_key=agent_model_key,
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
    parser.add_argument("--workspace-settings-file")
    parser.add_argument("--chat-models-file")
    parser.add_argument("--extensions-dir")
    parser.add_argument("--provider-name", default="AI Tunnel")
    parser.add_argument("--copy-api-key", action="store_true")
    parser.add_argument(
        "--install-byok-bootstrap-extension",
        dest="install_byok_bootstrap_extension",
        action="store_true",
        default=True,
        help=(
            "Install a tiny local VS Code extension that reads the configured "
            "API token file and stores it through VS Code's language-model "
            "SecretStorage path on the next reload. On by default."
        ),
    )
    parser.add_argument(
        "--no-install-byok-bootstrap-extension",
        dest="install_byok_bootstrap_extension",
        action="store_false",
    )
    parser.add_argument(
        "--reset-byok-migration",
        dest="reset_byok_migration",
        action="store_true",
        default=True,
        help=(
            "Clear Copilot Chat's CustomOAI BYOK migration flag so the next "
            "VS Code launch re-imports github.copilot.chat.customOAIModels. "
            "On by default; pass --no-reset-byok-migration to skip."
        ),
    )
    parser.add_argument(
        "--no-reset-byok-migration",
        dest="reset_byok_migration",
        action="store_false",
    )
    return parser


def reset_byok_migration_for_channel(channel: str) -> None:
    """Invoke scripts/reset-byok-migration.py for the requested channel.

    The reset is best-effort: if VS Code is currently running and the SQLite
    database is locked, we surface the error message but do not fail the
    bootstrap, since the rest of the configuration is still valid.
    """
    reset_script = Path(__file__).resolve().parent / "reset-byok-migration.py"
    if not reset_script.exists():
        print(f"Skipping BYOK migration reset; helper script not found: {reset_script}")
        return
    try:
        subprocess.run(
            [sys.executable, str(reset_script), "--channel", channel],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        print(
            "Warning: BYOK migration reset failed (close VS Code and re-run "
            f"'{reset_script.name}' if you want updated customOAIModels to be "
            f"re-imported). Details: {exc}"
        )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.exists():
        raise FileNotFoundError(f"Missing env file: {env_path}")

    values = read_env_file(env_path)
    user_dir = default_vscode_user_dir(args.channel)
    settings_path = Path(args.settings_file).resolve() if args.settings_file else user_dir / "settings.json"
    workspace_settings_path = (
        Path(args.workspace_settings_file).resolve()
        if args.workspace_settings_file
        else env_path.parent / ".vscode" / "settings.json"
    )
    chat_models_path = Path(args.chat_models_file).resolve() if args.chat_models_file else user_dir / "chatLanguageModels.json"
    extensions_dir = Path(args.extensions_dir).resolve() if args.extensions_dir else default_vscode_extensions_dir(args.channel)

    model_entries, api_public_url = build_model_entries(values)
    workspace_model_entries = load_workspace_model_entries(workspace_settings_path)
    chat_models = build_chat_model_models(workspace_model_entries, model_entries, api_public_url)
    api_token_path = resolve_env_path(env_path, require_env_key(values, "NGINX_API_TOKEN_FILE"))

    settings = load_json_object(settings_path)
    settings_model_entries = settings.setdefault("github.copilot.chat.customOAIModels", {})
    if not isinstance(settings_model_entries, dict):
        raise ValueError(f"Expected github.copilot.chat.customOAIModels to be a JSON object in {settings_path}")
    for model_id, model_entry in workspace_model_entries.items():
        existing_model_entry = settings_model_entries.get(model_id, {})
        if not isinstance(existing_model_entry, dict):
            existing_model_entry = {}
        settings_model_entries[model_id] = {**existing_model_entry, **model_entry, "url": api_public_url}
    for model_id, model_entry in model_entries:
        existing_model_entry = settings_model_entries.get(model_id, {})
        if not isinstance(existing_model_entry, dict):
            existing_model_entry = {}
        settings_model_entries[model_id] = {**existing_model_entry, **model_entry}
    save_json(settings_path, settings)

    providers, api_key_reference, removed_provider_count = sync_chat_language_models(
        load_json_array(chat_models_path),
        args.provider_name,
        chat_models,
    )
    save_json(chat_models_path, providers)

    print(f"Updated VS Code user settings: {settings_path}")
    for model_id, _ in model_entries:
        print(f"Registered model '{model_id}' in user settings")
    print(f"Updated chat language models: {chat_models_path}")
    print(
        f"Configured provider '{args.provider_name}' as OpenAI Compatible (customoai) "
        f"with {len(chat_models)} model(s); removed {removed_provider_count} stale provider entr{'y' if removed_provider_count == 1 else 'ies'}."
    )
    if api_key_reference:
        print("Preserved existing VS Code language-model secret placeholder for the provider.")
    else:
        print("No existing provider secret placeholder found; the BYOK bootstrap extension will create one on next reload.")
    print(f"API key source file: {api_token_path}")

    if args.install_byok_bootstrap_extension and (not args.settings_file or args.extensions_dir):
        extension_dir = install_byok_bootstrap_extension(
            extensions_dir,
            provider_name=args.provider_name,
            chat_models_file=chat_models_path,
            api_token_path=api_token_path,
            models=chat_models,
        )
        print(f"Installed AI Tunnel BYOK bootstrap extension: {extension_dir}")
    elif args.install_byok_bootstrap_extension and args.settings_file:
        print(
            "Skipping BYOK bootstrap extension install because --settings-file was passed; "
            "pass --extensions-dir to install into an explicit test/profile extension directory."
        )

    if args.copy_api_key:
        api_key = api_token_path.read_text(encoding="utf-8").strip()
        if not api_key:
            raise RuntimeError(f"API key file is empty: {api_token_path}")
        clipboard_command = copy_to_clipboard(api_key)
        print(f"Copied API key to the clipboard with {clipboard_command}")
        print("Next step: reload VS Code so the AI Tunnel BYOK bootstrap extension can store the provider token automatically.")
    else:
        print("Next step: reload VS Code so the AI Tunnel BYOK bootstrap extension can store the provider token automatically.")

    if args.reset_byok_migration and not args.settings_file:
        # Skip when targeting a non-default settings path (tests, sandboxes).
        reset_byok_migration_for_channel(args.channel)
    elif args.reset_byok_migration and args.settings_file:
        print(
            "Skipping BYOK migration reset because --settings-file was passed; "
            "run scripts/reset-byok-migration.py manually if you also want to "
            "clear the migration flag in your real VS Code state DB."
        )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"bootstrap-vscode-user error: {exc}", file=sys.stderr)
        raise SystemExit(1)