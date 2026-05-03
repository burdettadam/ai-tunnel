#!/usr/bin/env python3
"""Reset Copilot Chat's deprecated CustomOAI BYOK migration flag.

Background:
    GitHub Copilot Chat (Insiders 0.47.x) marks
    ``github.copilot.chat.customOAIModels`` as deprecated. On extension
    activation, a one-time migration copies entries from that setting into
    Copilot's internal BYOK provider list. The migration is gated by a key in
    the extension's global state. Due to a bug in the bundled extension, the
    cache key is built with the configuration *object* (rather than its id),
    producing the literal string ``copilot-byok-migration-CustomOAI-[object
    Object]``. Once that flag flips to ``true`` the migration never runs again,
    even if ``customOAIModels`` later changes. This means edits to the
    workspace or user ``settings.json`` (corrected URL, new model entries, new
    capabilities) are silently ignored after the first launch.

What this script does:
    Open VS Code's global state SQLite database (``state.vscdb``) and remove
    the migration flag(s) so the next Copilot Chat activation re-imports
    ``customOAIModels`` into the BYOK system. The actual model list and API
    keys are unaffected; only the migration gate is cleared. Always make a
    timestamped backup of the database next to itself before writing.

Notes:
    * VS Code must be closed (or at least the Copilot Chat extension host
      must be unloaded) when this runs. SQLite locking will surface as a clear
      error if the database is in use.
    * The API key for the CustomOAI provider lives in VS Code secret storage,
      not in the global state DB. This script does not touch it.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

COPILOT_CHAT_KEY = "GitHub.copilot-chat"
DEFAULT_FLAG_PREFIX = "copilot-byok-migration-CustomOAI"


def default_state_db_path(channel: str) -> Path:
    app_name = "Code - Insiders" if channel == "insiders" else "Code"
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", "").strip()
        if not appdata:
            raise RuntimeError("APPDATA is not set")
        return Path(appdata) / app_name / "User" / "globalStorage" / "state.vscdb"
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / app_name
            / "User"
            / "globalStorage"
            / "state.vscdb"
        )
    config_root = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(config_root) if config_root else Path.home() / ".config"
    return base / app_name / "User" / "globalStorage" / "state.vscdb"


def find_migration_flags(data: dict, flag_prefix: str) -> list[str]:
    if not isinstance(data, dict):
        return []
    return [key for key in data.keys() if isinstance(key, str) and key.startswith(flag_prefix)]


def reset_migration(db_path: Path, flag_prefix: str, dry_run: bool) -> int:
    if not db_path.exists():
        raise FileNotFoundError(f"VS Code state database not found: {db_path}")

    # Make a timestamped backup before we touch anything.
    backup_path = db_path.with_suffix(db_path.suffix + f".bak-{int(time.time())}")
    if not dry_run:
        shutil.copy2(db_path, backup_path)

    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        cur.execute("SELECT value FROM ItemTable WHERE key=?", (COPILOT_CHAT_KEY,))
        row = cur.fetchone()
        if row is None:
            print(f"No '{COPILOT_CHAT_KEY}' row in {db_path}; nothing to reset.")
            return 0

        try:
            payload = json.loads(row[0])
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Could not parse '{COPILOT_CHAT_KEY}' value as JSON: {exc}"
            )

        flags = find_migration_flags(payload, flag_prefix)
        if not flags:
            print(
                f"No keys starting with '{flag_prefix}' found in '{COPILOT_CHAT_KEY}'."
            )
            return 0

        for flag in flags:
            print(f"Removing migration flag: {flag}")
            if not dry_run:
                payload.pop(flag, None)

        if dry_run:
            print(f"Dry run: would have backed up to {backup_path}.")
            return len(flags)

        cur.execute(
            "UPDATE ItemTable SET value=? WHERE key=?",
            (json.dumps(payload, separators=(",", ":")), COPILOT_CHAT_KEY),
        )
        con.commit()
        print(f"Backup written: {backup_path}")
        print(
            f"Cleared {len(flags)} BYOK migration flag(s); reload VS Code to "
            "let Copilot Chat re-import customOAIModels."
        )
        return len(flags)
    finally:
        con.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--channel",
        choices=["insiders", "stable"],
        default="insiders",
        help="VS Code channel whose globalStorage to inspect (default: insiders).",
    )
    parser.add_argument(
        "--state-db",
        help="Override path to state.vscdb (default: derived from --channel).",
    )
    parser.add_argument(
        "--flag-prefix",
        default=DEFAULT_FLAG_PREFIX,
        help=(
            "Migration flag prefix to clear. The bundled extension stores it "
            f"as '{DEFAULT_FLAG_PREFIX}-[object Object]' due to a key-"
            "construction bug; the prefix match catches that and any future "
            "variants."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing to the database.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    db_path = Path(args.state_db).resolve() if args.state_db else default_state_db_path(args.channel)
    cleared = reset_migration(db_path, args.flag_prefix, args.dry_run)
    return 0 if cleared >= 0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"reset-byok-migration error: {exc}", file=sys.stderr)
        raise SystemExit(1)
