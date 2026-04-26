#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib import error, request


class ToolCallingProbeError(RuntimeError):
    pass


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


def build_probe_payload(model_id: str) -> dict[str, Any]:
    return {
        "model": model_id,
        "stream": False,
        "messages": [
            {
                "role": "system",
                "content": "You are running a tool-calling compatibility check. Use the provided tool exactly once.",
            },
            {
                "role": "user",
                "content": "Call the report_ready tool exactly once with status set to ok. Do not answer normally.",
            },
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "report_ready",
                    "description": "Report readiness for a tool-calling smoke test.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "status": {
                                "type": "string",
                                "description": "Use the literal string ok when the probe succeeds.",
                            }
                        },
                        "required": ["status"],
                        "additionalProperties": False,
                    },
                },
            }
        ],
        "tool_choice": {
            "type": "function",
            "function": {
                "name": "report_ready",
            },
        },
    }


def extract_tool_call(response_payload: dict[str, Any]) -> dict[str, Any]:
    choices = response_payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ToolCallingProbeError("Response did not contain any choices")

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise ToolCallingProbeError("Response choice was not a JSON object")

    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise ToolCallingProbeError("Response did not include a message object")

    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list) or not tool_calls:
        finish_reason = first_choice.get("finish_reason")
        raise ToolCallingProbeError(
            f"Response did not include tool_calls (finish_reason={finish_reason!r})"
        )

    first_tool_call = tool_calls[0]
    if not isinstance(first_tool_call, dict):
        raise ToolCallingProbeError("First tool call was not a JSON object")

    function_call = first_tool_call.get("function")
    if not isinstance(function_call, dict):
        raise ToolCallingProbeError("First tool call did not include a function object")

    if function_call.get("name") != "report_ready":
        raise ToolCallingProbeError(
            f"Model called {function_call.get('name')!r} instead of 'report_ready'"
        )

    arguments_text = function_call.get("arguments", "")
    try:
        arguments = json.loads(arguments_text) if arguments_text else {}
    except json.JSONDecodeError as exc:
        raise ToolCallingProbeError(f"Tool call arguments were not valid JSON: {exc}") from exc

    if arguments.get("status") != "ok":
        raise ToolCallingProbeError(
            f"Tool call arguments did not contain status='ok': {arguments!r}"
        )

    return first_tool_call


def probe_tool_calling(
    *,
    base_url: str,
    host_header: str,
    api_token: str,
    model_id: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    payload = build_probe_payload(model_id)
    body = json.dumps(payload).encode("utf-8")
    chat_completions_url = base_url.rstrip("/") + "/v1/chat/completions"
    req = request.Request(
        chat_completions_url,
        data=body,
        method="POST",
        headers={
            "Host": host_header,
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        },
    )

    try:
        with request.urlopen(req, timeout=timeout) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        raise ToolCallingProbeError(
            f"Tool-calling probe failed with HTTP {exc.code}: {response_body}"
        ) from exc
    except error.URLError as exc:
        raise ToolCallingProbeError(f"Tool-calling probe could not reach the model endpoint: {exc}") from exc

    tool_call = extract_tool_call(response_payload)
    return {
        "tool_call": tool_call,
        "response": response_payload,
    }


def pick_default_model_id(values: dict[str, str]) -> str:
    for key in ("OLLAMA_AGENT_MODEL_VSCODE_ID", "OLLAMA_AGENT_MODEL", "OLLAMA_MODEL_VSCODE_ID", "OLLAMA_MODEL"):
        value = values.get(key, "").strip()
        if value:
            return value
    raise KeyError("No model id was provided and neither OLLAMA_AGENT_MODEL nor OLLAMA_MODEL is set")


def probe_tool_calling_through_nginx(env_path: Path, model_id: str, timeout: float = 30.0) -> dict[str, Any]:
    values = read_env_file(env_path)
    token_path = resolve_env_path(env_path, require_env_key(values, "NGINX_API_TOKEN_FILE"))
    api_token = token_path.read_text(encoding="utf-8").strip()
    if not api_token:
        raise ToolCallingProbeError(f"API token file is empty: {token_path}")

    base_url = f"http://127.0.0.1:{require_env_key(values, 'NGINX_LISTEN_PORT')}"
    host_header = require_env_key(values, "OLLAMA_API_HOSTNAME")
    return probe_tool_calling(
        base_url=base_url,
        host_header=host_header,
        api_token=api_token,
        model_id=model_id,
        timeout=timeout,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe the local AI Tunnel endpoint for OpenAI-style tool calling support")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--model-id")
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.exists():
        raise FileNotFoundError(f"Missing env file: {env_path}")

    values = read_env_file(env_path)
    model_id = args.model_id or pick_default_model_id(values)
    result = probe_tool_calling_through_nginx(env_path, model_id, timeout=args.timeout)
    function_name = result["tool_call"]["function"]["name"]
    print(f"Tool calling smoke test passed for '{model_id}' via function '{function_name}'")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"check-tool-calling error: {exc}", file=sys.stderr)
        raise SystemExit(1)