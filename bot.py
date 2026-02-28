#!/usr/bin/env python3


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

# Global DataStore instance (initialized when bot is ready)
DATASTORE: Optional[DataStore] = None

# Data file locations
DATA_DIR = "data"
AAR_RECORDS_PATH = os.path.join(DATA_DIR, "aar_records.json")
AAR_ERRORS_PATH = os.path.join(DATA_DIR, "aar_errors.json")
PROCESSED_IDS_PATH = os.path.join(DATA_DIR, "processed_ids.json")
TROPHY_HALL_INDEX_PATH = os.path.join(DATA_DIR, "trophy_hall_index.json")
OATHS_INDEX_PATH = os.path.join(DATA_DIR, "oaths_index.json")
RITES_PATH = os.path.join(DATA_DIR, "rites.json")
ACTIVITY_STATUS_PATH = os.path.join(DATA_DIR, "activity_status.json")
ACTIVITY_STATUS_LAST_CHECK_PATH = os.path.join(
    DATA_DIR, "activity_status_last_check.json"
)
PROMOTION_TRACKING_PATH = os.path.join(DATA_DIR, "promotion_tracking.json")

# Channel ID for activity status change notifications
ACTIVITY_STATUS_CHANNEL_ID = 1459043645499117630

# Channel ID for veteran promotion notifications
VETERAN_PROMOTION_CHANNEL_ID = 1443813516979994634

# Channel ID for service stud milestone notifications
SERVICE_STUDS_CHANNEL_ID = 1430055064969674777

# Channel ID for Black Laurels eligibility notifications
BLACK_LAURELS_CHANNEL_ID = 1443813633220935774

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = discord.Client(intents=intents)
bot.tree = app_commands.CommandTree(bot)

# Global lock to serialize reconciliation runs
RECONCILE_LOCK = asyncio.Lock()
CHAPLAIN_INGEST_LOCK = asyncio.Lock()

# Flag to indicate a monthly full-history audit is pending/running so daily audits skip.
MONTHLY_AUDIT_PENDING = False

# Rites storage lock
RITES_LOCK = asyncio.Lock()

# Lock for rotation state operations
ROTATION_LOCK = asyncio.Lock()

# Lock for activity status operations
ACTIVITY_STATUS_LOCK = asyncio.Lock()

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

# Black Laurels strict enforcement begins on Feb 20, 2026 at 00:00 UTC
BLACK_LAURELS_STRICT_ENFORCEMENT_DATE = datetime(
    2026, 2, 20, 0, 0, 0, tzinfo=timezone.utc
)
# Black Laurels role ID for parsing
BLACK_LAURELS_ROLE_ID = 1440108298115485716
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
    """Post a concise status notice to ❖⋅data-vault⋅❖ and replace the previous one.
    kind: 'ONLINE' or 'OFFLINE' (case-insensitive).
    Behavior: always delete the most recent prior status bulletin (regardless of
    its previous state), then send the new bulletin so only one is visible."""
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
    content = f"{mention} V-1 STATUS: {status} {emoji}\n{flavor}"
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
            try:
                await asyncio.wait_for(_send_watch_command_notice("OFFLINE"), timeout=8)
            except Exception as e:
                logger.debug(f"Shutdown announce failed or timed out: {e}")
    except Exception:
        logger.debug("Shutdown announce threw an unexpected error")

    # Flush DataStore before closing, but don't block indefinitely
    try:
        if DATASTORE:
            try:
                await asyncio.wait_for(DATASTORE.shutdown(), timeout=15)
            except asyncio.TimeoutError:
                logger.warning("DataStore shutdown timed out; proceeding with close.")
            except Exception as e:
                logger.debug(f"DataStore shutdown failed: {e}")
    except Exception:
        logger.debug("Error during DataStore shutdown sequence")

    try:
        await asyncio.wait_for(bot.close(), timeout=10)
    except Exception:
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
        await RECONCILE_LOCK.acquire()
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
            try:
                if monthly:
                    MONTHLY_AUDIT_PENDING = False
            except Exception:
                pass
            RECONCILE_LOCK.release()
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

        await RECONCILE_LOCK.acquire()
        try:
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
        finally:
            RECONCILE_LOCK.release()
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


def _save_activity_status_last_check(check_time: datetime):
    """Save the timestamp of the last activity status check."""
    try:
        tmp_path = ACTIVITY_STATUS_LAST_CHECK_PATH + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump({"last_check_time": check_time.isoformat()}, f, indent=2)
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
        logger.debug(f"Failed to save activity status last check: {e}")


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

            message = f"⚔️ **{member_name}** has returned from the Reserves and stands ready for duty once more."

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
                            t = t.astimezone(tz=None).replace(tzinfo=None)
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
                            t = t.astimezone(tz=None).replace(tzinfo=None)
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
                        last_post_dt = last_post_dt.astimezone(tz=None).replace(
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
                            last_post_dt = last_post_dt.astimezone(tz=None).replace(
                                tzinfo=None
                            )
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
                    old_updated_at = None
                    if isinstance(old_entry, dict):
                        old_status = old_entry.get("status")
                        old_updated_at = old_entry.get("updated_at")
                    elif isinstance(old_entry, str):
                        old_status = old_entry

                    # Only notify for status changes if not first check and member is transitioning to a new state
                    # For inactive->active, only notify if they were marked inactive at least 7 days ago
                    # (prevents notifying brand-new members found in old records)
                    should_notify = False
                    if (
                        not is_first_check
                        and old_status
                        and old_status != current_status
                    ):
                        if current_status == "active" and old_status == "inactive":
                            # inactive->active: only notify if member was inactive for a while
                            # (i.e., marked inactive at least 7 days ago)
                            if old_updated_at:
                                try:
                                    last_update = datetime.fromisoformat(old_updated_at)
                                    if last_update.tzinfo is not None:
                                        last_update = last_update.astimezone(
                                            tz=None
                                        ).replace(tzinfo=None)
                                    days_inactive = (
                                        check_start_time - last_update
                                    ).days
                                    should_notify = days_inactive >= 7
                                except Exception:
                                    should_notify = False
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
                                changes.append((member, old_status, current_status))
                        except Exception:
                            pass
                except Exception:
                    continue

            # Step 4: Preserve status for members not rechecked
            for uid, status in prev_status.items():
                if uid not in new_status_map:
                    new_status_map[uid] = status

            # Save updated data
            # Note: _save_member_last_post_times already saves last_check_time,
            # so we don't call _save_activity_status_last_check separately
            # (that would overwrite and destroy member_last_posts data)
            _save_activity_status(new_status_map)
            _save_member_last_post_times(member_last_posts)

            # Send notifications for changes
            for member, old, new in changes:
                try:
                    await _send_activity_status_notification(guild, member, old, new)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.exception(
                        f"Failed to notify activity change for {member.id}: {e}"
                    )

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

        if not veteran_channel and not studs_channel and not black_laurels_channel:
            logger.warning("No promotion channels available")
            return

        # Load tracking data
        tracking = _load_promotion_tracking()
        notifications_sent = 0

        # Get Watch Command role for mentions
        watch_command_role = discord.utils.get(guild.roles, name="Watch Command")
        watch_command_mention = (
            watch_command_role.mention if watch_command_role else "@Watch Command"
        )

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
                        watch_brother_role = discord.utils.get(guild.roles, name="Watch Brother")
                        # Strip rank prefix from display name to avoid "Watch Brother Watch Brother X"
                        stripped_name = member.display_name
                        for prefix in ("Watch Brother", "Watch Sister"):
                            if stripped_name.lower().startswith(prefix.lower()):
                                stripped_name = stripped_name[len(prefix):].lstrip()
                                break
                        # Format: user mention, or if not mentionable use rank role + name
                        if watch_brother_role:
                            member_line = f"{watch_brother_role.mention} {stripped_name}"
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
                # Only notify if: 1) displayed studs increased since last check, 2) still owe more studs
                if is_veteran_or_higher and studs_channel:
                    # Calculate current studs entitlement
                    studs_time = weeks_in_server // 4
                    studs_aar = aar_points // 400
                    earned_studs = min(studs_time, studs_aar)

                    # Count currently displayed studs from nickname
                    dn = str(member.nick or member.display_name or "")
                    displayed_cer = dn.count("◆")
                    displayed_elec = dn.count("●")
                    displayed_plas = dn.count("○")
                    displayed_studs = (
                        displayed_cer * 25 + displayed_elec * 5 + displayed_plas
                    )

                    # First run: initialize tracking without notifying
                    if "last_studs_displayed" not in user_tracking:
                        user_tracking["last_studs_displayed"] = displayed_studs
                    last_displayed_studs = user_tracking["last_studs_displayed"]
                    # Only notify if they displayed studs (new or same) AND still owe more studs
                    if (
                        displayed_studs >= last_displayed_studs
                        and displayed_studs < earned_studs
                    ):
                        new_studs = displayed_studs - last_displayed_studs
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
                    # Always update tracking to reflect current displayed studs
                    user_tracking["last_studs_displayed"] = displayed_studs

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

                # Update tracking
                if user_tracking:
                    tracking[user_id] = user_tracking

            except Exception as e:
                logger.debug(f"Promotion check failed for member {member.id}: {e}")
                continue

        # Save tracking data
        _save_promotion_tracking(tracking)

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
    "Oathsworn",
    "Watch Veteran",
    "Watch Brother",
]

# Canonical list of known home chapters for lookup
HOME_CHAPTERS = [
    "Angels of Vengeance",
    "Black Templars",
    "Blood Angels",
    "Blood Ravens",
    "Carcharodons",
    "Cowled Wardens",
    "Crimson Fists",
    "Dark Angels",
    "Dark Krakens",
    "Death Spectres",
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
    "High Command",
]

# Restrict commands to a specific channel (demo/training)
ALLOWED_COMMAND_CHANNELS = {"❖⋅data-vault⋅❖"}

# Kill Team forum/thread configuration
# Populate `ALLOWED_KT_FORUM_PARENT_IDS` with forum (parent) channel IDs
# that host Kill Team posts. Example: {123456789012345678, 987654321098765432}
ALLOWED_KT_FORUM_PARENT_IDS: set[int] = set([1433351293103112202, 1458255656682258504])

# Hard-coded allowlist of Kill Team role IDs that may be used with
# /tally_deeds when invoked from Kill Team posts. Populate with ints.
ALLOWED_KT_ROLE_IDS: set[int] = set(
    [
        1449257158641455265,
        1444348999401210037,
        1458254715942080543,
        1458254904819974386,
        1433355179020914688,
    ]
)

# Optional mapping: forum parent id -> set of company
# 4im sorry team i spilled coffee on ole IDs that own
# the Kill Teams in that forum. Populate as needed to enable Lt/Captain checks.
FORUM_PARENT_COMPANY_ROLE_IDS: dict[int, set[int]] = {}

# Optional set of role IDs that are considered "company" roles. Populated
# automatically if FORUM_PARENT_COMPANY_ROLE_IDS is used, or filled manually.
COMPANY_ROLE_IDS: set[int] = set()


def is_allowed_channel(interaction: discord.Interaction):
    try:
        ch = interaction.channel
        # determine invoked command name if possible
        cmd_name = None
        try:
            cmd_name = getattr(getattr(interaction, "command", None), "name", None)
        except Exception:
            cmd_name = None
        if not cmd_name:
            try:
                data = getattr(interaction, "data", {}) or {}
                cmd_name = data.get("name")
            except Exception:
                cmd_name = None

        name = getattr(ch, "name", None)
        # Channel-specific policy:
        # - ❖⋅arming-chamber⋅❖: only /forge_rite and /litany_of_function
        if name == "❖⋅arming-chamber⋅❖":
            return cmd_name in ("forge_rite", "set_rite", "litany_of_function")
        # - ❖⋅data-vault⋅❖: everything except /forge_rite (litany allowed)
        if name == "❖⋅data-vault⋅❖":
            return (
                cmd_name is not None
                and cmd_name != "forge_rite"
                and cmd_name != "set_rite"
            ) or cmd_name == "litany_of_function"

        # Fallback: respect configured allowed channel IDs or names
        allowed_ids = set((CONFIG.get("allowed_command_channel_ids") or []))
        if allowed_ids and hasattr(ch, "id"):
            return str(ch.id) in {str(x) for x in allowed_ids}
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


def _is_techmarine_or_forgemaster(
    user: discord.User | discord.Member,
) -> Tuple[bool, str]:
    """Return (allowed, primary_role_key).
    primary_role_key is one of: 'forgemaster', 'techmarine', or '' for none.
    """
    try:
        names = {n.lower() for n in _canonical_role_names(user)}
    except Exception:
        names = set()
    if any("forgemaster" in n for n in names):
        return True, "forgemaster"
    if any("techmarine" in n for n in names):
        return True, "techmarine"
    return False, ""


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


def _extract_killteam_name(name: str) -> str:
    """Return a display-friendly Kill Team name by stripping the 'Kill Team' prefix.
    Handles optional separators like ':', '-', and varying whitespace/case.
    If no match, returns the original name (or 'Unknown' if empty).
    Ignores role names like 'Kill Team Champion' that aren't actual kill teams.
    """
    try:
        # Skip non-KT role names that start with "Kill Team"
        if name and name.lower().strip() == "kill team champion":
            return name or "Unknown"
        m = re.match(r"(?i)\s*kill\s*team\s*[:\-]?\s*(.+)", (name or ""))
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
    - Exact case-insensitive match against entries in `KILL_TEAMS`.
    - If a role name contains the canonical suffix (e.g., 'Solomon'), map to
      'Kill Team Solomon'.
    - If a role name uses a 'Kill Team' prefix (e.g., 'Kill Team: Solomon'),
      normalize with `_extract_killteam_name` and match.

    Returns the canonical `KILL_TEAMS` entry on match, else `None`.
    """
    try:
        roles = getattr(member, "roles", []) or []
        # map lower->canonical for fast lookup
        canonical_map = {kt.lower(): kt for kt in KILL_TEAMS}
        # suffixes (e.g., 'solomon') for fuzzy matching
        suffixes = [kt.lower().replace("kill team", "").strip() for kt in KILL_TEAMS]

        for r in roles:
            rn = (getattr(r, "name", "") or "").strip()
            if not rn:
                continue
            low = rn.lower()
            # 1) exact canonical match
            if low in canonical_map:
                return canonical_map[low]
            # 2) suffix contained in role name
            for kt, suf in zip(KILL_TEAMS, suffixes):
                if suf and suf in low:
                    return kt
            # 3) normalized 'Kill Team' prefixed roles
            extracted = _extract_killteam_name(rn)
            if extracted and extracted.lower() in {s for s in suffixes if s}:
                # find the canonical entry containing the extracted token
                for kt in KILL_TEAMS:
                    if extracted.lower() in kt.lower():
                        return kt
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


def is_watch_command(user: discord.User | discord.Member):
    # Define "Watch Command" as Sergeant and higher (including staff roles above it)
    return is_sergeant_or_higher(user)


def check_tally_deeds_permissions_in_kt_post(
    interaction: discord.Interaction,
    kt_role: Optional[discord.Role],
    target: Optional[discord.Member],
) -> Tuple[bool, Optional[str]]:
    """Special permission gating when `/tally_deeds` is invoked inside
    a Kill Team forum post (thread).

    Returns (handled, error_message).
    - handled == False: caller is NOT in a KT post context; caller should
      fall through to existing permission checks.
    - handled == True and error_message is None: permission granted for
      KT-post invocation; proceed with command.
    - handled == True and error_message is str: deny with that message.
    """
    try:
        ch = getattr(interaction, "channel", None)
        if ch is None:
            return False, None
        # Duck-type: Threads have a `parent` attribute and are instances of discord.Thread
        is_thread = (
            isinstance(ch, discord.Thread)
            if hasattr(discord, "Thread")
            else getattr(ch, "type", None) == discord.ChannelType.public_thread
        )
        parent = getattr(ch, "parent", None)
        parent_id = getattr(parent, "id", None)
        ch_id = getattr(ch, "id", None)

        # KT context if either:
        # - invocation is inside a thread whose parent (forum) is in allowed list
        # - invocation is inside the forum channel itself and its id is in allowed list
        is_kt_context = False
        try:
            if (
                is_thread
                and parent_id is not None
                and parent_id in ALLOWED_KT_FORUM_PARENT_IDS
            ):
                is_kt_context = True
            elif ch_id is not None and ch_id in ALLOWED_KT_FORUM_PARENT_IDS:
                is_kt_context = True
        except Exception:
            is_kt_context = False

        if not is_kt_context:
            # Not a Kill Team post we care about; let existing checks run
            return False, None

        # Inside a configured Kill Team post: enforce special rules.
        caller = interaction.user
        caller_role_names = _canonical_role_names(caller)
        allowed_ranks = {
            "Watch Sergeant",
            "Watch Lieutenant",
            "Watch Captain",
            "Forgemaster",
        }
        if not any(r in caller_role_names for r in allowed_ranks):
            return (
                True,
                "This command in Kill Team posts is restricted to Sergeants leading this Kill Team and Lieutenants or Captains in this Company.",
            )

        # Validate provided Kill Team role (if given)
        if kt_role is not None:
            try:
                krid = int(getattr(kt_role, "id", 0) or 0)
            except Exception:
                return True, "Invalid Kill Team role provided."
            # Forgemaster path will perform name-based validation below; do
            # not enforce the global ALLOWED_KT_ROLE_IDS membership for them
            # here (avoids blocking Forgemaster when the allowlist contains
            # different IDs such as forum/channel IDs).
            if "Forgemaster" not in caller_role_names:
                if ALLOWED_KT_ROLE_IDS and krid not in ALLOWED_KT_ROLE_IDS:
                    return (
                        True,
                        "The specified Kill Team role is not permitted in this context.",
                    )

        # Build caller and target role id sets
        try:
            caller_role_ids = {
                int(getattr(r, "id", 0)) for r in getattr(caller, "roles", [])
            }
        except Exception:
            caller_role_ids = set()
        try:
            target_role_ids = (
                {int(getattr(r, "id", 0)) for r in getattr(target, "roles", [])}
                if target
                else set()
            )
        except Exception:
            target_role_ids = set()

        # Sergeant rules
        if "Watch Sergeant" in caller_role_names:
            # Sergeant may only operate for their own Kill Team.
            # They must have a KT role among ALLOWED_KT_ROLE_IDS and if kt_role arg provided it must match theirs.
            sergeant_kt = caller_role_ids & (ALLOWED_KT_ROLE_IDS or set())
            if not sergeant_kt:
                return (
                    True,
                    "Sergeants must have an assigned Kill Team role to use this command in Kill Team posts.",
                )
            if kt_role is not None:
                if int(getattr(kt_role, "id", 0) or 0) not in sergeant_kt:
                    return (
                        True,
                        "Sergeants may only specify their own Kill Team role when running this command here.",
                    )
            else:
                # No kt_role arg: require a target who shares a KT role with the sergeant
                if not target:
                    return (
                        True,
                        "Sergeants must specify a target Brother or their Kill Team role when running this command in a Kill Team post.",
                    )
                if not (sergeant_kt & target_role_ids):
                    return True, "Target is not a member of the Sergeant's Kill Team."
            # If kt_role provided and target provided, also ensure target has that role
            if kt_role is not None and target is not None:
                if int(getattr(kt_role, "id", 0) or 0) not in target_role_ids:
                    return (
                        True,
                        "Target member does not belong to the specified Kill Team.",
                    )

            # All sergeant checks passed
            return True, None

        # Forgemaster: may run in any KT post, but args must align with the
        # Kill Team associated with this thread (no company restriction).
        if "Forgemaster" in caller_role_names:
            # Infer kill team name from thread or parent
            thread_name = getattr(ch, "name", None) or ""
            if not thread_name:
                thread_name = getattr(parent, "name", None) or ""
            thread_kt = (
                _extract_killteam_name(thread_name).lower() if thread_name else ""
            )

            # If a kt_role was provided, validate role id and that its name matches thread
            if kt_role is not None:
                try:
                    krid = int(getattr(kt_role, "id", 0) or 0)
                except Exception:
                    return True, "Invalid Kill Team role provided."
                # Forgemaster: allow any KT role id, but require the role's
                # name to match the thread (so a Forgemaster cannot run
                # Kill Team WiFi actions from the Kill Team Solomon channel).
                kt_name = _extract_killteam_name(getattr(kt_role, "name", "")).lower()
                if thread_kt and not (thread_kt in kt_name or kt_name in thread_kt):
                    return (
                        True,
                        "The specified Kill Team role does not match this thread.",
                    )
                if target is not None and krid not in target_role_ids:
                    return (
                        True,
                        "Target member does not belong to the specified Kill Team.",
                    )
                return True, None

            # No kt_role provided: require a target whose KT role matches the thread
            if target is None:
                return (
                    True,
                    "Forgemaster must specify a Kill Team role or a target when using this command in a Kill Team post.",
                )
            # Find target's KT roles (by allowed IDs if configured, else any role whose name looks like a KT)
            target_kt_roles = []
            for r in getattr(target, "roles", []) or []:
                try:
                    rid = int(getattr(r, "id", 0) or 0)
                except Exception:
                    rid = 0
                if ALLOWED_KT_ROLE_IDS:
                    if rid in ALLOWED_KT_ROLE_IDS:
                        target_kt_roles.append(r)
                else:
                    # Heuristic: role name contains 'kill' and 'team'
                    rn = (getattr(r, "name", "") or "").lower()
                    if "kill" in rn and "team" in rn:
                        target_kt_roles.append(r)
            if not target_kt_roles:
                return True, "Target member has no recognized Kill Team role."
            # Ensure at least one target KT role matches the thread name
            match = False
            for r in target_kt_roles:
                rn = _extract_killteam_name(getattr(r, "name", "")).lower()
                if thread_kt and (thread_kt in rn or rn in thread_kt):
                    match = True
                    break
            if not match:
                return True, "Target member's Kill Team does not match this thread."
            return True, None

        # Lieutenant / Captain rules
        # Determine owning company roles for this forum parent
        owning_company_ids = FORUM_PARENT_COMPANY_ROLE_IDS.get(parent_id, set())
        if not owning_company_ids:
            # If no mapping configured, deny to be conservative
            return (
                True,
                "Kill Team post not configured with an owning company; contact an administrator.",
            )
        if not (caller_role_ids & owning_company_ids):
            return (
                True,
                "You must belong to the company that owns this Kill Team post to run this command here.",
            )

        # Ensure a kt_role was provided and the target (if any) belongs to it
        if kt_role is None:
            return (
                True,
                "Lieutenants and Captains must specify a Kill Team role when using this command in a Kill Team post.",
            )
        krid = int(getattr(kt_role, "id", 0) or 0)
        if ALLOWED_KT_ROLE_IDS and krid not in ALLOWED_KT_ROLE_IDS:
            return (
                True,
                "The specified Kill Team role is not permitted in this context.",
            )
        if target is None:
            return (
                True,
                "You must specify a target Brother when running this command for a Kill Team here.",
            )
        if krid not in target_role_ids:
            return True, "Target member does not belong to the specified Kill Team."

        # Passed Lt/Captain checks
        return True, None
    except Exception:
        # On unexpected failure, deny with a safe message
        try:
            logger.exception("KT permission check failure")
        except Exception:
            pass
        return True, "Permission check failed; contact an administrator."


def can_reconcile_records(user: discord.User | discord.Member):
    # Only Watch Master and Forgemaster, or whitelisted user IDs for these rites
    admin_ids = set(str(x) for x in (CONFIG.get("admin_user_ids") or []))
    uid = str(getattr(user, "id", None))
    if uid in admin_ids:
        return True

    perms = CONFIG.get("permissions", {}) or {}
    roles_union: set[str] = set()
    ids_union: set[str] = set()
    for key in (
        "reconcile_records",
        "sanctify_battle_records",
        "audit_archive_discrepancies",
    ):
        block = perms.get(key, {}) or {}
        for r in block.get("roles") or []:
            roles_union.add(str(r))
        for i in block.get("user_ids") or []:
            ids_union.add(str(i))

    if not roles_union:
        roles_union = {"Watch Master", "Forgemaster"}

    if uid in ids_union:
        return True

    names = _canonical_role_names(user)
    return any(r in names for r in roles_union)


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


def _resolve_company_roles_from_text(
    guild: Optional[discord.Guild], text: str
) -> List[discord.Role]:
    """Parse a text argument to resolve one or more company roles.
    Accepts role mentions (<@&ID>) and case-insensitive role names containing 'company'.
    Deduplicates results and preserves input order when possible.
    """
    roles: List[discord.Role] = []
    if not guild or not getattr(guild, "roles", None):
        return roles
    by_id = {str(getattr(r, "id", "")): r for r in guild.roles}
    by_name_lower = {str(getattr(r, "name", "")).lower(): r for r in guild.roles}
    seen: set[str] = set()
    # 1) Mentions by ID
    try:
        for m in re.finditer(r"<@&(?P<id>\d+)>", text or ""):
            rid = m.group("id")
            r = by_id.get(str(rid))
            if r and str(getattr(r, "id", "")) not in seen:
                roles.append(r)
                seen.add(str(getattr(r, "id", "")))
    except Exception:
        pass
    # 2) Names: split on commas and whitespace; match case-insensitively
    try:
        parts = [p.strip() for p in re.split(r"[,\n]+|\s{2,}", text or "") if p.strip()]
        for p in parts:
            low = p.lower()
            r = None
            # Prefer exact name match
            r = by_name_lower.get(low)
            if not r:
                # Fallback: contains 'company' token
                for rn, ro in by_name_lower.items():
                    if ("company" in rn) and (low in rn or rn in low):
                        r = ro
                        break
            if r and str(getattr(r, "id", "")) not in seen:
                roles.append(r)
                seen.add(str(getattr(r, "id", "")))
    except Exception:
        pass
    # Filter to roles that look like company roles
    filtered: List[discord.Role] = []
    for r in roles:
        try:
            rn = (getattr(r, "name", "") or "").lower()
            if "company" in rn:
                filtered.append(r)
        except Exception:
            continue
    return filtered


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
                    f"Weekly maintenance loop started ({day_name} {SCHEDULE_WEEKLY_MAINTENANCE_HOUR}:00 ET, "
                    f"sanctify {SCHEDULE_WEEKLY_MAINTENANCE_INGEST_SPAN_DAYS}-day span + full audit)."
                )
    except Exception:
        logger.exception("Failed to start weekly maintenance loop")

    # Start honours runner loop (posts monthly honours)
    try:
        if not _scheduled_honours_runner.is_running():
            _scheduled_honours_runner.start()
            logger.info(
                "Honours runner loop started (15-min interval, posts at 1 AM UTC on 1st of month)."
            )
    except Exception:
        logger.exception("Failed to start honours runner loop")


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


@bot.tree.command(
    name="litany_of_function",
    description="Describe the duties of Jericho Logi-Scribe Servitor V-1.",
)
async def litany_of_function(interaction: discord.Interaction):
    if not (is_watch_command(interaction.user) and is_allowed_channel(interaction)):
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
            for r in pool:
                try:
                    if r not in remaining:
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
    if not (is_watch_command(interaction.user) and is_allowed_channel(interaction)):
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
# Calculated based on worst-case forge_rite output (~1100 chars overhead)
# to stay under Discord's 2000 char message limit
MAX_RITE_LENGTH = 700

# Chapter-specific blessings keyed by home chapter name
CHAPTER_BLESSINGS: Dict[str, str] = {
    "Angels of Vengeance": "The wrath of the Lion courses through your warplate.",
    "Black Templars": "No pity, no remorse, no fear—your armor embodies the Eternal Crusade.",
    "Blood Angels": "By the Blood of Sanguinius, your armor is sanctified.",
    "Blood Ravens": "Knowledge is power; guard it well within these sacred plates.",
    "Carcharodons": "From the void you came, and to the void your enemies shall fall.",
    "Cowled Wardens": "In shadow you watch, from shadow you strike—your armor bears witness.",
    "Crimson Fists": "The fist of Dorn strikes true; let your armor be unyielding.",
    "Dark Angels": "The secrets of the First are woven into your warplate's spirit.",
    "Dark Krakens": "From the abyssal depths, your armor rises to crush the foe.",
    "Death Spectres": "The shroud of death clings to your armor; let enemies despair.",
    "Exorcists": "Thrice-bound against the Warp, your armor stands inviolate.",
    "Flesh Tearers": "The Red Thirst is tempered within your armor's adamantine heart.",
    "Genesis Chapter": "The purity of Guilliman's line flows through these blessed plates.",
    "Hawk Lords": "Swift as the raptor, your armor bears you to righteous war.",
    "Imperial Fists": "Fortify your spirit as these plates fortify your flesh.",
    "Iron Hands": "The flesh is weak, but your armor is the strength of iron.",
    "Iron Hounds": "Relentless as the hound, your armor knows no surrender.",
    "Knights of the Raven": "In cunning silence, your armor conceals the Emperor's justice.",
    "Lamenters": "Though cursed, your armor shall not fail—for those we cherish, we die.",
    "Mentors": "Precision and wisdom are encoded in your warplate's machine-spirit.",
    "Minotaurs": "The fury of the bull charges forth; your armor is wrath incarnate.",
    "Raptors": "Silent and lethal, your armor whispers death to the enemies of Man.",
    "Raven Guard": "In the shadow of the Raven, your armor moves unseen.",
    "Red Scorpions": "Purity above all; your armor meets the Apothecary's exacting standards.",
    "Red Templars": "Speed and fury—your armor is the storm upon the enemy.",
    "Salamanders": "Into the fires of battle, your armor shields the innocent.",
    "Sons of Medusa": "Steel and logic strengthen your armor against all adversity.",
    "Space Wolves": "The spirit of Fenris howls within your blessed warplate.",
    "Storm Giants": "Thunder rolls and tempests rage; your armor channels the storm.",
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
    "Lord Executioner": "Blade of the Fortress, Lord Executioner",
    # Specialists
    "Watch Chaplain": "Keeper of the faith, Watch Chaplain",
    "Watch Apothecary": "Guardian of the gene-seed, Watch Apothecary",
    "Watch Librarian": "Warden of the Immaterium, Watch Librarian",
    "Watch Techmarine": "Servant of the Omnissiah, Watch Techmarine",
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
        "In shadow your service is marked; in shadow it is honored.",
        "Your studs gleam beneath the cowl of duty.",
        "Silent watchers know the weight of each mark you bear.",
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
        "Relentless as the hunt—your studs mark each pursuit.",
        "No quarry escapes one whose brow bears such marks.",
        "The pack honors your enduring service.",
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
        "Speed and fury—your studs earned at rapid pace.",
        "The storm of your service accumulates these marks.",
        "Momentum and dedication drive each stud earned.",
    ],
    "Salamanders": [
        "Vulkan's flame forges each mark upon your brow.",
        "Into the fires of service, your studs emerge tempered.",
        "Each mark protects those who cannot protect themselves.",
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
        "Thunder rolls with each mark you earn.",
        "The tempest of your service is measured in these studs.",
        "Lightning-struck and storm-forged, your marks gleam.",
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

# Ordo Xenos / Deathwatch-wide honor phrases (combined with chapter-specific flavor)
ORDO_XENOS_HONORS: List[str] = [
    "The Ordo Xenos records your vigilance against the alien threat.",
    "Your service to the Long Watch brings honor to the Deathwatch.",
    "The Vigil Eternal takes note of your unwavering dedication.",
    "Watch Fortress Jericho commends your steadfast duty.",
    "The Inquisition acknowledges your service to humanity.",
    "By the Vigil Oathstone, your deeds are inscribed.",
    "The Long Watch is strengthened by your commitment.",
    "Your vigilance shields the Imperium from the xenos threat.",
    "The alien recoils before one whose service is so marked.",
    "Each stud proclaims death to the enemies of Mankind.",
    "The Deathwatch honors those who stand the Long Vigil.",
    "Your dedication to the Vigil is beyond question.",
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

# Tier-based general venerations (fallback when chapter/rank specific not available)
SERVICE_STUDS_VENERATIONS_TIER1: List[str] = [
    # 1-4 studs - Proven Warrior
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
]

SERVICE_STUDS_VENERATIONS_TIER2: List[str] = [
    # 5-24 studs - Venerated Elder
    "Your service studs proclaim a warrior whose experience shapes the Watch itself.",
    "Few bear such marks of enduring service—honor is yours by right.",
    "The weight of your studs reflects the weight of your deeds.",
    "Younger brothers look to your studs and see their own path illuminated.",
    "Your marks of service are a legacy etched in adamantium and honor.",
    "The Watch is strengthened by warriors such as you.",
    "Your service studs tell tales that will echo through the centuries.",
    "The machine-spirit bows before one whose service spans so many campaigns.",
    "Your studs speak of war without end, and duty without question.",
    "Veterans of the Long Watch look upon your marks with reverence.",
    "The annals of Jericho record your deeds with each gleaming stud.",
    "Your studs are a chronicle of the Emperor's wrath made manifest.",
    "The fortress itself knows the tread of one so long in service.",
    "Your experience is a weapon as mighty as any bolter or blade.",
    "The studs upon your brow have witnessed the fall of countless xenos.",
]

SERVICE_STUDS_VENERATIONS_TIER3: List[str] = [
    # 25+ studs - Legendary Status
    "Your service studs rival those of the Ancients themselves.",
    "The studs upon your brow are a saga written in silver and blood.",
    "Even the machine-spirits whisper reverence for one so marked by duty.",
    "Your service marks proclaim a living legend of the Deathwatch.",
    "Generations of brothers have fought under the shadow of your example.",
    "The Watch Fortress trembles with honor at your presence.",
    "Your studs are beyond counting—a testament to duty eternal.",
    "The very stones of Jericho remember when your service began.",
    "Your marks of service would humble the most storied of veterans.",
    "The Omnissiah himself takes note of such devotion to duty.",
    "Legends are spoken of you in whispers throughout the Long Watch.",
    "Your studs are a constellation of glory upon your brow.",
    "To serve beside you is to walk with a demigod of war.",
    "The weight of your studs anchors the fortress in times of doubt.",
    "None who look upon your marks can question your commitment.",
    "Your service is measured not in studs, but in epochs.",
    "The Machine God records your deeds in the holiest of data-vaults.",
    "Your name shall be sung in the halls of Jericho for eternity.",
]

# Tiered milestone intros based on stud number being earned
# Tier 1: 1-4 (first marks), Tier 2: 5-24 (seasoned), Tier 3: 25+ (legendary)
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
    "LEGENDARY SERVICE! Even the Armorium's oldest servitors pause to record this.",
    "A ceramite stud! The highest honor the Watch can bestow!",
    "The Watch Fortress itself trembles at such monumental service!",
    "Legends walk among us—your marks proclaim it to all!",
    "The chronicles of Jericho inscribe this momentous milestone!",
]

# Special milestone announcements for exact stud numbers
SERVICE_STUDS_SPECIAL_MILESTONES: Dict[int, str] = {
    1: "**FIRST SERVICE STUD** — A warrior's journey begins!",
    5: "**FIFTH SERVICE STUD** — A proven veteran emerges!",
    10: "**TENTH SERVICE STUD** — A decade of unwavering duty!",
    25: "**CERAMITE STUD EARNED** — Legendary status achieved!",
    50: "**FIFTY SERVICE STUDS** — A living saga of war!",
    75: "**SEVENTY-FIVE STUDS** — The Ancients themselves take note!",
    100: "**ONE HUNDRED STUDS** — A monument of duty made flesh!",
}

# Deathwatch-themed opening phrases for service stud announcements
DEATHWATCH_STUD_OPENINGS: List[str] = [
    "Hear this, brothers of Watch Fortress Jericho!",
    "Let it be known throughout the Long Watch!",
    "The Fortress records a mark of honored service!",
    "By the Vigil Oathstone, witness this attestation!",
    "The chronicles of Jericho inscribe a new honor!",
    "Heed this proclamation, warriors of the Deathwatch!",
    "The Watch Eternal takes note of devoted service!",
]

# Deathwatch themed closings
DEATHWATCH_STUD_CLOSINGS: List[str] = [
    "Vigilus Aeterna. The Watch endures.",
    "For the Emperor and the Long Watch!",
    "The Vigil continues. Ave Imperator.",
    "By bolt and blade, the Watch persists.",
    "In Vigilance, Eternal.",
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

    # Determine tier based on displayed studs
    if displayed_studs <= 4:
        tier = 1
    elif displayed_studs <= 24:
        tier = 2
    else:
        tier = 3

    # Get role mentions
    chief_apothecary_role = discord.utils.get(guild.roles, name="Chief Apothecary")
    watch_apothecary_role = discord.utils.get(guild.roles, name="Watch Apothecary")
    watch_command_role = discord.utils.get(guild.roles, name="Watch Command")

    chief_mention = chief_apothecary_role.mention if chief_apothecary_role else "Chief Apothecary"
    apoth_mention = watch_apothecary_role.mention if watch_apothecary_role else "Watch Apothecary"
    cmd_mention = watch_command_role.mention if watch_command_role else "Watch Command"

    # Get Watch Brother role for honoring notification
    watch_brother_role = discord.utils.get(guild.roles, name="Watch Brother")
    wb_mention = watch_brother_role.mention if watch_brother_role else "Watch Brother"

    # Get emojis for rank and chapter
    rank_emoji = _get_rank_emoji(guild, member_rank_name)
    chapter_emoji = _get_emoji_by_name(guild, member_chapter) if member_chapter != "Unknown" else None

    # Compute stud pips display: ◆=25, ●=5, ○=1
    ceramite = displayed_studs // 25
    remainder = displayed_studs % 25
    electrum = remainder // 5
    plasteel = remainder % 5
    studs_pips = "◆" * ceramite + "●" * electrum + "○" * plasteel
    if not studs_pips:
        studs_pips = "○"  # First stud

    # Build embed similar to forge_rite format
    embed = discord.Embed(
        title="᛭⋅ MARK OF SERVICE ⋅᛭",
        description="*⌾ Watch Fortress Jericho ⌾*",
        color=0xC0C0C0,  # Silver for service studs
    )

    # Bearer field with rank emoji
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
    if member_chapter != "Unknown":
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        bearer_value += f"\nLineage: {chapter_prefix}{member_chapter}"
    bearer_value += f"\nService Studs: **[{studs_pips}]** ({displayed_studs})"
    embed.add_field(name="▸ Bearer", value=bearer_value, inline=True)

    # Calculate visual pip change (what actually gets added to nickname)
    # Clamp prev_studs to 0 minimum to handle edge cases in preview
    prev_studs = max(0, displayed_studs - new_studs)
    prev_ceramite = prev_studs // 25
    prev_electrum = (prev_studs % 25) // 5
    prev_plasteel = prev_studs % 5
    
    curr_ceramite = displayed_studs // 25
    curr_electrum = (displayed_studs % 25) // 5
    curr_plasteel = displayed_studs % 5
    
    # Compute net change in each pip type
    delta_ceramite = curr_ceramite - prev_ceramite
    delta_electrum = curr_electrum - prev_electrum
    delta_plasteel = curr_plasteel - prev_plasteel
    
    # Build visual pip change string showing what was gained
    # Show the highest tier pip that increased (the "upgrade")
    # If multiple pip types changed, show all positive deltas
    pip_changes = []
    if delta_ceramite > 0:
        pip_word = "Stud" if delta_ceramite == 1 else "Studs"
        pip_changes.append(f"+{delta_ceramite}◆ Ceramite {pip_word}")
    if delta_electrum > 0:
        pip_word = "Stud" if delta_electrum == 1 else "Studs"
        pip_changes.append(f"+{delta_electrum}● Electrum {pip_word}")
    if delta_plasteel > 0:
        pip_word = "Stud" if delta_plasteel == 1 else "Studs"
        pip_changes.append(f"+{delta_plasteel}○ Plasteel {pip_word}")
    
    if pip_changes:
        pip_change = ", ".join(pip_changes)
    else:
        # Fallback: just show raw new_studs count
        pip_change = f"+{new_studs} {stud_word}"

    # Service Record field with visual pip change
    record_value = f"**{pip_change}** Earned\n"
    record_value += f"Total: **{earned_studs}** | Displayed: **{displayed_studs}**"
    if owed_studs > 0:
        record_value += f"\nOwed: **{owed_studs}**"
    embed.add_field(name="▸ Service Record", value=record_value, inline=True)

    # Special milestone callout (prominent)
    special_milestone = SERVICE_STUDS_SPECIAL_MILESTONES.get(displayed_studs)
    if special_milestone:
        embed.add_field(name="▸ Milestone", value=special_milestone, inline=False)

    # Honor of the Long Watch: Ordo Xenos phrase + chapter-specific flavor
    ordo_honor = random.choice(ORDO_XENOS_HONORS)
    if member_chapter in CHAPTER_STUDS_FLAVOR:
        chapter_flavor = random.choice(CHAPTER_STUDS_FLAVOR[member_chapter])
        embed.add_field(name="▸ Honor of the Long Watch", value=f'*"{ordo_honor} {chapter_flavor}"*', inline=False)
    else:
        # Fallback to tier-based phrase
        if tier == 1:
            milestone_phrase = random.choice(SERVICE_STUDS_VENERATIONS_TIER1)
        elif tier == 2:
            milestone_phrase = random.choice(SERVICE_STUDS_VENERATIONS_TIER2)
        else:
            milestone_phrase = random.choice(SERVICE_STUDS_VENERATIONS_TIER3)
        embed.add_field(name="▸ Honor of the Long Watch", value=f'*"{ordo_honor} {milestone_phrase}"*', inline=False)

    # Call to action
    embed.add_field(
        name="▸ Rite of Marking",
        value=f"Report to {apoth_mention}, {chief_mention}, or {cmd_mention}",
        inline=False,
    )

    # Footer
    embed.set_footer(text="᛭⋅ Jericho Stands! ⋅᛭")

    # Content line with mentions (outside embed for pings)
    content = f"{wb_mention} {member.mention}"

    return content, embed


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
                ja = ja.astimezone(tz=None).replace(tzinfo=None)
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

        # Studs are the minimum of time-based and points-based
        return min(studs_time, studs_aar)
    except Exception:
        return 0


def _get_studs_veneration(studs_count: int) -> Optional[str]:
    """Get a random veneration phrase appropriate for the service studs count.

    Tiers:
    - 0: No veneration (newly promoted)
    - 1-4: Proven warrior
    - 5-24: Venerated elder
    - 25+: Legendary status
    """
    import random

    if studs_count <= 0:
        return None
    elif studs_count <= 4:
        return random.choice(SERVICE_STUDS_VENERATIONS_TIER1)
    elif studs_count <= 24:
        return random.choice(SERVICE_STUDS_VENERATIONS_TIER2)
    else:
        return random.choice(SERVICE_STUDS_VENERATIONS_TIER3)


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
                            wm_name = wm_name.replace("◆", "").replace("●", "").replace("○", "").strip()
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
                            cap_name = cap_name.replace("◆", "").replace("●", "").replace("○", "").strip()
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
    display_name = display_name.replace("◆", "").replace("●", "").replace("○", "").strip()

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


class ForgeRiteToggleView(discord.ui.View):
    """Toggle view for Forge Rite attestation: PC/Console (ANSI) vs Mobile (Embed)."""

    def __init__(
        self,
        text_content: str,
        embed: discord.Embed,
        bearer_mention: str,
        default: str = "ansi",
    ):
        super().__init__(timeout=None)  # No timeout - buttons work until bot restart
        self.text_content = text_content
        self.embed_obj = embed
        self.bearer_mention = bearer_mention
        self.current = default if default in ("ansi", "embed") else "ansi"
        self._ansi_max_len = 1900
        self._update_buttons()

    def _update_buttons(self):
        # Only PC/Console button remains; disable if ANSI too long
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "forge_show_ansi":
                    too_long = len(self.text_content) > self._ansi_max_len
                    child.disabled = too_long

    @discord.ui.button(
        label="PC/Console",
        style=discord.ButtonStyle.secondary,
        custom_id="forge_show_ansi",
    )
    async def show_ansi(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if len(self.text_content) > self._ansi_max_len:
            try:
                await interaction.response.send_message(
                    "PC/Console view exceeds message limit.",
                    ephemeral=True,
                )
            except Exception:
                pass
            return
        # Send ANSI view as ephemeral message (only the clicker sees it)
        try:
            await interaction.response.send_message(
                content=f"{self.bearer_mention}\n{self.text_content}",
                ephemeral=True,
            )
        except Exception:
            try:
                await interaction.followup.send(
                    "Unable to show PC/Console view.", ephemeral=True
                )
            except Exception:
                pass

    # Mobile button removed - embed is now the default public view.
    # Users can click PC/Console to get an ephemeral ANSI view.


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
@app_commands.describe(member="Member to attest")
async def _attest(interaction: discord.Interaction, member: discord.Member):
    import random

    allowed, role_key = _is_techmarine_or_forgemaster(interaction.user)
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

    # Build attestation using standardized Imperial date format
    try:
        ts = _format_imperial_date(datetime.utcnow())
    except Exception:
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    # Authority
    if role_key == "forgemaster":
        authority = "Jericho High Command"
    else:
        comp = _find_company_or_chapter(interaction.user) or "Unknown Company"
        authority = comp

    # Attesting name (strip stud pips)
    attester = getattr(interaction.user, "display_name", None) or getattr(
        interaction.user, "name", str(interaction.user.id)
    )
    attester = attester.replace("◆", "").replace("●", "").replace("○", "").strip()

    # Optional personal rite
    try:
        rite_text = await _get_user_rite(int(interaction.user.id))
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
    bearer_name = bearer_name.replace("◆", "").replace("●", "").replace("○", "").strip()

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

    # Service studs computation and veneration
    bearer_studs = _compute_member_service_studs(member)
    studs_veneration = _get_studs_veneration(bearer_studs)

    # Random Techmarine signature variation
    techmarine_signature = random.choice(TECHMARINE_SIGNATURES)

    # Random sacred Mechanicus phrase
    sacred_phrase = random.choice(SACRED_MECHANICUS_PHRASES)

    # Generate unique machine-spirit designation for the armor
    # Based on bearer ID + current timestamp to be unique per blessing
    import hashlib
    spirit_hash = hashlib.md5(f"{member.id}-{datetime.utcnow().isoformat()}".encode()).hexdigest()[:6].upper()
    # Format: PREFIX-HASH-SUFFIX (e.g., "FURY-A3C7B2-Θ")
    spirit_prefixes = ["FURY", "AEGIS", "VIGIL", "TALON", "WRATH", "PURITY", "FERRUM", "MORTIS", "VENATOR", "GLADIUS"]
    spirit_suffixes = ["Α", "Β", "Γ", "Δ", "Θ", "Λ", "Σ", "Ω", "Ξ", "Φ"]
    spirit_designation = f"{random.choice(spirit_prefixes)}-{spirit_hash}-{random.choice(spirit_suffixes)}"

    # Auto-sign: prefer Forgemaster or Techmarine mention
    try:
        company = _find_company_or_chapter(interaction.user)
        if role_key == "forgemaster":
            signer = f"{attester}, Jericho High Command"
        elif role_key == "techmarine":
            signer = f"{attester}, {company or 'Unknown Company'}"
        else:
            top_role = None
            try:
                roles = [
                    getattr(r, "name", "")
                    for r in getattr(interaction.user, "roles", [])
                    if getattr(r, "name", None)
                ]
                top_role = roles[-1] if roles else None
            except Exception:
                top_role = None
            signer = f"{top_role + ' ' if top_role else ''}{attester}"
    except Exception:
        signer = attester

    # ─────────────────────────────────────────────────────────────────────────
    # Assemble ANSI block (PC/Console view)
    # ─────────────────────────────────────────────────────────────────────────
    lines = []
    lines.append("```ansi")
    lines.append(
        "\u001b[32m=============================================================================="
    )
    lines.append("  WATCH FORTRESS JERICHO // COGITATOR-ATTESTATION")
    lines.append("  COGITATOR RITE — FORGE ATTESTATION")
    lines.append(
        "=============================================================================="
    )

    # Bearer section
    lines.append("▸ BEARER DESIGNATION")
    bearer_line = f"  {bearer_honorific} {bearer_name}"
    if bearer_title:
        bearer_line += f" • {bearer_title}"
    lines.append(bearer_line)
    if bearer_chapter:
        lines.append(f"  Lineage: {bearer_chapter}")
    if bearer_studs > 0:
        # Tiered stud display: ◆=25, ●=5, ○=1
        ceramite = bearer_studs // 25
        remainder = bearer_studs % 25
        electrum = remainder // 5
        plasteel = remainder % 5
        studs_pips = "◆" * ceramite + "●" * electrum + "○" * plasteel
        lines.append(f"  Service Studs: [{studs_pips}] ({bearer_studs})")
    lines.append("")

    # Honor of the Long Watch: Ordo Xenos phrase + chapter blessing
    ordo_honor = random.choice(ORDO_XENOS_HONORS)
    if chapter_blessing:
        lines.append("▸ HONOR OF THE LONG WATCH")
        lines.append(f'  "{ordo_honor} {chapter_blessing}"')
        lines.append("")
    else:
        lines.append("▸ HONOR OF THE LONG WATCH")
        lines.append(f'  "{ordo_honor}"')
        lines.append("")

    # Service studs veneration (if earned)
    if studs_veneration:
        lines.append("▸ MARK OF LONG SERVICE")
        lines.append(f'  "{studs_veneration}"')
        lines.append("")

    # Status
    lines.append("▸ MACHINE-SPIRIT STATUS")
    lines.append(f" Spirit Designation .... {spirit_designation}")
    lines.append("  Inspection ............ PASSED")
    lines.append("  Compliance ............ CONFIRMED")
    lines.append("  Warplate Integrity .... SANCTIFIED")
    lines.append("")

    # Consecration rite (if present)
    if rite_text:
        lines.append("▸ RITE OF CONSECRATION")
        for line in str(rite_text).splitlines():
            lines.append(f"  {line}")
        lines.append("")

    # Techmarine's affirmation
    lines.append("▸ TECHMARINE'S AFFIRMATION")
    lines.append(f'  "{techmarine_signature}"')
    lines.append("")

    # Authority block (consolidated)
    lines.append("▸ ATTESTATION")
    lines.append(f"  {attester}")
    lines.append(f"  {authority} • {ts}")
    lines.append(f"  \"{sacred_phrase}\"")
    lines.append("\u001b[0m```")

    ansi_content = "\n".join(lines)

    # ─────────────────────────────────────────────────────────────────────────
    # Build Mobile embed (condensed format)
    # ─────────────────────────────────────────────────────────────────────────
    
    # Get emojis for rank and chapter (mobile embed only - ANSI can't render them)
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
    chapter_emoji_str = _get_chapter_emoji(guild, bearer_chapter) if guild and bearer_chapter else bearer_chapter
    
    embed = discord.Embed(
        title="⚙️ COGITATOR RITE — FORGE ATTESTATION",
        description="*⌾ Watch Fortress Jericho ⌾*",
        color=0x2ECC71,
    )

    # Bearer field (condensed) with emojis
    # Split honorific if it contains a comma (e.g., "Blade of the Fortress, Lord Executioner")
    rank_prefix = f"{rank_emoji} " if rank_emoji else ""
    # Defensive pip stripping in case they survived from display name
    bearer_name = bearer_name.replace("◆", "").replace("●", "").replace("○", "").strip()
    if ", " in bearer_honorific:
        title_part, rank_part = bearer_honorific.rsplit(", ", 1)
        bearer_value = f"{rank_prefix}**{title_part},**\n**{rank_part} {bearer_name}**"
    else:
        bearer_value = f"{rank_prefix}**{bearer_honorific} {bearer_name}**"
    if bearer_title:
        bearer_value += f"\n*{bearer_title}*"
    if bearer_chapter:
        bearer_value += f"\nLineage: {chapter_emoji_str}"
    if bearer_studs > 0:
        # Tiered stud display: ◆=25, ●=5, ○=1
        ceramite = bearer_studs // 25
        remainder = bearer_studs % 25
        electrum = remainder // 5
        plasteel = remainder % 5
        studs_pips = "◆" * ceramite + "●" * electrum + "○" * plasteel
        bearer_value += f"\nService Studs: [{studs_pips}] ({bearer_studs})"
    embed.add_field(name="▸ Bearer", value=bearer_value, inline=True)

    # Status field
    embed.add_field(
        name="▸ Machine-Spirit",
        value=f"Spirit: `{spirit_designation}`\n✅ Inspection: PASSED\n✅ Compliance: CONFIRMED\n✅ Integrity: SANCTIFIED",
        inline=True,
    )

    # Honor of the Long Watch: Ordo Xenos phrase + chapter blessing
    ordo_honor_embed = random.choice(ORDO_XENOS_HONORS)
    if chapter_blessing:
        embed.add_field(
            name="▸ Honor of the Long Watch",
            value=f'*"{ordo_honor_embed} {chapter_blessing}"*',
            inline=False,
        )
    else:
        embed.add_field(
            name="▸ Honor of the Long Watch",
            value=f'*"{ordo_honor_embed}"*',
            inline=False,
        )

    # Service studs veneration (if earned)
    if studs_veneration:
        embed.add_field(
            name="▸ Mark of Long Service", value=f'*"{studs_veneration}"*', inline=False
        )

    # Consecration rite (if present)
    if rite_text:
        rite_display = str(rite_text)[:400] + ("…" if len(str(rite_text)) > 400 else "")
        embed.add_field(
            name="▸ Rite of Consecration", value=f"*{rite_display}*", inline=False
        )

    # Affirmation
    embed.add_field(
        name="▸ Techmarine's Affirmation",
        value=f'*"{techmarine_signature}"*',
        inline=False,
    )

    # Techmarine & Authority (consolidated)
    tech_value = f"**{attester}**\n{authority} • {ts}\n*\"{sacred_phrase}\"*"
    embed.add_field(name="▸ Attestation", value=tech_value, inline=False)

    # ─────────────────────────────────────────────────────────────────────────
    # Send with toggle view
    # ─────────────────────────────────────────────────────────────────────────
    try:
        view = ForgeRiteToggleView(
            text_content=ansi_content,
            embed=embed,
            bearer_mention=member.mention,
            default="embed",
        )
        # Default to embed view (mobile-friendly); PC/Console button sends ephemeral ANSI
        await interaction.response.send_message(
            content=member.mention,
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions(users=True),
            ephemeral=DEBUG_MODE,
        )
    except Exception:
        try:
            await interaction.response.send_message(
                "Failed to post attestation.", ephemeral=True
            )
        except Exception:
            pass


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
    dn = str(member.nick or member.display_name or "")
    displayed_cer = dn.count("◆")
    displayed_elec = dn.count("●")
    displayed_plas = dn.count("○")
    actual_displayed = displayed_cer * 25 + displayed_elec * 5 + displayed_plas
    
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
        can_reconcile_records(interaction.user) and is_allowed_channel(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    # Serialize concurrent invocations to avoid file races
    if RECONCILE_LOCK.locked():
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

    await RECONCILE_LOCK.acquire()
    try:
        await _reconciliation_core(interaction, span_days)
    finally:
        RECONCILE_LOCK.release()


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

    interaction_deferred = False
    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
        interaction_deferred = True
    except Exception:
        interaction_deferred = False

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
    interaction_deferred = False
    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
        interaction_deferred = True
    except Exception:
        interaction_deferred = False

    await RECONCILE_LOCK.acquire()
    try:
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
            try:
                await _reply_aar_rejection(msg, [f"Jump URL: {msg.jump_url}"] + errors)
            except Exception:
                pass
            to_react_err.append(msg)
            rejected += 1
            if scanned % 10 == 0:
                _print_progress("Ingest New AARs", scanned, scanned)
            continue
        await save_aar_record(record)
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

    return ingested, rejected


# Admin-only command to print cache sizes, dirty flags, last flush time, and cache hit/miss counters
@bot.tree.command(
    name="cache_stats", description="Show DataStore cache and flush stats (admin only)"
)
async def cache_stats(interaction: discord.Interaction):
    admin_ids = set(str(x) for x in (CONFIG.get("admin_user_ids") or []))
    if str(getattr(interaction.user, "id", None)) not in admin_ids:
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

    if not (is_watch_command(interaction.user) and is_allowed_channel(interaction)):
        await interaction.followup.send("Access denied.", ephemeral=True)
        return

    guild = interaction.guild or _resolve_notification_guild()
    if not guild:
        await interaction.followup.send("Guild not available.", ephemeral=True)
        return

    idx_veteran = _role_index("Watch Veteran")
    now = datetime.utcnow()
    mismatches: list[tuple[discord.Member, int, int]] = []

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
                            ja = ja.astimezone(tz=None).replace(tzinfo=None)
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

            # Count existing studs shown in nickname/display name
            dn = str(member.nick or member.display_name or "")
            existing_cer = dn.count("◆")
            existing_elec = dn.count("●")
            existing_plas = dn.count("○")
            existing_total = existing_cer * 25 + existing_elec * 5 + existing_plas

            if studs_count != existing_total:
                mismatches.append((member, studs_count, existing_total))
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
    comp_max = len("Computed")
    disp_max = len("Displayed")
    action_max = len("Action")
    for mem, comp, disp in mismatches:
        diff = comp - disp
        action = f"AWARD {diff}" if diff > 0 else f"REMOVE {abs(diff)}"
        name = getattr(mem, "display_name", str(getattr(mem, "id", "")))
        scomp = str(comp)
        sdisp = str(disp)
        rows.append((name, scomp, sdisp, action))
        name_max = max(name_max, len(name))
        comp_max = max(comp_max, len(scomp))
        disp_max = max(disp_max, len(sdisp))
        action_max = max(action_max, len(action))

    # Cap name width to avoid excessively wide blocks
    NAME_CAP = 36
    name_w = min(NAME_CAP, name_max)

    sep = "=" * (name_w + comp_max + disp_max + action_max + 10)

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
        + "Computed".rjust(comp_max)
        + "  "
        + "Displayed".rjust(disp_max)
        + "  "
        + "Action".rjust(action_max)
    )
    lines.append(header)
    lines.append(sep)
    for name, scomp, sdisp, action in rows:
        # Truncate name if necessary
        display_name = name if len(name) <= name_w else name[: name_w - 1] + "…"
        line = (
            "  "
            + display_name.ljust(name_w)
            + "  "
            + scomp.rjust(comp_max)
            + "  "
            + sdisp.rjust(disp_max)
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
        (name, scomp, sdisp, action)
        for name, scomp, sdisp, action in rows
        if "AWARD" in action
    ]
    removals_needed = [
        (name, scomp, sdisp, action)
        for name, scomp, sdisp, action in rows
        if "REMOVE" in action
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
    if not (is_watch_command(interaction.user) and is_allowed_channel(interaction)):
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
@app_commands.describe(limit="Optional: max number of records to reparse.")
async def reparse_records(interaction: discord.Interaction, limit: int | None = None):
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
        total = 0
        updated = 0
        failed = 0
        # Snapshot of records to process (respect optional limit)
        records_list = list(DATASTORE._records.items())
        if limit:
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
                    await DATASTORE.set_record(str(merged.get("aar_id")), merged)
                    updated += 1
            except Exception:
                failed += 1

        # Finalize progress output in terminal
        _print_progress(total_records, total_records)
        if sys.stdout.isatty():
            sys.stdout.write("\n")
            sys.stdout.flush()

        await interaction.followup.send(
            f"Reparse complete: processed={total}, updated={updated}, failed={failed}",
            ephemeral=True,
        )
    finally:
        RECONCILE_LOCK.release()


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
    # Special-case: if this is a Kill Team forum/thread post, use KT-specific
    # permission gating. Otherwise, fall through to the existing checks.
    try:
        handled, err = check_tally_deeds_permissions_in_kt_post(
            interaction, killteam, brother
        )
    except Exception:
        handled, err = False, None

    if handled:
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
    else:
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
        await interaction.response.send_message(
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
                            ja = ja.astimezone(tz=None).replace(tzinfo=None)
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
                studs_count = min(studs_time, studs_aar)
            else:
                studs_count = 0
        except Exception:
            studs_count = 0

        # Build display string using three-tier Unicode symbols:
        # - lowest: hollow circle '○' (Plasteel)
        # - mid: filled circle '●' per five (Electrum)
        # - top: diamond '◆' per twenty-five (Ceramite)
        # Append a type breakdown in parentheses using in-universe names.
        try:
            studs_symbols = ""
            if not studs_count:
                studs_display = "— (0 Plasteel)"
            else:
                # Breakdown into Ceramite (25), Electrum (5), Plasteel (1)
                ceramite_count = studs_count // 25
                electrum_count = (studs_count % 25) // 5
                plasteel_count = studs_count % 5

                studs_symbols = (
                    "◆" * ceramite_count + "●" * electrum_count + "○" * plasteel_count
                )

                parts: list[str] = []
                if ceramite_count:
                    parts.append(f"{ceramite_count} Ceramite")
                if electrum_count:
                    parts.append(f"{electrum_count} Electrum")
                if plasteel_count:
                    parts.append(f"{plasteel_count} Plasteel")
                types_str = ", ".join(parts) if parts else "0 Plasteel"
                studs_display = f"{studs_symbols} ({types_str})"

                # Compare with studs already present in the display name and add
                # an in-universe notification if there's a mismatch.
                try:
                    dn = str(display_name or "")
                    existing_cer = dn.count("◆")
                    existing_elec = dn.count("●")
                    existing_plas = dn.count("○")
                    existing_total = (
                        existing_cer * 25 + existing_elec * 5 + existing_plas
                    )
                    diff = studs_count - existing_total
                    if diff > 0:
                        # Loreful addendum when computed studs exceed what's shown
                        notif = f"(+{diff} studs earned to be awarded)"
                        studs_display = f"{studs_display} {notif}"
                    elif diff < 0:
                        # Note if the name shows more studs than computed
                        notif = f"({abs(diff)} excess stud(s) displayed)"
                        studs_display = f"{studs_display} {notif}"
                except Exception:
                    pass
        except Exception:
            studs_display = str(studs_count)
            studs_symbols = ""

        # Use in-memory records from DATASTORE
        ops_trials = 0
        siege_waves = 0
        initiation_event_times: List[datetime] = []
        for rec in DATASTORE.iter_records():
            try:
                brother_ids = rec.get("brother_ids") or []
                if str(target.id) not in brother_ids:
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
                inductee_count = sum(
                    1 for uid in all_inductees if uid != str(target.id)
                )
                if inductee_count == 0:
                    continue
                ts = rec.get("timestamp")
                try:
                    if ts:
                        t = datetime.fromisoformat(ts)
                        if t.tzinfo is not None:
                            try:
                                t = t.astimezone(tz=None).replace(tzinfo=None)
                            except Exception:
                                t = t.replace(tzinfo=None)
                        initiation_event_times.append(t)
                except Exception:
                    pass
                dclass = (rec.get("difficulty_class") or "").lower()
                if "siege" in dclass:
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
                pass
        trials_reported = (siege_waves // 15) + (ops_trials // 3)

        # Home chapter from resolved map (fallback: REDACTED)
        home_chapter = chapters_map.get(str(target.id)) if chapters_map else "REDACTED"

        # Determine Active/Inactive status: Active if any AAR in last 30 days.

        try:
            # Use in-memory records from DATASTORE
            timestamps = []
            for rec in DATASTORE.iter_records():
                if str(target.id) in (rec.get("brother_ids") or []):
                    ts = rec.get("timestamp")
                    if not ts:
                        continue
                    try:
                        t = datetime.fromisoformat(ts)
                    except Exception:
                        continue
                    if t.tzinfo is not None:
                        try:
                            t = t.astimezone(tz=None).replace(tzinfo=None)
                        except Exception:
                            t = t.replace(tzinfo=None)
                    timestamps.append(t)
            status = "Inactive"
            last_aar_date: Optional[datetime] = None
            days_since_aar: Optional[int] = None
            if timestamps:
                timestamps.sort(reverse=True)
                last_aar_date = timestamps[0]
                now = datetime.utcnow()
                days_since_aar = (now - last_aar_date).days
                cutoff = now - timedelta(days=28)
                for t in timestamps:
                    if t >= cutoff:
                        status = "Active"
                        break
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
            name_val = re.sub(r"[◆●○]+", "", name_raw).strip()
            if not name_val:
                name_val = name_raw
        except Exception:
            name_val = str(display_name or getattr(target, "display_name", "Unknown"))
        status_val = str(status or "Unknown")
        roster_items.append(
            {
                "name": name_val,
                "status": status_val,
                "aar": aar_val,
                "gene": gene_val,
                "armory": armory_val,
                "studs_symbols": studs_symbols,
                "studs_count": studs_count,
                "role_names": list(_canonical_role_names(target)),
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
            # Build a structured embed to minimize wrapping
            try:
                roster_embed = discord.Embed(
                    title="Kill Team Roster",
                    description=f"{_extract_killteam_name(getattr(killteam, 'name', 'Unknown'))}",
                    color=0x2ECC71,
                )
                # Chunk rows into fields to avoid long single blocks
                chunk_size = 15
                for i in range(0, len(formatted_rows), chunk_size):
                    chunk = formatted_rows[i : i + chunk_size]
                    # keep lines short using earlier truncation
                    field_value = "\n".join(f"• {row}" for row in chunk)
                    roster_embed.add_field(
                        name=f"Members {i + 1}–{min(i + chunk_size, len(formatted_rows))}",
                        value=field_value or "—",
                        inline=False,
                    )
                roster_embed.set_footer(
                    text="Roster generated from recent service records."
                )

                roster_view = ToggleFormatView(
                    text_content=roster_text, embed=roster_embed, default="ansi"
                )
                await interaction.followup.send(
                    content=roster_text, embed=None, view=roster_view, ephemeral=True
                )
            except Exception:
                # Fallback to ANSI block with toggle
                try:
                    roster_embed = _embed_from_ansi("Kill Team Roster", roster_text)
                    roster_view = ToggleFormatView(
                        text_content=roster_text, embed=roster_embed, default="ansi"
                    )
                    await interaction.followup.send(
                        content=roster_text,
                        embed=None,
                        view=roster_view,
                        ephemeral=True,
                    )
                except Exception:
                    await interaction.followup.send(roster_text, ephemeral=True)
        except Exception:
            # Continue even if roster formatting fails
            pass

        # Use month-to-date time period (matching preview_honours)
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
            force_data = active_rankings.get("avg_aar_per_member", {}).get(
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
            s_lines.append(
                f"High-Risk Ops            (Hard-Strat+Omega {int(risk_data[0])}) — Rank #{risk_data[1]}/{risk_data[2]}"
            )
            s_lines.append(
                f"AARs per Member          (Avg AAR/Member {force_data[0]:.1f}) — Rank #{force_data[1]}/{force_data[2]}"
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
            # Structured summary embed with concise inline fields
            title_type = "Chapter" if is_chapter_role else "Kill Team"
            embed = discord.Embed(
                title=f"{title_type} Monthly Honours",
                description=f"{display_label} — Month to Date ({span_days} Days)",
                color=0x2ECC71,
            )
            if queried_key:
                ops_data = active_rankings.get("ops", {}).get(queried_key, (0, 0, 0))
                avg_data = active_rankings.get("avg", {}).get(queried_key, (0.0, 0, 0))
                pres_data = active_rankings.get("pres", {}).get(queried_key, (0, 0, 0))
                risk_data = active_rankings.get("high_risk", {}).get(
                    queried_key, (0, 0, 0)
                )
                force_data = active_rankings.get("avg_aar_per_member", {}).get(
                    queried_key, (0.0, 0, 0)
                )
                embed.add_field(
                    name="Total Operations",
                    value=f"Ops {int(ops_data[0])} — #{ops_data[1]}/{ops_data[2]}",
                    inline=True,
                )
                embed.add_field(
                    name="Avg Points per Op",
                    value=f"Avg Op {avg_data[0]:.1f} — #{avg_data[1]}/{avg_data[2]}",
                    inline=True,
                )
                embed.add_field(
                    name="Armory + Gene-seed",
                    value=f"#{pres_data[1]}/{pres_data[2]}",
                    inline=True,
                )
                embed.add_field(
                    name="High-Risk Ops",
                    value=f"Hard-Strat+Omega {int(risk_data[0])} — #{risk_data[1]}/{risk_data[2]}",
                    inline=True,
                )
                embed.add_field(
                    name="AARs per Member",
                    value=f"Avg AAR/Member {force_data[0]:.1f} — #{force_data[1]}/{force_data[2]}",
                    inline=True,
                )
            view = ToggleFormatView(
                text_content=summary_text, embed=embed, default="ansi"
            )
            await interaction.followup.send(
                content=summary_text, embed=None, view=view, ephemeral=True
            )
        except Exception:
            # ignore send errors and proceed to attach full file
            try:
                fallback_title = (
                    "Chapter Summary" if is_chapter_role else "Kill Team Summary"
                )
                embed = _embed_from_ansi(fallback_title, summary_text)
                view = ToggleFormatView(
                    text_content=summary_text, embed=embed, default="ansi"
                )
                await interaction.followup.send(
                    content=summary_text, embed=None, view=view, ephemeral=True
                )
            except Exception:
                pass

    # Only send the detailed per-brother ledger for single-brother queries
    if not killteam:
        # If this was a single-brother request, provide a dramatically simpler
        # mobile-friendly embed (the Mobile button will show this). Otherwise
        # fall back to converting the ANSI text into an embed.
        try:
            if (len(members) == 1) and member_stat_rows_list:
                # Use the structured stat rows we captured earlier to build
                # a compact, easy-to-read embed for mobile.
                name_val = (
                    roster_items[0].get("name") if roster_items else "Deeds Ledger"
                )
                embed = discord.Embed(
                    title=f"Deeds Ledger — {name_val}",
                    color=0x2ECC71,
                )
                # Add fields non-inline so mobile clients render them vertically
                for label, value in member_stat_rows_list[0]:
                    try:
                        embed.add_field(name=label, value=value or "—", inline=False)
                    except Exception:
                        # fallback to a single combined field if something goes wrong
                        pass
            else:
                embed = _embed_from_ansi("Deeds Ledger", reply_text)
        except Exception:
            embed = _embed_from_ansi("Deeds Ledger", reply_text)

        view = ToggleFormatView(text_content=reply_text, embed=embed, default="ansi")
        await interaction.followup.send(
            content=reply_text, embed=None, view=view, ephemeral=True
        )

        # Send Monthly Honours as a separate additional message
        if len(members) == 1:
            # Use month-to-date time period (matching preview_honours)
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
                    "chapters_map": {},
                    "imperial_date": _format_imperial_date(datetime.utcnow()),
                    "span_days": mtd_span_days,
                }

            imperial_date = rankings.get("imperial_date", "")
            individual_rankings = rankings.get("individuals", {})
            chapter_rankings = rankings.get("chapters", {})
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
            ch_aar_data = chapter_rankings.get("avg_aar_per_member", {}).get(
                home_chapter, (0.0, 0, 0)
            )

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
                h_lines.append(
                    f"High-Risk Ops            (Hard-Strat+Omega {int(ch_risk_data[0])}) — Rank #{ch_risk_data[1]}/{ch_risk_data[2]}"
                )
                h_lines.append(
                    f"AARs per Member          (Avg AAR/Member {ch_aar_data[0]:.1f}) — Rank #{ch_aar_data[1]}/{ch_aar_data[2]}"
                )
            else:
                h_lines.append("  Chapter does not meet minimum threshold for ranking")

            h_lines.append("")
            h_lines.append(
                "=============================================================================="
            )
            h_lines.append("\u001b[0m```")
            honours_text = "\n".join(h_lines)

            # Build embed for honours
            try:
                honours_embed = discord.Embed(
                    title=f"Monthly Honours — {target_name}",
                    color=0x2ECC71,
                )
                honours_embed.add_field(
                    name="─── Individual Rankings ───", value="\u200b", inline=False
                )
                if ops_data[2] > 0:
                    honours_embed.add_field(
                        name="Total Operations",
                        value=f"#{ops_data[1]}/{ops_data[2]}",
                        inline=True,
                    )
                    honours_embed.add_field(
                        name="Avg Points per Op",
                        value=f"#{avg_data[1]}/{avg_data[2]}",
                        inline=True,
                    )
                    honours_embed.add_field(
                        name="Gene-seed Points",
                        value=f"#{gene_data[1]}/{gene_data[2]}",
                        inline=True,
                    )
                    honours_embed.add_field(
                        name="Armory Points",
                        value=f"#{armory_data[1]}/{armory_data[2]}",
                        inline=True,
                    )
                    honours_embed.add_field(
                        name="High-Risk Ops",
                        value=f"#{risk_data[1]}/{risk_data[2]}",
                        inline=True,
                    )
                honours_embed.add_field(
                    name=f"─── {home_chapter} Rankings ───",
                    value="\u200b",
                    inline=False,
                )
                if ch_ops_data[2] > 0:
                    honours_embed.add_field(
                        name="Total Operations",
                        value=f"#{ch_ops_data[1]}/{ch_ops_data[2]}",
                        inline=True,
                    )
                    honours_embed.add_field(
                        name="Avg Points per Op",
                        value=f"#{ch_avg_data[1]}/{ch_avg_data[2]}",
                        inline=True,
                    )
                    honours_embed.add_field(
                        name="Armory + Gene-seed",
                        value=f"#{ch_pres_data[1]}/{ch_pres_data[2]}",
                        inline=True,
                    )
                    honours_embed.add_field(
                        name="High-Risk Ops",
                        value=f"#{ch_risk_data[1]}/{ch_risk_data[2]}",
                        inline=True,
                    )
                    honours_embed.add_field(
                        name="AARs per Member",
                        value=f"#{ch_aar_data[1]}/{ch_aar_data[2]}",
                        inline=True,
                    )
            except Exception:
                honours_embed = _embed_from_ansi("Monthly Honours", honours_text)

            honours_view = ToggleFormatView(
                text_content=honours_text, embed=honours_embed, default="ansi"
            )
            await interaction.followup.send(
                content=honours_text, embed=None, view=honours_view, ephemeral=True
            )


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
        is_sergeant_or_higher(interaction.user) and is_allowed_channel(interaction)
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

    pair_counts = None
    triples = None
    spreads = None
    # Prefer using cached combat computations from DataStore if available
    try:
        if DATASTORE:
            cached = DATASTORE.get_combat_cache(span_days)
            if cached and isinstance(cached.get("data"), dict):
                pdata = cached.get("data")
                pair_counts = pdata.get("pair_counts")
                triples = pdata.get("triples")
                spreads = pdata.get("spreads")
    except Exception:
        pair_counts = None

    if pair_counts is None:
        # compute pair_counts off the event loop
        try:
            pair_counts = await asyncio.to_thread(_build_pair_counts, missions)
        except Exception:
            pair_counts = _build_pair_counts(missions)

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
                        "pair_counts": pair_counts,
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
        text = _format_bonds_for_discord(
            top_global,
            interaction.guild,
            window_days=span_days,
            chapters=chapters,
            spreads=spreads,
        )
        embed = _format_bonds_embed(
            top_global,
            guild=interaction.guild,
            window_days=span_days,
            chapters=chapters,
            spreads=spreads,
        )
        view = ToggleFormatView(text_content=text, embed=embed, default="ansi")
        # Use followup when we've deferred, fallback to response if not
        try:
            if interaction_deferred:
                await interaction.followup.send(content=text, view=view, ephemeral=True)
            else:
                await interaction.response.send_message(
                    content=text, view=view, ephemeral=True
                )
        except Exception:
            try:
                await interaction.response.send_message(
                    content=text, view=view, ephemeral=True
                )
            except Exception:
                logger.exception("combat_bonds: failed to send response or followup")
    else:
        target_id = str(brother.id)
        personal = _select_personal_bonds(triples, target_id, max_n=3)
        uids: List[str] = []
        for tri, _score in personal:
            uids.extend(list(tri))
        chapters = await _resolve_home_chapters(interaction.guild, sorted(set(uids)))
        text = _format_bonds_for_discord(
            personal,
            interaction.guild,
            window_days=span_days,
            chapters=chapters,
            spreads=spreads,
        )
        embed = _format_bonds_embed(
            personal,
            guild=interaction.guild,
            window_days=span_days,
            chapters=chapters,
            spreads=spreads,
        )
        view = ToggleFormatView(text_content=text, embed=embed, default="ansi")
        try:
            if interaction_deferred:
                await interaction.followup.send(content=text, view=view, ephemeral=True)
            else:
                await interaction.response.send_message(
                    content=text, view=view, ephemeral=True
                )
        except Exception:
            try:
                await interaction.response.send_message(
                    content=text, view=view, ephemeral=True
                )
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
    # Chapter Approved tag present (role mention)
    chapter_approved = False
    chapter_approved_extra_point_applied = False
    # Black Laurels tracking
    black_laurels_in_difficulty = False
    black_laurels_in_mission = False
    black_laurels_mentioned_elsewhere = False

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
                        rest_without_mentions = rest_without_mentions.replace(f"<@{uid}>", "").replace(f"<@!{uid}>", "")
                    rest_without_mentions = rest_without_mentions.strip().lower()
                    
                    # Valid if: "carried by" OR nothing left (just the tag)
                    if "carried" in rest_without_mentions or rest_without_mentions == "" or rest_without_mentions == "by":
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

    # Detect Chapter Approved role mention anywhere in the message.
    try:
        for role in message.role_mentions:
            try:
                rn = (getattr(role, "name", "") or "").strip().lower()
                rid = getattr(role, "id", None)
                # Accept either the canonical name or the known role ID
                if (
                    rn == "chapter approved"
                    or rid == 1467960627795464344
                    or str(rid) == "1467960627795464344"
                ):
                    chapter_approved = True
                    break
            except Exception:
                continue
    except Exception:
        chapter_approved = False

    # Detect Black Laurels role mention anywhere in the message.
    # Track if it's in difficulty/mission lines OR mentioned as a role elsewhere.
    try:
        for role in message.role_mentions:
            try:
                rn = (getattr(role, "name", "") or "").strip().lower()
                if "black" in rn and "laurel" in rn:
                    # If it's not already in difficulty or mission line, flag it as elsewhere
                    if not black_laurels_in_difficulty and not black_laurels_in_mission:
                        black_laurels_mentioned_elsewhere = True
                    break
            except Exception:
                continue
    except Exception:
        pass

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
        "chapter_approved": chapter_approved,
        "chapter_approved_extra_point_applied": chapter_approved_extra_point_applied,
        # Black Laurels tracking for validation
        "black_laurels_in_difficulty": black_laurels_in_difficulty,
        "black_laurels_in_mission": black_laurels_in_mission,
        "black_laurels_mentioned_elsewhere": black_laurels_mentioned_elsewhere,
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

        if has_black_laurels_difficulty or has_black_laurels_mission:
            # Black Laurels requires exactly 3 brothers (fireteam requirement)
            if len(brothers) != 3:
                errors.append(
                    "@Black_Laurels requires exactly 3 Brothers (a full fireteam)."
                )
            if is_in_grace_period:
                # GRACE PERIOD (before Feb 20, 2026): Allow Black Laurels on Mission OR Difficulty
                # Only check: must have @Absolute when Black Laurels is present
                if not has_absolute:
                    errors.append(
                        "@Black_Laurels requires @Absolute on the Difficulty line."
                    )
                # Check eligible missions
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
                # STRICT MODE (Feb 20, 2026+): Black Laurels ONLY on Mission line with @Absolute on Difficulty
                if has_black_laurels_difficulty and not has_black_laurels_mission:
                    errors.append(
                        "@Black_Laurels must be placed on the Mission line only."
                    )
                if not has_absolute:
                    errors.append(
                        "@Black_Laurels requires @Absolute on the Difficulty line."
                    )
                # Check eligible missions
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


# Use DataStore for processed IDs
def has_been_processed(aar_id: int):
    return DATASTORE.is_processed(aar_id)


# Use DataStore user_stats_cache for user stats
def compute_stats_for_user(user_id: str):
    return DATASTORE.get_user_stats(user_id)


def _induction_count_for_user(user_id: str) -> int:
    """Compute total inductions a brother participated in across all AARs.
    Rule: Siege initiation: 15 waves per inductee = 1 induction.
          Operation initiation: 3 trials per inductee = 1 induction.
          Each inductee in an AAR counts separately.
          Your own induction is excluded.
    """
    try:
        data = load_aar_data(AAR_RECORDS_PATH)
    except Exception:
        data = {}
    ops_trials = 0
    siege_waves = 0
    for rec in data.values():
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
            if "siege" in dclass:
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
    return int((siege_waves // 15) + (ops_trials // 3))


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
    spreads: Optional[Dict[str, int]] = None,
):
    """Render Combat Bonds as a Discord Embed (mobile-friendly).
    Shows up to 5 triads, with tier labels and member lines.
    """
    embed = discord.Embed(
        title="Combat Bonds — Multi-Member Battle-Litany",
        description=(
            f"Auspex Window: Last {window_days} day(s)"
            if window_days is not None
            else f"Auspex Window: Last {window_span} engagements"
        ),
        color=0x2ECC71,
    )
    if not bonds:
        embed.description = "No qualifying Combat Bonds found in the current window."
        return embed

    # Compact veneration key in the embed description
    try:
        embed.description = (
            embed.description or ""
        ) + "\n\nVeneration Key: FRAGILE | FORMING | RELIABLE | STALWART | INDOMITABLE"
    except Exception:
        pass

    scores_for_cutoffs = [score for _tri, score in bonds]
    cutoffs = _compute_bond_cutoffs(scores_for_cutoffs)
    ordinal_labels = {
        1: "PRIMARY",
        2: "SECONDARY",
        3: "TERTIARY",
        4: "QUATERNARY",
        5: "QUINARY",
    }

    def _member_label(uid: str) -> str:
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

    # Add a field per bond (Discord embeds allow up to 25 fields)
    rank = 1
    for triple, score in bonds:
        if rank > 5:
            break
        tier = _bond_tier_dynamic(score, cutoffs)
        members_in_group = list(triple)
        name = (
            f"{ordinal_labels.get(rank, 'BOND')} — {tier} ({len(members_in_group)}-man)"
        )
        value = "\n".join(f"• {_member_label(uid)}" for uid in members_in_group)
        embed.add_field(name=name, value=value, inline=False)
        rank += 1

    embed.set_footer(
        text="These Combat Bonds may be invoked by decree of Watch Command."
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
                },
            )
            u["ops"] += 1
            u["points"] += int(rec.get("points_for_op") or 0)
            u["armory"] += int(rec.get("armory_challenge_points") or 0)
            if is_high_risk:
                u["high_risk"] += 1
            if difficulty == "omega_ops":
                u["omega_kia"] += omega_kia
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

        # Team aggregation: attribute to member's teams
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

            resolved_teams: List[str] = []
            try:
                member_teams = _resolve_killteams_for_member(member)
                for mt in member_teams:
                    if mt not in resolved_teams:
                        resolved_teams.append(mt)
            except Exception:
                pass

            for resolved_team in resolved_teams:
                t = teams.setdefault(
                    str(resolved_team),
                    {
                        "ops": 0,
                        "points": 0,
                        "armory": 0,
                        "high_risk": 0,
                        "gene_carried": 0,
                        "gene_participated": 0,
                        "members": set(),
                    },
                )
                t["ops"] += 1
                t["points"] += int(rec.get("points_for_op") or 0)
                t["armory"] += int(rec.get("armory_challenge_points") or 0)
                if is_high_risk:
                    t["high_risk"] += 1
                try:
                    if rec.get("gene_seed_status") == "carried":
                        t["gene_carried"] += int(
                            rec.get("gene_seed_base_points_for_carrier") or 0
                        )
                    t["gene_participated"] += 1
                    t["members"].add(str(uid))
                except Exception:
                    pass

        # Chapter aggregation
        for uid in brother_ids:
            ch = chapters_map.get(str(uid))
            if ch:
                c = chapters.setdefault(
                    ch,
                    {
                        "ops": 0,
                        "points": 0,
                        "armory": 0,
                        "high_risk": 0,
                        "gene_carried": 0,
                        "gene_participated": 0,
                    },
                )
                c["ops"] += 1
                c["points"] += int(rec.get("points_for_op") or 0)
                c["armory"] += int(rec.get("armory_challenge_points") or 0)
                if is_high_risk:
                    c["high_risk"] += 1
                if rec.get("gene_seed_status") == "carried":
                    c["gene_carried"] += int(
                        rec.get("gene_seed_base_points_for_carrier") or 0
                    )
                c["gene_participated"] += 1
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

    # Build ranking functions
    def rank_users(metric_key: str, higher_is_better: bool = True):
        items = [(uid, v.get(metric_key, 0)) for uid, v in users.items()]
        items.sort(key=lambda x: x[1], reverse=higher_is_better)
        rankings = {}
        for idx, (uid, val) in enumerate(items, 1):
            rankings[uid] = (val, idx, len(items))
        return rankings

    def rank_teams(metric_key: str, higher_is_better: bool = True):
        items = [(tid, v.get(metric_key, 0)) for tid, v in teams.items()]
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
    individual_rankings = {
        "ops": rank_users("ops"),
        "avg": rank_users("avg"),
        "gene_carried": rank_users("gene_carried"),
        "armory": rank_users("armory"),
        "high_risk": rank_users("high_risk"),
        "omega_kia": rank_users("omega_kia"),
    }

    # Compute team rankings
    team_rankings = {
        "ops": rank_teams("ops"),
        "avg": rank_teams("avg"),
        "pres": rank_teams("pres"),
        "armory": rank_teams("armory"),
        "gene_carried": rank_teams("gene_carried"),
        "high_risk": rank_teams("high_risk"),
        "avg_aar_per_member": rank_teams("avg_aar_per_member"),
    }

    # Compute chapter rankings (matching kill team metrics)
    chapter_rankings = {
        "ops": rank_chapters("ops"),
        "avg": rank_chapters("avg"),
        "pres": rank_chapters("pres"),
        "armory": rank_chapters("armory"),
        "gene_carried": rank_chapters("gene_carried"),
        "high_risk": rank_chapters("high_risk"),
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


# --- Honours leaderboard generation and scheduled posting -----------------
LAST_MONTHLY_POST_DATE: Optional[str] = None


def _parse_iso_ts_to_utc_naive(ts_str: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


def _member_display_name(guild: discord.Guild, user_id: str) -> str:
    try:
        m = guild.get_member(int(user_id))
        if m:
            return getattr(m, "display_name", getattr(m, "name", str(user_id)))
    except Exception:
        pass
    return str(user_id)


def _format_imperial_date(dt: datetime) -> str:
    """Return Imperial date string like '0 123 456.M41 (Feb 21, 2026)' based on UTC datetime.

    - Check number: use 0 (event on Terra)
    - Year fraction: 3-digit fraction through the year (001..999)
    - Year: year within millennium (001..000 where 000 == 1000th year)
    - Millennium: M3
    - Real date appended in parentheses
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
        # Format real date compactly as "2/21/26"
        real_date = dt.strftime("%-m/%-d/%y")
        return f"0 {frac_s} {year_s}.{mill} ({real_date})"
    except Exception:
        return ""


def _role_for_chapter_mention(
    guild: discord.Guild, chapter_name: str
) -> Optional[discord.Role]:
    try:
        for r in guild.roles:
            if chapter_name.lower() in (r.name or "").lower():
                return r
    except Exception:
        pass
    return None


async def _build_honours(
    guild: discord.Guild,
    period_days: int,
    include_mentions: bool = True,
    start_dt: Optional[datetime] = None,
    end_dt: Optional[datetime] = None,
):
    """Return (mentions_line:str, ansi_block:str).

    Aggregates AAR records from DATASTORE. By default this aggregates records
    from the last `period_days` days. Optionally a specific UTC naive
    `start_dt` (inclusive) and `end_dt` (exclusive) may be provided to compute
    honours for an arbitrary calendar range (useful for previous-month reports).
    """
    now = datetime.utcnow()
    # Determine the effective window [start, end). When start_dt/end_dt are
    # provided they take precedence over `period_days`.
    if start_dt is not None and end_dt is not None:
        start = start_dt
        end = end_dt
    elif start_dt is not None:
        start = start_dt
        end = now
    else:
        start = now - timedelta(days=period_days)
        end = now
    # Aggregate per-user and per-team and per-chapter
    users: Dict[str, dict] = {}
    teams: Dict[str, dict] = {}
    chapters: Dict[str, dict] = {}
    # Track unique members per chapter for Ops/Member (Force-like) metric
    chapters_members: Dict[str, set] = {}

    if DATASTORE is None:
        return (
            "HONOURED:",
            """
==============================================================================
  WATCH FORTRESS JERICHO // LEDGER-CAST
  OPERATION-SCRIBE SERVITOR — MONTHLY HONOURS
==============================================================================

INDIVIDUAL DISTINCTIONS
Operations               Name (X)
Avg Pts/Op               Name (X.X)
Gene-seed Pts            Name (X)
Armory Pts               Name (X)
Hard-Strat+Omega         Name (X)

KILL TEAM DISTINCTIONS
Operations               Team (X)
Avg Pts/Op               Team (X.X)
Armory+Gene-seed         Team (X|X)
Hard-Strat+Omega         Team (X)

CHAPTER DISTINCTIONS
Operations               Chapter (X)
Avg Pts/Op               Chapter (X.X)
Armory+Gene-seed         Chapter (X|X)
Hard-Strat+Omega         Chapter (X)
AARs/Member              Chapter (X.X)

==============================================================================
""",
            None,  # No embed when no data
        )

    # Collect relevant records first, then resolve member chapters in bulk
    recs_in_window: List[dict] = []
    all_user_ids: set = set()
    for rec in DATASTORE.iter_records():
        ts = _parse_iso_ts_to_utc_naive(rec.get("timestamp") or "")
        if not ts:
            continue
        # Include records in the half-open interval [start, end)
        if ts < start or ts >= end:
            continue
        recs_in_window.append((ts, rec))
        for uid in rec.get("brother_ids") or []:
            all_user_ids.add(str(uid))

    # Resolve home chapters for all participating users in one call
    chapters_map: Dict[str, str] = {}
    try:
        if all_user_ids and guild:
            chapters_map = await _resolve_home_chapters(guild, sorted(all_user_ids))
    except Exception:
        chapters_map = {}

    # Pre-fetch all members in bulk to avoid repeated API calls in the processing loop
    members_cache: Dict[str, Optional[discord.Member]] = {}
    if guild and all_user_ids:
        for uid in all_user_ids:
            try:
                member = guild.get_member(int(uid))
            except Exception:
                member = None
            if member is None:
                try:
                    member = await guild.fetch_member(int(uid))
                except Exception:
                    member = None
            members_cache[str(uid)] = member

    # Process each record with resolved chapters and infer kill team from member roles when missing
    for ts, rec in recs_in_window:
        # Determine teams: try a few keys first
        team_key = (
            rec.get("kill_team")
            or rec.get("killteam")
            or rec.get("team")
            or rec.get("kill_team_name")
        )
        difficulty = rec.get("difficulty_class")
        is_high_risk = difficulty in ("hard_stratagem", "omega_ops")
        omega_kia = (
            int(rec.get("killed_in_action", 0) or 0) if difficulty == "omega_ops" else 0
        )

        brother_ids = [str(x) for x in (rec.get("brother_ids") or [])]

        # Normalize any provided team_key to a canonical Kill Team name when possible
        try:
            if team_key:
                tk_low = str(team_key).lower()
                for kt in KILL_TEAMS:
                    if (
                        kt.lower() in tk_low
                        or _extract_killteam_name(str(team_key)).lower() in kt.lower()
                    ):
                        team_key = kt
                        break
        except Exception:
            pass

        # If team not present, infer by majority Kill Team role among brothers using resolver
        if not team_key and guild and brother_ids:
            role_count: Dict[str, int] = {}
            for uid in brother_ids:
                member = members_cache.get(str(uid))
                if not member:
                    continue
                try:
                    resolved = _resolve_killteam_for_member(member)
                except Exception:
                    resolved = None
                if resolved:
                    role_count[resolved] = role_count.get(resolved, 0) + 1
            # pick majority canonical team if present
            if role_count:
                most, cnt = max(role_count.items(), key=lambda it: it[1])
                if cnt >= (len(brother_ids) / 2):
                    team_key = most

        # Aggregate user-level stats
        for uid in brother_ids:
            u = users.setdefault(
                uid,
                {
                    "ops": 0,
                    "points": 0,
                    "armory": 0,
                    "high_risk": 0,
                    "omega_kia": 0,
                    "first_ts": None,
                    "gene_carried": 0,
                    "gene_participated": 0,
                },
            )
            u["ops"] += 1
            u["points"] += int(rec.get("points_for_op") or 0)
            u["armory"] += int(rec.get("armory_challenge_points") or 0)
            if is_high_risk:
                u["high_risk"] += 1
            if difficulty == "omega_ops":
                u["omega_kia"] += omega_kia
            try:
                if (
                    str(rec.get("gene_seed_carrier_id")) == str(uid)
                    and (rec.get("gene_seed_status") or "") == "carried"
                ):
                    u["gene_carried"] += int(
                        rec.get("gene_seed_base_points_for_carrier") or 0
                    )
                if uid in brother_ids:
                    u["gene_participated"] += 1
            except Exception:
                pass
            if u["first_ts"] is None or ts < u["first_ts"]:
                u["first_ts"] = ts

        # Team aggregation: attribute contributions per brother to their own Kill Team
        for uid in brother_ids:
            member = members_cache.get(str(uid)) if guild else None
            # Build list of teams this member contributes to for this record
            resolved_teams: List[str] = []
            try:
                # Include record-level canonical team for all members (maintain existing semantics)
                if team_key and any(team_key == kt for kt in KILL_TEAMS):
                    resolved_teams.append(team_key)
                # Add per-member teams (canonical KT, company command, high command)
                if member:
                    try:
                        member_teams = _resolve_killteams_for_member(member)
                    except Exception:
                        member_teams = []
                    for mt in member_teams:
                        if mt not in resolved_teams:
                            resolved_teams.append(mt)
            except Exception:
                resolved_teams = []

            if not resolved_teams:
                continue

            for resolved_team in resolved_teams:
                t = teams.setdefault(
                    str(resolved_team),
                    {
                        "ops": 0,
                        "points": 0,
                        "armory": 0,
                        "high_risk": 0,
                        "first_ts": None,
                        "gene_carried": 0,
                        "gene_participated": 0,
                        "members": set(),
                    },
                )
                t["ops"] += 1
                t["points"] += int(rec.get("points_for_op") or 0)
                t["armory"] += int(rec.get("armory_challenge_points") or 0)
                if is_high_risk:
                    t["high_risk"] += 1
                if difficulty == "omega_ops":
                    t["omega_kia"] = t.get("omega_kia", 0) + omega_kia
                try:
                    if rec.get("gene_seed_status") == "carried":
                        # count gene carried points once per record per team-member
                        t["gene_carried"] += int(
                            rec.get("gene_seed_base_points_for_carrier") or 0
                        )
                    t["gene_participated"] += 1
                    try:
                        t["members"].add(str(uid))
                    except Exception:
                        # ensure members remains a set-like container
                        if not t.get("members"):
                            t["members"] = {str(uid)}
                        else:
                            try:
                                t["members"].add(str(uid))
                            except Exception:
                                pass
                except Exception:
                    pass
                if t["first_ts"] is None or ts < t["first_ts"]:
                    t["first_ts"] = ts

        # Chapters: per participating members' home chapters (use pre-resolved map)
        for uid in brother_ids:
            ch = chapters_map.get(str(uid))
            if ch:
                c = chapters.setdefault(
                    ch,
                    {
                        "ops": 0,
                        "points": 0,
                        "armory": 0,
                        "high_risk": 0,
                        "gene_carried": 0,
                        "gene_participated": 0,
                    },
                )
                c["ops"] += 1
                c["points"] += int(rec.get("points_for_op") or 0)
                c["armory"] += int(rec.get("armory_challenge_points") or 0)
                if is_high_risk:
                    c["high_risk"] += 1
                if rec.get("gene_seed_status") == "carried":
                    c["gene_carried"] += int(
                        rec.get("gene_seed_base_points_for_carrier") or 0
                    )
                c["gene_participated"] += 1
                # track unique members for Ops/Member calculation
                try:
                    chapters_members.setdefault(ch, set()).add(str(uid))
                except Exception:
                    pass

    # Compute winners with tie-breakers
    def sort_entities(data: Dict[str, dict], primary_key: str, reverse: bool = True):
        def key_fn(item):
            k, v = item
            primary = v.get(primary_key, 0)
            ops = v.get("ops", 0)
            high_risk = v.get("high_risk", 0)
            first_ts = v.get("first_ts") or datetime.max
            return (
                -primary if reverse else primary,
                -ops,
                -high_risk,
                first_ts,
                k,
            )

        return sorted(data.items(), key=key_fn)

    # Individual picks
    # Determine dynamic minimum ops required for the reporting window so
    # individual distinctions use the same thresholds as chapter doctrines.
    # Monthly leaderboards always use 28 ops minimum.
    if period_days >= 28:
        min_ops_required = 28
    else:
        min_ops_required = max(3, int(period_days * 0.3))

    # Filter users to those meeting the minimum ops requirement; if none
    # meet the threshold, fall back to including all users.
    users_for_eval = {
        k: v for k, v in users.items() if v.get("ops", 0) >= min_ops_required
    }
    if not users_for_eval:
        users_for_eval = users

    # Operational Tempo -> ops
    ops_sorted = sort_entities(users_for_eval, "ops")
    tempo_name = ops_sorted[0][0] if ops_sorted else ""

    # Veteran Lethality -> avg points per op
    for uid, v in users.items():
        v["avg"] = (v["points"] / v["ops"]) if v["ops"] else 0.0
    leth_sorted = sort_entities(
        {k: {**v, **{"avg": v["avg"]}} for k, v in users_for_eval.items()}, "avg"
    )
    lethal_name = leth_sorted[0][0] if leth_sorted else ""

    # Reliquary Bearer -> geneseed points (raw carried points)
    gene_sorted = sort_entities(users_for_eval, "gene_carried")
    gene_name = gene_sorted[0][0] if gene_sorted else ""

    # Vault Reclaimer -> armory
    arm_sorted = sort_entities(users_for_eval, "armory")
    arm_name = arm_sorted[0][0] if arm_sorted else ""

    # High-Risk Operator -> high_risk, include omega_kia
    high_sorted = sort_entities(users_for_eval, "high_risk")
    high_name = high_sorted[0][0] if high_sorted else ""

    # Kill team picks (use ops, avg, armory/gene, high risk)
    for tid, tv in teams.items():
        tv["avg"] = (tv["points"] / tv["ops"]) if tv["ops"] else 0.0
        tv["gene_rate"] = (
            (tv.get("gene_carried", 0) / tv.get("gene_participated", 1))
            if tv.get("gene_participated", 0)
            else 0.0
        )
        # Average AARs per member (force multiplier): ops divided by unique members
        try:
            members_count = (
                len(tv.get("members") or []) if tv.get("members") is not None else 0
            )
            tv["avg_aar_per_member"] = (
                (tv["ops"] / members_count) if members_count else 0.0
            )
        except Exception:
            tv["avg_aar_per_member"] = 0.0

    # Filter kill teams to those meeting the minimum ops requirement; if none
    # meet the threshold, fall back to including all teams.
    teams_for_eval = {
        k: v for k, v in teams.items() if v.get("ops", 0) >= min_ops_required
    }
    if not teams_for_eval:
        teams_for_eval = teams

    kt_ops = sort_entities(teams_for_eval, "ops")
    kt_avg = sort_entities(
        {k: {**v, **{"avg": v["avg"]}} for k, v in teams_for_eval.items()}, "avg"
    )
    kt_pres = sort_entities(
        {
            k: {**v, **{"pres": v.get("armory", 0) + v.get("gene_carried", 0)}}
            for k, v in teams_for_eval.items()
        },
        "pres",
    )
    kt_risk = sort_entities(teams_for_eval, "high_risk")
    # Force multiplier: average AAR per unique member
    kt_force = sort_entities(
        {
            k: {**v, **{"force": v.get("avg_aar_per_member", 0.0)}}
            for k, v in teams_for_eval.items()
        },
        "force",
    )

    # --- Compute Top 5 rankings by median rank across all metrics ---
    def _compute_dense_ranks(sorted_items: list, value_key: str) -> Dict[str, int]:
        """Given a sorted list, return dense ranks (ties get same rank)."""
        ranks = {}
        prev_val = None
        current_rank = 0
        for idx, (entity_id, data) in enumerate(sorted_items):
            val = data.get(value_key, 0)
            if val != prev_val:
                current_rank = idx + 1
            ranks[entity_id] = current_rank
            prev_val = val
        return ranks

    # Individual rankings across 5 metrics: ops, avg, gene_carried, armory, high_risk
    ind_metrics = [
        (ops_sorted, "ops"),
        (leth_sorted, "avg"),
        (gene_sorted, "gene_carried"),
        (arm_sorted, "armory"),
        (high_sorted, "high_risk"),
    ]
    ind_all_ranks: Dict[str, List[int]] = {}
    for sorted_list, key in ind_metrics:
        dense = _compute_dense_ranks(sorted_list, key)
        for uid, rank in dense.items():
            ind_all_ranks.setdefault(uid, []).append(rank)

    ind_median_ranks = {
        uid: statistics.median(ranks) for uid, ranks in ind_all_ranks.items() if ranks
    }
    ind_top5 = sorted(ind_median_ranks.items(), key=lambda x: (x[1], x[0]))[:5]

    # Kill Team rankings across 5 metrics: ops, avg, pres, high_risk, force
    kt_metrics = [
        (kt_ops, "ops"),
        (kt_avg, "avg"),
        (kt_pres, "pres"),
        (kt_risk, "high_risk"),
        (kt_force, "force"),
    ]
    kt_all_ranks: Dict[str, List[int]] = {}
    for sorted_list, key in kt_metrics:
        dense = _compute_dense_ranks(sorted_list, key)
        for tid, rank in dense.items():
            kt_all_ranks.setdefault(tid, []).append(rank)

    kt_median_ranks = {
        tid: statistics.median(ranks) for tid, ranks in kt_all_ranks.items() if ranks
    }
    kt_top5 = sorted(kt_median_ranks.items(), key=lambda x: (x[1], x[0]))[:5]

    # Build mention line
    honoured_parts: List[str] = []

    def user_mention(uid: str) -> str:
        return f"<@{uid}>" if include_mentions else _member_display_name(guild, uid)

    # Individuals: dedupe and collect top picks
    individual_uids = []
    for src in (tempo_name, lethal_name, gene_name, arm_name, high_name):
        if src and src not in individual_uids:
            individual_uids.append(src)
    honoured_parts.extend([user_mention(u) for u in individual_uids])

    # Teams: attempt to find roles for team mentions
    team_mentions: List[str] = []
    for t in [
        kt_ops[0][0] if kt_ops else None,
        kt_avg[0][0] if kt_avg else None,
        kt_pres[0][0] if kt_pres else None,
        kt_risk[0][0] if kt_risk else None,
        kt_force[0][0] if kt_force else None,
    ]:
        if not t:
            continue
        # Try to interpret t as role id
        try:
            rid = int(t)
            r = guild.get_role(rid)
            if r:
                team_mentions.append(f"<@&{r.id}>")
                continue
        except Exception:
            pass
        # Special mappings: any 'High Command' label -> explicit role-id mention
        try:
            if isinstance(t, str) and ("high command" in str(t).strip().lower()):
                # Force the exact role id mention desired for High Command
                team_mentions.append("<@&1452913063970865203>")
                continue
        except Exception:
            pass
        # Special mapping: '<Company> Command' -> '<Company> Command' role (not Watch Company)
        try:
            if isinstance(t, str) and t.strip().endswith(" Command"):
                target_name = t.strip()  # e.g. "Primus Command"
                for r in guild.roles:
                    rn = (r.name or "").strip()
                    if rn.lower() == target_name.lower():
                        team_mentions.append(f"<@&{r.id}>")
                        raise StopIteration
        except StopIteration:
            continue
        except Exception:
            pass
        # Fallback: search role by name containing team string
        try:
            for r in guild.roles:
                if (t or "").lower() in (r.name or "").lower():
                    team_mentions.append(f"<@&{r.id}>")
                    break
        except Exception:
            pass

    honoured_parts.extend(team_mentions)

    # Chapters: collect top doctrines (just names)
    # Compute top-by-ops chapters for display fallback; actual doctrine winners
    # (and mentions) are chosen below from normalized metrics.
    top_chapters = sorted(chapters.items(), key=lambda it: -it[1].get("ops", 0))[:4]

    # Keep top-by-ops chapters for mention/display; doctrine winners will be
    # selected from all eligible chapters below using normalized ranking to
    # reduce bias from chapter size.

    # Add all Top 5 individuals to mentions
    top5_individual_mentions: List[str] = []
    for uid, _ in ind_top5:
        if uid:
            top5_individual_mentions.append(user_mention(uid))
    honoured_parts.extend(top5_individual_mentions)

    # Add all Top 5 kill teams to mentions (using same role mapping logic as before)
    top5_team_mentions: List[str] = []
    for team_id, _ in kt_top5:
        if not team_id:
            continue
        # Try to interpret team_id as role id
        try:
            rid = int(team_id)
            r = guild.get_role(rid)
            if r:
                top5_team_mentions.append(f"<@&{r.id}>")
                continue
        except Exception:
            pass
        # Special mappings: any 'High Command' label -> explicit role-id mention
        try:
            if isinstance(team_id, str) and (
                "high command" in str(team_id).strip().lower()
            ):
                top5_team_mentions.append("<@&1452913063970865203>")
                continue
        except Exception:
            pass
        # Special mapping: '<Company> Command' -> '<Company> Command' role
        try:
            if isinstance(team_id, str) and team_id.strip().endswith(" Command"):
                target_name = team_id.strip()
                for r in guild.roles:
                    rn = (r.name or "").strip()
                    if rn.lower() == target_name.lower():
                        top5_team_mentions.append(f"<@&{r.id}>")
                        raise StopIteration
        except StopIteration:
            continue
        except Exception:
            pass
        # Fallback: search role by name containing team string
        mentioned = False
        try:
            for r in guild.roles:
                if (team_id or "").lower() in (r.name or "").lower():
                    top5_team_mentions.append(f"<@&{r.id}>")
                    mentioned = True
                    break
        except Exception:
            pass
        # Final fallback: if no role found, add team name as text
        if not mentioned and include_mentions:
            top5_team_mentions.append(f"@{team_id}")
    honoured_parts.extend(top5_team_mentions)

    # Chapters: placeholder - will be filled after ch_top5 is computed below
    top5_chapter_mentions: List[str] = []

    # Construct HONOURED line (will be finalized after chapters are processed)
    # honour_line placeholder - will be fully constructed after ch_top5 is computed

    # Build ANSI block exactly as requested, inserting selected display names and values
    def display_name_for(uid_key: str) -> str:
        if not uid_key:
            return "Name"
        if include_mentions:
            # show mention inside block? Spec: mentions must be BEFORE the ANSI block, never inside it.
            # So always show plain display name inside ANSI block.
            return _member_display_name(guild, uid_key)
        return _member_display_name(guild, uid_key)

    def fmt_avg(v):
        return f"{v:.1f}" if isinstance(v, float) else f"{float(v):.1f}"

    tempo_disp = display_name_for(tempo_name)
    tempo_val = users.get(tempo_name, {}).get("ops", 0)
    lethal_disp = display_name_for(lethal_name)
    lethal_val = users.get(lethal_name, {}).get("avg", 0.0)
    gene_disp = display_name_for(gene_name)
    # Show geneseed as points (sum of base points carried) rather than raw counts or rate
    gene_val = users.get(gene_name, {}).get("gene_carried", 0)
    arm_disp = display_name_for(arm_name)
    arm_val = users.get(arm_name, {}).get("armory", 0)
    high_disp = display_name_for(high_name)
    high_val = users.get(high_name, {}).get("high_risk", 0)
    high_kia = users.get(high_name, {}).get("omega_kia", 0)

    kt_ops_name = kt_ops[0][0] if kt_ops else "Team"
    kt_ops_val = teams.get(kt_ops_name, {}).get("ops", 0)
    kt_avg_name = kt_avg[0][0] if kt_avg else "Team"
    kt_avg_val = teams.get(kt_avg_name, {}).get("avg", 0.0)
    kt_pres_name = kt_pres[0][0] if kt_pres else "Team"
    kt_pres_arm = teams.get(kt_pres_name, {}).get("armory", 0)
    kt_pres_gene = teams.get(kt_pres_name, {}).get("gene_carried", 0)
    kt_risk_name = kt_risk[0][0] if kt_risk else "Team"
    kt_risk_val = teams.get(kt_risk_name, {}).get("high_risk", 0)
    kt_force_name = kt_force[0][0] if kt_force else "Team"
    kt_force_val = teams.get(kt_force_name, {}).get("avg_aar_per_member", 0.0)

    # doctrine winners (ch1..ch5) will be computed after metric helpers below
    ch1 = ch2 = ch3 = ch4 = ch5 = "Chapter"

    # Compute chapter metric values to display (matching kill team stats)
    def _chap_ops(ch):
        try:
            return chapters.get(ch, {}).get("ops", 0)
        except Exception:
            return 0

    def _chap_avg(ch):
        try:
            d = chapters.get(ch, {})
            return (
                (d.get("points", 0) / float(d.get("ops", 1)))
                if d.get("ops", 0)
                else 0.0
            )
        except Exception:
            return 0.0

    def _chap_pres(ch):
        try:
            d = chapters.get(ch, {})
            return d.get("armory", 0) + d.get("gene_carried", 0)
        except Exception:
            return 0

    def _chap_pres_armory(ch):
        try:
            return chapters.get(ch, {}).get("armory", 0)
        except Exception:
            return 0

    def _chap_pres_gene(ch):
        try:
            return chapters.get(ch, {}).get("gene_carried", 0)
        except Exception:
            return 0

    def _chap_high_risk(ch):
        try:
            return chapters.get(ch, {}).get("high_risk", 0)
        except Exception:
            return 0

    def _chap_avg_aar(ch):
        try:
            ops = chapters.get(ch, {}).get("ops", 0)
            members = len(chapters_members.get(ch, set()))
            return (ops / float(members)) if members else 0.0
        except Exception:
            return 0.0

    # Determine eligible chapters for doctrine evaluation to avoid noisy
    # single-member spikes; use z-score normalization to pick winners.
    MIN_CHAPTER_MEMBERS = 1
    # Dynamic min ops based on reporting window: monthly uses 28 ops minimum.
    if period_days >= 28:
        min_ops_required = 28
    else:
        # fallback proportional floor (30% of window) with a sensible min
        min_ops_required = max(3, int(period_days * 0.3))

    eligible = [
        ch
        for ch, d in chapters.items()
        if len(chapters_members.get(ch, set())) >= MIN_CHAPTER_MEMBERS
        and d.get("ops", 0) >= min_ops_required
    ]
    if not eligible:
        # fallback to top-chapters (display list) if no chapters meet thresholds
        eligible = [ch for ch, _ in top_chapters] if top_chapters else []

    # Compute median active member count for dampening outlier-sized chapters
    _active_counts = [len(chapters_members.get(ch, set())) for ch in eligible]
    _median_members = statistics.median(_active_counts) if _active_counts else 1.0

    def _apply_member_dampening(raw_vals: Dict[str, float]) -> Dict[str, float]:
        """Apply member-count-distance dampening to raw metric values.

        Chapters with active member counts far from the median get their
        scores pulled toward the global mean, reducing the impact of very
        small or very large chapters on doctrine rankings.
        """
        if not raw_vals:
            return {}
        global_mean = statistics.mean(raw_vals.values())
        dampened = {}
        for ch, raw in raw_vals.items():
            members = len(chapters_members.get(ch, set()))
            distance = abs(members - _median_members)
            # Normalize distance by median; chapters at median have 0 dampening
            dampening_factor = distance / _median_members if _median_members else 0.0
            # Weight: 1.0 at median, decreasing as distance grows
            weight = 1.0 / (1.0 + dampening_factor)
            dampened[ch] = weight * raw + (1.0 - weight) * global_mean
        return dampened

    def _pick_by_zscore(metric_fn):
        # Compute raw values for all eligible chapters
        try:
            raw_vals = {ch: float(metric_fn(ch)) for ch in eligible}
        except Exception:
            raw_vals = {}
        if not raw_vals:
            return "Chapter"
        # Apply member-count-distance dampening before z-score ranking
        dampened_vals = _apply_member_dampening(raw_vals)
        vals = list(dampened_vals.items())
        nums = [v for _, v in vals]
        mean = statistics.mean(nums)
        stdev = statistics.pstdev(nums) if len(nums) >= 2 else 0.0
        if stdev == 0.0:
            # no spread; pick highest dampened metric
            return max(vals, key=lambda it: it[1])[0]
        zscores = [(ch, (v - mean) / stdev) for ch, v in vals]
        return max(zscores, key=lambda it: it[1])[0]

    # Select distinction winners across eligible chapters (matching kill team metrics)
    ch1 = _pick_by_zscore(_chap_ops)
    ch2 = _pick_by_zscore(_chap_avg)
    ch3 = _pick_by_zscore(_chap_pres)
    ch4 = _pick_by_zscore(_chap_high_risk)
    ch5 = _pick_by_zscore(_chap_avg_aar)

    ch1_val = _chap_ops(ch1)
    ch2_val = _chap_avg(ch2)
    ch3_arm = _chap_pres_armory(ch3)
    ch3_gene = _chap_pres_gene(ch3)
    ch4_val = _chap_high_risk(ch4)
    ch5_val = _chap_avg_aar(ch5)
    omega_kia_seg = f" | Omega KIA {high_kia}" if high_kia else ""

    # --- Chapter rankings across 5 distinction metrics ---
    def _compute_chapter_ranks_by_metric(
        metric_fn, reverse: bool = True
    ) -> Dict[str, int]:
        """Compute dense ranks for chapters based on a metric function with dampening."""
        if not eligible:
            return {}

        # Calculate median member count for dampening
        _active_counts = [len(chapters_members.get(ch, set())) for ch in eligible]
        _median_members = statistics.median(_active_counts) if _active_counts else 1.0

        # Build raw values for eligible chapters
        raw_vals = {ch: metric_fn(ch) for ch in eligible}

        # Apply member-count-distance dampening before ranking
        if raw_vals:
            global_mean = statistics.mean(raw_vals.values())
            dampened_vals = {}
            for ch, raw in raw_vals.items():
                members = len(chapters_members.get(ch, set()))
                distance = abs(members - _median_members)
                dampening_factor = (
                    distance / _median_members if _median_members else 0.0
                )
                weight = 1.0 / (1.0 + dampening_factor)
                dampened_vals[ch] = weight * raw + (1.0 - weight) * global_mean
        else:
            dampened_vals = {}

        # Sort by dampened values
        sorted_by_val = sorted(
            [(ch, dampened_vals.get(ch, 0)) for ch in eligible],
            key=lambda x: (-x[1] if reverse else x[1], x[0]),
        )

        ranks = {}
        prev_val = None
        current_rank = 0
        for idx, (ch, val) in enumerate(sorted_by_val):
            if val != prev_val:
                current_rank = idx + 1
            ranks[ch] = current_rank
            prev_val = val
        return ranks

    ch_metrics = [_chap_ops, _chap_avg, _chap_pres, _chap_high_risk, _chap_avg_aar]
    ch_all_ranks: Dict[str, List[int]] = {}
    for metric_fn in ch_metrics:
        dense = _compute_chapter_ranks_by_metric(metric_fn)
        for ch, rank in dense.items():
            ch_all_ranks.setdefault(ch, []).append(rank)

    ch_median_ranks = {
        ch: statistics.median(ranks) for ch, ranks in ch_all_ranks.items() if ranks
    }
    ch_top5 = sorted(ch_median_ranks.items(), key=lambda x: (x[1], x[0]))[:5]

    # Add all Top 5 chapters to mentions (using role mapping logic)
    for chapter_name, _ in ch_top5:
        if not chapter_name:
            continue
        # Try to find role matching chapter name
        mentioned = False
        try:
            for r in guild.roles:
                if chapter_name.lower() in (r.name or "").lower():
                    top5_chapter_mentions.append(f"<@&{r.id}>")
                    mentioned = True
                    break
        except Exception:
            pass
        # Final fallback: if no role found, add chapter name as text
        if not mentioned and include_mentions:
            top5_chapter_mentions.append(f"@{chapter_name}")
    honoured_parts.extend(top5_chapter_mentions)

    # Construct HONOURED line
    honour_line = "HONOURED: " + " ".join(
        dict.fromkeys([p for p in honoured_parts if p])
    )

    # --- Build Top 5 Rankings block ---
    def _format_rank_display(rank_num: int, prev_rank: float, curr_rank: float) -> str:
        """Format rank number with tie handling."""
        if prev_rank is not None and curr_rank == prev_rank:
            return "  "  # Same rank as previous, show no number
        return f"{rank_num}."

    def _build_top5_block():
        period_label = "MONTHLY"
        # Use start date for the month title (since end_dt is first of next month)
        title_dt = start if start is not None else now
        try:
            title_dt = title_dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
        date_str = title_dt.strftime("%B %Y").upper()

        # Collect mentions for TOP RANKED line
        top_mentions = []

        # Individual mentions
        for uid, _ in ind_top5:
            if include_mentions:
                top_mentions.append(f"<@{uid}>")

        # Kill Team mentions (find roles)
        for tid, _ in kt_top5:
            if not include_mentions:
                continue
            # Try to interpret tid as role id
            try:
                rid = int(tid)
                r = guild.get_role(rid)
                if r:
                    top_mentions.append(f"<@&{r.id}>")
                    continue
            except Exception:
                pass
            # Special mapping: 'High Command'
            try:
                if isinstance(tid, str) and "high command" in str(tid).strip().lower():
                    top_mentions.append("<@&1452913063970865203>")
                    continue
            except Exception:
                pass
            # Special mapping: '<Company> Command'
            try:
                if isinstance(tid, str) and tid.strip().endswith(" Command"):
                    target_name = tid.strip()
                    for r in guild.roles:
                        rn = (r.name or "").strip()
                        if rn.lower() == target_name.lower():
                            top_mentions.append(f"<@&{r.id}>")
                            break
                    continue
            except Exception:
                pass
            # Fallback: search role by name containing team string
            try:
                for r in guild.roles:
                    if (tid or "").lower() in (r.name or "").lower():
                        top_mentions.append(f"<@&{r.id}>")
                        break
            except Exception:
                pass

        # Chapter mentions
        for ch, _ in ch_top5:
            if not include_mentions:
                continue
            try:
                r = _role_for_chapter_mention(guild, ch)
                if r:
                    top_mentions.append(f"<@&{r.id}>")
            except Exception:
                pass

        # Dedupe mentions while preserving order
        seen = set()
        deduped_mentions = []
        for m in top_mentions:
            if m not in seen:
                seen.add(m)
                deduped_mentions.append(m)

        lines = []
        lines.append(f"{date_str} {period_label} LEADERBOARDS")
        if deduped_mentions:
            lines.append("TOP RANKED: " + " ".join(deduped_mentions))
        lines.append("")
        lines.append("```ansi")
        lines.append(
            "\u001b[32m=============================================================================="
        )
        lines.append("  WATCH FORTRESS JERICHO // LEDGER-CAST")
        lines.append(f"  OPERATION-SCRIBE SERVITOR — {period_label} LEADERBOARDS")
        lines.append(f"  Date: {_format_imperial_date(display_dt)}")
        lines.append(
            "=============================================================================="
        )
        lines.append("")
        lines.append("TOP 5 BROTHERS")

        prev_rank = None
        display_rank = 0
        for idx, (uid, median_rank) in enumerate(ind_top5):
            curr_rank = median_rank
            if prev_rank is None or curr_rank != prev_rank:
                display_rank = idx + 1
            name = _member_display_name(guild, uid)
            lines.append(f"{display_rank}. {name} (Median Rank {median_rank:.1f})")
            prev_rank = curr_rank

        lines.append("")
        lines.append("TOP 5 KILL TEAMS")
        prev_rank = None
        display_rank = 0
        for idx, (tid, median_rank) in enumerate(kt_top5):
            curr_rank = median_rank
            if prev_rank is None or curr_rank != prev_rank:
                display_rank = idx + 1
            lines.append(f"{display_rank}. {tid} (Median Rank {median_rank:.1f})")
            prev_rank = curr_rank

        lines.append("")
        lines.append("TOP 5 CHAPTERS")
        prev_rank = None
        display_rank = 0
        for idx, (ch, median_rank) in enumerate(ch_top5):
            curr_rank = median_rank
            if prev_rank is None or curr_rank != prev_rank:
                display_rank = idx + 1
            lines.append(f"{display_rank}. {ch} (Median Rank {median_rank:.1f})")
            prev_rank = curr_rank

        lines.append(
            "=============================================================================="
        )
        lines.append("\u001b[0m```")
        return "\n".join(lines)

    # Build chapter mentions from doctrine winners only (preserve order and
    # dedupe). Only include winners that are non-empty and not the placeholder.
    chapter_mentions = []
    for ch in dict.fromkeys([ch1, ch2, ch3, ch4]):
        if not ch or ch == "Chapter":
            continue
        try:
            r = _role_for_chapter_mention(guild, ch)
            if r and include_mentions:
                chapter_mentions.append(f"<@&{r.id}>")
            else:
                chapter_mentions.append(ch)
        except Exception:
            chapter_mentions.append(ch)

    honoured_parts.extend(chapter_mentions)

    # Construct HONOURED line (dedupe while preserving order)
    honour_line = "HONOURED: " + " ".join(
        dict.fromkeys([p for p in honoured_parts if p])
    )

    # Choose display date for the honours header: use start of window (the actual
    # reporting month) rather than end (which is first of next month).
    display_dt = start if start is not None else now

    # Build unified ANSI block with distinctions + horizontal top 5 rankings
    def _format_top_rankings_horizontal():
        """Format top 5 rankings as three columns: Brothers | Kill Teams | Chapters."""

        def truncate_name(name: str, max_len: int = 25) -> str:
            try:
                return str(name)[:max_len].ljust(max_len)
            except Exception:
                return "Unknown".ljust(max_len)

        # Build column data
        bro_col = ["BROTHERS"]
        for idx, (uid, _) in enumerate(ind_top5):
            name = _member_display_name(guild, uid)
            bro_col.append(f"{idx + 1}.{truncate_name(name)}")

        kt_col = ["KILL TEAMS"]
        for idx, (tid, _) in enumerate(kt_top5):
            kt_col.append(f"{idx + 1}.{truncate_name(tid)}")

        ch_col = ["CHAPTERS"]
        for idx, (ch, _) in enumerate(ch_top5):
            ch_col.append(f"{idx + 1}.{truncate_name(ch)}")

        # Build 3-column layout with padding
        lines = []
        lines.append("")
        lines.append("TOP 5 RANKINGS")
        lines.append(
            "──────────────────────────────────────────────────────────────────────────────"
        )

        # Header + 5 data rows
        for i in range(6):
            bro_str = bro_col[i] if i < len(bro_col) else ""
            kt_str = kt_col[i] if i < len(kt_col) else ""
            ch_str = ch_col[i] if i < len(ch_col) else ""
            # Each column gets ~28 chars width for better spacing
            line = f"{bro_str:<28}  {kt_str:<28}  {ch_str:<28}"
            lines.append(line.rstrip())

        lines.append(
            "──────────────────────────────────────────────────────────────────────────────"
        )

        return "\n".join(lines)

    ansi_inner = (
        "==============================================================================\n"
        "  WATCH FORTRESS JERICHO // LEDGER-CAST\n"
        "  OPERATION-SCRIBE SERVITOR — MONTHLY LEADERBOARDS\n"
        f"  Date: {_format_imperial_date(display_dt)}\n"
        "==============================================================================\n"
        + _format_top_rankings_horizontal()
        + "\n\n"
        "INDIVIDUAL DISTINCTIONS\n"
        f"Operations               {tempo_disp} ({tempo_val})\n"
        f"Avg Pts/Op               {lethal_disp} ({fmt_avg(lethal_val)})\n"
        f"Gene-seed Pts            {gene_disp} ({gene_val})\n"
        f"Armory Pts               {arm_disp} ({arm_val})\n"
        f"Hard-Strat+Omega         {high_disp} ({high_val}{omega_kia_seg})\n\n"
        "KILL TEAM DISTINCTIONS\n"
        f"Operations               {kt_ops_name} ({kt_ops_val})\n"
        f"Avg Pts/Op               {kt_avg_name} ({fmt_avg(kt_avg_val)})\n"
        f"Armory+Gene-seed         {kt_pres_name} ({kt_pres_arm}|{kt_pres_gene})\n"
        f"Hard-Strat+Omega         {kt_risk_name} ({kt_risk_val})\n"
        f"AARs/Member              {kt_force_name} ({fmt_avg(kt_force_val)})\n\n"
        "CHAPTER DISTINCTIONS\n"
        f"Operations               {ch1} ({ch1_val})\n"
        f"Avg Pts/Op               {ch2} ({fmt_avg(ch2_val)})\n"
        f"Armory+Gene-seed         {ch3} ({ch3_arm}|{ch3_gene})\n"
        f"Hard-Strat+Omega         {ch4} ({ch4_val})\n"
        f"AARs/Member              {ch5} ({fmt_avg(ch5_val)})\n"
        "=============================================================================="
    )

    # Wrap the inner ANSI block in an ANSI color start and code fence for Discord
    ansi = f"```ansi\n\u001b[32m{ansi_inner}\n\u001b[0m```"

    # Apply fallbacks for character limit
    content = honour_line + "\n" + ansi
    if len(content) > 2000:
        # 1) Remove chapter distinction block from inner
        inner_no_doctrine = (
            ansi_inner.split("CHAPTER DISTINCTIONS")[0]
            + "=============================================================================="
        )
        ansi_no_doctrine = f"```ansi\n\u001b[32m{inner_no_doctrine}\n\u001b[0m```"
        content = honour_line + "\n" + ansi_no_doctrine
    if len(content) > 2000:
        # 2) Remove Omega KIA segment if present
        inner_no_kia = ansi_inner.replace(omega_kia_seg, "")
        ansi_no_kia = f"```ansi\n\u001b[32m{inner_no_kia}\n\u001b[0m```"
        content = honour_line + "\n" + ansi_no_kia
    if len(content) > 2000:
        # 3) Fallback: remove chapter distinctions AND top 5 rankings
        inner_compact = (
            ansi_inner.split("CHAPTER DISTINCTIONS")[0]
            + "=============================================================================="
        )
        ansi_compact = f"```ansi\n\u001b[32m{inner_compact}\n\u001b[0m```"
        content = honour_line + "\n" + ansi_compact

    # Create unified mobile embed combining both distinctions and top 5 rankings
    period_label = "Monthly"

    embed = discord.Embed(
        title=f"{period_label} Leaderboards",
        description=f"📅 **Date:** {_format_imperial_date(display_dt)}",
        color=0x2ECC71,
    )

    # Top 5 Brothers
    brothers_text = ""
    prev_rank = None
    display_rank = 0
    for idx, (uid, median_rank) in enumerate(ind_top5):
        curr_rank = median_rank
        if prev_rank is None or curr_rank != prev_rank:
            display_rank = idx + 1
        name = _member_display_name(guild, uid)
        brothers_text += f"{display_rank}. {name}\n"
        prev_rank = curr_rank

    if brothers_text:
        embed.add_field(
            name="🏃 Top 5 Brothers", value=brothers_text.strip(), inline=False
        )

    # Top 5 Kill Teams
    teams_text = ""
    prev_rank = None
    display_rank = 0
    for idx, (tid, median_rank) in enumerate(kt_top5):
        curr_rank = median_rank
        if prev_rank is None or curr_rank != prev_rank:
            display_rank = idx + 1
        teams_text += f"{display_rank}. {tid}\n"
        prev_rank = curr_rank

    if teams_text:
        embed.add_field(
            name="⚡ Top 5 Kill Teams", value=teams_text.strip(), inline=False
        )

    # Top 5 Chapters
    chapters_text = ""
    prev_rank = None
    display_rank = 0
    for idx, (ch, median_rank) in enumerate(ch_top5):
        curr_rank = median_rank
        if prev_rank is None or curr_rank != prev_rank:
            display_rank = idx + 1
        chapters_text += f"{display_rank}. {ch}\n"
        prev_rank = curr_rank

    if chapters_text:
        embed.add_field(
            name="🛡️ Top 5 Chapters", value=chapters_text.strip(), inline=False
        )

    # Individual Distinctions
    omega_suffix = f" | Omega KIA {high_kia}" if high_kia else ""
    individual_text = (
        f"**Operations:** {tempo_disp} ({tempo_val})\n"
        f"**Avg Pts/Op:** {lethal_disp} ({lethal_val:.1f})\n"
        f"**Gene-seed Pts:** {gene_disp} ({gene_val})\n"
        f"**Armory Pts:** {arm_disp} ({arm_val})\n"
        f"**Hard-Strat+Omega:** {high_disp} ({high_val}{omega_suffix})"
    )
    embed.add_field(
        name="🏆 Individual Distinctions", value=individual_text, inline=False
    )

    # Kill Team Distinctions
    killteam_text = (
        f"**Operations:** {kt_ops_name} ({kt_ops_val})\n"
        f"**Avg Pts/Op:** {kt_avg_name} ({kt_avg_val:.1f})\n"
        f"**Armory+Gene-seed:** {kt_pres_name} ({kt_pres_arm}|{kt_pres_gene})\n"
        f"**Hard-Strat+Omega:** {kt_risk_name} ({kt_risk_val})\n"
        f"**AARs/Member:** {kt_force_name} ({kt_force_val:.1f})"
    )
    embed.add_field(name="⚔️ Kill Team Distinctions", value=killteam_text, inline=False)

    # Chapter Distinctions
    chapter_text = (
        f"**Operations:** {ch1} ({ch1_val})\n"
        f"**Avg Pts/Op:** {ch2} ({ch2_val:.1f})\n"
        f"**Armory+Gene-seed:** {ch3} ({ch3_arm}|{ch3_gene})\n"
        f"**Hard-Strat+Omega:** {ch4} ({ch4_val})\n"
        f"**AARs/Member:** {ch5} ({ch5_val:.1f})"
    )
    embed.add_field(name="🛡️ Chapter Distinctions", value=chapter_text, inline=False)

    embed.set_footer(text="Use PC/Console button for detailed ANSI view")

    return honour_line, ansi, embed


@tasks.loop(minutes=15)
async def _scheduled_honours_runner():
    """Run every 15 minutes and post monthly honours when appropriate (UTC).
    Monthly posts on 1st of each month at 1 AM UTC.
    """
    try:
        # Use UTC for consistent scheduling
        now_utc = datetime.now(timezone.utc)
        today = now_utc.date()
        logger.info(
            f"Honours runner tick: {now_utc.isoformat()} weekday={today.weekday()} hour={now_utc.hour}"
        )

        if DATASTORE is None:
            logger.warning("Honours runner: DATASTORE is None, skipping")
            return
        # Resolve target guild and channel
        guild = _resolve_notification_guild()
        if not guild:
            logger.warning("Honours runner: Could not resolve guild, skipping")
            return
        ch_id = CONFIG.get("honours_channel_id")
        if not ch_id:
            logger.warning("Honours runner: honours_channel_id not set, skipping")
            return
        try:
            channel = guild.get_channel(int(ch_id)) or await bot.fetch_channel(
                int(ch_id)
            )
        except Exception:
            logger.exception("Honours runner: Could not resolve honours channel")
            return
        global LAST_MONTHLY_POST_DATE
        # Determine whether monthly honours posting is due (1st of month at 1 AM UTC)
        monthly_due = (
            today.day == 1
            and now_utc.hour == 1
            and LAST_MONTHLY_POST_DATE != str(today)
        )
        logger.info(
            f"Honours runner: monthly_due={monthly_due} LAST_MONTHLY={LAST_MONTHLY_POST_DATE}"
        )

        # Helper to send honours content respecting Discord message length
        async def _send_honours(line, block, embed=None):
            try:
                # Send unified honours content with PC/Mobile toggle
                content = line + "\n" + block
                if embed and len(content) <= 2000:
                    # Use ToggleFormatView - default to embed (mobile-friendly)
                    # ephemeral_context=False: PC button sends ephemeral, public message stays embed
                    view = ToggleFormatView(
                        text_content=content,
                        embed=embed,
                        default="embed",
                        ephemeral_context=False,
                    )
                    await channel.send(
                        embed=embed,
                        view=view,
                        allowed_mentions=discord.AllowedMentions(
                            users=True, roles=True
                        ),
                    )
                elif len(content) <= 2000:
                    # Fallback without embed if none provided
                    await channel.send(
                        content,
                        allowed_mentions=discord.AllowedMentions(
                            users=True, roles=True
                        ),
                    )
                else:
                    # Content still too long; send mentions + embed separately
                    await channel.send(
                        line,
                        allowed_mentions=discord.AllowedMentions(
                            users=True, roles=True
                        ),
                    )
                    if embed:
                        view = ToggleFormatView(
                            text_content=block,
                            embed=embed,
                            default="embed",
                            ephemeral_context=False,
                        )
                        await channel.send(embed=embed, view=view)
                    else:
                        await channel.send(block)
            except Exception:
                logger.exception("Failed to post honours")

        # Helper to run pre-audit (ingest + recheck) before posting honours
        async def _run_pre_audit(span_days: int):
            """Run sanctify_battle_records and audit_archive_discrepancies for the given window."""
            try:
                aar_channel = discord.utils.get(
                    guild.channels, name="᛭⋅⋅after-action-reports⋅⋅᛭"
                )
                if not aar_channel:
                    logger.warning("Pre-audit: AAR channel not found, skipping audit")
                    return
                # Acquire lock to prevent concurrent reconciliations
                if RECONCILE_LOCK.locked():
                    logger.info("Pre-audit: Reconcile lock held, skipping audit")
                    return
                await RECONCILE_LOCK.acquire()
                try:
                    # Run ingest (sanctify battle records)
                    logger.info(f"Pre-audit: Running ingest for last {span_days} days")
                    ingested, rejected = await _run_ingest_new(aar_channel, span_days)
                    logger.info(f"Pre-audit: Ingested {ingested}, rejected {rejected}")
                    # Run recheck (audit archive discrepancies)
                    logger.info(f"Pre-audit: Running recheck for last {span_days} days")
                    fixed, still_broken = await _run_recheck_errors(
                        aar_channel, span_days
                    )
                    logger.info(
                        f"Pre-audit: Fixed {fixed}, still broken {still_broken}"
                    )
                finally:
                    RECONCILE_LOCK.release()
            except Exception:
                logger.exception("Pre-audit failed")

        if monthly_due:
            # Compute previous month boundaries in UTC
            first_of_current_utc = datetime(now_utc.year, now_utc.month, 1)
            if now_utc.month == 1:
                prev_month = 12
                prev_year = now_utc.year - 1
            else:
                prev_month = now_utc.month - 1
                prev_year = now_utc.year
            # Run pre-audit for monthly window (days in previous month)
            monthly_days = calendar.monthrange(prev_year, prev_month)[1]
            await _run_pre_audit(monthly_days)

            prev_start_utc = datetime(prev_year, prev_month, 1)
            try:
                prev_start = prev_start_utc
                prev_end = first_of_current_utc
            except Exception:
                prev_start = datetime(prev_year, prev_month, 1)
                prev_end = datetime(now_utc.year, now_utc.month, 1)

            line, block, embed = await _build_honours(
                guild, 30, include_mentions=True, start_dt=prev_start, end_dt=prev_end
            )
            try:
                await _send_honours(line, block, embed)
            except Exception:
                logger.exception("Failed to post monthly honours")
            LAST_MONTHLY_POST_DATE = str(today)
    except Exception:
        logger.exception("Honours runner failed")


@_scheduled_honours_runner.before_loop
async def _before_honours_runner():
    await bot.wait_until_ready()


def _user_is_forgemaster(user: discord.User | discord.Member) -> bool:
    try:
        names = _canonical_role_names(user)
        return any("Forgemaster" in n for n in names)
    except Exception:
        return False


@bot.tree.command(
    name="preview_honours",
    description="Preview monthly honours (Forgemaster only)",
)
async def preview_honours(interaction: discord.Interaction):
    # Forgemaster-only
    if not _user_is_forgemaster(interaction.user):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    # Try to defer; if the interaction is already unknown/expired, fall back
    # to sending an immediate response later. Track whether defer succeeded.
    deferred = False
    try:
        await interaction.response.defer(ephemeral=True)
        deferred = True
    except Exception as e:
        logger.warning(f"preview_honours: defer failed: {e}")
        deferred = False
    guild = interaction.guild
    # Monthly preview: show current partial month (from 1st of current month to now)
    now = datetime.utcnow()
    first_of_current = datetime(now.year, now.month, 1)
    prev_start = first_of_current
    prev_end = now
    logger.info(f"preview_honours: building honours for {prev_start} to {prev_end}")
    try:
        honour_line, ansi, embed = await _build_honours(
            guild, 30, include_mentions=True, start_dt=prev_start, end_dt=prev_end
        )
        logger.info(
            f"preview_honours: built honours, line len={len(honour_line)}, ansi len={len(ansi)}, embed={type(embed)}"
        )
    except Exception as e:
        logger.exception(f"preview_honours: _build_honours failed: {e}")
        if deferred:
            await interaction.followup.send(
                f"Error building honours: {e}", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"Error building honours: {e}", ephemeral=True
            )
        return
    # Include mentions in preview so Forgemasters can test tagging; send unified message
    # with PC/Mobile toggle and respect Discord message length limits.

    content = honour_line + "\n" + ansi
    try:
        if len(content) <= 2000 and embed:
            # Use ToggleFormatView for preview with unified embed
            view = ToggleFormatView(text_content=content, embed=embed, default="ansi")
            if deferred:
                await interaction.followup.send(
                    content,
                    view=view,
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=True),
                )
            else:
                await interaction.response.send_message(
                    content,
                    view=view,
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=True),
                )
        elif len(content) <= 2000:
            if deferred:
                await interaction.followup.send(
                    content,
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=True),
                )
            else:
                await interaction.response.send_message(
                    content,
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=True),
                )
        else:
            # Split into mentions then ANSI block. If deferred, use followups; else
            # send the honour_line as the response and post the ANSI block to the
            # channel (non-ephemeral) as a best-effort fallback.
            if deferred:
                await interaction.followup.send(
                    honour_line,
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=True),
                )
                if embed:
                    view = ToggleFormatView(
                        text_content=ansi, embed=embed, default="ansi"
                    )
                    await interaction.followup.send(ansi, view=view, ephemeral=True)
                else:
                    await interaction.followup.send(ansi, ephemeral=True)
            else:
                await interaction.response.send_message(
                    honour_line,
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=True),
                )
                try:
                    ch = interaction.channel
                    if ch:
                        if embed:
                            view = ToggleFormatView(
                                text_content=ansi, embed=embed, default="ansi"
                            )
                            await ch.send(ansi, view=view)
                        else:
                            await ch.send(ansi)
                except Exception:
                    # last-resort: attempt to send ANSI as a normal followup
                    try:
                        if embed:
                            view = ToggleFormatView(
                                text_content=ansi, embed=embed, default="ansi"
                            )
                            await interaction.followup.send(ansi, view=view)
                        else:
                            await interaction.followup.send(ansi)
                    except Exception:
                        pass
    except Exception:
        # Fallback: try to send ANSI block without mentions
        try:
            if embed:
                view = ToggleFormatView(text_content=ansi, embed=embed, default="ansi")
                if deferred:
                    await interaction.followup.send(ansi, view=view, ephemeral=True)
                else:
                    await interaction.response.send_message(
                        ansi, view=view, ephemeral=True
                    )
            else:
                if deferred:
                    await interaction.followup.send(ansi, ephemeral=True)
                else:
                    await interaction.response.send_message(ansi, ephemeral=True)
        except Exception:
            # give up silently; command already logged
            pass


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


def can_roster_audit(user: discord.User | discord.Member) -> bool:
    """Check if user has permission to run roster_audit."""
    admin_ids = set(str(x) for x in (CONFIG.get("admin_user_ids") or []))
    uid = str(getattr(user, "id", None))
    if uid in admin_ids:
        return True

    perms = CONFIG.get("permissions", {}) or {}
    block = perms.get("roster_audit", {}) or {}

    for r in block.get("roles") or []:
        role_names = _canonical_role_names(user)
        if str(r) in role_names:
            return True

    for i in block.get("user_ids") or []:
        if uid == str(i):
            return True

    # Fallback: check if user has Watch Master or Forgemaster role
    names = _canonical_role_names(user)
    return any(n in ("Watch Master", "Forgemaster") for n in names)


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
    if not (can_roster_audit(interaction.user) and is_allowed_channel(interaction)):
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
    if not (is_watch_command(interaction.user) and is_allowed_channel(interaction)):
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
    studs_aar_met_time_not: List[Tuple[discord.Member, int, int, int, int, datetime]] = []  # member, aar_pts, weeks, earned, displayed, next_stud_date
    studs_aar_not_time_met: List[Tuple[discord.Member, int, int, int, int, int]] = []  # member, aar_pts, weeks, earned, displayed, aar_needed
    studs_aar_not_time_not: List[Tuple[discord.Member, int, int, int, int, datetime, int]] = []  # member, aar_pts, weeks, earned, displayed, next_time_date, aar_needed

    # --- Watch Veteran Queue (for Watch Brother only) ---
    # Requirements: 200 AAR points AND 2 weeks in server
    veteran_aar_met_time_not: List[Tuple[discord.Member, int, int, datetime]] = []  # member, aar_pts, weeks, promotion_date
    veteran_aar_not_time_met: List[Tuple[discord.Member, int, int, int]] = []  # member, aar_pts, weeks, aar_needed
    veteran_aar_not_time_not: List[Tuple[discord.Member, int, int, datetime, int]] = []  # member, aar_pts, weeks, time_date, aar_needed

    # Track roles that indicate veteran or higher
    veteran_or_higher_roles = {
        "Watch Veteran", "Oathsworn", "Kill Team Champion", "Watch Sergeant",
        "Watch Techmarine", "Watch Librarian", "Watch Apothecary", "Watch Chaplain",
        "Company Champion", "Watch Lieutenant", "Watch Captain", "Venerable",
        "Forgemaster", "Void Warden", "High Chaplain", "Chief Apothecary",
        "Lord Executioner", "Watch Master",
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
                days_until = weeks_needed * 7 - ((now - joined_at).days % 7) if joined_at else weeks_needed * 7
                promotion_date = now + timedelta(days=days_until)
                veteran_aar_met_time_not.append((member, aar_points, weeks_in_server, promotion_date))
            elif not aar_met and time_met:
                # Time met, waiting on AAR
                aar_needed = 200 - aar_points
                veteran_aar_not_time_met.append((member, aar_points, weeks_in_server, aar_needed))
            else:
                # Neither met
                weeks_needed = 2 - weeks_in_server
                days_until = weeks_needed * 7 - ((now - joined_at).days % 7) if joined_at else weeks_needed * 7
                time_date = now + timedelta(days=days_until)
                aar_needed = 200 - aar_points
                veteran_aar_not_time_not.append((member, aar_points, weeks_in_server, time_date, aar_needed))

        # --- Process Service Studs eligibility ---
        if is_veteran_or_higher:
            # Calculate current studs entitlement
            studs_time = weeks_in_server // 4
            studs_aar = aar_points // 400
            earned_studs = min(studs_time, studs_aar)

            # Count currently displayed studs from nickname
            dn = str(member.nick or member.display_name or "")
            displayed_cer = dn.count("◆")
            displayed_elec = dn.count("●")
            displayed_plas = dn.count("○")
            displayed_studs = displayed_cer * 25 + displayed_elec * 5 + displayed_plas

            # Check if they're owed studs (only show those who could earn more)
            # We want to show people who would be eligible for MORE studs if they meet requirements
            next_stud_threshold_time = (displayed_studs + 1) * 4  # weeks needed for next stud
            next_stud_threshold_aar = (displayed_studs + 1) * 400  # AAR needed for next stud

            aar_met_for_next = aar_points >= next_stud_threshold_aar
            time_met_for_next = weeks_in_server >= next_stud_threshold_time

            if aar_met_for_next and time_met_for_next:
                # Already eligible for next stud - they just need to display it
                pass
            elif aar_met_for_next and not time_met_for_next:
                # AAR met, waiting on time for next stud
                weeks_needed = next_stud_threshold_time - weeks_in_server
                days_until = weeks_needed * 7 - ((now - joined_at).days % 7) if joined_at else weeks_needed * 7
                next_stud_date = now + timedelta(days=days_until)
                studs_aar_met_time_not.append((member, aar_points, weeks_in_server, earned_studs, displayed_studs, next_stud_date))
            elif not aar_met_for_next and time_met_for_next:
                # Time met, waiting on AAR for next stud
                aar_needed = next_stud_threshold_aar - aar_points
                studs_aar_not_time_met.append((member, aar_points, weeks_in_server, earned_studs, displayed_studs, aar_needed))
            else:
                # Neither met for next stud
                weeks_needed = next_stud_threshold_time - weeks_in_server
                days_until = weeks_needed * 7 - ((now - joined_at).days % 7) if joined_at else weeks_needed * 7
                next_time_date = now + timedelta(days=days_until)
                aar_needed = next_stud_threshold_aar - aar_points
                studs_aar_not_time_not.append((member, aar_points, weeks_in_server, earned_studs, displayed_studs, next_time_date, aar_needed))

    # Sort lists by proximity to eligibility
    # For AAR met, time not: sort by soonest date
    veteran_aar_met_time_not.sort(key=lambda x: x[3])  # promotion_date
    studs_aar_met_time_not.sort(key=lambda x: x[5])  # next_stud_date
    
    # For AAR not, time met: sort by least AAR needed
    veteran_aar_not_time_met.sort(key=lambda x: x[3])  # aar_needed
    studs_aar_not_time_met.sort(key=lambda x: x[5])  # aar_needed
    
    # For neither met: sort by soonest time date (they can always grind AAR)
    veteran_aar_not_time_not.sort(key=lambda x: x[3])  # time_date
    studs_aar_not_time_not.sort(key=lambda x: x[5])  # next_time_date

    def _build_field_value(lines: List[str], total_count: int, max_shown: int = 10) -> str:
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
            lines.append(f"᛭⋅ {member.mention} | {aar_pts} AAR | **{date_str}**")
        veteran_embed.add_field(
            name=f"▸ Ready on Date ({len(veteran_aar_met_time_not)})",
            value=_build_field_value(lines, len(veteran_aar_met_time_not)),
            inline=False,
        )

    # AAR not, time met
    if veteran_aar_not_time_met:
        lines = []
        for member, aar_pts, weeks, aar_needed in veteran_aar_not_time_met:
            lines.append(f"᛭⋅ {member.mention} | {aar_pts} AAR | needs **{aar_needed}**")
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
            lines.append(f"᛭⋅ {member.mention} | {aar_pts} AAR | {date_str}, +{aar_needed}")
        veteran_embed.add_field(
            name=f"▸ Needs Both ({len(veteran_aar_not_time_not)})",
            value=_build_field_value(lines, len(veteran_aar_not_time_not)),
            inline=False,
        )

    if not (veteran_aar_met_time_not or veteran_aar_not_time_met or veteran_aar_not_time_not):
        veteran_embed.add_field(name="▸ Status", value="No Watch Brothers pending.", inline=False)

    total_veterans = len(veteran_aar_met_time_not) + len(veteran_aar_not_time_met) + len(veteran_aar_not_time_not)
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
        for member, aar_pts, weeks, earned, displayed, next_date in studs_aar_met_time_not:
            date_str = next_date.strftime("%b %d")
            lines.append(f"᛭⋅ {member.mention} | #{displayed+1} | **{date_str}**")
        studs_embed.add_field(
            name=f"▸ Ready on Date ({len(studs_aar_met_time_not)})",
            value=_build_field_value(lines, len(studs_aar_met_time_not)),
            inline=False,
        )

    # AAR not, time met
    if studs_aar_not_time_met:
        lines = []
        for member, aar_pts, weeks, earned, displayed, aar_needed in studs_aar_not_time_met:
            lines.append(f"᛭⋅ {member.mention} | [{displayed}] | needs **{aar_needed}**")
        studs_embed.add_field(
            name=f"▸ Needs AAR ({len(studs_aar_not_time_met)})",
            value=_build_field_value(lines, len(studs_aar_not_time_met)),
            inline=False,
        )

    # Neither met
    if studs_aar_not_time_not:
        lines = []
        for member, aar_pts, weeks, earned, displayed, next_time, aar_needed in studs_aar_not_time_not:
            date_str = next_time.strftime("%b %d")
            lines.append(f"᛭⋅ {member.mention} | [{displayed}] | {date_str}, +{aar_needed}")
        studs_embed.add_field(
            name=f"▸ Needs Both ({len(studs_aar_not_time_not)})",
            value=_build_field_value(lines, len(studs_aar_not_time_not)),
            inline=False,
        )

    if not (studs_aar_met_time_not or studs_aar_not_time_met or studs_aar_not_time_not):
        studs_embed.add_field(name="▸ Status", value="No veterans pending.", inline=False)

    total_studs = len(studs_aar_met_time_not) + len(studs_aar_not_time_met) + len(studs_aar_not_time_not)
    studs_embed.set_footer(text=f"᛭⋅ {total_studs} in queue ⋅᛭")
    embeds.append(studs_embed)

    await interaction.followup.send(embeds=embeds, ephemeral=True)


if __name__ == "__main__":
    _main()
