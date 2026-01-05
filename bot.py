#!/usr/bin/env python3

import os
import asyncio
import json
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta, timezone
import difflib
import re
import itertools
from typing import Dict, List, Tuple, Optional
import hashlib
from collections import Counter
import logging
import signal
import argparse
import statistics

# Data file locations
DATA_DIR = "data"
AAR_RECORDS_PATH = os.path.join(DATA_DIR, "aar_records.json")
AAR_ERRORS_PATH = os.path.join(DATA_DIR, "aar_errors.json")
PROCESSED_IDS_PATH = os.path.join(DATA_DIR, "processed_ids.json")
TROPHY_HALL_INDEX_PATH = os.path.join(DATA_DIR, "trophy_hall_index.json")
OATHS_INDEX_PATH = os.path.join(DATA_DIR, "oaths_index.json")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Global lock to serialize reconciliation runs
RECONCILE_LOCK = asyncio.Lock()
CHAPLAIN_INGEST_LOCK = asyncio.Lock()

# Guard to avoid double shutdown handling
SHUTDOWN_INITIATED = False

# Control whether startup/shutdown status broadcasts are sent.
BROADCAST_STATUS = True


def _is_truthy(val) -> bool:
    try:
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return val != 0
        if isinstance(val, str):
            return val.strip().lower() in {"1", "true", "yes", "on"}
    except Exception:
        pass
    return False


def _resolve_notification_guild() -> Optional[discord.Guild]:
    """Resolve the notification guild by name first, then ID, then fallback.
    Priority:
      1) CONFIG.guild_name (default "Watch Fortress Jericho")
      2) CONFIG.guild_id
      3) First connected guild
    """
    # 1) Try by configured name (or the known fortress name)
    try:
        target_name = CONFIG.get("guild_name") or "Watch Fortress Jericho"
    except Exception:
        target_name = "Watch Fortress Jericho"
    try:
        for g in bot.guilds:
            if getattr(g, "name", None) == target_name:
                return g
    except Exception:
        pass
    # 2) Try by configured ID
    try:
        gid = CONFIG.get("guild_id")
    except Exception:
        gid = None
    if gid:
        try:
            g = bot.get_guild(int(gid))
            if g:
                return g
        except Exception:
            pass
    # 3) Fallback to the first guild
    try:
        return bot.guilds[0] if bot.guilds else None
    except Exception:
        return None


async def _send_watch_command_notice(kind: str):
    """Send a status notice to ❖⋅data-vault⋅❖ mentioning @Watch Command.
    kind: 'ONLINE' or 'OFFLINE' (case-insensitive).
    Also deletes the most recent prior status bulletin of the opposite kind
    (e.g., when turning ONLINE, deletes the last OFFLINE bulletin)."""
    # Respect broadcast toggle (e.g., when debug mode disables broadcasts)
    try:
        if not BROADCAST_STATUS:
            return
    except Exception:
        # If BROADCAST_STATUS is undefined for any reason, continue safely
        pass
    guild = _resolve_notification_guild()
    if not guild:
        logger.debug("No guild available for notification.")
        return
    try:
        channel = discord.utils.get(guild.channels, name="❖⋅data-vault⋅❖")
    except Exception:
        channel = None
    if not channel:
        logger.debug("Notification channel '❖⋅data-vault⋅❖' not found.")
        return
    try:
        role = discord.utils.get(guild.roles, name="Watch Command")
    except Exception:
        role = None
    mention = f"<@&{role.id}>" if role else "@Watch Command"
    status = "ONLINE" if (kind or "").upper().startswith("ON") else "OFFLINE"
    # Delete the most recent opposite-status bulletin, if present
    try:
        target_delete = "OFFLINE" if status == "ONLINE" else "ONLINE"
        # Limit scan to a reasonable number to avoid rate limits
        async for msg in channel.history(limit=100):
            try:
                if getattr(msg.author, "id", None) != getattr(bot.user, "id", None):
                    continue
                content = msg.content or ""
                # Identify our bulletin by the header and the status line
                if (
                    "OPERATION-SCRIBE SERVITOR — STATUS BULLETIN" in content
                    and f"Status: {target_delete}" in content
                ):
                    await msg.delete()
                    break
            except Exception:
                # Continue scanning on per-message errors
                continue
    except Exception as e:
        logger.debug(f"Failed to delete previous status bulletin: {e}")

    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    # Compose styled ANSI block similar to other outputs; keep the actual mention outside the block to ping.
    block = (
        "```ansi\n"
        "\u001b[32m==============================================================================\n"
        "  WATCH FORTRESS JERICHO // ARCHIVE-COGITATOR\n"
        "  OPERATION-SCRIBE SERVITOR — STATUS BULLETIN\n"
        "==============================================================================\n"
        f"  Servitor Unit: Jericho Logi-Scribe V-1\n"
        f"  Status: {status}\n"
        f"  Timestamp: {ts}\n"
        "==============================================================================\n"
        "  Machine-Spirit Addendum:\n"
        "  Status broadcasts are preserved within the data-vault and\n"
        "  may be invoked by decree of the Forgemaster and Watch Techmarines alone.\n"
        "==============================================================================\n"
        "\u001b[0m```"
    )
    content = f"{mention}\n{block}"
    try:
        await channel.send(
            content, allowed_mentions=discord.AllowedMentions(roles=True)
        )
    except Exception as e:
        logger.debug(f"Failed to send notification: {e}")


async def _announce_shutdown_and_close():
    global SHUTDOWN_INITIATED
    if SHUTDOWN_INITIATED:
        return
    SHUTDOWN_INITIATED = True
    try:
        if BROADCAST_STATUS:
            await _send_watch_command_notice("OFFLINE")
    except Exception as e:
        logger.debug(f"Shutdown announce failed: {e}")
    try:
        await bot.close()
    except Exception:
        pass


# Config load
CONFIG_PATH = os.path.join("config", "config.json")
CONFIG: dict = {}
if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, "r") as f:
            CONFIG = json.load(f) or {}
    except Exception:
        CONFIG = {}

# Logging setup
log_level_str = ((CONFIG.get("logging") or {}).get("level") or "INFO").upper()
log_level = getattr(logging, log_level_str, logging.INFO)
logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("op-scribe-servitor")

# Apply initial debug setting from config (CLI may override later in _main)
try:
    BROADCAST_STATUS = not _is_truthy((CONFIG or {}).get("debug"))
except Exception:
    BROADCAST_STATUS = True

# Global rank priority list (highest -> lowest)
RANK_ROLES_PRIORITY = [
    "Watch Master",
    "Lord Executioner",
    "Chief Apothecary",
    "High Chaplain",
    "Forgemaster",
    "Void Warden",
    "Venerable",
    "Watch Captain",
    "Watch Lieutenant",
    "Company Champion",
    "Watch Apothecary",
    "Watch Chaplain",
    "Watch Librarian",
    "Watch Techmarine",
    "Watch Sergeant",
    "Kill Team Champion",
    "Watch Veteran",
    "Watch Brother",
]

# Restrict commands to a specific channel (demo/training)
ALLOWED_COMMAND_CHANNELS = {
    # Update to your desired demo channel name
    "❖⋅data-vault⋅❖",
    "demo",
}


def is_allowed_channel(interaction: discord.Interaction):
    try:
        ch = interaction.channel
        # Prefer ID-based gating from config; fall back to names
        allowed_ids = set((CONFIG.get("allowed_command_channel_ids") or []))
        if allowed_ids and hasattr(ch, "id"):
            return str(ch.id) in {str(x) for x in allowed_ids}
        name = getattr(ch, "name", None)
        return bool(name) and name in ALLOWED_COMMAND_CHANNELS
    except Exception:
        return False


def _print_progress(prefix: str, current: int, total: int, width: int = 40):
    try:
        if total <= 0:
            total = 1
        ratio = max(0.0, min(1.0, current / total))
        filled = int(ratio * width)
        bar = "#" * filled + "-" * (width - filled)
        print(
            f"\r{prefix} [{bar}] {current}/{total} ({ratio * 100:.1f}%)",
            end="",
            flush=True,
        )
        if current >= total:
            print("", flush=True)
    except Exception:
        # Avoid crashing on printing failures
        pass


def _role_index(role_name: str):
    try:
        return RANK_ROLES_PRIORITY.index(role_name)
    except ValueError:
        return None


def get_highest_rank_index(user: discord.User | discord.Member):
    """Return the highest (best) rank index found in user's roles.
    Duck-typed: any object with a 'roles' iterable of role-like objects is accepted.
    """
    roles = getattr(user, "roles", None)
    if roles is None:
        return None
    highest: Optional[int] = None
    for role in roles:
        name = getattr(role, "name", None)
        if not name:
            continue
        idx = _role_index(name)
        if idx is not None:
            highest = idx if highest is None else min(highest, idx)
    return highest


def _canonical_role_names(user: discord.User | discord.Member) -> set[str]:
    """Return a set of canonical role names including aliases from config."""
    names = set()
    aliases: Dict[str, List[str]] = CONFIG.get("role_aliases") or {}
    roles = getattr(user, "roles", [])
    for role in roles:
        rn = getattr(role, "name", None)
        if not rn:
            continue
        names.add(rn)
        # Map alias to canonical if configured
        for canon, alias_list in aliases.items():
            if rn in (alias_list or []):
                names.add(canon)
    return names


def _extract_killteam_name(name: str) -> str:
    """Return a display-friendly Kill Team name by stripping the 'Kill Team' prefix.
    Handles optional separators like ':', '-', and varying whitespace/case.
    If no match, returns the original name (or 'Unknown' if empty).
    """
    try:
        m = re.match(r"(?i)\s*kill\s*team\s*[:\-]?\s*(.+)", (name or ""))
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return name or "Unknown"


def is_sergeant_or_higher(user: discord.User | discord.Member):
    # Allow nickname override for owner/operator
    admin_ids = set(str(x) for x in (CONFIG.get("admin_user_ids") or []))
    if (
        str(getattr(user, "id", None)) in admin_ids
        or str(getattr(user, "nick", None)) == "Watch Veteran Jules"
    ):
        return True
    idx_sergeant = _role_index("Watch Sergeant")
    if idx_sergeant is None:
        return False
    # Consider aliases when computing highest rank
    role_names = _canonical_role_names(user)
    # Map to indices and pick the minimum
    indices = [i for name in role_names if (i := _role_index(name)) is not None]
    idx = min(indices) if indices else None
    return idx is not None and idx <= idx_sergeant


def is_watch_command(user: discord.User | discord.Member):
    # Define "Watch Command" as Sergeant and higher (including staff roles above it)
    return is_sergeant_or_higher(user)


def can_reconcile_records(user: discord.User | discord.Member):
    # Only Watch Master, Techmarines, and Forgemaster (config-driven)
    admin_ids = set(str(x) for x in (CONFIG.get("admin_user_ids") or []))
    if (
        str(getattr(user, "id", None)) in admin_ids
        or str(getattr(user, "nick", None)) == "Watch Veteran Jules"
    ):
        return True
    allowed_config = set(
        (CONFIG.get("permissions", {}).get("reconcile_records", {}).get("roles") or [])
    )
    allowed_default = {"Watch Master", "Forgemaster", "Watch Techmarine"}
    allowed = allowed_config or allowed_default
    names = _canonical_role_names(user)
    return any(r in names for r in allowed)


@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user}")
    # sync app_commands (slash commands)
    try:
        guild_id = CONFIG.get("guild_id")
        if guild_id:
            # During development, sync to a single guild for faster propagation
            synced = await bot.tree.sync(guild=discord.Object(id=int(guild_id)))
        else:
            synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} slash command(s).")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")

    # Announce startup to Watch Command in data-vault
    if BROADCAST_STATUS:
        try:
            await _send_watch_command_notice("ONLINE")
        except Exception as e:
            logger.debug(f"Startup announce failed: {e}")

    # Register graceful shutdown signal handlers
    try:
        loop = asyncio.get_running_loop()

        def _sig_handler():
            try:
                loop.create_task(_announce_shutdown_and_close())
            except Exception:
                pass

        try:
            loop.add_signal_handler(signal.SIGTERM, _sig_handler)
        except Exception:
            pass
        try:
            loop.add_signal_handler(signal.SIGINT, _sig_handler)
        except Exception:
            pass
    except Exception as e:
        logger.debug(f"Failed to register signal handlers: {e}")


@bot.tree.command(
    name="litany_of_function",
    description="Describe the duties of Jericho Logi-Scribe Servitor V-1.",
)
async def litany_of_function(interaction: discord.Interaction):
    if not (is_watch_command(interaction.user) and is_allowed_channel(interaction)):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    litany_text = """```ansi
\u001b[32m===============================================================
 WATCH FORTRESS JERICHO // ARCHIVE-COGITATOR
 LOGI-SCRIBE SERVITOR V-1 — FUNCTION LITANY
===============================================================
        ++ SECURE VOX-CHANNEL ACTIVE ++

Designation: Watch-Scribe Logi-Servitor V-1
Status: Active. Machine-spirit nominal.
Function: Record, audit, and sanctify deeds of the Long Watch.

Bound by the Edict of Record-Keeping.
Unauthorized personnel will be logged and ignored.

# High-Authority Commands:

• /tally_deeds @Brother
Returns AAR Points, Gene-Seed Credit,
Armory Tally, and Service Rank.
Permission: Sergeant+

• /combat_bonds [@Brother] [window:N]
Analyzes recent missions (default 100).
No target: top 3 fortress triads.
With target: strongest bonds.
Permission: Sergeant+

• /killteam_brief [company:@Role]
Brief Watch Company kill teams (last 100 AARs).
Permission: Above Sergeant

• /sanctify_battle_records [span_days:N]
Ingests sanctioned AARs using last cursor.
Permission: Watch Master, Forgemaster, Techmarine

• /audit_archive_discrepancies
Rechecks rejected AARs for resolved errors.
Permission: Watch Master, Forgemaster, Techmarine

• /reconcile_records [span_days:N]
Full rite: audit errors, then ingest new AARs.
Permission: Watch Master, Forgemaster, Techmarine

All commands restricted to sanctified channels.
This unit exists to preserve honor and memory.

# ++ END OF TRANSMISSION ++
===============================================================
\u001b[0m```"""
    await interaction.response.send_message(litany_text, ephemeral=True)


@bot.tree.command(
    name="reconcile_records", description="Reprocess AARs and update the archive."
)
@app_commands.describe(span_days="Optional: only scan messages from the last N days.")
async def reconcile_records(
    interaction: discord.Interaction, span_days: int | None = None
):
    if not (
        can_reconcile_records(interaction.user) and is_allowed_channel(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    # Serialize concurrent invocations to avoid file races
    if RECONCILE_LOCK.locked():
        await interaction.response.send_message(
            "Another reconciliation is in progress. Please try again shortly.",
            ephemeral=True,
        )
        return
    await interaction.response.defer(thinking=True, ephemeral=True)

    await RECONCILE_LOCK.acquire()
    try:
        await _reconciliation_core(interaction, span_days)
    finally:
        RECONCILE_LOCK.release()


@bot.tree.command(
    name="audit_archive_discrepancies",
    description="Recheck previously rejected AARs and restore any fixed entries.",
)
@app_commands.describe(span_days="Optional: only recheck errors from the last N days.")
async def audit_archive_discrepancies(
    interaction: discord.Interaction, span_days: int | None = None
):
    if not (
        can_reconcile_records(interaction.user) and is_allowed_channel(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    if RECONCILE_LOCK.locked():
        await interaction.response.send_message(
            "Another reconciliation is in progress. Please try again shortly.",
            ephemeral=True,
        )
        return
    await interaction.response.defer(thinking=True, ephemeral=True)

    await RECONCILE_LOCK.acquire()
    try:
        guild = interaction.guild
        aar_channel = discord.utils.get(
            guild.channels, name="᛭⋅⋅after-action-reports⋅⋅᛭"
        )
        if not aar_channel:
            await interaction.followup.send(
                "++ ERROR: '᛭⋅⋅after-action-reports⋅⋅᛭' CHANNEL NOT FOUND. ++",
                ephemeral=True,
            )
            return
        fixed, still_broken = await _run_recheck_errors(aar_channel, span_days)

        author_summaries = summarize_error_authors()
        author_lines = []
        for a in author_summaries:
            label = a.get("nickname") or a.get("username") or a.get("id") or "Unknown"
            author_lines.append(f"- {label}: {a['count']}")

        report = (
            "```ansi\n"
            "\u001b[32m==============================================================================\n"
            "  WATCH FORTRESS JERICHO // ARCHIVE-COGITATOR\n"
            "  OPERATION-SCRIBE SERVITOR — ERROR RECHECK RITE\n"
            "==============================================================================\n"
            f"  Restored Entries Returned to the Annals: {fixed}\n"
            f"  Faulted Reports Under Quarantine: {still_broken}\n"
        )
        if author_lines:
            report += "-----------------------------------------------\n"
            report += "Entries Rejected Due to Authorial Deviation:\n"
            for line in author_lines:
                report += f"  {line}\n"
        report += (
            "==============================================================================\n"
            "  Machine-Spirit Addendum:\n"
            "  These Records are logged for future deployment rites\n"
            "  and may be invoked by decree of Watch Command alone.\n"
            "==============================================================================\n"
            "\u001b[0m```"
        )
        await interaction.followup.send(report, ephemeral=True)
    finally:
        RECONCILE_LOCK.release()


@bot.tree.command(
    name="sanctify_battle_records",
    description="Ingest new sanctioned AARs (optionally scoped by span of days).",
)
@app_commands.describe(span_days="Optional: only scan messages from the last N days.")
async def sanctify_battle_records(
    interaction: discord.Interaction, span_days: int | None = None
):
    if not (
        can_reconcile_records(interaction.user) and is_allowed_channel(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    if RECONCILE_LOCK.locked():
        await interaction.response.send_message(
            "Another reconciliation is in progress. Please try again shortly.",
            ephemeral=True,
        )
        return
    await interaction.response.defer(thinking=True, ephemeral=True)

    await RECONCILE_LOCK.acquire()
    try:
        guild = interaction.guild
        aar_channel = discord.utils.get(
            guild.channels, name="᛭⋅⋅after-action-reports⋅⋅᛭"
        )
        if not aar_channel:
            await interaction.followup.send(
                "++ ERROR: '᛭⋅⋅after-action-reports⋅⋅᛭' CHANNEL NOT FOUND. ++",
                ephemeral=True,
            )
            return
        ingested, rejected = await _run_ingest_new(aar_channel, span_days)

        report = (
            "```ansi\n"
            "\u001b[32m==============================================================================\n"
            "  WATCH FORTRESS JERICHO // ARCHIVE-COGITATOR\n"
            "  OPERATION-SCRIBE SERVITOR — INGESTION RITE\n"
            "==============================================================================\n"
            + (
                f"  Scan Window: Last {span_days} day(s)\n"
                if span_days
                else "  Scan Window: Full history\n"
            )
            + f"  Sanctioned Operational Records: {ingested}\n"
            + f"  Logs Judged Corrupted or Unworthy: {rejected}\n"
            + "==============================================================================\n"
            + "  Machine-Spirit Addendum:\n"
            + "  These Records are logged for future deployment rites\n"
            + "  and may be invoked by decree of Watch Command alone.\n"
            + "==============================================================================\n"
            + "\u001b[0m```"
        )
        await interaction.followup.send(report, ephemeral=True)
    finally:
        RECONCILE_LOCK.release()


async def _reconciliation_core(interaction: discord.Interaction, span_days: int | None):
    guild = interaction.guild
    aar_channel = discord.utils.get(guild.channels, name="᛭⋅⋅after-action-reports⋅⋅᛭")
    if not aar_channel:
        await interaction.followup.send(
            "++ ERROR: '᛭⋅⋅after-action-reports⋅⋅᛭' CHANNEL NOT FOUND. ++"
        )
        return
    # First recheck errors, then ingest new
    fixed, still_broken = await _run_recheck_errors(aar_channel, span_days)
    ingested, rejected = await _run_ingest_new(aar_channel, span_days)

    # Compose combined report
    author_summaries = summarize_error_authors()
    author_lines = []
    for a in author_summaries:
        label = a.get("nickname") or a.get("username") or a.get("id") or "Unknown"
        author_lines.append(f"- {label}: {a['count']}")

    report_header = (
        "```ansi\n"
        "\u001b[32m==============================================================================\n"
        "  WATCH FORTRESS JERICHO // ARCHIVE-COGITATOR\n"
        "  OPERATION-SCRIBE SERVITOR — RECONCILIATION RITE\n"
        "==============================================================================\n"
        "  ++ LITANY OF RECONCILIATION COMPLETE ++\n"
        + (
            f"  Scan Window: Last {span_days} day(s)\n"
            if span_days
            else "  Scan Window: Full history\n"
        )
    )

    report = (
        report_header
        + f"  Sanctioned Operational Records: {ingested}\n"
        + f"  Logs Judged Corrupted or Unworthy: {rejected}\n"
        + f"  Restored Entries Returned to the Annals: {fixed}\n"
        + f"  Faulted Reports Under Quarantine: {still_broken}\n"
    )

    if author_lines:
        report += "-----------------------------------------------\n"
        report += "Entries Rejected Due to Authorial Deviation:\n"
        for line in author_lines:
            report += f"  {line}\n"

    report += "==============================================================================\n"
    report += "  Machine-Spirit Addendum:\n"
    report += "  These Records are logged for future deployment rites\n"
    report += "  and may be invoked by decree of Watch Command alone.\n"
    report += "==============================================================================\n"
    report += "\u001b[0m```"

    await interaction.followup.send(report, ephemeral=True)


async def _run_recheck_errors(
    aar_channel: discord.TextChannel, span_days: Optional[int] = None
):
    fixed = 0
    still_broken = 0
    cutoff_dt = None
    if span_days and span_days > 0:
        cutoff_dt = datetime.utcnow() - timedelta(days=span_days)
    error_entries = _load_json_dict(AAR_ERRORS_PATH)
    if len(error_entries) > 0:
        # If windowed, compute total within window for progress counters
        total_errs = 0
        done_errs = 0
        window_ids = list(error_entries.keys())
        if cutoff_dt is not None:
            # We will determine window membership lazily when fetching messages
            pass
        else:
            total_errs = len(error_entries)
        for aar_id_str in list(error_entries.keys()):
            try:
                aar_id = int(aar_id_str)
            except ValueError:
                del error_entries[aar_id_str]
                continue
            if has_been_processed(aar_id):
                data = _load_json_dict(AAR_RECORDS_PATH)
                sid = str(aar_id)
                if sid in data:
                    del data[sid]
                    _save_json_dict(AAR_RECORDS_PATH, data)
                fixed += 1
                done_errs += 1
                if cutoff_dt is None:
                    if (done_errs % 5 == 0) or (done_errs == total_errs):
                        _print_progress("Recheck Errors", done_errs, total_errs)
                continue
            try:
                msg = await aar_channel.fetch_message(aar_id)
            except Exception:
                msg = None
            if not msg:
                log_aar_errors(
                    aar_id, ["Original message not found; cannot reprocess."]
                )
                # Count as broken only for full scans (no reliable timestamp)
                if cutoff_dt is None:
                    still_broken += 1
                done_errs += 1
                if cutoff_dt is None:
                    if (done_errs % 5 == 0) or (done_errs == total_errs):
                        _print_progress("Recheck Errors", done_errs, total_errs)
                continue
            # Window filter: skip messages older than cutoff
            if cutoff_dt is not None:
                try:
                    msg_dt = msg.created_at
                    if msg_dt.tzinfo is not None:
                        msg_dt = msg_dt.astimezone(tz=None).replace(tzinfo=None)
                    if total_errs == 0:
                        # First time we see a windowed message, estimate total as count of window hits
                        pass
                    if msg_dt < cutoff_dt:
                        continue
                    total_errs += 1
                except Exception:
                    # If timestamp parse fails, conservatively skip from windowed run
                    continue
            record = parse_aar(msg)
            if record is None:
                log_aar_error_with_meta(
                    aar_id,
                    [f"Jump URL: {msg.jump_url}", "Parse failed: record is None"],
                    msg,
                )
                await _set_aar_reaction(msg, "error")
                still_broken += 1
            else:
                errors = validate_aar(record)
                if errors:
                    log_aar_error_with_meta(
                        aar_id, [f"Jump URL: {msg.jump_url}"] + errors, msg
                    )
                    await _set_aar_reaction(msg, "error")
                    still_broken += 1
                else:
                    save_aar_record(record)
                    data = _load_json_dict(AAR_ERRORS_PATH)
                    sid = str(aar_id)
                    if sid in data:
                        del data[sid]
                        _save_json_dict(AAR_ERRORS_PATH, data)
                    await _set_aar_reaction(msg, "ok")
                    fixed += 1
            done_errs += 1
            if cutoff_dt is None:
                if (done_errs % 5 == 0) or (done_errs == total_errs):
                    _print_progress("Recheck Errors", done_errs, total_errs)

    if cutoff_dt is None:
        remaining_errors = _load_json_dict(AAR_ERRORS_PATH)
        still_broken = len(remaining_errors)
    # For windowed runs, still_broken already reflects the subset processed
    return fixed, still_broken


async def _run_ingest_new(aar_channel: discord.TextChannel, span_days: Optional[int]):
    ingested = 0
    rejected = 0
    history_kwargs = {"limit": None}
    cutoff_dt = None
    if span_days and span_days > 0:
        cutoff_dt = datetime.utcnow() - timedelta(days=span_days)
        history_kwargs["after"] = cutoff_dt

    processed_ids = load_processed_ids()
    latest_processed_id: Optional[int] = None
    try:
        if processed_ids:
            latest_processed_id = max(int(x) for x in processed_ids if str(x).isdigit())
    except Exception:
        latest_processed_id = None

    scanned = 0
    to_react_ok: list[discord.Message] = []
    to_react_err: list[discord.Message] = []
    if cutoff_dt is None and latest_processed_id:
        try:
            history_kwargs["after"] = discord.Object(id=latest_processed_id)
        except Exception:
            pass

    async for msg in aar_channel.history(**history_kwargs):
        if not is_aar_message(msg):
            continue
        scanned += 1
        if cutoff_dt is None and latest_processed_id and msg.id <= latest_processed_id:
            _print_progress("Ingest New AARs", scanned, scanned)
            break
        record = parse_aar(msg)
        if record is None:
            log_aar_error_with_meta(
                msg.id,
                [f"Jump URL: {msg.jump_url}", "Parse failed: record is None"],
                msg,
            )
            to_react_err.append(msg)
            rejected += 1
            if scanned % 10 == 0:
                _print_progress("Ingest New AARs", scanned, scanned)
            continue
        aar_id = record.get("aar_id", msg.id)
        if has_been_processed(aar_id):
            existing = _load_json_dict(AAR_RECORDS_PATH).get(str(aar_id))
            existing_hash = (
                (existing or {}).get("content_hash")
                if isinstance(existing, dict)
                else None
            )
            existing_edited = (
                (existing or {}).get("edited_at")
                if isinstance(existing, dict)
                else None
            )
            msg_hash = record.get("content_hash")
            msg_edited = record.get("edited_at")
            needs_update = (msg_hash and msg_hash != existing_hash) or (
                msg_edited and msg_edited != existing_edited
            )
            if not needs_update:
                if scanned % 10 == 0:
                    _print_progress("Ingest New AARs", scanned, scanned)
                continue
        errors = validate_aar(record)
        if errors:
            log_aar_error_with_meta(aar_id, [f"Jump URL: {msg.jump_url}"] + errors, msg)
            to_react_err.append(msg)
            rejected += 1
            if scanned % 10 == 0:
                _print_progress("Ingest New AARs", scanned, scanned)
            continue
        save_aar_record(record)
        to_react_ok.append(msg)
        ingested += 1
        if scanned % 10 == 0:
            _print_progress("Ingest New AARs", scanned, scanned)

        if len(to_react_ok) + len(to_react_err) >= 25:
            for m in to_react_ok:
                await _set_aar_reaction(m, "ok")
            for m in to_react_err:
                await _set_aar_reaction(m, "error")
            to_react_ok.clear()
            to_react_err.clear()

    if to_react_ok or to_react_err:
        for m in to_react_ok:
            await _set_aar_reaction(m, "ok")
        for m in to_react_err:
            await _set_aar_reaction(m, "error")

    return ingested, rejected


@bot.tree.command(
    name="tally_deeds", description="Display the Deeds Ledger for a Brother."
)
@app_commands.describe(
    brother="The Watch Brother to query.",
    killteam="Role: tally every member of this kill team (mutually exclusive with brother)",
)
async def tally_deeds(
    interaction: discord.Interaction,
    brother: Optional[discord.Member] = None,
    killteam: Optional[discord.Role] = None,
):
    if not (is_watch_command(interaction.user) and is_allowed_channel(interaction)):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    # First response: defer, so we can do slower work safely
    await interaction.response.defer(thinking=False, ephemeral=True)

    # Mutual exclusivity and target selection: either a single brother or a killteam role
    if brother and killteam:
        await interaction.response.send_message(
            "Provide either 'brother' or 'killteam', not both.", ephemeral=True
        )
        return

    if killteam:
        members = [m for m in getattr(killteam, "members", [])]
        if not members:
            await interaction.followup.send(
                f"Killteam role '{getattr(killteam, 'name', '')}' has no members.",
                ephemeral=True,
            )
            return
    elif brother:
        members = [brother]
    else:
        await interaction.response.send_message(
            "Specify a brother or a killteam role.", ephemeral=True
        )
        return

    # We'll build one aggregated reply containing a block for each member
    member_blocks: list[str] = []
    # Aggregates for killteam summary
    agg_ops = 0
    agg_aar = 0
    agg_gene = 0
    agg_armory_raw = 0
    agg_waves = 0

    # Resolve home chapters for all members once (optimize network calls)
    try:
        all_ids = [str(m.id) for m in members]
        chapters_map = await _resolve_home_chapters(interaction.guild, all_ids)
    except Exception:
        chapters_map = {}

    # Process each member and build a block for each
    for target in members:
        stats = compute_stats_for_user(str(target.id))
        # Accumulate for team averages
        try:
            agg_ops += int(stats.get("ops", 0))
        except Exception:
            pass
        try:
            agg_aar += float(stats.get("aar_points", 0))
        except Exception:
            pass
        try:
            agg_gene += float(stats.get("gene_seed_points", 0))
        except Exception:
            pass
        try:
            agg_armory_raw += float(stats.get("armory_raw", 0))
        except Exception:
            pass
        try:
            agg_waves += float(stats.get("waves_participated", 0))
        except Exception:
            pass

        current_rank = "Unknown"
        for rank in RANK_ROLES_PRIORITY:
            for role in target.roles:
                if role.name == rank:
                    current_rank = rank
                    break
            if current_rank != "Unknown":
                break

        display_name = target.nick or target.display_name

        # Member join date (server join time); fallback to 'Unknown' if unavailable
        try:
            joined_at = getattr(target, "joined_at", None)
            joined_str = (
                joined_at.strftime("%Y-%m-%d %H:%M UTC") if joined_at else "Unknown"
            )
        except Exception:
            joined_str = "Unknown"

        data = load_aar_data(AAR_RECORDS_PATH)
        ops_trials = 0
        siege_inductions = 0
        for rec in data.values():
            try:
                brother_ids = rec.get("brother_ids") or []
                if str(target.id) not in brother_ids:
                    continue
                if not bool(rec.get("initiation_trial")):
                    continue
                dclass = (rec.get("difficulty_class") or "").lower()
                if "siege" in dclass:
                    # Siege initiation counts immediately as one induction
                    siege_inductions += 1
                else:
                    # Operation initiation requires three trials to count as one induction
                    ops_trials += 1
            except Exception:
                # Be resilient to malformed records
                pass
        trials_reported = siege_inductions + (ops_trials // 3)

        # Home chapter from resolved map (fallback: REDACTED)
        home_chapter = chapters_map.get(str(target.id)) if chapters_map else "REDACTED"

        # Determine Active/Inactive status: Active if any AAR in last 28 days.
        try:
            data = load_aar_data(AAR_RECORDS_PATH)
            # Collect timestamps for AARs involving this user
            timestamps = []
            for rec in data.values():
                if str(target.id) in (rec.get("brother_ids") or []):
                    ts = rec.get("timestamp")
                    if not ts:
                        continue
                    try:
                        t = datetime.fromisoformat(ts)
                    except Exception:
                        # Skip records with unparseable timestamps
                        continue
                    # Ensure naive datetimes are treated as UTC
                    if t.tzinfo is not None:
                        # Convert to UTC naive for comparison with datetime.utcnow()
                        try:
                            t = t.astimezone(tz=None).replace(tzinfo=None)
                        except Exception:
                            # Fallback: drop tzinfo
                            t = t.replace(tzinfo=None)
                    timestamps.append(t)

            status = "Inactive"
            if timestamps:
                # Sort newest first and check if any within the last 28 days from now
                timestamps.sort(reverse=True)
                now = datetime.utcnow()
                cutoff = now - timedelta(days=28)
                # If any timestamp is newer than cutoff, mark Active
                for t in timestamps:
                    if t >= cutoff:
                        status = "Active"
                        break
        except Exception:
            status = "Inactive"

        # Determine Company and Kill Team visibility and values per rank/command rules
        show_company = False
        show_killteam = False
        company = "Unknown"
        kt_name = "Unknown"
        try:
            role_names = _canonical_role_names(target)
            roles = getattr(target, "roles", [])

            # High command ranks that should NOT show Company
            high_command = {
                "Watch Master",
                "Lord Executioner",
                "Forgemaster",
                "Void Warden",
                "Chief Apothecary",
                "High Chaplain",
            }

            show_company = not any(r in role_names for r in high_command)
            if show_company:
                # Prefer an explicit role that contains the word 'company'
                for role in roles:
                    rn = getattr(role, "name", "") or ""
                    if "company" in rn.lower():
                        company = rn
                        break

            # Show Kill Team only for Sergeant and below
            idx_sergeant = _role_index("Watch Sergeant")
            highest_idx = get_highest_rank_index(target)
            if idx_sergeant is None:
                show_killteam = False
            elif highest_idx is None:
                show_killteam = True
            else:
                show_killteam = highest_idx >= idx_sergeant

            if show_killteam:
                for role in roles:
                    rn = getattr(role, "name", "") or ""
                    rn_l = rn.lower()
                    if "kill" in rn_l and "team" in rn_l:
                        kt_name = _extract_killteam_name(rn)
                        break
        except Exception:
            pass

        # Column-aligned stats
        stat_rows = [
            ("Status", status),
            ("Induction", joined_str),
            ("Home Chapter", home_chapter),
            ("Rank", current_rank),
        ]
        if show_company:
            stat_rows.append(("Company", company))
        if show_killteam:
            stat_rows.append(("Kill Team", kt_name))
        stat_rows.extend(
            [
                ("Total Operations", str(stats["ops"])),
                ("Total Siege Waves", str(stats["waves_participated"])),
                ("Brothers Sanctioned", str(trials_reported)),
                ("AAR Commendations", str(stats["aar_points"])),
                ("Gene-seed Secured", str(stats["gene_seed_points"])),
                ("Armory Data Recovered", str(stats["armory_points"])),
            ]
        )
        label_width = max(len(label) for label, _ in stat_rows) + 2
        lines = []
        lines.append("```ansi")
        lines.append(
            "\u001b[32m=============================================================================="
        )
        lines.append("  WATCH FORTRESS JERICHO // SERVICE-RECORD NODE")
        lines.append("  OPERATION-SCRIBE SERVITOR — DEEDS LEDGER")
        lines.append(
            "=============================================================================="
        )
        lines.append(f"  Tally for: {display_name}")
        lines.append(
            "------------------------------------------------------------------------------"
        )
        for label, value in stat_rows:
            lines.append(f"  {label:<{label_width}} {value}")
        lines.append(
            "=============================================================================="
        )
        lines.append("  Machine-Spirit Addendum:")
        lines.append("  These Deeds are logged for future deployment rites")
        lines.append("  and may be invoked by decree of Watch Command alone.")
        lines.append(
            "=============================================================================="
        )
        lines.append("\u001b[0m```")
        member_blocks.append("\n".join(lines))

    # Send one aggregated followup containing a block per member
    reply_text = "\n\n".join(member_blocks)

    # If killteam requested, prepare a short summary (under 2000 chars)
    if killteam:
        count = len(members)
        recent_records = _get_recent_missions(limit=100)
        member_ids = {
            str(getattr(m, "id", "")) for m in members if getattr(m, "id", None)
        }

        ops_count = 0
        aar_vals: List[float] = []
        gene_vals: List[float] = []
        armory_vals: List[float] = []
        waves_vals: List[float] = []  # siege-only

        for rec in recent_records:
            try:
                bros = [str(b) for b in (rec.get("brother_ids") or [])]
                participants_in_team = sum(1 for b in bros if b in member_ids)
                if participants_in_team <= 0:
                    continue
                ops_count += 1
                aar = float(rec.get("points_for_op", 0) or 0)
                armory = float(
                    rec.get("armory_challenge_points", rec.get("armory_data", 0) or 0)
                    or 0
                )
                gene = 0.0
                if (rec.get("gene_seed_status") or "").lower() == "carried":
                    gene = float(rec.get("gene_seed_base_points_for_carrier", 0) or 0)
                aar_vals.append(aar)
                armory_vals.append(armory)
                gene_vals.append(gene)
                dclass = (rec.get("difficulty_class") or "").lower()
                if "siege" in dclass:
                    waves_vals.append(float(rec.get("waves", 0) or 0))
            except Exception:
                pass

        def _mean(vals: List[float]) -> float:
            return (sum(vals) / len(vals)) if vals else 0.0

        avg_aar = _mean(aar_vals)
        avg_gene = _mean(gene_vals)
        avg_armory = _mean(armory_vals)
        avg_waves = _mean(waves_vals)

        # Format a compact ANSI-styled summary similar to individual tally output
        stat_rows_summary = [
            ("Window", "Last 100 AARs"),
            ("Kill Team", _extract_killteam_name(getattr(killteam, "name", "Unknown"))),
            ("Members", str(count)),
            ("Avg AAR Points", f"{avg_aar:.2f}"),
            ("Avg Gene-seed Points", f"{avg_gene:.2f}"),
            ("Avg Armory Data", f"{avg_armory:.2f}"),
            ("Operations", str(int(ops_count))),
            ("Avg Siege Waves", f"{avg_waves:.2f}"),
        ]
        label_width = max(len(label) for label, _ in stat_rows_summary) + 2
        s_lines = []
        s_lines.append("```ansi")
        s_lines.append(
            "\u001b[32m=============================================================================="
        )
        s_lines.append("  WATCH FORTRESS JERICHO // SERVICE-RECORD NODE")
        s_lines.append("  OPERATION-SCRIBE SERVITOR — KILL TEAM SUMMARY")
        s_lines.append(
            "=============================================================================="
        )
        for label, value in stat_rows_summary:
            s_lines.append(f"  {label:<{label_width}} {value}")
        s_lines.append(
            "=============================================================================="
        )
        s_lines.append("\u001b[0m```")
        summary_text = "\n".join(s_lines)
        try:
            await interaction.followup.send(summary_text, ephemeral=True)
        except Exception:
            # ignore send errors and proceed to attach full file
            pass

    # If caller requested a killteam, write the aggregated report to a temp file
    # and send it as a file attachment to the invoking user (ephemeral).
    if killteam:
        import tempfile

        try:
            with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as tf:
                tf.write(reply_text)
                tmp_path = tf.name
            # Send as file attachment; ephemeral send to the invoking user
            await interaction.followup.send(file=discord.File(tmp_path), ephemeral=True)
        except Exception:
            # Fallback to inline send on failure
            await interaction.followup.send(reply_text, ephemeral=True)
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
    else:
        await interaction.followup.send(reply_text, ephemeral=True)


@bot.tree.command(
    name="combat_bonds", description="Show top Combat Bonds (global or for a Brother)."
)
@app_commands.describe(
    brother="Optional: limit to bonds including this Brother.",
    window="Optional: number of most recent missions to consider.",
)
async def combat_bonds(
    interaction: discord.Interaction,
    brother: Optional[discord.Member] = None,
    window: Optional[int] = None,
):
    if not (
        is_sergeant_or_higher(interaction.user) and is_allowed_channel(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    # No defer: send a direct response to clear the interaction state

    span = window if (isinstance(window, int) and window > 0) else 100
    missions = _get_recent_missions(limit=span)
    # Collect all brothers seen in window
    all_bros: List[str] = []
    for rec in missions:
        all_bros.extend([str(b) for b in (rec.get("brother_ids") or [])])
    all_bros = sorted(set(all_bros))

    pair_counts = _build_pair_counts(missions)
    triples = _build_triple_bonds(pair_counts, all_bros)

    if brother is None:
        top_global = _select_top_global_bonds(triples, top_n=5)
        # Resolve chapters for all user IDs appearing in selected bonds
        uids: List[str] = []
        for tri, _score in top_global:
            uids.extend(list(tri))
        chapters = await _resolve_home_chapters(interaction.guild, sorted(set(uids)))
        text = _format_bonds_for_discord(
            top_global, interaction.guild, window_span=span, chapters=chapters
        )
        await interaction.response.send_message(text, ephemeral=True)
    else:
        target_id = str(brother.id)
        personal = _select_personal_bonds(triples, target_id, max_n=3)
        uids: List[str] = []
        for tri, _score in personal:
            uids.extend(list(tri))
        chapters = await _resolve_home_chapters(interaction.guild, sorted(set(uids)))
        text = _format_bonds_for_discord(
            personal, interaction.guild, window_span=span, chapters=chapters
        )
        await interaction.response.send_message(text, ephemeral=True)


@bot.tree.command(
    name="command_brief",
    description="Brief Watch Command on company kill teams (recent AARs).",
)
@app_commands.describe(
    company="The Watch Company role to analyze.",
    days="Optional: number of days to include (default 7).",
)
async def command_brief(
    interaction: discord.Interaction,
    company: discord.Role,
    days: Optional[int] = None,
):
    # Permissions: Sergeant and higher, restricted channel
    if not (
        is_sergeant_or_higher(interaction.user) and is_allowed_channel(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True, ephemeral=True)

    # Validate role
    if not company or not getattr(company, "members", None):
        await interaction.followup.send(
            "Provide a valid company role with members.", ephemeral=True
        )
        return

    guild = interaction.guild
    span_days = days if (isinstance(days, int) and days > 0) else 7
    recent_records = _get_missions_last_days(span_days)

    # Guard: ensure there are records for this company in the 30-day window
    try:
        company_ids: set[str] = {
            str(getattr(m, "id", ""))
            for m in getattr(company, "members", [])
            if getattr(m, "id", None)
        }
    except Exception:
        company_ids = set()
    has_company_records = False
    try:
        for rec in recent_records:
            bros = [str(b) for b in (rec.get("brother_ids") or [])]
            if set(bros) & company_ids:
                has_company_records = True
                break
    except Exception:
        has_company_records = False
    if not has_company_records:
        await interaction.followup.send(
            f"No AARs recorded in the last {span_days} days for this company window.",
            ephemeral=True,
        )
        return

    # Collect kill team roles (exclude rank-style roles) and map current membership (restricted to company)
    killteam_roles: List[discord.Role] = []
    try:
        for role in getattr(guild, "roles", []):
            rn = getattr(role, "name", "") or ""
            rl = rn.lower()
            if ("kill" in rl) and ("team" in rl):
                if "champion" in rl:
                    continue
                killteam_roles.append(role)
    except Exception:
        pass

    teams: List[dict] = []
    for kt in killteam_roles:
        try:
            kt_members = [
                m
                for m in getattr(kt, "members", [])
                if company in getattr(m, "roles", [])
            ]
        except Exception:
            kt_members = []
        member_ids = {
            str(getattr(m, "id", "")) for m in kt_members if getattr(m, "id", None)
        }
        if not member_ids:
            continue
        teams.append(
            {
                "role": kt,
                "name": _extract_killteam_name(getattr(kt, "name", "Unknown")),
                "member_ids": member_ids,
                "count": len(member_ids),
            }
        )

    # Also compute a synthetic team for Company Command
    try:
        idx_sergeant = _role_index("Watch Sergeant")
        idx_captain = _role_index("Watch Captain")
    except Exception:
        idx_sergeant = None
        idx_captain = None

    company_command_members: List[discord.Member] = []
    try:
        base_members = list(getattr(company, "members", []))
        for m in base_members:
            roles = getattr(m, "roles", [])
            # Must have a role that looks like "Watch Company Primus" (case-insensitive)
            has_primus = False
            for r in roles:
                rn = (getattr(r, "name", "") or "").lower()
                if ("primus" in rn) and ("company" in rn):
                    has_primus = True
                    break
            highest_idx = get_highest_rank_index(m)
            if (
                has_primus
                and idx_sergeant is not None
                and idx_captain is not None
                and highest_idx is not None
                and (
                    idx_captain <= highest_idx < idx_sergeant
                )  # Captain through just above Sergeant
            ):
                company_command_members.append(m)
    except Exception:
        company_command_members = []

    if len(company_command_members) > 0:
        member_ids = {
            str(getattr(m, "id", ""))
            for m in company_command_members
            if getattr(m, "id", None)
        }
        teams.append(
            {
                "role": None,
                "name": "Company Command",
                "member_ids": member_ids,
                "count": len(member_ids),
            }
        )

    # Compute per-team aggregates across recent_records using current membership
    team_stats: List[dict] = []
    for team in teams:
        mids = team["member_ids"]
        ops_count = 0
        aar_vals: List[float] = []
        armory_vals: List[float] = []
        gene_vals: List[float] = []
        waves_vals: List[float] = []  # siege-only
        total_scores: List[float] = []
        per_capita_vals: List[float] = []

        for rec in recent_records:
            bros = [str(b) for b in (rec.get("brother_ids") or [])]
            participants_in_team = sum(1 for b in bros if b in mids)
            if participants_in_team <= 0:
                continue

            ops_count += 1
            aar = float(rec.get("points_for_op", 0) or 0)
            # Prefer armory challenge points if present, else raw armory_data
            armory = float(
                rec.get("armory_challenge_points", rec.get("armory_data", 0) or 0) or 0
            )
            gene = 0.0
            try:
                if (rec.get("gene_seed_status") or "").lower() == "carried":
                    gene = float(rec.get("gene_seed_base_points_for_carrier", 0) or 0)
            except Exception:
                gene = 0.0
            aar_vals.append(aar)
            armory_vals.append(armory)
            gene_vals.append(gene)
            total_scores.append(aar + armory + gene)

            # Siege-only waves
            try:
                dclass = (rec.get("difficulty_class") or "").lower()
                if "siege" in dclass:
                    waves_vals.append(float(rec.get("waves", 0) or 0))
            except Exception:
                pass

            # Per-capita force multiplier uses AAR per participating member
            try:
                if participants_in_team > 0:
                    per_capita_vals.append(aar / float(participants_in_team))
            except Exception:
                pass

        def _mean(vals: List[float]) -> float:
            return (sum(vals) / len(vals)) if vals else 0.0

        def _pstdev(vals: List[float]) -> float:
            return statistics.pstdev(vals) if len(vals) >= 2 else 0.0

        avg_aar = _mean(aar_vals)
        avg_armory = _mean(armory_vals)
        avg_gene = _mean(gene_vals)
        avg_waves = _mean(waves_vals)
        reliability = 0.0
        if total_scores:
            reliability = _mean(total_scores) / (1.0 + _pstdev(total_scores))
        force_multiplier = _mean(per_capita_vals)

        team_stats.append(
            {
                "role": team["role"],
                "name": team["name"],
                "count": team["count"],
                "avg_ops": float(ops_count),  # Operational Tempo now counts ops
                "avg_aar": avg_aar,
                "avg_gene": avg_gene,
                "avg_armory": avg_armory,
                "avg_waves": avg_waves,
                "reliability": reliability,
                "force_multiplier": force_multiplier,
            }
        )

    if not team_stats:
        await interaction.followup.send(
            "No kill teams found for the provided company.", ephemeral=True
        )
        return

    # Find category winners
    def _winner(key: str):
        return max(team_stats, key=lambda t: t.get(key, 0.0))

    best_lethality = _winner("avg_aar")
    best_preservation = _winner("avg_gene")
    best_armory = _winner("avg_armory")
    best_tempo = _winner("avg_ops")
    best_siegebreaker = _winner("avg_waves")
    best_reliability = _winner("reliability")
    best_force = _winner("force_multiplier")

    # Risk Appetite: Shock vs Surgical
    # Risk appetite via medians
    try:
        aar_list = [t["avg_aar"] for t in team_stats]
        gene_list = [t["avg_gene"] for t in team_stats]
        med_aar = statistics.median(aar_list) if aar_list else 0.0
        med_gene = statistics.median(gene_list) if gene_list else 0.0
        ops_list = [t["avg_ops"] for t in team_stats]
        med_ops = statistics.median(ops_list) if ops_list else 0.0
        armory_list = [t["avg_armory"] for t in team_stats]
        med_armory = statistics.median(armory_list) if armory_list else 0.0
        rel_list = [t["reliability"] for t in team_stats]
        med_rel = statistics.median(rel_list) if rel_list else 0.0
        fm_list = [t["force_multiplier"] for t in team_stats]
        med_fm = statistics.median(fm_list) if fm_list else 0.0
        shock_deltas = [tt["avg_aar"] - tt["avg_gene"] for tt in team_stats]
        med_shock = statistics.median(shock_deltas) if shock_deltas else 0.0
        surg_deltas = [tt["avg_gene"] - tt["avg_aar"] for tt in team_stats]
        med_surg = statistics.median(surg_deltas) if surg_deltas else 0.0
        # Standard deviations for outlier detection
        sd_ops = statistics.pstdev(ops_list) if len(ops_list) >= 2 else 0.0
        sd_armory = statistics.pstdev(armory_list) if len(armory_list) >= 2 else 0.0
        sd_rel = statistics.pstdev(rel_list) if len(rel_list) >= 2 else 0.0
        sd_fm = statistics.pstdev(fm_list) if len(fm_list) >= 2 else 0.0
        sd_gene = statistics.pstdev(gene_list) if len(gene_list) >= 2 else 0.0
        sd_shock = statistics.pstdev(shock_deltas) if len(shock_deltas) >= 2 else 0.0
        sd_surg = statistics.pstdev(surg_deltas) if len(surg_deltas) >= 2 else 0.0
    except Exception:
        med_aar = 0.0
        med_gene = 0.0
        med_ops = 0.0
        med_armory = 0.0
        med_rel = 0.0
        med_fm = 0.0
        med_shock = 0.0
        med_surg = 0.0
        sd_ops = sd_armory = sd_rel = sd_fm = sd_gene = sd_shock = sd_surg = 0.0

    shock_candidates = []
    surgical_candidates = []
    for t in team_stats:
        a = t["avg_aar"]
        g = t["avg_gene"]
        if a > med_aar and g < med_gene:
            score = (a - med_aar) + (med_gene - g)
            shock_candidates.append((t, score))
        if a < med_aar and g > med_gene:
            score = (med_aar - a) + (g - med_gene)
            surgical_candidates.append((t, score))

    if shock_candidates:
        shock_team, shock_score = max(shock_candidates, key=lambda x: x[1])
    else:
        shock_team = max(team_stats, key=lambda t: (t["avg_aar"] - t["avg_gene"]))
        shock_score = (
            (shock_team["avg_aar"] - med_aar) + (med_gene - shock_team["avg_gene"])
            if med_aar or med_gene
            else (shock_team["avg_aar"] - shock_team["avg_gene"])
        )

    if surgical_candidates:
        surgical_team, surgical_score = max(surgical_candidates, key=lambda x: x[1])
    else:
        surgical_team = max(team_stats, key=lambda t: (t["avg_gene"] - t["avg_aar"]))
        surgical_score = (
            (med_aar - surgical_team["avg_aar"])
            + (surgical_team["avg_gene"] - med_gene)
            if med_aar or med_gene
            else (surgical_team["avg_gene"] - surgical_team["avg_aar"])
        )

    # Force Multiplier: per-op per-capita AAR average
    force_multiplier = best_force

    # Company specialists: pick most recently active member for each role
    def _member_display_name(m: discord.Member) -> str:
        try:
            return (
                getattr(m, "nick", None)
                or getattr(m, "display_name", None)
                or getattr(m, "name", None)
                or str(getattr(m, "id", "Unknown"))
            )
        except Exception:
            return str(getattr(m, "id", "Unknown"))

    # Build recency index from recent_records (0 = most recent)
    recency_index: Dict[str, int] = {}
    try:
        for idx, rec in enumerate(reversed(list(recent_records))):
            for b in rec.get("brother_ids") or []:
                sb = str(b)
                if sb not in recency_index:
                    recency_index[sb] = idx
    except Exception:
        recency_index = {}

    spec_roles_map = {
        "Watch Chaplain": "Chaplain",
        "Watch Apothecary": "Apothecary",
        "Watch Techmarine": "Techmarine",
        "Watch Librarian": "Librarian",
    }
    spec_names: Dict[str, str] = {}
    try:
        company_members = list(getattr(company, "members", []))
        for role_key, short_label in spec_roles_map.items():
            candidates: List[discord.Member] = []
            for m in company_members:
                names = _canonical_role_names(m)
                if role_key in names:
                    candidates.append(m)
            if not candidates:
                spec_names[short_label] = short_label
                continue
            # Pick most recently active by lowest recency index
            best_m = None
            best_idx = float("inf")
            for m in candidates:
                mid = str(getattr(m, "id", ""))
                idx = recency_index.get(mid, float("inf"))
                if idx < best_idx:
                    best_idx = idx
                    best_m = m
            spec_names[short_label] = (
                _member_display_name(best_m)
                if best_m
                else _member_display_name(candidates[0])
            )
    except Exception:
        # Fallback to role labels if failure
        for _rk, _sl in spec_roles_map.items():
            spec_names[_sl] = _sl

    # High Command assessment note
    # Identify High Command members and compute deployments and lethality relative to medians
    high_command_roles = {
        "Watch Master",
        "Forgemaster",
        "Lord Executioner",
        "Void Warden",
        "Voidwarden",
        "Chief Apothecary",
        "High Chaplain",
    }
    hc_ids: set[str] = set()
    try:
        for m in getattr(guild, "members", []):
            names = _canonical_role_names(m)
            if any(r in names for r in high_command_roles):
                uid = str(getattr(m, "id", ""))
                if uid:
                    hc_ids.add(uid)
    except Exception:
        hc_ids = set()

    hc_ops_count = 0
    hc_aar_vals: List[float] = []
    try:
        for rec in recent_records:
            bros = [str(b) for b in (rec.get("brother_ids") or [])]
            if any(b in hc_ids for b in bros):
                hc_ops_count += 1
                try:
                    hc_aar_vals.append(float(rec.get("points_for_op", 0) or 0))
                except Exception:
                    pass
    except Exception:
        pass

    def _mean(vals: List[float]) -> float:
        return (sum(vals) / len(vals)) if vals else 0.0

    hc_avg_aar = _mean(hc_aar_vals)

    # Determine case text
    hc_note_lines: List[str] = []
    if hc_ops_count <= 0:
        hc_note_lines = [
            "+ High Command recorded no deployments during this window.",
            "+ The Watch Master and the heads of the specialist orders remained committed ",
            "  to strategic oversight and internal readiness.",
        ]
    elif hc_ops_count < med_ops and hc_avg_aar > med_aar:
        hc_note_lines = [
            "+ High Command deployments during this window exceeded the company median in ",
            "  lethality but remained limited in frequency by doctrine.",
            "+ The Watch Master and the heads of the specialist orders deploy only when ",
            "  strategic necessity overrides standing command duties.",
        ]
    elif hc_ops_count < med_ops:
        hc_note_lines = [
            "+ High Command deployments during this window were limited in scope and ",
            "  aligned with oversight and command responsibilities.",
            "+ The Watch Master and the heads of the specialist orders prioritize ",
            "  continuity of command over routine engagement.",
        ]
    else:
        hc_note_lines = [
            "+ High Command maintained an elevated operational tempo during this window, ",
            "  temporarily assuming direct battlefield roles.",
            "+ This level of commitment indicates an exceptional operational posture under ",
            "  the Watch Master’s authority.",
        ]

    # Helper: label teams; prefix with "Kill Team" unless Company Command
    def _team_label(team: dict) -> str:
        name = team.get("name", "Unknown")
        return "Company Command" if name == "Company Command" else f"Kill Team {name}"

    # Abbreviated label for Company Command notes (KT instead of Kill Team)
    def _abbr_label(team: dict) -> str:
        try:
            return _team_label(team).replace("Kill Team ", "KT ")
        except Exception:
            return _team_label(team)

    # Render brief
    lines: List[str] = []
    lines.append("```ansi")
    lines.append(
        "\u001b[32m=============================================================================="
    )
    lines.append("  WATCH FORTRESS JERICHO // COMMAND BRIEF")
    lines.append("  OPERATION-SCRIBE SERVITOR — COMMAND BRIEF")
    lines.append(
        "=============================================================================="
    )
    lines.append(f"  {getattr(company, 'name', 'Unknown')}  |  Window: Last {span_days} Days")
    lines.append(
        "------------------------------------------------------------------------------"
    )
    lines.append(
        f"  Veteran Lethality Index     :: {_team_label(best_lethality)}  (Avg AAR: {best_lethality['avg_aar']:.2f})"
    )
    # Gene-seed and armory metrics are owned by Apothecarion and Techmarine briefs.
    lines.append(
        f"  Operational Tempo           :: {_team_label(best_tempo)}  (Ops: {int(best_tempo['avg_ops'])})"
    )
    lines.append(
        f"  Siegebreaker Rating         :: {_team_label(best_siegebreaker)}  (Avg Waves: {best_siegebreaker['avg_waves']:.2f})"
    )
    lines.append(
        f"  Kill Team Reliability Index :: {_team_label(best_reliability)}  (Score: {best_reliability['reliability']:.2f})"
    )
    lines.append(
        f"  Risk Appetite — Shock       :: {_team_label(shock_team)}  (Score: {shock_score:.2f})"
    )
    lines.append(
        f"  Risk Appetite — Surgical    :: {_team_label(surgical_team)}  (Score: {surgical_score:.2f})"
    )
    lines.append(
        f"  Force Multiplier Rating     :: {_team_label(force_multiplier)}  (Avg AAR/Member: {force_multiplier.get('force_multiplier', 0.0):.2f})"
    )
    # Company Command Notes section removed
    lines.append(
        "------------------------------------------------------------------------------"
    )
    lines.append("  High Command Notes:")
    # # Compact numeric comparison to show values used in assessment
    # try:
    #     lines.append(f"  [ops {hc_ops_count}|{int(med_ops)}; aar {hc_avg_aar:.1f}|{med_aar:.1f}]")
    # except Exception:
    #     pass
    for ln in hc_note_lines:
        lines.append(f"  {ln}")
    lines.append(
        "=============================================================================="
    )
    lines.append("\u001b[0m```")

    await interaction.followup.send("\n".join(lines), ephemeral=True)
    
@bot.tree.command(
    name="techmarine_brief",
    description="Report materiel recovery and armory efficiency (recent AARs).",
)
@app_commands.describe(
    company="The Watch Company role to analyze.",
    days="Optional: number of days to include (default 7).",
)
async def techmarine_brief(
    interaction: discord.Interaction,
    company: discord.Role,
    days: Optional[int] = None,
):
    # Permissions: Sergeant and higher, restricted channel
    if not (is_sergeant_or_higher(interaction.user) and is_allowed_channel(interaction)):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True, ephemeral=True)

    # Validate role
    if not company or not getattr(company, "members", None):
        await interaction.followup.send(
            "Provide a valid company role with members.", ephemeral=True
        )
        return

    guild = interaction.guild
    span_days = days if (isinstance(days, int) and days > 0) else 7
    recent_records = _get_missions_last_days(span_days)

    # Build roster per Kill Team for the selected company (intersection of members)
    killteam_roles: List[discord.Role] = []
    try:
        for role in getattr(guild, "roles", []):
            rn = getattr(role, "name", "") or ""
            rl = rn.lower()
            if ("kill" in rl) and ("team" in rl):
                if "champion" in rl:
                    continue
                killteam_roles.append(role)
    except Exception:
        pass

    company_members: List[discord.Member] = [m for m in getattr(company, "members", [])]
    company_ids: set[str] = {str(getattr(m, "id", "")) for m in company_members if getattr(m, "id", None)}

    # Guard: ensure there are records for this company in the 30-day window
    has_company_records = False
    try:
        for rec in recent_records:
            bros = [str(b) for b in (rec.get("brother_ids") or [])]
            if set(bros) & company_ids:
                has_company_records = True
                break
    except Exception:
        has_company_records = False
    if not has_company_records:
        await interaction.followup.send(f"No AARs recorded in the last {span_days} days for this company window.", ephemeral=True)
        return

    teams: List[Tuple[str, List[discord.Member]]] = []
    for kt in killteam_roles:
        kt_members = [m for m in getattr(kt, "members", []) if str(getattr(m, "id", "")) in company_ids]
        if kt_members:
            teams.append((_extract_killteam_name(getattr(kt, "name", "Unknown")), kt_members))

    # Company Command synthetic team: Sergeant+ in company
    idx_sergeant = _role_index("Watch Sergeant")
    company_command_members: List[discord.Member] = []
    if idx_sergeant is not None:
        for m in company_members:
            if is_sergeant_or_higher(m):
                company_command_members.append(m)
    if company_command_members:
        teams.append(("Company Command", company_command_members))

    if not teams:
        await interaction.followup.send("No Kill Teams or Company Command members found in the selected company.", ephemeral=True)
        return

    # Compute armory-only metrics per team
    team_member_ids: Dict[str, set[str]] = {name: {str(getattr(m, "id", "")) for m in members if getattr(m, "id", None)} for name, members in teams}
    per_team_values: Dict[str, List[float]] = {name: [] for name, _ in teams}
    for rec in recent_records:
        try:
            bros = [str(b) for b in (rec.get("brother_ids") or [])]
            arm = rec.get("armory_data")
            try:
                arm_val = float(arm) if arm is not None else None
            except Exception:
                arm_val = None
            if arm_val is None:
                continue
            for name, mids in team_member_ids.items():
                if set(bros) & mids:
                    per_team_values[name].append(arm_val)
        except Exception:
            continue

    def _mean(vals: List[float]) -> float:
        return (sum(vals) / len(vals)) if vals else 0.0

    def _pstdev(vals: List[float]) -> float:
        return statistics.pstdev(vals) if len(vals) >= 2 else 0.0

    team_stats: List[Tuple[str, float, float, float]] = []  # (name, avg, sd, total)
    for name, vals in per_team_values.items():
        avg = _mean(vals)
        sd = _pstdev(vals)
        total = sum(vals) if vals else 0.0
        team_stats.append((name, avg, sd, total))

    # Derive winners/labels, restricting to teams with at least one data point where relevant
    nonempty = [t for t in team_stats if t[3] > 0]
    best_yield = max(nonempty, key=lambda t: t[1]) if nonempty else None
    best_consistency = min(nonempty, key=lambda t: t[2]) if nonempty else None
    total_all = sum(t[3] for t in nonempty) if nonempty else 0.0
    top_share = None
    if nonempty and total_all > 0:
        top_share = max(nonempty, key=lambda t: t[3])
    low_yield = min(nonempty, key=lambda t: t[1]) if nonempty else None

    def _label(name: str) -> str:
        return name if name == "Company Command" else f"KT {name}"

    # Render brief (concise, advisory, ANSI)
    lines: List[str] = []
    lines.append("```ansi")
    lines.append("\u001b[32m==============================================================================")
    lines.append("  WATCH FORTRESS JERICHO // ARMORY COGITATOR")
    lines.append("  OPERATION-SCRIBE SERVITOR — TECHMARINE BRIEF")
    lines.append("==============================================================================")
    lines.append(f"  {getattr(company, 'name', 'Unknown')}  |  Window: Last {span_days} Days")
    lines.append("------------------------------------------------------------------------------")

    if best_yield:
        lines.append(
            f"  Armory Yield Efficiency    :: {_label(best_yield[0])}  (Avg Armory Data: {best_yield[1]:.2f})"
        )
    else:
        lines.append("  Armory Yield Efficiency    :: —")

    if best_consistency:
        lines.append(
            f"  Armory Consistency Index   :: {_label(best_consistency[0])}  (SD: {best_consistency[2]:.2f} — lower indicates steadier recovery)"
        )
    else:
        lines.append("  Armory Consistency Index   :: —")

    if top_share and total_all > 0:
        percent = (top_share[3] / total_all) * 100.0
        lines.append(
            f"  Materiel Concentration     :: {_label(top_share[0])}  (Share: {percent:.0f}%)"
        )
    else:
        lines.append("  Materiel Concentration     :: —")

    if low_yield:
        lines.append(
            f"  Low-Yield Outlier          :: {_label(low_yield[0])}  (Avg Armory Data: {low_yield[1]:.2f})"
        )
    else:
        lines.append("  Low-Yield Outlier          :: —")

    lines.append("==============================================================================")
    lines.append("\u001b[0m```")

    await interaction.followup.send("\n".join(lines), ephemeral=True)


# ===== Librarius Dossier (Kill Team) =====
# Mission classification table (11 operations + Siege special)
MISSION_TAGS: Dict[str, Dict[str, List[str]]] = {
    "Inferno": {
        "env": ["Jungle", "Industrial", "Refinery"],
        "doctrine": ["Sabotage", "Perimeter Strike", "Extraction"],
    },
    "Decapitation": {
        "env": ["Open Approach", "Urban Ruin", "Bridgework"],
        "doctrine": ["Assassination", "Target Elimination"],
    },
    "Vox Liberatis": {
        "env": ["Urban Ruin", "ECCLESIARCHY Interior", "Lower Levels"],
        "doctrine": ["Communications", "Heretic Purge"],
    },
    "Reliquary": {
        "env": ["Catacombs", "Tomb Interior", "Bridge Corridor"],
        "doctrine": ["Beacon Destruction", "Infiltration"],
    },
    "Fall of Atreus": {
        "env": ["Necropolis", "Cathedral", "Mechanicus Base"],
        "doctrine": ["Advance & Prepare", "Securement"],
    },
    "Ballistic Engine": {
        "env": ["Industrial Exterior", "Storm Desert", "Train Station"],
        "doctrine": ["Weapon Delivery", "Sabotage"],
    },
    "Termination": {
        "env": ["Jungle", "Generator Hall", "Reclamation Center"],
        "doctrine": ["Extermination", "Area Clearing"],
    },
    "Obelisk": {
        "env": ["Bridge Underpass", "Ruin Hollow", "Underground"],
        "doctrine": ["Objective Disruption", "Dark Labyrinth"],
    },
    "Exfiltration": {
        "env": ["Urban Pursuit", "Extraction Corridors"],
        "doctrine": ["Extraction", "Break Contact"],
    },
    "Vortex": {
        "env": ["Warp-Affected Area", "Unstable Terrain"],
        "doctrine": ["Containment", "Ritual Disruption"],
    },
    "Reclamation": {
        "env": ["Industrial Ruins", "Recovery Zones"],
        "doctrine": ["Asset Recovery", "Area Securement"],
    },
}

# Environment macro categories used for band rendering
ENV_MACROS_ORDER: List[str] = [
    "JUNGLE",
    "URBAN",
    "INDUSTRIAL",
    "UNDERGROUND",
    "SACRAL",
    "DESERT",
    "WARP",
    "FORTRESS",
]


def _env_macro_for(tag: str) -> str:
    t = (tag or "").lower()
    if "fortress" in t or "defensive grid" in t:
        return "FORTRESS"
    if "warp" in t or "unstable" in t:
        return "WARP"
    if "jungle" in t:
        return "JUNGLE"
    if (
        "urban" in t
        or "train station" in t
        or "bridge" in t
        or "ruin" in t
        or "pursuit" in t
        or "extraction corridor" in t
    ):
        return "URBAN"
    if (
        "industrial" in t
        or "refinery" in t
        or "generator" in t
        or "mechanicus" in t
        or "reclamation" in t
        or "recovery" in t
        or "exterior" in t
        or "ruins" in t
    ):
        return "INDUSTRIAL"
    if "catacomb" in t or "tomb" in t or "underground" in t or "lower level" in t:
        return "UNDERGROUND"
    if "ecclesiarchy" in t or "cathedral" in t or "necropolis" in t:
        return "SACRAL"
    if "desert" in t or "open approach" in t or "open field" in t:
        return "DESERT"
    return "URBAN"


def _normalize_mission(name: Optional[str]) -> str:
    try:
        return re.sub(r"[^a-z0-9]+", "", (name or "").lower()).strip()
    except Exception:
        return (name or "").lower().strip()


def _match_mission_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    canon = list(MISSION_TAGS.keys())
    norm_map = {_normalize_mission(k): k for k in canon}
    target = _normalize_mission(name)
    # Direct match on normalized keys
    if target in norm_map:
        return norm_map[target]
    # Fuzzy match across normalized keys
    candidates = difflib.get_close_matches(
        target, list(norm_map.keys()), n=1, cutoff=0.6
    )
    if candidates:
        return norm_map.get(candidates[0])
    # Also try startswith/contains on raw names
    lower = (name or "").lower()
    for k in canon:
        if lower.startswith(k.lower()) or k.lower() in lower:
            return k
    return None


def _tags_for_record(rec: dict) -> Tuple[List[str], List[str], Optional[str]]:
    """Return (env_tags, doctrine_tags, canonical_mission) for a record.
    Siege Mode handled via difficulty_class -> FORTRESS/HOLD+ATTRITION.
    Unknown missions yield empty tag lists.
    """
    dlower = (rec.get("difficulty_class") or rec.get("difficulty") or "").lower()
    mission = rec.get("mission")
    # Siege special handling
    if (
        "normal_siege" in dlower
        or "hard_siege" in dlower
        or (mission and "siege" in mission.lower())
    ):
        return (
            ["Fortress Sectors", "Defensive Grid"],
            ["Hold", "Attrition"],
            "Siege Mode",
        )
    m = _match_mission_name(mission)
    if not m:
        return [], [], None
    info = MISSION_TAGS.get(m) or {}
    return info.get("env", []), info.get("doctrine", []), m


def _render_band(label: str, states: List[str], active: Optional[str]) -> str:
    parts = [f"[ {s} ]" if (active and s == active) else s for s in states]
    return f"  {label}: " + " ".join(parts)


def _render_single_band(label: str, active: Optional[str]) -> str:
    """Render only the bracketed active state for concise bands."""
    return f"  {label}: [ {active or 'UNDETERMINED'} ]"


def _doctrinal_coherence_tier(distinct_count: int) -> str:
    # Map number of distinct doctrine tags to coherence tier
    if distinct_count <= 1:
        return "SPECIALIZED"
    if distinct_count == 2:
        return "REFINED"
    if distinct_count <= 4:
        return "STABLE"
    if distinct_count <= 6:
        return "EMERGENT"
    return "FRAGMENTED"


def _operational_exposure_tier(distinct_missions: int) -> str:
    if distinct_missions <= 1:
        return "ISOLATED"
    if distinct_missions == 2:
        return "LIMITED"
    if distinct_missions <= 4:
        return "DIVERSE"
    if distinct_missions <= 7:
        return "BROAD"
    return "EXTENSIVE"


DOCTRINE_BAND_ORDER: List[str] = [
    "Sabotage",
    "Perimeter Strike",
    "Extraction",
    "Assassination",
    "Target Elimination",
    "Communications",
    "Heretic Purge",
    "Beacon Destruction",
    "Infiltration",
    "Advance & Prepare",
    "Securement",
    "Weapon Delivery",
    "Extermination",
    "Area Clearing",
    "Objective Disruption",
    "Dark Labyrinth",
    "Break Contact",
    "Containment",
    "Ritual Disruption",
    "Asset Recovery",
    "Area Securement",
    "Hold",
    "Attrition",
]


@bot.tree.command(
    name="librarian_brief",
    description="Generate a Librarian dossier for a Kill Team over recent AARs.",
)
@app_commands.describe(
    company="The Watch Company role to analyze.",
    days="Optional: number of days to include (default 7).",
)
async def librarian_brief(
    interaction: discord.Interaction,
    company: discord.Role,
    days: Optional[int] = None,
):
    # Permissions: Sergeant and higher, restricted channel
    if not (
        is_sergeant_or_higher(interaction.user) and is_allowed_channel(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True, ephemeral=True)

    guild = interaction.guild
    if not guild:
        await interaction.followup.send(
            "++ ERROR: Guild not available. ++", ephemeral=True
        )
        return

    if not company or not getattr(company, "members", None):
        await interaction.followup.send(
            "++ ERROR: Company role not found or empty. ++", ephemeral=True
        )
        return

    # Window of records to analyze: last N days (default 7)
    span_days = days if (isinstance(days, int) and days > 0) else 7
    recent_records = _get_missions_last_days(span_days)

    # Identify Kill Team roles within the fortress
    killteam_roles: List[discord.Role] = []
    try:
        for r in getattr(guild, "roles", []):
            n = (getattr(r, "name", "") or "").strip()
            if re.search(r"(?i)^\s*kill\s*team", n):
                # Exclude rank-style role 'Kill Team Champion'
                if n.lower() == "kill team champion" or re.search(
                    r"(?i)kill\s*team\s*champion", n
                ):
                    continue
                killteam_roles.append(r)
    except Exception:
        killteam_roles = []

    # Build roster per Kill Team for the selected company (intersection of members)
    company_members: List[discord.Member] = [m for m in getattr(company, "members", [])]
    company_ids: set[str] = {
        str(getattr(m, "id", "")) for m in company_members if getattr(m, "id", None)
    }

    # Guard: ensure there are records for this company in the 30-day window
    has_company_records = False
    try:
        for rec in recent_records:
            bros = [str(b) for b in (rec.get("brother_ids") or [])]
            if set(bros) & company_ids:
                has_company_records = True
                break
    except Exception:
        has_company_records = False
    if not has_company_records:
        await interaction.followup.send(
            f"No AARs recorded in the last {span_days} days for this company window.",
            ephemeral=True,
        )
        return

    teams: List[Tuple[str, List[discord.Member]]] = []
    for kt in killteam_roles:
        kt_members = [
            m
            for m in getattr(kt, "members", [])
            if str(getattr(m, "id", "")) in company_ids
        ]
        if kt_members:
            teams.append((getattr(kt, "name", "Kill Team"), kt_members))

    # Company Command synthetic team: Sergeant+ in company
    idx_sergeant = _role_index("Watch Sergeant")
    company_command_members: List[discord.Member] = []
    if idx_sergeant is not None:
        for m in company_members:
            # Use existing is_sergeant_or_higher, already role-alias aware
            if is_sergeant_or_higher(m):
                company_command_members.append(m)
    if company_command_members:
        teams.append(("Company Command", company_command_members))

    if not teams:
        await interaction.followup.send(
            "No Kill Teams or Company Command members found in the selected company.",
            ephemeral=True,
        )
        return

    # Prepare ANSI-styled report
    lines: List[str] = []
    lines.append("```ansi")
    lines.append(
        "\u001b[32m=============================================================================="
    )
    lines.append("  WATCH FORTRESS JERICHO // LIBRARIUS OPERATIONAL BRIEF")
    lines.append("  OPERATION-SCRIBE SERVITOR — COMPANY DOCTRINAL DOSSIER")
    lines.append(
        "=============================================================================="
    )
    lines.append(f"  {getattr(company, 'name', 'Unknown')}  |  Window: Last {span_days} Days")
    lines.append(
        "------------------------------------------------------------------------------"
    )

    # Build team membership map for aggregation
    team_member_ids: Dict[str, set[str]] = {
        name: {str(getattr(m, "id", "")) for m in members if getattr(m, "id", None)}
        for name, members in teams
    }

    # Company-wide aggregates
    company_env_counts: Counter[str] = Counter()
    company_doc_counts: Counter[str] = Counter()
    company_missions_seen: set[str] = set()

    # Per-team doctrine counts for divergence
    per_team_doc_counts: Dict[str, Counter[str]] = {
        name: Counter() for name, _ in teams
    }

    for rec in recent_records:
        bros: List[str] = [str(b) for b in (rec.get("brother_ids") or [])]
        if not bros:
            continue
        if not (set(bros) & company_ids):
            continue
        env_tags, doc_tags, canon_mission = _tags_for_record(rec)
        for t in env_tags:
            company_env_counts[_env_macro_for(t)] += 1
        for d in doc_tags:
            company_doc_counts[d] += 1
        if canon_mission:
            company_missions_seen.add(canon_mission)
        # Attribute doctrines to any team intersecting this record
        for name, mids in team_member_ids.items():
            if set(bros) & mids:
                for d in doc_tags:
                    per_team_doc_counts[name][d] += 1

    # Helper to abbreviate team label
    def _abbr_label(n: str) -> str:
        if not n:
            return "Team"
        lower = n.lower()
        if lower.startswith("kill team "):
            return "KT " + n[10:]
        return "KT " + n if not lower.startswith("company command") else n

    # Primary Operational Environment
    if company_env_counts:
        env_sorted = sorted(company_env_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        env_name, env_count = env_sorted[0]
        total_env = sum(company_env_counts.values())
        env_pct = (env_count / total_env) if total_env > 0 else 0.0
        lines.append(
            f"  Primary Operational Environment :: {env_name} (Count: {env_count} — {env_pct:.0%})"
        )
    else:
        lines.append("  Primary Operational Environment :: URBAN")

    # Dominant Doctrinal Pattern (with split detection)
    if company_doc_counts:
        doc_sorted = sorted(company_doc_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        top_doc, top_cnt = doc_sorted[0]
        if len(doc_sorted) >= 2:
            second_doc, second_cnt = doc_sorted[1]
        else:
            second_doc, second_cnt = None, 0
        if second_doc and second_cnt >= 0.8 * top_cnt:
            lines.append(
                f"  Dominant Doctrinal Pattern      :: {top_doc} / {second_doc} Split (Counts: {top_cnt}|{second_cnt})"
            )
        else:
            lines.append(
                f"  Dominant Doctrinal Pattern      :: {top_doc} (Count: {top_cnt})"
            )
    else:
        lines.append("  Dominant Doctrinal Pattern      :: —")

    # Experience Saturation from distinct missions
    comp_exposure = _operational_exposure_tier(len(company_missions_seen))
    lines.append(
        f"  Experience Saturation           :: {comp_exposure} (Distinct Missions: {len(company_missions_seen)})"
    )

    # Cohesion Trend from distinct doctrines
    comp_coherence = _doctrinal_coherence_tier(len(set(company_doc_counts.keys())))
    lines.append(
        f"  Cohesion Trend                  :: {comp_coherence} (Distinct Doctrines: {len(set(company_doc_counts.keys()))})"
    )

    # Doctrinal Divergence: single winner or multiple if tied on dominance count
    div_line = None
    try:
        top_company_doc = None
        if company_doc_counts:
            top_company_doc = max(company_doc_counts.items(), key=lambda kv: kv[1])[0]
        team_tops: List[Tuple[str, str, int]] = []  # (team, doc, count)
        for name, counts in per_team_doc_counts.items():
            if not counts:
                continue
            doc, cnt = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
            team_tops.append((name, doc, cnt))
        # Prefer those diverging from company top
        source = [t for t in team_tops if t[1] != top_company_doc] or team_tops
        if source:
            max_cnt = max(cnt for _n, _d, cnt in source)
            winners = [(n, d, cnt) for n, d, cnt in source if cnt == max_cnt]
            names = " / ".join(_abbr_label(n) for n, _d, _c in winners)
            # If winners share same doc, show it; else show Mixed
            docs = {d for _n, d, _c in winners}
            doc_lbl = next(iter(docs)) if len(docs) == 1 else "Mixed"
            div_line = f"  Doctrinal Divergence            :: {names} ({doc_lbl})"
    except Exception:
        div_line = None
    lines.append(div_line or "  Doctrinal Divergence             :: —")

    # High Command Notes (company-level doctrinal and environmental posture)
    try:
        high_command_roles = {
            "Watch Master",
            "Forgemaster",
            "Lord Executioner",
            "Void Warden",
            "Voidwarden",
            "Chief Apothecary",
            "High Chaplain",
        }
        hc_ids: set[str] = set()
        for m in getattr(guild, "members", []):
            names = _canonical_role_names(m)
            if any(r in names for r in high_command_roles):
                uid = str(getattr(m, "id", ""))
                if uid:
                    hc_ids.add(uid)

        hc_ops_count = 0
        for rec in recent_records:
            bros = [str(b) for b in (rec.get("brother_ids") or [])]
            if any(b in hc_ids for b in bros):
                hc_ops_count += 1

        company_env_counts: Counter[str] = Counter()
        company_doc_counts: Counter[str] = Counter()
        company_missions_seen: set[str] = set()
        for rec in recent_records:
            bros = [str(b) for b in (rec.get("brother_ids") or [])]
            if not (set(bros) & company_ids):
                continue
            env_tags, doc_tags, canon_mission = _tags_for_record(rec)
            for t in env_tags:
                company_env_counts[_env_macro_for(t)] += 1
            for d in doc_tags:
                company_doc_counts[d] += 1
            if canon_mission:
                company_missions_seen.add(canon_mission)

        dom_env: Optional[str] = None
        if company_env_counts:
            dom_env = max(
                company_env_counts.items(),
                key=lambda kv: (
                    kv[1],
                    -ENV_MACROS_ORDER.index(kv[0])
                    if kv[0] in ENV_MACROS_ORDER
                    else 999,
                ),
            )[0]
        dom_doc: Optional[str] = None
        if company_doc_counts:
            dom_doc = max(company_doc_counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
        comp_coherence = _doctrinal_coherence_tier(len(set(company_doc_counts.keys())))
        comp_exposure = _operational_exposure_tier(len(company_missions_seen))

        lines.append(
            "------------------------------------------------------------------------------"
        )
        lines.append("  High Command Notes:")
        if hc_ops_count <= 0:
            lines.append(
                "  + High Command recorded no deployments across the dossier window."
            )
            lines.append(
                "  + Librarius counsel remained in strategic oversight: archives curated, auguries maintained."
            )
        elif hc_ops_count <= 3 and comp_coherence in ("SPECIALIZED", "REFINED"):
            lines.append(
                "  + High Command deployments were limited; doctrine indicates specialized orientation."
            )
            lines.append(
                f"  + Counsel calibrated for precision: {dom_doc or 'specialist doctrine'} in {dom_env or 'key theatres'}."
            )
        elif comp_exposure in ("BROAD", "EXTENSIVE") and comp_coherence in (
            "EMERGENT",
            "STABLE",
        ):
            lines.append(
                "  + Company operations spanned varied theatres; doctrine held adaptive coherence."
            )
            lines.append(
                "  + Librarius endorses flexible rites and cross-theatre stratagem rehearsal."
            )
        elif hc_ops_count >= 6 and comp_coherence in ("SPECIALIZED", "REFINED"):
            lines.append(
                "  + Elevated High Command deployments under focused campaigns."
            )
            lines.append(
                f"  + Orientation sustained by {dom_doc or 'focused doctrine'} across {comp_exposure.lower()} exposure."
            )
        else:
            lines.append(
                "  + High Command activity remained within expected bounds for the window."
            )
            lines.append(
                "  + Librarius counsel aligns with observed operations and doctrinal posture."
            )
    except Exception:
        pass

    lines.append(
        "=============================================================================="
    )
    lines.append("\u001b[0m```")

    # Length safeguard: strip decorative banners if message too long
    def _is_decoration_line(s: str) -> bool:
        t = s.replace("\u001b[32m", "").strip()
        return (set(t) <= {"=", "-"}) and (len(t) >= 20)

    msg = "\n".join(lines)
    if len(msg) > 1900:
        compacted: List[str] = []
        for ln in lines:
            if ln.strip().startswith("```") or ln.strip() == "\u001b[0m```":
                compacted.append(ln)
                continue
            if _is_decoration_line(ln):
                continue
            compacted.append(ln)
        if compacted and compacted[0].strip().startswith("```"):
            compacted.insert(1, "\u001b[32mLibrarius Operational Brief (Compact)")
        msg = "\n".join(compacted)

    await interaction.followup.send(msg, ephemeral=True)


def classify_difficulty(difficulty: str | None):
    if not difficulty:
        return None

    lower = difficulty.lower()

    if "ruthless" in lower:
        return "ruthless_ops"
    if "lethal" in lower:
        return "lethal_ops"
    if "absolute" in lower:
        return "absolute_ops"
    if "normal-stratagem" in lower:
        return "normal_stratagem"
    if "hard-stratagem" in lower:
        return "hard_stratagem"
    if "normal-siege" in lower:
        return "normal_siege"
    if "hard-siege" in lower:
        return "hard_siege"
    return None


def compute_points_for_op(difficulty_class: str | None, waves: int | None):
    if not difficulty_class:
        return 0

    if difficulty_class == "ruthless_ops":
        return 2
    if difficulty_class == "lethal_ops":
        return 3
    if difficulty_class == "absolute_ops":
        return 4
    if difficulty_class == "normal_stratagem":
        return 2
    if difficulty_class == "hard_stratagem":
        return 5
    if difficulty_class == "normal_siege":
        if waves is None:
            return 0
        return 3 * (waves // 5)
    if difficulty_class == "hard_siege":
        if waves is None:
            return 0
        return 4 * (waves // 5)

    return 0


def compute_gene_seed_base_points_for_carrier(difficulty_class: str | None):
    if not difficulty_class:
        return 0
    if difficulty_class == "ruthless_ops" or difficulty_class == "normal_stratagem":
        return 2
    if difficulty_class == "lethal_ops":
        return 3
    if difficulty_class == "absolute_ops":
        return 4
    if difficulty_class == "hard_stratagem":
        return 5
    if difficulty_class in ("normal_siege", "hard_siege"):
        return 0
    return 0


def compute_armory_bonus_points(difficulty_class: str | None, armory_data: int | None):
    if not difficulty_class or armory_data is None:
        return 0

    if difficulty_class == "normal_siege" or difficulty_class == "lethal_ops":
        return armory_data * 1
    elif difficulty_class == "hard_siege" or difficulty_class == "absolute_ops":
        return armory_data * 2
    elif difficulty_class == "hard_stratagem":
        return armory_data * 3

    return 0


def is_aar_message(message: discord.Message):
    content = message.content
    # Treat presence of the start marker as sufficient; END marker optional
    return "++ MISSION REPORT ++" in content or "++MISSION REPORT++" in content


def get_user_ids_in_line(line: str, message: discord.Message):
    """Return list of user IDs whose mention appears in this line."""
    ids = []
    for user in message.mentions:
        patterns = (f"<@{user.id}>", f"<@!{user.id}>")
        if any(p in line for p in patterns):
            ids.append(str(user.id))
    return ids


def parse_aar(message: discord.Message):
    content = message.content
    aar_id = message.id
    lines = content.splitlines()

    mission = None
    difficulty = None
    # Deprecated fields removed from persistence
    armory_data = 0
    gene_seed_status = "unknown"
    gene_seed_carrier_id = None
    gene_seed_carried_name = None
    brothers_ids = []
    brother_names = []
    waves = 0
    # Siege per-brother waves participation (parsed from Team lines as '@Brother N')
    brother_waves: Dict[str, int] = {}
    # Initiation Trial mention flag (lightweight)
    initiation_trial = False

    brothers_start_idx = None

    for i, raw_line in enumerate(lines):
        line = raw_line.strip()
        lower = line.lower()

        if lower.startswith("mission:"):
            mission = line.split(":", 1)[1].strip()
            # Also detect Initiation Trial tokens on the mission line
            # Deprecated: no longer tracking initiation trial state here
        elif lower.startswith("difficulty:") or lower.startswith("threat:"):
            after_colon = line.split(":", 1)[1]
            for role in message.role_mentions:
                mention = f"<@&{role.id}>"
                after_colon = after_colon.replace(mention, role.name)
            difficulty = after_colon.strip()
            # No longer persisting difficulty_tags or black_laurels_active

        # Armory / Armoury Data in any order, any capitalization
        elif ("armory" in lower or "armoury" in lower) and "data" in lower:
            # e.g. "Armory Data: 3" or "Armory data: 3"
            parts = line.split(":", 1)
            try:
                armory_data = int(parts[1].strip()) if len(parts) > 1 else 0
            except ValueError:
                logger.debug(f"Failed to parse armory data from line: {line}")
                armory_data = 0

        # Gene-Seed / Geneseed: lost / carried by @Brother
        elif ("gene-seed" in lower) or ("geneseed" in lower):
            parts = line.split(":", 1)
            rest = parts[1].strip() if len(parts) > 1 else ""
            rest_lower = rest.lower()

            if "lost" in rest_lower:
                gene_seed_status = "lost"
            elif "carried" in rest_lower:
                gene_seed_status = "carried"

            ids_here = get_user_ids_in_line(raw_line, message)
            if ids_here:
                gene_seed_carrier_id = ids_here[0]
                # Copilot: also set gene_seed_carried_name to the Discord nickname of the carrier
                for user in message.mentions:
                    if str(user.id) == gene_seed_carrier_id:
                        try:
                            gene_seed_carried_name = user.nick
                        except AttributeError:
                            logger.debug(
                                f"Failed to get nickname for user ID {gene_seed_carrier_id}"
                            )
                # If a Brother is tagged here, treat as carried regardless of wording
                gene_seed_status = "carried"

        for role in message.role_mentions:
            if role.name == "Initiation Trial":
                initiation_trial = True

        # Watch Command marker sometimes present on trial templates (deprecated persistence)
        if "watch command" in lower:
            # Deprecated: ignore trial template markers for persistence
            pass

        elif lower.startswith("brothers") or lower.startswith("team"):
            # Brothers/Team can appear on the same line as the header; include this line
            brothers_start_idx = i

        elif lower.startswith("waves:") or lower.startswith("wave:"):
            # Legacy/global waves support (non-siege or old format)
            parts = line.split(":", 1)
            try:
                waves = int(parts[1].strip())
            except Exception:
                waves = None

    difficulty_class = classify_difficulty(difficulty)
    points_for_op = compute_points_for_op(difficulty_class, waves)
    gene_seed_base_points_for_carrier = 0
    if gene_seed_status == "carried":
        gene_seed_base_points_for_carrier = compute_gene_seed_base_points_for_carrier(
            difficulty_class
        )

    # Collect Brothers from the "Brothers:" line and subsequent lines until END OF REPORT
    if brothers_start_idx is not None:
        for raw_line in lines[brothers_start_idx:]:
            line = raw_line.strip()
            if "++ end of report ++" in line.lower():
                break
            if not line:
                continue

            ids_here = get_user_ids_in_line(raw_line, message)
            for uid in ids_here:
                if uid not in brothers_ids:
                    brothers_ids.append(uid)
                    # Copilot: also append brother names as represented in discord
                    for user in message.mentions:
                        if str(user.id) == uid:
                            try:
                                brother_names.append(user.nick)
                            except AttributeError:
                                logger.debug(
                                    f"Failed to get nickname for user/ID {user.name}\/{uid}"
                                )
                # Try to parse per-brother waves from the same line, expecting an integer
                try:
                    # Find last integer token in the line
                    tokens = [t for t in line.replace("/", " ").split()]
                    nums = [int(t) for t in tokens if t.isdigit()]
                    if nums:
                        brother_waves[uid] = nums[-1]
                except Exception:
                    pass

    # Always return a record, even if Brothers section is missing; validation will handle errors
    return {
        "aar_id": aar_id,
        "mission": mission,
        "difficulty": difficulty,
        "difficulty_class": difficulty_class,
        # deprecated: removed from persisted record
        "armory_data": armory_data,
        "armory_challenge_points": compute_armory_bonus_points(
            difficulty_class, armory_data
        ),
        "gene_seed_status": gene_seed_status,
        "gene_seed_carrier_id": gene_seed_carrier_id,
        "gene_seed_carried_name": gene_seed_carried_name,
        "gene_seed_base_points_for_carrier": gene_seed_base_points_for_carrier,
        "brother_ids": brothers_ids,
        "brother_names": brother_names,
        "brother_waves": brother_waves,
        "waves": waves,
        "points_for_op": points_for_op,
        "timestamp": message.created_at.isoformat(),
        "edited_at": message.edited_at.isoformat()
        if getattr(message, "edited_at", None)
        else None,
        "content_hash": hashlib.sha256((content or "").encode("utf-8")).hexdigest(),
        "initiation_trial": initiation_trial,
    }


def validate_aar(record: dict):
    """
    Validate a parsed AAR record.
            "initiation_trial_tag_in_mission": initiation_trial_tag_in_mission,
            "initiation_trial_line_present": initiation_trial_line_present,
    Returns a list of human-readable error messages.
    If the list is empty, the record is considered valid.
    """
    errors: list[str] = []

    mission = record.get("mission")
    difficulty = record.get("difficulty") or ""
    waves = record.get("waves")
    brother_waves = record.get("brother_waves") or {}
    armory_data = record.get("armory_data")
    brothers = record.get("brother_ids") or []
    gene_status = record.get("gene_seed_status")
    gene_carrier = record.get("gene_seed_carrier_id")

    # 1) Mission required (except Siege templates where Mission may be omitted)
    dlower = (record.get("difficulty") or "").lower()
    is_siege = ("normal-siege" in dlower) or ("hard-siege" in dlower)
    if not mission and not is_siege:
        errors.append("Mission is missing (line starting with 'Mission:').")
    elif mission:
        mstr = str(mission)
        # Reject any user or role mentions or trial-style tokens in Mission
        # if "<@&" in mstr or "<@" in mstr:
        #     errors.append("Mission must be plain text; no Discord mentions are allowed after 'Mission:'.")
        if "/" in mstr:
            errors.append(
                "Mission must not include trial-style progress tokens like 'n/m' or '-/m'."
            )

    # 2) Difficulty must be one of the known tags
    dlower = difficulty.lower()
    known_tags = [
        "ruthless",
        "lethal",
        "absolute",
        "normal-stratagem",
        "hard-stratagem",
        "normal-siege",
        "hard-siege",
    ]
    if not difficulty or not any(tag in dlower for tag in known_tags):
        errors.append(
            "Difficulty is missing or does not contain a known tag "
            "(@Ruthless, @Lethal, @Absolute, @Normal-Stratagem, "
            "@Hard-Stratagem, @Normal-Siege, @Hard-Siege)."
        )
    else:
        # Only allow Black Laurels on Difficulty when Absolute is present
        has_black_laurels = "black" in dlower and "laurel" in dlower
        has_absolute = "absolute" in dlower
        if has_black_laurels and not has_absolute:
            errors.append(
                "@Black_Laurels may only be present when @Absolute is selected on the Difficulty line."
            )

    # 3) Siege must have waves data. Accept either global 'Waves:' or per-brother waves parsed from Team lines.
    if "normal-siege" in dlower or "hard-siege" in dlower:
        global_ok = False
        if waves is not None:
            try:
                int(waves)
                global_ok = True
            except (TypeError, ValueError):
                errors.append("Waves value could not be parsed as an integer.")
        per_bro_ok = any(isinstance(v, int) for v in brother_waves.values())
        if not (global_ok or per_bro_ok):
            errors.append(
                "Siege requires waves data: provide 'Waves:' or per-brother counts after mentions."
            )

    # 4) Armory/Armoury Data required and numeric
    if armory_data is None:
        errors.append("Armory/Armoury Data line is missing.")
    else:
        try:
            int(armory_data)
        except ValueError:
            errors.append("Armory/Armoury Data must be an integer (e.g. 3).")

    # 5) At least two Brothers
    if len(brothers) < 2:
        errors.append(
            "At least two Brothers must be listed under the 'Brothers:' section."
        )

    # 6) Initiation Trial placement rules
    if record.get("initiation_trial_active"):
        if record.get("initiation_trial_tag_in_mission"):
            errors.append(
                "Initiation Trial tag must be on its own line (e.g., '@Initiation Trial: n/m'), not inside 'Mission:'."
            )
        if not record.get("initiation_trial_line_present"):
            errors.append(
                "Provide a dedicated '@Initiation Trial: n/m' line; do not rely on Mission text alone."
            )
        if not record.get("initiation_trial_watch_command"):
            errors.append(
                "Trial template requires '@Watch Command' marker. Please include it."
            )

    # 7) Gene-seed logic
    allowed_statuses = {"lost", "carried", "unknown"}
    if gene_status not in allowed_statuses:
        errors.append(
            "Gene-Seed status must be 'lost', 'carried', or omitted "
            "(which becomes 'unknown')."
        )

    if gene_status == "carried":
        if gene_carrier is None:
            errors.append("Gene-Seed is 'carried' but no carrier is mentioned.")
        elif gene_carrier not in brothers:
            errors.append("Gene-Seed carrier must also be listed under 'Brothers:'.")

    return errors


def load_aar_data(filename: str):
    try:
        with open(filename, "r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return {}
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        # Handle empty or malformed JSON file gracefully
        return {}


def _load_json_dict(path: str):
    try:
        with open(path, "r") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}


def _save_json_dict(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def _load_json_list(path: str):
    try:
        with open(path, "r") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        return []


def _save_json_list(path: str, data: list):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def log_aar_errors(aar_id: int, errors: list[str]):
    data = _load_json_dict(AAR_ERRORS_PATH)
    data[str(aar_id)] = {"errors": errors}
    _save_json_dict(AAR_ERRORS_PATH, data)


def _author_info_from_message(msg: discord.Message):
    author = msg.author
    info = {
        "id": str(getattr(author, "id", "")),
        "username": getattr(author, "name", None) or getattr(author, "username", None),
        "nickname": getattr(author, "nick", None),
    }
    # Some guild member objects expose display_name for nickname
    try:
        if hasattr(author, "display_name") and info["nickname"] is None:
            info["nickname"] = author.display_name
    except Exception:
        pass
    return info


def log_aar_error_with_meta(aar_id: int, errors: list[str], msg: discord.Message):
    data = _load_json_dict(AAR_ERRORS_PATH)
    entry = {
        "errors": errors,
        "author": _author_info_from_message(msg),
    }
    data[str(aar_id)] = entry
    _save_json_dict(AAR_ERRORS_PATH, data)


def summarize_error_authors():
    """Return a list of author summaries from the error log.
    Each entry: {"id": str, "username": str|None, "nickname": str|None, "count": int}
    """
    data = _load_json_dict(AAR_ERRORS_PATH)
    by_author: dict[str, dict] = {}
    for _aar_id, entry in data.items():
        author = entry.get("author", {})
        aid = str(author.get("id", ""))
        if not aid:
            # Bucket unknown authors under empty id
            aid = ""
        if aid not in by_author:
            by_author[aid] = {
                "id": aid,
                "username": author.get("username"),
                "nickname": author.get("nickname"),
                "count": 0,
            }
        by_author[aid]["count"] += 1
        # Prefer latest known nickname/username if missing
        if not by_author[aid]["nickname"] and author.get("nickname"):
            by_author[aid]["nickname"] = author.get("nickname")
        if not by_author[aid]["username"] and author.get("username"):
            by_author[aid]["username"] = author.get("username")

    # Sort by count desc, then nickname/username
    summaries = list(by_author.values())
    summaries.sort(
        key=lambda x: (-x["count"], (x["nickname"] or x["username"] or "").lower())
    )
    return summaries


async def _set_aar_reaction(msg: discord.Message, status: str):
    """Set a single reaction on an AAR message based on status.
    status: 'ok' -> ✅, 'error' -> 🚫
    Ensures only one of these two reactions remains (no stacking).
    """
    ok_emoji = "✅"
    err_emoji = "🚫"
    try:
        # Remove previous bot-added status reactions to avoid stacking
        for reaction in msg.reactions:
            if str(reaction.emoji) in (ok_emoji, err_emoji):
                async for user in reaction.users():
                    if user == msg.guild.me:
                        await reaction.remove(user)
        # Add the desired reaction
        if status == "ok":
            await msg.add_reaction(ok_emoji)
        elif status == "error":
            await msg.add_reaction(err_emoji)
    except Exception as e:
        logger.debug(f"Failed to set reaction on message {msg.id}: {e}")


def load_processed_ids():
    ids = _load_json_list(PROCESSED_IDS_PATH)
    return set(str(x) for x in ids)


def add_processed_id(aar_id: int):
    ids = _load_json_list(PROCESSED_IDS_PATH)
    sid = str(aar_id)
    if sid not in ids:
        ids.append(sid)
        _save_json_list(PROCESSED_IDS_PATH, ids)


def save_aar_record(record: dict):
    filename = AAR_RECORDS_PATH
    data = load_aar_data(filename)

    key = str(record["aar_id"])
    data[key] = record

    # Atomic write via tmp+replace
    _save_json_dict(filename, data)

    # Mark as processed after successful save
    add_processed_id(record["aar_id"])

    # print(f"Saved AAR {record['aar_id']} to {filename}.")


def has_been_processed(aar_id: int):
    processed = load_processed_ids()
    return str(aar_id) in processed


def compute_stats_for_user(user_id: str):
    data = load_aar_data(AAR_RECORDS_PATH)

    ops = 0
    aar_points = 0
    armory_raw = 0
    armory_points = 0
    gene_carries = 0
    gene_seed_points = 0
    waves_participated = 0

    for record in data.values():
        brother_ids = record.get("brother_ids", [])
        if user_id in brother_ids:
            ops += 1
            # For Siege difficulties, compute AAR points per-brother using their waves
            difficulty_class = record.get("difficulty_class")
            if difficulty_class in ("normal_siege", "hard_siege"):
                bw = record.get("brother_waves") or {}
                try:
                    my_waves = int(bw.get(user_id, 0) or 0)
                except Exception:
                    my_waves = 0
                # If per-brother waves not present, fall back to legacy global waves
                if my_waves <= 0:
                    try:
                        my_waves = int(record.get("waves") or 0)
                    except Exception:
                        my_waves = 0
                # Apply siege points and tally waves participated
                if difficulty_class == "normal_siege":
                    aar_points += 3 * (my_waves // 5)
                else:
                    aar_points += 4 * (my_waves // 5)
                waves_participated += my_waves
            else:
                aar_points += record.get("points_for_op", 0)
            armory_data = record.get("armory_data")
            try:
                armory_raw += int(armory_data) if armory_data is not None else 0
            except ValueError:
                armory_raw += 0
            armory_points += record.get("armory_challenge_points", 0)

        # Treat as carried if status is 'carried' OR a carrier is named and status is not 'lost'
        status = (record.get("gene_seed_status") or "").lower()
        gene_carrier = record.get("gene_seed_carrier_id")
        effective_carried = status == "carried" or (
            gene_carrier is not None and status != "lost"
        )

        if effective_carried:
            if gene_carrier == user_id:
                gene_carries += 1
                gene_seed_points += record.get("gene_seed_base_points_for_carrier", 0)
            elif user_id in brother_ids:
                gene_seed_points += 1  # assist

    return {
        "ops": ops,
        "aar_points": aar_points,
        "armory_raw": armory_raw,
        "armory_points": armory_points,
        "gene_carries": gene_carries,
        "gene_seed_points": gene_seed_points,
        "waves_participated": waves_participated,
    }


def compute_stats_for_user_in_records(user_id: str, records: List[dict]):
    ops = 0
    aar_points = 0
    armory_raw = 0
    armory_points = 0
    gene_carries = 0
    gene_seed_points = 0
    waves_participated = 0

    for record in records:
        brother_ids = record.get("brother_ids", [])
        if user_id in brother_ids:
            ops += 1
            difficulty_class = record.get("difficulty_class")
            if difficulty_class in ("normal_siege", "hard_siege"):
                bw = record.get("brother_waves") or {}
                try:
                    my_waves = int(bw.get(user_id, 0) or 0)
                except Exception:
                    my_waves = 0
                if my_waves <= 0:
                    try:
                        my_waves = int(record.get("waves") or 0)
                    except Exception:
                        my_waves = 0
                if difficulty_class == "normal_siege":
                    aar_points += 3 * (my_waves // 5)
                else:
                    aar_points += 4 * (my_waves // 5)
                waves_participated += my_waves
            else:
                aar_points += record.get("points_for_op", 0)
            armory_data = record.get("armory_data")
            try:
                armory_raw += int(armory_data) if armory_data is not None else 0
            except ValueError:
                armory_raw += 0
            armory_points += record.get("armory_challenge_points", 0)

        status = (record.get("gene_seed_status") or "").lower()
        gene_carrier = record.get("gene_seed_carrier_id")
        effective_carried = status == "carried" or (
            gene_carrier is not None and status != "lost"
        )

        if effective_carried:
            if gene_carrier == user_id:
                gene_carries += 1
                gene_seed_points += record.get("gene_seed_base_points_for_carrier", 0)
            elif user_id in brother_ids:
                gene_seed_points += 1

    return {
        "ops": ops,
        "aar_points": aar_points,
        "armory_raw": armory_raw,
        "armory_points": armory_points,
        "gene_carries": gene_carries,
        "gene_seed_points": gene_seed_points,
        "waves_participated": waves_participated,
    }


# ===== Combat Bonds helpers =====
def _get_recent_missions(limit: int = 100):
    """Return the most recent missions (AAR records) sorted by timestamp desc."""
    data = load_aar_data(AAR_RECORDS_PATH)
    records = list(data.values())

    def _parse_ts(r: dict):
        ts = r.get("timestamp")
        try:
            return datetime.fromisoformat(ts).timestamp() if ts else 0.0
        except Exception:
            return 0.0

    records.sort(key=_parse_ts, reverse=True)
    return records[:limit]


def _parse_iso8601_to_utc(ts: Optional[str]):
    """Parse an ISO8601 timestamp into an aware UTC datetime.
    Accepts 'Z' or '+00:00' suffixes; treats naive timestamps as UTC.
    Returns None on failure.
    """
    if not ts:
        return None
    try:
        s = ts.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _get_missions_last_days(days: int):
    """Return missions with timestamp within the last N days (UTC), newest first."""
    try:
        span = max(1, int(days))
    except Exception:
        span = 7
    data = load_aar_data(AAR_RECORDS_PATH)
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=span)
    stamped: List[Tuple[datetime, dict]] = []
    for rec in data.values():
        dt = _parse_iso8601_to_utc(rec.get("timestamp"))
        if dt and dt >= cutoff:
            stamped.append((dt, rec))
    stamped.sort(key=lambda t: t[0], reverse=True)
    return [r for _dt, r in stamped]


def _build_pair_counts(missions):
    """Count how often each pair of brothers appears together in provided missions.
    Keys are sorted tuples of brother IDs (str, str).
    """
    pair_counts: Dict[Tuple[str, str], int] = {}
    for rec in missions:
        bros: List[str] = [str(b) for b in (rec.get("brother_ids") or [])]
        # unique per mission to avoid duplicate counting same brother twice
        unique_bros = sorted(set(bros))
        for a, b in itertools.combinations(unique_bros, 2):
            key = (a, b) if a < b else (b, a)
            pair_counts[key] = pair_counts.get(key, 0) + 1
    return pair_counts


def _build_triple_bonds(pair_counts: Dict[Tuple[str, str], int], brothers: List[str]):
    """Create 3-brother bonds and score them as sum of the three pairwise counts.
    Skip any triple where a pair never appeared together (pair count == 0).
    Returns list of ((id1,id2,id3), score) sorted by score desc.
    """
    triples: List[Tuple[Tuple[str, str, str], int]] = []
    uniq_bros = sorted(set(brothers))
    for x, y, z in itertools.combinations(uniq_bros, 3):
        pairs = [tuple(sorted((x, y))), tuple(sorted((x, z))), tuple(sorted((y, z)))]
        # all pairs must exist at least once
        if any(pair_counts.get(p, 0) <= 0 for p in pairs):
            continue
        score = sum(pair_counts.get(p, 0) for p in pairs)
        triples.append(((x, y, z), score))
    triples.sort(key=lambda t: t[1], reverse=True)
    return triples


def _select_top_global_bonds(
    triples: List[Tuple[Tuple[str, str, str], int]], top_n: int = 3
):
    """Select top-N global bonds ensuring no brother repeats across groups."""
    selected: List[Tuple[Tuple[str, str, str], int]] = []
    used: set[str] = set()
    for triple, score in triples:
        if any(b in used for b in triple):
            continue
        selected.append((triple, score))
        used.update(triple)
        if len(selected) >= top_n:
            break
    return selected


def _select_personal_bonds(
    triples: List[Tuple[Tuple[str, str, str], int]], target_id: str, max_n: int = 3
):
    """Return up to max_n bonds that include the target brother."""
    results = [t for t in triples if target_id in t[0]]
    return results[:max_n]


def _bond_tier(score: int):
    """Map bond score to a tier label."""
    if score <= 6:
        return "FRAGILE"
    if score <= 12:
        return "FORMING"
    if score <= 21:
        return "RELIABLE"
    if score <= 33:
        return "STALWART"
    return "INDOMITABLE"


def _render_veneration_line(active_tier: str):
    """Return cogitator-style veneration line with only the active tier bracketed.
    Example: "Veneration: FRAGILE  FORMING  RELIABLE  [ STALWART ]  INDOMITABLE"
    """
    tiers = ["FRAGILE", "FORMING", "RELIABLE", "STALWART", "INDOMITABLE"]
    parts = [f"[ {t} ]" if t == active_tier else t for t in tiers]
    return "Veneration: " + "  ".join(parts)


async def _resolve_home_chapters(
    guild: Optional[discord.Guild], user_ids: List[str], limit: int = 500
):
    """Resolve home chapters for given users by scanning the '#◈⋅⋅record-of-blood⋅⋅◈' channel.
    Logic: find a message that mentions the user; detect the chapter within that same message's content.
    The chapter is detected by matching any of the known `home_chapters` names within the message.
    Returns mapping of user_id -> chapter string. Missing entries map to 'REDACTED'.
    """
    home_chapters = [
        "Black Templars",
        "Blood Angels",
        "Blood Ravens",
        "Cowled Wardens",
        "Crimson Fists",
        "Dark Angels",
        "Dark Krakens",
        "Death Spectres",
        "Flesh Eaters",
        "Flesh Tearers",
        "Hawk Lords",
        "Imperial Fists",
        "Iron Hands",
        "Lamenters",
        "Mentors",
        "Minotaurs",
        "Raven Guard",
        "Red Templars",
        "Salamanders",
        "Sons of Medusa",
        "Space Wolves",
        "Storm Giants",
        "Ultramarines",
        "White Scars",
        "Black Shields",
    ]
    chapters: Dict[str, str] = {}
    if not guild:
        return chapters
    channel = discord.utils.get(guild.channels, name="◈⋅⋅record-of-blood⋅⋅◈")
    if not channel:
        return chapters
    target_set = set(user_ids)
    # Oldest first so 'prev_msg' is the message above (older) when we hit a mention line
    async for msg in channel.history(limit=limit, oldest_first=True):
        # Collect mentioned IDs in this message
        mentioned = {str(u.id) for u in msg.mentions}
        intersect = mentioned & target_set
        if intersect:
            for uid in intersect:
                if uid not in chapters:
                    chapter = "REDACTED"
                    # Adjusted: find chapter within the SAME message content
                    if msg.content:
                        text = msg.content.strip()
                        lower_text = text.lower()
                        match = next(
                            (hc for hc in home_chapters if hc.lower() in lower_text),
                            None,
                        )
                        if match:
                            chapter = match
                    chapters[uid] = chapter
        if len(chapters) == len(target_set):
            break
    return chapters


def _format_bonds_for_discord(
    bonds: List[Tuple[Tuple[str, str, str], int]],
    guild: Optional[discord.Guild] = None,
    window_span: int = 100,
    chapters: Optional[Dict[str, str]] = None,
):
    """Produce styled Combat Bonds output matching the requested layout."""
    if not bonds:
        return "No qualifying Combat Bonds found in the current window."
    lines: List[str] = []
    lines.append("```ansi")
    lines.append(
        "\u001b[32m=============================================================================="
    )
    lines.append("  WATCH FORTRESS JERICHO // COMBAT BONDS COGITATOR")
    lines.append("  SUB-ROUTINE: TRIADIC BATTLE-LITANY INDEX")
    lines.append(
        "=============================================================================="
    )
    lines.append(f"  Auspex Window: Last {window_span} sanctioned engagement(s)")
    rank = 1
    ordinal_labels = {
        1: "PRIMARY",
        2: "SECONDARY",
        3: "TERTIARY",
        4: "QUATERNARY",
        5: "QUINARY",
    }
    for triple, score in bonds:
        tier = _bond_tier(score)
        a, b, c = triple

        # Resolve members and labels (rank + name + chapter)
        def _member_label(uid: str):
            member = None
            name = "REDACTED"
            if guild:
                try:
                    member = guild.get_member(int(uid))
                except Exception:
                    member = None
            if member:
                name = member.nick or member.display_name

            chap = (chapters or {}).get(uid)
            chap_str = chap if chap else "REDACTED"
            return f"{name} [{chap_str}]"

        # Optional codename derived from majority chapter
        tri_chapters = [(chapters or {}).get(x) for x in (a, b, c)]
        tri_chapters = [ch for ch in tri_chapters if ch]
        title = ordinal_labels.get(rank, "BOND")

        lines.append(f"    ++ {title} BOND ++")
        lines.append(f"    {_member_label(a)}")
        lines.append(f"    {_member_label(b)}")
        lines.append(f"    {_member_label(c)}")
        lines.append(f"    {_render_veneration_line(tier)}")
        lines.append("")
        rank += 1
    lines.append(
        "=============================================================================="
    )
    lines.append("  Machine-Spirit Addendum:")
    lines.append("  These Combat Bonds are logged for future deployment rites")
    lines.append("  and may be invoked by decree of the Kill Team Sergeants or ")
    lines.append("  any of their commanding officers.")
    lines.append(
        "=============================================================================="
    )
    lines.append("\u001b[0m```")
    return "\n".join(lines)


@bot.tree.command(
    name="apothecary_brief",
    description="Summarize availability per Kill Team in a Company (window configurable).",
)
@app_commands.describe(
    company="The Company role to analyze (e.g., '@Watch Company Primus').",
    days="Optional: number of days to include (default 7).",
)
async def apothecary_brief(
    interaction: discord.Interaction,
    company: discord.Role,
    days: Optional[int] = None,
):
    # Permissions: restricted to Watch Command and allowed channels
    if not (is_watch_command(interaction.user) and is_allowed_channel(interaction)):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    await interaction.response.defer(thinking=False, ephemeral=True)

    guild = interaction.guild
    if not guild:
        await interaction.followup.send("Guild context unavailable.", ephemeral=True)
        return

    # Identify Kill Team roles within the fortress
    killteam_roles: List[discord.Role] = []
    try:
        for role in guild.roles:
            rn = getattr(role, "name", "") or ""
            rl = rn.lower()
            if ("kill" in rl) and ("team" in rl):
                # Exclude rank-style roles like 'Kill Team Champion'
                if "champion" in rl:
                    continue
                killteam_roles.append(role)
    except Exception:
        killteam_roles = []

    # Build roster per Kill Team for the selected company (intersection of members)
    company_members: List[discord.Member] = [m for m in getattr(company, "members", [])]
    company_ids: set[str] = {
        str(getattr(m, "id", "")) for m in company_members if getattr(m, "id", None)
    }

    teams: List[Tuple[str, List[discord.Member]]] = []
    for kt in killteam_roles:
        kt_members = [
            m
            for m in getattr(kt, "members", [])
            if str(getattr(m, "id", "")) in company_ids
        ]
        if kt_members:
            teams.append(
                (_extract_killteam_name(getattr(kt, "name", "Unknown")), kt_members)
            )

    # Company Command: mirror killteam_brief logic (Captain through above Sergeant, in 'Watch Company Primus')
    try:
        idx_sergeant = _role_index("Watch Sergeant")
        idx_captain = _role_index("Watch Captain")
    except Exception:
        idx_sergeant = None
        idx_captain = None

    company_command_members: List[discord.Member] = []
    try:
        base_members = list(getattr(company, "members", []))
        for m in base_members:
            roles = getattr(m, "roles", [])
            has_primus = False
            for r in roles:
                rn = (getattr(r, "name", "") or "").lower()
                if ("primus" in rn) and ("company" in rn):
                    has_primus = True
                    break
            highest_idx = get_highest_rank_index(m)
            if (
                has_primus
                and idx_sergeant is not None
                and idx_captain is not None
                and highest_idx is not None
                and (idx_captain <= highest_idx < idx_sergeant)
            ):
                company_command_members.append(m)
    except Exception:
        company_command_members = []

    # Compute last-N-day activity map from AAR records
    span_days = days if (isinstance(days, int) and days > 0) else 7
    recent_records = _get_missions_last_days(span_days)

    # Guard: ensure there are records for this company in the 30-day window
    has_company_records = False
    try:
        for rec in recent_records:
            bros = [str(b) for b in (rec.get("brother_ids") or [])]
            if set(bros) & company_ids:
                has_company_records = True
                break
    except Exception:
        has_company_records = False
    if not has_company_records:
        await interaction.followup.send(
            f"No AARs recorded in the last {span_days} days for this company window.",
            ephemeral=True,
        )
        return

    active_map: Dict[str, bool] = {}
    for rec in recent_records:
        try:
            for uid in rec.get("brother_ids") or []:
                sid = str(uid)
                if sid:
                    active_map[sid] = True
        except Exception:
            continue

    def _absence_stats(members: List[discord.Member]):
        measures: List[int] = []  # 1 = absent, 0 = active
        active_cnt = 0
        for m in members:
            sid = str(getattr(m, "id", ""))
            is_active = bool(active_map.get(sid, False))
            measures.append(0 if is_active else 1)
            if is_active:
                active_cnt += 1
        n = len(measures)
        avg = (sum(measures) / n) if n > 0 else 0.0
        med = statistics.median(measures) if measures else 0.0
        try:
            sd = statistics.pstdev(measures) if n > 1 else 0.0
        except Exception:
            sd = 0.0
        return {
            "count": n,
            "active": active_cnt,
            "absent": n - active_cnt,
            "avg": avg,
            "median": med,
            "stdev": sd,
        }

    # Prepare ANSI-styled report
    lines: List[str] = []
    lines.append("```ansi")
    lines.append(
        "\u001b[32m=============================================================================="
    )
    lines.append("  WATCH FORTRESS JERICHO // APOTHECARION NODE")
    lines.append(f"  OPERATION-SCRIBE SERVITOR — BIOLOGICAL READINESS LEDGER ({span_days} DAYS)")
    lines.append(
        "=============================================================================="
    )
    lines.append(f"  {getattr(company, 'name', 'Unknown')}  |  Window: Last {span_days} Days")
    lines.append(
        "------------------------------------------------------------------------------"
    )

    # Helpers to render tiered lines with a single active tier bracketed
    def _render_band(label: str, tiers: List[str], active_tier: str) -> str:
        parts = [f"[ {t} ]" if t == active_tier else t for t in tiers]
        return f"    {label}:        " + "  ".join(parts)

    # Compact single-line renderer for per-team metrics
    def _render_compact_row(label: str, s: Dict[str, float]) -> str:
        return (
            f"  {label}: "
            f"R[{_select_readiness_tier(s)}] "
            f"C[{_select_care_tier(s)}] "
            f"S[{_select_stability_tier(s)}]"
        )

    def _select_readiness_tier(s: Dict[str, float]) -> str:
        n = int(s.get("count", 0) or 0)
        active = int(s.get("active", 0) or 0)
        avg_absent = float(s.get("avg", 0.0) or 0.0)
        med = float(s.get("median", 0.0) or 0.0)
        if n <= 0:
            return "CRITICAL"
        p_active = (active / n) if n > 0 else 0.0
        # Base tier from active proportion (best to worst)
        if p_active >= 0.95:
            tier = "FULL"
        elif p_active >= 0.85:
            tier = "NEAR-TOTAL"
        elif p_active >= 0.65:
            tier = "HIGH"
        elif p_active >= 0.40:
            tier = "DEGRADED"
        else:
            tier = "CRITICAL"
        # Adjust with median: if majority absent (median ~1) and avg high, nudge worse; if majority ready and avg low, nudge better
        ordering = ["CRITICAL", "DEGRADED", "HIGH", "NEAR-TOTAL", "FULL"]
        idx = ordering.index(tier)
        try:
            if (med >= 1.0 and avg_absent >= 0.5) and idx > 0:
                idx -= 1
            elif (med <= 0.0 and avg_absent <= 0.25) and idx < len(ordering) - 1:
                idx += 1
        except Exception:
            pass
        return ordering[idx]

    def _select_care_tier(s: Dict[str, float]) -> str:
        avg_absent = float(s.get("avg", 0.0) or 0.0)
        # Care load increases with absent incidence
        if avg_absent <= 0.05:
            return "CLEAR"
        if avg_absent <= 0.15:
            return "NEGLIGIBLE"
        if avg_absent <= 0.35:
            return "LOW"
        if avg_absent <= 0.60:
            return "ELEVATED"
        return "CRITICAL"

    def _select_stability_tier(s: Dict[str, float]) -> str:
        sd = float(s.get("stdev", 0.0) or 0.0)
        # Lower dispersion indicates steadier participation across roster
        if sd <= 0.10:
            return "UNIFORM"
        if sd <= 0.18:
            return "CONSISTENT"
        if sd <= 0.26:
            return "STABLE"
        if sd <= 0.34:
            return "VARIABLE"
        return "FRACTURED"

    # Company Command stats (used in summary line)
    stats_cmd = _absence_stats(company_command_members)

    # Build per-team stats for summary computations
    team_stats: List[Tuple[str, Dict[str, float]]] = []
    for name, members in sorted(teams, key=lambda t: t[0].lower()):
        team_stats.append((name, _absence_stats(members)))

    # Overall Biological Readiness across all units (Company Command + Kill Teams)
    try:
        by_id: Dict[str, discord.Member] = {}
        # include all team members
        for name, members in teams:
            for m in members:
                sid = str(getattr(m, "id", ""))
                if sid:
                    by_id[sid] = m
        # include company command
        for m in company_command_members:
            sid = str(getattr(m, "id", ""))
            if sid:
                by_id[sid] = m
        all_units: List[discord.Member] = list(by_id.values())
        overall_stats = _absence_stats(all_units)
        overall_ready = _select_readiness_tier(overall_stats)
    except Exception:
        overall_ready = "CRITICAL"

    # Care Load Concentration: team with highest average absence
    care_team_label = "N/A"
    care_team_name: Optional[str] = None
    care_stats: Optional[Dict[str, float]] = None
    try:
        if team_stats:
            # choose by avg desc, tie-break by stdev desc then name asc
            def _care_key(item):
                name, s = item
                return (
                    float(s.get("avg", 0.0) or 0.0),
                    float(s.get("stdev", 0.0) or 0.0),
                    -ord(name[0].lower()) if name else 0,
                )

            worst = max(
                team_stats,
                key=lambda it: (
                    float(it[1].get("avg", 0.0) or 0.0),
                    float(it[1].get("stdev", 0.0) or 0.0),
                    it[0].lower(),
                ),
            )
            w_name, w_stats = worst
            care_team_name = w_name
            care_stats = w_stats
            care_team_label = f"KT {w_name} ({_select_care_tier(w_stats)})"
    except Exception:
        pass

    # Stability Outlier: team with highest dispersion (stdev)
    stab_team_label = "N/A"
    stab_team_name: Optional[str] = None
    stab_stats: Optional[Dict[str, float]] = None
    try:
        if team_stats:
            worst = max(
                team_stats, key=lambda it: float(it[1].get("stdev", 0.0) or 0.0)
            )
            w_name, w_stats = worst
            stab_team_name = w_name
            stab_stats = w_stats
            stab_team_label = f"KT {w_name} ({_select_stability_tier(w_stats)})"
    except Exception:
        pass

    # Most Stable Formation: single winner or multiple if tie at lowest dispersion
    stable_names_fmt = "N/A"
    stable_tier = "UNDETERMINED"
    stable_best_stats: Optional[Dict[str, float]] = None
    try:
        if team_stats:
            sd_pairs: List[Tuple[str, float, Dict[str, float]]] = [
                (n, float(s.get("stdev", 0.0) or 0.0), s) for n, s in team_stats
            ]
            if sd_pairs:
                min_sd = min(sd for _n, sd, _s in sd_pairs)
                epsilon = 1e-9
                winners: List[Tuple[str, Dict[str, float]]] = [
                    (n, s) for n, sd, s in sd_pairs if abs(sd - min_sd) <= epsilon
                ]
                names = [f"KT {n}" for n, _s in winners]
                stable_names_fmt = " / ".join(names) if names else "N/A"
                if winners:
                    stable_best_stats = winners[0][1]
                    stable_tier = _select_stability_tier(stable_best_stats)
    except Exception:
        pass

    # Company Command Status: readiness and stability
    cc_ready = _select_readiness_tier(stats_cmd)
    cc_stab = _select_stability_tier(stats_cmd)

    # Apothecarion-owned Gene-Seed Preservation metric across recent window
    try:
        team_member_ids: Dict[str, set[str]] = {
            name: {str(getattr(m, "id", "")) for m in members if getattr(m, "id", None)}
            for name, members in teams
        }
        per_team_gene: List[Tuple[str, float]] = []
        for name, mids in team_member_ids.items():
            vals: List[float] = []
            for rec in recent_records:
                bros = [str(b) for b in (rec.get("brother_ids") or [])]
                if not (set(bros) & mids):
                    continue
                gene = 0.0
                try:
                    if (rec.get("gene_seed_status") or "").lower() == "carried":
                        gene = float(rec.get("gene_seed_base_points_for_carrier", 0) or 0)
                except Exception:
                    gene = 0.0
                vals.append(gene)
            avg_gene = (sum(vals) / len(vals)) if vals else 0.0
            per_team_gene.append((name, avg_gene))
        best_gene_team = max(per_team_gene, key=lambda t: t[1]) if per_team_gene else None
    except Exception:
        best_gene_team = None

    # Summary section with contextual parentheses similar to techmarine_brief
    # Overall readiness: show active/total and percentage
    try:
        o_count = int(
            overall_stats.get("count", 0) if "overall_stats" in locals() else 0
        )
        o_active = int(
            overall_stats.get("active", 0) if "overall_stats" in locals() else 0
        )
        p_active = (o_active / o_count) if o_count > 0 else 0.0
        lines.append(
            f"  Overall Biological Readiness   :: {overall_ready} (Active: {o_active}/{o_count} — {p_active:.0%})"
        )
    except Exception:
        lines.append(f"  Overall Biological Readiness   :: {overall_ready} (All Units)")

    # Care concentration: show avg absent (and stdev for quick context)
    if care_stats is not None:
        c_avg = float(care_stats.get("avg", 0.0) or 0.0)
        c_sd = float(care_stats.get("stdev", 0.0) or 0.0)
        lines.append(
            f"  Care Load Concentration        :: {care_team_label} (Avg Absent: {c_avg:.2f}; SD: {c_sd:.2f})"
        )
    else:
        lines.append(f"  Care Load Concentration        :: {care_team_label}")

    # Stability outlier: show stdev
    if stab_stats is not None:
        s_sd = float(stab_stats.get("stdev", 0.0) or 0.0)
        lines.append(
            f"  Stability Outlier              :: {stab_team_label} (Stdev: {s_sd:.2f})"
        )
    else:
        lines.append(f"  Stability Outlier              :: {stab_team_label}")

    # Most stable formation: show stdev of most stable
    if stable_best_stats is not None:
        mb_sd = float(stable_best_stats.get("stdev", 0.0) or 0.0)
        lines.append(
            f"  Most Stable Formation          :: {stable_names_fmt} ({stable_tier}) (Stdev: {mb_sd:.2f})"
        )
    else:
        lines.append(
            f"  Most Stable Formation          :: {stable_names_fmt} ({cc_stab if stable_tier == 'UNDETERMINED' else stable_tier})"
        )

    # Company Command status: show active/total and stdev
    try:
        cc_count = int(stats_cmd.get("count", 0) or 0)
        cc_active = int(stats_cmd.get("active", 0) or 0)
        cc_sd = float(stats_cmd.get("stdev", 0.0) or 0.0)
        lines.append(
            f"  Company Command Status         :: {cc_ready} READINESS — {cc_stab} STABILITY (Active: {cc_active}/{cc_count}; SD: {cc_sd:.2f})"
        )
    except Exception:
        lines.append(
            f"  Company Command Status         :: {cc_ready} READINESS — {cc_stab} STABILITY"
        )

    # Gene-Seed Preservation summary (Apothecarion ownership)
    if best_gene_team is not None:
        try:
            name, avg_gene = best_gene_team
            label = f"KT {name}" if name != "Company Command" else name
            lines.append(
                f"  Gene-Seed Preservation         :: {label}  (Avg Gene: {avg_gene:.2f})"
            )
        except Exception:
            pass

    # High Command Notes (window posture using medical load and stability signals)
    try:
        high_command_roles = {
            "Watch Master",
            "Forgemaster",
            "Lord Executioner",
            "Void Warden",
            "Voidwarden",
            "Chief Apothecary",
            "High Chaplain",
        }
        hc_ids: set[str] = set()
        for m in getattr(guild, "members", []):
            names = _canonical_role_names(m)
            if any(r in names for r in high_command_roles):
                uid = str(getattr(m, "id", ""))
                if uid:
                    hc_ids.add(uid)

        hc_ops_count = 0
        for rec in recent_records:
            try:
                bros = [str(b) for b in (rec.get("brother_ids") or [])]
                if any(b in hc_ids for b in bros):
                    hc_ops_count += 1
            except Exception:
                continue

        # Aggregate care and stability signals across Company Command + Kill Teams
        care_levels: List[str] = []
        stab_levels: List[str] = []
        read_levels: List[str] = []
        care_levels.append(_select_care_tier(stats_cmd))
        stab_levels.append(_select_stability_tier(stats_cmd))
        read_levels.append(_select_readiness_tier(stats_cmd))
        for name, members in teams:
            s = _absence_stats(members)
            care_levels.append(_select_care_tier(s))
            stab_levels.append(_select_stability_tier(s))
            read_levels.append(_select_readiness_tier(s))

        def _ratio(xs: List[str], bad: set[str]) -> float:
            n = len(xs)
            if n <= 0:
                return 0.0
            return sum(1 for x in xs if x in bad) / float(n)

        care_ratio = _ratio(care_levels, {"ELEVATED", "CRITICAL"})
        stab_ratio = _ratio(stab_levels, {"VARIABLE", "FRACTURED"})
        read_ratio = _ratio(read_levels, {"DEGRADED", "CRITICAL"})

        lines.append(
            "------------------------------------------------------------------------------"
        )
        lines.append("  High Command Notes:")
        if hc_ops_count <= 0:
            lines.append(
                f"  + High Command recorded no deployments in the last {span_days} days."
            )
            lines.append(
                "  + Oversight posture maintained; Apothecarion focuses on steady readiness cycles."
            )
        elif hc_ops_count <= 2 and (care_ratio >= 0.4 or stab_ratio >= 0.4):
            lines.append(
                "  + High Command deployments remained limited while medical load and/or stability"
            )
            lines.append("    indicators trended adverse across the roster.")
            lines.append(
                "  + Cautionary posture: prioritize triage rites and cohesion drills prior to escalation."
            )
        elif hc_ops_count >= 5 and read_ratio >= 0.4:
            lines.append(
                "  + Elevated High Command deployments coincided with degraded readiness across teams."
            )
            lines.append(
                "  + Emergency posture justified: Apothecarion release criteria tightened; rotations shortened."
            )
        elif hc_ops_count >= 5 and stab_ratio < 0.3:
            lines.append(
                "  + High Command maintained a proactive operational posture with steady cohesion."
            )
            lines.append(
                "  + Apothecarion endorses forward deployment under current stability readings."
            )
        else:
            lines.append(
                "  + High Command deployments remained within expected bounds for the period."
            )
            lines.append(
                "  + Apothecarion advises routine cycles: recovery rites, gene-seed audits, cohesion maintenance."
            )
    except Exception:
        pass

    lines.append(
        "=============================================================================="
    )
    lines.append("\u001b[0m```")

    # Safeguard: if message is still too long, strip decorative lines
    def _is_decoration_line(s: str) -> bool:
        t = s.replace("\u001b[32m", "").strip()
        return (set(t) <= {"=", "-"}) and (len(t) >= 20)

    msg = "\n".join(lines)
    if len(msg) > 1900:
        compacted: List[str] = []
        for ln in lines:
            if ln.strip().startswith("```") or ln.strip() == "\u001b[0m```":
                compacted.append(ln)
                continue
            if _is_decoration_line(ln):
                continue
            compacted.append(ln)
        # Insert concise header after the opening code fence
        if compacted and compacted[0].strip().startswith("```"):
            compacted.insert(1, f"\u001b[32mApothecarion Readiness Brief ({span_days} Days)")
        msg = "\n".join(compacted)

    await interaction.followup.send(msg, ephemeral=True)


def _main():
    # Parse CLI args for debug flag (overrides config). Use parse_known to avoid discord.py argv issues.
    try:
        parser = argparse.ArgumentParser(add_help=True)
        parser.add_argument(
            "--debug",
            action="store_true",
            help="Disable startup/shutdown status broadcasts",
        )
        args, _unknown = parser.parse_known_args()
        # Merge: CLI overrides config
        debug_flag = bool(args.debug) or _is_truthy((CONFIG or {}).get("debug"))
        # Update global broadcast toggle
        global BROADCAST_STATUS
        BROADCAST_STATUS = not debug_flag
    except Exception as e:
        logger.debug(f"Failed to parse CLI args: {e}")
    try:
        token = os.getenv("DISCORD_TOKEN")
    except Exception as e:
        print(e)
    finally:
        token = (
            "REDACTED_DISCORD_TOKEN"
        )
    if not token:
        raise RuntimeError("DISCORD_TOKEN environment variable not set")
    bot.run(token)


async def _ingest_trophy_hall(guild: Optional[discord.Guild]):
    added = 0
    updated = 0
    if not guild:
        return added, updated
    channel = discord.utils.get(guild.channels, name="❖⋅trophy-hall⋅❖")
    if not channel:
        return added, updated
    index = _load_json_dict(TROPHY_HALL_INDEX_PATH)
    meta = index.get("_meta") or {}
    last_id_str = meta.get("last_ingested_message_id")
    try:
        last_id = int(last_id_str) if last_id_str else 0
    except Exception:
        last_id = 0
    tri_buf: List[discord.Message] = []
    async for msg in channel.history(limit=None, oldest_first=True):
        if last_id and msg.id <= last_id:
            continue
        tri_buf.append(msg)
        if len(tri_buf) >= 3:
            m1, m2, m3 = tri_buf[-3:]
            key = str(m3.id)
            content_hashes = {
                str(m1.id): hashlib.sha256(
                    (m1.content or "").encode("utf-8")
                ).hexdigest(),
                str(m2.id): hashlib.sha256(
                    (m2.content or "").encode("utf-8")
                ).hexdigest(),
                str(m3.id): hashlib.sha256(
                    (m3.content or "").encode("utf-8")
                ).hexdigest(),
            }
            edited_at = {
                str(m1.id): m1.edited_at.isoformat()
                if getattr(m1, "edited_at", None)
                else None,
                str(m2.id): m2.edited_at.isoformat()
                if getattr(m2, "edited_at", None)
                else None,
                str(m3.id): m3.edited_at.isoformat()
                if getattr(m3, "edited_at", None)
                else None,
            }
            completers = [str(u.id) for u in m3.mentions]
            challenge_title = (m1.content or "").strip()
            entry = {
                "challenge_title": challenge_title,
                "completer_user_ids": completers,
                "completion_message_id": str(m3.id),
                "message_ids": [str(m1.id), str(m2.id), str(m3.id)],
                "content_hashes": content_hashes,
                "edited_at": edited_at,
            }
            prev = index.get(key)
            if not prev:
                index[key] = entry
                added += 1
            else:
                prev_hashes = prev.get("content_hashes") or {}
                prev_completers = set(prev.get("completer_user_ids") or [])
                if prev_hashes != content_hashes or prev_completers != set(completers):
                    index[key] = entry
                    updated += 1
            meta["last_ingested_message_id"] = str(m3.id)
    keys = [k for k in index.keys() if k != "_meta"]
    try:
        recent_sorted = sorted([int(k) for k in keys])
    except Exception:
        recent_sorted = []
    recent_keys = [str(k) for k in recent_sorted[-50:]]
    for kid in recent_keys:
        e = index.get(kid) or {}
        mids = e.get("message_ids") or []
        changed = False
        hashes = e.get("content_hashes") or {}
        edits = e.get("edited_at") or {}
        new_hashes = dict(hashes)
        new_edits = dict(edits)
        for mid in mids:
            try:
                m = await channel.fetch_message(int(mid))
            except Exception:
                continue
            h = hashlib.sha256((m.content or "").encode("utf-8")).hexdigest()
            ea = m.edited_at.isoformat() if getattr(m, "edited_at", None) else None
            if new_hashes.get(mid) != h or new_edits.get(mid) != ea:
                new_hashes[mid] = h
                new_edits[mid] = ea
                changed = True
        if changed:
            e["content_hashes"] = new_hashes
            e["edited_at"] = new_edits
            index[kid] = e
            updated += 1
    index["_meta"] = meta
    _save_json_dict(TROPHY_HALL_INDEX_PATH, index)
    return added, updated


async def _parse_oath_message(msg: discord.Message):
    content = msg.content or ""
    lines = [l.strip() for l in content.splitlines()]
    user_id = None
    if msg.mentions:
        user_id = str(msg.mentions[0].id)
    current_rank_raw = None
    for l in lines:
        low = l.lower()
        if low.startswith("current rank:") or low.startswith("rank:"):
            try:
                current_rank_raw = l.split(":", 1)[1].strip()
            except Exception:
                current_rank_raw = l.strip()
            break
    target_role_names = [getattr(r, "name", "") for r in msg.role_mentions]
    has_oath = True if user_id else False
    entry = {
        "message_id": str(msg.id),
        "user_id": user_id,
        "current_rank_raw": current_rank_raw,
        "oath_target_roles": [n for n in target_role_names if n],
        "content_hash": hashlib.sha256((content).encode("utf-8")).hexdigest(),
        "edited_at": msg.edited_at.isoformat()
        if getattr(msg, "edited_at", None)
        else None,
        "has_oath": bool(has_oath),
        "timestamp": msg.created_at.isoformat(),
    }
    return entry


async def _ingest_record_of_oaths(guild: Optional[discord.Guild]):
    added = 0
    updated = 0
    if not guild:
        return added, updated
    channel = discord.utils.get(guild.channels, name="❖⋅record-of-oaths⋅❖")
    if not channel:
        return added, updated
    index = _load_json_dict(OATHS_INDEX_PATH)
    meta = index.get("_meta") or {}
    last_id_str = meta.get("last_ingested_message_id")
    try:
        last_id = int(last_id_str) if last_id_str else 0
    except Exception:
        last_id = 0
    async for msg in channel.history(limit=None):
        if last_id and msg.id <= last_id:
            break
        entry = await _parse_oath_message(msg)
        key = str(msg.id)
        prev = index.get(key)
        if not prev:
            index[key] = entry
            added += 1
        else:
            if (
                prev.get("content_hash") != entry["content_hash"]
                or prev.get("edited_at") != entry["edited_at"]
            ):
                index[key] = entry
                updated += 1
        meta["last_ingested_message_id"] = str(msg.id)
    keys = [k for k in index.keys() if k != "_meta"]
    try:
        recent_sorted = sorted([int(k) for k in keys], reverse=True)
    except Exception:
        recent_sorted = []
    recent_keys = [str(k) for k in recent_sorted[:200]]
    for kid in recent_keys:
        try:
            m = await channel.fetch_message(int(kid))
        except Exception:
            continue
        e = await _parse_oath_message(m)
        prev = index.get(kid)
        if (
            not prev
            or prev.get("content_hash") != e.get("content_hash")
            or prev.get("edited_at") != e.get("edited_at")
        ):
            index[kid] = e
            updated += 1
    index["_meta"] = meta
    _save_json_dict(OATHS_INDEX_PATH, index)
    return added, updated


BATTLE_LINE_ORDER = [
    "Watch Brother",
    "Watch Veteran",
    "Watch Sergeant",
    "Watch Lieutenant",
    "Watch Captain",
]
CHAMPION_ROLES = {"Kill Team Champion", "Company Champion", "Lord Executioner"}
SPECIALIST_ROLES = {
    "Watch Chaplain",
    "Watch Apothecary",
    "Watch Librarian",
    "Watch Techmarine",
}
HIGH_COMMAND_ROLES = {
    "Watch Master",
    "High Chaplain",
    "Chief Apothecary",
    "Void Warden",
    "Forgemaster",
}


def _member_has_any_role(member: discord.Member, targets: set[str]) -> bool:
    names = _canonical_role_names(member)
    return any(t in names for t in targets)


def _current_battle_line_index(member: discord.Member) -> Optional[int]:
    names = _canonical_role_names(member)
    idxs = [i for i, r in enumerate(BATTLE_LINE_ORDER) if r in names]
    if not idxs:
        return None
    return max(idxs)


def _rank_index_from_text(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    low = (text or "").lower()
    for i, r in enumerate(BATTLE_LINE_ORDER):
        if r.lower() in low:
            return i
    return None


def _evaluate_oath(member: discord.Member, oath_entry: dict) -> str:
    targets = set(oath_entry.get("oath_target_roles") or [])
    bl_targets = [r for r in BATTLE_LINE_ORDER if r in targets]
    has_champion = any(r in CHAMPION_ROLES for r in targets)
    has_specialist = any(r in SPECIALIST_ROLES for r in targets)
    has_highcmd = any(r in HIGH_COMMAND_ROLES for r in targets)
    if bl_targets:
        target_idx = max([BATTLE_LINE_ORDER.index(r) for r in bl_targets])
        cur_idx = _current_battle_line_index(member)
        if cur_idx is None:
            cur_idx = _rank_index_from_text(oath_entry.get("current_rank_raw"))
        if cur_idx is not None and cur_idx >= target_idx:
            return "fulfilled"
        return "unfulfilled"
    if has_champion and _member_has_any_role(member, CHAMPION_ROLES):
        return "fulfilled"
    if has_specialist and _member_has_any_role(member, SPECIALIST_ROLES):
        return "fulfilled"
    if has_highcmd and _member_has_any_role(member, HIGH_COMMAND_ROLES):
        return "fulfilled"
    if targets:
        return "unclassified"
    return "unclassified"


def _discipline_tier(participation_pct: float, unfulfilled_count: int) -> str:
    if participation_pct >= 80.0 and unfulfilled_count <= 1:
        return "Exemplary"
    if participation_pct >= 60.0 and unfulfilled_count <= 2:
        return "Steady"
    if participation_pct >= 40.0:
        return "Lacking"
    return "Requires Intervention"


def _collect_company_teams(guild: discord.Guild, company: discord.Role):
    killteam_roles: List[discord.Role] = []
    try:
        for r in getattr(guild, "roles", []):
            n = getattr(r, "name", "")
            low = n.lower()
            if "kill" in low and "team" in low and "champion" not in low:
                killteam_roles.append(r)
    except Exception:
        killteam_roles = []
    company_members: List[discord.Member] = [m for m in getattr(company, "members", [])]
    company_ids: set[str] = {
        str(getattr(m, "id", "")) for m in company_members if getattr(m, "id", None)
    }
    teams: List[Tuple[str, List[discord.Member]]] = []
    for kt in killteam_roles:
        kt_members = [
            m
            for m in getattr(kt, "members", [])
            if str(getattr(m, "id", "")) in company_ids
        ]
        if kt_members:
            teams.append(
                (_extract_killteam_name(getattr(kt, "name", "Unknown")), kt_members)
            )
    idx_sergeant = _role_index("Watch Sergeant")
    company_command_members: List[discord.Member] = []
    if idx_sergeant is not None:
        for m in company_members:
            names = _canonical_role_names(m)
            bl_idxs = [i for i, r in enumerate(BATTLE_LINE_ORDER) if r in names]
            if bl_idxs and max(bl_idxs) >= BATTLE_LINE_ORDER.index("Watch Sergeant"):
                company_command_members.append(m)
    if company_command_members:
        teams.append(("Company Command", company_command_members))
    return teams


def _build_chaplain_report(guild: discord.Guild, company: discord.Role):
    trophy = _load_json_dict(TROPHY_HALL_INDEX_PATH)
    oaths = _load_json_dict(OATHS_INDEX_PATH)
    completer_ids: set[str] = set()
    for key, e in trophy.items():
        if key == "_meta":
            continue
        for uid in e.get("completer_user_ids") or []:
            completer_ids.add(str(uid))
    oath_entries_by_user: Dict[str, dict] = {}
    for key, e in oaths.items():
        if key == "_meta":
            continue
        uid = e.get("user_id")
        if not uid:
            continue
        prev = oath_entries_by_user.get(uid)
        if not prev or int(key) > int(prev.get("message_id", "0")):
            oath_entries_by_user[uid] = e
    teams = _collect_company_teams(guild, company)
    lines: List[str] = []
    lines.append("```ansi")
    lines.append(
        "\u001b[32m=============================================================================="
    )
    lines.append("  WATCH FORTRESS JERICHO // CHAPLAIN DISCIPLINE BRIEF")
    lines.append("  OPERATION-SCRIBE SERVITOR — DISCIPLINE ASSESSMENT")
    lines.append(
        "=============================================================================="
    )
    lines.append(
        f"  {getattr(company, 'name', 'Unknown')}  |  Cached Sources: Trophy Hall, Record of Oaths"
    )
    lines.append(
        "------------------------------------------------------------------------------"
    )
    for team_name, members in teams:
        member_ids = {
            str(getattr(m, "id", "")) for m in members if getattr(m, "id", None)
        }
        if not member_ids:
            continue
        challenge_done = len(member_ids & completer_ids)
        challenge_pct = 100.0 * challenge_done / max(1, len(member_ids))
        oath_users = [uid for uid in member_ids if uid in oath_entries_by_user]
        oath_participation_pct = 100.0 * len(oath_users) / max(1, len(member_ids))
        fulfilled = 0
        unfulfilled = 0
        unclassified = 0
        for uid in oath_users:
            m = next((mm for mm in members if str(getattr(mm, "id", "")) == uid), None)
            if not m:
                continue
            status = _evaluate_oath(m, oath_entries_by_user.get(uid) or {})
            if status == "fulfilled":
                fulfilled += 1
            elif status == "unfulfilled":
                unfulfilled += 1
            else:
                unclassified += 1
        tier = _discipline_tier(oath_participation_pct, unfulfilled)
        lines.append(f"  Kill Team {team_name}   :: Discipline: {tier}")
        lines.append(f"    Oath Participation    :: {oath_participation_pct:.0f}%")
        lines.append(
            f"    Oath Status           :: Fulfilled {fulfilled}  Unfulfilled {unfulfilled}  Unclassified {unclassified}"
        )
        flag_zero = "  (Zero challenge participation)" if challenge_done == 0 else ""
        lines.append(f"    Challenge Compliance  :: {challenge_pct:.0f}%{flag_zero}")
    lines.append(
        "=============================================================================="
    )
    lines.append("\u001b[0m```")
    msg = "\n".join(lines)
    if len(msg) > 1900:
        trimmed = []
        for ln in lines:
            if ln.strip().startswith("Challenge Compliance"):
                continue
            trimmed.append(ln)
        msg = "\n".join(trimmed)
    if len(msg) > 1900:
        trimmed2 = []
        for ln in msg.splitlines():
            if "Unclassified" in ln:
                ln = ln.split("Unclassified")[0].rstrip()
            trimmed2.append(ln)
        msg = "\n".join(trimmed2)
    return msg


@bot.tree.command(
    name="chaplain_brief",
    description="Generate a Chaplain discipline brief for a Company; optionally update caches.",
)
@app_commands.describe(
    company="The Watch Company role to analyze.",
    update="If true, ingest updates from Trophy Hall and Record of Oaths.",
)
async def chaplain_brief(
    interaction: discord.Interaction,
    company: discord.Role,
    update: Optional[bool] = False,
):
    if not (
        is_sergeant_or_higher(interaction.user) and is_allowed_channel(interaction)
    ):
        try:
            await interaction.response.send_message(
                "++ ACCESS DENIED: SERGEANT+ IN SANCTIFIED CHANNEL REQUIRED. ++",
                ephemeral=True,
            )
        except Exception:
            return
        return
    await interaction.response.defer(thinking=True, ephemeral=True)
    guild = interaction.guild
    if not guild or not company or not getattr(company, "members", None):
        await interaction.followup.send(
            "++ ERROR: COMPANY ROLE OR GUILD UNAVAILABLE. ++", ephemeral=True
        )
        return
    if update:
        if CHAPLAIN_INGEST_LOCK.locked():
            await interaction.followup.send(
                "++ INTAKE IN PROGRESS: PLEASE RETRY SHORTLY. ++", ephemeral=True
            )
            return
        await CHAPLAIN_INGEST_LOCK.acquire()
        try:
            th_added, th_updated = await _ingest_trophy_hall(guild)
            oa_added, oa_updated = await _ingest_record_of_oaths(guild)
            await interaction.followup.send(
                f"++ CACHE UPDATED: TrophyHall +{th_added}/{th_updated} | Oaths +{oa_added}/{oa_updated} ++",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(f"++ UPDATE FAILED: {e} ++", ephemeral=True)
        finally:
            try:
                CHAPLAIN_INGEST_LOCK.release()
            except Exception:
                pass
    report = _build_chaplain_report(guild, company)
    await interaction.followup.send(report, ephemeral=True)


if __name__ == "__main__":
    _main()
