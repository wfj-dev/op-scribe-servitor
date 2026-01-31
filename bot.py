#!/usr/bin/env python3


import os
import asyncio
import json
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta, timezone
import uuid
import re
import itertools
from collections import Counter
from typing import Dict, List, Tuple, Optional
import hashlib
import logging
import time
from logging.handlers import RotatingFileHandler
import signal
import argparse
import statistics

# Import DataStore
from datastore import DataStore

# Data file locations
DATA_DIR = "data"
AAR_RECORDS_PATH = os.path.join(DATA_DIR, "aar_records.json")
AAR_ERRORS_PATH = os.path.join(DATA_DIR, "aar_errors.json")
PROCESSED_IDS_PATH = os.path.join(DATA_DIR, "processed_ids.json")
TROPHY_HALL_INDEX_PATH = os.path.join(DATA_DIR, "trophy_hall_index.json")
OATHS_INDEX_PATH = os.path.join(DATA_DIR, "oaths_index.json")
RITES_PATH = os.path.join(DATA_DIR, "rites.json")

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

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Global lock to serialize reconciliation runs
RECONCILE_LOCK = asyncio.Lock()
CHAPLAIN_INGEST_LOCK = asyncio.Lock()

# Rites storage lock
RITES_LOCK = asyncio.Lock()

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

    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    emoji = "✅" if status == "ONLINE" else "⛔"
    flavor = (
        "Machine-spirit standing by."
        if status == "ONLINE"
        else "Machine-spirit at rest."
    )
    # Concise, at-a-glance status with a touch of flavor
    content = f"{mention} V-1 STATUS: {status} {emoji} — {ts}\n{flavor}"
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
    # Flush DataStore before closing
    try:
        if DATASTORE:
            await DATASTORE.shutdown()
    except Exception as e:
        logger.debug(f"DataStore shutdown failed: {e}")
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
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(fh)
except Exception as e:
    try:
        print(f"[Logging setup] File handler failed: {e}")
    except Exception:
        pass

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
    "Oathsworn",
    "Watch Veteran",
    "Watch Brother",
]

# Canonical list of known home chapters for lookup
HOME_CHAPTERS = [
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
    "Red Scorpions",
    "Red Templars",
    "Salamanders",
    "Sons of Medusa",
    "Space Wolves",
    "Storm Giants",
    "Ultramarines",
    "White Scars",
    "Black Shield",
]

# Restrict commands to a specific channel (demo/training)
ALLOWED_COMMAND_CHANNELS = {
    # Update to your desired demo channel name
    "❖⋅data-vault⋅❖"
}

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
    except Exception as e:
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
    litany_text = (
        "Jericho Logi-Scribe Servitor V-1 — Function Litany\n\n"
        "Sanctioned Commands (summary):\n"
        "• /tally_deeds @Brother — Deeds ledger: AAR points, gene-seed credit, armory tally, rank. (Sergeant+)\n"
        "• /combat_bonds [@Brother] [window:N] — Fortress/top bonds or target bonds (default 100 AARs). (Sergeant+)\n"
        "• /audit_archive_discrepancies — Re-check rejected AARs for resolution. (Watch Master/Forgemaster)\n"
        "• /sanctify_battle_records [span_days:N] — Ingest sanctioned AARs via cursor. (Watch Master/Forgemaster)\n"
        "• /reconcile_records [span_days:N] — Audit then ingest in one rite. (Watch Master/Forgemaster)\n\n"
        "Commands restricted to sanctified channels. Honor and memory preserved."
    )
    await interaction.response.send_message(litany_text, ephemeral=True)


# Forge rite command group
# top-level commands: /forge_rite and /set_rite (not a command group)


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
    try:
        await _set_user_rite(int(interaction.user.id), rite_text)
        await interaction.response.send_message(
            "Consecration rite saved.", ephemeral=True
        )
    except Exception:
        await interaction.response.send_message("Failed to save rite.", ephemeral=True)


@bot.tree.command(
    name="forge_rite",
    description="Generate and post a cogitator attestation block for a member.",
)
@app_commands.describe(member="Member to attest")
async def _attest(interaction: discord.Interaction, member: discord.Member):
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

    # Build attestation
    # Warhammer 40k-style date: check.dayOfYear(3-digit).yearWithinMillennium(3-digit).Millennium
    try:
        now = datetime.now()
        year = now.year
        day_of_year = now.timetuple().tm_yday
        # Check digit (usually 0)
        check = 0
        year_within_millennium = year % 1000
        millennium = (year - 1) // 1000 + 1
        ts = f"{check}.{day_of_year:03d}.{year_within_millennium:03d}.M{millennium}"
    except Exception:
        ts = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    ledger = uuid.uuid4().hex[:12].upper()
    # Authority
    if role_key == "forgemaster":
        authority = "Jericho High Command"
    else:
        comp = _find_company_or_chapter(interaction.user) or "Unknown Company"
        authority = comp

    # Attesting name
    attester = getattr(interaction.user, "display_name", None) or getattr(
        interaction.user, "name", str(interaction.user.id)
    )

    # Optional personal rite
    try:
        rite_text = await _get_user_rite(int(interaction.user.id))
    except Exception:
        rite_text = None

    # Auto-sign: prefer Forgemaster or Techmarine mention
    try:
        company = _find_company_or_chapter(interaction.user)
        # Use the attester (display name) and avoid duplicating the role/token
        if role_key == "forgemaster":
            signer = f"{attester}, Jericho High Command"
        elif role_key == "techmarine":
            signer = f"{attester}, {company or 'Unknown Company'}"
        else:
            # fallback: include top role (if any) or just the display name
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

    # Assemble block
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
    bearer_name = getattr(member, "display_name", None) or getattr(
        member, "name", str(member.id)
    )
    lines.append(f"Bearer: {bearer_name}")
    lines.append("")
    lines.append("Inspection Status = PASSED")
    lines.append("Regulation Compliance = CONFIRMED")
    lines.append("")
    lines.append(f"Attesting Techmarine = {attester}")
    lines.append(f"Authority = {authority}")
    lines.append(f"Timestamp = {ts}")
    lines.append(f"Ledger Reference = {ledger}")
    lines.append("")
    if rite_text:
        lines.append("Consecration Rite:")
        for l in str(rite_text).splitlines():
            lines.append(f"  {l}")
        lines.append("")
    lines.append(f"WITNESSED AND SEALED: {signer}")
    lines.append(
        "=============================================================================="
    )
    lines.append("\u001b[0m```")

    try:
        # Ping the bearer (so they receive a notification) but keep the
        # formatted attestation block separate so the display remains intact.
        content = f"{member.mention}\n" + "\n".join(lines)
        await interaction.response.send_message(
            content, allowed_mentions=discord.AllowedMentions(users=True)
        )
    except Exception:
        try:
            await interaction.response.send_message(
                "Failed to post attestation.", ephemeral=True
            )
        except Exception:
            pass


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
                # If the AAR has been processed since the error was recorded,
                # remove it from the errors archive rather than touching the
                # saved records. Previously this removed the record file by
                # mistake which prevented error entries from being cleared.
                data = _load_json_dict(AAR_ERRORS_PATH)
                sid = str(aar_id)
                if sid in data:
                    del data[sid]
                    _save_json_dict(AAR_ERRORS_PATH, data)
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
                    await save_aar_record(record)
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
        await save_aar_record(record)
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
        last_flush_str = datetime.datetime.utcfromtimestamp(last_flush).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    else:
        last_flush_str = "Never"
    msg = (
        f"```ansi\n"
        f"\u001b[32m==============================================================================\n"
        f"  WATCH FORTRESS JERICHO // SERVITOR CACHE DIAGNOSTICS\n"
        f"==============================================================================\n"
        f"  User Stats Cache Size:        {stats['user_stats_cache_size']}\n"
        f"  Home Chapter Cache Size:      {stats['home_chapter_cache_size']}\n"
        f"  Dirty AAR Records:            {stats['dirty_records']}\n"
        f"  Dirty Processed IDs:          {stats['dirty_ids']}\n"
        f"  Last Flush Time:              {last_flush_str}\n"
        f"  Home Chapter Cache Hits:      {stats['home_chapter_cache_hits']}\n"
        f"  Home Chapter Cache Misses:    {stats['home_chapter_cache_misses']}\n"
        f"==============================================================================\n"
        f"\u001b[0m```"
    )
    await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(
    name="reparse_records",
    description="Re-parse stored AAR records from their message_url and update records (admin).",
)
@app_commands.describe(limit="Optional: max number of records to reparse.")
async def reparse_records(interaction: discord.Interaction, limit: int | None = None):
    if not (can_reconcile_records(interaction.user) and is_allowed_channel(interaction)):
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
        # Iterate snapshot of records
        for key, rec in list(DATASTORE._records.items()):
            if limit and total >= limit:
                break
            total += 1
            msg_url = rec.get("message_url")
            if not msg_url:
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
                    higher = [i for i in member_rank_indices if i is not None and i < role_idx]
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
                        names = {getattr(r, "name", "") for r in getattr(m, "roles", [])}
                        if (getattr(killteam, "name", "") in names) or (leader in names):
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
            joined_str = (
                joined_at.strftime("%Y-%m-%d %H:%M UTC") if joined_at else "Unknown"
            )
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
                studs_display = f"— (0 Plasteel)"
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
                types_str = ", ".join(parts) if parts else f"0 Plasteel"
                studs_display = f"{studs_symbols} ({types_str})"

                # Compare with studs already present in the display name and add
                # an in-universe notification if there's a mismatch.
                try:
                    dn = str(display_name or "")
                    existing_cer = dn.count("◆")
                    existing_elec = dn.count("●")
                    existing_plas = dn.count("○")
                    existing_total = existing_cer * 25 + existing_elec * 5 + existing_plas
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
        siege_inductions = 0
        initiation_event_times: List[datetime] = []
        for rec in DATASTORE.iter_records():
            try:
                brother_ids = rec.get("brother_ids") or []
                if str(target.id) not in brother_ids:
                    continue
                if not bool(rec.get("initiation_trial")):
                    continue
                # Do not count a user's own initiation as a sanctioned induction
                if rec.get("initiate_id") == str(target.id):
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
                    siege_inductions += 1
                else:
                    ops_trials += 1
            except Exception:
                pass
        trials_reported = siege_inductions + (ops_trials // 3)

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
            if timestamps:
                timestamps.sort(reverse=True)
                now = datetime.utcnow()
                cutoff = now - timedelta(days=30)
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
        stat_rows = [
            ("Status", status),
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
            curr_len = sum(len(l) + 1 for l in r_lines)
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

        count = len(members)
        span_days = 7
        recent_records = _get_missions_last_days(span_days)
        member_ids = {
            str(getattr(m, "id", "")) for m in members if getattr(m, "id", None)
        }

        ops_count = 0
        aar_vals: List[float] = []
        gene_vals: List[float] = []
        armory_vals: List[float] = []
        waves_vals: List[float] = []  # siege-only
        per_capita_vals: List[float] = []
        ops_types: Counter = Counter()

        for rec in recent_records:
            try:
                bros = [str(b) for b in (rec.get("brother_ids") or [])]
                participants_in_team = sum(1 for b in bros if b in member_ids)
                if participants_in_team <= 0:
                    continue
                # Track mission types for top-N breakdown (e.g., Inferno, Decapitation)
                try:
                    mission_raw = rec.get("mission") or ""
                    # strip role mentions like <@&12345>
                    mission_clean = re.sub(r"<@&\d+>", "", mission_raw).strip()
                    # Use the first token or the whole cleaned string if single-word missions
                    mission_key = mission_clean.split()[0] if mission_clean else "Unknown"
                    ops_types[mission_key] += 1
                except Exception:
                    pass
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
                # Per-capita AAR for force multiplier
                try:
                    if participants_in_team > 0:
                        per_capita_vals.append(aar / float(participants_in_team))
                except Exception:
                    pass
            except Exception:
                pass

        def _mean(vals: List[float]) -> float:
            return (sum(vals) / len(vals)) if vals else 0.0

        avg_aar = _mean(aar_vals)
        avg_gene = _mean(gene_vals)
        avg_armory = _mean(armory_vals)
        avg_waves = _mean(waves_vals)
        # Reliability and Force Multiplier (single-team context)
        total_scores: List[float] = [
            a + g + r for a, g, r in zip(aar_vals, gene_vals, armory_vals)
        ]

        def _pstdev(vals: List[float]) -> float:
            return statistics.pstdev(vals) if len(vals) >= 2 else 0.0

        reliability = (
            (_mean(total_scores) / (1.0 + _pstdev(total_scores)))
            if total_scores
            else 0.0
        )
        force_multiplier = _mean(per_capita_vals)

        # Build top-3 operation type breakdown for Operational Tempo
        try:
            top_ops = ops_types.most_common(3)
            if top_ops:
                top_ops_str = ", ".join(f"{k} {v}" for k, v in top_ops)
            else:
                top_ops_str = ""
        except Exception:
            top_ops_str = ""

        # Format a compact ANSI-styled summary similar to individual tally output
        stat_rows_summary = [
            ("Window", f"Last {span_days} Days"),
            ("Kill Team", _extract_killteam_name(getattr(killteam, "name", "Unknown"))),
            ("Members", str(count)),
            ("Veteran Lethality Index", f"Avg AAR {avg_aar:.2f}"),
            (
                "Operational Tempo",
                f"Ops {int(ops_count)}" + (f" (top: {top_ops_str})" if top_ops_str else ""),
            ),
            ("Siegebreaker Rating", f"Avg Waves {avg_waves:.2f}"),
            ("Preservation — Gene", f"Avg {avg_gene:.2f}"),
            ("Preservation — Armory", f"Avg {avg_armory:.2f}"),
            ("Kill Team Reliability Index", f"Score {reliability:.2f}"),
            ("Force Multiplier Rating", f"Avg AAR/Member {force_multiplier:.2f}"),
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
            # Structured summary embed with concise inline fields
            embed = discord.Embed(
                title="Kill Team Summary",
                description=f"{_extract_killteam_name(getattr(killteam, 'name', 'Unknown'))} — Last {span_days} Days",
                color=0x2ECC71,
            )
            for label, value in stat_rows_summary:
                embed.add_field(name=label, value=value, inline=True)
            view = ToggleFormatView(
                text_content=summary_text, embed=embed, default="ansi"
            )
            await interaction.followup.send(
                content=summary_text, embed=None, view=view, ephemeral=True
            )
        except Exception:
            # ignore send errors and proceed to attach full file
            try:
                embed = _embed_from_ansi("Kill Team Summary", summary_text)
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
                name_val = roster_items[0].get("name") if roster_items else "Deeds Ledger"
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
    # No defer: send a direct response to clear the interaction state

    # Default to last 30 days; if provided, interpret `window` as days
    span_days = window if (isinstance(window, int) and window > 0) else 30
    missions = _get_missions_last_days(span_days)
    # Collect all brothers seen in window
    all_bros: List[str] = []
    for rec in missions:
        all_bros.extend([str(b) for b in (rec.get("brother_ids") or [])])
    all_bros = sorted(set(all_bros))

    pair_counts = _build_pair_counts(missions)
    triples = _build_triple_bonds(pair_counts, all_bros)
    # Active members in the window: those who appeared in at least one AAR
    active_count = len(all_bros)
    spreads = _build_spread_counts(pair_counts, active_count=active_count)

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
        await interaction.response.send_message(content=text, view=view, ephemeral=True)
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
        await interaction.response.send_message(content=text, view=view, ephemeral=True)


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
    # Initiation Trial (legacy boolean) and initiate id
    initiation_trial = False
    initiate_id = None

    brothers_start_idx = None

    for i, raw_line in enumerate(lines):
        line = raw_line.strip()
        lower = line.lower()

        if lower.startswith("mission:"):
            mission = line.split(":", 1)[1].strip()
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

        # Detect Trial: lines (e.g. 'Trial: 1/1' or 'Trial: -/3') and try to extract an initiate
        if lower.startswith("trial:"):
            # Mark legacy flag and try to find an initiate mention on the same or next few lines
            initiation_trial = True
            ids_here = get_user_ids_in_line(raw_line, message)
            if ids_here:
                initiate_id = ids_here[0]
            else:
                for j in range(i + 1, min(i + 4, len(lines))):
                    look_line = lines[j].strip()
                    if not look_line:
                        continue
                    ids_here = get_user_ids_in_line(look_line, message)
                    if ids_here and len(ids_here) == 1:
                        initiate_id = ids_here[0]
                        break

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
        "initiate_id": initiate_id,
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

    # 6) Initiation Trial placement rules (simplified)
    if record.get("initiation_trial"):
        if not record.get("initiate_id"):
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
    Rule: Siege initiation counts immediately as one induction.
          Operation initiation requires three trials to count as one induction.
    """
    try:
        data = load_aar_data(AAR_RECORDS_PATH)
    except Exception:
        data = {}
    ops_trials = 0
    siege_inductions = 0
    for rec in data.values():
        try:
            brother_ids = rec.get("brother_ids") or []
            if str(user_id) not in brother_ids:
                continue
            if not bool(rec.get("initiation_trial")):
                continue
            # Exclude records where the user is the initiate (their own induction)
            if rec.get("initiate_id") == str(user_id):
                continue
            dclass = (rec.get("difficulty_class") or "").lower()
            if "siege" in dclass:
                siege_inductions += 1
            else:
                ops_trials += 1
        except Exception:
            # Be resilient to malformed records
            pass
    return int(siege_inductions + (ops_trials // 3))


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
        c = [int(pair_counts.get(p, 0) or 0) for p in pairs]
        c_ab, c_ac, c_bc = c
        # Eligibility: all pairs must meet minimum count
        if (c_ab < min_pair) or (c_ac < min_pair) or (c_bc < min_pair):
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
        denom = (1.0 / float(c_ab)) + (1.0 / float(c_ac)) + (1.0 / float(c_bc))
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
    """Resolve home chapters for given users by scanning the '◈⋅⋅record-of-blood⋅⋅◈' channel.
    Logic: find a message that mentions the user; detect the chapter within that same message's content.
    The chapter is detected by matching any of the known `home_chapters` names within the message.
    Returns mapping of user_id -> chapter string. Missing entries map to 'REDACTED'.
    """
    # Use module-level HOME_CHAPTERS for canonical chapter names
    home_chapters = HOME_CHAPTERS
    chapters: Dict[str, str] = {}
    if not guild:
        return chapters
    channel = discord.utils.get(guild.channels, name="❖⋅⋅record-of-blood⋅⋅❖")
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
    lines.append("  SUB-ROUTINE: TRIADIC BATTLE-LITANY INDEX")
    lines.append(
        "=============================================================================="
    )
    if window_days is not None:
        lines.append(f"  Auspex Window: Last {window_days} day(s)")
    else:
        lines.append(f"  Auspex Window: Last {window_span} sanctioned engagement(s)")
    rank = 1
    scores_for_cutoffs = [score for _tri, score in bonds]
    cutoffs = _compute_bond_cutoffs(scores_for_cutoffs)
    ordinal_labels = {
        1: "PRIMARY",
        2: "SECONDARY",
        3: "TERTIARY",
        4: "QUATERNARY",
        5: "QUINARY",
    }
    for triple, score in bonds:
        tier = _bond_tier_dynamic(score, cutoffs)
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
        title="Combat Bonds — Triadic Battle-Litany",
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
        a, b, c = triple
        name = f"{ordinal_labels.get(rank, 'BOND')} — {tier}"
        value = f"• {_member_label(a)}\n• {_member_label(b)}\n• {_member_label(c)}"
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
    ):
        # Extend lifetime to reduce 'Interaction failed' after short delays
        super().__init__(timeout=900)
        self.text_content = text_content or ""
        self.embed_obj = embed
        self.current = default if default in ("ansi", "embed") else "ansi"
        # Soft safety margin for Discord's 2000-char content limit
        self._ansi_max_len = 1900

        # Initialize button states based on available formats
        self._update_buttons()

    def _update_buttons(self):
        # Ensure children exist before setting states (created by decorators)
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "show_ansi":
                    too_long = len(self.text_content) > self._ansi_max_len
                    child.disabled = (
                        (self.current == "ansi") or (not self.text_content) or too_long
                    )
                elif child.custom_id == "show_embed":
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
            # Graceful fallback: keep embed and notify
            note = "PC/Console view exceeds message limit; showing Mobile view instead."
            try:
                await interaction.response.send_message(note, ephemeral=True)
            except Exception:
                try:
                    await interaction.response.defer()
                except Exception:
                    pass
            return
        self.current = "ansi"
        self._update_buttons()
        try:
            await interaction.response.edit_message(
                content=self.text_content, embed=None, view=self
            )
        except Exception:
            # Fallback notify if edit fails (e.g., stale interaction)
            try:
                await interaction.followup.send(
                    "Unable to switch to PC/Console view.", ephemeral=True
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


BATTLE_LINE_ORDER = [
    "Watch Brother",
    "Watch Veteran",
    "Watch Knight",
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

if __name__ == "__main__":
    _main()
