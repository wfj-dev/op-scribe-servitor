"""Roster operations: activity status, promotion milestones, deeds/stats,
combat bonds, milestone announcements, roster audit."""

import os
import asyncio
import json
import discord
from discord import app_commands
from datetime import datetime, timedelta, timezone
from discord.ext import tasks
import re
import itertools
from typing import Dict, List, Set, Tuple, Optional
import logging
import random
import sys as _sys
import statistics

from .constants import *  # noqa: F401,F403
from .constants import _strip_display_name
from .flavor_text import *  # noqa: F401,F403
from .permissions import *  # noqa: F401,F403
from .studs import *  # noqa: F401,F403
from .librarius_ops import _get_warp_exposure_state, _get_warp_sanction_status
from . import _bot_globals as _g


def _b(name):
    """Resolve name via bot module for test-mock compatibility."""
    m = _sys.modules.get("opscribe.bot") or _sys.modules.get("bot")
    return getattr(m, name) if (m is not None and hasattr(m, name)) else globals().get(name)


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
        _g.logger.debug(f"Failed to load member last post times: {e}")
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
        _g.logger.debug(f"Failed to save member last post times: {e}")


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
        _g.logger.debug(f"Failed to load activity status last check: {e}")
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
        _g.logger.exception(f"Failed to save activity status: {e}")


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
        _g.logger.exception(f"Failed to save promotion tracking: {e}")


def _load_award_queue() -> List[Dict]:
    """Load the pending award announcement queue."""
    try:
        if os.path.exists(AWARD_QUEUE_PATH):
            with open(AWARD_QUEUE_PATH, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
    except Exception:
        pass
    return []


def _save_award_queue(queue: List[Dict]):
    """Persist the award announcement queue to disk."""
    try:
        tmp_path = AWARD_QUEUE_PATH + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(queue, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, AWARD_QUEUE_PATH)
    except Exception as e:
        _g.logger.warning(f"Failed to save award queue: {e}")


def _enqueue_award_announcement(
    member_id: str,
    award_type: str,
    member_chapter: str,
    channel_id: str,
    guild_id: str,
):
    """Append one award announcement to the persistent queue."""
    queue = _load_award_queue()
    queue.append({
        "member_id": member_id,
        "award_type": award_type,
        "member_chapter": member_chapter,
        "channel_id": channel_id,
        "guild_id": guild_id,
        "queued_at": datetime.utcnow().isoformat(),
    })
    _save_award_queue(queue)


def _load_induction_overrides() -> Dict[str, str]:
    """Load induction date overrides: user_id -> ISO date string (YYYY-MM-DD)."""
    try:
        if os.path.exists(INDUCTION_OVERRIDES_PATH):
            with open(INDUCTION_OVERRIDES_PATH, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_induction_overrides(overrides: Dict[str, str]):
    """Persist induction overrides to disk with backup."""
    try:
        tmp_path = INDUCTION_OVERRIDES_PATH + ".tmp"
        bak_path = INDUCTION_OVERRIDES_PATH + ".bak"
        with open(tmp_path, "w") as f:
            json.dump(overrides, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(INDUCTION_OVERRIDES_PATH):
            try:
                os.replace(INDUCTION_OVERRIDES_PATH, bak_path)
            except Exception:
                pass
        os.replace(tmp_path, INDUCTION_OVERRIDES_PATH)
    except Exception as e:
        _g.logger.exception(f"Failed to save induction overrides: {e}")


def _get_effective_induction_date(member: discord.Member) -> Optional[datetime]:
    """Return the effective induction date for a member.

    If an override exists, returns that date (as datetime at midnight UTC).
    Otherwise, returns the member's Discord server join date.
    """
    user_id = str(getattr(member, "id", ""))
    overrides = _load_induction_overrides()
    if user_id in overrides:
        try:
            # Parse ISO date string to datetime
            date_str = overrides[user_id]
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    # Fallback to Discord join date
    joined_at = getattr(member, "joined_at", None)
    return joined_at


def _get_member_company_name(member: discord.Member) -> Optional[str]:
    """Return the Watch Company name for a member (e.g., 'Watch Company Primus'), or None."""
    # Check for Dreadnought Cadre first
    try:
        for r in getattr(member, "roles", []) or []:
            if getattr(r, "id", 0) == DREADNOUGHT_CADRE_ROLE_ID:
                return "Dreadnought Cadre"
    except Exception:
        pass

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


# All Watch Company role names. Order matches numerical seniority.
_WATCH_COMPANY_ROLE_NAMES: List[str] = [
    "Watch Company Primus",
    "Watch Company Secundus",
    "Watch Company Tertius",
    "Watch Company Quartus",
    "Watch Company Quintus",
]


def _orphan_companies_for_role(guild: Optional[discord.Guild], specialist_role: str) -> set:
    """Return the set of Watch Company role names that have no active member with ``specialist_role``.

    A company is "covered" when at least one non-bot member has both ``specialist_role``
    AND that company role. Used by /armor_status and /warp_status gap-filling so a
    specialist whose home company is clear backfills coverage on companies without
    a counterpart specialist before reaching into peer territory.
    """
    companies = set(_WATCH_COMPANY_ROLE_NAMES)
    if guild is None:
        return companies
    covered: set = set()
    try:
        for member in guild.members:
            if getattr(member, "bot", False):
                continue
            role_names = {(getattr(r, "name", "") or "").strip() for r in (getattr(member, "roles", []) or [])}
            if specialist_role not in role_names:
                continue
            for c in companies:
                if c in role_names:
                    covered.add(c)
    except Exception:
        pass
    return companies - covered


def _company_scope_ring(
    member_company: Optional[str],
    caller_company: Optional[str],
    orphan_companies: set,
) -> int:
    """Return ring rank for a candidate brother: lower fills first.

    0 — caller's own company (primary responsibility)
    1 — orphan company (no counterpart specialist assigned)
    2 — other companies (peer-covered territory, lowest priority)
    3 — no company assignment
    """
    if not member_company:
        return 3
    if caller_company and member_company == caller_company:
        return 0
    if member_company in orphan_companies:
        return 1
    return 2


def _is_active_participant(member: Optional[discord.Member]) -> bool:
    """Return True if ``member`` should participate in armor/warp systems.

    A member counts as an active participant when they:
      - Hold at least one Watch rank role (anything in RANK_HONORIFICS), AND
      - Are not in Reserves, AND
      - Are not Interred (sarcophagus inactive — Honored/Venerable Dreadnoughts
        remain active until interred).

    Bots and members without a Watch rank are excluded. Used as the single
    authority for "should this member's AAR record drive armor damage / warp
    exposure / appear in /armor_status / /warp_status?" — keeps both systems
    in sync on inactive vs. participant status.
    """
    if member is None or getattr(member, "bot", False):
        return False
    roles = getattr(member, "roles", []) or []
    role_names = {(getattr(r, "name", "") or "").strip() for r in roles}
    role_ids = {getattr(r, "id", 0) for r in roles}
    # Must have at least one ranked role
    if not (role_names & set(RANK_HONORIFICS.keys())):
        return False
    # Excluded: Reserves
    if RESERVES_ROLE_ID in role_ids or "reserves" in {n.lower() for n in role_names}:
        return False
    # Excluded: Interred Brother (inactive sarcophagus)
    if INTERRED_BROTHER_ROLE_ID in role_ids or "Interred Brother" in role_names:
        return False
    return True


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
        idx = _b("_role_index")(name)
        if idx is not None:
            if best_idx is None or idx < best_idx:
                best_idx = idx
                best_role = role
    return best_role


async def _handle_dreadnought_inactivity(member: discord.Member):
    """Handle dreadnought interment when they become inactive (28 days no AAR).

    Removes Venerable/Honored Dreadnought role and adds Interred Brother role.
    Sends a notification about the interment.
    """
    try:
        guild = member.guild
        if not guild:
            return

        role_ids = {getattr(r, "id", 0) for r in getattr(member, "roles", [])}

        # Check if member has a dreadnought role
        dreadnought_role_to_remove = None
        dreadnought_type = None

        if VENERABLE_DREADNOUGHT_ROLE_ID in role_ids:
            dreadnought_role_to_remove = guild.get_role(VENERABLE_DREADNOUGHT_ROLE_ID)
            dreadnought_type = "Venerable Dreadnought"
        elif HONORED_DREADNOUGHT_ROLE_ID in role_ids:
            dreadnought_role_to_remove = guild.get_role(HONORED_DREADNOUGHT_ROLE_ID)
            dreadnought_type = "Honored Dreadnought"

        # If member has a dreadnought role, inter them
        if dreadnought_role_to_remove and dreadnought_type:
            interred_role = guild.get_role(INTERRED_BROTHER_ROLE_ID)

            if not interred_role:
                _g.logger.warning(f"Interred Brother role {INTERRED_BROTHER_ROLE_ID} not found")
                return

            # Remove dreadnought role and add interred brother role
            await member.remove_roles(dreadnought_role_to_remove, reason="Dreadnought inactive for 28 days")
            await member.add_roles(interred_role, reason="Dreadnought interred due to inactivity")

            # Send notification
            channel = guild.get_channel(DREADNOUGHT_INACTIVITY_CHANNEL_ID)
            if channel:
                member_name = _get_member_display_name(member)

                # Get Watch Master and Forgemaster for notification
                watch_master_role = discord.utils.get(guild.roles, name="Watch Master")
                forgemaster_role = discord.utils.get(guild.roles, name="Forgemaster")

                role_mentions = []
                if watch_master_role:
                    role_mentions.append(watch_master_role.mention)
                if forgemaster_role:
                    role_mentions.append(forgemaster_role.mention)

                mention_str = " ".join(role_mentions) if role_mentions else ""

                # Get venerable emoji
                venerable_emoji = _b("_get_emoji_by_name")(guild, "venerable") or "⚙️"

                lines = [
                    f"{venerable_emoji} **INTERMENT PROTOCOL INITIATED** {venerable_emoji}",
                    "",
                    f"᛭⋅ {dreadnought_type}: **{member_name}** {member.mention}",
                    "᛭⋅ Status: **Interred** (28 days inactive)",
                    "᛭⋅ Sarcophagus sealed and preserved in stasis",
                    "᛭⋅ The machine-spirit awaits the call to war",
                    "",
                    "*May the Omnissiah watch over this ancient warrior's slumber.*",
                ]

                if mention_str:
                    content = f"{mention_str}\n" + "\n".join(lines)
                else:
                    content = "\n".join(lines)

                await channel.send(content)
                _g.logger.info(f"Interred {dreadnought_type} {member.id} due to inactivity")

    except Exception as e:
        _g.logger.exception(f"Failed to handle dreadnought inactivity: {e}")


async def _send_activity_status_notification(
    guild: discord.Guild,
    member: discord.Member,
    old_status: str,
    new_status: str,
):
    """Send a notification to the activity status channel when a member's status changes."""
    try:
        # Skip notifications for members who aren't Watch Brothers (don't have any Watch rank role)
        member_role_names = {r.name for r in member.roles}
        qualifying_roles = set(_b("RANK_ROLES_PRIORITY")) | {"Watch Sister"}
        if not any(r in member_role_names for r in qualifying_roles):
            _g.logger.debug(f"Skipping activity notification for {member.name}: not a Watch Brother")
            return

        channel = guild.get_channel(ACTIVITY_STATUS_CHANNEL_ID)
        if not channel:
            try:
                channel = await _g.bot.fetch_channel(ACTIVITY_STATUS_CHANNEL_ID)
            except Exception:
                _g.logger.warning(f"Activity status channel {ACTIVITY_STATUS_CHANNEL_ID} not found")
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
            company_role = discord.utils.get(guild.roles, name=company_name) if company_name else None
            company_mention = company_role.mention if company_role else (company_name or "Unknown")

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
        _g.logger.info(f"Activity status notification sent for {member_name}: {old_status} -> {new_status}")

    except Exception as e:
        _g.logger.exception(f"Failed to send activity status notification: {e}")


async def _check_activity_status_changes():
    """Check guild members for activity status changes with optimized scanning.

    First run: Scans all records to build baseline of member last-post times.
    Subsequent runs: Only scans recent records + checks 28-day threshold against saved times.
    """
    async with _g.ACTIVITY_STATUS_LOCK:
        try:
            guild = _b("_resolve_notification_guild")()
            if not guild:
                _g.logger.debug("Activity status check: no guild available")
                return

            if _g.DATASTORE is None:
                _g.logger.debug("Activity status check: _g.DATASTORE not initialized")
                return

            # Load previous status and member last post times
            prev_status = _load_activity_status()
            member_last_posts = _load_member_last_post_times()  # Dict[user_id] -> ISO timestamp string
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
                _g.logger.info("Activity status check: first run, building baseline of member last posts")
                for rec in _g.DATASTORE.iter_records():
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
                            if uid_str not in new_member_last_posts or ts > new_member_last_posts[uid_str]:
                                new_member_last_posts[uid_str] = ts
                    except Exception:
                        continue
                member_last_posts = new_member_last_posts
            else:
                # Subsequent runs: scan only recent records and update timestamps
                _g.logger.debug(
                    f"Activity status check: scanning records since {last_check_time.isoformat() if last_check_time else 'beginning'}"
                )
                recent_cutoff = last_check_time or (check_start_time - timedelta(days=365))

                for rec in _g.DATASTORE.iter_records():
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
                                if uid_str not in member_last_posts or ts > member_last_posts.get(uid_str, ""):
                                    member_last_posts[uid_str] = ts
                    except Exception:
                        continue

            # Step 2: Determine which members to check and compute their status
            cutoff_datetime = check_start_time - timedelta(days=cutoff_days)
            users_to_check: Set[str] = set(member_last_posts.keys()) if is_first_check else set()

            # Add members who had recent activity
            for uid, last_post_str in member_last_posts.items():
                try:
                    last_post_dt = datetime.fromisoformat(last_post_str)
                    if last_post_dt.tzinfo is not None:
                        last_post_dt = last_post_dt.astimezone(timezone.utc).replace(tzinfo=None)
                    # Check if record is recent (within 4 hours) or if member was previously active and is now at/past 28 days
                    is_recent = last_post_dt >= recent_cutoff if not is_first_check else False
                    is_at_threshold = last_post_dt < cutoff_datetime  # At or past 28-day threshold

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

            _g.logger.debug(f"Activity status check: checking {len(users_to_check)} members")

            # Step 3: Compute status for identified members
            for user_id in users_to_check:
                try:
                    last_post_str = member_last_posts.get(user_id)
                    if last_post_str:
                        last_post_dt = datetime.fromisoformat(last_post_str)
                        if last_post_dt.tzinfo is not None:
                            last_post_dt = last_post_dt.astimezone(timezone.utc).replace(tzinfo=None)
                        # Status is active if last post is within 28 days, else inactive
                        current_status = "active" if last_post_dt >= cutoff_datetime else "inactive"
                    else:
                        current_status = "inactive"

                    # Guard against false-inactive transitions caused by a stale member_last_posts
                    # cache. The "subsequent runs" scan only looks at records newer than
                    # last_check_time; if an AAR was re-ingested or processed late with an old
                    # Discord timestamp it will be skipped, leaving member_last_posts stale.
                    # Before triggering an active->inactive notification, do a full scan of all
                    # datastore records for this member to confirm the true last-post time.
                    if current_status == "inactive" and _g.DATASTORE is not None:
                        try:
                            uid_int = int(user_id)
                            true_last_post: Optional[str] = None
                            for _rec in _g.DATASTORE.iter_records():
                                _rec_brothers = _rec.get("brother_ids") or []
                                if uid_int in _rec_brothers or user_id in _rec_brothers:
                                    _ts = _rec.get("timestamp")
                                    if _ts and (true_last_post is None or _ts > true_last_post):
                                        true_last_post = _ts
                                        # Early exit: already found a record within 28 days
                                        try:
                                            _early_dt = datetime.fromisoformat(_ts)
                                            if _early_dt.tzinfo is not None:
                                                _early_dt = _early_dt.astimezone(timezone.utc).replace(tzinfo=None)
                                            if _early_dt >= cutoff_datetime:
                                                break
                                        except Exception:
                                            pass
                            if true_last_post:
                                _tp_dt = datetime.fromisoformat(true_last_post)
                                if _tp_dt.tzinfo is not None:
                                    _tp_dt = _tp_dt.astimezone(timezone.utc).replace(tzinfo=None)
                                if _tp_dt >= cutoff_datetime:
                                    # Member is actually active; update cache and correct status
                                    member_last_posts[user_id] = true_last_post
                                    current_status = "active"
                        except (ValueError, TypeError) as _e:
                            _g.logger.debug(f"Activity status full-scan error for {user_id}: {_e}")
                        except Exception as _e:
                            _g.logger.debug(f"Activity status full-scan unexpected error for {user_id}: {_e}")

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
                    if not is_first_check and old_status and old_status != current_status:
                        # Always notify on status changes (both departures and returns)
                        should_notify = True

                    if should_notify:
                        # Status changed; find member in guild
                        try:
                            member = guild.get_member(int(user_id))
                            if not member:
                                member = await guild.fetch_member(int(user_id))
                            if member and not member.bot:
                                changes.append((member, old_status, current_status, user_id))
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

            # Handle Dreadnought inactivity: if a dreadnought becomes inactive, inter them
            for member, old, new, uid in changes:
                if new == "inactive":
                    try:
                        await _handle_dreadnought_inactivity(member)
                    except Exception as e:
                        _g.logger.exception(f"Failed to handle dreadnought inactivity for {member.id}: {e}")

            # Send notifications for changes; mark notified_inactive only on confirmed delivery
            for member, old, new, uid in changes:
                try:
                    await _send_activity_status_notification(guild, member, old, new)
                    if new == "inactive" and uid in new_status_map:
                        new_status_map[uid]["notified_inactive"] = True
                    await asyncio.sleep(0.5)
                except Exception as e:
                    _g.logger.exception(f"Failed to notify activity change for {member.id}: {e}")

            # Save updated activity status (after notifications so notified_inactive reflects actual sends)
            _save_activity_status(new_status_map)

            if changes:
                _g.logger.info(
                    f"Activity status check complete: {len(changes)} change(s), {len(users_to_check)} members checked"
                )
            else:
                _g.logger.debug(f"Activity status check complete: no changes ({len(users_to_check)} members checked)")

        except Exception as e:
            _g.logger.exception(f"Activity status check failed: {e}")


async def _check_award_milestones_for_members(member_ids: List[str], guild: discord.Guild) -> None:
    """Check award eligibility immediately for specific members after AAR ingestion.

    Runs Watch Veteran / Ardent Raider / Apothecarion Medal / Crimson Laurels checks
    for only the given member IDs. Black Laurels, Service Studs, and Oathsworn are
    omitted here — the 4-hourly loop handles those.
    """
    if not member_ids or _g.DATASTORE is None:
        return

    watch_veteran_role = guild.get_role(WATCH_VETERAN_ROLE_ID)
    ardent_raider_role = guild.get_role(ARDENT_RAIDER_ROLE_ID)
    apothecarion_medal_role = guild.get_role(APOTHECARION_SERVICE_MEDAL_ROLE_ID)
    crimson_laurels_role = guild.get_role(CRIMSON_LAURELS_ROLE_ID)
    black_laurels_role = discord.utils.get(guild.roles, name="Black Laurels")

    tracking = _load_promotion_tracking()
    notifications_sent = 0

    for uid_str in member_ids:
        try:
            if not str(uid_str).isdigit():
                continue
            member = guild.get_member(int(uid_str))
            if not member or member.bot:
                continue

            role_names = {getattr(r, "name", "") for r in getattr(member, "roles", [])}
            is_watch_brother = "Watch Brother" in role_names or "Watch Sister" in role_names
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
                    "Venerable Dreadnought",
                    "Honored Dreadnought",
                    "Forgemaster",
                    "Void Warden",
                    "High Chaplain",
                    "Chief Apothecary",
                    "Castellan",
                    "Lord Executioner",
                    "Huntmaster",
                    "Watch Master",
                ]
            )
            is_watch_brother_only = is_watch_brother and not is_veteran_or_higher

            if not (is_watch_brother_only or is_veteran_or_higher):
                continue

            user_tracking = tracking.get(uid_str, {})
            stats = compute_stats_for_user(uid_str)
            aar_points = int(stats.get("aar_points", 0) or 0)

            member_chapter = "Unknown"
            for role in getattr(member, "roles", []):
                if getattr(role, "name", "") in _b("HOME_CHAPTERS"):
                    member_chapter = role.name
                    break

            ann_channel: Optional[discord.abc.Messageable] = None
            ann_channel_fetched = False

            async def _get_ann_channel() -> Optional[discord.abc.Messageable]:
                nonlocal ann_channel, ann_channel_fetched
                if not ann_channel_fetched:
                    ann_channel = await _b("_get_award_announcement_channel")(member, guild)
                    ann_channel_fetched = True
                return ann_channel

            # Watch Veteran (Watch Brother only, 200 AAR + 2 weeks)
            if is_watch_brother_only and watch_veteran_role:
                joined_at = _get_effective_induction_date(member)
                if joined_at:
                    if joined_at.tzinfo is not None:
                        joined_at = joined_at.replace(tzinfo=None)
                    weeks_in_server = max(0, (datetime.utcnow() - joined_at).days // 7)
                else:
                    weeks_in_server = 0
                if (
                    aar_points >= 200
                    and weeks_in_server >= 2
                    and watch_veteran_role not in member.roles
                    and not user_tracking.get("veteran_assigned")
                ):
                    assigned = False
                    try:
                        await member.add_roles(watch_veteran_role, reason="Auto-promotion: 200 AAR + 2 weeks")
                        assigned = True
                    except Exception as e:
                        _g.logger.warning(f"Failed to assign Watch Veteran role to {member.id}: {e}")
                    if assigned:
                        ch = await _get_ann_channel()
                        if ch:
                            _enqueue_award_announcement(
                                str(member.id), "watch_veteran", member_chapter, str(ch.id), str(guild.id)
                            )
                            notifications_sent += 1
                        else:
                            _g.logger.warning(
                                f"Watch Veteran announcement channel not found for {member.id}; role assigned but no announcement sent"
                            )
                        user_tracking["veteran_assigned"] = True

            # Ardent Raider (200 armory points)
            if ardent_raider_role:
                armory_points = int(stats.get("armory_points", 0) or 0)
                if ardent_raider_role in member.roles:
                    user_tracking["ardent_raider_notified"] = True
                elif armory_points >= ARDENT_RAIDER_ARMORY_POINTS_THRESHOLD:
                    assigned = False
                    try:
                        await member.add_roles(ardent_raider_role, reason="Auto-award: 200 armory points")
                        assigned = True
                    except Exception as e:
                        _g.logger.warning(f"Failed to assign Ardent Raider role to {member.id}: {e}")
                    if assigned:
                        if not user_tracking.get("ardent_raider_notified"):
                            ch = await _get_ann_channel()
                            if ch:
                                _enqueue_award_announcement(
                                    str(member.id), "ardent_raider", member_chapter, str(ch.id), str(guild.id)
                                )
                                notifications_sent += 1
                            else:
                                _g.logger.warning(
                                    f"Ardent Raider announcement channel not found for {member.id}; role assigned but no announcement sent"
                                )
                        user_tracking["ardent_raider_notified"] = True

            # Apothecarion Medal (150 gene-seed points)
            if apothecarion_medal_role:
                gene_seed_points = int(stats.get("gene_seed_points", 0) or 0)
                if apothecarion_medal_role in member.roles:
                    user_tracking["for_the_fallen_notified"] = True
                elif gene_seed_points >= FOR_THE_FALLEN_GENESEED_POINTS_THRESHOLD:
                    assigned = False
                    try:
                        await member.add_roles(apothecarion_medal_role, reason="Auto-award: 150 geneseed points")
                        assigned = True
                    except Exception as e:
                        _g.logger.warning(f"Failed to assign Apothecarion Medal role to {member.id}: {e}")
                    if assigned:
                        if not user_tracking.get("for_the_fallen_notified"):
                            ch = await _get_ann_channel()
                            if ch:
                                _enqueue_award_announcement(
                                    str(member.id), "apothecarion_medal", member_chapter, str(ch.id), str(guild.id)
                                )
                                notifications_sent += 1
                            else:
                                _g.logger.warning(
                                    f"Apothecarion Medal announcement channel not found for {member.id}; role assigned but no announcement sent"
                                )
                        user_tracking["for_the_fallen_notified"] = True

            # Crimson Laurels (1000 AAR points + Black Laurels role)
            if crimson_laurels_role:
                has_bl_for_cl = black_laurels_role and black_laurels_role in member.roles
                if crimson_laurels_role in member.roles:
                    user_tracking["crimson_laurels_notified"] = True
                elif (
                    aar_points >= CRIMSON_LAURELS_AAR_POINTS_THRESHOLD
                    and has_bl_for_cl
                ):
                    assigned = False
                    try:
                        await member.add_roles(crimson_laurels_role, reason="Auto-award: 1000 AAR + Black Laurels")
                        assigned = True
                    except Exception as e:
                        _g.logger.warning(f"Failed to assign Crimson Laurels role to {member.id}: {e}")
                    if assigned:
                        if not user_tracking.get("crimson_laurels_notified"):
                            ch = await _get_ann_channel()
                            if ch:
                                _enqueue_award_announcement(
                                    str(member.id), "crimson_laurels", member_chapter, str(ch.id), str(guild.id)
                                )
                                notifications_sent += 1
                            else:
                                _g.logger.warning(
                                    f"Crimson Laurels announcement channel not found for {member.id}; role assigned but no announcement sent"
                                )
                        user_tracking["crimson_laurels_notified"] = True

            if user_tracking:
                tracking[uid_str] = user_tracking

        except Exception as e:
            _g.logger.debug(f"Award check failed for member {uid_str}: {e}")
            continue

    async with _g.PROMOTION_TRACKING_LOCK:
        fresh = _load_promotion_tracking()
        for uid, data in tracking.items():
            fresh.setdefault(uid, {}).update(data)
        _save_promotion_tracking(fresh)

    if notifications_sent > 0:
        _g.logger.info(
            f"Award check (AAR-triggered) for {len(member_ids)} member(s): {notifications_sent} announcement(s) queued"
        )


async def _enforce_challenge_grace_periods(
    guild: discord.Guild,
    user_bl_missions: Dict[str, set],
    tracking: dict,
) -> int:
    """Revoke challenge awards once their grace-period deadline has passed and
    the holder no longer meets current requirements.

    Config key ``challenge_grace_periods`` maps challenge name → deadline date string
    ``"YYYY-MM-DD"``.  Once today >= deadline, every role holder who does not satisfy
    the current requirements has their role removed, the completion flag cleared so
    they can re-earn automatically, and a notification embed posted to general chat.

    Supported challenge keys:
      black_laurels   — BL mission set (pre-computed user_bl_missions)
      dual_vigil      — Dual Vigil mission set (challenge_progress.json)
      order_omega     — Order Omega mission set (challenge_progress.json)
      crux_terminatus — Crux Terminatus (role-based + BL Rank A audit)

    Args:
        guild: The Discord guild to operate on.
        user_bl_missions: ``{user_id_str: set_of_mission_names}`` pre-computed for BL check.
        tracking: The promotion-tracking dict; BL flag changes written in-place.

    Returns:
        Total number of roles revoked.
    """
    grace_cfg = ((_g.CONFIG or {}).get("challenge_grace_periods") or {})
    if not grace_cfg:
        return 0

    today = datetime.utcnow().date()
    general_channel = guild.get_channel(MILESTONES_CHANNEL_ID)

    async def _notify_revoked(member: discord.Member, role_name: str, reasons: list[str]) -> None:
        """Post a role-revoked notification embed to general chat."""
        if not general_channel:
            return
        try:
            reason_text = "\n".join(f"\u2022 {r}" for r in reasons)
            embed = discord.Embed(
                title=f"{role_name} \u2014 Role Revoked",
                description=(
                    f"{member.mention}, your **{role_name}** has been revoked.\n\n"
                    f"You no longer meet the following requirements:\n{reason_text}\n\n"
                    "Complete the missing requirements to have it reinstated."
                ),
                colour=discord.Colour.red(),
            )
            await general_channel.send(embed=embed)
        except Exception as exc:
            _g.logger.warning(f"Grace period: failed to send revocation notification for {member.id}: {exc}")

    # Maps challenge config key → (role_id, required_missions, data_source, notified_key)
    # data_source "bl"   → qualification data from user_bl_missions (BL AAR scan)
    # data_source "cp"   → qualification data from challenge_progress.json
    # data_source "crux" → custom validator (role-based + BL Rank A audit)
    CHALLENGE_META: Dict[str, tuple] = {
        "black_laurels": (
            BLACK_LAURELS_ROLE_ID,
            BLACK_LAURELS_REQUIRED_MISSIONS,
            "bl",
            "black_laurels_notified",  # bool key in promotion_tracking
        ),
        "dual_vigil": (
            DUAL_VIGIL_AWARD_ROLE_ID,
            DUAL_VIGIL_REQUIRED_MISSIONS,
            "cp",
            "dual_vigil",  # entry in challenge_progress[uid]["notified"] list
        ),
        "order_omega": (
            THE_ORDER_OMEGA_ROLE_ID,
            ORDER_OMEGA_REQUIRED_MISSIONS,
            "cp",
            "order_omega",  # entry in challenge_progress[uid]["notified"] list
        ),
        "crux_terminatus": (
            CRUX_TERMINATUS_ROLE_ID,
            None,   # requirements are role/AAR-based, not a mission set
            "crux",
            "crux_terminatus",  # entry in challenge_progress[uid]["notified"] list
        ),
    }

    # Partition live deadlines by data source
    bl_pending: list = []    # (role_id, required, notified_key)
    cp_pending: list = []    # (challenge_name, role_id, required, notified_key)
    crux_pending: bool = False

    for challenge_name, deadline_str in grace_cfg.items():
        try:
            deadline = datetime.strptime(deadline_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            _g.logger.warning(
                f"Grace period: invalid deadline '{deadline_str}' for '{challenge_name}'"
            )
            continue

        if today < deadline:
            continue  # still inside the grace window

        meta = CHALLENGE_META.get(challenge_name)
        if meta is None:
            _g.logger.warning(
                f"Grace period: unknown challenge '{challenge_name}', skipping"
            )
            continue

        role_id, required, source, notified_key = meta
        if source == "bl":
            bl_pending.append((role_id, required, notified_key))
        elif source == "crux":
            crux_pending = True
        else:
            cp_pending.append((challenge_name, role_id, required, notified_key))

    total_revoked = 0

    # ── Black Laurels (uses pre-computed user_bl_missions) ──────────────────
    for role_id, required, notified_key in bl_pending:
        role = discord.utils.get(guild.roles, id=role_id)
        if role is None:
            _g.logger.warning(f"Grace period: role {role_id} not found in guild")
            continue
        for member in guild.members:
            if member.bot or role not in member.roles:
                continue
            uid = str(member.id)
            completed = user_bl_missions.get(uid, set())
            if completed >= required:
                continue  # member already has all missions
            missing = sorted(required - completed)
            try:
                await member.remove_roles(role, reason="Grace period expired: new mission required")
                total_revoked += 1
                tracking.setdefault(uid, {})[notified_key] = False
                _g.logger.info(f"Grace period: revoked {role.name} from {uid} ({member.display_name})")
                await _notify_revoked(
                    member, role.name,
                    [f"Complete all required missions. Missing: {', '.join(missing)}"]
                )
            except Exception as exc:
                _g.logger.warning(f"Grace period: failed to revoke {role.name} from {uid}: {exc}")

    # ── challenge_progress.json challenges (Dual Vigil, Order Omega, etc.) ──
    if cp_pending:
        async with _g.CHALLENGE_PROGRESS_LOCK:
            cp_data = _b("_load_challenge_progress")()
            cp_dirty = False
            for challenge_name, role_id, required, notified_key in cp_pending:
                role = discord.utils.get(guild.roles, id=role_id)
                if role is None:
                    _g.logger.warning(f"Grace period: role {role_id} for '{challenge_name}' not found")
                    continue
                for member in guild.members:
                    if member.bot or role not in member.roles:
                        continue
                    uid = str(member.id)
                    user_cp = cp_data.get(uid, {})
                    logged = {m["mission"] for m in user_cp.get(challenge_name, [])}
                    if logged >= required:
                        continue  # still fully qualified
                    missing = sorted(required - logged)
                    try:
                        await member.remove_roles(role, reason="Grace period expired: new mission required")
                        total_revoked += 1
                        notified_list = user_cp.get("notified", [])
                        if notified_key in notified_list:
                            notified_list.remove(notified_key)
                            user_cp["notified"] = notified_list
                            cp_data[uid] = user_cp
                            cp_dirty = True
                        _g.logger.info(f"Grace period: revoked {role.name} from {uid} ({member.display_name})")
                        await _notify_revoked(
                            member, role.name,
                            [f"Complete all required missions. Missing: {', '.join(missing)}"]
                        )
                    except Exception as exc:
                        _g.logger.warning(f"Grace period: failed to revoke {challenge_name} from {uid}: {exc}")
            if cp_dirty:
                _b("_save_challenge_progress")(cp_data)

    # ── Crux Terminatus (role-based + BL Rank A audit) ───────────────────────
    if crux_pending:
        crux_role = guild.get_role(CRUX_TERMINATUS_ROLE_ID)
        if crux_role:
            async with _g.CHALLENGE_PROGRESS_LOCK:
                cp_data = _b("_load_challenge_progress")()
                cp_dirty = False
                for member in list(crux_role.members):
                    if member.bot:
                        continue
                    try:
                        member_role_ids: set[int] = {r.id for r in member.roles}
                        uid = str(member.id)
                        failed: list[str] = []

                        # Req 1: BL role held + all post-enforcement BL AARs are Rank A
                        has_bl = BLACK_LAURELS_ROLE_ID in member_role_ids
                        if not has_bl:
                            failed.append("Black Laurels role not held")
                        elif _g.DATASTORE:
                            _all_rank_a = True
                            for _rec in _g.DATASTORE.iter_records():
                                _bl = _rec.get("black_laurels_in_mission") or _rec.get("black_laurels_in_difficulty")
                                _mission_raw = re.sub(r"<@&\d+>", "", (_rec.get("mission") or "")).lower().strip()
                                _mission_clean = re.split(r"\s*@", _mission_raw)[0].strip()
                                if not _bl or _mission_clean not in BLACK_LAURELS_REQUIRED_MISSIONS:
                                    continue
                                if uid not in [str(b) for b in (_rec.get("brother_ids") or [])]:
                                    continue
                                _rank = (_rec.get("rank") or "").upper()
                                if _rank != "A":
                                    _ts = _rec.get("timestamp", "")
                                    _pre = True
                                    try:
                                        if _ts:
                                            _rec_dt = datetime.fromisoformat(_ts)
                                            if _rec_dt >= BLACK_LAURELS_STRICT_ENFORCEMENT_DATE:
                                                _pre = False
                                    except Exception:
                                        pass
                                    if not _pre:
                                        _all_rank_a = False
                                        break
                            if not _all_rank_a:
                                failed.append("Black Laurels \u2014 not all missions completed at Rank A")

                        # Req 2: Distinguished SOK-G Pipehitter role
                        if DISTINGUISHED_PIPEHITTER_ROLE_ID not in member_role_ids:
                            failed.append("Distinguished SOK-G: Pipehitter role not held")

                        # Req 3: 2+ Terminus Slayer class roles
                        ts_count = sum(1 for rid in KILL_LOG_CLASS_ROLES if rid in member_role_ids)
                        if ts_count < 2:
                            failed.append(f"Terminus Slayer classes: {ts_count}/2 completed")

                        if not failed:
                            continue

                        await member.remove_roles(crux_role, reason="Crux Terminatus requirements no longer met")
                        total_revoked += 1
                        user_cp = cp_data.get(uid, {})
                        notified_list = user_cp.get("notified", [])
                        if "crux_terminatus" in notified_list:
                            notified_list.remove("crux_terminatus")
                            user_cp["notified"] = notified_list
                            cp_data[uid] = user_cp
                            cp_dirty = True
                        _g.logger.info(f"Grace period: revoked Crux Terminatus from {uid} ({member.display_name})")
                        await _notify_revoked(member, "Crux Terminatus", failed)

                    except Exception as exc:
                        _g.logger.exception(f"Grace period: error processing Crux for {member.id}: {exc}")
                if cp_dirty:
                    _b("_save_challenge_progress")(cp_data)

    if total_revoked:
        _g.logger.info(f"Grace period enforcement: {total_revoked} role(s) revoked")

    return total_revoked


async def _check_promotion_milestones():
    """Check guild members for promotion eligibility milestones and send notifications.

    Checks for:
    - Watch Veteran eligibility: 200 AAR points AND 2 weeks in server
    - Service Studs milestones: new studs earned (1 per 4 weeks AND 400 AAR points)
    """
    try:
        guild = _b("_resolve_notification_guild")()
        if not guild:
            _g.logger.debug("Promotion check: no guild available")
            return

        if _g.DATASTORE is None:
            _g.logger.debug("Promotion check: _g.DATASTORE not initialized")
            return

        # Get service studs channel
        studs_channel = guild.get_channel(SERVICE_STUDS_CHANNEL_ID)
        if not studs_channel:
            try:
                studs_channel = await _g.bot.fetch_channel(SERVICE_STUDS_CHANNEL_ID)
            except Exception:
                _g.logger.warning(f"Service studs channel {SERVICE_STUDS_CHANNEL_ID} not found")
                studs_channel = None

        # Get Black Laurels notification channel
        black_laurels_channel = guild.get_channel(BLACK_LAURELS_CHANNEL_ID)
        if not black_laurels_channel:
            try:
                black_laurels_channel = await _g.bot.fetch_channel(BLACK_LAURELS_CHANNEL_ID)
            except Exception:
                _g.logger.warning(f"Black Laurels channel {BLACK_LAURELS_CHANNEL_ID} not found")
                black_laurels_channel = None

        # Get Oathsworn eligibility notification channel
        oathsworn_channel = guild.get_channel(OATHSWORN_CHANNEL_ID)
        if not oathsworn_channel:
            try:
                oathsworn_channel = await _g.bot.fetch_channel(OATHSWORN_CHANNEL_ID)
            except Exception:
                _g.logger.warning(f"Oathsworn channel {OATHSWORN_CHANNEL_ID} not found")
                oathsworn_channel = None

        if not studs_channel and not black_laurels_channel and not oathsworn_channel:
            _g.logger.warning("No promotion channels available")
            return

        # Load tracking data
        tracking = _load_promotion_tracking()
        notifications_sent = 0

        # Get Watch Captain/Lieutenant roles for mentions
        watch_captain_role = discord.utils.get(guild.roles, name="Watch Captain")
        watch_lt_role = discord.utils.get(guild.roles, name="Watch Lieutenant")
        captain_mention = watch_captain_role.mention if watch_captain_role else "@Watch Captain"
        lt_mention = watch_lt_role.mention if watch_lt_role else "@Watch Lieutenant"
        watch_command_mention = f"{captain_mention} / {lt_mention}"

        # Get Watch Veteran role for mentions
        watch_veteran_role = guild.get_role(WATCH_VETERAN_ROLE_ID)

        # Get Black Laurels role for mentions
        black_laurels_role = discord.utils.get(guild.roles, name="Black Laurels")
        black_laurels_mention = black_laurels_role.mention if black_laurels_role else "@Black Laurels"

        # Get award roles (by ID to avoid name change issues)
        ardent_raider_role = guild.get_role(ARDENT_RAIDER_ROLE_ID)
        apothecarion_medal_role = guild.get_role(APOTHECARION_SERVICE_MEDAL_ROLE_ID)
        crimson_laurels_role = guild.get_role(CRIMSON_LAURELS_ROLE_ID)

        # Build a map of user_id -> set of completed Black Laurels missions
        user_bl_missions: Dict[str, set] = {}
        for rec in _g.DATASTORE.iter_records():
            difficulty = (rec.get("difficulty") or "").lower()
            black_laurels_in_difficulty = "black" in difficulty and "laurel" in difficulty
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
                has_black_laurels = black_laurels_in_difficulty or black_laurels_in_mission
            else:
                has_black_laurels = black_laurels_in_difficulty

            if not has_black_laurels:
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
                role_names = {getattr(r, "name", "") for r in getattr(member, "roles", [])}
                is_watch_brother = "Watch Brother" in role_names or "Watch Sister" in role_names
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
                        "Venerable Dreadnought",
                        "Honored Dreadnought",
                        "Forgemaster",
                        "Void Warden",
                        "High Chaplain",
                        "Chief Apothecary",
                        "Castellan",
                        "Lord Executioner",
                        "Huntmaster",
                        "Watch Master",
                    ]
                )
                # Watch Brother ONLY = has Watch Brother but NOT any higher rank
                is_watch_brother_only = is_watch_brother and not is_veteran_or_higher

                if not (is_watch_brother_only or is_veteran_or_higher):
                    continue

                user_id = str(member.id)
                user_tracking = tracking.get(user_id, {})
                ann_channel: Optional[discord.abc.Messageable] = None
                ann_channel_resolved = False

                async def _get_member_award_announcement_channel() -> Optional[discord.abc.Messageable]:
                    nonlocal ann_channel, ann_channel_resolved
                    if not ann_channel_resolved:
                        ann_channel = await _b("_get_award_announcement_channel")(member, guild)
                        ann_channel_resolved = True
                    return ann_channel

                # Get member stats
                stats = compute_stats_for_user(user_id)
                aar_points = int(stats.get("aar_points", 0) or 0)

                # Get member induction time (supports override)
                joined_at = _get_effective_induction_date(member)
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
                    if role_name in _b("HOME_CHAPTERS"):
                        member_chapter = role_name
                        break

                # Auto-assign Watch Veteran + public announcement (200 AAR + 2 weeks)
                # Only for Watch Brother ONLY (not already Veteran+)
                if is_watch_brother_only and watch_veteran_role:
                    is_eligible = aar_points >= 200 and weeks_in_server >= 2
                    has_veteran_role = watch_veteran_role in member.roles
                    if is_eligible and not has_veteran_role and not user_tracking.get("veteran_assigned"):
                        veteran_role_assigned = False
                        try:
                            await member.add_roles(watch_veteran_role, reason="Auto-promotion: 200 AAR + 2 weeks")
                            veteran_role_assigned = True
                        except Exception as e:
                            _g.logger.warning(f"Failed to assign Watch Veteran role to {member.id}: {e}")
                        if veteran_role_assigned:
                            ann_channel = await _get_member_award_announcement_channel()
                            if ann_channel:
                                _enqueue_award_announcement(
                                    str(member.id), "watch_veteran", member_chapter, str(ann_channel.id), str(guild.id)
                                )
                                notifications_sent += 1
                            else:
                                _g.logger.warning(
                                    f"Watch Veteran announcement channel not found for {member.id}; role assigned but no announcement sent"
                                )
                            user_tracking["veteran_assigned"] = True

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
                        content, embed = _b("_get_service_studs_announcement")(
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
                            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
                        )
                        notifications_sent += 1
                        await asyncio.sleep(0.5)
                        # Only update tracking when we actually announce, so new_studs
                        # correctly reflects the full step (e.g. +4 at each auramite
                        # milestone) rather than just the last incremental earn.
                        user_tracking["last_earned_studs"] = earned_studs

                # Check Black Laurels eligibility (all 8 required missions completed)
                if black_laurels_channel and not user_tracking.get("black_laurels_notified"):
                    completed_bl = user_bl_missions.get(user_id, set())
                    is_bl_eligible = (
                        len(completed_bl) >= len(BLACK_LAURELS_REQUIRED_MISSIONS)
                        and completed_bl >= BLACK_LAURELS_REQUIRED_MISSIONS
                    )
                    # Only notify if eligible and doesn't already have the role
                    has_bl_role = black_laurels_role and black_laurels_role in member.roles
                    if is_bl_eligible and not has_bl_role:
                        msg = (
                            f"᛭⋅ {member.mention}\n"
                            f"᛭⋅ <:Deathwatch:1433161009106780170> {black_laurels_mention} <:Deathwatch:1433161009106780170>\n"
                            f"᛭⋅ {watch_command_mention}\n"
                            f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯"
                        )
                        await black_laurels_channel.send(
                            msg,
                            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
                        )
                        user_tracking["black_laurels_notified"] = True
                        notifications_sent += 1
                        await asyncio.sleep(0.5)

                # Auto-assign Ardent Raider Ribbon + public announcement (200 armory points)
                if ardent_raider_role:
                    armory_points = int(stats.get("armory_points", 0) or 0)
                    is_ar_eligible = armory_points >= ARDENT_RAIDER_ARMORY_POINTS_THRESHOLD
                    has_ar_role = ardent_raider_role in member.roles
                    if has_ar_role:
                        user_tracking["ardent_raider_notified"] = True
                    elif is_ar_eligible:
                        ardent_raider_assigned = False
                        try:
                            await member.add_roles(ardent_raider_role, reason="Auto-award: 200 armory points")
                            ardent_raider_assigned = True
                        except Exception as e:
                            _g.logger.warning(f"Failed to assign Ardent Raider role to {member.id}: {e}")
                        if ardent_raider_assigned:
                            if not user_tracking.get("ardent_raider_notified"):
                                ann_channel = await _get_member_award_announcement_channel()
                                if ann_channel:
                                    _enqueue_award_announcement(
                                        str(member.id), "ardent_raider", member_chapter, str(ann_channel.id), str(guild.id)
                                    )
                                    notifications_sent += 1
                                else:
                                    _g.logger.warning(
                                        f"Ardent Raider announcement channel not found for {member.id}; role assigned but no announcement sent"
                                    )
                            user_tracking["ardent_raider_notified"] = True

                # Auto-assign Apothecarion Service Medal + public announcement (150 geneseed points)
                if apothecarion_medal_role:
                    gene_seed_points = int(stats.get("gene_seed_points", 0) or 0)
                    is_ftf_eligible = gene_seed_points >= FOR_THE_FALLEN_GENESEED_POINTS_THRESHOLD
                    has_ftf_role = apothecarion_medal_role in member.roles
                    if has_ftf_role:
                        user_tracking["for_the_fallen_notified"] = True
                    elif is_ftf_eligible:
                        apothecarion_medal_assigned = False
                        try:
                            await member.add_roles(apothecarion_medal_role, reason="Auto-award: 150 geneseed points")
                            apothecarion_medal_assigned = True
                        except Exception as e:
                            _g.logger.warning(f"Failed to assign Apothecarion Medal role to {member.id}: {e}")
                        if apothecarion_medal_assigned:
                            if not user_tracking.get("for_the_fallen_notified"):
                                ann_channel = await _get_member_award_announcement_channel()
                                if ann_channel:
                                    _enqueue_award_announcement(
                                        str(member.id), "apothecarion_medal", member_chapter, str(ann_channel.id), str(guild.id)
                                    )
                                    notifications_sent += 1
                                else:
                                    _g.logger.warning(
                                        f"Apothecarion Medal announcement channel not found for {member.id}; role assigned but no announcement sent"
                                    )
                            user_tracking["for_the_fallen_notified"] = True

                # Auto-assign Crimson Laurels + public announcement (1000 AAR + Black Laurels)
                if crimson_laurels_role:
                    has_bl_role_for_cl = black_laurels_role and black_laurels_role in member.roles
                    is_cl_eligible = aar_points >= CRIMSON_LAURELS_AAR_POINTS_THRESHOLD and has_bl_role_for_cl
                    has_cl_role = crimson_laurels_role in member.roles
                    if has_cl_role:
                        user_tracking["crimson_laurels_notified"] = True
                    elif is_cl_eligible:
                        crimson_laurels_assigned = False
                        try:
                            await member.add_roles(crimson_laurels_role, reason="Auto-award: 1000 AAR + Black Laurels")
                            crimson_laurels_assigned = True
                        except Exception as e:
                            _g.logger.warning(f"Failed to assign Crimson Laurels role to {member.id}: {e}")
                        if crimson_laurels_assigned:
                            if not user_tracking.get("crimson_laurels_notified"):
                                ann_channel = await _get_member_award_announcement_channel()
                                if ann_channel:
                                    _enqueue_award_announcement(
                                        str(member.id), "crimson_laurels", member_chapter, str(ann_channel.id), str(guild.id)
                                    )
                                    notifications_sent += 1
                                else:
                                    _g.logger.warning(
                                        f"Crimson Laurels announcement channel not found for {member.id}; role assigned but no announcement sent"
                                    )
                            user_tracking["crimson_laurels_notified"] = True

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
                            "Venerable Dreadnought",
                            "Honored Dreadnought",
                            "Forgemaster",
                            "Void Warden",
                            "High Chaplain",
                            "Chief Apothecary",
                            "Castellan",
                            "Lord Executioner",
                            "Huntmaster",
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
                        oathsworn_role = discord.utils.get(guild.roles, name="Oathsworn")
                        has_oathsworn_role = oathsworn_role and oathsworn_role in member.roles

                        if is_oathsworn_eligible and not has_oathsworn_role:
                            # Generate flavorful announcement with embed and poll
                            content, embed, poll = _b("_get_oathsworn_announcement")(
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
                                allowed_mentions=discord.AllowedMentions(users=True, roles=True),
                            )
                            user_tracking["oathsworn_notified"] = True
                            notifications_sent += 1
                            await asyncio.sleep(0.5)

                # Update tracking
                if user_tracking:
                    tracking[user_id] = user_tracking

            except Exception as e:
                _g.logger.debug(f"Promotion check failed for member {member.id}: {e}")
                continue

        # Save tracking data (merge with current on-disk state under lock to avoid
        # overwriting concurrent changes from promotion_queue)
        await _enforce_challenge_grace_periods(guild, user_bl_missions, tracking)
        async with _g.PROMOTION_TRACKING_LOCK:
            fresh_tracking = _load_promotion_tracking()
            for uid, data in tracking.items():
                fresh_tracking.setdefault(uid, {}).update(data)
            _save_promotion_tracking(fresh_tracking)

        if notifications_sent > 0:
            _g.logger.info(f"Promotion check complete: {notifications_sent} announcement(s) queued")
        else:
            _g.logger.debug("Promotion check complete: no new milestones")

    except Exception as e:
        _g.logger.exception(f"Promotion check failed: {e}")


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
        _g.logger.exception("Error running activity status check loop")


_AWARD_DISPATCH_FN_MAP = {
    "watch_veteran": "_get_watch_veteran_announcement",
    "ardent_raider": "_get_ardent_raider_announcement",
    "apothecarion_medal": "_get_apothecarion_medal_announcement",
    "crimson_laurels": "_get_crimson_laurels_announcement",
    "sok_g_pipehitter": "_get_sok_g_pipehitter_announcement",
    "distinguished_pipehitter": "_get_distinguished_pipehitter_announcement",
    "black_laurels": "_get_black_laurels_announcement",
    "crux_terminatus": "_get_crux_terminatus_announcement",
    "kadaku_campaign_medal": "_get_kadaku_campaign_announcement",
    "black_reef_campaign_medal": "_get_black_reef_campaign_announcement",
    "distinguished_black_reef_campaign_medal": "_get_distinguished_black_reef_announcement",
    "the_order_omega": "_get_order_omega_announcement",
    "dual_vigil": "_get_dual_vigil_announcement",
    "terminus_slayer_assault": "_get_terminus_slayer_assault_announcement",
    "terminus_slayer_bulwark": "_get_terminus_slayer_bulwark_announcement",
    "terminus_slayer_heavy": "_get_terminus_slayer_heavy_announcement",
    "terminus_slayer_sniper": "_get_terminus_slayer_sniper_announcement",
    "terminus_slayer_tactical": "_get_terminus_slayer_tactical_announcement",
    "terminus_slayer_techmarine": "_get_terminus_slayer_techmarine_announcement",
    "terminus_slayer_vanguard": "_get_terminus_slayer_vanguard_announcement",
    "master_terminus_slayer": "_get_master_terminus_slayer_announcement",
}

# Maps award_type → (role_id, challenge_progress_notified_key) for challenge awards.
# Non-challenge awards (promotions, terminus slayer, etc.) are not listed here.
_CHALLENGE_AWARD_ROLE_MAP: dict[str, tuple[int, str]] = {
    "sok_g_pipehitter":                        (PIPEHITTER_ROLE_ID,                              "sok_g_pipehitter"),
    "distinguished_pipehitter":                (DISTINGUISHED_PIPEHITTER_ROLE_ID,                "distinguished_sok_g_pipehitter"),
    "black_laurels":                           (BLACK_LAURELS_ROLE_ID,                           "black_laurels"),
    "crux_terminatus":                         (CRUX_TERMINATUS_ROLE_ID,                         "crux_terminatus"),
    "kadaku_campaign_medal":                   (KADAKU_CAMPAIGN_MEDAL_ROLE_ID,                   "kadaku_campaign"),
    "black_reef_campaign_medal":               (BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID,               "black_reef"),
    "distinguished_black_reef_campaign_medal": (DISTINGUISHED_BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID, "distinguished_black_reef"),
    "the_order_omega":                         (THE_ORDER_OMEGA_ROLE_ID,                         "order_omega"),
    "dual_vigil":                              (DUAL_VIGIL_AWARD_ROLE_ID,                        "dual_vigil"),
}


async def _dm_award_failure(item: Dict, reason: str) -> None:
    """DM all admin_user_ids when an award announcement is dropped."""
    admin_ids = [str(a) for a in (_g.CONFIG.get("admin_user_ids") or [])]
    if not admin_ids:
        return
    member_id = item.get("member_id", "?")
    award_type = item.get("award_type", "?")
    channel_id = item.get("channel_id", "?")
    msg = (
        f"⚠️ **Award announcement dropped**\n"
        f"• Award: `{award_type}`\n"
        f"• Member: <@{member_id}> (`{member_id}`)\n"
        f"• Channel: <#{channel_id}> (`{channel_id}`)\n"
        f"• Reason: {reason}"
    )
    for admin_id in admin_ids:
        try:
            user = await _g.bot.fetch_user(int(admin_id))
            if user:
                await user.send(msg)
        except Exception as dm_exc:
            if _g.logger:
                _g.logger.warning(f"Award dispatch: failed to DM admin {admin_id}: {dm_exc}")


@tasks.loop(minutes=15)
async def _award_announcement_dispatch_loop():
    """Drains one pending award announcement every 15 minutes to avoid post spam."""
    try:
        queue = _load_award_queue()
        if not queue:
            return

        item = queue.pop(0)
        _save_award_queue(queue)

        guild = _g.bot.get_guild(int(item["guild_id"]))
        if not guild:
            reason = f"guild `{item['guild_id']}` not found"
            _g.logger.warning(f"Award dispatch: {reason}; dropping item")
            await _dm_award_failure(item, reason)
            return

        member = guild.get_member(int(item["member_id"]))
        if not member:
            reason = f"member `{item['member_id']}` not found in guild"
            _g.logger.warning(f"Award dispatch: {reason}; dropping item")
            await _dm_award_failure(item, reason)
            return

        channel_id = int(item["channel_id"])
        channel = guild.get_channel(channel_id) or guild.get_thread(channel_id)
        if not channel:
            try:
                channel = await _g.bot.fetch_channel(channel_id)
            except Exception as exc:
                _g.logger.warning(
                    f"Award dispatch: channel {channel_id} not found ({exc}); falling back to service studs channel"
                )
                channel = guild.get_channel(SERVICE_STUDS_CHANNEL_ID)
        if not channel:
            reason = f"no usable channel (tried `{channel_id}` + service studs fallback)"
            _g.logger.warning(f"Award dispatch: {reason}; dropping item")
            await _dm_award_failure(item, reason)
            return

        fn_name = _AWARD_DISPATCH_FN_MAP.get(item["award_type"])
        if not fn_name:
            reason = f"unknown award type `{item['award_type']}`"
            _g.logger.warning(f"Award dispatch: {reason}; dropping item")
            await _dm_award_failure(item, reason)
            return

        fn = _b(fn_name)
        if not fn:
            reason = f"announcement function `{fn_name}` not found in bot module"
            _g.logger.warning(f"Award dispatch: {reason}; dropping item")
            await _dm_award_failure(item, reason)
            return

        content, embed, award_file = fn(
            member=member,
            member_chapter=item.get("member_chapter", "Unknown"),
            guild=guild,
        )
        send_kwargs: Dict = {
            "embed": embed,
            "allowed_mentions": discord.AllowedMentions(users=True, roles=True),
        }
        if award_file:
            send_kwargs["file"] = award_file
        try:
            await channel.send(content, **send_kwargs)
        except Exception as exc:
            fallback = guild.get_channel(SERVICE_STUDS_CHANNEL_ID)
            if fallback and fallback.id != getattr(channel, "id", None):
                _g.logger.warning(
                    f"Award dispatch: send to {getattr(channel, 'id', '?')} failed ({exc}); retrying in service studs channel"
                )
                # Re-open the file if it was consumed by the failed send
                if award_file:
                    send_kwargs["file"] = _b("_get_award_image")(award_file.filename) or award_file
                await fallback.send(content, **send_kwargs)
            else:
                raise
        _g.logger.info(
            f"Award announcement dispatched: {item['award_type']} for {item['member_id']} "
            f"({len(queue)} remaining in queue)"
        )
    except Exception:
        _g.logger.exception("Error in award announcement dispatch loop")


@_g.bot.tree.command(
    name="litany_of_function",
    description="Describe the duties of Jericho Logi-Scribe Servitor V-1.",
)
async def litany_of_function(interaction: discord.Interaction):
    if not (
        _b("check_command_permission")(interaction.user, "litany_of_function") and _b("is_allowed_channel")(interaction)
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
        "/sanctify_battle_records [span_days] — Ingest chronicled AARs (admin).",
        "/audit_archive_discrepancies [span_days] — Recheck rejected AARs (admin).",
        "/reparse_records [limit] — Re-parse stored AARs from message URLs (admin).",
        "/cache_stats — Show DataStore cache and flush stats (admin).",
        "/audit_service_studs — List service-stud mismatches (Watch Command only).",
        "",
        "Notes: Some commands are restricted by role/config; outputs are capped or paginated.",
    ]
    text = "\n".join(lines)
    # Ensure message stays comfortably under Discord's 2000-char limit
    if len(text) > 1900:
        text = text[:1900].rsplit("\n", 1)[0] + "\n…"
    await interaction.response.send_message(text, ephemeral=True)


async def _requeue_award_type_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=k, value=k)
        for k in _AWARD_DISPATCH_FN_MAP
        if current.lower() in k.lower()
    ][:25]


@_g.bot.tree.command(
    name="requeue_award",
    description="Manually enqueue a missed award announcement for a member (admin).",
)
@app_commands.describe(
    member="The member who earned the award.",
    award_type="Award type string (e.g. terminus_slayer_tactical).",
    channel="Channel to post in (defaults to the member's announcement channel).",
)
@app_commands.autocomplete(award_type=_requeue_award_type_autocomplete)
async def requeue_award(
    interaction: discord.Interaction,
    member: discord.Member,
    award_type: str,
    channel: Optional[discord.TextChannel] = None,
):
    if not _b("check_command_permission")(interaction.user, "requeue_award"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    if award_type not in _AWARD_DISPATCH_FN_MAP:
        known = ", ".join(f"`{k}`" for k in sorted(_AWARD_DISPATCH_FN_MAP))
        await interaction.response.send_message(
            f"Unknown award type `{award_type}`.\nKnown types: {known}", ephemeral=True
        )
        return

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Must be used in a server.", ephemeral=True)
        return

    # Resolve posting channel
    target_channel = channel
    if target_channel is None:
        try:
            target_channel = await _b("_get_award_announcement_channel")(member, guild)
        except Exception:
            pass
    if target_channel is None:
        await interaction.response.send_message(
            "Could not resolve an announcement channel. Provide one explicitly with the `channel` parameter.",
            ephemeral=True,
        )
        return

    # Determine chapter
    home_chapters: list[str] = _b("HOME_CHAPTERS") or []
    member_role_names = {r.name for r in member.roles}
    member_chapter = next(
        (hc for hc in home_chapters if hc in member_role_names), "Unknown"
    )

    # --- Role assignment ---
    role_assigned = False
    challenge_info = _CHALLENGE_AWARD_ROLE_MAP.get(award_type)
    if challenge_info:
        role_id, notified_key = challenge_info
        role = guild.get_role(role_id)
        if role is None:
            await interaction.response.send_message(
                f"⚠️ Could not find role ID `{role_id}` for `{award_type}` in this guild.",
                ephemeral=True,
            )
            return
        if discord.utils.get(member.roles, id=role_id):
            await interaction.response.send_message(
                f"ℹ️ {member.mention} already has the **{role.name}** role. "
                f"Enqueue anyway? If so, remove the role first and re-run.",
                ephemeral=True,
            )
            return
        try:
            await member.add_roles(role, reason=f"requeue_award: {award_type} by {interaction.user}")
            role_assigned = True
        except Exception as exc:
            await interaction.response.send_message(
                f"❌ Failed to assign **{role.name}** to {member.mention}: `{exc}`",
                ephemeral=True,
            )
            return

        # --- Mark notified in challenge_progress.json ---
        try:
            async with _g.CHALLENGE_PROGRESS_LOCK:
                cp_data = _b("_load_challenge_progress")()
                user_entry = cp_data.setdefault(str(member.id), {"notified": []})
                notified_list = user_entry.setdefault("notified", [])
                if notified_key not in notified_list:
                    notified_list.append(notified_key)
                    user_entry["notified"] = notified_list
                    _b("_save_challenge_progress")(cp_data)
        except Exception as exc:
            if _g.logger:
                _g.logger.warning(
                    f"requeue_award: could not mark notified for {member.id} / {notified_key}: {exc}"
                )

    _enqueue_award_announcement(
        str(member.id),
        award_type,
        member_chapter,
        str(target_channel.id),
        str(guild.id),
    )

    role_note = f" Role **{role.name}** assigned." if role_assigned else ""
    await interaction.response.send_message(
        f"✅ Enqueued `{award_type}` for {member.mention} → <#{target_channel.id}>.{role_note}\n"
        f"It will be posted within the next dispatch cycle (≤15 min).",
        ephemeral=True,
    )


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
    return {"remaining": _b("HOME_CHAPTERS").copy(), "selected": {}}


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


async def _select_home_chapters_for_month(offset: int = 0, guild: Optional[discord.Guild] = None) -> Tuple[str, str]:
    """Select (and cache) two chapters for a month specified by offset from now.

    If a selection for that month already exists, return it. Otherwise pick two
    random chapters from the current `remaining` pool (resetting if needed),
    remove them from the pool, cache the pair under that month, and persist.
    """
    async with _g.ROTATION_LOCK:
        state = _load_home_chapter_rotation()
        target = _month_key_for_offset(offset)
        selected = state.get("selected", {}) or {}

        def _active_for_month(month_key: str, days: int = 28) -> List[str]:
            """Determine active _b('HOME_CHAPTERS') for the month using guild roles only.

            Active determination (new behavior): a chapter is active if at least one
            guild member holds the chapter role and that member does NOT have any
            role whose name contains 'reserve' (case-insensitive). This ignores
            AAR activity entirely as requested.
            """
            try:
                g = guild or _b("_resolve_notification_guild")()
                if g is None:
                    return _b("HOME_CHAPTERS").copy()

                active_chapters = set()
                members = getattr(g, "members", []) or []

                for canon in _b("HOME_CHAPTERS"):
                    canon_low = canon.lower()
                    total_with_role = 0
                    non_reserve_count = 0
                    for mbr in members:
                        try:
                            # Check if member has the exact chapter role name
                            has_chap = any(
                                (getattr(r, "name", "") or "").strip().lower() == canon_low
                                for r in getattr(mbr, "roles", []) or []
                            )
                            if not has_chap:
                                continue
                            total_with_role += 1
                            # If member has any role with 'reserve' in its name, treat as reserve
                            has_reserve = any(
                                (getattr(rr, "name", "") or "").lower().find("reserve") >= 0
                                for rr in getattr(mbr, "roles", []) or []
                            )
                            if not has_reserve:
                                non_reserve_count += 1
                        except Exception:
                            continue
                    if total_with_role > 0 and non_reserve_count > 0:
                        active_chapters.add(canon)

                active_list = sorted(active_chapters)
                return active_list if active_list else _b("HOME_CHAPTERS").copy()
            except Exception:
                return _b("HOME_CHAPTERS").copy()

        # If we have a cached pair for the target month, check if we should return it as-is.
        if target in selected and isinstance(selected[target], list) and len(selected[target]) == 2:
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
                            candidates = [c for c in _b("HOME_CHAPTERS") if c not in new_pair]
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
                pool = set(_b("HOME_CHAPTERS"))

            # Keep any still-active picks, replace inactive ones
            kept = [p for p in pair if p in pool]
            needed = 2 - len(kept)
            # Build candidate list excluding already-kept and excluding other months' selected entries
            candidates = [c for c in pool if c not in kept]
            if len(candidates) < needed:
                candidates = [c for c in _b("HOME_CHAPTERS") if c not in kept]

            try:
                new_picks = random.sample(candidates, needed) if needed > 0 else []
            except Exception:
                # Fallback to any remaining
                new_picks = (candidates + _b("HOME_CHAPTERS"))[:needed]

            new_pair = kept + new_picks
            # Ensure two items and deterministic order
            new_pair = new_pair[:2]
            selected[target] = new_pair
            # Also remove replacements from remaining pool if present
            remaining = [r for r in (state.get("remaining") or []) if r in _b("HOME_CHAPTERS")]
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
                g = guild or _b("_resolve_notification_guild")()
                if g is None:
                    return _b("HOME_CHAPTERS").copy()

                # Determine chapters based solely on guild membership/reserves status
                active_chapters = set()
                members = getattr(g, "members", []) or []

                for canon in _b("HOME_CHAPTERS"):
                    canon_low = canon.lower()
                    total_with_role = 0
                    non_reserve_count = 0
                    for mbr in members:
                        try:
                            has_chap = any(
                                (getattr(r, "name", "") or "").strip().lower() == canon_low
                                for r in getattr(mbr, "roles", []) or []
                            )
                            if not has_chap:
                                continue
                            total_with_role += 1
                            has_reserve = any(
                                (getattr(rr, "name", "") or "").lower().find("reserve") >= 0
                                for rr in getattr(mbr, "roles", []) or []
                            )
                            if not has_reserve:
                                non_reserve_count += 1
                        except Exception:
                            continue
                    if total_with_role > 0 and non_reserve_count > 0:
                        active_chapters.add(canon)

                active_list = sorted(active_chapters)
                return active_list if active_list else _b("HOME_CHAPTERS").copy()
            except Exception:
                return _b("HOME_CHAPTERS").copy()

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
            pool = _b("HOME_CHAPTERS").copy()
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


@_g.bot.tree.command(
    name="pick_home_chapters",
    description="Show selected home chapters for this month and next (plans ahead).",
)
async def pick_home_chapters(interaction: discord.Interaction):
    if not (
        _b("check_command_permission")(interaction.user, "pick_home_chapters") and _b("is_allowed_channel")(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    # Compute current and next month keys and selections
    this_key = _month_key_for_offset(0)
    next_key = _month_key_for_offset(1)
    g = interaction.guild or _b("_resolve_notification_guild")()
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
    if _b("DEBUG_MODE"):
        try:
            selected_chapters = [a1, b1, a2, b2]
            # dedupe while preserving order
            seen = set()
            selected_unique = [c for c in selected_chapters if c and (c not in seen and not seen.add(c))]
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
                    print(f"  {display} ({getattr(m, 'id', '')}) - Reserves: {has_reserves}")
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
# ─────────────────────────────────────────────────────────────────────────────
# Dynamic Forge Rite Components
# ─────────────────────────────────────────────────────────────────────────────
# Pure data tables (CHAPTER_BLESSINGS, RANK_HONORIFICS, RANK_PRESTIGE_WEIGHTS,
# TECHMARINE_*_ACKNOWLEDGMENTS, etc.) live in flavor_text.py. The two helper
# functions below remain here because they reference them.


async def _forum_post_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
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


@_g.bot.tree.command(name="tally_deeds", description="Display the Deeds Ledger for a Brother.")
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
                send_to_channel = await _g.bot.fetch_channel(channel_id)
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
        if not isinstance(send_to_channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)):
            await interaction.response.send_message(
                "send_to must be a thread or forum post — not a forum channel itself.",
                ephemeral=True,
            )
            return

    # Permission check: requires Watch Command role and allowed channel
    if not (_b("check_command_permission")(interaction.user, "tally_deeds") and _b("is_allowed_channel")(interaction)):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    # Mutual exclusivity check BEFORE deferring - must provide one or the other, not both
    if brother and killteam:
        await interaction.response.send_message("Provide either 'brother' or 'killteam', not both.", ephemeral=True)
        return

    # First response: defer, so we can do slower work safely
    await interaction.response.defer(thinking=False, ephemeral=True)

    if killteam:
        members = [m for m in getattr(killteam, "members", [])]
        # If the provided role is one of the canonical rank roles, restrict
        # the roster to members who have that rank and do NOT hold any
        # higher-ranked role. Higher rank == lower index in
        # _b('RANK_ROLES_PRIORITY').
        try:
            role_name = getattr(killteam, "name", "") or ""
            role_idx = _b("_role_index")(role_name)
        except Exception:
            role_idx = None

        if role_idx is not None:
            filtered: List[discord.Member] = []
            for m in members:
                try:
                    # Collect indices of all rank roles this member has
                    member_rank_indices = [_b("_role_index")(getattr(r, "name", "")) for r in getattr(m, "roles", [])]
                    # Must explicitly have the passed role
                    has_target_role = any(getattr(r, "name", "") == role_name for r in getattr(m, "roles", []))
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
        await interaction.followup.send("Specify a brother or a killteam role.", ephemeral=True)
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
        for rank in _b("RANK_ROLES_PRIORITY"):
            for role in target.roles:
                if role.name == rank:
                    current_rank = rank
                    break
            if current_rank != "Unknown":
                break

        display_name = target.nick or target.display_name

        # Member induction date (custom override or server join time); fallback to 'Unknown'
        try:
            joined_at = _get_effective_induction_date(target)
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
            idx_veteran = _b("_role_index")("Watch Veteran")
            highest_idx = _b("get_highest_rank_index")(target)
            # Only compute if the user has a recognized rank at or above Watch Veteran
            if (idx_veteran is not None) and (highest_idx is not None) and (highest_idx <= idx_veteran):
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

        # Use in-memory records from _g.DATASTORE
        trials_reported = _count_inductions_from_records(str(target.id), _g.DATASTORE.iter_records())

        # Home chapter from resolved map (fallback: REDACTED)
        home_chapter = chapters_map.get(str(target.id)) if chapters_map else "REDACTED"

        # Determine Active/Inactive status: Active if any AAR in last 30 days.

        try:
            # Use cached last_aar_ts from user_stats_cache to avoid O(N) record scan
            cached_ts = _g.DATASTORE.get_user_stats(str(target.id)).get("last_aar_ts")
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
            role_names = _b("_canonical_role_names")(target)
            roles = getattr(target, "roles", [])

            # High command ranks that should NOT show Company
            high_command = {
                "Watch Master",
                "Lord Executioner",
                "Huntmaster",
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
                        kt_name = _b("_extract_killteam_name")(rn)
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
        lines.append("\u001b[32m==============================================================================")
        lines.append("  WATCH FORTRESS JERICHO // SERVICE-RECORD NODE")
        lines.append("  OPERATION-SCRIBE SERVITOR — DEEDS LEDGER")
        lines.append("==============================================================================")
        lines.append(f"  Tally for: {display_name}")
        lines.append("------------------------------------------------------------------------------")
        for label, value in stat_rows:
            lines.append(f"  {label:<{label_width}} {value}")
        lines.append("==============================================================================")
        lines.append("  Machine-Spirit Addendum:")
        lines.append("  These Deeds are logged for future deployment rites")
        lines.append("  and may be invoked by decree of Watch Command alone.")
        lines.append("==============================================================================")
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
                "role_names": list(_b("_canonical_role_names")(target)),
                "home_chapter": home_chapter,
                # Rank bucket for roster sorting: Sergeant (0), Kill Team Champion (1), Veteran (2), Brother/Sister (3), Other (9)
                "rank_bucket": (
                    0
                    if ("Watch Sergeant" in _b("_canonical_role_names")(target))
                    else 1
                    if ("Kill Team Champion" in _b("_canonical_role_names")(target))
                    else 2
                    if ("Watch Veteran" in _b("_canonical_role_names")(target))
                    else 3
                    if (
                        ("Watch Brother" in _b("_canonical_role_names")(target))
                        or ("Watch Sister" in _b("_canonical_role_names")(target))
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
            r_lines.append("\u001b[32m==============================================================================")
            r_lines.append("  WATCH FORTRESS JERICHO // SERVICE-RECORD NODE")
            r_lines.append("  KILL TEAM DEEDS ROSTER")
            r_lines.append("==============================================================================")

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
                    status_flag = 0 if str(it.get("status", "")).lower() == "active" else 1
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
            status_w = max((_len_str(it.get("status", "")) for it in sorted_items), default=1)
            # Cap widths to keep table tidy and avoid overflow from long names
            name_w = min(name_w, 24)
            status_w = min(status_w, 12)
            aar_w = max((_len_str(it.get("aar", 0)) for it in sorted_items), default=1)
            gene_w = max((_len_str(it.get("gene", 0)) for it in sorted_items), default=1)
            armory_w = max((_len_str(it.get("armory", 0)) for it in sorted_items), default=1)
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
                kt_display_name = _b("_extract_killteam_name")(getattr(killteam, "name", "Unknown"))
                roster_embed = discord.Embed(
                    title="᛭⋅ KILL TEAM ROSTER ⋅᛭",
                    description=f"*⌾ {kt_display_name} ⌾*",
                    color=0x2ECC71,
                )

                # Build roster entries using combat bonds style formatting
                roster_lines = []
                for it in sorted_items:
                    _member_id = str(it.get("member_id", "") or "")  # Available but not displayed in this format
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
                    for rp in _b("RANK_ROLES_PRIORITY"):
                        if rp in role_names:
                            member_rank = rp
                            break
                    rank_emoji = _b("_get_rank_emoji")(interaction.guild, member_rank) if member_rank else ""

                    # Strip rank prefix from name (case-insensitive)
                    stripped_name = nm
                    for rp in _b("RANK_ROLES_PRIORITY"):
                        if stripped_name.lower().startswith(rp.lower()):
                            stripped_name = stripped_name[len(rp) :].lstrip()
                            break
                    # Truncate after stripping
                    stripped_name = stripped_name[:20]

                    # Get chapter emoji
                    chapter_emoji = ""
                    if home_ch and home_ch not in ("Unknown", "REDACTED"):
                        chapter_emoji = _b("_get_emoji_by_name")(interaction.guild, home_ch) or ""

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
                        f"{status_icon} {member_label}\nAAR: {aar_v} | Gene: {gene_v} | Armory: {armory_v}"
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

                roster_embed.set_footer(text="᛭⋅ Roster generated from recent service records ⋅᛭")

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
                        await interaction.followup.send(embed=roster_embed, ephemeral=True)
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
        kt_display = _b("_extract_killteam_name")(kt_name_raw)
        is_chapter_role = kt_name_raw in _b("HOME_CHAPTERS")

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
                val, rank, total = active_rankings.get(metric_key, {}).get(key, (0, 0, 0))
                return f"#{rank}/{total}"
            except Exception:
                return "—"

        def fmt_val_rank(metric_key: str, key: str, val_fmt: str = "") -> str:
            try:
                val, rank, total = active_rankings.get(metric_key, {}).get(key, (0, 0, 0))
                if val_fmt:
                    return f"{val_fmt.format(val)} (#{rank}/{total})"
                return f"{val} (#{rank}/{total})"
            except Exception:
                return "—"

        # Build the new honours-style output for kill team or chapter
        s_lines = []
        s_lines.append("```ansi")
        s_lines.append("\u001b[32m==============================================================================")
        s_lines.append("  WATCH FORTRESS JERICHO // LEDGER-CAST")
        s_lines.append("  OPERATION-SCRIBE SERVITOR — MONTHLY HONOURS")
        s_lines.append(f"  Date: {imperial_date}")
        s_lines.append(f"  {display_type}: {display_label}")
        s_lines.append("==============================================================================")
        s_lines.append("")
        s_lines.append(f"{display_type} DISTINCTIONS")

        if queried_key:
            # Get values and ranks for each metric
            ops_data = active_rankings.get("ops", {}).get(queried_key, (0, 0, 0))
            avg_data = active_rankings.get("avg", {}).get(queried_key, (0.0, 0, 0))
            pres_data = active_rankings.get("pres", {}).get(queried_key, (0, 0, 0))
            armory_data = active_rankings.get("armory", {}).get(queried_key, (0, 0, 0))
            gene_data = active_rankings.get("gene_carried", {}).get(queried_key, (0, 0, 0))
            risk_data = active_rankings.get("high_risk", {}).get(queried_key, (0, 0, 0))
            omega_kia_data = active_rankings.get("omega_kia", {}).get(queried_key, (0, 0, 0))
            force_data = active_rankings.get("avg_aar_per_member", {}).get(queried_key, (0.0, 0, 0))
            cohesion_data = active_rankings.get("cohesion", {}).get(queried_key, (0.0, 0, 0))

            s_lines.append(f"Total Operations         (Ops {int(ops_data[0])}) — Rank #{ops_data[1]}/{ops_data[2]}")
            s_lines.append(f"Avg Points per Op        (Avg Op {avg_data[0]:.1f}) — Rank #{avg_data[1]}/{avg_data[2]}")
            s_lines.append(
                f"Armory + Gene-seed       (ArmoryPts {armory_data[0]:.1f} | GenePts {gene_data[0]:.1f}) — Rank #{pres_data[1]}/{pres_data[2]}"
            )
            omega_suffix = f" | Omega KIA {int(omega_kia_data[0])}" if omega_kia_data[0] > 0 else ""
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
        s_lines.append("==============================================================================")
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
                armory_data = active_rankings.get("armory", {}).get(queried_key, (0, 0, 0))
                gene_data = active_rankings.get("gene_carried", {}).get(queried_key, (0, 0, 0))
                risk_data = active_rankings.get("high_risk", {}).get(queried_key, (0, 0, 0))
                omega_kia_data = active_rankings.get("omega_kia", {}).get(queried_key, (0, 0, 0))
                force_data = active_rankings.get("avg_aar_per_member", {}).get(queried_key, (0.0, 0, 0))
                cohesion_data = active_rankings.get("cohesion", {}).get(queried_key, (0.0, 0, 0))

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
                omega_suffix = f" | KIA {int(omega_kia_data[0])}" if omega_kia_data[0] > 0 else ""
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
                await interaction.followup.send(f"Posted to <#{send_to_channel.id}>.", ephemeral=True)
            else:
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            # Fallback to simple embed
            try:
                fallback_title = "Chapter Summary" if is_chapter_role else "Kill Team Summary"
                embed = _embed_from_ansi(fallback_title, summary_text)
                if send_to_channel:
                    await send_to_channel.send(embed=embed)
                    await interaction.followup.send(f"Posted to <#{send_to_channel.id}>.", ephemeral=True)
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
                for rank in _b("RANK_ROLES_PRIORITY"):
                    if rank in [getattr(r, "name", "") for r in target.roles]:
                        member_rank_name = rank
                        break
                rank_emoji = _b("_get_rank_emoji")(guild, member_rank_name) if guild else ""

                # Get home chapter emoji
                home_ch = stat_dict.get("Home Chapter", "Unknown")
                chapter_emoji = (
                    _b("_get_emoji_by_name")(guild, home_ch)
                    if guild and home_ch and home_ch not in ("Unknown", "REDACTED")
                    else None
                )

                embed = discord.Embed(
                    title="᛭⋅ DEEDS LEDGER ⋅᛭",
                    description="*⌾ Watch Fortress Jericho ⌾*",
                    color=0x2ECC71,
                )

                # ▸ Bearer field (exactly matching forge_rite format)
                bearer_honorific, bearer_name, bearer_title = _b("_get_bearer_rank_and_title")(target)
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
                    lineage_display = "REDACTED" if home_ch == "Black Shield" else home_ch
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
                embed.add_field(name="▸ Status", value="\n".join(status_lines), inline=True)

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
                    armor_state = await _b("_get_armor_state")(int(target.id))
                    points_since_blessing = armor_state.get("points_since_blessing", 0)
                    spirit_fractured = armor_state.get("spirit_fractured", False)
                    armor_tier = _b("_get_member_damage_tier")(target)
                    damage_probability = _b("_get_damage_probability")(points_since_blessing)
                    _prob_percent = damage_probability * 100  # Calculated but not displayed in this context
                    machine_spirit = await _b("_get_machine_spirit")(int(target.id))

                    # Roll scan detection (same as armor_status)
                    scan_result = await _b("_get_or_roll_scan_result")(
                        int(target.id), armor_tier, points_since_blessing, spirit_fractured
                    )
                    scan_missed = not scan_result["detected"]

                    if scan_missed:
                        # Undetected - mask armor data
                        embed.add_field(
                            name="▸ Armor Integrity",
                            value="⚫ **UNDETECTED** | Spirit: ???\nPenalty Risk: ???",
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
                        machine_spirit_emoji = _b("_get_emoji_by_name")(guild, "MachineSpirit") or "⚙️"

                        if spirit_fractured:
                            spirit_display = f"{machine_spirit_emoji} SEVERED"
                        elif machine_spirit:
                            spirit_display = f"{machine_spirit_emoji} `{machine_spirit}` ({spirit_status})"
                        else:
                            spirit_display = f"{machine_spirit_emoji} *UNBOUND*"

                        armor_lines = [f"{armor_icon} **{armor_status}** | {spirit_display}"]
                        # Show penalty risk and cycles (hide cycles for nominal brothers)
                        penalty_risk = _b("_get_tier_risk_display")(armor_tier, spirit_fractured)
                        if armor_status == "NOMINAL":
                            armor_lines.append(f"Penalty Risk: {penalty_risk}")
                        else:
                            armor_lines.append(f"Penalty Risk: {penalty_risk} | Cycles: {points_since_blessing}c")

                        embed.add_field(
                            name="▸ Armor Integrity",
                            value="\n".join(armor_lines),
                            inline=False,
                        )
                except Exception:
                    pass  # Skip armor field if data unavailable

                # ▸ Warp Sanction field (status only; raw exposure hidden from brothers)
                try:
                    warp_state = await _get_warp_exposure_state(int(target.id))
                    warp_points = int(warp_state.get("points_since_warding", 0) or 0)
                    sanction_key = await _get_warp_sanction_status(warp_points, int(target.id))
                    sanction_label, sanction_desc = WARP_SANCTION_STATUS.get(
                        sanction_key,
                        ("Cleansed", "Clear or minimal contamination detected."),
                    )
                    if warp_state.get("warp_corrupted"):
                        sanction_label = f"{sanction_label} — CORRUPTED"
                        sanction_desc = (
                            "Warp corruption confirmed by repeated restricted-tier exposure. "
                            "Void Warden intervention required."
                        )
                    embed.add_field(
                        name="▸ Warp Sanction",
                        value=f"🧿 **{sanction_label.upper()}**\n{sanction_desc}",
                        inline=False,
                    )
                except Exception:
                    pass

                # ▸ Challenges field
                target_role_ids_ch = {getattr(r, "id", 0) for r in getattr(target, "roles", [])}
                completed_challenges = []
                for role_id_ch, display_name_ch, emoji_hint in CHALLENGE_ROLES:
                    if role_id_ch in target_role_ids_ch:
                        emoji_str = ""
                        if emoji_hint:
                            if emoji_hint.startswith("unicode:"):
                                emoji_str = f"{emoji_hint[8:]} "
                            else:
                                emoji = _b("_get_emoji_by_name")(guild, emoji_hint)
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

                # Footer
                embed.set_footer(text="᛭⋅ Recorded by decree of Watch Command ⋅᛭")
            else:
                embed = _embed_from_ansi("Deeds Ledger", reply_text)
        except Exception:
            embed = _embed_from_ansi("Deeds Ledger", reply_text)

        # Huntmaster Jack portrait
        _HUNTMASTER_JACK_ID = 1444810056821637133
        _jack_file = None
        if len(members) == 1 and int(target.id) == _HUNTMASTER_JACK_ID:
            _jack_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "1444810056821637133_Huntmaster_Jack.png")
            if os.path.exists(_jack_path):
                _jack_file = discord.File(_jack_path, filename="1444810056821637133_Huntmaster_Jack.png")
                embed.set_thumbnail(url="attachment://1444810056821637133_Huntmaster_Jack.png")

        # Send embed only (clean output like forge_rite/stud announcement)
        if send_to_channel:
            await send_to_channel.send(embed=embed, **({"file": _jack_file} if _jack_file else {}))
        else:
            await interaction.followup.send(embed=embed, ephemeral=True, **({"file": _jack_file} if _jack_file else {}))

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
            target_name = getattr(target, "display_name", getattr(target, "name", "Unknown"))
            home_chapter = resolved_chapters_map.get(target_id, chapters_map.get(target_id, "Unknown"))

            # Get individual ranking data
            ops_data = individual_rankings.get("ops", {}).get(target_id, (0, 0, 0))
            avg_data = individual_rankings.get("avg", {}).get(target_id, (0.0, 0, 0))
            gene_data = individual_rankings.get("gene_carried", {}).get(target_id, (0, 0, 0))
            armory_data = individual_rankings.get("armory", {}).get(target_id, (0, 0, 0))
            risk_data = individual_rankings.get("high_risk", {}).get(target_id, (0, 0, 0))
            omega_kia_data = individual_rankings.get("omega_kia", {}).get(target_id, (0, 0, 0))
            black_laurels_data = individual_rankings.get("black_laurels", {}).get(target_id, (0, 0, 0))

            # Get chapter ranking data (matching kill team metrics)
            ch_ops_data = chapter_rankings.get("ops", {}).get(home_chapter, (0, 0, 0))
            ch_avg_data = chapter_rankings.get("avg", {}).get(home_chapter, (0.0, 0, 0))
            ch_pres_data = chapter_rankings.get("pres", {}).get(home_chapter, (0, 0, 0))
            ch_armory_val = chapter_rankings.get("armory", {}).get(home_chapter, (0, 0, 0))[0]
            ch_gene_val = chapter_rankings.get("gene_carried", {}).get(home_chapter, (0, 0, 0))[0]
            ch_risk_data = chapter_rankings.get("high_risk", {}).get(home_chapter, (0, 0, 0))
            ch_omega_kia_data = chapter_rankings.get("omega_kia", {}).get(home_chapter, (0, 0, 0))
            ch_aar_data = chapter_rankings.get("avg_aar_per_member", {}).get(home_chapter, (0.0, 0, 0))

            # Get target's kill teams using _resolve_killteams_for_member
            target_killteams = []
            try:
                target_killteams = _b("_resolve_killteams_for_member")(target)
            except Exception:
                pass

            # Build honours ANSI block
            h_lines = []
            h_lines.append("```ansi")
            h_lines.append("\u001b[32m==============================================================================")
            h_lines.append("  WATCH FORTRESS JERICHO // LEDGER-CAST")
            h_lines.append("  OPERATION-SCRIBE SERVITOR — MONTHLY HONOURS")
            h_lines.append(f"  Date: {imperial_date}")
            h_lines.append(f"  Brother: {target_name}")
            h_lines.append(f"  Home Chapter: {home_chapter}")
            h_lines.append("==============================================================================")
            h_lines.append("")
            h_lines.append("INDIVIDUAL DISTINCTIONS")

            if ops_data[2] > 0:  # Has ranking data
                h_lines.append(f"Total Operations         (Ops {int(ops_data[0])}) — Rank #{ops_data[1]}/{ops_data[2]}")
                h_lines.append(
                    f"Avg Points per Op        (Avg Op {avg_data[0]:.1f}) — Rank #{avg_data[1]}/{avg_data[2]}"
                )
                h_lines.append(
                    f"Gene-seed Points         (GeneseedPts {int(gene_data[0])}) — Rank #{gene_data[1]}/{gene_data[2]}"
                )
                h_lines.append(
                    f"Armory Points            (ArmoryPts {int(armory_data[0])}) — Rank #{armory_data[1]}/{armory_data[2]}"
                )
                omega_suffix = f" | Omega KIA {int(omega_kia_data[0])}" if omega_kia_data[0] > 0 else ""
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
                ch_omega_suffix = f" | Omega KIA {int(ch_omega_kia_data[0])}" if ch_omega_kia_data[0] > 0 else ""
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
                    kt_armory_val = team_rankings.get("armory", {}).get(kt_name, (0, 0, 0))[0]
                    kt_gene_val = team_rankings.get("gene_carried", {}).get(kt_name, (0, 0, 0))[0]
                    kt_risk_data = team_rankings.get("high_risk", {}).get(kt_name, (0, 0, 0))
                    kt_aar_data = team_rankings.get("avg_aar_per_member", {}).get(kt_name, (0.0, 0, 0))
                    kt_cohesion_data = team_rankings.get("cohesion", {}).get(kt_name, (0.0, 0, 0))
                    kt_omega_kia_data = team_rankings.get("omega_kia", {}).get(kt_name, (0, 0, 0))
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
                            f" | Omega KIA {int(kt_omega_kia_data[0])}" if kt_omega_kia_data[0] > 0 else ""
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
            h_lines.append("==============================================================================")
            h_lines.append("\u001b[0m```")
            honours_text = "\n".join(h_lines)

            # Build a clean, mobile-friendly embed (Jericho embed style)
            try:
                # Get chapter emoji for display
                guild = interaction.guild
                chapter_emoji = (
                    _b("_get_emoji_by_name")(guild, home_chapter)
                    if guild and home_chapter and home_chapter not in ("Unknown", "REDACTED")
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

                _median_rank = None  # Calculated below as overall_rank
                if individual_ranks:
                    _median_rank = statistics.median(individual_ranks)

                # Compute overall rank as average of individual rankings
                overall_rank = None
                if individual_ranks:
                    overall_rank = statistics.median(individual_ranks)

                # ▸ Individual Distinctions field
                if ops_data[2] > 0:
                    omega_suffix = f" | KIA {int(omega_kia_data[0])}" if omega_kia_data[0] > 0 else ""
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
                lineage_display = "REDACTED" if home_chapter == "Black Shield" else home_chapter
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

                _ch_median_rank = None  # Calculated below as ch_overall_rank
                if chapter_ranks:
                    _ch_median_rank = statistics.median(chapter_ranks)

                # Compute overall rank as median of chapter rankings
                ch_overall_rank = None
                if chapter_ranks:
                    ch_overall_rank = statistics.median(chapter_ranks)

                if ch_ops_data[2] > 0:
                    ch_omega_suffix = f" | KIA {int(ch_omega_kia_data[0])}" if ch_omega_kia_data[0] > 0 else ""
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
                    kt_risk_data = team_rankings.get("high_risk", {}).get(kt_name, (0, 0, 0))
                    kt_aar_data = team_rankings.get("avg_aar_per_member", {}).get(kt_name, (0.0, 0, 0))
                    kt_cohesion_data = team_rankings.get("cohesion", {}).get(kt_name, (0.0, 0, 0))
                    kt_omega_kia_data = team_rankings.get("omega_kia", {}).get(kt_name, (0, 0, 0))

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
                        kt_omega_suffix = f" | KIA {int(kt_omega_kia_data[0])}" if kt_omega_kia_data[0] > 0 else ""
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
                await interaction.followup.send(f"Posted to <#{send_to_channel.id}>.", ephemeral=True)
            else:
                await interaction.followup.send(embed=honours_embed, ephemeral=True)


@_g.bot.tree.command(
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
    caller_role_names = _b("_canonical_role_names")(caller)

    # Forgemaster bypass for testing
    is_forgemaster = "Forgemaster" in caller_role_names

    # Check caller has Watch Brother role (Forgemaster exempt)
    if not is_forgemaster and ("Watch Brother" not in caller_role_names and "Watch Sister" not in caller_role_names):
        await interaction.response.send_message("This command is for Watch Brothers only.", ephemeral=True)
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
        await interaction.response.send_message("Could not determine channel context.", ephemeral=True)
        return

    is_thread = (
        isinstance(ch, discord.Thread)
        if hasattr(discord, "Thread")
        else getattr(ch, "type", None) == discord.ChannelType.public_thread
    )
    parent = getattr(ch, "parent", None)
    parent_id = getattr(parent, "id", None) if parent else None

    if not is_forgemaster and not (is_thread and parent_id and parent_id in _b("ALLOWED_KT_FORUM_PARENT_IDS")):
        await interaction.response.send_message(
            "This command can only be used in your Kill Team forum post.",
            ephemeral=True,
        )
        return

    # Get caller's Kill Team name using shared resolution logic
    caller_kt_name = _b("_resolve_killteam_for_member")(caller)
    if caller_kt_name:
        caller_kt_name = caller_kt_name.lower()
    if not is_forgemaster and not caller_kt_name:
        await interaction.response.send_message("You must belong to a Kill Team to use this command.", ephemeral=True)
        return

    # Extract KT name from thread name and verify match (Forgemaster exempt)
    thread_name = getattr(ch, "name", "") or ""
    thread_kt = _b("_extract_killteam_name")(thread_name).lower() if thread_name else ""

    if not is_forgemaster and (not thread_kt or not (thread_kt in caller_kt_name or caller_kt_name in thread_kt)):
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
    for rank in _b("RANK_ROLES_PRIORITY"):
        for role in target.roles:
            if role.name == rank:
                current_rank = rank
                break
        if current_rank != "Watch Brother":
            break

    display_name = target.nick or target.display_name

    # Induction date (custom override or server join time)
    try:
        joined_at = _get_effective_induction_date(target)
        if joined_at:
            if joined_at.tzinfo is None:
                joined_at = joined_at.replace(tzinfo=timezone.utc)
            ja_utc = joined_at.astimezone(timezone.utc)
            days_since_join = (datetime.now(timezone.utc) - ja_utc).days
            joined_str = f"{ja_utc.strftime('%Y-%m-%d %H:%M %Z')} ({days_since_join}d ago)"
        else:
            joined_str = "Unknown"
    except Exception:
        joined_str = "Unknown"

    # Service studs (only for Watch Veteran+)
    MAX_STUDS = 16
    try:
        studs_count = 0
        idx_veteran = _b("_role_index")("Watch Veteran")
        highest_idx = _b("get_highest_rank_index")(target)
        if idx_veteran is not None and highest_idx is not None and highest_idx <= idx_veteran:
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
    trials_reported = _count_inductions_from_records(str(target.id), _g.DATASTORE.iter_records())

    # Home chapter
    try:
        chapters_map = await _resolve_home_chapters(guild, [str(target.id)])
        home_chapter = chapters_map.get(str(target.id), "REDACTED")
    except Exception:
        home_chapter = "REDACTED"

    # Active/Inactive status
    try:
        # Use cached last_aar_ts from user_stats_cache to avoid O(N) record scan
        cached_ts = _g.DATASTORE.get_user_stats(str(target.id)).get("last_aar_ts")
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
            "Huntmaster",
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
                kt_name = _b("_extract_killteam_name")(rn)
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
    for rp in _b("RANK_ROLES_PRIORITY"):
        if name_val.lower().startswith(rp.lower()):
            name_val = name_val[len(rp) :].lstrip()
            break
    name_val = re.sub(r"[●⚬]+", "", name_val).strip() or display_name

    # Get rank emoji
    rank_emoji = _b("_get_rank_emoji")(guild, current_rank) if guild else ""

    # Get chapter emoji
    chapter_emoji = (
        _b("_get_emoji_by_name")(guild, home_chapter)
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
    bearer_honorific, bearer_name, bearer_title = _b("_get_bearer_rank_and_title")(target)
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
        armor_state = await _b("_get_armor_state")(int(target.id))
        points_since_blessing = armor_state.get("points_since_blessing", 0)
        spirit_fractured = armor_state.get("spirit_fractured", False)
        armor_tier = _b("_get_member_damage_tier")(target)
        damage_probability = _b("_get_damage_probability")(points_since_blessing)
        _prob_percent = damage_probability * 100  # Calculated but not displayed in this context
        machine_spirit = await _b("_get_machine_spirit")(int(target.id))

        # Roll scan detection (same as armor_status)
        scan_result = await _b("_get_or_roll_scan_result")(
            int(target.id), armor_tier, points_since_blessing, spirit_fractured
        )
        scan_missed = not scan_result["detected"]

        if scan_missed:
            # Undetected - mask armor data
            embed.add_field(
                name="▸ Armor Integrity",
                value="⚫ **UNDETECTED** | Spirit: ???\nPenalty Risk: ???",
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
            machine_spirit_emoji = _b("_get_emoji_by_name")(guild, "MachineSpirit") or "⚙️"

            if spirit_fractured:
                spirit_display = f"{machine_spirit_emoji} SEVERED"
            elif machine_spirit:
                spirit_display = f"{machine_spirit_emoji} `{machine_spirit}` ({spirit_status})"
            else:
                spirit_display = f"{machine_spirit_emoji} *UNBOUND*"

            armor_lines = [f"{armor_icon} **{armor_status}** | {spirit_display}"]
            # Show penalty risk and cycles (hide cycles for nominal brothers)
            penalty_risk = _b("_get_tier_risk_display")(armor_tier, spirit_fractured)
            if armor_status == "NOMINAL":
                armor_lines.append(f"Penalty Risk: {penalty_risk}")
            else:
                armor_lines.append(f"Penalty Risk: {penalty_risk} | Cycles: {points_since_blessing}c")

            embed.add_field(
                name="▸ Armor Integrity",
                value="\n".join(armor_lines),
                inline=False,
            )
    except Exception:
        pass  # Skip armor field if data unavailable

    # ▸ Warp Sanction field (status only; raw exposure hidden from brothers)
    try:
        warp_state = await _get_warp_exposure_state(int(target.id))
        warp_points = int(warp_state.get("points_since_warding", 0) or 0)
        sanction_key = await _get_warp_sanction_status(warp_points, int(target.id))
        sanction_label, sanction_desc = WARP_SANCTION_STATUS.get(
            sanction_key,
            ("Cleansed", "Clear or minimal contamination detected."),
        )
        if warp_state.get("warp_corrupted"):
            sanction_label = f"{sanction_label} — CORRUPTED"
            sanction_desc = (
                "Warp corruption confirmed by repeated restricted-tier exposure. Void Warden intervention required."
            )
        embed.add_field(
            name="▸ Warp Sanction",
            value=f"🧿 **{sanction_label.upper()}**\n{sanction_desc}",
            inline=False,
        )
    except Exception:
        pass

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
                    emoji = _b("_get_emoji_by_name")(guild, emoji_hint)
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

    # Huntmaster Jack portrait
    _HUNTMASTER_JACK_ID = 1444810056821637133
    _jack_file = None
    if int(target.id) == _HUNTMASTER_JACK_ID:
        _jack_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "1444810056821637133_Huntmaster_Jack.png")
        if os.path.exists(_jack_path):
            _jack_file = discord.File(_jack_path, filename="1444810056821637133_Huntmaster_Jack.png")
            embed.set_thumbnail(url="attachment://1444810056821637133_Huntmaster_Jack.png")

    await interaction.followup.send(embed=embed, ephemeral=True, **({"file": _jack_file} if _jack_file else {}))
    now_mtd = datetime.utcnow()
    first_of_month = datetime(now_mtd.year, now_mtd.month, 1)
    mtd_span_days = max(1, (now_mtd - first_of_month).days)

    try:
        rankings = await _compute_fortress_rankings(guild, mtd_span_days, start_dt=first_of_month, end_dt=now_mtd)
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
    risk_data = individual_rankings.get("high_risk", {}).get(target_id, (0, 0, 0))
    black_laurels_data = individual_rankings.get("black_laurels", {}).get(target_id, (0, 0, 0))
    omega_kia_data = individual_rankings.get("omega_kia", {}).get(target_id, (0, 0, 0))

    # Chapter ranking
    ch_ops_data = chapter_rankings.get("ops", {}).get(home_chapter, (0, 0, 0))
    ch_avg_data = chapter_rankings.get("avg", {}).get(home_chapter, (0.0, 0, 0))
    ch_pres_data = chapter_rankings.get("pres", {}).get(home_chapter, (0, 0, 0))
    ch_armory_val = chapter_rankings.get("armory", {}).get(home_chapter, (0, 0, 0))[0]
    ch_gene_val = chapter_rankings.get("gene_carried", {}).get(home_chapter, (0, 0, 0))[0]
    ch_risk_data = chapter_rankings.get("high_risk", {}).get(home_chapter, (0, 0, 0))
    ch_aar_data = chapter_rankings.get("avg_aar_per_member", {}).get(home_chapter, (0.0, 0, 0))
    ch_omega_kia_data = chapter_rankings.get("omega_kia", {}).get(home_chapter, (0, 0, 0))

    # Kill team rankings
    target_killteams = []
    try:
        target_killteams = _b("_resolve_killteams_for_member")(target)
    except Exception:
        pass

    # Build Monthly Honours embed
    honours_embed = discord.Embed(
        title="᛭⋅ MONTHLY HONOURS ⋅᛭",
        description=f"*⌾ {target_name} ⌾*\nMonth to Date ({mtd_span_days} Days)",
        color=0x2ECC71,
    )

    # Individual distinctions
    if ops_data[2] > 0:
        omega_suffix = f" | KIA {int(omega_kia_data[0])}" if omega_kia_data[0] > 0 else ""
        indiv_value = (
            f"**Operations:** {int(ops_data[0])} (#{ops_data[1]}/{ops_data[2]})\n"
            f"**Avg Pts/Op:** {avg_data[0]:.1f} (#{avg_data[1]}/{avg_data[2]})\n"
            f"**Gene-seed:** {int(gene_data[0])} (#{gene_data[1]}/{gene_data[2]})\n"
            f"**Armory:** {int(armory_data[0])} (#{armory_data[1]}/{armory_data[2]})\n"
            f"**High-Risk:** {int(risk_data[0])}{omega_suffix} (#{risk_data[1]}/{risk_data[2]})\n"
            f"**Black Laurels:** {int(black_laurels_data[0])} (#{black_laurels_data[1]}/{black_laurels_data[2]})"
        )
        # Compute overall rank as median of individual rankings
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
        if individual_ranks:
            overall_rank = statistics.median(individual_ranks)
            indiv_value += f"\n**Overall Rank:** #{overall_rank:.1f}"
    else:
        indiv_value = "No ranking data available"
    honours_embed.add_field(name="▸ Individual Distinctions", value=indiv_value, inline=False)

    # Chapter distinctions
    chapter_emoji = (
        _b("_get_emoji_by_name")(guild, home_chapter)
        if guild and home_chapter and home_chapter not in ("Unknown", "REDACTED")
        else None
    )
    chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
    lineage_display = "REDACTED" if home_chapter == "Black Shield" else home_chapter

    if ch_ops_data[2] > 0:
        ch_omega_suffix = f" | KIA {int(ch_omega_kia_data[0])}" if ch_omega_kia_data[0] > 0 else ""
        ch_value = (
            f"**Operations:** {int(ch_ops_data[0])} (#{ch_ops_data[1]}/{ch_ops_data[2]})\n"
            f"**Avg Pts/Op:** {ch_avg_data[0]:.1f} (#{ch_avg_data[1]}/{ch_avg_data[2]})\n"
            f"**Armory + Gene:** (ArmoryPts {ch_armory_val:.1f} | GenePts {ch_gene_val:.1f}) — Rank #{ch_pres_data[1]}/{ch_pres_data[2]}\n"
            f"**High-Risk:** {int(ch_risk_data[0])}{ch_omega_suffix} (#{ch_risk_data[1]}/{ch_risk_data[2]})\n"
            f"**AARs/Member:** {ch_aar_data[0]:.1f} (#{ch_aar_data[1]}/{ch_aar_data[2]})"
        )
        # Compute overall rank as median of chapter rankings
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
        if chapter_ranks:
            ch_overall_rank = statistics.median(chapter_ranks)
            ch_value += f"\n**Overall Rank:** #{ch_overall_rank:.1f}"
    else:
        ch_value = "Below minimum threshold"
    honours_embed.add_field(name=f"▸ {chapter_prefix}{lineage_display} Chapter", value=ch_value, inline=False)

    # Kill Team distinctions
    for kt_n in target_killteams:
        kt_ops_data = team_rankings.get("ops", {}).get(kt_n, (0, 0, 0))
        kt_avg_data = team_rankings.get("avg", {}).get(kt_n, (0.0, 0, 0))
        kt_pres_data = team_rankings.get("pres", {}).get(kt_n, (0, 0, 0))
        kt_armory_val = team_rankings.get("armory", {}).get(kt_n, (0, 0, 0))[0]
        kt_gene_val = team_rankings.get("gene_carried", {}).get(kt_n, (0, 0, 0))[0]
        kt_risk_data = team_rankings.get("high_risk", {}).get(kt_n, (0, 0, 0))
        kt_aar_data = team_rankings.get("avg_aar_per_member", {}).get(kt_n, (0.0, 0, 0))
        kt_cohesion_data = team_rankings.get("cohesion", {}).get(kt_n, (0.0, 0, 0))
        kt_omega_kia_data = team_rankings.get("omega_kia", {}).get(kt_n, (0, 0, 0))

        if kt_ops_data[2] > 0:
            kt_omega_suffix = f" | KIA {int(kt_omega_kia_data[0])}" if kt_omega_kia_data[0] > 0 else ""
            kt_value = (
                f"**Operations:** {int(kt_ops_data[0])} (#{kt_ops_data[1]}/{kt_ops_data[2]})\n"
                f"**Avg Pts/Op:** {kt_avg_data[0]:.1f} (#{kt_avg_data[1]}/{kt_avg_data[2]})\n"
                f"**Armory + Gene:** (ArmoryPts {kt_armory_val:.1f} | GenePts {kt_gene_val:.1f}) — Rank #{kt_pres_data[1]}/{kt_pres_data[2]}\n"
                f"**High-Risk:** {int(kt_risk_data[0])}{kt_omega_suffix} (#{kt_risk_data[1]}/{kt_risk_data[2]})\n"
                f"**AARs/Member:** {kt_aar_data[0]:.1f} (#{kt_aar_data[1]}/{kt_aar_data[2]})\n"
                f"**Cohesion:** {kt_cohesion_data[0]:.1f}% (#{kt_cohesion_data[1]}/{kt_cohesion_data[2]})"
            )
            # Compute overall rank as median of kill team rankings
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
            if kt_ranks:
                kt_overall_rank = statistics.median(kt_ranks)
                kt_value += f"\n**Overall Rank:** #{kt_overall_rank:.1f}"
        else:
            kt_value = "No ranking data available"
        honours_embed.add_field(name=f"▸ {kt_n}", value=kt_value, inline=False)

    honours_embed.set_footer(text=f"᛭⋅ Imperial Date: {imperial_date} ⋅᛭")

    await interaction.followup.send(embed=honours_embed, ephemeral=True)


@_g.bot.tree.command(name="combat_bonds", description="Show top Combat Bonds (global or for a Brother).")
@app_commands.describe(
    brother="Optional: limit to bonds including this Brother.",
    window="Optional: number of days to include (default 30).",
)
async def combat_bonds(
    interaction: discord.Interaction,
    brother: Optional[discord.Member] = None,
    window: Optional[int] = None,
):
    if not (_b("check_command_permission")(interaction.user, "combat_bonds") and _b("is_allowed_channel")(interaction)):
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
        if _g.DATASTORE:
            cached = _g.DATASTORE.get_combat_cache(span_days)
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
        spreads = await asyncio.to_thread(_build_spread_counts, pair_counts, active_count=active_count)
    except Exception:
        spreads = _build_spread_counts(pair_counts, active_count=active_count)

    # Store in DataStore cache if available
    try:
        if _g.DATASTORE:
            await _g.DATASTORE.set_combat_cache(
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
                _g.logger.exception("combat_bonds: failed to send response or followup")
    else:
        target_id = str(brother.id)
        # Get pairwise bonds for the target brother
        personal_pairs = _select_personal_pair_bonds(pair_counts, target_id, max_n=5)
        # Resolve chapters for partners
        partner_uids = [uid for uid, _score in personal_pairs]
        chapters = await _resolve_home_chapters(interaction.guild, sorted(set(partner_uids)))
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
                _g.logger.exception("combat_bonds: failed to send response or followup")


def compute_stats_for_user(user_id: str):
    return _g.DATASTORE.get_user_stats(user_id)


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
        data = _b("load_aar_data")(AAR_RECORDS_PATH)
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
        effective_carried = status == "carried" or (gene_carrier is not None and status != "lost")

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
    data = _b("load_aar_data")(AAR_RECORDS_PATH)
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

    Eligible members must have one of the roles in _b('RANK_ROLES_PRIORITY') or
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
        if isinstance(total_members, int) and total_members > 0 and cached_members < total_members:
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
    qualifying_roles = set(_b("RANK_ROLES_PRIORITY")) | {"Watch Sister"}
    for uid in user_ids:
        try:
            member = guild.get_member(int(uid))
            if member is None:
                continue
            member_role_names = {getattr(r, "name", "") for r in getattr(member, "roles", [])}
            if any(r in member_role_names for r in qualifying_roles):
                eligible.add(str(uid))
        except Exception:
            continue
    return eligible


def _filter_pair_counts_by_eligible(
    pair_counts: Dict[Tuple[str, str], int], eligible_ids: set
) -> Dict[Tuple[str, str], int]:
    """Filter pair_counts to only include pairs where both members are eligible."""
    return {k: v for k, v in pair_counts.items() if k[0] in eligible_ids and k[1] in eligible_ids}


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

    Config knobs (_g.CONFIG.combat_bonds):
      - dominance_alpha (float, default 0.5): strength of dominance penalty [0..1]
      - min_pair (int, default 1): minimum pair count required to qualify
      - min_balance_ratio (float, default 0.0): require min(C)/max(C) >= ratio (0 disables)

    Returns list of ((id1, id2, id3), score:int) sorted by score desc.
    """
    # Load config with safe defaults
    try:
        _cb = _g.CONFIG.get("combat_bonds") or {}
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
        if (c_ab < float(min_pair)) or (c_ac < float(min_pair)) or (c_bc < float(min_pair)):
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
        _cb = _g.CONFIG.get("combat_bonds") or {}
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
    connected_bros = {b for b, conn_count in brother_connection_scores.items() if conn_count >= 2}
    uniq_bros = sorted([b for b in uniq_bros if b in connected_bros])

    # Further limit to top 50 most-connected brothers if the set is still large
    # This prevents O(n^5) blowup while keeping the most relevant bonds
    max_brothers_for_combos = 50
    if len(uniq_bros) > max_brothers_for_combos:
        sorted_by_connections = sorted(uniq_bros, key=lambda b: brother_connection_scores.get(b, 0), reverse=True)
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
            c_vals: List[float] = [float(pair_counts.get(k, 0) or 0.0) for k in pair_keys]
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


def _build_spread_counts(pair_counts: Dict[Tuple[str, str], int], active_count: Optional[int] = None):
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

    Optional config knobs (with safe defaults) from _g.CONFIG.combat_bonds:
      per_partner_cap (int, default 5), depth_exponent (float, default 0.5)
    """
    # Configurable knobs
    try:
        _cb = _g.CONFIG.get("combat_bonds") or {}
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
        active = int(active_count) if (active_count and int(active_count) > 0) else max(1, len(freqs))
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
        _cb = _g.CONFIG.get("combat_bonds") or {}
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


def _select_top_global_bonds(triples: List[Tuple[Tuple[str, str, str], int]], top_n: int = 3):
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


def _select_personal_bonds(triples: List[Tuple[Tuple[str, str, str], int]], target_id: str, max_n: int = 3):
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


async def _resolve_home_chapters(guild: Optional[discord.Guild], user_ids: List[str], limit: int = 500):
    """Resolve home chapters for given users by consulting their Guild roles.

    Logic: for each user id, attempt to get the corresponding Member from
    `guild`. Inspect the member's role names and match any canonical
    `_b('HOME_CHAPTERS')` entry case-insensitively (substring match). The first
    matching canonical name is returned. If no match is found or the member
    cannot be resolved, the value 'REDACTED' is used as a fallback.

    Returns a mapping of user_id -> chapter string.
    """
    home_chapters = _b("HOME_CHAPTERS")
    chapters: Dict[str, str] = {}
    if not guild:
        return chapters

    # Iterate requested users and resolve via member roles
    # Match strategy: exact (case-insensitive) equality between a member's
    # individual role names and the canonical `_b('HOME_CHAPTERS')` entries.
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
                    (getattr(r, "name", "") or "").strip() for r in member.roles if getattr(r, "name", None)
                }
                match = next(
                    (hc for hc in home_chapters if any(rn.lower() == hc.lower() for rn in member_role_names)),
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
    lines.append("\u001b[32m==============================================================================")
    lines.append("  WATCH FORTRESS JERICHO // COMBAT BONDS COGITATOR")
    lines.append("  SUB-ROUTINE: BATTLE-LITANY INDEX")
    lines.append("==============================================================================")
    if window_days is not None:
        lines.append(f"  Auspex Window: Last {window_days} day(s)")
    else:
        lines.append(f"  Auspex Window: Last {window_span} sanctioned engagement(s)")
    # Veneration key (compact) — per-bond output will include only the tier label
    lines.append("  Veneration Key: FRAGILE | FORMING | RELIABLE | STALWART | INDOMITABLE\n")
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

            # Resolve chapter from member roles by matching against _b('HOME_CHAPTERS')
            chap = None
            if member:
                try:
                    member_role_names = {
                        (getattr(r, "name", "") or "").strip() for r in member.roles if getattr(r, "name", None)
                    }
                    match = next(
                        (hc for hc in _b("HOME_CHAPTERS") if any(rn.lower() == hc.lower() for rn in member_role_names)),
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
    footer = "\n" + "==============================================================================" + "\n\u001b[0m```"

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
    lines.append("==============================================================================")
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
    window_text = f"Last {window_days} day(s)" if window_days is not None else f"Last {window_span} engagements"
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

    embed.set_footer(text="᛭⋅ These Combat Bonds may be invoked by decree of Watch Command. ⋅᛭")
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
            (getattr(r, "name", "") or "").strip() for r in target_member.roles if getattr(r, "name", None)
        }
        for rp in _b("RANK_ROLES_PRIORITY"):
            if rp in member_role_names:
                target_rank = rp
                break
        target_chapter = next(
            (hc for hc in _b("HOME_CHAPTERS") if any(rn.lower() == hc.lower() for rn in member_role_names)),
            None,
        )
    except Exception:
        pass

    # Strip rank prefix from name
    if target_rank:
        for rp in _b("RANK_ROLES_PRIORITY"):
            if target_name.lower().startswith(rp.lower()):
                target_name = target_name[len(rp) :].lstrip()
                break

    # Get emojis
    rank_emoji = _b("_get_rank_emoji")(guild, target_rank) if guild and target_rank else ""
    chapter_emoji = _b("_get_emoji_by_name")(guild, target_chapter) if guild and target_chapter else ""

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
                (getattr(r, "name", "") or "").strip() for r in member.roles if getattr(r, "name", None)
            }
            # Find member rank
            member_rank = None
            for rp in _b("RANK_ROLES_PRIORITY"):
                if rp in member_role_names:
                    member_rank = rp
                    break
            if member_rank:
                rank_emoji = _b("_get_rank_emoji")(guild, member_rank)
                # Strip rank prefix from name (case-insensitive)
                for rp in _b("RANK_ROLES_PRIORITY"):
                    if name.lower().startswith(rp.lower()):
                        name = name[len(rp) :].lstrip()
                        break
            # Resolve chapter
            chap = None
            try:
                match = next(
                    (hc for hc in _b("HOME_CHAPTERS") if any(rn.lower() == hc.lower() for rn in member_role_names)),
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
        chap_emoji = _b("_get_emoji_by_name")(guild, chap) if guild and chap else None
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

    embed.set_footer(text="᛭⋅ These Combat Bonds may be invoked by decree of Watch Command. ⋅᛭")
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
                        child.disabled = (self.current == "ansi") or (not self.text_content) or too_long
                    else:
                        # For public context, disable only if ANSI unavailable
                        child.disabled = (not self.text_content) or too_long
                elif child.custom_id == "show_embed":
                    # Only relevant in ephemeral context
                    child.disabled = (self.current == "embed") or (self.embed_obj is None)

    @discord.ui.button(label="PC/Console", style=discord.ButtonStyle.secondary, custom_id="show_ansi")
    async def show_ansi(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.text_content:
            try:
                await interaction.response.send_message("No PC/Console output available.", ephemeral=True)
            except Exception:
                pass
            return
        if len(self.text_content) > self._ansi_max_len:
            try:
                await interaction.response.send_message("PC/Console view exceeds message limit.", ephemeral=True)
            except Exception:
                pass
            return

        if self.ephemeral_context:
            # Toggle the message in place (for ephemeral messages)
            self.current = "ansi"
            self._update_buttons()
            try:
                await interaction.response.edit_message(content=self.text_content, embed=None, view=self)
            except Exception:
                try:
                    await interaction.followup.send("Unable to switch to PC/Console view.", ephemeral=True)
                except Exception:
                    pass
        else:
            # Send ANSI view as ephemeral message (for public messages)
            try:
                await interaction.response.send_message(content=self.text_content, ephemeral=True)
            except Exception:
                try:
                    await interaction.followup.send("Unable to show PC/Console view.", ephemeral=True)
                except Exception:
                    pass

    @discord.ui.button(label="Mobile", style=discord.ButtonStyle.primary, custom_id="show_embed")
    async def show_embed(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.embed_obj is None:
            await interaction.response.defer()
            return
        self.current = "embed"
        self._update_buttons()
        await interaction.response.edit_message(content=None, embed=self.embed_obj, view=self)


def _embed_from_ansi(title: str, text_block: str, color: int = 0x2ECC71) -> discord.Embed:
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
    "Lord Executioner",
    "Huntmaster",
    "High Chaplain",
    "Chief Apothecary",
    "Void Warden",
    "Forgemaster",
    "Castellan",
    "Venerable Dreadnought",
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

    if _g.DATASTORE is None:
        return {
            "individuals": {},
            "teams": {},
            "chapters": {},
            "imperial_date": _format_imperial_date(now),
        }

    # Collect records in window
    recs_in_window: List[tuple] = []
    all_user_ids: set = set()
    for rec in _g.DATASTORE.iter_records():
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
            names = _b("_canonical_role_names")(member)
            # Check if member has any rank in _b('RANK_ROLES_PRIORITY')
            if any(r in names for r in _b("RANK_ROLES_PRIORITY")):
                watch_brother_plus_ids.add(str(member.id))
    except Exception:
        pass

    # Process each record
    for ts, rec in recs_in_window:
        difficulty = rec.get("difficulty_class")
        is_high_risk = difficulty in ("hard_stratagem", "omega_ops")
        omega_kia = int(rec.get("killed_in_action", 0) or 0) if difficulty == "omega_ops" else 0
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
                    u["gene_carried"] += int(rec.get("gene_seed_base_points_for_carrier") or 0)
                u["gene_participated"] += 1
            except Exception:
                pass

        # Team aggregation: collect all teams participating in this AAR, then count once per team
        aar_teams: Dict[str, List[str]] = {}  # team -> list of member uids in this AAR
        resolved_participants = 0  # Count only resolved members for cohesion calculation
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
                member_teams = _b("_resolve_killteams_for_member")(member)
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
                    t["gene_carried"] += int(rec.get("gene_seed_base_points_for_carrier") or 0)
                t["gene_participated"] += 1
                # Track unique members who participated
                for uid in team_member_ids:
                    t["members"].add(str(uid))
            except Exception:
                pass

        # Chapter aggregation: collect all chapters participating in this AAR, then count once per chapter
        aar_chapters: Dict[str, List[str]] = {}  # chapter -> list of member uids in this AAR
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
                c["gene_carried"] += int(rec.get("gene_seed_base_points_for_carrier") or 0)
            c["gene_participated"] += 1
            # Track unique members for ops/member calculation
            for uid in chapter_member_ids:
                chapters_members.setdefault(ch, set()).add(str(uid))

    # Compute derived metrics for users
    for uid, v in users.items():
        v["avg"] = (v["points"] / v["ops"]) if v["ops"] else 0.0
        v["gene_rate"] = (v["gene_carried"] / v["gene_participated"]) if v["gene_participated"] else 0.0

    # Compute derived metrics for teams
    for tid, tv in teams.items():
        tv["avg"] = (tv["points"] / tv["ops"]) if tv["ops"] else 0.0
        tv["gene_rate"] = (
            (tv.get("gene_carried", 0) / tv.get("gene_participated", 1)) if tv.get("gene_participated", 0) else 0.0
        )
        members_count = len(tv.get("members") or set())
        tv["avg_aar_per_member"] = (tv["ops"] / members_count) if members_count else 0.0
        tv["pres"] = tv.get("armory", 0) + tv.get("gene_carried", 0)
        # Squad Cohesion: average cohesion % for ops where 2+ teammates ran together
        cohesion_count = tv.get("cohesion_count", 0)
        tv["cohesion"] = (tv.get("cohesion_sum", 0.0) / cohesion_count) if cohesion_count > 0 else 0.0

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
        if len(chapters_members.get(ch, set())) >= 1 and d.get("ops", 0) >= min_ops_required
    ]

    for ch, c in chapters.items():
        c["avg_armory"] = (c["armory"] / c["ops"]) if c["ops"] else 0.0
        c["avg_ops"] = (c["points"] / c["ops"]) if c["ops"] else 0.0
        c["avg"] = c["avg_ops"]  # Alias for consistency with kill teams
        c["pres"] = c["armory"] + c["gene_carried"]  # Combined preservation
        members_count = len(chapters_members.get(ch, set()))
        c["ops_per_member"] = (c["ops"] / members_count) if members_count else 0.0
        c["avg_aar_per_member"] = c["ops_per_member"]  # Alias for consistency
        c["gene_rate"] = (c["gene_carried"] / c["gene_participated"]) if c["gene_participated"] else 0.0

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
        eligible_users = {uid: v for uid, v in users.items() if v.get("ops", 0) >= min_ops} if min_ops > 0 else users
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
        eligible_teams = {tid: v for tid, v in teams.items() if v.get("ops", 0) >= min_ops} if min_ops > 0 else teams
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
        raw_vals = {ch: chapters.get(ch, {}).get(metric_key, 0) for ch in eligible_chapters}
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
        "avg_aar_per_member": rank_teams("avg_aar_per_member", min_ops=user_min_ops_required),
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
        # Normalize decorative unicode + strip stud pips in one pass
        name = _strip_display_name(display_name)
        # Strip [R] prefix for Reserves members
        if name.startswith("[R] "):
            name = name[4:].strip()
        # Get member's roles
        member_role_names = {(getattr(r, "name", "") or "").strip() for r in member.roles if getattr(r, "name", None)}
        # Find member's rank and strip rank prefix from name
        member_rank = None
        for rp in _b("RANK_ROLES_PRIORITY"):
            if rp in member_role_names:
                member_rank = rp
                break
        if member_rank:
            rank_emoji = _b("_get_rank_emoji")(guild, member_rank)
            # Strip the member's actual rank prefix from name (case-insensitive)
            if name.lower().startswith(member_rank.lower()):
                name = name[len(member_rank) :].lstrip()

        # Resolve chapter if requested
        if include_chapter:
            chap = None
            # Try from member roles first
            try:
                match = next(
                    (hc for hc in _b("HOME_CHAPTERS") if any(rn.lower() == hc.lower() for rn in member_role_names)),
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
                chapter_emoji = _b("_get_emoji_by_name")(guild, chap) or ""

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
        _g.logger.warning(f"Failed to load milestone tracking: {e}")
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
        _g.logger.exception(f"Failed to save milestone tracking: {e}")


def _calculate_current_milestones() -> dict:
    """Calculate current totals for all milestone categories from AAR records."""
    if _g.DATASTORE is None:
        return {}

    records = _g.DATASTORE.get_all_records()

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


def _check_milestone_thresholds(current: dict, last_announced: dict) -> list[tuple[str, int, int]]:
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
    return info.get(metric, ("MILESTONE", "An achievement has been reached", "Deathwatch", 0xC0C0C0))


def _build_milestone_embed(
    guild: discord.Guild,
    metric: str,
    milestone_value: int,
    current_value: int,
) -> discord.Embed:
    """Build an embed for a milestone announcement."""
    title, description, emoji_name, color = _get_milestone_display_info(metric)

    # Get emoji if available
    emoji = _b("_get_emoji_by_name")(guild, emoji_name)
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
    # (_g.LAST_MILESTONE_CHECK_DATE accessed via _g)
    try:
        if not MILESTONES_ENABLED:
            return

        if _g.DATASTORE is None:
            return

        # Use UTC for consistent scheduling
        now_utc = datetime.now(timezone.utc)
        today = now_utc.date()

        # Load tracking data early so the persisted last_check_date is the
        # source of truth for interval gating (survives bot restarts).
        tracking = _load_milestone_tracking()
        persisted_last_check = tracking.get("last_check_date")

        # Use persisted date preferentially; fall back to in-memory value
        last_check_str = persisted_last_check or _g.LAST_MILESTONE_CHECK_DATE
        if last_check_str:
            try:
                last_check = datetime.strptime(last_check_str, "%Y-%m-%d").date()
                days_since = (today - last_check).days
                if days_since < MILESTONES_CHECK_INTERVAL_DAYS:
                    return
            except Exception:
                pass  # If parsing fails, proceed with check

        _g.logger.info("Milestone check starting...")

        # Resolve target guild and channel
        guild = _b("_resolve_notification_guild")()
        if not guild:
            _g.logger.warning("Milestone check: Could not resolve guild, skipping")
            return

        try:
            channel = guild.get_channel(MILESTONES_CHANNEL_ID) or await _g.bot.fetch_channel(MILESTONES_CHANNEL_ID)
        except Exception:
            _g.logger.exception("Milestone check: Could not resolve channel")
            return

        last_announced = tracking.get("last_announced", {})

        # Calculate current totals
        current = _calculate_current_milestones()
        if not current:
            _g.logger.warning("Milestone check: Could not calculate current totals")
            return

        # Check for crossed milestones
        crossed = _check_milestone_thresholds(current, last_announced)

        if not crossed:
            _g.logger.info("Milestone check complete: no new milestones")
            _g.LAST_MILESTONE_CHECK_DATE = str(today)
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
                embed = _build_milestone_embed(guild, metric, milestone_value, current_value)
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
                _g.logger.exception(f"Failed to post milestone announcement for {metric}: {e}")

        # Save updated tracking
        tracking["last_announced"] = last_announced
        tracking["last_check_date"] = str(today)
        _save_milestone_tracking(tracking)

        _g.LAST_MILESTONE_CHECK_DATE = str(today)
        _g.logger.info(f"Milestone check complete: {announcements_sent} announcement(s) posted")

    except Exception as e:
        _g.logger.exception(f"Milestone check failed: {e}")


# ============================================================================
# ROSTER AUDIT COMMAND
# ============================================================================

# Static mappings for position labels to required roles
POSITION_LABEL_MAP = {
    "WatchMaster": "Watch Master",
    "LordExecutioner": "Lord Executioner",
    "Huntmaster": "Huntmaster",
    "ChiefApothecary": "Chief Apothecary",
    "HighChaplain": "High Chaplain",
    "Forgemaster": "Forgemaster",
    "VoidWarden": "Void Warden",
    "VenerableDreadnought": "Venerable Dreadnought",
    "HonoredDreadnought": "Honored Dreadnought",
    "InterredBrother": "Interred Brother",
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
            _g.logger.debug(f"Error fetching channel history: {e}")
            return None, None, []

        # Reverse to get oldest first, so indexing is intuitive
        messages.reverse()

        # Extract based on position
        high_cmd = messages[4] if len(messages) > 4 else None
        company_cmd = messages[5] if len(messages) > 5 else None
        kill_teams = messages[6:] if len(messages) > 6 else []

        _g.logger.debug(f"Found {len(messages)} messages in roster channel")
        _g.logger.debug(f"HC: msg {4}, CC: msg {5}, KTs: msgs {6}+")

        return high_cmd, company_cmd, kill_teams
    except Exception:
        _g.logger.exception("Error finding roster messages")
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
            if not line.strip() or "[Vacant]" in line or "###" in line or "⎯" in line or "__" in line:
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
        _g.logger.exception("Error parsing roster section")

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
        _g.logger.debug(f"Parsing {len(lines)} lines from kill teams section")
        _g.logger.debug(f"Content preview: {content[:300]}")
        current_kt_role_id = None
        current_kt_members = {}
        current_rank = "Member"  # Default rank for unlabeled members

        for line_num, line in enumerate(lines):
            line_stripped = line.strip()
            if not line_stripped:
                continue

            # Check if this is a Kill Team header (contains "###" and a role mention like <@&ID>)
            if "###" in line and "<@&" in line:
                _g.logger.debug(f"Line {line_num}: Possible KT header: {line[:100]}")
                # Extract role mention for kill team
                role_id = _extract_role_mention_from_text(line)
                if role_id:
                    _g.logger.info(f"Found KT role ID: {role_id} from line: {line[:80]}")
                    # Save previous team if exists
                    if current_kt_role_id is not None and current_kt_members:
                        kill_teams[current_kt_role_id] = current_kt_members
                    # Start new team
                    current_kt_role_id = role_id
                    current_kt_members = {}
                    current_rank = "Member"
                    continue
                else:
                    _g.logger.debug(f"Could not extract role ID from line: {line[:80]}")

            # Check if this is a rank marker (e.g., "**Sergeant:**" or "**Champion:**")
            if "**Sergeant:**" in line:
                current_rank = "Sergeant"
                _g.logger.debug(f"Line {line_num}: Switching to Sergeant rank (applies to next member only)")
                continue
            elif "**Champion:**" in line:
                current_rank = "Champion"
                _g.logger.debug(f"Line {line_num}: Switching to Champion rank (applies to next member only)")
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
                    _g.logger.debug(
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
                    _g.logger.debug(f"Line {line_num}: Empty rank slot, resetting to Member")
                    current_rank = "Member"

        # Save last team
        if current_kt_role_id is not None and current_kt_members:
            kill_teams[current_kt_role_id] = current_kt_members
            _g.logger.info(f"Saved KT {current_kt_role_id} with {len(current_kt_members)} members")
        _g.logger.debug(f"Finished parsing kill teams: {len(kill_teams)} teams found")
        if not kill_teams:
            _g.logger.debug("No kill teams found - checking if we ever entered a KT block")
    except Exception:
        _g.logger.exception("Error parsing kill teams section")

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
        company_command_role_id = int(company_config.get("companyCommandRoleId", 0) or 0)

        # Find roster messages
        high_cmd_msg, company_cmd_msg, kill_teams_msgs = await _find_roster_messages(guild, roster_channel_id)

        # Debug logging
        _g.logger.info(f"\n=== AUDITING {company_config.get('name', 'Unknown')} ===")
        _g.logger.info(f"Roster Channel ID: {roster_channel_id}")
        _g.logger.info(f"High Command Message: {high_cmd_msg.id if high_cmd_msg else 'NOT FOUND'}")
        _g.logger.info(f"Company Command Message: {company_cmd_msg.id if company_cmd_msg else 'NOT FOUND'}")
        _g.logger.info(f"Kill Teams Messages: {len(kill_teams_msgs)} found")

        # Parse Company Command and Kill Teams (High Command is passed in as shared)
        company_cmd_roster = _parse_roster_section(company_cmd_msg.content) if company_cmd_msg else {}

        # Parse all kill team messages and merge
        kill_teams_roster = {}
        for kt_msg in kill_teams_msgs:
            kt_data = _parse_kill_teams_section(kt_msg.content)
            kill_teams_roster.update(kt_data)

        # Debug logging
        _g.logger.info(f"=== AUDITING {company_config.get('name', 'Unknown')} ===")
        _g.logger.info(f"High Command members: {list(high_cmd_roster.keys())}")
        _g.logger.info(f"Company Command members: {list(company_cmd_roster.keys())}")
        _g.logger.info(f"Kill Teams roster dict: {kill_teams_roster}")
        if kill_teams_msgs:
            _g.logger.info(f"Kill Teams messages: {len(kill_teams_msgs)} messages")
            for kt_msg in kill_teams_msgs:
                _g.logger.info(f"  KT Message {kt_msg.id}: {len(kt_msg.content)} chars")
        for kt_id, kt_members in kill_teams_roster.items():
            kt_role = guild.get_role(kt_id)
            kt_name = kt_role.name if kt_role else f"KT-{kt_id}"
            _g.logger.info(f"Kill Team {kt_name} (ID: {kt_id}): {list(kt_members.keys())}")

        # Collect all users from this company's rosters (not including shared High Command)
        company_roster_users = set(company_cmd_roster.keys()) | {
            uid for kt in kill_teams_roster.values() for uid in kt.keys()
        }

        # All roster users for this company (High Command + company-specific)
        all_roster_users = set(high_cmd_roster.keys()) | company_roster_users
        _g.logger.info(f"Total roster users: {list(all_roster_users)}")

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
                is_valid, missing, extra = _validate_high_command_roles(expected_roles, actual_roles)
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
            company_role_obj = guild.get_role(company_role_id) if company_role_id else None
            company_cmd_role_obj = guild.get_role(company_command_role_id) if company_command_role_id else None

            # Build set of all kill team role IDs that exist
            kt_roles = set(kill_teams_roster.keys())

            # Scan all members with company role
            if company_role_obj:
                for member in company_role_obj.members:
                    if member.id not in all_roster_users:
                        # Check if they have kill team or company command roles
                        member_role_ids = {r.id for r in member.roles}
                        if company_cmd_role_obj and company_cmd_role_obj.id in member_role_ids:
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
                                guild.get_role(rid).name if guild.get_role(rid) else f"KT-{rid}"
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
            _g.logger.exception("Error checking for extra users")

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
            for company in (_g.CONFIG.get("companies") or {}).values():
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
        _g.logger.exception(f"Error auditing company {company_key}")

    return result


def _format_audit_summary(audit_results: List[Dict[str, any]]) -> str:
    """Format audit results as ANSI summary."""
    lines = []
    lines.append("```ansi")
    lines.append("\u001b[32m==============================================================================")
    lines.append("  WATCH FORTRESS JERICHO // ROSTER AUDIT — SUMMARY")
    lines.append("==============================================================================")

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

    if not any(r.get("missing") or r.get("extra") or r.get("mismatch") for r in audit_results):
        lines.append("")
        lines.append("  No discrepancies found.")

    lines.append("")
    lines.append("==============================================================================")
    lines.append("\u001b[0m```")

    return "\n".join(lines)


def _format_audit_full(audit_results: List[Dict[str, any]]) -> str:
    """Format audit results as full ANSI detail."""
    lines = []
    lines.append("```ansi")
    lines.append("\u001b[32m==============================================================================")
    lines.append("  WATCH FORTRESS JERICHO // ROSTER AUDIT — FULL REPORT")
    lines.append("==============================================================================")

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

    if not any(r.get("missing") or r.get("extra") or r.get("mismatch") for r in audit_results):
        lines.append("")
        lines.append("  No discrepancies found.")

    lines.append("")
    lines.append("==============================================================================")
    lines.append("\u001b[0m```")

    return "\n".join(lines)


@_g.bot.tree.command(
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
        _b("check_command_permission")(interaction.user, "promotion_queue") and _b("is_allowed_channel")(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild or _b("_resolve_notification_guild")()
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
    veteran_aar_not_time_met: List[Tuple[discord.Member, int, int, int]] = []  # member, aar_pts, weeks, aar_needed
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
        "Venerable Dreadnought",
        "Honored Dreadnought",
        "Forgemaster",
        "Void Warden",
        "High Chaplain",
        "Chief Apothecary",
        "Lord Executioner",
        "Huntmaster",
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

        # Get induction date and calculate weeks (supports override)
        joined_at = _get_effective_induction_date(member)
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

                next_stud_threshold_time = next_target * 4  # weeks needed for next milestone
                next_stud_threshold_aar = next_target * 400  # AAR needed for next milestone

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
                    days_until = weeks_needed * 7 - ((now - joined_at).days % 7) if joined_at else weeks_needed * 7
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
        return _format_member_styled(guild, str(member.id), chapters_map=None, include_chapter=False)

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
            lines.append(f"᛭⋅ {member_str}{arrow} | {aar_pts} AAR | needs **{aar_needed}**")
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
            lines.append(f"᛭⋅ {member_str}{arrow} | {aar_pts} AAR | {date_str}, +{aar_needed}")
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
            lines.append(f"᛭⋅ {member_str}{arrow} | →{target_str} | needs **{aar_needed}**")
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
            lines.append(f"᛭⋅ {member_str}{arrow} | →{target_str} | {date_str}, +{aar_needed}")
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

    # Save current positions for next comparison (merge with current on-disk state
    # under lock to avoid overwriting concurrent changes from _check_promotion_milestones)
    async with _g.PROMOTION_TRACKING_LOCK:
        fresh_tracking = _load_promotion_tracking()
        for uid, pos in veteran_positions.items():
            fresh_tracking.setdefault(uid, {})["veteran_position"] = pos
        for uid, pos in studs_positions.items():
            fresh_tracking.setdefault(uid, {})["studs_position"] = pos
        _save_promotion_tracking(fresh_tracking)

    await interaction.followup.send(embeds=embeds, ephemeral=True)


# TODO: include vacant command positions in output and whether or not there is an outstanding oath for that role. need to work on the oath parsing logic.
@_g.bot.tree.command(
    name="company_roster",
    description="Show Kill Teams and member counts for the entire Fortress.",
)
async def company_roster(interaction: discord.Interaction):
    """Show Kill Teams and their member counts for all Watch Companies."""
    # Permission check: Watch Command only, in the designated channel
    if not (
        _b("check_command_permission")(interaction.user, "company_roster") and _b("is_allowed_channel")(interaction)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    guild = interaction.guild or _b("_resolve_notification_guild")()
    if not guild:
        await interaction.response.send_message("Could not resolve guild.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    # All company names to iterate through
    all_companies = [
        "Watch Company Primus",
        "Watch Company Secundus",
        "Watch Company Tertius",
        "Watch Company Quartus",
        "Watch Company Quintus",
    ]

    # Command roles that are fine without a Kill Team assignment
    company_command_roles = {
        "Primus Command",
        "Secundus Command",
        "Tertius Command",
        "Quartus Command",
        "Quintus Command",
    }

    embeds = []
    fortress_total = 0
    fortress_in_kts = 0
    fortress_unassigned = 0

    for company in all_companies:
        # Find the company role
        company_role = discord.utils.get(guild.roles, name=company)
        if not company_role:
            continue

        # Find all members with this company role (excluding those above Sergeant)
        # Watch Sergeant and below are indices 16+ in _b('RANK_ROLES_PRIORITY')
        sergeant_idx = _b("_role_index")("Watch Sergeant")
        company_members = []
        for m in guild.members:
            if company_role not in m.roles or m.bot:
                continue
            # Get highest rank index
            highest_idx = _b("get_highest_rank_index")(m)
            # Include if no rank role, or if rank is Sergeant or below (higher index = lower rank)
            if highest_idx is None or (sergeant_idx is not None and highest_idx >= sergeant_idx):
                company_members.append(m)

        if not company_members:
            continue

        # Group members by their Kill Team role
        kt_counts: Dict[str, List[discord.Member]] = {}
        no_kt_members: List[discord.Member] = []

        for member in company_members:
            # Find member's kill team role from _b("ALLOWED_KT_ROLE_IDS")
            member_kt = None
            for role in member.roles:
                if role.id in _b("ALLOWED_KT_ROLE_IDS"):
                    member_kt = role.name
                    break

            if member_kt:
                kt_counts.setdefault(member_kt, []).append(member)
            else:
                # Check if member has a company command role - if so, they're fine
                has_command_role = any(role.name in company_command_roles for role in member.roles)
                if not has_command_role:
                    no_kt_members.append(member)

        # Sort kill teams by name
        sorted_kts = sorted(kt_counts.items(), key=lambda x: x[0])

        # Build embed for this company
        short_name = _extract_company_short_name(company)
        embed = discord.Embed(
            title=f"᛭⋅ {short_name.upper()} COMPANY ⋅᛭",
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

        # Summary for this company
        total_in_kts = sum(len(m) for m in kt_counts.values())
        embed.set_footer(
            text=f"᛭⋅ {total_in_kts} in Kill Teams | {len(no_kt_members)} unassigned | {len(company_members)} total ⋅᛭"
        )

        embeds.append(embed)
        fortress_total += len(company_members)
        fortress_in_kts += total_in_kts
        fortress_unassigned += len(no_kt_members)

    if not embeds:
        await interaction.followup.send("No companies found with members.", ephemeral=True)
        return

    # Add a summary embed at the end
    summary_embed = discord.Embed(
        title="᛭⋅ FORTRESS SUMMARY ⋅᛭",
        color=0xFFD700,
    )
    summary_embed.add_field(
        name="Total Marines",
        value=str(fortress_total),
        inline=True,
    )
    summary_embed.add_field(
        name="In Kill Teams",
        value=str(fortress_in_kts),
        inline=True,
    )
    summary_embed.add_field(
        name="Unassigned",
        value=str(fortress_unassigned),
        inline=True,
    )
    embeds.append(summary_embed)

    await interaction.followup.send(embeds=embeds, ephemeral=True)


# ---------------------------------------------------------------------------
# __all__: export all names needed by tests and by bot.py references.
# Must include underscore-prefixed names explicitly.
# ---------------------------------------------------------------------------

__all__ = [
    # ── Activity status ──────────────────────────────────────────────────────
    "_load_activity_status",
    "_save_activity_status",
    "_load_member_last_post_times",
    "_save_member_last_post_times",
    "_load_activity_status_last_check",
    "_check_activity_status_changes",
    "_send_activity_status_notification",
    "_handle_dreadnought_inactivity",
    "_activity_status_check_loop",
    # ── Induction / member helpers ───────────────────────────────────────────
    "_load_induction_overrides",
    "_save_induction_overrides",
    "_get_effective_induction_date",
    "_get_member_company_name",
    "_extract_company_short_name",
    "_orphan_companies_for_role",
    "_company_scope_ring",
    "_is_active_participant",
    "_WATCH_COMPANY_ROLE_NAMES",
    "_find_company_command_staff",
    "_find_kt_sergeant",
    "_find_all_captains_and_lieutenants",
    "_find_watch_master",
    "_get_member_display_name",
    "_get_member_rank_role",
    # ── Promotion milestones ─────────────────────────────────────────────────
    "_load_promotion_tracking",
    "_save_promotion_tracking",
    "_check_award_milestones_for_members",
    "_enforce_challenge_grace_periods",
    "_check_promotion_milestones",
    # ── Home chapter rotation ────────────────────────────────────────────────
    "_month_key_for_offset",
    "_load_home_chapter_rotation",
    "_save_home_chapter_rotation",
    "_get_saturdays_for_month",
    "_select_home_chapters_for_month",
    "ROTATION_STATE_PATH",
    # ── Deeds / stats ────────────────────────────────────────────────────────
    "_get_missions_last_days",
    "_get_eligible_combat_bonds_ids",
    "_filter_pair_counts_by_eligible",
    "_build_pair_counts",
    "_build_triple_bonds",
    "_build_group_bonds",
    "_build_spread_counts",
    "_select_top_global_bonds",
    "_select_personal_bonds",
    "_select_personal_pair_bonds",
    "_bond_tier",
    "_percentile",
    "_compute_bond_cutoffs",
    "_bond_tier_dynamic",
    "_resolve_home_chapters",
    "_format_bonds_for_discord",
    "_format_bonds_embed",
    "_format_personal_bonds_jericho_embed",
    "_embed_from_ansi",
    "_compute_fortress_rankings",
    "_parse_iso8601_to_utc",
    "_format_member_styled",
    "_format_imperial_date",
    "_forum_post_autocomplete",
    "_induction_count_for_user",
    "_count_inductions_from_records",
    # ── Milestone announcements ──────────────────────────────────────────────
    "_load_milestone_tracking",
    "_save_milestone_tracking",
    "_calculate_current_milestones",
    "_check_milestone_thresholds",
    "_get_milestone_display_info",
    "_build_milestone_embed",
    "_scheduled_milestone_check",
    # ── Roster audit ─────────────────────────────────────────────────────────
    "_extract_mentions_from_text",
    "_extract_role_mention_from_text",
    "_extract_position_label",
    "_find_roster_messages",
    "_parse_roster_section",
    "_parse_kill_teams_section",
    "_get_user_roles_by_id",
    "_validate_high_command_roles",
    "_validate_company_command_roles",
    "_validate_kill_team_member_roles",
    "_audit_company_roster",
    "_format_audit_summary",
    "_format_audit_full",
    "_parse_iso_ts_to_utc_naive",
    # ── Award announcement queue ─────────────────────────────────────────────
    "_load_award_queue",
    "_save_award_queue",
    "_enqueue_award_announcement",
    "_dm_award_failure",
    "_award_announcement_dispatch_loop",
    # ── Public names ─────────────────────────────────────────────────────────
    "HIGH_COMMAND_ROLES",
    "BATTLE_LINE_ORDER",
    "CHAMPION_ROLES",
    "SPECIALIST_ROLES",
    "POSITION_LABEL_MAP",
    "ToggleFormatView",
    # ── Public command functions ──────────────────────────────────────────────
    "litany_of_function",
    "requeue_award",
    "pick_home_chapters",
    "tally_deeds",
    "my_deeds",
    "combat_bonds",
    "promotion_queue",
    "company_roster",
    # ── Public stats/data functions ───────────────────────────────────────────
    "compute_stats_for_user",
    "compute_stats_for_user_in_records",
]
