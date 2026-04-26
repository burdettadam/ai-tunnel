#!/usr/bin/env python3

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path


DEFAULT_GIT_URL = "git+https://github.com/burdettadam/workspace-memory-bridge.git"


def module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def run(command: list[str], cwd: Path | None = None) -> None:
    subprocess.run(command, check=True, cwd=str(cwd) if cwd else None)


def install_bridge(args: argparse.Namespace) -> None:
    if args.install_source == "installed":
        if not module_available("workspace_memory_bridge"):
            raise RuntimeError("workspace_memory_bridge is not installed in the current interpreter")
        return

    if args.install_source in {"auto", "local"} and args.bridge_repo.exists():
        run([sys.executable, "-m", "pip", "install", "-e", str(args.bridge_repo.resolve())])
        return

    if args.install_source in {"auto", "git"}:
        run([sys.executable, "-m", "pip", "install", args.git_url])
        return

    raise RuntimeError(
        "Unable to install workspace-memory-bridge. Pass --install-source git, install it manually, or provide a valid --bridge-repo path."
    )


def scaffold_bridge(args: argparse.Namespace) -> None:
    command = [
        sys.executable,
        "-m",
        "workspace_memory_bridge.scaffold",
        "--workspace-root",
        str(args.workspace_root.resolve()),
        "--wing",
        args.wing,
        "--room-prefix",
        args.room_prefix,
    ]
    if args.bootstrap_palace:
        command.append("--bootstrap-palace")
    if args.mine_limit > 0:
        command.extend(["--mine-limit", str(args.mine_limit)])
    if args.smoke_http:
        command.append("--smoke-http")

    run(command, cwd=args.workspace_root.resolve())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install and scaffold workspace-memory-bridge for this repo."
    )
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--bridge-repo",
        type=Path,
        default=Path("..").joinpath("workspace-memory-bridge"),
        help="Local workspace-memory-bridge checkout used for editable install when available.",
    )
    parser.add_argument(
        "--install-source",
        choices=["auto", "local", "git", "installed"],
        default="auto",
        help="Where to source workspace-memory-bridge from before scaffolding.",
    )
    parser.add_argument("--git-url", default=DEFAULT_GIT_URL)
    parser.add_argument("--wing", default="ai-tunnel")
    parser.add_argument("--room-prefix", default="ai-tunnel")
    parser.add_argument("--bootstrap-palace", action="store_true")
    parser.add_argument("--mine-limit", type=int, default=0)
    parser.add_argument("--smoke-http", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    install_bridge(args)
    scaffold_bridge(args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"bootstrap-workspace-memory error: {exc}", file=sys.stderr)
        raise SystemExit(1)