#!/usr/bin/env python3

import os
import asyncio
import json
import discord
from discord import app_commands
from datetime import datetime, timedelta, timezone
from discord.ext import tasks
from typing import Dict, List, Tuple, Optional
import logging
import time
from logging.handlers import RotatingFileHandler
import signal
import argparse

# Import DataStore
from .datastore import DataStore

# Pure literal constants (role IDs, channel IDs, file paths, thresholds,
# mission sets, etc.) live in constants.py. Re-export everything so that
# existing references (and `from bot import X` in tests) keep working.
from .constants import *  # noqa: F401,F403
from .flavor_text import *  # noqa: F401,F403
from .permissions import *  # noqa: F401,F403
from .studs import *  # noqa: F401,F403
from .role_aliases import canonicalize_role_name, expand_role_names

# Global DataStore instance (initialized when bot is ready)
DATASTORE: Optional[DataStore] = None

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

# Lock for induction date overrides
INDUCTION_OVERRIDES_LOCK = asyncio.Lock()

# Lock for challenge progress tracking
CHALLENGE_PROGRESS_LOCK = asyncio.Lock()

# Lock for LFG queue operations
LFG_QUEUE_LOCK = asyncio.Lock()

# Lock for Terminus Kill Log subsystem
TERMINUS_SLAYER_LOCK = asyncio.Lock()

# Lock for auto-roster embed state
ROSTER_STATE_LOCK = asyncio.Lock()

# In-memory LFG queues: {message_id: LFGQueue data}
LFG_ACTIVE_QUEUES: Dict[int, dict] = {}

# Guard to avoid double shutdown handling
SHUTDOWN_INITIATED = False


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


# Store the status bulletin message ID for edit-in-place behavior
_STATUS_BULLETIN_MSG_ID: Optional[int] = None


async def _send_watch_command_notice(kind: str):
    """Post or update a pinned status notice in the status channel.
    kind: 'ONLINE' or 'OFFLINE' (case-insensitive).
    Behavior: edit the existing pinned bulletin if found, otherwise create and pin."""
    global _STATUS_BULLETIN_MSG_ID

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

    emoji = "✅" if status == "ONLINE" else "⛔"
    flavor = "Machine-spirit standing by." if status == "ONLINE" else "Machine-spirit at rest."
    content = f"V-1 STATUS: {status} {emoji}\n{flavor}"

    # Try to find and edit existing bulletin (from memory or by scanning pinned)
    existing_msg = None

    # First check cached ID
    if _STATUS_BULLETIN_MSG_ID:
        try:
            existing_msg = await channel.fetch_message(_STATUS_BULLETIN_MSG_ID)
        except discord.NotFound:
            _STATUS_BULLETIN_MSG_ID = None
        except Exception:
            pass

    # If not cached, scan pinned messages for our bulletin
    if not existing_msg:
        try:
            pinned = await channel.pins()
            for msg in pinned:
                if getattr(msg.author, "id", None) != getattr(bot.user, "id", None):
                    continue
                msg_content = msg.content or ""
                if "V-1 STATUS:" in msg_content:
                    existing_msg = msg
                    _STATUS_BULLETIN_MSG_ID = msg.id
                    break
        except Exception as e:
            logger.debug(f"Failed to scan pinned messages: {e}")

    # Edit existing or create new
    try:
        if existing_msg:
            await existing_msg.edit(content=content)
            logger.info(f"Status bulletin updated: {status}")
        else:
            sent_msg = await channel.send(content)
            _STATUS_BULLETIN_MSG_ID = sent_msg.id
            # Try to pin
            try:
                await sent_msg.pin()
            except Exception:
                pass  # Don't fail if we can't pin
            logger.info(f"Status bulletin posted and pinned: {status}")
    except Exception as e:
        logger.warning(f"Failed to send/update status notification: {e}")


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
                logger.info("Debug mode shutdown: skipping broadcast and datastore flush")
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
        aar_channel = guild.get_channel(AAR_CHANNEL_ID)
        if not aar_channel:
            logger.debug("Scheduled audit: AAR channel not found; skipping.")
            return
        async with RECONCILE_LOCK:
            if monthly:
                # Mark monthly as running while we hold the lock
                MONTHLY_AUDIT_PENDING = True
            try:
                fixed, still_broken = await _run_recheck_errors(aar_channel, span_days)
                logger.info(f"Scheduled audit complete: restored={fixed}, broken_remaining={still_broken}")
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
    """Run hourly; on configured day/hour run sanctify + reparse + full audit.

    Default: Tuesday 8 AM UTC. Runs sanctify (45-day span) to catch missed AARs,
    then reparses the same span to apply any parsing improvements,
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
        if now_utc.weekday() != SCHEDULE_WEEKLY_MAINTENANCE_DAY or now_utc.hour != SCHEDULE_WEEKLY_MAINTENANCE_HOUR:
            return

        # Prevent duplicate runs on same date
        if LAST_WEEKLY_MAINTENANCE_DATE == str(today):
            return

        logger.info(
            f"Weekly maintenance starting: sanctify + reparse ({SCHEDULE_WEEKLY_MAINTENANCE_INGEST_SPAN_DAYS}-day span) + full audit"
        )

        guild = _resolve_notification_guild()
        if not guild:
            logger.warning("Weekly maintenance: no guild available; skipping.")
            return

        aar_channel = guild.get_channel(AAR_CHANNEL_ID)
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
            ingested, rejected = await _run_ingest_new(aar_channel, SCHEDULE_WEEKLY_MAINTENANCE_INGEST_SPAN_DAYS)
            logger.info(f"Weekly maintenance: Ingested {ingested}, rejected {rejected}")

            # 2) Reparse the same span to apply any parsing improvements
            logger.info(
                f"Weekly maintenance: Running reparse for last {SCHEDULE_WEEKLY_MAINTENANCE_INGEST_SPAN_DAYS} days"
            )
            total, updated, failed, changes_by_field = await _run_reparse_records(
                days=SCHEDULE_WEEKLY_MAINTENANCE_INGEST_SPAN_DAYS
            )
            if changes_by_field:
                sorted_changes = sorted(changes_by_field.items(), key=lambda x: -x[1])
                changes_summary = ", ".join(f"{k}={v}" for k, v in sorted_changes)
                logger.info(f"Weekly maintenance: Reparse processed={total}, updated={updated}, failed={failed} | fields: {changes_summary}")
            else:
                logger.info(f"Weekly maintenance: Reparse processed={total}, updated={updated}, failed={failed}")

            # 3) Run full audit (no span limit) to catch all fixed errors
            logger.info("Weekly maintenance: Running full audit (no span limit)")
            fixed, still_broken = await _run_recheck_errors(aar_channel, None)
            logger.info(f"Weekly maintenance: Fixed {fixed}, still broken {still_broken}")

            LAST_WEEKLY_MAINTENANCE_DATE = str(today)
            logger.info("Weekly maintenance completed successfully.")
    except Exception:
        logger.exception("Weekly maintenance failed")


@_scheduled_weekly_maintenance_loop.before_loop
async def _before_weekly_maintenance_loop():
    await bot.wait_until_ready()


# Track last monthly archive audit run date to prevent duplicate runs
LAST_MONTHLY_ARCHIVE_AUDIT_DATE: Optional[str] = None


@tasks.loop(minutes=60)
async def _monthly_archive_audit_loop():
    """Run hourly; on the 1st of each month at the configured hour, recheck
    the error archive for the last SCHEDULE_MONTHLY_ARCHIVE_AUDIT_SPAN_DAYS days.

    This is a lighter, targeted sweep distinct from the full-history monthly audit
    (_monthly_audit_loop) and complements the weekly maintenance cycle.
    """
    global LAST_MONTHLY_ARCHIVE_AUDIT_DATE
    try:
        if DATASTORE is None:
            return
        now_utc = datetime.now(timezone.utc)
        today = now_utc.date()

        # Only run on the 1st of the month at the configured hour
        if today.day != 1 or now_utc.hour != SCHEDULE_MONTHLY_ARCHIVE_AUDIT_HOUR:
            return

        # Prevent duplicate runs on same date
        if LAST_MONTHLY_ARCHIVE_AUDIT_DATE == str(today):
            return

        if RECONCILE_LOCK.locked():
            logger.info("Monthly archive audit: reconcile lock held; skipping.")
            return

        guild = _resolve_notification_guild()
        if not guild:
            logger.warning("Monthly archive audit: no guild available; skipping.")
            return

        aar_channel = guild.get_channel(AAR_CHANNEL_ID)
        if not aar_channel:
            logger.warning("Monthly archive audit: AAR channel not found; skipping.")
            return

        logger.info(
            f"Monthly archive audit starting: rechecking errors from last "
            f"{SCHEDULE_MONTHLY_ARCHIVE_AUDIT_SPAN_DAYS} days."
        )
        async with RECONCILE_LOCK:
            fixed, still_broken = await _run_recheck_errors(aar_channel, SCHEDULE_MONTHLY_ARCHIVE_AUDIT_SPAN_DAYS)
            logger.info(f"Monthly archive audit complete: restored={fixed}, broken_remaining={still_broken}")

        LAST_MONTHLY_ARCHIVE_AUDIT_DATE = str(today)
    except Exception:
        logger.exception("Monthly archive audit failed")


@_monthly_archive_audit_loop.before_loop
async def _before_monthly_archive_audit_loop():
    await bot.wait_until_ready()


@tasks.loop(minutes=60)
async def _terminus_reminder_loop():
    """Hourly: post a reminder for kill log entries pending over 72 hours."""
    try:
        await _terminus_ops.check_stale_kill_logs()
    except Exception:
        logger.exception("Terminus kill log stale reminder check failed")


@_terminus_reminder_loop.before_loop
async def _before_terminus_reminder_loop():
    await bot.wait_until_ready()


@tasks.loop(hours=4)
async def _challenge_sweep_loop():
    """Every 4 hours: sweep challenge_progress.json for completed but un-notified awards.

    Catches members who finished all required ops before a bot restart, code
    change, or data reset — cases the per-AAR trigger would never re-evaluate.
    """
    try:
        logger.info("challenge sweep loop: tick")
        guild = _resolve_notification_guild()
        if guild is None:
            logger.warning("challenge sweep loop: no guild resolved; skipping")
            return
        count = await _sweep_challenge_completions(guild)
        logger.info(f"challenge sweep loop: complete, awards_queued={count}")
    except Exception:
        logger.exception("Challenge sweep loop failed")


@_challenge_sweep_loop.before_loop
async def _before_challenge_sweep_loop():
    await bot.wait_until_ready()
    # Short delay on first run to let member cache fully populate.
    logger.info("challenge sweep loop: waiting 60s for member cache before first run")
    await asyncio.sleep(60)


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
    SCHEDULE_DAILY_AUDIT_SPAN_DAYS = int(schedules_cfg.get("daily_audit_span_days") or SCHEDULE_DAILY_AUDIT_SPAN_DAYS)
    # Weekly maintenance settings
    if "weekly_maintenance_enabled" in schedules_cfg:
        SCHEDULE_WEEKLY_MAINTENANCE_ENABLED = _is_truthy(schedules_cfg.get("weekly_maintenance_enabled"))
    if schedules_cfg.get("weekly_maintenance_ingest_span_days"):
        SCHEDULE_WEEKLY_MAINTENANCE_INGEST_SPAN_DAYS = int(schedules_cfg.get("weekly_maintenance_ingest_span_days"))
    if schedules_cfg.get("weekly_maintenance_day") is not None:
        SCHEDULE_WEEKLY_MAINTENANCE_DAY = int(schedules_cfg.get("weekly_maintenance_day"))
    if schedules_cfg.get("weekly_maintenance_hour") is not None:
        SCHEDULE_WEEKLY_MAINTENANCE_HOUR = int(schedules_cfg.get("weekly_maintenance_hour"))
    # Monthly archive audit settings
    if "monthly_archive_audit_enabled" in schedules_cfg:
        SCHEDULE_MONTHLY_ARCHIVE_AUDIT_ENABLED = _is_truthy(schedules_cfg.get("monthly_archive_audit_enabled"))
    if schedules_cfg.get("monthly_archive_audit_span_days") is not None:
        SCHEDULE_MONTHLY_ARCHIVE_AUDIT_SPAN_DAYS = int(schedules_cfg.get("monthly_archive_audit_span_days"))
    if schedules_cfg.get("monthly_archive_audit_hour") is not None:
        SCHEDULE_MONTHLY_ARCHIVE_AUDIT_HOUR = int(schedules_cfg.get("monthly_archive_audit_hour"))
    if "role_integrity_audit_enabled" in schedules_cfg:
        SCHEDULE_ROLE_INTEGRITY_AUDIT_ENABLED = _is_truthy(schedules_cfg.get("role_integrity_audit_enabled"))
    if schedules_cfg.get("role_integrity_audit_hour") is not None:
        SCHEDULE_ROLE_INTEGRITY_AUDIT_HOUR = int(schedules_cfg.get("role_integrity_audit_hour"))
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
        fh = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
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


# ─────────────────────────────────────────────────────────────────────────────
# Populate shared globals, then import extracted domain modules.
# Must come after all locks, CONFIG, and logger are defined above.
# ─────────────────────────────────────────────────────────────────────────────
from . import _bot_globals as _g  # noqa: E402

_g.bot = bot
_g.DATASTORE = DATASTORE
_g.CONFIG = CONFIG
_g.logger = logger
_g.RECONCILE_LOCK = RECONCILE_LOCK
_g.MONTHLY_AUDIT_PENDING = MONTHLY_AUDIT_PENDING
_g.RITES_LOCK = RITES_LOCK
_g.MACHINE_SPIRITS_LOCK = MACHINE_SPIRITS_LOCK
_g.ROTATION_LOCK = ROTATION_LOCK
_g.ACTIVITY_STATUS_LOCK = ACTIVITY_STATUS_LOCK
_g.PROMOTION_TRACKING_LOCK = PROMOTION_TRACKING_LOCK
_g.INDUCTION_OVERRIDES_LOCK = INDUCTION_OVERRIDES_LOCK
_g.CHALLENGE_PROGRESS_LOCK = CHALLENGE_PROGRESS_LOCK
_g.LFG_QUEUE_LOCK = LFG_QUEUE_LOCK
_g.LFG_ACTIVE_QUEUES = LFG_ACTIVE_QUEUES
_g.SHUTDOWN_INITIATED = SHUTDOWN_INITIATED
_g.TERMINUS_SLAYER_LOCK = TERMINUS_SLAYER_LOCK
_g.ROSTER_STATE_LOCK = ROSTER_STATE_LOCK
_g.DEBUG_MODE = DEBUG_MODE


from .forge_ops import *  # noqa: E402,F401,F403
from .aar_ops import *  # noqa: E402,F401,F403
from .roster_ops import *  # noqa: E402,F401,F403
from .roster_ops import (  # noqa: E402 - underscore prefix excluded from import *
    _award_announcement_dispatch_loop,
    _role_integrity_audit_loop,
)
from . import auto_ingest as _auto_ingest  # noqa: E402,F401  # imported for slash command registration side effect
from . import terminus_ops as _terminus_ops  # noqa: E402,F401  # imported for slash command registration side effect
from . import roster_embeds as _roster_embeds  # noqa: E402,F401  # imported for slash command + loop registration
from . import target_packages_ops as _target_packages_ops  # noqa: E402,F401  # imported for slash command + loop registration
from . import loa_ops as _loa_ops  # noqa: E402,F401  # imported for LOA slash command + expiry loop
from . import snapshot_challenge_baseline as _snapshot_challenge_baseline  # noqa: E402,F401  # imported for snapshot command registration
from . import poll_ops as _poll_ops  # noqa: E402,F401  # imported for governance poll command + loop registration

# Lines 828-2593 extracted to roster_ops.py

# Global rank priority model (tiered, highest -> lowest).
# Ranks inside the same tier are peers and intentionally share the same priority.
RANK_TIERS: Dict[int, List[str]] = {
    0: ["Watch Master"],
    1: [
        "Blade Master",
        "Castellan",
        "Chief Apothecary",
        "Forgemaster",
        "High Chaplain",
        "Huntmaster",
        "Venerable Dreadnought",
        "Void Warden",
        "Watch Captain",
    ],
    2: [
        "First Blade",
        "Honored Dreadnought",
        "Watch Apothecary",
        "Watch Chaplain",
        "Watch Keeper",
        "Watch Librarian",
        "Watch Lieutenant",
        "Watch Techmarine",
    ],
    3: ["Veteran Sergeant"],
    4: ["Watch Sergeant"],
    5: ["Bladeguard", "Oathsworn"],
    6: ["Watch Veteran"],
    7: ["Watch Brother"],
}

# Flattened list retained for iteration and display in other modules.
RANK_ROLES_PRIORITY = [rank for tier in sorted(RANK_TIERS) for rank in RANK_TIERS[tier]]

# Canonical rank -> tier mapping used by comparison helpers.
RANK_ROLE_TIERS: Dict[str, int] = {
    rank: tier
    for tier, ranks in RANK_TIERS.items()
    for rank in ranks
}

# Canonical list of known home chapters for lookup
HOME_CHAPTERS = [
    "Angels of Defiance",
    "Angels Encarmine",
    "Angels of Vengeance",
    "Atlantian Spears",
    "Black Templars",
    "Bleeding Hearts",
    "Blood Scythes",
    "Blood Angels",
    "Blood Ravens",
    "Brazen Minotaurs",
    "Carcharodons",
    "Carmine Blades",
    "Celestial Lions",
    "Consecrators",
    "Cowled Wardens",
    "Crimson Fists",
    "Dark Angels",
    "Dark Krakens",
    "Dragonspears",
    "Death Exorcists",
    "Death Spectres",
    "Epsilon Paladins",
    "Exorcists",
    "Executioners",
    "Flesh Tearers",
    "Genesis Chapter",
    "Hawk Lords",
    "Howling Griffons",
    "Hospitallers",
    "Imperial Fists",
    "Imperius Reavers",
    "Iron Hands",
    "Iron Hounds",
    "Iron Lords",
    "Iron Ravens",
    "Iron Snakes",
    "Knights of Abhorrence",
    "Knights of the Raven",
    "Lamenters",
    "Marines Errant",
    "Marines Malevolent",
    "Mantis Warriors",
    "Mentors",
    "Minotaurs",
    "Mortifactors",
    "Necropolis Hawks",
    "Revilers",
    "Raptors",
    "Raven Guard",
    "Red Scorpions",
    "Red Templars",
    "Salamanders",
    "Sable Knights",
    "Scythes of the Emperor",
    "Sons of Medusa",
    "Space Wolves",
    "Storm Giants",
    "Tempestuous Angels",
    "The Drakes",
    "Tigers Argent",
    "Tome Keepers",
    "Ultramarines",
    "White Templars",
    "White Scars",
    "Wolfspear",
    "Black Shield",
]

# Kill Teams - dynamically populated from ALLOWED_KT_ROLE_IDS on startup
# This avoids needing to update names when KT roles are renamed
KILL_TEAMS: List[str] = []

# Command-level teams (company commands and high command)
_DEFAULT_COMMAND_TEAMS = [
    "Primus Command",
    "Secundus Command",
    "Tertius Command",
    "Quartus Command",
    "Quintus Command",
    "High Command",
]
_DEFAULT_COMMAND_TEAM_ROLE_IDS = {
    "high command": 1452913063970865203,
    "primus command": 1468794571889709248,
    "secundus command": 1468797860014325902,
    "tertius command": 1468797905740759082,
    "quartus command": None,  # Not yet configured in the server
    "quintus command": None,  # Not yet configured in the server
}
try:
    _company_cfg = CONFIG.get("companies") or {}
    _configured_command_teams = []
    _configured_command_team_role_ids = {"high command": HIGH_COMMAND_ROLE_ID}
    if isinstance(_company_cfg, dict):
        for key, entry in _company_cfg.items():
            company_name = str((entry or {}).get("name") or key or "").strip()
            if not company_name:
                continue
            command_name = f"{company_name} Command"
            _configured_command_teams.append(command_name)
            try:
                command_role_id = int((entry or {}).get("companyCommandRoleId") or 0)
            except Exception:
                command_role_id = 0
            if command_role_id:
                _configured_command_team_role_ids[command_name.lower()] = command_role_id
    COMMAND_TEAMS = _configured_command_teams + ["High Command"] if _configured_command_teams else list(_DEFAULT_COMMAND_TEAMS)
    COMMAND_TEAM_ROLE_IDS = (
        _configured_command_team_role_ids
        if len(_configured_command_team_role_ids) > 1
        else dict(_DEFAULT_COMMAND_TEAM_ROLE_IDS)
    )
except Exception:
    COMMAND_TEAMS = list(_DEFAULT_COMMAND_TEAMS)
    COMMAND_TEAM_ROLE_IDS = dict(_DEFAULT_COMMAND_TEAM_ROLE_IDS)

# Default allowed command channels (can be overridden in config.json "default_allowed_channels")
DEFAULT_ALLOWED_CHANNELS = {"❖⋅data-vault⋅❖"}

# Kill Team forum/thread configuration
# Populate `ALLOWED_KT_FORUM_PARENT_IDS` with forum (parent) channel IDs
# that host Kill Team posts. Example: {123456789012345678, 987654321098765432}
_DEFAULT_ALLOWED_KT_FORUM_PARENT_IDS = {1433351293103112202, 1458255656682258504, 1486238369175437342}
try:
    _forum_parent_cfg = ((CONFIG.get("target_packages") or {}).get("kt_forum_parent_ids") or [])
    ALLOWED_KT_FORUM_PARENT_IDS: set[int] = {int(x) for x in _forum_parent_cfg if x is not None}
    if not ALLOWED_KT_FORUM_PARENT_IDS:
        ALLOWED_KT_FORUM_PARENT_IDS = set(_DEFAULT_ALLOWED_KT_FORUM_PARENT_IDS)
except Exception:
    ALLOWED_KT_FORUM_PARENT_IDS = set(_DEFAULT_ALLOWED_KT_FORUM_PARENT_IDS)
    logger.warning("Invalid target_packages.kt_forum_parent_ids config; using defaults", exc_info=True)

# Hard-coded allowlist of Kill Team role IDs that may be used with
# /tally_deeds when invoked from Kill Team posts. Populate with ints.
_DEFAULT_ALLOWED_KT_ROLE_IDS = {
    1458254715942080543,
    1458254904819974386,
    1433355179020914688,
    1444348999401210037,
    1486476398058012712,
    1498104968513847386,
}
try:
    _kt_role_ids_cfg = ((CONFIG.get("target_packages") or {}).get("kt_role_ids") or [])
    ALLOWED_KT_ROLE_IDS: set[int] = {int(x) for x in _kt_role_ids_cfg if x is not None}
    if not ALLOWED_KT_ROLE_IDS:
        ALLOWED_KT_ROLE_IDS = set(_DEFAULT_ALLOWED_KT_ROLE_IDS)
except Exception:
    ALLOWED_KT_ROLE_IDS = set(_DEFAULT_ALLOWED_KT_ROLE_IDS)
    logger.warning("Invalid target_packages.kt_role_ids config; using defaults", exc_info=True)

# Mapping of Kill Team role ID → that KT's chat channel ID.
# Used to route auto-award announcements to the member's KT channel.
# Falls back to the general channel (SERVICE_STUDS_CHANNEL_ID) if not found.
# Format: {role_id: channel_id}
# Example: {1458254715942080543: 1234567890123456789}
try:
    _kt_role_channel_cfg = ((CONFIG.get("target_packages") or {}).get("kt_role_channel_map") or {})
    KT_ROLE_CHANNEL_MAP: dict[int, int] = {
        int(role_id): int(channel_id)
        for role_id, channel_id in _kt_role_channel_cfg.items()
        if role_id is not None and channel_id is not None
    }
except Exception:
    KT_ROLE_CHANNEL_MAP = {}
    logger.warning("Invalid target_packages.kt_role_channel_map config; using empty map", exc_info=True)

# Optional mapping: forum parent id -> set of company role IDs that own
# the Kill Teams in that forum. Populate as needed to enable Lt/Captain checks.
try:
    _forum_parent_company_cfg = ((CONFIG.get("target_packages") or {}).get("forum_parent_company_role_ids") or {})
    FORUM_PARENT_COMPANY_ROLE_IDS: dict[int, set[int]] = {
        int(parent_id): {int(role_id) for role_id in (role_ids or []) if role_id is not None}
        for parent_id, role_ids in _forum_parent_company_cfg.items()
        if parent_id is not None
    }
except Exception:
    FORUM_PARENT_COMPANY_ROLE_IDS = {}
    logger.warning("Invalid target_packages.forum_parent_company_role_ids config; using empty map", exc_info=True)


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
                 (matches current channel ID OR parent channel ID for threads/forum posts)
      2. CONFIG["default_allowed_channels"] or DEFAULT_ALLOWED_CHANNELS constant
    """
    try:
        ch = interaction.channel
        ch_name = getattr(ch, "name", None)
        ch_id = str(getattr(ch, "id", ""))
        parent_id = getattr(ch, "parent_id", None)
        if parent_id is None:
            parent = getattr(ch, "parent", None)
            parent_id = getattr(parent, "id", None)
        parent_id_str = str(parent_id) if parent_id is not None else ""

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

        # Check command-level channel restrictions: if a command is listed here,
        # it may ONLY be invoked from the specified channel IDs.
        restrictions = CONFIG.get("command_channel_restrictions") or {}
        if cmd_name and cmd_name in restrictions:
            allowed_cmd_channels = {str(c) for c in (restrictions[cmd_name] or [])}
            return ch_id in allowed_cmd_channels

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

        # Fallback: check allowed channel IDs from config. For forum threads,
        # also allow parent forum IDs so new posts inherit access automatically.
        allowed_ids = set(CONFIG.get("allowed_command_channel_ids") or [])
        if allowed_ids:
            allowed_id_strs = {str(x) for x in allowed_ids}
            if (ch_id and ch_id in allowed_id_strs) or (parent_id_str and parent_id_str in allowed_id_strs):
                return True
            return False

        # KT forum posts inherit broad command access from their approved forum
        # parent channels so current and future posts do not need manual entries.
        allowed_kt_forum_parent_ids = {str(x) for x in (ALLOWED_KT_FORUM_PARENT_IDS or set())}
        if parent_id_str and parent_id_str in allowed_kt_forum_parent_ids:
            return True

        # Final fallback: default allowed channel names
        default_channels = set(CONFIG.get("default_allowed_channels") or DEFAULT_ALLOWED_CHANNELS)
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
    # Lower value means higher authority. Same-tier ranks intentionally share
    # the same value so they compare as peers.
    return RANK_ROLE_TIERS.get(role_name)


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
    aliases: Dict[str, List[str]] = CONFIG.get("role_aliases") or {}
    roles = getattr(user, "roles", [])
    role_names: list[str] = []
    for role in roles:
        rn = getattr(role, "name", None)
        if not rn:
            continue
        role_names.append(rn)
    return expand_role_names(role_names, role_aliases=aliases)


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


# Lines 3040-5931 extracted to forge_ops.py
def is_sergeant_or_higher(user: discord.User | discord.Member):
    # Allow nickname override for owner/operator
    admin_ids = set(str(x) for x in (CONFIG.get("admin_user_ids") or []))
    if str(getattr(user, "id", None)) in admin_ids or str(getattr(user, "nick", None)) == "Watch Techmarine Jules":
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


# Permission track data tables (BATTLE_LINE_TRACK, BATTLE_LINE_RANKS,
# OATHSWORN_TRACK, OATHSWORN_RANKS, CHAMPION_TRACK, CHAMPION_RANKS,
# SPECIALIST_TRACKS, SPECIALIST_RANKS, DREAD_TRACK, DREAD_RANKS,
# HIGH_COMMAND_RANKS, WATCH_COMMAND_ROLES) live in permissions.py.


def _user_meets_track_requirement(user_roles: set[str], min_rank: str) -> bool:
    """Check if user meets a min_rank requirement based on track logic.

    - Battle Line: linear hierarchy (Sergeant+ means Sergeant, Lt, Captain)
    - Champion: KT Champion → First Blade → Blade Master
    - Specialist: each of the 4 sub-tracks leads to its High Command role

    Watch Master always qualifies for everything.
    """
    aliases: Dict[str, List[str]] = CONFIG.get("role_aliases") or {}
    min_rank = canonicalize_role_name(min_rank, role_aliases=aliases)

    # Watch Master always has access
    if "Watch Master" in user_roles:
        return True

    # Check specialist tracks (4 independent sub-tracks)
    if min_rank in SPECIALIST_TRACKS:
        allowed_roles = SPECIALIST_TRACKS[min_rank]
        return bool(user_roles & allowed_roles)

    # Check oathsworn track
    if min_rank in OATHSWORN_TRACK:
        allowed_roles = OATHSWORN_TRACK[min_rank]
        return bool(user_roles & allowed_roles)

    # Check dread track
    if min_rank in DREAD_TRACK:
        allowed_roles = DREAD_TRACK[min_rank]
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


def check_command_permission(user: discord.User | discord.Member, command_name: str) -> bool:
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
      - Champion: Bladeguard → First Blade → Blade Master
      - Specialist (4 sub-tracks): Techmarine→Forgemaster, Librarian→Void Warden, etc.

    Each track is independent. Watch Master has access to everything.
    """
    # Debug mode: admin gets god perms, everyone else blocked.
    if globals().get("DEBUG_MODE"):
        admin_ids = set(str(x) for x in (CONFIG.get("admin_user_ids") or []))
        uid = str(getattr(user, "id", None))
        return uid in admin_ids

    perms = CONFIG.get("permissions", {}) or {}
    cmd_perms = perms.get(command_name, {}) or {}

    # Admin override in production: only applies if user is in user_ids whitelist
    # for the specific command, or if the command has no explicit config at all.
    admin_ids = set(str(x) for x in (CONFIG.get("admin_user_ids") or []))
    uid = str(getattr(user, "id", None))

    # Check user_ids whitelist
    user_whitelist = cmd_perms.get("user_ids") or []
    if uid in {str(x) for x in user_whitelist}:
        return True

    user_roles = _canonical_role_names(user)
    aliases: Dict[str, List[str]] = CONFIG.get("role_aliases") or {}

    # Check min_rank using track-aware logic
    min_rank = cmd_perms.get("min_rank")
    if min_rank:
        canonical_min_rank = canonicalize_role_name(str(min_rank), role_aliases=aliases)
        if _user_meets_track_requirement(user_roles, canonical_min_rank):
            return True

    # Check roles list (user must have any of these roles)
    # "Watch Command" is a shorthand that expands to all Watch Command roles
    allowed_roles = {
        canonicalize_role_name(str(role_name), role_aliases=aliases)
        for role_name in (cmd_perms.get("roles") or [])
        if str(role_name).strip()
    }
    if "Watch Command" in allowed_roles:
        allowed_roles.discard("Watch Command")
        allowed_roles.update(WATCH_COMMAND_ROLES)
    if allowed_roles:
        if user_roles & allowed_roles:
            return True

    # If the command has explicit config but user doesn't match, deny
    if cmd_perms:
        return False

    # No explicit entry — fall back to the _default policy in config.
    # If no _default is configured either, deny access.
    default_perms = perms.get("_default", {}) or {}
    if not default_perms:
        return False

    default_min_rank = default_perms.get("min_rank")
    if default_min_rank:
        canonical_default_min_rank = canonicalize_role_name(str(default_min_rank), role_aliases=aliases)
        if _user_meets_track_requirement(user_roles, canonical_default_min_rank):
            return True

    default_roles = {
        canonicalize_role_name(str(role_name), role_aliases=aliases)
        for role_name in (default_perms.get("roles") or [])
        if str(role_name).strip()
    }
    if "Watch Command" in default_roles:
        default_roles.discard("Watch Command")
        default_roles.update(WATCH_COMMAND_ROLES)
    if default_roles and user_roles & default_roles:
        return True

    return False


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


def can_reconcile_records(user: "discord.User | discord.Member") -> bool:
    """Return True if the user may run reconcile_records commands.

    Requires Watch Techmarine (or Forgemaster / Watch Master).
    """
    user_roles = _canonical_role_names(user)
    return bool(user_roles & {"Watch Techmarine", "Forgemaster", "Watch Master"})


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
            _g.DATASTORE = DATASTORE  # Update the shared global reference
            logger.info("DataStore initialized on ready; background flush started.")
        except Exception as e:
            logger.exception(f"Failed to initialize DataStore on ready: {e}")
    # sync app_commands (slash commands)
    try:
        # Register strike directives commands
        _target_packages_ops._register_commands(bot.tree)
    except Exception:
        logger.exception("Failed to register strike directives commands")
    try:
        # Register LOA commands
        _loa_ops._register_commands(bot.tree)
    except Exception:
        logger.exception("Failed to register LOA commands")
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
                    logger.info("Monthly audit loop started (daily check for month-end).")
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

    # Start award announcement dispatch loop (drains 1 queued post every 15 min)
    try:
        if not _award_announcement_dispatch_loop.is_running():
            _award_announcement_dispatch_loop.start()
            logger.info("Award announcement dispatch loop started (15 min interval).")
    except Exception:
        logger.exception("Failed to start award announcement dispatch loop")

    # Start daily role integrity audit loop if enabled
    try:
        if SCHEDULE_ROLE_INTEGRITY_AUDIT_ENABLED:
            if not _role_integrity_audit_loop.is_running():
                _role_integrity_audit_loop.start()
                logger.info(
                    "Role integrity audit loop started "
                    f"(daily gate, target hour {SCHEDULE_ROLE_INTEGRITY_AUDIT_HOUR:02d}:00 UTC)."
                )
            try:
                ran = await _run_role_integrity_audit_once(force=True)
                if ran:
                    logger.info("Role integrity audit executed on startup (forced run).")
                else:
                    logger.info("Role integrity audit startup run skipped (disabled or guild unavailable).")
            except Exception:
                logger.exception("Failed to execute startup role integrity audit")
    except Exception:
        logger.exception("Failed to start role integrity audit loop")

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
                    f"sanctify + reparse {SCHEDULE_WEEKLY_MAINTENANCE_INGEST_SPAN_DAYS}-day span + full audit)."
                )
    except Exception:
        logger.exception("Failed to start weekly maintenance loop")

    # Start monthly archive audit loop if enabled (default: enabled)
    try:
        if SCHEDULE_MONTHLY_ARCHIVE_AUDIT_ENABLED:
            if not _monthly_archive_audit_loop.is_running():
                _monthly_archive_audit_loop.start()
                logger.info(
                    f"Monthly archive audit loop started (1st of month {SCHEDULE_MONTHLY_ARCHIVE_AUDIT_HOUR}:00 UTC, "
                    f"{SCHEDULE_MONTHLY_ARCHIVE_AUDIT_SPAN_DAYS}-day span)."
                )
    except Exception:
        logger.exception("Failed to start monthly archive audit loop")

    # Start terminus kill log stale reminder loop
    try:
        if not _terminus_reminder_loop.is_running():
            _terminus_reminder_loop.start()

        if not _challenge_sweep_loop.is_running():
            _challenge_sweep_loop.start()
            logger.info("Terminus kill log reminder loop started (hourly check).")
    except Exception:
        logger.exception("Failed to start terminus kill log reminder loop")

    # Start auto-roster daily update loop
    try:
        if not _roster_embeds._roster_update_loop.is_running():
            _roster_embeds._roster_update_loop.start()
            logger.info("Auto-roster embed daily update loop started (24h interval).")
    except Exception:
        logger.exception("Failed to start auto-roster update loop")

    # Register auto-ingest loop.
    try:
        if not _auto_ingest._auto_ingest_loop.is_running():
            _auto_ingest._auto_ingest_loop.start()
            logger.info("Auto-AAR-ingest loop started (gated by config cadence).")
    except Exception:
        logger.exception("Failed to start auto-ingest loop")

    # Restore LFG queue views and start expiration loop
    try:
        await _restore_lfg_queue_views()
        if not _lfg_queue_expiration_loop.is_running():
            _lfg_queue_expiration_loop.start()
            default_expiry_mins = _get_lfg_default_expiry_minutes()
            max_expiry_mins = _get_lfg_max_expiry_minutes()
            logger.info(
                f"LFG queue expiration loop started (default: {default_expiry_mins} min, max: {max_expiry_mins} min)."
            )
    except Exception:
        logger.exception("Failed to start LFG queue system")

    # Register persistent views for armor submissions
    try:
        await register_armor_submission_views()
    except Exception:
        logger.exception("Failed to register armor submission persistent views")

    # Start strike directives expiry loop
    try:
        if not _target_packages_ops._tp_expiry_loop.is_running():
            _target_packages_ops._tp_expiry_loop.start()
            logger.info("Strike directives expiry loop started (30min interval).")
    except Exception:
        logger.exception("Failed to start strike directives expiry loop")

    # Start strike queue matchmaking sweep loop
    try:
        if not _target_packages_ops._strike_queue_match_sweep_loop.is_running():
            sweep_minutes = _target_packages_ops._strike_queue_match_sweep_minutes()
            _target_packages_ops._strike_queue_match_sweep_loop.change_interval(minutes=sweep_minutes)
            _target_packages_ops._strike_queue_match_sweep_loop.start()
            logger.info(f"Strike queue match sweep loop started ({sweep_minutes}min interval).")
    except Exception:
        logger.exception("Failed to start strike queue match sweep loop")

    # Start LOA expiry loop
    try:
        if not _loa_ops._loa_expiry_loop.is_running():
            _loa_ops._loa_expiry_loop.start()
            logger.info("LOA expiry loop started (30min interval).")
    except Exception:
        logger.exception("Failed to start LOA expiry loop")

    # Register persistent views for Terminus kill log entries
    try:
        await _terminus_ops.register_persistent_views()
    except Exception:
        logger.exception("Failed to register terminus kill log persistent views")

    # Register persistent views for Strike Directives (Sgt accept + sign-up buttons)
    try:
        await _target_packages_ops.register_persistent_views()
    except Exception:
        logger.exception("Failed to register strike directives persistent views")

    # Register persistent views for governance polls and start expiry loop
    try:
        await _poll_ops.register_persistent_views()
    except Exception:
        logger.exception("Failed to register governance poll persistent views")

    try:
        if not _poll_ops._governance_poll_expiry_loop.is_running():
            _poll_ops._governance_poll_expiry_loop.start()
            logger.info("Governance poll expiry loop started (2min interval).")
    except Exception:
        logger.exception("Failed to start governance poll expiry loop")


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


async def _record_spirit_released(user_id: int, spirit: str, age_days: int) -> None:
    """Best-effort chronicle append for a released machine spirit.

    This tolerates older extracts where forge chronicle helpers are not
    re-exported into this module.
    """
    try:
        load_fn = globals().get("_load_forge_chronicle")
        save_fn = globals().get("_save_forge_chronicle")
        if not callable(load_fn) or not callable(save_fn):
            return

        chronicle = load_fn() or {}
        history = chronicle.setdefault("spirit_releases", [])
        history.append(
            {
                "user_id": str(user_id),
                "spirit": str(spirit),
                "age_days": int(age_days),
                "released_at": datetime.utcnow().isoformat(),
            }
        )
        if len(history) > 500:
            del history[:-500]
        save_fn(chronicle)
    except Exception as exc:
        logger.debug(f"Failed to record released machine spirit for {user_id}: {exc}")


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    """Handle member role changes - release spirit when member goes inactive."""
    try:
        roles_changed = {r.id for r in before.roles} != {r.id for r in after.roles}

        # Check if Reserves role was added
        before_role_ids = {r.id for r in before.roles}
        after_role_ids = {r.id for r in after.roles}

        # Also check by name in case ID doesn't match
        before_role_names = {r.name.lower() for r in before.roles}
        after_role_names = {r.name.lower() for r in after.roles}

        reserves_added = (RESERVES_ROLE_ID in after_role_ids and RESERVES_ROLE_ID not in before_role_ids) or (
            "reserves" in after_role_names and "reserves" not in before_role_names
        )

        if reserves_added:
            # Member went inactive - release their machine spirit
            # Get spirit info before deleting to calculate age
            spirits_data = _load_machine_spirits()
            spirit_info = spirits_data.get(str(after.id), {})
            age_days = 0
            if isinstance(spirit_info, dict) and spirit_info.get("bound_ts"):
                try:
                    bound_dt = datetime.fromisoformat(spirit_info["bound_ts"])
                    age_days = (datetime.utcnow() - bound_dt).days
                except Exception:
                    pass

            spirit = await _delete_machine_spirit(after.id)
            if spirit:
                # Record the release in the chronicle with age
                await _record_spirit_released(after.id, spirit, age_days)
                logger.info(f"Released machine spirit {spirit} for {after.display_name} (went inactive, age: {age_days}d)")

        if roles_changed:
            removed_from_packages = await _target_packages_ops._reconcile_member_directive_attachments(after, after.guild)
            if removed_from_packages:
                logger.info(
                    "Removed %s from directives after role change: %s",
                    after.display_name,
                    ", ".join(removed_from_packages),
                )
            still_queued = await _target_packages_ops._reconcile_member_strike_queue_entry(after)
            if not still_queued:
                logger.debug(f"Strike queue entry removed or absent for {after.display_name} after role change.")
            if after.guild:
                await _target_packages_ops._evaluate_strike_queue_matches(after.guild)
    except Exception as e:
        logger.debug(f"Error in on_member_update: {e}")


async def _handle_lfg_button(interaction: discord.Interaction, custom_id: str):
    """Handle LFG button interactions globally."""
    try:
        parts = custom_id.split(":")
        if len(parts) != 2:
            logger.warning(f"Invalid LFG custom_id format: {custom_id}")
            return

        action, queue_id_str = parts
        queue_id = int(queue_id_str)
        logger.info(f"LFG button: action={action} queue_id={queue_id} user={interaction.user.id}")

        # Create a view instance to use its methods
        view = LFGQueueView(queue_id)

        if action == "lfg_join":
            await view.join_queue(interaction)
        elif action == "lfg_leave":
            await view.leave_queue(interaction)
        elif action == "lfg_close":
            await view.close_queue(interaction)

        logger.info(f"LFG button handler completed: {action} queue_id={queue_id}")
    except Exception as e:
        logger.warning(f"Error in _handle_lfg_button: {e}", exc_info=True)
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("An error occurred processing this button.", ephemeral=True)
        except Exception:
            pass


@bot.event
async def on_interaction(interaction: discord.Interaction):
    # Handle LFG button interactions
    try:
        if interaction and interaction.type == discord.InteractionType.component and interaction.data:
            custom_id = interaction.data.get("custom_id", "")
            logger.info(f"Button interaction received: custom_id={custom_id}")
            if custom_id.startswith("lfg_"):
                logger.info(f"Routing to LFG button handler: {custom_id}")
                await _handle_lfg_button(interaction, custom_id)
                return
    except Exception as e:
        logger.warning(f"Error handling LFG button: {e}")

    # Pre-invocation logging for slash commands
    try:
        if interaction and interaction.type == discord.InteractionType.application_command:
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
async def on_app_command_completion(interaction: discord.Interaction, command: app_commands.Command):
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
        msg = "Command failed due to an internal servitor fault. The issue has been logged."

    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.followup.send(msg, ephemeral=True)
    except Exception:
        pass


@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    """Detect when a processed or errored AAR message is deleted and notify staff."""
    try:
        message_id = str(payload.message_id)

        # Check if this was a processed AAR or an errored AAR
        is_processed = DATASTORE.is_processed(message_id)
        error_entry = None
        if not is_processed:
            # Check if it was an errored AAR
            error_data = _load_json_dict(AAR_ERRORS_PATH)
            error_entry = error_data.get(message_id)
            if not error_entry:
                return  # Not a tracked AAR at all

        # Get the stored record for details (may be None for errored AARs)
        record = DATASTORE.get_record(message_id) or {}

        # Resolve guild and notification channel
        guild = None
        try:
            guild = bot.get_guild(payload.guild_id) if payload.guild_id else None
        except Exception:
            pass
        if not guild:
            logger.warning(f"AAR {message_id} deleted but guild not found.")
            return
        # Verify this was from the AAR channel
        if payload.channel_id != AAR_CHANNEL_ID:
            return
        # Get notification channel
        notify_channel = discord.utils.get(guild.channels, name="❖⋅data-vault⋅❖")
        if not notify_channel:
            logger.warning(f"AAR {message_id} deleted but notification channel not found.")
            return

        # Get Watch Command role for ping
        watch_role = discord.utils.get(guild.roles, name="Watch Command")
        mention = f"<@&{watch_role.id}>" if watch_role else "@Watch Command"

        if error_entry:
            # Errored AAR deletion notification - PUBLIC SHAMING EDITION
            author_info = error_entry.get("author", {})
            author_id = author_info.get("id")
            author_mention = f"<@{author_id}>" if author_id else "Unknown"
            author_name = author_info.get("nickname") or author_info.get("username") or "Unknown"
            errors = error_entry.get("errors", [])
            error_preview = "\n".join(errors[:5])  # Show first 5 errors
            if len(errors) > 5:
                error_preview += f"\n... and {len(errors) - 5} more"

            # Get preserved content if available
            preserved_content = error_entry.get("content", "")
            content_preview = preserved_content[:300] + "..." if len(preserved_content) > 300 else preserved_content
            error_timestamp = error_entry.get("timestamp", "Unknown")

            alert_lines = [
                "# ☠️ UNAUTHORIZED AAR DELETION ☠️",
                "",
                f"## {author_mention} deleted an AAR with errors instead of fixing it",
                "",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"**Brother:** {author_mention} ({author_name})",
                f"**Original Timestamp:** {error_timestamp}",
                f"**Message ID:** `{message_id}`",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "",
                "**The errors that needed correction:**",
                f"```\n{error_preview}\n```",
            ]

            if content_preview:
                alert_lines.extend(
                    [
                        "",
                        "**Deleted Content:**",
                        f"```\n{content_preview}\n```",
                    ]
                )

            alert_lines.extend(
                [
                    "",
                    "⚠️ **AARs with errors must be EDITED and CORRECTED, not deleted.**",
                    "",
                    "If you made a mistake, repost the AAR with the correct format.",
                    f"Watch Command has been notified. {mention}",
                ]
            )
            alert_content = "\n".join(alert_lines)

            # Truncate if too long
            if len(alert_content) > 1900 and content_preview:
                # Shrink content preview
                content_preview = preserved_content[:150] + "..." if preserved_content else ""
                alert_lines = [
                    "# ☠️ UNAUTHORIZED AAR DELETION ☠️",
                    "",
                    f"## {author_mention} deleted an AAR with errors instead of fixing it",
                    "",
                    f"**Brother:** {author_mention} ({author_name})",
                    f"**Message ID:** `{message_id}`",
                    "",
                    "**Errors:**",
                    f"```\n{error_preview}\n```",
                ]
                if content_preview:
                    alert_lines.extend(["**Content:**", f"```\n{content_preview}\n```"])
                alert_lines.extend(["", f"⚠️ AARs must be EDITED, not deleted. {mention}"])
                alert_content = "\n".join(alert_lines)

            # Remove from error tracking since the message is gone
            try:
                error_data = _load_json_dict(AAR_ERRORS_PATH)
                if message_id in error_data:
                    del error_data[message_id]
                    _save_json_dict(AAR_ERRORS_PATH, error_data)
            except Exception:
                pass
        else:
            # Processed AAR deletion - THIS IS WORSE, IT WAS A VALID RECORD
            brother_ids = record.get("brother_ids", [])
            mission = record.get("mission", "Unknown")
            difficulty = record.get("difficulty", "Unknown")
            timestamp = record.get("timestamp", "Unknown")
            author_mention = f"<@{brother_ids[0]}>" if brother_ids else "Unknown"
            preserved_content = record.get("content", "")
            content_preview = preserved_content[:300] + "..." if len(preserved_content) > 300 else preserved_content

            # Build alert content, shrinking the preview as needed to stay within limits
            while True:
                alert_lines = [
                    "# 🚨 ARCHIVE TAMPERING DETECTED 🚨",
                    "",
                    f"## {author_mention} DELETED A PROCESSED AAR",
                    "",
                    "**This record was VALIDATED and ARCHIVED. Deletion is FORBIDDEN.**",
                    "",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    f"**Brother:** {author_mention}",
                    f"**Mission:** {mission}",
                    f"**Difficulty:** {difficulty}",
                    f"**Original Timestamp:** {timestamp}",
                    f"**Message ID:** `{message_id}`",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    "",
                    "**Deleted Content:**",
                    f"```\n{content_preview}\n```",
                    "",
                    "⛔ **AARs are PERMANENT RECORDS. They cannot be deleted without Watch Command authorization.**",
                    "",
                    "The record has been preserved in the archive. This incident has been logged.",
                    f"{mention}",
                ]
                alert_content = "\n".join(alert_lines)
                if len(alert_content) <= 1900 or not content_preview:
                    break
                overflow = len(alert_content) - 1900
                if overflow >= len(content_preview):
                    content_preview = ""
                else:
                    target_len = len(content_preview) - overflow
                    if len(preserved_content) > target_len:
                        body_len = max(0, target_len - 3)
                        content_preview = preserved_content[:body_len] + "..."
                    else:
                        content_preview = preserved_content[:target_len]

        try:
            await notify_channel.send(
                alert_content,
                allowed_mentions=discord.AllowedMentions(roles=True, users=True),
            )
            log_type = "errored" if error_entry else "processed"
            logger.info(f"AAR deletion notification sent for {log_type} message {message_id}")
        except Exception as e:
            logger.error(f"Failed to send AAR deletion notification: {e}")
    except Exception as e:
        logger.error(f"Error in on_raw_message_delete handler: {e}", exc_info=True)


# Lines 6690-17573 extracted to roster_ops.py
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
        _g.DEBUG_MODE = DEBUG_MODE  # propagate to shared globals for other modules
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


# Lines 17605-20389 extracted to roster_ops.py
