"""Leave of Absence (LOA) subsystem.

/set_loa schedules LOA for a member for a date range.
LOA role is applied at LOA start, removed at LOA end, or cleared earlier when a
valid AAR is ingested.
"""

import os
import json
import asyncio
from datetime import datetime, timezone
import sys as _sys

import discord
from discord import app_commands
from discord.ext import tasks as _tasks

from .constants import LOA_ROLE_ID, DATA_DIR
from . import _bot_globals as _g

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b(name):
    m = _sys.modules.get("opscribe.bot") or _sys.modules.get("bot")
    return getattr(m, name) if (m is not None and hasattr(m, name)) else globals().get(name)


LOA_RECORDS_PATH = os.path.join(DATA_DIR, "loa_records.json")
_LOA_LOCK = asyncio.Lock()


def _load_loa() -> dict:
    try:
        if not os.path.exists(LOA_RECORDS_PATH):
            return {"records": {}}
        with open(LOA_RECORDS_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {"records": {}}
    except Exception:
        return {"records": {}}


def _save_loa(data: dict) -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LOA_RECORDS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _g.logger.error(f"[LOA] Failed to save loa_records.json: {e}")


def _get_active_loa(user_id: int) -> dict | None:
    """Return active LOA record for user_id if one exists and is current, else None."""
    data = _load_loa()
    rec = data["records"].get(str(user_id))
    if not rec:
        return None
    try:
        start = datetime.fromisoformat(rec["start"])
        end = datetime.fromisoformat(rec["end"])
        now = datetime.now(timezone.utc)
        if start <= now <= end:
            return rec
    except Exception:
        pass
    return None


async def _apply_loa_role(member: discord.Member) -> bool:
    """Ensure LOA role is present on member. Returns True if role was added."""
    try:
        loa_role = member.guild.get_role(LOA_ROLE_ID)
        if loa_role and loa_role not in member.roles:
            await member.add_roles(loa_role, reason="LOA window started")
            _g.logger.info(f"[LOA] Added LOA role to {member.display_name} ({member.id})")
            return True
    except Exception as e:
        _g.logger.warning(f"[LOA] Failed to add LOA role to {getattr(member, 'id', '?')}: {e}")
    return False


async def _remove_loa_role(member: discord.Member) -> bool:
    """Remove LOA role from member. Returns True if removed."""
    try:
        loa_role = member.guild.get_role(LOA_ROLE_ID)
        if loa_role and loa_role in member.roles:
            await member.remove_roles(loa_role, reason="LOA expired or AAR submitted")
            _g.logger.info(f"[LOA] Removed LOA role from {member.display_name} ({member.id})")
            return True
    except Exception as e:
        _g.logger.warning(f"[LOA] Failed to remove LOA role from {getattr(member, 'id', '?')}: {e}")
    return False


async def clear_loa_on_aar(user_id: int, guild: discord.Guild) -> None:
    """Called by aar_ops after a successful ingest. Clears LOA if active."""
    rec = _get_active_loa(user_id)
    if not rec:
        return
    member = guild.get_member(user_id)
    if member:
        await _remove_loa_role(member)
    # Remove from records regardless
    async with _LOA_LOCK:
        data = _load_loa()
        data["records"].pop(str(user_id), None)
        _save_loa(data)
    _g.logger.info(f"[LOA] LOA cleared for {user_id} due to AAR ingest")


# ---------------------------------------------------------------------------
# Background expiry task
# ---------------------------------------------------------------------------

@_tasks.loop(minutes=30)
async def _loa_expiry_loop():
    """Sync LOA role state: apply when started, remove when expired."""
    try:
        bot = getattr(_g, "bot", None) or _b("bot")
        if not bot:
            return
        guild_id = (_b("CONFIG") or {}).get("guild_id")
        guild = bot.get_guild(int(guild_id)) if guild_id else next(iter(bot.guilds), None)
        if not guild:
            return

        now = datetime.now(timezone.utc)
        async with _LOA_LOCK:
            data = _load_loa()
            expired = []
            started = []
            for uid_str, rec in data["records"].items():
                try:
                    start = datetime.fromisoformat(rec["start"])
                    end = datetime.fromisoformat(rec["end"])
                    if now > end:
                        expired.append(uid_str)
                    elif start <= now <= end:
                        started.append(uid_str)
                except Exception:
                    expired.append(uid_str)

            for uid_str in started:
                member = guild.get_member(int(uid_str))
                if member:
                    await _apply_loa_role(member)

            for uid_str in expired:
                member = guild.get_member(int(uid_str))
                if member:
                    await _remove_loa_role(member)
                data["records"].pop(uid_str, None)

            if expired:
                _save_loa(data)
                _g.logger.info(f"[LOA] Expired and cleared {len(expired)} LOA record(s)")
    except Exception as e:
        _g.logger.error(f"[LOA] Expiry loop error: {e}")


# ---------------------------------------------------------------------------
# Slash command
# ---------------------------------------------------------------------------

@app_commands.command(
    name="set_loa",
    description="[Watch Apothecary / Forgemaster] Set Leave of Absence for a member.",
)
@app_commands.describe(
    member="The member going on LOA",
    start_date="LOA start date (YYYY-MM-DD)",
    end_date="LOA end date (YYYY-MM-DD)",
)
async def set_loa(
    interaction: discord.Interaction,
    member: discord.Member,
    start_date: str,
    end_date: str,
):
    if not _b("check_command_permission")(interaction.user, "set_loa"):
        await interaction.response.send_message(
            "Only Watch Apothecary or Forgemaster may set Leave of Absence.", ephemeral=True
        )
        return

    # Parse dates
    try:
        start_dt = datetime.strptime(start_date.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_date.strip(), "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
    except ValueError:
        await interaction.response.send_message(
            "Invalid date format. Use `YYYY-MM-DD` (e.g. `2026-06-15`).", ephemeral=True
        )
        return

    if end_dt <= start_dt:
        await interaction.response.send_message("End date must be after start date.", ephemeral=True)
        return

    now = datetime.now(timezone.utc)

    # Resolve LOA role and assign immediately only when window is already active.
    loa_role = interaction.guild.get_role(LOA_ROLE_ID)
    if not loa_role:
        await interaction.response.send_message("LOA role not found in this server.", ephemeral=True)
        return

    role_applied_now = False
    if start_dt <= now <= end_dt:
        try:
            await member.add_roles(loa_role, reason=f"LOA set by {interaction.user.display_name}")
            role_applied_now = True
        except discord.Forbidden:
            await interaction.response.send_message("Missing permissions to assign the LOA role.", ephemeral=True)
            return

    # Store record
    async with _LOA_LOCK:
        data = _load_loa()
        data["records"][str(member.id)] = {
            "user_id": member.id,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "set_by": interaction.user.id,
        }
        _save_loa(data)

    days = (end_dt - start_dt).days + 1
    if role_applied_now:
        msg = (
            f"**{member.display_name}** is now on LOA from `{start_date}` to `{end_date}` ({days} day{'s' if days != 1 else ''}).\n"
            f"LOA role will be automatically removed at end of day on `{end_date}`, or earlier if an AAR is ingested."
        )
    else:
        msg = (
            f"Scheduled LOA for **{member.display_name}** from `{start_date}` to `{end_date}` ({days} day{'s' if days != 1 else ''}).\n"
            f"LOA role will be applied automatically on `{start_date}` and removed at end of day on `{end_date}`, or earlier if an AAR is ingested."
        )
    await interaction.response.send_message(msg, ephemeral=True)
    _g.logger.info(f"[LOA] {member.display_name} ({member.id}) set on LOA {start_date}→{end_date} by {interaction.user.id}")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def _register_commands(tree: app_commands.CommandTree) -> None:
    if tree.get_command("set_loa") is None:
        tree.add_command(set_loa)


__all__ = [
    "set_loa",
    "_register_commands",
    "_loa_expiry_loop",
    "clear_loa_on_aar",
    "_get_active_loa",
]
