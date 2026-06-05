"""Ordo Xenos Target Packages subsystem.

Strike packages issued by Ordo Xenos for Watch Fortress Jericho to complete.
Commands: /request_target_packages, /target_packages, /assign_package,
          /submit_target_package, /target_package_status
"""

import os
import json
import random
import string
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
import sys as _sys

import discord
from discord import app_commands
from discord.ext import tasks as _tasks

from .constants import *  # noqa: F401,F403
from .permissions import *  # noqa: F401,F403
from . import _bot_globals as _g

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b(name):
    """Resolve name via bot module for test-mock compatibility."""
    m = _sys.modules.get("opscribe.bot") or _sys.modules.get("bot")
    return getattr(m, name) if (m is not None and hasattr(m, name)) else globals().get(name)


TARGET_PACKAGES_PATH = os.path.join(DATA_DIR, "target_packages.json")
_REFERENCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reference")

_TP_LOCK = asyncio.Lock()

GREEK_LETTERS = [
    "Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta",
    "Iota", "Kappa", "Lambda", "Mu", "Nu", "Xi", "Omicron", "Pi", "Rho",
    "Sigma", "Tau", "Upsilon", "Phi", "Chi", "Psi", "Omega",
]

# Requirement tier keys used in briefing_templates.json
_REQ_TIER_VETERAN_OATHSWORN = "veteran_oathsworn"
_REQ_TIER_KT_COMMAND = "kt_command"
_REQ_TIER_COMPANY_COMMAND = "company_command"
_REQ_TIER_HC = "hc"
_REQ_TIER_NO_REQ = "no_req"

# Role name sets per requirement tier
_TIER_ROLES = {
    _REQ_TIER_VETERAN_OATHSWORN: ["Watch Veteran", "Oathsworn"],
    _REQ_TIER_KT_COMMAND: ["Watch Sergeant", "Kill Team Champion", "Judiciar"],
    _REQ_TIER_COMPANY_COMMAND: [
        "Watch Captain", "Watch Lieutenant", "Company Champion",
        "Watch Techmarine", "Watch Apothecary", "Watch Chaplain",
        "Watch Librarian", "Watch Keeper", "Honored Dreadnought",
    ],
    _REQ_TIER_HC: [
        "Watch Master", "Lord Executioner", "Forgemaster", "Chief Apothecary",
        "High Chaplain", "Huntmaster", "Void Warden", "Castellan",
        "Venerable Dreadnought",
    ],
}

# Requirement tier draw weights (must sum to 100)
_TIER_WEIGHTS = [
    (_REQ_TIER_NO_REQ, 50),
    (_REQ_TIER_VETERAN_OATHSWORN, 15),
    (_REQ_TIER_KT_COMMAND, 20),
    (_REQ_TIER_COMPANY_COMMAND, 10),
    (_REQ_TIER_HC, 5),
]

# Strat table: rep range -> (pos_count, neg_count)
_STRAT_TABLE = {
    -2: (2, 5),
    -1: (2, 4),
    0: (3, 3),
    1: (4, 2),
    2: (5, 2),
}

# Chaos-only mission IDs that force Intel Lapse
_INTEL_LAPSE_MISSIONS = {3, 4, 8, 10, 12}

# Package statuses
STATUS_UNASSIGNED = "unassigned"          # Generated, WM hasn't distributed
STATUS_DISTRIBUTED = "distributed"        # Sent to Captain, not assigned to KT
STATUS_AWAITING_SPECIALIST = "awaiting_specialist"  # KT assigned, specialist needed
STATUS_ACTIVE = "active"                  # Fully ready to run
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"                  # Assigned, deadline passed
STATUS_LAPSED = "lapsed"                  # Distributed, never assigned, deadline passed

# ---------------------------------------------------------------------------
# Data I/O
# ---------------------------------------------------------------------------

def _load_tp() -> dict:
    try:
        if not os.path.exists(TARGET_PACKAGES_PATH):
            return _empty_tp_store()
        with open(TARGET_PACKAGES_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or _empty_tp_store()
    except Exception:
        return _empty_tp_store()


def _save_tp(data: dict) -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(TARGET_PACKAGES_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _g.logger.error(f"[TP] Failed to save target_packages.json: {e}")


def _empty_tp_store() -> dict:
    return {
        "rep": 0.0,
        "cycle": {
            "generated_at": None,
            "total": 0,
            "completed": 0,
            "failed": 0,
            "lapsed": 0,
        },
        "entity_stats": {
            "companies": {},
            "kill_teams": {},
            "cadres": {},
        },
        "packages": {},
    }


def _load_graph() -> dict:
    path = os.path.join(_REFERENCE_DIR, "jericho_reach_graph.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_stratagems() -> list:
    path = os.path.join(_REFERENCE_DIR, "stratagems.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [s for s in data["stratagems"] if not s.get("excluded", False)]


def _load_operations() -> list:
    path = os.path.join(_REFERENCE_DIR, "operations.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["operations"] if isinstance(data, dict) else data


def _load_briefing_templates() -> dict:
    path = os.path.join(_REFERENCE_DIR, "briefing_templates.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

def _generate_package_id(existing_ids: set) -> str:
    """Generate a unique OX-XXXXX ID (5 alphanumeric chars, uppercase)."""
    chars = string.ascii_uppercase + string.digits
    for _ in range(1000):
        suffix = "".join(random.choices(chars, k=5))
        pid = f"OX-{suffix}"
        if pid not in existing_ids:
            return pid
    raise RuntimeError("Failed to generate unique package ID after 1000 attempts")


# ---------------------------------------------------------------------------
# Active roster helpers
# ---------------------------------------------------------------------------

def _is_active(member: discord.Member) -> bool:
    """Return True if member is not in Reserves."""
    return not any(getattr(r, "id", 0) == RESERVES_ROLE_ID for r in getattr(member, "roles", []))


def _member_role_names(member: discord.Member) -> set:
    return {(getattr(r, "name", "") or "").strip() for r in getattr(member, "roles", [])}


def _active_members(guild: discord.Guild) -> list:
    return [m for m in guild.members if not m.bot and _is_active(m)]


def _count_active_kts(guild: discord.Guild) -> int:
    """Count distinct Kill Teams with at least one active non-reserves member holding a KT role (excl. champion)."""
    kt_role_names = {
        "Watch Sergeant", "Judiciar", "Oathsworn", "Watch Veteran", "Watch Brother",
    }
    occupied: set = set()
    kill_teams = _b("KILL_TEAMS") or []
    kt_lower = {kt.lower(): kt for kt in kill_teams}
    for member in _active_members(guild):
        roles = _member_role_names(member)
        if not roles.intersection(kt_role_names):
            continue
        for r in getattr(member, "roles", []):
            rn = (getattr(r, "name", "") or "").strip().lower()
            if rn in kt_lower:
                occupied.add(kt_lower[rn])
                break
    return max(len(occupied), 1)


def _get_active_roles_in_guild(guild: discord.Guild) -> set:
    """Return set of role names held by at least one active non-reserves member."""
    active = _active_members(guild)
    present: set = set()
    for m in active:
        present.update(_member_role_names(m))
    return present


# ---------------------------------------------------------------------------
# Stratagem draw (conflict-aware)
# ---------------------------------------------------------------------------

def _draw_strats(rep: float, active_strats: list) -> dict:
    """Draw stratagem pool for a package given current rep.

    Returns {"core": [...], "wildcards": [...], "intel_lapse": bool}
    (intel_lapse is injected later based on mission).
    """
    rep_tier = max(-2, min(2, round(rep)))
    pos_count, neg_count = _STRAT_TABLE[rep_tier]

    buffs = [s for s in active_strats if s["type"] == "buff"]
    debuffs = [s for s in active_strats if s["type"] == "debuff"]
    neutrals = [s for s in active_strats
                if s["type"] in ("special", "enemy_modifier")
                and s["name"] != "Intelligence Lapse"]

    def conflicts_with_pool(candidate: dict, pool: list) -> bool:
        # Category conflict
        for cat in candidate.get("restriction_categories", []):
            for drawn in pool:
                if cat in drawn.get("restriction_categories", []):
                    return True
        # Specific conflict (bidirectional)
        for drawn in pool:
            if candidate["name"] in drawn.get("specific_conflicts", []):
                return True
            if drawn["name"] in candidate.get("specific_conflicts", []):
                return True
        return False

    def draw_from(pool: list, count: int, existing: list) -> list:
        available = [s for s in pool if not conflicts_with_pool(s, existing)]
        random.shuffle(available)
        chosen = []
        for s in available:
            if len(chosen) >= count:
                break
            if not conflicts_with_pool(s, existing + chosen):
                chosen.append(s)
        return chosen

    core: list = []
    core += draw_from(buffs, pos_count, core)
    core += draw_from(debuffs, neg_count, core)

    # Wildcards: 0-3
    wildcard_count = random.randint(0, 3)
    wildcards = draw_from(neutrals, wildcard_count, core)

    return {
        "core": [{"name": s["name"], "type": s["type"]} for s in core],
        "wildcards": [{"name": s["name"], "type": s["type"]} for s in wildcards],
    }


# ---------------------------------------------------------------------------
# Requirement generation
# ---------------------------------------------------------------------------

def _pick_requirement(tier_key: str, available_roles: set) -> list:
    """Pick 1 or 2 role requirements from the given tier, only from available roles."""
    pool = [r for r in _TIER_ROLES[tier_key] if r in available_roles]
    if not pool:
        return []
    count = random.choices([1, 2], weights=[75, 25])[0]
    return random.sample(pool, min(count, len(pool)))


def _draw_requirement_tier(available_roles: set) -> tuple:
    """Return (tier_key, [role_names]).  Falls back to no_req if tier has no eligible roles."""
    tiers, weights = zip(*_TIER_WEIGHTS)
    for _ in range(10):
        tier = random.choices(tiers, weights=weights)[0]
        if tier == _REQ_TIER_NO_REQ:
            return (_REQ_TIER_NO_REQ, [])
        roles = _pick_requirement(tier, available_roles)
        if roles:
            return (tier, roles)
    return (_REQ_TIER_NO_REQ, [])


# ---------------------------------------------------------------------------
# Briefing text assembly
# ---------------------------------------------------------------------------

def _build_briefing(node_name: str, world_type: str, mission_id: int,
                    tier_key: str, req_roles: list, rep: float,
                    templates: dict) -> str:
    # World type hook
    hooks = templates["world_type_hooks"].get(world_type, templates["world_type_hooks"]["dead_world"])
    hook = random.choice(hooks).replace("{node}", node_name)

    # Mission hook
    mission_hooks = templates["mission_hooks"].get(str(mission_id), ["eliminate the xenos threat in the area"])
    mission_hook = random.choice(mission_hooks)

    # Requirement clause
    rank_str = " and ".join(req_roles) if req_roles else ""
    tier_templates_raw = templates["req_tier_templates"].get(tier_key, templates["req_tier_templates"]["no_req"])
    req_clause = random.choice(tier_templates_raw).replace("{rank}", rank_str).replace("{mission}", "")
    if len(req_roles) > 1:
        req_clause = req_clause.replace(" is required", " are required").replace(" is required.", " are required.")

    # Find operation name
    try:
        ops = _load_operations()
        op_name = next((o["name"] for o in ops if o["id"] == mission_id), str(mission_id))
    except Exception:
        op_name = str(mission_id)

    sentence1 = f"{hook} — {mission_hook}. {req_clause} ({op_name})"

    sentence2 = ""
    rep_tier = max(-2, min(2, round(rep)))
    if rep_tier <= -2:
        tone_options = templates["strat_tone"]["rep_neg2"]
        sentence2 = random.choice(tone_options)
    elif rep_tier == -1:
        tone_options = templates["strat_tone"]["rep_neg1"]
        sentence2 = random.choice(tone_options)

    return f"{sentence1}\n{sentence2}".strip()


# ---------------------------------------------------------------------------
# Package generation
# ---------------------------------------------------------------------------

def _generate_single_package(
    existing_ids: set,
    rep: float,
    graph: dict,
    active_strats: list,
    templates: dict,
    available_roles: set,
    ops_list: list,
) -> dict:
    # Pick random node with eligible missions
    world_type_missions: dict = graph["world_type_missions"]
    eligible_nodes = [
        n for n in graph["nodes"]
        if world_type_missions.get(n["type"], [])
    ]
    node = random.choice(eligible_nodes)
    world_type = node["type"]
    mission_id = random.choice(world_type_missions[world_type])

    op_data = next((o for o in ops_list if o["id"] == mission_id), {})
    intel_lapse_forced = op_data.get("intel_lapse_forced", False) or mission_id in _INTEL_LAPSE_MISSIONS

    mode = random.choices(["Hard-Strat", "Omega-Strat"], weights=[70, 30])[0]

    tier_key, req_roles = _draw_requirement_tier(available_roles)
    strats = _draw_strats(rep, active_strats)
    briefing = _build_briefing(
        node["id"], world_type, mission_id, tier_key, req_roles, rep, templates
    )

    now = datetime.now(timezone.utc)
    deadline = now + timedelta(days=7)
    pid = _generate_package_id(existing_ids)

    return {
        "id": pid,
        "node": node["id"],
        "world_type": world_type,
        "mission_id": mission_id,
        "mode": mode,
        "requirement_tier": tier_key,
        "required_roles": req_roles,
        "stratagems": strats,
        "intel_lapse": intel_lapse_forced,
        "briefing": briefing,
        "status": STATUS_UNASSIGNED,
        "assigned_captain_id": None,
        "assigned_kt": None,
        "assigned_specialist_ids": [],
        "assigned_company": None,
        "generated_at": now.isoformat(),
        "deadline": deadline.isoformat(),
        "completed_at": None,
        "submitted_by": None,
        "aar_link": None,
    }


async def generate_packages(guild: discord.Guild) -> list:
    """Generate a batch of target packages. Returns list of package dicts."""
    async with _TP_LOCK:
        data = _load_tp()
        rep = data.get("rep", 0.0)

        graph = _load_graph()
        active_strats = _load_stratagems()
        templates = _load_briefing_templates()
        ops_list = _load_operations()
        available_roles = _get_active_roles_in_guild(guild)

        kt_count = _count_active_kts(guild)
        multiplier = random.randint(1, 3)
        count = kt_count * multiplier

        existing_ids = set(data["packages"].keys())
        new_packages = []
        for _ in range(count):
            pkg = _generate_single_package(
                existing_ids, rep, graph, active_strats, templates, available_roles, ops_list
            )
            existing_ids.add(pkg["id"])
            data["packages"][pkg["id"]] = pkg
            data["cycle"]["total"] += count
            new_packages.append(pkg)

        data["cycle"]["generated_at"] = datetime.now(timezone.utc).isoformat()
        _save_tp(data)
        return new_packages


async def distribute_packages(package_ids: list, guild: discord.Guild) -> None:
    """Mark packages as distributed and notify Captains in highcom channel."""
    async with _TP_LOCK:
        data = _load_tp()
        config_tp = (_b("CONFIG") or {}).get("target_packages", {})
        highcom_channel_id = config_tp.get("highcom_channel_id")

        captain_roles = {"Watch Captain", "Watch Lieutenant"}

        for pid in package_ids:
            if pid in data["packages"]:
                data["packages"][pid]["status"] = STATUS_DISTRIBUTED

        _save_tp(data)

    # Notify highcom channel
    if highcom_channel_id:
        channel = guild.get_channel(int(highcom_channel_id))
        if channel:
            # Ping Captains
            captain_mentions = []
            for m in guild.members:
                if m.bot:
                    continue
                roles = _member_role_names(m)
                if roles.intersection(captain_roles) and _is_active(m):
                    captain_mentions.append(m.mention)

            mention_str = " ".join(captain_mentions) if captain_mentions else ""
            count = len(package_ids)
            await channel.send(
                f"{mention_str}\n"
                f"**Ordo Xenos has transmitted {count} target package{'s' if count != 1 else ''} "
                f"to Watch Fortress Jericho.** Use `/target_packages` to review and assign."
            )


async def assign_package_to_kt(
    package_id: str,
    kt_name: str,
    company_name: str,
    captain_member: discord.Member,
    guild: discord.Guild,
) -> tuple:
    """Assign a package to a KT. Returns (success: bool, message: str)."""
    async with _TP_LOCK:
        data = _load_tp()
        pkg = data["packages"].get(package_id)
        if not pkg:
            return False, f"Package `{package_id}` not found."
        if pkg["status"] not in (STATUS_DISTRIBUTED,):
            return False, f"Package `{package_id}` is not available for assignment (status: {pkg['status']})."

        # Check KT package cap (max 3)
        kt_active = [
            p for p in data["packages"].values()
            if p.get("assigned_kt") == kt_name
            and p["status"] in (STATUS_AWAITING_SPECIALIST, STATUS_ACTIVE)
        ]
        if len(kt_active) >= 3:
            return False, f"{kt_name} already has 3 active packages. Cannot assign more until one is completed."

        # Determine if specialist is needed
        specialist_roles = set(
            _TIER_ROLES.get(_REQ_TIER_KT_COMMAND, []) +
            _TIER_ROLES.get(_REQ_TIER_COMPANY_COMMAND, []) +
            _TIER_ROLES.get(_REQ_TIER_HC, [])
        )
        req_roles = pkg.get("required_roles", [])
        needs_specialist_attach = any(r in specialist_roles for r in req_roles)

        new_status = STATUS_AWAITING_SPECIALIST if needs_specialist_attach else STATUS_ACTIVE

        pkg["assigned_kt"] = kt_name
        pkg["assigned_company"] = company_name
        pkg["assigned_captain_id"] = captain_member.id
        pkg["status"] = new_status

        # Init entity stats
        stats = data["entity_stats"]
        if kt_name not in stats["kill_teams"]:
            stats["kill_teams"][kt_name] = {"completed": 0, "failed": 0}
        if company_name and company_name not in stats["companies"]:
            stats["companies"][company_name] = {"completed": 0, "failed": 0}

        _save_tp(data)

    # Notify KT channel
    await _notify_kt_assigned(package_id, kt_name, pkg, guild)

    # If specialist needed, ping cadre leaders in highcom
    if needs_specialist_attach:
        await _notify_cadre_leaders_needed(package_id, req_roles, guild)

    return True, f"Package `{package_id}` assigned to {kt_name}."


async def assign_specialist(
    package_id: str,
    specialist_member: discord.Member,
    cadre_leader: discord.Member,
    guild: discord.Guild,
) -> tuple:
    """Attach a specialist to a package. Returns (success, message)."""
    async with _TP_LOCK:
        data = _load_tp()
        pkg = data["packages"].get(package_id)
        if not pkg:
            return False, f"Package `{package_id}` not found."
        if pkg["status"] not in (STATUS_AWAITING_SPECIALIST, STATUS_ACTIVE):
            return False, f"Package `{package_id}` cannot accept a specialist attachment (status: {pkg['status']})."

        # Check specialist not already locked on another package
        active_statuses = {STATUS_AWAITING_SPECIALIST, STATUS_ACTIVE}
        for p in data["packages"].values():
            if (specialist_member.id in p.get("assigned_specialist_ids", [])
                    and p["id"] != package_id
                    and p["status"] in active_statuses):
                return False, f"{specialist_member.display_name} is already attached to package `{p['id']}`."

        pkg.setdefault("assigned_specialist_ids", [])
        if specialist_member.id not in pkg["assigned_specialist_ids"]:
            pkg["assigned_specialist_ids"].append(specialist_member.id)

        # Check if all required roles are now covered
        if _requirements_satisfied(pkg, guild):
            pkg["status"] = STATUS_ACTIVE

        _save_tp(data)

    return True, (
        f"{specialist_member.display_name} attached to package `{package_id}`. "
        f"Status: `{pkg['status']}`."
    )


def _requirements_satisfied(pkg: dict, guild: discord.Guild) -> bool:
    """Check if all required roles for a package are satisfied by assigned members."""
    req_roles = pkg.get("required_roles", [])
    if not req_roles:
        return True

    kt_name = pkg.get("assigned_kt")
    company_name = pkg.get("assigned_company")
    specialist_ids = set(pkg.get("assigned_specialist_ids", []))

    # Build set of roles held by: KT members + company members + HC members + attached specialists
    covered_roles: set = set()
    for m in guild.members:
        if m.bot or not _is_active(m):
            continue
        roles = _member_role_names(m)
        # Is this member part of the assigned KT or company, or HC?
        from .forge_ops import _resolve_killteam_for_member
        from .roster_ops import _get_member_company_name
        member_kt = _resolve_killteam_for_member(m)
        member_company = _get_member_company_name(m)
        is_hc = any(r in HIGH_COMMAND_RANKS for r in roles)
        if (member_kt == kt_name or member_company == company_name or is_hc or m.id in specialist_ids):
            covered_roles.update(roles)

    return all(role in covered_roles for role in req_roles)


async def submit_package(
    package_id: str,
    aar_link: str,
    submitter: discord.Member,
    guild: discord.Guild,
) -> tuple:
    """Submit a completed package. Returns (success, message)."""
    async with _TP_LOCK:
        data = _load_tp()
        pkg = data["packages"].get(package_id)
        if not pkg:
            return False, f"Package `{package_id}` not found."

        if pkg["status"] not in (STATUS_ACTIVE, STATUS_AWAITING_SPECIALIST):
            return False, f"Package `{package_id}` cannot be submitted (status: `{pkg['status']}`)."

        # Check deadline
        deadline = datetime.fromisoformat(pkg["deadline"])
        if datetime.now(timezone.utc) > deadline:
            return False, f"Package `{package_id}` has expired (deadline passed)."

        # Validate submitter is in the assigned KT or company
        from .forge_ops import _resolve_killteam_for_member
        from .roster_ops import _get_member_company_name
        submitter_kt = _resolve_killteam_for_member(submitter)
        submitter_company = _get_member_company_name(submitter)
        submitter_roles = _member_role_names(submitter)
        is_hc = any(r in HIGH_COMMAND_RANKS for r in submitter_roles)

        assigned_kt = pkg.get("assigned_kt")
        assigned_company = pkg.get("assigned_company")

        if not (submitter_kt == assigned_kt or submitter_company == assigned_company or is_hc):
            return False, (
                f"You are not part of the assigned unit for package `{package_id}`. "
                f"Only members of {assigned_kt or assigned_company} may submit."
            )

        # Check required roles are satisfied
        if not _requirements_satisfied(pkg, guild):
            missing = [r for r in pkg.get("required_roles", [])
                       if not _role_satisfied_by_unit(r, pkg, guild)]
            return False, (
                f"Package `{package_id}` requires {', '.join(missing)} — "
                f"this role is not present in the assigned unit. "
                f"Contact the relevant Cadre Leader to assign a specialist."
            )

        pkg["status"] = STATUS_COMPLETED
        pkg["completed_at"] = datetime.now(timezone.utc).isoformat()
        pkg["submitted_by"] = submitter.id
        pkg["aar_link"] = aar_link

        # Update entity stats
        stats = data["entity_stats"]
        kt = pkg.get("assigned_kt")
        company = pkg.get("assigned_company")
        if kt:
            stats["kill_teams"].setdefault(kt, {"completed": 0, "failed": 0})
            stats["kill_teams"][kt]["completed"] += 1
        if company:
            stats["companies"].setdefault(company, {"completed": 0, "failed": 0})
            stats["companies"][company]["completed"] += 1

        data["cycle"]["completed"] += 1
        _update_rep(data)
        _save_tp(data)

    return True, f"Package `{package_id}` marked completed. Ordo Xenos standing updated."


def _role_satisfied_by_unit(role: str, pkg: dict, guild: discord.Guild) -> bool:
    """Check if a single required role is satisfied by the assigned unit."""
    kt_name = pkg.get("assigned_kt")
    company_name = pkg.get("assigned_company")
    specialist_ids = set(pkg.get("assigned_specialist_ids", []))

    for m in guild.members:
        if m.bot or not _is_active(m):
            continue
        roles = _member_role_names(m)
        if role not in roles:
            continue
        from .forge_ops import _resolve_killteam_for_member
        from .roster_ops import _get_member_company_name
        member_kt = _resolve_killteam_for_member(m)
        member_company = _get_member_company_name(m)
        is_hc = any(r in HIGH_COMMAND_RANKS for r in roles)
        if (member_kt == kt_name or member_company == company_name or is_hc or m.id in specialist_ids):
            return True
    return False


def _update_rep(data: dict) -> None:
    """Recalculate and clamp rep after a completed/failed package."""
    cycle = data["cycle"]
    total_assigned = cycle.get("completed", 0) + cycle.get("failed", 0)
    if total_assigned == 0:
        return
    delta = (cycle["completed"] - cycle["failed"]) / total_assigned
    data["rep"] = max(-2.0, min(2.0, data.get("rep", 0.0) + delta))


# ---------------------------------------------------------------------------
# Deadline expiry checker
# ---------------------------------------------------------------------------

async def expire_packages(guild: discord.Guild) -> None:
    """Check for expired packages and mark them failed or lapsed."""
    async with _TP_LOCK:
        data = _load_tp()
        now = datetime.now(timezone.utc)
        changed = False

        for pkg in data["packages"].values():
            if pkg["status"] in (STATUS_COMPLETED, STATUS_FAILED, STATUS_LAPSED):
                continue
            deadline = datetime.fromisoformat(pkg["deadline"])
            if now <= deadline:
                continue

            if pkg["status"] in (STATUS_ACTIVE, STATUS_AWAITING_SPECIALIST):
                pkg["status"] = STATUS_FAILED
                data["cycle"]["failed"] += 1
                # Update entity stats
                kt = pkg.get("assigned_kt")
                company = pkg.get("assigned_company")
                if kt:
                    data["entity_stats"]["kill_teams"].setdefault(kt, {"completed": 0, "failed": 0})
                    data["entity_stats"]["kill_teams"][kt]["failed"] += 1
                if company:
                    data["entity_stats"]["companies"].setdefault(company, {"completed": 0, "failed": 0})
                    data["entity_stats"]["companies"][company]["failed"] += 1
                changed = True

            elif pkg["status"] == STATUS_DISTRIBUTED:
                pkg["status"] = STATUS_LAPSED
                data["cycle"]["lapsed"] += 1
                changed = True

        if changed:
            _update_rep(data)
            _save_tp(data)


# ---------------------------------------------------------------------------
# Notification helpers
# ---------------------------------------------------------------------------

async def _notify_kt_assigned(
    package_id: str, kt_name: str, pkg: dict, guild: discord.Guild
) -> None:
    """Ping the KT in their channel that a package has been assigned."""
    from .forge_ops import _get_award_announcement_channel

    # Find a member of the assigned KT to resolve their channel
    for m in guild.members:
        if m.bot or not _is_active(m):
            continue
        from .forge_ops import _resolve_killteam_for_member
        if _resolve_killteam_for_member(m) == kt_name:
            channel = await _get_award_announcement_channel(m, guild)
            if channel:
                mode = pkg.get("mode", "")
                mission_id = pkg.get("mission_id")
                try:
                    ops = _load_operations()
                    op_name = next((o["name"] for o in ops if o["id"] == mission_id), str(mission_id))
                except Exception:
                    op_name = str(mission_id)
                await channel.send(
                    f"**{kt_name}** — Target Package `{package_id}` has been assigned to your Kill Team.\n"
                    f"Mission: **{op_name}** | Mode: **{mode}**\n"
                    f"Use `/target_package_status {package_id}` to view full details."
                )
            return


async def _notify_cadre_leaders_needed(
    package_id: str, req_roles: list, guild: discord.Guild
) -> None:
    """Ping relevant cadre leaders in highcom that their specialist is needed."""
    config_tp = (_b("CONFIG") or {}).get("target_packages", {})
    highcom_channel_id = config_tp.get("highcom_channel_id")
    if not highcom_channel_id:
        return

    channel = guild.get_channel(int(highcom_channel_id))
    if not channel:
        return

    # Map required roles to cadre leaders
    cadre_map = {
        "Watch Techmarine": "Forgemaster",
        "Honored Dreadnought": "Forgemaster",
        "Venerable Dreadnought": "Forgemaster",
        "Watch Apothecary": "Chief Apothecary",
        "Watch Chaplain": "High Chaplain",
        "Judiciar": "High Chaplain",
        "Watch Librarian": "Void Warden",
        "Watch Keeper": "Castellan",
        "Kill Team Champion": "Lord Executioner",
        "Company Champion": "Lord Executioner",
    }

    needed_cadre_leaders: set = set()
    for role in req_roles:
        cl = cadre_map.get(role)
        if cl:
            needed_cadre_leaders.add(cl)

    if not needed_cadre_leaders:
        return

    # Find members with those roles
    mentions = []
    for m in guild.members:
        if m.bot or not _is_active(m):
            continue
        roles = _member_role_names(m)
        if roles.intersection(needed_cadre_leaders):
            mentions.append(m.mention)

    if mentions:
        mention_str = " ".join(mentions)
        await channel.send(
            f"{mention_str}\n"
            f"Package `{package_id}` requires specialist attachment "
            f"({', '.join(req_roles)}). Use `/assign_package` to attach."
        )


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------

def _rep_bar(rep: float) -> str:
    filled = round((rep + 2) / 4 * 10)
    filled = max(0, min(10, filled))
    return "█" * filled + "░" * (10 - filled)


def _strat_line(strat: dict) -> str:
    t = strat["type"]
    if t == "buff":
        prefix = "[+]"
    elif t == "debuff":
        prefix = "[-]"
    else:
        prefix = "[~]"
    return f"{prefix} {strat['name']}"


def _build_package_embed(pkg: dict, rep: float, index: int = 0, total: int = 0) -> discord.Embed:
    pid = pkg["id"]
    node = pkg.get("node", "Unknown")
    mission_id = pkg.get("mission_id")
    mode = pkg.get("mode", "")
    status = pkg.get("status", "")
    req_roles = pkg.get("required_roles", [])
    briefing = pkg.get("briefing", "")
    stratagems = pkg.get("stratagems", {})
    intel_lapse = pkg.get("intel_lapse", False)

    try:
        ops = _load_operations()
        op_name = next((o["name"] for o in ops if o["id"] == mission_id), str(mission_id))
    except Exception:
        op_name = str(mission_id)

    deadline_str = ""
    if pkg.get("deadline"):
        deadline = datetime.fromisoformat(pkg["deadline"])
        remaining = deadline - datetime.now(timezone.utc)
        if remaining.total_seconds() > 0:
            days = remaining.days
            hours = remaining.seconds // 3600
            deadline_str = f"{days}d {hours}h"
        else:
            deadline_str = "EXPIRED"

    title = f"TARGET PACKAGE {pid}"
    if index and total:
        title += f"  [{index}/{total}]"

    embed = discord.Embed(title=title, color=0x8B0000)
    embed.add_field(
        name="Theatre",
        value=f"{node} | {op_name}",
        inline=True,
    )
    embed.add_field(name="Mode", value=mode, inline=True)
    embed.add_field(name="Deadline", value=deadline_str or "—", inline=True)

    if briefing:
        embed.add_field(name="Briefing", value=briefing, inline=False)

    if req_roles:
        embed.add_field(name="Required", value=", ".join(req_roles), inline=False)

    core_strats = stratagems.get("core", [])
    wildcards = stratagems.get("wildcards", [])
    strat_lines = [_strat_line(s) for s in core_strats]
    if wildcards:
        strat_lines += [_strat_line(s) for s in wildcards]
    if intel_lapse:
        strat_lines.append("[~] Intelligence Lapse *(forced)*")
    if strat_lines:
        embed.add_field(
            name="Stratagems",
            value="\n".join(strat_lines),
            inline=False,
        )

    # Assignment info
    kt = pkg.get("assigned_kt")
    company = pkg.get("assigned_company")
    assign_info = status.upper()
    if kt:
        assign_info += f" → {kt}"
    elif company:
        assign_info += f" → {company}"
    embed.add_field(name="Status", value=assign_info, inline=True)

    rep_display = f"{_rep_bar(rep)} {rep:+.2f}"
    embed.set_footer(text=f"Ordo Xenos Standing: {rep_display}")

    return embed


def _build_status_board_embed(data: dict) -> discord.Embed:
    """Build a compact multi-package status board embed."""
    packages = data.get("packages", {})
    rep = data.get("rep", 0.0)
    cycle = data.get("cycle", {})

    active_pkgs = [
        p for p in packages.values()
        if p["status"] not in (STATUS_COMPLETED, STATUS_FAILED, STATUS_LAPSED)
    ]
    completed = cycle.get("completed", 0)
    failed = cycle.get("failed", 0)
    lapsed = cycle.get("lapsed", 0)

    embed = discord.Embed(
        title="ORDO XENOS TARGET PACKAGES",
        color=0x8B0000,
    )
    embed.add_field(
        name="Standing",
        value=f"{_rep_bar(rep)} {rep:+.2f}",
        inline=False,
    )
    embed.add_field(
        name="Cycle",
        value=f"✅ {completed} completed  ❌ {failed} failed  ⏳ {lapsed} lapsed",
        inline=False,
    )

    if not active_pkgs:
        embed.add_field(name="Active Packages", value="No active packages.", inline=False)
        return embed

    lines = []
    for p in active_pkgs[:20]:
        kt = p.get("assigned_kt", "")
        assign = f"→ {kt}" if kt else ""
        mode_short = "HS" if "Hard" in p.get("mode", "") else "Ω"
        lines.append(f"`{p['id']}` {mode_short} {assign}  [{p['status'].upper()}]")

    embed.add_field(
        name=f"Active Packages ({len(active_pkgs)})",
        value="\n".join(lines) or "None",
        inline=False,
    )
    return embed


# ---------------------------------------------------------------------------
# Pagination view
# ---------------------------------------------------------------------------

class PackagePaginatorView(discord.ui.View):
    def __init__(self, packages: list, rep: float, show_distribute: bool = False):
        super().__init__(timeout=600)
        self.packages = packages
        self.rep = rep
        self.index = 0
        self.show_distribute = show_distribute

        if show_distribute:
            distribute_btn = discord.ui.Button(
                label="Distribute All",
                style=discord.ButtonStyle.danger,
                custom_id="tp_distribute_all",
            )
            distribute_btn.callback = self.distribute_all
            self.add_item(distribute_btn)

    def current_embed(self) -> discord.Embed:
        return _build_package_embed(
            self.packages[self.index],
            self.rep,
            index=self.index + 1,
            total=len(self.packages),
        )

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = (self.index - 1) % len(self.packages)
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = (self.index + 1) % len(self.packages)
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def distribute_all(self, interaction: discord.Interaction):
        guild = interaction.guild
        ids = [p["id"] for p in self.packages]
        await interaction.response.defer(ephemeral=True)
        await distribute_packages(ids, guild)

        # Disable the distribute button after use
        for item in self.children:
            if getattr(item, "custom_id", None) == "tp_distribute_all":
                item.disabled = True
                item.label = "Distributed ✓"
        await interaction.edit_original_response(
            content=f"**{len(ids)} package{'s' if len(ids) != 1 else ''} distributed to Watch Captains.**",
            embed=self.current_embed(),
            view=self,
        )


# ---------------------------------------------------------------------------
# Permission checks
# ---------------------------------------------------------------------------

def _is_admin(member: discord.Member) -> bool:
    """Return True if member is in admin_user_ids config."""
    admin_ids = set(str(x) for x in ((_b("CONFIG") or {}).get("admin_user_ids") or []))
    return str(getattr(member, "id", None)) in admin_ids


def _is_debug_mode() -> bool:
    return bool(_b("DEBUG_MODE"))


def _has_role(member: discord.Member, role_name: str) -> bool:
    return any((getattr(r, "name", "") or "").strip() == role_name for r in getattr(member, "roles", []))


def _is_watch_master(member: discord.Member) -> bool:
    if _is_admin(member):
        return True
    if _is_debug_mode():
        return False
    return _has_role(member, "Watch Master")


def _is_captain_or_lt(member: discord.Member) -> bool:
    if _is_admin(member):
        return True
    if _is_debug_mode():
        return False
    return _has_role(member, "Watch Captain") or _has_role(member, "Watch Lieutenant")


_CADRE_LEADER_ROLES = {
    "Lord Executioner", "Forgemaster", "Chief Apothecary",
    "High Chaplain", "Void Warden", "Castellan",
}


def _is_cadre_leader(member: discord.Member) -> bool:
    if _is_admin(member):
        return True
    if _is_debug_mode():
        return False
    roles = _member_role_names(member)
    return bool(roles.intersection(_CADRE_LEADER_ROLES))


def _cadre_leader_owns(cadre_leader: discord.Member, specialist_role: str) -> bool:
    """Return True if the cadre leader has authority over the given specialist role."""
    _CADRE_OWNERSHIP = {
        "Lord Executioner": {"Kill Team Champion", "Company Champion"},
        "Forgemaster": {"Watch Techmarine", "Venerable Dreadnought", "Honored Dreadnought"},
        "Chief Apothecary": {"Watch Apothecary"},
        "High Chaplain": {"Watch Chaplain", "Judiciar"},
        "Void Warden": {"Watch Librarian"},
        "Castellan": {"Watch Keeper"},
    }
    for cl_role, owned in _CADRE_OWNERSHIP.items():
        if _has_role(cadre_leader, cl_role) and specialist_role in owned:
            return True
    return False


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

_bot_tree = None


def _get_tree():
    m = _sys.modules.get("opscribe.bot") or _sys.modules.get("bot")
    return getattr(m, "tree", None) if m else None


# /request_target_packages — WM only
@app_commands.command(
    name="request_target_packages",
    description="[Watch Master] Request a new batch of Ordo Xenos target packages.",
)
async def request_target_packages(interaction: discord.Interaction):
    if not _is_watch_master(interaction.user):
        await interaction.response.send_message(
            "Only the Watch Master may request target packages.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild

    packages = await generate_packages(guild)
    data = _load_tp()
    rep = data.get("rep", 0.0)

    if not packages:
        await interaction.followup.send("No active Kill Teams found — cannot generate packages.", ephemeral=True)
        return

    view = PackagePaginatorView(packages, rep, show_distribute=True)
    await interaction.followup.send(
        content=f"**{len(packages)} target package{'s' if len(packages) != 1 else ''} received from Ordo Xenos.** "
                f"Review below and press **Distribute All** when ready.",
        embed=view.current_embed(),
        view=view,
        ephemeral=True,
    )


# /target_packages — role-overloaded view
@app_commands.command(
    name="target_packages",
    description="View Ordo Xenos target packages relevant to your role.",
)
async def target_packages(interaction: discord.Interaction):
    member = interaction.user
    data = _load_tp()
    rep = data.get("rep", 0.0)
    packages = data.get("packages", {})

    if _is_watch_master(member):
        embed = _build_status_board_embed(data)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if _is_captain_or_lt(member):
        from .roster_ops import _get_member_company_name
        company = _get_member_company_name(member)
        company_pkgs = [
            p for p in packages.values()
            if (p.get("assigned_company") == company or p["status"] == STATUS_DISTRIBUTED)
            and p["status"] not in (STATUS_COMPLETED, STATUS_FAILED, STATUS_LAPSED)
        ]
        if not company_pkgs:
            await interaction.response.send_message("No active packages for your company.", ephemeral=True)
            return
        view = PackagePaginatorView(company_pkgs, rep)
        await interaction.response.send_message(embed=view.current_embed(), view=view, ephemeral=True)
        return

    if _is_cadre_leader(member):
        # Show packages that have required roles belonging to their cadre
        cadre_pkgs = [
            p for p in packages.values()
            if p["status"] in (STATUS_AWAITING_SPECIALIST,)
            and any(_cadre_leader_owns(member, r) for r in p.get("required_roles", []))
        ]
        if not cadre_pkgs:
            await interaction.response.send_message("No packages currently awaiting your cadre's specialists.", ephemeral=True)
            return
        view = PackagePaginatorView(cadre_pkgs, rep)
        await interaction.response.send_message(embed=view.current_embed(), view=view, ephemeral=True)
        return

    # Default: show packages assigned to member's KT
    from .forge_ops import _resolve_killteam_for_member
    kt = _resolve_killteam_for_member(member)
    if kt:
        kt_pkgs = [
            p for p in packages.values()
            if p.get("assigned_kt") == kt
            and p["status"] not in (STATUS_COMPLETED, STATUS_FAILED, STATUS_LAPSED)
        ]
        if not kt_pkgs:
            await interaction.response.send_message("No active packages assigned to your Kill Team.", ephemeral=True)
            return
        view = PackagePaginatorView(kt_pkgs, rep)
        await interaction.response.send_message(embed=view.current_embed(), view=view, ephemeral=True)
        return

    await interaction.response.send_message("No packages found for your current role.", ephemeral=True)


# /assign_package — Captain assigns to KT, or Cadre Leader assigns specialist
@app_commands.command(
    name="assign_package",
    description="[Captain/Cadre Leader] Assign a target package to a Kill Team or attach a specialist.",
)
@app_commands.describe(
    package_id="The target package ID (e.g. OX-A4B2C)",
    kill_team="Kill Team to assign the package to (Captains only)",
    specialist="Specialist member to attach to the package (Cadre Leaders only)",
)
async def assign_package(
    interaction: discord.Interaction,
    package_id: str,
    kill_team: Optional[str] = None,
    specialist: Optional[discord.Member] = None,
):
    member = interaction.user
    guild = interaction.guild
    package_id = package_id.strip().upper()

    if _is_captain_or_lt(member):
        if not kill_team:
            await interaction.response.send_message(
                "Please specify a Kill Team name when assigning a package.", ephemeral=True
            )
            return
        from .roster_ops import _get_member_company_name
        company = _get_member_company_name(member)
        success, msg = await assign_package_to_kt(package_id, kill_team, company, member, guild)
        await interaction.response.send_message(msg, ephemeral=True)
        return

    if _is_cadre_leader(member):
        if not specialist:
            await interaction.response.send_message(
                "Please specify a specialist member to attach.", ephemeral=True
            )
            return
        # Validate cadre leader owns the specialist's role
        specialist_roles = _member_role_names(specialist)
        owned = any(_cadre_leader_owns(member, r) for r in specialist_roles)
        if not owned:
            await interaction.response.send_message(
                f"{specialist.display_name} is not in your cadre.", ephemeral=True
            )
            return
        success, msg = await assign_specialist(package_id, specialist, member, guild)
        await interaction.response.send_message(msg, ephemeral=True)
        return

    await interaction.response.send_message(
        "You do not have permission to assign packages. "
        "This command is for Watch Captains, Watch Lieutenants, and Cadre Leaders.",
        ephemeral=True,
    )


# /submit_target_package
@app_commands.command(
    name="submit_target_package",
    description="Submit a completed Ordo Xenos target package.",
)
@app_commands.describe(
    package_id="The target package ID (e.g. OX-A4B2C)",
    aar_link="Link to the After Action Report",
)
async def submit_target_package(
    interaction: discord.Interaction,
    package_id: str,
    aar_link: str,
):
    package_id = package_id.strip().upper()
    success, msg = await submit_package(package_id, aar_link, interaction.user, interaction.guild)
    await interaction.response.send_message(msg, ephemeral=not success)


# /target_package_status
@app_commands.command(
    name="target_package_status",
    description="View the full status of a specific target package.",
)
@app_commands.describe(package_id="The target package ID (e.g. OX-A4B2C)")
async def target_package_status(
    interaction: discord.Interaction,
    package_id: str,
):
    member = interaction.user
    package_id = package_id.strip().upper()

    can_view = (
        _is_admin(member)
        or _is_watch_master(member)
        or _is_captain_or_lt(member)
        or _is_cadre_leader(member)
    )

    data = _load_tp()
    pkg = data["packages"].get(package_id)
    if not pkg:
        await interaction.response.send_message(f"Package `{package_id}` not found.", ephemeral=True)
        return

    # Non-command members can only view their own KT's packages
    if not can_view:
        from .forge_ops import _resolve_killteam_for_member
        kt = _resolve_killteam_for_member(member)
        if pkg.get("assigned_kt") != kt:
            await interaction.response.send_message(
                f"Package `{package_id}` is not assigned to your Kill Team.", ephemeral=True
            )
            return

    embed = _build_package_embed(pkg, data.get("rep", 0.0))

    # Add specialist info if any
    specialist_ids = pkg.get("assigned_specialist_ids", [])
    if specialist_ids:
        names = []
        for sid in specialist_ids:
            m = interaction.guild.get_member(sid)
            names.append(m.display_name if m else str(sid))
        embed.add_field(name="Attached Specialists", value=", ".join(names), inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# Register commands + expiry loop
# ---------------------------------------------------------------------------

def _register_commands(tree: app_commands.CommandTree) -> None:
    tree.add_command(request_target_packages)
    tree.add_command(target_packages)
    tree.add_command(assign_package)
    tree.add_command(submit_target_package)
    tree.add_command(target_package_status)



@_tasks.loop(minutes=30)
async def _tp_expiry_loop():
    """Periodically expire overdue packages."""
    try:
        m = _sys.modules.get("opscribe.bot") or _sys.modules.get("bot")
        bot = getattr(m, "bot", None) if m else None
        if not bot:
            return
        guild_id = _b("CONFIG") and (_b("CONFIG") or {}).get("guild_id")
        if not guild_id:
            for guild in bot.guilds:
                await expire_packages(guild)
        else:
            guild = bot.get_guild(int(guild_id))
            if guild:
                await expire_packages(guild)
    except Exception as e:
        _g.logger.error(f"[TP] Expiry loop error: {e}")


# Public exports
__all__ = [
    "request_target_packages",
    "target_packages",
    "assign_package",
    "submit_target_package",
    "target_package_status",
    "_register_commands",
    "_tp_expiry_loop",
    "generate_packages",
    "distribute_packages",
    "expire_packages",
]
