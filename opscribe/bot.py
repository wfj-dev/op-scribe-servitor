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

# Track last milestone check date to prevent duplicate runs
LAST_MILESTONE_CHECK_DATE: Optional[str] = None


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
_g.LAST_MILESTONE_CHECK_DATE = LAST_MILESTONE_CHECK_DATE
_g.TERMINUS_SLAYER_LOCK = TERMINUS_SLAYER_LOCK
_g.ROSTER_STATE_LOCK = ROSTER_STATE_LOCK
_g.DEBUG_MODE = DEBUG_MODE


from .forge_ops import *  # noqa: E402,F401,F403
from .aar_ops import *  # noqa: E402,F401,F403
from .roster_ops import *  # noqa: E402,F401,F403
from .roster_ops import _award_announcement_dispatch_loop  # noqa: E402 - underscore prefix excluded from import *
from . import auto_ingest as _auto_ingest  # noqa: E402,F401  # imported for slash command registration side effect
from . import terminus_ops as _terminus_ops  # noqa: E402,F401  # imported for slash command registration side effect
from . import roster_embeds as _roster_embeds  # noqa: E402,F401  # imported for slash command + loop registration
from . import target_packages_ops as _target_packages_ops  # noqa: E402,F401  # imported for slash command + loop registration
from . import loa_ops as _loa_ops  # noqa: E402,F401  # imported for LOA slash command + expiry loop

# Lines 828-2593 extracted to roster_ops.py

# Global rank priority list (highest -> lowest)
RANK_ROLES_PRIORITY = [
    "Watch Master",
    "Lord Executioner",
    "Chief Apothecary",
    "High Chaplain",
    "Forgemaster",
    "Castellan",
    "Void Warden",
    "Huntmaster",
    "Watch Captain",
    "Venerable Dreadnought",
    "Watch Lieutenant",
    "Company Champion",
    "Watch Apothecary",
    "Watch Chaplain",
    "Watch Librarian",
    "Watch Techmarine",
    "Watch Keeper",
    "Watch Sergeant",
    "Honored Dreadnought",
    "Interred Brother",
    "Oathsworn",
    "Kill Team Champion",
    "Watch Veteran",
    "Watch Brother",
]

# Canonical list of known home chapters for lookup
HOME_CHAPTERS = [
    "Angels of Defiance",
    "Angels of Vengeance",
    "Black Templars",
    "Bleeding Hearts",
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
    "Flesh Tearers",
    "Genesis Chapter",
    "Hawk Lords",
    "Hospitallers",
    "Imperial Fists",
    "Imperius Reavers",
    "Iron Hands",
    "Iron Hounds",
    "Iron Lords",
    "Iron Ravens",
    "Knights of the Raven",
    "Lamenters",
    "Marines Errant",
    "Marines Malevolent",
    "Mentors",
    "Minotaurs",
    "Necropolis Hawks",
    "Raptors",
    "Raven Guard",
    "Red Scorpions",
    "Red Templars",
    "Salamanders",
    "Scythes of the Emperor",
    "Sons of Medusa",
    "Space Wolves",
    "Storm Giants",
    "Tempestuous Angels",
    "The Drakes",
    "Tome Keepers",
    "Ultramarines",
    "White Scars",
    "Wolfspear",
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
ALLOWED_KT_FORUM_PARENT_IDS: set[int] = set([1433351293103112202, 1458255656682258504, 1486238369175437342])

# Hard-coded allowlist of Kill Team role IDs that may be used with
# /tally_deeds when invoked from Kill Team posts. Populate with ints.
ALLOWED_KT_ROLE_IDS: set[int] = set(
    [
        1458254715942080543,
        1458254904819974386,
        1433355179020914688,
        1444348999401210037,
        1486476398058012712,
        1498104968513847386,
    ]
)

# Mapping of Kill Team role ID → that KT's chat channel ID.
# Used to route auto-award announcements to the member's KT channel.
# Falls back to the general channel (SERVICE_STUDS_CHANNEL_ID) if not found.
# Format: {role_id: channel_id}
# Example: {1458254715942080543: 1234567890123456789}
KT_ROLE_CHANNEL_MAP: dict[int, int] = {}

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

        # Fallback: check allowed channel IDs from config
        allowed_ids = set(CONFIG.get("allowed_command_channel_ids") or [])
        if allowed_ids and ch_id:
            return ch_id in {str(x) for x in allowed_ids}

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


def _compute_authority_bracket_member_ids(
    viewer: "discord.Member",
    guild: "discord.Guild",
    role_key: str,
    cadre: str,
) -> Optional[set]:
    """Resolve the viewer's authority bracket for status displays.

    ``cadre`` is ``"techmarine"`` or ``"librarian"`` and selects which specialist
    role is treated as "under" a HighCom viewer (Forgemaster oversees Techmarines,
    Void Warden oversees Librarians).

    Bracket semantics — used to hide out-of-bracket brothers unless the viewer's
    own bracket is fully clear:
      • Forgemaster / Void Warden (and Forgemaster debug) → HighCom members +
        members holding the relevant specialist cadre role.
      • Company-level specialist (Watch Techmarine / Watch Librarian) → only
        members of the viewer's company.
      • Anyone else → returns None (no bracket; show flat list).
    """
    if guild is None:
        return None
    role_key = (role_key or "").lower()
    cadre_role_name = TECHMARINE_ROLE_NAME if cadre == "techmarine" else LIBRARIAN_ROLE_NAME
    if role_key in ("forgemaster", "forgemaster_debug", "void_warden"):
        ids: set = set()
        for m in guild.members:
            if getattr(m, "bot", False):
                continue
            role_names = {r.name for r in getattr(m, "roles", []) or []}
            if role_names & HIGH_COMMAND_ROLES:
                ids.add(m.id)
            elif cadre_role_name in role_names:
                ids.add(m.id)
        return ids
    if role_key in ("techmarine", "librarian"):
        company = _get_member_company_name(viewer)
        if not company:
            return None
        ids = set()
        for m in guild.members:
            if getattr(m, "bot", False):
                continue
            try:
                if _get_member_company_name(m) == company:
                    ids.add(m.id)
            except Exception:
                continue
        return ids
    return None


def _find_responsible_attestor(bearer: discord.Member, guild: discord.Guild) -> Tuple[Optional[discord.Member], str]:
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

    logger.debug(f"[attestor] Finding attestor for bearer={bearer.display_name} (id={bearer.id})")
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
                    all_techmarines_found.append((m.display_name, m_company, list(m_roles)))
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
# CHAMPION_TRACK, CHAMPION_RANKS, SPECIALIST_TRACKS, SPECIALIST_RANKS,
# HIGH_COMMAND_RANKS, WATCH_COMMAND_ROLES) live in permissions.py.


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
      - Champion: Kill Team Champion → Company Champion → Lord Executioner
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

    # No explicit entry — fall back to the _default policy in config.
    # If no _default is configured either, deny access.
    default_perms = perms.get("_default", {}) or {}
    if not default_perms:
        return False

    default_min_rank = default_perms.get("min_rank")
    if default_min_rank and _user_meets_track_requirement(user_roles, default_min_rank):
        return True

    default_roles = set(default_perms.get("roles") or [])
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

    # Start milestone check loop if enabled (default: enabled)
    try:
        if MILESTONES_ENABLED:
            if not _scheduled_milestone_check.is_running():
                _scheduled_milestone_check.start()
                logger.info(f"Milestone check loop started (every {MILESTONES_CHECK_INTERVAL_DAYS} days).")
    except Exception:
        logger.exception("Failed to start milestone check loop")

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

    # Start strike directives expiry loop
    try:
        if not _target_packages_ops._tp_expiry_loop.is_running():
            _target_packages_ops._tp_expiry_loop.start()
            logger.info("Strike directives expiry loop started (30min interval).")
    except Exception:
        logger.exception("Failed to start strike directives expiry loop")

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
async def on_member_update(before: discord.Member, after: discord.Member):
    """Handle member role changes - release spirit when member goes inactive."""
    try:
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
