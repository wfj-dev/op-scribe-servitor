"""Reconcile recent challenge-role revokes from grace-period logs.

Default behavior is dry-run and writes a manifest without changing Discord roles.

Examples:
  python3 scripts/reconcile_recent_challenge_revokes.py
  python3 scripts/reconcile_recent_challenge_revokes.py --hours 12 --apply
  python3 scripts/reconcile_recent_challenge_revokes.py --roles "Crux Terminatus" "The Order Omega"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import discord


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "config.json"
LOG_DIR = ROOT / "logs"
DATA_DIR = ROOT / "data"

ROLE_NAME_TO_ID = {
    "Crux Terminatus": 1476288996756820109,
    "The Order Omega": 1502135764312526858,
    "Black Laurels": 1440108298115485716,
}

ROLE_NAME_TO_NOTIFIED_KEY = {
    "Crux Terminatus": "crux_terminatus",
    "The Order Omega": "order_omega",
}

LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) "
    r"\[INFO\] Grace period: revoked (?P<role>.+?) from (?P<uid>\d+) \((?P<name>.*)\)$"
)


@dataclass
class RevokeEvent:
    timestamp: datetime
    role_name: str
    user_id: int
    display_name: str
    source_file: str
    source_line: int


def _iter_log_files() -> list[Path]:
    paths = sorted(LOG_DIR.glob("op-scribe-servitor.log*"))
    return [p for p in paths if p.is_file()]


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S,%f")


def _scan_events(paths: Iterable[Path], role_filter: set[str]) -> list[RevokeEvent]:
    events: list[RevokeEvent] = []
    for path in paths:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f, start=1):
                m = LINE_RE.match(line.strip())
                if not m:
                    continue
                role_name = m.group("role")
                if role_name not in role_filter:
                    continue
                events.append(
                    RevokeEvent(
                        timestamp=_parse_ts(m.group("ts")),
                        role_name=role_name,
                        user_id=int(m.group("uid")),
                        display_name=m.group("name"),
                        source_file=path.name,
                        source_line=i,
                    )
                )
    return events


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
        return data if isinstance(data, dict) else {}


def _save_json(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _apply_notified_fix(user_ids_by_key: dict[str, set[int]]) -> None:
    cp_path = DATA_DIR / "challenge_progress.json"
    cp = _load_json(cp_path)
    changed = 0

    for key, user_ids in user_ids_by_key.items():
        for uid in user_ids:
            uid_str = str(uid)
            entry = cp.setdefault(uid_str, {})
            notified = entry.get("notified")
            if not isinstance(notified, list):
                notified = []
            if key not in notified:
                notified.append(key)
                entry["notified"] = notified
                cp[uid_str] = entry
                changed += 1

    if changed:
        _save_json(cp_path, cp)
    print(f"[data] challenge_progress: updated notified entries={changed}")


async def _apply_role_restore(events: list[RevokeEvent], guild_id: int | None) -> None:
    token = __import__("os").environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is required for --apply")

    # Restore latest revoke per user+role to avoid duplicate operations.
    latest: dict[tuple[int, str], RevokeEvent] = {}
    for ev in events:
        key = (ev.user_id, ev.role_name)
        prev = latest.get(key)
        if prev is None or ev.timestamp > prev.timestamp:
            latest[key] = ev

    intents = discord.Intents.default()
    intents.members = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        try:
            guild = None
            if guild_id is not None:
                guild = client.get_guild(guild_id)
                if guild is None:
                    try:
                        guild = await client.fetch_guild(guild_id)
                    except Exception:
                        guild = None
            if guild is None:
                guild = client.guilds[0] if client.guilds else None
            if guild is None:
                raise RuntimeError("Could not resolve guild")

            restored = 0
            already = 0
            missing_member = 0
            failed = 0
            user_ids_by_key: dict[str, set[int]] = {"crux_terminatus": set(), "order_omega": set()}

            for ev in sorted(latest.values(), key=lambda x: (x.role_name, x.user_id)):
                role_id = ROLE_NAME_TO_ID.get(ev.role_name)
                if role_id is None:
                    continue
                role = guild.get_role(role_id)
                if role is None:
                    print(f"[warn] role not found in guild: {ev.role_name} ({role_id})")
                    failed += 1
                    continue
                try:
                    member = await guild.fetch_member(ev.user_id)
                except discord.NotFound:
                    print(f"[skip] member not found: {ev.user_id} for {ev.role_name}")
                    missing_member += 1
                    continue
                except Exception as exc:
                    print(f"[err] fetch member failed {ev.user_id}: {exc}")
                    failed += 1
                    continue

                if role in member.roles:
                    already += 1
                else:
                    try:
                        await member.add_roles(role, reason="Recovery: reconcile recent grace-period revoke")
                        restored += 1
                        print(f"[ok] restored {ev.role_name} -> {ev.user_id} ({member.display_name})")
                    except Exception as exc:
                        print(f"[err] restore failed {ev.user_id} {ev.role_name}: {exc}")
                        failed += 1
                        continue

                key = ROLE_NAME_TO_NOTIFIED_KEY.get(ev.role_name)
                if key:
                    user_ids_by_key.setdefault(key, set()).add(ev.user_id)

            _apply_notified_fix(user_ids_by_key)
            print(
                "[summary] restored="
                f"{restored} already={already} missing_member={missing_member} failed={failed}"
            )
        finally:
            await client.close()

    await client.start(token)


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile recent challenge-role revokes from logs.")
    parser.add_argument("--hours", type=float, default=12.0, help="Window size in hours (default: 12)")
    parser.add_argument(
        "--roles",
        nargs="+",
        default=["Crux Terminatus", "The Order Omega"],
        help="Role names to reconcile",
    )
    parser.add_argument("--manifest", default=str(DATA_DIR / "recent_challenge_revokes_manifest.json"))
    parser.add_argument("--apply", action="store_true", help="Apply role restoration in Discord")
    args = parser.parse_args()

    role_filter = {r for r in args.roles if r in ROLE_NAME_TO_ID}
    if not role_filter:
        raise SystemExit("No valid role names provided.")

    log_files = _iter_log_files()
    if not log_files:
        raise SystemExit("No log files found under logs/.")

    all_events = _scan_events(log_files, role_filter)
    if not all_events:
        print("No matching grace-period revoke events found for requested roles.")
        return

    latest_ts = max(ev.timestamp for ev in all_events)
    window_start = latest_ts - timedelta(hours=args.hours)
    in_window = [ev for ev in all_events if window_start <= ev.timestamp <= latest_ts]

    in_window.sort(key=lambda x: x.timestamp)
    counts: dict[str, int] = {}
    for ev in in_window:
        counts[ev.role_name] = counts.get(ev.role_name, 0) + 1

    manifest = {
        "generated_at": datetime.utcnow().isoformat(),
        "latest_timestamp": latest_ts.isoformat(),
        "window_start": window_start.isoformat(),
        "window_hours": args.hours,
        "roles": sorted(role_filter),
        "counts": counts,
        "events": [
            {
                "timestamp": ev.timestamp.isoformat(),
                "role_name": ev.role_name,
                "role_id": ROLE_NAME_TO_ID.get(ev.role_name),
                "user_id": ev.user_id,
                "display_name": ev.display_name,
                "source": f"{ev.source_file}:{ev.source_line}",
            }
            for ev in in_window
        ],
    }

    manifest_path = Path(args.manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"Latest timestamp: {latest_ts}")
    print(f"Window: {window_start} -> {latest_ts} ({args.hours}h)")
    print(f"Roles: {', '.join(sorted(role_filter))}")
    print(f"Counts: {counts}")
    print(f"Manifest: {manifest_path}")

    if not args.apply:
        print("Dry-run only. Re-run with --apply to restore roles.")
        return

    cfg = _load_json(CONFIG_PATH)
    guild_id = cfg.get("guild_id")
    if isinstance(guild_id, str) and guild_id.isdigit():
        guild_id = int(guild_id)
    elif not isinstance(guild_id, int):
        guild_id = None

    asyncio.run(_apply_role_restore(in_window, guild_id))


if __name__ == "__main__":
    main()
