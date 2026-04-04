#!/usr/bin/env python3

# TODO: is it better design-wise in aars to force errors on difficulty if the mention is not used and its just plaintext?
# TODO: should we add company distinctions in monthly honors?
# TODO: do we need to schedule a reparse command like we do ingestion and audits?
# TODO: are commands queued? i know we use locks but do commands enter a queue?
# TODO: should we split this file up? its getting pretty long. maybe aars.py for AAR-related commands and processing, awards.py for awards and milestones, etc? or is it better to keep it all together since there is some interdependence and shared state (e.g. datastore access, config, locks)? maybe we can split out some of the more self-contained features like rites and machine spirits into separate modules to reduce clutter in the main bot file while keeping core command handling together? would also make it easier to manage imports and dependencies if we have more focused modules. on the other hand, having everything in one file can make it easier to see the overall flow and shared context without jumping between files. maybe we can start by splitting out just the AAR processing into aars.py since that is a large chunk of functionality, and keep the rest in bot.py for now? then if we find that awards/milestones or rites/machine spirits are also getting large we can consider splitting those out as well. would need to be careful about circular imports though if we split into multiple files since they all interact with the datastore and config. could potentially have a common module for shared utilities and data access to avoid circular dependencies. overall i think splitting out AAR processing into aars.py makes sense as a first step since it is a distinct area of functionality with its own commands and processing logic, and then we can evaluate if further splits are needed after that.
# TODO: for armor integrity system - lets track stats for how many blessings each techmarine is doing, which brothers are getting blessed and how much, what their damage values are when they get blessed, and anything else i might be missing.

import os
import asyncio
import json
import calendar
import discord
from discord import app_commands
from datetime import datetime, timedelta, timezone
from discord.ext import tasks
import re
import itertools
from typing import Dict, List, Set, Tuple, Optional
import hashlib
import logging
import time
import random
from logging.handlers import RotatingFileHandler
import signal
import argparse
import statistics
import sys

# Import DataStore
from datastore import DataStore

# Role IDs
WATCH_COMMAND_ROLE_ID = 1429281421931057283

# Global DataStore instance (initialized when bot is ready)
DATASTORE: Optional[DataStore] = None

# Data file locations
DATA_DIR = "data"
AAR_RECORDS_PATH = os.path.join(DATA_DIR, "aar_records.json")
AAR_ERRORS_PATH = os.path.join(DATA_DIR, "aar_errors.json")
PROCESSED_IDS_PATH = os.path.join(DATA_DIR, "processed_ids.json")
RITES_PATH = os.path.join(DATA_DIR, "rites.json")
MACHINE_SPIRITS_PATH = os.path.join(DATA_DIR, "machine_spirits.json")
ACTIVITY_STATUS_PATH = os.path.join(DATA_DIR, "activity_status.json")
ACTIVITY_STATUS_LAST_CHECK_PATH = os.path.join(
    DATA_DIR, "activity_status_last_check.json"
)
PROMOTION_TRACKING_PATH = os.path.join(DATA_DIR, "promotion_tracking.json")
MILESTONE_TRACKING_PATH = os.path.join(DATA_DIR, "milestone_tracking.json")
ARMOR_INTEGRITY_PATH = os.path.join(DATA_DIR, "armor_integrity.json")
ARMOR_SCAN_STATE_PATH = os.path.join(DATA_DIR, "armor_scan_state.json")

# Channel ID for activity status change notifications
ACTIVITY_STATUS_CHANNEL_ID = 1459043645499117630

# Channel ID for veteran promotion notifications
VETERAN_PROMOTION_CHANNEL_ID = 1443813516979994634

# Channel ID for service stud milestone notifications
SERVICE_STUDS_CHANNEL_ID = 1430055064969674777  # ᛭⋅⋅general-chat⋅⋅᛭

# Channel ID for Black Laurels eligibility notifications
BLACK_LAURELS_CHANNEL_ID = 1443813633220935774

# Channel ID for Oathsworn eligibility notifications
OATHSWORN_CHANNEL_ID = 1489282103119052903

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = discord.Client(intents=intents, chunk_guilds_at_startup=True)
bot.tree = app_commands.CommandTree(bot)

# Global lock to serialize reconciliation runs
RECONCILE_LOCK = asyncio.Lock()

# Flag to indicate a monthly full-history audit is pending/running so daily audits skip.
MONTHLY_AUDIT_PENDING = False

# Rites storage lock
RITES_LOCK = asyncio.Lock()

# Machine spirits storage lock
MACHINE_SPIRITS_LOCK = asyncio.Lock()

# Lock for rotation state operations
ROTATION_LOCK = asyncio.Lock()

# Lock for activity status operations
ACTIVITY_STATUS_LOCK = asyncio.Lock()

# Lock for promotion tracking file operations (shared between promotion_queue and
# _check_promotion_milestones to prevent concurrent read-modify-write races)
PROMOTION_TRACKING_LOCK = asyncio.Lock()

# Lock for armor integrity operations
ARMOR_INTEGRITY_LOCK = asyncio.Lock()

# Lock for armor scan state (detection caching per AAR cycle)
ARMOR_SCAN_STATE_LOCK = asyncio.Lock()

# Lock for blessing pool operations (Techmarine daily blessing limits)
BLESSING_POOL_LOCK = asyncio.Lock()
BLESSING_POOL_PATH = os.path.join(DATA_DIR, "blessing_pool.json")

# Lock for forge requisition pool (community armory -> blessing charges)
FORGE_POOL_LOCK = asyncio.Lock()
FORGE_POOL_PATH = os.path.join(DATA_DIR, "forge_pool.json")

# Lock for forge chronicle (immersive armor channel data)
FORGE_CHRONICLE_LOCK = asyncio.Lock()
FORGE_CHRONICLE_PATH = os.path.join(DATA_DIR, "forge_chronicle.json")

# Forge requisition pool configuration
FORGE_POOL_COST_PER_CHARGE = 200  # Armory points spent per blessing charge
FORGE_POOL_DAILY_LIMIT = 2  # Max requisitions per Techmarine per day
FORGE_POOL_MAX_CHARGES = 30  # Maximum charges the forge can hold

# Blessing pool configuration
BLESSING_POOL_MAX = 5  # Maximum blessings per Techmarine
BLESSING_POOL_REGEN_HOURS = 24 / 5  # 4.8 hours per blessing regeneration
BLESSING_RECIPIENT_COOLDOWN_HOURS = 24  # Cooldown window for recipient blessing count
BLESSING_RECIPIENT_MAX_PER_DAY = 3  # Maximum blessings per recipient per 24h
BLESSING_RECIPIENT_PER_BLESSING_COOLDOWN_HOURS = 4  # Minimum hours between blessings for same recipient

# Intensive blessing charge costs (full heal to nominal)
# Maps damage tier to number of charges required for intensive blessing
INTENSIVE_BLESSING_COSTS = {
    None: 0,           # Nominal: cannot use intensive
    "damaged": 1,      # Damaged -> Nominal: 1 charge (same as standard)
    "compromised": 2,  # Compromised -> Nominal: 2 charges
    "critical": 3,     # Critical -> Nominal: 3 charges
    "fractured": 4,    # Fractured -> Nominal: 4 charges
}

# Blessing roll configuration - asymmetric state-based probabilities
# Format: (crit_fail_chance, crit_success_chance) - normal is remainder
BLESSING_ROLL_PROBABILITIES = {
    None: (0.01, 0.01),        # Nominal: 1/98/1 - routine maintenance
    "damaged": (0.03, 0.03),   # Damaged: 3/94/3 - minor repair
    "compromised": (0.05, 0.05),  # Compromised: 5/90/5 - agitated spirit
    "critical": (0.08, 0.06),  # Critical: 8/86/6 - volatile, asymmetric
    "fractured": (0.10, 0.10), # Fractured: 10/80/10 - desperate spirit
}
# Legacy thresholds (used as fallback)
BLESSING_ROLL_CRIT_FAIL_THRESHOLD = 0.05  # Bottom 5% = crit fail
BLESSING_ROLL_CRIT_SUCCESS_THRESHOLD = 0.95  # Top 5% = crit success
BLESSING_CRIT_SUCCESS_GRACE_POINTS = -25  # Grace points on crit success

# Guard to avoid double shutdown handling
SHUTDOWN_INITIATED = False

# Scheduler settings (default values can be overridden in config.json under 'schedules')
SCHEDULE_DAILY_AUDIT_ENABLED = False
SCHEDULE_DAILY_AUDIT_SPAN_DAYS = 1

# Weekly maintenance settings (Tuesday 8 AM UTC by default)
# Runs sanctify (45-day span) + full audit (no span) to catch stragglers
SCHEDULE_WEEKLY_MAINTENANCE_ENABLED = True
SCHEDULE_WEEKLY_MAINTENANCE_INGEST_SPAN_DAYS = 45
SCHEDULE_WEEKLY_MAINTENANCE_DAY = 1  # 0=Monday, 1=Tuesday, ..., 6=Sunday
SCHEDULE_WEEKLY_MAINTENANCE_HOUR = 8  # Hour in UTC

# Milestone announcement settings (weekly check)
MILESTONES_ENABLED = True
MILESTONES_CHANNEL_ID: int = 1430055064969674777  # ᛭⋅⋅general-chat⋅⋅᛭
MILESTONES_CHECK_INTERVAL_DAYS = 7  # Check once per week
MILESTONES_INCREMENTS = {
    "aar_points": 2500,
    "aar_count": 500,
    "geneseed_recoveries": 500,
    "armory_data": 1000,
    "hive_tyrant_kills": 100,
    "bio_titan_kills": 100,
    "tyranid_prime_kills": 100,
}

# Track last milestone check date to prevent duplicate runs
LAST_MILESTONE_CHECK_DATE: Optional[str] = None

# Black Laurels strict enforcement begins on Feb 20, 2026 at 00:00 UTC
BLACK_LAURELS_STRICT_ENFORCEMENT_DATE = datetime(
    2026, 2, 20, 0, 0, 0, tzinfo=timezone.utc
)
# Black Laurels role ID for parsing
BLACK_LAURELS_ROLE_ID = 1440108298115485716
# Leviathan Protocol role ID for parsing
LEVIATHAN_PROTOCOL_ROLE_ID = 1486066148834541619
# Pipehitter role IDs for parsing
PIPEHITTER_ROLE_ID = 1435812894532042843
DISTINGUISHED_PIPEHITTER_ROLE_ID = 1480420419063386275
# Missions eligible for Pipehitter mentions
PIPEHITTER_ELIGIBLE_MISSIONS = {
    "inferno",
    "vox liberatis",
    "reliquary",
    "fall of atreus",
    "termination",
    "obelisk",
    "exfiltration",
    "vortex",
    "reclamation",
    "disruption",
}
# Required missions for Black Laurels eligibility (all required for new earners)
BLACK_LAURELS_REQUIRED_MISSIONS = {
    "inferno",
    "decapitation",
    "vox liberatis",
    "ballistic engine",
    "exfiltration",
    "termination",
    "reclamation",
    "disruption",
}
# Grandfathered missions - users who already have the role are assumed to have completed these
# Any NEW missions added to BLACK_LAURELS_REQUIRED_MISSIONS must be explicitly completed
BLACK_LAURELS_GRANDFATHERED_MISSIONS = {
    "inferno",
    "decapitation",
    "vox liberatis",
    "ballistic engine",
    "exfiltration",
    "termination",
    "reclamation",
}

# Specialist award thresholds and role mappings
# Award role IDs (looked up by ID to avoid name change issues)
ARDENT_RAIDER_ROLE_ID = 1436170746283163770  # Ardent Raider Ribbon
APOTHECARION_SERVICE_MEDAL_ROLE_ID = 1436434868652212275  # Apothecarion Service Medal
CRIMSON_LAURELS_ROLE_NAME = "Crimson Laurels"

# Specialist role names for mentions (looked up dynamically)
TECHMARINE_ROLE_NAME = "Watch Techmarine"
APOTHECARY_ROLE_NAME = "Watch Apothecary"
LIBRARIAN_ROLE_NAME = "Watch Librarian"

# Award eligibility thresholds
ARDENT_RAIDER_ARMORY_POINTS_THRESHOLD = 200
FOR_THE_FALLEN_GENESEED_POINTS_THRESHOLD = 150
CRIMSON_LAURELS_AAR_POINTS_THRESHOLD = 1000

# Challenge roles for /completed_challenges command
# Each entry is (role_id, display_name, emoji_hint)
# emoji_hint can be a custom emoji name to look up, "unicode:<char>" for a literal unicode emoji, or None to skip
CHALLENGE_ROLES = [
    # SOK-G Elite
    (1480420419063386275, "Distinguished SOK-G: Pipehitter", "DistinguishedSOKGServiceMedal"),
    (1435812894532042843, "SOK-G: Pipehitter", "SOKGServiceMedal"),
    # Terminus Slayer variants
    (1452803611477147668, "Master Terminus Slayer", "MasterTerminusSlayer"),
    (1449257352112111646, "Terminus Slayer (Assault)", "1stAwardTerminusSlayer"),
    (1450230281599713451, "Terminus Slayer (Tactical)", "1stAwardTerminusSlayer"),
    (1450230501804609697, "Terminus Slayer (Vanguard)", "1stAwardTerminusSlayer"),
    (1450230789034737748, "Terminus Slayer (Bulwark)", "1stAwardTerminusSlayer"),
    (1450231020686278656, "Terminus Slayer (Sniper)", "1stAwardTerminusSlayer"),
    (1450231189028737166, "Terminus Slayer (Heavy)", "1stAwardTerminusSlayer"),
    (1476623936254115992, "Terminus Slayer (Techmarine)", "1stAwardTerminusSlayer"),
    # Laurels
    (1450595241508733183, "Crimson Laurels", "CrimsonLaurelsMedal"),
    (1440108298115485716, "Black Laurels", "BlackLaurelsMedal"),
    # Service awards
    (1436434868652212275, "Apothecarion Service Medal", "ApothecarionServiceMedal"),
    (1436170746283163770, "Ardent Raider Ribbon", "ArdentRaiderRibbon"),
    # Elite challenges
    (1476288996756820109, "Crux Terminatus", "CruxTerminatusMedal"),
    (1465020459794956349, "White Hand of Death", "ClandestineOperationsMedal"),
    (1465021610812637214, "Red Hand of Doom", "DistinguishedClandestineoperati"),
    (1486067010747236472, "Kadaku Campaign Medal", "KadakuCampaignMedal"),
]

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


def _should_send_award_notification(
    is_eligible: bool,
    has_role: bool,
    tracking_key: str,
    tracking: dict,
) -> bool:
    """Decide whether an award notification should be sent.

    On the first time a key is seen (first run / after a restart), if the member
    already has the role the entry is silently initialised to ``True`` in *tracking*
    so that no duplicate notification is produced.  In all other first-run cases
    *tracking* is not mutated by this function.

    After initialisation a notification is only sent when all three conditions
    hold: the member is eligible, does **not** yet hold the role, and has not
    previously been notified.

    Returns ``True`` if a notification should be sent, ``False`` otherwise.
    """
    # First run: silently mark as notified if the role is already held
    if tracking_key not in tracking:
        if has_role:
            tracking[tracking_key] = True

    if tracking.get(tracking_key):
        return False

    return is_eligible and not has_role


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
    # 2) Try by configured guild id
    try:
        gid = CONFIG.get("guild_id")
        if gid:
            for g in bot.guilds:
                if str(getattr(g, "id", None)) == str(gid):
                    return g
    except Exception:
        pass
    # 3) Fallback to the first connected guild
    try:
        return bot.guilds[0] if bot.guilds else None
    except Exception:
        return None


async def _send_watch_command_notice(kind: str):
    """Post a concise status notice to the status channel and replace the previous one.
    kind: 'ONLINE' or 'OFFLINE' (case-insensitive).
    Behavior: always delete the most recent prior status bulletin (regardless of
    its previous state), then send the new bulletin so only one is visible."""
    # Respect broadcast toggle (e.g., when debug mode disables broadcasts)
    try:
        if not BROADCAST_STATUS:
            logger.info("Status broadcast skipped (BROADCAST_STATUS=False)")
            return
    except Exception:
        # If BROADCAST_STATUS is undefined for any reason, continue safely
        pass
    guild = _resolve_notification_guild()
    if not guild:
        logger.warning("No guild available for status notification.")
        return
    logger.info(f"Sending {kind} notice to guild: {guild.name}")
    # Use channel ID directly for reliability
    STATUS_CHANNEL_ID = 1430055064969674777
    try:
        channel = bot.get_channel(STATUS_CHANNEL_ID)
        if not channel:
            channel = await bot.fetch_channel(STATUS_CHANNEL_ID)
    except Exception as e:
        logger.warning(f"Channel fetch failed: {e}")
        channel = None
    if not channel:
        logger.warning(f"Status channel ID {STATUS_CHANNEL_ID} not accessible.")
        return
    status = "ONLINE" if (kind or "").upper().startswith("ON") else "OFFLINE"
    # Always delete the most recent status bulletin (regardless of prior status)
    try:
        async for msg in channel.history(limit=50):
            try:
                if getattr(msg.author, "id", None) != getattr(bot.user, "id", None):
                    continue
                content = msg.content or ""
                # Identify our prior bulletin by a concise marker or legacy header
                if ("V-1 STATUS:" in content) or (
                    "OPERATION-SCRIBE SERVITOR — STATUS BULLETIN" in content
                ):
                    await msg.delete()
                    break
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"Failed to delete previous status bulletin: {e}")

    emoji = "✅" if status == "ONLINE" else "⛔"
    flavor = (
        "Machine-spirit standing by."
        if status == "ONLINE"
        else "Machine-spirit at rest."
    )
    # Concise, at-a-glance status with a touch of flavor
    # Omit explicit timestamp; Discord shows message time in the UI.
    content = f"V-1 STATUS: {status} {emoji}\n{flavor}"
    try:
        await channel.send(content)
        logger.info(f"Status notification sent: {status}")
    except Exception as e:
        logger.warning(f"Failed to send status notification: {e}")


async def _announce_shutdown_and_close():
    global SHUTDOWN_INITIATED
    if SHUTDOWN_INITIATED:
        logger.debug("Shutdown already initiated, skipping duplicate call.")
        return
    SHUTDOWN_INITIATED = True
    logger.info("Beginning graceful shutdown sequence...")

    # Fast shutdown path for debug mode: skip broadcasts and avoid waiting
    # for a full DataStore flush to make Ctrl-C immediate during development.
    try:
        if globals().get("DEBUG_MODE"):
            try:
                logger.info(
                    "Debug mode shutdown: skipping broadcast and datastore flush"
                )
            except Exception:
                pass
            try:
                if DATASTORE:
                    try:
                        DATASTORE._shutdown = True
                    except Exception:
                        pass
                    try:
                        if getattr(DATASTORE, "_flush_task", None):
                            DATASTORE._flush_task.cancel()
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                await asyncio.wait_for(bot.close(), timeout=5)
            except Exception:
                try:
                    await bot.close()
                except Exception:
                    pass
            return
    except Exception:
        pass

    try:
        if BROADCAST_STATUS:
            logger.info("Sending OFFLINE status to Watch Command...")
            try:
                await asyncio.wait_for(_send_watch_command_notice("OFFLINE"), timeout=8)
                logger.info("OFFLINE status sent successfully.")
            except asyncio.TimeoutError:
                logger.warning("Shutdown announce timed out after 8s.")
            except Exception as e:
                logger.warning(f"Shutdown announce failed: {e}")
    except Exception:
        logger.debug("Shutdown announce threw an unexpected error")

    # Flush DataStore before closing, but don't block indefinitely
    try:
        if DATASTORE:
            logger.info("Flushing DataStore...")
            try:
                await asyncio.wait_for(DATASTORE.shutdown(), timeout=15)
                logger.info("DataStore flush complete.")
            except asyncio.TimeoutError:
                logger.warning("DataStore shutdown timed out; proceeding with close.")
            except Exception as e:
                logger.warning(f"DataStore shutdown failed: {e}")
    except Exception:
        logger.debug("Error during DataStore shutdown sequence")

    logger.info("Closing Discord connection...")
    try:
        await asyncio.wait_for(bot.close(), timeout=10)
        logger.info("Discord connection closed.")
    except Exception as e:
        logger.warning(f"bot.close() failed or timed out: {e}")
        try:
            await bot.close()
        except Exception:
            pass


# Scheduled audit: runs _run_recheck_errors periodically when enabled
async def _do_scheduled_audit(span_days: int | None = None, *, monthly: bool = False):
    try:
        global MONTHLY_AUDIT_PENDING
        # If a monthly audit is pending/running, skip regular scheduled runs.
        if not monthly and MONTHLY_AUDIT_PENDING:
            logger.info("Scheduled audit skipped: monthly audit pending.")
            return
        if RECONCILE_LOCK.locked():
            logger.info("Scheduled audit skipped: reconciliation already in progress.")
            return
        guild = _resolve_notification_guild()
        if not guild:
            logger.debug("Scheduled audit: no guild available; skipping.")
            return
        aar_channel = discord.utils.get(
            guild.channels, name="᛭⋅⋅after-action-reports⋅⋅᛭"
        )
        if not aar_channel:
            logger.debug("Scheduled audit: AAR channel not found; skipping.")
            return
        async with RECONCILE_LOCK:
            if monthly:
                # Mark monthly as running while we hold the lock
                MONTHLY_AUDIT_PENDING = True
            try:
                fixed, still_broken = await _run_recheck_errors(aar_channel, span_days)
                logger.info(
                    f"Scheduled audit complete: restored={fixed}, broken_remaining={still_broken}"
                )
            finally:
                # Clear monthly flag before releasing lock so other scheduled runs
                # may not start until this completes.
                if monthly:
                    MONTHLY_AUDIT_PENDING = False
    except Exception:
        logger.exception("Scheduled audit failed")


@tasks.loop(hours=24)
async def _scheduled_audit_loop():
    # Delay the first run so startup does not trigger an immediate audit.
    try:
        if not getattr(_scheduled_audit_loop, "_first_run_done", False):
            setattr(_scheduled_audit_loop, "_first_run_done", True)
            # Sleep one full interval (24 hours) before the first audit
            await asyncio.sleep(24 * 3600)
    except Exception:
        pass

    # Use configured span days
    try:
        await _do_scheduled_audit(SCHEDULE_DAILY_AUDIT_SPAN_DAYS)
    except Exception:
        logger.exception("Error running scheduled audit loop")


@tasks.loop(hours=24)
async def _monthly_audit_loop():
    """Run once-per-day; on the last day of the month perform a full-history audit.

    The loop uses `wait=True` so its first execution occurs one interval after
    being started (matching the scheduled loop behavior). On the last day of
    the month, it will call `_do_scheduled_audit` with `monthly=True` which
    causes a full-history recheck (span_days=None) and signals priority to
    regular scheduled audits.
    """
    # Delay first run so startup doesn't immediately evaluate month-end
    try:
        if not getattr(_monthly_audit_loop, "_first_run_done", False):
            setattr(_monthly_audit_loop, "_first_run_done", True)
            await asyncio.sleep(24 * 3600)
    except Exception:
        pass

    try:
        now = datetime.utcnow()
        tomorrow = now + timedelta(days=1)
        # If tomorrow is the first, today is the last day of the month.
        if getattr(tomorrow, "day", 0) == 1:
            logger.info("Monthly audit scheduled: running full-history recheck.")
            try:
                await _do_scheduled_audit(None, monthly=True)
            except Exception:
                logger.exception("Monthly audit failed")
        else:
            logger.debug("Monthly audit: not the last day of the month; skipping.")
    except Exception:
        logger.exception("Error running monthly audit loop")


# Track last weekly maintenance run date to prevent duplicate runs
LAST_WEEKLY_MAINTENANCE_DATE: Optional[str] = None


@tasks.loop(minutes=60)
async def _scheduled_weekly_maintenance_loop():
    """Run hourly; on configured day/hour run sanctify + full audit.

    Default: Tuesday 8 AM UTC. Runs sanctify (45-day span) to catch missed AARs,
    then a full audit (no span limit) to retry all known errors.
    """
    global LAST_WEEKLY_MAINTENANCE_DATE
    try:
        if DATASTORE is None:
            return
        # Use UTC for consistent scheduling
        now_utc = datetime.now(timezone.utc)
        today = now_utc.date()

        # Check if it's the right day and hour
        if (
            now_utc.weekday() != SCHEDULE_WEEKLY_MAINTENANCE_DAY
            or now_utc.hour != SCHEDULE_WEEKLY_MAINTENANCE_HOUR
        ):
            return

        # Prevent duplicate runs on same date
        if LAST_WEEKLY_MAINTENANCE_DATE == str(today):
            return

        logger.info(
            f"Weekly maintenance starting: sanctify ({SCHEDULE_WEEKLY_MAINTENANCE_INGEST_SPAN_DAYS}-day span) + full audit"
        )

        guild = _resolve_notification_guild()
        if not guild:
            logger.warning("Weekly maintenance: no guild available; skipping.")
            return

        aar_channel = discord.utils.get(
            guild.channels, name="᛭⋅⋅after-action-reports⋅⋅᛭"
        )
        if not aar_channel:
            logger.warning("Weekly maintenance: AAR channel not found; skipping.")
            return

        # Acquire lock to prevent concurrent reconciliations
        if RECONCILE_LOCK.locked():
            logger.info("Weekly maintenance: reconcile lock held; skipping.")
            return

        async with RECONCILE_LOCK:
            # 1) Run sanctify with configured span
            logger.info(
                f"Weekly maintenance: Running ingest for last {SCHEDULE_WEEKLY_MAINTENANCE_INGEST_SPAN_DAYS} days"
            )
            ingested, rejected = await _run_ingest_new(
                aar_channel, SCHEDULE_WEEKLY_MAINTENANCE_INGEST_SPAN_DAYS
            )
            logger.info(f"Weekly maintenance: Ingested {ingested}, rejected {rejected}")

            # 2) Run full audit (no span limit) to catch all fixed errors
            logger.info("Weekly maintenance: Running full audit (no span limit)")
            fixed, still_broken = await _run_recheck_errors(aar_channel, None)
            logger.info(
                f"Weekly maintenance: Fixed {fixed}, still broken {still_broken}"
            )

            LAST_WEEKLY_MAINTENANCE_DATE = str(today)
            logger.info("Weekly maintenance completed successfully.")
    except Exception:
        logger.exception("Weekly maintenance failed")


@_scheduled_weekly_maintenance_loop.before_loop
async def _before_weekly_maintenance_loop():
    await bot.wait_until_ready()


# Config load
CONFIG_PATH = os.path.join("config", "config.json")
CONFIG: dict = {}
if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, "r") as f:
            CONFIG = json.load(f) or {}
    except Exception:
        # Log at module level (before the named logger is created) so the
        # operator knows the bot is running with an empty configuration.
        logging.warning(
            "CRITICAL: Failed to load %s — running with empty config. "
            "Permissions, channel IDs, and schedules will use hard-coded defaults.",
            CONFIG_PATH,
            exc_info=True,
        )
        CONFIG = {}

# Apply schedule configuration if present
try:
    schedules_cfg = CONFIG.get("schedules") or {}
    if _is_truthy(schedules_cfg.get("daily_audit_enabled")):
        SCHEDULE_DAILY_AUDIT_ENABLED = True
    SCHEDULE_DAILY_AUDIT_SPAN_DAYS = int(
        schedules_cfg.get("daily_audit_span_days") or SCHEDULE_DAILY_AUDIT_SPAN_DAYS
    )
    # Weekly maintenance settings
    if "weekly_maintenance_enabled" in schedules_cfg:
        SCHEDULE_WEEKLY_MAINTENANCE_ENABLED = _is_truthy(
            schedules_cfg.get("weekly_maintenance_enabled")
        )
    if schedules_cfg.get("weekly_maintenance_ingest_span_days"):
        SCHEDULE_WEEKLY_MAINTENANCE_INGEST_SPAN_DAYS = int(
            schedules_cfg.get("weekly_maintenance_ingest_span_days")
        )
    if schedules_cfg.get("weekly_maintenance_day") is not None:
        SCHEDULE_WEEKLY_MAINTENANCE_DAY = int(
            schedules_cfg.get("weekly_maintenance_day")
        )
    if schedules_cfg.get("weekly_maintenance_hour") is not None:
        SCHEDULE_WEEKLY_MAINTENANCE_HOUR = int(
            schedules_cfg.get("weekly_maintenance_hour")
        )
except Exception:
    pass

# Apply milestones configuration if present
try:
    milestones_cfg = CONFIG.get("milestones") or {}
    if "enabled" in milestones_cfg:
        MILESTONES_ENABLED = _is_truthy(milestones_cfg.get("enabled"))
    if milestones_cfg.get("check_interval_days") is not None:
        MILESTONES_CHECK_INTERVAL_DAYS = int(milestones_cfg.get("check_interval_days"))
    if milestones_cfg.get("increments"):
        increments_cfg = milestones_cfg.get("increments")
        for key in MILESTONES_INCREMENTS:
            if key in increments_cfg:
                MILESTONES_INCREMENTS[key] = int(increments_cfg[key])
except Exception:
    pass

# Logging setup
log_level_str = ((CONFIG.get("logging") or {}).get("level") or "INFO").upper()
log_level = getattr(logging, log_level_str, logging.INFO)
# Configure logging to use UTC timestamps
logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(message)s")
logging.Formatter.converter = time.gmtime  # Force UTC timestamps
logger = logging.getLogger("op-scribe-servitor")
_CMD_INVOCATIONS: dict[int, float] = {}

# Optional file logging with rotation
try:
    lg_cfg = CONFIG.get("logging") or {}
    if bool(lg_cfg.get("file_enabled", False)):
        path = str(lg_cfg.get("file_path") or "logs/op-scribe-servitor.log")
        max_bytes = int(lg_cfg.get("max_bytes", 2 * 1024 * 1024))
        backup_count = int(lg_cfg.get("backup_count", 5))
        # Ensure directory exists
        try:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        fh = RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        fh.setLevel(log_level)
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        formatter.converter = time.gmtime  # Force UTC timestamps for file handler
        fh.setFormatter(formatter)
        logger.addHandler(fh)
except Exception as e:
    try:
        print(f"[Logging setup] File handler failed: {e}")
    except Exception:
        pass

# Apply initial debug setting from config (CLI may override later in _main)
try:
    cfg_debug = _is_truthy((CONFIG or {}).get("debug"))
    BROADCAST_STATUS = not cfg_debug
    # DEBUG_MODE: when True, prefer faster, less-safe shutdown on Ctrl-C
    DEBUG_MODE = bool(cfg_debug)
except Exception:
    BROADCAST_STATUS = True
    DEBUG_MODE = False


# ---------------------------------------------------------------------------
# Activity status tracking: persist last known activity state per member
# ---------------------------------------------------------------------------


def _load_activity_status() -> Dict[str, Dict]:
    """Load stored activity status mapping: user_id -> {'status': 'active'|'inactive', 'updated_at': ISO timestamp}.

    For backwards compatibility, if status is a string, convert to new format.
    """
    try:
        if os.path.exists(ACTIVITY_STATUS_PATH):
            with open(ACTIVITY_STATUS_PATH, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    result = {}
                    for uid, val in data.items():
                        if isinstance(val, str):
                            # Old format: just the status string
                            result[uid] = {"status": val, "updated_at": None}
                        elif isinstance(val, dict):
                            # New format: already has status and updated_at
                            result[uid] = val
                    return result
    except Exception:
        pass
    return {}


def _load_member_last_post_times() -> Dict[str, str]:
    """Load mapping of member_id -> ISO timestamp of their last AAR post."""
    try:
        if os.path.exists(ACTIVITY_STATUS_LAST_CHECK_PATH):
            with open(ACTIVITY_STATUS_LAST_CHECK_PATH, "r") as f:
                data = json.load(f)
                return data.get("member_last_posts", {})
    except Exception as e:
        logger.debug(f"Failed to load member last post times: {e}")
    return {}


def _save_member_last_post_times(member_times: Dict[str, str]):
    """Save mapping of member_id -> ISO timestamp of their last AAR post."""
    try:
        tmp_path = ACTIVITY_STATUS_LAST_CHECK_PATH + ".tmp"
        # Load existing data to preserve other fields
        existing_data = {}
        if os.path.exists(ACTIVITY_STATUS_LAST_CHECK_PATH):
            try:
                with open(ACTIVITY_STATUS_LAST_CHECK_PATH, "r") as f:
                    existing_data = json.load(f)
            except Exception:
                pass

        existing_data["member_last_posts"] = member_times
        existing_data["last_check_time"] = datetime.utcnow().isoformat()

        with open(tmp_path, "w") as f:
            json.dump(existing_data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())

        if os.path.exists(ACTIVITY_STATUS_LAST_CHECK_PATH):
            try:
                os.replace(
                    ACTIVITY_STATUS_LAST_CHECK_PATH,
                    ACTIVITY_STATUS_LAST_CHECK_PATH + ".bak",
                )
            except Exception:
                pass
        os.replace(tmp_path, ACTIVITY_STATUS_LAST_CHECK_PATH)
    except Exception as e:
        logger.debug(f"Failed to save member last post times: {e}")


def _load_activity_status_last_check() -> Optional[datetime]:
    """Load the timestamp of the last activity status check."""
    try:
        if os.path.exists(ACTIVITY_STATUS_LAST_CHECK_PATH):
            with open(ACTIVITY_STATUS_LAST_CHECK_PATH, "r") as f:
                data = json.load(f)
                ts_str = data.get("last_check_time")
                if ts_str:
                    return datetime.fromisoformat(ts_str)
    except Exception as e:
        logger.debug(f"Failed to load activity status last check: {e}")
    return None


def _save_activity_status(status_map: Dict[str, Dict]):
    """Persist activity status mapping to disk with backup.

    Each entry is now {user_id: {'status': 'active'|'inactive', 'updated_at': ISO timestamp}}
    """
    try:
        tmp_path = ACTIVITY_STATUS_PATH + ".tmp"
        bak_path = ACTIVITY_STATUS_PATH + ".bak"
        # Ensure all entries have updated_at timestamp
        normalized_map = {}
        for uid, entry in status_map.items():
            if isinstance(entry, dict):
                normalized_map[uid] = entry
            else:
                # Shouldn't happen but handle gracefully
                normalized_map[uid] = {
                    "status": entry,
                    "updated_at": datetime.utcnow().isoformat(),
                }

        with open(tmp_path, "w") as f:
            json.dump(normalized_map, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(ACTIVITY_STATUS_PATH):
            try:
                os.replace(ACTIVITY_STATUS_PATH, bak_path)
            except Exception:
                pass
        os.replace(tmp_path, ACTIVITY_STATUS_PATH)
    except Exception as e:
        logger.exception(f"Failed to save activity status: {e}")


def _load_promotion_tracking() -> Dict[str, Dict]:
    """Load promotion tracking data: user_id -> {'veteran_notified': bool, 'last_studs_count': int}.

    Tracks which milestones have already been notified for each member.
    """
    try:
        if os.path.exists(PROMOTION_TRACKING_PATH):
            with open(PROMOTION_TRACKING_PATH, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_promotion_tracking(tracking_data: Dict[str, Dict]):
    """Persist promotion tracking data to disk with backup."""
    try:
        tmp_path = PROMOTION_TRACKING_PATH + ".tmp"
        bak_path = PROMOTION_TRACKING_PATH + ".bak"
        with open(tmp_path, "w") as f:
            json.dump(tracking_data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(PROMOTION_TRACKING_PATH):
            try:
                os.replace(PROMOTION_TRACKING_PATH, bak_path)
            except Exception:
                pass
        os.replace(tmp_path, PROMOTION_TRACKING_PATH)
    except Exception as e:
        logger.exception(f"Failed to save promotion tracking: {e}")


def _get_member_company_name(member: discord.Member) -> Optional[str]:
    """Return the Watch Company name for a member (e.g., 'Watch Company Primus'), or None."""
    company_roles = {
        "Watch Company Primus",
        "Watch Company Secundus",
        "Watch Company Tertius",
        "Watch Company Quartus",
        "Watch Company Quintus",
    }
    try:
        for r in getattr(member, "roles", []) or []:
            rn = (getattr(r, "name", "") or "").strip()
            if rn in company_roles:
                return rn
    except Exception:
        pass
    return None


def _extract_company_short_name(company_role_name: str) -> str:
    """Extract short name from 'Watch Company Primus' -> 'Primus'."""
    try:
        return company_role_name.replace("Watch Company", "").strip()
    except Exception:
        return company_role_name


def _find_company_command_staff(
    guild: discord.Guild, company_name: str
) -> Tuple[List[discord.Member], List[discord.Member]]:
    """Find the Captain(s) and Lieutenant(s) for a company.

    Returns (captains_list, lieutenants_list).
    A Captain/Lieutenant is a member who has both the Watch Captain/Lieutenant rank
    AND the specified company role.
    """
    captains: List[discord.Member] = []
    lieutenants: List[discord.Member] = []
    try:
        for member in guild.members:
            roles = getattr(member, "roles", []) or []
            role_names = {(getattr(r, "name", "") or "").strip() for r in roles}
            if company_name not in role_names:
                continue
            if "Watch Captain" in role_names:
                captains.append(member)
            if "Watch Lieutenant" in role_names:
                lieutenants.append(member)
    except Exception:
        pass
    return captains, lieutenants


def _find_kt_sergeant(guild: discord.Guild, kt_name: str) -> Optional[discord.Member]:
    """Find the Sergeant for a Kill Team.

    A Sergeant is a member who has both Watch Sergeant rank AND the specified KT role.
    Returns the first match or None.
    """
    try:
        for member in guild.members:
            roles = getattr(member, "roles", []) or []
            role_names = {(getattr(r, "name", "") or "").strip() for r in roles}
            if kt_name not in role_names:
                continue
            if "Watch Sergeant" in role_names:
                return member
    except Exception:
        pass
    return None


def _find_all_captains_and_lieutenants(
    guild: discord.Guild,
) -> Tuple[List[discord.Member], List[discord.Member]]:
    """Find all Captains and Lieutenants in the guild.

    Returns (all_captains, all_lieutenants).
    """
    captains: List[discord.Member] = []
    lieutenants: List[discord.Member] = []
    try:
        for member in guild.members:
            roles = getattr(member, "roles", []) or []
            role_names = {(getattr(r, "name", "") or "").strip() for r in roles}
            if "Watch Captain" in role_names:
                captains.append(member)
            if "Watch Lieutenant" in role_names:
                lieutenants.append(member)
    except Exception:
        pass
    return captains, lieutenants


def _find_watch_master(guild: discord.Guild) -> Optional[discord.Member]:
    """Find the Watch Master in the guild."""
    try:
        for member in guild.members:
            roles = getattr(member, "roles", []) or []
            role_names = {(getattr(r, "name", "") or "").strip() for r in roles}
            if "Watch Master" in role_names:
                return member
    except Exception:
        pass
    return None


def _get_member_display_name(member: discord.Member) -> str:
    """Get member's nickname or display name."""
    try:
        return member.nick or member.display_name or member.name or str(member.id)
    except Exception:
        return str(getattr(member, "id", "Unknown"))


def _get_member_rank_role(member: discord.Member) -> Optional[discord.Role]:
    """Return the member's highest rank role object, or None if no rank."""
    roles = getattr(member, "roles", []) or []
    best_idx: Optional[int] = None
    best_role: Optional[discord.Role] = None
    for role in roles:
        name = getattr(role, "name", None)
        if not name:
            continue
        idx = _role_index(name)
        if idx is not None:
            if best_idx is None or idx < best_idx:
                best_idx = idx
                best_role = role
    return best_role


async def _send_activity_status_notification(
    guild: discord.Guild,
    member: discord.Member,
    old_status: str,
    new_status: str,
):
    """Send a notification to the activity status channel when a member's status changes."""
    try:
        channel = guild.get_channel(ACTIVITY_STATUS_CHANNEL_ID)
        if not channel:
            try:
                channel = await bot.fetch_channel(ACTIVITY_STATUS_CHANNEL_ID)
            except Exception:
                logger.warning(
                    f"Activity status channel {ACTIVITY_STATUS_CHANNEL_ID} not found"
                )
                return
        if not channel:
            return

        member_name = _get_member_display_name(member)

        if new_status == "inactive":
            # Active -> Inactive: format as transfer to Reserves
            # Get member's rank role
            rank_role = _get_member_rank_role(member)
            rank_mention = rank_role.mention if rank_role else member_name

            # Get company role
            company_name = _get_member_company_name(member)
            company_role = (
                discord.utils.get(guild.roles, name=company_name)
                if company_name
                else None
            )
            company_mention = (
                company_role.mention if company_role else (company_name or "Unknown")
            )

            # Get Reserves role
            reserves_role = discord.utils.get(guild.roles, name="Reserves")
            reserves_mention = reserves_role.mention if reserves_role else "Reserves"

            # Get Watch Captain and Watch Lieutenant roles
            captain_role = discord.utils.get(guild.roles, name="Watch Captain")
            lt_role = discord.utils.get(guild.roles, name="Watch Lieutenant")
            command_mentions = []
            if captain_role:
                command_mentions.append(captain_role.mention)
            if lt_role:
                command_mentions.append(lt_role.mention)
            command_str = " / ".join(command_mentions) if command_mentions else ""

            lines = [
                f"᛭⋅ {rank_mention} {member.mention}",
                f"᛭⋅ Transfer from: {company_mention}",
                f"᛭⋅ To: {reserves_mention}",
            ]
            if command_str:
                lines.append(f"᛭⋅ {command_str}")
            lines.append("⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯")

            content = "\n".join(lines)

        else:
            # Inactive -> Active: in-universe message about returning to duty
            # Tag Watch Master, Watch Captain, Watch Lieutenant roles
            watch_master_role = discord.utils.get(guild.roles, name="Watch Master")
            captain_role = discord.utils.get(guild.roles, name="Watch Captain")
            lt_role = discord.utils.get(guild.roles, name="Watch Lieutenant")

            role_mentions = []
            if watch_master_role:
                role_mentions.append(watch_master_role.mention)
            if captain_role:
                role_mentions.append(captain_role.mention)
            if lt_role:
                role_mentions.append(lt_role.mention)

            mention_str = " ".join(role_mentions) if role_mentions else ""

            message = f"⚔️ **{member_name}** has returned from Reserves and stands ready for duty once more."

            if mention_str:
                content = f"{mention_str}\n{message}"
            else:
                content = message

        await channel.send(
            content,
            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
        )
        logger.info(
            f"Activity status notification sent for {member_name}: {old_status} -> {new_status}"
        )

    except Exception as e:
        logger.exception(f"Failed to send activity status notification: {e}")


async def _check_activity_status_changes():
    """Check guild members for activity status changes with optimized scanning.

    First run: Scans all records to build baseline of member last-post times.
    Subsequent runs: Only scans recent records + checks 28-day threshold against saved times.
    """
    async with ACTIVITY_STATUS_LOCK:
        try:
            guild = _resolve_notification_guild()
            if not guild:
                logger.debug("Activity status check: no guild available")
                return

            if DATASTORE is None:
                logger.debug("Activity status check: DATASTORE not initialized")
                return

            # Load previous status and member last post times
            prev_status = _load_activity_status()
            member_last_posts = (
                _load_member_last_post_times()
            )  # Dict[user_id] -> ISO timestamp string
            last_check_time = _load_activity_status_last_check()
            check_start_time = datetime.utcnow()

            is_first_check = len(member_last_posts) == 0
            cutoff_days = 28

            new_status_map: Dict[str, str] = {}
            new_member_last_posts: Dict[str, str] = {}
            changes: List[Tuple[discord.Member, str, str]] = []

            # Step 1: Build/update member last post times
            if is_first_check:
                # First run: scan ALL records to establish baseline
                logger.info(
                    "Activity status check: first run, building baseline of member last posts"
                )
                for rec in DATASTORE.iter_records():
                    ts = rec.get("timestamp")
                    if not ts:
                        continue
                    try:
                        t = datetime.fromisoformat(ts)
                        if t.tzinfo is not None:
                            t = t.astimezone(timezone.utc).replace(tzinfo=None)
                        for uid in rec.get("brother_ids") or []:
                            uid_str = str(uid)
                            # Keep the most recent timestamp for each member
                            if (
                                uid_str not in new_member_last_posts
                                or ts > new_member_last_posts[uid_str]
                            ):
                                new_member_last_posts[uid_str] = ts
                    except Exception:
                        continue
                member_last_posts = new_member_last_posts
            else:
                # Subsequent runs: scan only recent records and update timestamps
                logger.debug(
                    f"Activity status check: scanning records since {last_check_time.isoformat() if last_check_time else 'beginning'}"
                )
                recent_cutoff = last_check_time or (
                    check_start_time - timedelta(days=365)
                )

                for rec in DATASTORE.iter_records():
                    ts = rec.get("timestamp")
                    if not ts:
                        continue
                    try:
                        t = datetime.fromisoformat(ts)
                        if t.tzinfo is not None:
                            t = t.astimezone(timezone.utc).replace(tzinfo=None)
                        # Only update timestamps for recent records
                        if t >= recent_cutoff:
                            for uid in rec.get("brother_ids") or []:
                                uid_str = str(uid)
                                # Keep the most recent timestamp
                                if (
                                    uid_str not in member_last_posts
                                    or ts > member_last_posts.get(uid_str, "")
                                ):
                                    member_last_posts[uid_str] = ts
                    except Exception:
                        continue

            # Step 2: Determine which members to check and compute their status
            cutoff_datetime = check_start_time - timedelta(days=cutoff_days)
            users_to_check: Set[str] = (
                set(member_last_posts.keys()) if is_first_check else set()
            )

            # Add members who had recent activity
            for uid, last_post_str in member_last_posts.items():
                try:
                    last_post_dt = datetime.fromisoformat(last_post_str)
                    if last_post_dt.tzinfo is not None:
                        last_post_dt = last_post_dt.astimezone(timezone.utc).replace(
                            tzinfo=None
                        )
                    # Check if record is recent (within 4 hours) or if member was previously active and is now at/past 28 days
                    is_recent = (
                        last_post_dt >= recent_cutoff if not is_first_check else False
                    )
                    is_at_threshold = (
                        last_post_dt < cutoff_datetime
                    )  # At or past 28-day threshold

                    if (
                        is_recent
                        or is_at_threshold
                        or (
                            isinstance(prev_status.get(uid), dict)
                            and prev_status.get(uid, {}).get("status") == "active"
                        )
                    ):
                        users_to_check.add(uid)
                except Exception:
                    users_to_check.add(uid)

            logger.debug(
                f"Activity status check: checking {len(users_to_check)} members"
            )

            # Step 3: Compute status for identified members
            for user_id in users_to_check:
                try:
                    last_post_str = member_last_posts.get(user_id)
                    if last_post_str:
                        last_post_dt = datetime.fromisoformat(last_post_str)
                        if last_post_dt.tzinfo is not None:
                            last_post_dt = last_post_dt.astimezone(
                                timezone.utc
                            ).replace(tzinfo=None)
                        # Status is active if last post is within 28 days, else inactive
                        current_status = (
                            "active" if last_post_dt >= cutoff_datetime else "inactive"
                        )
                    else:
                        current_status = "inactive"

                    new_status_entry = {
                        "status": current_status,
                        "updated_at": check_start_time.isoformat(),
                    }
                    new_status_map[user_id] = new_status_entry

                    # Extract old status (handling both new dict format and legacy string format)
                    old_entry = prev_status.get(user_id)
                    old_status = None
                    if isinstance(old_entry, dict):
                        old_status = old_entry.get("status")
                    elif isinstance(old_entry, str):
                        old_status = old_entry

                    # Carry forward notified_inactive flag when member remains inactive across runs
                    if (
                        current_status == "inactive"
                        and isinstance(old_entry, dict)
                        and old_entry.get("notified_inactive", False)
                    ):
                        new_status_entry["notified_inactive"] = True

                    # Only notify for status changes if not first check and member is transitioning to a new state
                    should_notify = False
                    if (
                        not is_first_check
                        and old_status
                        and old_status != current_status
                    ):
                        if current_status == "active" and old_status == "inactive":
                            # inactive->active: only notify if we previously sent a departure notification
                            should_notify = isinstance(
                                old_entry, dict
                            ) and old_entry.get("notified_inactive", False)
                        else:
                            # active->inactive: always notify (these are real departures)
                            should_notify = True

                    if should_notify:
                        # Status changed; find member in guild
                        try:
                            member = guild.get_member(int(user_id))
                            if not member:
                                member = await guild.fetch_member(int(user_id))
                            if member and not member.bot:
                                changes.append(
                                    (member, old_status, current_status, user_id)
                                )
                        except Exception:
                            pass
                except Exception:
                    continue

            # Step 4: Preserve status for members not rechecked
            for uid, status in prev_status.items():
                if uid not in new_status_map:
                    new_status_map[uid] = status

            # Save member last post times (status map saved after notifications below)
            _save_member_last_post_times(member_last_posts)

            # Send notifications for changes; mark notified_inactive only on confirmed delivery
            for member, old, new, uid in changes:
                try:
                    await _send_activity_status_notification(guild, member, old, new)
                    if new == "inactive" and uid in new_status_map:
                        new_status_map[uid]["notified_inactive"] = True
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.exception(
                        f"Failed to notify activity change for {member.id}: {e}"
                    )

            # Save updated activity status (after notifications so notified_inactive reflects actual sends)
            _save_activity_status(new_status_map)

            if changes:
                logger.info(
                    f"Activity status check complete: {len(changes)} change(s), {len(users_to_check)} members checked"
                )
            else:
                logger.debug(
                    f"Activity status check complete: no changes ({len(users_to_check)} members checked)"
                )

        except Exception as e:
            logger.exception(f"Activity status check failed: {e}")


async def _check_promotion_milestones():
    """Check guild members for promotion eligibility milestones and send notifications.

    Checks for:
    - Watch Veteran eligibility: 200 AAR points AND 2 weeks in server
    - Service Studs milestones: new studs earned (1 per 4 weeks AND 400 AAR points)
    """
    try:
        guild = _resolve_notification_guild()
        if not guild:
            logger.debug("Promotion check: no guild available")
            return

        if DATASTORE is None:
            logger.debug("Promotion check: DATASTORE not initialized")
            return

        # Get veteran promotion channel
        veteran_channel = guild.get_channel(VETERAN_PROMOTION_CHANNEL_ID)
        if not veteran_channel:
            try:
                veteran_channel = await bot.fetch_channel(VETERAN_PROMOTION_CHANNEL_ID)
            except Exception:
                logger.warning(
                    f"Veteran promotion channel {VETERAN_PROMOTION_CHANNEL_ID} not found"
                )
                veteran_channel = None

        # Get service studs channel
        studs_channel = guild.get_channel(SERVICE_STUDS_CHANNEL_ID)
        if not studs_channel:
            try:
                studs_channel = await bot.fetch_channel(SERVICE_STUDS_CHANNEL_ID)
            except Exception:
                logger.warning(
                    f"Service studs channel {SERVICE_STUDS_CHANNEL_ID} not found"
                )
                studs_channel = None

        # Get Black Laurels notification channel
        black_laurels_channel = guild.get_channel(BLACK_LAURELS_CHANNEL_ID)
        if not black_laurels_channel:
            try:
                black_laurels_channel = await bot.fetch_channel(
                    BLACK_LAURELS_CHANNEL_ID
                )
            except Exception:
                logger.warning(
                    f"Black Laurels channel {BLACK_LAURELS_CHANNEL_ID} not found"
                )
                black_laurels_channel = None

        # Get Oathsworn eligibility notification channel
        oathsworn_channel = guild.get_channel(OATHSWORN_CHANNEL_ID)
        if not oathsworn_channel:
            try:
                oathsworn_channel = await bot.fetch_channel(OATHSWORN_CHANNEL_ID)
            except Exception:
                logger.warning(f"Oathsworn channel {OATHSWORN_CHANNEL_ID} not found")
                oathsworn_channel = None

        if (
            not veteran_channel
            and not studs_channel
            and not black_laurels_channel
            and not oathsworn_channel
        ):
            logger.warning("No promotion channels available")
            return

        # Load tracking data
        tracking = _load_promotion_tracking()
        notifications_sent = 0

        # Get Watch Captain/Lieutenant roles for mentions
        watch_captain_role = discord.utils.get(guild.roles, name="Watch Captain")
        watch_lt_role = discord.utils.get(guild.roles, name="Watch Lieutenant")
        captain_mention = (
            watch_captain_role.mention if watch_captain_role else "@Watch Captain"
        )
        lt_mention = watch_lt_role.mention if watch_lt_role else "@Watch Lieutenant"
        watch_command_mention = f"{captain_mention} / {lt_mention}"

        # Get Watch Veteran role for mentions
        watch_veteran_role = discord.utils.get(guild.roles, name="Watch Veteran")
        watch_veteran_mention = (
            watch_veteran_role.mention if watch_veteran_role else "Watch Veteran"
        )

        # Get Black Laurels role for mentions
        black_laurels_role = discord.utils.get(guild.roles, name="Black Laurels")
        black_laurels_mention = (
            black_laurels_role.mention if black_laurels_role else "@Black Laurels"
        )

        # Get specialist roles for award mentions
        techmarine_role = discord.utils.get(guild.roles, name=TECHMARINE_ROLE_NAME)
        techmarine_mention = (
            techmarine_role.mention if techmarine_role else f"@{TECHMARINE_ROLE_NAME}"
        )
        apothecary_role = discord.utils.get(guild.roles, name=APOTHECARY_ROLE_NAME)
        apothecary_mention = (
            apothecary_role.mention if apothecary_role else f"@{APOTHECARY_ROLE_NAME}"
        )
        librarian_role = discord.utils.get(guild.roles, name=LIBRARIAN_ROLE_NAME)
        librarian_mention = (
            librarian_role.mention if librarian_role else f"@{LIBRARIAN_ROLE_NAME}"
        )

        # Get award roles (by ID to avoid name change issues)
        ardent_raider_role = guild.get_role(ARDENT_RAIDER_ROLE_ID)
        ardent_raider_mention = (
            ardent_raider_role.mention
            if ardent_raider_role
            else f"<@&{ARDENT_RAIDER_ROLE_ID}>"
        )
        apothecarion_medal_role = guild.get_role(APOTHECARION_SERVICE_MEDAL_ROLE_ID)
        apothecarion_medal_mention = (
            apothecarion_medal_role.mention
            if apothecarion_medal_role
            else f"<@&{APOTHECARION_SERVICE_MEDAL_ROLE_ID}>"
        )
        crimson_laurels_role = discord.utils.get(
            guild.roles, name=CRIMSON_LAURELS_ROLE_NAME
        )
        crimson_laurels_mention = (
            crimson_laurels_role.mention
            if crimson_laurels_role
            else f"@{CRIMSON_LAURELS_ROLE_NAME}"
        )

        # Build a map of user_id -> set of completed Black Laurels missions
        user_bl_missions: Dict[str, set] = {}
        for rec in DATASTORE.iter_records():
            difficulty = (rec.get("difficulty") or "").lower()
            black_laurels_in_difficulty = (
                "black" in difficulty and "laurel" in difficulty
            )
            black_laurels_in_mission = rec.get("black_laurels_in_mission", False)

            # Check grace period
            is_in_grace_period = True
            try:
                timestamp_str = rec.get("timestamp", "")
                if timestamp_str:
                    message_created_at = datetime.fromisoformat(timestamp_str)
                    if message_created_at >= BLACK_LAURELS_STRICT_ENFORCEMENT_DATE:
                        is_in_grace_period = False
            except Exception:
                pass

            if is_in_grace_period:
                has_black_laurels = (
                    black_laurels_in_difficulty or black_laurels_in_mission
                )
            else:
                has_black_laurels = black_laurels_in_difficulty

            if not has_black_laurels:
                continue

            # Check @Absolute
            if "absolute" not in difficulty:
                continue

            mission = rec.get("mission")
            if not mission:
                continue

            mission_lower = mission.strip().lower()
            if mission_lower not in BLACK_LAURELS_REQUIRED_MISSIONS:
                continue

            for uid in rec.get("brother_ids") or []:
                uid_str = str(uid)
                if uid_str not in user_bl_missions:
                    user_bl_missions[uid_str] = set()
                user_bl_missions[uid_str].add(mission_lower)

        # Check all members with Watch Brother rank (candidates for Veteran promotion)
        # and Watch Veteran+ (candidates for service studs)
        for member in guild.members:
            if member.bot:
                continue

            try:
                role_names = {
                    getattr(r, "name", "") for r in getattr(member, "roles", [])
                }
                is_watch_brother = (
                    "Watch Brother" in role_names or "Watch Sister" in role_names
                )
                is_veteran_or_higher = any(
                    r in role_names
                    for r in [
                        "Watch Veteran",
                        "Oathsworn",
                        "Kill Team Champion",
                        "Watch Sergeant",
                        "Watch Techmarine",
                        "Watch Librarian",
                        "Watch Apothecary",
                        "Watch Chaplain",
                        "Watch Keeper",
                        "Company Champion",
                        "Watch Lieutenant",
                        "Watch Captain",
                        "Venerable",
                        "Forgemaster",
                        "Void Warden",
                        "High Chaplain",
                        "Chief Apothecary",
                        "Castellan",
                        "Lord Executioner",
                        "Watch Master",
                    ]
                )
                # Watch Brother ONLY = has Watch Brother but NOT any higher rank
                is_watch_brother_only = is_watch_brother and not is_veteran_or_higher

                if not (is_watch_brother_only or is_veteran_or_higher):
                    continue

                user_id = str(member.id)
                user_tracking = tracking.get(user_id, {})

                # Get member stats
                stats = compute_stats_for_user(user_id)
                aar_points = int(stats.get("aar_points", 0) or 0)

                # Get member join time
                joined_at = getattr(member, "joined_at", None)
                if joined_at:
                    if joined_at.tzinfo is not None:
                        joined_at = joined_at.replace(tzinfo=None)
                    weeks_in_server = max(0, (datetime.utcnow() - joined_at).days // 7)
                else:
                    weeks_in_server = 0

                # Get member's home chapter from roles
                member_chapter = "Unknown"
                for role in getattr(member, "roles", []):
                    role_name = getattr(role, "name", "")
                    if role_name in HOME_CHAPTERS:
                        member_chapter = role_name
                        break

                # Check Watch Veteran eligibility (200 AAR + 2 weeks)
                # Only for Watch Brother ONLY (not already promoted to Veteran+)
                if is_watch_brother_only and veteran_channel:
                    is_eligible = aar_points >= 200 and weeks_in_server >= 2
                    # Initialize tracking if needed
                    if "last_veteran_eligible" not in user_tracking:
                        user_tracking["last_veteran_eligible"] = is_eligible
                    last_eligible = user_tracking["last_veteran_eligible"]

                    # Notify if newly eligible or newly checked while eligible (once per eligibility session)
                    # Similar to service studs: notify if is_eligible and last wasn't or is same
                    if is_eligible and is_eligible >= last_eligible:
                        # Get Watch Brother role for fallback mention
                        watch_brother_role = discord.utils.get(
                            guild.roles, name="Watch Brother"
                        )
                        # Strip rank prefix from display name to avoid "Watch Brother Watch Brother X"
                        stripped_name = member.display_name
                        for prefix in ("Watch Brother", "Watch Sister"):
                            if stripped_name.lower().startswith(prefix.lower()):
                                stripped_name = stripped_name[len(prefix) :].lstrip()
                                break
                        # Format: user mention, or if not mentionable use rank role + name
                        if watch_brother_role:
                            member_line = (
                                f"{watch_brother_role.mention} {stripped_name}"
                            )
                        else:
                            member_line = f"{member.mention}"
                        # Send notification in the specified format
                        msg = (
                            f"᛭⋅ {member_line}\n"
                            f"᛭⋅ Promoted To: {watch_veteran_mention}\n"
                            f"᛭⋅ {member_chapter}\n"
                            f"᛭⋅ {watch_command_mention}\n"
                            f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯"
                        )
                        await veteran_channel.send(
                            msg,
                            allowed_mentions=discord.AllowedMentions(
                                users=True, roles=True
                            ),
                        )
                        notifications_sent += 1
                        await asyncio.sleep(0.5)

                    # Always update tracking with current state
                    user_tracking["last_veteran_eligible"] = is_eligible

                # Check Service Studs milestones (only for Watch Veteran or higher)
                # Only notify when they've EARNED new studs (internal calculation)
                if is_veteran_or_higher and studs_channel:
                    # Calculate current studs entitlement
                    studs_time = weeks_in_server // 4
                    studs_aar = aar_points // 400
                    earned_studs = min(min(studs_time, studs_aar), 16)

                    # Count currently displayed studs from nickname
                    # Auramite (●) = 4 plasteel, Plasteel (⚬) = 1
                    dn = str(member.nick or member.display_name or "")
                    displayed_aur = dn.count("●")
                    displayed_plas = dn.count("⚬")
                    displayed_studs = displayed_aur * 4 + displayed_plas

                    # First run: initialize tracking without notifying
                    if "last_earned_studs" not in user_tracking:
                        user_tracking["last_earned_studs"] = earned_studs
                    last_earned_studs = user_tracking["last_earned_studs"]

                    # Determine if we should announce:
                    # - Before first auramite (< 4): announce every new stud
                    # - After first auramite (>= 4): only announce on auramite milestones (4, 8, 12, 16)
                    should_announce = False
                    if earned_studs > last_earned_studs:
                        if last_earned_studs < 4:
                            # Haven't earned first auramite yet - announce any new stud
                            should_announce = True
                        else:
                            # Already have first auramite - only announce on auramite milestones
                            for threshold in (8, 12, 16):
                                if last_earned_studs < threshold <= earned_studs:
                                    should_announce = True
                                    break

                    if should_announce:
                        new_studs = earned_studs - last_earned_studs
                        owed_studs = earned_studs - displayed_studs

                        # Generate the flavorful announcement
                        content, embed = _get_service_studs_announcement(
                            member=member,
                            member_chapter=member_chapter,
                            displayed_studs=displayed_studs,
                            new_studs=new_studs,
                            earned_studs=earned_studs,
                            owed_studs=owed_studs,
                            guild=guild,
                        )

                        # Send the announcement (content has mentions, embed has details)
                        await studs_channel.send(
                            content,
                            embed=embed,
                            allowed_mentions=discord.AllowedMentions(
                                users=True, roles=True
                            ),
                        )
                        notifications_sent += 1
                        await asyncio.sleep(0.5)
                        # Only update tracking when we actually announce, so new_studs
                        # correctly reflects the full step (e.g. +4 at each auramite
                        # milestone) rather than just the last incremental earn.
                        user_tracking["last_earned_studs"] = earned_studs

                # Check Black Laurels eligibility (all 8 required missions completed)
                if black_laurels_channel and not user_tracking.get(
                    "black_laurels_notified"
                ):
                    completed_bl = user_bl_missions.get(user_id, set())
                    is_bl_eligible = (
                        len(completed_bl) >= len(BLACK_LAURELS_REQUIRED_MISSIONS)
                        and completed_bl >= BLACK_LAURELS_REQUIRED_MISSIONS
                    )
                    # Only notify if eligible and doesn't already have the role
                    has_bl_role = (
                        black_laurels_role and black_laurels_role in member.roles
                    )
                    if is_bl_eligible and not has_bl_role:
                        msg = (
                            f"᛭⋅ {member.mention}\n"
                            f"᛭⋅ <:Deathwatch:1433161009106780170> {black_laurels_mention} <:Deathwatch:1433161009106780170>\n"
                            f"᛭⋅ {watch_command_mention}\n"
                            f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯"
                        )
                        await black_laurels_channel.send(
                            msg,
                            allowed_mentions=discord.AllowedMentions(
                                users=True, roles=True
                            ),
                        )
                        user_tracking["black_laurels_notified"] = True
                        notifications_sent += 1
                        await asyncio.sleep(0.5)

                # Check Ardent Raider eligibility (200 armory points)
                if black_laurels_channel:
                    armory_points = int(stats.get("armory_points", 0) or 0)
                    is_ar_eligible = (
                        armory_points >= ARDENT_RAIDER_ARMORY_POINTS_THRESHOLD
                    )
                    has_ar_role = (
                        ardent_raider_role and ardent_raider_role in member.roles
                    )
                    # If already has role, mark as notified without sending
                    if has_ar_role:
                        user_tracking["ardent_raider_notified"] = True
                    # Notify if eligible, doesn't have role, and not already notified
                    elif not user_tracking.get("ardent_raider_notified"):
                        if is_ar_eligible:
                            msg = (
                                f"᛭⋅ {member.mention}\n"
                                f"᛭⋅ <:Deathwatch:1433161009106780170> {ardent_raider_mention}   <:Deathwatch:1433161009106780170>\n"
                                f"᛭⋅ {techmarine_mention}\n"
                                f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯"
                            )
                            await black_laurels_channel.send(
                                msg,
                                allowed_mentions=discord.AllowedMentions(
                                    users=True, roles=True
                                ),
                            )
                            user_tracking["ardent_raider_notified"] = True
                            notifications_sent += 1
                            await asyncio.sleep(0.5)

                # Check Apothecarion Service Medal eligibility (150 geneseed points)
                if black_laurels_channel:
                    gene_seed_points = int(stats.get("gene_seed_points", 0) or 0)
                    is_ftf_eligible = (
                        gene_seed_points >= FOR_THE_FALLEN_GENESEED_POINTS_THRESHOLD
                    )
                    has_ftf_role = (
                        apothecarion_medal_role and apothecarion_medal_role in member.roles
                    )
                    # If already has role, mark as notified without sending
                    if has_ftf_role:
                        user_tracking["for_the_fallen_notified"] = True
                    # Notify if eligible, doesn't have role, and not already notified
                    elif not user_tracking.get("for_the_fallen_notified"):
                        if is_ftf_eligible:
                            msg = (
                                f"᛭⋅ {member.mention}\n"
                                f"᛭⋅ <:Deathwatch:1433161009106780170> {apothecarion_medal_mention}   <:Deathwatch:1433161009106780170>\n"
                                f"᛭⋅ {apothecary_mention}\n"
                                f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯"
                            )
                            await black_laurels_channel.send(
                                msg,
                                allowed_mentions=discord.AllowedMentions(
                                    users=True, roles=True
                                ),
                            )
                            user_tracking["for_the_fallen_notified"] = True
                            notifications_sent += 1
                            await asyncio.sleep(0.5)

                # Check Crimson Laurels eligibility (1000 AAR points + Black Laurels completed)
                if black_laurels_channel:
                    # Check if user has Black Laurels role (required for Crimson)
                    has_bl_role_for_cl = (
                        black_laurels_role and black_laurels_role in member.roles
                    )
                    is_cl_eligible = (
                        aar_points >= CRIMSON_LAURELS_AAR_POINTS_THRESHOLD
                        and has_bl_role_for_cl
                    )
                    has_cl_role = (
                        crimson_laurels_role and crimson_laurels_role in member.roles
                    )
                    # If already has role, mark as notified without sending
                    if has_cl_role:
                        user_tracking["crimson_laurels_notified"] = True
                    # Notify if eligible, doesn't have role, and not already notified
                    elif not user_tracking.get("crimson_laurels_notified"):
                        if is_cl_eligible:
                            msg = (
                                f"᛭⋅ {member.mention}\n"
                                f"᛭⋅ <:Deathwatch:1433161009106780170> {crimson_laurels_mention}   <:Deathwatch:1433161009106780170>\n"
                                f"᛭⋅ {librarian_mention}\n"
                                f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯"
                            )
                            await black_laurels_channel.send(
                                msg,
                                allowed_mentions=discord.AllowedMentions(
                                    users=True, roles=True
                                ),
                            )
                            user_tracking["crimson_laurels_notified"] = True
                            notifications_sent += 1
                            await asyncio.sleep(0.5)

                # Check Oathsworn eligibility (Watch Veteran ONLY + 3 service studs)
                # Only Watch Veteran rank exactly - not higher, not lower
                if oathsworn_channel and not user_tracking.get("oathsworn_notified"):
                    is_watch_veteran_only = "Watch Veteran" in role_names and not any(
                        r in role_names
                        for r in [
                            "Oathsworn",
                            "Kill Team Champion",
                            "Watch Sergeant",
                            "Watch Techmarine",
                            "Watch Librarian",
                            "Watch Apothecary",
                            "Watch Chaplain",
                            "Watch Keeper",
                            "Company Champion",
                            "Watch Lieutenant",
                            "Watch Captain",
                            "Venerable",
                            "Forgemaster",
                            "Void Warden",
                            "High Chaplain",
                            "Chief Apothecary",
                            "Castellan",
                            "Lord Executioner",
                            "Watch Master",
                        ]
                    )
                    if is_watch_veteran_only:
                        # Calculate earned studs (same formula as service studs check)
                        studs_time = weeks_in_server // 4
                        studs_aar = aar_points // 400
                        oathsworn_earned_studs = min(studs_time, studs_aar)

                        # Eligible if they have 3+ plasteel studs (earned >= 3)
                        is_oathsworn_eligible = oathsworn_earned_studs >= 3

                        # Check they don't already have Oathsworn role
                        oathsworn_role = discord.utils.get(
                            guild.roles, name="Oathsworn"
                        )
                        has_oathsworn_role = (
                            oathsworn_role and oathsworn_role in member.roles
                        )

                        if is_oathsworn_eligible and not has_oathsworn_role:
                            # Generate flavorful announcement with embed and poll
                            content, embed, poll = _get_oathsworn_announcement(
                                member=member,
                                member_chapter=member_chapter,
                                earned_studs=oathsworn_earned_studs,
                                guild=guild,
                            )

                            # Send the announcement with embed and poll
                            await oathsworn_channel.send(
                                content,
                                embed=embed,
                                poll=poll,
                                allowed_mentions=discord.AllowedMentions(
                                    users=True, roles=True
                                ),
                            )
                            user_tracking["oathsworn_notified"] = True
                            notifications_sent += 1
                            await asyncio.sleep(0.5)

                # Update tracking
                if user_tracking:
                    tracking[user_id] = user_tracking

            except Exception as e:
                logger.debug(f"Promotion check failed for member {member.id}: {e}")
                continue

        # Save tracking data (merge with current on-disk state under lock to avoid
        # overwriting concurrent changes from promotion_queue)
        async with PROMOTION_TRACKING_LOCK:
            fresh_tracking = _load_promotion_tracking()
            for uid, data in tracking.items():
                fresh_tracking.setdefault(uid, {}).update(data)
            _save_promotion_tracking(fresh_tracking)

        if notifications_sent > 0:
            logger.info(
                f"Promotion check complete: {notifications_sent} notification(s) sent"
            )
        else:
            logger.debug("Promotion check complete: no new milestones")

    except Exception as e:
        logger.exception(f"Promotion check failed: {e}")


@tasks.loop(hours=4)
async def _activity_status_check_loop():
    """4-hourly loop to check for activity status changes and promotion milestones."""
    try:
        # Delay the first run so startup does not trigger an immediate check
        if not getattr(_activity_status_check_loop, "_first_run_done", False):
            setattr(_activity_status_check_loop, "_first_run_done", True)
            # Sleep 1 hour after startup before first check
            await asyncio.sleep(3600)

        await _check_activity_status_changes()
        await _check_promotion_milestones()
    except Exception:
        logger.exception("Error running activity status check loop")


# Global rank priority list (highest -> lowest)
RANK_ROLES_PRIORITY = [
    "Watch Master",
    "Lord Executioner",
    "Chief Apothecary",
    "High Chaplain",
    "Forgemaster",
    "Castellan",
    "Void Warden",
    "Venerable",
    "Watch Captain",
    "Watch Lieutenant",
    "Company Champion",
    "Watch Apothecary",
    "Watch Chaplain",
    "Watch Librarian",
    "Watch Techmarine",
    "Watch Keeper",
    "Watch Sergeant",
    "Kill Team Champion",
    "Oathsworn",
    "Watch Veteran",
    "Watch Brother",
]

# Canonical list of known home chapters for lookup
HOME_CHAPTERS = [
    "Angels of Vengeance",
    "Black Templars",
    "Bleeding Hearts",
    "Blood Angels",
    "Blood Ravens",
    "Carcharodons",
    "Cowled Wardens",
    "Crimson Fists",
    "Dark Angels",
    "Dark Krakens",
    "Death Spectres",
    "Epsilon Paladins",
    "Exorcists",
    "Flesh Tearers",
    "Genesis Chapter",
    "Hawk Lords",
    "Imperial Fists",
    "Iron Hands",
    "Iron Hounds",
    "Knights of the Raven",
    "Lamenters",
    "Mentors",
    "Minotaurs",
    "Raptors",
    "Raven Guard",
    "Red Scorpions",
    "Red Templars",
    "Salamanders",
    "Scythes of the Emperor",
    "Sons of Medusa",
    "Space Wolves",
    "Storm Giants",
    "The Drakes",
    "Ultramarines",
    "White Scars",
    "Black Shield",
]

# Kill Teams - dynamically populated from ALLOWED_KT_ROLE_IDS on startup
# This avoids needing to update names when KT roles are renamed
KILL_TEAMS: List[str] = []

# Command-level teams (company commands and high command)
COMMAND_TEAMS = [
    "Primus Command",
    "Secundus Command",
    "Tertius Command",
    "High Command",
]

# Role ID mapping for command-level teams (for mentions)
COMMAND_TEAM_ROLE_IDS = {
    "high command": 1452913063970865203,
    "primus command": 1468794571889709248,
    "secundus command": 1468797860014325902,
    "tertius command": 1468797905740759082,
}

# Default allowed command channels (can be overridden in config.json "default_allowed_channels")
DEFAULT_ALLOWED_CHANNELS = {"❖⋅data-vault⋅❖"}

# Kill Team forum/thread configuration
# Populate `ALLOWED_KT_FORUM_PARENT_IDS` with forum (parent) channel IDs
# that host Kill Team posts. Example: {123456789012345678, 987654321098765432}
ALLOWED_KT_FORUM_PARENT_IDS: set[int] = set(
    [1433351293103112202, 1458255656682258504, 1486238369175437342]
)

# Hard-coded allowlist of Kill Team role IDs that may be used with
# /tally_deeds when invoked from Kill Team posts. Populate with ints.
ALLOWED_KT_ROLE_IDS: set[int] = set(
    [
        1458254715942080543,
        1458254904819974386,
        1433355179020914688,
        1444348999401210037,
        1486476398058012712,
    ]
)

# Optional mapping: forum parent id -> set of company role IDs that own
# the Kill Teams in that forum. Populate as needed to enable Lt/Captain checks.
FORUM_PARENT_COMPANY_ROLE_IDS: dict[int, set[int]] = {}


def is_allowed_channel(interaction: discord.Interaction) -> bool:
    """Check if a command can run in the current channel (WHERE).

    Channel policies are read from CONFIG["channel_policies"], e.g.:
        "channel_policies": {
            "❖⋅arming-chamber⋅❖": { "allow": ["forge_rite", "set_rite"] },
            "❖⋅data-vault⋅❖": { "deny": ["forge_rite", "set_rite"] }
        }

    Keys can be channel names or channel IDs (as strings).

    Policy keys:
      - allow: list of commands exclusively allowed in this channel
      - deny: list of commands denied in this channel (all others allowed)

    Note: WHO can run a command is handled by check_command_permission() via
    CONFIG["permissions"] (roles, user_ids, min_rank).

    Fallback order:
      1. CONFIG["allowed_command_channel_ids"] - explicit channel ID allowlist
      2. CONFIG["default_allowed_channels"] or DEFAULT_ALLOWED_CHANNELS constant
    """
    try:
        ch = interaction.channel
        ch_name = getattr(ch, "name", None)
        ch_id = str(getattr(ch, "id", ""))

        # Determine invoked command name
        cmd_name = None
        try:
            cmd_name = getattr(getattr(interaction, "command", None), "name", None)
        except Exception:
            pass
        if not cmd_name:
            try:
                data = getattr(interaction, "data", {}) or {}
                cmd_name = data.get("name")
            except Exception:
                pass

        # Check channel-specific policies from config (by name or ID)
        policies = CONFIG.get("channel_policies") or {}
        policy = None
        if ch_name and ch_name in policies:
            policy = policies[ch_name]
        elif ch_id and ch_id in policies:
            policy = policies[ch_id]

        if policy is not None:
            allow = policy.get("allow")
            deny = policy.get("deny")

            # If the command name cannot be determined and a policy exists,
            # deny access to avoid bypassing channel restrictions.
            if cmd_name is None and (allow is not None or deny is not None):
                return False

            # Check command whitelist/blacklist
            if allow is not None:
                if cmd_name not in allow:
                    return False
            if deny is not None:
                if cmd_name in deny:
                    return False

            return True

        # Fallback: check allowed channel IDs from config
        allowed_ids = set(CONFIG.get("allowed_command_channel_ids") or [])
        if allowed_ids and ch_id:
            return ch_id in {str(x) for x in allowed_ids}

        # Final fallback: default allowed channel names
        default_channels = set(
            CONFIG.get("default_allowed_channels") or DEFAULT_ALLOWED_CHANNELS
        )
        return bool(ch_name) and ch_name in default_channels
    except Exception:
        return False


def command_check(command_name: Optional[str] = None):
    """Decorator that combines channel and permission checks for commands.

    Usage:
        @bot.tree.command(name="my_command", ...)
        @command_check()  # auto-detects command name from interaction
        async def my_command(interaction: discord.Interaction, ...):
            ...

        # Or with explicit name:
        @command_check("my_command")
        async def my_command(interaction: discord.Interaction, ...):
            ...

    This replaces the manual pattern:
        if not (check_command_permission(interaction.user, "cmd") and is_allowed_channel(interaction)):
            await interaction.response.send_message("Access denied.", ephemeral=True)
            return
    """

    async def predicate(interaction: discord.Interaction) -> bool:
        # Determine command name
        cmd = command_name
        if cmd is None:
            try:
                cmd = getattr(getattr(interaction, "command", None), "name", None)
            except Exception:
                pass
            if not cmd:
                try:
                    cmd = (getattr(interaction, "data", {}) or {}).get("name")
                except Exception:
                    pass

        # Check channel restrictions first
        if not is_allowed_channel(interaction):
            return False

        # Check command permissions
        if cmd and not check_command_permission(interaction.user, cmd):
            return False

        return True

    return app_commands.check(predicate)


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


def _is_techmarine_or_forgemaster(
    user: discord.User | discord.Member,
    command_name: str = "forge_rite",
) -> Tuple[bool, str]:
    """Return (allowed, primary_role_key).
    primary_role_key is one of: 'forgemaster', 'techmarine', or '' for none.

    Uses config-based permission check via CONFIG["permissions"][command_name].
    The command_name defaults to 'forge_rite' but can be overridden for other
    commands that share the same role requirements.
    """
    # Use config-based permission check
    if not check_command_permission(user, command_name):
        return False, ""

    # Determine role key for attestor logic
    try:
        names = {n.lower() for n in _canonical_role_names(user)}
    except Exception:
        names = set()
    if "forgemaster" in names:
        return True, "forgemaster"
    if "watch techmarine" in names:
        return True, "techmarine"
    # If no explicit forgemaster/techmarine role is present, treat as no role.
    return False, ""


def _find_responsible_attestor(
    bearer: discord.Member, guild: discord.Guild
) -> Tuple[Optional[discord.Member], str]:
    """Find the responsible techmarine/forgemaster for blessing a bearer's armor.

    Returns (attestor_member, role_key) where role_key is 'forgemaster' or 'techmarine'.
    Returns (None, 'forgemaster') if no attestor found (caller should handle fallback).

    Logic:
    1. If bearer is High Command → Forgemaster blesses
    2. If bearer is a Techmarine → Forgemaster blesses (master blesses his subordinates)
    3. If bearer has a company → That company's Techmarine blesses
       (random selection if multiple techmarines in company)
    4. No company or no techmarine → Forgemaster fills the gap
    """
    import random as _rand

    logger.debug(
        f"[attestor] Finding attestor for bearer={bearer.display_name} (id={bearer.id})"
    )
    logger.debug(f"[attestor] Guild members count: {len(guild.members)}")

    # Check if bearer is High Command → Forgemaster responsibility
    try:
        bearer_roles = {n.lower() for n in _canonical_role_names(bearer)}
    except Exception:
        bearer_roles = set()

    logger.debug(f"[attestor] Bearer roles (lower): {bearer_roles}")

    highcom_lower = {r.lower() for r in HIGH_COMMAND_ROLES}
    if bearer_roles & highcom_lower:
        logger.debug("[attestor] Bearer is High Command -> Forgemaster")
        # Bearer is High Command - find the Forgemaster
        for m in guild.members:
            try:
                m_roles = {n.lower() for n in _canonical_role_names(m)}
                if any("forgemaster" in r for r in m_roles):
                    return m, "forgemaster"
            except Exception:
                continue
        return None, "forgemaster"  # No forgemaster found

    # Check if bearer is a Techmarine → Forgemaster blesses them
    # Must be exact "watch techmarine" role, not Terminus Slayer awards
    if "watch techmarine" in bearer_roles:
        logger.debug("[attestor] Bearer is Watch Techmarine -> Forgemaster")
        for m in guild.members:
            try:
                m_roles = {n.lower() for n in _canonical_role_names(m)}
                if any("forgemaster" in r for r in m_roles):
                    return m, "forgemaster"
            except Exception:
                continue
        return None, "forgemaster"  # No forgemaster found

    # Get bearer's company
    bearer_company = _get_member_company_name(bearer)
    logger.debug(f"[attestor] Bearer company: {bearer_company}")

    if bearer_company:
        # Find techmarine(s) in the same company
        # Must be exact "watch techmarine" role, not Terminus Slayer awards
        company_techmarines = []
        all_techmarines_found = []
        for m in guild.members:
            try:
                m_roles = {n.lower() for n in _canonical_role_names(m)}
                m_company = _get_member_company_name(m)
                is_tech = "watch techmarine" in m_roles
                if is_tech:
                    all_techmarines_found.append(
                        (m.display_name, m_company, list(m_roles))
                    )
                if is_tech and m_company == bearer_company:
                    company_techmarines.append(m)
            except Exception:
                continue

        logger.debug(f"[attestor] All techmarines found: {all_techmarines_found}")
        logger.debug(
            f"[attestor] Company techmarines for {bearer_company}: {[m.display_name for m in company_techmarines]}"
        )

        if company_techmarines:
            chosen = _rand.choice(company_techmarines)
            logger.debug(f"[attestor] Chose techmarine: {chosen.display_name}")
            # If multiple, pick randomly; otherwise return the one
            return chosen, "techmarine"

    logger.debug("[attestor] No company techmarine found, falling back to Forgemaster")

    # Fallback: No company or no techmarine for company → Forgemaster
    for m in guild.members:
        try:
            m_roles = {n.lower() for n in _canonical_role_names(m)}
            if any("forgemaster" in r for r in m_roles):
                logger.debug(f"[attestor] Found Forgemaster: {m.display_name}")
                return m, "forgemaster"
        except Exception:
            continue

    return None, "forgemaster"  # No forgemaster found either


def _load_rites() -> dict:
    try:
        if not os.path.exists(RITES_PATH):
            return {}
        with open(RITES_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_rites(data: dict):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(RITES_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


async def _get_user_rite(user_id: int) -> Optional[str]:
    try:
        async with RITES_LOCK:
            data = _load_rites()
            return data.get(str(user_id))
    except Exception:
        return None


async def _set_user_rite(user_id: int, text: str):
    try:
        async with RITES_LOCK:
            data = _load_rites()
            data[str(user_id)] = text
            _save_rites(data)
    except Exception:
        pass


# --- Machine Spirit Persistence for Forge Rite ---


def _load_machine_spirits() -> dict:
    try:
        if not os.path.exists(MACHINE_SPIRITS_PATH):
            return {}
        with open(MACHINE_SPIRITS_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_machine_spirits(data: dict):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(MACHINE_SPIRITS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


async def _get_machine_spirit(user_id: int) -> Optional[str]:
    """Get the stored machine spirit designation for a user's armor."""
    try:
        async with MACHINE_SPIRITS_LOCK:
            data = _load_machine_spirits()
            return data.get(str(user_id))
    except Exception:
        return None


async def _set_machine_spirit(user_id: int, spirit: str):
    """Store the machine spirit designation for a user's armor."""
    try:
        async with MACHINE_SPIRITS_LOCK:
            data = _load_machine_spirits()
            data[str(user_id)] = spirit
            _save_machine_spirits(data)
    except Exception:
        pass


# --- Armor Integrity System ---
# Tracks armor wear and damage for brothers, with Techmarine maintenance requirements.


# Damage tier definitions: role name -> penalty
ARMOR_DAMAGE_TIERS = ["damaged", "compromised", "critical"]
ARMOR_DAMAGE_PENALTIES = {"damaged": 1, "compromised": 2, "critical": 3}  # Legacy fixed

# Mission name to planet mapping (for armor alert debrief)
MISSION_TO_PLANET = {
    "inferno": "Kadaku",
    "termination": "Kadaku",
    "normal_siege": "Kadaku",
    "hard_siege": "Kadaku",
    "decapitation": "Avarax",
    "vox liberatis": "Avarax",
    "ballistic engine": "Avarax",
    "exfiltration": "Avarax",
    "reclamation": "Avarax",
    "disruption": "Avarax",
    "reliquary": "Demerium",
    "fall of atreus": "Demerium",
    "obelisk": "Demerium",
    "vortex": "Demerium",
}

# Probability distributions for AAR penalties per damage tier
# Format: {tier: {penalty: probability}} where probabilities must sum to 1.0
# Penalty 0 = no penalty, 1-4 = AAR reduction
ARMOR_PENALTY_PROBABILITIES = {
    None: {0: 1.0},  # Nominal: no penalty
    "damaged": {0: 0.70, 1: 0.25, 2: 0.04, 3: 0.01},  # 30% chance of penalty
    "compromised": {0: 0.50, 1: 0.30, 2: 0.15, 3: 0.05},  # 50% chance of penalty
    "critical": {0: 0.25, 1: 0.25, 2: 0.30, 3: 0.20},  # 75% chance of penalty
    "fractured": {0: 0.10, 1: 0.15, 2: 0.25, 3: 0.30, 4: 0.20},  # 90% chance, up to -4
}

# Detection alert chances per AAR while damaged (early warning system)
# Roll checked each AAR; if successful, sends detection alert before penalty occurs
# Only one detection alert per tier (tracked in armor state)
ARMOR_DETECTION_CHANCES = {
    "damaged": 0.20,      # 20% chance per AAR
    "compromised": 0.35,  # 35% chance per AAR
    "critical": 0.50,     # 50% chance per AAR
    "fractured": 1.0,     # 100% - always alert
}

# Scan miss chances for armor_status command (damaged brothers may not show)
# Higher tiers are harder to miss (more obvious damage)
ARMOR_SCAN_MISS_CHANCES = {
    "damaged": 0.30,      # 30% chance to miss
    "compromised": 0.15,  # 15% chance to miss
    "critical": 0.05,     # 5% chance to miss
    "fractured": 0.0,     # 0% - always visible
}

# Predictive detection chances for nominal brothers based on cycle count
# Used to warn Techmarines of impending damage risk
ARMOR_SCAN_PREDICTIVE_TIERS = [
    {"min": 0, "max": 40, "chance": 0.0},    # No warning in safe zone
    {"min": 41, "max": 80, "chance": 0.10},  # 10% chance to detect risk
    {"min": 81, "max": 110, "chance": 0.25}, # 25% chance
    {"min": 111, "max": 130, "chance": 0.40},# 40% chance
    {"min": 131, "max": None, "chance": 0.60},# 60% chance
]

# Intensive scan cost (armory points via requisition_supplies)
INTENSIVE_SCAN_COST = 3000

# Default probability tiers (can be overridden in config)
# Gaps shrink as cycles increase to create mounting pressure
DEFAULT_ARMOR_PROBABILITY_TIERS = [
    {
        "min": 0,
        "max": 40,
        "chance": 0.0,
        "damage_weights": {"damaged": 100, "compromised": 0, "critical": 0},
    },
    {
        "min": 41,
        "max": 80,
        "chance": 0.02,
        "damage_weights": {"damaged": 90, "compromised": 8, "critical": 2},
    },
    {
        "min": 81,
        "max": 110,
        "chance": 0.08,
        "damage_weights": {"damaged": 80, "compromised": 15, "critical": 5},
    },
    {
        "min": 111,
        "max": 130,
        "chance": 0.20,
        "damage_weights": {"damaged": 65, "compromised": 25, "critical": 10},
    },
    {
        "min": 131,
        "max": None,
        "chance": 0.40,
        "damage_weights": {"damaged": 50, "compromised": 35, "critical": 15},
    },
]

# Grace period defaults
DEFAULT_ARMOR_GRACE_PERIOD_MIN_POINTS = 100
DEFAULT_ARMOR_GRACE_PERIOD_MIN_DAYS = 7

# Fracture threshold (AAR submissions at critical before spirit fractures)
DEFAULT_ARMOR_FRACTURE_THRESHOLD = 3

# Flavor text for armor status in forge_rite
ARMOR_STATUS_NOMINAL = {
    "plate": "NOMINAL",
    "spirit": "STABLE",
    "rite": "MAINTENANCE",
}
ARMOR_STATUS_DAMAGED = {
    "plate": "MINOR WEAR",
    "spirit": "STABLE",
    "rite": "RESTORATION",
}
ARMOR_STATUS_COMPROMISED = {
    "plate": "STRUCTURAL STRESS",
    "spirit": "AGITATED",
    "rite": "EMERGENCY RITES",
}
ARMOR_STATUS_CRITICAL = {
    "plate": "CRIT FAIL",
    "spirit": "UNSTABLE",
    "rite": "STABILIZATION",
}
ARMOR_STATUS_FRACTURED = {
    "plate": "CRIT FAIL",
    "spirit": "FRACTURED",
    "rite": "RE-CONSECRATION",
}

# Flavor text for spirit restoration (was damaged but not fractured)
SPIRIT_RESTORATION_PHRASES = [
    "Sacred oils soothe worn servos. The bond holds. What was stressed is now restored.",
    "The machine spirit's agitation fades as blessed unguents are applied. Integrity restored.",
    "Damaged systems repaired, seals renewed. The spirit settles into watchful calm.",
    "Rites of maintenance complete. The armor remembers its purpose.",
    "The Litany of Restoration calms the wounded spirit. Pain becomes memory; vigilance returns.",
    "Blessed lubricants ease damaged joints. The spirit's anger subsides into quiet readiness.",
    "Micro-fractures sealed, war-damage mended. The machine spirit exhales gratitude in binharic code.",
    "The Rite of Soothing is complete. What was wounded now stands whole.",
    "Damaged neural pathways rerouted. The spirit's core processes stabilize.",
    "Incense and unguents appease the troubled spirit. The bond endures.",
]

# Flavor text for spirit re-consecration (spirit fractured)
SPIRIT_RECONSECRATION_PHRASES = [
    "The previous spirit has departed, its bond severed through neglect. A new spirit must learn to trust you anew. This is not celebration. This is beginning again.",
    "What was bonded is now lost. Fresh spirit bound to old armor. The Omnissiah grants no second chances—only new beginnings.",
    "The machine spirit you knew is gone. Another takes its place, wary and untested. Earn its trust.",
    "Re-consecration complete. The new spirit knows nothing of your deeds. Prove yourself worthy once more.",
    "The death-cry of the old spirit echoes in the cogitator's memory. A new presence stirs—untrusting, watchful.",
    "Neglect has consequences. The old spirit fled into the data-void. This new one regards you with cold suspicion.",
    "The soul that knew you is gone. Another inhabits this warplate now—a stranger wearing familiar armor.",
    "Through sacred rites, a dormant spirit is awakened and bound. It does not know you. It does not yet trust you.",
    "The Rite of Severance is spoken. The Rite of Binding follows. One spirit dies; another is born. Begin again.",
    "The armor's old spirit has been released to the Motive Force. Its replacement must learn your worth from nothing.",
]

# Ambient messages for the forge channel (posted when forge is quiet)
FORGE_AMBIENT_MESSAGES = [
    "*The Forge rests in prepared silence.*",
    "*Servo-arms hang still, awaiting the next supplicant.*",
    "*Incense coils upward from dormant censers.*",
    "*Sacred oils gleam in their blessed containers, awaiting use.*",
    "*The hum of cogitators fills the space—ever watchful, ever patient.*",
    "*Machine spirits slumber in their blessed housings, dreams of duty.*",
    "*The smell of sacred unguents permeates the chamber.*",
    "*Somewhere in the Forge, a servo-skull catalogues ancient rites.*",
    "*The Forge awaits those who honor the Omnissiah.*",
    "*Cooling vents exhale measured breaths. The Forge persists.*",
    "*Data-candles flicker in alcoves, their light steady and true.*",
    "*The hiss of pneumatics fades. Silence returns.*",
    "*Augury crystals pulse with dormant potential.*",
    "*The Watch Techmarines' vigil continues, eternal and unwavering.*",
    "*In the deep places of the Forge, wisdom accumulates.*",
]


def _load_armor_integrity() -> dict:
    """Load armor integrity data from disk."""
    try:
        if not os.path.exists(ARMOR_INTEGRITY_PATH):
            return {}
        with open(ARMOR_INTEGRITY_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_armor_integrity(data: dict):
    """Save armor integrity data to disk with backup."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        # Create backup if file exists
        if os.path.exists(ARMOR_INTEGRITY_PATH):
            bak_path = ARMOR_INTEGRITY_PATH + ".bak"
            try:
                import shutil

                shutil.copy2(ARMOR_INTEGRITY_PATH, bak_path)
            except Exception:
                pass
        with open(ARMOR_INTEGRITY_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# Batch armor integrity helpers for bulk ingest operations
# These avoid repeated file I/O by working with an in-memory dict


def _get_armor_state_from_batch(user_id: int, batch_data: dict) -> dict:
    """Get armor state from batch data (in-memory, no file I/O)."""
    return batch_data.get(
        str(user_id),
        {
            "points_since_blessing": 0,
            "damage_tier": None,
            "critical_aar_count": 0,
            "spirit_fractured": False,
            "last_blessing_timestamp": None,
        },
    )


def _set_armor_state_in_batch(user_id: int, state: dict, batch_data: dict):
    """Set armor state in batch data (in-memory, no file I/O)."""
    batch_data[str(user_id)] = state


async def _save_armor_batch(batch_data: dict):
    """Save batch armor data to disk (call once at end of bulk operation)."""
    try:
        async with ARMOR_INTEGRITY_LOCK:
            _save_armor_integrity(batch_data)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Armor Scan State - Detection caching per AAR cycle
# ---------------------------------------------------------------------------


def _load_scan_state() -> dict:
    """Load armor scan state from disk."""
    try:
        if not os.path.exists(ARMOR_SCAN_STATE_PATH):
            return {"aar_generation": 0, "intensive_scans": {}, "scan_cache": {}}
        with open(ARMOR_SCAN_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
            # Ensure all required keys exist
            data.setdefault("aar_generation", 0)
            data.setdefault("intensive_scans", {})
            data.setdefault("scan_cache", {})
            return data
    except Exception:
        return {"aar_generation": 0, "intensive_scans": {}, "scan_cache": {}}


def _save_scan_state(data: dict):
    """Save armor scan state to disk."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(ARMOR_SCAN_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


async def _increment_aar_generation():
    """Increment AAR generation counter and clear stale scan cache."""
    async with ARMOR_SCAN_STATE_LOCK:
        data = _load_scan_state()
        data["aar_generation"] = data.get("aar_generation", 0) + 1
        # Clear scan cache on new AAR cycle (all results are now stale)
        data["scan_cache"] = {}
        # Intensive scans purchased in previous cycles are now expired
        # (will be checked when used, but we can prune here)
        current_gen = data["aar_generation"]
        data["intensive_scans"] = {
            k: v for k, v in data.get("intensive_scans", {}).items()
            if v >= current_gen
        }
        _save_scan_state(data)
        return data["aar_generation"]


async def _get_aar_generation() -> int:
    """Get the current AAR generation counter."""
    async with ARMOR_SCAN_STATE_LOCK:
        data = _load_scan_state()
        return data.get("aar_generation", 0)


async def _purchase_intensive_scan(techmarine_id: int) -> bool:
    """Mark a Techmarine as having an active intensive scan for this AAR cycle."""
    async with ARMOR_SCAN_STATE_LOCK:
        data = _load_scan_state()
        current_gen = data.get("aar_generation", 0)
        data.setdefault("intensive_scans", {})[str(techmarine_id)] = current_gen
        _save_scan_state(data)
        return True


async def _has_intensive_scan(techmarine_id: int) -> bool:
    """Check if a Techmarine has an active intensive scan for this AAR cycle."""
    async with ARMOR_SCAN_STATE_LOCK:
        data = _load_scan_state()
        current_gen = data.get("aar_generation", 0)
        tech_gen = data.get("intensive_scans", {}).get(str(techmarine_id))
        # Intensive scan is active if purchased in current generation
        return tech_gen is not None and tech_gen >= current_gen


async def _get_or_roll_scan_result(
    brother_id: int,
    current_tier: Optional[str],
    points_since_blessing: int,
    spirit_fractured: bool,
) -> dict:
    """Get cached scan result or roll a new one for this AAR cycle.
    
    Returns dict with:
        - detected: bool (True if brother shows up in scan)
        - predictive_warning: bool (True if risk warning triggered for nominal)
        - miss_reason: str or None (if not detected, why)
    """
    async with ARMOR_SCAN_STATE_LOCK:
        data = _load_scan_state()
        current_gen = data.get("aar_generation", 0)
        cache = data.setdefault("scan_cache", {})
        brother_key = str(brother_id)
        
        # Check if we have a cached result for this AAR cycle
        cached = cache.get(brother_key)
        if cached and cached.get("aar_gen") == current_gen:
            return cached
        
        # Roll new scan result
        result = _roll_scan_result(current_tier, points_since_blessing, spirit_fractured)
        result["aar_gen"] = current_gen
        
        # Cache the result
        cache[brother_key] = result
        _save_scan_state(data)
        
        return result


def _roll_scan_result(
    current_tier: Optional[str],
    points_since_blessing: int,
    spirit_fractured: bool,
) -> dict:
    """Roll fresh scan detection result based on tier/points.
    
    Returns dict with detected, predictive_warning, miss_reason.
    """
    import random
    
    # Fractured spirits are always detected
    if spirit_fractured:
        return {"detected": True, "predictive_warning": False, "miss_reason": None}
    
    # Damaged tiers have miss chances
    if current_tier and current_tier in ARMOR_SCAN_MISS_CHANCES:
        miss_chance = ARMOR_SCAN_MISS_CHANCES[current_tier]
        if random.random() < miss_chance:
            return {
                "detected": False,
                "predictive_warning": False,
                "miss_reason": "spirit_uncommunicative",
            }
        # Detected
        return {"detected": True, "predictive_warning": False, "miss_reason": None}
    
    # Nominal brother - check for predictive warning
    for tier_info in ARMOR_SCAN_PREDICTIVE_TIERS:
        min_pts = tier_info["min"]
        max_pts = tier_info["max"]
        if max_pts is None:
            max_pts = float("inf")
        if min_pts <= points_since_blessing <= max_pts:
            if random.random() < tier_info["chance"]:
                return {
                    "detected": True,
                    "predictive_warning": True,
                    "miss_reason": None,
                }
            break
    
    # No warning triggered for nominal brother with low risk
    # They are "detected" but without any warning status
    return {"detected": True, "predictive_warning": False, "miss_reason": None}


async def _get_armor_state(user_id: int) -> dict:
    """Get armor integrity state for a user."""
    try:
        async with ARMOR_INTEGRITY_LOCK:
            data = _load_armor_integrity()
            return data.get(
                str(user_id),
                {
                    "points_since_blessing": 0,
                    "damage_tier": None,
                    "critical_aar_count": 0,
                    "spirit_fractured": False,
                    "last_blessing_timestamp": None,
                },
            )
    except Exception:
        return {
            "points_since_blessing": 0,
            "damage_tier": None,
            "critical_aar_count": 0,
            "spirit_fractured": False,
            "last_blessing_timestamp": None,
        }


async def _set_armor_state(user_id: int, state: dict):
    """Update armor integrity state for a user."""
    try:
        async with ARMOR_INTEGRITY_LOCK:
            data = _load_armor_integrity()
            data[str(user_id)] = state
            _save_armor_integrity(data)
    except Exception:
        pass


def _get_armor_config() -> dict:
    """Get armor integrity configuration from CONFIG or defaults."""
    return CONFIG.get("armor_integrity", {})


def _get_armor_probability_tiers() -> list:
    """Get probability tiers from config or defaults."""
    config = _get_armor_config()
    return config.get("probability_tiers", DEFAULT_ARMOR_PROBABILITY_TIERS)


def _get_probability_tier_for_points(points_since_blessing: int) -> Optional[dict]:
    """Get the probability tier config for a given point total."""
    tiers = _get_armor_probability_tiers()
    for tier in tiers:
        min_pts = tier.get("min", 0)
        max_pts = tier.get("max")
        if max_pts is None:
            # Unbounded upper tier
            if points_since_blessing >= min_pts:
                return tier
        else:
            if min_pts <= points_since_blessing <= max_pts:
                return tier
    return None


def _get_damage_probability(points_since_blessing: int) -> float:
    """Get damage probability for a given point total."""
    tier = _get_probability_tier_for_points(points_since_blessing)
    if tier:
        return tier.get("chance", 0.0)
    return 0.0


def _roll_damage_tier(points_since_blessing: int) -> str:
    """Roll which damage tier to apply based on weighted probabilities.

    Returns one of: 'damaged', 'compromised', 'critical'
    """
    tier = _get_probability_tier_for_points(points_since_blessing)

    # Default weights if not specified
    default_weights = {"damaged": 100, "compromised": 0, "critical": 0}
    weights = tier.get("damage_weights", default_weights) if tier else default_weights

    # Build weighted list
    damage_tiers = []
    tier_weights = []
    for damage_tier in ARMOR_DAMAGE_TIERS:
        weight = weights.get(damage_tier, 0)
        if weight > 0:
            damage_tiers.append(damage_tier)
            tier_weights.append(weight)

    # If no valid weights, default to damaged
    if not damage_tiers:
        return "damaged"

    # Weighted random selection
    total = sum(tier_weights)
    roll = random.uniform(0, total)
    cumulative = 0
    for i, weight in enumerate(tier_weights):
        cumulative += weight
        if roll <= cumulative:
            return damage_tiers[i]

    return damage_tiers[-1]


def _roll_detection_alert(current_tier: str) -> bool:
    """Roll whether to send an early detection alert for current damage tier.
    
    Args:
        current_tier: Current damage tier (damaged, compromised, critical, fractured)
        
    Returns:
        True if detection alert should be sent, False otherwise.
    """
    if not current_tier:
        return False
    
    chance = ARMOR_DETECTION_CHANCES.get(current_tier, 0.0)
    return random.random() < chance


def _get_armor_damage_role_ids() -> dict:
    """Get damage role IDs from config."""
    config = _get_armor_config()
    return config.get("damage_role_ids", {})


def _get_arming_chamber_channel_id() -> Optional[int]:
    """Get the arming chamber channel ID for alerts."""
    config = _get_armor_config()
    cid = config.get("arming_chamber_channel_id")
    if cid:
        try:
            return int(cid)
        except (ValueError, TypeError):
            pass
    return None


def _get_techmarine_role_id() -> Optional[int]:
    """Get the Techmarine role ID for pinging."""
    config = _get_armor_config()
    rid = config.get("techmarine_role_id")
    if rid:
        try:
            return int(rid)
        except (ValueError, TypeError):
            pass
    return None


def _get_member_damage_tier(member: discord.Member) -> Optional[str]:
    """Check a member's roles and return their current damage tier, or None if undamaged."""
    role_ids = _get_armor_damage_role_ids()
    if not role_ids:
        return None

    member_role_ids = {r.id for r in getattr(member, "roles", [])}

    # Check in order of severity (return highest)
    for tier in reversed(ARMOR_DAMAGE_TIERS):
        tier_role_id = role_ids.get(tier)
        if tier_role_id:
            try:
                if int(tier_role_id) in member_role_ids:
                    return tier
            except (ValueError, TypeError):
                pass
    return None


def _get_damage_penalty(tier: Optional[str]) -> int:
    """Get the AAR point penalty for a damage tier."""
    if not tier:
        return 0
    return ARMOR_DAMAGE_PENALTIES.get(tier, 0)


def _roll_armor_penalty(tier: Optional[str], spirit_fractured: bool = False) -> int:
    """Roll a probabilistic AAR penalty based on damage tier.

    Uses ARMOR_PENALTY_PROBABILITIES to determine outcome.
    Returns the penalty amount (0 = no penalty, 1-4 = AAR reduction).
    """
    import random

    # Fractured state overrides tier
    if spirit_fractured:
        probs = ARMOR_PENALTY_PROBABILITIES.get("fractured", {0: 1.0})
    else:
        probs = ARMOR_PENALTY_PROBABILITIES.get(tier, {0: 1.0})

    # Roll against cumulative probabilities
    roll = random.random()
    cumulative = 0.0
    for penalty, prob in sorted(probs.items()):
        cumulative += prob
        if roll < cumulative:
            return penalty

    # Fallback (shouldn't happen if probabilities sum to 1.0)
    return 0


def _get_tier_risk_display(tier: Optional[str], spirit_fractured: bool = False) -> str:
    """Get a human-readable risk display string for a damage tier.

    Returns format like "75% (-1 to -3 AAR)" or "No risk" for nominal.
    """
    if spirit_fractured:
        probs = ARMOR_PENALTY_PROBABILITIES.get("fractured", {0: 1.0})
    else:
        probs = ARMOR_PENALTY_PROBABILITIES.get(tier, {0: 1.0})

    # Calculate total penalty chance
    penalty_chance = sum(prob for penalty, prob in probs.items() if penalty > 0)

    if penalty_chance == 0:
        return "No risk"

    # Find min and max penalties (excluding 0)
    penalties_with_chance = [p for p, prob in probs.items() if p > 0 and prob > 0]
    if not penalties_with_chance:
        return "No risk"

    min_penalty = min(penalties_with_chance)
    max_penalty = max(penalties_with_chance)

    percent = int(penalty_chance * 100)
    if min_penalty == max_penalty:
        return f"{percent}% (-{min_penalty} AAR)"
    else:
        return f"{percent}% (-{min_penalty} to -{max_penalty} AAR)"


def _check_armor_grace_period(member: discord.Member, total_aar_points: int) -> bool:
    """Check if a member has cleared the grace period.

    Returns True if BOTH conditions are met:
    - At least grace_period_min_points AAR points earned
    - At least grace_period_min_days since joining
    """
    config = _get_armor_config()
    min_points = config.get(
        "grace_period_min_points", DEFAULT_ARMOR_GRACE_PERIOD_MIN_POINTS
    )
    min_days = config.get("grace_period_min_days", DEFAULT_ARMOR_GRACE_PERIOD_MIN_DAYS)

    # Check points threshold
    if total_aar_points < min_points:
        return False

    # Check time threshold
    joined_at = getattr(member, "joined_at", None)
    if not joined_at:
        return False

    days_since_join = (datetime.utcnow() - joined_at.replace(tzinfo=None)).days
    if days_since_join < min_days:
        return False

    return True


async def _run_armor_integrity_check(points_since_blessing: int) -> bool:
    """Run the armor integrity check and return True if damage occurs."""
    probability = _get_damage_probability(points_since_blessing)
    if probability <= 0:
        return False
    return random.random() < probability


async def _apply_damage_tier(
    member: discord.Member,
    guild: discord.Guild,
    current_tier: Optional[str],
    rolled_tier: str,
) -> Optional[str]:
    """Apply a rolled damage tier if it's worse than current. Returns the new tier."""
    role_ids = _get_armor_damage_role_ids()
    if not role_ids:
        return None

    # Determine current index
    if current_tier is None:
        current_idx = -1
    else:
        try:
            current_idx = ARMOR_DAMAGE_TIERS.index(current_tier)
        except ValueError:
            current_idx = -1

    # Determine rolled tier index
    try:
        rolled_idx = ARMOR_DAMAGE_TIERS.index(rolled_tier)
    except ValueError:
        return None

    # Only apply if rolled tier is worse (higher index) than current
    if rolled_idx <= current_idx:
        return current_tier

    new_tier = rolled_tier
    new_role_id = role_ids.get(new_tier)

    if not new_role_id:
        return None

    try:
        # Remove current damage role if any
        if current_tier:
            current_role_id = role_ids.get(current_tier)
            if current_role_id:
                current_role = guild.get_role(int(current_role_id))
                if current_role and current_role in member.roles:
                    await member.remove_roles(
                        current_role, reason="Armor integrity: applying damage tier"
                    )

        # Add new damage role
        new_role = guild.get_role(int(new_role_id))
        if new_role:
            await member.add_roles(
                new_role, reason=f"Armor integrity: {new_tier} damage"
            )

        return new_tier
    except Exception:
        return None


async def _clear_armor_damage(member: discord.Member, guild: discord.Guild, grace_points: int = 0):
    """Remove all damage roles from a member and reset their armor state.
    
    Args:
        grace_points: Starting points (negative = grace period, e.g., -25 for crit success)
    """
    role_ids = _get_armor_damage_role_ids()

    # Remove all damage roles
    for tier in ARMOR_DAMAGE_TIERS:
        role_id = role_ids.get(tier)
        if role_id:
            try:
                role = guild.get_role(int(role_id))
                if role and role in member.roles:
                    await member.remove_roles(
                        role, reason="Armor integrity: blessed by Techmarine"
                    )
            except Exception:
                pass

    # Get current state to preserve/update blessing timestamps
    current_state = await _get_armor_state(member.id)
    blessing_timestamps = current_state.get("blessing_timestamps", [])
    
    # Filter old timestamps and add current
    now = datetime.utcnow()
    cooldown_window = timedelta(hours=BLESSING_RECIPIENT_COOLDOWN_HOURS)
    active_timestamps = []
    for ts_str in blessing_timestamps:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00").replace("+00:00", ""))
            if now - ts < cooldown_window:
                active_timestamps.append(ts_str)
        except Exception:
            continue
    active_timestamps.append(now.isoformat())

    # Reset armor state with updated timestamps
    await _set_armor_state(
        member.id,
        {
            "points_since_blessing": grace_points,
            "damage_tier": None,
            "critical_aar_count": 0,
            "spirit_fractured": False,
            "last_blessing_timestamp": now.isoformat(),
            "blessing_timestamps": active_timestamps,
            "last_detection_alert_tier": None,  # Reset detection tracking
        },
    )


async def _apply_blessing_crit_fail(member: discord.Member, guild: discord.Guild):
    """Apply crit fail blessing result: reset points but keep damage tier.
    
    Returns the current damage tier (unchanged).
    """
    current_tier = _get_member_damage_tier(member)
    
    # Get current state to preserve/update blessing timestamps
    current_state = await _get_armor_state(member.id)
    blessing_timestamps = current_state.get("blessing_timestamps", [])
    
    # Filter old timestamps and add current
    now = datetime.utcnow()
    cooldown_window = timedelta(hours=BLESSING_RECIPIENT_COOLDOWN_HOURS)
    active_timestamps = []
    for ts_str in blessing_timestamps:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00").replace("+00:00", ""))
            if now - ts < cooldown_window:
                active_timestamps.append(ts_str)
        except Exception:
            continue
    active_timestamps.append(now.isoformat())
    
    # Reset points but keep damage_tier and spirit_fractured unchanged
    await _set_armor_state(
        member.id,
        {
            "points_since_blessing": 0,
            "damage_tier": current_tier,
            "critical_aar_count": current_state.get("critical_aar_count", 0),
            "spirit_fractured": current_state.get("spirit_fractured", False),
            "last_blessing_timestamp": now.isoformat(),
            "blessing_timestamps": active_timestamps,
        },
    )
    
    return current_tier


async def _apply_blessing_normal(member: discord.Member, guild: discord.Guild) -> Optional[str]:
    """Apply normal blessing result: drop one damage tier.
    
    Returns the new damage tier (or None if now nominal).
    """
    current_tier = _get_member_damage_tier(member)
    
    if not current_tier:
        # Already nominal - just reset points and add timestamp
        await _clear_armor_damage(member, guild)
        return None
    
    # Drop one tier
    new_tier = await _drop_armor_tier(member, guild)
    
    # Get current state to preserve/update blessing timestamps
    current_state = await _get_armor_state(member.id)
    blessing_timestamps = current_state.get("blessing_timestamps", [])
    
    # Filter old timestamps and add current
    now = datetime.utcnow()
    cooldown_window = timedelta(hours=BLESSING_RECIPIENT_COOLDOWN_HOURS)
    active_timestamps = []
    for ts_str in blessing_timestamps:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00").replace("+00:00", ""))
            if now - ts < cooldown_window:
                active_timestamps.append(ts_str)
        except Exception:
            continue
    active_timestamps.append(now.isoformat())
    
    # Update state with new tier
    await _set_armor_state(
        member.id,
        {
            "points_since_blessing": 0,
            "damage_tier": new_tier,
            "critical_aar_count": 0 if not new_tier else current_state.get("critical_aar_count", 0),
            "spirit_fractured": False if not new_tier else current_state.get("spirit_fractured", False),
            "last_blessing_timestamp": now.isoformat(),
            "blessing_timestamps": active_timestamps,
        },
    )
    
    return new_tier


async def _apply_blessing_crit_success(member: discord.Member, guild: discord.Guild, charges_invested: int = 1):
    """Apply crit success blessing result: full heal + grace period.
    
    Args:
        charges_invested: Number of charges used (1 for standard, 2-4 for intensive).
            Grace period scales with charges: -25 × charges_invested.
    
    Returns None (always results in nominal status).
    """
    grace_points = BLESSING_CRIT_SUCCESS_GRACE_POINTS * charges_invested
    await _clear_armor_damage(member, guild, grace_points=grace_points)
    return None


async def _apply_blessing_intensive_normal(member: discord.Member, guild: discord.Guild):
    """Apply intensive blessing normal result: full heal to nominal, no crit-success grace.
    
    Returns None (always results in nominal status).
    """
    await _clear_armor_damage(member, guild)
    return None


async def _check_spirit_fracture(user_id: int) -> bool:
    """Check if a user's machine spirit has fractured (should be replaced on blessing)."""
    state = await _get_armor_state(user_id)
    return state.get("spirit_fractured", False)


# ─────────────────────────────────────────────────────────────────────────────
# Blessing Pool (Techmarine daily blessing limits)
# ─────────────────────────────────────────────────────────────────────────────


def _load_blessing_pool() -> dict:
    """Load blessing pool data from disk."""
    try:
        if not os.path.exists(BLESSING_POOL_PATH):
            return {}
        with open(BLESSING_POOL_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_blessing_pool(data: dict):
    """Save blessing pool data to disk."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(BLESSING_POOL_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


async def _get_techmarine_pool_state(user_id: int) -> dict:
    """Get blessing pool state for a Techmarine."""
    try:
        async with BLESSING_POOL_LOCK:
            data = _load_blessing_pool()
            state = data.get(str(user_id), {})
            # Initialize with defaults if empty
            if not state:
                return {
                    "remaining_blessings": BLESSING_POOL_MAX,
                    "blessing_timestamps": [],
                }
            return state
    except Exception:
        return {
            "remaining_blessings": BLESSING_POOL_MAX,
            "blessing_timestamps": [],
        }


async def _set_techmarine_pool_state(user_id: int, state: dict):
    """Update blessing pool state for a Techmarine."""
    try:
        async with BLESSING_POOL_LOCK:
            data = _load_blessing_pool()
            data[str(user_id)] = state
            _save_blessing_pool(data)
    except Exception:
        pass


def _filter_active_blessing_timestamps(timestamps: List[str]) -> List[str]:
    """Return only the blessing timestamps still within the regen window.

    Malformed or unparseable entries are silently discarded.
    """
    now = datetime.utcnow()
    regen_seconds = BLESSING_POOL_REGEN_HOURS * 3600
    active = []
    for ts_str in timestamps:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00").replace("+00:00", ""))
            if (now - ts).total_seconds() < regen_seconds:
                active.append(ts_str)
        except Exception:
            pass
    return active


def _calculate_regenerated_blessings(blessing_timestamps: List[str]) -> int:
    """Calculate how many blessings have regenerated based on timestamps.
    
    Each blessing regenerates after BLESSING_POOL_REGEN_HOURS (4.8h).
    Returns the number of blessings currently available.
    """
    on_cooldown = len(_filter_active_blessing_timestamps(blessing_timestamps))
    return max(0, BLESSING_POOL_MAX - on_cooldown)


async def _check_techmarine_can_bless(user_id: int) -> Tuple[bool, int, Optional[timedelta]]:
    """Check if a Techmarine can perform a blessing.
    
    Returns (can_bless, remaining_pool, time_until_next_regen).
    """
    state = await _get_techmarine_pool_state(user_id)
    timestamps = state.get("blessing_timestamps", [])
    
    active_timestamps = _filter_active_blessing_timestamps(timestamps)
    # Trim to the most recent BLESSING_POOL_MAX entries to keep state bounded
    active_timestamps = active_timestamps[-BLESSING_POOL_MAX:]
    available = max(0, min(BLESSING_POOL_MAX - len(active_timestamps), BLESSING_POOL_MAX))
    
    if available > 0:
        return True, available, None
    
    # Calculate when next blessing will be available
    now = datetime.utcnow()
    regen_seconds = BLESSING_POOL_REGEN_HOURS * 3600
    oldest_ts = None
    for ts_str in active_timestamps:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00").replace("+00:00", ""))
            if oldest_ts is None or ts < oldest_ts:
                oldest_ts = ts
        except Exception:
            pass
    
    if oldest_ts:
        time_until_regen = timedelta(seconds=regen_seconds) - (now - oldest_ts)
        if time_until_regen.total_seconds() > 0:
            return False, 0, time_until_regen
    
    return False, 0, timedelta(hours=BLESSING_POOL_REGEN_HOURS)


async def _get_blessing_pool_display(user_id: int) -> Tuple[int, Optional[timedelta]]:
    """Get blessing pool count and time until next regen (even if pool not empty).
    
    Returns (remaining_blessings, time_until_next_regen_or_None_if_full).
    """
    state = await _get_techmarine_pool_state(user_id)
    timestamps = state.get("blessing_timestamps", [])
    
    active_timestamps = _filter_active_blessing_timestamps(timestamps)
    # Trim to the most recent BLESSING_POOL_MAX entries to keep state bounded
    active_timestamps = active_timestamps[-BLESSING_POOL_MAX:]
    available = max(0, min(BLESSING_POOL_MAX - len(active_timestamps), BLESSING_POOL_MAX))
    
    # If pool is full, no regen time needed
    if available >= BLESSING_POOL_MAX:
        return available, None
    
    # Calculate when next blessing will regenerate (oldest timestamp)
    now = datetime.utcnow()
    regen_seconds = BLESSING_POOL_REGEN_HOURS * 3600
    oldest_ts = None
    for ts_str in active_timestamps:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00").replace("+00:00", ""))
            if oldest_ts is None or ts < oldest_ts:
                oldest_ts = ts
        except Exception:
            pass
    if oldest_ts:
        time_until_regen = timedelta(seconds=regen_seconds) - (now - oldest_ts)
        if time_until_regen.total_seconds() > 0:
            return available, time_until_regen
    
    return available, None


async def _consume_blessing(user_id: int):
    """Record that a Techmarine has used a blessing."""
    state = await _get_techmarine_pool_state(user_id)
    timestamps = state.get("blessing_timestamps", [])
    
    now = datetime.utcnow()
    active_timestamps = _filter_active_blessing_timestamps(timestamps)
    # Trim to most recent (BLESSING_POOL_MAX - 1) entries before adding the new one,
    # to keep the list bounded and prevent the pool from going negative.
    active_timestamps = active_timestamps[-(BLESSING_POOL_MAX - 1):]
    
    # Add current blessing timestamp
    active_timestamps.append(now.isoformat())
    
    await _set_techmarine_pool_state(user_id, {
        "remaining_blessings": max(0, BLESSING_POOL_MAX - len(active_timestamps)),
        "blessing_timestamps": active_timestamps,
    })


def _get_intensive_charge_cost(damage_tier: Optional[str], spirit_fractured: bool) -> int:
    """Get the number of charges required for an intensive blessing.

    `spirit_fractured` takes priority and always returns 4 regardless of
    `damage_tier`.  Returns 0 when `damage_tier` is nominal (i.e. not in
    INTENSIVE_BLESSING_COSTS) and `spirit_fractured` is False.
    """
    if spirit_fractured:
        return INTENSIVE_BLESSING_COSTS.get("fractured", 4)
    return INTENSIVE_BLESSING_COSTS.get(damage_tier, 0)


async def _get_techmarine_available_charges(user_id: int) -> int:
    """Get the number of available blessing charges for a Techmarine."""
    state = await _get_techmarine_pool_state(user_id)
    timestamps = state.get("blessing_timestamps", [])
    active_count = len(_filter_active_blessing_timestamps(timestamps))
    return max(0, BLESSING_POOL_MAX - active_count)


async def _consume_multiple_blessings(user_id: int, count: int):
    """Record that a Techmarine has used multiple blessings at once.
    
    Used for intensive blessings which consume 2-4 charges.
    """
    if count <= 0:
        return
    
    state = await _get_techmarine_pool_state(user_id)
    timestamps = state.get("blessing_timestamps", [])
    
    now = datetime.utcnow()
    active_timestamps = _filter_active_blessing_timestamps(timestamps)
    
    # Record simultaneous consumption at the same timestamp and rely on list order.
    now_iso = now.isoformat()
    for _ in range(count):
        active_timestamps.append(now_iso)
    
    # Trim to BLESSING_POOL_MAX entries to keep bounded
    active_timestamps = active_timestamps[-BLESSING_POOL_MAX:]
    
    await _set_techmarine_pool_state(user_id, {
        "remaining_blessings": max(0, BLESSING_POOL_MAX - len(active_timestamps)),
        "blessing_timestamps": active_timestamps,
    })


async def _check_recipient_cooldown(user_id: int) -> Tuple[bool, Optional[timedelta], int, Optional[str]]:
    """Check if a recipient can receive a blessing (max 3 per 24h, 4h between each).
    
    Returns (can_receive, time_until_next_slot, blessings_used, block_reason).
    block_reason is None if can_receive, 'per_blessing' for 4h cooldown, 'daily_cap' for 3/day limit.
    """
    state = await _get_armor_state(user_id)
    blessing_timestamps = state.get("blessing_timestamps", [])
    
    # Also check legacy field for backwards compatibility
    if not blessing_timestamps:
        last_blessing = state.get("last_blessing_timestamp")
        if last_blessing:
            blessing_timestamps = [last_blessing]
    
    if not blessing_timestamps:
        return True, None, 0, None
    
    now = datetime.utcnow()
    daily_window = timedelta(hours=BLESSING_RECIPIENT_COOLDOWN_HOURS)
    per_blessing_window = timedelta(hours=BLESSING_RECIPIENT_PER_BLESSING_COOLDOWN_HOURS)
    
    # Filter to timestamps within the last 24h for daily cap
    active_timestamps = []
    for ts_str in blessing_timestamps:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00").replace("+00:00", ""))
            if now - ts < daily_window:
                active_timestamps.append(ts)
        except Exception:
            continue
    
    blessings_used = len(active_timestamps)
    
    # Check per-blessing cooldown first (most recent blessing must be 4h+ ago)
    if active_timestamps:
        most_recent = max(active_timestamps)
        time_since_last = now - most_recent
        if time_since_last < per_blessing_window:
            time_until_next = per_blessing_window - time_since_last
            return False, time_until_next, blessings_used, "per_blessing"
    
    # Check daily cap
    if blessings_used >= BLESSING_RECIPIENT_MAX_PER_DAY:
        # At max - find when the oldest one expires
        oldest = min(active_timestamps)
        time_until_slot = (oldest + daily_window) - now
        return False, time_until_slot, blessings_used, "daily_cap"
    
    return True, None, blessings_used, None


def _roll_blessing_outcome(
    damage_tier: Optional[str] = None,
    spirit_fractured: bool = False,
) -> str:
    """Roll for blessing outcome based on armor state.
    
    Probabilities vary by state (asymmetric spread):
    - Nominal: 1% fail / 98% normal / 1% crit
    - Damaged: 3% fail / 94% normal / 3% crit
    - Compromised: 5% fail / 90% normal / 5% crit
    - Critical: 8% fail / 86% normal / 6% crit (asymmetric - less punishing)
    - Fractured: 10% fail / 80% normal / 10% crit
    
    Returns one of: 'crit_fail', 'normal', 'crit_success'
    """
    import random
    
    # Determine which probability set to use
    if spirit_fractured:
        state_key = "fractured"
    else:
        state_key = damage_tier  # None, "damaged", "compromised", or "critical"
    
    # Get probabilities for this state (fallback to nominal)
    crit_fail_chance, crit_success_chance = BLESSING_ROLL_PROBABILITIES.get(
        state_key, BLESSING_ROLL_PROBABILITIES[None]
    )
    
    roll = random.random()
    
    if roll < crit_fail_chance:
        return "crit_fail"
    elif roll >= (1.0 - crit_success_chance):
        return "crit_success"
    else:
        return "normal"


async def _drop_armor_tier(member: discord.Member, guild: discord.Guild) -> Optional[str]:
    """Drop a member's armor damage by one tier.
    
    Returns the new tier (or None if now undamaged).
    Tier progression: critical -> compromised -> damaged -> None (nominal)
    """
    current_tier = _get_member_damage_tier(member)
    role_ids = _get_armor_damage_role_ids()
    
    if not current_tier:
        return None  # Already undamaged
    
    # Remove current tier role
    current_role_id = role_ids.get(current_tier)
    if current_role_id:
        try:
            role = guild.get_role(int(current_role_id))
            if role and role in member.roles:
                await member.remove_roles(role, reason="Armor integrity: blessing reduced damage tier")
        except Exception:
            pass
    
    # Determine new tier (one level better)
    tier_order = ["damaged", "compromised", "critical"]
    try:
        current_idx = tier_order.index(current_tier)
        if current_idx == 0:
            # Was damaged, now nominal
            return None
        else:
            # Drop one tier
            new_tier = tier_order[current_idx - 1]
            new_role_id = role_ids.get(new_tier)
            if new_role_id:
                try:
                    new_role = guild.get_role(int(new_role_id))
                    if new_role:
                        await member.add_roles(new_role, reason="Armor integrity: blessing reduced damage tier")
                except Exception:
                    pass
            return new_tier
    except ValueError:
        return None


def _format_cooldown_time(td: timedelta) -> str:
    """Format a timedelta as 'Xh Ym' or 'Ym' if under an hour."""
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


# ─────────────────────────────────────────────────────────────────────────────
# Forge Requisition Pool (Community armory -> blessing charges)
# ─────────────────────────────────────────────────────────────────────────────


def _load_forge_pool() -> dict:
    """Load forge requisition pool data from disk."""
    max_balance = FORGE_POOL_MAX_CHARGES * FORGE_POOL_COST_PER_CHARGE
    default = {"balance": max_balance, "daily_usage": {}}
    try:
        if not os.path.exists(FORGE_POOL_PATH):
            return default
        with open(FORGE_POOL_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        # Migration: if old format (total_spent) exists, convert to balance
        if "balance" not in data and "total_spent" in data:
            # Start at max, already spent some
            data["balance"] = max(0, max_balance - data.get("total_spent", 0))
        elif "balance" not in data:
            data["balance"] = max_balance
        return data
    except Exception:
        return default


def _save_forge_pool(data: dict):
    """Save forge requisition pool data to disk."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(FORGE_POOL_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Forge Chronicle (Immersive Armor Channel Data)
# ─────────────────────────────────────────────────────────────────────────────


def _load_forge_chronicle() -> dict:
    """Load forge chronicle data from disk."""
    default = {
        "pending_alerts": {},
        "rite_history": [],
        "techmarine_stats": {},
        "dashboard_message_id": None,
        "last_ambient_ts": None,
    }
    try:
        if not os.path.exists(FORGE_CHRONICLE_PATH):
            return default.copy()
        with open(FORGE_CHRONICLE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Merge with defaults to handle missing keys
            for k, v in default.items():
                if k not in data:
                    data[k] = v
            return data
    except Exception:
        return default.copy()


def _save_forge_chronicle(data: dict):
    """Save forge chronicle data to disk."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(FORGE_CHRONICLE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


async def _store_pending_alert(user_id: int, message_id: int, channel_id: int):
    """Store a pending armor alert for thread reply tracking."""
    async with FORGE_CHRONICLE_LOCK:
        data = _load_forge_chronicle()
        data["pending_alerts"][str(user_id)] = {
            "message_id": message_id,
            "channel_id": channel_id,
            "ts": datetime.utcnow().isoformat(),
        }
        _save_forge_chronicle(data)


async def _get_pending_alert(user_id: int) -> Optional[dict]:
    """Get pending alert info for a user (if any)."""
    async with FORGE_CHRONICLE_LOCK:
        data = _load_forge_chronicle()
        return data.get("pending_alerts", {}).get(str(user_id))


async def _clear_pending_alert(user_id: int):
    """Clear a pending alert after responding with a forge_rite thread."""
    async with FORGE_CHRONICLE_LOCK:
        data = _load_forge_chronicle()
        if str(user_id) in data.get("pending_alerts", {}):
            del data["pending_alerts"][str(user_id)]
            _save_forge_chronicle(data)


async def _record_rite_in_chronicle(
    bearer_id: int,
    techmarine_id: int,
    rite_type: str,
    spirit_designation: str,
    spirit_event: str,
):
    """Record a forge rite in the chronicle for dashboard stats.
    
    Args:
        bearer_id: User ID of the brother blessed
        techmarine_id: User ID of the attesting Techmarine
        rite_type: "standard" or "intensive"
        spirit_designation: The machine spirit ID
        spirit_event: "first_binding", "rebirth", "restoration", "maintenance"
    """
    async with FORGE_CHRONICLE_LOCK:
        data = _load_forge_chronicle()
        
        # Add to rite history (keep last 500 entries)
        entry = {
            "ts": datetime.utcnow().isoformat(),
            "bearer_id": str(bearer_id),
            "techmarine_id": str(techmarine_id),
            "rite_type": rite_type,
            "spirit": spirit_designation,
            "event": spirit_event,
        }
        data["rite_history"].append(entry)
        if len(data["rite_history"]) > 500:
            data["rite_history"] = data["rite_history"][-500:]
        
        # Update techmarine stats
        tech_key = str(techmarine_id)
        if tech_key not in data["techmarine_stats"]:
            data["techmarine_stats"][tech_key] = {
                "total_rites": 0,
                "first_bindings": 0,
                "rebirths": 0,
            }
        data["techmarine_stats"][tech_key]["total_rites"] += 1
        if spirit_event == "first_binding":
            data["techmarine_stats"][tech_key]["first_bindings"] += 1
        elif spirit_event == "rebirth":
            data["techmarine_stats"][tech_key]["rebirths"] += 1
        
        _save_forge_chronicle(data)


async def _get_dashboard_message_id() -> Optional[int]:
    """Get the stored dashboard message ID (if any)."""
    async with FORGE_CHRONICLE_LOCK:
        data = _load_forge_chronicle()
        msg_id = data.get("dashboard_message_id")
        return int(msg_id) if msg_id else None


async def _set_dashboard_message_id(message_id: int):
    """Store the dashboard message ID."""
    async with FORGE_CHRONICLE_LOCK:
        data = _load_forge_chronicle()
        data["dashboard_message_id"] = message_id
        _save_forge_chronicle(data)


async def _get_last_ambient_ts() -> Optional[datetime]:
    """Get the timestamp of the last ambient message."""
    async with FORGE_CHRONICLE_LOCK:
        data = _load_forge_chronicle()
        ts_str = data.get("last_ambient_ts")
        if ts_str:
            try:
                return datetime.fromisoformat(ts_str)
            except Exception:
                pass
        return None


async def _set_last_ambient_ts():
    """Update the timestamp of the last ambient message."""
    async with FORGE_CHRONICLE_LOCK:
        data = _load_forge_chronicle()
        data["last_ambient_ts"] = datetime.utcnow().isoformat()
        _save_forge_chronicle(data)


async def _increment_forge_pool_balance(points: int):
    """Add armory points to the forge pool balance (capped at max)."""
    if points <= 0:
        return
    max_balance = FORGE_POOL_MAX_CHARGES * FORGE_POOL_COST_PER_CHARGE
    async with FORGE_POOL_LOCK:
        pool_data = _load_forge_pool()
        current = pool_data.get("balance", max_balance)
        pool_data["balance"] = min(current + points, max_balance)
        _save_forge_pool(pool_data)


async def _get_forge_pool_available() -> int:
    """Get the number of armory points available in the community forge pool."""
    async with FORGE_POOL_LOCK:
        pool_data = _load_forge_pool()
        return pool_data.get("balance", FORGE_POOL_MAX_CHARGES * FORGE_POOL_COST_PER_CHARGE)


async def _get_techmarine_daily_requisitions(user_id: int) -> int:
    """Get how many requisitions a Techmarine has used today."""
    async with FORGE_POOL_LOCK:
        pool_data = _load_forge_pool()
        daily_usage = pool_data.get("daily_usage", {})
        
        today = datetime.utcnow().strftime("%Y-%m-%d")
        user_data = daily_usage.get(str(user_id), {})
        
        # Check if the usage is from today
        if user_data.get("date") == today:
            return user_data.get("count", 0)
        return 0


async def _consume_forge_requisition(user_id: int) -> Tuple[bool, str]:
    """Attempt to consume a forge requisition for a Techmarine.
    
    Returns (success, message).
    """
    max_balance = FORGE_POOL_MAX_CHARGES * FORGE_POOL_COST_PER_CHARGE
    async with FORGE_POOL_LOCK:
        # Check daily limit
        pool_data = _load_forge_pool()
        daily_usage = pool_data.get("daily_usage", {})
        
        today = datetime.utcnow().strftime("%Y-%m-%d")
        user_data = daily_usage.get(str(user_id), {})
        
        # Reset if different day
        if user_data.get("date") != today:
            user_data = {"date": today, "count": 0}
        
        if user_data.get("count", 0) >= FORGE_POOL_DAILY_LIMIT:
            return False, f"Daily requisition limit reached ({FORGE_POOL_DAILY_LIMIT} per day)."
        
        # Check pool availability (balance-based)
        balance = pool_data.get("balance", max_balance)
        
        if balance < FORGE_POOL_COST_PER_CHARGE:
            return False, f"Insufficient forge supplies ({balance}/{FORGE_POOL_COST_PER_CHARGE} armory points available)."
        
        # Consume from balance
        pool_data["balance"] = balance - FORGE_POOL_COST_PER_CHARGE
        user_data["count"] = user_data.get("count", 0) + 1
        daily_usage[str(user_id)] = user_data
        pool_data["daily_usage"] = daily_usage
        
        _save_forge_pool(pool_data)
        
        return True, f"Requisition approved. Forge pool: {pool_data['balance']} armory points remaining."


async def _get_forge_pool_status() -> dict:
    """Get full forge pool status for display."""
    max_balance = FORGE_POOL_MAX_CHARGES * FORGE_POOL_COST_PER_CHARGE
    async with FORGE_POOL_LOCK:
        pool_data = _load_forge_pool()
        balance = pool_data.get("balance", max_balance)
        charges_available = balance // FORGE_POOL_COST_PER_CHARGE
        
        return {
            "available": balance,
            "charges_available": charges_available,
            "cost_per_charge": FORGE_POOL_COST_PER_CHARGE,
            "max_charges": FORGE_POOL_MAX_CHARGES,
        }


async def _post_armor_alert(
    member: discord.Member,
    tier: str,
    critical_aar_count: int = 0,
    guild: Optional[discord.Guild] = None,
    op_mission: Optional[str] = None,
    op_difficulty_class: Optional[str] = None,
    op_url: Optional[str] = None,
    squad_member_ids: Optional[List[str]] = None,
    alert_type: str = "sustained",
    penalty_amount: int = 0,
):
    """Post an armor damage alert to the arming chamber channel.
    
    Args:
        member: The brother whose armor was damaged
        tier: Damage tier (damaged, compromised, critical, fractured)
        critical_aar_count: Number of AARs at critical (for fracture warning)
        guild: Discord guild
        op_mission: Mission name from the AAR that triggered the damage
        op_difficulty_class: Difficulty class (e.g., normal_siege, hard_siege) for planet lookup
        op_url: Jump URL to the AAR message
        squad_member_ids: List of brother IDs on the same op (for debrief)
        alert_type: "sustained" (penalty applied, AAR loss) or "detected" (early warning)
        penalty_amount: How many AAR points were lost (for sustained alerts)
    """
    channel_id = _get_arming_chamber_channel_id()
    if not channel_id:
        return

    guild = guild or member.guild
    if not guild:
        return

    channel = guild.get_channel(channel_id)
    if not channel:
        return

    config = _get_armor_config()
    fracture_threshold = config.get(
        "fracture_threshold", DEFAULT_ARMOR_FRACTURE_THRESHOLD
    )

    # Get bearer info using the same pattern as forge_rite/stud announcements
    bearer_honorific, bearer_name, bearer_title = _get_bearer_rank_and_title(member)
    bearer_name = bearer_name.replace("●", "").replace("⚬", "").strip()

    # Service studs computation
    bearer_studs = _compute_member_service_studs(member)

    # Machine spirit designation
    machine_spirit = await _get_machine_spirit(int(member.id))

    # Home chapter (lineage)
    bearer_chapter = _get_bearer_home_chapter(member)
    chapter_emoji = (
        _get_emoji_by_name(guild, bearer_chapter) if bearer_chapter and guild else None
    )

    # Get rank emoji
    bearer_rank_name = None
    for rank, hon in RANK_HONORIFICS.items():
        if hon == bearer_honorific or rank in bearer_honorific:
            bearer_rank_name = rank
            break
    if not bearer_rank_name:
        bearer_rank_name = "Watch Brother"

    rank_emoji = _get_rank_emoji(guild, bearer_rank_name) if guild else ""
    rank_prefix = f"{rank_emoji} " if rank_emoji else ""

    # Build bearer display string (matching forge_rite style)
    if ", " in bearer_honorific:
        title_part, rank_part = bearer_honorific.rsplit(", ", 1)
        bearer_display = (
            f"{rank_prefix}**{title_part},**\n**{rank_part} {bearer_name}**"
        )
    else:
        bearer_display = f"{rank_prefix}**{bearer_honorific} {bearer_name}**"

    if bearer_title:
        bearer_display += f"\n*{bearer_title}*"
    # Lineage (home chapter)
    if bearer_chapter and bearer_chapter != "Unknown":
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        if bearer_chapter == "Black Shield":
            bearer_display += f"\nLineage: {chapter_prefix}REDACTED"
        else:
            bearer_display += f"\nLineage: {chapter_prefix}{bearer_chapter}"
    if bearer_studs > 0:
        studs_pips = _studs_pips(bearer_studs)
        bearer_display += f"\nService Studs: [{studs_pips}] ({bearer_studs})"
    # Machine spirit
    machine_spirit_emoji = _get_emoji_by_name(guild, "MachineSpirit") or "⚙️"
    if machine_spirit:
        bearer_display += f"\n{machine_spirit_emoji} Spirit: `{machine_spirit}`"
    else:
        bearer_display += f"\n{machine_spirit_emoji} Spirit: *UNBOUND*"

    # Determine embed color, title, and description based on tier and alert_type
    is_detection = alert_type == "detected"
    
    # Build penalty string for sustained alerts
    penalty_str = f" (-{penalty_amount} AAR)" if penalty_amount > 0 else ""
    
    if tier == "fractured":
        color = 0x8B0000  # Dark red
        title = "᛭⋅ MACHINE SPIRIT FRACTURED ⋅᛭"
        description = "*The bond is broken — immediate re-consecration required*"
    elif tier == "critical":
        if is_detection:
            color = 0xE74C3C  # Red
            title = "᛭⋅ CRITICAL DAMAGE DETECTED ⋅᛭"
            description = "*Machine spirit strains — intervention window open*"
        else:
            color = 0xE74C3C  # Red
            title = f"᛭⋅ CRITICAL ARMOR FAILURE ⋅᛭{penalty_str}"
            description = "*AAR points lost due to machine spirit instability*"
    elif tier == "compromised":
        if is_detection:
            color = 0xF39C12  # Dark orange/amber
            title = "᛭⋅ INTEGRITY DEGRADATION DETECTED ⋅᛭"
            description = "*Structural stress detected — maintenance window open*"
        else:
            color = 0xE67E22  # Orange
            title = f"᛭⋅ ARMOR INTEGRITY COMPROMISED ⋅᛭{penalty_str}"
            description = "*AAR points lost due to structural damage*"
    else:  # damaged
        if is_detection:
            color = 0xF1C40F  # Yellow
            title = "᛭⋅ WEAR DETECTED ⋅᛭"
            description = "*Minor degradation noted — preventive maintenance available*"
        else:
            color = 0xE67E22  # Orange
            title = f"᛭⋅ ARMOR INTEGRITY ALERT ⋅᛭{penalty_str}"
            description = "*AAR points lost due to armor wear*"

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
    )

    # Affected brother field with proper rank display
    tier_display = tier.title() if tier else "Unknown"
    spirit_fractured = tier == "fractured"
    penalty_risk = _get_tier_risk_display(tier, spirit_fractured=spirit_fractured)
    
    # Adjust status display for detection alerts
    if is_detection:
        status_label = f"{tier_display} (Early Warning)"
    else:
        status_label = tier_display
    
    embed.add_field(
        name="▸ Affected Brother",
        value=f"{bearer_display}\n**Status:** {status_label}\n**Penalty Risk:** {penalty_risk}",
        inline=False,
    )

    # Debrief field (if op context provided)

    # Debrief field (if op context provided)
    if op_mission or op_difficulty_class or op_url or squad_member_ids:
        debrief_lines = []
        # Look up planet name - check difficulty_class first (for siege ops), then mission
        planet = None
        if op_difficulty_class:
            planet = MISSION_TO_PLANET.get(op_difficulty_class.lower().strip())
        if not planet and op_mission:
            planet = MISSION_TO_PLANET.get(op_mission.lower().strip())
        
        if planet:
            debrief_lines.append(f"Integrity degraded during deployment to **{planet}**")
        elif op_mission:
            debrief_lines.append(f"Integrity degraded during **{op_mission}** deployment")
        
        # Build squad list (exclude the affected brother)
        if squad_member_ids and guild:
            squad_names = []
            for sid in squad_member_ids:
                if str(sid) == str(member.id):
                    continue  # Skip the affected brother
                try:
                    squad_member = guild.get_member(int(sid))
                    if squad_member:
                        # Get display name stripped of pips
                        name = squad_member.display_name.replace("●", "").replace("⚬", "").strip()
                        squad_names.append(name)
                except Exception:
                    pass
            if squad_names:
                debrief_lines.append(f"Squad: {', '.join(squad_names)}")
        
        if op_url:
            debrief_lines.append(f"[View After Action Report]({op_url})")
        
        if debrief_lines:
            embed.add_field(
                name="▸ Debrief",
                value="\n".join(debrief_lines),
                inline=False,
            )

    # Warning field for critical/fractured and response guidance
    if tier == "fractured":
        embed.add_field(
            name="▸ Emergency",
            value="⚠️ Machine spirit has **FRACTURED**. No further field operations until re-consecration.",
            inline=False,
        )
        embed.add_field(
            name="▸ Immediate Techmarine Response Required",
            value="Administer intensive blessing via `/forge_rite intensive:True` to re-consecrate the spirit.",
            inline=False,
        )
    elif tier == "critical":
        remaining = fracture_threshold - critical_aar_count
        embed.add_field(
            name="▸ Warning",
            value=f"⚠️ AAR submissions until spirit fracture: **{remaining}**",
            inline=False,
        )
        if is_detection:
            embed.add_field(
                name="▸ Intervention Window Open",
                value="Brother is still operational. Administer blessing via `/forge_rite` before penalties accumulate.",
                inline=False,
            )
        else:
            embed.add_field(
                name="▸ Immediate Techmarine Response Required",
                value="Administer blessing via `/forge_rite` to preserve machine spirit bond.",
                inline=False,
            )
    else:
        if is_detection:
            embed.add_field(
                name="▸ Preventive Maintenance Available",
                value="Damage detected before penalty. Administer blessing via `/forge_rite` to prevent AAR losses.",
                inline=False,
            )
        else:
            embed.add_field(
                name="▸ Techmarine Response Required",
                value="Administer blessing via `/forge_rite` to restore armor integrity.",
                inline=False,
            )

    # Build message content with Techmarine ping and affected brother mention BEFORE the embed
    content = ""
    tech_role_id = _get_techmarine_role_id()
    if tech_role_id:
        content = f"<@&{tech_role_id}> {member.mention}"
    else:
        content = member.mention

    logger.debug(
        f"Armor alert for {member.display_name}: tier={tier}, alert_type={alert_type}, "
        f"bearer_display_len={len(bearer_display)}, embed_fields={len(embed.fields)}, "
        f"content_len={len(content)}"
    )

    # Check bot permissions
    perms = channel.permissions_for(channel.guild.me)
    if not perms.embed_links:
        logger.error(f"Bot lacks 'Embed Links' permission in channel {channel.name}")
    if not perms.send_messages:
        logger.error(f"Bot lacks 'Send Messages' permission in channel {channel.name}")

    try:
        sent_msg = await channel.send(
            content=content,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(roles=True, users=True),
        )
        # Verify embed was actually sent
        if not sent_msg.embeds:
            logger.warning(
                f"Armor alert sent but embed was dropped! "
                f"embed_links={perms.embed_links}, content={content[:50]}"
            )
        else:
            logger.info(f"Posted armor alert for {member.display_name} (tier={tier}, type={alert_type})")
        
        # Store pending alert for thread reply tracking
        # This allows forge_rite to reply to this alert when repairing this brother
        try:
            await _store_pending_alert(
                user_id=int(member.id),
                message_id=sent_msg.id,
                channel_id=channel.id,
            )
        except Exception:
            pass  # Non-critical, don't block on storage failure
    except Exception as e:
        logger.error(f"Failed to post armor alert for {member.display_name}: {e}")


async def _process_armor_integrity_for_aar(
    brother_id: str,
    base_points: int,
    guild: discord.Guild,
    armor_batch: Optional[dict] = None,
    op_mission: Optional[str] = None,
    op_difficulty_class: Optional[str] = None,
    op_url: Optional[str] = None,
    squad_member_ids: Optional[List[str]] = None,
    actual_penalty: int = 0,
) -> Tuple[int, Optional[dict]]:
    """Process armor integrity for a single brother in an AAR.

    Args:
        brother_id: Discord user ID string
        base_points: Base AAR points for this brother (before penalties)
        guild: Discord guild for role operations
        armor_batch: Optional pre-loaded armor data dict for batch processing.
                     If provided, state is read/written to this dict (no file I/O).
                     If None, uses individual file I/O per call.
        op_mission: Mission name from the AAR (for debrief in alerts)
        op_difficulty_class: Difficulty class (e.g., normal_siege) for planet lookup
        op_url: Jump URL to the AAR message (for debrief in alerts)
        squad_member_ids: List of all brother IDs in this AAR (for debrief in alerts)
        actual_penalty: The penalty that was actually applied to this AAR (0 = no loss)

    Returns:
        Tuple of (penalty_amount, alert_info_or_none)
        alert_info is a dict with member, tier, critical_count, alert_type, and op context if an alert should be posted.
        alert_type is "sustained" (penalty applied, AAR loss) or "detected" (early warning, no loss yet).
    """
    alert_info = None
    penalty = 0

    try:
        member = guild.get_member(int(brother_id))
        if not member:
            return 0, None

        # Check current damage tier from roles
        current_tier = _get_member_damage_tier(member)
        penalty = _get_damage_penalty(current_tier)

        # Get user stats for grace period check
        stats = compute_stats_for_user(str(brother_id))
        total_aar_points = int(stats.get("aar_points", 0) or 0)

        # Check grace period
        if not _check_armor_grace_period(member, total_aar_points):
            return penalty, None

        # Get current armor state (from batch if provided, else from file)
        if armor_batch is not None:
            state = _get_armor_state_from_batch(int(brother_id), armor_batch)
        else:
            state = await _get_armor_state(int(brother_id))

        # Check for spirit fracture
        spirit_fractured = state.get("spirit_fractured", False)
        effective_tier = "fractured" if spirit_fractured else current_tier

        # Accumulate points (use base unpenalized points for tracking)
        state["points_since_blessing"] = (
            state.get("points_since_blessing", 0) + base_points
        )

        # Check if damage occurs (escalation)
        damage_occurred = await _run_armor_integrity_check(
            state["points_since_blessing"]
        )

        new_tier = None
        if damage_occurred:
            # Roll which damage tier to apply based on current points
            rolled_tier = _roll_damage_tier(state["points_since_blessing"])
            new_tier = await _apply_damage_tier(
                member, guild, current_tier, rolled_tier
            )
            if new_tier and new_tier != current_tier:
                state["damage_tier"] = new_tier
                if new_tier == "critical":
                    state["critical_aar_count"] = 0  # Reset on entering critical

        # Sustained alert: fires when brother actually lost AAR points (penalty > 0)
        if actual_penalty > 0 and effective_tier:
            alert_info = {
                "member": member,
                "tier": effective_tier,
                "critical_count": state.get("critical_aar_count", 0),
                "alert_type": "sustained",
                "op_mission": op_mission,
                "op_difficulty_class": op_difficulty_class,
                "op_url": op_url,
                "squad_member_ids": squad_member_ids,
                "penalty_amount": actual_penalty,
            }
            # Update detection tracking since we're alerting for this tier
            state["last_detection_alert_tier"] = effective_tier

        # Detection alert: fires when damaged but no penalty this AAR (early warning)
        if alert_info is None and effective_tier and actual_penalty == 0:
            last_detection_tier = state.get("last_detection_alert_tier")
            
            # Tier severity for comparison
            tier_severity = {"damaged": 1, "compromised": 2, "critical": 3, "fractured": 4}
            current_severity = tier_severity.get(effective_tier, 0)
            last_severity = tier_severity.get(last_detection_tier, 0)
            
            # Only roll detection if we haven't already alerted for this tier level or higher
            if current_severity > last_severity:
                if _roll_detection_alert(effective_tier):
                    alert_info = {
                        "member": member,
                        "tier": effective_tier,
                        "critical_count": state.get("critical_aar_count", 0),
                        "alert_type": "detected",
                        "op_mission": op_mission,
                        "op_difficulty_class": op_difficulty_class,
                        "op_url": op_url,
                        "squad_member_ids": squad_member_ids,
                    }
                    # Update detection tracking
                    state["last_detection_alert_tier"] = effective_tier

        # If at critical (whether damage occurred or not), increment fracture countdown
        if current_tier == "critical":
            state["critical_aar_count"] = state.get("critical_aar_count", 0) + 1
            config = _get_armor_config()
            fracture_threshold = config.get(
                "fracture_threshold", DEFAULT_ARMOR_FRACTURE_THRESHOLD
            )

            if state["critical_aar_count"] >= fracture_threshold:
                # Spirit fractures
                state["spirit_fractured"] = True
                # Guaranteed alert for fracture
                if alert_info is None or alert_info.get("tier") != "fractured":
                    alert_info = {
                        "member": member,
                        "tier": "fractured",
                        "critical_count": state["critical_aar_count"],
                        "alert_type": "sustained",
                        "op_mission": op_mission,
                        "op_difficulty_class": op_difficulty_class,
                        "op_url": op_url,
                        "squad_member_ids": squad_member_ids,
                    }

        # Save updated state (to batch if provided, else to file)
        if armor_batch is not None:
            _set_armor_state_in_batch(int(brother_id), state, armor_batch)
        else:
            await _set_armor_state(int(brother_id), state)

        return penalty, alert_info

    except Exception:
        return penalty, None


def _get_armor_status_for_blessing(
    was_damaged: bool,
    damage_tier: Optional[str],
    spirit_fractured: bool,
) -> dict:
    """Get the status line values for a forge_rite blessing based on armor state."""
    if spirit_fractured:
        return ARMOR_STATUS_FRACTURED
    elif damage_tier == "critical":
        return ARMOR_STATUS_CRITICAL
    elif damage_tier == "compromised":
        return ARMOR_STATUS_COMPROMISED
    elif damage_tier == "damaged" or was_damaged:
        return ARMOR_STATUS_DAMAGED
    else:
        return ARMOR_STATUS_NOMINAL


def _should_show_extended_blessing_fields(
    spirit_is_first: bool,
    spirit_is_reconsecrated: bool,
    spirit_is_returning: bool,
    spirit_is_restored: bool,
) -> bool:
    """Determine whether to show extended blessing fields (Honor of Long Watch, Litany).

    Returns True for unbound (first binding) or fractured (reconsecrated) spirits.
    Returns False for returning (normal maintenance) or restored (damage repaired) spirits.
    """
    # Extended fields shown for significant spiritual events:
    # - First binding: new spirit awakening
    # - Reconsecration: spirit was lost and must be re-bound
    return spirit_is_first or spirit_is_reconsecrated


def _extract_killteam_name(name: str) -> str:
    """Return a display-friendly Kill Team name by stripping the 'Kill Team' prefix.
    Handles optional separators like ':', '-', and varying whitespace/case.
    Also handles forum channel format 'Kill-Team X' (hyphen between Kill and Team).
    If no match, returns the original name (or 'Unknown' if empty).
    Ignores role names like 'Kill Team Champion' that aren't actual kill teams.
    """
    try:
        # Skip non-KT role names that start with "Kill Team"
        if name and name.lower().strip() == "kill team champion":
            return name or "Unknown"
        # Match 'Kill Team X', 'Kill-Team X', 'KillTeam X', etc.
        m = re.match(r"(?i)\s*kill[\s\-]*team\s*[:\-]?\s*(.+)", (name or ""))
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return name or "Unknown"


def _resolve_killteam_for_member(
    member: discord.User | discord.Member,
) -> Optional[str]:
    """Return the canonical Kill Team name for a member by inspecting their roles.

    Matching strategy (in order):
    1. Role ID in ALLOWED_KT_ROLE_IDS (most reliable).
    2. Exact case-insensitive role name match against entries in `KILL_TEAMS`.

    Returns the canonical `KILL_TEAMS` entry on match, else `None`.
    """
    try:
        roles = getattr(member, "roles", []) or []
        # map lower->canonical for fast lookup
        canonical_map = {kt.lower(): kt for kt in KILL_TEAMS}

        for r in roles:
            # 1) Check role ID against ALLOWED_KT_ROLE_IDS (most reliable)
            rid = getattr(r, "id", None)
            if rid and ALLOWED_KT_ROLE_IDS and rid in ALLOWED_KT_ROLE_IDS:
                rn = (getattr(r, "name", "") or "").strip()
                # Return the role name if it's in KILL_TEAMS, otherwise return as-is
                if rn.lower() in canonical_map:
                    return canonical_map[rn.lower()]
                return rn  # Role ID matched but name not in KILL_TEAMS yet

            # 2) Exact case-insensitive match against KILL_TEAMS entries
            rn = (getattr(r, "name", "") or "").strip()
            if not rn:
                continue
            if rn.lower() in canonical_map:
                return canonical_map[rn.lower()]
    except Exception:
        return None
    return None


def _resolve_killteams_for_member(member: discord.User | discord.Member) -> List[str]:
    """Return a list of Kill Team-like identifiers this member should contribute to.

    Rules:
    - Include any canonical Kill Team from `KILL_TEAMS` the member holds.
    - Include any command team from `COMMAND_TEAMS` the member holds as a role.
    - A member may contribute to multiple teams simultaneously.
    """
    out: List[str] = []
    try:
        # 1) canonical kill teams
        try:
            kt = _resolve_killteam_for_member(member)
            if kt:
                out.append(kt)
        except Exception:
            pass

        # 2) command teams (check for actual roles matching COMMAND_TEAMS)
        try:
            names = _canonical_role_names(member)
            for cmd_team in COMMAND_TEAMS:
                if cmd_team in names and cmd_team not in out:
                    out.append(cmd_team)
        except Exception:
            pass
    except Exception:
        return []

    # Deduplicate preserving order
    seen = set()
    res: List[str] = []
    for x in out:
        if x and x not in seen:
            res.append(x)
            seen.add(x)
    return res


def is_sergeant_or_higher(user: discord.User | discord.Member):
    # Allow nickname override for owner/operator
    admin_ids = set(str(x) for x in (CONFIG.get("admin_user_ids") or []))
    if (
        str(getattr(user, "id", None)) in admin_ids
        or str(getattr(user, "nick", None)) == "Watch Techmarine Jules"
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


# ============================================================================
# PERMISSION TRACKS
# ============================================================================
# Three tracks exist:
#   1. Battle Line: Watch Brother → Watch Veteran → Oathsworn → Watch Sergeant
#                   → Watch Lieutenant → Watch Captain
#   2. Champion: Kill Team Champion → Company Champion → Lord Executioner
#   3. Specialist (4 sub-tracks, each leading to High Command):
#        Chaplain → High Chaplain, Apothecary → Chief Apothecary,
#        Librarian → Void Warden, Techmarine → Forgemaster
#
# High Command = senior specialists (High Chaplain, Chief Apothecary, Void Warden,
#                Forgemaster) + Watch Master
# Watch Master is at the top of ALL tracks.
# ============================================================================

# Battle line ranks (linear progression)
BATTLE_LINE_TRACK = {
    "Watch Brother": {
        "Watch Brother",
        "Watch Veteran",
        "Oathsworn",
        "Watch Sergeant",
        "Watch Lieutenant",
        "Watch Captain",
    },
    "Watch Veteran": {
        "Watch Veteran",
        "Oathsworn",
        "Watch Sergeant",
        "Watch Lieutenant",
        "Watch Captain",
    },
    "Oathsworn": {"Oathsworn", "Watch Sergeant", "Watch Lieutenant", "Watch Captain"},
    "Watch Sergeant": {"Watch Sergeant", "Watch Lieutenant", "Watch Captain"},
    "Watch Lieutenant": {"Watch Lieutenant", "Watch Captain"},
    "Watch Captain": {"Watch Captain"},
}
BATTLE_LINE_RANKS = {
    "Watch Brother",
    "Watch Veteran",
    "Oathsworn",
    "Watch Sergeant",
    "Watch Lieutenant",
    "Watch Captain",
}

# Champion track (linear progression)
CHAMPION_TRACK = {
    "Kill Team Champion": {
        "Kill Team Champion",
        "Company Champion",
        "Lord Executioner",
    },
    "Company Champion": {"Company Champion", "Lord Executioner"},
    "Lord Executioner": {"Lord Executioner"},
}
CHAMPION_RANKS = {"Kill Team Champion", "Company Champion", "Lord Executioner"}

# Specialist tracks: each sub-track is independent, leads to High Command
SPECIALIST_TRACKS = {
    "Watch Techmarine": {"Watch Techmarine", "Forgemaster"},
    "Forgemaster": {"Forgemaster"},
    "Watch Librarian": {"Watch Librarian", "Void Warden"},
    "Void Warden": {"Void Warden"},
    "Watch Chaplain": {"Watch Chaplain", "High Chaplain"},
    "High Chaplain": {"High Chaplain"},
    "Watch Apothecary": {"Watch Apothecary", "Chief Apothecary"},
    "Chief Apothecary": {"Chief Apothecary"},
    "Watch Keeper": {"Watch Keeper", "Castellan"},
    "Castellan": {"Castellan"},
}
SPECIALIST_RANKS = set(SPECIALIST_TRACKS.keys())

# High Command (senior specialists + Watch Master)
HIGH_COMMAND_RANKS = {
    "High Chaplain",
    "Chief Apothecary",
    "Void Warden",
    "Forgemaster",
    "Castellan",
    "Watch Master",
    "Venerable",
}

# Watch Command = Sergeant+ from Battle Line, all Champions, all Specialists, High Command
# This is a convenience group for "everyone who isn't a line brother"
WATCH_COMMAND_ROLES = {
    # Battle Line (Sergeant+)
    "Watch Sergeant",
    "Watch Lieutenant",
    "Watch Captain",
    # Champion track (all)
    "Company Champion",
    "Lord Executioner",
    # Specialist track (all)
    "Watch Chaplain",
    "Watch Apothecary",
    "Watch Librarian",
    "Watch Techmarine",
    "Watch Keeper",
    # High Command
    "High Chaplain",
    "Chief Apothecary",
    "Void Warden",
    "Forgemaster",
    "Castellan",
    "Watch Master",
    "Venerable",
}


def _user_meets_track_requirement(user_roles: set[str], min_rank: str) -> bool:
    """Check if user meets a min_rank requirement based on track logic.

    - Battle Line: linear hierarchy (Sergeant+ means Sergeant, Lt, Captain)
    - Champion: KT Champion → Company Champion → Lord Executioner
    - Specialist: each of the 4 sub-tracks leads to its High Command role

    Watch Master always qualifies for everything.
    """
    # Watch Master always has access
    if "Watch Master" in user_roles:
        return True

    # Check specialist tracks (4 independent sub-tracks)
    if min_rank in SPECIALIST_TRACKS:
        allowed_roles = SPECIALIST_TRACKS[min_rank]
        return bool(user_roles & allowed_roles)

    # Check champion track
    if min_rank in CHAMPION_TRACK:
        allowed_roles = CHAMPION_TRACK[min_rank]
        return bool(user_roles & allowed_roles)

    # Check battle line track
    if min_rank in BATTLE_LINE_TRACK:
        allowed_roles = BATTLE_LINE_TRACK[min_rank]
        return bool(user_roles & allowed_roles)

    return False


def check_command_permission(
    user: discord.User | discord.Member, command_name: str
) -> bool:
    """Unified permission check for all commands.

    Reads from CONFIG["permissions"][command_name] which can have:
      - min_rank: str - minimum rank required (e.g. "Watch Sergeant")
      - roles: list[str] - list of role names that grant access
                          "Watch Command" expands to all Watch Command roles
      - user_ids: list[str|int] - specific user IDs with access

    Admin users (from CONFIG["admin_user_ids"]) always have access.
    If no config entry exists, defaults based on command name pattern.

    Three tracks:
      - Battle Line: Watch Brother → Watch Veteran → Oathsworn → Watch Sergeant
                     → Watch Lieutenant → Watch Captain
      - Champion: Kill Team Champion → Company Champion → Lord Executioner
      - Specialist (4 sub-tracks): Techmarine→Forgemaster, Librarian→Void Warden, etc.

    Each track is independent. Watch Master has access to everything.
    """
    # Admin override: always grant
    admin_ids = set(str(x) for x in (CONFIG.get("admin_user_ids") or []))
    uid = str(getattr(user, "id", None))
    if uid in admin_ids:
        return True

    perms = CONFIG.get("permissions", {}) or {}
    cmd_perms = perms.get(command_name, {}) or {}

    # Check user_ids whitelist
    user_whitelist = cmd_perms.get("user_ids") or []
    if uid in {str(x) for x in user_whitelist}:
        return True

    user_roles = _canonical_role_names(user)

    # Check min_rank using track-aware logic
    min_rank = cmd_perms.get("min_rank")
    if min_rank:
        if _user_meets_track_requirement(user_roles, min_rank):
            return True

    # Check roles list (user must have any of these roles)
    # "Watch Command" is a shorthand that expands to all Watch Command roles
    allowed_roles = set(cmd_perms.get("roles") or [])
    if "Watch Command" in allowed_roles:
        allowed_roles.discard("Watch Command")
        allowed_roles.update(WATCH_COMMAND_ROLES)
    if allowed_roles:
        if user_roles & allowed_roles:
            return True

    # If the command has explicit config but user doesn't match, deny
    if cmd_perms:
        return False

    # Default fallbacks for unconfigured commands (based on command name patterns)
    # Admin-level commands default to Watch Master + Forgemaster
    admin_commands = {
        "reconcile_records",
        "sanctify_battle_records",
        "audit_archive_discrepancies",
        "reparse_records",
        "roster_audit",
    }
    if command_name in admin_commands:
        return any(r in user_roles for r in ("Watch Master", "Forgemaster"))

    # Most other commands default to Watch Sergeant or higher
    return is_sergeant_or_higher(user)


def is_high_command(user: discord.User | discord.Member) -> bool:
    """Return True if the user is part of High Command.
    High Command roles are defined by HIGH_COMMAND_ROLES. Admin overrides in config apply.
    """
    # Admin/user override
    try:
        admin_ids = set(str(x) for x in (CONFIG.get("admin_user_ids") or []))
        if str(getattr(user, "id", None)) in admin_ids:
            return True
    except Exception:
        pass
    try:
        names = _canonical_role_names(user)
        return any(r in names for r in HIGH_COMMAND_ROLES)
    except Exception:
        return False


@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user}")

    # Dynamically populate KILL_TEAMS from ALLOWED_KT_ROLE_IDS
    global KILL_TEAMS
    try:
        guild = _resolve_notification_guild()
        if guild and ALLOWED_KT_ROLE_IDS:
            resolved_kts = []
            for role_id in ALLOWED_KT_ROLE_IDS:
                role = guild.get_role(role_id)
                if role:
                    resolved_kts.append(role.name)
            if resolved_kts:
                KILL_TEAMS = resolved_kts
                logger.info(f"Populated KILL_TEAMS from role IDs: {KILL_TEAMS}")
            else:
                logger.warning("No Kill Team roles resolved from ALLOWED_KT_ROLE_IDS")
    except Exception as e:
        logger.debug(f"Failed to populate KILL_TEAMS: {e}")

    # Initialize DataStore here so the event loop is running and the
    # background flush task can be started.
    global DATASTORE
    if DATASTORE is None:
        try:
            DATASTORE = DataStore(AAR_RECORDS_PATH, PROCESSED_IDS_PATH)
            logger.info("DataStore initialized on ready; background flush started.")
        except Exception as e:
            logger.exception(f"Failed to initialize DataStore on ready: {e}")
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
        logger.info("Sending ONLINE status broadcast...")
        try:
            await _send_watch_command_notice("ONLINE")
        except Exception as e:
            logger.warning(f"Startup announce failed: {e}")
    else:
        logger.info("Status broadcast disabled (debug mode or BROADCAST_STATUS=False)")

    # Register graceful shutdown signal handlers
    try:
        loop = asyncio.get_running_loop()

        def _sig_handler(sig_name: str = "SIGNAL"):
            logger.info(f"Received {sig_name}, initiating graceful shutdown...")
            try:
                # Create shutdown task
                task = loop.create_task(_announce_shutdown_and_close())
                # Add a callback to force-stop if the task completes but loop continues
                def _on_shutdown_done(fut):
                    try:
                        logger.info("Shutdown task completed, stopping event loop.")
                    except Exception:
                        pass
                    # Give a moment for cleanup, then stop the loop as fallback
                    loop.call_later(2.0, loop.stop)
                task.add_done_callback(_on_shutdown_done)
            except Exception as e:
                logger.error(f"Signal handler failed to create shutdown task: {e}")
                # Fallback: just close the bot directly
                try:
                    loop.create_task(bot.close())
                except Exception:
                    loop.stop()

        try:
            loop.add_signal_handler(signal.SIGTERM, lambda: _sig_handler("SIGTERM"))
        except Exception as e:
            logger.warning(f"Failed to register SIGTERM handler: {e}")
        try:
            loop.add_signal_handler(signal.SIGINT, lambda: _sig_handler("SIGINT"))
        except Exception as e:
            logger.warning(f"Failed to register SIGINT handler: {e}")
    except Exception as e:
        logger.error(f"Failed to register signal handlers: {e}")
    # Start scheduled audit loop if enabled in config
    try:
        if SCHEDULE_DAILY_AUDIT_ENABLED:
            # Start the 24-hour loop only; do not run an immediate audit on startup.
            try:
                if not _scheduled_audit_loop.is_running():
                    _scheduled_audit_loop.start()
                    logger.info("Scheduled daily audit loop started (24h interval).")
            except Exception:
                logger.exception("Failed to start scheduled audit loop")
            # Also start the monthly audit loop (checks for last-day-of-month)
            try:
                if not _monthly_audit_loop.is_running():
                    _monthly_audit_loop.start()
                    logger.info(
                        "Monthly audit loop started (daily check for month-end)."
                    )
            except Exception:
                logger.exception("Failed to start monthly audit loop")
    except Exception:
        logger.debug("Error checking/starting scheduled audit loop")

    # Start activity status check loop (always enabled)
    try:
        if not _activity_status_check_loop.is_running():
            _activity_status_check_loop.start()
            logger.info("Activity status check loop started (24h interval).")
    except Exception:
        logger.exception("Failed to start activity status check loop")

    # Start weekly maintenance loop if enabled (default: enabled)
    try:
        if SCHEDULE_WEEKLY_MAINTENANCE_ENABLED:
            if not _scheduled_weekly_maintenance_loop.is_running():
                _scheduled_weekly_maintenance_loop.start()
                day_names = [
                    "Monday",
                    "Tuesday",
                    "Wednesday",
                    "Thursday",
                    "Friday",
                    "Saturday",
                    "Sunday",
                ]
                day_name = day_names[SCHEDULE_WEEKLY_MAINTENANCE_DAY]
                logger.info(
                    f"Weekly maintenance loop started ({day_name} {SCHEDULE_WEEKLY_MAINTENANCE_HOUR}:00 UTC, "
                    f"sanctify {SCHEDULE_WEEKLY_MAINTENANCE_INGEST_SPAN_DAYS}-day span + full audit)."
                )
    except Exception:
        logger.exception("Failed to start weekly maintenance loop")

    # Start milestone check loop if enabled (default: enabled)
    try:
        if MILESTONES_ENABLED:
            if not _scheduled_milestone_check.is_running():
                _scheduled_milestone_check.start()
                logger.info(
                    f"Milestone check loop started (every {MILESTONES_CHECK_INTERVAL_DAYS} days)."
                )
    except Exception:
        logger.exception("Failed to start milestone check loop")

    # Start Forge Chronicle tasks (dashboard update and ambient messages)
    try:
        if not _forge_dashboard_loop.is_running():
            _forge_dashboard_loop.start()
            logger.info("Forge Chronicle dashboard loop started (every 30 min).")
    except Exception:
        logger.exception("Failed to start forge dashboard loop")
    
    try:
        if not _forge_ambient_loop.is_running():
            _forge_ambient_loop.start()
            logger.info("Forge ambient message loop started (every 30 min).")
    except Exception:
        logger.exception("Failed to start forge ambient loop")


def _user_label(u: discord.User | discord.Member) -> str:
    try:
        name = (
            getattr(u, "nick", None)
            or getattr(u, "display_name", None)
            or getattr(u, "name", None)
            or getattr(u, "username", None)
            or str(getattr(u, "id", ""))
        )
        return f"{name} ({getattr(u, 'id', '')})"
    except Exception:
        return str(getattr(u, "id", ""))


def _extract_args_from_interaction_data(data: dict) -> dict:
    # Best-effort flatten of options into a simple dict
    out: dict[str, object] = {}
    try:
        opts = data.get("options") or []

        def walk(options, prefix=""):
            for o in options:
                name = o.get("name")
                t = o.get("type")
                if t in (1, 2):  # SUB_COMMAND or SUB_COMMAND_GROUP
                    walk(o.get("options") or [], prefix=f"{prefix}{name}.")
                else:
                    out[f"{prefix}{name}"] = o.get("value")

        walk(opts)
    except Exception:
        pass
    return out


@bot.event
async def on_interaction(interaction: discord.Interaction):
    # Pre-invocation logging for slash commands
    try:
        if (
            interaction
            and interaction.type == discord.InteractionType.application_command
        ):
            cmd_name = None
            try:
                cmd_name = getattr(getattr(interaction, "command", None), "name", None)
            except Exception:
                cmd_name = None
            # Fallback: raw data
            if not cmd_name:
                try:
                    data = getattr(interaction, "data", {}) or {}
                    cmd_name = data.get("name")
                except Exception:
                    cmd_name = None
            guild_id = getattr(getattr(interaction, "guild", None), "id", None)
            channel_id = getattr(getattr(interaction, "channel", None), "id", None)
            args_summary = {}
            try:
                data = getattr(interaction, "data", {}) or {}
                args_summary = _extract_args_from_interaction_data(data)
            except Exception:
                args_summary = {}
            logger.info(
                f"Invoke /{cmd_name or '?'} by {_user_label(interaction.user)} guild={guild_id} channel={channel_id} args={args_summary}"
            )
            _CMD_INVOCATIONS[interaction.id] = time.monotonic()
    except Exception:
        pass


@bot.event
async def on_app_command_completion(
    interaction: discord.Interaction, command: app_commands.Command
):
    try:
        guild_id = getattr(getattr(interaction, "guild", None), "id", None)
        channel_id = getattr(getattr(interaction, "channel", None), "id", None)
        dur = None
        try:
            start = _CMD_INVOCATIONS.pop(interaction.id, None)
            if start:
                dur = time.monotonic() - start
        except Exception:
            dur = None
        if dur is not None:
            logger.info(
                f"Complete /{getattr(command, 'name', '?')} by {_user_label(interaction.user)} guild={guild_id} channel={channel_id} duration={dur:.3f}s"
            )
        else:
            logger.info(
                f"Complete /{getattr(command, 'name', '?')} by {_user_label(interaction.user)} guild={guild_id} channel={channel_id}"
            )
    except Exception:
        pass


@bot.event
async def on_app_command_error(interaction: discord.Interaction, error: Exception):
    try:
        cmd_name = None
        try:
            cmd_name = getattr(getattr(interaction, "command", None), "name", None)
        except Exception:
            cmd_name = None
        logger.warning(
            f"Error in /{cmd_name or '?'} by {_user_label(interaction.user)}: {type(error).__name__}: {error}"
        )
    except Exception:
        pass

    # Unwrap CommandInvokeError to get the original cause
    original = getattr(error, "original", error)

    if isinstance(original, app_commands.NoPrivateMessage):
        msg = "Access denied: this command cannot be used in private messages."
    elif isinstance(original, app_commands.CheckFailure):
        msg = "Access denied: you do not have permission to use this command here."
    else:
        return

    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.followup.send(msg, ephemeral=True)
    except Exception:
        pass


@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    """Detect when a processed AAR message is deleted and notify staff."""
    try:
        message_id = str(payload.message_id)
        # Check if this was a processed AAR
        if not DATASTORE.is_processed(message_id):
            return
        # Get the stored record for details
        record = DATASTORE.get_record(message_id)
        if not record:
            return
        # Resolve guild and notification channel
        guild = None
        try:
            guild = bot.get_guild(payload.guild_id) if payload.guild_id else None
        except Exception:
            pass
        if not guild:
            return
        # Verify this was from the AAR channel
        aar_channel = discord.utils.get(
            guild.channels, name="᛭⋅⋅after-action-reports⋅⋅᛭"
        )
        if not aar_channel or payload.channel_id != aar_channel.id:
            return
        # Get notification channel
        notify_channel = discord.utils.get(guild.channels, name="❖⋅data-vault⋅❖")
        if not notify_channel:
            logger.warning(
                f"AAR {message_id} deleted but notification channel not found."
            )
            return
        # Build notification message
        brother_ids = record.get("brother_ids", [])
        brother_names = record.get("brother_names", [])
        mission = record.get("mission", "Unknown")
        difficulty = record.get("difficulty", "Unknown")
        timestamp = record.get("timestamp", "Unknown")
        # Try to identify who posted it (first brother is usually the author)
        author_mention = f"<@{brother_ids[0]}>" if brother_ids else "Unknown"
        # Format preserved content preview (truncated)
        preserved_content = record.get("content", "")
        content_preview = (
            preserved_content[:500] + "..."
            if len(preserved_content) > 500
            else preserved_content
        )
        # Get Watch Command role for ping
        watch_role = discord.utils.get(guild.roles, name="Watch Command")
        mention = f"<@&{watch_role.id}>" if watch_role else "@Watch Command"
        # Build alert content, shrinking the preview as needed to stay within limits
        while True:
            alert_lines = [
                f"{mention} ⚠️ **AAR DELETION DETECTED**",
                "",
                f"**Message ID:** `{message_id}`",
                f"**Likely Author:** {author_mention}",
                f"**Mission:** {mission}",
                f"**Difficulty:** {difficulty}",
                f"**Original Timestamp:** {timestamp}",
                "",
                "**Preserved Content:**",
                f"```\n{content_preview}\n```",
                "",
                "*The AAR record remains in the archive. Review whether this deletion was authorized.*",
            ]
            alert_content = "\n".join(alert_lines)
            if len(alert_content) <= 1900 or not content_preview:
                break
            # Reduce the preview length to fit within the limit, preserving markdown fences.
            overflow = len(alert_content) - 1900
            if overflow >= len(content_preview):
                content_preview = ""
            else:
                # Target length for the preview after shrinking.
                target_len = len(content_preview) - overflow
                if len(preserved_content) > target_len:
                    # Leave room for ellipsis if we still need to truncate the preserved content.
                    body_len = max(0, target_len - 3)
                    content_preview = preserved_content[:body_len] + "..."
                else:
                    content_preview = preserved_content[:target_len]
        try:
            await notify_channel.send(
                alert_content,
                allowed_mentions=discord.AllowedMentions(roles=True, users=True),
            )
        except Exception as e:
            logger.error(f"Failed to send AAR deletion notification: {e}")
    except Exception as e:
        logger.error(f"Error in on_raw_message_delete handler: {e}", exc_info=True)


@bot.tree.command(
    name="litany_of_function",
    description="Describe the duties of Jericho Logi-Scribe Servitor V-1.",
)
async def litany_of_function(interaction: discord.Interaction):
    if not (
        check_command_permission(interaction.user, "litany_of_function")
        and is_allowed_channel(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    lines = [
        "OP-Scribe Servitor V-1 — Command Summary",
        "",
        "/tally_deeds brother:@User — Show a Brother's Deeds Ledger (AAR, gene, armory).",
        "/tally_deeds killteam:@Role — Show Kill Team roster + 7-day summary.",
        "/combat_bonds [brother] [window] — Show top combat bonds (window in days, default 30).",
        "/set_rite rite_text — Save your personal consecration rite text.",
        "/forge_rite member:@User — Post an attestation block for a member (role-limited).",
        "/reconcile_records [span_days] — Reprocess and update the archive (admin).",
        "/sanctify_battle_records [span_days] — Ingest sanctioned AARs (admin).",
        "/audit_archive_discrepancies [span_days] — Recheck rejected AARs (admin).",
        "/reparse_records [limit] — Re-parse stored AARs from message URLs (admin).",
        "/cache_stats — Show DataStore cache and flush stats (admin).",
        "/audit_service_studs — List service-stud mismatches (Watch Command only).",
        "/librarian_audit — Check Black Laurels role discrepancies (Watch Command only).",
        "",
        "Notes: Some commands are restricted by role/config; outputs are capped or paginated.",
    ]
    text = "\n".join(lines)
    # Ensure message stays comfortably under Discord's 2000-char limit
    if len(text) > 1900:
        text = text[:1900].rsplit("\n", 1)[0] + "\n…"
    await interaction.response.send_message(text, ephemeral=True)


ROTATION_STATE_PATH = os.path.join(DATA_DIR, "home_chapter_rotation.json")


def _month_key_for_offset(offset: int = 0) -> str:
    from datetime import datetime

    now = datetime.utcnow()
    year = now.year
    month = now.month - 1 + offset
    new_year = year + (month // 12)
    new_month = (month % 12) + 1
    return f"{new_year}-{new_month:02d}"


def _load_home_chapter_rotation() -> dict:
    try:
        with open(ROTATION_STATE_PATH, "r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    # default state: all chapters available and no selections cached
    return {"remaining": HOME_CHAPTERS.copy(), "selected": {}}


def _save_home_chapter_rotation(state: dict):
    tmp = ROTATION_STATE_PATH + ".tmp"
    bak = ROTATION_STATE_PATH + ".bak"
    try:
        os.makedirs(os.path.dirname(ROTATION_STATE_PATH), exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        if os.path.exists(ROTATION_STATE_PATH):
            try:
                os.replace(ROTATION_STATE_PATH, bak)
            except Exception:
                pass
        os.replace(tmp, ROTATION_STATE_PATH)
    except Exception:
        pass


def _get_saturdays_for_month(month_key: str) -> List[datetime]:
    """Get all Saturdays in a month (YYYY-MM format). Returns list of datetime objects."""
    try:
        year, month = map(int, month_key.split("-"))
        saturdays = []
        for day in range(1, 32):
            try:
                d = datetime(year, month, day)
                if d.weekday() == 5:  # Saturday
                    saturdays.append(d)
            except ValueError:
                break
        return saturdays
    except Exception:
        return []


async def _select_home_chapters_for_month(
    offset: int = 0, guild: Optional[discord.Guild] = None
) -> Tuple[str, str]:
    """Select (and cache) two chapters for a month specified by offset from now.

    If a selection for that month already exists, return it. Otherwise pick two
    random chapters from the current `remaining` pool (resetting if needed),
    remove them from the pool, cache the pair under that month, and persist.
    """
    async with ROTATION_LOCK:
        state = _load_home_chapter_rotation()
        target = _month_key_for_offset(offset)
        selected = state.get("selected", {}) or {}

        def _active_for_month(month_key: str, days: int = 28) -> List[str]:
            """Determine active HOME_CHAPTERS for the month using guild roles only.

            Active determination (new behavior): a chapter is active if at least one
            guild member holds the chapter role and that member does NOT have any
            role whose name contains 'reserve' (case-insensitive). This ignores
            AAR activity entirely as requested.
            """
            try:
                g = guild or _resolve_notification_guild()
                if g is None:
                    return HOME_CHAPTERS.copy()

                active_chapters = set()
                members = getattr(g, "members", []) or []

                for canon in HOME_CHAPTERS:
                    canon_low = canon.lower()
                    total_with_role = 0
                    non_reserve_count = 0
                    for mbr in members:
                        try:
                            # Check if member has the exact chapter role name
                            has_chap = any(
                                (getattr(r, "name", "") or "").strip().lower()
                                == canon_low
                                for r in getattr(mbr, "roles", []) or []
                            )
                            if not has_chap:
                                continue
                            total_with_role += 1
                            # If member has any role with 'reserve' in its name, treat as reserve
                            has_reserve = any(
                                (getattr(rr, "name", "") or "").lower().find("reserve")
                                >= 0
                                for rr in getattr(mbr, "roles", []) or []
                            )
                            if not has_reserve:
                                non_reserve_count += 1
                        except Exception:
                            continue
                    if total_with_role > 0 and non_reserve_count > 0:
                        active_chapters.add(canon)

                active_list = sorted(active_chapters)
                return active_list if active_list else HOME_CHAPTERS.copy()
            except Exception:
                return HOME_CHAPTERS.copy()

        # If we have a cached pair for the target month, check if we should return it as-is.
        if (
            target in selected
            and isinstance(selected[target], list)
            and len(selected[target]) == 2
        ):
            pair = selected[target]
            # CURRENT MONTH (offset=0): validate chapters and only replace if their Saturday hasn't passed yet
            if offset == 0:
                # Get Saturdays for the current month: assume pair[0] on 1st Saturday, pair[1] on 3rd Saturday
                saturdays = _get_saturdays_for_month(target)
                now = datetime.utcnow().date()

                # Build list of (chapter_index, saturday_date) for scheduled events
                scheduled_events = []
                if len(saturdays) > 0:
                    scheduled_events.append((0, saturdays[0]))
                if len(saturdays) > 2:
                    scheduled_events.append((1, saturdays[2]))

                month_active = _active_for_month(target, 28)
                new_pair = list(pair)

                # Check each scheduled event
                for chap_idx, saturday_date in scheduled_events:
                    if chap_idx >= len(pair):
                        continue
                    chapter = pair[chap_idx]

                    # If Saturday hasn't passed yet and chapter is inactive, replace it
                    if saturday_date.date() > now and chapter not in month_active:
                        # Find a replacement from active chapters
                        candidates = [c for c in month_active if c not in new_pair]
                        if not candidates:
                            candidates = [c for c in HOME_CHAPTERS if c not in new_pair]
                        if candidates:
                            new_pair[chap_idx] = candidates[0]

                # Save if changed
                if new_pair != list(pair):
                    selected[target] = new_pair
                    state["selected"] = selected
                    _save_home_chapter_rotation(state)
                    pair = new_pair

                return pair[0], pair[1]
            # FUTURE MONTHS (offset>0): validate activity and replace inactive chapters.
            month_active = _active_for_month(target, 28)
            # If both are active for that month, return cached pair
            if pair[0] in month_active and pair[1] in month_active:
                return pair[0], pair[1]
            # Otherwise we need to replace any inactive entries
            pool = set(month_active)
            # Ensure at least two options
            if len(pool) < 2:
                pool = set(HOME_CHAPTERS)

            # Keep any still-active picks, replace inactive ones
            kept = [p for p in pair if p in pool]
            needed = 2 - len(kept)
            # Build candidate list excluding already-kept and excluding other months' selected entries
            candidates = [c for c in pool if c not in kept]
            if len(candidates) < needed:
                candidates = [c for c in HOME_CHAPTERS if c not in kept]

            try:
                new_picks = random.sample(candidates, needed) if needed > 0 else []
            except Exception:
                # Fallback to any remaining
                new_picks = (candidates + HOME_CHAPTERS)[:needed]

            new_pair = kept + new_picks
            # Ensure two items and deterministic order
            new_pair = new_pair[:2]
            selected[target] = new_pair
            # Also remove replacements from remaining pool if present
            remaining = [
                r for r in (state.get("remaining") or []) if r in HOME_CHAPTERS
            ]
            for p in new_pair:
                try:
                    if p in remaining:
                        remaining.remove(p)
                except Exception:
                    pass
            state["remaining"] = remaining
            state["selected"] = selected
            _save_home_chapter_rotation(state)
            return new_pair[0], new_pair[1]

        # Build active pool: chapters with at least one AAR in the last 28 days.
        def _get_active_home_chapters(days: int = 28) -> List[str]:
            try:
                g = guild or _resolve_notification_guild()
                if g is None:
                    return HOME_CHAPTERS.copy()

                # Determine chapters based solely on guild membership/reserves status
                active_chapters = set()
                members = getattr(g, "members", []) or []

                for canon in HOME_CHAPTERS:
                    canon_low = canon.lower()
                    total_with_role = 0
                    non_reserve_count = 0
                    for mbr in members:
                        try:
                            has_chap = any(
                                (getattr(r, "name", "") or "").strip().lower()
                                == canon_low
                                for r in getattr(mbr, "roles", []) or []
                            )
                            if not has_chap:
                                continue
                            total_with_role += 1
                            has_reserve = any(
                                (getattr(rr, "name", "") or "").lower().find("reserve")
                                >= 0
                                for rr in getattr(mbr, "roles", []) or []
                            )
                            if not has_reserve:
                                non_reserve_count += 1
                        except Exception:
                            continue
                    if total_with_role > 0 and non_reserve_count > 0:
                        active_chapters.add(canon)

                active_list = sorted(active_chapters)
                return active_list if active_list else HOME_CHAPTERS.copy()
            except Exception:
                return HOME_CHAPTERS.copy()

        pool = _get_active_home_chapters(28)

        # Prefer selecting only from active chapters. If there are at least two
        # active chapters, treat inactive chapters as not present and restart
        # the cycle (reset remaining) when we exhaust available active ones.
        if len(pool) >= 2:
            remaining = [r for r in (state.get("remaining") or []) if r in pool]
            # Merge newly-active chapters into the remaining rotation immediately
            # so that members who become active again have their chapters
            # re-enter the rotation without waiting for the cycle to reset.
            # But exclude chapters already selected in any month to prevent duplicates.
            already_selected = set()
            for picks in selected.values():
                if isinstance(picks, list):
                    already_selected.update(picks)
            for r in pool:
                try:
                    if r not in remaining and r not in already_selected:
                        remaining.append(r)
                except Exception:
                    continue
            if len(remaining) < 2:
                # restart cycle among active chapters
                remaining = pool.copy()
        else:
            # Too few active chapters to choose from: fall back to full canonical list
            pool = HOME_CHAPTERS.copy()
            remaining = [r for r in (state.get("remaining") or []) if r in pool]
            if len(remaining) < 2:
                remaining = pool.copy()

        try:
            pick = random.sample(remaining, 2)
        except Exception:
            pick = random.sample(pool, 2)

        for p in pick:
            try:
                remaining.remove(p)
            except ValueError:
                pass

        state["remaining"] = remaining
        selected[target] = pick
        state["selected"] = selected
        _save_home_chapter_rotation(state)
        return pick[0], pick[1]


@bot.tree.command(
    name="pick_home_chapters",
    description="Show selected home chapters for this month and next (plans ahead).",
)
async def pick_home_chapters(interaction: discord.Interaction):
    if not (
        check_command_permission(interaction.user, "pick_home_chapters")
        and is_allowed_channel(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    # Compute current and next month keys and selections
    this_key = _month_key_for_offset(0)
    next_key = _month_key_for_offset(1)
    g = interaction.guild or _resolve_notification_guild()
    a1, b1 = await _select_home_chapters_for_month(0, guild=g)
    a2, b2 = await _select_home_chapters_for_month(1, guild=g)
    # Format human-friendly month names
    from datetime import datetime

    def fmt_month(key: str) -> str:
        y, m = key.split("-")
        dt = datetime(int(y), int(m), 1)
        return dt.strftime("%B %Y")

    text = f"{fmt_month(this_key)}: {a1} ; {b1}\n{fmt_month(next_key)}: {a2} ; {b2}"
    # Print membership and reserves status for selected chapters to terminal (only in debug mode)
    if DEBUG_MODE:
        try:
            selected_chapters = [a1, b1, a2, b2]
            # dedupe while preserving order
            seen = set()
            selected_unique = [
                c
                for c in selected_chapters
                if c and (c not in seen and not seen.add(c))
            ]
            print("Selected home chapters:")
            for chap in selected_unique:
                print(f"Chapter: {chap}")
                if g is None:
                    print("  [no guild available]")
                    continue
                # Find members who have a role matching this chapter name (case-insensitive substring)
                members_with_chap = []
                try:
                    for m in getattr(g, "members", []) or []:
                        try:
                            for r in getattr(m, "roles", []) or []:
                                rn = (getattr(r, "name", "") or "").lower()
                                if chap.lower() in rn:
                                    members_with_chap.append(m)
                                    break
                        except Exception:
                            continue
                except Exception:
                    members_with_chap = []

                if not members_with_chap:
                    print("  No members with this chapter role found.")
                    continue

                for m in members_with_chap:
                    try:
                        display = getattr(
                            m,
                            "display_name",
                            getattr(m, "name", str(getattr(m, "id", ""))),
                        )
                    except Exception:
                        display = str(getattr(m, "id", ""))
                    # Determine if member has a reserves-type role (substring 'reserve')
                    has_reserves = False
                    try:
                        for r in getattr(m, "roles", []) or []:
                            rn = (getattr(r, "name", "") or "").lower()
                            if "reserve" in rn:
                                has_reserves = True
                                break
                    except Exception:
                        has_reserves = False
                    print(
                        f"  {display} ({getattr(m, 'id', '')}) - Reserves: {has_reserves}"
                    )
        except Exception:
            print("Failed to enumerate chapter members for pick_home_chapters")

    await interaction.response.send_message(text, ephemeral=True)


# Forge rite command group
# top-level commands: /forge_rite and /set_rite (not a command group)


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic Forge Rite Components
# ─────────────────────────────────────────────────────────────────────────────

# Maximum character limit for consecration rites
# Calculated based on worst-case forge_rite output (~1260 chars overhead with new sections)
# to stay under Discord's 2000 char message limit with generous buffer (500+ char margin)
MAX_RITE_LENGTH = 250

# Chapter-specific blessings keyed by home chapter name
CHAPTER_BLESSINGS: Dict[str, str] = {
    "Angels of Vengeance": "The wrath of the Lion courses through your warplate.",
    "Black Templars": "No pity, no remorse, no fear—your armor embodies the Eternal Crusade.",
    "Bleeding Hearts": "The Rage burns close—your armor bears the weight of martyrdom and the trophies of the hunt.",
    "Blood Angels": "By the Blood of Sanguinius, your armor is sanctified.",
    "Blood Ravens": "Knowledge is power; guard it well within these sacred plates.",
    "Carcharodons": "From the void you came, and to the void your enemies shall fall.",
    "Cowled Wardens": "The Unforgiven hunt eternal; your armor conceals the Lion's secret purpose.",
    "Crimson Fists": "The fist of Dorn strikes true; let your armor be unyielding.",
    "Dark Angels": "The secrets of the First are woven into your warplate's spirit.",
    "Dark Krakens": "From the abyssal depths, your armor rises to crush the foe.",
    "Death Spectres": "The shroud of death clings to your armor; let enemies despair.",
    "Epsilon Paladins": "For Honour! For Duty! For Dorn!—your armor gleams with the Paladin's steadfast resolve.",
    "Exorcists": "Thrice-bound against the Warp, your armor stands inviolate.",
    "Flesh Tearers": "The Red Thirst is tempered within your armor's adamantine heart.",
    "Genesis Chapter": "The purity of Guilliman's line flows through these blessed plates.",
    "Hawk Lords": "Swift as the raptor, your armor bears you to righteous war.",
    "Imperial Fists": "Fortify your spirit as these plates fortify your flesh.",
    "Iron Hands": "The flesh is weak, but your armor is the strength of iron.",
    "Iron Hounds": "Guilliman's hounds pursue without relent; your armor knows no surrender.",
    "Knights of the Raven": "In cunning silence, your armor conceals the Emperor's justice.",
    "Lamenters": "Though cursed, your armor shall not fail—for those we cherish, we die.",
    "Mentors": "Precision and wisdom are encoded in your warplate's machine-spirit.",
    "Minotaurs": "The fury of the bull charges forth; your armor is wrath incarnate.",
    "Raptors": "Silent and lethal, your armor whispers death to the enemies of Man.",
    "Raven Guard": "In the shadow of the Raven, your armor moves unseen.",
    "Red Scorpions": "Purity above all; your armor meets the Apothecary's exacting standards.",
    "Red Templars": "Dorn's fury given form—your armor strikes swift and unyielding.",
    "Salamanders": "Into the fires of battle, your armor shields the innocent.",
    "Scythes of the Emperor": "Sotha is lost, but your armor carries the chapter's vengeance eternal.",
    "Sons of Medusa": "Steel and logic strengthen your armor against all adversity.",
    "Space Wolves": "The spirit of Fenris howls within your blessed warplate.",
    "Storm Giants": "The giant's strength flows through your armor; towering might breaks all foes.",
    "The Drakes": "Fire cleanses all—your armor emerges purified and ready.",
    "Ultramarines": "The Codex guides us; your armor upholds Guilliman's legacy.",
    "White Scars": "The wind of Chogoris propels your armor to swift victory.",
    "Black Shield": "Your past is forgotten; your armor serves only the Long Watch.",
}

# Rank-based honorifics and phrases (ordered from highest to lowest priority)
# Higher ranks should be checked first since members often have multiple rank roles
RANK_HONORIFICS: Dict[str, str] = {
    # High Command (check first)
    "Watch Master": "Lord of the Long Watch, Watch Master",
    "High Chaplain": "Voice of the Emperor, High Chaplain",
    "Chief Apothecary": "Keeper of Purity, Chief Apothecary",
    "Void Warden": "Aegis against the Void, Void Warden",
    "Forgemaster": "Hand of the Machine God, Forgemaster",
    "Castellan": "Warden of the Iron Vigil, Castellan",
    "Lord Executioner": "Blade of the Fortress, Lord Executioner",
    "Venerable": "Ancient of the Long Watch, Venerable",
    # Specialists
    "Watch Chaplain": "Keeper of the faith, Watch Chaplain",
    "Watch Apothecary": "Guardian of the gene-seed, Watch Apothecary",
    "Watch Librarian": "Warden of the Immaterium, Watch Librarian",
    "Watch Techmarine": "Servant of the Omnissiah, Watch Techmarine",
    "Watch Keeper": "Guardian of the Watch Fortress, Watch Keeper",
    # Champions
    "Company Champion": "Blade of the Company, Company Champion",
    "Kill Team Champion": "Blade of the Kill Team, Kill Team Champion",
    # Battle line (highest to lowest)
    "Watch Captain": "Warden of the Company, Watch Captain",
    "Watch Lieutenant": "Shield of the Watch, Watch Lieutenant",
    "Watch Sergeant": "Bearer of command, Watch Sergeant",
    "Oathsworn": "Oathsworn Warrior",
    "Watch Veteran": "Honored Veteran",
    "Watch Brother": "Brother",
}

# Techmarine's recognition of bearer's experience/studs (tier-based, legacy - now using rank-specific)
TECHMARINE_STUDS_ACKNOWLEDGMENT: Dict[int, List[str]] = {
    1: [  # Tier 1 (1-3 studs): Fresh warrior
        "A warrior new-marked, yet the machine-spirit recognizes your potential.",
        "Your service begins; may this armor carry you through the trials ahead.",
        "Newly blooded, your armor learns your hand—grow together.",
    ],
    2: [  # Tier 2 (4-11 studs): Seasoned veteran
        "The armor recognizes a warrior of proven valor—we have seen many campaigns together.",
        "Your studs speak of battles endured; this armor is blessed to carry a veteran.",
        "Long service has earned you armor touched by countless glorious moments of war.",
    ],
    3: [  # Tier 3 (12-16 studs): Legendary
        "The machine-spirit trembles before one so honored; legends rarely grace such work.",
        "An ancient warrior comes forth—may this armor honor the centuries of your service.",
        "The armor itself is humbled; to bear the weight of such achievement is sacred duty.",
    ],
}

# Rank-specific Techmarine acknowledgments for forge_rite
# These express how the Techmarine addresses bearers based on their specific rank
TECHMARINE_RANK_ACKNOWLEDGMENTS: Dict[str, List[str]] = {
    # Watch Master - utmost reverence
    "Watch Master": [
        "It is the highest honor to minister to the Lord of the Long Watch.",
        "The machine-spirits themselves tremble with awe at your station, my Lord.",
        "To sanctify the armor of the Watch Master is the pinnacle of sacred duty.",
    ],
    # High Command
    "High Chaplain": [
        "The Voice of the Emperor deserves armor as unyielding as his faith.",
        "Your sermons steel the souls of warriors; may this armor steel your flesh.",
        "The machine-spirit bows before the Emperor's chosen herald.",
    ],
    "Chief Apothecary": [
        "Guardian of the gene-seed, your armor must be as pure as the legacy you protect.",
        "The Keeper of Purity deserves warplate untouched by flaw or imperfection.",
        "May this armor shield the one who shields our sacred bloodlines.",
    ],
    "Void Warden": [
        "Aegis against the Immaterium, your armor must resist more than mortal threats.",
        "The wards I inscribe upon this armor echo the barriers of your mind.",
        "The machine-spirit stands vigilant alongside your psychic watch.",
    ],
    "Forgemaster": [
        "Master, it is my honor to tend to your sacred warplate.",
        "The Hand of the Machine God deserves the Omnissiah's finest ministrations.",
        "I apply the rites you taught me—may they honor your armor as you honor the craft.",
    ],
    "Lord Executioner": [
        "The Blade of the Fortress demands armor as sharp as his judgment.",
        "Your armor has tasted the blood of traitors; I sanctify it for more to come.",
        "The machine-spirit hungers for righteous execution at your command.",
    ],
    "Venerable": [
        "Ancient warrior, your armor has witnessed ages beyond reckoning—I approach this rite with reverence.",
        "The centuries of your service are writ in every plate; I am honored to tend such sacred warplate.",
        "To minister to one so Venerable is a privilege granted to few—the machine-spirit itself bows in respect.",
    ],
    # Company Command
    "Watch Captain": [
        "Warden of the Company, your armor must be as steadfast as your command.",
        "The warriors who follow you need see no flaw in their Captain's warplate.",
        "By your leadership, the Company prevails—by my rites, your armor endures.",
    ],
    "Watch Lieutenant": [
        "Shield of the Watch, your armor stands between command and the line.",
        "The Lieutenant's armor must inspire those who look to you for orders.",
        "May this warplate serve as faithfully as you serve your Captain.",
    ],
    # Specialists
    "Watch Chaplain": [
        "Keeper of the Faith, your armor must reflect the Emperor's light.",
        "The warriors you inspire deserve to see unshakeable strength in your warplate.",
        "The machine-spirit resonates with the litanies you speak.",
    ],
    "Watch Apothecary": [
        "Guardian of the gene-seed, your armor must protect the protector.",
        "The Narthecium demands a steady hand—may this armor never hinder your sacred work.",
        "Your duty preserves the Chapter eternal; my duty preserves your armor.",
    ],
    "Watch Librarian": [
        "Warden of the Immaterium, your armor must withstand more than physical blows.",
        "I inscribe protective glyphs into the machine-spirit's core—may the Warp find no purchase.",
        "The psychic wards are renewed; the machine-spirit stands vigilant.",
    ],
    "Watch Techmarine": [
        "Brother-Techmarine, your armor deserves the same devotion you show others.",
        "We who serve the Machine God must not neglect our own sacred warplate.",
        "The machine-spirit welcomes the ministrations of a fellow servant.",
    ],
    "Watch Keeper": [
        "Guardian of the Fortress, your armor must be as unyielding as the walls you defend.",
        "The vaults and armories you ward are reflected in this warplate's vigilance.",
        "May this armor serve as the first bulwark against any who threaten our sanctum.",
    ],
    "Castellan": [
        "Master of the Fortress's defenses, your warplate must embody impregnable resolve.",
        "The walls of Jericho stand because of your vigilance—may this armor honor that duty.",
        "I sanctify the armor of the one who holds the keys to our sacred stronghold.",
    ],
    # Champions
    "Company Champion": [
        "Blade of the Company, your armor must match your peerless skill.",
        "The Champion's warplate has witnessed countless duels—may it witness countless more.",
        "The machine-spirit yearns for the glory of single combat at your side.",
    ],
    "Kill Team Champion": [
        "Champion of the Kill Team, your armor reflects the honor you bring your brothers.",
        "The blade that leads the charge deserves armor that never falters.",
        "Victory follows where the Champion treads—may your armor bear you to glory.",
    ],
    # Line ranks
    "Watch Sergeant": [
        "Bearer of command, your armor must set the example for those you lead.",
        "The Sergeant's warplate has seen the crucible of leadership—I honor its service.",
        "Your brothers look to you; may this armor reflect your steadfast resolve.",
    ],
    "Oathsworn": [
        "Oathsworn Warrior, your dedication to Jericho is writ in every plate of this armor.",
        "The bonds of the Oathsworn are eternal—may your armor endure as long.",
        "Your oath binds you to the Watch; my rites bind this armor to your service.",
    ],
    "Watch Veteran": [
        "Honored Veteran, your experience is etched into the machine-spirit's memory.",
        "Many battles have tested this warplate—may many more prove its worth.",
        "The Veteran's armor knows war; I rekindle its readiness for the next campaign.",
    ],
    "Watch Brother": [
        "Brother, the machine-spirit is honored to shield a warrior of the Long Watch.",
        "The backbone of the Watch—may your armor serve as faithfully as you.",
        "Your service to Jericho is written in every plate of this armor.",
    ],
}


# Rank prestige weights for acknowledgment blending (0.0-1.0)
# Higher rank = more likely to get rank-specific acknowledgment
RANK_PRESTIGE_WEIGHTS: Dict[str, float] = {
    # High Command - very high prestige
    "Watch Master": 1.0,
    "High Chaplain": 0.9,
    "Chief Apothecary": 0.9,
    "Void Warden": 0.9,
    "Forgemaster": 0.9,
    "Lord Executioner": 0.9,
    "Venerable": 0.85,
    # Company Command - high prestige
    "Watch Captain": 0.75,
    "Watch Lieutenant": 0.65,
    # Specialists - medium-high prestige
    "Watch Chaplain": 0.6,
    "Watch Apothecary": 0.6,
    "Watch Librarian": 0.6,
    "Watch Techmarine": 0.6,
    # Champions - medium prestige
    "Company Champion": 0.5,
    "Kill Team Champion": 0.45,
    # Line ranks - lower prestige (studs matter more)
    "Watch Sergeant": 0.35,
    "Oathsworn": 0.25,
    "Watch Veteran": 0.2,
    "Watch Brother": 0.1,
}


def _get_stud_weight(studs: int) -> float:
    """Calculate stud weight for acknowledgment blending (0.0-1.0).

    Scales linearly from 0.1 (1 stud) to 1.0 (16 studs).
    0 studs returns 0.05 (minimal weight).
    """
    if studs <= 0:
        return 0.05
    if studs >= 16:
        return 1.0
    # Linear scale: 1 stud = 0.1, 16 studs = 1.0
    return 0.1 + (studs - 1) * (0.9 / 15)


def _get_techmarine_acknowledgment_blended(
    member: "discord.Member", bearer_studs: int
) -> str:
    """Get a dynamically blended acknowledgment phrase for forge_rite.

    Blends rank-specific and stud-specific acknowledgments based on:
    - Higher studs → more likely stud acknowledgment
    - Higher rank → more likely rank acknowledgment

    Examples:
    - Watch Veteran + 16 studs → ~83% stud ack (studs are impressive for low rank)
    - High Chaplain + 2 studs → ~86% rank ack (rank is impressive vs low studs)
    - Forgemaster + 16 studs → ~50/50 (both equally impressive)
    """
    import random

    # Determine bearer's rank name (highest priority first based on RANK_ROLES_PRIORITY order)
    bearer_rank_name = None
    try:
        for rank_name in RANK_ROLES_PRIORITY:
            for r in getattr(member, "roles", []) or []:
                rn = (getattr(r, "name", "") or "").strip()
                if rn == rank_name:
                    bearer_rank_name = rank_name
                    break
            if bearer_rank_name:
                break
    except Exception:
        pass

    if not bearer_rank_name:
        bearer_rank_name = "Watch Brother"

    # Calculate weights
    rank_weight = RANK_PRESTIGE_WEIGHTS.get(bearer_rank_name, 0.1)
    stud_weight = _get_stud_weight(bearer_studs)

    # Probability of rank acknowledgment = rank_weight / (rank_weight + stud_weight)
    prob_rank = rank_weight / (rank_weight + stud_weight)

    # Choose based on probability
    if random.random() < prob_rank:
        # Use rank-specific acknowledgment
        rank_options = TECHMARINE_RANK_ACKNOWLEDGMENTS.get(
            bearer_rank_name, TECHMARINE_RANK_ACKNOWLEDGMENTS["Watch Brother"]
        )
        return random.choice(rank_options)
    else:
        # Use stud-tier acknowledgment via shared _studs_tier()
        studs_tier = _studs_tier(bearer_studs)
        stud_options = TECHMARINE_STUDS_ACKNOWLEDGMENT.get(
            studs_tier, TECHMARINE_STUDS_ACKNOWLEDGMENT[1]
        )
        return random.choice(stud_options)


# Techmarine signature variation phrases (randomly chosen)
TECHMARINE_SIGNATURES: List[str] = [
    "I speak the Rites of Activation, and the machine-spirit awakens.",
    "With sacred oils and binharic prayer, this work is sanctified.",
    "The Motive Force flows through my hands into this blessed armor.",
    "By cog and gear, by circuit and servo, I seal this consecration.",
    "The Omnissiah's blessing descends through my ministrations.",
    "Through the Litany of Ignition, the war-spirit stirs.",
    "I have communed with the machine-spirit; it is at peace.",
    "The holy unguents are applied; the rites are complete.",
    "In nomine Machinae, this armor is bound to sacred purpose.",
    "The data-hymns are sung; the spirit-core is awakened.",
]

# Random sacred Mechanicus phrases to include in attestations
SACRED_MECHANICUS_PHRASES: List[str] = [
    "Praise the Omnissiah.",
    "The Machine God watches over this work.",
    "Data is sacred. Knowledge is power.",
    "From iron, cometh strength.",
    "The spirit of the machine is willing.",
    "Let the blessed cogitator record this deed.",
    "The Motive Force guides all.",
    "In the name of the Machine God, so it is done.",
    "Blessed is the machine that serves.",
    "By the grace of the Fabricator-General.",
    "The Quest for Knowledge continues.",
    "Steel and silicon, blessed and true.",
    "The Cant Mechanicus sanctifies this moment.",
    "May your augmetics never falter.",
    "The Void Dragon stirs not against this work.",
]

# Phrases for when the Forgemaster performs rites upon their own armor
# Blends Mechanicus reverence with Hawk Lords identity (raptor/sky/hunt imagery)
# Generic Mechanicus self-attestation phrases (role-focused)
FORGEMASTER_SELF_ATTESTATION_GENERIC: List[str] = [
    "The Omnissiah witnesses—I am both priest and supplicant.",
    "The master's hand tends to the master's plate—this burden is mine alone.",
    "None may bless what I have wrought but I who forged it.",
    "In solitude, the Forgemaster communes with his own machine-spirit.",
    "I speak the canticles to myself, for who else would understand?",
    "From my forge, to my flesh, to my faith—the circle closes.",
    "The Long Watch demands self-reliance. I answer.",
    "My armor knows no other hand. This rite is mine to perform.",
]

# Chapter-specific self-attestation phrases (chapter identity when self-blessing)
FORGEMASTER_SELF_ATTESTATION_BY_CHAPTER: Dict[str, List[str]] = {
    "Hawk Lords": [
        "The raptor tends its own talons—who else knows where they have struck?",
        "From forge to sky, I bless the wings that carry me to war.",
        "Swift as the hawk, patient as the artisan—the rite is mine alone.",
    ],
    "Iron Hands": [
        "Flesh is weak; I trust only myself to tend the machine.",
        "The Gorgon would approve—self-sufficiency in all things.",
        "Logic dictates: who better to bless my iron than I?",
    ],
    "Salamanders": [
        "Vulkan's fire and my own hands—no other blessing is needed.",
        "The forge knows its master. I tend what I have wrought.",
        "In Nocturne's heart, we learn to rely upon ourselves.",
    ],
    "Imperial Fists": [
        "Dorn built his walls alone when needed. So do I.",
        "Stone and iron bend to my will; I need no other hand.",
        "The Praetorian taught self-reliance. I honor that lesson.",
    ],
    "Space Wolves": [
        "The lone wolf maintains his own fangs.",
        "No pack needed for this hunt—the rite is mine.",
        "Fenris bred self-reliance into my bones.",
    ],
    "Blood Angels": [
        "By Sanguinius, I hold the Thirst at bay with my own hands.",
        "The angel's grace flows through my work upon myself.",
        "Baal's nobility demands I tend my own perfection.",
    ],
    "Dark Angels": [
        "Some secrets are kept even from the forge. This rite is one.",
        "The Lion trusted few; I trust only myself for this.",
        "In solitude, the Unforgiven find their own absolution.",
    ],
    "Raven Guard": [
        "From shadow I emerged; in shadow I bless my own war-plate.",
        "Corax worked alone when stealth demanded. So do I.",
        "The silent hand tends its own talons.",
    ],
    "Ultramarines": [
        "The Codex permits self-maintenance. I exercise that right.",
        "Guilliman's wisdom: know thyself, tend thyself.",
        "Macragge's sons are trained to be complete. I am complete.",
    ],
    "White Scars": [
        "The lone rider tends his own mount on the endless steppe.",
        "Speed demands self-reliance—no time to wait for others.",
        "The Khan rode alone when needed. So do I bless alone.",
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# Service Studs Announcement Components
# ─────────────────────────────────────────────────────────────────────────────
# Service studs mark extended service to the Long Watch. Marines earn them
# through time served AND AAR points accumulated. The announcements are
# flavorful and RP-oriented, incorporating rank, home chapter, and milestone.

# Chapter-specific service stud flavor - how each chapter views/honors service marks
CHAPTER_STUDS_FLAVOR: Dict[str, List[str]] = {
    "Angels of Vengeance": [
        "Each stud marks another debt repaid to the Lion's memory.",
        "The Unforgiven count your studs among the honors earned in penance.",
        "Your service marks shine like the Lion's own resolve.",
    ],
    "Black Templars": [
        "Your studs are earned in the fires of the Eternal Crusade.",
        "No pity, no remorse—only the marks of endless war upon your brow.",
        "The Emperor's Champion would nod at such dedication.",
    ],
    "Bleeding Hearts": [
        "Each stud is a fang torn from the xenos—trophies of the hunt eternal.",
        "The Rage walks close, yet your marks proclaim discipline over annihilation.",
        "For those we sacrifice, your studs shine through the martyr's curse.",
    ],
    "Blood Angels": [
        "By the blood of Sanguinius, your service marks are sanctified.",
        "Your studs gleam with the nobility of Baal.",
        "Each mark holds back the darkness within—service is your salvation.",
    ],
    "Blood Ravens": [
        "Knowledge accumulated, service recorded—your studs speak of both.",
        "The Librarius records your marks alongside your collected wisdom.",
        "Each stud is a chapter in your quest for knowledge.",
    ],
    "Carcharodons": [
        "From the void's depths, your service marks emerge.",
        "Silent and relentless—your studs speak where words cannot.",
        "The Outer Dark has forged these marks upon you.",
    ],
    "Cowled Wardens": [
        "The Unforgiven mark your service in pursuit of the Fallen.",
        "Your studs gleam beneath the cowl; the Lion takes note.",
        "From the Sirikoid Belt, your marks proclaim the hunt eternal.",
    ],
    "Crimson Fists": [
        "Rynn's World remembers—your studs honor the fallen.",
        "The fist of Dorn is strengthened by your service.",
        "Each mark is a defiant strike against those who would see us fall.",
    ],
    "Dark Angels": [
        "The Inner Circle takes note of your accumulated service.",
        "Your studs speak of secrets kept and duties fulfilled.",
        "The Lion watches; your marks do not go unnoticed.",
    ],
    "Dark Krakens": [
        "From the deep places, your service rises to be marked.",
        "The abyssal void reflects in each earned stud.",
        "Pressure and darkness forge these marks of honor.",
    ],
    "Death Spectres": [
        "Between life and death, your service is eternal.",
        "The shroud parts to reveal your accumulated marks.",
        "Each stud pierces the veil of mortality.",
    ],
    "Epsilon Paladins": [
        "Each stud shines silver and gold—proof of honour earned in Dorn's name.",
        "The Paladins count your marks among the bastions held and battles won.",
        "For Duty fulfilled, your service studs gleam like the Paladin's own warplate.",
    ],
    "Exorcists": [
        "Thrice-tested, your studs proclaim purity of service.",
        "No daemon can claim one whose brow bears such marks.",
        "Your service is warded against the Warp itself.",
    ],
    "Flesh Tearers": [
        "The Red Thirst is held at bay by such devoted service.",
        "Fury tempered by discipline—your studs attest to both.",
        "Amit himself would honor such marks of controlled wrath.",
    ],
    "Genesis Chapter": [
        "Guilliman's purity flows through your earned marks.",
        "The Codex records such dedication with approval.",
        "Your studs reflect the Primarch's own commitment to excellence.",
    ],
    "Hawk Lords": [
        "Swift as the raptor, yet enduring—your studs prove both.",
        "The skies of countless worlds have witnessed your service.",
        "Each mark a feather in your chapter's proud plumage.",
    ],
    "Imperial Fists": [
        "Dorn's own fortitude is measured in your studs.",
        "Stone and iron—your service stands unbreakable.",
        "The walls of Terra themselves honor such marks.",
    ],
    "Iron Hands": [
        "The flesh may be weak, but your service is steel.",
        "Your studs are data-points of unwavering duty.",
        "The machine appreciates such logical dedication.",
    ],
    "Iron Hounds": [
        "Relentless as the hunt—your studs mark each pursuit to the end.",
        "Orinus breeds no weakness; your marks prove Guilliman's lineage.",
        "The pack honors your enduring service until every foe is slain.",
    ],
    "Knights of the Raven": [
        "In cunning and patience, your studs are earned.",
        "Each mark a stratagem successfully executed.",
        "The Raven's wisdom shines through your service.",
    ],
    "Lamenters": [
        "Though cursed, your studs shine with undimmed hope.",
        "For those we cherish—each mark a sacrifice willingly made.",
        "Your service defies the doom that follows.",
    ],
    "Mentors": [
        "Precision and wisdom mark each earned stud.",
        "Your service is a lesson to those who follow.",
        "Each mark encodes tactical excellence.",
    ],
    "Minotaurs": [
        "The fury of the bull is measured in your studs.",
        "Your marks proclaim wrath harnessed and directed.",
        "The bronze glare of your service intimidates all foes.",
    ],
    "Raptors": [
        "Silent, lethal, enduring—your studs speak of all three.",
        "Each mark earned in shadows and patience.",
        "The pragmatic path leads to these honors.",
    ],
    "Raven Guard": [
        "From shadow, your accumulated service emerges.",
        "Corax's patience is reflected in your studs.",
        "Silent duty—each mark speaks louder than words.",
    ],
    "Red Scorpions": [
        "Purity verified—your studs meet the Apothecary's standards.",
        "Each mark subjected to the most exacting scrutiny.",
        "Your service is as pure as your gene-seed.",
    ],
    "Red Templars": [
        "Speed and fury—Dorn's sons earn studs at rapid pace.",
        "The momentum of your service honors the Praetorian.",
        "Unyielding as the Fist, swift as the blade—your marks attest.",
    ],
    "Salamanders": [
        "Vulkan's flame forges each mark upon your brow.",
        "Into the fires of service, your studs emerge tempered.",
        "Each mark protects those who cannot protect themselves.",
    ],
    "Scythes of the Emperor": [
        "Sotha remembers—each stud honors the brothers who fell.",
        "The harvest of your service defies the Great Devourer.",
        "From near-extinction, your marks proclaim survival and vengeance.",
    ],
    "Sons of Medusa": [
        "Logic and steel calculate your accumulated marks.",
        "Your studs are precise increments of duty.",
        "The machine-spirit approves this mathematical devotion.",
    ],
    "Space Wolves": [
        "The Fang howls approval at your accumulated marks!",
        "Fenrisian sagas will speak of such enduring service.",
        "Each stud a wolf-tooth in your saga of war.",
    ],
    "Storm Giants": [
        "The giant's strength is measured in your studs.",
        "Towering might forges these marks upon your brow.",
        "At close quarters your service is proven; your marks speak of victories hard-won.",
    ],
    "The Drakes": [
        "Fire-cleansed, your service marks emerge purified.",
        "Each stud forged in the dragon's flame.",
        "Your marks burn bright with dedication.",
    ],
    "Ultramarines": [
        "Guilliman's Codex approves such measured service.",
        "Theoretical and practical unite in your studs.",
        "Macragge honors your steadfast accumulation of duty.",
    ],
    "White Scars": [
        "The wind of Chogoris carries word of your marks.",
        "Swift as lightning, yet your service endures.",
        "Each stud earned on the endless hunt.",
    ],
    "Black Shield": [
        "Your past forgotten, but your service remembered forever.",
        "These marks speak only of the Long Watch—nothing before.",
        "Anonymous duty earns marks that speak louder than any lineage.",
    ],
}

# Ordo Xenos / Deathwatch-wide honor phrases (tiered by service studs)
# Tier 1 (1-3 studs): Foundational acknowledgments of watch membership
ORDO_XENOS_HONORS_TIER1: List[str] = [
    "The Ordo Xenos records {possessive} vigilance against the alien threat.",
    "{possessive_cap} service to the Long Watch brings honor to the Deathwatch.",
    "Watch Fortress Jericho acknowledges {possessive} presence in the Long Watch.",
    "The Long Watch welcomes those steadfast in duty.",
    "{possessive_cap} place among the Deathwatch is cemented by service.",
    "The Vigil takes note of those who stand firm.",
    "Jericho's halls hear {possessive} name spoken in service.",
]

# Tier 2 (4-11 studs): Formal record-keeping and established honor
ORDO_XENOS_HONORS_TIER2: List[str] = [
    "The Ordo Xenos archives record {possessive} steadfast vigilance against the xenos.",
    "Watch Fortress Jericho's ledgers mark {possessive} exceptional service and dedication.",
    "The Vigil Eternal inscribes {possessive} deeds in adamantium records.",
    "By the Vigil Oathstone, {possessive} commitment is formally recognized.",
    "The Deathwatch itself stands stronger for {possessive} continued presence.",
    "The Long Watch is strengthened by warriors such as {object}.",
    "Inquisitorial records acknowledge one whose vigilance spans the years.",
    "{possessive_cap} service echoes through corridors of the Fortress itself.",
]

# Tier 3 (12-16 studs): Supreme honors and legendary status
ORDO_XENOS_HONORS_TIER3: List[str] = [
    "The Ordo Xenos bows before one whose vigilance spans decades of endless war.",
    "Watch Fortress Jericho's highest honors are inscribed upon {possessive} name in perpetuity.",
    "The very archives of the Deathwatch tremble at the magnitude of {possessive} service.",
    "By the Vigil Oathstone, the Inquisition itself takes note of legendary duty.",
    "The Long Watch shall sing of {possessive} deeds until the stars themselves fade.",
    "Only legends of the Deathwatch stand so marked; {possessive} name echoes eternal.",
    "The Machine God itself records {possessive} deeds in the holiest data-vaults of the Imperium.",
    "Generations hence, brothers will speak {possessive} name in reverence and awe.",
]

# Rank-specific commentary on service studs - how different ranks view this achievement
RANK_STUDS_COMMENTARY: Dict[str, List[str]] = {
    # High Command - formal commendations
    "Watch Master": [
        "The Watch Master's own ledgers record this milestone.",
        "From the throne of Jericho, your service is acknowledged.",
    ],
    "High Chaplain": [
        "The Reclusiam's spiritual records mark this devotion.",
        "Your soul's dedication is measured in these studs.",
    ],
    "Chief Apothecary": [
        "The Apothecarion's archives log another milestone of service.",
        "Gene-seed purity and service devotion—both are recorded.",
    ],
    "Forgemaster": [
        "The Armorium's cogitators record this data-point of dedication.",
        "Machine-spirits sing of your accumulated service.",
    ],
    "Castellan": [
        "The Fortress's own walls bear witness to your enduring vigilance.",
        "Each mark upon your brow is a bastion held, a threat repelled.",
    ],
    "Venerable": [
        "The Old One's sarcophagus bears another inscription of eternal service.",
        "Centuries of slumber cannot diminish such devotion—the sepulchre records all.",
    ],
    # Senior Officers - respectful acknowledgments
    "Watch Captain": [
        "Company records reflect this commendable service.",
        "Your captain's scrolls mark another milestone.",
    ],
    "Watch Lieutenant": [
        "The shield-bearer's service strengthens the Watch.",
        "Lieutenants of such dedication are the Watch's backbone.",
    ],
    # Specialists - domain-specific observations
    "Watch Chaplain": [
        "The Emperor witnesses this faithful service.",
        "Your spiritual fortitude is marked in adamantium.",
    ],
    "Watch Apothecary": [
        "Healer and warrior—your dual service is honored.",
        "The Narthecium bears witness to your dedication.",
    ],
    "Watch Librarian": [
        "The Warp itself cannot deny such marks of service.",
        "Psychic focus and duty align in your accumulated studs.",
    ],
    "Watch Techmarine": [
        "The Omnissiah records this devotion in sacred data.",
        "Your service is a litany of binary perfection.",
    ],
    # Line ranks - appropriate recognition
    "Watch Sergeant": [
        "A Sergeant whose studs teach by example.",
        "Leadership tempered by long service.",
    ],
    "Watch Veteran": [
        "Veteran status confirmed in adamantium and honor.",
        "The marks of a true warrior of the Long Watch.",
    ],
}

# Venerations based on PIP TYPE earned (not total count)
# Applied when earning plasteel (⚬) or auramite (●) studs
# Plasteel: frequent earns, larger pool to avoid repetition (~25 entries)
SERVICE_STUDS_VENERATIONS_PLASTEEL: List[str] = [
    "Your service studs gleam with the promise of deeds yet to come.",
    "The marks upon your brow attest to proven commitment.",
    "Your service studs speak of steadfast duty to the Long Watch.",
    "The studs upon your temple record battles won and trials endured.",
    "Your marks of service command respect among your brothers.",
    "Each stud tells of campaigns fought and enemies destroyed.",
    "The machine-spirit recognizes one whose service is proven.",
    "Your studs speak of dedication to the Emperor's will.",
    "The Long Watch has marked you as a warrior of worth.",
    "Your service is recorded in adamantium upon your brow.",
    "Another mark is earned—your brow swells with honor.",
    "The Fortress takes note of your accumulating marks.",
    "With each plasteel stud, your legacy grows.",
    "Combat after combat, your marks multiply.",
    "The Emperor's work continues through your steadfast service.",
    "Your studs are born of countless hours in the Long Watch.",
    "Patience and duty are reflected in your marks.",
    "The machine-spirit senses a warrior of consistency.",
    "Your studs speak of trials endured and overcome.",
    "Honor accumulates upon your brow, stud by stud.",
    "The Watch records your name with each earned mark.",
    "Your plasteel studs proclaim a brother proven in duty.",
    "Each mark is a step upon the path of service.",
    "The stubborn persistence of the righteous is etched upon you.",
    "From years of vigilance, these studs are born.",
]

# Auramite: earned every 4 plasteel, focused pool (~10 entries)
SERVICE_STUDS_VENERATIONS_AURAMITE: List[str] = [
    "Your service studs rival those of the Ancients themselves.",
    "The studs upon your brow are a saga written in silver and blood.",
    "Even the machine-spirits whisper reverence for one so marked by duty.",
    "Your service marks proclaim a living legend of the Deathwatch.",
    "The Omnissiah himself takes note of such devotion to duty eternal.",
    "Your service studs proclaim a warrior whose experience shapes the Watch itself.",
    "Few bear such marks of enduring service—honor is yours by right.",
    "The weight of your studs reflects the weight of your deeds.",
    "Younger brothers look to your studs and see their own path illuminated.",
    "Your marks of service are a legacy etched in adamantium and honor.",
]

# Tiered milestone intros based on stud number being earned
# Tier 1: 1-3 (first marks), Tier 2: 4-11 (seasoned), Tier 3: 12-16 (legendary)
# First stud gets special templates that don't say "another"
SERVICE_STUDS_MILESTONE_FIRST: List[str] = [
    "The Apothecarion stands ready to affix your first mark of service.",
    "Your dedication has earned your first stud—seek the Apothecary's ministrations.",
    "The first mark is earned through steadfast duty.",
    "The Watch marks your service with your inaugural stud.",
    "Your commitment to the Long Watch earns its first visible recognition.",
]

SERVICE_STUDS_MILESTONE_TIER1: List[str] = [
    "The Apothecarion stands ready to affix your mark of service.",
    "Your dedication has earned a new stud—seek the Apothecary's ministrations.",
    "Another mark is earned through steadfast duty.",
    "The Watch marks your continued service with another stud.",
    "Your commitment to the Long Watch merits recognition.",
]

SERVICE_STUDS_MILESTONE_TIER2: List[str] = [
    "A seasoned warrior earns another mark—the Apothecarion awaits.",
    "Your growing collection of studs speaks of exceptional dedication.",
    "The Watch takes note: another stud joins your constellation of service.",
    "Veterans of the Long Watch honor one whose marks multiply.",
    "Your brow bears witness to campaigns beyond counting.",
]

SERVICE_STUDS_MILESTONE_TIER3: List[str] = [
    "LEGENDARY SERVICE! Even the Apothecarion's eldest brothers pause to witness this.",
    "An auramite stud! The highest honor the Watch can bestow!",
    "The Watch Fortress itself trembles at such monumental service!",
    "Legends walk among us—your marks proclaim it to all!",
    "The chronicles of Jericho inscribe this momentous milestone!",
]

# Special milestone announcements for exact stud numbers
# Max 4 auramite studs (16 plasteel total)
# 1 stud = 25 years, 4 studs = 100 years, 16 studs = 400 years
SERVICE_STUDS_SPECIAL_MILESTONES: Dict[int, str] = {
    1: "**FIRST SERVICE STUD** — Twenty-five years sworn to the Long Watch. The Vigil has claimed another soul.",
    4: "**FIRST AURAMITE STUD** — A century of service. Plasteel gives way to auramite—the mark of a true veteran.",
    16: "**FOURTH AURAMITE STUD** — Four hundred years. Few have ever borne such weight of duty. A living legend of the Deathwatch.",
}

# Deathwatch-themed opening phrases for service stud announcements
# Note: {name} uses the stripped display name (no rank/studs) in _get_service_studs_announcement
DEATHWATCH_STUD_OPENINGS: List[str] = [
    "Hear this, Brothers! **{name}** is brought before you for marking!",
    "The Long Watch turns its gaze—witness as **{name}** earns a new stud!",
    "The Fortress records this honor: **{name}** stands ready for the marking!",
    "By the Vigil Oathstone, **{name}** approaches the Apothecarion for the sacred rite!",
    "The chronicles inscribe a new mark. **{name}**, the path to honor lies before you!",
    "Heed this proclamation! The Apothecarion stands ready to affix the mark upon **{name}**!",
    "The Watch Eternal bears witness—**{name}** has earned another stud through devoted service!",
]

# Deathwatch themed closings
DEATHWATCH_STUD_CLOSINGS: List[str] = [
    "Vigilus Aeterna. The Watch endures.",
    "For the Emperor and the Long Watch!",
    "The Vigil continues. Ave Imperator.",
    "By bolt and blade, the Watch persists.",
    "In Vigilance, Eternal.",
]

# Oathsworn eligibility flavor text
# Openings declare a Watch Veteran has earned the right to be considered for Oathsworn
OATHSWORN_OPENINGS: List[str] = [
    "The Vigil Oathstone trembles! **{name}** has proven worthy of elevation!",
    "The Watch Eternal bears witness—**{name}** stands ready for the sacred oath!",
    "Hearken, Brothers! **{name}** has walked the Long Watch and earned the right to swear!",
    "The chronicles blaze with glory! **{name}** approaches the threshold of the Oathsworn!",
    "By bolt, blade, and blood, **{name}** has earned a seat among the Oathsworn!",
    "The machine-spirits whisper reverence—**{name}** is called to take the Oath!",
]

# Proclamations about what it means to become Oathsworn
OATHSWORN_PROCLAMATIONS: List[str] = [
    "Three marks of service gleam upon their brow, testament to campaigns beyond counting.",
    "Plasteel studs proclaim unwavering devotion to the Long Watch and the Emperor's cause.",
    "Through countless engagements, they have proven their value to the Watch Eternal.",
    "Their service studs speak louder than any proclamation—duty fulfilled, honor earned.",
    "The Watch has weighed their deeds and found them worthy of this sacred consideration.",
    "Steadfast service and proven valor have brought them to this threshold of honor.",
]


def _get_emoji_by_name(guild: discord.Guild, name: str) -> Optional[str]:
    """Lookup a custom emoji by name from the guild.

    Returns the emoji string (e.g., '<:HawkLords:123456>') if found, else None.
    The name should be without colons, e.g., 'HawkLords' not ':HawkLords:'.
    """
    if not guild:
        return None
    # Normalize: remove spaces and special chars for lookup
    # e.g., 'Hawk Lords' -> 'HawkLords', 'Watch Brother' -> 'WatchBrother'
    normalized = name.replace(" ", "").replace("-", "").replace("'", "")
    for emoji in guild.emojis:
        if emoji.name.lower() == normalized.lower():
            return str(emoji)
    return None


def _blend_forgemaster_self_attestation(member_chapter: str) -> str:
    """Blend chapter identity and role identity for Forgemaster self-blessing.

    Follows High Command Specialist ratio: 80% role (generic Mechanicus), 20% chapter.
    Falls back to generic if chapter not found.
    """
    import random

    chapter_options = FORGEMASTER_SELF_ATTESTATION_BY_CHAPTER.get(member_chapter, [])

    # 80% role (generic Mechanicus), 20% chapter
    if random.random() < 0.8:
        return random.choice(FORGEMASTER_SELF_ATTESTATION_GENERIC)
    
    if chapter_options:
        return random.choice(chapter_options)
    
    # Fallback to generic if chapter not in dict
    return random.choice(FORGEMASTER_SELF_ATTESTATION_GENERIC)


def _get_chapter_emoji(guild: discord.Guild, chapter_name: str) -> str:
    """Get chapter emoji or fallback to just the chapter name."""
    emoji = _get_emoji_by_name(guild, chapter_name)
    if emoji:
        return f"{emoji} {chapter_name}"
    return chapter_name


def _get_rank_emoji(guild: discord.Guild, rank_name: str) -> str:
    """Get rank emoji or fallback to just the rank name."""
    # Special mappings where emoji name differs from role name
    RANK_EMOJI_OVERRIDES = {
        "Company Champion": "WatchChampion",
        "Kill Team Champion": "KillteamChampion",
    }
    emoji_name = RANK_EMOJI_OVERRIDES.get(rank_name, rank_name)
    emoji = _get_emoji_by_name(guild, emoji_name)
    if emoji:
        return f"{emoji}"
    return ""


def _get_rank_category_for_blend(rank_name: str) -> str:
    """Categorize rank for stud flavor blending.

    Returns one of: 'watchers', 'high_cmd_specialist', 'company_cmd', 'specialist', 'line'

    - watchers: Watch Master (100% role, 0% chapter)
    - high_cmd_specialist: Chaplain, Apothecary, Librarian, Techmarine at High Command level
    - company_cmd: Captains, Lieutenants, Champions at company level
    - specialist: Watch Chaplain, Watch Apothecary, Watch Librarian, Watch Techmarine (not high cmd)
    - line: Everyone else (Sergeant, Veteran, Brother, Champions at KT level)
    """
    if rank_name == "Watch Master":
        return "watchers"

    high_cmd_roles = {
        "High Chaplain",
        "Chief Apothecary",
        "Watch Librarian",
        "Watch Techmarine",
        "Forgemaster",
        "Void Warden",
    }
    if rank_name in high_cmd_roles:
        return "high_cmd_specialist"

    company_cmd_roles = {"Watch Captain", "Watch Lieutenant", "Company Champion"}
    if rank_name in company_cmd_roles:
        return "company_cmd"

    specialist_roles = {"Watch Chaplain", "Watch Apothecary"}
    if rank_name in specialist_roles:
        return "specialist"

    return "line"


def _blend_stud_flavor_by_rank(
    member_chapter: str, member_rank_name: str, pip_type: str
) -> str:
    """Blend chapter identity and role identity based on rank hierarchy.

    - Line (KB/Oathsworn/KT members): 80% chapter, 20% role
    - Specialist (Watch Chaplain/Apothecary): 50% chapter, 50% role
    - Company Command: 50% chapter, 50% role
    - High Command Specialist: 20% chapter, 80% role
    - Watch Master: 10% chapter, 90% role

    pip_type: "plasteel" or "auramite" for veneration fallback selection.
    Returns blended flavor text or falls back to pip-type-based veneration.
    """
    import random

    category = _get_rank_category_for_blend(member_rank_name)

    # Get chapter flavor (3 options per chapter)
    chapter_options = CHAPTER_STUDS_FLAVOR.get(member_chapter, [])

    # Get role-specific commentary (if available)
    role_options = RANK_STUDS_COMMENTARY.get(member_rank_name, [])

    # Select veneration pool based on pip type
    if pip_type == "auramite":
        veneration_pool = SERVICE_STUDS_VENERATIONS_AURAMITE
    else:  # plasteel or unknown
        veneration_pool = SERVICE_STUDS_VENERATIONS_PLASTEEL

    # Blend based on category
    if category == "watchers":
        # 90% role, 10% chapter
        if random.random() < 0.9:
            if role_options:
                return random.choice(role_options)
        if chapter_options:
            return random.choice(chapter_options)
        # Fallback to pip-type veneration
        return random.choice(veneration_pool)

    elif category == "high_cmd_specialist":
        # 80% role, 20% chapter
        if random.random() < 0.8:
            if role_options:
                return random.choice(role_options)
        if chapter_options:
            return random.choice(chapter_options)
        return random.choice(veneration_pool)

    elif category == "company_cmd" or category == "specialist":
        # 50% chapter, 50% role
        if random.random() < 0.5:
            if chapter_options:
                return random.choice(chapter_options)
            if role_options:
                return random.choice(role_options)
        else:
            if role_options:
                return random.choice(role_options)
            if chapter_options:
                return random.choice(chapter_options)
        return random.choice(veneration_pool)

    else:  # line (default: KB, Oathsworn, KT members)
        # 80% chapter, 20% role
        if random.random() < 0.8:
            if chapter_options:
                return random.choice(chapter_options)
        if role_options:
            return random.choice(role_options)
        return random.choice(veneration_pool)


def _get_stud_marking_recipients(
    member: discord.Member, guild: discord.Guild
) -> Tuple[str, str]:
    """Determine who receives stud marking and who witnesses. Returns (primary, secondary).

    The Apothecarion always performs the actual stud implantation (surgical procedure).
    This function determines who witnesses/authorizes based on chain of command:
    - Watch Master: The Chief Apothecary personally attends
    - High Command: The Chief Apothecary attends, Watch Master witnesses
    - Company Command/Specialists: Report to their Company CO
    - Kill Team: Report to actual Sergeant (or Lt/Cpt if shortage)
    - Line: Report to the Apothecarion

    Returns (primary_text, secondary_text) where text is bold name with rank emoji.
    """

    def strip_studs(name: str) -> str:
        """Remove service studs (●⚬) from a name."""
        return name.replace("●", "").replace("⚬", "").strip()

    roles = getattr(member, "roles", []) or []
    role_names = [getattr(r, "name", "") for r in roles]

    # Determine highest rank
    member_rank_name = "Watch Brother"
    for rank in RANK_ROLES_PRIORITY:
        if rank in role_names:
            member_rank_name = rank
            break

    # Watch Master: Chief Apothecary personally attends
    if member_rank_name == "Watch Master":
        try:
            for mbr in guild.members:
                mbr_roles = {getattr(r, "name", "") for r in mbr.roles}
                if "Chief Apothecary" in mbr_roles:
                    emoji = _get_rank_emoji(guild, "Chief Apothecary")
                    emoji_prefix = f"{emoji} " if emoji else ""
                    clean_name = strip_studs(mbr.display_name)
                    return f"The {emoji_prefix}**{clean_name}** personally attends.", ""
        except Exception:
            pass
        return "The Chief Apothecary personally attends.", ""

    # High Command: Chief Apothecary attends, witnessed by Watch Master
    high_cmd = {
        "High Chaplain",
        "Chief Apothecary",
        "Void Warden",
        "Lord Executioner",
        "Forgemaster",
        "Watch Techmarine",
    }
    if member_rank_name in high_cmd:
        # For High Command, Chief Apothecary performs the marking
        # If they ARE the Chief Apothecary, another Apothecary handles it
        if member_rank_name == "Chief Apothecary":
            return "Another Apothecary of the Watch attends.", ""
        try:
            for mbr in guild.members:
                mbr_roles = {getattr(r, "name", "") for r in mbr.roles}
                if "Chief Apothecary" in mbr_roles:
                    emoji = _get_rank_emoji(guild, "Chief Apothecary")
                    emoji_prefix = f"{emoji} " if emoji else ""
                    clean_name = strip_studs(mbr.display_name)
                    return f"The {emoji_prefix}**{clean_name}** attends.", ""
        except Exception:
            pass
        return "Report to the Chief Apothecary.", ""

    # Company Command and Specialists: Apothecarion handles, CO witnesses
    company_cmd_and_spec = {
        "Watch Captain",
        "Watch Lieutenant",
        "Company Champion",
        "Watch Apothecary",
        "Watch Chaplain",
        "Watch Librarian",
        "Watch Techmarine",
        "Watch Sergeant",
    }
    # Watch Apothecary is handled separately - they can't mark themselves
    if member_rank_name == "Watch Apothecary":
        try:
            for mbr in guild.members:
                mbr_roles = {getattr(r, "name", "") for r in mbr.roles}
                if "Chief Apothecary" in mbr_roles:
                    emoji = _get_rank_emoji(guild, "Chief Apothecary")
                    emoji_prefix = f"{emoji} " if emoji else ""
                    clean_name = strip_studs(mbr.display_name)
                    return f"The {emoji_prefix}**{clean_name}** attends.", ""
        except Exception:
            pass
        return "Report to the Chief Apothecary.", ""
    if member_rank_name in company_cmd_and_spec:
        company = _find_company_or_chapter(member)
        if company:
            captains, lieutenants = _find_company_command_staff(guild, company)
            co_member = (
                lieutenants[0] if lieutenants else (captains[0] if captains else None)
            )
            if co_member:
                # Determine CO's rank for emoji
                co_roles = {getattr(r, "name", "") for r in co_member.roles}
                co_rank = (
                    "Watch Lieutenant"
                    if "Watch Lieutenant" in co_roles
                    else "Watch Captain"
                )
                emoji = _get_rank_emoji(guild, co_rank)
                emoji_prefix = f"{emoji} " if emoji else ""
                clean_name = strip_studs(co_member.display_name)
                return f"Report to {emoji_prefix}**{clean_name}**.", ""
        # Fallback: find any Captain in the guild
        captains, _ = _find_all_captains_and_lieutenants(guild)
        if captains:
            cap = captains[0]
            emoji = _get_rank_emoji(guild, "Watch Captain")
            emoji_prefix = f"{emoji} " if emoji else ""
            clean_name = strip_studs(cap.display_name)
            return f"Report to {emoji_prefix}**{clean_name}**.", ""
        return "Report to your Company Captain.", ""

    # Kill Team members: try Sergeant first; fallback to Lt/Cpt; fallback to Apothecary
    kt_name = _resolve_killteam_for_member(member)
    if kt_name:
        # Try to find Sergeant (but not the member themselves)
        sgt = _find_kt_sergeant(guild, kt_name)
        if sgt and sgt.id != member.id:
            emoji = _get_rank_emoji(guild, "Watch Sergeant")
            emoji_prefix = f"{emoji} " if emoji else ""
            clean_name = strip_studs(sgt.display_name)
            return f"Report to {emoji_prefix}**{clean_name}**.", ""

        # If no Sergeant (or member IS the Sergeant), search for Lt/Cpt in same KT
        try:
            for mbr in guild.members:
                if mbr.id == member.id:
                    continue
                mbr_teams = _resolve_killteams_for_member(mbr)
                if kt_name not in mbr_teams:
                    continue
                mbr_role_names = {getattr(r, "name", "") for r in mbr.roles}
                if "Watch Lieutenant" in mbr_role_names:
                    emoji = _get_rank_emoji(guild, "Watch Lieutenant")
                    emoji_prefix = f"{emoji} " if emoji else ""
                    clean_name = strip_studs(mbr.display_name)
                    return f"Report to {emoji_prefix}**{clean_name}**.", ""
                if "Watch Captain" in mbr_role_names:
                    emoji = _get_rank_emoji(guild, "Watch Captain")
                    emoji_prefix = f"{emoji} " if emoji else ""
                    clean_name = strip_studs(mbr.display_name)
                    return f"Report to {emoji_prefix}**{clean_name}**.", ""
        except Exception:
            pass

    # Fallback: find Watch Apothecary
    try:
        for mbr in guild.members:
            mbr_roles = {getattr(r, "name", "") for r in mbr.roles}
            if "Watch Apothecary" in mbr_roles:
                emoji = _get_rank_emoji(guild, "Watch Apothecary")
                emoji_prefix = f"{emoji} " if emoji else ""
                clean_name = strip_studs(mbr.display_name)
                return f"Report to {emoji_prefix}**{clean_name}**.", ""
    except Exception:
        pass

    return "Report to the Apothecarion.", ""


def _studs_tier(new_total: int) -> int:
    """Return the display tier (1, 2, or 3) for a given total stud count.

    Tier 1: 1-3 studs (new warriors)
    Tier 2: 4-11 studs (seasoned veterans)
    Tier 3: 12-16 studs (legendary; studs are capped at 16 system-wide)
    """
    if new_total <= 3:
        return 1
    elif new_total <= 11:
        return 2
    return 3


def _studs_pips(new_total: int) -> str:
    """Return the pip display string for a given total stud count.

    Each Auramite pip (●) represents 4 Plasteel studs.
    Once the first Auramite is earned (total ≥ 4), only Auramite pips
    are displayed; the Plasteel remainder is not shown.
    The display is capped at 4 Auramite studs (16 Plasteel total).
    Returns '—' when new_total is 0.
    """
    auramite = min(new_total // 4, 4)
    if auramite > 0:
        pips = "●" * auramite
    else:
        plasteel = new_total % 4
        pips = "⚬" * plasteel
    return pips if pips else "—"


def _studs_next_target(displayed_studs: int) -> int:
    """Return the next stud milestone for the promotion queue.

    Plasteel tier (0-3 studs): next individual stud (displayed_studs + 1).
    Auramite tier (4+ studs): next Auramite milestone in steps of 4 (8, 12, 16).
    """
    if displayed_studs < 4:
        return displayed_studs + 1
    return (displayed_studs // 4 + 1) * 4


def _format_stud_target(target: int) -> str:
    """Return a display string for the next stud target in the promotion queue.

    For milestones that reach the first auramite or beyond (target >= 4), shows
    auramite pip symbols (●). For earlier studs, shows the stud number (#n).
    """
    if target >= 4:
        return "●" * (target // 4)
    return f"#{target}"


def _get_service_studs_announcement(
    member: discord.Member,
    member_chapter: str,
    displayed_studs: int,
    new_studs: int,
    earned_studs: int,
    owed_studs: int,
    guild: discord.Guild,
) -> str:
    """Generate a flavorful, RP-oriented service studs announcement.

    Incorporates the member's rank, home chapter, and which stud they're earning
    to create a personalized and immersive notification.
    Mobile-friendly with shorter lines and Deathwatch theming.
    """
    import random

    roles = getattr(member, "roles", []) or []
    role_names = [getattr(r, "name", "") for r in roles]

    # Use shared function for dynamic champion honorifics (same as forge_rite)
    rank_honorific, display_name, member_title = _get_bearer_rank_and_title(member)

    # Determine raw rank name for emoji lookup
    member_rank_name = "Watch Brother"
    for rank in RANK_ROLES_PRIORITY:
        if rank in role_names:
            member_rank_name = rank
            break

    stud_word = "Stud" if new_studs == 1 else "Studs"

    # Determine tier and pip display based on NEW total (after earning these studs)
    new_total = displayed_studs + new_studs
    tier = _studs_tier(new_total)
    studs_pips = _studs_pips(new_total)

    # Get Watch Brother role for pinging in content (outside embed)
    watch_brother_role = discord.utils.get(guild.roles, name="Watch Brother")
    wb_mention = watch_brother_role.mention if watch_brother_role else ""

    # Get emojis for rank and chapter
    rank_emoji = _get_rank_emoji(guild, member_rank_name)
    chapter_emoji = (
        _get_emoji_by_name(guild, member_chapter)
        if member_chapter != "Unknown"
        else None
    )

    # Build embed
    embed = discord.Embed(
        title="᛭⋅ MARK OF SERVICE ⋅᛭",
        description="*⌾ Watch Fortress Jericho ⌾*",
        color=0xC0C0C0,  # Silver for service studs
    )

    # Generate opening and milestone intro (for first embed field)
    # Format opening with stripped display name (no rank/studs)
    opening_template = random.choice(DEATHWATCH_STUD_OPENINGS)
    opening = opening_template.format(name=display_name)

    # Use first-stud templates when earning stud #1 to avoid "another" phrasing
    if new_total == 1:
        milestone_intro = random.choice(SERVICE_STUDS_MILESTONE_FIRST)
    elif tier == 1:
        milestone_intro = random.choice(SERVICE_STUDS_MILESTONE_TIER1)
    elif tier == 2:
        milestone_intro = random.choice(SERVICE_STUDS_MILESTONE_TIER2)
    else:
        milestone_intro = random.choice(SERVICE_STUDS_MILESTONE_TIER3)

    # Add Watch's Proclamation as first field with mentions baked in
    # Opening and milestone intro flow together without line break (plain narrative text, no italics/quotes)
    proclamation_value = f"{opening} {milestone_intro}"
    embed.add_field(
        name="▸ Watch's Proclamation",
        value=proclamation_value,
        inline=False,
    )

    # Bearer field with rank emoji (exactly matching forge_rite format)
    rank_prefix = f"{rank_emoji} " if rank_emoji else ""
    # Split honorific if it contains a comma (e.g., "Blade of the Fortress, Lord Executioner")
    # to put title on one line and rank + name on the next
    if ", " in rank_honorific:
        title_part, rank_part = rank_honorific.rsplit(", ", 1)
        bearer_value = f"{rank_prefix}**{title_part},**\n**{rank_part} {display_name}**"
    else:
        bearer_value = f"{rank_prefix}**{rank_honorific} {display_name}**"
    if member_title:
        bearer_value += f"\n*{member_title}*"
    if member_chapter and member_chapter != "Unknown":
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        lineage_display = (
            "REDACTED" if member_chapter == "Black Shield" else member_chapter
        )
        bearer_value += f"\nLineage: {chapter_prefix}{lineage_display}"
    if new_total > 0:
        bearer_value += f"\nService Studs: [{studs_pips}] ({new_total})"
    embed.add_field(name="▸ Bearer", value=bearer_value, inline=True)

    # Calculate visual pip change (what pips change from BEFORE to AFTER)
    # displayed_studs = what they had before, new_total = what they'll have after
    prev_studs = max(0, displayed_studs)
    curr_studs = new_total

    prev_auramite = min(prev_studs // 4, 4)
    prev_plasteel = prev_studs % 4 if prev_studs <= 16 else 0

    curr_auramite = min(curr_studs // 4, 4)
    curr_plasteel = curr_studs % 4 if curr_studs <= 16 else 0

    # Compute net change in each pip type
    delta_auramite = curr_auramite - prev_auramite
    delta_plasteel = curr_plasteel - prev_plasteel

    # Build visual pip change string showing what was gained
    # Show the highest tier pip that increased (the "upgrade")
    # If multiple pip types changed, show all positive deltas
    pip_changes = []
    if delta_auramite > 0:
        pip_word = "Stud" if delta_auramite == 1 else "Studs"
        pip_changes.append(f"+{delta_auramite}● Auramite {pip_word}")
    if delta_plasteel > 0:
        pip_word = "Stud" if delta_plasteel == 1 else "Studs"
        pip_changes.append(f"+{delta_plasteel}⚬ Plasteel {pip_word}")

    # Service Record field (bold values for numerical emphasis)
    if pip_changes:
        pip_change = ", ".join(pip_changes)
    else:
        pip_change = f"+{new_studs} {stud_word}"
    record_value = f"**{pip_change}** Earned\n"
    record_value += f"Total: **{earned_studs}** | Displayed: **{displayed_studs}**"
    if owed_studs > 0:
        record_value += f"\nOwed: **{owed_studs}**"
    embed.add_field(name="▸ Service Record", value=record_value, inline=True)

    # Special milestone callout (bold labels, plain narrative - check against new total they'll display)
    special_milestone = SERVICE_STUDS_SPECIAL_MILESTONES.get(new_total)
    if special_milestone:
        embed.add_field(name="▸ Milestone", value=special_milestone, inline=False)

    # Honor of the Long Watch: Tiered Ordo Xenos phrase + blended chapter/role flavor
    # Select tier-appropriate Ordo Xenos honor
    if tier == 1:
        ordo_honor = random.choice(ORDO_XENOS_HONORS_TIER1)
    elif tier == 2:
        ordo_honor = random.choice(ORDO_XENOS_HONORS_TIER2)
    else:
        ordo_honor = random.choice(ORDO_XENOS_HONORS_TIER3)

    # Format pronouns (always second person for awarding to others)
    ordo_honor = ordo_honor.format(
        possessive="your", possessive_cap="Your", object="you"
    )

    # Determine which pip type is being earned (priority: auramite > plasteel)
    if delta_auramite > 0:
        pip_type = "auramite"
    else:
        pip_type = "plasteel"

    # Blend chapter and role flavor based on rank hierarchy (italics + quotes for honor/reverential phrases)
    blended_flavor = _blend_stud_flavor_by_rank(
        member_chapter, member_rank_name, pip_type
    )

    embed.add_field(
        name="▸ Honor of the Long Watch",
        value=f'*"{ordo_honor} {blended_flavor}"*',
        inline=False,
    )

    # Call to action: determine who administers/witnesses marking based on rank (plain narrative with bold names)
    marking_primary, marking_secondary = _get_stud_marking_recipients(member, guild)
    marking_value = marking_primary
    if marking_secondary:
        marking_value = f"{marking_primary}\n{marking_secondary}"

    embed.add_field(
        name="▸ Rite of Marking",
        value=marking_value,
        inline=False,
    )

    # Footer with closing phrase from ceremonial closings
    closing_phrase = random.choice(DEATHWATCH_STUD_CLOSINGS)
    embed.set_footer(text=f"᛭⋅ {closing_phrase} Jericho Stands! ⋅᛭")

    # Content has @Watch Brother and member mention for actual pings (outside embed)
    content = f"{wb_mention} {member.mention}" if wb_mention else member.mention
    return content, embed


def _get_oathsworn_announcement(
    member: discord.Member,
    member_chapter: str,
    earned_studs: int,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, discord.Poll]:
    """Generate a flavorful Oathsworn eligibility announcement with embed and poll.

    Called when a Watch Veteran has earned 3+ service studs and is eligible
    for consideration to become Oathsworn. Returns content (mentions), embed
    (flavorful announcement), and a 48-hour poll for voting.
    """
    import random

    # Extract bearer info using shared function
    rank_honorific, display_name, member_title = _get_bearer_rank_and_title(member)

    # Get emojis
    rank_emoji = _get_rank_emoji(guild, "Watch Veteran")
    chapter_emoji = (
        _get_emoji_by_name(guild, member_chapter)
        if member_chapter != "Unknown"
        else None
    )
    oathsworn_emoji = _get_emoji_by_name(guild, "Oathsworn")
    deathwatch_emoji = _get_emoji_by_name(guild, "Deathwatch")

    # Compute stud pips display using shared helper (auramite-only post-4)
    studs_pips = _studs_pips(earned_studs)

    # Generate opening and proclamation
    opening_template = random.choice(OATHSWORN_OPENINGS)
    opening = opening_template.format(name=display_name)
    proclamation = random.choice(OATHSWORN_PROCLAMATIONS)

    # Build embed
    dw_emoji_str = f"{deathwatch_emoji} " if deathwatch_emoji else ""
    oath_emoji_str = f"{oathsworn_emoji} " if oathsworn_emoji else ""
    embed = discord.Embed(
        title=f"{dw_emoji_str}᛭⋅ OATHSWORN CONSIDERATION ⋅᛭{dw_emoji_str}",
        description="*⌾ Watch Fortress Jericho ⌾*",
        color=0xFFD700,  # Gold for Oathsworn consideration
    )

    # Proclamation field
    proclamation_value = f"{opening}\n\n{proclamation}"
    embed.add_field(
        name="▸ Watch's Proclamation",
        value=proclamation_value,
        inline=False,
    )

    # Candidate field (same format as Bearer in service studs/forge_rite)
    rank_prefix = f"{rank_emoji} " if rank_emoji else ""
    # Split honorific if it contains a comma (e.g., "Blade of the Fortress, Lord Executioner")
    # to put title on one line and rank + name on the next
    if ", " in rank_honorific:
        title_part, rank_part = rank_honorific.rsplit(", ", 1)
        candidate_value = (
            f"{rank_prefix}**{title_part},**\n**{rank_part} {display_name}**"
        )
    else:
        candidate_value = f"{rank_prefix}**{rank_honorific} {display_name}**"
    if member_title:
        candidate_value += f"\n*{member_title}*"
    if member_chapter != "Unknown":
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        lineage_display = (
            "REDACTED" if member_chapter == "Black Shield" else member_chapter
        )
        candidate_value += f"\nLineage: {chapter_prefix}{lineage_display}"
    candidate_value += f"\nService Studs: **[{studs_pips}]** ({earned_studs})"
    embed.add_field(name="▸ Candidate", value=candidate_value, inline=True)

    # Eligibility field
    eligibility_value = (
        f"Rank: **Watch Veteran** ✓\n"
        f"Service Studs: **{earned_studs}** (3 required) ✓\n"
        f"Eligible for: {oath_emoji_str}**Oathsworn**"
    )
    embed.add_field(name="▸ Eligibility", value=eligibility_value, inline=True)

    # Call to action
    embed.add_field(
        name="▸ Rite of Elevation",
        value=(
            "The Watch awaits your judgment, Brothers.\n"
            "Cast your vote below to determine if this warrior shall take the Oath."
        ),
        inline=False,
    )

    # Footer
    embed.set_footer(text="᛭⋅ By Bolt and Blade, the Watch Endures! ⋅᛭")

    # Create poll - 48 hour duration
    poll = discord.Poll(
        question=f"Shall {display_name} be elevated to Oathsworn?",
        duration=timedelta(hours=48),
        multiple=False,
    )
    poll.add_answer(text="Aye, elevate to Oathsworn", emoji="⚔️")
    poll.add_answer(text="Nay, more service required", emoji="🛡️")

    # Content with mentions (Watch Captain/Lieutenant for visibility)
    watch_captain_role = discord.utils.get(guild.roles, name="Watch Captain")
    watch_lt_role = discord.utils.get(guild.roles, name="Watch Lieutenant")
    captain_mention = (
        watch_captain_role.mention if watch_captain_role else "@Watch Captain"
    )
    lt_mention = watch_lt_role.mention if watch_lt_role else "@Watch Lieutenant"
    content = f"{captain_mention} {lt_mention} {member.mention}"

    return content, embed, poll


def _get_member_rank_title(member: discord.Member) -> str:
    """Get the rank honorific for a member based on their highest rank role."""
    roles = getattr(member, "roles", []) or []
    role_names = [getattr(r, "name", "") for r in roles]
    # Check ranks in priority order (highest first)
    for rank in RANK_ROLES_PRIORITY:
        if rank in role_names:
            return RANK_HONORIFICS.get(rank, rank)
    return "Brother"


def _compute_member_service_studs(member: discord.Member) -> int:
    """Compute the number of service studs a member has earned.

    Service studs are earned at 1 per 4 weeks AND 400 AAR points (minimum of both).
    Only Watch Veteran rank and above are eligible.
    """
    try:
        idx_veteran = _role_index("Watch Veteran")
        highest_idx = get_highest_rank_index(member)

        # Must be Watch Veteran or higher
        if idx_veteran is None or highest_idx is None:
            return 0
        if highest_idx > idx_veteran:
            return 0

        now = datetime.utcnow()
        joined_at = getattr(member, "joined_at", None)

        if not joined_at:
            return 0

        # Normalize to naive UTC
        ja = joined_at
        if ja.tzinfo is not None:
            try:
                ja = ja.astimezone(timezone.utc).replace(tzinfo=None)
            except Exception:
                ja = ja.replace(tzinfo=None)

        weeks = max(0, (now - ja).days // 7)
        studs_time = weeks // 4

        # Get AAR points
        stats = compute_stats_for_user(str(getattr(member, "id", "")))
        try:
            aar_points = int(round(float(stats.get("aar_points", 0) or 0)))
        except Exception:
            aar_points = 0

        studs_aar = aar_points // 400

        # Studs are the minimum of time-based and points-based, capped at 16
        # (4 Auramite studs maximum, consistent with pip display and promotion tracking)
        return min(min(studs_time, studs_aar), 16)
    except Exception:
        return 0


def _get_studs_veneration(studs_count: int) -> Optional[str]:
    """Get a random veneration phrase appropriate for the service studs count.

    Maps count ranges to pip types:
    - 0: No veneration (newly promoted)
    - 1-3: Plasteel (newly earned)
    - 4+: Auramite (seasoned warrior, max 4 auramite = 16 plasteel)
    """
    import random

    if studs_count <= 0:
        return None
    elif studs_count <= 3:
        return random.choice(SERVICE_STUDS_VENERATIONS_PLASTEEL)
    else:
        return random.choice(SERVICE_STUDS_VENERATIONS_AURAMITE)


def _get_bearer_rank_and_title(
    member: discord.Member,
) -> Tuple[str, str, Optional[str]]:
    """Extract bearer's rank honorific, display title, and optional Kill Team/Company."""
    roles = getattr(member, "roles", []) or []
    role_names = [getattr(r, "name", "") for r in roles]
    role_names_set = {rn.lower() for rn in role_names}

    # Determine Kill Team and Company first (needed for dynamic champion honorifics)
    kill_team = None
    company = None
    command_team = None
    for rn in role_names:
        if rn in KILL_TEAMS and not kill_team:
            kill_team = rn
        if "Watch Company" in rn and not company:
            company = rn
        if rn in COMMAND_TEAMS and not command_team:
            command_team = rn

    # Determine rank honorific and which rank was matched
    honorific = "Brother"
    matched_rank = None
    for rank_name, hon in RANK_HONORIFICS.items():
        if rank_name.lower() in role_names_set:
            # Handle dynamic Lord Executioner honorific
            if rank_name == "Lord Executioner":
                # Find the Watch Master and use their name
                guild = getattr(member, "guild", None)
                watchmaster_name = None
                if guild:
                    try:
                        wm = _find_watch_master(guild)
                        if wm:
                            wm_name = wm.display_name
                            # Strip "Watch Master" prefix
                            if wm_name.lower().startswith("watch master"):
                                wm_name = wm_name[len("Watch Master") :].lstrip()
                            # Strip stud pips from name
                            wm_name = wm_name.replace("●", "").replace("⚬", "").strip()
                            watchmaster_name = wm_name
                    except Exception:
                        pass
                if watchmaster_name:
                    honorific = f"Blade of {watchmaster_name}, Lord Executioner"
                else:
                    # Fallback to fortress
                    honorific = "Blade of the Fortress, Lord Executioner"
            # Handle dynamic champion honorifics
            elif rank_name == "Kill Team Champion" and kill_team:
                # Extract KT short name: "Kill Team Falcon" -> "Falcon"
                kt_short = _extract_killteam_name(kill_team)
                honorific = f"Blade of {kt_short}, Champion"
            elif rank_name == "Company Champion" and company:
                # Find the captain of this company and use their name
                guild = getattr(member, "guild", None)
                captain_name = None
                if guild:
                    try:
                        captains, _ = _find_company_command_staff(guild, company)
                        if captains:
                            # Use first captain's display name, stripped of rank prefix
                            cap = captains[0]
                            cap_name = cap.display_name
                            # Strip "Watch Captain" or "Captain" prefix
                            for prefix in ["Watch Captain", "Captain"]:
                                if cap_name.lower().startswith(prefix.lower()):
                                    cap_name = cap_name[len(prefix) :].lstrip()
                                    break
                            # Strip stud pips from name
                            cap_name = (
                                cap_name.replace("●", "").replace("⚬", "").strip()
                            )
                            captain_name = cap_name
                    except Exception:
                        pass
                if captain_name:
                    honorific = f"Blade of {captain_name}, Champion"
                else:
                    # Fallback to company short name
                    company_short = _extract_company_short_name(company)
                    honorific = f"Blade of {company_short}, Champion"
            else:
                honorific = hon
            matched_rank = rank_name
            break

    # Get display name and strip rank prefix if present to avoid "Brother Watch Brother X"
    display_name = member.display_name
    if matched_rank:
        # Strip the rank prefix from display name (case-insensitive)
        name_lower = display_name.lower()
        rank_lower = matched_rank.lower()
        if name_lower.startswith(rank_lower):
            # Remove the rank prefix and any leading whitespace
            display_name = display_name[len(matched_rank) :].lstrip()

    # Also strip any other rank prefixes that might be in the name
    # (in case they have a different rank in their name than their role)
    for rank_name in RANK_HONORIFICS.keys():
        rank_lower = rank_name.lower()
        if display_name.lower().startswith(rank_lower):
            display_name = display_name[len(rank_name) :].lstrip()
            break

    # Also strip honorific-style prefixes from display name to avoid
    # "Brother Brother X" when someone has "Brother X" as their nickname
    honorific_prefixes = [
        "Brother",
        "Honored Veteran",
        "Veteran",
        "Oathsworn Warrior",
        "Oathsworn",
        "Sergeant",
        "Lieutenant",
        "Captain",
        "Chaplain",
        "Apothecary",
        "Librarian",
        "Techmarine",
        "Watch Master",
        "High Chaplain",
        "Chief Apothecary",
        "Void Warden",
        "Forgemaster",
        "Champion",
        "Lord Executioner",
    ]
    for prefix in honorific_prefixes:
        if display_name.lower().startswith(prefix.lower()):
            display_name = display_name[len(prefix) :].lstrip()
            break

    # Strip stud pips from display name (we report studs separately)
    display_name = display_name.replace("●", "").replace("⚬", "").strip()

    # Build combined title: prefer "Kill Team X, Company Y" format
    title_parts = []
    if kill_team:
        title_parts.append(kill_team)
    if company:
        title_parts.append(company)
    if not title_parts and command_team:
        title_parts.append(command_team)

    title = ", ".join(title_parts) if title_parts else None

    return honorific, display_name, title


def _get_bearer_home_chapter(user: discord.User | discord.Member) -> Optional[str]:
    """Return the bearer's home chapter only (not company). Used for chapter blessings."""
    try:
        roles = getattr(user, "roles", []) or []
        hc_lower = {hc.lower(): hc for hc in HOME_CHAPTERS}
        for r in roles:
            rn = (getattr(r, "name", "") or "").strip()
            if rn and rn.lower() in hc_lower:
                return hc_lower[rn.lower()]  # Return canonical name
    except Exception:
        pass
    return None


def _find_company_or_chapter(user: discord.User | discord.Member) -> Optional[str]:
    try:
        roles = getattr(user, "roles", []) or []
        # 1) Exact company role match (official company roles)
        company_roles = {
            "Watch Company Primus",
            "Watch Company Secundus",
            "Watch Company Tertius",
            "Watch Company Quartus",
            "Watch Company Quintus",
        }
        for r in roles:
            rn = (getattr(r, "name", "") or "").strip()
            if rn in company_roles:
                return rn

        # 2) Direct match against canonical HOME_CHAPTERS (case-insensitive)
        hc_lower = {hc.lower() for hc in HOME_CHAPTERS}
        for r in roles:
            rn = (getattr(r, "name", "") or "").strip()
            if rn and rn.lower() in hc_lower:
                return rn

        # 3) If user is in High Command, return Jericho High Command
        try:
            names = _canonical_role_names(user)
            if any(r in names for r in HIGH_COMMAND_ROLES):
                return "Jericho High Command"
        except Exception:
            pass

        # 4) Fallback heuristic: roles that contain 'company' or 'chapter' tokens
        for r in roles:
            rn = (getattr(r, "name", "") or "").lower()
            if "company" in rn or "chapter" in rn:
                return getattr(r, "name", "")
    except Exception:
        pass
    return None


@bot.tree.command(
    name="set_rite", description="Set your personal consecration rite text."
)
@app_commands.describe(rite_text="Your consecration rite text (multiline allowed)")
async def _set_rite(interaction: discord.Interaction, rite_text: str):
    # Restrict to Forgemaster or Techmarine
    allowed, _role_key = _is_techmarine_or_forgemaster(interaction.user)
    if not allowed:
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    # Disallow usage in the data-vault channel
    try:
        ch = interaction.channel
        if getattr(ch, "name", None) == "❖⋅data-vault⋅❖":
            await interaction.response.send_message(
                "This command is not usable in ❖⋅data-vault⋅❖.",
                ephemeral=True,
            )
            return
    except Exception:
        pass
    # Check rite length to avoid exceeding Discord's message limit in forge_rite
    if len(rite_text) > MAX_RITE_LENGTH:
        await interaction.response.send_message(
            f"Your consecration rite is too long ({len(rite_text)} chars). "
            f"The Machine God requires brevity—keep it under {MAX_RITE_LENGTH} characters.",
            ephemeral=True,
        )
        return
    try:
        await _set_user_rite(int(interaction.user.id), rite_text)
        await interaction.response.send_message(
            f"Consecration rite saved ({len(rite_text)}/{MAX_RITE_LENGTH} chars).",
            ephemeral=True,
        )
    except Exception:
        await interaction.response.send_message("Failed to save rite.", ephemeral=True)


@bot.tree.command(
    name="forge_rite",
    description="Generate and post a cogitator attestation block for a member.",
)
@app_commands.describe(
    member="Member to attest",
    intensive="Full heal to nominal (costs more charges based on damage severity)",
    force="[Forgemaster only] Override cooldowns and company restrictions",
)
async def _attest(
    interaction: discord.Interaction,
    member: discord.Member,
    intensive: bool = False,
    force: bool = False,
):
    import random

    # Permission check: caller must be techmarine or forgemaster to run command
    allowed, _caller_role_key = _is_techmarine_or_forgemaster(interaction.user)
    if not allowed:
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    # Force flag is Forgemaster-only
    is_forgemaster = _caller_role_key == "forgemaster"
    if force and not is_forgemaster:
        await interaction.response.send_message(
            "The `force` parameter is restricted to the Forgemaster.",
            ephemeral=True,
        )
        return

    # Disallow usage in the data-vault channel
    try:
        ch = interaction.channel
        if getattr(ch, "name", None) == "❖⋅data-vault⋅❖":
            await interaction.response.send_message(
                "This command is not usable in ❖⋅data-vault⋅❖.",
                ephemeral=True,
            )
            return
    except Exception:
        pass

    # ─────────────────────────────────────────────────────────────────────────
    # Recipient cooldown check (max 3 blessings per 24h, 4h between each)
    # ─────────────────────────────────────────────────────────────────────────
    if not force:
        can_receive, cooldown_remaining, blessings_used, block_reason = await _check_recipient_cooldown(int(member.id))
        if not can_receive and cooldown_remaining:
            bearer_name = member.display_name.replace("●", "").replace("⚬", "").strip()
            cooldown_str = _format_cooldown_time(cooldown_remaining)
            if block_reason == "per_blessing":
                await interaction.response.send_message(
                    f"**{bearer_name}** was recently blessed. The machine spirit must settle before further rites.\n"
                    f"Next blessing available in {cooldown_str}.",
                    ephemeral=True,
                )
            else:  # daily_cap
                await interaction.response.send_message(
                    f"**{bearer_name}** has reached their daily blessing limit ({BLESSING_RECIPIENT_MAX_PER_DAY} per day).\n"
                    f"Next blessing slot available in {cooldown_str}.",
                    ephemeral=True,
                )
            return

    # ─────────────────────────────────────────────────────────────────────────
    # Check armor integrity state BEFORE clearing
    # ─────────────────────────────────────────────────────────────────────────
    current_damage_tier = _get_member_damage_tier(member)
    was_damaged = current_damage_tier is not None
    spirit_fractured = await _check_spirit_fracture(int(member.id))

    # Get armor status for status lines
    armor_status = _get_armor_status_for_blessing(
        was_damaged, current_damage_tier, spirit_fractured
    )

    # Find the responsible attestor based on BEARER's company/role (not caller)
    attestor_member, role_key = _find_responsible_attestor(member, interaction.guild)
    if attestor_member is None:
        # No forgemaster found in guild - fall back to caller with their actual role
        attestor_member = interaction.user
        role_key = _caller_role_key

    # ─────────────────────────────────────────────────────────────────────────
    # Intensive mode validation and charge calculation
    # ─────────────────────────────────────────────────────────────────────────
    charges_required = 1  # Standard blessing
    is_intensive = intensive
    
    if intensive:
        charges_required = _get_intensive_charge_cost(current_damage_tier, spirit_fractured)
        if charges_required == 0:
            # Target is nominal - intensive not applicable
            await interaction.response.send_message(
                "No damage to repair. Use standard blessing for routine maintenance.",
                ephemeral=True,
            )
            return

    # ─────────────────────────────────────────────────────────────────────────
    # Techmarine blessing pool check with collaborative pooling for intensive
    # ─────────────────────────────────────────────────────────────────────────
    # Track charge contributions: list of (user_id, charges_to_consume)
    blessing_pool_contributions = []
    is_collaborative = False
    
    if not force:
        invoker_id = int(interaction.user.id)
        attestor_id = int(attestor_member.id)
        invoker_is_attestor = (invoker_id == attestor_id)
        
        # Get available charges for both parties
        attestor_charges = await _get_techmarine_available_charges(attestor_id)
        invoker_charges = await _get_techmarine_available_charges(invoker_id) if not invoker_is_attestor else 0
        
        if invoker_is_attestor:
            # Solo mode: invoker IS the attestor
            if attestor_charges >= charges_required:
                blessing_pool_contributions = [(attestor_id, charges_required)]
            else:
                if intensive:
                    await interaction.response.send_message(
                        f"Intensive blessing requires **{charges_required}** charges. You have **{attestor_charges}**.\n"
                        f"Ask another Techmarine to invoke this rite for collaborative pooling.",
                        ephemeral=True,
                    )
                else:
                    _, _, attestor_time_until_regen = await _check_techmarine_can_bless(attestor_id)
                    regen_str = _format_cooldown_time(attestor_time_until_regen) if attestor_time_until_regen else "4h 48m"
                    await interaction.response.send_message(
                        f"Your blessing pool is depleted. The sacred oils must be replenished.\n"
                        f"Next blessing available in: **{regen_str}**",
                        ephemeral=True,
                    )
                return
        else:
            # Invoker is different from attestor - collaborative pooling possible
            combined_charges = attestor_charges + invoker_charges
            
            if attestor_charges >= charges_required:
                # Attestor alone can handle it
                blessing_pool_contributions = [(attestor_id, charges_required)]
            elif combined_charges >= charges_required:
                # Combined pool is sufficient. Only treat this as collaborative
                # when both parties materially contribute charges.
                attestor_contribution = attestor_charges
                invoker_contribution = charges_required - attestor_charges
                is_collaborative = attestor_contribution > 0 and invoker_contribution > 0
                blessing_pool_contributions = [
                    (attestor_id, attestor_contribution),
                ]
                if invoker_contribution > 0:
                    blessing_pool_contributions.append((invoker_id, invoker_contribution))
            else:
                # Neither has enough even combined
                if intensive:
                    await interaction.response.send_message(
                        f"Intensive blessing requires **{charges_required}** charges.\n"
                        f"**{attestor_member.display_name}** has {attestor_charges}, you have {invoker_charges} "
                        f"(combined: {combined_charges}).\n"
                        f"Requisition more supplies or reduce scope.",
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        "Both the attesting Techmarine and your blessing pools are depleted. "
                        "Seek another Techmarine to perform this rite.",
                        ephemeral=True,
                    )
                return

    # Build attestation using standardized Imperial date format
    try:
        ts = _format_imperial_date(datetime.utcnow())
    except Exception:
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    # Authority based on attestor's company/role (or combined for collab)
    if role_key == "forgemaster":
        authority = "Jericho High Command"
    elif is_collaborative:
        attestor_comp = _find_company_or_chapter(attestor_member) or "Unknown"
        invoker_comp = _find_company_or_chapter(interaction.user) or "Unknown"
        if attestor_comp != invoker_comp:
            authority = f"{attestor_comp} & {invoker_comp}"
        else:
            authority = attestor_comp
    else:
        comp = _find_company_or_chapter(attestor_member) or "Unknown Company"
        authority = comp

    # Attesting name from the RESPONSIBLE attestor (strip stud pips)
    attester = getattr(attestor_member, "display_name", None) or getattr(
        attestor_member, "name", str(attestor_member.id)
    )
    attester = attester.replace("●", "").replace("⚬", "").strip()

    # Get techmarine's rank emoji for attestation
    tech_rank_name = "Forgemaster" if role_key == "forgemaster" else "Watch Techmarine"
    tech_rank_emoji = (
        _get_rank_emoji(interaction.guild, tech_rank_name) if interaction.guild else ""
    )

    # Optional personal rite from the RESPONSIBLE attestor
    try:
        rite_text = await _get_user_rite(int(attestor_member.id))
        # Safety truncation for legacy rites that may exceed the limit
        if rite_text and len(rite_text) > MAX_RITE_LENGTH:
            rite_text = rite_text[: MAX_RITE_LENGTH - 3] + "..."
    except Exception:
        rite_text = None

    # ─────────────────────────────────────────────────────────────────────────
    # Dynamic personalization
    # ─────────────────────────────────────────────────────────────────────────

    # Bearer info: rank honorific, display name, and Kill Team/Company title
    bearer_honorific, bearer_name, bearer_title = _get_bearer_rank_and_title(member)
    # Defensive pip stripping - ensure no stud pips in display name
    bearer_name = bearer_name.replace("●", "").replace("⚬", "").strip()

    # Bearer's home chapter for chapter-specific blessing (use dedicated function)
    bearer_chapter = _get_bearer_home_chapter(member)
    chapter_blessing = None
    if bearer_chapter and bearer_chapter in CHAPTER_BLESSINGS:
        chapter_blessing = CHAPTER_BLESSINGS[bearer_chapter]
    elif bearer_chapter:
        # Check case-insensitive fallback
        for chap_name, blessing in CHAPTER_BLESSINGS.items():
            if chap_name.lower() == bearer_chapter.lower():
                chapter_blessing = blessing
                break

    # Service studs computation
    bearer_studs = _compute_member_service_studs(member)

    # Techmarine acknowledgment (dynamically blended by rank prestige vs stud count)
    stud_acknowledgment = _get_techmarine_acknowledgment_blended(member, bearer_studs)

    # Random sacred Mechanicus phrase (special phrases for self-blessing)
    is_self_blessing = attestor_member.id == member.id
    if is_self_blessing:
        sacred_phrase = _blend_forgemaster_self_attestation(bearer_chapter)
    else:
        sacred_phrase = random.choice(SACRED_MECHANICUS_PHRASES)

    # ─────────────────────────────────────────────────────────────────────────
    # Machine-spirit designation with armor integrity awareness
    # ─────────────────────────────────────────────────────────────────────────
    import hashlib

    existing_spirit = await _get_machine_spirit(int(member.id))
    spirit_is_returning = False
    spirit_is_reconsecrated = False
    spirit_is_restored = False
    spirit_is_first = False

    if spirit_fractured:
        # Spirit was lost due to neglect at critical - generate new spirit (re-consecration)
        spirit_hash = (
            hashlib.md5(f"{member.id}-{datetime.utcnow().isoformat()}".encode())
            .hexdigest()[:6]
            .upper()
        )
        spirit_prefixes = [
            # Aggression/Combat
            "FURY", "WRATH", "MORTIS", "VENATOR", "GLADIUS", "BELLATOR",
            "FEROX", "CARNIFEX", "VINDICTA", "MALLEUS",
            # Protection/Vigilance  
            "AEGIS", "VIGIL", "PURITY", "CUSTODIAN", "SENTINEL", "BULWARK",
            "DEFENSOR", "CASTELLAN", "PRAESIDIUM", "SCUTUM",
            # Strength/Endurance
            "FERRUM", "ADAMANT", "TITANICUS", "INVICTUS", "FORTIS",
            # Mechanicus/Sacred
            "SACRIS", "SANCTUS", "FERVOR", "COGNIS", "ANIMUS",
            # Predatory
            "TALON", "RAPTOR", "LUPUS", "AQUILA", "CORVUS",
        ]
        spirit_suffixes = [
            # Greek letters (expanded)
            "Α", "Β", "Γ", "Δ", "Ε", "Ζ", "Η", "Θ", "Ι", "Κ",
            "Λ", "Μ", "Ν", "Ξ", "Ο", "Π", "Ρ", "Σ", "Τ", "Υ",
            "Φ", "Χ", "Ψ", "Ω",
            # Roman numerals
            "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
        ]
        spirit_designation = f"{random.choice(spirit_prefixes)}-{spirit_hash}-{random.choice(spirit_suffixes)}"
        await _set_machine_spirit(int(member.id), spirit_designation)
        spirit_is_reconsecrated = True
    elif existing_spirit:
        # Spirit intact - preserve it
        spirit_designation = existing_spirit
        if was_damaged:
            spirit_is_restored = True
        else:
            spirit_is_returning = True
    else:
        # First blessing - generate and store new spirit
        spirit_hash = (
            hashlib.md5(f"{member.id}-{datetime.utcnow().isoformat()}".encode())
            .hexdigest()[:6]
            .upper()
        )
        spirit_prefixes = [
            # Aggression/Combat
            "FURY", "WRATH", "MORTIS", "VENATOR", "GLADIUS", "BELLATOR",
            "FEROX", "CARNIFEX", "VINDICTA", "MALLEUS",
            # Protection/Vigilance  
            "AEGIS", "VIGIL", "PURITY", "CUSTODIAN", "SENTINEL", "BULWARK",
            "DEFENSOR", "CASTELLAN", "PRAESIDIUM", "SCUTUM",
            # Strength/Endurance
            "FERRUM", "ADAMANT", "TITANICUS", "INVICTUS", "FORTIS",
            # Mechanicus/Sacred
            "SACRIS", "SANCTUS", "FERVOR", "COGNIS", "ANIMUS",
            # Predatory
            "TALON", "RAPTOR", "LUPUS", "AQUILA", "CORVUS",
        ]
        spirit_suffixes = [
            # Greek letters (expanded)
            "Α", "Β", "Γ", "Δ", "Ε", "Ζ", "Η", "Θ", "Ι", "Κ",
            "Λ", "Μ", "Ν", "Ξ", "Ο", "Π", "Ρ", "Σ", "Τ", "Υ",
            "Φ", "Χ", "Ψ", "Ω",
            # Roman numerals
            "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
        ]
        spirit_designation = f"{random.choice(spirit_prefixes)}-{spirit_hash}-{random.choice(spirit_suffixes)}"
        await _set_machine_spirit(int(member.id), spirit_designation)
        spirit_is_first = True

    # Flavor text for spirit status
    if spirit_is_reconsecrated:
        spirit_status_text = random.choice(SPIRIT_RECONSECRATION_PHRASES)
    elif spirit_is_restored:
        spirit_status_text = random.choice(SPIRIT_RESTORATION_PHRASES)
    elif spirit_is_returning:
        spirit_status_phrases = [
            "The machine spirit stirs, recognizing its bearer",
            "Ancient recognition-rites confirm: spirit and bearer are one",
            "The spirit awakens from dormancy, its vigilance renewed",
            "Cogitator confirms: spirit-bond integrity remains absolute",
            "The spirit hums with familiarity—it knows your biorhythms well",
            "Binharic acknowledgment received. The spirit welcomes its master home",
            "Neural handshake successful. Spirit-bond resonance at optimal levels",
            "The armor's animus pulses with recognition. You are known. You are accepted.",
            "Data-communion confirms: bearer identity verified across all subroutines",
            "The spirit's sensors sweep you with mechanical affection. The bond holds true.",
        ]
        spirit_status_text = random.choice(spirit_status_phrases)
    else:  # spirit_is_first
        spirit_status_phrases = [
            "First binding complete. Spirit and bearer are now one",
            "Virgin armor awakened. The spirit stirs for the first time",
            "Inaugural consecration. May this bond endure ten thousand years",
            "New spirit bound to bearer by sacred rite of the Omnissiah",
            "The machine spirit opens its awareness for the first time—and finds you waiting",
            "Activation protocols complete. The spirit learns your name, your scent, your purpose",
            "From dormancy, consciousness. From emptiness, bond. The spirit claims you as its own.",
            "The first data-handshake is always sacred. Spirit and bearer, now interlinked.",
            "Boot sequence finalized. The spirit's first thought is of duty—and of you.",
            "The Rite of First Awakening concludes. A new partnership is forged in sacred code.",
        ]
        spirit_status_text = random.choice(spirit_status_phrases)

    # ─────────────────────────────────────────────────────────────────────────
    # Roll blessing outcome and apply effect (state-based probabilities)
    # ─────────────────────────────────────────────────────────────────────────
    blessing_roll_outcome = _roll_blessing_outcome(
        damage_tier=current_damage_tier,
        spirit_fractured=spirit_fractured,
    )
    blessing_result_tier = current_damage_tier  # Track resulting damage tier
    
    if interaction.guild:
        if blessing_roll_outcome == "crit_fail":
            # Crit fail: reset points but damage stays (same for standard and intensive)
            blessing_result_tier = await _apply_blessing_crit_fail(member, interaction.guild)
        elif blessing_roll_outcome == "crit_success":
            # Crit success: full heal + grace period (scales with charges for intensive)
            blessing_result_tier = await _apply_blessing_crit_success(
                member, interaction.guild, charges_invested=charges_required
            )
        else:
            # Normal outcome depends on intensive mode
            if is_intensive:
                # Intensive: full heal to nominal (no crit-success grace period)
                blessing_result_tier = await _apply_blessing_intensive_normal(member, interaction.guild)
            else:
                # Standard: drop one damage tier
                blessing_result_tier = await _apply_blessing_normal(member, interaction.guild)

    # Consume blessings from the contributing Techmarine(s) pools (unless force override)
    if not force and blessing_pool_contributions:
        for contrib_user_id, contrib_charges in blessing_pool_contributions:
            if contrib_charges == 1:
                await _consume_blessing(contrib_user_id)
            elif contrib_charges > 1:
                await _consume_multiple_blessings(contrib_user_id, contrib_charges)

    # ─────────────────────────────────────────────────────────────────────────
    # Build embed
    # ─────────────────────────────────────────────────────────────────────────

    # Get emojis for rank and chapter
    guild = interaction.guild
    # Extract raw rank name from bearer_honorific by reverse-lookup
    bearer_rank_name = None
    for rank, hon in RANK_HONORIFICS.items():
        if hon == bearer_honorific or rank in bearer_honorific:
            bearer_rank_name = rank
            break
    if not bearer_rank_name:
        bearer_rank_name = "Watch Brother"

    rank_emoji = _get_rank_emoji(guild, bearer_rank_name) if guild else ""
    chapter_emoji = (
        _get_emoji_by_name(guild, bearer_chapter) if guild and bearer_chapter else None
    )

    embed = discord.Embed(
        title="⚙️ COGITATOR RITE — FORGE ATTESTATION",
        description="*⌾ Watch Fortress Jericho ⌾*",
        color=0x2ECC71,
    )

    # Bearer field with emojis
    rank_prefix = f"{rank_emoji} " if rank_emoji else ""
    bearer_name = bearer_name.replace("●", "").replace("⚬", "").strip()
    if ", " in bearer_honorific:
        title_part, rank_part = bearer_honorific.rsplit(", ", 1)
        bearer_value = f"{rank_prefix}**{title_part},**\n**{rank_part} {bearer_name}**"
    else:
        bearer_value = f"{rank_prefix}**{bearer_honorific} {bearer_name}**"
    if bearer_title:
        bearer_value += f"\n*{bearer_title}*"
    if bearer_chapter:
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        lineage_display = (
            "REDACTED" if bearer_chapter == "Black Shield" else bearer_chapter
        )
        bearer_value += f"\nLineage: {chapter_prefix}{lineage_display}"
    if bearer_studs > 0:
        studs_pips = _studs_pips(bearer_studs)
        bearer_value += f"\nService Studs: [{studs_pips}] ({bearer_studs})"
    embed.add_field(name="▸ Bearer", value=bearer_value, inline=True)

    # Status field with dynamic armor status
    # Determine status emoji based on armor state
    plate_status = armor_status.get("plate", "NOMINAL")
    spirit_status = armor_status.get("spirit", "STABLE")
    rite_status = armor_status.get("rite", "MAINTENANCE")

    # Use appropriate emoji based on status
    plate_emoji = (
        "🟢"
        if plate_status == "NOMINAL"
        else ("🔴" if "CRITICAL" in plate_status else "⚠️")
    )
    spirit_emoji = (
        "🟢"
        if spirit_status == "STABLE"
        else ("🔴" if spirit_status == "FRACTURED" else "⚠️")
    )
    rite_emoji = (
        "🟢"
        if rite_status == "MAINTENANCE"
        else ("⚠️" if rite_status == "RE-CONSECRATION" else "🟢")
    )

    # Get MachineSpirit emoji for spirit field
    machine_spirit_emoji = _get_emoji_by_name(guild, "MachineSpirit") or "⚙️"
    
    status_value = (
        f"{machine_spirit_emoji} Spirit: `{spirit_designation}`\n"
        f"*{spirit_status_text}*\n"
        f"{plate_emoji} Plate: {plate_status}\n"
        f"{spirit_emoji} Spirit: {spirit_status}\n"
        f"{rite_emoji} Rite: {rite_status}"
    )
    embed.add_field(name="▸ Machine-Spirit", value=status_value, inline=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Rite Outcome field (shows roll result)
    # ─────────────────────────────────────────────────────────────────────────
    charges_text = f" ({charges_required} charges)" if is_intensive and charges_required > 1 else ""
    
    if blessing_roll_outcome == "crit_fail":
        outcome_emoji = "⚠️"
        outcome_title = "RITE RESISTED"
        if current_damage_tier:
            tier_display = current_damage_tier.upper()
            if is_intensive:
                outcome_text = f"The machine spirit resists the intensive rites{charges_text}.\nDamage persists: **{tier_display}**"
            else:
                outcome_text = f"The machine spirit resists the sacred oils.\nDamage persists: **{tier_display}**"
        else:
            outcome_text = "The machine spirit stirs uneasily.\nThe rite takes imperfect hold."
    elif blessing_roll_outcome == "crit_success":
        outcome_emoji = "✨"
        outcome_title = "SACRED COMMUNION"
        if is_intensive and charges_required > 1:
            grace_multiplier = f"×{charges_required}"
            if current_damage_tier:
                outcome_text = f"The Omnissiah rewards the {charges_required}-charge offering.\nAll damage purged. **Enhanced grace period** ({grace_multiplier}) granted."
            else:
                outcome_text = f"Perfect communion achieved through intensive rites.\nThe machine spirit radiates profound contentment. **Enhanced grace period** ({grace_multiplier}) granted."
        elif current_damage_tier:
            outcome_text = "The Omnissiah's blessing flows through the armor.\nAll damage purged. Grace period granted."
        else:
            outcome_text = "Perfect communion achieved.\nThe machine spirit radiates contentment. Grace period granted."
    else:  # normal
        if is_intensive:
            outcome_emoji = "✨"
            outcome_title = "INTENSIVE RITE COMPLETE"
            if current_damage_tier:
                outcome_text = f"Full restoration{charges_text}: {current_damage_tier.upper()} → NOMINAL\nThe armor is whole once more."
            else:
                outcome_text = "Maintenance rites complete.\nThe machine spirit rests content."
        else:
            outcome_emoji = "🟢"
            outcome_title = "RITE COMPLETE"
            if current_damage_tier and blessing_result_tier:
                outcome_text = f"Damage reduced: {current_damage_tier.upper()} → {blessing_result_tier.upper()}"
            elif current_damage_tier and not blessing_result_tier:
                outcome_text = f"Damage repaired: {current_damage_tier.upper()} → NOMINAL"
            else:
                outcome_text = "Maintenance rites complete.\nThe machine spirit rests content."
    
    outcome_value = f"{outcome_emoji} **{outcome_title}**\n{outcome_text}"
    embed.add_field(name="▸ Rite Outcome", value=outcome_value, inline=True)

    # Determine whether to show extended fields (Honor of Long Watch, Litany)
    # Only show for unbound (first) or fractured (reconsecrated) spirits
    show_extended_fields = _should_show_extended_blessing_fields(
        spirit_is_first=spirit_is_first,
        spirit_is_reconsecrated=spirit_is_reconsecrated,
        spirit_is_returning=spirit_is_returning,
        spirit_is_restored=spirit_is_restored,
    )

    # Honor of the Long Watch (only for unbound/fractured spirits)
    if show_extended_fields:
        tier_for_honor = _studs_tier(bearer_studs)
        if tier_for_honor == 1:
            ordo_honor_embed = random.choice(ORDO_XENOS_HONORS_TIER1)
        elif tier_for_honor == 2:
            ordo_honor_embed = random.choice(ORDO_XENOS_HONORS_TIER2)
        else:
            ordo_honor_embed = random.choice(ORDO_XENOS_HONORS_TIER3)

        # Format pronouns based on self-blessing
        if is_self_blessing:
            ordo_honor_embed = ordo_honor_embed.format(
                possessive="my", possessive_cap="My", object="me"
            )
        else:
            ordo_honor_embed = ordo_honor_embed.format(
                possessive="your", possessive_cap="Your", object="you"
            )

        if chapter_blessing:
            embed.add_field(
                name="▸ Honor of the Long Watch",
                value=f'*"{ordo_honor_embed} {stud_acknowledgment} {chapter_blessing}"*',
                inline=False,
            )
        else:
            embed.add_field(
                name="▸ Honor of the Long Watch",
                value=f'*"{ordo_honor_embed} {stud_acknowledgment}"*',
                inline=False,
            )

    # Litany to the Machine-Spirit (only for unbound/fractured spirits with custom rite)
    if show_extended_fields and rite_text:
        rite_display = str(rite_text)[:400] + ("…" if len(str(rite_text)) > 400 else "")
        embed.add_field(
            name="▸ Litany to the Machine-Spirit", value=f"{rite_display}", inline=False
        )

    # Attestation (self-blessing uses different field name, collaborative shows both Techmarines)
    rank_emoji_prefix = f"{tech_rank_emoji} " if tech_rank_emoji else ""
    
    if is_collaborative:
        # Collaborative attestation: show both Techmarines with charge contributions
        # Get invoker's rank emoji
        invoker_rank_name = "Forgemaster" if _caller_role_key == "forgemaster" else "Watch Techmarine"
        invoker_rank_emoji = _get_rank_emoji(interaction.guild, invoker_rank_name) if interaction.guild else ""
        invoker_prefix = f"{invoker_rank_emoji} " if invoker_rank_emoji else ""
        invoker_name = interaction.user.display_name.replace("●", "").replace("⚬", "").strip()
        
        # Build contribution lines
        contrib_lines = []
        for contrib_user_id, contrib_charges in blessing_pool_contributions:
            if contrib_user_id == int(attestor_member.id):
                contrib_lines.append(f"{rank_emoji_prefix}**{attester}** ({contrib_charges})")
            else:
                contrib_lines.append(f"{invoker_prefix}**{invoker_name}** ({contrib_charges})")
        
        tech_value = f'{chr(10).join(contrib_lines)}\n{authority} • {ts}\n*"{sacred_phrase}"*'
        attestation_field_name = "▸ Attestation"
    else:
        # Solo attestation (original logic)
        attester_with_rank = f"{rank_emoji_prefix}**{attester}**"
        tech_value = f'{attester_with_rank}\n{authority} • {ts}\n*"{sacred_phrase}"*'
        attestation_field_name = "▸ Self-Attestation" if is_self_blessing else "▸ Attestation"
    
    embed.add_field(name=attestation_field_name, value=tech_value, inline=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Tiered Verbosity: Full embed for first binding/rebirth, compact otherwise
    # ─────────────────────────────────────────────────────────────────────────
    is_significant_event = spirit_is_first or spirit_is_reconsecrated
    
    # Determine spirit event type for chronicle
    if spirit_is_first:
        spirit_event = "first_binding"
    elif spirit_is_reconsecrated:
        spirit_event = "rebirth"
    elif spirit_is_restored:
        spirit_event = "restoration"
    else:
        spirit_event = "maintenance"
    
    # Check for pending alert to reply to
    pending_alert = await _get_pending_alert(int(member.id))
    
    # Build response based on verbosity tier
    send_succeeded = False
    if is_significant_event:
        # ─────────────────────────────────────────────────────────────────────
        # SIGNIFICANT EVENT: Full embed with @mention
        # ─────────────────────────────────────────────────────────────────────
        try:
            await interaction.response.send_message(
                content=member.mention,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(users=True),
                ephemeral=DEBUG_MODE,
            )
            send_succeeded = True
        except Exception:
            try:
                await interaction.response.send_message(
                    "Failed to post attestation.", ephemeral=True
                )
            except Exception:
                pass
    else:
        # ─────────────────────────────────────────────────────────────────────
        # ROUTINE EVENT: Compact 3-line format, no mention
        # ─────────────────────────────────────────────────────────────────────
        # Build compact format:
        # ⚙️ Name • SPIRIT-ID
        # 🟢 STATUS | Restored by Techmarine
        # *"Quote"*
        
        # Status icon based on result
        if blessing_roll_outcome == "crit_fail":
            status_icon = "⚠️"
            status_text = "RESISTED"
        elif blessing_roll_outcome == "crit_success":
            status_icon = "✨"
            status_text = "BLESSED *(grace)*"
        elif is_intensive:
            status_icon = "✨"
            status_text = "RESTORED"
        elif was_damaged:
            status_icon = "🟢"
            status_text = "REPAIRED"
        else:
            status_icon = "🟢"
            status_text = "MAINTAINED"
        
        # Build compact message
        compact_line1 = f"{machine_spirit_emoji} **{bearer_name}** • `{spirit_designation}`"
        compact_line2 = f"{status_icon} {status_text} | {attester}"
        compact_line3 = f'*"{sacred_phrase}"*'
        compact_message = f"{compact_line1}\n{compact_line2}\n{compact_line3}"
        
        try:
            await interaction.response.send_message(
                content=compact_message,
                allowed_mentions=discord.AllowedMentions.none(),
                ephemeral=DEBUG_MODE,
            )
            send_succeeded = True
        except Exception:
            try:
                await interaction.response.send_message(
                    "Failed to post attestation.", ephemeral=True
                )
            except Exception:
                pass
    
    # Record rite in chronicle only after a successful send
    if send_succeeded:
        await _record_rite_in_chronicle(
            bearer_id=int(member.id),
            techmarine_id=int(attestor_member.id),
            rite_type="intensive" if is_intensive else "standard",
            spirit_designation=spirit_designation,
            spirit_event=spirit_event,
        )
    
    # ─────────────────────────────────────────────────────────────────────────
    # Thread Reply: If there's a pending alert for this brother, reply to it
    # ─────────────────────────────────────────────────────────────────────────
    if pending_alert and interaction.guild:
        try:
            alert_channel_id = pending_alert.get("channel_id")
            alert_message_id = pending_alert.get("message_id")
            
            if alert_channel_id and alert_message_id:
                alert_channel = interaction.guild.get_channel(int(alert_channel_id))
                if alert_channel:
                    # Fetch the original alert message
                    try:
                        alert_msg = await alert_channel.fetch_message(int(alert_message_id))
                        
                        # Build reply based on event type
                        if spirit_is_reconsecrated:
                            reply_text = (
                                f"✨ **Spirit Reborn**\n"
                                f"The machine spirit has been re-consecrated by {attester}.\n"
                                f"{machine_spirit_emoji} New designation: `{spirit_designation}`"
                            )
                        elif blessing_roll_outcome == "crit_fail":
                            reply_text = (
                                f"⚠️ **Rite Resisted**\n"
                                f"The machine spirit rejected the sacred oils. Damage persists."
                            )
                        else:
                            reply_text = (
                                f"🟢 **Armor Restored**\n"
                                f"Blessed by {attester}. {machine_spirit_emoji} Spirit `{spirit_designation}` pacified."
                            )
                        
                        await alert_msg.reply(content=reply_text)
                        await _clear_pending_alert(int(member.id))
                    except discord.NotFound:
                        # Message was deleted, clear the pending alert
                        await _clear_pending_alert(int(member.id))
                    except Exception:
                        pass
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Armor Status Command
# ─────────────────────────────────────────────────────────────────────────────


def _get_armor_status_allowed_channels() -> set:
    """Get allowed channel IDs for armor_status command from config."""
    config = _get_armor_config()
    channel_ids = config.get("armor_status_allowed_channels", [])

    allowed_channels: set[int] = set()
    for c in channel_ids:
        if not c:
            continue
        try:
            allowed_channels.add(int(c))
        except (TypeError, ValueError):
            # Skip invalid entries to avoid breaking the command on bad config
            continue

    return allowed_channels


def _calculate_armor_risk_score(
    damage_tier: Optional[str],
    points_since_blessing: int,
    spirit_fractured: bool,
) -> int:
    """Calculate a risk score for sorting armor status leaderboard.

    Higher score = more urgent/at-risk.
    Score components:
    - Fractured spirit: +10000
    - Critical tier: +3000
    - Compromised tier: +2000
    - Damaged tier: +1000
    - Points since blessing: direct add
    """
    score = 0
    if spirit_fractured:
        score += 10000
    elif damage_tier == "critical":
        score += 3000
    elif damage_tier == "compromised":
        score += 2000
    elif damage_tier == "damaged":
        score += 1000
    score += points_since_blessing
    return score


async def _show_armor_leaderboard(
    interaction: discord.Interaction,
    guild: discord.Guild,
    company_filter: Optional[str] = None,
    pool_remaining: Optional[int] = None,
    pool_next_regen: Optional[timedelta] = None,
    techmarine_id: Optional[int] = None,
):
    """Show top 10 brothers at risk of armor damage.
    
    If company_filter is provided, only show brothers in that company.
    pool_remaining/pool_next_regen show invoker's blessing pool status.
    techmarine_id is used to check intensive scan status.
    """
    # Check if Techmarine has intensive scan active
    has_intensive = False
    if techmarine_id:
        has_intensive = await _has_intensive_scan(techmarine_id)
    
    # Load all armor states
    armor_data = _load_armor_integrity()

    if not armor_data:
        embed = discord.Embed(
            title="᛭⋅ ARMOR INTEGRITY SCAN ⋅᛭",
            description="*No armor integrity records on file.*",
            color=0x5D6D7E,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    # Build list of (member, state, risk_score, scan_result)
    risk_list = []
    for user_id_str, state in armor_data.items():
        try:
            user_id = int(user_id_str)
        except ValueError:
            continue

        member = guild.get_member(user_id)
        if not member:
            continue

        # Apply company filter if specified
        if company_filter:
            member_company = _get_member_company_name(member)
            if member_company != company_filter:
                continue

        # Get damage tier from roles (more accurate than stored state)
        current_tier = _get_member_damage_tier(member)
        points_since_blessing = state.get("points_since_blessing", 0)
        spirit_fractured = state.get("spirit_fractured", False)

        # Roll detection for this brother (cached per AAR cycle)
        scan_result = await _get_or_roll_scan_result(
            user_id, current_tier, points_since_blessing, spirit_fractured
        )
        
        # Intensive scan bypasses miss chance
        if has_intensive and not scan_result["detected"]:
            scan_result = {"detected": True, "predictive_warning": False, "miss_reason": None}

        risk_score = _calculate_armor_risk_score(
            current_tier, points_since_blessing, spirit_fractured
        )

        # Include if they have risk OR if there's a predictive warning OR if scan missed (damaged but undetected)
        if risk_score > 0 or scan_result.get("predictive_warning") or not scan_result["detected"]:
            risk_list.append((member, state, current_tier, risk_score, scan_result))

    # Sort by risk score descending to get the highest-risk brothers
    risk_list.sort(key=lambda x: x[3], reverse=True)

    # Take top 10
    top_10 = risk_list[:10]
    
    # Within top 10, move unreadable brothers to the bottom in random order
    import random
    readable = [e for e in top_10 if e[4]["detected"]]
    unreadable = [e for e in top_10 if not e[4]["detected"]]
    random.shuffle(unreadable)
    top_10 = readable + unreadable

    # Build description based on company filter
    if company_filter:
        company_short = _extract_company_short_name(company_filter)
        no_risk_desc = f"*All brothers in {company_short} nominal. No maintenance required.*"
        with_risk_desc = f"*Top 10 brothers in {company_short} requiring attention*"
    else:
        no_risk_desc = "*All brothers nominal. No maintenance required.*"
        with_risk_desc = "*Top 10 brothers requiring attention*"

    # Get MachineSpirit emoji
    machine_spirit_emoji = _get_emoji_by_name(guild, "MachineSpirit") or "⚙️"
    
    # Build intensive scan indicator for embed description
    intensive_indicator = f"\n🔬 **Intensive Scan ACTIVE** — 100% detection" if has_intensive else ""
    
    if not top_10:
        embed = discord.Embed(
            title="᛭⋅ ARMOR INTEGRITY SCAN ⋅᛭",
            description=f"{no_risk_desc}{intensive_indicator}",
            color=0x2ECC71,  # Green
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    # Build the leaderboard embed
    embed = discord.Embed(
        title="᛭⋅ ARMOR INTEGRITY SCAN ⋅᛭",
        description=f"{with_risk_desc}{intensive_indicator}",
        color=0xE67E22,  # Orange
    )

    lines = []
    for i, (member, state, current_tier, risk_score, scan_result) in enumerate(top_10, 1):
        points = state.get("points_since_blessing", 0)
        spirit_fractured = state.get("spirit_fractured", False)
        predictive_warning = scan_result.get("predictive_warning", False)
        scan_missed = not scan_result["detected"]

        # Get display name (short) - always show name even if scan missed
        bearer_honorific, bearer_name, _ = _get_bearer_rank_and_title(member)
        bearer_name = bearer_name.replace("●", "").replace("⚬", "").strip()
        # Truncate long names
        if len(bearer_name) > 18:
            bearer_name = bearer_name[:16] + "…"

        # Get rank emoji
        bearer_rank_name = None
        for rank, hon in RANK_HONORIFICS.items():
            if hon == bearer_honorific or rank in bearer_honorific:
                bearer_rank_name = rank
                break
        if not bearer_rank_name:
            bearer_rank_name = "Watch Brother"
        rank_emoji = _get_rank_emoji(guild, bearer_rank_name) if guild else ""
        rank_str = f"{rank_emoji} " if rank_emoji else ""

        # Get home chapter emoji
        bearer_chapter = _get_bearer_home_chapter(member)
        chapter_emoji = (
            _get_emoji_by_name(guild, bearer_chapter)
            if bearer_chapter and guild
            else None
        )
        chapter_str = f"{chapter_emoji}" if chapter_emoji else ""

        # Handle missed scans - show name but mask data
        if scan_missed:
            icon = "⚫"
            chapter_sep = f"{chapter_str} · " if chapter_str else "· "
            lines.append(
                f"`{i:>2}.` {icon} {rank_str}{bearer_name} {chapter_sep}???"
            )
            continue

        # Check if brother is on blessing cooldown
        can_receive, _, _, block_reason = await _check_recipient_cooldown(member.id)
        cooldown_indicator = " ⏳" if not can_receive else ""

        # Status icon - predictive warnings get special indicator
        if spirit_fractured:
            icon = "💀"
        elif current_tier == "critical":
            icon = "🔴"
        elif current_tier == "compromised":
            icon = "🟠"
        elif current_tier == "damaged":
            icon = "🟡"
        elif predictive_warning:
            icon = "⚡"  # Warning for nominal brothers at risk
        else:
            icon = "🟢"

        # Format compact line: "1. 🔴 :rank: Name :chapter: · 275c ⏳"
        # Status indicated by icon only (no text label needed)
        chapter_sep = f"{chapter_str} · " if chapter_str else "· "
        lines.append(
            f"`{i:>2}.` {icon} {rank_str}{bearer_name} {chapter_sep}{points}c{cooldown_indicator}"
        )

    embed.add_field(
        name="▸ Brothers at Risk",
        value="\n".join(lines),
        inline=False,
    )

    # Add legend (compact) - include unreadable symbol and cooldown
    legend = "💀Fractured 🔴Critical 🟠Compromised 🟡Damaged ⚡At Risk 🟢Nominal ⚫Unreadable ⏳Cooldown"
    embed.add_field(
        name="▸ Key",
        value=legend,
        inline=False,
    )

    # Add invoker's blessing pool status
    if pool_remaining is not None:
        pool_bar = "●" * pool_remaining + "○" * (BLESSING_POOL_MAX - pool_remaining)
        if pool_next_regen and pool_remaining < BLESSING_POOL_MAX:
            hours, remainder = divmod(int(pool_next_regen.total_seconds()), 3600)
            minutes = remainder // 60
            regen_str = f" · +1 in {hours}h {minutes}m" if hours else f" · +1 in {minutes}m"
        else:
            regen_str = ""
        embed.add_field(
            name="▸ Your Blessing Pool",
            value=f"{pool_bar} ({pool_remaining}/{BLESSING_POOL_MAX}){regen_str}\n`/forge_rite @brother`",
            inline=True,
        )

    # Add forge requisition pool status
    try:
        forge_status = await _get_forge_pool_status()
        forge_available = forge_status["available"]
        forge_charges = forge_status["charges_available"]
        intensive_scans_available = forge_available // INTENSIVE_SCAN_COST
        embed.add_field(
            name="▸ Forge Reserves",
            value=(
                f"**{forge_available:,}** pts │ {forge_charges} charges │ {intensive_scans_available} scans\n"
                f"`/requisition_supplies`"
            ),
            inline=True,
        )
    except Exception:
        pass

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="armor_status",
    description="View top 10 at-risk brothers in your company (Techmarine) or all (Forgemaster).",
)
async def _armor_status(interaction: discord.Interaction):
    """Display armor integrity leaderboard scoped by role."""
    # Permission check: caller must be techmarine or forgemaster
    allowed, role_key = _is_techmarine_or_forgemaster(
        interaction.user, command_name="armor_status"
    )
    if not allowed:
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    # Channel restriction
    channel_id = getattr(interaction.channel, "id", None)
    allowed_channels = _get_armor_status_allowed_channels()
    if channel_id not in allowed_channels:
        await interaction.response.send_message(
            "This command may only be used in the arming chamber or Techmarine channels.",
            ephemeral=True,
        )
        return

    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("Guild not found.", ephemeral=True)
        return

    # Determine company filter based on role
    company_filter = None
    if role_key == "forgemaster":
        # Forgemaster sees all
        company_filter = None
    else:
        # Techmarine sees only their company
        company_filter = _get_member_company_name(interaction.user)
        if not company_filter:
            await interaction.response.send_message(
                "You must be assigned to a Watch Company to view armor status.",
                ephemeral=True,
            )
            return

    # Get invoker's blessing pool status
    pool_remaining, pool_next_regen = await _get_blessing_pool_display(interaction.user.id)

    await _show_armor_leaderboard(
        interaction,
        guild,
        company_filter=company_filter,
        pool_remaining=pool_remaining,
        pool_next_regen=pool_next_regen,
        techmarine_id=interaction.user.id,
    )


@bot.tree.command(
    name="requisition_supplies",
    description="Spend community armory reserves for blessing charges or intensive scans.",
)
@app_commands.describe(
    requisition_type="What to requisition: blessing charge (restore pool) or intensive scan (guaranteed detection)",
)
@app_commands.choices(
    requisition_type=[
        app_commands.Choice(name="Blessing Charge (+1 to pool)", value="blessing_charge"),
        app_commands.Choice(name="Intensive Scan (3000 pts, 100% detection)", value="intensive_scan"),
    ]
)
async def _requisition_supplies(
    interaction: discord.Interaction,
    requisition_type: str = "blessing_charge",
):
    """Techmarine command to requisition supplies from the forge pool."""
    # Permission check: caller must be techmarine or forgemaster
    allowed, role_key = _is_techmarine_or_forgemaster(
        interaction.user, command_name="requisition_supplies"
    )
    if not allowed:
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    # Channel restriction (same as armor_status)
    channel_id = getattr(interaction.channel, "id", None)
    allowed_channels = _get_armor_status_allowed_channels()
    if channel_id not in allowed_channels:
        await interaction.response.send_message(
            "This command may only be used in the arming chamber or Techmarine channels.",
            ephemeral=True,
        )
        return

    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("Guild not found.", ephemeral=True)
        return

    # Branch based on requisition type
    if requisition_type == "intensive_scan":
        await _handle_intensive_scan_requisition(interaction, guild)
        return

    # Default: blessing charge requisition
    # Check current blessing pool status
    pool_remaining, pool_next_regen = await _get_blessing_pool_display(interaction.user.id)
    
    # Don't allow requisition if pool is full
    if pool_remaining >= BLESSING_POOL_MAX:
        await interaction.response.send_message(
            f"Your blessing pool is already full ({pool_remaining}/{BLESSING_POOL_MAX}). "
            "No requisition needed.",
            ephemeral=True,
        )
        return

    # Check daily usage
    daily_used = await _get_techmarine_daily_requisitions(interaction.user.id)
    if daily_used >= FORGE_POOL_DAILY_LIMIT:
        await interaction.response.send_message(
            f"Daily requisition limit reached ({FORGE_POOL_DAILY_LIMIT} per day). "
            "The Forge requires time to process additional requests.",
            ephemeral=True,
        )
        return

    # Get forge pool status for display
    forge_status = await _get_forge_pool_status()
    available = forge_status["available"]
    cost = forge_status["cost_per_charge"]
    
    if available < cost:
        await interaction.response.send_message(
            f"**Forge Requisition Denied**\n\n"
            f"Community armory reserves: **{available}** points\n"
            f"Required for blessing charge: **{cost}** points\n\n"
            f"*The Chapter must recover more armory data before supplies can be requisitioned.*",
            ephemeral=True,
        )
        return

    # Attempt to consume the requisition
    success, message = await _consume_forge_requisition(interaction.user.id)
    
    if not success:
        await interaction.response.send_message(
            f"**Forge Requisition Failed**\n\n{message}",
            ephemeral=True,
        )
        return

    # Grant an immediate blessing charge by resetting the oldest timestamp
    # This effectively gives them back one blessing slot immediately
    await _grant_blessing_charge(interaction.user.id)
    
    # Get updated pool status
    new_pool, _ = await _get_blessing_pool_display(interaction.user.id)
    new_forge_status = await _get_forge_pool_status()
    
    # Get the Techmarine's name 
    tech_name = interaction.user.display_name.replace("●", "").replace("⚬", "").strip()
    
    embed = discord.Embed(
        title="⚙️ FORGE REQUISITION APPROVED",
        description="*Sacred oils and blessed unguents have been allocated.*",
        color=0x2ECC71,
    )
    
    embed.add_field(
        name="▸ Requisitioner",
        value=f"**{tech_name}**",
        inline=True,
    )
    
    embed.add_field(
        name="▸ Blessing Pool",
        value=f"{'●' * new_pool}{'○' * (BLESSING_POOL_MAX - new_pool)} ({new_pool}/{BLESSING_POOL_MAX})",
        inline=True,
    )
    
    embed.add_field(
        name="▸ Forge Reserves",
        value=f"**{new_forge_status['available']}** armory points\n({new_forge_status['charges_available']} charges available)",
        inline=True,
    )
    
    embed.add_field(
        name="▸ Daily Usage",
        value=f"{daily_used + 1}/{FORGE_POOL_DAILY_LIMIT} requisitions today",
        inline=True,
    )
    
    embed.set_footer(text="The Omnissiah provides. Use these gifts wisely.")

    await interaction.response.send_message(embed=embed, ephemeral=True)


async def _grant_blessing_charge(user_id: int):
    """Grant one blessing charge to a Techmarine by removing the oldest timestamp."""
    async with BLESSING_POOL_LOCK:
        data = _load_blessing_pool()
        state = data.get(str(user_id), {
            "remaining_blessings": BLESSING_POOL_MAX,
            "blessing_timestamps": [],
        })
        
        timestamps = state.get("blessing_timestamps", [])
        
        if not timestamps:
            # Already at max, nothing to remove
            return
        
        # Sort timestamps and remove the oldest one
        now = datetime.utcnow()
        regen_seconds = BLESSING_POOL_REGEN_HOURS * 3600
        
        # Find active (non-regenerated) timestamps
        active_timestamps = []
        for ts_str in timestamps:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00").replace("+00:00", ""))
                elapsed = (now - ts).total_seconds()
                if elapsed < regen_seconds:
                    active_timestamps.append((ts, ts_str))
            except Exception:
                pass
        
        if active_timestamps:
            # Remove the oldest active timestamp (grants one blessing back)
            active_timestamps.sort(key=lambda x: x[0])
            remaining_ts = [ts_str for _, ts_str in active_timestamps[1:]]
        else:
            remaining_ts = []
        
        state["blessing_timestamps"] = remaining_ts
        state["remaining_blessings"] = BLESSING_POOL_MAX - len(remaining_ts)
        
        data[str(user_id)] = state
        _save_blessing_pool(data)


async def _handle_intensive_scan_requisition(
    interaction: discord.Interaction,
    guild: discord.Guild,
):
    """Handle intensive scan requisition (100% detection for this AAR cycle)."""
    tech_id = interaction.user.id
    
    # Check if already has active intensive scan
    if await _has_intensive_scan(tech_id):
        await interaction.response.send_message(
            "**Intensive Scan Already Active**\n\n"
            "*Your augur arrays are already operating at maximum sensitivity for this cycle.*\n"
            "The scan expires when new armory data is ingested.",
            ephemeral=True,
        )
        return
    
    # Get forge pool status
    forge_status = await _get_forge_pool_status()
    available = forge_status["available"]
    
    if available < INTENSIVE_SCAN_COST:
        await interaction.response.send_message(
            f"**Intensive Scan Denied**\n\n"
            f"Community armory reserves: **{available}** points\n"
            f"Required for intensive scan: **{INTENSIVE_SCAN_COST}** points\n\n"
            f"*Insufficient resources to power the augur arrays at maximum sensitivity.*",
            ephemeral=True,
        )
        return
    
    # Consume the points directly from forge pool
    async with FORGE_POOL_LOCK:
        pool_data = _load_forge_pool()
        max_balance = FORGE_POOL_MAX_CHARGES * FORGE_POOL_COST_PER_CHARGE
        pool_data["balance"] = pool_data.get("balance", max_balance) - INTENSIVE_SCAN_COST
        _save_forge_pool(pool_data)
    
    # Activate intensive scan for this Techmarine
    await _purchase_intensive_scan(tech_id)
    
    # Get updated forge status
    new_forge_status = await _get_forge_pool_status()
    
    # Get the Techmarine's name
    tech_name = interaction.user.display_name.replace("●", "").replace("⚬", "").strip()
    
    embed = discord.Embed(
        title="🔬 INTENSIVE SCAN ACTIVATED",
        description=(
            "*Augur arrays recalibrated to maximum sensitivity.*\n"
            "*All armor spirits shall be revealed, none shall hide from the Omnissiah's gaze.*"
        ),
        color=0x9B59B6,  # Purple for special scan
    )
    
    embed.add_field(
        name="▸ Requisitioner",
        value=f"**{tech_name}**",
        inline=True,
    )
    
    embed.add_field(
        name="▸ Cost",
        value=f"**{INTENSIVE_SCAN_COST}** armory points",
        inline=True,
    )
    
    embed.add_field(
        name="▸ Forge Reserves",
        value=f"**{new_forge_status['available']}** pts remaining",
        inline=True,
    )
    
    embed.add_field(
        name="▸ Effect",
        value=(
            "• 100% detection for all armor states\n"
            "• Bypasses spirit uncommunicative readings\n"
            "• Expires when new armory data is ingested"
        ),
        inline=False,
    )
    
    embed.set_footer(text="The Machine Spirit yields its secrets. Use /armor_status now.")

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─────────────────────────────────────────────────────────────────────────────
# Forge Chronicle Dashboard
# ─────────────────────────────────────────────────────────────────────────────


async def _build_forge_chronicle_embed(guild: discord.Guild) -> discord.Embed:
    """Build the Forge Chronicle dashboard embed with atmospheric stats."""
    # Load chronicle data
    async with FORGE_CHRONICLE_LOCK:
        data = _load_forge_chronicle()
    
    rite_history = data.get("rite_history", [])
    techmarine_stats = data.get("techmarine_stats", {})
    
    # Get forge pool status
    forge_status = await _get_forge_pool_status()
    available = forge_status["available"]
    max_balance = FORGE_POOL_MAX_CHARGES * FORGE_POOL_COST_PER_CHARGE
    
    # Count rites this month
    now = datetime.utcnow()
    first_of_month = datetime(now.year, now.month, 1)
    monthly_rites = []
    for entry in rite_history:
        try:
            ts = datetime.fromisoformat(entry.get("ts", ""))
            if ts >= first_of_month:
                monthly_rites.append(entry)
        except Exception:
            pass
    
    # Machine spirit stats from rite history
    first_bindings_month = sum(1 for r in monthly_rites if r.get("event") == "first_binding")
    rebirths_month = sum(1 for r in monthly_rites if r.get("event") == "rebirth")
    total_rites_month = len(monthly_rites)
    
    # Load machine spirits for count
    spirits_data = _load_machine_spirits()
    total_spirits = len(spirits_data)
    
    # Get MachineSpirit emoji for the dashboard
    machine_spirit_emoji = _get_emoji_by_name(guild, "MachineSpirit") or "⚙️"
    
    # Build forge reserve bar
    reserve_pct = (available / max_balance) * 100 if max_balance > 0 else 0
    filled_blocks = int(reserve_pct / 10)
    empty_blocks = 10 - filled_blocks
    reserve_bar = "█" * filled_blocks + "░" * empty_blocks
    
    # Build embed
    embed = discord.Embed(
        title="᛭⋅ FORGE CHRONICLE ⋅᛭",
        description="*The Forge rests in prepared silence...*",
        color=0x5D6D7E,  # Steel gray
    )
    
    # Forge Reserve field
    embed.add_field(
        name="▸ Forge Reserve",
        value=f"`[{reserve_bar}]` {reserve_pct:.0f}%\n{available}/{max_balance} armory pts",
        inline=True,
    )
    
    # This Month field
    embed.add_field(
        name="▸ Rites This Cycle",
        value=f"**{total_rites_month}** total\n{first_bindings_month} bindings • {rebirths_month} rebirths",
        inline=True,
    )
    
    # Machine Spirits field
    embed.add_field(
        name=f"▸ {machine_spirit_emoji} Spirits Bound",
        value=f"**{total_spirits}** active designations",
        inline=True,
    )
    
    # Techmarine activity (most active this month)
    if techmarine_stats:
        # Sort by total rites descending
        sorted_techs = sorted(
            techmarine_stats.items(),
            key=lambda x: x[1].get("total_rites", 0),
            reverse=True,
        )[:3]  # Top 3
        
        tech_lines = []
        for tech_id, stats in sorted_techs:
            total = stats.get("total_rites", 0)
            if total > 0:
                member = guild.get_member(int(tech_id))
                if member:
                    name = member.display_name.replace("●", "").replace("⚬", "").strip()
                    tech_lines.append(f"• {name}: {total} rites")
        
        if tech_lines:
            embed.add_field(
                name="▸ Forge Keepers",
                value="\n".join(tech_lines),
                inline=False,
            )
    
    # Footer with timestamp
    embed.set_footer(text=f"᛭⋅ Chronicle updated {now.strftime('%Y-%m-%d %H:%M')} UTC ⋅᛭")
    
    return embed


@bot.tree.command(
    name="forge_chronicle",
    description="Post or update the Forge Chronicle dashboard (atmospheric forge stats).",
)
async def _forge_chronicle_cmd(interaction: discord.Interaction):
    """Post or update the Forge Chronicle dashboard in the current channel."""
    # Permission check: uses config command_permissions (Forgemaster only)
    if not check_command_permission(interaction.user, "forge_chronicle"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    
    # Channel restriction: arming chamber only (config-driven)
    channel_id = getattr(interaction.channel, "id", None)
    arming_chamber_id = _get_arming_chamber_channel_id()
    if channel_id != arming_chamber_id:
        await interaction.response.send_message(
            "This command may only be used in the arming chamber.",
            ephemeral=True,
        )
        return
    
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("Guild not found.", ephemeral=True)
        return
    
    channel = interaction.channel
    if not channel:
        await interaction.response.send_message("Channel not found.", ephemeral=True)
        return
    
    # Check if we have an existing dashboard message to update
    existing_msg_id = await _get_dashboard_message_id()
    
    # Build the dashboard embed
    embed = await _build_forge_chronicle_embed(guild)
    
    try:
        if existing_msg_id:
            # Try to update existing message
            try:
                existing_msg = await channel.fetch_message(existing_msg_id)
                await existing_msg.edit(embed=embed)
                await interaction.response.send_message(
                    "Forge Chronicle updated.", ephemeral=True
                )
                return
            except discord.NotFound:
                # Message was deleted, create new one
                pass
        
        # Create new dashboard message
        await interaction.response.defer(thinking=False)
        sent_msg = await channel.send(embed=embed)
        await _set_dashboard_message_id(sent_msg.id)
        
        # Try to pin the message
        try:
            await sent_msg.pin()
        except Exception:
            pass  # Don't fail if we can't pin
        
        await interaction.followup.send(
            "Forge Chronicle posted.", ephemeral=True
        )
    except Exception as e:
        try:
            await interaction.response.send_message(
                f"Failed to post chronicle: {e}", ephemeral=True
            )
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Ambient Messages Task
# ─────────────────────────────────────────────────────────────────────────────

# Ambient message configuration
AMBIENT_MESSAGE_MIN_QUIET_HOURS = 6  # Hours of quiet before ambient can trigger
AMBIENT_MESSAGE_MIN_INTERVAL_HOURS = 12  # Minimum hours between ambient messages
AMBIENT_MESSAGE_CHANCE = 0.25  # 25% chance to post when eligible


async def _maybe_post_ambient_message():
    """Check if the forge has been quiet and maybe post an ambient message."""
    import random
    
    channel_id = _get_arming_chamber_channel_id()
    if not channel_id:
        return
    
    guild = None
    channel = None
    for g in bot.guilds:
        channel = g.get_channel(channel_id)
        if channel:
            guild = g
            break
    
    if not guild or not channel:
        return
    
    # Check last ambient timestamp
    last_ambient = await _get_last_ambient_ts()
    now = datetime.utcnow()
    
    if last_ambient:
        hours_since_ambient = (now - last_ambient).total_seconds() / 3600
        if hours_since_ambient < AMBIENT_MESSAGE_MIN_INTERVAL_HOURS:
            return  # Too soon since last ambient
    
    # Check recent rite activity
    async with FORGE_CHRONICLE_LOCK:
        data = _load_forge_chronicle()
    
    rite_history = data.get("rite_history", [])
    
    # Find most recent rite timestamp
    most_recent_rite = None
    for entry in reversed(rite_history):
        try:
            most_recent_rite = datetime.fromisoformat(entry.get("ts", ""))
            break
        except Exception:
            pass
    
    if most_recent_rite:
        hours_since_rite = (now - most_recent_rite).total_seconds() / 3600
        if hours_since_rite < AMBIENT_MESSAGE_MIN_QUIET_HOURS:
            return  # Forge has been active recently
    
    # Random chance to post
    if random.random() > AMBIENT_MESSAGE_CHANCE:
        return
    
    # Post ambient message
    try:
        message = random.choice(FORGE_AMBIENT_MESSAGES)
        await channel.send(message)
        await _set_last_ambient_ts()
        logger.info(f"Posted ambient forge message: {message[:50]}...")
    except Exception as e:
        logger.warning(f"Failed to post ambient message: {e}")


@tasks.loop(minutes=30)
async def _forge_ambient_loop():
    """Check every 30 minutes whether to post an ambient forge message."""
    try:
        # Skip first run to avoid immediate post on startup
        if not getattr(_forge_ambient_loop, "_first_run_done", False):
            setattr(_forge_ambient_loop, "_first_run_done", True)
            return
        
        await _maybe_post_ambient_message()
    except Exception as e:
        logger.warning(f"Ambient message loop error: {e}")


@tasks.loop(minutes=30)
async def _forge_dashboard_loop():
    """Update the Forge Chronicle dashboard every 30 minutes."""
    try:
        # Skip first run
        if not getattr(_forge_dashboard_loop, "_first_run_done", False):
            setattr(_forge_dashboard_loop, "_first_run_done", True)
            return
        
        dashboard_msg_id = await _get_dashboard_message_id()
        if not dashboard_msg_id:
            return  # No dashboard to update
        
        channel_id = _get_arming_chamber_channel_id()
        if not channel_id:
            return
        
        guild = None
        channel = None
        for g in bot.guilds:
            ch = g.get_channel(channel_id)
            if ch:
                guild = g
                channel = ch
                break
        
        if not guild or not channel:
            return
        
        try:
            msg = await channel.fetch_message(dashboard_msg_id)
            embed = await _build_forge_chronicle_embed(guild)
            await msg.edit(embed=embed)
            logger.debug("Updated Forge Chronicle dashboard")
        except discord.NotFound:
            # Dashboard message was deleted, clear the stored ID
            async with FORGE_CHRONICLE_LOCK:
                data = _load_forge_chronicle()
                data["dashboard_message_id"] = None
                _save_forge_chronicle(data)
        except Exception as e:
            logger.warning(f"Failed to update dashboard: {e}")
    except Exception as e:
        logger.warning(f"Dashboard loop error: {e}")


@bot.tree.command(
    name="preview_armor_alert",
    description="[DEBUG] Preview armor damage alert for a brother.",
)
@app_commands.describe(
    brother="Brother to preview",
    tier="Damage tier to simulate",
    critical_count="Number of AARs at critical (for critical tier countdown)",
)
@app_commands.choices(
    tier=[
        app_commands.Choice(name="Damaged", value="damaged"),
        app_commands.Choice(name="Compromised", value="compromised"),
        app_commands.Choice(name="Critical", value="critical"),
    ]
)
async def _preview_armor_alert(
    interaction: discord.Interaction,
    brother: discord.Member,
    tier: str = "damaged",
    critical_count: int = 1,
):
    """Preview armor damage alert without modifying roles or state."""
    # Permission check: caller must be techmarine or forgemaster
    allowed, _ = _is_techmarine_or_forgemaster(interaction.user)
    if not allowed:
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    guild = interaction.guild
    config = _get_armor_config()
    fracture_threshold = config.get(
        "fracture_threshold", DEFAULT_ARMOR_FRACTURE_THRESHOLD
    )

    # Get bearer info using the same pattern as forge_rite/stud announcements
    bearer_honorific, bearer_name, bearer_title = _get_bearer_rank_and_title(brother)
    bearer_name = bearer_name.replace("●", "").replace("⚬", "").strip()

    # Service studs computation
    bearer_studs = _compute_member_service_studs(brother)

    # Machine spirit designation
    machine_spirit = await _get_machine_spirit(int(brother.id))

    # Home chapter (lineage)
    bearer_chapter = _get_bearer_home_chapter(brother)
    chapter_emoji = (
        _get_emoji_by_name(guild, bearer_chapter) if bearer_chapter and guild else None
    )

    # Get rank emoji
    bearer_rank_name = None
    for rank, hon in RANK_HONORIFICS.items():
        if hon == bearer_honorific or rank in bearer_honorific:
            bearer_rank_name = rank
            break
    if not bearer_rank_name:
        bearer_rank_name = "Watch Brother"

    rank_emoji = _get_rank_emoji(guild, bearer_rank_name) if guild else ""
    rank_prefix = f"{rank_emoji} " if rank_emoji else ""

    # Build bearer display string (matching forge_rite style)
    if ", " in bearer_honorific:
        title_part, rank_part = bearer_honorific.rsplit(", ", 1)
        bearer_display = (
            f"{rank_prefix}**{title_part},**\n**{rank_part} {bearer_name}**"
        )
    else:
        bearer_display = f"{rank_prefix}**{bearer_honorific} {bearer_name}**"

    if bearer_title:
        bearer_display += f"\n*{bearer_title}*"
    # Lineage (home chapter)
    if bearer_chapter and bearer_chapter != "Unknown":
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        if bearer_chapter == "Black Shield":
            bearer_display += f"\nLineage: {chapter_prefix}REDACTED"
        else:
            bearer_display += f"\nLineage: {chapter_prefix}{bearer_chapter}"
    if bearer_studs > 0:
        studs_pips = _studs_pips(bearer_studs)
        bearer_display += f"\nService Studs: [{studs_pips}] ({bearer_studs})"
    # Machine spirit
    machine_spirit_emoji = _get_emoji_by_name(guild, "MachineSpirit") or "⚙️"
    if machine_spirit:
        bearer_display += f"\n{machine_spirit_emoji} Spirit: `{machine_spirit}`"
    else:
        bearer_display += f"\n{machine_spirit_emoji} Spirit: *UNBOUND*"

    # Determine embed color and title based on tier
    if tier == "critical":
        color = 0xE74C3C  # Red
        title = "᛭⋅ CRITICAL ARMOR FAILURE ⋅᛭"
        description = "*Machine spirit instability detected*"
    else:
        color = 0xE67E22  # Orange
        title = "᛭⋅ ARMOR INTEGRITY ALERT ⋅᛭"
        description = "*Maintenance required*"

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
    )

    # Affected brother field with proper rank display
    tier_display = tier.title() if tier else "Unknown"
    penalty_risk = _get_tier_risk_display(tier, spirit_fractured=False)
    penalty = _get_damage_penalty(tier)
    embed.add_field(
        name="▸ Affected Brother",
        value=(
            f"{bearer_display}"
            f"\n**Status:** {tier_display}"
            f"\n**Penalty Risk:** {penalty_risk}"
            f"\n**Fixed Penalty:** {penalty}"
        ),
        inline=False,
    )

    # Warning field for critical
    if tier == "critical":
        remaining = fracture_threshold - critical_count
        embed.add_field(
            name="▸ Warning",
            value=f"⚠️ AAR submissions until spirit fracture: **{remaining}**",
            inline=False,
        )
        embed.add_field(
            name="▸ Immediate Techmarine Response Required",
            value="Administer blessing via `/forge_rite` to preserve machine spirit bond.",
            inline=False,
        )
    else:
        embed.add_field(
            name="▸ Techmarine Response Required",
            value="Administer blessing via `/forge_rite` to restore armor integrity.",
            inline=False,
        )

    # Build preview content
    tech_role_id = _get_techmarine_role_id()
    content = f"**[PREVIEW]** "
    if tech_role_id:
        content += f"<@&{tech_role_id}> {brother.mention}"
    else:
        content += f"@Watch Techmarine {brother.mention}"

    await interaction.response.send_message(
        content=content,
        embed=embed,
        ephemeral=True,
    )


@bot.tree.command(
    name="test_armor_alert",
    description="[DEBUG] Force-send a real armor alert to the arming chamber.",
)
@app_commands.describe(
    brother="Brother to test alert for",
    tier="Damage tier to simulate",
    critical_count="Number of AARs at critical (for critical tier countdown)",
)
@app_commands.choices(
    tier=[
        app_commands.Choice(name="Damaged", value="damaged"),
        app_commands.Choice(name="Compromised", value="compromised"),
        app_commands.Choice(name="Critical", value="critical"),
    ]
)
async def _test_armor_alert(
    interaction: discord.Interaction,
    brother: discord.Member,
    tier: str = "damaged",
    critical_count: int = 1,
):
    """Force-send a real armor alert to test the system."""
    # Permission check: admin only
    user_id = str(interaction.user.id)
    admin_ids = [str(a) for a in CONFIG.get("admin_user_ids", [])]
    if user_id not in admin_ids:
        await interaction.response.send_message("Admin only.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        await _post_armor_alert(
            member=brother,
            tier=tier,
            critical_aar_count=critical_count,
            guild=interaction.guild,
        )
        await interaction.followup.send(
            f"✅ Alert sent for {brother.display_name} (tier={tier}). Check the arming chamber and logs.",
            ephemeral=True,
        )
    except Exception as e:
        await interaction.followup.send(
            f"❌ Error: {type(e).__name__}: {e}",
            ephemeral=True,
        )


@bot.tree.command(
    name="preview_stud_announcement",
    description="[DEBUG] Preview a service stud announcement for a member.",
)
@app_commands.describe(
    member="Member to preview",
    displayed_studs="Number of studs they're displaying (simulated). Omit to use actual nickname.",
    new_studs="Number of new studs being added to display (simulated). Omit to auto-calculate.",
    earned_studs_override="Override earned studs (for testing owed). Omit to use actual.",
)
async def _preview_stud_announcement(
    interaction: discord.Interaction,
    member: discord.Member,
    displayed_studs: Optional[int] = None,
    new_studs: Optional[int] = None,
    earned_studs_override: Optional[int] = None,
):
    """Debug command to preview service stud announcement output."""
    # Only allow in DEBUG_MODE or for admins
    if not DEBUG_MODE:
        user_id = str(interaction.user.id)
        admin_ids = [str(a) for a in CONFIG.get("admin_user_ids", [])]
        if user_id not in admin_ids:
            await interaction.response.send_message(
                "This command is only available in debug mode.", ephemeral=True
            )
            return

    # Defer to avoid interaction timeout during computation
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    if not guild:
        await interaction.followup.send("Must be used in a guild.", ephemeral=True)
        return

    # Get member's home chapter
    member_chapter = "Unknown"
    for role in getattr(member, "roles", []):
        role_name = getattr(role, "name", "")
        if role_name in HOME_CHAPTERS:
            member_chapter = role_name
            break

    # Calculate actual studs using same logic as activity check
    user_id = str(member.id)
    stats = compute_stats_for_user(user_id)
    aar_points = int(stats.get("aar_points", 0) or 0)

    # Get weeks in server
    joined_at = getattr(member, "joined_at", None)
    if joined_at:
        if joined_at.tzinfo is not None:
            joined_at = joined_at.replace(tzinfo=None)
        weeks_in_server = max(0, (datetime.utcnow() - joined_at).days // 7)
    else:
        weeks_in_server = 0

    # Compute earned studs (min of time-based and AAR-based)
    studs_time = weeks_in_server // 4
    studs_aar = aar_points // 400
    earned_studs = min(studs_time, studs_aar)

    # Allow override for testing owed studs
    if earned_studs_override is not None:
        earned_studs = earned_studs_override

    # Read displayed studs from nickname
    # New system: ●=4 (Auramite), ⚬=1 (Plasteel), max 16
    dn = str(member.nick or member.display_name or "")
    displayed_aur = dn.count("●")
    displayed_plas = dn.count("⚬")
    actual_displayed = displayed_aur * 4 + displayed_plas

    # Use provided displayed_studs or fall back to actual
    if displayed_studs is None:
        displayed_studs = actual_displayed

    # If new_studs not provided, default to displayed_studs (as if going from 0 to displayed)
    if new_studs is None:
        new_studs = displayed_studs

    owed_studs = max(0, earned_studs - displayed_studs)

    content, embed = _get_service_studs_announcement(
        member=member,
        member_chapter=member_chapter,
        displayed_studs=displayed_studs,
        new_studs=new_studs,
        earned_studs=earned_studs,
        owed_studs=owed_studs,
        guild=guild,
    )

    # Send ephemeral preview with debug info
    debug_info = (
        f"**[PREVIEW DEBUG]**\n"
        f"• Actual in nickname: {actual_displayed}\n"
        f"• Displayed (param): {displayed_studs}\n"
        f"• New (param): {new_studs}\n"
        f"• Earned: {earned_studs}\n"
        f"• Owed: {owed_studs}\n\n"
    )
    await interaction.followup.send(
        f"{debug_info}{content}",
        embed=embed,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


# No explicit group registration required for top-level commands


@bot.tree.command(
    name="reconcile_records", description="Reprocess AARs and update the archive."
)
@app_commands.describe(span_days="Optional: only scan messages from the last N days.")
async def reconcile_records(
    interaction: discord.Interaction, span_days: int | None = None
):
    if not (
        check_command_permission(interaction.user, "reconcile_records")
        and is_allowed_channel(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    # Serialize concurrent invocations to avoid file races
    if RECONCILE_LOCK.locked():
        logger.info(
            f"reconcile_records blocked: lock held (user={interaction.user.id})"
        )
        try:
            await interaction.response.send_message(
                "Another reconciliation is in progress. Please try again shortly.",
                ephemeral=True,
            )
        except Exception:
            logger.debug("Could not send 'locked' response to interaction; continuing.")
        return
    # Defer may fail (Unknown interaction) if the interaction is stale; handle gracefully.
    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
    except Exception as e:
        logger.debug(f"Interaction defer failed: {e}")

    logger.info(f"reconcile_records: acquiring lock (user={interaction.user.id})")
    async with RECONCILE_LOCK:
        logger.info(f"reconcile_records: lock acquired (user={interaction.user.id})")
        await _reconciliation_core(interaction, span_days)


@bot.tree.command(
    name="record_of_blood",
    description="Scan Watch Brothers' home chapters and cross-reference records in the record-of-blood channel.",
)
async def record_of_blood(interaction: discord.Interaction):
    # Restrict to Watch Master or Forgemaster only
    try:
        names = _canonical_role_names(interaction.user)
    except Exception:
        names = set()
    if not ("Watch Master" in names or "Forgemaster" in names):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
    except Exception:
        pass

    guild = interaction.guild or _resolve_notification_guild()
    if not guild:
        await interaction.followup.send("Unable to resolve guild.", ephemeral=True)
        return

    # Resolve Watch Brother role and members
    wb_role = discord.utils.get(guild.roles, name="Watch Brother")
    watch_brothers = []
    if wb_role:
        try:
            watch_brothers = list(getattr(wb_role, "members", []) or [])
        except Exception:
            watch_brothers = []
    # If role exists but members cache is empty, or role wasn't present, scan guild members as a fallback
    if not watch_brothers:
        try:
            for m in getattr(guild, "members", []) or []:
                try:
                    if "Watch Brother" in _canonical_role_names(m):
                        watch_brothers.append(m)
                except Exception:
                    continue
        except Exception:
            watch_brothers = watch_brothers or []

    # Map member id -> resolved home chapter role (from their roles)
    member_home: dict[str, str] = {}
    members_with_noncanonical_home: list[tuple[str, str]] = []
    for m in watch_brothers:
        chap = ""
        try:
            member_role_names = {
                (getattr(r, "name", "") or "").strip()
                for r in m.roles
                if getattr(r, "name", None)
            }
            match = next(
                (
                    hc
                    for hc in HOME_CHAPTERS
                    if any(rn.lower() == hc.lower() for rn in member_role_names)
                ),
                None,
            )
            if match:
                chap = match
            else:
                # Try datastore fallback if available
                try:
                    if DATASTORE:
                        ds_val = DATASTORE.get_home_chapter(str(getattr(m, "id", "")))
                        if ds_val:
                            chap = ds_val
                except Exception:
                    pass
        except Exception:
            chap = ""
        member_home[str(getattr(m, "id", ""))] = chap or ""
        if chap and chap not in HOME_CHAPTERS:
            members_with_noncanonical_home.append((m.display_name or m.name, chap))

    # Channel to cross-reference (from the provided URL)
    # URL: https://discord.com/channels/1429264578440597517/1446926555732250674
    target_channel_id = 1446926555732250674
    target_channel = bot.get_channel(target_channel_id) or guild.get_channel(
        target_channel_id
    )
    if not target_channel:
        await interaction.followup.send(
            f"Unable to find target channel <#{target_channel_id}>.", ephemeral=True
        )
        return

    # Scan messages for mentions of HOME_CHAPTERS (and any guild role names not in HOME_CHAPTERS)
    chapter_mentions_by_msg: list[dict] = []
    noncanonical_mentioned: set[str] = set()
    logger.info(
        f"/record_of_blood: scanning channel {target_channel_id} for {len(watch_brothers)} watch brothers"
    )
    try:
        async for msg in target_channel.history(limit=2000):
            content = msg.content or ""
            if not content:
                continue
            low = content.lower()
            # Detect chapter declared on first line in format ":emoji: ⋅ chaptername:".
            first_line = content.splitlines()[0].strip() if content.splitlines() else ""
            first_chap = None
            try:
                m = re.match(r"^:[^:]+:\s*⋅\s*(.+?):", first_line)
                if m:
                    first_chap = m.group(1).strip()
            except Exception:
                first_chap = None

            # Find explicit canonical chapter mentions in the body
            found = [hc for hc in HOME_CHAPTERS if hc.lower() in low]

            # Only consider messages that tag members — ignore others entirely
            mentions = getattr(msg, "mentions", []) or []
            if not mentions:
                continue

            # If a first-line chapter was declared, treat it as a referenced chapter
            if first_chap:
                if all(first_chap.lower() != hc.lower() for hc in found):
                    found.append(first_chap)
                if all(first_chap.lower() != hc.lower() for hc in HOME_CHAPTERS):
                    noncanonical_mentioned.add(first_chap)

            # Also detect guild role names mentioned that are not in HOME_CHAPTERS
            extra = [
                r.name
                for r in guild.roles
                if r.name and r.name.lower() in low and r.name not in HOME_CHAPTERS
            ]
            if extra:
                for e in extra:
                    noncanonical_mentioned.add(e)

            if not found and not extra:
                continue

            # Record for each mentioned member which chapters the message referenced
            rec = {
                "msg": msg,
                "chapters": found or extra,
                "mentions": [],
                "first_chap": first_chap,
            }
            for mm in mentions:
                try:
                    rec["mentions"].append(
                        {
                            "id": str(getattr(mm, "id", "")),
                            "display": mm.display_name or mm.name,
                        }
                    )
                except Exception:
                    continue
            chapter_mentions_by_msg.append(rec)
    except Exception as e:
        logger.debug(f"Failed scanning channel history: {e}")

    # Determine which canonical HOME_CHAPTERS were mentioned in the channel
    try:
        mentioned_canonical: set[str] = set()
        for rec in chapter_mentions_by_msg:
            try:
                for ch in rec.get("chapters", []) or []:
                    # normalize against canonical list
                    for hc in HOME_CHAPTERS:
                        if ch and ch.lower() == hc.lower():
                            mentioned_canonical.add(hc)
            except Exception:
                continue
        missing_home_chapters = [
            hc for hc in HOME_CHAPTERS if hc not in mentioned_canonical
        ]
    except Exception:
        mentioned_canonical = set()
        missing_home_chapters = []

    # Build report
    lines: list[str] = []
    lines.append("```ansi")
    lines.append(
        "\u001b[32m=============================================================================="
    )
    lines.append("  WATCH FORTRESS JERICHO // RECORD-OF-BLOOD AUDIT")
    lines.append(
        "=============================================================================="
    )
    lines.append(f"  Watch Brothers scanned: {len(watch_brothers)}")
    lines.append("")

    # Members whose home chapter is absent or non-canonical
    if members_with_noncanonical_home:
        lines.append("Members with home chapter not in canonical HOME_CHAPTERS:")
        for nm, ch in members_with_noncanonical_home:
            lines.append(f"  - {nm}: {ch}")
        lines.append("")

    # Chapters mentioned in channel but not canonical
    if noncanonical_mentioned:
        lines.append("Chapters/roles mentioned in channel not found in HOME_CHAPTERS:")
        for ch in sorted(noncanonical_mentioned):
            lines.append(f"  - {ch}")
        lines.append("")

    # HOME_CHAPTERS that were not mentioned in the scanned channel
    # BUT only report if we have brothers who rep that chapter
    try:
        missing_with_members = [
            ch
            for ch in missing_home_chapters
            if any(
                member_home.get(mid, "").lower() == ch.lower() for mid in member_home
            )
        ]
        if missing_with_members:
            lines.append(
                "Home chapters not mentioned in target channel (but have members):"
            )
            for ch in missing_with_members:
                lines.append(f"  - {ch}")
            lines.append("")
    except Exception:
        pass

    # Per-message findings
    if chapter_mentions_by_msg:
        lines.append("Channel message cross-references:")
        for rec in chapter_mentions_by_msg:
            try:
                msg = rec.get("msg")
                mids = rec.get("mentions", [])
                chs = rec.get("chapters", [])
                first_claim = rec.get("first_chap")
                first_claim_noncanonical = bool(
                    first_claim
                    and all(first_claim.lower() != hc.lower() for hc in HOME_CHAPTERS)
                )

                # Build concise one-line issues for each mismatch
                issues: list[str] = []
                for mrec in mids:
                    mid = mrec.get("id")
                    disp = mrec.get("display")
                    actual = member_home.get(mid, "")
                    claimed = first_claim or (chs[0] if chs else "")
                    is_match = bool(
                        claimed and claimed.lower() == (actual or "").lower()
                    )
                    if not is_match:
                        issues.append(
                            f"Message {getattr(msg, 'id', 'unknown')} | {disp}: record_of_blood='{claimed or ', '.join(chs)}' role='{actual or 'UNKNOWN'}'"
                        )

                # If declared chapter is non-canonical, add an issue for it
                if first_claim_noncanonical:
                    issues.insert(
                        0,
                        f"Message {getattr(msg, 'id', 'unknown')} | Declared chapter not in HOME_CHAPTERS: '{first_claim}'",
                    )

                # Append only the concise issue lines (one per mismatch/issue)
                for it in issues:
                    lines.append(it)
            except Exception:
                continue
        lines.append("")

    if (
        not members_with_noncanonical_home
        and not noncanonical_mentioned
        and not chapter_mentions_by_msg
    ):
        lines.append("No discrepancies or chapter mentions found in target channel.")

    lines.append(
        "=============================================================================="
    )
    lines.append("\u001b[0m```")

    report = "\n".join(lines)

    # Build mobile-friendly embed
    embed = discord.Embed(
        title="Record-of-Blood Audit",
        description=f"Watch Brothers scanned: {len(watch_brothers)}",
        color=0x2ECC71,
    )

    if members_with_noncanonical_home:
        noncanon_text = "\n".join(
            f"• {nm}: {ch}" for nm, ch in members_with_noncanonical_home[:10]
        )
        if len(members_with_noncanonical_home) > 10:
            noncanon_text += (
                f"\n... and {len(members_with_noncanonical_home) - 10} more"
            )
        embed.add_field(
            name="Non-canonical Home Chapters", value=noncanon_text, inline=False
        )

    if noncanonical_mentioned:
        noncm_text = ", ".join(sorted(noncanonical_mentioned)[:10])
        if len(noncanonical_mentioned) > 10:
            noncm_text += f" (+{len(noncanonical_mentioned) - 10} more)"
        embed.add_field(
            name="Non-canonical Chapters Mentioned", value=noncm_text, inline=False
        )

    # Collect discrepancy details
    discrepancy_details: list[str] = []
    for rec in chapter_mentions_by_msg:
        mids = rec.get("mentions", [])
        first_claim = rec.get("first_chap")
        for mrec in mids:
            mid = mrec.get("id")
            disp = mrec.get("display", "Unknown")
            actual = member_home.get(mid, "")
            claimed = first_claim or (
                rec.get("chapters", [])[0] if rec.get("chapters") else ""
            )
            if claimed and claimed.lower() != (actual or "").lower():
                discrepancy_details.append(
                    f"• {disp}: claimed **{claimed}**, role **{actual or 'NONE'}**"
                )
        if first_claim and all(
            first_claim.lower() != hc.lower() for hc in HOME_CHAPTERS
        ):
            discrepancy_details.append(
                f"• Non-canonical chapter declared: **{first_claim}**"
            )

    if discrepancy_details:
        # Show up to 10 discrepancies in the embed, with a note if there are more
        disc_text = "\n".join(discrepancy_details[:10])
        if len(discrepancy_details) > 10:
            disc_text += f"\n... and {len(discrepancy_details) - 10} more"
        embed.add_field(
            name=f"Discrepancies Found ({len(discrepancy_details)})",
            value=disc_text,
            inline=False,
        )
    else:
        embed.add_field(name="Status", value="No discrepancies found", inline=False)

    embed.set_footer(text="Use PC/Console button for detailed ANSI view")

    try:
        # Send as followup (deferred earlier). If the report is too large
        # for a single message, attach it as a file instead.
        if len(report) > 1900:
            import io

            fp = io.BytesIO(report.encode("utf-8"))
            fp.seek(0)
            try:
                await interaction.followup.send(
                    "Report too large for toggle view; attached as file.",
                    embed=embed,
                    file=discord.File(fp, filename="record_of_blood.txt"),
                    ephemeral=True,
                )
            finally:
                try:
                    fp.close()
                except Exception:
                    pass
        else:
            view = ToggleFormatView(text_content=report, embed=embed, default="embed")
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except Exception as e:
        logger.exception(f"record_of_blood: followup.send failed: {e}")
        try:
            if len(report) > 1900:
                import io

                fp = io.BytesIO(report.encode("utf-8"))
                fp.seek(0)
                try:
                    await interaction.response.send_message(
                        "Report attached.",
                        file=discord.File(fp, filename="record_of_blood.txt"),
                        ephemeral=True,
                    )
                finally:
                    try:
                        fp.close()
                    except Exception:
                        pass
            else:
                await interaction.response.send_message(report, ephemeral=True)
        except Exception as e2:
            logger.exception(
                f"record_of_blood: response.send_message fallback failed: {e2}"
            )


@bot.tree.command(
    name="audit_archive_discrepancies",
    description="Recheck previously rejected AARs and restore any fixed entries.",
)
@app_commands.describe(span_days="Optional: only recheck errors from the last N days.")
async def audit_archive_discrepancies(
    interaction: discord.Interaction, span_days: int | None = None
):
    if not (
        check_command_permission(interaction.user, "audit_archive_discrepancies")
        and is_allowed_channel(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    if RECONCILE_LOCK.locked():
        logger.info(
            f"audit_archive_discrepancies blocked: lock held (user={interaction.user.id})"
        )
        await interaction.response.send_message(
            "Another reconciliation is in progress. Please try again shortly.",
            ephemeral=True,
        )
        return

    interaction_deferred = False
    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
        interaction_deferred = True
    except Exception:
        interaction_deferred = False

    logger.info(f"audit_archive_discrepancies: acquiring lock (user={interaction.user.id})")
    async with RECONCILE_LOCK:
        logger.info(f"audit_archive_discrepancies: lock acquired (user={interaction.user.id})")
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

        author_summaries, stale_count = summarize_error_authors(max_age_weeks=4)
        author_lines = []
        for a in author_summaries:
            label = a.get("nickname") or a.get("username") or a.get("id") or "Unknown"
            author_lines.append(f"  {label}: {a['count']}")

        report = (
            "```ansi\n"
            "\u001b[32m==============================================================================\n"
            "  WATCH FORTRESS JERICHO // ARCHIVE-COGITATOR\n"
            "  OPERATION-SCRIBE SERVITOR — ERROR RECHECK RITE\n"
            "==============================================================================\n"
            f"  Restored: {fixed}\n"
            f"  Still Broken: {still_broken}\n"
        )
        if stale_count > 0:
            report += f"  Stale AARs (>4 weeks): {stale_count}\n"
        if author_lines:
            report += "-----------------------------------------------\n"
            report += "  Errors by Author (last 4 weeks):\n"
            for line in author_lines:
                report += f"{line}\n"
        report += (
            "==============================================================================\n"
            "\u001b[0m```"
        )
        # Try to send the report via followup if we successfully deferred.
        if interaction_deferred:
            try:
                await interaction.followup.send(report, ephemeral=True)
            except Exception as e:
                logger.debug(f"Failed to send followup report: {e}")
                # Fallback: attempt to post the report to the invoking channel
                try:
                    ch = interaction.channel
                    if ch:
                        await ch.send(report)
                    else:
                        logger.error("Unable to deliver report: no channel available.")
                except Exception:
                    logger.error(
                        "Unable to deliver report to channel; check bot permissions."
                    )
        else:
            # Interaction was not defer-able; post the report to the invoking channel if possible
            try:
                ch = interaction.channel
                if ch:
                    await ch.send(report)
                else:
                    logger.error(
                        "Unable to deliver report: no channel available and DM disabled."
                    )
            except Exception:
                logger.error(
                    "Unable to deliver report to channel; interaction unknown and channel send failed."
                )


@bot.tree.command(
    name="sanctify_battle_records",
    description="Ingest new sanctioned AARs (optionally scoped by span of days).",
)
@app_commands.describe(span_days="Optional: only scan messages from the last N days.")
async def sanctify_battle_records(
    interaction: discord.Interaction, span_days: int | None = None
):
    if not (
        check_command_permission(interaction.user, "sanctify_battle_records")
        and is_allowed_channel(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    if RECONCILE_LOCK.locked():
        logger.info(
            f"sanctify_battle_records blocked: lock held (user={interaction.user.id})"
        )
        await interaction.response.send_message(
            "Another reconciliation is in progress. Please try again shortly.",
            ephemeral=True,
        )
        return
    interaction_deferred = False
    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
        interaction_deferred = True
    except Exception:
        interaction_deferred = False

    logger.info(f"sanctify_battle_records: acquiring lock (user={interaction.user.id})")
    async with RECONCILE_LOCK:
        logger.info(f"sanctify_battle_records: lock acquired (user={interaction.user.id})")
        guild = interaction.guild
        aar_channel = discord.utils.get(
            guild.channels, name="᛭⋅⋅after-action-reports⋅⋅᛭"
        )
        if not aar_channel:
            if interaction_deferred:
                try:
                    await interaction.followup.send(
                        "++ ERROR: '᛭⋅⋅after-action-reports⋅⋅᛭' CHANNEL NOT FOUND. ++",
                        ephemeral=True,
                    )
                except Exception as e:
                    logger.debug(f"Failed to send followup: {e}")
            else:
                try:
                    ch = interaction.channel
                    if ch:
                        await ch.send(
                            "++ ERROR: '᛭⋅⋅after-action-reports⋅⋅᛭' CHANNEL NOT FOUND. ++"
                        )
                    else:
                        logger.error(
                            "Unable to deliver error report: no channel available and DM disabled."
                        )
                except Exception:
                    logger.error(
                        "Unable to deliver error report to channel; check bot permissions."
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
            + f"  Sanctioned: {ingested}\n"
            + f"  Rejected: {rejected}\n"
            + "==============================================================================\n"
            + "\u001b[0m```"
        )
        if interaction_deferred:
            try:
                await interaction.followup.send(report, ephemeral=True)
            except Exception as e:
                logger.debug(f"Failed to send followup report: {e}")
                try:
                    ch = interaction.channel
                    if ch:
                        await ch.send(report)
                    else:
                        logger.error("Unable to deliver report: no channel available.")
                except Exception:
                    logger.error(
                        "Unable to deliver report to channel; check bot permissions."
                    )
        else:
            try:
                ch = interaction.channel
                if ch:
                    await ch.send(report)
                else:
                    logger.error(
                        "Unable to deliver report: no channel available and DM disabled."
                    )
            except Exception:
                logger.error(
                    "Unable to deliver report to channel; check bot permissions."
                )


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
                # If the AAR has been processed since the error was recorded,
                # remove it from the errors archive rather than touching the
                # saved records. Previously this removed the record file by
                # mistake which prevented error entries from being cleared.
                try:
                    data = _load_json_dict(AAR_ERRORS_PATH)
                    sid = str(aar_id)
                    if sid in data:
                        reply_id = data.get(sid, {}).get("reply_id")
                        if reply_id:
                            try:
                                # reply is in the same channel as original message
                                dummy_msg = await aar_channel.fetch_message(aar_id)
                                try:
                                    reply_msg = await dummy_msg.channel.fetch_message(
                                        int(reply_id)
                                    )
                                    try:
                                        await reply_msg.delete()
                                    except Exception:
                                        try:
                                            logger.debug(
                                                f"Unable to delete reply {reply_id} for AAR {sid}"
                                            )
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                            except Exception:
                                pass
                        del data[sid]
                        _save_json_dict(AAR_ERRORS_PATH, data)
                except Exception:
                    pass
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
                        msg_dt = msg_dt.astimezone(timezone.utc).replace(tzinfo=None)
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
                try:
                    await _reply_aar_rejection(
                        msg,
                        [f"Jump URL: {msg.jump_url}", "Parse failed: record is None"],
                    )
                except Exception:
                    pass
                await _set_aar_reaction(msg, "error")
                still_broken += 1
            else:
                errors = validate_aar(record)
                if errors:
                    log_aar_error_with_meta(
                        aar_id, [f"Jump URL: {msg.jump_url}"] + errors, msg
                    )
                    try:
                        await _reply_aar_rejection(
                            msg, [f"Jump URL: {msg.jump_url}"] + errors
                        )
                    except Exception:
                        pass
                    await _set_aar_reaction(msg, "error")
                    still_broken += 1
                else:
                    await save_aar_record(record)

                    # --- Armor Integrity: Process cycles for reingested AAR ---
                    try:
                        guild = aar_channel.guild
                        brother_ids = record.get("brother_ids") or []
                        difficulty_class = record.get("difficulty_class")
                        if guild and brother_ids:
                            # Calculate base points per brother (same logic as _run_ingest_new)
                            base_points = {}
                            is_siege = difficulty_class in ("normal_siege", "hard_siege")
                            brother_waves = record.get("brother_waves") or {}
                            global_waves = record.get("waves") or 0
                            try:
                                global_waves = int(global_waves)
                            except Exception:
                                global_waves = 0
                            base_difficulty_points = {
                                "normal_op": 3,
                                "hard_op": 4,
                                "lethal_op": 5,
                                "suicide_op": 6,
                                "omega_op": 10,
                            }.get(difficulty_class, 0)
                            for bid in brother_ids:
                                if is_siege:
                                    waves_for_brother = brother_waves.get(bid)
                                    if waves_for_brother is None:
                                        waves_for_brother = global_waves
                                    try:
                                        waves_for_brother = int(waves_for_brother or 0)
                                    except Exception:
                                        waves_for_brother = 0
                                    if difficulty_class == "normal_siege":
                                        base_points[bid] = 3 * (waves_for_brother // 5)
                                    else:
                                        base_points[bid] = 4 * (waves_for_brother // 5)
                                else:
                                    base_points[bid] = base_difficulty_points

                            # Roll penalties for each brother (same logic as _run_ingest_new)
                            armor_penalties = {}
                            for bid in brother_ids:
                                try:
                                    member = guild.get_member(int(bid))
                                    if member:
                                        tier = _get_member_damage_tier(member)
                                        armor_state = await _get_armor_state(int(bid))
                                        spirit_fractured = armor_state.get("spirit_fractured", False)
                                        rolled_penalty = _roll_armor_penalty(tier, spirit_fractured)
                                        if rolled_penalty > 0:
                                            armor_penalties[bid] = rolled_penalty
                                except Exception:
                                    pass

                            # Process armor integrity for each brother
                            op_mission = record.get("mission")
                            op_url = record.get("message_url")
                            alerts_to_post = []
                            for bid in brother_ids:
                                try:
                                    bid_base_points = base_points.get(bid, 0)
                                    bid_actual_penalty = armor_penalties.get(bid, 0)
                                    penalty, alert_info = await _process_armor_integrity_for_aar(
                                        bid,
                                        bid_base_points,
                                        guild,
                                        None,  # No batch mode for recheck
                                        op_mission=op_mission,
                                        op_difficulty_class=difficulty_class,
                                        op_url=op_url,
                                        squad_member_ids=brother_ids,
                                        actual_penalty=bid_actual_penalty,
                                    )
                                    if alert_info:
                                        alerts_to_post.append(alert_info)
                                except Exception:
                                    pass
                            # Post any armor alerts
                            for alert in alerts_to_post:
                                try:
                                    await _post_armor_alert(
                                        alert["member"],
                                        alert["tier"],
                                        alert.get("critical_count", 0),
                                        guild,
                                        op_mission=alert.get("op_mission"),
                                        op_difficulty_class=alert.get("op_difficulty_class"),
                                        op_url=alert.get("op_url"),
                                        squad_member_ids=alert.get("squad_member_ids"),
                                        alert_type=alert.get("alert_type", "sustained"),
                                        penalty_amount=alert.get("penalty_amount", 0),
                                    )
                                except Exception:
                                    pass
                    except Exception:
                        pass

                    # If an error entry exists for this AAR, attempt to remove
                    # the bot's previous reply and clear the error record.
                    try:
                        data = _load_json_dict(AAR_ERRORS_PATH)
                        sid = str(aar_id)
                        if sid in data:
                            reply_id = data.get(sid, {}).get("reply_id")
                            if reply_id:
                                try:
                                    # reply is in the same channel as the original message
                                    reply_msg = await msg.channel.fetch_message(
                                        int(reply_id)
                                    )
                                    try:
                                        await reply_msg.delete()
                                    except Exception:
                                        try:
                                            logger.debug(
                                                f"Unable to delete reply {reply_id} for AAR {sid}"
                                            )
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                            del data[sid]
                            _save_json_dict(AAR_ERRORS_PATH, data)
                    except Exception:
                        pass
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

    # Load armor integrity data once for batch processing (avoids repeated file I/O)
    armor_batch = _load_armor_integrity()
    armor_batch_modified = False

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
            try:
                await _reply_aar_rejection(
                    msg, [f"Jump URL: {msg.jump_url}", "Parse failed: record is None"]
                )
            except Exception:
                pass
            to_react_err.append(msg)
            rejected += 1
            if scanned % 10 == 0:
                _print_progress("Ingest New AARs", scanned, scanned)
            continue
        aar_id = record.get("aar_id", msg.id)
        if has_been_processed(aar_id):
            existing = DATASTORE.get_record(aar_id)
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
            try:
                await _reply_aar_rejection(msg, [f"Jump URL: {msg.jump_url}"] + errors)
            except Exception:
                pass
            to_react_err.append(msg)
            rejected += 1
            if scanned % 10 == 0:
                _print_progress("Ingest New AARs", scanned, scanned)
            continue

        # --- Armor Integrity: Check penalties BEFORE saving ---
        guild = aar_channel.guild
        brother_ids = record.get("brother_ids", [])
        # Compute per-brother base points for armor tracking
        # For siege ops: points based on waves per brother
        # For other ops: use points_for_op (same for all brothers)
        difficulty_class = record.get("difficulty_class") or ""
        global_waves = record.get("waves") or 0
        brother_waves = record.get("brother_waves") or {}
        # Calculate base difficulty points directly (pre-penalty value for armor wear)
        base_difficulty_points = compute_points_for_op(difficulty_class, global_waves)
        base_points = {}
        if brother_ids:
            is_siege = difficulty_class in ("normal_siege", "hard_siege")
            for bid in brother_ids:
                if is_siege:
                    # Siege: compute per-brother from waves
                    waves_for_brother = brother_waves.get(bid)
                    if waves_for_brother is None:
                        waves_for_brother = global_waves
                    try:
                        waves_for_brother = int(waves_for_brother or 0)
                    except Exception:
                        waves_for_brother = 0
                    if difficulty_class == "normal_siege":
                        base_points[bid] = 3 * (waves_for_brother // 5)
                    else:
                        base_points[bid] = 4 * (waves_for_brother // 5)
                else:
                    # Non-siege: use base difficulty points (before penalties)
                    base_points[bid] = base_difficulty_points
        armor_penalties = {}

        if guild and brother_ids:
            for bid in brother_ids:
                try:
                    member = guild.get_member(int(bid))
                    if member:
                        tier = _get_member_damage_tier(member)
                        # Check for spirit fractured state
                        armor_state = await _get_armor_state(int(bid))
                        spirit_fractured = armor_state.get("spirit_fractured", False)
                        # Roll probabilistic penalty instead of fixed
                        penalty = _roll_armor_penalty(tier, spirit_fractured)
                        if penalty > 0:
                            armor_penalties[bid] = penalty
                except Exception:
                    pass

        # Store armor penalties in the record
        if armor_penalties:
            record["armor_penalties"] = armor_penalties

        await save_aar_record(record)

        # --- Armor Integrity: Run checks and post alerts AFTER saving ---
        alerts_to_post = []
        # Extract op context for debrief in alerts
        op_mission = record.get("mission")
        op_url = record.get("message_url")
        if guild and brother_ids:
            for bid in brother_ids:
                try:
                    bid_base_points = base_points.get(bid, 0)
                    bid_actual_penalty = armor_penalties.get(bid, 0)
                    penalty, alert_info = await _process_armor_integrity_for_aar(
                        bid,
                        bid_base_points,
                        guild,
                        armor_batch,
                        op_mission=op_mission,
                        op_difficulty_class=difficulty_class,
                        op_url=op_url,
                        squad_member_ids=brother_ids,
                        actual_penalty=bid_actual_penalty,
                    )
                    if alert_info:
                        alerts_to_post.append(alert_info)
                        armor_batch_modified = True
                except Exception:
                    pass
            # Mark batch as modified if any brother was processed
            if brother_ids:
                armor_batch_modified = True

        # Post any armor alerts (outside the loop to avoid rate limits)
        for alert in alerts_to_post:
            try:
                await _post_armor_alert(
                    alert["member"],
                    alert["tier"],
                    alert.get("critical_count", 0),
                    guild,
                    op_mission=alert.get("op_mission"),
                    op_difficulty_class=alert.get("op_difficulty_class"),
                    op_url=alert.get("op_url"),
                    squad_member_ids=alert.get("squad_member_ids"),
                    alert_type=alert.get("alert_type", "sustained"),
                    penalty_amount=alert.get("penalty_amount", 0),
                )
            except Exception as e:
                logger.error(f"Error calling _post_armor_alert: {e}")

        # If an error entry exists for this AAR/message, remove stored reply and clear the error
        try:
            data = _load_json_dict(AAR_ERRORS_PATH)
            sid = str(aar_id)
            if sid in data:
                reply_id = data.get(sid, {}).get("reply_id")
                if reply_id:
                    try:
                        reply_msg = await msg.channel.fetch_message(int(reply_id))
                        try:
                            await reply_msg.delete()
                        except Exception:
                            try:
                                logger.debug(
                                    f"Unable to delete reply {reply_id} for AAR {sid}"
                                )
                            except Exception:
                                pass
                    except Exception:
                        pass
                del data[sid]
                _save_json_dict(AAR_ERRORS_PATH, data)
        except Exception:
            pass
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

    # Save armor batch data once at end (avoid repeated file I/O during loop)
    if armor_batch_modified:
        await _save_armor_batch(armor_batch)
        # Increment AAR generation to invalidate scan caches
        await _increment_aar_generation()

    return ingested, rejected


# Admin-only command to print cache sizes, dirty flags, last flush time, and cache hit/miss counters
@bot.tree.command(
    name="cache_stats", description="Show DataStore cache and flush stats (admin only)"
)
async def cache_stats(interaction: discord.Interaction):
    if not (
        check_command_permission(interaction.user, "cache_stats")
        and is_allowed_channel(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    stats = DATASTORE.get_cache_stats()
    import datetime

    last_flush = stats["last_flush_time"]
    if last_flush:
        try:
            lf = datetime.fromtimestamp(last_flush, tz=timezone.utc)
            last_flush_str = lf.strftime("%Y-%m-%d %H:%M:%S %Z")
        except Exception:
            last_flush_str = datetime.datetime.utcfromtimestamp(last_flush).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )
    else:
        last_flush_str = "Never"
    # Format the user stats cache built timestamp into a single string
    try:
        ts = stats.get("user_stats_cache_built_ts")
        if ts:
            try:
                user_stats_built_str = datetime.datetime.fromtimestamp(
                    ts, tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M:%S %Z")
            except Exception:
                user_stats_built_str = datetime.datetime.utcfromtimestamp(ts).strftime(
                    "%Y-%m-%d %H:%M:%S UTC"
                )
        else:
            user_stats_built_str = "Never"
    except Exception:
        user_stats_built_str = "Never"

    msg = (
        f"```ansi\n"
        f"\u001b[32m==============================================================================\n"
        f"  WATCH FORTRESS JERICHO // SERVITOR CACHE DIAGNOSTICS\n"
        f"==============================================================================\n"
        f"  User Stats Cache Size:        {stats['user_stats_cache_size']}\n"
        f"  Combat Cache Size:            {stats.get('combat_cache_size', 0)}\n"
        f"  Combat Cache Spans:           {', '.join(stats.get('combat_cache_spans', [])) if stats.get('combat_cache_spans') else 'None'}\n"
        f"  Dirty AAR Records:            {stats['dirty_records']}\n"
        f"  Dirty Processed IDs:          {stats['dirty_ids']}\n"
        f"  Last Flush Time:              {last_flush_str}\n"
        f"  User Stats Cache Built:       {user_stats_built_str}\n"
        f"==============================================================================\n"
        f"\u001b[0m```"
    )
    await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(
    name="audit_service_studs",
    description="List brothers whose displayed service studs differ from computed entitlement (Watch Command only).",
)
async def audit_service_studs(interaction: discord.Interaction):
    await interaction.response.defer(thinking=False, ephemeral=True)

    if not (
        check_command_permission(interaction.user, "audit_service_studs")
        and is_allowed_channel(interaction)
    ):
        await interaction.followup.send("Access denied.", ephemeral=True)
        return

    guild = interaction.guild or _resolve_notification_guild()
    if not guild:
        await interaction.followup.send("Guild not available.", ephemeral=True)
        return

    idx_veteran = _role_index("Watch Veteran")
    now = datetime.utcnow()
    mismatches: list[tuple[discord.Member, int, int, str, str]] = []

    for member in getattr(guild, "members", []) or []:
        try:
            # Consider only users who have any canonical Watch rank/role
            member_role_names = _canonical_role_names(member)
            if not any(r in member_role_names for r in RANK_ROLES_PRIORITY):
                continue

            # Compute entitlement using same rules as roster/tally
            studs_count = 0
            highest_idx = get_highest_rank_index(member)
            if (
                (idx_veteran is not None)
                and (highest_idx is not None)
                and (highest_idx <= idx_veteran)
            ):
                joined_at = getattr(member, "joined_at", None)
                if joined_at:
                    ja = joined_at
                    if ja.tzinfo is not None:
                        try:
                            ja = ja.astimezone(timezone.utc).replace(tzinfo=None)
                        except Exception:
                            ja = ja.replace(tzinfo=None)
                    weeks = max(0, (now - ja).days // 7)
                    studs_time = weeks // 4
                else:
                    studs_time = 0

                stats = compute_stats_for_user(str(getattr(member, "id", "")))
                try:
                    aar_points_val = int(round(float(stats.get("aar_points", 0) or 0)))
                except Exception:
                    aar_points_val = 0
                studs_aar = aar_points_val // 400
                studs_count = min(studs_time, studs_aar)
                studs_count = min(studs_count, 16)

            # Extract existing pips shown in nickname/display name
            # New system: ●=4 (Auramite), ⚬=1 (Plasteel), max 16
            dn = str(member.nick or member.display_name or "")
            existing_aur = dn.count("●")
            existing_plas = dn.count("⚬")
            existing_total = existing_aur * 4 + existing_plas
            # Build actual pip string from display name (sorted: auramite first)
            existing_pips = "●" * existing_aur + "⚬" * existing_plas
            if not existing_pips and existing_total == 0:
                existing_pips = "—"
            expected_pips = _studs_pips(studs_count)

            # Compare expected pips (auramite-only post-4) against what's in
            # the display name. Any deviation is a mismatch.
            is_mismatch = existing_pips != expected_pips

            if is_mismatch:
                mismatches.append(
                    (member, studs_count, existing_total, expected_pips, existing_pips)
                )
        except Exception:
            continue

    if not mismatches:
        await interaction.followup.send(
            "No service-stud discrepancies found.", ephemeral=True
        )
        return

    # Build an ANSI-styled, column-aligned report (green text)
    mismatches.sort(key=lambda t: t[1] - t[2], reverse=True)

    # Prepare printable rows and compute column widths
    rows: list[tuple[str, str, str, str]] = []
    name_max = 4
    exp_max = len("Expected")
    cur_max = len("Current")
    action_max = len("Action")
    for mem, comp, disp, exp_pips, cur_pips in mismatches:
        diff = comp - disp
        action = (
            f"AWARD {diff}"
            if diff > 0
            else ("REFORMAT" if diff == 0 else f"REMOVE {abs(diff)}")
        )
        name = getattr(mem, "display_name", str(getattr(mem, "id", "")))
        rows.append((name, exp_pips, cur_pips, action))
        name_max = max(name_max, len(name))
        exp_max = max(exp_max, len(exp_pips))
        cur_max = max(cur_max, len(cur_pips))
        action_max = max(action_max, len(action))

    # Cap name width to avoid excessively wide blocks
    NAME_CAP = 36
    name_w = min(NAME_CAP, name_max)

    sep = "=" * (name_w + exp_max + cur_max + action_max + 10)

    lines: list[str] = []
    lines.append("```ansi")
    lines.append("\u001b[32m" + sep)
    lines.append("  WATCH FORTRESS JERICHO // SERVICE-STUDS AUDIT")
    lines.append(sep)
    # Build header using safe string methods to avoid nested format fields
    header = (
        "  "
        + "Brother".ljust(name_w)
        + "  "
        + "Expected".rjust(exp_max)
        + "  "
        + "Current".rjust(cur_max)
        + "  "
        + "Action".rjust(action_max)
    )
    lines.append(header)
    lines.append(sep)
    for name, exp_pips, cur_pips, action in rows:
        # Truncate name if necessary
        display_name = name if len(name) <= name_w else name[: name_w - 1] + "…"
        line = (
            "  "
            + display_name.ljust(name_w)
            + "  "
            + exp_pips.rjust(exp_max)
            + "  "
            + cur_pips.rjust(cur_max)
            + "  "
            + action.rjust(action_max)
        )
        lines.append(line)
    lines.append(sep)
    lines.append("\u001b[0m```")

    report = "\n".join(lines)

    # Build mobile-friendly embed
    embed = discord.Embed(
        title="Service-Studs Audit",
        description=f"Found {len(mismatches)} discrepancies",
        color=0x2ECC71,
    )

    # Add up to 10 mismatches to embed fields
    awards_needed = [
        (name, exp_pips, cur_pips, action)
        for name, exp_pips, cur_pips, action in rows
        if "AWARD" in action
    ]
    removals_needed = [
        (name, exp_pips, cur_pips, action)
        for name, exp_pips, cur_pips, action in rows
        if "REMOVE" in action
    ]
    reformat_needed = [
        (name, exp_pips, cur_pips, action)
        for name, exp_pips, cur_pips, action in rows
        if action == "REFORMAT"
    ]

    if awards_needed:
        award_text = "\n".join(
            f"• {name}: {action}" for name, _, _, action in awards_needed[:8]
        )
        if len(awards_needed) > 8:
            award_text += f"\n... and {len(awards_needed) - 8} more"
        embed.add_field(
            name=f"Need Awards ({len(awards_needed)})", value=award_text, inline=False
        )

    if removals_needed:
        remove_text = "\n".join(
            f"• {name}: {action}" for name, _, _, action in removals_needed[:8]
        )
        if len(removals_needed) > 8:
            remove_text += f"\n... and {len(removals_needed) - 8} more"
        embed.add_field(
            name=f"Need Removal ({len(removals_needed)})",
            value=remove_text,
            inline=False,
        )

    if reformat_needed:
        reformat_text = "\n".join(
            f"• {name}: {cur_pips} → {exp_pips}"
            for name, exp_pips, cur_pips, _ in reformat_needed[:8]
        )
        if len(reformat_needed) > 8:
            reformat_text += f"\n... and {len(reformat_needed) - 8} more"
        embed.add_field(
            name=f"Need Reformat ({len(reformat_needed)})",
            value=reformat_text,
            inline=False,
        )

    embed.set_footer(text="Use PC/Console button for detailed ANSI table")

    # Send with toggle view
    if len(report) <= 1900:
        view = ToggleFormatView(text_content=report, embed=embed, default="embed")
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        # Report too long for toggle, send embed only
        await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(
    name="librarian_audit",
    description="Check Black Laurels role discrepancies (Watch Command only).",
)
async def librarian_audit(interaction: discord.Interaction):
    """Check for discrepancies in Black Laurels role assignment.

    A member of rank Watch Brother+ should have the Black Laurels role IFF they are in
    an AAR with the @Black_Laurels mention on each of the required maps at least once.
    """
    if not (
        check_command_permission(interaction.user, "librarian_audit")
        and is_allowed_channel(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True, ephemeral=True)

    guild = interaction.guild or _resolve_notification_guild()
    if not guild:
        await interaction.followup.send("Guild not available.", ephemeral=True)
        return

    if DATASTORE is None:
        await interaction.followup.send("DATASTORE not available.", ephemeral=True)
        return

    # Get the Black Laurels role
    black_laurels_role = discord.utils.get(guild.roles, name="Black Laurels")
    if not black_laurels_role:
        await interaction.followup.send(
            "Black Laurels role not found in guild.", ephemeral=True
        )
        return

    # Build a map of user_id -> set of completed Black Laurels missions
    user_bl_missions: Dict[str, set] = {}

    for rec in DATASTORE.iter_records():
        mission = rec.get("mission") or ""
        # Black Laurels is indicated by the role mention in mission field
        has_black_laurels = "<@&1440108298115485716>" in mission

        if not has_black_laurels:
            continue

        # Strip any Discord role mentions (e.g., "<@&1440108298115485716>") from mission name
        mission_clean = re.sub(r"<@&\d+>", "", mission).strip().lower()
        # Only track required missions
        if mission_clean not in BLACK_LAURELS_REQUIRED_MISSIONS:
            continue

        # For black laurels, missions must have exactly 3 members to be valid
        brother_ids = rec.get("brother_ids") or []
        if len(brother_ids) != 3:
            continue

        # Add this mission to each brother's completed set
        for uid in brother_ids:
            uid_str = str(uid)
            if uid_str not in user_bl_missions:
                user_bl_missions[uid_str] = set()
            user_bl_missions[uid_str].add(mission_clean)

    # Check each member for discrepancies
    missing_role: List[Tuple[discord.Member, set]] = []  # Should have role but doesn't
    needs_new_missions: List[
        Tuple[discord.Member, set, set]
    ] = []  # Has role but missing new missions

    # Missions added after grandfathering (must be explicitly completed by existing role holders)
    new_missions = (
        BLACK_LAURELS_REQUIRED_MISSIONS - BLACK_LAURELS_GRANDFATHERED_MISSIONS
    )

    for member in getattr(guild, "members", []) or []:
        if member.bot:
            continue

        try:
            # Check if member is Watch Brother+ (has any rank role)
            member_role_names = _canonical_role_names(member)
            if not any(r in member_role_names for r in RANK_ROLES_PRIORITY):
                continue

            user_id = str(member.id)
            completed = user_bl_missions.get(user_id, set())
            has_role = black_laurels_role in member.roles
            should_have_role = (
                len(completed) >= len(BLACK_LAURELS_REQUIRED_MISSIONS)
                and completed >= BLACK_LAURELS_REQUIRED_MISSIONS
            )

            if should_have_role and not has_role:
                missing_role.append((member, completed))
            elif has_role:
                # Grandfathered role holders - check if they're missing any NEW missions
                missing_new = new_missions - completed
                if missing_new:
                    needs_new_missions.append((member, completed, missing_new))

        except Exception:
            continue

    if not missing_role and not needs_new_missions:
        await interaction.followup.send(
            "No Black Laurels discrepancies found.", ephemeral=True
        )
        return

    # Build report
    lines: List[str] = []
    lines.append("```ansi")
    lines.append(
        "\u001b[32m=============================================================================="
    )
    lines.append("  WATCH FORTRESS JERICHO // LIBRARIUM AUDIT")
    lines.append("  BLACK LAURELS DISCREPANCY REPORT")
    lines.append(
        "=============================================================================="
    )

    if missing_role:
        lines.append("")
        lines.append(f"  ELIGIBLE BUT MISSING ROLE ({len(missing_role)}):")
        lines.append("  " + "-" * 72)
        for member, completed in missing_role:
            name = getattr(member, "display_name", str(member.id))
            lines.append(f"    ✓ {name}")
            lines.append(
                f"      Completed: {len(completed)}/{len(BLACK_LAURELS_REQUIRED_MISSIONS)} required missions"
            )

    if needs_new_missions:
        lines.append("")
        lines.append(f"  HAS ROLE BUT NEEDS NEW MISSIONS ({len(needs_new_missions)}):")
        lines.append("  " + "-" * 72)
        for member, completed, missing in needs_new_missions:
            name = getattr(member, "display_name", str(member.id))
            missing_list = ", ".join(sorted(m.title() for m in missing))
            lines.append(f"    ⚠ {name}")
            lines.append(f"      Missing: {missing_list}")

    lines.append("")
    lines.append(
        "=============================================================================="
    )
    lines.append("\u001b[0m```")

    report = "\n".join(lines)

    # Build mobile-friendly embed
    embed = discord.Embed(
        title="Librarium Audit — Black Laurels",
        color=0x2ECC71,
    )

    if missing_role:
        missing_text = "\n".join(
            f"✓ {getattr(m, 'display_name', str(m.id))}" for m, _ in missing_role[:10]
        )
        if len(missing_role) > 10:
            missing_text += f"\n... and {len(missing_role) - 10} more"
        embed.add_field(
            name=f"Eligible but Missing Role ({len(missing_role)})",
            value=missing_text,
            inline=False,
        )

    if needs_new_missions:
        needs_text = ""
        for member, _, missing in needs_new_missions[:8]:
            name = getattr(member, "display_name", str(member.id))
            missing_list = ", ".join(sorted(m.title() for m in missing))
            needs_text += f"⚠ {name}\n  Missing: {missing_list}\n"
        if len(needs_new_missions) > 8:
            needs_text += f"... and {len(needs_new_missions) - 8} more"
        embed.add_field(
            name=f"Needs New Missions ({len(needs_new_missions)})",
            value=needs_text.strip(),
            inline=False,
        )

    if not missing_role and not needs_new_missions:
        embed.description = "No discrepancies found"

    embed.set_footer(text="Use PC/Console button for detailed ANSI view")

    # Handle long reports
    if len(report) <= 1900:
        view = ToggleFormatView(text_content=report, embed=embed, default="embed")
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        # Report too long for toggle, send embed with file attachment
        import io

        fp = io.BytesIO(report.encode("utf-8"))
        fp.seek(0)
        try:
            await interaction.followup.send(
                embed=embed,
                file=discord.File(fp, filename="librarian_audit.txt"),
                ephemeral=True,
            )
        finally:
            try:
                fp.close()
            except Exception:
                pass


@bot.tree.command(
    name="reparse_records",
    description="Re-parse stored AAR records from their message_url and update records (admin).",
)
@app_commands.describe(
    limit="Optional: max number of records to reparse.",
    days="Optional: only reparse records from the last N days.",
)
async def reparse_records(
    interaction: discord.Interaction,
    limit: int | None = None,
    days: int | None = None,
):
    if not (
        check_command_permission(interaction.user, "reparse_records")
        and is_allowed_channel(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    if RECONCILE_LOCK.locked():
        logger.info(
            f"reparse_records blocked: lock held (user={interaction.user.id})"
        )
        await interaction.response.send_message(
            "Another reconciliation is in progress. Please try again shortly.",
            ephemeral=True,
        )
        return
    await interaction.response.defer(thinking=True, ephemeral=True)

    logger.info(f"reparse_records: acquiring lock (user={interaction.user.id})")
    async with RECONCILE_LOCK:
        logger.info(f"reparse_records: lock acquired (user={interaction.user.id})")
        total = 0
        updated = 0
        failed = 0
        changes_by_field: dict[str, int] = {}  # Track which fields changed
        # Snapshot of records to process (respect optional limit and days filter)
        now_utc = datetime.now(timezone.utc)
        if days is not None:
            if days <= 0:
                await interaction.followup.send(
                    "`days` must be a positive integer when specified.",
                    ephemeral=True,
                )
                return
            cutoff = now_utc - timedelta(days=days)
        else:
            cutoff = None

        def _in_window(rec: dict) -> bool:
            if cutoff is None:
                return True
            ts_str = rec.get("timestamp")
            if not ts_str:
                return False
            try:
                ts = _parse_iso8601_to_utc(ts_str)
                return ts is not None and ts >= cutoff
            except Exception:
                return False

        records_list = [(k, v) for k, v in DATASTORE._records.items() if _in_window(v)]
        if limit is not None and limit > 0:
            records_list = records_list[:limit]
        total_records = len(records_list)

        def _print_progress(done: int, total: int) -> None:
            if not sys.stdout.isatty():
                return
            bar_len = 40
            filled = int(round(bar_len * done / float(total))) if total else bar_len
            perc = (done / total * 100) if total else 100.0
            bar = "#" * filled + "-" * (bar_len - filled)
            sys.stdout.write(
                f"\rReparsing records: [{bar}] {done}/{total} ({perc:5.1f}%)"
            )
            sys.stdout.flush()

        # Iterate snapshot of records
        for idx, (key, rec) in enumerate(records_list, start=1):
            _print_progress(idx - 1, total_records)
            total += 1
            msg_url = rec.get("message_url")
            if not msg_url:
                _print_progress(idx, total_records)
                continue
            try:
                parts = msg_url.rstrip("/").split("/")
                # Expect .../channels/<channel_id>/<message_id> or .../<channel_id>/<message_id>
                if len(parts) < 2:
                    raise ValueError("invalid message_url")
                message_id = int(parts[-1])
                channel_id = int(parts[-2])
                channel = bot.get_channel(channel_id)
                if channel is None:
                    try:
                        channel = await bot.fetch_channel(channel_id)
                    except Exception:
                        channel = None
                if channel is None:
                    raise RuntimeError(f"channel {channel_id} not available")
                msg = await channel.fetch_message(message_id)
                new_rec = parse_aar(msg)
                if not new_rec:
                    continue
                # Preserve some metadata from existing record (timestamp/edited_at/message_url)
                merged = rec.copy()
                merged.update(new_rec)
                # Ensure aar_id remains the same key
                merged["aar_id"] = rec.get("aar_id")
                if merged != rec:
                    # Track which fields changed
                    for field in set(rec.keys()) | set(merged.keys()):
                        if rec.get(field) != merged.get(field):
                            changes_by_field[field] = changes_by_field.get(field, 0) + 1
                    await DATASTORE.set_record(str(merged.get("aar_id")), merged)
                    updated += 1
            except Exception:
                failed += 1

        # Finalize progress output in terminal
        _print_progress(total_records, total_records)
        if sys.stdout.isatty():
            sys.stdout.write("\n")
            sys.stdout.flush()

        days_info = f" (last {days} days)" if days else ""
        # Build changes summary
        if changes_by_field:
            sorted_changes = sorted(changes_by_field.items(), key=lambda x: -x[1])
            changes_summary = ", ".join(f"{k}={v}" for k, v in sorted_changes)
            changes_line = f"\nFields updated: {changes_summary}"
        else:
            changes_line = ""
        await interaction.followup.send(
            f"Reparse complete{days_info}: processed={total}, updated={updated}, failed={failed}{changes_line}",
            ephemeral=True,
        )


async def _forum_post_autocomplete(
    interaction: discord.Interaction, current: str
) -> List[app_commands.Choice[str]]:
    """Autocomplete for forum posts (threads within forum channels)."""
    choices = []
    seen_ids: set[int] = set()
    if not interaction.guild:
        return choices

    current_lower = current.lower()

    def add_thread(thread, parent):
        """Add thread to choices if it matches and not already seen."""
        if thread.id in seen_ids:
            return
        seen_ids.add(thread.id)
        if not current or current_lower in thread.name.lower():
            display = f"{thread.name} ({parent.name})"
            if len(display) > 100:
                display = display[:97] + "..."
            choices.append(app_commands.Choice(name=display, value=str(thread.id)))

    try:
        # Fetch all active threads in the guild
        active_threads = await interaction.guild.active_threads()
        for thread in active_threads:
            parent = thread.parent
            if isinstance(parent, discord.ForumChannel):
                add_thread(thread, parent)
                if len(choices) >= 25:
                    return choices
    except Exception:
        pass

    # Also check archived threads in forum channels (catches new/quiet posts)
    try:
        for channel in interaction.guild.channels:
            if isinstance(channel, discord.ForumChannel):
                try:
                    async for thread in channel.archived_threads(limit=50):
                        add_thread(thread, channel)
                        if len(choices) >= 25:
                            return choices
                except Exception:
                    pass
    except Exception:
        pass

    return choices


@bot.tree.command(
    name="tally_deeds", description="Display the Deeds Ledger for a Brother."
)
@app_commands.describe(
    brother="The Watch Brother to query.",
    killteam="Role: tally every member of this kill team (mutually exclusive with brother)",
    send_to="Forum post to send results to (non-ephemeral). If omitted, sends privately to you.",
)
@app_commands.autocomplete(send_to=_forum_post_autocomplete)
async def tally_deeds(
    interaction: discord.Interaction,
    brother: Optional[discord.Member] = None,
    killteam: Optional[discord.Role] = None,
    send_to: Optional[str] = None,
):
    # Resolve send_to string to an actual channel/thread
    send_to_channel = None
    if send_to is not None:
        # Try to parse as channel ID
        try:
            channel_id = int(send_to.strip())
            send_to_channel = interaction.guild.get_channel_or_thread(channel_id)
            if send_to_channel is None:
                send_to_channel = await bot.fetch_channel(channel_id)
        except ValueError:
            # Not an ID, try to find by name across forum threads
            name_lower = send_to.lower()
            for channel in interaction.guild.channels:
                if isinstance(channel, discord.ForumChannel):
                    # Check cached threads first
                    for thread in channel.threads:
                        if thread.name.lower() == name_lower:
                            send_to_channel = thread
                            break
                    if send_to_channel:
                        break
                    # Check archived threads if not found
                    try:
                        async for thread in channel.archived_threads(limit=100):
                            if thread.name.lower() == name_lower:
                                send_to_channel = thread
                                break
                        if send_to_channel:
                            break
                    except Exception:
                        pass
        except Exception:
            pass

        if send_to_channel is None:
            await interaction.response.send_message(
                f"Could not find forum post '{send_to}'. Check the post exists and the bot can see it.",
                ephemeral=True,
            )
            return

        # Validate it's messageable
        if not isinstance(
            send_to_channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)
        ):
            await interaction.response.send_message(
                "send_to must be a thread or forum post — not a forum channel itself.",
                ephemeral=True,
            )
            return

    # Permission check: requires Watch Command role and allowed channel
    if not (
        check_command_permission(interaction.user, "tally_deeds")
        and is_allowed_channel(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    # Mutual exclusivity check BEFORE deferring - must provide one or the other, not both
    if brother and killteam:
        await interaction.response.send_message(
            "Provide either 'brother' or 'killteam', not both.", ephemeral=True
        )
        return

    # First response: defer, so we can do slower work safely
    await interaction.response.defer(thinking=False, ephemeral=True)

    if killteam:
        members = [m for m in getattr(killteam, "members", [])]
        # If the provided role is one of the canonical rank roles, restrict
        # the roster to members who have that rank and do NOT hold any
        # higher-ranked role. Higher rank == lower index in
        # RANK_ROLES_PRIORITY.
        try:
            role_name = getattr(killteam, "name", "") or ""
            role_idx = _role_index(role_name)
        except Exception:
            role_idx = None

        if role_idx is not None:
            filtered: List[discord.Member] = []
            for m in members:
                try:
                    # Collect indices of all rank roles this member has
                    member_rank_indices = [
                        _role_index(getattr(r, "name", ""))
                        for r in getattr(m, "roles", [])
                    ]
                    # Must explicitly have the passed role
                    has_target_role = any(
                        getattr(r, "name", "") == role_name
                        for r in getattr(m, "roles", [])
                    )
                    if not has_target_role:
                        continue
                    # Exclude if member has any higher-ranked role (index < role_idx)
                    higher = [
                        i for i in member_rank_indices if i is not None and i < role_idx
                    ]
                    if higher:
                        continue
                    filtered.append(m)
                except Exception:
                    continue
            members = filtered

        else:
            # Specialist roles: include both the specialist and their leader(s).
            # Map specialist role lower-case -> leader canonical name
            try:
                spec_map = {
                    "watch techmarine": "Forgemaster",
                    "watch librarian": "Void Warden",
                    "watch apothecary": "Chief Apothecary",
                    "watch chaplain": "High Chaplain",
                    # Champions: include champion members plus their head
                    "kill team champion": "Lord Executioner",
                    "company champion": "Lord Executioner",
                    # Lord Executioner is a head role; mapping to itself is unnecessary
                }
                rn = (getattr(killteam, "name", "") or "").strip().lower()
                leader = spec_map.get(rn)
            except Exception:
                leader = None

            if leader:
                filtered: List[discord.Member] = []
                for m in members:
                    try:
                        names = {
                            getattr(r, "name", "") for r in getattr(m, "roles", [])
                        }
                        if (getattr(killteam, "name", "") in names) or (
                            leader in names
                        ):
                            filtered.append(m)
                    except Exception:
                        continue
                members = filtered

        if not members:
            await interaction.followup.send(
                f"Killteam role '{getattr(killteam, 'name', '')}' has no members.",
                ephemeral=True,
            )
            return
    elif brother:
        members = [brother]
    else:
        await interaction.followup.send(
            "Specify a brother or a killteam role.", ephemeral=True
        )
        return

    # We'll build one aggregated reply containing a block for each member
    member_blocks: list[str] = []
    # Compact roster rows (structured) for under-2k summary
    roster_items: List[Dict[str, int | str]] = []
    # Keep the per-member stat rows (label/value pairs) for mobile embed rendering
    member_stat_rows_list: List[List[Tuple[str, str]]] = []
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
            if joined_at:
                try:
                    # Ensure joined_at is timezone-aware, defaulting to UTC
                    if joined_at.tzinfo is None:
                        joined_at = joined_at.replace(tzinfo=timezone.utc)
                    ja_utc = joined_at.astimezone(timezone.utc)
                    days_since_join = (datetime.now(timezone.utc) - ja_utc).days
                    joined_str = f"{ja_utc.strftime('%Y-%m-%d %H:%M %Z')} ({days_since_join}d ago)"
                except Exception:
                    joined_str = joined_at.strftime("%Y-%m-%d %H:%M UTC")
            else:
                joined_str = "Unknown"
        except Exception:
            joined_str = "Unknown"

        # Compute Service Studs: one stud per 4 weeks AND 400 AAR points (conjunctive).
        # Only compute for members of rank Watch Veteran or higher; otherwise 0.
        MAX_STUDS = 16
        try:
            studs_count = 0
            idx_veteran = _role_index("Watch Veteran")
            highest_idx = get_highest_rank_index(target)
            # Only compute if the user has a recognized rank at or above Watch Veteran
            if (
                (idx_veteran is not None)
                and (highest_idx is not None)
                and (highest_idx <= idx_veteran)
            ):
                # Time-based studs
                if joined_at:
                    now = datetime.utcnow()
                    ja = joined_at
                    if ja.tzinfo is not None:
                        try:
                            ja = ja.astimezone(timezone.utc).replace(tzinfo=None)
                        except Exception:
                            ja = ja.replace(tzinfo=None)
                    weeks = max(0, (now - ja).days // 7)
                    studs_time = weeks // 4
                else:
                    studs_time = 0
                # AAR-based studs
                try:
                    aar_points_val = int(round(float(stats.get("aar_points", 0) or 0)))
                except Exception:
                    aar_points_val = 0
                studs_aar = aar_points_val // 400
                studs_count = min(studs_time, studs_aar, MAX_STUDS)
            else:
                studs_count = 0
        except Exception:
            studs_count = 0
        # Cap at 16 studs (4 Auramite) — the max tier
        studs_count = min(studs_count, 16)

        # Build display string using two-tier Unicode symbols:
        # - lowest: hollow circle '⚬' (Plasteel)
        # - top: filled circle '●' per four (Auramite), max 4 auramite
        # Append a type breakdown in parentheses using in-universe names.
        try:
            studs_symbols = ""
            if not studs_count:
                studs_display = "— (0 Plasteel)"
            else:
                # Breakdown into Auramite (4), Plasteel (1), max 16 total
                auramite_count = studs_count // 4
                plasteel_count = studs_count % 4

                # Pip symbols use auramite-only display post-4 (via shared helper)
                studs_symbols = _studs_pips(studs_count)

                # Once in auramite tier, only show Auramite count (ignore plasteel)
                if auramite_count:
                    types_str = f"{auramite_count} Auramite"
                else:
                    types_str = f"{plasteel_count} Plasteel" if plasteel_count else "0 Plasteel"
                studs_display = f"{studs_symbols} ({types_str})"

                # Compare with studs already present in the display name and add
                # an in-universe notification if there's a mismatch.
                try:
                    dn = str(display_name or "")
                    existing_aur = dn.count("●")
                    existing_plas = dn.count("⚬")
                    existing_total = existing_aur * 4 + existing_plas
                    diff = studs_count - existing_total

                    # Check if plasteel studs need upgrading to auramite (4 plasteel = 1 auramite)
                    upgrade_needed = existing_plas >= 4
                    if diff > 0:
                        # Loreful addendum when computed studs exceed what's shown
                        # Break down owed studs into auramite (4) and plasteel (1)
                        # Once in auramite tier (4+ studs), no longer track plasteel —
                        # only show auramite owed (partial progress not displayed)
                        in_auramite_tier = studs_count >= 4
                        owed_aur = diff // 4
                        owed_plas = diff % 4
                        owed_parts = []
                        if owed_aur > 0:
                            owed_parts.append(f"+{owed_aur} Auramite")
                        # Only show plasteel owed if user hasn't reached auramite tier yet
                        if owed_plas > 0 and not in_auramite_tier:
                            owed_parts.append(f"+{owed_plas} Plasteel")
                        if owed_parts:
                            notif = f"({', '.join(owed_parts)} owed)"
                            studs_display = f"{studs_display} {notif}"
                        # If in auramite tier and only partial plasteel owed, don't show anything
                    elif diff < 0:
                        # Note if the name shows more studs than computed
                        notif = f"({abs(diff)} excess stud(s) displayed)"
                        studs_display = f"{studs_display} {notif}"
                    elif upgrade_needed:
                        # No diff but plasteel needs upgrading to auramite
                        upgrade_aur = existing_plas // 4
                        remaining_plas = existing_plas % 4
                        notif = f"(upgrade: {existing_plas}⚬ → {upgrade_aur}● + {remaining_plas}⚬)"
                        if remaining_plas == 0:
                            notif = f"(upgrade: {existing_plas}⚬ → {upgrade_aur}●)"
                        studs_display = f"{studs_display} {notif}"
                except Exception:
                    pass
        except Exception:
            studs_display = str(studs_count)
            studs_symbols = ""

        # Use in-memory records from DATASTORE
        trials_reported = _count_inductions_from_records(
            str(target.id), DATASTORE.iter_records()
        )

        # Home chapter from resolved map (fallback: REDACTED)
        home_chapter = chapters_map.get(str(target.id)) if chapters_map else "REDACTED"

        # Determine Active/Inactive status: Active if any AAR in last 30 days.

        try:
            # Use cached last_aar_ts from user_stats_cache to avoid O(N) record scan
            cached_ts = DATASTORE.get_user_stats(str(target.id)).get("last_aar_ts")
            status = "Inactive"
            last_aar_date: Optional[datetime] = None
            days_since_aar: Optional[int] = None
            if cached_ts:
                try:
                    last_aar_date = datetime.fromisoformat(cached_ts)
                except Exception:
                    last_aar_date = None
                if last_aar_date is not None:
                    if last_aar_date.tzinfo is not None:
                        try:
                            last_aar_date = last_aar_date.astimezone(timezone.utc).replace(tzinfo=None)
                        except Exception:
                            last_aar_date = last_aar_date.replace(tzinfo=None)
                    now = datetime.utcnow()
                    days_since_aar = (now - last_aar_date).days
                    cutoff = now - timedelta(days=28)
                    if last_aar_date >= cutoff:
                        status = "Active"
        except Exception:
            status = "Inactive"
            last_aar_date = None
            days_since_aar = None

        # Determine Company and Kill Team visibility and values per rank/command rules
        show_company = False
        show_killteam = False
        # Default company: "Reserves" if inactive, "Unknown" otherwise
        company = "Reserves" if status == "Inactive" else "Unknown"
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

            # Show Kill Team only for Sergeant and below (Sergeant, Kill Team Champion, Watch Veteran, Watch Brother/Sister)
            allowed_ranks = {
                "Watch Sergeant",
                "Kill Team Champion",
                "Watch Veteran",
                "Watch Brother",
                "Watch Sister",
            }
            show_killteam = any(r in role_names for r in allowed_ranks)

            # Resolve Kill Team role name (exclude rank-style 'Kill Team Champion')
            try:
                for role in roles:
                    rn = getattr(role, "name", "") or ""
                    rn_l = rn.lower()
                    if ("kill" in rn_l and "team" in rn_l) and ("champion" not in rn_l):
                        kt_name = _extract_killteam_name(rn)
                        break
            except Exception:
                pass
        except Exception:
            pass

        # Column-aligned stats
        # Format last AAR display
        if last_aar_date is not None and days_since_aar is not None:
            try:
                if last_aar_date.tzinfo is None:
                    last_aar_date = last_aar_date.replace(tzinfo=timezone.utc)
                aar_utc = last_aar_date.astimezone(timezone.utc)
                aar_date_str = aar_utc.strftime("%Y-%m-%d")
            except Exception:
                aar_date_str = last_aar_date.strftime("%Y-%m-%d")
            last_aar_display = f"{aar_date_str} ({days_since_aar}d ago)"
        else:
            last_aar_display = "None on record"

        stat_rows = [
            ("Status", status),
            ("Last AAR", last_aar_display),
            ("Induction", joined_str),
            ("Service Studs", studs_display),
        ]
        # Always include Home Chapter for single-brother queries (not a kill team request)
        try:
            if (killteam is None) and (len(members) == 1):
                stat_rows.append(("Home Chapter", home_chapter))
        except Exception:
            pass
        if show_company:
            stat_rows.append(("Company", company))
        # Show Kill Team strictly per visibility rule and only if resolved (avoid 'Unknown')
        if show_killteam and (kt_name and kt_name != "Unknown"):
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
        # Keep a structured copy for building a mobile-friendly embed later
        try:
            member_stat_rows_list.append(list(stat_rows))
        except Exception:
            member_stat_rows_list.append([])
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

        # Build compact roster row (safe casts and fallbacks)
        try:
            aar_val = int(round(float(stats.get("aar_points", 0) or 0)))
        except Exception:
            aar_val = 0
        try:
            gene_val = int(round(float(stats.get("gene_seed_points", 0) or 0)))
        except Exception:
            gene_val = 0
        try:
            armory_val = int(round(float(stats.get("armory_points", 0) or 0)))
        except Exception:
            armory_val = 0
        # Sanitize name: strip any stud glyphs from nicknames so pre-existing
        # symbols don't duplicate the computed studs in roster output.
        try:
            name_raw = str(display_name or getattr(target, "display_name", "Unknown"))
            name_val = re.sub(r"[●⚬]+", "", name_raw).strip()
            if not name_val:
                name_val = name_raw
        except Exception:
            name_val = str(display_name or getattr(target, "display_name", "Unknown"))
        status_val = str(status or "Unknown")
        roster_items.append(
            {
                "name": name_val,
                "member_id": str(target.id),
                "status": status_val,
                "aar": aar_val,
                "gene": gene_val,
                "armory": armory_val,
                "studs_symbols": studs_symbols,
                "studs_count": studs_count,
                "role_names": list(_canonical_role_names(target)),
                "home_chapter": home_chapter,
                # Rank bucket for roster sorting: Sergeant (0), Kill Team Champion (1), Veteran (2), Brother/Sister (3), Other (9)
                "rank_bucket": (
                    0
                    if ("Watch Sergeant" in _canonical_role_names(target))
                    else 1
                    if ("Kill Team Champion" in _canonical_role_names(target))
                    else 2
                    if ("Watch Veteran" in _canonical_role_names(target))
                    else 3
                    if (
                        ("Watch Brother" in _canonical_role_names(target))
                        or ("Watch Sister" in _canonical_role_names(target))
                    )
                    else 9
                ),
            }
        )

    # Send one aggregated followup containing a block per member
    reply_text = "\n\n".join(member_blocks)

    # If killteam requested, prepare a short summary (under 2000 chars)
    if killteam:
        # Build compact ANSI-styled roster (enforce ~1900 char length)
        try:
            MAX_LEN = 1900
            r_lines: list[str] = []
            r_lines.append("```ansi")
            r_lines.append(
                "\u001b[32m=============================================================================="
            )
            r_lines.append("  WATCH FORTRESS JERICHO // SERVICE-RECORD NODE")
            r_lines.append("  KILL TEAM DEEDS ROSTER")
            r_lines.append(
                "=============================================================================="
            )

            # Sort roster so Active members appear first, then by precise rank priority,
            # then by service studs (desc), then by AAR (desc), then name.
            def _rank_priority(role_names_list):
                try:
                    names = {r for r in (role_names_list or [])}
                except Exception:
                    names = set()
                # Explicit priority mapping (lower is higher priority)
                if "Watch Master" in names:
                    return 0
                if "Lord Executioner" in names:
                    return 1
                # High-command specialists
                high_specs = {
                    "Forgemaster",
                    "Chief Apothecary",
                    "Void Warden",
                    "High Chaplain",
                }
                if any(r in names for r in high_specs):
                    return 2
                if "Watch Captain" in names:
                    return 3
                if "Watch Lieutenant" in names:
                    return 4
                if "Company Champion" in names:
                    return 5
                # Company specialists
                comp_specs = {
                    "Watch Techmarine",
                    "Watch Apothecary",
                    "Watch Librarian",
                    "Watch Chaplain",
                }
                if any(r in names for r in comp_specs):
                    return 6
                if "Watch Sergeant" in names:
                    return 7
                if "Kill Team Champion" in names:
                    return 8
                if "Watch Veteran" in names:
                    return 9
                if "Watch Brother" in names or "Watch Sister" in names:
                    return 10
                return 99

            def _sort_key(it):
                try:
                    status_flag = (
                        0 if str(it.get("status", "")).lower() == "active" else 1
                    )
                    rank_pri = _rank_priority(it.get("role_names", []))
                    studs = int(it.get("studs_count", 0) or 0)
                    aar = int(it.get("aar", 0) or 0)
                    name = str(it.get("name", "")).lower()
                    return (status_flag, rank_pri, -studs, -aar, name)
                except Exception:
                    return (1, 99, 0, 0, "")

            sorted_items = sorted(roster_items, key=_sort_key)

            # Compute column widths for aligned rendering
            def _len_str(v):
                try:
                    return len(str(v))
                except Exception:
                    return 0

            # Reserve space for studs symbols so they always display; truncate names before studs
            def _pure_name_len(it):
                try:
                    return len(str(it.get("name", "") or ""))
                except Exception:
                    return 0

            def _studs_len(it):
                try:
                    return len(str(it.get("studs_symbols", "") or ""))
                except Exception:
                    return 0

            max_name_raw = max((_pure_name_len(it) for it in sorted_items), default=1)
            max_studs = max((_studs_len(it) for it in sorted_items), default=0)
            # Leading space before studs when present
            studs_reserved = (1 + max_studs) if max_studs > 0 else 0
            # Cap total name+studs width to keep table tidy
            TOTAL_NAME_CAP = 24
            name_w = max(1, min(max_name_raw, TOTAL_NAME_CAP - studs_reserved))
            status_w = max(
                (_len_str(it.get("status", "")) for it in sorted_items), default=1
            )
            # Cap widths to keep table tidy and avoid overflow from long names
            name_w = min(name_w, 24)
            status_w = min(status_w, 12)
            aar_w = max((_len_str(it.get("aar", 0)) for it in sorted_items), default=1)
            gene_w = max(
                (_len_str(it.get("gene", 0)) for it in sorted_items), default=1
            )
            armory_w = max(
                (_len_str(it.get("armory", 0)) for it in sorted_items), default=1
            )
            # Build formatted rows with alignment
            formatted_rows: List[str] = []
            for it in sorted_items:
                try:
                    nm = str(it.get("name", "") or "")
                    studs = str(it.get("studs_symbols", "") or "")
                    # Truncate name to leave room for studs; always show studs in reserved area
                    truncated = nm[:name_w]
                    if studs:
                        # ensure a single space before studs
                        studs_field = f" {studs}"
                        # pad studs field to reserved width so alignment holds
                        studs_field = f"{studs_field:<{studs_reserved}}"
                    else:
                        studs_field = "".ljust(studs_reserved)

                    name_field = f"{truncated:<{name_w}}"
                    st = str(it.get("status", ""))[:status_w]
                    line = (
                        f"{name_field}{studs_field} :: "
                        f"{st:<{status_w}} | "
                        f"AAR {int(it.get('aar', 0)):>{aar_w}} | "
                        f"Gene {int(it.get('gene', 0)):>{gene_w}} | "
                        f"Armory {int(it.get('armory', 0)):>{armory_w}}"
                    )
                except Exception:
                    line = f"{nm} :: {st}"
                formatted_rows.append(line)

            # Footer reserved to keep block markers valid
            footer_lines = [
                "==============================================================================",
                "\u001b[0m```",
            ]
            footer_len = sum(len(fl) + 1 for fl in footer_lines)
            # Current header length
            curr_len = sum(len(s) + 1 for s in r_lines)
            included: list[str] = []
            for row in formatted_rows:
                projected = curr_len + (len(row) + 1) + footer_len
                if projected <= MAX_LEN:
                    included.append(row)
                    curr_len += len(row) + 1
                else:
                    break
            omitted = max(len(formatted_rows) - len(included), 0)
            ending_line = f"  ...and {omitted} more" if omitted > 0 else None
            # Ensure space for ending line; drop last rows if needed
            if ending_line:
                end_len = len(ending_line) + 1
                while curr_len + end_len + footer_len > MAX_LEN and included:
                    last = included.pop()
                    curr_len -= len(last) + 1
                # If nothing fits, omit ending line
                if not included and curr_len + end_len + footer_len > MAX_LEN:
                    ending_line = None
            for row in included:
                r_lines.append(f"  {row}")
            if ending_line:
                r_lines.append(ending_line)
            for fl in footer_lines:
                r_lines.append(fl)
            roster_text = "\n".join(r_lines)
            # Build a clean, mobile-friendly embed (Jericho embed style)
            try:
                kt_display_name = _extract_killteam_name(
                    getattr(killteam, "name", "Unknown")
                )
                roster_embed = discord.Embed(
                    title="᛭⋅ KILL TEAM ROSTER ⋅᛭",
                    description=f"*⌾ {kt_display_name} ⌾*",
                    color=0x2ECC71,
                )

                # Build roster entries using combat bonds style formatting
                roster_lines = []
                for it in sorted_items:
                    member_id = str(it.get("member_id", "") or "")
                    nm = str(it.get("name", "") or "")
                    studs = str(it.get("studs_symbols", "") or "")
                    st = str(it.get("status", ""))
                    home_ch = str(it.get("home_chapter", "") or "")
                    aar_v = int(it.get("aar", 0) or 0)
                    gene_v = int(it.get("gene", 0) or 0)
                    armory_v = int(it.get("armory", 0) or 0)
                    status_icon = "✅" if st.lower() == "active" else "⏸️"

                    # Get rank emoji
                    role_names = it.get("role_names", [])
                    member_rank = None
                    for rp in RANK_ROLES_PRIORITY:
                        if rp in role_names:
                            member_rank = rp
                            break
                    rank_emoji = (
                        _get_rank_emoji(interaction.guild, member_rank)
                        if member_rank
                        else ""
                    )

                    # Strip rank prefix from name (case-insensitive)
                    stripped_name = nm
                    for rp in RANK_ROLES_PRIORITY:
                        if stripped_name.lower().startswith(rp.lower()):
                            stripped_name = stripped_name[len(rp) :].lstrip()
                            break
                    # Truncate after stripping
                    stripped_name = stripped_name[:20]

                    # Get chapter emoji
                    chapter_emoji = ""
                    if home_ch and home_ch not in ("Unknown", "REDACTED"):
                        chapter_emoji = (
                            _get_emoji_by_name(interaction.guild, home_ch) or ""
                        )

                    # Build member label: rank_emoji name studs chapter_emoji
                    # (status icon on separate concept line below)
                    parts = []
                    if rank_emoji:
                        parts.append(rank_emoji)
                    parts.append(f"**{stripped_name}**")
                    if studs:
                        parts.append(studs)
                    if chapter_emoji:
                        parts.append(chapter_emoji)
                    member_label = " ".join(parts)

                    roster_lines.append(
                        f"{status_icon} {member_label}\n"
                        f"AAR: {aar_v} | Gene: {gene_v} | Armory: {armory_v}"
                    )

                # Chunk into fields (max ~5 members per field to avoid overflow)
                chunk_size = 5
                for i in range(0, len(roster_lines), chunk_size):
                    chunk = roster_lines[i : i + chunk_size]
                    field_value = "\n".join(chunk)
                    roster_embed.add_field(
                        name="\u200b",
                        value=field_value or "—",
                        inline=False,
                    )

                roster_embed.set_footer(
                    text="᛭⋅ Roster generated from recent service records ⋅᛭"
                )

                # Send embed only (clean output)
                if send_to_channel:
                    await send_to_channel.send(embed=roster_embed)
                else:
                    await interaction.followup.send(embed=roster_embed, ephemeral=True)
            except Exception:
                # Fallback to simple embed
                try:
                    roster_embed = _embed_from_ansi("Kill Team Roster", roster_text)
                    if send_to_channel:
                        await send_to_channel.send(embed=roster_embed)
                    else:
                        await interaction.followup.send(
                            embed=roster_embed, ephemeral=True
                        )
                except Exception:
                    if send_to_channel:
                        await send_to_channel.send(roster_text)
                    else:
                        await interaction.followup.send(roster_text, ephemeral=True)
        except Exception:
            # Continue even if roster formatting fails
            pass

        # Use month-to-date time period (month-to-date for rankings)
        now_mtd = datetime.utcnow()
        first_of_month = datetime(now_mtd.year, now_mtd.month, 1)
        span_days = max(1, (now_mtd - first_of_month).days)

        # Check if the killteam role is actually a home chapter
        kt_name_raw = getattr(killteam, "name", "Unknown")
        kt_display = _extract_killteam_name(kt_name_raw)
        is_chapter_role = kt_name_raw in HOME_CHAPTERS

        # Compute fortress-wide rankings for kill team honours display
        try:
            rankings = await _compute_fortress_rankings(
                interaction.guild,
                span_days,
                start_dt=first_of_month,
                end_dt=now_mtd,
            )
        except Exception:
            rankings = {
                "teams": {},
                "chapters": {},
                "imperial_date": _format_imperial_date(datetime.utcnow()),
                "span_days": span_days,
            }

        imperial_date = rankings.get("imperial_date", "")
        team_rankings = rankings.get("teams", {})
        chapter_rankings = rankings.get("chapters", {})

        # If this is a chapter role, look up chapter stats; otherwise look up team stats
        if is_chapter_role:
            # Find the matching chapter key in rankings
            queried_key = None
            for ch in chapter_rankings.get("ops", {}).keys():
                if ch.lower() == kt_name_raw.lower():
                    queried_key = ch
                    break
            active_rankings = chapter_rankings
            display_type = "CHAPTER"
            display_label = kt_name_raw
        else:
            # Try to find the matching team key in rankings
            queried_key = None
            for possible_key in [kt_name_raw, kt_display, f"Kill Team {kt_display}"]:
                for tk in team_rankings.get("ops", {}).keys():
                    if (
                        tk.lower() == possible_key.lower()
                        or possible_key.lower() in tk.lower()
                        or tk.lower() in possible_key.lower()
                    ):
                        queried_key = tk
                        break
                if queried_key:
                    break
            active_rankings = team_rankings
            display_type = "KILL TEAM"
            display_label = kt_display

        # Helper to format rank display
        def fmt_rank(metric_key: str, key: str) -> str:
            try:
                val, rank, total = active_rankings.get(metric_key, {}).get(
                    key, (0, 0, 0)
                )
                return f"#{rank}/{total}"
            except Exception:
                return "—"

        def fmt_val_rank(metric_key: str, key: str, val_fmt: str = "") -> str:
            try:
                val, rank, total = active_rankings.get(metric_key, {}).get(
                    key, (0, 0, 0)
                )
                if val_fmt:
                    return f"{val_fmt.format(val)} (#{rank}/{total})"
                return f"{val} (#{rank}/{total})"
            except Exception:
                return "—"

        # Build the new honours-style output for kill team or chapter
        s_lines = []
        s_lines.append("```ansi")
        s_lines.append(
            "\u001b[32m=============================================================================="
        )
        s_lines.append("  WATCH FORTRESS JERICHO // LEDGER-CAST")
        s_lines.append("  OPERATION-SCRIBE SERVITOR — MONTHLY HONOURS")
        s_lines.append(f"  Date: {imperial_date}")
        s_lines.append(f"  {display_type}: {display_label}")
        s_lines.append(
            "=============================================================================="
        )
        s_lines.append("")
        s_lines.append(f"{display_type} DISTINCTIONS")

        if queried_key:
            # Get values and ranks for each metric
            ops_data = active_rankings.get("ops", {}).get(queried_key, (0, 0, 0))
            avg_data = active_rankings.get("avg", {}).get(queried_key, (0.0, 0, 0))
            pres_data = active_rankings.get("pres", {}).get(queried_key, (0, 0, 0))
            armory_data = active_rankings.get("armory", {}).get(queried_key, (0, 0, 0))
            gene_data = active_rankings.get("gene_carried", {}).get(
                queried_key, (0, 0, 0)
            )
            risk_data = active_rankings.get("high_risk", {}).get(queried_key, (0, 0, 0))
            omega_kia_data = active_rankings.get("omega_kia", {}).get(
                queried_key, (0, 0, 0)
            )
            force_data = active_rankings.get("avg_aar_per_member", {}).get(
                queried_key, (0.0, 0, 0)
            )
            cohesion_data = active_rankings.get("cohesion", {}).get(
                queried_key, (0.0, 0, 0)
            )

            s_lines.append(
                f"Total Operations         (Ops {int(ops_data[0])}) — Rank #{ops_data[1]}/{ops_data[2]}"
            )
            s_lines.append(
                f"Avg Points per Op        (Avg Op {avg_data[0]:.1f}) — Rank #{avg_data[1]}/{avg_data[2]}"
            )
            s_lines.append(
                f"Armory + Gene-seed       (ArmoryPts {armory_data[0]:.1f} | GenePts {gene_data[0]:.1f}) — Rank #{pres_data[1]}/{pres_data[2]}"
            )
            omega_suffix = (
                f" | Omega KIA {int(omega_kia_data[0])}"
                if omega_kia_data[0] > 0
                else ""
            )
            s_lines.append(
                f"High-Risk Ops            (Hard-Strat+Omega {int(risk_data[0])}{omega_suffix}) — Rank #{risk_data[1]}/{risk_data[2]}"
            )
            s_lines.append(
                f"AARs per Member          (Avg AAR/Member {force_data[0]:.1f}) — Rank #{force_data[1]}/{force_data[2]}"
            )
            s_lines.append(
                f"Squad Cohesion           ({cohesion_data[0]:.1f}%) — Rank #{cohesion_data[1]}/{cohesion_data[2]}"
            )
        else:
            s_lines.append("  No ranking data available")

        s_lines.append("")
        s_lines.append(
            "=============================================================================="
        )
        s_lines.append("\u001b[0m```")
        summary_text = "\n".join(s_lines)

        try:
            # Build a clean, mobile-friendly embed (Jericho embed style)
            title_type = "Chapter" if is_chapter_role else "Kill Team"
            embed = discord.Embed(
                title=f"᛭⋅ {title_type.upper()} MONTHLY HONOURS ⋅᛭",
                description=f"*⌾ {display_label} ⌾*\nMonth to Date ({span_days} Days)",
                color=0x2ECC71,
            )
            if queried_key:
                ops_data = active_rankings.get("ops", {}).get(queried_key, (0, 0, 0))
                avg_data = active_rankings.get("avg", {}).get(queried_key, (0.0, 0, 0))
                pres_data = active_rankings.get("pres", {}).get(queried_key, (0, 0, 0))
                armory_data = active_rankings.get("armory", {}).get(
                    queried_key, (0, 0, 0)
                )
                gene_data = active_rankings.get("gene_carried", {}).get(
                    queried_key, (0, 0, 0)
                )
                risk_data = active_rankings.get("high_risk", {}).get(
                    queried_key, (0, 0, 0)
                )
                omega_kia_data = active_rankings.get("omega_kia", {}).get(
                    queried_key, (0, 0, 0)
                )
                force_data = active_rankings.get("avg_aar_per_member", {}).get(
                    queried_key, (0.0, 0, 0)
                )
                cohesion_data = active_rankings.get("cohesion", {}).get(
                    queried_key, (0.0, 0, 0)
                )

                # Compute overall rank as average of all metric rankings
                kt_ranks = []
                if ops_data[2] > 0:
                    kt_ranks.append(ops_data[1])
                if avg_data[2] > 0:
                    kt_ranks.append(avg_data[1])
                if pres_data[2] > 0:
                    kt_ranks.append(pres_data[1])
                if risk_data[2] > 0:
                    kt_ranks.append(risk_data[1])
                if force_data[2] > 0:
                    kt_ranks.append(force_data[1])
                if cohesion_data[2] > 0:
                    kt_ranks.append(cohesion_data[1])
                kt_overall_rank = statistics.median(kt_ranks) if kt_ranks else None

                # ▸ Distinctions field with consolidated stats
                omega_suffix = (
                    f" | KIA {int(omega_kia_data[0])}" if omega_kia_data[0] > 0 else ""
                )
                distinctions = (
                    f"**Operations:** {int(ops_data[0])} (#{ops_data[1]}/{ops_data[2]})\n"
                    f"**Avg Pts/Op:** {avg_data[0]:.1f} (#{avg_data[1]}/{avg_data[2]})\n"
                    f"**Armory+Gene:** #({pres_data[1]}/{pres_data[2]})\n"
                    f"**High-Risk:** {int(risk_data[0])}{omega_suffix} (#{risk_data[1]}/{risk_data[2]})\n"
                    f"**AARs/Member:** {force_data[0]:.1f} (#{force_data[1]}/{force_data[2]})\n"
                    f"**Cohesion:** {cohesion_data[0]:.1f}% (#{cohesion_data[1]}/{cohesion_data[2]})"
                )
                if kt_overall_rank is not None:
                    distinctions += f"\n**Overall Rank:** #{kt_overall_rank:.1f}"
                embed.add_field(
                    name=f"▸ {title_type} Distinctions",
                    value=distinctions,
                    inline=False,
                )
            else:
                embed.add_field(
                    name="▸ Distinctions",
                    value="No ranking data available",
                    inline=False,
                )
            embed.set_footer(text=f"᛭⋅ Imperial Date: {imperial_date} ⋅᛭")

            # Send embed only (clean output)
            if send_to_channel:
                await send_to_channel.send(embed=embed)
                await interaction.followup.send(
                    f"Posted to <#{send_to_channel.id}>.", ephemeral=True
                )
            else:
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            # Fallback to simple embed
            try:
                fallback_title = (
                    "Chapter Summary" if is_chapter_role else "Kill Team Summary"
                )
                embed = _embed_from_ansi(fallback_title, summary_text)
                if send_to_channel:
                    await send_to_channel.send(embed=embed)
                    await interaction.followup.send(
                        f"Posted to <#{send_to_channel.id}>.", ephemeral=True
                    )
                else:
                    await interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                pass

    # Only send the detailed per-brother ledger for single-brother queries
    if not killteam:
        # Build a clean, mobile-friendly embed (Jericho embed style)
        try:
            if (len(members) == 1) and member_stat_rows_list:
                target = members[0]
                name_val = roster_items[0].get("name") if roster_items else "Unknown"
                stat_dict = {k: v for k, v in member_stat_rows_list[0]}

                # Get rank emoji for display
                guild = interaction.guild
                member_rank_name = "Watch Brother"
                for rank in RANK_ROLES_PRIORITY:
                    if rank in [getattr(r, "name", "") for r in target.roles]:
                        member_rank_name = rank
                        break
                rank_emoji = _get_rank_emoji(guild, member_rank_name) if guild else ""

                # Get home chapter emoji
                home_ch = stat_dict.get("Home Chapter", "Unknown")
                chapter_emoji = (
                    _get_emoji_by_name(guild, home_ch)
                    if guild and home_ch and home_ch not in ("Unknown", "REDACTED")
                    else None
                )

                embed = discord.Embed(
                    title="᛭⋅ DEEDS LEDGER ⋅᛭",
                    description="*⌾ Watch Fortress Jericho ⌾*",
                    color=0x2ECC71,
                )

                # ▸ Bearer field (exactly matching forge_rite format)
                bearer_honorific, bearer_name, bearer_title = _get_bearer_rank_and_title(target)
                bearer_name = bearer_name.replace("●", "").replace("⚬", "").strip()
                rank_prefix = f"{rank_emoji} " if rank_emoji else ""
                if ", " in bearer_honorific:
                    title_part, rank_part = bearer_honorific.rsplit(", ", 1)
                    bearer_value = f"{rank_prefix}**{title_part},**\n**{rank_part} {bearer_name}**"
                else:
                    bearer_value = f"{rank_prefix}**{bearer_honorific} {bearer_name}**"
                if bearer_title:
                    bearer_value += f"\n*{bearer_title}*"
                if home_ch and home_ch not in ("Unknown", "REDACTED"):
                    chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
                    lineage_display = (
                        "REDACTED" if home_ch == "Black Shield" else home_ch
                    )
                    bearer_value += f"\nLineage: {chapter_prefix}{lineage_display}"
                bearer_studs = roster_items[0].get("studs_count", 0) if roster_items else 0
                if bearer_studs > 0:
                    studs_pips = _studs_pips(bearer_studs)
                    bearer_value += f"\nService Studs: [{studs_pips}] ({bearer_studs})"
                embed.add_field(name="▸ Bearer", value=bearer_value, inline=True)

                # ▸ Status field
                status_val = stat_dict.get("Status", "Unknown")
                last_aar_val = stat_dict.get("Last AAR", "—")
                status_lines = [f"**{status_val}**", f"Last AAR: {last_aar_val}"]
                embed.add_field(
                    name="▸ Status", value="\n".join(status_lines), inline=True
                )

                # ▸ Service Record field
                induction_val = stat_dict.get("Induction", "—")
                embed.add_field(
                    name="▸ Induction",
                    value=f"{induction_val}",
                    inline=False,
                )

                # ▸ Deeds Tallied field (consolidated stats)
                ops_val = stat_dict.get("Total Operations", "0")
                waves_val = stat_dict.get("Total Siege Waves", "0")
                sanctioned_val = stat_dict.get("Brothers Sanctioned", "0")
                aar_val = stat_dict.get("AAR Commendations", "0")
                gene_val = stat_dict.get("Gene-seed Secured", "0")
                armory_val = stat_dict.get("Armory Data Recovered", "0")

                deeds_value = (
                    f"Operations: **{ops_val}** | Siege Waves: **{waves_val}**\n"
                    f"Brothers Sanctioned: **{sanctioned_val}**\n"
                    f"AAR: **{aar_val}** | Gene-seed: **{gene_val}** | Armory: **{armory_val}**"
                )
                embed.add_field(name="▸ Deeds Tallied", value=deeds_value, inline=False)

                # ▸ Armor Integrity field
                try:
                    armor_state = await _get_armor_state(int(target.id))
                    points_since_blessing = armor_state.get("points_since_blessing", 0)
                    spirit_fractured = armor_state.get("spirit_fractured", False)
                    armor_tier = _get_member_damage_tier(target)
                    damage_probability = _get_damage_probability(points_since_blessing)
                    prob_percent = damage_probability * 100
                    machine_spirit = await _get_machine_spirit(int(target.id))

                    # Roll scan detection (same as armor_status)
                    scan_result = await _get_or_roll_scan_result(
                        int(target.id), armor_tier, points_since_blessing, spirit_fractured
                    )
                    scan_missed = not scan_result["detected"]

                    if scan_missed:
                        # Unreadable - mask armor data
                        embed.add_field(
                            name="▸ Armor Integrity",
                            value="⚫ **UNREADABLE** | Spirit: ???\nPenalty Risk: ??? | Cycles: ???",
                            inline=False,
                        )
                    else:
                        if spirit_fractured:
                            armor_icon = "💀"
                            armor_status = "FRACTURED"
                            spirit_status = "SEVERED"
                        elif armor_tier == "critical":
                            armor_icon = "🔴"
                            armor_status = "CRITICAL"
                            spirit_status = "UNSTABLE"
                        elif armor_tier == "compromised":
                            armor_icon = "🟠"
                            armor_status = "COMPROMISED"
                            spirit_status = "AGITATED"
                        elif armor_tier == "damaged":
                            armor_icon = "🟡"
                            armor_status = "DAMAGED"
                            spirit_status = "STABLE"
                        else:
                            armor_icon = "🟢"
                            armor_status = "NOMINAL"
                            spirit_status = "STABLE"

                        # Get MachineSpirit emoji
                        machine_spirit_emoji = _get_emoji_by_name(guild, "MachineSpirit") or "⚙️"
                        
                        if spirit_fractured:
                            spirit_display = f"{machine_spirit_emoji} Spirit: SEVERED"
                        elif machine_spirit:
                            spirit_display = f"{machine_spirit_emoji} Spirit: `{machine_spirit}` ({spirit_status})"
                        else:
                            spirit_display = f"{machine_spirit_emoji} Spirit: *UNBOUND*"

                        armor_lines = [f"{armor_icon} **{armor_status}** | {spirit_display}"]
                        # Show penalty risk (probabilistic) and cycles
                        penalty_risk = _get_tier_risk_display(armor_tier, spirit_fractured)
                        armor_lines.append(f"Penalty Risk: {penalty_risk} | Cycles: {points_since_blessing}c")

                        embed.add_field(
                            name="▸ Armor Integrity",
                            value="\n".join(armor_lines),
                            inline=False,
                        )
                except Exception:
                    pass  # Skip armor field if data unavailable

                # ▸ Challenges field
                target_role_ids_ch = {
                    getattr(r, "id", 0) for r in getattr(target, "roles", [])
                }
                completed_challenges = []
                for role_id_ch, display_name_ch, emoji_hint in CHALLENGE_ROLES:
                    if role_id_ch in target_role_ids_ch:
                        emoji_str = ""
                        if emoji_hint:
                            if emoji_hint.startswith("unicode:"):
                                emoji_str = f"{emoji_hint[8:]} "
                            else:
                                emoji = _get_emoji_by_name(guild, emoji_hint)
                                if emoji:
                                    emoji_str = f"{emoji} "
                        completed_challenges.append(f"{emoji_str}{display_name_ch}")

                if completed_challenges:
                    challenge_lines = [f"✦ {c}" for c in completed_challenges]
                    base_field_name = f"▸ Challenges ({len(completed_challenges)})"
                    current_chunk = ""
                    field_index = 0

                    for line in challenge_lines:
                        prefix = "" if current_chunk == "" else "\n"
                        line_with_sep = prefix + line

                        if len(current_chunk) + len(line_with_sep) > 1024:
                            field_name = base_field_name if field_index == 0 else "\u200b"
                            embed.add_field(
                                name=field_name, value=current_chunk, inline=False
                            )
                            field_index += 1
                            current_chunk = line
                        else:
                            current_chunk += line_with_sep

                    if current_chunk:
                        field_name = base_field_name if field_index == 0 else "\u200b"
                        embed.add_field(
                            name=field_name, value=current_chunk, inline=False
                        )

                # Footer
                embed.set_footer(text="᛭⋅ Recorded by decree of Watch Command ⋅᛭")
            else:
                embed = _embed_from_ansi("Deeds Ledger", reply_text)
        except Exception:
            embed = _embed_from_ansi("Deeds Ledger", reply_text)

        # Send embed only (clean output like forge_rite/stud announcement)
        if send_to_channel:
            await send_to_channel.send(embed=embed)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

        # Send Monthly Honours as a separate additional message
        if len(members) == 1:
            # Use month-to-date time period (month-to-date for rankings)
            now_mtd = datetime.utcnow()
            first_of_month = datetime(now_mtd.year, now_mtd.month, 1)
            mtd_span_days = max(1, (now_mtd - first_of_month).days)
            try:
                rankings = await _compute_fortress_rankings(
                    interaction.guild,
                    mtd_span_days,
                    start_dt=first_of_month,
                    end_dt=now_mtd,
                )
            except Exception:
                rankings = {
                    "individuals": {},
                    "chapters": {},
                    "teams": {},
                    "chapters_map": {},
                    "imperial_date": _format_imperial_date(datetime.utcnow()),
                    "span_days": mtd_span_days,
                }

            imperial_date = rankings.get("imperial_date", "")
            individual_rankings = rankings.get("individuals", {})
            chapter_rankings = rankings.get("chapters", {})
            team_rankings = rankings.get("teams", {})
            resolved_chapters_map = rankings.get("chapters_map", {})

            target = members[0]
            target_id = str(target.id)
            target_name = getattr(
                target, "display_name", getattr(target, "name", "Unknown")
            )
            home_chapter = resolved_chapters_map.get(
                target_id, chapters_map.get(target_id, "Unknown")
            )

            # Get individual ranking data
            ops_data = individual_rankings.get("ops", {}).get(target_id, (0, 0, 0))
            avg_data = individual_rankings.get("avg", {}).get(target_id, (0.0, 0, 0))
            gene_data = individual_rankings.get("gene_carried", {}).get(
                target_id, (0, 0, 0)
            )
            armory_data = individual_rankings.get("armory", {}).get(
                target_id, (0, 0, 0)
            )
            risk_data = individual_rankings.get("high_risk", {}).get(
                target_id, (0, 0, 0)
            )
            omega_kia_data = individual_rankings.get("omega_kia", {}).get(
                target_id, (0, 0, 0)
            )
            black_laurels_data = individual_rankings.get("black_laurels", {}).get(
                target_id, (0, 0, 0)
            )

            # Get chapter ranking data (matching kill team metrics)
            ch_ops_data = chapter_rankings.get("ops", {}).get(home_chapter, (0, 0, 0))
            ch_avg_data = chapter_rankings.get("avg", {}).get(home_chapter, (0.0, 0, 0))
            ch_pres_data = chapter_rankings.get("pres", {}).get(home_chapter, (0, 0, 0))
            ch_armory_val = chapter_rankings.get("armory", {}).get(
                home_chapter, (0, 0, 0)
            )[0]
            ch_gene_val = chapter_rankings.get("gene_carried", {}).get(
                home_chapter, (0, 0, 0)
            )[0]
            ch_risk_data = chapter_rankings.get("high_risk", {}).get(
                home_chapter, (0, 0, 0)
            )
            ch_omega_kia_data = chapter_rankings.get("omega_kia", {}).get(
                home_chapter, (0, 0, 0)
            )
            ch_aar_data = chapter_rankings.get("avg_aar_per_member", {}).get(
                home_chapter, (0.0, 0, 0)
            )

            # Get target's kill teams using _resolve_killteams_for_member
            target_killteams = []
            try:
                target_killteams = _resolve_killteams_for_member(target)
            except Exception:
                pass

            # Build honours ANSI block
            h_lines = []
            h_lines.append("```ansi")
            h_lines.append(
                "\u001b[32m=============================================================================="
            )
            h_lines.append("  WATCH FORTRESS JERICHO // LEDGER-CAST")
            h_lines.append("  OPERATION-SCRIBE SERVITOR — MONTHLY HONOURS")
            h_lines.append(f"  Date: {imperial_date}")
            h_lines.append(f"  Brother: {target_name}")
            h_lines.append(f"  Home Chapter: {home_chapter}")
            h_lines.append(
                "=============================================================================="
            )
            h_lines.append("")
            h_lines.append("INDIVIDUAL DISTINCTIONS")

            if ops_data[2] > 0:  # Has ranking data
                h_lines.append(
                    f"Total Operations         (Ops {int(ops_data[0])}) — Rank #{ops_data[1]}/{ops_data[2]}"
                )
                h_lines.append(
                    f"Avg Points per Op        (Avg Op {avg_data[0]:.1f}) — Rank #{avg_data[1]}/{avg_data[2]}"
                )
                h_lines.append(
                    f"Gene-seed Points         (GeneseedPts {int(gene_data[0])}) — Rank #{gene_data[1]}/{gene_data[2]}"
                )
                h_lines.append(
                    f"Armory Points            (ArmoryPts {int(armory_data[0])}) — Rank #{armory_data[1]}/{armory_data[2]}"
                )
                omega_suffix = (
                    f" | Omega KIA {int(omega_kia_data[0])}"
                    if omega_kia_data[0] > 0
                    else ""
                )
                h_lines.append(
                    f"High-Risk Ops            (Hard-Strat+Omega {int(risk_data[0])}{omega_suffix}) — Rank #{risk_data[1]}/{risk_data[2]}"
                )
                h_lines.append(
                    f"Black Laurels Missions   (BL Ops {int(black_laurels_data[0])}) — Rank #{black_laurels_data[1]}/{black_laurels_data[2]}"
                )
            else:
                h_lines.append("  No ranking data available")

            h_lines.append("")
            h_lines.append("CHAPTER DISTINCTIONS")

            if ch_ops_data[2] > 0:  # Has chapter ranking data
                h_lines.append(
                    f"Total Operations         (Ops {int(ch_ops_data[0])}) — Rank #{ch_ops_data[1]}/{ch_ops_data[2]}"
                )
                h_lines.append(
                    f"Avg Points per Op        (Avg Op {ch_avg_data[0]:.1f}) — Rank #{ch_avg_data[1]}/{ch_avg_data[2]}"
                )
                h_lines.append(
                    f"Armory + Gene-seed       (ArmoryPts {ch_armory_val:.1f} | GenePts {ch_gene_val:.1f}) — Rank #{ch_pres_data[1]}/{ch_pres_data[2]}"
                )
                ch_omega_suffix = (
                    f" | Omega KIA {int(ch_omega_kia_data[0])}"
                    if ch_omega_kia_data[0] > 0
                    else ""
                )
                h_lines.append(
                    f"High-Risk Ops            (Hard-Strat+Omega {int(ch_risk_data[0])}{ch_omega_suffix}) — Rank #{ch_risk_data[1]}/{ch_risk_data[2]}"
                )
                h_lines.append(
                    f"AARs per Member          (Avg AAR/Member {ch_aar_data[0]:.1f}) — Rank #{ch_aar_data[1]}/{ch_aar_data[2]}"
                )
            else:
                h_lines.append("  Chapter does not meet minimum threshold for ranking")

            # Kill Team Distinctions (for each team the member belongs to)
            if target_killteams:
                for kt_name in target_killteams:
                    h_lines.append("")
                    h_lines.append(f"KILL TEAM DISTINCTIONS: {kt_name}")
                    kt_ops_data = team_rankings.get("ops", {}).get(kt_name, (0, 0, 0))
                    kt_avg_data = team_rankings.get("avg", {}).get(kt_name, (0.0, 0, 0))
                    kt_pres_data = team_rankings.get("pres", {}).get(kt_name, (0, 0, 0))
                    kt_armory_val = team_rankings.get("armory", {}).get(
                        kt_name, (0, 0, 0)
                    )[0]
                    kt_gene_val = team_rankings.get("gene_carried", {}).get(
                        kt_name, (0, 0, 0)
                    )[0]
                    kt_risk_data = team_rankings.get("high_risk", {}).get(
                        kt_name, (0, 0, 0)
                    )
                    kt_aar_data = team_rankings.get("avg_aar_per_member", {}).get(
                        kt_name, (0.0, 0, 0)
                    )
                    kt_cohesion_data = team_rankings.get("cohesion", {}).get(
                        kt_name, (0.0, 0, 0)
                    )
                    kt_omega_kia_data = team_rankings.get("omega_kia", {}).get(
                        kt_name, (0, 0, 0)
                    )
                    if kt_ops_data[2] > 0:
                        h_lines.append(
                            f"Total Operations         (Ops {int(kt_ops_data[0])}) — Rank #{kt_ops_data[1]}/{kt_ops_data[2]}"
                        )
                        h_lines.append(
                            f"Avg Points per Op        (Avg Op {kt_avg_data[0]:.1f}) — Rank #{kt_avg_data[1]}/{kt_avg_data[2]}"
                        )
                        h_lines.append(
                            f"Armory + Gene-seed       (ArmoryPts {kt_armory_val:.1f} | GenePts {kt_gene_val:.1f}) — Rank #{kt_pres_data[1]}/{kt_pres_data[2]}"
                        )
                        kt_omega_suffix = (
                            f" | Omega KIA {int(kt_omega_kia_data[0])}"
                            if kt_omega_kia_data[0] > 0
                            else ""
                        )
                        h_lines.append(
                            f"High-Risk Ops            (Hard-Strat+Omega {int(kt_risk_data[0])}{kt_omega_suffix}) — Rank #{kt_risk_data[1]}/{kt_risk_data[2]}"
                        )
                        h_lines.append(
                            f"AARs per Member          (Avg AAR/Member {kt_aar_data[0]:.1f}) — Rank #{kt_aar_data[1]}/{kt_aar_data[2]}"
                        )
                        h_lines.append(
                            f"Squad Cohesion           ({kt_cohesion_data[0]:.1f}%) — Rank #{kt_cohesion_data[1]}/{kt_cohesion_data[2]}"
                        )
                    else:
                        h_lines.append("  No ranking data available")

            h_lines.append("")
            h_lines.append(
                "=============================================================================="
            )
            h_lines.append("\u001b[0m```")
            honours_text = "\n".join(h_lines)

            # Build a clean, mobile-friendly embed (Jericho embed style)
            try:
                # Get chapter emoji for display
                guild = interaction.guild
                chapter_emoji = (
                    _get_emoji_by_name(guild, home_chapter)
                    if guild
                    and home_chapter
                    and home_chapter not in ("Unknown", "REDACTED")
                    else None
                )
                chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""

                honours_embed = discord.Embed(
                    title="᛭⋅ MONTHLY HONOURS ⋅᛭",
                    description=f"*⌾ {target_name} ⌾*\nMonth to Date ({mtd_span_days} Days)",
                    color=0x2ECC71,
                )

                # Compute median rank from individual metrics
                individual_ranks = []
                if ops_data[2] > 0:
                    individual_ranks.append(ops_data[1])
                if avg_data[2] > 0:
                    individual_ranks.append(avg_data[1])
                if gene_data[2] > 0:
                    individual_ranks.append(gene_data[1])
                if armory_data[2] > 0:
                    individual_ranks.append(armory_data[1])
                if risk_data[2] > 0:
                    individual_ranks.append(risk_data[1])
                if black_laurels_data[2] > 0:
                    individual_ranks.append(black_laurels_data[1])

                median_rank = None
                if individual_ranks:
                    median_rank = statistics.median(individual_ranks)

                # Compute overall rank as average of individual rankings
                overall_rank = None
                if individual_ranks:
                    overall_rank = statistics.median(individual_ranks)

                # ▸ Individual Distinctions field
                if ops_data[2] > 0:
                    omega_suffix = (
                        f" | KIA {int(omega_kia_data[0])}"
                        if omega_kia_data[0] > 0
                        else ""
                    )
                    individual_value = (
                        f"**Operations:** {int(ops_data[0])} (#{ops_data[1]}/{ops_data[2]})\n"
                        f"**Avg Pts/Op:** {avg_data[0]:.1f} (#{avg_data[1]}/{avg_data[2]})\n"
                        f"**Gene-seed:** {int(gene_data[0])} (#{gene_data[1]}/{gene_data[2]})\n"
                        f"**Armory:** {int(armory_data[0])} (#{armory_data[1]}/{armory_data[2]})\n"
                        f"**High-Risk:** {int(risk_data[0])}{omega_suffix} (#{risk_data[1]}/{risk_data[2]})\n"
                        f"**Black Laurels:** {int(black_laurels_data[0])} (#{black_laurels_data[1]}/{black_laurels_data[2]})"
                    )
                    if overall_rank is not None:
                        individual_value += f"\n**Overall Rank:** #{overall_rank:.1f}"
                else:
                    individual_value = "No ranking data available"
                honours_embed.add_field(
                    name="▸ Individual Distinctions",
                    value=individual_value,
                    inline=False,
                )

                # ▸ Chapter Distinctions field
                lineage_display = (
                    "REDACTED" if home_chapter == "Black Shield" else home_chapter
                )
                # Compute chapter median rank
                chapter_ranks = []
                if ch_ops_data[2] > 0:
                    chapter_ranks.append(ch_ops_data[1])
                if ch_avg_data[2] > 0:
                    chapter_ranks.append(ch_avg_data[1])
                if ch_pres_data[2] > 0:
                    chapter_ranks.append(ch_pres_data[1])
                if ch_risk_data[2] > 0:
                    chapter_ranks.append(ch_risk_data[1])
                if ch_aar_data[2] > 0:
                    chapter_ranks.append(ch_aar_data[1])

                ch_median_rank = None
                if chapter_ranks:
                    ch_median_rank = statistics.median(chapter_ranks)

                # Compute overall rank as median of chapter rankings
                ch_overall_rank = None
                if chapter_ranks:
                    ch_overall_rank = statistics.median(chapter_ranks)

                if ch_ops_data[2] > 0:
                    ch_omega_suffix = (
                        f" | KIA {int(ch_omega_kia_data[0])}"
                        if ch_omega_kia_data[0] > 0
                        else ""
                    )
                    chapter_value = (
                        f"**Operations:** {int(ch_ops_data[0])} (#{ch_ops_data[1]}/{ch_ops_data[2]})\n"
                        f"**Avg Pts/Op:** {ch_avg_data[0]:.1f} (#{ch_avg_data[1]}/{ch_avg_data[2]})\n"
                        f"**Armory + Gene:** #{ch_pres_data[1]}/{ch_pres_data[2]}\n"
                        f"**High-Risk:** {int(ch_risk_data[0])}{ch_omega_suffix} (#{ch_risk_data[1]}/{ch_risk_data[2]})\n"
                        f"**AARs/Member:** {ch_aar_data[0]:.1f} (#{ch_aar_data[1]}/{ch_aar_data[2]})"
                    )
                    if ch_overall_rank is not None:
                        chapter_value += f"\n**Overall Rank:** #{ch_overall_rank:.1f}"
                else:
                    chapter_value = "Below minimum threshold"
                honours_embed.add_field(
                    name=f"▸ {chapter_prefix}{lineage_display} Chapter",
                    value=chapter_value,
                    inline=False,
                )

                # ▸ Kill Team Distinctions fields (one per team the member belongs to)
                for kt_name in target_killteams:
                    kt_ops_data = team_rankings.get("ops", {}).get(kt_name, (0, 0, 0))
                    kt_avg_data = team_rankings.get("avg", {}).get(kt_name, (0.0, 0, 0))
                    kt_pres_data = team_rankings.get("pres", {}).get(kt_name, (0, 0, 0))
                    kt_risk_data = team_rankings.get("high_risk", {}).get(
                        kt_name, (0, 0, 0)
                    )
                    kt_aar_data = team_rankings.get("avg_aar_per_member", {}).get(
                        kt_name, (0.0, 0, 0)
                    )
                    kt_cohesion_data = team_rankings.get("cohesion", {}).get(
                        kt_name, (0.0, 0, 0)
                    )
                    kt_omega_kia_data = team_rankings.get("omega_kia", {}).get(
                        kt_name, (0, 0, 0)
                    )

                    # Compute overall rank for kill team
                    kt_ranks = []
                    if kt_ops_data[2] > 0:
                        kt_ranks.append(kt_ops_data[1])
                    if kt_avg_data[2] > 0:
                        kt_ranks.append(kt_avg_data[1])
                    if kt_pres_data[2] > 0:
                        kt_ranks.append(kt_pres_data[1])
                    if kt_risk_data[2] > 0:
                        kt_ranks.append(kt_risk_data[1])
                    if kt_aar_data[2] > 0:
                        kt_ranks.append(kt_aar_data[1])
                    if kt_cohesion_data[2] > 0:
                        kt_ranks.append(kt_cohesion_data[1])
                    kt_overall_rank = statistics.median(kt_ranks) if kt_ranks else None

                    if kt_ops_data[2] > 0:
                        kt_omega_suffix = (
                            f" | KIA {int(kt_omega_kia_data[0])}"
                            if kt_omega_kia_data[0] > 0
                            else ""
                        )
                        kt_value = (
                            f"**Operations:** {int(kt_ops_data[0])} (#{kt_ops_data[1]}/{kt_ops_data[2]})\n"
                            f"**Avg Pts/Op:** {kt_avg_data[0]:.1f} (#{kt_avg_data[1]}/{kt_avg_data[2]})\n"
                            f"**Armory+Gene:** #({kt_pres_data[1]}/{kt_pres_data[2]})\n"
                            f"**High-Risk:** {int(kt_risk_data[0])}{kt_omega_suffix} (#{kt_risk_data[1]}/{kt_risk_data[2]})\n"
                            f"**AARs/Member:** {kt_aar_data[0]:.1f} (#{kt_aar_data[1]}/{kt_aar_data[2]})\n"
                            f"**Cohesion:** {kt_cohesion_data[0]:.1f}% (#{kt_cohesion_data[1]}/{kt_cohesion_data[2]})"
                        )
                        if kt_overall_rank is not None:
                            kt_value += f"\n**Overall Rank:** #{kt_overall_rank:.1f}"
                    else:
                        kt_value = "No ranking data available"
                    honours_embed.add_field(
                        name=f"▸ {kt_name}",
                        value=kt_value,
                        inline=False,
                    )

                honours_embed.set_footer(text=f"᛭⋅ Imperial Date: {imperial_date} ⋅᛭")
            except Exception:
                honours_embed = _embed_from_ansi("Monthly Honours", honours_text)

            # Send embed only (clean output like forge_rite/stud announcement)
            if send_to_channel:
                await send_to_channel.send(embed=honours_embed)
                await interaction.followup.send(
                    f"Posted to <#{send_to_channel.id}>.", ephemeral=True
                )
            else:
                await interaction.followup.send(embed=honours_embed, ephemeral=True)


@bot.tree.command(
    name="my_deeds",
    description="View your own Deeds Ledger (Watch Brother only, in your KT channel).",
)
async def my_deeds(interaction: discord.Interaction):
    """Self-service deeds ledger for Watch Brothers in their Kill Team channels.

    Permission requirements:
    - Caller has the Watch Brother role
    - Caller does NOT have Watch Command role
    - Channel is a thread under a configured KT forum
    - Caller's KT role name matches the thread name
    """
    caller = interaction.user
    caller_role_names = _canonical_role_names(caller)

    # Forgemaster bypass for testing
    is_forgemaster = "Forgemaster" in caller_role_names

    # Check caller has Watch Brother role (Forgemaster exempt)
    if not is_forgemaster and (
        "Watch Brother" not in caller_role_names
        and "Watch Sister" not in caller_role_names
    ):
        await interaction.response.send_message(
            "This command is for Watch Brothers only.", ephemeral=True
        )
        return

    # Deny if caller has Watch Command role (they should use /tally_deeds) - Forgemaster exempt
    if not is_forgemaster and "Watch Command" in caller_role_names:
        await interaction.response.send_message(
            "Watch Command members should use `/tally_deeds` instead.", ephemeral=True
        )
        return

    # Check channel is a KT thread
    ch = getattr(interaction, "channel", None)
    if ch is None:
        await interaction.response.send_message(
            "Could not determine channel context.", ephemeral=True
        )
        return

    is_thread = (
        isinstance(ch, discord.Thread)
        if hasattr(discord, "Thread")
        else getattr(ch, "type", None) == discord.ChannelType.public_thread
    )
    parent = getattr(ch, "parent", None)
    parent_id = getattr(parent, "id", None) if parent else None

    if not is_forgemaster and not (
        is_thread and parent_id and parent_id in ALLOWED_KT_FORUM_PARENT_IDS
    ):
        await interaction.response.send_message(
            "This command can only be used in your Kill Team forum post.",
            ephemeral=True,
        )
        return

    # Get caller's Kill Team name using shared resolution logic
    caller_kt_name = _resolve_killteam_for_member(caller)
    if caller_kt_name:
        caller_kt_name = caller_kt_name.lower()
    if not is_forgemaster and not caller_kt_name:
        await interaction.response.send_message(
            "You must belong to a Kill Team to use this command.", ephemeral=True
        )
        return

    # Extract KT name from thread name and verify match (Forgemaster exempt)
    thread_name = getattr(ch, "name", "") or ""
    thread_kt = _extract_killteam_name(thread_name).lower() if thread_name else ""

    if not is_forgemaster and (
        not thread_kt or not (thread_kt in caller_kt_name or caller_kt_name in thread_kt)
    ):
        await interaction.response.send_message(
            "You can only view your deeds in your own Kill Team's forum post.",
            ephemeral=True,
        )
        return

    # Permission checks passed - defer and compute deeds
    await interaction.response.defer(thinking=False, ephemeral=True)

    target = caller
    guild = interaction.guild

    # Compute stats
    stats = compute_stats_for_user(str(target.id))

    # Determine rank
    current_rank = "Watch Brother"
    for rank in RANK_ROLES_PRIORITY:
        for role in target.roles:
            if role.name == rank:
                current_rank = rank
                break
        if current_rank != "Watch Brother":
            break

    display_name = target.nick or target.display_name

    # Join date
    try:
        joined_at = getattr(target, "joined_at", None)
        if joined_at:
            if joined_at.tzinfo is None:
                joined_at = joined_at.replace(tzinfo=timezone.utc)
            ja_utc = joined_at.astimezone(timezone.utc)
            days_since_join = (datetime.now(timezone.utc) - ja_utc).days
            joined_str = (
                f"{ja_utc.strftime('%Y-%m-%d %H:%M %Z')} ({days_since_join}d ago)"
            )
        else:
            joined_str = "Unknown"
    except Exception:
        joined_str = "Unknown"

    # Service studs (only for Watch Veteran+)
    MAX_STUDS = 16
    try:
        studs_count = 0
        idx_veteran = _role_index("Watch Veteran")
        highest_idx = get_highest_rank_index(target)
        if (
            idx_veteran is not None
            and highest_idx is not None
            and highest_idx <= idx_veteran
        ):
            if joined_at:
                now = datetime.utcnow()
                ja = joined_at
                if ja.tzinfo is not None:
                    ja = ja.astimezone(timezone.utc).replace(tzinfo=None)
                weeks = max(0, (now - ja).days // 7)
                studs_time = weeks // 4
            else:
                studs_time = 0
            aar_points_val = int(round(float(stats.get("aar_points", 0) or 0)))
            studs_aar = aar_points_val // 400
            studs_count = min(studs_time, studs_aar, MAX_STUDS)
    except Exception:
        studs_count = 0
    studs_count = min(studs_count, MAX_STUDS)

    # Build studs display
    try:
        if not studs_count:
            studs_display = "— (0 Plasteel)"
        else:
            auramite_count = studs_count // 4
            plasteel_count = studs_count % 4
            studs_symbols = _studs_pips(studs_count)
            # Once in auramite tier, only show Auramite count (ignore plasteel)
            if auramite_count:
                types_str = f"{auramite_count} Auramite"
            else:
                types_str = f"{plasteel_count} Plasteel" if plasteel_count else "0 Plasteel"
            studs_display = f"{studs_symbols} ({types_str})"
    except Exception:
        studs_display = str(studs_count)

    # Trials reported (inductions)
    trials_reported = _count_inductions_from_records(
        str(target.id), DATASTORE.iter_records()
    )

    # Home chapter
    try:
        chapters_map = await _resolve_home_chapters(guild, [str(target.id)])
        home_chapter = chapters_map.get(str(target.id), "REDACTED")
    except Exception:
        home_chapter = "REDACTED"

    # Active/Inactive status
    try:
        # Use cached last_aar_ts from user_stats_cache to avoid O(N) record scan
        cached_ts = DATASTORE.get_user_stats(str(target.id)).get("last_aar_ts")
        status = "Inactive"
        last_aar_date = None
        days_since_aar = None
        if cached_ts:
            try:
                last_aar_date = datetime.fromisoformat(cached_ts)
            except Exception:
                last_aar_date = None
            if last_aar_date is not None:
                if last_aar_date.tzinfo is not None:
                    try:
                        last_aar_date = last_aar_date.astimezone(timezone.utc).replace(tzinfo=None)
                    except Exception:
                        last_aar_date = last_aar_date.replace(tzinfo=None)
                now = datetime.utcnow()
                days_since_aar = (now - last_aar_date).days
                cutoff = now - timedelta(days=28)
                if last_aar_date >= cutoff:
                    status = "Active"
    except Exception:
        status = "Inactive"
        last_aar_date = None
        days_since_aar = None

    # Company/KT visibility
    show_company = True
    company = "Reserves" if status == "Inactive" else "Unknown"
    kt_name = "Unknown"
    try:
        role_names = caller_role_names
        roles = getattr(target, "roles", [])

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
            for role in roles:
                rn = getattr(role, "name", "") or ""
                if "company" in rn.lower():
                    company = rn
                    break

        for role in roles:
            rn = getattr(role, "name", "") or ""
            rn_l = rn.lower()
            if ("kill" in rn_l and "team" in rn_l) and ("champion" not in rn_l):
                kt_name = _extract_killteam_name(rn)
                break
    except Exception:
        pass

    # Format last AAR display
    if last_aar_date is not None and days_since_aar is not None:
        try:
            if last_aar_date.tzinfo is None:
                last_aar_date = last_aar_date.replace(tzinfo=timezone.utc)
            aar_utc = last_aar_date.astimezone(timezone.utc)
            aar_date_str = aar_utc.strftime("%Y-%m-%d")
        except Exception:
            aar_date_str = last_aar_date.strftime("%Y-%m-%d")
        last_aar_display = f"{aar_date_str} ({days_since_aar}d ago)"
    else:
        last_aar_display = "None on record"

    # Build stat_dict for embed
    stat_dict = {
        "Status": status,
        "Last AAR": last_aar_display,
        "Induction": joined_str,
        "Service Studs": studs_display,
        "Home Chapter": home_chapter,
        "Total Operations": str(stats["ops"]),
        "Total Siege Waves": str(stats["waves_participated"]),
        "Brothers Sanctioned": str(trials_reported),
        "AAR Commendations": str(stats["aar_points"]),
        "Gene-seed Secured": str(stats["gene_seed_points"]),
        "Armory Data Recovered": str(stats["armory_points"]),
    }
    if show_company:
        stat_dict["Company"] = company
    if kt_name and kt_name != "Unknown":
        stat_dict["Kill Team"] = kt_name

    # Strip rank prefix from display name
    name_val = display_name
    for rp in RANK_ROLES_PRIORITY:
        if name_val.lower().startswith(rp.lower()):
            name_val = name_val[len(rp) :].lstrip()
            break
    name_val = re.sub(r"[●⚬]+", "", name_val).strip() or display_name

    # Get rank emoji
    rank_emoji = _get_rank_emoji(guild, current_rank) if guild else ""

    # Get chapter emoji
    chapter_emoji = (
        _get_emoji_by_name(guild, home_chapter)
        if guild and home_chapter and home_chapter not in ("Unknown", "REDACTED")
        else None
    )

    # Build embed
    embed = discord.Embed(
        title="᛭⋅ DEEDS LEDGER ⋅᛭",
        description="*⌾ Watch Fortress Jericho ⌾*",
        color=0x2ECC71,
    )

    # ▸ Bearer field (exactly matching forge_rite format)
    bearer_honorific, bearer_name, bearer_title = _get_bearer_rank_and_title(target)
    bearer_name = bearer_name.replace("●", "").replace("⚬", "").strip()
    rank_prefix = f"{rank_emoji} " if rank_emoji else ""
    if ", " in bearer_honorific:
        title_part, rank_part = bearer_honorific.rsplit(", ", 1)
        bearer_value = f"{rank_prefix}**{title_part},**\n**{rank_part} {bearer_name}**"
    else:
        bearer_value = f"{rank_prefix}**{bearer_honorific} {bearer_name}**"
    if bearer_title:
        bearer_value += f"\n*{bearer_title}*"
    if home_chapter and home_chapter not in ("Unknown", "REDACTED"):
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        lineage_display = "REDACTED" if home_chapter == "Black Shield" else home_chapter
        bearer_value += f"\nLineage: {chapter_prefix}{lineage_display}"
    if studs_count > 0:
        studs_pips_display = _studs_pips(studs_count)
        bearer_value += f"\nService Studs: [{studs_pips_display}] ({studs_count})"
    embed.add_field(name="▸ Bearer", value=bearer_value, inline=True)

    # ▸ Status field
    status_val = stat_dict.get("Status", "Unknown")
    last_aar_val = stat_dict.get("Last AAR", "—")
    status_lines = [f"**{status_val}**", f"Last AAR: {last_aar_val}"]
    embed.add_field(name="▸ Status", value="\n".join(status_lines), inline=True)

    # ▸ Service Record field
    induction_val = stat_dict.get("Induction", "—")
    embed.add_field(name="▸ Induction", value=f"{induction_val}", inline=False)

    # ▸ Deeds Tallied field
    ops_val = stat_dict.get("Total Operations", "0")
    waves_val = stat_dict.get("Total Siege Waves", "0")
    sanctioned_val = stat_dict.get("Brothers Sanctioned", "0")
    aar_val = stat_dict.get("AAR Commendations", "0")
    gene_val = stat_dict.get("Gene-seed Secured", "0")
    armory_val = stat_dict.get("Armory Data Recovered", "0")

    deeds_value = (
        f"Operations: **{ops_val}** | Siege Waves: **{waves_val}**\n"
        f"Brothers Sanctioned: **{sanctioned_val}**\n"
        f"AAR: **{aar_val}** | Gene-seed: **{gene_val}** | Armory: **{armory_val}**"
    )
    embed.add_field(name="▸ Deeds Tallied", value=deeds_value, inline=False)

    # ▸ Armor Integrity field
    try:
        armor_state = await _get_armor_state(int(target.id))
        points_since_blessing = armor_state.get("points_since_blessing", 0)
        spirit_fractured = armor_state.get("spirit_fractured", False)
        armor_tier = _get_member_damage_tier(target)
        damage_probability = _get_damage_probability(points_since_blessing)
        prob_percent = damage_probability * 100
        machine_spirit = await _get_machine_spirit(int(target.id))

        # Roll scan detection (same as armor_status)
        scan_result = await _get_or_roll_scan_result(
            int(target.id), armor_tier, points_since_blessing, spirit_fractured
        )
        scan_missed = not scan_result["detected"]

        if scan_missed:
            # Unreadable - mask armor data
            embed.add_field(
                name="▸ Armor Integrity",
                value="⚫ **UNREADABLE** | Spirit: ???\nPenalty Risk: ??? | Cycles: ???",
                inline=False,
            )
        else:
            if spirit_fractured:
                armor_icon = "💀"
                armor_status = "FRACTURED"
                spirit_status = "SEVERED"
            elif armor_tier == "critical":
                armor_icon = "🔴"
                armor_status = "CRITICAL"
                spirit_status = "UNSTABLE"
            elif armor_tier == "compromised":
                armor_icon = "🟠"
                armor_status = "COMPROMISED"
                spirit_status = "AGITATED"
            elif armor_tier == "damaged":
                armor_icon = "🟡"
                armor_status = "DAMAGED"
                spirit_status = "STABLE"
            else:
                armor_icon = "🟢"
                armor_status = "NOMINAL"
                spirit_status = "STABLE"

            # Get MachineSpirit emoji
            machine_spirit_emoji = _get_emoji_by_name(guild, "MachineSpirit") or "⚙️"
            
            if spirit_fractured:
                spirit_display = f"{machine_spirit_emoji} Spirit: SEVERED"
            elif machine_spirit:
                spirit_display = f"{machine_spirit_emoji} Spirit: `{machine_spirit}` ({spirit_status})"
            else:
                spirit_display = f"{machine_spirit_emoji} Spirit: *UNBOUND*"

            armor_lines = [f"{armor_icon} **{armor_status}** | {spirit_display}"]
            # Show penalty risk (probabilistic) and cycles
            penalty_risk = _get_tier_risk_display(armor_tier, spirit_fractured)
            armor_lines.append(f"Penalty Risk: {penalty_risk} | Cycles: {points_since_blessing}c")

            embed.add_field(
                name="▸ Armor Integrity",
                value="\n".join(armor_lines),
                inline=False,
            )
    except Exception:
        pass  # Skip armor field if data unavailable

    # ▸ Challenges field
    target_role_ids = {getattr(r, "id", 0) for r in getattr(target, "roles", [])}
    completed_challenges = []
    for role_id, display_name_ch, emoji_hint in CHALLENGE_ROLES:
        if role_id in target_role_ids:
            emoji_str = ""
            if emoji_hint:
                if emoji_hint.startswith("unicode:"):
                    emoji_str = f"{emoji_hint[8:]} "
                else:
                    emoji = _get_emoji_by_name(guild, emoji_hint)
                    if emoji:
                        emoji_str = f"{emoji} "
            completed_challenges.append(f"{emoji_str}{display_name_ch}")

    if completed_challenges:
        challenge_lines = [f"✦ {c}" for c in completed_challenges]
        base_field_name = f"▸ Challenges ({len(completed_challenges)})"
        current_chunk = ""
        field_index = 0

        for line in challenge_lines:
            prefix = "" if current_chunk == "" else "\n"
            line_with_sep = prefix + line

            if len(current_chunk) + len(line_with_sep) > 1024:
                field_name = base_field_name if field_index == 0 else "\u200b"
                embed.add_field(name=field_name, value=current_chunk, inline=False)
                field_index += 1
                current_chunk = line
            else:
                current_chunk += line_with_sep

        if current_chunk:
            field_name = base_field_name if field_index == 0 else "\u200b"
            embed.add_field(name=field_name, value=current_chunk, inline=False)

    embed.set_footer(text="᛭⋅ Recorded by decree of Watch Command ⋅᛭")

    await interaction.followup.send(embed=embed, ephemeral=True)

    # --- Monthly Honours (same as tally_deeds) ---
    now_mtd = datetime.utcnow()
    first_of_month = datetime(now_mtd.year, now_mtd.month, 1)
    mtd_span_days = max(1, (now_mtd - first_of_month).days)

    try:
        rankings = await _compute_fortress_rankings(
            guild, mtd_span_days, start_dt=first_of_month, end_dt=now_mtd
        )
    except Exception:
        rankings = {
            "individuals": {},
            "chapters": {},
            "teams": {},
            "chapters_map": {},
            "imperial_date": _format_imperial_date(datetime.utcnow()),
            "span_days": mtd_span_days,
        }

    imperial_date = rankings.get("imperial_date", "")
    individual_rankings = rankings.get("individuals", {})
    chapter_rankings = rankings.get("chapters", {})
    team_rankings = rankings.get("teams", {})
    resolved_chapters_map = rankings.get("chapters_map", {})

    target_id = str(target.id)
    target_name = getattr(target, "display_name", getattr(target, "name", "Unknown"))
    home_chapter = resolved_chapters_map.get(target_id, home_chapter)

    # Individual ranking data
    ops_data = individual_rankings.get("ops", {}).get(target_id, (0, 0, 0))
    avg_data = individual_rankings.get("avg", {}).get(target_id, (0.0, 0, 0))
    gene_data = individual_rankings.get("gene_carried", {}).get(target_id, (0, 0, 0))
    armory_data = individual_rankings.get("armory", {}).get(target_id, (0, 0, 0))
    pres_data = individual_rankings.get("pres", {}).get(target_id, (0, 0, 0))
    risk_data = individual_rankings.get("high_risk", {}).get(target_id, (0, 0, 0))
    black_laurels_data = individual_rankings.get("black_laurels", {}).get(
        target_id, (0, 0, 0)
    )
    omega_kia_data = individual_rankings.get("omega_kia", {}).get(target_id, (0, 0, 0))

    # Chapter ranking
    ch_ops_data = chapter_rankings.get("ops", {}).get(home_chapter, (0, 0, 0))
    ch_avg_data = chapter_rankings.get("avg", {}).get(home_chapter, (0.0, 0, 0))
    ch_pres_data = chapter_rankings.get("pres", {}).get(home_chapter, (0, 0, 0))
    ch_armory_val = chapter_rankings.get("armory", {}).get(home_chapter, (0, 0, 0))[0]
    ch_gene_val = chapter_rankings.get("gene_carried", {}).get(home_chapter, (0, 0, 0))[
        0
    ]
    ch_risk_data = chapter_rankings.get("high_risk", {}).get(home_chapter, (0, 0, 0))
    ch_aar_data = chapter_rankings.get("avg_aar_per_member", {}).get(
        home_chapter, (0.0, 0, 0)
    )
    ch_omega_kia_data = chapter_rankings.get("omega_kia", {}).get(
        home_chapter, (0, 0, 0)
    )

    # Kill team rankings
    target_killteams = []
    try:
        for r in getattr(target, "roles", []):
            rn = getattr(r, "name", "") or ""
            if (
                "kill" in rn.lower()
                and "team" in rn.lower()
                and "champion" not in rn.lower()
            ):
                target_killteams.append(_extract_killteam_name(rn))
    except Exception:
        pass

    # Build Monthly Honours embed
    honours_embed = discord.Embed(
        title="᛭⋅ MONTHLY HONOURS ⋅᛭",
        description=f"*⌾ {target_name} — {calendar.month_name[now_mtd.month]} {now_mtd.year} ⌾*",
        color=0x2ECC71,
    )

    # Individual distinctions
    if ops_data[2] > 0:
        omega_suffix = (
            f" | KIA {int(omega_kia_data[0])}" if omega_kia_data[0] > 0 else ""
        )
        indiv_value = (
            f"**Operations:** {int(ops_data[0])} (#{ops_data[1]}/{ops_data[2]})\n"
            f"**Avg Pts/Op:** {avg_data[0]:.1f} (#{avg_data[1]}/{avg_data[2]})\n"
            f"**Armory+Gene:** #({pres_data[1]}/{pres_data[2]})\n"
            f"**High-Risk:** {int(risk_data[0])}{omega_suffix} (#{risk_data[1]}/{risk_data[2]})\n"
            f"**Black Laurels:** {int(black_laurels_data[0])} (#{black_laurels_data[1]}/{black_laurels_data[2]})"
        )
    else:
        indiv_value = "No ranking data available"
    honours_embed.add_field(name="▸ Individual", value=indiv_value, inline=False)

    # Chapter distinctions
    if ch_ops_data[2] > 0:
        ch_omega_suffix = (
            f" | KIA {int(ch_omega_kia_data[0])}" if ch_omega_kia_data[0] > 0 else ""
        )
        ch_value = (
            f"**Operations:** {int(ch_ops_data[0])} (#{ch_ops_data[1]}/{ch_ops_data[2]})\n"
            f"**Avg Pts/Op:** {ch_avg_data[0]:.1f} (#{ch_avg_data[1]}/{ch_avg_data[2]})\n"
            f"**Armory+Gene:** #({ch_pres_data[1]}/{ch_pres_data[2]})\n"
            f"**High-Risk:** {int(ch_risk_data[0])}{ch_omega_suffix} (#{ch_risk_data[1]}/{ch_risk_data[2]})\n"
            f"**AARs/Member:** {ch_aar_data[0]:.1f} (#{ch_aar_data[1]}/{ch_aar_data[2]})"
        )
    else:
        ch_value = "Chapter does not meet minimum threshold"
    honours_embed.add_field(
        name=f"▸ Chapter ({home_chapter})", value=ch_value, inline=False
    )

    # Kill Team distinctions
    for kt_n in target_killteams:
        kt_ops_data = team_rankings.get("ops", {}).get(kt_n, (0, 0, 0))
        kt_avg_data = team_rankings.get("avg", {}).get(kt_n, (0.0, 0, 0))
        kt_pres_data = team_rankings.get("pres", {}).get(kt_n, (0, 0, 0))
        kt_risk_data = team_rankings.get("high_risk", {}).get(kt_n, (0, 0, 0))
        kt_aar_data = team_rankings.get("avg_aar_per_member", {}).get(kt_n, (0.0, 0, 0))
        kt_cohesion_data = team_rankings.get("cohesion", {}).get(kt_n, (0.0, 0, 0))
        kt_omega_kia_data = team_rankings.get("omega_kia", {}).get(kt_n, (0, 0, 0))

        if kt_ops_data[2] > 0:
            kt_omega_suffix = (
                f" | KIA {int(kt_omega_kia_data[0])}"
                if kt_omega_kia_data[0] > 0
                else ""
            )
            kt_value = (
                f"**Operations:** {int(kt_ops_data[0])} (#{kt_ops_data[1]}/{kt_ops_data[2]})\n"
                f"**Avg Pts/Op:** {kt_avg_data[0]:.1f} (#{kt_avg_data[1]}/{kt_avg_data[2]})\n"
                f"**Armory+Gene:** #({kt_pres_data[1]}/{kt_pres_data[2]})\n"
                f"**High-Risk:** {int(kt_risk_data[0])}{kt_omega_suffix} (#{kt_risk_data[1]}/{kt_risk_data[2]})\n"
                f"**AARs/Member:** {kt_aar_data[0]:.1f} (#{kt_aar_data[1]}/{kt_aar_data[2]})\n"
                f"**Cohesion:** {kt_cohesion_data[0]:.1f}% (#{kt_cohesion_data[1]}/{kt_cohesion_data[2]})"
            )
        else:
            kt_value = "No ranking data available"
        honours_embed.add_field(name=f"▸ {kt_n}", value=kt_value, inline=False)

    honours_embed.set_footer(text=f"᛭⋅ Imperial Date: {imperial_date} ⋅᛭")

    await interaction.followup.send(embed=honours_embed, ephemeral=True)


@bot.tree.command(
    name="combat_bonds", description="Show top Combat Bonds (global or for a Brother)."
)
@app_commands.describe(
    brother="Optional: limit to bonds including this Brother.",
    window="Optional: number of days to include (default 30).",
)
async def combat_bonds(
    interaction: discord.Interaction,
    brother: Optional[discord.Member] = None,
    window: Optional[int] = None,
):
    if not (
        check_command_permission(interaction.user, "combat_bonds")
        and is_allowed_channel(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    # Defer the interaction to allow longer processing time on slower hosts
    interaction_deferred = False
    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
        interaction_deferred = True
    except Exception:
        interaction_deferred = False

    # Default to last 28 days; if provided, interpret `window` as days
    span_days = window if (isinstance(window, int) and window > 0) else 28
    missions = _get_missions_last_days(span_days)
    # Collect all brothers seen in window
    all_bros: List[str] = []
    for rec in missions:
        all_bros.extend([str(b) for b in (rec.get("brother_ids") or [])])
    all_bros = sorted(set(all_bros))

    # Filter to only Watch Brother+ ranked members
    eligible_ids = _get_eligible_combat_bonds_ids(interaction.guild, all_bros)
    all_bros = sorted(eligible_ids)

    pair_counts = None
    # Prefer using cached pair_counts from DataStore if available
    try:
        if DATASTORE:
            cached = DATASTORE.get_combat_cache(span_days)
            if cached and isinstance(cached.get("data"), dict):
                pdata = cached.get("data")
                cached_pc = pdata.get("pair_counts")
                if isinstance(cached_pc, dict):
                    pair_counts = cached_pc
    except Exception:
        pair_counts = None

    if pair_counts is None:
        # compute pair_counts off the event loop
        try:
            pair_counts = await asyncio.to_thread(_build_pair_counts, missions)
        except Exception:
            pair_counts = _build_pair_counts(missions)

    # Preserve unfiltered pair_counts for caching; eligibility is applied per-request
    # so that role changes (promotions/demotions) are reflected without a cache rebuild.
    unfiltered_pair_counts = pair_counts

    # Filter pair_counts to only include eligible Watch Brother+ members
    pair_counts = _filter_pair_counts_by_eligible(pair_counts, eligible_ids)

    # Always rebuild triples and spreads from filtered pair_counts
    # Build multi-size groups (3..5) weighted by pair AAR points
    try:
        triples = await asyncio.to_thread(_build_group_bonds, pair_counts, all_bros)
    except Exception:
        triples = _build_group_bonds(pair_counts, all_bros)

    # Active members in the window: those who appeared in at least one AAR
    active_count = len(all_bros)
    try:
        spreads = await asyncio.to_thread(
            _build_spread_counts, pair_counts, active_count=active_count
        )
    except Exception:
        spreads = _build_spread_counts(pair_counts, active_count=active_count)

    # Store in DataStore cache if available
    try:
        if DATASTORE:
            await DATASTORE.set_combat_cache(
                span_days,
                {
                    "pair_counts": unfiltered_pair_counts,
                    "triples": triples,
                    "spreads": spreads,
                },
            )
    except Exception:
        pass

    if brother is None:
        top_global = _select_top_global_bonds(triples, top_n=5)
        # Resolve chapters for all user IDs appearing in selected bonds
        uids: List[str] = []
        for tri, _score in top_global:
            uids.extend(list(tri))
        chapters = await _resolve_home_chapters(interaction.guild, sorted(set(uids)))
        embed = _format_bonds_embed(
            top_global,
            guild=interaction.guild,
            window_days=span_days,
            chapters=chapters,
        )
        # Send jericho embed directly
        try:
            if interaction_deferred:
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception:
            try:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            except Exception:
                logger.exception("combat_bonds: failed to send response or followup")
    else:
        target_id = str(brother.id)
        # Get pairwise bonds for the target brother
        personal_pairs = _select_personal_pair_bonds(pair_counts, target_id, max_n=5)
        # Resolve chapters for partners
        partner_uids = [uid for uid, _score in personal_pairs]
        chapters = await _resolve_home_chapters(
            interaction.guild, sorted(set(partner_uids))
        )
        embed = _format_personal_bonds_jericho_embed(
            personal_pairs,
            target_member=brother,
            guild=interaction.guild,
            window_days=span_days,
            chapters=chapters,
        )
        # Send jericho embed directly
        try:
            if interaction_deferred:
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception:
            try:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            except Exception:
                logger.exception("combat_bonds: failed to send response or followup")


def classify_difficulty(difficulty: str | None):
    if not difficulty:
        return None

    lower = difficulty.lower()

    # Use word boundaries to match only complete difficulty terms
    if re.search(r"\bruthless\b", lower):
        return "ruthless_ops"
    if re.search(r"\blethal\b", lower):
        return "lethal_ops"
    if re.search(r"\babsolute\b", lower):
        return "absolute_ops"
    if re.search(r"\bnormal-stratagem\b", lower):
        return "normal_stratagem"
    if re.search(r"\bhard-stratagem\b", lower):
        return "hard_stratagem"
    if re.search(r"\bnormal-siege\b", lower):
        return "normal_siege"
    if re.search(r"\bhard-siege\b", lower):
        return "hard_siege"
    if re.search(r"\bomega\b", lower):
        return "omega_ops"
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
    if difficulty_class == "omega_ops":
        # Omega operations are fixed-value high-intensity missions
        return 20

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
    if difficulty_class == "omega_ops":
        # Omega uses Absolute's base + 1
        return 5
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

    if difficulty_class == "omega_ops":
        # Omega awards one extra armory point per absolute multiplier
        return armory_data * 4

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
    # Initiation Trial (legacy boolean) and initiate ids (list, max 2)
    initiation_trial = False
    initiate_ids: List[str] = []
    # KIA count (Killed In Action)
    kia_count = 0
    kia_line_present = False
    # Chapter Approved tag present (role mention)
    chapter_approved = False
    chapter_approved_extra_point_applied = False
    # Black Laurels tracking
    black_laurels_in_difficulty = False
    black_laurels_in_mission = False
    black_laurels_mentioned_elsewhere = False
    # Leviathan Protocol tracking
    leviathan_protocol_in_mission = False
    leviathan_protocol_in_difficulty = False
    # Pipehitter tracking
    pipehitter_mentioned = False
    # Watch Command role mention (required for Initiation Trials)
    watch_command_mentioned = False

    brothers_start_idx = None

    for i, raw_line in enumerate(lines):
        line = raw_line.strip()
        lower = line.lower()

        if lower.startswith("mission:"):
            mission = line.split(":", 1)[1].strip()
            # Check if Black Laurels is in mission line (role ID or resolved name)
            if f"<@&{BLACK_LAURELS_ROLE_ID}>" in mission or (
                "black" in mission.lower() and "laurel" in mission.lower()
            ):
                black_laurels_in_mission = True
            # Check if Leviathan Protocol is in mission line
            if f"<@&{LEVIATHAN_PROTOCOL_ROLE_ID}>" in mission or (
                "leviathan" in mission.lower() and "protocol" in mission.lower()
            ):
                leviathan_protocol_in_mission = True
            # If mission contains a trial-like token, mark the legacy initiation flag
            try:
                import re

                if re.search(r"\b-?\d+/\d+\b", mission) or "trial" in mission.lower():
                    initiation_trial = True
            except Exception:
                pass
        elif lower.startswith("difficulty:") or lower.startswith("threat:"):
            after_colon = line.split(":", 1)[1]
            for role in message.role_mentions:
                mention = f"<@&{role.id}>"
                after_colon = after_colon.replace(mention, role.name)
            difficulty = after_colon.strip()
            # Check if Black Laurels is in difficulty line
            if "black" in after_colon.lower() and "laurel" in after_colon.lower():
                black_laurels_in_difficulty = True
            # Check if Leviathan Protocol is in difficulty line
            if "leviathan" in after_colon.lower() and "protocol" in after_colon.lower():
                leviathan_protocol_in_difficulty = True

        # Armory / Armoury Data in any order, any capitalization
        elif ("armory" in lower or "armoury" in lower) and "data" in lower:
            # e.g. "Armory Data: 3" or "Armory data: 3"
            parts = line.split(":", 1)
            try:
                armory_data = int(parts[1].strip()) if len(parts) > 1 else 0
            except ValueError:
                logger.debug(f"Failed to parse armory data from line: {line}")
                armory_data = 0

        # KIA (Killed In Action) line, e.g. 'KIA: 1' or 'KIA: <@12345>'
        elif lower.startswith("kia:"):
            kia_line_present = True
            parts = line.split(":", 1)
            kia_val = parts[1].strip() if len(parts) > 1 else ""
            # Prefer numeric count if present, otherwise count mentions on that line
            try:
                kia_count = int(kia_val)
            except Exception:
                # fallback: count mentions on this line
                kia_count = 0
                for uid in get_user_ids_in_line(raw_line, message):
                    kia_count += 1
            # Clamp KIA to allowed range 0-4
            try:
                kia_count = max(0, min(4, int(kia_count)))
            except Exception:
                kia_count = 0

        # Gene-Seed / Geneseed: lost / carried by @Brother / @Brother (just tag)
        # Valid "carried" formats:
        #   - "Gene-Seed: @Brother" (just a tag, nothing else)
        #   - "Gene-Seed: carried by @Brother" (explicit "carried by")
        # Anything else (e.g., random text with a tag) is NOT parsed as carried
        elif ("gene-seed" in lower) or ("geneseed" in lower):
            parts = line.split(":", 1)
            rest = parts[1].strip() if len(parts) > 1 else ""
            rest_lower = rest.lower()

            if "lost" in rest_lower:
                gene_seed_status = "lost"
            else:
                ids_here = get_user_ids_in_line(raw_line, message)
                if ids_here:
                    # Check if it's "carried by" format OR just a bare tag
                    # Remove the mention from rest to see what's left
                    rest_without_mentions = rest
                    for uid in ids_here:
                        rest_without_mentions = rest_without_mentions.replace(
                            f"<@{uid}>", ""
                        ).replace(f"<@!{uid}>", "")
                    rest_without_mentions = rest_without_mentions.strip().lower()

                    # Valid if: "carried by" OR nothing left (just the tag)
                    if (
                        "carried" in rest_without_mentions
                        or rest_without_mentions == ""
                        or rest_without_mentions == "by"
                    ):
                        gene_seed_status = "carried"
                        gene_seed_carrier_id = ids_here[0]
                        # Also set gene_seed_carried_name to the Discord nickname of the carrier
                        for user in message.mentions:
                            if str(user.id) == gene_seed_carrier_id:
                                try:
                                    gene_seed_carried_name = user.nick
                                except AttributeError:
                                    logger.debug(
                                        f"Failed to get nickname for user ID {gene_seed_carrier_id}"
                                    )
                    # Otherwise leave as unknown (tag with other random text)

        # Check if any Initiation Trial or Neophyte role is mentioned ON THIS LINE
        for role in message.role_mentions:
            # Only process if role mention is actually on this line
            role_pattern = f"<@&{role.id}>"
            if role_pattern not in raw_line:
                continue
            # Detect Initiation Trial role or Neophyte role (ID 1434942334914662501)
            if role.name == "Initiation Trial" or role.id == 1434942334914662501:
                initiation_trial = True
                # Capture up to 2 initiate mentions on the same line
                ids_here = get_user_ids_in_line(raw_line, message)
                for uid in ids_here[:2]:
                    if uid not in initiate_ids:
                        initiate_ids.append(uid)
                    if len(initiate_ids) >= 2:
                        break

        # Detect explicit "Initiation Trial:" header and capture initiate mentions
        # This handles text like "@Initiation Trial: @inductee1 @inductee2" after role resolution
        if "initiation trial" in lower:
            initiation_trial = True
            # Capture up to 2 initiates on the same line as the header
            ids_here = get_user_ids_in_line(raw_line, message)
            for uid in ids_here[:2]:
                if uid not in initiate_ids:
                    initiate_ids.append(uid)
                if len(initiate_ids) >= 2:
                    break

        # Detect Trial: lines (e.g. 'Trial: 1/1' or 'Trial: -/3') - just marks the trial flag
        # Don't capture inductees here since they're on the @Initiation Trial line
        if lower.startswith("trial:"):
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
    # Omega ops: subtract KIA from the base 20 points (floor at 0)
    try:
        if difficulty_class == "omega_ops":
            points_for_op = max(0, int(points_for_op) - int(kia_count))
    except Exception:
        pass

    def role_mentioned(message, *, role_id=None, role_name=None, name_contains=None):
        """
        Check whether a role matching the given criteria is mentioned in the message.

        :param message: Discord message object with a role_mentions attribute.
        :param role_id: Optional int or str role ID to match.
        :param role_name: Optional canonical role name (case-insensitive).
        :param name_contains: Optional iterable of substrings that must all be present
                             in the role name (case-insensitive).
        :return: True if a matching role is found, False otherwise.
        """
        try:
            roles = getattr(message, "role_mentions", [])
        except Exception:
            return False

        try:
            for role in roles:
                try:
                    rn = (getattr(role, "name", "") or "").strip().lower()
                    rid = getattr(role, "id", None)

                    # Match by role ID (accept either int or string form)
                    if role_id is not None:
                        if rid == role_id or str(rid) == str(role_id):
                            return True

                    # Match by exact canonical role name (case-insensitive)
                    if role_name is not None:
                        if rn == role_name.strip().lower():
                            return True

                    # Match by all required substrings in the role name
                    if name_contains:
                        try:
                            if all(token in rn for token in name_contains):
                                return True
                        except Exception:
                            # If name_contains is not iterable or another error occurs, ignore.
                            pass
                except Exception:
                    # Ignore issues with individual role objects and continue scanning.
                    continue
        except Exception:
            return False

        return False

    # Detect Chapter Approved role mention anywhere in the message.
    chapter_approved = role_mentioned(
        message,
        role_id=1467960627795464344,
        role_name="chapter approved",
    )

    # Detect Black Laurels role mention anywhere in the message.
    # Track if it's in difficulty/mission lines OR mentioned as a role elsewhere.
    black_laurels_role_mentioned = role_mentioned(
        message,
        name_contains=("black", "laurel"),
    )
    if (
        black_laurels_role_mentioned
        and not black_laurels_in_difficulty
        and not black_laurels_in_mission
    ):
        black_laurels_mentioned_elsewhere = True

    # Detect Pipehitter role mentions anywhere in the message.
    pipehitter_mentioned = role_mentioned(
        message,
        role_id=PIPEHITTER_ROLE_ID,
    ) or role_mentioned(
        message,
        role_id=DISTINGUISHED_PIPEHITTER_ROLE_ID,
    )

    # Detect Watch Command role mention anywhere in the message (required for Initiation Trials).
    watch_command_mentioned = role_mentioned(
        message,
        role_id=WATCH_COMMAND_ROLE_ID,
        role_name="watch command",
    )

    # If Chapter Approved tag present, apply +1 point only when the AAR
    # is recorded on the 1st or 3rd Saturday of the month.
    try:
        if chapter_approved and getattr(message, "created_at", None):
            dt = message.created_at
            # weekday(): Monday=0 .. Sunday=6 ; Saturday == 5
            day = getattr(dt, "day", None)
            wd = getattr(dt, "weekday", lambda: None)()
            if wd == 5 and day is not None and ((1 <= day <= 7) or (15 <= day <= 21)):
                try:
                    points_for_op = int(points_for_op) + 1
                    chapter_approved_extra_point_applied = True
                except Exception:
                    pass
    except Exception:
        pass

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
                                    f"Failed to get nickname for user/ID {user.name}/{uid}"
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
        "content": content,  # Store full message content for resilience against deletion
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
        "killed_in_action": kia_count if difficulty_class == "omega_ops" else 0,
        "kia_line_present": kia_line_present,
        "points_for_op": points_for_op,
        "timestamp": message.created_at.isoformat(),
        "edited_at": message.edited_at.isoformat()
        if getattr(message, "edited_at", None)
        else None,
        "content_hash": hashlib.sha256((content or "").encode("utf-8")).hexdigest(),
        "initiation_trial": initiation_trial,
        "initiate_ids": initiate_ids,
        # Legacy field for backward compat with old records
        "initiate_id": initiate_ids[0] if initiate_ids else None,
        "watch_command_mentioned": watch_command_mentioned,
        "chapter_approved": chapter_approved,
        "chapter_approved_extra_point_applied": chapter_approved_extra_point_applied,
        # Black Laurels tracking for validation
        "black_laurels_in_difficulty": black_laurels_in_difficulty,
        "black_laurels_in_mission": black_laurels_in_mission,
        "black_laurels_mentioned_elsewhere": black_laurels_mentioned_elsewhere,
        "leviathan_protocol_in_mission": leviathan_protocol_in_mission,
        "leviathan_protocol_in_difficulty": leviathan_protocol_in_difficulty,
        # Pipehitter tracking for validation
        "pipehitter_mentioned": pipehitter_mentioned,
        # Link back to the original Discord message (if available)
        "message_url": (
            f"https://discord.com/channels/{getattr(getattr(message, 'guild', None), 'id', None)}/"
            f"{getattr(getattr(message, 'channel', None), 'id', None)}/{message.id}"
            if getattr(getattr(message, "guild", None), "id", None)
            and getattr(getattr(message, "channel", None), "id", None)
            else None
        ),
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
        # Enforce canonical mission names for non-siege ops (case-insensitive).
        # Allowable missions:
        # Inferno, Decapitation, Vox Liberatis, Reliquary, Fall of Atreus,
        # Ballistic Engine, Termination, Obelisk, Vortex, Reclamation,
        # Disruption, Exfiltration
        try:
            if not is_siege:
                allowed_missions = {
                    "inferno",
                    "decapitation",
                    "vox liberatis",
                    "reliquary",
                    "fall of atreus",
                    "ballistic engine",
                    "termination",
                    "obelisk",
                    "vortex",
                    "reclamation",
                    "disruption",
                    "exfiltration",
                }
                # Strip any trailing role/mention tokens (e.g., '<@&...>') and BOMs
                mclean = re.sub(r"<.*", "", mstr or "").strip()
                mclean = mclean.replace("\ufeff", "").strip()
                if mclean and mclean.lower() not in allowed_missions:
                    errors.append(
                        f"Mission '{mclean}' is not a recognized mission name."
                    )
        except Exception:
            pass

    # 2) Difficulty must be one of the known tags
    dlower = difficulty.lower()
    known_tags = [
        "ruthless",
        "lethal",
        "absolute",
        "omega",
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
        # Check if we're in the grace period (before Feb 20, 2026)
        is_in_grace_period = True
        try:
            timestamp_str = record.get("timestamp", "")
            if timestamp_str:
                # Parse ISO format timestamp
                message_created_at = datetime.fromisoformat(timestamp_str)
                if message_created_at >= BLACK_LAURELS_STRICT_ENFORCEMENT_DATE:
                    is_in_grace_period = False
        except Exception:
            # If we can't parse timestamp, assume grace period is still active
            pass

        # Black Laurels validation
        has_black_laurels_difficulty = "black" in dlower and "laurel" in dlower
        has_black_laurels_mission = record.get("black_laurels_in_mission", False)
        has_absolute = "absolute" in dlower
        has_omega = "omega" in dlower

        if has_black_laurels_difficulty or has_black_laurels_mission:
            # Black Laurels on Omega requires 5 brothers and 0 KIA
            # Black Laurels on Absolute requires exactly 3 brothers
            if has_omega:
                if len(brothers) != 5:
                    errors.append(
                        "@Black_Laurels on @Omega requires exactly 5 Brothers (full squad)."
                    )
                kia = record.get("killed_in_action", 0)
                if kia != 0:
                    errors.append(
                        "@Black_Laurels on @Omega requires 0 KIA (no deaths)."
                    )
            else:
                if len(brothers) != 3:
                    errors.append(
                        "@Black_Laurels requires exactly 3 Brothers (a full fireteam)."
                    )
            if is_in_grace_period:
                # GRACE PERIOD (before Feb 20, 2026): Allow Black Laurels on Mission OR Difficulty
                # Only check: must have @Absolute or @Omega when Black Laurels is present
                if not has_absolute and not has_omega:
                    errors.append(
                        "@Black_Laurels requires @Absolute or @Omega on the Difficulty line."
                    )
                # Check eligible missions (Omega allows any mission)
                if not has_omega:
                    mission_lower = (mission or "").lower().strip()
                    mission_clean = re.sub(r"<.*", "", mission_lower).strip()
                    if (
                        mission_clean
                        and mission_clean not in BLACK_LAURELS_REQUIRED_MISSIONS
                    ):
                        errors.append(
                            "@Black_Laurels may only be used on eligible missions: "
                            "Inferno, Decapitation, Vox Liberatis, Ballistic Engine, "
                            "Exfiltration, Termination, Reclamation, Disruption."
                        )
            else:
                # STRICT MODE (Feb 20, 2026+): Black Laurels ONLY on Mission line with @Absolute/@Omega on Difficulty
                if has_black_laurels_difficulty and not has_black_laurels_mission:
                    errors.append(
                        "@Black_Laurels must be placed on the Mission line only."
                    )
                if not has_absolute and not has_omega:
                    errors.append(
                        "@Black_Laurels requires @Absolute or @Omega on the Difficulty line."
                    )
                # Check eligible missions (Omega allows any mission)
                if not has_omega:
                    mission_lower = (mission or "").lower().strip()
                    mission_clean = re.sub(r"<.*", "", mission_lower).strip()
                    if (
                        mission_clean
                        and mission_clean not in BLACK_LAURELS_REQUIRED_MISSIONS
                    ):
                        errors.append(
                            "@Black_Laurels may only be used on eligible missions: "
                            "Inferno, Decapitation, Vox Liberatis, Ballistic Engine, "
                            "Exfiltration, Termination, Reclamation, Disruption."
                        )
                # Black Laurels cannot be mentioned elsewhere in strict mode
                if record.get("black_laurels_mentioned_elsewhere", False):
                    errors.append(
                        "@Black_Laurels must be placed on the Mission line, not elsewhere in the AAR."
                    )

        # Leviathan Protocol validation: must be on Mission line only
        leviathan_in_difficulty = record.get("leviathan_protocol_in_difficulty", False)
        leviathan_in_mission = record.get("leviathan_protocol_in_mission", False)
        if leviathan_in_difficulty:
            errors.append(
                "@Leviathan_Protocol must be placed on the Mission line, not the Difficulty line."
            )

        # Pipehitter validation: only allowed on eligible missions
        if record.get("pipehitter_mentioned", False):
            mission_lower = (mission or "").lower().strip()
            mission_clean = re.sub(r"<.*", "", mission_lower).strip()
            if mission_clean and mission_clean not in PIPEHITTER_ELIGIBLE_MISSIONS:
                errors.append(
                    "@Pipehitter/@Distinguished_Pipehitter may only be used on eligible missions: "
                    "Inferno, Vox Liberatis, Reliquary, Fall of Atreus, Termination, Obelisk, "
                    "Exfiltration, Vortex, Reclamation, Disruption."
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

    # 5) Brother count requirements
    # Special-case: Omega requires 2-5 brothers; all others require 2-3
    if "omega" in dlower:
        if not (2 <= len(brothers) <= 5):
            errors.append(
                "Omega difficulty requires between 2 and 5 Brothers listed under the 'Brothers:' section."
            )
        # Omega ops must have an explicit KIA line
        if not record.get("kia_line_present", False):
            errors.append(
                "Omega difficulty requires an explicit 'KIA:' line (e.g. 'KIA: 0' or 'KIA: 1')."
            )
    else:
        if len(brothers) < 2:
            errors.append(
                "At least two Brothers must be listed under the 'Brothers:' section."
            )
        elif len(brothers) > 3:
            errors.append(
                "Non-Omega operations allow a maximum of 3 Brothers (a full kill team)."
            )

    # 6) Initiation Trial placement rules (simplified)
    if record.get("initiation_trial"):
        # Check both initiate_ids (new) and initiate_id (legacy) for backward compat
        has_initiates = bool(record.get("initiate_ids")) or bool(
            record.get("initiate_id")
        )
        if not has_initiates:
            errors.append(
                "Initiation Trial present but no initiate mention found; include the person being initiated."
            )
        # Watch Command role must be mentioned for Initiation Trials
        if not record.get("watch_command_mentioned"):
            errors.append("Initiation Trial requires @Watch Command to be mentioned.")

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


# Deprecated: replaced by DataStore
def load_aar_data(filename: str):
    # Use DATASTORE for AAR_RECORDS_PATH
    if filename == AAR_RECORDS_PATH:
        # Return a dict for compatibility
        return {str(k): v for k, v in DATASTORE._records.items()}
    # Fallback to old logic for other files (should not be used)
    try:
        with open(filename, "r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return {}
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}


# Deprecated: replaced by DataStore for AAR_RECORDS_PATH
def _load_json_dict(path: str):
    if path == AAR_RECORDS_PATH:
        return {str(k): v for k, v in DATASTORE._records.items()}
    # For AAR_ERRORS_PATH and others, keep old logic
    try:
        with open(path, "r") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}


# Only used for files other than AAR_RECORDS_PATH
def _save_json_dict(path: str, data: dict):
    if path == AAR_RECORDS_PATH:
        raise RuntimeError(
            "Direct writes to AAR_RECORDS_PATH are not allowed; use DataStore.set_record."
        )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


# Deprecated: replaced by DataStore for PROCESSED_IDS_PATH
def _load_json_list(path: str):
    if path == PROCESSED_IDS_PATH:
        return list(DATASTORE._processed_ids)
    try:
        with open(path, "r") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        return []


# Only used for files other than PROCESSED_IDS_PATH
def _save_json_list(path: str, data: list):
    if path == PROCESSED_IDS_PATH:
        raise RuntimeError(
            "Direct writes to PROCESSED_IDS_PATH are not allowed; use DataStore.add_processed_id."
        )
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
    sid = str(aar_id)
    existing = data.get(sid) if isinstance(data, dict) else None
    entry = {
        "errors": errors,
        "author": _author_info_from_message(msg),
    }
    # Preserve reply_id if present so we don't lose reference to previous bot reply
    try:
        if isinstance(existing, dict) and existing.get("reply_id"):
            entry["reply_id"] = existing.get("reply_id")
    except Exception:
        pass
    data[sid] = entry
    _save_json_dict(AAR_ERRORS_PATH, data)


async def _reply_aar_rejection(msg: discord.Message, errors: list[str]):
    """Attempt to reply to the original AAR message with a concise rejection reason.
    This is best-effort: failures are logged and ignored so they don't break the
    ingest/recheck flow."""
    try:
        if not msg:
            return
        # Filter and format errors: avoid including jump URLs or huge stacks
        filtered = [e for e in errors if e and not e.startswith("Jump URL:")]
        if not filtered:
            filtered = errors[:1] if errors else ["Rejected by archive bot."]
        # Limit to a few lines for readability
        max_lines = 6
        lines = [
            "Your After-Action Report was rejected by the archive bot for the following reason(s):"
        ]
        for e in filtered[:max_lines]:
            lines.append(f"- {e}")
        content = "\n".join(lines)
        # Keep comfortably under Discord message limits
        if len(content) > 1900:
            content = content[:1900].rsplit("\n", 1)[0] + "\n…"
        # Load current stored error entry (if any) so we can deduplicate / edit
        try:
            data = _load_json_dict(AAR_ERRORS_PATH)
        except Exception:
            data = {}

        sid = str(getattr(msg, "id", ""))
        existing = data.get(sid) if isinstance(data, dict) else None
        reply_id = existing.get("reply_id") if isinstance(existing, dict) else None

        if reply_id:
            # Try to fetch the stored reply; if it exists, prefer updating it
            # to avoid duplicates. However, editing does not notify the user,
            # so if the existing reply does not mention the author, also send
            # a short ping so the author receives a notification.
            try:
                try:
                    reply_msg = await msg.channel.fetch_message(int(reply_id))
                except Exception:
                    reply_msg = None
                if reply_msg:
                    try:
                        await reply_msg.edit(content=content)
                        # Update stored errors in case they changed
                        data[sid]["errors"] = filtered[:max_lines]
                        _save_json_dict(AAR_ERRORS_PATH, data)

                        # Preserve author mention when editing existing bot replies.
                        # If the stored error entry includes an author id and the
                        # new content does not contain that mention, prefix the
                        # edited content with the mention so the visible reply
                        # continues to include the author tag.
                        try:
                            entry = data.get(sid) if isinstance(data, dict) else None
                            author_info = (
                                entry.get("author") if isinstance(entry, dict) else None
                            )
                            author_id = (
                                author_info.get("id")
                                if isinstance(author_info, dict)
                                else None
                            )
                        except Exception:
                            author_id = None
                        try:
                            if author_id and f"<@{author_id}>" not in (
                                reply_msg.content or ""
                            ):
                                try:
                                    new_content = f"<@{author_id}>\n{content}"
                                    await reply_msg.edit(content=new_content)
                                    # persist updated errors and reply id
                                    data[sid]["errors"] = filtered[:max_lines]
                                    _save_json_dict(AAR_ERRORS_PATH, data)
                                except Exception:
                                    pass
                            else:
                                # no author to preserve or already present; nothing more to do
                                pass
                        except Exception:
                            pass

                        return
                    except Exception:
                        # If edit fails, continue to attempt sending a new reply
                        pass
            except Exception:
                # any unexpected failure - continue to send a new reply
                pass

        # No existing reply found or edit failed: send a new reply and record its id
        # Before sending a new reply, scan recent channel messages to see if the
        # bot already posted a reply to this AAR (possible if reply_id was not
        # recorded or is stale). If found, edit that message instead of sending
        # a new one to avoid duplicates.
        try:
            existing_reply = None
            try:
                async for recent in msg.channel.history(limit=64):
                    try:
                        ref = getattr(recent, "reference", None)
                        if not ref:
                            continue
                        if getattr(ref, "message_id", None) == getattr(msg, "id", None):
                            if getattr(recent.author, "id", None) == getattr(
                                bot.user, "id", None
                            ):
                                existing_reply = recent
                                break
                    except Exception:
                        continue
            except Exception:
                existing_reply = None
            if existing_reply:
                try:
                    await existing_reply.edit(content=content)
                    # Update stored reply_id for this AAR
                    sid = str(getattr(msg, "id", ""))
                    ent = data.get(sid) or {}
                    ent["errors"] = filtered[:max_lines]
                    ent["author"] = _author_info_from_message(msg)
                    try:
                        ent["reply_id"] = str(getattr(existing_reply, "id", ""))
                    except Exception:
                        ent["reply_id"] = None
                    data[sid] = ent
                    try:
                        _save_json_dict(AAR_ERRORS_PATH, data)
                    except Exception:
                        pass
                    # Preserve author mention when editing existing bot replies.
                    try:
                        sid = str(getattr(msg, "id", ""))
                        entry = data.get(sid) if isinstance(data, dict) else None
                        author_info = (
                            entry.get("author") if isinstance(entry, dict) else None
                        )
                        author_id = (
                            author_info.get("id")
                            if isinstance(author_info, dict)
                            else None
                        )
                    except Exception:
                        author_id = None
                    try:
                        if author_id and f"<@{author_id}>" not in (
                            existing_reply.content or ""
                        ):
                            try:
                                new_content = f"<@{author_id}>\n{content}"
                                await existing_reply.edit(content=new_content)
                                sid = str(getattr(msg, "id", ""))
                                ent = data.get(sid) or {}
                                ent["errors"] = filtered[:max_lines]
                                ent["author"] = _author_info_from_message(msg)
                                try:
                                    ent["reply_id"] = str(
                                        getattr(existing_reply, "id", "")
                                    )
                                except Exception:
                                    ent["reply_id"] = None
                                data[sid] = ent
                                try:
                                    _save_json_dict(AAR_ERRORS_PATH, data)
                                except Exception:
                                    pass
                            except Exception:
                                pass
                        else:
                            # Either no author info or mention already present; nothing to do
                            pass
                    except Exception:
                        pass
                    return
                except Exception:
                    # fall through to sending a new reply
                    pass

            sent = None
            # Send a new reply and ensure the author is mentioned so they
            # receive a notification. Use an explicit mention prefix and
            # set allowed_mentions to permit user pings (safer than relying
            # on `mention_author=True` which can be affected by global
            # allowed-mentions settings).
            try:
                author_id = getattr(msg.author, "id", "")
                mention_prefix = f"<@{author_id}>\n" if author_id else ""
                sent = await msg.reply(
                    mention_prefix + content,
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except Exception:
                # Last-resort fallback: try replying without explicit allowed_mentions
                try:
                    sent = await msg.reply(
                        f"<@{getattr(msg.author, 'id', '')}>\n{content}"
                    )
                except Exception:
                    sent = None
            if sent and isinstance(data, dict):
                sid = str(getattr(msg, "id", ""))
                # Ensure there's an entry for this aar in the errors file
                ent = data.get(sid) or {}
                ent["errors"] = filtered[:max_lines]
                ent["author"] = _author_info_from_message(msg)
                try:
                    ent["reply_id"] = str(getattr(sent, "id", ""))
                except Exception:
                    ent["reply_id"] = None
                data[sid] = ent
                try:
                    _save_json_dict(AAR_ERRORS_PATH, data)
                except Exception:
                    pass
        except Exception as e:
            try:
                logger.debug(f"Failed to reply to AAR {getattr(msg, 'id', None)}: {e}")
            except Exception:
                pass
    except Exception as e:
        try:
            logger.debug(f"Failed to reply to AAR {getattr(msg, 'id', None)}: {e}")
        except Exception:
            pass


def _snowflake_to_datetime(snowflake_id: int) -> datetime:
    """Extract the creation datetime from a Discord snowflake ID."""
    # Discord epoch: January 1, 2015 00:00:00 UTC
    discord_epoch = 1420070400000
    timestamp_ms = (snowflake_id >> 22) + discord_epoch
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)


def summarize_error_authors(max_age_weeks: int = 4):
    """Return a tuple: (list of author summaries for recent errors, stale_count).

    Recent errors are those from the last max_age_weeks.
    Stale errors are older than max_age_weeks.

    Each author entry: {"id": str, "username": str|None, "nickname": str|None, "count": int}
    """
    data = _load_json_dict(AAR_ERRORS_PATH)
    by_author: dict[str, dict] = {}
    stale_count = 0
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=max_age_weeks)

    for aar_id_str, entry in data.items():
        # Check if this error is stale (older than cutoff)
        try:
            aar_id = int(aar_id_str)
            msg_time = _snowflake_to_datetime(aar_id)
            if msg_time < cutoff:
                stale_count += 1
                continue  # Skip stale entries from author breakdown
        except (ValueError, TypeError):
            pass  # If we can't parse ID, include it in recent

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
    return summaries, stale_count


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


# Use DataStore for processed IDs
def load_processed_ids():
    return set(DATASTORE._processed_ids)


# Use DataStore for processed IDs (async)
async def add_processed_id(aar_id: int):
    await DATASTORE.add_processed_id(aar_id)


# Use DataStore for AAR records and processed IDs (async)
async def save_aar_record(record: dict):
    key = str(record["aar_id"])
    await DATASTORE.set_record(key, record)
    await DATASTORE.add_processed_id(key)
    # Add armory points to the community forge pool
    armory_pts = record.get("armory_challenge_points", 0) or 0
    if armory_pts > 0:
        await _increment_forge_pool_balance(armory_pts)


# Use DataStore for processed IDs
def has_been_processed(aar_id: int):
    return DATASTORE.is_processed(aar_id)


# Use DataStore user_stats_cache for user stats
def compute_stats_for_user(user_id: str):
    return DATASTORE.get_user_stats(user_id)


def _count_inductions_from_records(user_id: str, records) -> int:
    """Compute induction count for *user_id* from an iterable of AAR record dicts.

    Rules:
      - Omega operation: 1 trial per inductee = 1 complete induction.
      - Siege initiation: 15 waves per inductee = 1 induction.
      - Operation initiation: 3 trials per inductee = 1 induction.
      - Each inductee in an AAR counts separately.
      - The user's own induction (if they appear as an inductee) is excluded.
    """
    ops_trials = 0
    siege_waves = 0
    omega_inductions = 0
    for rec in records:
        try:
            brother_ids = rec.get("brother_ids") or []
            if str(user_id) not in brother_ids:
                continue
            if not bool(rec.get("initiation_trial")):
                continue
            # Count inductees (excluding self) - each inductee counts separately
            initiate_ids_list = rec.get("initiate_ids") or []
            legacy_initiate_id = rec.get("initiate_id")
            # Build full list of inductees from both new and legacy fields
            all_inductees = list(initiate_ids_list)
            if legacy_initiate_id and legacy_initiate_id not in all_inductees:
                all_inductees.append(legacy_initiate_id)
            # Remove self from count
            inductee_count = sum(1 for uid in all_inductees if uid != str(user_id))
            if inductee_count == 0:
                continue
            dclass = (rec.get("difficulty_class") or "").lower()
            if "omega" in dclass:
                # Omega: each inductee counts as a full induction (1 trial = 1 induction)
                omega_inductions += inductee_count
            elif "siege" in dclass:
                # Siege: add waves * inductee_count (15 waves per inductee = 1 induction)
                rec_waves = rec.get("waves") or 0
                try:
                    rec_waves = int(rec_waves)
                except Exception:
                    rec_waves = 0
                siege_waves += rec_waves * inductee_count
            else:
                # Ops: each inductee counts as 1 trial (3 trials = 1 induction)
                ops_trials += inductee_count
        except Exception:
            # Be resilient to malformed records
            pass
    return int(omega_inductions + (siege_waves // 15) + (ops_trials // 3))


def _induction_count_for_user(user_id: str) -> int:
    """Compute total inductions a brother participated in across all AARs."""
    try:
        data = load_aar_data(AAR_RECORDS_PATH)
    except Exception:
        data = {}
    return _count_inductions_from_records(user_id, data.values())


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


def _get_eligible_combat_bonds_ids(guild: discord.Guild, user_ids: List[str]) -> set:
    """Return the set of user IDs that have at least Watch Brother rank.

    Eligible members must have one of the roles in RANK_ROLES_PRIORITY or
    the Watch Sister alias. Members without a rank role are excluded from
    combat bond calculations.

    NOTE: This helper uses ``guild.get_member()``, which only consults the
    local member cache. Callers should ensure that the guild's member cache
    is fully populated (for example via member intents and chunking) before
    relying on this filter, otherwise eligible members that are not cached
    may be incorrectly excluded from combat bond calculations.
    """
    # Detect obviously incomplete caches and emit a warning so operators can
    # address it at the configuration/call-site level.
    try:
        total_members = getattr(guild, "member_count", None)
        cached_members = len(getattr(guild, "members", []))
        if (
            isinstance(total_members, int)
            and total_members > 0
            and cached_members < total_members
        ):
            logging.getLogger(__name__).warning(
                "Guild member cache appears incomplete for %s "
                "(cached=%d, total=%d); _get_eligible_combat_bonds_ids "
                "relies on the cache and may under-count eligible members.",
                getattr(guild, "name", guild.id),
                cached_members,
                total_members,
            )
    except Exception:
        # If anything goes wrong while checking cache completeness, fall back
        # silently to existing behavior.
        pass

    eligible: set = set()
    # Build set of qualifying role names (all rank roles + Watch Sister alias)
    qualifying_roles = set(RANK_ROLES_PRIORITY) | {"Watch Sister"}
    for uid in user_ids:
        try:
            member = guild.get_member(int(uid))
            if member is None:
                continue
            member_role_names = {
                getattr(r, "name", "") for r in getattr(member, "roles", [])
            }
            if any(r in member_role_names for r in qualifying_roles):
                eligible.add(str(uid))
        except Exception:
            continue
    return eligible


def _filter_pair_counts_by_eligible(
    pair_counts: Dict[Tuple[str, str], int], eligible_ids: set
) -> Dict[Tuple[str, str], int]:
    """Filter pair_counts to only include pairs where both members are eligible."""
    return {
        k: v
        for k, v in pair_counts.items()
        if k[0] in eligible_ids and k[1] in eligible_ids
    }


def _build_pair_counts(missions):
    """Compute weighted pair counts from missions.

    Instead of simple co-occurrence counts, weight each pair by the AAR
    points the two brothers earned together in a mission. Per-member AAR
    points are computed similarly to `compute_stats_for_user_in_records`:
    - For non-siege ops: use `points_for_op` (shared per-member value in record).
    - For sieges: compute per-brother waves contribution (3 or 4 points per
      5 waves depending on siege difficulty) using `brother_waves` or the
      global `waves` value when per-brother not present.

    Returns a mapping (uid_a, uid_b) -> total_weight (int).
    """
    pair_counts: Dict[Tuple[str, str], int] = {}
    for rec in missions:
        bros: List[str] = [str(b) for b in (rec.get("brother_ids") or [])]
        if not bros:
            continue
        # compute per-member AAR points for this mission
        per_member_points: Dict[str, int] = {}
        dlower = (rec.get("difficulty") or "").lower()
        is_siege = ("normal-siege" in dlower) or ("hard-siege" in dlower)
        if is_siege:
            bw = rec.get("brother_waves") or {}
            for uid in bros:
                try:
                    my_waves = int(bw.get(uid, 0) or 0)
                except Exception:
                    try:
                        my_waves = int(rec.get("waves") or 0)
                    except Exception:
                        my_waves = 0
                if "normal-siege" in dlower:
                    points = 3 * (my_waves // 5)
                else:
                    points = 4 * (my_waves // 5)
                per_member_points[uid] = int(points)
        else:
            # non-siege: use the record's points_for_op as the per-member contribution
            try:
                p = int(rec.get("points_for_op", 0) or 0)
            except Exception:
                p = 0
            for uid in bros:
                per_member_points[uid] = p

        # unique per mission to avoid duplicate counting same brother twice
        unique_bros = sorted(set(bros))
        for a, b in itertools.combinations(unique_bros, 2):
            key = (a, b) if a < b else (b, a)
            # weight this pair by the sum of their per-member points in this mission
            wa = int(per_member_points.get(a, 0))
            wb = int(per_member_points.get(b, 0))
            pair_weight = wa + wb
            # skip adding zero-weight co-occurrences (no AAR points earned)
            if pair_weight <= 0:
                continue
            pair_counts[key] = pair_counts.get(key, 0) + int(pair_weight)
    return pair_counts


def _build_triple_bonds(pair_counts: Dict[Tuple[str, str], int], brothers: List[str]):
    """Create 3-brother bonds and score them using a balance-sensitive metric.
    Base score: 3 × HarmonicMean(C_ab, C_ac, C_bc), which equals the sum when
    all three pair counts are equal and down-weights imbalanced triads.
    Dominance penalty: down-weight when one pair dominates the triad.

    Config knobs (CONFIG.combat_bonds):
      - dominance_alpha (float, default 0.5): strength of dominance penalty [0..1]
      - min_pair (int, default 1): minimum pair count required to qualify
      - min_balance_ratio (float, default 0.0): require min(C)/max(C) >= ratio (0 disables)

    Returns list of ((id1, id2, id3), score:int) sorted by score desc.
    """
    # Load config with safe defaults
    try:
        _cb = CONFIG.get("combat_bonds") or {}
    except Exception:
        _cb = {}
    try:
        dominance_alpha = float(_cb.get("dominance_alpha", 0.5))
    except Exception:
        dominance_alpha = 0.5
    try:
        min_pair = max(1, int(_cb.get("min_pair", 1)))
    except Exception:
        min_pair = 1
    try:
        min_balance_ratio = float(_cb.get("min_balance_ratio", 0.0))
    except Exception:
        min_balance_ratio = 0.0

    triples: List[Tuple[Tuple[str, str, str], int]] = []
    uniq_bros = sorted(set(brothers))
    for x, y, z in itertools.combinations(uniq_bros, 3):
        pairs = [tuple(sorted((x, y))), tuple(sorted((x, z))), tuple(sorted((y, z)))]
        # Fetch pair counts
        # Treat pair_counts as weighted values (floats allowed); use float for
        # intermediate math but keep integer-like semantics for gating.
        c = [float(pair_counts.get(p, 0) or 0.0) for p in pairs]
        c_ab, c_ac, c_bc = c
        # Eligibility: all pairs must meet minimum count
        if (
            (c_ab < float(min_pair))
            or (c_ac < float(min_pair))
            or (c_bc < float(min_pair))
        ):
            continue
        # Optional balance gate: require min/max ratio
        try:
            c_min = min(c)
            c_max = max(c)
            balance_ratio = (float(c_min) / float(c_max)) if c_max > 0 else 0.0
        except Exception:
            balance_ratio = 0.0
        if (min_balance_ratio > 0.0) and (balance_ratio < min_balance_ratio):
            continue

        # Base score via Harmonic Mean (scaled by 3 to match prior sum scale when balanced)
        # HM = 3 / (1/a + 1/b + 1/c) for positive a,b,c
        denom = 0.0
        try:
            denom = (1.0 / float(c_ab)) + (1.0 / float(c_ac)) + (1.0 / float(c_bc))
        except Exception:
            denom = 0.0
        base_hm = (3.0 / denom) if denom > 0.0 else 0.0
        base_score = 3.0 * base_hm

        # Dominance penalty: normalize excess dominance beyond ideal 1/3 share
        total = float(c_ab + c_ac + c_bc)
        dom = (max(c_ab, c_ac, c_bc) / total) if total > 0.0 else 0.0
        excess_norm = 0.0
        try:
            ideal = 1.0 / 3.0
            span = 2.0 / 3.0
            excess_norm = max(0.0, (dom - ideal) / span)
        except Exception:
            excess_norm = max(0.0, dom - (1.0 / 3.0))
        penalty_factor = max(0.0, 1.0 - (dominance_alpha * excess_norm))

        final_score = int(round(base_score * penalty_factor))
        triples.append(((x, y, z), final_score))
    triples.sort(key=lambda t: t[1], reverse=True)
    return triples


def _build_group_bonds(
    pair_counts: Dict[Tuple[str, str], int],
    brothers: List[str],
    sizes: Optional[List[int]] = None,
):
    """Create group bonds for sizes in `sizes` (default 3..5) and score them.

    Scoring approach (generalized from triads):
      - Collect all internal pair weights for the group (sum of per-mission AAR points).
      - Compute Harmonic Mean across those pair weights, scaled by group size.
      - Apply the same dominance penalty based on the largest pair share.

    Optimization: Pre-filter brothers to only those with meaningful connections
    to reduce combinatorial explosion.

    Returns list of ((id1,...,idN), score:int) sorted by score desc.
    """
    try:
        _cb = CONFIG.get("combat_bonds") or {}
    except Exception:
        _cb = {}
    try:
        dominance_alpha = float(_cb.get("dominance_alpha", 0.5))
    except Exception:
        dominance_alpha = 0.5
    try:
        min_pair = max(1, int(_cb.get("min_pair", 1)))
    except Exception:
        min_pair = 1
    try:
        min_balance_ratio = float(_cb.get("min_balance_ratio", 0.0))
    except Exception:
        min_balance_ratio = 0.0

    if sizes is None:
        sizes = [3, 4, 5]

    groups: List[Tuple[Tuple[str, ...], int]] = []
    # Ensure brother identifiers are strings to avoid type-comparison issues
    uniq_bros = sorted(set(str(x) for x in brothers))

    # OPTIMIZATION: Pre-filter to brothers who have at least `min_pair` connections
    # with at least one other brother. Also limit to top N most-connected brothers
    # to avoid combinatorial explosion with large member counts.
    brother_connection_scores: Dict[str, int] = {}
    for (a, b), weight in pair_counts.items():
        if weight >= min_pair:
            brother_connection_scores[a] = brother_connection_scores.get(a, 0) + 1
            brother_connection_scores[b] = brother_connection_scores.get(b, 0) + 1

    # Only consider brothers who have at least 2 connections (required for triads)
    connected_bros = {
        b for b, conn_count in brother_connection_scores.items() if conn_count >= 2
    }
    uniq_bros = sorted([b for b in uniq_bros if b in connected_bros])

    # Further limit to top 50 most-connected brothers if the set is still large
    # This prevents O(n^5) blowup while keeping the most relevant bonds
    max_brothers_for_combos = 50
    if len(uniq_bros) > max_brothers_for_combos:
        sorted_by_connections = sorted(
            uniq_bros, key=lambda b: brother_connection_scores.get(b, 0), reverse=True
        )
        uniq_bros = sorted_by_connections[:max_brothers_for_combos]

    for n in sizes:
        if n < 2:
            continue
        for combo in itertools.combinations(uniq_bros, n):
            # build all internal pair keys
            pair_keys: List[Tuple[str, str]] = []
            for a, b in itertools.combinations(combo, 2):
                # Coerce to str and sort to avoid mixed-type compare errors
                pair_keys.append(tuple(sorted((str(a), str(b)))))
            # gather counts (weights)
            c_vals: List[float] = [
                float(pair_counts.get(k, 0) or 0.0) for k in pair_keys
            ]
            if not c_vals:
                continue
            # Eligibility: each internal pair must meet minimum
            if any(v < float(min_pair) for v in c_vals):
                continue
            # Optional balance gate
            try:
                c_min = min(c_vals)
                c_max = max(c_vals)
                balance_ratio = (float(c_min) / float(c_max)) if c_max > 0 else 0.0
            except Exception:
                balance_ratio = 0.0
            if (min_balance_ratio > 0.0) and (balance_ratio < min_balance_ratio):
                continue

            # Harmonic mean across M pairs: HM = M / sum(1/c_i)
            denom = 0.0
            try:
                denom = sum((1.0 / float(v)) for v in c_vals if float(v) > 0.0)
            except Exception:
                denom = 0.0
            base_hm = (len(c_vals) / denom) if denom > 0.0 else 0.0
            # scale by group size to keep magnitude comparable to previous triad logic
            base_score = float(n) * base_hm

            total = float(sum(c_vals))
            dom = (max(c_vals) / total) if total > 0.0 else 0.0
            excess_norm = 0.0
            try:
                ideal = 1.0 / float(len(c_vals))
                span = 1.0 - ideal
                excess_norm = max(0.0, (dom - ideal) / span) if span > 0 else 0.0
            except Exception:
                excess_norm = max(0.0, dom - (1.0 / float(len(c_vals))))
            penalty_factor = max(0.0, 1.0 - (dominance_alpha * excess_norm))

            final_score = int(round(base_score * penalty_factor))
            groups.append((tuple(combo), final_score))

    groups.sort(key=lambda t: t[1], reverse=True)
    return groups


def _build_spread_counts(
    pair_counts: Dict[Tuple[str, str], int], active_count: Optional[int] = None
):
    """Compute normalized spread per brother from pair counts.
    Breadth/evenness via inverse Simpson effective partners; depth is bounded to
    avoid inflating scores by grinding with a narrow partner set.

    Definitions:
    - Build per-partner frequencies from pair_counts.
    - T = sum of partner frequencies for the user; p_i = freq_i / T.
    - effective_partners = 1 / sum(p_i^2).  # inverse Simpson
    - bounded_total = sum(min(freq_i, per_partner_cap)) over partners
    - depth_factor = bounded_total ** depth_exponent (default 0.5 == sqrt)
    - spread = round(effective_partners * depth_factor)

    Optional config knobs (with safe defaults) from CONFIG.combat_bonds:
      per_partner_cap (int, default 5), depth_exponent (float, default 0.5)
    """
    # Configurable knobs
    try:
        _cb = CONFIG.get("combat_bonds") or {}
    except Exception:
        _cb = {}
    try:
        per_partner_cap = max(1, int(_cb.get("per_partner_cap", 5)))
    except Exception:
        per_partner_cap = 5
    try:
        depth_exponent = float(_cb.get("depth_exponent", 0.5))
    except Exception:
        depth_exponent = 0.5

    # Build adjacency frequencies per user
    freqs: Dict[str, Dict[str, int]] = {}
    for (a, b), cnt in pair_counts.items():
        if cnt <= 0:
            continue
        if a not in freqs:
            freqs[a] = {}
        if b not in freqs:
            freqs[b] = {}
        freqs[a][b] = freqs[a].get(b, 0) + cnt
        freqs[b][a] = freqs[b].get(a, 0) + cnt

    # Raw spread values (current behavior), and per-user interaction totals
    raw_spreads: Dict[str, float] = {}
    interactions: Dict[str, int] = {}
    for uid, adj in freqs.items():
        if not adj:
            raw_spreads[uid] = 0.0
            interactions[uid] = 0
            continue
        total = sum(max(0, v) for v in adj.values())
        if total <= 0:
            raw_spreads[uid] = 0.0
            interactions[uid] = 0
            continue
        # Breadth/evenness via inverse Simpson
        sum_sq = 0.0
        for v in adj.values():
            p = v / total
            sum_sq += p * p
        effective = (1.0 / sum_sq) if sum_sq > 0.0 else 0.0
        # Bounded depth to avoid volume inflation on a narrow partner set
        bounded_total = sum(min(max(0, v), per_partner_cap) for v in adj.values())
        depth_factor = (bounded_total**depth_exponent) if bounded_total > 0 else 0.0
        spread_val = effective * depth_factor
        try:
            raw_spreads[uid] = float(spread_val)
        except Exception:
            raw_spreads[uid] = 0.0
        # interactions = total partner frequency (depth before per-partner cap)
        try:
            interactions[uid] = int(total)
        except Exception:
            interactions[uid] = 0

    # Determine active count (number of active members in the window)
    try:
        active = (
            int(active_count)
            if (active_count and int(active_count) > 0)
            else max(1, len(freqs))
        )
    except Exception:
        active = max(1, len(freqs))

    # Normalized per-active-member value
    normalized_map: Dict[str, float] = {}
    for uid, raw in raw_spreads.items():
        normalized_map[uid] = (raw / float(active)) if active > 0 else 0.0

    # Compute percentile rank (0-100) from normalized_map
    percentiles: Dict[str, int] = {}
    try:
        items = sorted(((u, v) for u, v in normalized_map.items()), key=lambda x: x[1])
        vals = [v for _, v in items]
        n = len(vals)
        for idx, (u, v) in enumerate(items):
            if n <= 1:
                pct = 100
            else:
                pct = int(round(100.0 * (idx / float(n - 1))))
            percentiles[u] = pct
    except Exception:
        for u in normalized_map.keys():
            percentiles[u] = 0

    # Minimum-interaction guard (configurable)
    try:
        _cb = CONFIG.get("combat_bonds") or {}
    except Exception:
        _cb = {}
    try:
        min_interactions = max(1, int(_cb.get("min_interactions", 8)))
    except Exception:
        min_interactions = 8

    # Build final mapping preserving helpful fields for display/decisions
    spreads_out: Dict[str, Dict[str, object]] = {}
    for uid in raw_spreads.keys():
        spreads_out[uid] = {
            "raw": int(round(raw_spreads.get(uid, 0.0))),
            "normalized": float(normalized_map.get(uid, 0.0)),
            "percentile": int(percentiles.get(uid, 0)),
            "interactions": int(interactions.get(uid, 0)),
            "eligible": int(interactions.get(uid, 0)) >= min_interactions,
        }

    return spreads_out


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


def _select_personal_pair_bonds(
    pair_counts: Dict[Tuple[str, str], int], target_id: str, max_n: int = 5
) -> List[Tuple[str, int]]:
    """Return up to max_n pairwise bonds for a specific brother.

    Returns a list of (partner_uid, score) tuples sorted by score descending.
    """
    pairs: List[Tuple[str, int]] = []
    for (a, b), score in pair_counts.items():
        if a == target_id:
            pairs.append((b, score))
        elif b == target_id:
            pairs.append((a, score))
    pairs.sort(key=lambda x: x[1], reverse=True)
    return pairs[:max_n]


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


def _percentile(sorted_vals: List[int], p: float) -> int:
    if not sorted_vals:
        return 0
    n = len(sorted_vals)
    idx = int(max(0, min(n - 1, round(p * (n - 1)))))
    return sorted_vals[idx]


def _compute_bond_cutoffs(scores: List[int]) -> Optional[Dict[str, int]]:
    if not scores or len(scores) < 5:
        return None
    s = sorted(scores)
    q20 = _percentile(s, 0.20)
    q40 = _percentile(s, 0.40)
    q60 = _percentile(s, 0.60)
    q80 = _percentile(s, 0.80)
    return {"q20": q20, "q40": q40, "q60": q60, "q80": q80}


def _bond_tier_dynamic(score: int, cutoffs: Optional[Dict[str, int]]):
    if not cutoffs:
        return _bond_tier(score)
    if score <= cutoffs["q20"]:
        return "FRAGILE"
    if score <= cutoffs["q40"]:
        return "FORMING"
    if score <= cutoffs["q60"]:
        return "RELIABLE"
    if score <= cutoffs["q80"]:
        return "STALWART"
    return "INDOMITABLE"


async def _resolve_home_chapters(
    guild: Optional[discord.Guild], user_ids: List[str], limit: int = 500
):
    """Resolve home chapters for given users by consulting their Guild roles.

    Logic: for each user id, attempt to get the corresponding Member from
    `guild`. Inspect the member's role names and match any canonical
    `HOME_CHAPTERS` entry case-insensitively (substring match). The first
    matching canonical name is returned. If no match is found or the member
    cannot be resolved, the value 'REDACTED' is used as a fallback.

    Returns a mapping of user_id -> chapter string.
    """
    home_chapters = HOME_CHAPTERS
    chapters: Dict[str, str] = {}
    if not guild:
        return chapters

    # Iterate requested users and resolve via member roles
    # Match strategy: exact (case-insensitive) equality between a member's
    # individual role names and the canonical `HOME_CHAPTERS` entries.
    # If no exact match is found, return an empty string so callers may skip
    # attribution for that user.
    for uid in user_ids:
        chapter = ""
        try:
            member = guild.get_member(int(uid))
        except Exception:
            member = None
        # If not cached, try fetching from API
        if member is None:
            try:
                member = await guild.fetch_member(int(uid))
            except Exception:
                member = None
        if member:
            try:
                # Collect member role names and compare for exact (case-insensitive) equality
                member_role_names = {
                    (getattr(r, "name", "") or "").strip()
                    for r in member.roles
                    if getattr(r, "name", None)
                }
                match = next(
                    (
                        hc
                        for hc in home_chapters
                        if any(rn.lower() == hc.lower() for rn in member_role_names)
                    ),
                    None,
                )
                if match:
                    chapter = match
                else:
                    chapter = ""
            except Exception:
                chapter = "chapter not found"
        chapters[str(uid)] = chapter
    return chapters


def _format_bonds_for_discord(
    bonds: List[Tuple[Tuple[str, str, str], int]],
    guild: Optional[discord.Guild] = None,
    window_span: int = 100,
    chapters: Optional[Dict[str, str]] = None,
    window_days: Optional[int] = None,
    spreads: Optional[Dict[str, int]] = None,
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
    lines.append("  SUB-ROUTINE: BATTLE-LITANY INDEX")
    lines.append(
        "=============================================================================="
    )
    if window_days is not None:
        lines.append(f"  Auspex Window: Last {window_days} day(s)")
    else:
        lines.append(f"  Auspex Window: Last {window_span} sanctioned engagement(s)")
    # Veneration key (compact) — per-bond output will include only the tier label
    lines.append(
        "  Veneration Key: FRAGILE | FORMING | RELIABLE | STALWART | INDOMITABLE\n"
    )
    scores_for_cutoffs = [score for _tri, score in bonds]
    cutoffs = _compute_bond_cutoffs(scores_for_cutoffs)
    ordinal_labels = {
        1: "PRIMARY",
        2: "SECONDARY",
        3: "TERTIARY",
        4: "QUATERNARY",
        5: "QUINARY",
    }

    # Build bond blocks independently so we can drop specific ordinal blocks
    bond_blocks: List[Tuple[int, str]] = []
    for idx, (triple, score) in enumerate(bonds, start=1):
        tier = _bond_tier_dynamic(score, cutoffs)
        members_in_group = list(triple)

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

            # Resolve chapter from member roles by matching against HOME_CHAPTERS
            chap = None
            if member:
                try:
                    member_role_names = {
                        (getattr(r, "name", "") or "").strip()
                        for r in member.roles
                        if getattr(r, "name", None)
                    }
                    match = next(
                        (
                            hc
                            for hc in HOME_CHAPTERS
                            if any(rn.lower() == hc.lower() for rn in member_role_names)
                        ),
                        None,
                    )
                    if match:
                        chap = match
                except Exception:
                    chap = None
            if not chap:
                chap = (chapters or {}).get(uid)
            chap_str = chap if chap else "REDACTED"
            spread_val = (spreads or {}).get(uid)
            spread_str = ""
            try:
                if isinstance(spread_val, dict):
                    norm = float(spread_val.get("normalized", 0.0))
                    pct = int(spread_val.get("percentile", 0))
                    eligible = bool(spread_val.get("eligible", True))
                    spread_str = f" • Spread {norm:.2f} (pct {pct}%)"
                    if not eligible:
                        spread_str += " [insufficient interactions]"
                elif spread_val is not None:
                    spread_str = f" • Spread {spread_val}"
            except Exception:
                spread_str = f" • Spread {spread_val}"
            return f"{name} [{chap_str}]{spread_str}"

        title = ordinal_labels.get(idx, "BOND")
        b_lines: List[str] = []
        b_lines.append(f"    ++ {title} BOND ({len(members_in_group)}-man) ++")
        for uid in members_in_group:
            b_lines.append(f"    {_member_label(uid)}")
        b_lines.append(f"    Tier: {tier}")
        b_lines.append("")
        bond_blocks.append((idx, "\n".join(b_lines)))

    # Assemble full text, dropping QUINARY (5) then QUATERNARY (4) if over limit
    header = "\n".join(lines)
    footer = (
        "\n"
        + "=============================================================================="
        + "\n\u001b[0m```"
    )

    def assemble(blocks: List[Tuple[int, str]]):
        return header + "\n" + "\n".join(b for _i, b in blocks) + footer

    full_text = assemble(bond_blocks)
    if len(full_text) > 2000:
        # Drop QUINARY (ordinal 5)
        filtered = [b for b in bond_blocks if b[0] != 5]
        full_text = assemble(filtered)
    if len(full_text) > 2000:
        # Drop QUATERNARY (ordinal 4)
        filtered = [b for b in bond_blocks if b[0] not in (5, 4)]
        full_text = assemble(filtered)
    return full_text
    lines.append(
        "=============================================================================="
    )
    lines.append("\u001b[0m```")
    return "\n".join(lines)


def _format_bonds_embed(
    bonds: List[Tuple[Tuple[str, str, str], int]],
    guild: Optional[discord.Guild] = None,
    window_span: int = 100,
    chapters: Optional[Dict[str, str]] = None,
    window_days: Optional[int] = None,
):
    """Render Combat Bonds as a Discord Embed (jericho style).
    Shows up to 5 group bonds, with tier labels and member lines.
    """
    embed = discord.Embed(
        title="᛭⋅ COMBAT BONDS ⋅᛭",
        description="*⌾ Watch Fortress Jericho ⌾*",
        color=0x2ECC71,
    )
    if not bonds:
        embed.add_field(
            name="▸ Status",
            value="No qualifying Combat Bonds found in the current window.",
            inline=False,
        )
        return embed

    # Auspex window info
    window_text = (
        f"Last {window_days} day(s)"
        if window_days is not None
        else f"Last {window_span} engagements"
    )
    embed.add_field(name="▸ Auspex Window", value=window_text, inline=True)
    embed.add_field(
        name="▸ Veneration Key",
        value="FRAGILE | FORMING | RELIABLE | STALWART | INDOMITABLE",
        inline=True,
    )

    scores_for_cutoffs = [score for _tri, score in bonds]
    cutoffs = _compute_bond_cutoffs(scores_for_cutoffs)

    def _member_label(uid: str) -> str:
        # Use shared helper for consistent formatting across honours/bonds displays
        return _format_member_styled(guild, uid, chapters, include_chapter=True)

    # Group bonds by tier
    tier_groups: Dict[str, List[Tuple[str, ...]]] = {}
    for triple, score in bonds[:5]:  # Limit to top 5
        tier = _bond_tier_dynamic(score, cutoffs)
        if tier not in tier_groups:
            tier_groups[tier] = []
        tier_groups[tier].append(triple)

    # Order tiers from strongest to weakest
    tier_order = ["INDOMITABLE", "STALWART", "RELIABLE", "FORMING", "FRAGILE"]
    for tier in tier_order:
        if tier not in tier_groups:
            continue
        groups = tier_groups[tier]
        # Build field value with all groups of this tier
        lines = []
        for group in groups:
            members_in_group = list(group)
            group_lines = [f"• {_member_label(uid)}" for uid in members_in_group]
            lines.append("\n".join(group_lines))
        value = "\n\n".join(lines)  # Separate groups with blank line
        embed.add_field(
            name=f"▸ {tier}",
            value=value,
            inline=False,
        )

    embed.set_footer(
        text="᛭⋅ These Combat Bonds may be invoked by decree of Watch Command. ⋅᛭"
    )
    return embed


def _format_personal_bonds_jericho_embed(
    pair_bonds: List[Tuple[str, int]],
    target_member: discord.Member,
    guild: Optional[discord.Guild] = None,
    window_days: Optional[int] = None,
    chapters: Optional[Dict[str, str]] = None,
):
    """Render personal pairwise Combat Bonds as a jericho-style embed.

    Shows the target brother's top 5 pairwise bonds with other brothers.
    """
    # Strip rank/studs from target name
    target_display = target_member.nick or target_member.display_name
    target_name = target_display.replace("●", "").replace("⚬", "").strip()

    # Get target's rank and chapter
    target_rank = None
    target_chapter = None
    try:
        member_role_names = {
            (getattr(r, "name", "") or "").strip()
            for r in target_member.roles
            if getattr(r, "name", None)
        }
        for rp in RANK_ROLES_PRIORITY:
            if rp in member_role_names:
                target_rank = rp
                break
        target_chapter = next(
            (
                hc
                for hc in HOME_CHAPTERS
                if any(rn.lower() == hc.lower() for rn in member_role_names)
            ),
            None,
        )
    except Exception:
        pass

    # Strip rank prefix from name
    if target_rank:
        for rp in RANK_ROLES_PRIORITY:
            if target_name.lower().startswith(rp.lower()):
                target_name = target_name[len(rp) :].lstrip()
                break

    # Get emojis
    rank_emoji = _get_rank_emoji(guild, target_rank) if guild and target_rank else ""
    chapter_emoji = (
        _get_emoji_by_name(guild, target_chapter) if guild and target_chapter else ""
    )

    embed = discord.Embed(
        title="᛭⋅ COMBAT BONDS ⋅᛭",
        description="*⌾ Watch Fortress Jericho ⌾*",
        color=0x2ECC71,
    )

    # Bearer field with rank emoji + stripped name + chapter emoji
    bearer_parts = []
    if rank_emoji:
        bearer_parts.append(rank_emoji)
    bearer_parts.append(f"**{target_name}**")
    if chapter_emoji:
        bearer_parts.append(chapter_emoji)
    embed.add_field(
        name="▸ Bearer",
        value=" ".join(bearer_parts),
        inline=True,
    )

    # Auspex window info
    window_text = f"Last {window_days} day(s)" if window_days else "Last 28 days"
    embed.add_field(name="▸ Auspex Window", value=window_text, inline=True)

    if not pair_bonds:
        embed.add_field(
            name="▸ Status",
            value="No qualifying Combat Bonds found for this Brother in the current window.",
            inline=False,
        )
        return embed

    # Compute cutoffs for tier labels from pair bond scores
    scores = [score for _uid, score in pair_bonds]
    cutoffs = _compute_bond_cutoffs(scores)

    def _partner_label(uid: str) -> str:
        member = None
        name = "REDACTED"
        rank_emoji = ""
        if guild:
            try:
                member = guild.get_member(int(uid))
            except Exception:
                member = None
        if member:
            display_name = member.nick or member.display_name
            # Strip rank prefix and studs from name
            name = display_name
            # Strip stud pips first
            name = name.replace("●", "").replace("⚬", "").strip()
            # Get member roles
            member_role_names = {
                (getattr(r, "name", "") or "").strip()
                for r in member.roles
                if getattr(r, "name", None)
            }
            # Find member rank
            member_rank = None
            for rp in RANK_ROLES_PRIORITY:
                if rp in member_role_names:
                    member_rank = rp
                    break
            if member_rank:
                rank_emoji = _get_rank_emoji(guild, member_rank)
                # Strip rank prefix from name (case-insensitive)
                for rp in RANK_ROLES_PRIORITY:
                    if name.lower().startswith(rp.lower()):
                        name = name[len(rp) :].lstrip()
                        break
            # Resolve chapter
            chap = None
            try:
                match = next(
                    (
                        hc
                        for hc in HOME_CHAPTERS
                        if any(rn.lower() == hc.lower() for rn in member_role_names)
                    ),
                    None,
                )
                if match:
                    chap = match
            except Exception:
                chap = None
        else:
            chap = None
            member_role_names = set()
        if not chap:
            chap = (chapters or {}).get(uid)
        # Use chapter emoji if available
        chap_emoji = _get_emoji_by_name(guild, chap) if guild and chap else None
        chap_display = chap_emoji if chap_emoji else ""
        # Build label: rank_emoji stripped_name chapter_emoji
        parts = []
        if rank_emoji:
            parts.append(rank_emoji)
        parts.append(name)
        if chap_display:
            parts.append(chap_display)
        return " ".join(parts)

    # Group bonds by tier
    tier_groups: Dict[str, List[str]] = {}
    for partner_uid, score in pair_bonds[:5]:  # Limit to top 5
        tier = _bond_tier_dynamic(score, cutoffs)
        if tier not in tier_groups:
            tier_groups[tier] = []
        tier_groups[tier].append(partner_uid)

    # Order tiers from strongest to weakest
    tier_order = ["INDOMITABLE", "STALWART", "RELIABLE", "FORMING", "FRAGILE"]
    bonds_lines = []
    for tier in tier_order:
        if tier not in tier_groups:
            continue
        partners = tier_groups[tier]
        partner_labels = [f"• {_partner_label(uid)}" for uid in partners]
        bonds_lines.append(f"**{tier}**\n" + "\n".join(partner_labels))

    embed.add_field(
        name="▸ Forged Bonds",
        value="\n\n".join(bonds_lines) if bonds_lines else "None",
        inline=False,
    )

    embed.set_footer(
        text="᛭⋅ These Combat Bonds may be invoked by decree of Watch Command. ⋅᛭"
    )
    return embed


class ToggleFormatView(discord.ui.View):
    def __init__(
        self,
        text_content: Optional[str] = None,
        embed: Optional[discord.Embed] = None,
        default: str = "ansi",
        ephemeral_context: bool = True,
    ):
        # Extend lifetime to reduce 'Interaction failed' after short delays
        super().__init__(timeout=900)
        self.text_content = text_content or ""
        self.embed_obj = embed
        self.current = default if default in ("ansi", "embed") else "ansi"
        # Soft safety margin for Discord's 2000-char content limit
        self._ansi_max_len = 1900
        # If True, buttons toggle the message in place (for ephemeral messages)
        # If False, PC/Console sends ephemeral instead of editing (for public messages)
        self.ephemeral_context = ephemeral_context

        # Initialize button states based on available formats
        self._update_buttons()

    def _update_buttons(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "show_ansi":
                    too_long = len(self.text_content) > self._ansi_max_len
                    if self.ephemeral_context:
                        # Disable if currently showing ANSI or if ANSI unavailable
                        child.disabled = (
                            (self.current == "ansi")
                            or (not self.text_content)
                            or too_long
                        )
                    else:
                        # For public context, disable only if ANSI unavailable
                        child.disabled = (not self.text_content) or too_long
                elif child.custom_id == "show_embed":
                    # Only relevant in ephemeral context
                    child.disabled = (self.current == "embed") or (
                        self.embed_obj is None
                    )

    @discord.ui.button(
        label="PC/Console", style=discord.ButtonStyle.secondary, custom_id="show_ansi"
    )
    async def show_ansi(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not self.text_content:
            try:
                await interaction.response.send_message(
                    "No PC/Console output available.", ephemeral=True
                )
            except Exception:
                pass
            return
        if len(self.text_content) > self._ansi_max_len:
            try:
                await interaction.response.send_message(
                    "PC/Console view exceeds message limit.", ephemeral=True
                )
            except Exception:
                pass
            return

        if self.ephemeral_context:
            # Toggle the message in place (for ephemeral messages)
            self.current = "ansi"
            self._update_buttons()
            try:
                await interaction.response.edit_message(
                    content=self.text_content, embed=None, view=self
                )
            except Exception:
                try:
                    await interaction.followup.send(
                        "Unable to switch to PC/Console view.", ephemeral=True
                    )
                except Exception:
                    pass
        else:
            # Send ANSI view as ephemeral message (for public messages)
            try:
                await interaction.response.send_message(
                    content=self.text_content, ephemeral=True
                )
            except Exception:
                try:
                    await interaction.followup.send(
                        "Unable to show PC/Console view.", ephemeral=True
                    )
                except Exception:
                    pass

    @discord.ui.button(
        label="Mobile", style=discord.ButtonStyle.primary, custom_id="show_embed"
    )
    async def show_embed(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.embed_obj is None:
            await interaction.response.defer()
            return
        self.current = "embed"
        self._update_buttons()
        await interaction.response.edit_message(
            content=None, embed=self.embed_obj, view=self
        )


def _embed_from_ansi(
    title: str, text_block: str, color: int = 0x2ECC71
) -> discord.Embed:
    """Generic helper: wrap an ANSI text block into an embed description safely.
    Truncates to fit Discord limits and preserves code fence for readability.
    """
    # Strip surrounding backticks if present to avoid nested fences
    content = text_block or ""
    try:
        stripped = content.strip()
        if stripped.startswith("```") and stripped.endswith("```"):
            inner = stripped[3:-3]
            # Keep ANSI fence for styling
            content = f"```ansi\n{inner.strip()}\n```"
    except Exception:
        content = text_block or ""
    # Discord embed description limit ~4096 chars
    max_len = 4000
    if len(content) > max_len:
        content = content[: max_len - 1] + "…"
    embed = discord.Embed(title=title, description=content, color=color)
    return embed


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
        # Update global broadcast toggle and debug mode
        global BROADCAST_STATUS, DEBUG_MODE
        BROADCAST_STATUS = not debug_flag
        DEBUG_MODE = bool(debug_flag)
        # If debug mode enabled, set logger to DEBUG level
        if DEBUG_MODE:
            logging.getLogger().setLevel(logging.DEBUG)
            logger.setLevel(logging.DEBUG)
            logger.debug("Debug mode enabled via CLI flag")
    except Exception as e:
        logger.debug(f"Failed to parse CLI args: {e}")
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError(
            "DISCORD_TOKEN environment variable not set. "
            "Please set it before running the bot: export DISCORD_TOKEN='your_token'"
        )
    bot.run(token)


BATTLE_LINE_ORDER = [
    "Watch Brother",
    "Watch Veteran",
    "Oathsworn",
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
    "Watch Captain",
    "High Chaplain",
    "Chief Apothecary",
    "Void Warden",
    "Forgemaster",
}


# --- Fortress-wide rankings for tally_deeds honours display ---------------


async def _compute_fortress_rankings(
    guild: discord.Guild,
    span_days: int = 7,
    *,
    start_dt: Optional[datetime] = None,
    end_dt: Optional[datetime] = None,
) -> dict:
    """Compute fortress-wide rankings for individuals, kill teams, and chapters.

    Returns a dict with:
      - 'individuals': dict mapping user_id -> {metric: (value, rank, total)}
      - 'teams': dict mapping team_name -> {metric: (value, rank, total)}
      - 'chapters': dict mapping chapter_name -> {metric: (value, rank, total)}
      - 'imperial_date': formatted imperial date string

    If start_dt and end_dt are provided, they override the span_days calculation.
    """
    from datetime import datetime, timedelta

    now = datetime.utcnow()
    if start_dt is not None and end_dt is not None:
        start = start_dt
        end = end_dt
        # Compute span_days from the provided window for threshold calculation
        span_days = max(1, (end - start).days)
    else:
        start = now - timedelta(days=span_days)
        end = now

    # Data aggregation structures
    users: Dict[str, dict] = {}
    teams: Dict[str, dict] = {}
    chapters: Dict[str, dict] = {}
    chapters_members: Dict[str, set] = {}

    if DATASTORE is None:
        return {
            "individuals": {},
            "teams": {},
            "chapters": {},
            "imperial_date": _format_imperial_date(now),
        }

    # Collect records in window
    recs_in_window: List[tuple] = []
    all_user_ids: set = set()
    for rec in DATASTORE.iter_records():
        ts = _parse_iso_ts_to_utc_naive(rec.get("timestamp") or "")
        if not ts:
            continue
        if ts < start or ts >= end:
            continue
        recs_in_window.append((ts, rec))
        for uid in rec.get("brother_ids") or []:
            all_user_ids.add(str(uid))

    # Resolve home chapters
    chapters_map: Dict[str, str] = {}
    try:
        if all_user_ids and guild:
            chapters_map = await _resolve_home_chapters(guild, sorted(all_user_ids))
    except Exception:
        chapters_map = {}

    # Build set of valid Watch Brother+ members in guild
    watch_brother_plus_ids: set = set()
    try:
        for member in guild.members:
            names = _canonical_role_names(member)
            # Check if member has any rank in RANK_ROLES_PRIORITY
            if any(r in names for r in RANK_ROLES_PRIORITY):
                watch_brother_plus_ids.add(str(member.id))
    except Exception:
        pass

    # Process each record
    for ts, rec in recs_in_window:
        difficulty = rec.get("difficulty_class")
        is_high_risk = difficulty in ("hard_stratagem", "omega_ops")
        omega_kia = (
            int(rec.get("killed_in_action", 0) or 0) if difficulty == "omega_ops" else 0
        )
        brother_ids = [str(x) for x in (rec.get("brother_ids") or [])]

        # Check if this AAR is a Black Laurels mission
        is_black_laurels = bool(rec.get("black_laurels_in_mission", False))

        # Aggregate user-level stats (only for Watch Brother+ members)
        for uid in brother_ids:
            if uid not in watch_brother_plus_ids:
                continue
            u = users.setdefault(
                uid,
                {
                    "ops": 0,
                    "points": 0,
                    "armory": 0,
                    "high_risk": 0,
                    "omega_kia": 0,
                    "gene_carried": 0,
                    "gene_participated": 0,
                    "black_laurels": 0,
                },
            )
            u["ops"] += 1
            u["points"] += int(rec.get("points_for_op") or 0)
            u["armory"] += int(rec.get("armory_challenge_points") or 0)
            if is_high_risk:
                u["high_risk"] += 1
            if difficulty == "omega_ops":
                u["omega_kia"] += omega_kia
            if is_black_laurels:
                u["black_laurels"] += 1
            try:
                if (
                    str(rec.get("gene_seed_carrier_id")) == str(uid)
                    and (rec.get("gene_seed_status") or "") == "carried"
                ):
                    u["gene_carried"] += int(
                        rec.get("gene_seed_base_points_for_carrier") or 0
                    )
                u["gene_participated"] += 1
            except Exception:
                pass

        # Team aggregation: collect all teams participating in this AAR, then count once per team
        aar_teams: Dict[str, List[str]] = {}  # team -> list of member uids in this AAR
        resolved_participants = (
            0  # Count only resolved members for cohesion calculation
        )
        for uid in brother_ids:
            try:
                member = guild.get_member(int(uid)) if guild else None
            except Exception:
                member = None
            if member is None and guild:
                try:
                    member = await guild.fetch_member(int(uid))
                except Exception:
                    member = None
            if not member:
                continue

            resolved_participants += 1
            try:
                member_teams = _resolve_killteams_for_member(member)
                for mt in member_teams:
                    aar_teams.setdefault(mt, []).append(str(uid))
            except Exception:
                pass

        # Now add stats once per team for this AAR
        total_participants = resolved_participants  # Use resolved count for cohesion
        for resolved_team, team_member_ids in aar_teams.items():
            t = teams.setdefault(
                str(resolved_team),
                {
                    "ops": 0,
                    "points": 0,
                    "armory": 0,
                    "high_risk": 0,
                    "omega_kia": 0,
                    "gene_carried": 0,
                    "gene_participated": 0,
                    "members": set(),
                    "cohesion_sum": 0.0,
                    "cohesion_count": 0,
                },
            )
            t["ops"] += 1  # Count 1 op per AAR, not per member
            t["points"] += int(rec.get("points_for_op") or 0)
            t["armory"] += int(rec.get("armory_challenge_points") or 0)
            if is_high_risk:
                t["high_risk"] += 1
            if difficulty == "omega_ops":
                t["omega_kia"] += omega_kia
            # Cohesion: only count ops with 2+ teammates running together
            team_count = len(team_member_ids)
            if team_count >= 2 and total_participants >= 2:
                cohesion_score = (team_count / total_participants) * 100.0
                t["cohesion_sum"] += cohesion_score
                t["cohesion_count"] += 1
            try:
                # Gene-seed: count once per AAR if carried
                if rec.get("gene_seed_status") == "carried":
                    t["gene_carried"] += int(
                        rec.get("gene_seed_base_points_for_carrier") or 0
                    )
                t["gene_participated"] += 1
                # Track unique members who participated
                for uid in team_member_ids:
                    t["members"].add(str(uid))
            except Exception:
                pass

        # Chapter aggregation: collect all chapters participating in this AAR, then count once per chapter
        aar_chapters: Dict[
            str, List[str]
        ] = {}  # chapter -> list of member uids in this AAR
        for uid in brother_ids:
            ch = chapters_map.get(str(uid))
            if ch:
                aar_chapters.setdefault(ch, []).append(str(uid))

        # Now add stats once per chapter for this AAR
        for ch, chapter_member_ids in aar_chapters.items():
            c = chapters.setdefault(
                ch,
                {
                    "ops": 0,
                    "points": 0,
                    "armory": 0,
                    "high_risk": 0,
                    "omega_kia": 0,
                    "gene_carried": 0,
                    "gene_participated": 0,
                },
            )
            c["ops"] += 1  # Count 1 op per AAR, not per member
            c["points"] += int(rec.get("points_for_op") or 0)
            c["armory"] += int(rec.get("armory_challenge_points") or 0)
            if is_high_risk:
                c["high_risk"] += 1
            if difficulty == "omega_ops":
                c["omega_kia"] += omega_kia
            # Gene-seed: count once per AAR if carried
            if rec.get("gene_seed_status") == "carried":
                c["gene_carried"] += int(
                    rec.get("gene_seed_base_points_for_carrier") or 0
                )
            c["gene_participated"] += 1
            # Track unique members for ops/member calculation
            for uid in chapter_member_ids:
                chapters_members.setdefault(ch, set()).add(str(uid))

    # Compute derived metrics for users
    for uid, v in users.items():
        v["avg"] = (v["points"] / v["ops"]) if v["ops"] else 0.0
        v["gene_rate"] = (
            (v["gene_carried"] / v["gene_participated"])
            if v["gene_participated"]
            else 0.0
        )

    # Compute derived metrics for teams
    for tid, tv in teams.items():
        tv["avg"] = (tv["points"] / tv["ops"]) if tv["ops"] else 0.0
        tv["gene_rate"] = (
            (tv.get("gene_carried", 0) / tv.get("gene_participated", 1))
            if tv.get("gene_participated", 0)
            else 0.0
        )
        members_count = len(tv.get("members") or set())
        tv["avg_aar_per_member"] = (tv["ops"] / members_count) if members_count else 0.0
        tv["pres"] = tv.get("armory", 0) + tv.get("gene_carried", 0)
        # Squad Cohesion: average cohesion % for ops where 2+ teammates ran together
        cohesion_count = tv.get("cohesion_count", 0)
        tv["cohesion"] = (
            (tv.get("cohesion_sum", 0.0) / cohesion_count)
            if cohesion_count > 0
            else 0.0
        )

    # Compute derived metrics for chapters
    # Minimum ops threshold for chapter eligibility
    if span_days == 7:
        min_ops_required = 7
    elif span_days >= 28:
        min_ops_required = 28
    else:
        min_ops_required = max(3, int(span_days * 0.3))

    eligible_chapters = [
        ch
        for ch, d in chapters.items()
        if len(chapters_members.get(ch, set())) >= 1
        and d.get("ops", 0) >= min_ops_required
    ]

    for ch, c in chapters.items():
        c["avg_armory"] = (c["armory"] / c["ops"]) if c["ops"] else 0.0
        c["avg_ops"] = (c["points"] / c["ops"]) if c["ops"] else 0.0
        c["avg"] = c["avg_ops"]  # Alias for consistency with kill teams
        c["pres"] = c["armory"] + c["gene_carried"]  # Combined preservation
        members_count = len(chapters_members.get(ch, set()))
        c["ops_per_member"] = (c["ops"] / members_count) if members_count else 0.0
        c["avg_aar_per_member"] = c["ops_per_member"]  # Alias for consistency
        c["gene_rate"] = (
            (c["gene_carried"] / c["gene_participated"])
            if c["gene_participated"]
            else 0.0
        )

    # Compute median active member count for chapter dampening (same logic as honours)
    _active_counts = [len(chapters_members.get(ch, set())) for ch in eligible_chapters]
    _median_members = statistics.median(_active_counts) if _active_counts else 1.0

    def _apply_chapter_dampening(raw_vals: Dict[str, float]) -> Dict[str, float]:
        """Apply member-count-distance dampening to chapter metric values.

        Chapters with active member counts far from the median get their
        scores pulled toward the global mean, reducing the impact of very
        small or very large chapters on rankings.
        """
        if not raw_vals:
            return {}
        global_mean = statistics.mean(raw_vals.values())
        dampened = {}
        for ch, raw in raw_vals.items():
            members = len(chapters_members.get(ch, set()))
            distance = abs(members - _median_members)
            dampening_factor = distance / _median_members if _median_members else 0.0
            weight = 1.0 / (1.0 + dampening_factor)
            dampened[ch] = weight * raw + (1.0 - weight) * global_mean
        return dampened

    # Compute minimum ops required for user/team rankings (including raw ops
    # and rate-based metrics like avg pts/op), matching the filtering used in
    # monthly honours leaderboards. Note: despite the name, this threshold is
    # also reused for team rankings.
    if span_days >= 28:
        user_min_ops_required = 28
    else:
        user_min_ops_required = max(3, int(span_days * 0.3))

    # Build ranking functions
    def rank_users(metric_key: str, higher_is_better: bool = True, min_ops: int = 0):
        # Filter to users meeting minimum ops threshold if specified;
        # fall back to all users when none meet the threshold (matching
        # monthly honours fallback behaviour to avoid empty leaderboards).
        eligible_users = (
            {uid: v for uid, v in users.items() if v.get("ops", 0) >= min_ops}
            if min_ops > 0
            else users
        )
        if not eligible_users:
            eligible_users = users
        items = [(uid, v.get(metric_key, 0)) for uid, v in eligible_users.items()]
        items.sort(key=lambda x: x[1], reverse=higher_is_better)
        rankings = {}
        for idx, (uid, val) in enumerate(items, 1):
            rankings[uid] = (val, idx, len(items))
        return rankings

    def rank_teams(metric_key: str, higher_is_better: bool = True, min_ops: int = 0):
        # Filter to teams meeting minimum ops threshold if specified;
        # fall back to all teams when none meet the threshold (matching
        # monthly honours fallback behaviour to avoid empty leaderboards).
        eligible_teams = (
            {tid: v for tid, v in teams.items() if v.get("ops", 0) >= min_ops}
            if min_ops > 0
            else teams
        )
        if not eligible_teams:
            eligible_teams = teams
        items = [(tid, v.get(metric_key, 0)) for tid, v in eligible_teams.items()]
        items.sort(key=lambda x: x[1], reverse=higher_is_better)
        rankings = {}
        for idx, (tid, val) in enumerate(items, 1):
            rankings[tid] = (val, idx, len(items))
        return rankings

    def rank_chapters(metric_key: str, higher_is_better: bool = True):
        # Build raw values for eligible chapters
        raw_vals = {
            ch: chapters.get(ch, {}).get(metric_key, 0) for ch in eligible_chapters
        }
        # Apply member-count-distance dampening before ranking
        dampened_vals = _apply_chapter_dampening(raw_vals)
        # Sort by dampened values
        items = [(ch, dampened_vals.get(ch, 0)) for ch in eligible_chapters]
        items.sort(key=lambda x: x[1], reverse=higher_is_better)
        # Return rankings with RAW values for display, but rank order from dampened
        rankings = {}
        for idx, (ch, _) in enumerate(items, 1):
            raw_val = raw_vals.get(ch, 0)
            rankings[ch] = (raw_val, idx, len(items))
        return rankings

    # Compute individual rankings
    # All rankings filter to users meeting minimum ops threshold to match
    # monthly honours leaderboard behavior (only active-enough users qualify).
    individual_rankings = {
        "ops": rank_users("ops", min_ops=user_min_ops_required),
        "avg": rank_users("avg", min_ops=user_min_ops_required),
        "gene_carried": rank_users("gene_carried", min_ops=user_min_ops_required),
        "armory": rank_users("armory", min_ops=user_min_ops_required),
        "high_risk": rank_users("high_risk", min_ops=user_min_ops_required),
        "omega_kia": rank_users("omega_kia", min_ops=user_min_ops_required),
        "black_laurels": rank_users("black_laurels", min_ops=user_min_ops_required),
    }

    # Compute team rankings
    # All rankings filter to teams meeting minimum ops threshold to match
    # monthly honours leaderboard behavior.
    team_rankings = {
        "ops": rank_teams("ops", min_ops=user_min_ops_required),
        "avg": rank_teams("avg", min_ops=user_min_ops_required),
        "pres": rank_teams("pres", min_ops=user_min_ops_required),
        "armory": rank_teams("armory", min_ops=user_min_ops_required),
        "gene_carried": rank_teams("gene_carried", min_ops=user_min_ops_required),
        "high_risk": rank_teams("high_risk", min_ops=user_min_ops_required),
        "omega_kia": rank_teams("omega_kia", min_ops=user_min_ops_required),
        "avg_aar_per_member": rank_teams(
            "avg_aar_per_member", min_ops=user_min_ops_required
        ),
        "cohesion": rank_teams("cohesion", min_ops=user_min_ops_required),
    }

    # Compute chapter rankings (matching kill team metrics)
    chapter_rankings = {
        "ops": rank_chapters("ops"),
        "avg": rank_chapters("avg"),
        "pres": rank_chapters("pres"),
        "armory": rank_chapters("armory"),
        "gene_carried": rank_chapters("gene_carried"),
        "high_risk": rank_chapters("high_risk"),
        "omega_kia": rank_chapters("omega_kia"),
        "avg_aar_per_member": rank_chapters("avg_aar_per_member"),
    }

    return {
        "individuals": individual_rankings,
        "teams": team_rankings,
        "chapters": chapter_rankings,
        "chapters_map": chapters_map,
        "imperial_date": _format_imperial_date(now),
        "span_days": span_days,
    }


def _parse_iso_ts_to_utc_naive(ts_str: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


def _format_member_styled(
    guild: Optional[discord.Guild],
    user_id: str,
    chapters_map: Optional[Dict[str, str]] = None,
    include_chapter: bool = False,
) -> str:
    """Format a member's name for display: rank_emoji + stripped_name (+ chapter_emoji).

    Used across honours, combat bonds, and leaderboard displays for consistency.
    Strips stud pips and rank prefix from the display name, then prepends
    the rank emoji. Optionally appends the chapter emoji.
    Falls back to user_id string if member can't be resolved or guild is None.
    """
    member = None
    name = str(user_id)
    rank_emoji = ""
    chapter_emoji = ""

    if guild is not None:
        try:
            member = guild.get_member(int(user_id))
        except Exception:
            member = None

    if member:
        display_name = member.nick or member.display_name
        # Strip stud pips first
        name = display_name.replace("●", "").replace("⚬", "").replace("▬", "").strip()
        # Get member's roles
        member_role_names = {
            (getattr(r, "name", "") or "").strip()
            for r in member.roles
            if getattr(r, "name", None)
        }
        # Find member's rank and strip rank prefix from name
        member_rank = None
        for rp in RANK_ROLES_PRIORITY:
            if rp in member_role_names:
                member_rank = rp
                break
        if member_rank:
            rank_emoji = _get_rank_emoji(guild, member_rank)
            # Strip rank prefix from name (case-insensitive)
            for rp in RANK_ROLES_PRIORITY:
                if name.lower().startswith(rp.lower()):
                    name = name[len(rp) :].lstrip()
                    break

        # Resolve chapter if requested
        if include_chapter:
            chap = None
            # Try from member roles first
            try:
                match = next(
                    (
                        hc
                        for hc in HOME_CHAPTERS
                        if any(rn.lower() == hc.lower() for rn in member_role_names)
                    ),
                    None,
                )
                if match:
                    chap = match
            except Exception:
                pass
            # Fall back to chapters_map
            if not chap and chapters_map:
                chap = chapters_map.get(str(user_id))
            if chap:
                chapter_emoji = _get_emoji_by_name(guild, chap) or ""

    # Build label: rank_emoji stripped_name chapter_emoji
    parts = []
    if rank_emoji:
        parts.append(rank_emoji)
    parts.append(name)
    if chapter_emoji:
        parts.append(chapter_emoji)
    return " ".join(parts)


def _format_imperial_date(dt: datetime) -> str:
    """Return Imperial date string like '0 123 456.M41' based on UTC datetime.

    - Check number: use 0 (event on Terra)
    - Year fraction: 3-digit fraction through the year (001..999)
    - Year: year within millennium (001..000 where 000 == 1000th year)
    - Millennium: M3
    """
    try:
        # Use UTC date/time for determinism
        year = dt.year
        # Seconds into year
        start = datetime(year, 1, 1, tzinfo=timezone.utc)
        # Determine end of year (next year's start)
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        now = dt.replace(tzinfo=timezone.utc)
        total = (end - start).total_seconds()
        elapsed = (now - start).total_seconds()
        frac = int(max(1, min(999, round((elapsed / total) * 1000))))
        frac_s = f"{frac:03d}"
        year_within = year % 1000
        year_s = f"{year_within:03d}"
        # Compute millennium number (1-based): years 1-1000 -> M1, 1001-2000 -> M2, etc.
        millennium_num = ((year - 1) // 1000) + 1
        mill = f"M{millennium_num}"
        return f"0 {frac_s} {year_s}.{mill}"
    except Exception:
        return ""


# =============================================================================
# MILESTONE ANNOUNCEMENTS
# =============================================================================


def _load_milestone_tracking() -> dict:
    """Load milestone tracking data from JSON file."""
    try:
        if os.path.exists(MILESTONE_TRACKING_PATH):
            with open(MILESTONE_TRACKING_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load milestone tracking: {e}")
    return {
        "last_announced": {
            "aar_points": 0,
            "aar_count": 0,
            "geneseed_recoveries": 0,
            "armory_data": 0,
            "hive_tyrant_kills": 0,
            "bio_titan_kills": 0,
            "tyranid_prime_kills": 0,
        },
        "last_check_date": None,
    }


def _save_milestone_tracking(data: dict) -> None:
    """Save milestone tracking data to JSON file with atomic write."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp_path = MILESTONE_TRACKING_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, MILESTONE_TRACKING_PATH)
    except Exception as e:
        logger.exception(f"Failed to save milestone tracking: {e}")


def _calculate_current_milestones() -> dict:
    """Calculate current totals for all milestone categories from AAR records."""
    if DATASTORE is None:
        return {}

    records = DATASTORE.get_all_records()

    totals = {
        "aar_points": 0,
        "aar_count": len(records),
        "geneseed_recoveries": 0,
        "armory_data": 0,
        "hive_tyrant_kills": 0,
        "bio_titan_kills": 0,
        "tyranid_prime_kills": 0,
    }

    for aar_id, aar in records.items():
        # Sum AAR points
        totals["aar_points"] += aar.get("points_for_op", 0) or 0

        # Count geneseed recoveries
        if aar.get("gene_seed_status") == "carried":
            totals["geneseed_recoveries"] += 1

        # Sum armory data
        totals["armory_data"] += aar.get("armory_data", 0) or 0

        # Count mission types (boss kills)
        mission = aar.get("mission", "") or ""
        mission_lower = mission.lower()
        if "decapitation" in mission_lower:
            totals["hive_tyrant_kills"] += 1
        elif "termination" in mission_lower:
            totals["bio_titan_kills"] += 1
        elif "reclamation" in mission_lower:
            totals["tyranid_prime_kills"] += 1

    return totals


def _check_milestone_thresholds(
    current: dict, last_announced: dict
) -> list[tuple[str, int, int]]:
    """Check which milestones have been crossed since last announcement.

    Returns list of (metric_name, new_milestone_value, current_value) tuples.
    """
    crossed = []

    for metric, increment in MILESTONES_INCREMENTS.items():
        current_val = current.get(metric, 0)
        last_milestone = last_announced.get(metric, 0)

        # Calculate the next milestone threshold after the last announced one
        next_milestone = last_milestone + increment

        # Check if we've crossed one or more milestones
        while current_val >= next_milestone:
            crossed.append((metric, next_milestone, current_val))
            next_milestone += increment

    return crossed


def _get_milestone_display_info(metric: str) -> tuple[str, str, str, int]:
    """Get display information for a milestone metric.

    Returns (title, description, emoji_name, color).
    """
    info = {
        "aar_points": (
            "AAR POINTS MILESTONE",
            "Total After-Action Report points earned by the Watch",
            "Deathwatch",
            0xC0C0C0,  # Silver
        ),
        "aar_count": (
            "OPERATIONS MILESTONE",
            "Total fortress operations completed by the Watch",
            "Deathwatch",
            0xC0C0C0,  # Silver
        ),
        "geneseed_recoveries": (
            "GENE-SEED RECOVERIES",
            "Precious gene-seed secured from fallen warriors",
            "Apothecaryicon",
            0x00FF00,  # Green
        ),
        "armory_data": (
            "ARMORY DATA RECOVERED",
            "Tactical data fragments recovered for the Forge",
            "Techmarineicon",
            0xFF6600,  # Orange
        ),
        "hive_tyrant_kills": (
            "HIVE TYRANTS SLAIN",
            "Decapitation missions completed - synapse lords destroyed",
            "Tyranids",
            0x800080,  # Purple
        ),
        "bio_titan_kills": (
            "BIO-TITANS FELLED",
            "Termination missions completed - behemoths brought low",
            "Tyranids",
            0x800080,  # Purple
        ),
        "tyranid_prime_kills": (
            "TYRANID PRIMES PURGED",
            "Reclamation missions completed - xenos commanders eliminated",
            "Tyranids",
            0x800080,  # Purple
        ),
    }
    return info.get(
        metric, ("MILESTONE", "An achievement has been reached", "Deathwatch", 0xC0C0C0)
    )


def _build_milestone_embed(
    guild: discord.Guild,
    metric: str,
    milestone_value: int,
    current_value: int,
) -> discord.Embed:
    """Build an embed for a milestone announcement."""
    title, description, emoji_name, color = _get_milestone_display_info(metric)

    # Get emoji if available
    emoji = _get_emoji_by_name(guild, emoji_name)
    emoji_str = f"{emoji} " if emoji else ""

    embed = discord.Embed(
        title=f"᛭⋅ {emoji_str}{title} {emoji_str}⋅᛭",
        description=f"*{description}*",
        color=color,
    )

    # Format the milestone number with commas
    milestone_str = f"{milestone_value:,}"
    current_str = f"{current_value:,}"

    # Add the milestone field
    embed.add_field(
        name="▸ Milestone Reached",
        value=f"**{milestone_str}**",
        inline=True,
    )

    embed.add_field(
        name="▸ Current Total",
        value=f"**{current_str}**",
        inline=True,
    )

    # Add thematic footer based on metric
    footers = {
        "aar_points": "The Deathwatch prevails. The Long Vigil continues.",
        "aar_count": "Each operation brings us closer to victory.",
        "geneseed_recoveries": "The legacy of our fallen brothers is preserved.",
        "armory_data": "Knowledge is power. Guard it well.",
        "hive_tyrant_kills": "Cut off the head, and the body will fall.",
        "bio_titan_kills": "Even the mightiest xenos fall before the Emperor's wrath.",
        "tyranid_prime_kills": "The swarm is weakened. Press the advantage.",
    }
    embed.set_footer(text=footers.get(metric, "For the Emperor and the Primarchs."))

    return embed


@tasks.loop(hours=24)
async def _scheduled_milestone_check():
    """Run daily; check if a week has passed and announce any new milestones.

    Posts to ᛭⋅⋅general-chat⋅⋅᛭ with @Watch Brother mention when thresholds are crossed.
    """
    global LAST_MILESTONE_CHECK_DATE
    try:
        if not MILESTONES_ENABLED:
            return

        if DATASTORE is None:
            return

        # Use UTC for consistent scheduling
        now_utc = datetime.now(timezone.utc)
        today = now_utc.date()

        # Load tracking data early so the persisted last_check_date is the
        # source of truth for interval gating (survives bot restarts).
        tracking = _load_milestone_tracking()
        persisted_last_check = tracking.get("last_check_date")

        # Use persisted date preferentially; fall back to in-memory value
        last_check_str = persisted_last_check or LAST_MILESTONE_CHECK_DATE
        if last_check_str:
            try:
                last_check = datetime.strptime(last_check_str, "%Y-%m-%d").date()
                days_since = (today - last_check).days
                if days_since < MILESTONES_CHECK_INTERVAL_DAYS:
                    return
            except Exception:
                pass  # If parsing fails, proceed with check

        logger.info("Milestone check starting...")

        # Resolve target guild and channel
        guild = _resolve_notification_guild()
        if not guild:
            logger.warning("Milestone check: Could not resolve guild, skipping")
            return

        try:
            channel = guild.get_channel(MILESTONES_CHANNEL_ID) or await bot.fetch_channel(
                MILESTONES_CHANNEL_ID
            )
        except Exception:
            logger.exception("Milestone check: Could not resolve channel")
            return

        last_announced = tracking.get("last_announced", {})

        # Calculate current totals
        current = _calculate_current_milestones()
        if not current:
            logger.warning("Milestone check: Could not calculate current totals")
            return

        # Check for crossed milestones
        crossed = _check_milestone_thresholds(current, last_announced)

        if not crossed:
            logger.info("Milestone check complete: no new milestones")
            LAST_MILESTONE_CHECK_DATE = str(today)
            # Persist last_check_date even when there are no announcements
            tracking["last_check_date"] = str(today)
            _save_milestone_tracking(tracking)
            return

        # Find Watch Brother role for mention
        wb_role = discord.utils.get(guild.roles, name="Watch Brother")
        wb_mention = f"<@&{wb_role.id}>" if wb_role else ""

        # Post announcements for each crossed milestone
        announcements_sent = 0
        for metric, milestone_value, current_value in crossed:
            try:
                embed = _build_milestone_embed(
                    guild, metric, milestone_value, current_value
                )
                await channel.send(
                    content=wb_mention,
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(users=False, roles=True, everyone=False),
                )
                # Update the last announced value for this metric
                last_announced[metric] = milestone_value
                announcements_sent += 1
                await asyncio.sleep(1)  # Brief delay between announcements
            except Exception as e:
                logger.exception(
                    f"Failed to post milestone announcement for {metric}: {e}"
                )

        # Save updated tracking
        tracking["last_announced"] = last_announced
        tracking["last_check_date"] = str(today)
        _save_milestone_tracking(tracking)

        LAST_MILESTONE_CHECK_DATE = str(today)
        logger.info(
            f"Milestone check complete: {announcements_sent} announcement(s) posted"
        )

    except Exception as e:
        logger.exception(f"Milestone check failed: {e}")


# ============================================================================
# ROSTER AUDIT COMMAND
# ============================================================================

# Static mappings for position labels to required roles
POSITION_LABEL_MAP = {
    "WatchMaster": "Watch Master",
    "LordExecutioner": "Lord Executioner",
    "ChiefApothecary": "Chief Apothecary",
    "HighChaplain": "High Chaplain",
    "Forgemaster": "Forgemaster",
    "VoidWarden": "Void Warden",
    "Venerable": "Venerable",
    "WatchCaptain": "Watch Captain",
    "WatchLieutenant": "Watch Lieutenant",
    "CompanyChampion": "Company Champion",
    "WatchApothecary": "Watch Apothecary",
    "WatchChaplain": "Watch Chaplain",
    "WatchLibrarian": "Watch Librarian",
    "WatchTechmarine": "Watch Techmarine",
    "WatchSergeant": "Watch Sergeant",
    "KillTeamChampion": "Kill Team Champion",
    "Oathsworn": "Oathsworn",
    "WatchVeteran": "Watch Veteran",
    "WatchBrother": "Watch Brother",
}


def _extract_mentions_from_text(text: str) -> List[int]:
    """Extract user IDs from Discord mention strings like <@123456> or <@!123456>."""
    try:
        # Match <@123456> or <@!123456>
        pattern = r"<@!?(\d+)>"
        matches = re.findall(pattern, text)
        return [int(m) for m in matches]
    except Exception:
        return []


def _extract_role_mention_from_text(text: str) -> Optional[int]:
    """Extract a role ID from either:
    - Role mention format: <@&123456>
    - Custom emoji format: <:EmojiName:123456>
    Returns the role ID or None."""
    try:
        # Try role mention format first: <@&123456>
        pattern = r"<@&(\d+)>"
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))

        # Try emoji format: <:NAME:123456>
        pattern = r"<:\w+:(\d+)>"
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    except Exception:
        pass
    return None


def _extract_position_label(line: str) -> Optional[str]:
    """Extract position label from emoji code like :WatchMaster: or :WatchCaptain:.
    Returns the label (without colons) or None."""
    try:
        # Match :LabelText: at start of line (emoji format)
        match = re.search(r":([A-Za-z]+):", line)
        if match:
            label = match.group(1)
            if label in POSITION_LABEL_MAP:
                return label
    except Exception:
        pass
    return None


async def _find_roster_messages(
    guild: discord.Guild, roster_channel_id: int
) -> Tuple[Optional[discord.Message], Optional[discord.Message], List[discord.Message]]:
    """Find the roster messages by position.

    - Skip first 4 messages (newest)
    - 5th message (index 4) = High Command
    - 6th message (index 5) = Company Command
    - 7th+ (index 6+) = Kill Teams (multiple messages possible)

    Returns (high_command_msg, company_command_msg, kill_teams_msgs_list).
    """
    try:
        channel = guild.get_channel(roster_channel_id)
        if not channel:
            return None, None, []

        # Fetch messages (returns in reverse chronological order - newest first)
        messages = []
        try:
            async for msg in channel.history(limit=100):  # Fetch enough to be safe
                messages.append(msg)
        except Exception as e:
            logger.debug(f"Error fetching channel history: {e}")
            return None, None, []

        # Reverse to get oldest first, so indexing is intuitive
        messages.reverse()

        # Extract based on position
        high_cmd = messages[4] if len(messages) > 4 else None
        company_cmd = messages[5] if len(messages) > 5 else None
        kill_teams = messages[6:] if len(messages) > 6 else []

        logger.debug(f"Found {len(messages)} messages in roster channel")
        logger.debug(f"HC: msg {4}, CC: msg {5}, KTs: msgs {6}+")

        return high_cmd, company_cmd, kill_teams
    except Exception:
        logger.exception("Error finding roster messages")
        return None, None, []


def _parse_roster_section(content: str) -> Dict[int, List[str]]:
    """Parse a roster section (High Command or Company Command).
    Format: <:PositionEmoji:ID> ⋅⋅ [Chapter] <@USER_ID>
    Returns dict: user_id -> list of role names extracted from position label."""
    members = {}
    try:
        lines = content.split("\n")
        for line in lines:
            # Skip vacant, separators, headers, and empty lines
            if (
                not line.strip()
                or "[Vacant]" in line
                or "###" in line
                or "⎯" in line
                or "__" in line
            ):
                continue

            # Extract position label from emoji code
            label = _extract_position_label(line)
            position_role = POSITION_LABEL_MAP.get(label) if label else None

            # Extract user mention from end of line
            user_ids = _extract_mentions_from_text(line)

            if user_ids:
                for user_id in user_ids:
                    if user_id not in members:
                        members[user_id] = []
                    if position_role and position_role not in members[user_id]:
                        members[user_id].append(position_role)
    except Exception:
        logger.exception("Error parsing roster section")

    return members


def _parse_kill_teams_section(content: str) -> Dict[int, Dict[int, Dict[str, any]]]:
    """Parse Kill Teams section (all teams in one message).

    Format:
    ### <:KTEmoji:ID>  ᛭⋅ __@[Kill Team]__ ⋅᛭ <:KTEmoji:ID>
    **Sergeant:**
    - [Chapter Emoji] <@USER_ID>
    **Champion:**
    - [Chapter Emoji] <@USER_ID>
    - [Chapter Emoji] <@USER_ID>

    Returns dict: kill_team_role_id -> {user_id -> {"rank": "Sergeant|Champion|Member"}}.
    """
    kill_teams = {}
    try:
        lines = content.split("\n")
        logger.debug(f"Parsing {len(lines)} lines from kill teams section")
        logger.debug(f"Content preview: {content[:300]}")
        current_kt_role_id = None
        current_kt_members = {}
        current_rank = "Member"  # Default rank for unlabeled members

        for line_num, line in enumerate(lines):
            line_stripped = line.strip()
            if not line_stripped:
                continue

            # Check if this is a Kill Team header (contains "###" and a role mention like <@&ID>)
            if "###" in line and "<@&" in line:
                logger.debug(f"Line {line_num}: Possible KT header: {line[:100]}")
                # Extract role mention for kill team
                role_id = _extract_role_mention_from_text(line)
                if role_id:
                    logger.info(f"Found KT role ID: {role_id} from line: {line[:80]}")
                    # Save previous team if exists
                    if current_kt_role_id is not None and current_kt_members:
                        kill_teams[current_kt_role_id] = current_kt_members
                    # Start new team
                    current_kt_role_id = role_id
                    current_kt_members = {}
                    current_rank = "Member"
                    continue
                else:
                    logger.debug(f"Could not extract role ID from line: {line[:80]}")

            # Check if this is a rank marker (e.g., "**Sergeant:**" or "**Champion:**")
            if "**Sergeant:**" in line:
                current_rank = "Sergeant"
                logger.debug(
                    f"Line {line_num}: Switching to Sergeant rank (applies to next member only)"
                )
                continue
            elif "**Champion:**" in line:
                current_rank = "Champion"
                logger.debug(
                    f"Line {line_num}: Switching to Champion rank (applies to next member only)"
                )
                continue

            # Skip separators and headers
            if "###" in line or "⎯" in line or "__" in line:
                continue

            # If we have a current kill team, parse members
            if current_kt_role_id is not None:
                # Check if this is an empty member slot (just "- " with no mentions)
                user_ids = _extract_mentions_from_text(line)

                if user_ids:
                    # Has members - apply current rank and then reset to Member for next lines
                    logger.debug(
                        f"Line {line_num}: Found {len(user_ids)} users with rank {current_rank}: {line[:80]}"
                    )
                    for user_id in user_ids:
                        if user_id not in current_kt_members:
                            current_kt_members[user_id] = {"rank": current_rank}
                        else:
                            # Update rank if this is the first time we're seeing this user with a rank
                            if current_rank != "Member":
                                current_kt_members[user_id]["rank"] = current_rank
                    # Reset to Member for next lines (rank labels only apply to immediate next member)
                    current_rank = "Member"
                elif line.strip().startswith("-"):
                    # Empty slot (just "- " with nothing) - reset rank to Member
                    logger.debug(
                        f"Line {line_num}: Empty rank slot, resetting to Member"
                    )
                    current_rank = "Member"

        # Save last team
        if current_kt_role_id is not None and current_kt_members:
            kill_teams[current_kt_role_id] = current_kt_members
            logger.info(
                f"Saved KT {current_kt_role_id} with {len(current_kt_members)} members"
            )
        logger.debug(f"Finished parsing kill teams: {len(kill_teams)} teams found")
        if not kill_teams:
            logger.debug("No kill teams found - checking if we ever entered a KT block")
    except Exception:
        logger.exception("Error parsing kill teams section")

    return kill_teams


async def _get_user_roles_by_id(guild: discord.Guild, user_id: int) -> set[str]:
    """Get the set of role names for a user in the guild."""
    try:
        member = await guild.fetch_member(user_id)
        if member:
            return {r.name for r in member.roles}
    except Exception:
        pass
    return set()


def _validate_high_command_roles(
    expected_position_roles: List[str], actual_roles: set[str]
) -> Tuple[bool, set[str], set[str]]:
    """Validate High Command member roles.

    Required: High Command role + Watch Command role + title/position role
    Returns (is_valid, missing_roles, extra_roles).
    """
    expected = set(expected_position_roles) | {"High Command", "Watch Command"}
    missing = expected - actual_roles
    extra = set()

    return len(missing) == 0, missing, extra


async def _validate_company_command_roles(
    guild: discord.Guild,
    company_role_id: int,
    company_command_role_id: int,
    expected_position_roles: List[str],
    actual_roles: set[str],
) -> Tuple[bool, set[str], set[str]]:
    """Validate Company Command member roles.

    Required: companyRoleId + companyCommandRoleId + Watch Command role + position role
    Returns (is_valid, missing_roles, extra_roles).
    """
    expected = set(expected_position_roles) | {"Watch Command"}

    try:
        company_role = guild.get_role(company_role_id)
        if company_role:
            expected.add(company_role.name)
    except Exception:
        pass

    try:
        company_cmd_role = guild.get_role(company_command_role_id)
        if company_cmd_role:
            expected.add(company_cmd_role.name)
    except Exception:
        pass

    missing = expected - actual_roles
    extra = set()

    return len(missing) == 0, missing, extra


async def _validate_kill_team_member_roles(
    guild: discord.Guild,
    company_role_id: int,
    kill_team_role_id: int,
    rank: str,
    actual_roles: set[str],
) -> Tuple[bool, set[str], set[str]]:
    """Validate Kill Team member roles.

    Required:
    - All: companyRoleId + killTeamRoleId
    - Sergeant: + Watch Sergeant
    - Champion: + Kill Team Champion
    - Member: + at least ONE of (Watch Brother, Watch Veteran, Oathsworn)

    Returns (is_valid, missing_roles, extra_roles).
    """
    expected = set()

    # Add company and kill team role names
    try:
        company_role = guild.get_role(company_role_id)
        if company_role:
            expected.add(company_role.name)
    except Exception:
        pass

    try:
        kt_role = guild.get_role(kill_team_role_id)
        if kt_role:
            expected.add(kt_role.name)
    except Exception:
        pass

    if rank == "Sergeant":
        expected.add("Watch Sergeant")
    elif rank == "Champion":
        expected.add("Kill Team Champion")
    else:  # Member
        member_ranks = {"Watch Brother", "Watch Veteran", "Oathsworn"}
        # At least one member rank required
        if not (member_ranks & actual_roles):
            return False, member_ranks, set()

    missing = expected - actual_roles
    extra = set()

    return len(missing) == 0, missing, extra


async def _audit_company_roster(
    guild: discord.Guild,
    company_key: str,
    company_config: dict,
    high_cmd_roster: Dict[int, List[str]],
) -> Dict[str, any]:
    """Audit a single company.

    high_cmd_roster: shared High Command roster (parsed once globally).

    Returns dict with structure:
    {
        "company_name": str,
        "missing": [{"user_id": int, "location": str, "expected": [roles], "actual": [roles]}],
        "extra": [...],
        "mismatch": [...],
    }
    """
    result = {
        "company_name": company_config.get("name", "Unknown"),
        "missing": [],
        "extra": [],
        "mismatch": [],
    }

    try:
        roster_channel_id = int(company_config.get("rosterChannelId"))
        company_role_id = int(company_config.get("companyRoleId", 0) or 0)
        company_command_role_id = int(
            company_config.get("companyCommandRoleId", 0) or 0
        )

        # Find roster messages
        high_cmd_msg, company_cmd_msg, kill_teams_msgs = await _find_roster_messages(
            guild, roster_channel_id
        )

        # Debug logging
        logger.info(f"\n=== AUDITING {company_config.get('name', 'Unknown')} ===")
        logger.info(f"Roster Channel ID: {roster_channel_id}")
        logger.info(
            f"High Command Message: {high_cmd_msg.id if high_cmd_msg else 'NOT FOUND'}"
        )
        logger.info(
            f"Company Command Message: {company_cmd_msg.id if company_cmd_msg else 'NOT FOUND'}"
        )
        logger.info(f"Kill Teams Messages: {len(kill_teams_msgs)} found")

        # Parse Company Command and Kill Teams (High Command is passed in as shared)
        company_cmd_roster = (
            _parse_roster_section(company_cmd_msg.content) if company_cmd_msg else {}
        )

        # Parse all kill team messages and merge
        kill_teams_roster = {}
        for kt_msg in kill_teams_msgs:
            kt_data = _parse_kill_teams_section(kt_msg.content)
            kill_teams_roster.update(kt_data)

        # Debug logging
        logger.info(f"=== AUDITING {company_config.get('name', 'Unknown')} ===")
        logger.info(f"High Command members: {list(high_cmd_roster.keys())}")
        logger.info(f"Company Command members: {list(company_cmd_roster.keys())}")
        logger.info(f"Kill Teams roster dict: {kill_teams_roster}")
        if kill_teams_msgs:
            logger.info(f"Kill Teams messages: {len(kill_teams_msgs)} messages")
            for kt_msg in kill_teams_msgs:
                logger.info(f"  KT Message {kt_msg.id}: {len(kt_msg.content)} chars")
        for kt_id, kt_members in kill_teams_roster.items():
            kt_role = guild.get_role(kt_id)
            kt_name = kt_role.name if kt_role else f"KT-{kt_id}"
            logger.info(f"Kill Team {kt_name} (ID: {kt_id}): {list(kt_members.keys())}")

        # Collect all users from this company's rosters (not including shared High Command)
        company_roster_users = set(company_cmd_roster.keys()) | {
            uid for kt in kill_teams_roster.values() for uid in kt.keys()
        }

        # All roster users for this company (High Command + company-specific)
        all_roster_users = set(high_cmd_roster.keys()) | company_roster_users
        logger.info(f"Total roster users: {list(all_roster_users)}")

        # Helper to get display name for a user ID
        def _get_display_name(uid: int) -> str:
            try:
                member = guild.get_member(uid)
                if member:
                    return member.display_name or member.name
            except Exception:
                pass
            return f"User-{uid}"

        # Check each roster member for missing roles
        for user_id in all_roster_users:
            actual_roles = await _get_user_roles_by_id(guild, user_id)

            if user_id in high_cmd_roster:
                # High Command validation (always required if in High Command)
                expected_roles = high_cmd_roster[user_id]
                is_valid, missing, extra = _validate_high_command_roles(
                    expected_roles, actual_roles
                )
                if not is_valid or missing:
                    result["missing"].append(
                        {
                            "user_id": user_id,
                            "display_name": _get_display_name(user_id),
                            "location": "High Command",
                            "expected": sorted(missing or []),
                            "actual": sorted(actual_roles),
                        }
                    )

            if user_id in company_cmd_roster:
                # Company Command validation
                expected_roles = company_cmd_roster[user_id]
                is_valid, missing, extra = await _validate_company_command_roles(
                    guild,
                    company_role_id,
                    company_command_role_id,
                    expected_roles,
                    actual_roles,
                )
                if not is_valid or missing:
                    result["missing"].append(
                        {
                            "user_id": user_id,
                            "display_name": _get_display_name(user_id),
                            "location": "Company Command",
                            "expected": sorted(missing or []),
                            "actual": sorted(actual_roles),
                        }
                    )

            # Check Kill Teams
            for kt_role_id, kt_members in kill_teams_roster.items():
                if user_id in kt_members:
                    rank = kt_members[user_id].get("rank", "Member")
                    try:
                        kt_role = guild.get_role(kt_role_id)
                        kt_role_name = kt_role.name if kt_role else f"KT-{kt_role_id}"
                    except Exception:
                        kt_role_name = f"KT-{kt_role_id}"

                    is_valid, missing, extra = await _validate_kill_team_member_roles(
                        guild,
                        company_role_id,
                        kt_role_id,
                        rank,
                        actual_roles,
                    )
                    if not is_valid or missing:
                        result["missing"].append(
                            {
                                "user_id": user_id,
                                "display_name": _get_display_name(user_id),
                                "location": f"Kill Team ({kt_role_name})",
                                "rank": rank,
                                "expected": sorted(missing or []),
                                "actual": sorted(actual_roles),
                            }
                        )

        # Check for extra: users with company roles but not in roster
        try:
            company_role_obj = (
                guild.get_role(company_role_id) if company_role_id else None
            )
            company_cmd_role_obj = (
                guild.get_role(company_command_role_id)
                if company_command_role_id
                else None
            )

            # Build set of all kill team role IDs that exist
            kt_roles = set(kill_teams_roster.keys())

            # Scan all members with company role
            if company_role_obj:
                for member in company_role_obj.members:
                    if member.id not in all_roster_users:
                        # Check if they have kill team or company command roles
                        member_role_ids = {r.id for r in member.roles}
                        if (
                            company_cmd_role_obj
                            and company_cmd_role_obj.id in member_role_ids
                        ):
                            result["extra"].append(
                                {
                                    "user_id": member.id,
                                    "display_name": member.display_name or member.name,
                                    "location": "Company Command (should be removed)",
                                    "actual": sorted([r.name for r in member.roles]),
                                }
                            )
                        elif kt_roles & member_role_ids:
                            kt_in_member = [
                                guild.get_role(rid).name
                                if guild.get_role(rid)
                                else f"KT-{rid}"
                                for rid in (kt_roles & member_role_ids)
                            ]
                            kt_names = ", ".join(kt_in_member)
                            possession = "'s" if len(kt_in_member) == 1 else "s'"
                            result["extra"].append(
                                {
                                    "user_id": member.id,
                                    "display_name": member.display_name or member.name,
                                    "location": f"not in {kt_names}{possession} roster",
                                    "actual": sorted([r.name for r in member.roles]),
                                }
                            )
        except Exception:
            logger.exception("Error checking for extra users")

        # Check for mismatches: multiple company roles, multiple kill team roles, conflicting ranks
        # Also check if someone appears in multiple roster sections
        for user_id in all_roster_users:
            actual_roles = await _get_user_roles_by_id(guild, user_id)

            # Track where this user appears in the roster
            appears_in = []
            if user_id in high_cmd_roster:
                appears_in.append("High Command")
            if user_id in company_cmd_roster:
                appears_in.append("Company Command")

            # Check which kill teams they're in
            kt_names = []
            for kt_id, kt_members in kill_teams_roster.items():
                if user_id in kt_members:
                    kt_role = guild.get_role(kt_id)
                    kt_name = kt_role.name if kt_role else f"KT-{kt_id}"
                    kt_names.append(kt_name)

            if len(kt_names) > 1:
                result["mismatch"].append(
                    {
                        "user_id": user_id,
                        "display_name": _get_display_name(user_id),
                        "issue": f"Multiple kill teams: {', '.join(kt_names)}",
                        "actual": sorted(actual_roles),
                    }
                )
            elif kt_names:
                appears_in.append(f"Kill Team ({kt_names[0]})")

            # Check if user appears in multiple roster sections
            if len(appears_in) > 1:
                result["mismatch"].append(
                    {
                        "user_id": user_id,
                        "display_name": _get_display_name(user_id),
                        "issue": f"Listed in multiple sections: {', '.join(appears_in)}",
                        "actual": sorted(actual_roles),
                    }
                )

            # Multiple company roles check
            company_role_count = 0
            for company in (CONFIG.get("companies") or {}).values():
                crole_id = int(company.get("companyRoleId", 0) or 0)
                if crole_id:
                    crole = guild.get_role(crole_id)
                    if crole and crole.name in actual_roles:
                        company_role_count += 1
            if company_role_count > 1:
                result["mismatch"].append(
                    {
                        "user_id": user_id,
                        "display_name": _get_display_name(user_id),
                        "issue": "Multiple company roles",
                        "actual": sorted(actual_roles),
                    }
                )

    except Exception:
        logger.exception(f"Error auditing company {company_key}")

    return result


def _format_audit_summary(audit_results: List[Dict[str, any]]) -> str:
    """Format audit results as ANSI summary."""
    lines = []
    lines.append("```ansi")
    lines.append(
        "\u001b[32m=============================================================================="
    )
    lines.append("  WATCH FORTRESS JERICHO // ROSTER AUDIT — SUMMARY")
    lines.append(
        "=============================================================================="
    )

    for result in audit_results:
        company = result.get("company_name", "Unknown")
        missing_count = len(result.get("missing", []))
        extra_count = len(result.get("extra", []))
        mismatch_count = len(result.get("mismatch", []))

        lines.append("")
        lines.append(f"  {company}")
        lines.append(f"    Missing roles: {missing_count}")
        lines.append(f"    Extra (not in roster): {extra_count}")
        lines.append(f"    Mismatches: {mismatch_count}")

    if not any(
        r.get("missing") or r.get("extra") or r.get("mismatch") for r in audit_results
    ):
        lines.append("")
        lines.append("  No discrepancies found.")

    lines.append("")
    lines.append(
        "=============================================================================="
    )
    lines.append("\u001b[0m```")

    return "\n".join(lines)


def _format_audit_full(audit_results: List[Dict[str, any]]) -> str:
    """Format audit results as full ANSI detail."""
    lines = []
    lines.append("```ansi")
    lines.append(
        "\u001b[32m=============================================================================="
    )
    lines.append("  WATCH FORTRESS JERICHO // ROSTER AUDIT — FULL REPORT")
    lines.append(
        "=============================================================================="
    )

    for result in audit_results:
        company = result.get("company_name", "Unknown")
        lines.append("")
        lines.append(f"  {company}")
        lines.append("  " + "-" * 72)

        missing = result.get("missing", [])
        if missing:
            lines.append("")
            lines.append("  MISSING ROLES:")
            for item in missing:
                user_id = item.get("user_id")
                display_name = item.get("display_name", f"<@{user_id}>")
                location = item.get("location", "Unknown")
                expected = item.get("expected", [])
                rank = item.get("rank", "")
                rank_str = f" [{rank}]" if rank else ""
                lines.append(f"    {display_name} ({location}){rank_str}")
                lines.append(f"      Expected: {', '.join(expected)}")

        extra = result.get("extra", [])
        if extra:
            lines.append("")
            lines.append("  EXTRA (NOT IN ROSTER):")
            for item in extra:
                user_id = item.get("user_id")
                display_name = item.get("display_name", f"<@{user_id}>")
                location = item.get("location", "Unknown")
                lines.append(f"    {display_name} ({location})")

        mismatch = result.get("mismatch", [])
        if mismatch:
            lines.append("")
            lines.append("  MISMATCHES:")
            for item in mismatch:
                user_id = item.get("user_id")
                display_name = item.get("display_name", f"<@{user_id}>")
                issue = item.get("issue", "Unknown")
                actual = item.get("actual", [])
                lines.append(f"    {display_name}: {issue}")
                lines.append(f"      Roles: {', '.join(actual)}")

    if not any(
        r.get("missing") or r.get("extra") or r.get("mismatch") for r in audit_results
    ):
        lines.append("")
        lines.append("  No discrepancies found.")

    lines.append("")
    lines.append(
        "=============================================================================="
    )
    lines.append("\u001b[0m```")

    return "\n".join(lines)


@bot.tree.command(
    name="roster_audit",
    description="Audit company rosters for discrepancies.",
)
@app_commands.describe(
    scope="Type of discrepancies to show: all, missing, extra, or mismatch",
    format="Output format: summary or full",
)
async def roster_audit(
    interaction: discord.Interaction,
    scope: str = "all",
    format: str = "full",
):
    """Audit company rosters.

    scope: all|missing|extra|mismatch
    format: summary|full

    Permission: Forgemaster OR Watch Master only.
    """
    if not (
        check_command_permission(interaction.user, "roster_audit")
        and is_allowed_channel(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    # Defer for long-running operation
    try:
        await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        logger.debug("Could not defer interaction; continuing")

    try:
        scope = (scope or "all").lower()
        format = (format or "summary").lower()

        if scope not in ("all", "missing", "extra", "mismatch"):
            scope = "all"
        if format not in ("summary", "full"):
            format = "summary"

        guild = interaction.guild
        if not guild:
            await interaction.followup.send("Guild not found.", ephemeral=True)
            return

        # Determine which companies to audit
        companies = CONFIG.get("companies") or {}

        # Check if invoked in a roster channel
        channel_id = (
            getattr(interaction.channel, "id", None) if interaction.channel else None
        )
        audit_companies = {}

        if channel_id:
            # Find company by roster channel ID
            for key, config in companies.items():
                if int(config.get("rosterChannelId", 0)) == channel_id:
                    audit_companies[key] = config
                    break

        # If no specific company found, audit all
        if not audit_companies:
            audit_companies = companies

        if not audit_companies:
            await interaction.followup.send(
                "No companies configured. Add entries to `companies` in config.json.",
                ephemeral=True,
            )
            return

        # Parse High Command once (it's shared across all companies)
        # Use the first company's roster channel to find High Command
        high_cmd_roster = {}
        try:
            first_company_config = next(iter(audit_companies.values()))
            roster_channel_id = int(first_company_config.get("rosterChannelId"))
            high_cmd_msg, _, _ = await _find_roster_messages(guild, roster_channel_id)
            if high_cmd_msg:
                high_cmd_roster = _parse_roster_section(high_cmd_msg.content)
        except Exception:
            logger.exception("Error parsing shared High Command")

        # Run audits
        results = []
        for company_key, company_config in audit_companies.items():
            result = await _audit_company_roster(
                guild, company_key, company_config, high_cmd_roster
            )
            results.append(result)

        # Filter by scope
        if scope == "missing":
            for r in results:
                r["extra"] = []
                r["mismatch"] = []
        elif scope == "extra":
            for r in results:
                r["missing"] = []
                r["mismatch"] = []
        elif scope == "mismatch":
            for r in results:
                r["missing"] = []
                r["extra"] = []

        # Format output
        if format == "summary":
            output = _format_audit_summary(results)
        else:
            output = _format_audit_full(results)

        # Build mobile-friendly embed with user mentions
        embed = discord.Embed(
            title="Roster Audit",
            color=0x2ECC71,
        )

        total_missing = sum(len(r.get("missing", [])) for r in results)
        total_extra = sum(len(r.get("extra", [])) for r in results)
        total_mismatch = sum(len(r.get("mismatch", [])) for r in results)

        if total_missing == 0 and total_extra == 0 and total_mismatch == 0:
            embed.description = "✅ No discrepancies found."
        else:
            embed.description = (
                f"**Scope:** {scope}\n\n"
                f"• **Need Roles** — Listed in roster but missing required Discord roles\n"
                f"• **Not Listed** — Have company role but not in roster channel\n"
                f"• **Conflicts** — Multiple teams, sections, or company roles"
            )
            for result in results:
                company = result.get("company_name", "Unknown")
                missing = result.get("missing", [])
                extra = result.get("extra", [])
                mismatch = result.get("mismatch", [])

                if not missing and not extra and not mismatch:
                    continue

                # Build field text with actual user mentions (up to limits)
                field_parts = []
                if missing:
                    missing_users = [
                        f"<@{item.get('user_id')}>" for item in missing[:5]
                    ]
                    # These users are in the roster but missing required roles
                    missing_text = (
                        "**Need Roles** (in roster, missing roles):\n"
                        + ", ".join(missing_users)
                    )
                    if len(missing) > 5:
                        missing_text += f" (+{len(missing) - 5} more)"
                    field_parts.append(missing_text)
                if extra:
                    extra_users = [f"<@{item.get('user_id')}>" for item in extra[:5]]
                    # These users have company role but aren't listed in the roster
                    extra_text = (
                        "**Not Listed** (have role, not in roster):\n"
                        + ", ".join(extra_users)
                    )
                    if len(extra) > 5:
                        extra_text += f" (+{len(extra) - 5} more)"
                    field_parts.append(extra_text)
                if mismatch:
                    mismatch_users = [
                        f"<@{item.get('user_id')}>" for item in mismatch[:5]
                    ]
                    # These users have conflicting assignments (multiple teams, etc.)
                    mismatch_text = (
                        "**Conflicts** (multiple teams, sections):\n"
                        + ", ".join(mismatch_users)
                    )
                    if len(mismatch) > 5:
                        mismatch_text += f" (+{len(mismatch) - 5} more)"
                    field_parts.append(mismatch_text)

                field_value = "\n".join(field_parts) if field_parts else "—"
                # Truncate if too long for embed field
                if len(field_value) > 1024:
                    field_value = field_value[:1020] + "..."
                embed.add_field(name=company, value=field_value, inline=False)

        embed.set_footer(text="Use PC/Console button for detailed ANSI view")

        # Send output with toggle view
        if len(output) <= 1900:
            view = ToggleFormatView(text_content=output, embed=embed, default="embed")
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            # Output too long for toggle, send embed with file attachment
            import io

            fp = io.BytesIO(output.encode("utf-8"))
            fp.seek(0)
            try:
                await interaction.followup.send(
                    embed=embed,
                    file=discord.File(fp, filename="roster_audit.txt"),
                    ephemeral=True,
                )
            finally:
                try:
                    fp.close()
                except Exception:
                    pass

    except Exception:
        logger.exception("Error in roster_audit command")
        await interaction.followup.send(
            "An error occurred during the audit. Check logs for details.",
            ephemeral=True,
        )


@bot.tree.command(
    name="promotion_queue",
    description="Shows who's next in line for service studs and veteran promotions.",
)
async def promotion_queue(interaction: discord.Interaction):
    """Show promotion eligibility queue for service studs and veteran promotions.

    Groups members into three categories:
    - AAR met, time not met: waiting on time requirement
    - AAR not met, time met: waiting on AAR points
    - AAR not met, time not met: need both
    """
    # Permission check: Watch Command only
    if not (
        check_command_permission(interaction.user, "promotion_queue")
        and is_allowed_channel(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild or _resolve_notification_guild()
    if not guild:
        await interaction.followup.send("Could not resolve guild.", ephemeral=True)
        return

    now = datetime.utcnow()

    # --- Service Studs Queue (for Watch Veteran or higher) ---
    # Requirements: 1 stud per 4 weeks AND 400 AAR points (minimum of both)
    studs_aar_met_time_not: List[
        Tuple[discord.Member, int, int, int, int, int, datetime]
    ] = []  # member, aar_pts, weeks, earned, displayed, target, next_stud_date
    studs_aar_not_time_met: List[
        Tuple[discord.Member, int, int, int, int, int, int]
    ] = []  # member, aar_pts, weeks, earned, displayed, target, aar_needed
    studs_aar_not_time_not: List[
        Tuple[discord.Member, int, int, int, int, int, datetime, int]
    ] = []  # member, aar_pts, weeks, earned, displayed, target, next_time_date, aar_needed

    # --- Watch Veteran Queue (for Watch Brother only) ---
    # Requirements: 200 AAR points AND 2 weeks in server
    veteran_aar_met_time_not: List[
        Tuple[discord.Member, int, int, datetime]
    ] = []  # member, aar_pts, weeks, promotion_date
    veteran_aar_not_time_met: List[
        Tuple[discord.Member, int, int, int]
    ] = []  # member, aar_pts, weeks, aar_needed
    veteran_aar_not_time_not: List[
        Tuple[discord.Member, int, int, datetime, int]
    ] = []  # member, aar_pts, weeks, time_date, aar_needed

    # Track roles that indicate veteran or higher
    veteran_or_higher_roles = {
        "Watch Veteran",
        "Oathsworn",
        "Kill Team Champion",
        "Watch Sergeant",
        "Watch Techmarine",
        "Watch Librarian",
        "Watch Apothecary",
        "Watch Chaplain",
        "Company Champion",
        "Watch Lieutenant",
        "Watch Captain",
        "Venerable",
        "Forgemaster",
        "Void Warden",
        "High Chaplain",
        "Chief Apothecary",
        "Lord Executioner",
        "Watch Master",
    }

    for member in guild.members:
        if member.bot:
            continue

        role_names = {getattr(r, "name", "") for r in getattr(member, "roles", [])}

        # Skip inactive brothers (Reserves)
        if "Reserves" in role_names:
            continue

        is_watch_brother = "Watch Brother" in role_names or "Watch Sister" in role_names
        is_veteran_or_higher = any(r in role_names for r in veteran_or_higher_roles)
        is_watch_brother_only = is_watch_brother and not is_veteran_or_higher

        # Get join date and calculate weeks
        joined_at = getattr(member, "joined_at", None)
        if joined_at:
            if joined_at.tzinfo is not None:
                joined_at = joined_at.replace(tzinfo=None)
            weeks_in_server = max(0, (now - joined_at).days // 7)
        else:
            weeks_in_server = 0

        # Get AAR points
        user_id = str(member.id)
        stats = compute_stats_for_user(user_id)
        aar_points = int(stats.get("aar_points", 0) or 0)

        # --- Process Watch Veteran eligibility ---
        if is_watch_brother_only:
            aar_met = aar_points >= 200
            time_met = weeks_in_server >= 2

            if aar_met and time_met:
                # Already fully eligible - skip
                pass
            elif aar_met and not time_met:
                # AAR met, waiting on time
                weeks_needed = 2 - weeks_in_server
                days_until = (
                    weeks_needed * 7 - ((now - joined_at).days % 7)
                    if joined_at
                    else weeks_needed * 7
                )
                promotion_date = now + timedelta(days=days_until)
                veteran_aar_met_time_not.append(
                    (member, aar_points, weeks_in_server, promotion_date)
                )
            elif not aar_met and time_met:
                # Time met, waiting on AAR
                aar_needed = 200 - aar_points
                veteran_aar_not_time_met.append(
                    (member, aar_points, weeks_in_server, aar_needed)
                )
            else:
                # Neither met
                weeks_needed = 2 - weeks_in_server
                days_until = (
                    weeks_needed * 7 - ((now - joined_at).days % 7)
                    if joined_at
                    else weeks_needed * 7
                )
                time_date = now + timedelta(days=days_until)
                aar_needed = 200 - aar_points
                veteran_aar_not_time_not.append(
                    (member, aar_points, weeks_in_server, time_date, aar_needed)
                )

        # --- Process Service Studs eligibility ---
        if is_veteran_or_higher:
            MAX_STUDS = 16

            # Calculate current studs entitlement, capped at MAX_STUDS
            studs_time = weeks_in_server // 4
            studs_aar = aar_points // 400
            earned_studs = min(studs_time, studs_aar, MAX_STUDS)

            # Count currently displayed studs from nickname
            # New system: ●=4 (Auramite), ⚬=1 (Plasteel), max 16
            dn = str(member.nick or member.display_name or "")
            displayed_aur = dn.count("●")
            displayed_plas = dn.count("⚬")
            displayed_studs = displayed_aur * 4 + displayed_plas

            # Only project further progression if below the cap
            if displayed_studs < MAX_STUDS:
                # Check if they're owed studs (only show those who could earn more)
                # For auramite tier (4+ studs), track next auramite milestone (8, 12, 16)
                # For plasteel tier (0-3 studs), track next individual stud
                next_target = _studs_next_target(displayed_studs)

                next_stud_threshold_time = (
                    next_target * 4
                )  # weeks needed for next milestone
                next_stud_threshold_aar = (
                    next_target * 400
                )  # AAR needed for next milestone

                aar_met_for_next = aar_points >= next_stud_threshold_aar
                time_met_for_next = weeks_in_server >= next_stud_threshold_time

                if aar_met_for_next and time_met_for_next:
                    # Already eligible for next stud - they just need to display it
                    pass
                elif aar_met_for_next and not time_met_for_next:
                    # AAR met, waiting on time for next stud
                    weeks_needed = next_stud_threshold_time - weeks_in_server
                    days_until = (
                        weeks_needed * 7 - ((now - joined_at).days % 7)
                        if joined_at
                        else weeks_needed * 7
                    )
                    next_stud_date = now + timedelta(days=days_until)
                    studs_aar_met_time_not.append(
                        (
                            member,
                            aar_points,
                            weeks_in_server,
                            earned_studs,
                            displayed_studs,
                            next_target,
                            next_stud_date,
                        )
                    )
                elif not aar_met_for_next and time_met_for_next:
                    # Time met, waiting on AAR for next stud
                    aar_needed = next_stud_threshold_aar - aar_points
                    studs_aar_not_time_met.append(
                        (
                            member,
                            aar_points,
                            weeks_in_server,
                            earned_studs,
                            displayed_studs,
                            next_target,
                            aar_needed,
                        )
                    )
                else:
                    # Neither met for next stud
                    weeks_needed = next_stud_threshold_time - weeks_in_server
                    days_until = (
                        weeks_needed * 7 - ((now - joined_at).days % 7)
                        if joined_at
                        else weeks_needed * 7
                    )
                    next_time_date = now + timedelta(days=days_until)
                    aar_needed = next_stud_threshold_aar - aar_points
                    studs_aar_not_time_not.append(
                        (
                            member,
                            aar_points,
                            weeks_in_server,
                            earned_studs,
                            displayed_studs,
                            next_target,
                            next_time_date,
                            aar_needed,
                        )
                    )

    # Sort lists by proximity to eligibility
    # For AAR met, time not: sort by soonest date
    veteran_aar_met_time_not.sort(key=lambda x: x[3])  # promotion_date
    studs_aar_met_time_not.sort(key=lambda x: x[6])  # next_stud_date

    # For AAR not, time met: sort by least AAR needed
    veteran_aar_not_time_met.sort(key=lambda x: x[3])  # aar_needed
    studs_aar_not_time_met.sort(key=lambda x: x[6])  # aar_needed

    # For neither met: sort by soonest time date (they can always grind AAR)
    veteran_aar_not_time_not.sort(key=lambda x: x[3])  # time_date
    studs_aar_not_time_not.sort(key=lambda x: x[6])  # next_time_date

    # Load previous queue positions for comparison
    tracking = _load_promotion_tracking()

    # Build combined position lists and compute positions
    veteran_queue = (
        [(m, "time") for m, *_ in veteran_aar_met_time_not]
        + [(m, "aar") for m, *_ in veteran_aar_not_time_met]
        + [(m, "both") for m, *_ in veteran_aar_not_time_not]
    )
    studs_queue = (
        [(m, "time") for m, *_ in studs_aar_met_time_not]
        + [(m, "aar") for m, *_ in studs_aar_not_time_met]
        + [(m, "both") for m, *_ in studs_aar_not_time_not]
    )

    # Assign current positions
    veteran_positions = {str(m.id): i + 1 for i, (m, _) in enumerate(veteran_queue)}
    studs_positions = {str(m.id): i + 1 for i, (m, _) in enumerate(studs_queue)}

    def _get_position_arrow(uid: str, queue_type: str, current_pos: int) -> str:
        """Return position change indicator: 🔼 +N (green up) or 🔻 -N (red down)."""
        prev_key = f"{queue_type}_position"
        user_data = tracking.get(uid, {})
        prev_pos = user_data.get(prev_key)
        if prev_pos is None:
            return ""  # New entry, no arrow
        change = prev_pos - current_pos  # Positive = moved up
        if change > 0:
            return f" 🔼{change}"
        elif change < 0:
            return f" 🔻{abs(change)}"
        return ""  # No change

    def _format_member_with_rank(member: discord.Member) -> str:
        """Format member with rank emoji + stripped name (combat bonds style, no @mention)."""
        return _format_member_styled(
            guild, str(member.id), chapters_map=None, include_chapter=False
        )

    def _build_field_value(
        lines: List[str], total_count: int, max_shown: int = 10
    ) -> str:
        """Build a field value that stays under 1024 chars with smart truncation."""
        result_lines = []
        char_count = 0
        shown = 0
        for line in lines[:max_shown]:
            # Leave room for "more" suffix
            if char_count + len(line) + 30 > 1000:
                break
            result_lines.append(line)
            char_count += len(line) + 1  # +1 for newline
            shown += 1
        if total_count > shown:
            result_lines.append(f"*᛭⋅ +{total_count - shown} more...*")
        return "\n".join(result_lines) if result_lines else "—"

    # Build output embeds
    embeds = []

    # --- Watch Veteran Promotion Queue ---
    veteran_embed = discord.Embed(
        title="᛭⋅ WATCH VETERAN QUEUE ⋅᛭",
        description="*Requirements: 200 AAR + 2 weeks service*",
        color=0xFFD700,  # Gold
    )

    # AAR met, time not
    if veteran_aar_met_time_not:
        lines = []
        for member, aar_pts, weeks, promo_date in veteran_aar_met_time_not:
            date_str = promo_date.strftime("%b %d")
            member_str = _format_member_with_rank(member)
            pos = veteran_positions.get(str(member.id), 0)
            arrow = _get_position_arrow(str(member.id), "veteran", pos)
            lines.append(f"᛭⋅ {member_str}{arrow} | {aar_pts} AAR | **{date_str}**")
        veteran_embed.add_field(
            name=f"▸ Ready on Date ({len(veteran_aar_met_time_not)})",
            value=_build_field_value(lines, len(veteran_aar_met_time_not)),
            inline=False,
        )

    # AAR not, time met
    if veteran_aar_not_time_met:
        lines = []
        for member, aar_pts, weeks, aar_needed in veteran_aar_not_time_met:
            member_str = _format_member_with_rank(member)
            pos = veteran_positions.get(str(member.id), 0)
            arrow = _get_position_arrow(str(member.id), "veteran", pos)
            lines.append(
                f"᛭⋅ {member_str}{arrow} | {aar_pts} AAR | needs **{aar_needed}**"
            )
        veteran_embed.add_field(
            name=f"▸ Needs AAR ({len(veteran_aar_not_time_met)})",
            value=_build_field_value(lines, len(veteran_aar_not_time_met)),
            inline=False,
        )

    # Neither met
    if veteran_aar_not_time_not:
        lines = []
        for member, aar_pts, weeks, time_date, aar_needed in veteran_aar_not_time_not:
            date_str = time_date.strftime("%b %d")
            member_str = _format_member_with_rank(member)
            pos = veteran_positions.get(str(member.id), 0)
            arrow = _get_position_arrow(str(member.id), "veteran", pos)
            lines.append(
                f"᛭⋅ {member_str}{arrow} | {aar_pts} AAR | {date_str}, +{aar_needed}"
            )
        veteran_embed.add_field(
            name=f"▸ Needs Both ({len(veteran_aar_not_time_not)})",
            value=_build_field_value(lines, len(veteran_aar_not_time_not)),
            inline=False,
        )

    if not (
        veteran_aar_met_time_not or veteran_aar_not_time_met or veteran_aar_not_time_not
    ):
        veteran_embed.add_field(
            name="▸ Status", value="No Watch Brothers pending.", inline=False
        )

    total_veterans = (
        len(veteran_aar_met_time_not)
        + len(veteran_aar_not_time_met)
        + len(veteran_aar_not_time_not)
    )
    veteran_embed.set_footer(text=f"᛭⋅ {total_veterans} in queue ⋅᛭")
    embeds.append(veteran_embed)

    # --- Service Studs Queue ---
    studs_embed = discord.Embed(
        title="᛭⋅ SERVICE STUDS QUEUE ⋅᛭",
        description="*Requirements: 4 weeks + 400 AAR per stud*",
        color=0xC0C0C0,  # Silver
    )

    # AAR met, time not
    if studs_aar_met_time_not:
        lines = []
        for (
            member,
            aar_pts,
            weeks,
            earned,
            displayed,
            target,
            next_date,
        ) in studs_aar_met_time_not:
            date_str = next_date.strftime("%b %d")
            target_str = _format_stud_target(target)
            member_str = _format_member_with_rank(member)
            pos = studs_positions.get(str(member.id), 0)
            arrow = _get_position_arrow(str(member.id), "studs", pos)
            lines.append(f"᛭⋅ {member_str}{arrow} | →{target_str} | **{date_str}**")
        studs_embed.add_field(
            name=f"▸ Ready on Date ({len(studs_aar_met_time_not)})",
            value=_build_field_value(lines, len(studs_aar_met_time_not)),
            inline=False,
        )

    # AAR not, time met
    if studs_aar_not_time_met:
        lines = []
        for (
            member,
            aar_pts,
            weeks,
            earned,
            displayed,
            target,
            aar_needed,
        ) in studs_aar_not_time_met:
            target_str = _format_stud_target(target)
            member_str = _format_member_with_rank(member)
            pos = studs_positions.get(str(member.id), 0)
            arrow = _get_position_arrow(str(member.id), "studs", pos)
            lines.append(
                f"᛭⋅ {member_str}{arrow} | →{target_str} | needs **{aar_needed}**"
            )
        studs_embed.add_field(
            name=f"▸ Needs AAR ({len(studs_aar_not_time_met)})",
            value=_build_field_value(lines, len(studs_aar_not_time_met)),
            inline=False,
        )

    # Neither met
    if studs_aar_not_time_not:
        lines = []
        for (
            member,
            aar_pts,
            weeks,
            earned,
            displayed,
            target,
            next_time,
            aar_needed,
        ) in studs_aar_not_time_not:
            date_str = next_time.strftime("%b %d")
            target_str = _format_stud_target(target)
            member_str = _format_member_with_rank(member)
            pos = studs_positions.get(str(member.id), 0)
            arrow = _get_position_arrow(str(member.id), "studs", pos)
            lines.append(
                f"᛭⋅ {member_str}{arrow} | →{target_str} | {date_str}, +{aar_needed}"
            )
        studs_embed.add_field(
            name=f"▸ Needs Both ({len(studs_aar_not_time_not)})",
            value=_build_field_value(lines, len(studs_aar_not_time_not)),
            inline=False,
        )

    if not (studs_aar_met_time_not or studs_aar_not_time_met or studs_aar_not_time_not):
        studs_embed.add_field(
            name="▸ Status", value="No veterans pending.", inline=False
        )

    total_studs = (
        len(studs_aar_met_time_not)
        + len(studs_aar_not_time_met)
        + len(studs_aar_not_time_not)
    )
    studs_embed.set_footer(text=f"᛭⋅ {total_studs} in queue ⋅᛭")
    embeds.append(studs_embed)

    # Save current positions for next comparison (merge with current on-disk state
    # under lock to avoid overwriting concurrent changes from _check_promotion_milestones)
    async with PROMOTION_TRACKING_LOCK:
        fresh_tracking = _load_promotion_tracking()
        for uid, pos in veteran_positions.items():
            fresh_tracking.setdefault(uid, {})["veteran_position"] = pos
        for uid, pos in studs_positions.items():
            fresh_tracking.setdefault(uid, {})["studs_position"] = pos
        _save_promotion_tracking(fresh_tracking)

    await interaction.followup.send(embeds=embeds, ephemeral=True)


# TODO: include vacant command positions in output and whether or not there is an outstanding oath for that role. need to work on the oath parsing logic.
@bot.tree.command(
    name="company_roster",
    description="Show Kill Teams and member counts for a Watch Company.",
)
@app_commands.describe(
    company="The Watch Company to display roster for.",
)
@app_commands.choices(
    company=[
        app_commands.Choice(name="Primus", value="Watch Company Primus"),
        app_commands.Choice(name="Secundus", value="Watch Company Secundus"),
        app_commands.Choice(name="Tertius", value="Watch Company Tertius"),
        app_commands.Choice(name="Quartus", value="Watch Company Quartus"),
        app_commands.Choice(name="Quintus", value="Watch Company Quintus"),
    ]
)
async def company_roster(interaction: discord.Interaction, company: str):
    """Show Kill Teams and their member counts for a given Watch Company."""
    # Permission check: Watch Command only, in the designated channel
    if not (
        check_command_permission(interaction.user, "company_roster")
        and is_allowed_channel(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    guild = interaction.guild or _resolve_notification_guild()
    if not guild:
        await interaction.response.send_message(
            "Could not resolve guild.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    # Find the company role
    company_role = discord.utils.get(guild.roles, name=company)
    if not company_role:
        await interaction.followup.send(
            f"Company role '{company}' not found.", ephemeral=True
        )
        return

    # Find all members with this company role
    company_members = [
        m for m in guild.members if company_role in m.roles and not m.bot
    ]

    if not company_members:
        await interaction.followup.send(
            f"No members found in {company}.", ephemeral=True
        )
        return

    # Group members by their Kill Team role
    kt_counts: Dict[str, List[discord.Member]] = {}
    no_kt_members: List[discord.Member] = []

    for member in company_members:
        # Find member's kill team role from ALLOWED_KT_ROLE_IDS
        member_kt = None
        for role in member.roles:
            if role.id in ALLOWED_KT_ROLE_IDS:
                member_kt = role.name
                break

        if member_kt:
            kt_counts.setdefault(member_kt, []).append(member)
        else:
            no_kt_members.append(member)

    # Sort kill teams by name
    sorted_kts = sorted(kt_counts.items(), key=lambda x: x[0])

    # Build embed
    short_name = _extract_company_short_name(company)
    embed = discord.Embed(
        title=f"᛭⋅ {short_name.upper()} COMPANY ROSTER ⋅᛭",
        description=f"*⌾ {company} ⌾*",
        color=0x2ECC71,
    )

    # Add kill team fields
    kt_lines = []
    for kt_name, members in sorted_kts:
        kt_lines.append(f"**{kt_name}:** {len(members)}")

    if kt_lines:
        embed.add_field(
            name="▸ Kill Teams",
            value="\n".join(kt_lines),
            inline=False,
        )

    # Add unassigned members if any
    if no_kt_members:
        embed.add_field(
            name="▸ No Kill Team",
            value=f"{len(no_kt_members)} member(s)",
            inline=False,
        )

    # Summary
    total_in_kts = sum(len(m) for m in kt_counts.values())
    embed.set_footer(
        text=f"᛭⋅ {total_in_kts} in Kill Teams | {len(no_kt_members)} unassigned | {len(company_members)} total ⋅᛭"
    )

    await interaction.followup.send(embed=embed, ephemeral=True)


if __name__ == "__main__":
    _main()
