"""Publish a sanitized roster snapshot to the external Strategium service."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

from . import _bot_globals as _g

DEFAULT_PUBLISH_INTERVAL_MINUTES = 5
RANK_KEYS = {
    "Watch Master": "watch_master",
    "Forgemaster": "forgemaster",
    "Forge Master": "forgemaster",
    "Void Warden": "void_warden",
    "Chief Apothecary": "chief_apothecary",
    "High Chaplain": "high_chaplain",
    "Blade Master": "blade_master",
    "Huntmaster": "huntmaster",
    "Hunt Master": "huntmaster",
    "Venerable Dreadnought": "venerable_dreadnought",
    "Honored Dreadnought": "honored_dreadnought",
    "Watch Captain": "watch_captain",
    "First Blade": "first_blade",
    "Watch Apothecary": "apothecary",
    "Watch Chaplain": "chaplain",
    "Watch Librarian": "librarian",
    "Watch Lieutenant": "watch_lieutenant",
    "Watch Techmarine": "techmarine",
    "Veteran Sergeant": "veteran_sergeant",
    "Watch Sergeant": "watch_sergeant",
    "Bladeguard": "blade_guard",
    "Oathsworn": "oathsworn",
    "Watch Veteran": "watch_veteran",
    "Watch Brother": "watch_brother",
}
FORMATION_BY_RANK = {
    "forgemaster": "armory",
    "techmarine": "armory",
    "dreadnought": "armory",
    "honored_dreadnought": "armory",
    "venerable_dreadnought": "armory",
    "void_warden": "librarius",
    "librarian": "librarius",
    "high_chaplain": "reclusiam",
    "chaplain": "reclusiam",
    "chief_apothecary": "apothecarion",
    "apothecary": "apothecarion",
    "blade_master": "hall_of_blades",
    "first_blade": "hall_of_blades",
    "blade_guard": "hall_of_blades",
    "huntmaster": "black_vault",
    "kill_marine": "black_vault",
}
COMPANIES = {
    "Watch Company Primus": 1,
    "Watch Company Secundus": 2,
    "Watch Company Tertius": 3,
    "Watch Company Quartus": 4,
    "Watch Company Quintus": 5,
}
COMPANY_NAMES = {1: "Primus", 2: "Secundus", 3: "Tertius", 4: "Quartus", 5: "Quintus"}
FORMATION_STATS_KEYS = {
    "Armory": "armory",
    "Librarius": "librarius",
    "Reclusiam": "reclusiam",
    "Apothecarion": "apothecarion",
    "Blades": "hall_of_blades",
}


def _config() -> dict[str, Any]:
    return (_g.CONFIG or {}).get("strategium_publish") or {}


def publish_url() -> str:
    return str(os.getenv("STRATEGIUM_PUBLISH_URL") or _config().get("url") or "").strip()


def publish_secret() -> str:
    return str(os.getenv("STRATEGIUM_BOT_SHARED_SECRET") or _config().get("shared_secret") or "").strip()


def publish_interval_minutes() -> float:
    raw = os.getenv("STRATEGIUM_PUBLISH_INTERVAL_MINUTES") or _config().get("interval_minutes")
    try:
        return max(1.0, float(raw or DEFAULT_PUBLISH_INTERVAL_MINUTES))
    except (TypeError, ValueError):
        return DEFAULT_PUBLISH_INTERVAL_MINUTES


def _role_name(member: Any) -> Optional[str]:
    from . import bot

    aliases = {"Forge Master": "Forgemaster", "Hunt Master": "Huntmaster"}
    priority = {name: index for index, name in enumerate(getattr(bot, "RANK_ROLES_PRIORITY", []))}
    candidates = []
    for role in getattr(member, "roles", []) or []:
        name = str(getattr(role, "name", "") or "")
        canonical = aliases.get(name, name)
        if canonical in RANK_KEYS:
            candidates.append((priority.get(canonical, len(priority)), name))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def _display_name(member: Any) -> str:
    from .roster_embeds import _clean_roster_name

    from .constants import _normalize_display_name

    name = _normalize_display_name(_clean_roster_name(member))
    prefixes = sorted(
        set(RANK_KEYS) | {"Forge Master", "Hunt Master"},
        key=len,
        reverse=True,
    )
    changed = True
    while changed:
        changed = False
        for prefix in prefixes:
            if name.casefold().startswith(prefix.casefold() + " "):
                name = name[len(prefix):].strip()
                changed = True
                break
    name = name.replace("●", "").replace("⚬", "").replace("▬", "").strip()
    words = []
    for word in name.split():
        if len(word) > 1 and (word.isupper() or (word[0].islower() and word[1:].isupper())):
            word = word[:1].upper() + word[1:].lower()
        words.append(word)
    return " ".join(words)


def _chapter(member: Any) -> Optional[str]:
    from . import bot
    from .constants import _strip_display_name

    home_chapters = set(getattr(bot, "HOME_CHAPTERS", []))
    for role in getattr(member, "roles", []) or []:
        name = getattr(role, "name", "")
        if name in home_chapters:
            return _strip_display_name(name)
    return None


def _company(member: Any) -> Optional[int]:
    from .roster_ops import _get_member_company_name

    return COMPANIES.get(_get_member_company_name(member))


def _kill_team(member: Any) -> Optional[str]:
    from . import bot

    configured_ids = {int(value) for value in getattr(bot, "ALLOWED_KT_ROLE_IDS", set()) if value}
    configured_names = {
        str(value).strip().casefold(): str(value).strip()
        for value in (getattr(bot, "KILL_TEAMS", []) or [])
        if str(value).strip()
    }
    for role in getattr(member, "roles", []) or []:
        role_id = getattr(role, "id", None)
        role_name = str(getattr(role, "name", "") or "").strip()
        if role_id in configured_ids:
            return role_name or None
        if role_name.casefold() in configured_names:
            return configured_names[role_name.casefold()]
        if role_name.casefold().startswith("kill team ") or role_name.casefold().startswith("kill-team "):
            return role_name
    return None


def _resolve_company_kill_teams(guild: Any) -> tuple[dict[str, list[dict[str, str]]], dict[str, str]]:
    """Derive up to 4 Kill Teams per Watch Company from active guild roles."""
    from .roster_embeds import _get_kill_teams_for_company

    catalog: dict[str, list[dict[str, str]]] = {str(c): [] for c in range(1, 6)}
    slot_by_name_cf: dict[str, str] = {}

    if not guild:
        return catalog, slot_by_name_cf

    for cid in range(1, 6):
        c_str = str(cid)
        company_name = f"Watch Company {COMPANY_NAMES.get(cid, '')}"
        try:
            kt_list = _get_kill_teams_for_company(guild, company_name)
            for slot_num, (kt_name, kt_role_id, _) in enumerate(kt_list, start=1):
                slot_id = f"{cid}-{slot_num}"
                catalog[c_str].append({
                    "id": slot_id,
                    "name": kt_name,
                    "roleId": kt_role_id,
                })
                slot_by_name_cf[kt_name.casefold()] = slot_id
        except Exception:
            pass

    return catalog, slot_by_name_cf


def _load_user_directive_counts() -> dict[str, int]:
    """Calculate completed strike directives count for each member."""
    data_dir = str(getattr(_g, "CONFIG", {}).get("data_dir") or "data")
    path = os.path.join(data_dir, "target_packages.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            tp = json.load(handle) or {}
        packages = tp.get("packages", {}) or {}
        user_counts: dict[str, int] = {}
        for pkg in packages.values():
            if not isinstance(pkg, dict):
                continue
            if str(pkg.get("status", "")).strip().lower() != "completed":
                continue
            participants = set()
            for uid in (pkg.get("signed_up") or []):
                if uid:
                    participants.add(str(uid).strip())
            for uid in (pkg.get("assigned_specialist_ids") or []):
                if uid:
                    participants.add(str(uid).strip())
            for uid in participants:
                user_counts[uid] = user_counts.get(uid, 0) + 1
        return user_counts
    except Exception:
        return {}


def _directive_stats() -> dict[str, Any]:
    data_dir = str(getattr(_g, "CONFIG", {}).get("data_dir") or "data")
    path = os.path.join(data_dir, "target_packages.json")
    honors_path = os.path.join(data_dir, "honors.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            source = json.load(handle) or {}
        entity = source.get("entity_stats") or {}
        result = {
            "cycle": source.get("cycle") or {},
            "fortress": {"rep": source.get("rep", 0), **(source.get("cycle") or {})},
            "companies": {},
            "killTeams": {},
            "formations": {},
        }

        for c_name, c_stat in (entity.get("companies") or {}).items():
            result["companies"][c_name] = {
                "completed": c_stat.get("completed", 0),
                "failed": c_stat.get("failed", 0),
                "rep_earned": c_stat.get("rep_earned", 0.0),
            }

        for kt_name, kt_stat in (entity.get("kill_teams") or {}).items():
            result["killTeams"][kt_name] = {
                "completed": kt_stat.get("completed", 0),
                "failed": kt_stat.get("failed", 0),
                "rep_earned": kt_stat.get("rep_earned", 0.0),
            }

        for cadre_name, cadre_stat in (entity.get("cadres") or {}).items():
            f_key = FORMATION_STATS_KEYS.get(cadre_name)
            if f_key:
                result["formations"][f_key] = {
                    "name": cadre_name,
                    "completed": cadre_stat.get("completed", 0),
                    "failed": cadre_stat.get("failed", 0),
                    "rep_earned": cadre_stat.get("rep_earned", 0.0),
                }

        try:
            with open(honors_path, "r", encoding="utf-8") as handle:
                honors = json.load(handle) or {}

            for c_name, h_stat in (honors.get("companies") or {}).items():
                entry = result["companies"].setdefault(c_name, {})
                entry["tier"] = h_stat.get("tier")
                entry["tier_index"] = h_stat.get("tier_index", 0)
                entry["completions_28d"] = h_stat.get("completions_28d", 0)
                entry["rep_earned_28d"] = h_stat.get("rep_earned_28d", 0.0)
                if not entry.get("completed"):
                    entry["completed"] = h_stat.get("completions_28d", 0)
                if not entry.get("rep_earned"):
                    entry["rep_earned"] = h_stat.get("rep_earned_28d", 0.0)

            for kt_name, h_stat in (honors.get("kill_teams") or {}).items():
                entry = result["killTeams"].setdefault(kt_name, {})
                entry["tier"] = h_stat.get("tier")
                entry["tier_index"] = h_stat.get("tier_index", 0)
                entry["completions_28d"] = h_stat.get("completions_28d", 0)
                entry["rep_earned_28d"] = h_stat.get("rep_earned_28d", 0.0)
                if not entry.get("completed"):
                    entry["completed"] = h_stat.get("completions_28d", 0)
                if not entry.get("rep_earned"):
                    entry["rep_earned"] = h_stat.get("rep_earned_28d", 0.0)

            for cadre_name, h_stat in (honors.get("cadres") or {}).items():
                f_key = FORMATION_STATS_KEYS.get(cadre_name)
                if f_key:
                    entry = result["formations"].setdefault(f_key, {"name": cadre_name})
                    entry["tier"] = h_stat.get("tier")
                    entry["tier_index"] = h_stat.get("tier_index", 0)
                    entry["completions_28d"] = h_stat.get("completions_28d", 0)
                    entry["rep_earned_28d"] = h_stat.get("rep_earned_28d", 0.0)
                    if not entry.get("completed"):
                        entry["completed"] = h_stat.get("completions_28d", 0)
                    if not entry.get("rep_earned"):
                        entry["rep_earned"] = h_stat.get("rep_earned_28d", 0.0)
        except Exception:
            pass

        return result
    except Exception:
        return {"cycle": {}, "fortress": {}, "companies": {}, "killTeams": {}, "formations": {}}


def _joined_at(member: Any) -> Optional[str]:
    from .roster_ops import _get_effective_induction_date

    joined = _get_effective_induction_date(member)
    if not joined:
        return None
    if joined.tzinfo is None:
        joined = joined.replace(tzinfo=timezone.utc)
    return joined.astimezone(timezone.utc).isoformat()


def _stats(member: Any) -> dict[str, Any]:
    datastore = _g.DATASTORE
    if datastore is None:
        return {}
    try:
        value = datastore.get_user_stats(str(member.id))
        if not isinstance(value, dict):
            return {}
        return {
            key: item for key, item in value.items()
            if key not in {
                "operational_rating", "operational_rating_raw", "operational_rating_delta",
                "operational_rating_events", "operational_rating_tier", "last_aar_ts",
            }
        }
    except Exception:
        return {}


def _service_studs(member: Any) -> tuple[int, str]:
    from .forge_ops import _compute_member_service_studs
    from .studs import _studs_pips

    studs = int(_compute_member_service_studs(member) or 0)
    return studs, _studs_pips(studs) if studs else "—"


def build_snapshot(bot_client: Any) -> dict[str, Any]:
    guild_id = (_g.CONFIG or {}).get("guild_id")
    guild = None
    if guild_id:
        guild = bot_client.get_guild(int(guild_id))
    if guild is None and bot_client.guilds:
        guild = bot_client.guilds[0]
    if guild is None:
        return {"generatedAt": None, "members": []}

    members = []
    kill_team_catalog, slot_by_name_cf = _resolve_company_kill_teams(guild)
    user_directive_counts = _load_user_directive_counts()

    for member in guild.members:
        if getattr(member, "bot", False):
            continue
        role_name = _role_name(member)
        rank = RANK_KEYS.get(role_name or "")
        if rank is None:
            continue
        company = _company(member)
        kill_team_name = _kill_team(member) if company else None
        kill_team_slot = slot_by_name_cf.get(kill_team_name.casefold()) if (company and kill_team_name) else None

        stats = _stats(member)
        studs, studs_pips = _service_studs(member)
        members.append({
            "id": str(member.id),
            "name": _display_name(member),
            "chapter": _chapter(member),
            "rank": rank,
            "company": company,
            "killTeam": kill_team_slot or (f"{company}-1" if kill_team_name else None),
            "killTeamName": kill_team_name,
            "formation": None if company else FORMATION_BY_RANK.get(rank),
            "serverJoinedAt": _joined_at(member),
            "aarCount": int(stats.get("ops") or 0),
            "aarPoints": int(stats.get("aar_points") or 0),
            "strikeDirectives": int(user_directive_counts.get(str(member.id), 0)),
            "serviceStuds": studs,
            "serviceStudPips": studs_pips,
            "vigilYears": studs * 25,
            "stats": stats,
        })
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "members": members,
        "killTeams": kill_team_catalog,
        "directiveStats": _directive_stats(),
    }


async def publish_snapshot(bot_client: Any, session: aiohttp.ClientSession) -> bool:
    url = publish_url()
    secret = publish_secret()
    if not url or not secret:
        return False
    body = json.dumps(build_snapshot(bot_client), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    try:
        async with session.post(url, data=body, headers={
            "Content-Type": "application/json",
            "X-Strategium-Signature": signature,
        }, timeout=aiohttp.ClientTimeout(total=20)) as response:
            if response.status >= 300:
                _g.logger.warning("Strategium snapshot rejected with HTTP %s", response.status)
                return False
            return True
    except (aiohttp.ClientError, OSError) as error:
        _g.logger.warning("Strategium snapshot publish failed: %s", error)
        return False
