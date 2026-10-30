"""Publish a sanitized roster snapshot to the external Strategium service."""

from __future__ import annotations

import hashlib
import hmac
import asyncio
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp

from . import _bot_globals as _g
from .constants import CHALLENGE_ROLES, _is_member_in_reserves, _normalize_display_name

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
    "Kill-Marine": "kill_marine",
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
REFERENCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reference")
EQUERRY_ROLE_NAME = "High Command Equerry"
# Award role display name -> file in assets/Ribbons (numeric prefix is order of precedence).
RIBBON_FILES = {
    "The Order Omega": "1-Order Omega.png",
    "Crux Terminatus": "2-Crux Terminatus.png",
    "White Hand of Death": "3-Clandestine Ops Medal.png",
    "Red Hand of Doom": "3a-Distinguished Clandestine Ops Medal.png",
    "Black Reef Campaign Medal": "4-Black Reef Campaign Medal.png",
    "Distinguished Black Reef Campaign Medal": "4a-Distinguished Black Reef Campaign Medal.png",
    "Herisor Defense Medal": "5-Herisor Defense Medal.png",
    "Distinguished Herisor Defense Medal": "5a-Distinguished Herisor Defense Medal.png",
    "Distinguished Herisor Defense Medal with Valor": "5b-Distinguished Herisor Defense Medal with Valor.png",
    "Octavian Operation Medal": "6-Octavian Operation Medal.png",
    "Distinguished Octavian Operation Medal": "6a-Distinguished Octavian Operation Medal.png",
    "Kadaku Campaign Medal": "7-Kadaku Campaign Medal.png",
    "Distinguished Kadaku Campaign Medal": "7a-Distinguished Kadaku Campaign Medal.png",
    "SOK-G: Pipehitter": "8-SOK-G Service Medal.png",
    "Distinguished SOK-G: Pipehitter": "8a-Distinguished SOK-G Service Medal.png",
    "Order of the Aquiline Brotherhood": "9-Order of the Aquiline Brotherhood.png",
    "Master Terminus Slayer": "10f-Master Terminus Slayer Medal.png",
    "Crimson Laurels": "11-Crimson Laurels Medal.png",
    "Black Laurels": "12-Black Laurels Medal.png",
    "Apothecarion Service Medal": "13-Apothecarion Service Medal.png",
    "Ardent Raider Ribbon": "14-Ardent Raider Ribbon.png",
}
# Indexed by how many Terminus Slayer class variants a brother holds.
TERMINUS_SLAYER_RIBBONS = [
    ("Terminus Slayer (1st Award)", "10-Terminus Slayer 1st Award.png"),
    ("Terminus Slayer (2nd Award)", "10a-Terminus Slayer 2nd Award.png"),
    ("Terminus Slayer (3rd Award)", "10b-Terminus Slayer 3rd Award.png"),
    ("Terminus Slayer (4th Award)", "10c-Terminus Slayer 4th Award.png"),
    ("Terminus Slayer (5th Award)", "10d-Terminus Slayer 5th Award.png"),
    ("Terminus Slayer (6th Award)", "10e-Terminus Slayer 6th Award.png"),
]
def _config() -> dict[str, Any]:
    return (_g.CONFIG or {}).get("strategium_publish") or {}


def publish_url() -> str:
    url = str(os.getenv("STRATEGIUM_PUBLISH_URL") or _config().get("url") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return ""
    return url


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


def _is_equerry(member: Any) -> bool:
    return any(str(getattr(role, "name", "") or "") == EQUERRY_ROLE_NAME for role in getattr(member, "roles", []) or [])


def _ribbon_order(filename: str) -> tuple[int, str]:
    prefix = filename.split("-", 1)[0]
    digits = "".join(ch for ch in prefix if ch.isdigit())
    return (int(digits or 999), prefix[len(digits):])


def _awards(member: Any) -> list[dict[str, str]]:
    held = {getattr(role, "id", None) for role in getattr(member, "roles", []) or []}
    names = [name for role_id, name, _ in CHALLENGE_ROLES if role_id in held]
    candidates = [{"name": name, "ribbon": RIBBON_FILES[name]} for name in names if name in RIBBON_FILES]
    slayer_count = sum(1 for name in names if name.startswith("Terminus Slayer ("))
    if slayer_count:
        tier = min(slayer_count, len(TERMINUS_SLAYER_RIBBONS))
        candidates.append({"name": TERMINUS_SLAYER_RIBBONS[tier - 1][0], "ribbon": TERMINUS_SLAYER_RIBBONS[tier - 1][1]})
    # Ribbons sharing a numeric prefix are tiers of one award; the highest suffix supersedes the rest.
    best: dict[int, dict[str, str]] = {}
    for award in candidates:
        number, suffix = _ribbon_order(award["ribbon"])
        current = best.get(number)
        if current is None or suffix > _ribbon_order(current["ribbon"])[1]:
            best[number] = award
    return sorted(best.values(), key=lambda award: _ribbon_order(award["ribbon"]))


def _display_name(member: Any) -> str:
    raw_name = getattr(member, "nick", None) or getattr(member, "display_name", None) or getattr(member, "name", "")
    name = re.sub(r"<a?:[A-Za-z0-9_]+:\d+>", "", str(raw_name))
    name = _normalize_display_name(name).replace("●", "").replace("⚬", "").replace("▬", "").strip()
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
    from .roster_ops import _is_kill_team_membership_role

    catalog: dict[str, list[dict[str, str]]] = {str(c): [] for c in range(1, 6)}
    slot_by_name_cf: dict[str, str] = {}

    if not guild:
        return catalog, slot_by_name_cf

    kill_team_roles = sorted(
        [
            role for role in getattr(guild, "roles", []) or []
            if (_is_kill_team_membership_role(role) or "kill team" in str(getattr(role, "name", "")).casefold())
            and "champion" not in str(getattr(role, "name", "")).casefold()
        ],
        key=lambda role: str(getattr(role, "name", "")).casefold(),
    )
    for cid in range(1, 6):
        c_str = str(cid)
        company_name = f"Watch Company {COMPANY_NAMES.get(cid, '')}"
        company_role_ids = {
            getattr(role, "id", None)
            for role in getattr(guild, "roles", []) or []
            if str(getattr(role, "name", "")).casefold() == company_name.casefold()
        }
        eligible_roles = []
        for role in kill_team_roles:
            members = [
                member for member in getattr(role, "members", []) or []
                if not getattr(member, "bot", False)
                and not _is_member_in_reserves(member)
                and company_role_ids.intersection(
                    getattr(member_role, "id", None)
                    for member_role in getattr(member, "roles", []) or []
                )
                and not {str(getattr(member_role, "name", "")).casefold() for member_role in getattr(member, "roles", []) or []}
                .intersection({"first blade", "bladeguard"})
            ]
            if members:
                eligible_roles.append(role)
            if len(eligible_roles) >= 4:
                break

        for slot_num, role in enumerate(eligible_roles, start=1):
            role_name = str(getattr(role, "name", ""))
            slot_id = f"{cid}-{slot_num}"
            catalog[c_str].append({"id": slot_id, "name": role_name, "roleId": getattr(role, "id", 0)})
            slot_by_name_cf[role_name.casefold()] = slot_id

    return catalog, slot_by_name_cf


def _select_guild(bot_client: Any) -> Any:
    configured_id = (_g.CONFIG or {}).get("guild_id")
    if configured_id:
        try:
            guild = bot_client.get_guild(int(configured_id))
        except (TypeError, ValueError):
            guild = None
        if guild is not None:
            return guild

    configured_name = str((_g.CONFIG or {}).get("guild_name") or "").strip().casefold()
    if configured_name:
        for guild in getattr(bot_client, "guilds", []) or []:
            if str(getattr(guild, "name", "")).strip().casefold() == configured_name:
                return guild
    return None


def _read_json(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def _reach_graph() -> dict[str, list[dict[str, Any]]]:
    graph = _read_json(os.path.join(REFERENCE_DIR, "jericho_reach_graph.json")) or {}
    nodes = []
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict) or not node.get("id"):
            continue
        nodes.append({
            "id": str(node["id"]),
            "type": str(node.get("type") or ""),
            "region": str(node.get("region") or ""),
            "x": float(node.get("x") or 0),
            "y": float(node.get("y") or 0),
            "gamePlanet": bool(node.get("game_planet")),
        })
    edges = []
    for edge in graph.get("edges") or []:
        if not isinstance(edge, dict) or not edge.get("source") or not edge.get("target"):
            continue
        edges.append({
            "source": str(edge["source"]),
            "target": str(edge["target"]),
            "proximity": str(edge.get("proximity") or ""),
        })
    return {"nodes": nodes, "edges": edges}


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


def _recent_aar_teammates(member: Any) -> list[str]:
    datastore = _g.DATASTORE
    if datastore is None:
        return []
    getter = getattr(datastore, "get_recent_teammate_ids", None)
    if not callable(getter):
        return []
    try:
        return [str(user_id) for user_id in getter(str(member.id), limit=5) if str(user_id).isdigit()][:5]
    except Exception:
        _g.logger.exception("Failed to build recent AAR teammates for member %s", member.id)
        return []


def _service_studs(member: Any) -> tuple[int, str]:
    from .forge_ops import _compute_member_service_studs
    from .studs import _studs_pips

    studs = int(_compute_member_service_studs(member) or 0)
    return studs, _studs_pips(studs) if studs else "—"


def build_snapshot(bot_client: Any) -> dict[str, Any]:
    guild = _select_guild(bot_client)
    if guild is None:
        return {"generatedAt": None, "members": []}

    members = []
    reserve_ids: set[str] = set()
    kill_team_catalog, slot_by_name_cf = _resolve_company_kill_teams(guild)
    from . import galactic_war_runtime as war
    from .galactic_war import Renown

    _war_members, war_rosters, company_teams, war_teams = war.directory(guild)
    recognition = war.get_store().recognition(war_rosters, company_teams, datetime.now(timezone.utc))
    user_directive_counts = {identity: entry["completed"] for identity, entry in recognition["members"].items()}
    directive_stats = {
        "killTeams": {team["name"]: recognition["formations"].get(team["id"], Renown().as_dict()) for team in war_teams},
        "companies": {f"Watch Company {COMPANY_NAMES[int(company)]}": entry for company, entry in recognition["companies"].items()},
        "formations": {key: entry for key, entry in recognition["formations"].items() if key in FORMATION_BY_RANK.values()},
    }

    for member in guild.members:
        if getattr(member, "bot", False):
            continue
        if _is_member_in_reserves(member):
            reserve_ids.add(str(member.id))
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
            "killTeam": kill_team_slot,
            "killTeamName": kill_team_name,
            "formation": None if company else FORMATION_BY_RANK.get(rank),
            "equerry": _is_equerry(member),
            "serverJoinedAt": _joined_at(member),
            "aarCount": int(stats.get("ops") or 0),
            "aarPoints": int(stats.get("aar_points") or 0),
            "recentAarTeammates": _recent_aar_teammates(member),
            "strikeDirectives": int(user_directive_counts.get(str(member.id), 0)),
            "strikeLaurel": recognition["members"].get(str(member.id), {}).get("laurel", ""),
            "serviceStuds": studs,
            "serviceStudPips": studs_pips,
            "awards": _awards(member),
            "oathSeal": None,
            "vigilYears": studs * 25,
            "stats": stats,
        })
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "members": members,
        "killTeams": kill_team_catalog,
        "directiveStats": directive_stats,
        "reach": {**_reach_graph(), "directives": [], "rep": 0},
    }


async def publish_snapshot(bot_client: Any, session: aiohttp.ClientSession) -> bool:
    url = publish_url()
    secret = publish_secret()
    if not url or not secret:
        return False
    snapshot = await asyncio.to_thread(build_snapshot, bot_client)
    body = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    try:
        async with session.post(url, data=body, headers={
            "Content-Type": "application/json",
            "X-Strategium-Signature": signature,
        }, timeout=aiohttp.ClientTimeout(total=20), allow_redirects=False) as response:
            if response.status >= 300:
                _g.logger.warning("Strategium snapshot rejected with HTTP %s", response.status)
                return False
            return True
    except (aiohttp.ClientError, OSError) as error:
        _g.logger.warning("Strategium snapshot publish failed: %s", error)
        return False
