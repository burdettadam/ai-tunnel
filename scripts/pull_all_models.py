#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from modelctl import NON_OLLAMA_LOCAL_MODEL_IDS, read_env_file, validate_ollama_model_id


def docker_command() -> str:
    return os.environ.get("MODEL_PULL_DOCKER_COMMAND") or os.environ.get("MODELCTL_DOCKER_COMMAND") or "docker"


def build_compose_prefix(env_file: Path, compose_files: list[str]) -> list[str]:
    command = [docker_command(), "compose"]
    for compose_file in compose_files:
        command.extend(["-f", compose_file])
    command.extend(["--env-file", str(env_file)])
    return command


def load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def collect_model_ids(env_values: dict[str, str], settings: dict) -> list[str]:
    model_ids: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        normalized_candidate = candidate.strip()
        if normalized_candidate.lower() in NON_OLLAMA_LOCAL_MODEL_IDS:
            print(f"Skipping non-Ollama local model '{normalized_candidate}'")
            return
        normalized = validate_ollama_model_id(normalized_candidate)
        if normalized in seen:
            return
        seen.add(normalized)
        model_ids.append(normalized)

    for key in ("OLLAMA_MODEL", "OLLAMA_AGENT_MODEL", "OLLAMA_LOCAL_SMOKE_MODEL"):
        value = env_values.get(key, "").strip()
        if value:
            add(value)

    model_entries = settings.get("github.copilot.chat.customOAIModels", {})
    if isinstance(model_entries, dict):
        for model_id in model_entries:
            add(str(model_id))

    return model_ids


def ensure_ollama_running(compose_prefix: list[str]) -> None:
    ps_result = subprocess.run(
        [*compose_prefix, "ps", "--services", "--status", "running"],
        check=True,
        capture_output=True,
        text=True,
    )
    running_services = {line.strip() for line in ps_result.stdout.splitlines() if line.strip()}
    if "ollama" in running_services:
        return
    print("Starting ollama service before pull-all")
    subprocess.run([*compose_prefix, "up", "-d", "ollama"], check=True)


def pull_models(compose_prefix: list[str], model_ids: list[str]) -> None:
    for model_id in model_ids:
        print(f"Pulling model '{model_id}'")
        subprocess.run([*compose_prefix, "exec", "ollama", "ollama", "pull", model_id], check=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pull every locally supported model referenced by the repo env file and shared catalog")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--settings-file", default=".vscode/settings.json")
    parser.add_argument("--compose-file", action="append", default=["compose.yaml"])
    return parser


def main() -> int:
    args = build_parser().parse_args()
    env_path = Path(args.env_file)
    if not env_path.exists():
        raise FileNotFoundError(f"Missing env file: {env_path}")

    _, env_values = read_env_file(env_path)
    settings = load_settings(Path(args.settings_file))
    model_ids = collect_model_ids(env_values, settings)
    if not model_ids:
        raise ValueError("No local model ids were found in the env file or shared catalog")

    compose_prefix = build_compose_prefix(env_path, args.compose_file)
    ensure_ollama_running(compose_prefix)
    print(f"Pulling {len(model_ids)} local models")
    pull_models(compose_prefix, model_ids)
    print("Finished pulling all local models")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"pull-all-models error: {exc}", file=sys.stderr)
        raise SystemExit(1)
