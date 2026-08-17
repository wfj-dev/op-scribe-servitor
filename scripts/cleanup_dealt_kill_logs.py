#!/usr/bin/env python3
"""Delete finalized kill-log embed messages from the kill-log channel.

This script removes the original kill-log embed for entries that are already
dealt with. It does not delete the public denial notice that was posted after a
rejection; that message is intentionally preserved.

Usage:
  python3 scripts/cleanup_dealt_kill_logs.py --dry-run
  python3 scripts/cleanup_dealt_kill_logs.py --apply
  python3 scripts/cleanup_dealt_kill_logs.py --statuses verified rejected force_approved apo_revoked
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import discord


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "config.json"
TERMINUS_STATE_PATH = ROOT / "data" / "terminus_slayer.json"

FINAL_STATUSES = {"verified", "force_approved", "rejected", "apo_revoked"}


@dataclass
class CleanupTarget:
    kill_log_id: str
    status: str
    channel_id: int
    message_id: int


def _load_state() -> dict:
    with TERMINUS_STATE_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _collect_targets(state: dict, statuses: set[str]) -> list[CleanupTarget]:
    entries = state.get("entries") or {}
    targets: list[CleanupTarget] = []

    for kill_log_id, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "")
        if status not in statuses:
            continue
        message_id_raw = entry.get("embed_message_id")
        channel_id_raw = entry.get("channel_id")
        if not message_id_raw or not channel_id_raw:
            continue
        try:
            message_id = int(message_id_raw)
            channel_id = int(channel_id_raw)
        except Exception:
            continue
        targets.append(
            CleanupTarget(
                kill_log_id=kill_log_id,
                status=status,
                channel_id=channel_id,
                message_id=message_id,
            )
        )

    return targets


async def _run_cleanup(targets: list[CleanupTarget], guild_id: int | None, apply: bool) -> None:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN environment variable is required")

    intents = discord.Intents.default()
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

            deleted = 0
            missing = 0
            skipped = 0

            for target in targets:
                channel = guild.get_channel(target.channel_id)
                if channel is None:
                    print(f"[skip] channel not found for {target.kill_log_id} ({target.channel_id})")
                    skipped += 1
                    continue

                try:
                    message = await channel.fetch_message(target.message_id)
                except discord.NotFound:
                    print(f"[missing] {target.kill_log_id} message {target.message_id} already gone")
                    missing += 1
                    continue
                except Exception as exc:
                    print(f"[error] {target.kill_log_id} fetch failed: {exc}")
                    skipped += 1
                    continue

                if not apply:
                    print(
                        f"[dry-run] would delete {target.kill_log_id} "
                        f"(status={target.status}, message={target.message_id})"
                    )
                    continue

                try:
                    await message.delete()
                    print(f"[deleted] {target.kill_log_id} message {target.message_id}")
                    deleted += 1
                except Exception as exc:
                    print(f"[error] delete failed for {target.kill_log_id}: {exc}")
                    skipped += 1

            print(
                f"[summary] targets={len(targets)} deleted={deleted} "
                f"missing={missing} skipped={skipped}"
            )
        finally:
            if not client.is_closed():
                await client.close()

    async with client:
        await client.start(token)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete finalized kill-log embeds from the kill-log channel."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete messages. Without this flag the script only prints a dry run.",
    )
    parser.add_argument(
        "--statuses",
        nargs="+",
        default=sorted(FINAL_STATUSES),
        help="Final statuses to clean up.",
    )
    args = parser.parse_args()

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        config = json.load(f)

    guild_id_raw = config.get("guild_id")
    guild_id = int(guild_id_raw) if guild_id_raw else None

    state = _load_state()
    statuses = {str(status) for status in args.statuses}
    targets = _collect_targets(state, statuses)

    if not targets:
        print("No finalized kill-log embeds found for the selected statuses.")
        return

    print(f"Found {len(targets)} finalized kill-log embed(s) to evaluate.")
    asyncio.run(_run_cleanup(targets, guild_id, args.apply))


if __name__ == "__main__":
    main()