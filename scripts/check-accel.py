#!/usr/bin/env python3

import argparse
import platform
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


def append_env_arg(command: list[str], key: str, value: str | None) -> None:
    if value:
        command.extend(["-e", f"{key}={value}"])


def require_linux_host(provider: str) -> None:
    host_os = platform.system()
    if host_os != "Linux":
        raise RuntimeError(
            f"{provider} container checks in this repo assume a Linux host that can pass through GPU devices like /dev/dri or /dev/kfd. Current host: {host_os}"
        )


def build_command(provider: str, env_values: dict[str, str]) -> list[str]:
    if provider == "nvidia":
        image = env_values.get("OLLAMA_IMAGE", "ollama/ollama:latest")
        return [
            "docker",
            "run",
            "--rm",
            "--gpus",
            "all",
            "--entrypoint",
            "/bin/sh",
            image,
            "-lc",
            "test -c /dev/nvidiactl || test -c /dev/nvidia0",
        ]

    if provider == "amd":
        require_linux_host("AMD")
        image = env_values.get("OLLAMA_IMAGE_AMD", "ollama/ollama:rocm")
        command = [
            "docker",
            "run",
            "--rm",
            "--device",
            "/dev/kfd",
            "--device",
            "/dev/dri",
            "--entrypoint",
            "/bin/sh",
            image,
            "-lc",
            "test -e /dev/kfd && test -d /dev/dri",
        ]
        append_env_arg(command, "ROCR_VISIBLE_DEVICES", env_values.get("ROCR_VISIBLE_DEVICES"))
        append_env_arg(command, "HSA_OVERRIDE_GFX_VERSION", env_values.get("HSA_OVERRIDE_GFX_VERSION"))
        return command

    require_linux_host("Vulkan")
    image = env_values.get("OLLAMA_IMAGE", "ollama/ollama:latest")
    command = [
        "docker",
        "run",
        "--rm",
        "--device",
        "/dev/dri",
        "-e",
        "OLLAMA_VULKAN=1",
        "--entrypoint",
        "/bin/sh",
        image,
        "-lc",
        "test -d /dev/dri",
    ]
    append_env_arg(command, "GGML_VK_VISIBLE_DEVICES", env_values.get("GGML_VK_VISIBLE_DEVICES"))
    return command


def run_command(command: list[str]) -> int:
    try:
        result = subprocess.run(command, check=False)
    except FileNotFoundError as exc:
        print(f"check-accel error: {exc}", file=sys.stderr)
        return 1
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Docker GPU passthrough for the Ollama stack")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--provider", choices=["nvidia", "amd", "vulkan"], required=True)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    env_path = Path(args.env_file)
    if not env_path.exists():
        raise FileNotFoundError(f"Missing env file: {env_path}")

    env_values = read_env_file(env_path)
    command = build_command(args.provider, env_values)
    print(f"Running {args.provider} acceleration check")
    print(" ".join(command))
    exit_code = run_command(command)

    if exit_code == 0:
        print(f"{args.provider} acceleration check passed")
        return 0

    print(
        f"{args.provider} acceleration check failed. Confirm Docker GPU passthrough is configured for this provider before starting the stack.",
        file=sys.stderr,
    )
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"check-accel error: {exc}", file=sys.stderr)
        raise SystemExit(1)