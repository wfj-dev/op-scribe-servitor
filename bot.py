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
import time
from logging.handlers import RotatingFileHandler
import signal
import argparse
import statistics
import tempfile
from pathlib import Path

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
        await channel.send(content, allowed_mentions=discord.AllowedMentions(roles=True))
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


def _resolve_company_command_members(company: discord.Role) -> List[discord.Member]:
    """Return Company Command members for a company using consistent rules:
    - Strictly higher than Sergeant (exclude Sergeant and below)
    - Up to Captain (include Watch Lieutenant and Watch Captain)
    - Include specialist orders and Company Champion
    - Exclude Lord Executioner
    Applies across briefs for consistency.
    """
    try:
        idx_sergeant = _role_index("Watch Sergeant")
        idx_captain = _role_index("Watch Captain")
    except Exception:
        idx_sergeant = None
        idx_captain = None
    members: List[discord.Member] = []
    try:
        base_members = list(getattr(company, "members", []))
    except Exception:
        base_members = []
    excluded_roles = {
        "Lord Executioner",
        "High Chaplain",
        "Forgemaster",
        "Void Warden",
        "Voidwarden",
        "Chief Apothecary",
    }
    for m in base_members:
        try:
            names = _canonical_role_names(m)
            # Explicit exclusion of high-command roles
            if any(er in names for er in excluded_roles):
                continue
            highest_idx = get_highest_rank_index(m)
            if (
                idx_sergeant is not None
                and idx_captain is not None
                and highest_idx is not None
                and (idx_captain <= highest_idx <= idx_sergeant)
            ):
                members.append(m)
        except Exception:
            continue
    return members


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


def can_reconcile_records(user: discord.User | discord.Member):
    # Only Watch Master and Forgemaster, or whitelisted user IDs for these rites
    admin_ids = set(str(x) for x in (CONFIG.get("admin_user_ids") or []))
    uid = str(getattr(user, "id", None))
    if uid in admin_ids:
        return True

    perms = CONFIG.get("permissions", {}) or {}
    roles_union: set[str] = set()
    ids_union: set[str] = set()
    for key in ("reconcile_records", "sanctify_battle_records", "audit_archive_discrepancies"):
        block = perms.get(key, {}) or {}
        for r in (block.get("roles") or []):
            roles_union.add(str(r))
        for i in (block.get("user_ids") or []):
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


def _resolve_company_roles_from_text(guild: Optional[discord.Guild], text: str) -> List[discord.Role]:
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


def _build_command_brief_text(guild: discord.Guild, company: discord.Role, span_days: int = 7) -> Optional[str]:
    """Generate the ANSI text content for Command Brief without sending messages."""
    try:
        recent_records = _get_missions_last_days(span_days)
        company_ids: set[str] = {
            str(getattr(m, "id", ""))
            for m in getattr(company, "members", [])
            if getattr(m, "id", None)
        }
        has_company_records = False
        for rec in recent_records:
            bros = [str(b) for b in (rec.get("brother_ids") or [])]
            if set(bros) & company_ids:
                has_company_records = True
                break
        if not has_company_records:
            return None

        killteam_roles: List[discord.Role] = []
        for role in getattr(guild, "roles", []):
            rn = getattr(role, "name", "") or ""
            rl = rn.lower()
            if ("kill" in rl) and ("team" in rl) and ("champion" not in rl):
                killteam_roles.append(role)

        teams: List[dict] = []
        for kt in killteam_roles:
            kt_members = [
                m for m in getattr(kt, "members", []) if company in getattr(m, "roles", [])
            ]
            member_ids = {
                str(getattr(m, "id", "")) for m in kt_members if getattr(m, "id", None)
            }
            if not member_ids:
                continue
            teams.append({
                "role": kt,
                "name": _extract_killteam_name(getattr(kt, "name", "Unknown")),
                "member_ids": member_ids,
                "count": len(member_ids),
            })

        company_command_members: List[discord.Member] = _resolve_company_command_members(company)
        if len(company_command_members) > 0:
            member_ids = {
                str(getattr(m, "id", "")) for m in company_command_members if getattr(m, "id", None)
            }
            teams.append({
                "role": None,
                "name": "Company Command",
                "member_ids": member_ids,
                "count": len(member_ids),
            })

        team_stats: List[dict] = []
        for team in teams:
            mids = team["member_ids"]
            ops_count = 0
            aar_vals: List[float] = []
            armory_vals: List[float] = []
            gene_vals: List[float] = []
            waves_vals: List[float] = []
            total_scores: List[float] = []
            per_capita_vals: List[float] = []

            for rec in recent_records:
                bros = [str(b) for b in (rec.get("brother_ids") or [])]
                participants_in_team = sum(1 for b in bros if b in mids)
                if participants_in_team <= 0:
                    continue

                ops_count += 1
                aar = float(rec.get("points_for_op", 0) or 0)
                armory = float(rec.get("armory_challenge_points", rec.get("armory_data", 0) or 0) or 0)
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
                try:
                    dclass = (rec.get("difficulty_class") or "").lower()
                    if "siege" in dclass:
                        waves_vals.append(float(rec.get("waves", 0) or 0))
                except Exception:
                    pass
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
            reliability = _mean(total_scores) / (1.0 + _pstdev(total_scores)) if total_scores else 0.0
            force_multiplier = _mean(per_capita_vals)

            team_stats.append({
                "role": team["role"],
                "name": team["name"],
                "count": team["count"],
                "avg_ops": float(ops_count),
                "avg_aar": avg_aar,
                "avg_gene": avg_gene,
                "avg_armory": avg_armory,
                "avg_waves": avg_waves,
                "reliability": reliability,
                "force_multiplier": force_multiplier,
            })

        if not team_stats:
            return None

        def _winner(key: str):
            return max(team_stats, key=lambda t: t.get(key, 0.0))

        best_lethality = _winner("avg_aar")
        best_tempo = _winner("avg_ops")
        best_siegebreaker = _winner("avg_waves")
        best_reliability = _winner("reliability")
        best_force = _winner("force_multiplier")

        aar_list = [t["avg_aar"] for t in team_stats]
        gene_list = [t["avg_gene"] for t in team_stats]
        armory_list = [t["avg_armory"] for t in team_stats]
        med_aar = statistics.median(aar_list) if aar_list else 0.0
        med_gene = statistics.median(gene_list) if gene_list else 0.0
        med_armory = statistics.median(armory_list) if armory_list else 0.0
        try:
            sd_aar = statistics.pstdev(aar_list) if len(aar_list) >= 2 else 0.0
        except Exception:
            sd_aar = 0.0
        try:
            sd_gene = statistics.pstdev(gene_list) if len(gene_list) >= 2 else 0.0
        except Exception:
            sd_gene = 0.0
        try:
            sd_arm = statistics.pstdev(armory_list) if len(armory_list) >= 2 else 0.0
        except Exception:
            sd_arm = 0.0

        def _quad_label(code: str) -> str:
            mapping = {
                "HH": "Sanctifier",
                "HL": "Purgator",
                "LH": "Conservator",
                "LL": "Dormant",
            }
            return mapping.get(code, code)

        def _quad_code(z_aar: float, z_pres: float) -> str:
            return ("H" if z_aar >= 0 else "L") + ("H" if z_pres >= 0 else "L")

        def _quad_score(z_aar: float, z_pres: float, code: str, dx: float, dp: float) -> float:
            xa = z_aar if sd_aar > 0.0 else dx
            xp = z_pres if (sd_gene > 0.0 or sd_arm > 0.0) else dp
            if code == "HH":
                return max(xa, 0.0) + max(xp, 0.0)
            if code == "HL":
                return max(xa, 0.0) + max(-xp, 0.0)
            if code == "LH":
                return max(-xa, 0.0) + max(xp, 0.0)
            return max(-xa, 0.0) + max(-xp, 0.0)

        risk_profiles: List[Tuple[dict, str, float, float, float]] = []
        for t in team_stats:
            dx = (float(t.get("avg_aar", 0.0) or 0.0) - med_aar)
            dy = (float(t.get("avg_gene", 0.0) or 0.0) - med_gene)
            dz = (float(t.get("avg_armory", 0.0) or 0.0) - med_armory)
            z_aar = (dx / sd_aar) if sd_aar > 0.0 else 0.0
            z_gene = (dy / sd_gene) if sd_gene > 0.0 else 0.0
            z_arm = (dz / sd_arm) if sd_arm > 0.0 else 0.0
            z_pres = 0.6 * z_gene + 0.4 * z_arm
            dp = 0.6 * dy + 0.4 * dz
            code = _quad_code(z_aar, z_pres)
            score = _quad_score(z_aar, z_pres, code, dx, dp)
            risk_profiles.append((t, code, score, dx, dp))

        def _team_label(team: dict) -> str:
            name = team.get("name", "Unknown")
            return "Company Command" if name == "Company Command" else f"Kill Team {name}"

        def _fmt_delta(val: float) -> str:
            try:
                return f"{val:+.2f}"
            except Exception:
                return str(val)

        lines: List[str] = []
        lines.append("```ansi")
        lines.append("\u001b[32m==============================================================================")
        lines.append("  WATCH FORTRESS JERICHO // COMMAND BRIEF")
        lines.append("  OPERATION-SCRIBE SERVITOR — COMMAND BRIEF")
        lines.append("==============================================================================")
        lines.append(f"  {getattr(company, 'name', 'Unknown')}  |  Window: Last {span_days} Days")
        lines.append("------------------------------------------------------------------------------")
        kv_lines: List[Tuple[str, str]] = []
        kv_lines.append(("Veteran Lethality Index", f"{_team_label(best_lethality)}  (Avg AAR: {best_lethality['avg_aar']:.2f})"))
        kv_lines.append(("Operational Tempo", f"{_team_label(best_tempo)}  (Ops: {int(best_tempo['avg_ops'])})"))
        kv_lines.append(("Siegebreaker Rating", f"{_team_label(best_siegebreaker)}  (Avg Waves: {best_siegebreaker['avg_waves']:.2f})"))
        kv_lines.append(("Kill Team Reliability Index", f"{_team_label(best_reliability)}  (Score: {best_reliability['reliability']:.2f})"))
        EPS = 0.05
        for t, code, score, dx, dp in risk_profiles:
            disp_label = _quad_label(code)
            try:
                if abs(dx) < EPS and abs(dp) < EPS:
                    disp_label = "Orthodox"
            except Exception:
                pass
            kv_lines.append((f"Risk Appetite — {_team_label(t)}", f"{disp_label}  (AAR Δ {_fmt_delta(dx)} | Pres* Δ {_fmt_delta(dp)}; Index {score:.2f})"))
        kv_lines.append(("Force Multiplier Rating", f"{_team_label(best_force)}  (Avg AAR/Member: {best_force.get('force_multiplier', 0.0):.2f})"))
        try:
            label_width = max((len(k) for k, _ in kv_lines), default=0)
        except Exception:
            label_width = 0
        for k, v in kv_lines:
            lines.append(f"  {k:<{label_width}} :: {v}")
        lines.append("------------------------------------------------------------------------------")
        lines.append("  High Command Notes:")
        lines.append("  + See live brief for detailed assessment.")
        lines.append("==============================================================================")
        lines.append("\u001b[0m```")
        return "\n".join(lines)
    except Exception:
        return None


def _build_techmarine_brief_text(guild: discord.Guild, company: discord.Role, span_days: int = 7) -> Optional[str]:
    """Generate ANSI text for Techmarine Brief (materiel recovery) without sending."""
    try:
        recent_records = _get_missions_last_days(span_days)
        # Collect killteams within company
        killteam_roles: List[discord.Role] = []
        for role in getattr(guild, "roles", []):
            rn = getattr(role, "name", "") or ""
            rl = rn.lower()
            if ("kill" in rl) and ("team" in rl) and ("champion" not in rl):
                killteam_roles.append(role)
        teams: List[dict] = []
        for kt in killteam_roles:
            kt_members = [m for m in getattr(kt, "members", []) if company in getattr(m, "roles", [])]
            mids = {str(getattr(m, "id", "")) for m in kt_members if getattr(m, "id", None)}
            if mids:
                teams.append({"name": _extract_killteam_name(getattr(kt, "name", "Unknown")), "member_ids": mids})
        # Company Command synthetic
        cc_members = _resolve_company_command_members(company)
        if cc_members:
            mids = {str(getattr(m, "id", "")) for m in cc_members if getattr(m, "id", None)}
            teams.append({"name": "Company Command", "member_ids": mids})

        # Per-team stats (various armory metrics)
        per_team_yield: List[Tuple[str, float]] = []
        per_team_points_avg: Dict[str, float] = {}
        per_team_consistency: List[Tuple[str, float, float]] = []
        per_team_ops_count: Dict[str, int] = {}
        for team in teams:
            name = team["name"]
            mids = set(team["member_ids"])
            arm_vals: List[float] = []
            pts_vals: List[float] = []
            ops = 0
            for rec in recent_records:
                bros = [str(b) for b in (rec.get("brother_ids") or [])]
                if not (set(bros) & mids):
                    continue
                arm = rec.get("armory_data")
                try:
                    arm_val = float(arm) if arm is not None else None
                except Exception:
                    arm_val = None
                if arm_val is not None:
                    arm_vals.append(arm_val)
                    ops += 1
                try:
                    pts = float(compute_armory_bonus_points(rec.get("difficulty_class"), arm))
                except Exception:
                    pts = 0.0
                pts_vals.append(pts)
            avg_yield = (sum(arm_vals) / len(arm_vals)) if arm_vals else 0.0
            avg_pts = (sum(pts_vals) / len(pts_vals)) if pts_vals else 0.0
            sd = statistics.pstdev(arm_vals) if len(arm_vals) >= 2 else 0.0
            per_team_yield.append((name, avg_yield))
            per_team_points_avg[name] = avg_pts
            per_team_consistency.append((name, avg_yield, sd))
            per_team_ops_count[name] = int(ops)

        # Bests and totals
        best_yield = max(per_team_yield, key=lambda t: t[1]) if per_team_yield else None
        best_points = max(per_team_points_avg.items(), key=lambda kv: kv[1]) if per_team_points_avg else None
        best_consistency = None
        if per_team_consistency:
            # lower SD is steadier -> best consistency is min SD
            best_consistency = min(per_team_consistency, key=lambda t: t[2])
        # Concentration and median
        total_all = 0.0
        share_by_team: Dict[str, float] = {}
        yield_vals = []
        for name, avg_y in per_team_yield:
            share_by_team[name] = avg_y
            yield_vals.append(avg_y)
            total_all += avg_y
        top_share = None
        if share_by_team:
            top_share = max(((n, v, total_all, v) for n, v in share_by_team.items()), key=lambda t: t[1])
        best_median = None
        if yield_vals:
            med = statistics.median(yield_vals)
            # team closest to median
            best_median = min(per_team_yield, key=lambda t: abs(t[1] - med))
        # High-Value salvage frequency
        hv_threshold = None
        try:
            hv_threshold = statistics.quantiles(yield_vals, n=4)[2] if len(yield_vals) >= 4 else (statistics.median(yield_vals) if yield_vals else 0.0)
        except Exception:
            hv_threshold = statistics.median(yield_vals) if yield_vals else 0.0
        best_hv = None
        if hv_threshold is not None:
            best_hv = None
            # Simplified HV frequency: team with highest avg_pts considered
            if per_team_points_avg:
                hv_name, hv_val = max(per_team_points_avg.items(), key=lambda kv: kv[1])
                hv_cnt = per_team_ops_count.get(hv_name, 0)
                hv_tot = sum(per_team_ops_count.values()) or 1
                hv_rate = (hv_cnt / float(hv_tot)) if hv_tot else 0.0
                best_hv = (hv_name, hv_cnt, hv_tot, hv_rate)

        def _label(name: str) -> str:
            return name if name == "Company Command" else f"KT {name}"

        lines: List[str] = []
        lines.append("```ansi")
        lines.append("\u001b[32m==============================================================================")
        lines.append("  WATCH FORTRESS JERICHO // TECHMARINE BRIEF")
        lines.append("  OPERATION-SCRIBE SERVITOR — ARMORY RECOVERY LEDGER")
        lines.append("==============================================================================")
        lines.append(f"  {getattr(company, 'name', 'Unknown')}  |  Window: Last {span_days} Days")
        lines.append("------------------------------------------------------------------------------")
        if best_yield:
            lines.append(f"  Armory Yield Efficiency    :: {_label(best_yield[0])}  (Avg Armory Data: {best_yield[1]:.2f})")
        else:
            lines.append("  Armory Yield Efficiency    :: —")
        if best_points:
            lines.append(f"  Risk-Adjusted Armory Yield :: {_label(best_points[0])}  (Avg Points: {best_points[1]:.2f})")
        else:
            lines.append("  Risk-Adjusted Armory Yield :: —")
        if best_consistency:
            lines.append(f"  Armory Consistency Index   :: {_label(best_consistency[0])}  (SD: {best_consistency[2]:.2f} — lower indicates steadier recovery)")
        else:
            lines.append("  Armory Consistency Index   :: —")
        if top_share and total_all > 0:
            percent = (top_share[3] / total_all) * 100.0
            lines.append(f"  Materiel Concentration     :: {_label(top_share[0])}  (Share: {percent:.0f}%)")
        else:
            lines.append("  Materiel Concentration     :: —")
        if best_median:
            lines.append(f"  Typical Salvage Yield      :: {_label(best_median[0])}  (Median: {best_median[1]:.2f})")
        else:
            lines.append("  Typical Salvage Yield      :: —")
        if best_hv:
            hv_name, hv_cnt, hv_tot, hv_rate = best_hv
            lines.append(f"  High-Value Salvage Freq.   :: {_label(hv_name)}  (Rate: {hv_rate*100:.0f}% of {hv_tot})")
        else:
            lines.append("  High-Value Salvage Freq.   :: —")
        lines.append("==============================================================================")
        lines.append("\u001b[0m```")
        return "\n".join(lines)
    except Exception:
        return None


def _build_librarian_brief_text(guild: discord.Guild, company: discord.Role, span_days: int = 7) -> Optional[str]:
    """Generate ANSI text for Librarian Brief without sending."""
    try:
        # Reuse core of librarian_brief rendering without embeds
        killteam_roles: List[discord.Role] = []
        for r in getattr(guild, "roles", []):
            n = (getattr(r, "name", "") or "").strip()
            if re.search(r"(?i)^\s*kill\s*team", n):
                if n.lower() == "kill team champion" or re.search(r"(?i)kill\s*team\s*champion", n):
                    continue
                killteam_roles.append(r)
        company_members: List[discord.Member] = [m for m in getattr(company, "members", [])]
        company_ids: set[str] = {str(getattr(m, "id", "")) for m in company_members if getattr(m, "id", None)}
        span_days = span_days if (isinstance(span_days, int) and span_days > 0) else 7
        recent_records = _get_missions_last_days(span_days)
        has_company_records = False
        for rec in recent_records:
            bros = [str(b) for b in (rec.get("brother_ids") or [])]
            if set(bros) & company_ids:
                has_company_records = True
                break
        if not has_company_records:
            return None
        teams: List[Tuple[str, List[discord.Member]]] = []
        for kt in killteam_roles:
            kt_members = [m for m in getattr(kt, "members", []) if str(getattr(m, "id", "")) in company_ids]
            if kt_members:
                teams.append((getattr(kt, "name", "Kill Team"), kt_members))
        company_command_members = _resolve_company_command_members(company)
        if company_command_members:
            teams.append(("Company Command", company_command_members))
        lines: List[str] = []
        lines.append("```ansi")
        lines.append("\u001b[32m==============================================================================")
        lines.append("  WATCH FORTRESS JERICHO // LIBRARIUS OPERATIONAL BRIEF")
        lines.append("  OPERATION-SCRIBE SERVITOR — COMPANY DOCTRINAL DOSSIER")
        lines.append("==============================================================================")
        lines.append(f"  {getattr(company, 'name', 'Unknown')}  |  Window: Last {span_days} Days")
        lines.append("------------------------------------------------------------------------------")
        # For brevity, include a concise saturation/cohesion summary
        company_doc_counts: Counter[str] = Counter()
        company_missions_seen: set[str] = set()
        for rec in recent_records:
            bros: List[str] = [str(b) for b in (rec.get("brother_ids") or [])]
            if not (set(bros) & company_ids):
                continue
            _env, doc_tags, canon_mission = _tags_for_record(rec)
            for d in doc_tags:
                company_doc_counts[d] += 1
            if canon_mission:
                company_missions_seen.add(canon_mission)
        total_doc = sum(company_doc_counts.values())
        if total_doc > 0:
            top_doc, top_cnt = max(company_doc_counts.items(), key=lambda kv: (kv[1], kv[0]))
            top_share_pct = 100.0 * (top_cnt / float(total_doc))
            coh_tier = _cohesion_concentration_tier(top_share_pct)
            lines.append(f"  Cohesion Trend               :: {coh_tier} (Top Doctrine Share: {top_share_pct:.0f}%)")
        else:
            lines.append("  Cohesion Trend               :: UNDETERMINED")
        lines.append(f"  Mission Exposure             :: {_operational_exposure_tier(len(company_missions_seen))}")
        lines.append("==============================================================================")
        lines.append("\u001b[0m```")
        return "\n".join(lines)
    except Exception:
        return None


def _build_apothecary_brief_text(guild: discord.Guild, company: discord.Role, span_days: int = 7) -> Optional[str]:
    """Generate ANSI text for Apothecary Brief without sending."""
    try:
        # Minimal reuse of apothecary_brief core to compute readiness
        killteam_roles: List[discord.Role] = []
        for role in getattr(guild, "roles", []):
            rn = getattr(role, "name", "") or ""
            rl = rn.lower()
            if ("kill" in rl) and ("team" in rl) and ("champion" not in rl):
                killteam_roles.append(role)
        company_members: List[discord.Member] = [m for m in getattr(company, "members", [])]
        company_ids: set[str] = {str(getattr(m, "id", "")) for m in company_members if getattr(m, "id", None)}
        teams: List[Tuple[str, List[discord.Member]]] = []
        for kt in killteam_roles:
            kt_members = [m for m in getattr(kt, "members", []) if str(getattr(m, "id", "")) in company_ids]
            if kt_members:
                teams.append((_extract_killteam_name(getattr(kt, "name", "Unknown")), kt_members))
        company_command_members: List[discord.Member] = _resolve_company_command_members(company)
        span_days = span_days if (isinstance(span_days, int) and span_days > 0) else 7
        recent_records = _get_missions_last_days(span_days)
        has_company_records = False
        for rec in recent_records:
            bros = [str(b) for b in (rec.get("brother_ids") or [])]
            if set(bros) & company_ids:
                has_company_records = True
                break
        if not has_company_records:
            return None
        active_map: Dict[str, bool] = {}
        for rec in recent_records:
            for uid in rec.get("brother_ids") or []:
                sid = str(uid)
                if sid:
                    active_map[sid] = True

        def _absence_stats(members: List[discord.Member]):
            measures: List[int] = []
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
            sd = statistics.pstdev(measures) if n > 1 else 0.0
            return {"count": n, "active": active_cnt, "absent": n - active_cnt, "avg": avg, "median": med, "stdev": sd}

        stats_cmd = _absence_stats(company_command_members)
        lines: List[str] = []
        lines.append("```ansi")
        lines.append("\u001b[32m==============================================================================")
        lines.append("  WATCH FORTRESS JERICHO // APOTHECARION NODE")
        lines.append(f"  OPERATION-SCRIBE SERVITOR — BIOLOGICAL READINESS LEDGER ({span_days} DAYS)")
        lines.append("==============================================================================")
        lines.append(f"  {getattr(company, 'name', 'Unknown')}  |  Window: Last {span_days} Days")
        lines.append("------------------------------------------------------------------------------")
        cc_ready = "CRITICAL"
        cc_stab = "UNDETERMINED"
        try:
            def _select_readiness_tier(s: Dict[str, float]) -> str:
                n = int(s.get("count", 0) or 0)
                active = int(s.get("active", 0) or 0)
                avg_absent = float(s.get("avg", 0.0) or 0.0)
                med = float(s.get("median", 0.0) or 0.0)
                if n <= 0:
                    return "CRITICAL"
                p_active = (active / n) if n > 0 else 0.0
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
                ordering = ["CRITICAL", "DEGRADED", "HIGH", "NEAR-TOTAL", "FULL"]
                idx = ordering.index(tier)
                if (med >= 1.0 and avg_absent >= 0.5) and idx > 0:
                    idx -= 1
                elif (med <= 0.0 and avg_absent <= 0.25) and idx < len(ordering) - 1:
                    idx += 1
                return ordering[idx]
            def _select_stability_tier(s: Dict[str, float]) -> str:
                sd = float(s.get("stdev", 0.0) or 0.0)
                if sd <= 0.10:
                    return "UNIFORM"
                if sd <= 0.18:
                    return "CONSISTENT"
                if sd <= 0.26:
                    return "STABLE"
                if sd <= 0.34:
                    return "VARIABLE"
                return "FRACTURED"
            cc_ready = _select_readiness_tier(stats_cmd)
            cc_stab = _select_stability_tier(stats_cmd)
        except Exception:
            pass
        lines.append(f"  Company Command Status         :: {cc_ready} READINESS — {cc_stab} STABILITY")
        lines.append("==============================================================================")
        lines.append("\u001b[0m```")
        return "\n".join(lines)
    except Exception:
        return None


@bot.tree.command(
    name="company_briefs",
    description="Run five briefs per company and attach files.",
)
@app_commands.describe(
    companies="One or more company roles (mentions or names)",
    days="Optional: number of days to include (default 7)",
)
async def company_briefs(
    interaction: discord.Interaction,
    companies: str,
    days: Optional[int] = None,
):
    # Restrict to allowed channel and (High Command OR whitelisted user ID)
    allowed_ids = set(str(x) for x in (CONFIG.get("company_briefs_allowed_user_ids") or []))
    if not is_allowed_channel(interaction):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    user_id_str = str(getattr(interaction.user, "id", ""))
    if not (is_high_command(interaction.user) or (user_id_str and user_id_str in allowed_ids)):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True, ephemeral=True)

    guild = interaction.guild
    if not guild:
        await interaction.followup.send("Guild unavailable.", ephemeral=True)
        return

    # Resolve companies from text
    resolved = _resolve_company_roles_from_text(guild, companies)
    if not resolved:
        await interaction.followup.send(
            "No valid company roles found in input.", ephemeral=True
        )
        return

    span_days = days if (isinstance(days, int) and days and days > 0) else 7

    # Build attachments per company
    temp_files: List[Path] = []
    files_to_send: List[discord.File] = []
    try:
        for comp in resolved:
            content_parts: List[str] = []
            # Command Brief
            cmd_txt = _build_command_brief_text(guild, comp, span_days)
            if cmd_txt:
                content_parts.append(cmd_txt)
            # Apothecary Brief
            apo_txt = _build_apothecary_brief_text(guild, comp, span_days)
            if apo_txt:
                content_parts.append(apo_txt)
            # Chaplain Brief
            try:
                chap_txt, _chap_meta = _build_chaplain_report(guild, comp)
            except Exception:
                chap_txt = None
            if chap_txt:
                content_parts.append(chap_txt)
            # Librarian Brief
            lib_txt = _build_librarian_brief_text(guild, comp, span_days)
            if lib_txt:
                content_parts.append(lib_txt)
            # Techmarine Brief
            tech_txt = _build_techmarine_brief_text(guild, comp, span_days)
            if tech_txt:
                content_parts.append(tech_txt)

            if not content_parts:
                # Skip if no content generated
                continue

            # Write per-company temp file
            safe_name = re.sub(r"[^a-zA-Z0-9_\-]+", "_", getattr(comp, "name", "Company"))
            ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            fname = f"briefs_{safe_name}_{ts}.txt"
            tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=f"_{fname}")
            try:
                tmp.write("\n\n".join(content_parts))
            finally:
                tmp.close()
            p = Path(tmp.name)
            temp_files.append(p)
            files_to_send.append(discord.File(str(p), filename=fname))

        if not files_to_send:
            await interaction.followup.send("No brief content generated.", ephemeral=True)
            return

        # Send attachments (batch if many)
        # Discord typically allows multiple attachments; keep a soft cap of 8 per message
        BATCH = 8
        for i in range(0, len(files_to_send), BATCH):
            batch = files_to_send[i : i + BATCH]
            await interaction.followup.send(
                content=f"Attached {len(batch)} company brief file(s).",
                files=batch,
                ephemeral=True,
            )
    finally:
        # Cleanup temp files
        for p in temp_files:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass


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


def _user_label(u: discord.User | discord.Member) -> str:
    try:
        name = getattr(u, "nick", None) or getattr(u, "display_name", None) or getattr(u, "name", None) or getattr(u, "username", None) or str(getattr(u, "id", ""))
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
            logger.info(f"Invoke /{cmd_name or '?'} by {_user_label(interaction.user)} guild={guild_id} channel={channel_id} args={args_summary}")
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
            logger.info(f"Complete /{getattr(command, 'name', '?')} by {_user_label(interaction.user)} guild={guild_id} channel={channel_id} duration={dur:.3f}s")
        else:
            logger.info(f"Complete /{getattr(command, 'name', '?')} by {_user_label(interaction.user)} guild={guild_id} channel={channel_id}")
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
        logger.warning(f"Error in /{cmd_name or '?'} by {_user_label(interaction.user)}: {type(error).__name__}: {error}")
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
        "• /command_brief [company:@Role] [days:N] — Company ops: tempo, risk, highlights. (Above Sergeant)\n"
        "• /techmarine_brief [company:@Role] [days:N] — Materiel yields, consistency, risk-adjusted metrics. (Above Sergeant)\n"
        "• /librarian_brief [company:@Role] [days:N] — Knowledge ops, formations, stability patterns. (Above Sergeant)\n"
        "• /high_command_brief [days:N] — Strategic company summary for High Command (High Command only).\n"
        "• /apothecary_brief [company:@Role] [days:N] — Biological readiness, preservation, care load. (Above Sergeant)\n"
        "• /chaplain_brief [company:@Role] [days:N] — Morale, oaths, honors, spiritual readiness. (Above Sergeant)\n"
        "• /company_briefs companies:\"@Role …\" [days:N] — Five briefs per company; returns files. (High Command/whitelist)\n"
        "• /audit_archive_discrepancies — Re-check rejected AARs for resolution. (Watch Master/Forgemaster)\n"
        "• /sanctify_battle_records [span_days:N] — Ingest sanctioned AARs via cursor. (Watch Master/Forgemaster)\n"
        "• /reconcile_records [span_days:N] — Audit then ingest in one rite. (Watch Master/Forgemaster)\n\n"
        "Commands restricted to sanctified channels. Honor and memory preserved."
    )
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
    # Compact roster rows (structured) for under-2k summary
    roster_items: List[Dict[str, int | str]] = []
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
            if (idx_veteran is not None) and (highest_idx is not None) and (highest_idx <= idx_veteran):
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

        # Build display string using Unicode circles: hollow circles '○' up to 5,
        # then a filled circle '●' to indicate more than 5. Always append numeric count in parentheses.
        try:
            studs_symbols = ""
            if not studs_count:
                studs_display = f"— ({studs_count})"
            else:
                if studs_count <= 5:
                    studs_symbols = "○" * studs_count
                else:
                    studs_symbols = "○" * 5 + "●"
                studs_display = f"{studs_symbols} ({studs_count})"
        except Exception:
            studs_display = str(studs_count)
            studs_symbols = ""

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

        # Determine Active/Inactive status: Active if any AAR in last 30 days.
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
                # Sort newest first and check if any within the last 30 days from now
                timestamps.sort(reverse=True)
                now = datetime.utcnow()
                cutoff = now - timedelta(days=30)
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
            r_lines.append("\u001b[32m==============================================================================")
            r_lines.append("  WATCH FORTRESS JERICHO // SERVICE-RECORD NODE")
            r_lines.append("  KILL TEAM DEEDS ROSTER")
            r_lines.append("==============================================================================")
            # Sort roster by rank bucket (Sergeant, Veteran/Champion, Brother/Sister)
            sorted_items = sorted(
                roster_items,
                key=lambda it: (
                    int(it.get("rank_bucket", 9)),
                    str(it.get("name", "")).lower(),
                ),
            )

            # Compute column widths for aligned rendering
            def _len_str(v):
                try:
                    return len(str(v))
                except Exception:
                    return 0
            # Include studs symbols in name width so studs appear directly after names, aligned
            def _name_with_studs_len(it):
                try:
                    nm = str(it.get("name", "") or "")
                    studs = str(it.get("studs_symbols", "") or "")
                    return len(nm) + (1 + len(studs) if studs else 0)
                except Exception:
                    return 0
            name_w = max((_name_with_studs_len(it) for it in sorted_items), default=1)
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
                    nm = str(it.get('name','') or '')
                    studs = str(it.get('studs_symbols','') or '')
                    combined = f"{nm} {studs}" if studs else nm
                    combined = combined[:name_w]
                    st = str(it.get('status',''))[:status_w]
                    line = (
                        f"{combined:<{name_w}} :: "
                        f"{st:<{status_w}} | "
                        f"AAR {int(it.get('aar',0)):>{aar_w}} | "
                        f"Gene {int(it.get('gene',0)):>{gene_w}} | "
                        f"Armory {int(it.get('armory',0)):>{armory_w}}"
                    )
                except Exception:
                    line = f"{nm} :: {st}"
                formatted_rows.append(line)

            # Footer reserved to keep block markers valid
            footer_lines = ["==============================================================================", "\u001b[0m```"]
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
                    color=0x2ecc71,
                )
                # Chunk rows into fields to avoid long single blocks
                chunk_size = 15
                for i in range(0, len(formatted_rows), chunk_size):
                    chunk = formatted_rows[i : i + chunk_size]
                    # keep lines short using earlier truncation
                    field_value = "\n".join(f"• {row}" for row in chunk)
                    roster_embed.add_field(
                        name=f"Members {i+1}–{min(i+chunk_size, len(formatted_rows))}",
                        value=field_value or "—",
                        inline=False,
                    )
                roster_embed.set_footer(text="Roster generated from recent service records.")

                roster_view = ToggleFormatView(text_content=roster_text, embed=roster_embed, default="ansi")
                await interaction.followup.send(content=roster_text, embed=None, view=roster_view, ephemeral=True)
            except Exception:
                # Fallback to ANSI block with toggle
                try:
                    roster_embed = _embed_from_ansi("Kill Team Roster", roster_text)
                    roster_view = ToggleFormatView(text_content=roster_text, embed=roster_embed, default="ansi")
                    await interaction.followup.send(content=roster_text, embed=None, view=roster_view, ephemeral=True)
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
        total_scores: List[float] = [a + g + r for a, g, r in zip(aar_vals, gene_vals, armory_vals)]
        def _pstdev(vals: List[float]) -> float:
            return statistics.pstdev(vals) if len(vals) >= 2 else 0.0
        reliability = (_mean(total_scores) / (1.0 + _pstdev(total_scores))) if total_scores else 0.0
        force_multiplier = _mean(per_capita_vals)

        # Format a compact ANSI-styled summary similar to individual tally output
        stat_rows_summary = [
            ("Window", f"Last {span_days} Days"),
            ("Kill Team", _extract_killteam_name(getattr(killteam, "name", "Unknown"))),
            ("Members", str(count)),
            ("Veteran Lethality Index", f"Avg AAR {avg_aar:.2f}"),
            ("Operational Tempo", f"Ops {int(ops_count)}"),
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
                color=0x2ecc71,
            )
            for label, value in stat_rows_summary:
                embed.add_field(name=label, value=value, inline=True)
            view = ToggleFormatView(text_content=summary_text, embed=embed, default="ansi")
            await interaction.followup.send(content=summary_text, embed=None, view=view, ephemeral=True)
        except Exception:
            # ignore send errors and proceed to attach full file
            try:
                embed = _embed_from_ansi("Kill Team Summary", summary_text)
                view = ToggleFormatView(text_content=summary_text, embed=embed, default="ansi")
                await interaction.followup.send(content=summary_text, embed=None, view=view, ephemeral=True)
            except Exception:
                pass

    # Only send the detailed per-brother ledger for single-brother queries
    if not killteam:
        embed = _embed_from_ansi("Deeds Ledger", reply_text)
        view = ToggleFormatView(text_content=reply_text, embed=embed, default="ansi")
        await interaction.followup.send(content=reply_text, embed=None, view=view, ephemeral=True)


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
    span_days = days if (isinstance(days, int) and days > 0) else 30
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

    # Also compute a synthetic team for Company Command (shared rule)
    company_command_members: List[discord.Member] = _resolve_company_command_members(company)

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

    # Risk Appetite medians (used for per-team cube profiles below)
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
    except Exception:
        med_aar = 0.0
        med_gene = 0.0
        med_ops = 0.0
        med_armory = 0.0
        med_rel = 0.0
        med_fm = 0.0

    # Composite Preservation axis (Gene + Armory) for 2×2 Risk Appetite grid
    # Use z-scores when dispersion is available; fallback to median-centered distances
    try:
        sd_aar = statistics.pstdev(aar_list) if len(aar_list) >= 2 else 0.0
    except Exception:
        sd_aar = 0.0
    try:
        sd_gene = statistics.pstdev(gene_list) if len(gene_list) >= 2 else 0.0
    except Exception:
        sd_gene = 0.0
    try:
        sd_arm = statistics.pstdev(armory_list) if len(armory_list) >= 2 else 0.0
    except Exception:
        sd_arm = 0.0

    def _quad_label(code: str) -> str:
        # 40k/Deathwatch flavored, descriptive, one word per quadrant
        mapping = {
            "HH": "Sanctifier",  # High lethality + High preservation
            "HL": "Purgator",    # High lethality + Low preservation
            "LH": "Conservator", # Low lethality + High preservation
            "LL": "Dormant",   # Low lethality + Low preservation
        }
        return mapping.get(code, code)

    def _quad_code(z_aar: float, z_pres: float) -> str:
        return ("H" if z_aar >= 0 else "L") + ("H" if z_pres >= 0 else "L")

    def _quad_score(z_aar: float, z_pres: float, code: str, dx: float, dp: float) -> float:
        xa = z_aar if sd_aar > 0.0 else dx
        xp = z_pres if (sd_gene > 0.0 or sd_arm > 0.0) else dp
        if code == "HH":
            return max(xa, 0.0) + max(xp, 0.0)
        if code == "HL":
            return max(xa, 0.0) + max(-xp, 0.0)
        if code == "LH":
            return max(-xa, 0.0) + max(xp, 0.0)
        return max(-xa, 0.0) + max(-xp, 0.0)  # LL

    risk_profiles: List[Tuple[dict, str, float, float, float]] = []
    for t in team_stats:
        # Median-centered distances for axes
        dx = (float(t.get("avg_aar", 0.0) or 0.0) - med_aar)
        dy = (float(t.get("avg_gene", 0.0) or 0.0) - med_gene)
        dz = (float(t.get("avg_armory", 0.0) or 0.0) - med_armory)
        # Axis z-scores (zero if dispersion not available)
        z_aar = (dx / sd_aar) if sd_aar > 0.0 else 0.0
        z_gene = (dy / sd_gene) if sd_gene > 0.0 else 0.0
        z_arm = (dz / sd_arm) if sd_arm > 0.0 else 0.0
        # Composite Preservation*
        z_pres = 0.6 * z_gene + 0.4 * z_arm
        dp = 0.6 * dy + 0.4 * dz
        code = _quad_code(z_aar, z_pres)
        score = _quad_score(z_aar, z_pres, code, dx, dp)
        risk_profiles.append((t, code, score, dx, dp))

    # Debug-only: dump medians/dispersion and per-team deltas to terminal
    try:
        if not BROADCAST_STATUS:
            header = "[Command Brief Diagnostics]"
            print(header)
            print(
                f"  Medians — AAR: {med_aar:.3f}, Gene: {med_gene:.3f}, Armory: {med_armory:.3f}, Ops: {med_ops:.3f}"
            )
            print(
                f"  Dispersion — sd(AAR): {sd_aar:.3f}, sd(Gene): {sd_gene:.3f}, sd(Armory): {sd_arm:.3f}"
            )
            for t, code, score, dx, dp in risk_profiles:
                name = t.get("name", "Unknown")
                a = float(t.get("avg_aar", 0.0) or 0.0)
                g = float(t.get("avg_gene", 0.0) or 0.0)
                r = float(t.get("avg_armory", 0.0) or 0.0)
                near = (abs(dx) < 0.05 and abs(dp) < 0.05)
                flag = " [near-median]" if near else ""
                print(
                    f"  {name:<18} | avgAAR {a:.3f} avgGene {g:.3f} avgArm {r:.3f} | ΔAAR {dx:+.3f} ΔPres* {dp:+.3f} | code {code} idx {score:.3f}{flag}"
                )
    except Exception:
        pass

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
            "+ The Watch Master and the specialist orders kept to the strategium, prioritizing ",
            "  oversight and readiness rites.",
        ]
    elif hc_ops_count < med_ops and hc_avg_aar > med_aar:
        hc_note_lines = [
            "+ High Command deployments during this window exceeded the company median in ",
            "  lethality but remained limited in frequency by doctrine.",
            "+ The Watch Master and the heads of the specialist orders deploy only when ",
            "  strategic necessity overrides standing command duties.",
        ]
    elif hc_ops_count >= med_ops and hc_avg_aar <= med_aar:
        hc_note_lines = [
            "+ High Command maintained a hands-on presence to reinforce cohesion and command drills.",
            "+ Direct oversight emphasized continuity of command over high-intensity sorties.",
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
    # Build aligned key/value lines so all '::' markers line up
    kv_lines: List[Tuple[str, str]] = []
    kv_lines.append((
        "Veteran Lethality Index",
        f"{_team_label(best_lethality)}  (Avg AAR: {best_lethality['avg_aar']:.2f})",
    ))
    kv_lines.append((
        "Operational Tempo",
        f"{_team_label(best_tempo)}  (Ops: {int(best_tempo['avg_ops'])})",
    ))
    kv_lines.append((
        "Siegebreaker Rating",
        f"{_team_label(best_siegebreaker)}  (Avg Waves: {best_siegebreaker['avg_waves']:.2f})",
    ))
    kv_lines.append((
        "Kill Team Reliability Index",
        f"{_team_label(best_reliability)}  (Score: {best_reliability['reliability']:.2f})",
    ))
    # Per-team composite Risk Appetite (aligned)
    def _fmt_delta(val: float) -> str:
        try:
            return f"{val:+.2f}"
        except Exception:
            return str(val)

    # Treat near-median teams as neutral for readability (avoids HH/LL on exact ties)
    EPS = 0.05
    for t, code, score, dx, dp in risk_profiles:
        disp_label = _quad_label(code)
        try:
            if abs(dx) < EPS and abs(dp) < EPS:
                disp_label = "Orthodox"  # lore-friendly neutral label for median posture
        except Exception:
            pass
        kv_lines.append((
            f"Risk Appetite — {_team_label(t)}",
            f"{disp_label}  (AAR Δ {_fmt_delta(dx)} | Pres* Δ {_fmt_delta(dp)}; Index {score:.2f})",
        ))
    kv_lines.append((
        "Force Multiplier Rating",
        f"{_team_label(force_multiplier)}  (Avg AAR/Member: {force_multiplier.get('force_multiplier', 0.0):.2f})",
    ))

    label_width = 0
    try:
        label_width = max((len(k) for k, _ in kv_lines), default=0)
    except Exception:
        label_width = 0
    for k, v in kv_lines:
        lines.append(f"  {k:<{label_width}} :: {v}")
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

    # Send response using structured embed to minimize wrapping
    try:
        msg = "\n".join(lines)
        embed = discord.Embed(
            title="Command Brief",
            description=f"{getattr(company, 'name', 'Unknown')} — Last {span_days} Days",
            color=0x2ecc71,
        )
        # Key metrics (concise inline fields)
        embed.add_field(
            name="Veteran Lethality",
            value=f"{_team_label(best_lethality)} (Avg AAR {best_lethality['avg_aar']:.2f})",
            inline=True,
        )
        embed.add_field(
            name="Operational Tempo",
            value=f"{_team_label(best_tempo)} (Ops {int(best_tempo['avg_ops'])})",
            inline=True,
        )
        embed.add_field(
            name="Siegebreaker",
            value=f"{_team_label(best_siegebreaker)} (Avg Waves {best_siegebreaker['avg_waves']:.2f})",
            inline=True,
        )
        embed.add_field(
            name="Reliability",
            value=f"{_team_label(best_reliability)} (Score {best_reliability['reliability']:.2f})",
            inline=True,
        )
        embed.add_field(
            name="Force Multiplier",
            value=f"{_team_label(force_multiplier)} (Avg AAR/Member {force_multiplier.get('force_multiplier', 0.0):.2f})",
            inline=True,
        )
        # Risk Appetite summary (limit to top 8 entries for readability)
        try:
            risk_lines: List[str] = []
            EPS = 0.05
            for idx, (t, code, score, dx, dp) in enumerate(risk_profiles):
                if idx >= 8:
                    break
                disp_label = _quad_label(code)
                try:
                    if abs(dx) < EPS and abs(dp) < EPS:
                        disp_label = "Orthodox"
                except Exception:
                    pass
                risk_lines.append(
                    f"• {_team_label(t)} — {disp_label} (Δ AAR {dx:+.2f} | Δ Pres {dp:+.2f}; Ix {score:.2f})"
                )
            if risk_lines:
                embed.add_field(name="Risk Appetite", value="\n".join(risk_lines), inline=False)
        except Exception:
            pass

        # High Command Notes
        if hc_note_lines:
            embed.add_field(
                name="High Command Notes",
                value="\n".join(f"• {ln}" for ln in hc_note_lines)[:1024] or "—",
                inline=False,
            )

        view = ToggleFormatView(text_content=msg, embed=embed, default="ansi")
        await interaction.followup.send(content=msg, embed=None, view=view, ephemeral=True)
    except Exception as e:
        try:
            err_type = type(e).__name__
            err_msg = str(e)
            note = "Unknown interaction token (defer/send mismatch or expired)" if "Unknown interaction" in err_msg or "10062" in err_msg else ""
            print(f"[Command Brief Error] {err_type}: {err_msg} {note}")
        except Exception:
            pass
        # Re-raise so the framework can handle/report appropriately
        raise
    
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
    span_days = days if (isinstance(days, int) and days > 0) else 30
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

    # Company Command synthetic team via shared rule
    company_command_members = _resolve_company_command_members(company)
    if company_command_members:
        teams.append(("Company Command", company_command_members))

    if not teams:
        await interaction.followup.send("No Kill Teams or Company Command members found in the selected company.", ephemeral=True)
        return

    # Compute armory-only metrics per team
    team_member_ids: Dict[str, set[str]] = {name: {str(getattr(m, "id", "")) for m in members if getattr(m, "id", None)} for name, members in teams}
    per_team_values: Dict[str, List[float]] = {name: [] for name, _ in teams}
    per_team_points: Dict[str, List[float]] = {name: [] for name, _ in teams}
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
            # Compute risk-adjusted armory points for this operation
            try:
                arm_points = float(compute_armory_bonus_points(rec.get("difficulty_class"), arm))
            except Exception:
                arm_points = 0.0
            for name, mids in team_member_ids.items():
                if set(bros) & mids:
                    per_team_values[name].append(arm_val)
                    per_team_points[name].append(arm_points)
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

    # Compute points averages and op counts per team for medians and ranking
    per_team_points_avg: Dict[str, float] = {n: _mean(per_team_points.get(n, [])) for n, _ in teams}
    per_team_ops_count: Dict[str, int] = {n: len(per_team_values.get(n, [])) for n, _ in teams}

    # New metrics: Typical Salvage Yield (Median) and High-Value Salvage Frequency
    # - Typical Salvage Yield: per-team median of `armory_data`
    # - High-Value Salvage Frequency: share of team ops with `armory_data` >= company Q3
    per_team_median: Dict[str, float] = {}
    for name, vals in per_team_values.items():
        try:
            per_team_median[name] = statistics.median(vals) if vals else 0.0
        except Exception:
            per_team_median[name] = 0.0

    best_median: Optional[Tuple[str, float]] = None
    try:
        median_candidates = [
            (name, per_team_median.get(name, 0.0))
            for name, _ in teams
            if per_team_ops_count.get(name, 0) > 0
        ]
        if median_candidates:
            best_median = max(median_candidates, key=lambda x: x[1])
    except Exception:
        best_median = None

    # Company armory distribution (only ops where any company member participated)
    company_arm_values: List[float] = []
    try:
        for rec in recent_records:
            try:
                bros = [str(b) for b in (rec.get("brother_ids") or [])]
                if not (set(bros) & company_ids):
                    continue
                arm = rec.get("armory_data")
                if arm is None:
                    continue
                company_arm_values.append(float(arm))
            except Exception:
                continue
    except Exception:
        company_arm_values = []

    # 75th percentile (Q3) threshold for "high-value" salvage
    q3_threshold: float = 0.0
    try:
        if company_arm_values:
            # Use inclusive method to avoid dropping extremes on small samples
            qs = statistics.quantiles(company_arm_values, n=4, method="inclusive") if len(company_arm_values) >= 2 else []
            q3_threshold = qs[2] if qs else company_arm_values[0]
        else:
            q3_threshold = 0.0
    except Exception:
        try:
            q3_threshold = statistics.median(company_arm_values) if company_arm_values else 0.0
        except Exception:
            q3_threshold = 0.0

    per_team_hv_rate: Dict[str, Tuple[int, int, float]] = {}  # name -> (hv_count, total, rate)
    for name, vals in per_team_values.items():
        if vals:
            hv_count = sum(1 for v in vals if v >= q3_threshold)
            total = len(vals)
            rate = (hv_count / total) if total > 0 else 0.0
            per_team_hv_rate[name] = (hv_count, total, rate)
        else:
            per_team_hv_rate[name] = (0, 0, 0.0)

    best_hv: Optional[Tuple[str, int, int, float]] = None
    try:
        hv_candidates = [
            (name, hv[0], hv[1], hv[2])
            for name, hv in per_team_hv_rate.items()
            if per_team_ops_count.get(name, 0) > 0
        ]
        if hv_candidates:
            best_hv = max(hv_candidates, key=lambda x: x[3])
    except Exception:
        best_hv = None

    # Derive winners/labels, restricting to teams with at least one data point where relevant
    nonempty = [t for t in team_stats if t[3] > 0]
    best_yield = max(nonempty, key=lambda t: t[1]) if nonempty else None
    best_consistency = min(nonempty, key=lambda t: t[2]) if nonempty else None
    total_all = sum(t[3] for t in nonempty) if nonempty else 0.0
    top_share = None
    if nonempty and total_all > 0:
        top_share = max(nonempty, key=lambda t: t[3])

    # Winner for risk-adjusted armory yield (average points per operation)
    best_points: Optional[Tuple[str, float]] = None
    try:
        candidates = [
            (name, per_team_points_avg.get(name, 0.0))
            for name, _ in teams
            if per_team_ops_count.get(name, 0) > 0
        ]
        if candidates:
            best_points = max(candidates, key=lambda x: x[1])
    except Exception:
        best_points = None

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

    # Insert new risk-adjusted armory metric (average challenge points per operation)
    if best_points:
        lines.append(
            f"  Risk-Adjusted Armory Yield :: {_label(best_points[0])}  (Avg Points: {best_points[1]:.2f})"
        )
    else:
        lines.append("  Risk-Adjusted Armory Yield :: —")

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

    # Typical Salvage Yield (Median)
    if best_median:
        lines.append(
            f"  Typical Salvage Yield      :: {_label(best_median[0])}  (Median: {best_median[1]:.2f})"
        )
    else:
        lines.append("  Typical Salvage Yield      :: —")

    # High-Value Salvage Frequency (≥ company Q3)
    if best_hv:
        hv_name, hv_cnt, hv_tot, hv_rate = best_hv
        try:
            q3_disp = f"{q3_threshold:.0f}"
        except Exception:
            q3_disp = f"{q3_threshold}"
        lines.append(
            f"  High-Value Salvage Freq.   :: {_label(hv_name)}  (≥ Q3 {q3_disp}; Rate: {hv_rate*100:.0f}% of {hv_tot})"
        )
    else:
        lines.append("  High-Value Salvage Freq.   :: —")

    # High Command comparison notes (armory focus)
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

        hc_arm_vals: List[float] = []
        hc_pts_vals: List[float] = []
        hc_ops = 0
        for rec in recent_records:
            try:
                bros = [str(b) for b in (rec.get("brother_ids") or [])]
                if not any(b in hc_ids for b in bros):
                    continue
                arm = rec.get("armory_data")
                if arm is None:
                    continue
                # Count only armory-bearing ops for frequency in this brief
                hc_ops += 1
                try:
                    arm_val = float(arm)
                except Exception:
                    arm_val = None
                if arm_val is not None:
                    hc_arm_vals.append(arm_val)
                try:
                    pts = float(compute_armory_bonus_points(rec.get("difficulty_class"), arm))
                except Exception:
                    pts = 0.0
                hc_pts_vals.append(pts)
            except Exception:
                continue

        # Company medians across teams (only teams with at least one armory record)
        valid_teams = [t for t in team_stats if t[3] > 0]
        comp_med_data = statistics.median([t[1] for t in valid_teams]) if valid_teams else 0.0
        comp_med_points = statistics.median(
            [per_team_points_avg.get(t[0], 0.0) for t in valid_teams]
        ) if valid_teams else 0.0
        comp_med_ops = statistics.median(
            [per_team_ops_count.get(t[0], 0) for t in valid_teams]
        ) if valid_teams else 0.0

        # Compose notes
        lines.append("------------------------------------------------------------------------------")
        lines.append("  High Command Notes:")
        if len(hc_arm_vals) == 0:
            lines.append(
                "  + High Command recorded no materiel recovery during this window."
            )
            lines.append(
                "  + Stewardship posture maintained: stockpile audits and rites of upkeep emphasized."
            )
        else:
            hc_avg_arm = (sum(hc_arm_vals) / len(hc_arm_vals)) if hc_arm_vals else 0.0
            hc_avg_pts = (sum(hc_pts_vals) / len(hc_pts_vals)) if hc_pts_vals else 0.0
            if (hc_avg_pts > comp_med_points) and (hc_ops < comp_med_ops):
                lines.append(
                    "  + High Command deployments exceeded company norms for materiel recovery under\n    risk, while remaining limited in frequency by doctrine."
                )
            elif hc_avg_arm >= comp_med_data:
                lines.append(
                    "  + High Command maintained materiel recovery in line with company standards."
                )
            elif (hc_ops >= comp_med_ops) and (hc_avg_pts < comp_med_points):
                lines.append(
                    "  + High Command accepted operational risk to meet materiel exigencies despite modest yields."
                )
            else:
                lines.append(
                    "  + High Command deployments favored command oversight over direct materiel recovery."
                )
    except Exception:
        pass

    lines.append("==============================================================================")
    lines.append("\u001b[0m```")

    msg = "\n".join(lines)
    # Structured embed for Techmarine Brief to reduce wrapping
    try:
        embed = discord.Embed(
            title="Techmarine Brief",
            description=f"{getattr(company, 'name', 'Unknown')} — Last {span_days} Days",
            color=0x2ecc71,
        )
        def _label(name: str) -> str:
            return name if name == "Company Command" else f"KT {name}"
        if best_yield:
            embed.add_field(
                name="Armory Yield Efficiency",
                value=f"{_label(best_yield[0])} (Avg Armory {best_yield[1]:.2f})",
                inline=True,
            )
        if best_points:
            embed.add_field(
                name="Risk-Adjusted Yield",
                value=f"{_label(best_points[0])} (Avg Points {best_points[1]:.2f})",
                inline=True,
            )
        if best_consistency:
            embed.add_field(
                name="Armory Consistency",
                value=f"{_label(best_consistency[0])} (SD {best_consistency[2]:.2f})",
                inline=True,
            )
        if top_share and total_all > 0:
            percent = (top_share[3] / total_all) * 100.0
            embed.add_field(
                name="Materiel Concentration",
                value=f"{_label(top_share[0])} (Share {percent:.0f}%)",
                inline=True,
            )
        if best_median:
            embed.add_field(
                name="Typical Salvage (Median)",
                value=f"{_label(best_median[0])} (Median {best_median[1]:.2f})",
                inline=True,
            )
        if best_hv:
            hv_name, hv_cnt, hv_tot, hv_rate = best_hv
            embed.add_field(
                name="High-Value Salvage Freq.",
                value=f"{_label(hv_name)} (Rate {hv_rate*100:.0f}% of {hv_tot})",
                inline=True,
            )
        # High Command Notes
        if "hc_arm_vals" in locals():
            try:
                hc_lines: List[str] = []
                if len(hc_arm_vals) == 0:
                    hc_lines.append("High Command recorded no materiel recovery during this window.")
                    hc_lines.append("Stewardship posture maintained: stockpile audits and rites of upkeep emphasized.")
                else:
                    hc_avg_pts = (sum(hc_pts_vals) / len(hc_pts_vals)) if hc_pts_vals else 0.0
                    hc_avg_arm = (sum(hc_arm_vals) / len(hc_arm_vals)) if hc_arm_vals else 0.0
                    if (hc_avg_pts > comp_med_points) and (hc_ops < comp_med_ops):
                        hc_lines.append("Elevated recovery under risk with restrained deployments by doctrine.")
                    elif hc_avg_arm >= comp_med_data:
                        hc_lines.append("Materiel recovery consistent with company standards.")
                    elif (hc_ops >= comp_med_ops) and (hc_avg_pts < comp_med_points):
                        hc_lines.append("Operational risk accepted despite modest yields.")
                    else:
                        hc_lines.append("Oversight favored over direct materiel recovery.")
                if hc_lines:
                    embed.add_field(name="High Command Notes", value="\n".join(f"• {x}" for x in hc_lines)[:1024], inline=False)
            except Exception:
                pass

        view = ToggleFormatView(text_content=msg, embed=embed, default="ansi")
        await interaction.followup.send(content=msg, embed=None, view=view, ephemeral=True)
    except Exception:
        embed = _embed_from_ansi("Techmarine Brief", msg)
        view = ToggleFormatView(text_content=msg, embed=embed, default="ansi")
        await interaction.followup.send(content=msg, embed=None, view=view, ephemeral=True)


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

# Abbreviations for mission names (used in concise distribution displays)
MISSION_ABBREVIATIONS: Dict[str, str] = {
    "Inferno": "INF",
    "Decapitation": "DEC",
    "Vox Liberatis": "VL",
    "Reliquary": "REL",
    "Fall of Atreus": "FOA",
    "Ballistic Engine": "BE",
    "Termination": "TER",
    "Obelisk": "OBL",
    "Exfiltration": "EXF",
    "Vortex": "VTX",
    "Reclamation": "REC",
    "Siege": "SGE",
}

def _abbr_mission_name(name: Optional[str]) -> str:
    """Return a concise abbreviation for a canonical mission name.
    Falls back to uppercase initials (up to 3 chars) if unmapped.
    """
    try:
        if not name:
            return "—"
        ab = MISSION_ABBREVIATIONS.get(name)
        if ab:
            return ab
        words = re.split(r"\s+", name.strip())
        abbr = "".join(w[0] for w in words if w)[:3].upper()
        return abbr or (name.strip()[:3].upper())
    except Exception:
        return (name or "").strip()[:3].upper() or "—"

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

# Percent-based tier variants for weekly-normalized interpretation
def _doctrinal_coherence_tier_pct(coverage_pct: float) -> str:
    """Map doctrine coverage percentage to coherence tier.
    Intended for weekly windows where normalization by catalog size is preferred.
    """
    p = max(0.0, min(100.0, coverage_pct))
    if p <= 10.0:
        return "SPECIALIZED"
    if p <= 20.0:
        return "REFINED"
    if p <= 40.0:
        return "STABLE"
    if p <= 60.0:
        return "EMERGENT"
    return "FRAGMENTED"

def _operational_exposure_tier_pct(coverage_pct: float) -> str:
    """Map mission coverage percentage to exposure tier.
    Weekly assumption: score by fraction of mission catalog seen in window.
    """
    p = max(0.0, min(100.0, coverage_pct))
    if p <= 10.0:
        return "ISOLATED"
    if p <= 25.0:
        return "LIMITED"
    if p <= 40.0:
        return "DIVERSE"
    if p <= 60.0:
        return "BROAD"
    return "EXTENSIVE"

# New helper: cohesion by concentration (top-1 doctrine share)
def _cohesion_concentration_tier(share_pct: float) -> str:
    """Map top doctrine share percentage to a loreful cohesion tier.
    Lower share => more balanced/mixed; higher => concentrated/monolithic.
    """
    p = max(0.0, min(100.0, share_pct))
    if p <= 35.0:
        return "BALANCED"
    if p <= 50.0:
        return "LEANING"
    if p <= 65.0:
        return "FOCUSED"
    if p <= 80.0:
        return "ORTHODOX"
    return "MONOLITHIC"

# New helper: evenness band for per-mission replay distribution
def _evenness_band(cv: float) -> str:
    """Qualitative banding for coefficient of variation (sd/mean).
    Smaller CV indicates more even replay across missions.
    """
    try:
        c = float(cv)
        if c <= 0.25:
            return "Balanced"
        if c <= 0.50:
            return "Mixed"
        return "Skewed"
    except Exception:
        return "Undetermined"

# New helper: doctrine diversity band using HHI
def _diversity_band_hhi(hhi: float) -> str:
    """Classify doctrine diversity using Herfindahl–Hirschman Index (HHI).
    Lower HHI => more diverse; higher => more concentrated.
    """
    try:
        h = float(hhi)
        if h <= 0.20:
            return "Eclectic"
        if h <= 0.35:
            return "Mixed"
        if h <= 0.50:
            return "Concentrated"
        return "Dominant"
    except Exception:
        return "Undetermined"


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
    span_days = days if (isinstance(days, int) and days > 0) else 30
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

    # Company Command synthetic team via shared rule
    company_command_members = _resolve_company_command_members(company)
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
    company_mission_counts: Counter[str] = Counter()

    # Per-team doctrine counts for divergence
    per_team_doc_counts: Dict[str, Counter[str]] = {
        name: Counter() for name, _ in teams
    }
    # Track which teams appeared in any AAR within the window (team coverage)
    represented_teams: set[str] = set()

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
            company_mission_counts[canon_mission] += 1
        # Attribute doctrines to any team intersecting this record
        for name, mids in team_member_ids.items():
            if set(bros) & mids:
                for d in doc_tags:
                    per_team_doc_counts[name][d] += 1
                represented_teams.add(name)

    # Helper to abbreviate team label
    def _abbr_label(n: str) -> str:
        if not n:
            return "Team"
        lower = n.lower()
        if lower.startswith("kill team "):
            return "KT " + n[10:]
        return "KT " + n if not lower.startswith("company command") else n

    # Operational Environments: show top 3 by share (value only)
    if company_env_counts:
        env_sorted = sorted(company_env_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        total_env = sum(company_env_counts.values())
        top_envs = env_sorted[:3]
        _env_parts = []
        for name, cnt in top_envs:
            pct = (cnt / total_env) if total_env > 0 else 0.0
            _env_parts.append(f"{name} ({pct:.0%})")
        env_value = " ".join(_env_parts) if _env_parts else "—"
    else:
        env_value = "—"

    # Doctrinal Pattern: show top 3 by count (value only)
    if company_doc_counts:
        doc_sorted = sorted(company_doc_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        top_docs = doc_sorted[:3]
        _doc_parts = [f"{name} ({cnt})" for name, cnt in top_docs]
        doc_value = " ".join(_doc_parts) if _doc_parts else "—"
    else:
        doc_value = "—"

    # Mission Coverage and Replay Rate (separate, clearer metrics)
    distinct_missions = len(company_mission_counts) or len(company_missions_seen)
    total_runs = sum(company_mission_counts.values())
    try:
        intensity = (total_runs / float(max(1, distinct_missions))) if distinct_missions > 0 else 1.0
    except Exception:
        intensity = 1.0
    # Redefine Coverage as Team Coverage: fraction of company teams that appeared
    try:
        teams_total = len(teams)
    except Exception:
        teams_total = 0
    try:
        coverage_pct = 100.0 * (len(represented_teams) / float(max(1, teams_total))) if teams_total else 0.0
    except Exception:
        coverage_pct = 0.0
    coverage_tier = _operational_exposure_tier_pct(coverage_pct)
    coverage_value = f"{coverage_tier} (Coverage: {coverage_pct:.0f}%)"
    replay_value = f"{intensity:.1f}× per mission"

    # Evenness across mission runs via CV (sd/mean)
    counts = list(company_mission_counts.values())
    cv = None
    if counts and distinct_missions > 0 and intensity > 0:
        mean_runs = intensity
        try:
            var = sum((c - mean_runs) ** 2 for c in counts) / float(len(counts))
            sd = var ** 0.5
            cv = (sd / mean_runs) if mean_runs > 0 else 0.0
        except Exception:
            cv = None
        # Build top-3 missions by share to visualize distribution
        try:
            total_runs = sum(company_mission_counts.values())
        except Exception:
            total_runs = 0
        mission_sorted = sorted(company_mission_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        top_missions = mission_sorted[:3]
        parts = []
        for mname, mcnt in top_missions:
            share = (mcnt / float(total_runs)) if total_runs > 0 else 0.0
            parts.append(f"{_abbr_mission_name(mname)} ({share:.0%})")
        tops = " ".join(parts) if parts else "—"
        evenness_value = (
            f"{_evenness_band(cv)} (CV {cv:.2f}) — Top Missions: {tops}"
            if isinstance(cv, (int, float))
            else "Undetermined (CV —)"
        )
    else:
        evenness_value = "Undetermined (CV —)"

    # Composite one-line saturation summary combining coverage, replay, evenness
    cover_norm = max(0.0, min(1.0, (coverage_pct or 0.0) / 100.0))
    # Replay normalization via banded scaling for intuitiveness
    if intensity <= 1.2:
        replay_norm = 0.2
    elif intensity <= 1.6:
        replay_norm = 0.4
    elif intensity <= 2.0:
        replay_norm = 0.6
    elif intensity <= 3.0:
        replay_norm = 0.8
    else:
        replay_norm = 1.0
    # Evenness normalization: lower CV -> higher score
    if isinstance(cv, (int, float)) and cv >= 0:
        evenness_norm = max(0.0, min(1.0, 1.0 - min(cv, 1.0)))
    else:
        evenness_norm = 0.5
    saturation_score = 100.0 * (0.4 * cover_norm + 0.4 * replay_norm + 0.2 * evenness_norm)
    if saturation_score <= 20.0:
        sat_tier = "ISOLATED"
    elif saturation_score <= 40.0:
        sat_tier = "LIMITED"
    elif saturation_score <= 60.0:
        sat_tier = "DIVERSE"
    elif saturation_score <= 80.0:
        sat_tier = "BROAD"
    else:
        sat_tier = "EXTENSIVE"
    sat_value = f"{sat_tier} (Team Coverage: {coverage_pct:.0f}% • {intensity:.1f}×)"

    # Cohesion Trend via top doctrine share (concentration) and Diversity (HHI)
    total_doc = sum(company_doc_counts.values())
    if total_doc > 0:
        try:
            top_doc_count = max(company_doc_counts.values())
        except Exception:
            top_doc_count = 0
        try:
            top_share_pct = 100.0 * (top_doc_count / float(max(1, total_doc)))
        except Exception:
            top_share_pct = 0.0
        coh_tier = _cohesion_concentration_tier(top_share_pct)
        coh_value = f"{coh_tier} (Top Doctrine Share: {top_share_pct:.0f}%)"
        try:
            hhi = sum((cnt / float(total_doc)) ** 2 for cnt in company_doc_counts.values())
            diversity_value = f"{_diversity_band_hhi(hhi)} (HHI {hhi:.2f})"
        except Exception:
            diversity_value = "Undetermined (HHI —)"
    else:
        coh_value = "UNDETERMINED"
        diversity_value = "—"

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
            if len(docs) == 1:
                div_line = f"  Doctrinal Divergence            :: {names} ({doc_lbl} : {max_cnt})"
            else:
                div_line = f"  Doctrinal Divergence            :: {names} (Mixed)"
    except Exception:
        div_line = None
    # Capture divergence value only
    if div_line and "::" in div_line:
        div_value = div_line.split("::", 1)[1].strip()
    elif div_line:
        div_value = div_line.strip()
    else:
        div_value = "—"

    # Render aligned key/value lines with padded labels so '::' columns align
    kv_items = [
        ("Operational Environments", env_value),
        ("Doctrinal Pattern", doc_value),
        ("Operational Saturation", sat_value),
        ("Operational Equilibrium", evenness_value),
        ("Cohesion Trend", coh_value),
        ("Doctrine Diversity", diversity_value),
        ("Doctrinal Divergence", div_value),
    ]
    try:
        label_width = max((len(k) for k, _ in kv_items), default=0)
    except Exception:
        label_width = 0
    for k, v in kv_items:
        lines.append(f"  {k:<{label_width}} :: {v}")

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
        top_share_pct = 0.0
        if company_doc_counts:
            dom_doc, dom_cnt = max(company_doc_counts.items(), key=lambda kv: (kv[1], kv[0]))
            total_cnt = sum(company_doc_counts.values()) or 1
            top_share_pct = 100.0 * (dom_cnt / float(total_cnt))
        comp_coherence = _cohesion_concentration_tier(top_share_pct)
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
        elif hc_ops_count <= 3 and comp_coherence in ("FOCUSED", "ORTHODOX", "MONOLITHIC"):
            lines.append(
                "  + High Command deployments were limited; doctrine indicates specialized orientation."
            )
            lines.append(
                f"  + Counsel calibrated for precision: {dom_doc or 'specialist doctrine'} in {dom_env or 'key theatres'}."
            )
        elif comp_exposure in ("BROAD", "EXTENSIVE") and comp_coherence in ("BALANCED", "LEANING"):
            lines.append(
                "  + Company operations spanned varied theatres; doctrine held adaptive coherence."
            )
            lines.append(
                "  + Librarius endorses flexible rites and cross-theatre stratagem rehearsal."
            )
        elif hc_ops_count >= 6 and comp_coherence in ("FOCUSED", "ORTHODOX", "MONOLITHIC"):
            lines.append(
                "  + Elevated High Command deployments under focused campaigns."
            )
            lines.append(
                f"  + Orientation sustained by {dom_doc or 'focused doctrine'} across {comp_exposure.lower()} exposure."
            )
        else:
            lines.append(
                "  + High Command maintained strategic oversight consistent with the dossier window."
            )
            lines.append(
                "  + Librarius counsel aligns with observed theatres and doctrinal posture."
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

    # Send response with structured embed to reduce wrapping
    try:
        embed = discord.Embed(
            title="Librarian Brief",
            description=f"{getattr(company, 'name', 'Unknown')} — Last {span_days} Days",
            color=0x2ecc71,
        )
        # Add concise KV items as fields
        for k, v in kv_items:
            embed.add_field(name=k, value=v, inline=False)
        # High Command Notes
        if 'hc_ops_count' in locals():
            hc_lines: List[str] = []
            try:
                if hc_ops_count <= 0:
                    hc_lines.append("High Command recorded no deployments across the dossier window.")
                    hc_lines.append("Strategic oversight maintained; archives curated, auguries sustained.")
                elif hc_ops_count <= 3 and comp_coherence in ("FOCUSED", "ORTHODOX", "MONOLITHIC"):
                    hc_lines.append("Limited deployments; doctrine indicates specialized orientation.")
                    hc_lines.append(f"Counsel calibrated for precision: {dom_doc or 'specialist doctrine'} in {dom_env or 'key theatres'}.")
                elif comp_exposure in ("BROAD", "EXTENSIVE") and comp_coherence in ("BALANCED", "LEANING"):
                    hc_lines.append("Operations spanned varied theatres; doctrine held adaptive coherence.")
                    hc_lines.append("Librarius endorses flexible rites and cross-theatre stratagem rehearsal.")
                elif hc_ops_count >= 6 and comp_coherence in ("FOCUSED", "ORTHODOX", "MONOLITHIC"):
                    hc_lines.append("Elevated deployments under focused campaigns.")
                    hc_lines.append(f"Orientation sustained by {dom_doc or 'focused doctrine'} across {comp_exposure.lower()} exposure.")
                else:
                    hc_lines.append("Strategic oversight consistent with the dossier window.")
                    hc_lines.append("Counsel aligns with observed theatres and doctrinal posture.")
            except Exception:
                pass
            if hc_lines:
                embed.add_field(name="High Command Notes", value="\n".join(f"• {x}" for x in hc_lines)[:1024], inline=False)

        view = ToggleFormatView(text_content=msg, embed=embed, default="ansi")
        await interaction.followup.send(content=msg, embed=None, view=view, ephemeral=True)
    except Exception as e:
        try:
            err_type = type(e).__name__
            err_msg = str(e)
            is_unknown = ("Unknown interaction" in err_msg) or ("10062" in err_msg)
            print(f"[Librarian Brief Error] {err_type}: {err_msg}")
            if is_unknown:
                # Fallback: post non-ephemeral message directly to the channel if available
                ch = getattr(interaction, "channel", None)
                if ch:
                    try:
                        await ch.send("(Fallback delivery)\n" + msg)
                        print("[Librarian Brief] Fallback message posted to channel.")
                        return
                    except Exception as se:
                        print(f"[Librarian Brief Fallback Error] {type(se).__name__}: {se}")
                # If channel not available or fallback fails, suppress raising to avoid 10062 bubbling
                return
        except Exception:
            pass
        # Non-10062 errors: re-raise for framework handling
        raise


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
        _cb = (CONFIG.get("combat_bonds") or {})
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
            ideal = (1.0 / 3.0)
            span = (2.0 / 3.0)
            excess_norm = max(0.0, (dom - ideal) / span)
        except Exception:
            excess_norm = max(0.0, dom - (1.0 / 3.0))
        penalty_factor = max(0.0, 1.0 - (dominance_alpha * excess_norm))

        final_score = int(round(base_score * penalty_factor))
        triples.append(((x, y, z), final_score))
    triples.sort(key=lambda t: t[1], reverse=True)
    return triples


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

    Optional config knobs (with safe defaults) from CONFIG.combat_bonds:
      per_partner_cap (int, default 5), depth_exponent (float, default 0.5)
    """
    # Configurable knobs
    try:
        _cb = (CONFIG.get("combat_bonds") or {})
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
        depth_factor = (bounded_total ** depth_exponent) if bounded_total > 0 else 0.0
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
        _cb = (CONFIG.get("combat_bonds") or {})
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
        "Red Scorpions",
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
            f"Auspex Window: Last {window_days} day(s)" if window_days is not None else f"Auspex Window: Last {window_span} engagements"
        ),
        color=0x2ecc71,
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

    embed.set_footer(text="These Combat Bonds may be invoked by decree of Watch Command.")
    return embed


class ToggleFormatView(discord.ui.View):
    def __init__(self, text_content: Optional[str] = None, embed: Optional[discord.Embed] = None, default: str = "ansi"):
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
                    child.disabled = (self.current == "ansi") or (not self.text_content) or too_long
                elif child.custom_id == "show_embed":
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
            await interaction.response.edit_message(content=self.text_content, embed=None, view=self)
        except Exception:
            # Fallback notify if edit fails (e.g., stale interaction)
            try:
                await interaction.followup.send("Unable to switch to PC/Console view.", ephemeral=True)
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


def _embed_from_ansi(title: str, text_block: str, color: int = 0x2ecc71) -> discord.Embed:
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

    # Company Command via shared rule
    company_command_members: List[discord.Member] = _resolve_company_command_members(company)

    # Compute last-N-day activity map from AAR records
    span_days = days if (isinstance(days, int) and days > 0) else 30
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

    # Initiation Rites Leadership: show the formation(s) with highest average inductions
    try:
        induction_avgs: List[Tuple[str, float]] = []
        for name, members in teams:
            counts = [_induction_count_for_user(str(getattr(m, "id", ""))) for m in members]
            avg_ind = (sum(counts) / float(len(counts))) if counts else 0.0
            induction_avgs.append((name, avg_ind))
        if induction_avgs:
            best_avg = max(avg for _n, avg in induction_avgs)
            epsilon = 1e-9
            winners = [n for n, avg in induction_avgs if abs(avg - best_avg) <= epsilon]
            winners_fmt = " / ".join([f"KT {n}" if n != "Company Command" else n for n in winners]) if winners else "N/A"
            lines.append(
                f"  Initiation Rites Leadership    :: {winners_fmt} (Avg Inductions: {best_avg:.2f})"
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
        elif hc_ops_count <= 2 and (care_ratio < 0.2 and stab_ratio < 0.2 and read_ratio < 0.2):
            lines.append(
                "  + Limited High Command deployments with favorable medical and stability readings."
            )
            lines.append(
                "  + Green posture: sanction forward drills at discretion; maintain recovery cadence."
            )
        elif hc_ops_count <= 4 and ((care_ratio >= 0.4) ^ (stab_ratio >= 0.4) ^ (read_ratio >= 0.4)):
            lines.append(
                "  + Mixed signals across readiness facets under a restrained High Command tempo."
            )
            lines.append(
                "  + Cautionary posture: apply targeted remediation while avoiding broad escalation."
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

    # Structured embed for Apothecary Brief to reduce wrapping
    try:
        # Debug output when running with debug flag (prints to stdout when BROADCAST_STATUS is False,
        # otherwise uses logger.debug). This helps trace member counts and active mappings.
        try:
            dbg_lines: List[str] = []
            dbg_lines.append("[DEBUG] Apothecary Brief internal state:")
            dbg_lines.append(f"  Role: {getattr(company, 'name', 'Unknown')}")
            try:
                dbg_lines.append(f"  role_members: {len(company_members)}")
            except Exception:
                dbg_lines.append("  role_members: <unavailable>")
            try:
                dbg_lines.append(f"  company_command_members: {len(company_command_members)}")
            except Exception:
                dbg_lines.append("  company_command_members: <unavailable>")
            try:
                dbg_lines.append(f"  recent_records (window): {len(recent_records)}")
            except Exception:
                dbg_lines.append("  recent_records: <unavailable>")
            try:
                dbg_lines.append(f"  active_map count: {len(active_map)}")
            except Exception:
                dbg_lines.append("  active_map: <unavailable>")
            try:
                if 'overall_stats' in locals():
                    dbg_lines.append(f"  overall_stats: count={overall_stats.get('count',0)} active={overall_stats.get('active',0)} absent={overall_stats.get('absent',0)}")
            except Exception:
                pass

            # Prepare readable member lists (name (id)) for company members and all units
            def _fmt_member(m: discord.Member) -> str:
                try:
                    name = getattr(m, 'nick', None) or getattr(m, 'display_name', None) or getattr(m, 'name', None) or str(getattr(m, 'id', ''))
                    return f"{name} ({getattr(m, 'id', '')})"
                except Exception:
                    return str(getattr(m, 'id', ''))

            try:
                # Company members active/inactive
                comp_active: List[str] = []
                comp_inactive: List[str] = []
                for m in company_members:
                    sid = str(getattr(m, 'id', ''))
                    if active_map.get(sid):
                        comp_active.append(_fmt_member(m))
                    else:
                        comp_inactive.append(_fmt_member(m))
                dbg_lines.append(f"  Company members active: {len(comp_active)}")
                dbg_lines.extend([f"    {x}" for x in comp_active[:100]])
                if len(comp_active) > 100:
                    dbg_lines.append(f"    ... and {len(comp_active)-100} more active members")
                dbg_lines.append(f"  Company members inactive: {len(comp_inactive)}")
                dbg_lines.extend([f"    {x}" for x in comp_inactive[:100]])
                if len(comp_inactive) > 100:
                    dbg_lines.append(f"    ... and {len(comp_inactive)-100} more inactive members")
            except Exception:
                dbg_lines.append("  Company member lists: <error building lists>")

            # Per-member AAR counts and AAR IDs (limited) for company members — helps trace who triggered active_map
            try:
                dbg_lines.append("  Per-member AAR counts (company members):")
                # Build mapping uid -> list of recent record ids where uid appears
                per_member_aars: Dict[str, List[str]] = {}
                for rec in recent_records:
                    rid = str(rec.get("record_id") or rec.get("id") or rec.get("message_id") or "")
                    for uid in rec.get("brother_ids") or []:
                        suid = str(uid)
                        if not suid:
                            continue
                        per_member_aars.setdefault(suid, []).append(rid or "<anon>")

                for m in company_members:
                    sid = str(getattr(m, 'id', ''))
                    if not sid:
                        continue
                    aars = per_member_aars.get(sid, [])
                    # limit list length to avoid blowup
                    sample = aars[:10]
                    dbg_lines.append(f"    { _fmt_member(m) }: {len(aars)} AARs -> {sample if sample else '[]'}")
            except Exception:
                dbg_lines.append("  Per-member AAR counts: <error building mapping>")

            try:
                # All units (teams + company command) active/inactive
                all_active: List[str] = []
                all_inactive: List[str] = []
                if 'all_units' in locals():
                    for m in all_units:
                        sid = str(getattr(m, 'id', ''))
                        if active_map.get(sid):
                            all_active.append(_fmt_member(m))
                        else:
                            all_inactive.append(_fmt_member(m))
                    dbg_lines.append(f"  All units active: {len(all_active)}")
                    dbg_lines.extend([f"    {x}" for x in all_active[:100]])
                    if len(all_active) > 100:
                        dbg_lines.append(f"    ... and {len(all_active)-100} more active members")
                    dbg_lines.append(f"  All units inactive: {len(all_inactive)}")
                    dbg_lines.extend([f"    {x}" for x in all_inactive[:100]])
                    if len(all_inactive) > 100:
                        dbg_lines.append(f"    ... and {len(all_inactive)-100} more inactive members")
            except Exception:
                dbg_lines.append("  All-unit lists: <error building lists>")

            dbg_msg = "\n".join(dbg_lines)
            if not BROADCAST_STATUS:
                print(dbg_msg)
            else:
                logger.debug(dbg_msg)
        except Exception:
            pass
        embed = discord.Embed(
            title="Apothecary Brief",
            description=f"{getattr(company, 'name', 'Unknown')} — Last {span_days} Days",
            color=0x2ecc71,
        )
        # Overall readiness
        try:
            o_count = int(
                overall_stats.get("count", 0) if "overall_stats" in locals() else 0
            )
            o_active = int(
                overall_stats.get("active", 0) if "overall_stats" in locals() else 0
            )
            p_active = (o_active / o_count) if o_count > 0 else 0.0
            embed.add_field(
                name="Overall Biological Readiness",
                value=f"{overall_ready} (Active {o_active}/{o_count} — {p_active:.0%})",
                inline=False,
            )
        except Exception:
            embed.add_field(name="Overall Biological Readiness", value=f"{overall_ready}", inline=False)

        # Care load concentration
        if care_stats is not None:
            c_avg = float(care_stats.get("avg", 0.0) or 0.0)
            c_sd = float(care_stats.get("stdev", 0.0) or 0.0)
            embed.add_field(
                name="Care Load Concentration",
                value=f"{care_team_label} (Avg Absent {c_avg:.2f}; SD {c_sd:.2f})",
                inline=True,
            )
        else:
            embed.add_field(name="Care Load Concentration", value=f"{care_team_label}", inline=True)

        # Stability outlier
        if stab_stats is not None:
            s_sd = float(stab_stats.get("stdev", 0.0) or 0.0)
            embed.add_field(
                name="Stability Outlier",
                value=f"{stab_team_label} (Stdev {s_sd:.2f})",
                inline=True,
            )
        else:
            embed.add_field(name="Stability Outlier", value=f"{stab_team_label}", inline=True)

        # Most stable formation
        if stable_best_stats is not None:
            mb_sd = float(stable_best_stats.get("stdev", 0.0) or 0.0)
            embed.add_field(
                name="Most Stable Formation",
                value=f"{stable_names_fmt} ({stable_tier}) (SD {mb_sd:.2f})",
                inline=True,
            )
        else:
            embed.add_field(
                name="Most Stable Formation",
                value=f"{stable_names_fmt} ({stable_tier})",
                inline=True,
            )

        # Company Command Status
        embed.add_field(
            name="Company Command Status",
            value=f"{cc_ready} READINESS — {cc_stab} STABILITY",
            inline=True,
        )

        # Gene-Seed Preservation
        if best_gene_team is not None:
            try:
                name, avg_gene = best_gene_team
                label = f"KT {name}" if name != "Company Command" else name
                embed.add_field(
                    name="Gene-Seed Preservation",
                    value=f"{label} (Avg Gene {avg_gene:.2f})",
                    inline=True,
                )
            except Exception:
                pass

        # Initiation Rites Leadership
        try:
            if 'winners_fmt' in locals() and 'best_avg' in locals():
                embed.add_field(
                    name="Initiation Rites Leadership",
                    value=f"{winners_fmt} (Avg Inductions {best_avg:.2f})",
                    inline=True,
                )
        except Exception:
            pass

        # High Command Notes
        try:
            hc_lines: List[str] = []
            hc_lines.append(f"Deployments in window: {hc_ops_count}")
            # Aggregate care/stability/readiness signals ratios already computed
            hc_lines.append(f"Care adverse ratio: {care_ratio:.0%}")
            hc_lines.append(f"Stability adverse ratio: {stab_ratio:.0%}")
            hc_lines.append(f"Readiness adverse ratio: {read_ratio:.0%}")
            embed.add_field(name="High Command Notes", value="\n".join(f"• {x}" for x in hc_lines)[:1024], inline=False)
        except Exception:
            pass

        view = ToggleFormatView(text_content=msg, embed=embed, default="ansi")
        await interaction.followup.send(content=msg, embed=None, view=view, ephemeral=True)
    except Exception:
        embed = _embed_from_ansi("Apothecary Brief", msg)
        view = ToggleFormatView(text_content=msg, embed=embed, default="ansi")
        await interaction.followup.send(content=msg, embed=None, view=view, ephemeral=True)


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
    channel = discord.utils.get(guild.channels, name="❖⋅⋅hall-of-glory⋅⋅❖")
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
    for i, kid in enumerate(recent_keys, start=1):
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
        try:
            _print_progress("Trophy Hall refresh", i, len(recent_keys))
        except Exception:
            pass
    index["_meta"] = meta
    _save_json_dict(TROPHY_HALL_INDEX_PATH, index)
    return added, updated


async def _parse_oath_message(msg: discord.Message):
    content = msg.content or ""
    lines = [ln.strip() for ln in content.splitlines()]
    user_id = None
    user_display_name = None
    if msg.mentions:
        m0 = msg.mentions[0]
        user_id = str(m0.id)
        try:
            user_display_name = (
                getattr(m0, "nick", None)
                or getattr(m0, "display_name", None)
                or getattr(m0, "name", None)
                or getattr(m0, "username", None)
            )
        except Exception:
            user_display_name = None
    oath_rank_at_time_raw = None
    for ln in lines:
        low = ln.lower()
        if low.startswith("current rank:") or low.startswith("rank:"):
            try:
                oath_rank_at_time_raw = ln.split(":", 1)[1].strip()
            except Exception:
                oath_rank_at_time_raw = ln.strip()
            break
    target_role_names = [getattr(r, "name", "") for r in msg.role_mentions]
    # Also parse plaintext oath targets anywhere in the message (case-insensitive)
    try:
        # Valid battle-line targets are strictly above Watch Veteran
        allowed_bl = [
            r for r in BATTLE_LINE_ORDER if r not in ("Watch Brother", "Watch Veteran")
        ]
        all_allowed_roles = set(allowed_bl) | CHAMPION_ROLES | SPECIALIST_ROLES | HIGH_COMMAND_ROLES

        # Prefer explicit "Oath:" line if present, otherwise search entire content
        oath_text = None
        for ln in lines:
            low = ln.lower()
            if low.startswith("oath"):
                try:
                    # Support formats like "Oath:", "Oath -", "Oath —"
                    split_tok = ":" if ":" in ln else ("-" if "-" in ln else "—" if "—" in ln else None)
                    if split_tok:
                        oath_text = ln.split(split_tok, 1)[1].strip()
                    else:
                        oath_text = ln.strip()
                except Exception:
                    oath_text = ln.strip()
                break
        text_to_search = (oath_text or content).lower()

        for r in all_allowed_roles:
            try:
                if r and r.lower() in text_to_search:
                    target_role_names.append(r)
            except Exception:
                continue
        # De-duplicate while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for n in target_role_names:
            if n and n not in seen:
                deduped.append(n)
                seen.add(n)
        target_role_names = deduped
    except Exception:
        pass
    has_oath = True if user_id else False
    guild_id = str(getattr(getattr(msg, "guild", None), "id", ""))
    channel_id = str(getattr(getattr(msg, "channel", None), "id", ""))
    message_link = (
        f"https://discord.com/channels/{guild_id}/{channel_id}/{msg.id}"
        if guild_id and channel_id
        else None
    )
    # Final sanitation: remove invalid battle-line oath targets (Brother/Veteran)
    sanitized_targets = [
        n
        for n in target_role_names
        if n and n not in ("Watch Brother", "Watch Veteran")
    ]
    entry = {
        "message_id": str(msg.id),
        "user_id": user_id,
        "user_display_name": user_display_name,
        "oath_rank_at_time_raw": oath_rank_at_time_raw,
        "oath_target_roles": sanitized_targets,
        "guild_id": guild_id,
        "channel_id": channel_id,
        "message_link": message_link,
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
    channel = discord.utils.get(guild.channels, name="❖⋅oaths⋅❖")
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
    for i, kid in enumerate(recent_keys, start=1):
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
        try:
            _print_progress("Oaths refresh", i, len(recent_keys))
        except Exception:
            pass
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

# Challenge roles source of truth (case-insensitive resolution at runtime)
# TODO: Fill with all standalone challenge role names and the 6 Terminus Slayer sub-challenge names.
CHALLENGE_ROLE_NAMES = [
    # Examples/placeholder; add all actual challenge role names here:
    "SOKG: Pipehitter",
    "Ardent Raider",
    "Centurion of the Fallen",
    "Black Laurels",
    "Crimson Laurels",
    "Terminus Slayer - Tactical",
    "Terminus Slayer - Assault",
    "Terminus Slayer - Vanguard",
    "Terminus Slayer - Sniper",
    "Terminus Slayer - Heavy",
    "Terminus Slayer - Bulwark",
    "Terminus Slayer - Techmarine",
    "Master Terminus Slayer"
]

def _resolve_challenge_roles(guild: Optional[discord.Guild]) -> List[discord.Role]:
    """Resolve configured challenge role names to Role objects by case-insensitive match.
    Skips names not found in the guild.
    """
    resolved: List[discord.Role] = []
    if not guild or not getattr(guild, "roles", None):
        return resolved
    # Build lookup by lower-cased role name
    by_name: Dict[str, discord.Role] = {}
    try:
        for r in getattr(guild, "roles", []):
            n = getattr(r, "name", None)
            if n:
                by_name[n.lower()] = r
    except Exception:
        by_name = {}
    for name in CHALLENGE_ROLE_NAMES:
        key = (name or "").lower()
        r = by_name.get(key)
        if r:
            resolved.append(r)
    return resolved

def _member_challenge_compliance(member: Optional[discord.Member], challenge_roles: List[discord.Role]) -> float:
    """Return member's individual challenge compliance: completed/total_possible.
    If no challenge roles are resolved or member is None, returns 0.0.
    """
    if not member or not challenge_roles:
        return 0.0
    try:
        member_role_ids = {str(getattr(r, "id", "")) for r in getattr(member, "roles", [])}
        resolved_ids = [str(getattr(r, "id", "")) for r in challenge_roles]
        total_possible = len([rid for rid in resolved_ids if rid])
        if total_possible == 0:
            return 0.0
        completed = sum(1 for rid in resolved_ids if rid in member_role_ids)
        return completed / float(total_possible)
    except Exception:
        return 0.0


def _has_stable_leadership(team_name: str, members: List[discord.Member]) -> bool:
    """Determine stable leadership per team rules.
    - Kill Teams: at least one Watch Sergeant who is not also a Watch Lieutenant or Watch Captain.
    - Company Command: at least one Watch Captain present.
    """
    try:
        if team_name == "Company Command":
            try:
                msg = f"CC leadership check — member count: {len(members)}"
                (print(msg) if not BROADCAST_STATUS else logger.debug(msg))
                for m in members:
                    names = _canonical_role_names(m)
                    label = (
                        getattr(m, "nick", None)
                        or getattr(m, "display_name", None)
                        or getattr(m, "name", None)
                        or getattr(m, "username", None)
                        or str(getattr(m, "id", ""))
                    )
                    try:
                        msg = f"CC member: {label} roles={sorted(list(names))}"
                    except Exception:
                        msg = f"CC member: {label}"
                    (print(msg) if not BROADCAST_STATUS else logger.debug(msg))
                    if "Watch Captain" in names:
                        msg = f"Stable leader present: {label} holds Watch Captain"
                        (print(msg) if not BROADCAST_STATUS else logger.debug(msg))
                        return True
            except Exception:
                pass
            return False

        # Kill Team: require a dedicated Sergeant (not outranked by Lt/Captain)
        for m in members:
            names = _canonical_role_names(m)
            if (
                "Watch Sergeant" in names
                and "Watch Lieutenant" not in names
                and "Watch Captain" not in names
            ):
                return True
        return False
    except Exception:
        return False


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
    # Recognized battle-line targets in the oath
    # Ignore Watch Brother/Veteran as valid oath targets
    valid_bl_targets = {"Watch Sergeant", "Watch Lieutenant", "Watch Captain"}
    bl_targets = [r for r in BATTLE_LINE_ORDER if r in targets and r in valid_bl_targets]
    # Recognized role targets (Champion/Specialist/High Command)
    targeted_roles = {
        r
        for r in targets
        if (r in CHAMPION_ROLES or r in SPECIALIST_ROLES or r in HIGH_COMMAND_ROLES)
    }

    # Use member's CURRENT roles as source of truth
    if not member:
        # Member missing/unresolvable at report time
        if targeted_roles:
            return "unclassified"
        if bl_targets:
            return "unclassified"
        return "unclassified"

    cur_idx = _current_battle_line_index(member)
    # Mixed-oath handling: fulfilled if ANY target satisfied (across roles or battle-line)
    role_satisfied = False
    if targeted_roles:
        role_satisfied = _member_has_any_role(member, targeted_roles)

    bl_satisfied = False
    if bl_targets:
        target_idxs = [BATTLE_LINE_ORDER.index(r) for r in bl_targets]
        if cur_idx is not None:
            # Any-of semantics: reaching any listed rank (or higher) fulfills
            bl_satisfied = any(cur_idx >= idx for idx in target_idxs)
        else:
            # Fallback: attempt to resolve rank token from display name if cur rank unresolved
            name_text = (
                getattr(member, "nick", None)
                or getattr(member, "display_name", None)
                or getattr(member, "name", None)
                or getattr(member, "username", None)
            )
            name_idx = _rank_index_from_text(name_text)
            if name_idx is not None:
                bl_satisfied = any(name_idx >= idx for idx in target_idxs)

    if role_satisfied or bl_satisfied:
        return "fulfilled"

    if targeted_roles or bl_targets:
        return "unfulfilled"

    # No recognizable targets -> UNCLASSIFIED
    return "unclassified"


def _discipline_tier(challenge_pct: float, has_stable_leadership: bool, dist_mean: float, dist_sd: float) -> str:
    """Classify discipline into five tiers using distribution-aware thresholds.
    Oaths are narrative only and not factored.

    Tiers:
    - EXEMPLARIS: leadership present and engagement notably above peers (z >= +0.8) or cp >= 65%.
    - STALWART: leadership present and above-average engagement (z >= +0.2) or cp >= 50%.
    - STEADFAST: leadership present and near-average engagement (z >= -0.2) or cp >= 30%.
    - LITURGICAL CORRECTION REQUIRED: leadership present but low engagement; OR no leadership with some engagement.
    - DISCIPLINE DERELICT: no stable leadership and very low engagement (z <= -0.8 or cp <= 10%).
    """
    try:
        cp = float(challenge_pct or 0.0)
    except Exception:
        cp = 0.0
    if cp < 0.0:
        cp = 0.0
    if cp > 100.0:
        cp = 100.0
    try:
        mu = float(dist_mean or 0.0)
        sd = float(dist_sd or 0.0)
    except Exception:
        mu, sd = 0.0, 0.0
    z = (cp - mu) / sd if sd and sd > 0.0 else 0.0

    # Leadership absent: cap at Requires Intervention, Lacking if truly low
    if not has_stable_leadership:
        if z <= -0.8 or cp <= 10.0:
            return "Discipline Derelict"
        return "Liturgical Correction Required"

    # Leadership present: allow full 4-tier expressiveness
    if z >= 0.8 or cp >= 65.0:
        return "Exemplaris"
    if z >= 0.2 or cp >= 50.0:
        return "Stalwart"
    if z >= -0.2 or cp >= 30.0:
        return "Steadfast"
    return "Liturgical Correction Required"


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
    # Company Command via shared rule; build debug reasons for visibility
    company_command_members: List[discord.Member] = _resolve_company_command_members(company)
    cc_debug: List[Tuple[discord.Member, str]] = []
    excluded_roles = {
        "Lord Executioner",
        "High Chaplain",
        "Forgemaster",
        "Void Warden",
        "Voidwarden",
        "Chief Apothecary",
    }
    for m in company_members:
        names = _canonical_role_names(m)
        if any(er in names for er in excluded_roles):
            reason = "excluded: high-command" if "Lord Executioner" not in names else "excluded: lord-executioner"
            cc_debug.append((m, reason))
            continue
        included = m in company_command_members
        has_lt = "Watch Lieutenant" in names
        has_capt = "Watch Captain" in names
        is_specialist = any(r in names for r in SPECIALIST_ROLES)
        is_company_champion = "Company Champion" in names
        if included:
            reason = (
                "captain" if has_capt else ("lieutenant" if has_lt else ("specialist" if is_specialist else "champion"))
            )
            cc_debug.append((m, reason))
    if company_command_members:
        # Debug-only: dump Company Command roster to terminal
        try:
            header = (
                f"Company Command roster for {getattr(company, 'name', 'Unknown')} — count: {len(company_command_members)}"
            )
            (print(header) if not BROADCAST_STATUS else logger.debug(header))
            for m, reason in cc_debug:
                label = (
                    getattr(m, "nick", None)
                    or getattr(m, "display_name", None)
                    or getattr(m, "name", None)
                    or getattr(m, "username", None)
                    or str(getattr(m, "id", ""))
                )
                try:
                    role_list = sorted(list(_canonical_role_names(m)))
                    line = f" - {label} [{', '.join(role_list)}]; reason={reason}"
                except Exception:
                    line = f" - {label}; reason={reason}"
                (print(line) if not BROADCAST_STATUS else logger.debug(line))
        except Exception:
            pass
        teams.append(("Company Command", company_command_members))
    return teams


def _build_chaplain_report(guild: discord.Guild, company: discord.Role):
    trophy = _load_json_dict(TROPHY_HALL_INDEX_PATH)
    oaths = _load_json_dict(OATHS_INDEX_PATH)
    # Challenge roles resolved from guild at runtime (case-insensitive)
    challenge_roles: List[discord.Role] = _resolve_challenge_roles(guild)
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
    # Unclassified entries are counted for display only; no ledger persisted

    # Pre-pass: compute per-team challenge compliance and leadership for distribution
    team_challenge: Dict[str, float] = {}
    team_leadership: Dict[str, bool] = {}
    # Structured metrics collected for embed formatting later
    teams_metrics: List[Dict[str, object]] = []
    for team_name, members in teams:
        member_ids = {
            str(getattr(m, "id", "")) for m in members if getattr(m, "id", None)
        }
        member_list = [m for m in members if str(getattr(m, "id", "")) in member_ids]
        if member_list:
            try:
                avg_pre = sum(_member_challenge_compliance(m, challenge_roles) for m in member_list) / float(len(member_list))
                cp_pre = 100.0 * avg_pre
            except Exception:
                cp_pre = 0.0
        else:
            cp_pre = 0.0
        team_challenge[team_name] = cp_pre
        team_leadership[team_name] = _has_stable_leadership(team_name, member_list)

    cp_values = list(team_challenge.values())
    try:
        mu = (sum(cp_values) / float(len(cp_values))) if cp_values else 0.0
    except Exception:
        mu = 0.0
    try:
        if cp_values and len(cp_values) >= 2:
            var = sum((x - mu) ** 2 for x in cp_values) / float(len(cp_values))
            sd = var ** 0.5
        else:
            sd = 0.0
    except Exception:
        sd = 0.0

    for team_name, members in teams:
        member_ids = {
            str(getattr(m, "id", "")) for m in members if getattr(m, "id", None)
        }
        if not member_ids:
            continue
        # Compute average of individual member compliance
        member_list = [m for m in members if str(getattr(m, "id", "")) in member_ids]
        if member_list:
            avg = sum(_member_challenge_compliance(m, challenge_roles) for m in member_list) / float(len(member_list))
            challenge_pct = 100.0 * avg
        else:
            challenge_pct = 0.0
        oath_users = [uid for uid in member_ids if uid in oath_entries_by_user]
        oath_participation_pct = 100.0 * len(oath_users) / max(1, len(member_ids))
        # Stable leadership per new rules (precomputed)
        has_stable_leadership = team_leadership.get(team_name, False)
        fulfilled = 0
        unfulfilled = 0
        unclassified = 0
        # Collect per-oath statuses for persistence
        per_oath_status: List[Tuple[str, str]] = []  # (message_id, status)
        for uid in oath_users:
            m = next((mm for mm in members if str(getattr(mm, "id", "")) == uid), None)
            if not m:
                continue
            entry = oath_entries_by_user.get(uid) or {}
            status = _evaluate_oath(m, entry)
            mid = str(entry.get("message_id") or "")
            if mid:
                per_oath_status.append((mid, status))
            if status == "fulfilled":
                fulfilled += 1
            elif status == "unfulfilled":
                unfulfilled += 1
            else:
                unclassified += 1
        # Discipline now based on challenge compliance (distribution-aware) and leadership presence only
        tier = _discipline_tier(challenge_pct, has_stable_leadership, mu, sd)
        lines.append(f"  KT {team_name}")
        lines.append(f"    Discipline Status     :: {tier.upper()}")
        lines.append(
            f"    Oath Adherence        :: {oath_participation_pct:.0f}%  (Fulfilled {fulfilled} | Unfulfilled {unfulfilled} | Unclassified {unclassified})"
        )
        lines.append(f"    Challenge Compliance  :: {challenge_pct:.0f}%")
        # Collect structured metrics for embed
        teams_metrics.append({
            "name": str(team_name),
            "discipline": str(tier).upper(),
            "oath_pct": float(oath_participation_pct),
            "fulfilled": int(fulfilled),
            "unfulfilled": int(unfulfilled),
            "unclassified": int(unclassified),
            "challenge_pct": float(challenge_pct),
        })
        # Persist oath status back into OATHS index
        try:
            for mid, st in per_oath_status:
                oe = oaths.get(mid)
                if isinstance(oe, dict):
                    oe["status"] = st
                    oe["status_updated_at"] = datetime.utcnow().isoformat()
                    oaths[mid] = oe
        except Exception:
            pass
    # High Command Notes (state-based; no window references)
    hc_notes: List[str] = []
    try:
        hc_ids: set[str] = set()
        for m in getattr(guild, "members", []):
            names = _canonical_role_names(m)
            if any(r in names for r in HIGH_COMMAND_ROLES):
                uid = str(getattr(m, "id", ""))
                if uid:
                    hc_ids.add(uid)

        # Oath evaluation for High Command
        hc_fulfilled = hc_unfulfilled = hc_unclassified = 0
        for uid, entry in oath_entries_by_user.items():
            if uid not in hc_ids:
                continue
            member = None
            try:
                member = guild.get_member(int(uid)) if guild else None
            except Exception:
                member = None
            status = _evaluate_oath(member, entry or {}) if member else "unclassified"
            if status == "fulfilled":
                hc_fulfilled += 1
            elif status == "unfulfilled":
                hc_unfulfilled += 1
            else:
                hc_unclassified += 1

        hc_total_oath = hc_fulfilled + hc_unfulfilled + hc_unclassified
        hc_fulfill_rate = (100.0 * hc_fulfilled / hc_total_oath) if hc_total_oath > 0 else 0.0

        # Challenge compliance for High Command from member roles (average compliance)
        hc_members: List[discord.Member] = []
        try:
            for uid in hc_ids:
                m = guild.get_member(int(uid)) if guild else None
                if m:
                    hc_members.append(m)
        except Exception:
            hc_members = []
        if hc_members:
            hc_avg = sum(_member_challenge_compliance(m, challenge_roles) for m in hc_members) / float(len(hc_members))
            hc_challenge_pct = 100.0 * hc_avg
        else:
            hc_challenge_pct = 0.0

        lines.append("------------------------------------------------------------------------------")
        lines.append("  High Command Notes:")
        if hc_total_oath == 0 and hc_challenge_pct <= 0.0:
            lines.append(
                "  + High Command maintained a posture of oversight, issuing guidance rather than formal oaths."
            )
            hc_notes.append("Oversight posture; guidance predominates over formal oaths.")
        elif hc_fulfill_rate >= 75.0 and hc_challenge_pct >= 75.0:
            lines.append(
                "  + High Command discipline stands as a doctrinal exemplar for the company."
            )
            hc_notes.append("Discipline exemplary across oaths and challenges.")
        elif hc_fulfill_rate >= 75.0 and hc_challenge_pct < 50.0:
            lines.append(
                "  + High Command ritual adherence is exemplary; progression remains deliberately measured."
            )
            hc_notes.append("Ritual adherence exemplary; progression measured.")
        elif hc_total_oath > 0 and (hc_unclassified / float(hc_total_oath)) >= 0.5:
            lines.append(
                "  + Edicts predominated over formal oaths; statuses remain pending codification."
            )
            hc_notes.append("Edicts predominate; many statuses pending codification.")
        else:
            lines.append(
                "  + High Command discipline reflects command authority rather than aspirational\n    progression."
            )
            hc_notes.append("Discipline reflects command authority over aspirational progression.")
    except Exception:
        pass

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
    # Unclassified ledger removed; statuses persisted in Oaths index only
    # Persist updated Oaths index statuses
    try:
        _save_json_dict(OATHS_INDEX_PATH, oaths)
    except Exception:
        pass
    # Return both ANSI message and structured metrics for embed formatting
    return msg, {"company": getattr(company, "name", "Unknown"), "teams": teams_metrics, "hc_notes": hc_notes}


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
    report, chaplain = _build_chaplain_report(guild, company)
    # Build structured embed to reduce wrapping
    try:
        embed = discord.Embed(
            title="Chaplain Brief",
            description=f"{chaplain.get('company','Unknown')} — Cached Sources: Trophy Hall, Record of Oaths",
            color=0x2ecc71,
        )
        teams = chaplain.get("teams", []) or []
        # Limit to 20 fields to respect embed limits; use concise formatting per team
        MAX_TEAM_FIELDS = 20
        for idx, t in enumerate(teams[:MAX_TEAM_FIELDS]):
            name = str(t.get("name", "Unknown"))
            discipline = str(t.get("discipline", "UNDETERMINED"))
            chall = float(t.get("challenge_pct", 0.0) or 0.0)
            oath_pct = float(t.get("oath_pct", 0.0) or 0.0)
            f = int(t.get("fulfilled", 0) or 0)
            u = int(t.get("unfulfilled", 0) or 0)
            x = int(t.get("unclassified", 0) or 0)
            value = (
                f"Discipline {discipline}\n"
                f"Challenge {chall:.0f}%\n"
                f"Oath {oath_pct:.0f}% (F {f} | U {u} | Unc {x})"
            )
            embed.add_field(name=f"KT {name}", value=value, inline=True)

        # High Command Notes
        hc_notes = chaplain.get("hc_notes", []) or []
        if hc_notes:
            embed.add_field(
                name="High Command Notes",
                value="\n".join(f"• {n}" for n in hc_notes)[:1024],
                inline=False,
            )

        view = ToggleFormatView(text_content=report, embed=embed, default="ansi")
        await interaction.followup.send(content=report, embed=None, view=view, ephemeral=True)
    except Exception:
        # Fallback to ANSI within embed
        embed = _embed_from_ansi("Chaplain Brief", report)
        view = ToggleFormatView(text_content=report, embed=embed, default="ansi")
        await interaction.followup.send(content=report, embed=None, view=view, ephemeral=True)


@bot.tree.command(
    name="high_command_brief",
    description="High Command summary brief: strategic company-level metrics.",
)
@app_commands.describe(
    days="Optional: number of days to include (default 30)",
)
async def high_command_brief(
    interaction: discord.Interaction,
    days: Optional[int] = 30,
):
    # Restrict to allowed channel and High Command only
    if not is_allowed_channel(interaction):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    if not is_high_command(interaction.user):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True, ephemeral=True)

    guild = interaction.guild
    if not guild:
        await interaction.followup.send("Guild unavailable.", ephemeral=True)
        return

    span_days = days if (isinstance(days, int) and days > 0) else 30
    recent = _get_missions_last_days(span_days)

    # Collect company roles in guild (exclude champion roles like 'Company Champion')
    company_roles: List[discord.Role] = [
        r
        for r in getattr(guild, "roles", [])
        if "company" in (getattr(r, "name", "") or "").lower()
        and (getattr(r, "name", "") not in CHAMPION_ROLES)
    ]
    company_by_id: Dict[str, discord.Role] = {str(getattr(r, "id")): r for r in company_roles}
    # Initialize counters
    deploy_counts: Dict[str, int] = {str(getattr(r, "id")): 0 for r in company_roles}
    armory_sums: Dict[str, float] = {str(getattr(r, "id")): 0.0 for r in company_roles}
    armory_counts: Dict[str, int] = {str(getattr(r, "id")): 0 for r in company_roles}
    gene_carried_counts: Dict[str, int] = {str(getattr(r, "id")): 0 for r in company_roles}
    gene_total_counts: Dict[str, int] = {str(getattr(r, "id")): 0 for r in company_roles}
    init_trials_counts: Dict[str, int] = {str(getattr(r, "id")): 0 for r in company_roles}
    init_trials_success: Dict[str, int] = {str(getattr(r, "id")): 0 for r in company_roles}

    # For chaplain flagging: prepare team membership map per company
    company_team_members: Dict[str, Dict[str, set]] = {}
    for comp in company_roles:
        teams = _collect_company_teams(guild, comp)
        mapping: Dict[str, set] = {}
        for tname, members in teams:
            mapping[str(tname)] = {str(getattr(m, "id", "")) for m in members if getattr(m, "id", None)}
        company_team_members[str(getattr(comp, "id"))] = mapping

    # Helper to get company role ids for a member
    def member_company_ids(member: Optional[discord.Member]) -> set:
        ids = set()
        if not member:
            return ids
        for r in getattr(member, "roles", []):
            rn = getattr(r, "name", "") or ""
            if "company" in rn.lower():
                ids.add(str(getattr(r, "id", "")))
        return ids

    # Determine which records include High Command presence and per-company attribution
    hc_mission_count = 0
    doctrine_counter: Counter = Counter()
    env_counter: Counter = Counter()

    # Build set of high command user ids in guild for quick lookup
    # Only include members who are recognized as High Command AND hold one of the
    # specified High-Command-type roles (strict membership filter).
    REQUIRED_HC_ROLES = {
        "watch master",
        "lord executioner",
        "forgemaster",
        "chief apothecary",
        "void warden",
        "voidwarden",
        "high chaplain",
    }
    hc_ids = set()
    for m in getattr(guild, "members", []):
        try:
            if not is_high_command(m):
                continue
            names = _canonical_role_names(m)
            names_lc = {n.lower() for n in (names or set())}
            if names_lc & REQUIRED_HC_ROLES:
                uid = str(getattr(m, "id", ""))
                if uid:
                    hc_ids.add(uid)
        except Exception:
            continue

    # Iterate recent records
    for rec in recent:
        bros = [str(b) for b in (rec.get("brother_ids") or [])]
        if not bros:
            continue
        if not any(b in hc_ids for b in bros):
            continue
        # High Command participated in this mission
        hc_mission_count += 1
        # Determine involved companies for mission by checking members' company roles
        involved_comp_ids: set = set()
        for b in bros:
            try:
                mb = guild.get_member(int(b)) if guild else None
            except Exception:
                mb = None
            if mb:
                involved_comp_ids |= member_company_ids(mb)
        # Also inspect mission text for role mention (legacy company mention in mission)
        try:
            mtext = rec.get("mission") or ""
            for m in re.finditer(r"<@&(?P<id>\d+)>", str(mtext)):
                rid = m.group("id")
                if rid in company_by_id:
                    involved_comp_ids.add(rid)
        except Exception:
            pass

        # Tag environment and doctrine
        envs, docs, _canon = _tags_for_record(rec)
        for e in envs:
            env_counter[e] += 1
        for d in docs:
            doctrine_counter[d] += 1

        # Attribute metrics to each company involved in this mission
        for cid in list(involved_comp_ids):
            if cid not in deploy_counts:
                # Unknown company role (skip)
                continue
            deploy_counts[cid] += 1
            # Armory
            try:
                arm = rec.get("armory_data")
                if arm is not None:
                    arm_v = float(arm)
                    armory_sums[cid] += arm_v
                    armory_counts[cid] += 1
            except Exception:
                pass
            # Gene-seed
            try:
                gene_total_counts[cid] += 1
                if (rec.get("gene_seed_status") or "").lower() == "carried":
                    gene_carried_counts[cid] += 1
            except Exception:
                pass
            # Initiation trials
            try:
                if bool(rec.get("initiation_trial")):
                    init_trials_counts[cid] += 1
                    if float(rec.get("points_for_op") or 0.0) > 0.0:
                        init_trials_success[cid] += 1
            except Exception:
                pass

    # Primary Deployment Company
    total_hc_ops = hc_mission_count or 1
    primary_comp_id = None
    if deploy_counts:
        primary_comp_id = max(deploy_counts.items(), key=lambda kv: kv[1])[0]

    # Deployment distribution (sorted high->low)
    distribution_entries: List[Tuple[str, int, float]] = []
    for cid, cnt in sorted(deploy_counts.items(), key=lambda kv: kv[1], reverse=True):
        role = company_by_id.get(cid)
        name = getattr(role, "name", "Unknown") if role else "Unknown"
        pct = (100.0 * cnt / total_hc_ops) if total_hc_ops else 0.0
        distribution_entries.append((name, cnt, pct))

    # Least-attended company
    least_comp_id = None
    if deploy_counts:
        least_comp_id = min(deploy_counts.items(), key=lambda kv: kv[1])[0]

    # Apothecarion Readiness Index: combined (gene_recovery_rate + induction_efficiency)/2 -> lowest
    apoth_scores: Dict[str, float] = {}
    for cid in deploy_counts.keys():
        gt = gene_total_counts.get(cid, 0)
        gc = gene_carried_counts.get(cid, 0)
        gene_rate = (gc / gt) if gt > 0 else 0.0
        itot = init_trials_counts.get(cid, 0)
        isum = init_trials_success.get(cid, 0)
        induction_eff = (isum / itot) if itot > 0 else 0.0
        apoth_scores[cid] = (gene_rate + induction_eff) / 2.0
    apoth_lowest_id = None
    if apoth_scores:
        apoth_lowest_id = min(apoth_scores.items(), key=lambda kv: kv[1])[0]

    # Chaplaincy Stability Flag: count teams flagged during HC missions
    chaplain_flags: Dict[str, int] = {str(getattr(r, 'id')): 0 for r in company_roles}
    # Build chaplain metrics once per company
    chaplain_cache: Dict[str, List[dict]] = {}
    for comp in company_roles:
        try:
            _, meta = _build_chaplain_report(guild, comp)
            chaplain_cache[str(getattr(comp, 'id'))] = meta.get('teams', []) or []
        except Exception:
            chaplain_cache[str(getattr(comp, 'id'))] = []

    # For each recent mission with HC, check teams present and if discipline is problematic
    problematic = {"LITURGICAL CORRECTION REQUIRED", "DISCIPLINE DERELICT"}
    for rec in recent:
        bros = [str(b) for b in (rec.get("brother_ids") or [])]
        if not bros or not any(b in hc_ids for b in bros):
            continue
        # For each company, check teams that intersect participants
        for cid, teams_map in company_team_members.items():
            if cid not in company_by_id:
                continue
            # if no intersection skip
            team_present = False
            for tname, mids in teams_map.items():
                if set(bros) & mids:
                    # lookup discipline for this team from chaplain_cache
                    teams_meta = chaplain_cache.get(cid, [])
                    for tm in teams_meta:
                        if str(tm.get('name')) == str(tname):
                            disc = str(tm.get('discipline', '')).upper()
                            if disc in problematic:
                                chaplain_flags[cid] = chaplain_flags.get(cid, 0) + 1
                            break

    # Librarius Operational Doctrine Bias
    # Aggregate raw environment tags into macro categories via _env_macro_for
    macro_env_counts: Counter = Counter()
    for ename, cnt in env_counter.items():
        try:
            macro = _env_macro_for(ename)
        except Exception:
            macro = _env_macro_for(ename)
        macro_env_counts[macro] += cnt
    top_env = macro_env_counts.most_common(3)
    top_docs = doctrine_counter.most_common(3)

    # Derive top doctrine share and cohesion/exposure tiers for Librarius note
    dom_doc = None
    top_share_pct = 0.0
    if doctrine_counter:
        dom_doc, dom_cnt = max(doctrine_counter.items(), key=lambda kv: (kv[1], kv[0]))
        total_docs_cnt = sum(doctrine_counter.values()) or 1
        top_share_pct = 100.0 * (dom_cnt / float(total_docs_cnt))
    comp_coherence = _cohesion_concentration_tier(top_share_pct)
    comp_exposure = _operational_exposure_tier(len(env_counter))

    # Mechanicus Yield Priority: highest average armory during HC missions
    mech_priority_id = None
    mech_avg: Dict[str, float] = {}
    for cid in armory_counts.keys():
        cnt = armory_counts.get(cid, 0)
        if cnt > 0:
            avg = armory_sums.get(cid, 0.0) / float(cnt)
            mech_avg[cid] = avg
    if mech_avg:
        mech_priority_id = max(mech_avg.items(), key=lambda kv: kv[1])[0]

    # Build brief text (aligned columns)
    lines: List[str] = []
    lines.append("```ansi")
    lines.append("\u001b[32m==============================================================================")
    lines.append("  WATCH FORTRESS JERICHO // HIGH COMMAND BRIEF")
    lines.append("  OPERATION-SCRIBE SERVITOR — STRATEGIC COMPANY SUMMARY")
    lines.append("==============================================================================")
    lines.append(f"  Window: Last {span_days} Days | HC Deployments Observed: {hc_mission_count}")
    lines.append("------------------------------------------------------------------------------")

    # Prepare key/value items and compute uniform label width for alignment
    kv_items: List[Tuple[str, str]] = []

    # 2 Deployment distribution (compact, single-line)
    if distribution_entries:
        dist_parts: List[str] = []
        for name, cnt, pct in distribution_entries:
            short = name
            if isinstance(name, str) and name.startswith("Watch Company "):
                short = name.replace("Watch Company ", "")
            dist_parts.append(f"{short} {cnt} ops ({pct:.0f}%)")
        kv_items.append(("Deployment Distribution", " | ".join(dist_parts)))
    else:
        kv_items.append(("Deployment Distribution", "—"))

    # 4 Apothecarion Readiness
    if apoth_lowest_id and apoth_lowest_id in company_by_id:
        val = apoth_scores.get(apoth_lowest_id, 0.0)
        kv_items.append(("Apothecarion Readiness Index", f"{company_by_id[apoth_lowest_id].name} (Score: {val:.2f})"))
    else:
        kv_items.append(("Apothecarion Readiness Index", "UNDETERMINED"))

    # 5 Chaplaincy — Average Discipline per company
    tier_map: Dict[str, int] = {
        "EXEMPLARIS": 5,
        "STALWART": 4,
        "STEADFAST": 3,
        "LITURGICAL CORRECTION REQUIRED": 2,
        "DISCIPLINE DERELICT": 1,
    }

    def _tier_from_score(avg: float) -> str:
        if avg >= 4.5:
            return "Exemplaris"
        if avg >= 3.5:
            return "Stalwart"
        if avg >= 2.5:
            return "Steadfast"
        if avg >= 1.5:
            return "Liturgical Correction Required"
        return "Discipline Derelict"

    chap_parts: List[str] = []
    chap_avg_map: Dict[str, float] = {}
    for comp in company_roles:
        cid = str(getattr(comp, "id"))
        cname = getattr(comp, "name", "Unknown")
        short = cname
        if isinstance(cname, str) and cname.startswith("Watch Company "):
            short = cname.replace("Watch Company ", "")
        teams_meta = chaplain_cache.get(cid, [])
        scores: List[int] = []
        for tm in teams_meta:
            disc = str(tm.get("discipline", "")).upper()
            if disc in tier_map:
                scores.append(tier_map[disc])
        if scores:
            avg = sum(scores) / float(len(scores))
            tier_label = _tier_from_score(avg)
            chap_parts.append(f"{short} {tier_label.upper()} ({avg:.2f})")
            chap_avg_map[cid] = avg
        else:
            chap_parts.append(f"{short} —")
    kv_items.append(("Chaplaincy — Avg Discipline", " | ".join(chap_parts)))

    # 6 Librarius Operational Doctrine Bias (split: top doctrines, top environments)
    if top_docs:
        docs_str = ", ".join([f"{d[0]} ({d[1]})" for d in top_docs])
        kv_items.append(("Librarius — Top Doctrines", docs_str))
    else:
        kv_items.append(("Librarius — Top Doctrines", "UNDETERMINED"))

    # Cohesion metric: dominant doctrine share and qualitative tier
    kv_items.append(("Librarius — Cohesion", f"{comp_coherence} ({dom_doc or '—'} {top_share_pct:.0f}%)"))

    if top_env:
        # Order top environments by their macro category per ENV_MACROS_ORDER
        def _env_order_key(kv: Tuple[str, int]) -> Tuple[int, int]:
            name, cnt = kv
            macro = _env_macro_for(name)
            try:
                idx = ENV_MACROS_ORDER.index(macro)
            except Exception:
                idx = len(ENV_MACROS_ORDER) + 1
            return (idx, -cnt)

        ordered_envs = sorted(top_env, key=_env_order_key)
        env_str = " | ".join([f"{e[0]} ({e[1]})" for e in ordered_envs])
        kv_items.append(("Librarius — Top Environments", env_str))
    else:
        kv_items.append(("Librarius — Top Environments", "UNDETERMINED"))

    # 7 Mechanicus Yield Priority
    if mech_priority_id and mech_priority_id in company_by_id:
        kv_items.append(("Mechanicus Yield Priority", f"{company_by_id[mech_priority_id].name} (Avg Armory: {mech_avg.get(mech_priority_id,0.0):.2f})"))
    else:
        kv_items.append(("Mechanicus Yield Priority", "UNDETERMINED"))

    try:
        label_width = max((len(k) for k, _ in kv_items), default=0)
    except Exception:
        label_width = 0
    for k, v in kv_items:
        lines.append(f"  {k:<{label_width}} :: {v}")

    lines.append("------------------------------------------------------------------------------")
    # Synthesized High Command Note
    notes: List[str] = []
    try:
        if primary_comp_id and deploy_counts.get(primary_comp_id,0) > (0.5 * total_hc_ops):
            notes.append(f"High Command deployments concentrated in {company_by_id[primary_comp_id].name}; consider redistributing oversight.")
        if apoth_lowest_id and apoth_scores.get(apoth_lowest_id,1.0) < 0.25:
            notes.append(f"Apothecarion attention advised for {company_by_id[apoth_lowest_id].name}; low gene/induction metrics.")
        # Librarius synthesized note based on doctrine cohesion and exposure
        try:
            if comp_coherence and comp_exposure:
                if comp_exposure in ("BROAD", "EXTENSIVE") and comp_coherence in ("BALANCED", "LEANING"):
                    notes.append("Librarius: operations spanned varied theatres; doctrine held adaptive coherence.")
                elif hc_mission_count >= 6 and comp_coherence in ("FOCUSED", "ORTHODOX", "MONOLITHIC"):
                    notes.append("Librarius: elevated focused campaigns with concentrated doctrine.")
                elif comp_coherence in ("MONOLITHIC", "ORTHODOX"):
                    notes.append("Librarius: doctrinal concentration observed; recommend cross-theatre doctrine rehearsal.")
        except Exception:
            pass

        # Recommend chaplain intervention only when average discipline falls below threshold
        try:
            if chap_avg_map:
                worst_chap_id, worst_avg = min(chap_avg_map.items(), key=lambda kv: kv[1])
                if worst_avg is not None and worst_avg < 2.5 and worst_chap_id in company_by_id:
                    notes.append(f"Chaplaincy intervention recommended for {company_by_id[worst_chap_id].name}.")
        except Exception:
            pass
        if mech_priority_id:
            notes.append(f"Mechanicus: prioritize inspections of {company_by_id[mech_priority_id].name} armories.")
    except Exception:
        pass
    if not notes:
        notes.append("High Command posture nominal; no immediate strategic alerts.")

    lines.append("  High Command Note:")
    for n in notes:
        lines.append(f"  + {n}")
    lines.append("==============================================================================")
    lines.append("\u001b[0m```")

    report = "\n".join(lines)
    await interaction.followup.send(content=report, ephemeral=True)


if __name__ == "__main__":
    _main()
