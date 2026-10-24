"""Galactic War campaign rules, independent of Discord and legacy directives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from typing import Iterable, Mapping

RENOWN_THRESHOLDS = (4, 8, 28, 56)
RENOWN_UNLOCKS = ("", "Capes", "Halos", "Crests", "Fortress lore eligibility")
RENOWN_WINDOW = timedelta(days=28)
CAMPAIGN_DURATION = timedelta(days=14)
ASSIST_CREDIT = Fraction(1, 4)


class WarReportPending(RuntimeError):
    """A valid strike contract needs independent evidence review before settlement."""


SPECIALIST_TRACKS = (
    ("Bladeguard", "First Blade", "High Blade", "Blade Master"),
    ("Watch Chaplain", "Reclusiarch", "High Chaplain"),
    ("Watch Apothecary", "Primus Medicae", "Chief Apothecary"),
    ("Watch Librarian", "Lexicanum Primus", "Void Warden"),
    ("Watch Techmarine", "Forge Adept", "Forgemaster"),
    ("Kill-Marine", "Venator Primus", "Huntmaster"),
)
EQUERRY_TITLES = {
    "First Blade": "High Blade", "Watch Chaplain": "Reclusiarch",
    "Watch Apothecary": "Primus Medicae", "Watch Librarian": "Lexicanum Primus",
    "Watch Techmarine": "Forge Adept", "Kill-Marine": "Venator Primus",
}
REQUIREMENT_WEIGHTS = {
    "": 150,
    "Bladeguard": 4, "First Blade": 3, "High Blade": 2, "Blade Master": 1,
    "Watch Chaplain": 5, "Reclusiarch": 2, "High Chaplain": 1,
    "Watch Apothecary": 5, "Primus Medicae": 2, "Chief Apothecary": 1,
    "Watch Librarian": 5, "Lexicanum Primus": 2, "Void Warden": 1,
    "Watch Techmarine": 5, "Forge Adept": 2, "Forgemaster": 1,
    "Kill-Marine": 5, "Venator Primus": 2, "Huntmaster": 1,
}
ATLAS_BIOMES = ("jungle", "desert", "ice", "industrial", "warzone", "underhive", "starship", "asteroid")
ATLAS_FIT = {
    "hive": "PPPNNNPS", "forge": "SPPNNPNP", "agri": "NPSPPSSS",
    "civilised": "PPPPNPPS", "feral": "NNNSPSSS", "death": "NPPSPSSS",
    "dead": "SNNPNPPN", "cemetery": "SPPSNPSS", "research": "NPNPPPNN",
}
WORLD_TYPES = {
    "hive_world": "hive", "forge_world": "forge", "agri_world": "agri",
    "civilized_world": "civilised", "civilised_world": "civilised",
    "feral_world": "feral", "feudal_world": "feudal", "death_world": "death",
    "dead_world": "dead", "cemetery_world": "cemetery",
    "research_world": "research", "research_station": "research", "station": "research",
}


def shared_campaign_content(geography: dict, definition: dict) -> tuple[list[dict], list[dict]]:
    catalog = geography.get("objectiveCatalog")
    if not isinstance(catalog, dict) or catalog.get("schemaVersion") != 1:
        raise ValueError("Shared objective catalogue version 1 is required; rebuild Strategium geography")
    entries = catalog.get("objectives")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
        raise ValueError("Invalid shared objective catalogue")
    contracts = {entry["id"]: entry for entry in definition["objectives"]}
    if len(contracts) != len(definition["objectives"]):
        raise ValueError("Duplicate campaign objective IDs")
    settings = {f"{world}|{biome}" for world in {*ATLAS_FIT, "feudal"} for biome in ATLAS_BIOMES}
    seen = set()
    objectives = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise ValueError("Invalid shared objective ID")
        identity = entry["id"]
        if identity in seen or identity not in contracts:
            raise ValueError("Shared objective IDs do not match campaign contracts")
        seen.add(identity)
        contract = contracts[identity]
        rank = contract.get("required_rank", "")
        if rank not in REQUIREMENT_WEIGHTS or entry.get("category") != ("specialist" if rank else "general"):
            raise ValueError(f"Shared objective category does not match campaign contract: {identity}")
        pairs = entry.get("settings")
        if not isinstance(pairs, list) or len(pairs) > 80 or any(not isinstance(pair, str) or pair not in settings for pair in pairs) or len(pairs) != len(set(pairs)):
            raise ValueError(f"Invalid shared objective settings: {identity}")
        if not isinstance(entry.get("name"), str) or not entry["name"].strip() or len(entry["name"]) > 120:
            raise ValueError(f"Invalid shared objective name: {identity}")
        objectives.append({**contract, "name": entry["name"], "settings": pairs.copy(), "version": catalog["schemaVersion"]})
    if seen != set(contracts):
        raise ValueError("Shared objective IDs do not match campaign contracts")
    systems = {system["id"]: system for system in geography["systems"]}
    world_classes = geography.get("worldClasses")
    if not isinstance(world_classes, dict) or set(world_classes) != {*ATLAS_FIT, "feudal"}:
        raise ValueError("Shared Imperial world classifications are required; rebuild Strategium geography")
    for classification in world_classes.values():
        if not isinstance(classification, dict) or any(not isinstance(classification.get(field), str) or not 1 <= len(classification[field]) <= 30 for field in ("name", "symbol")):
            raise ValueError("Invalid shared Imperial world classification")
    planets = []
    for body in geography["bodies"]:
        if body.get("battleEligible") is not True:
            continue
        metadata = definition.get("planets", {}).get(body["id"], {})
        authored = "biome" in body
        world_type = WORLD_TYPES.get(body["kind"], "") if authored else metadata.get("world_type") or WORLD_TYPES.get(body["kind"], "")
        biome = body["biome"] if authored else metadata.get("biome", "")
        if not isinstance(world_type, str) or world_type not in {*ATLAS_FIT, "feudal"} or not isinstance(biome, str) or biome not in ATLAS_BIOMES:
            raise ValueError(f"World type and biome must be classified for {body['id']}")
        planets.append({
            "id": body["id"], "name": body["name"], "system_id": body["systemId"],
            "subsector_id": systems[body["systemId"]]["sectorId"],
            "world_type": world_type, "biome": biome,
            "world_class": dict(world_classes[world_type]),
            "terrain_provisional": not authored and metadata.get("provisional") is True,
        })
    return planets, objectives


def atlas_compatible(world_type: str, biome: str, objective: dict) -> bool:
    if world_type not in {*ATLAS_FIT, "feudal"} or biome not in ATLAS_BIOMES:
        return False
    required_rank = objective.get("required_rank", "")
    if required_rank not in REQUIREMENT_WEIGHTS:
        return False
    if "settings" in objective:
        return f"{world_type}|{biome}" in objective["settings"]
    if world_type not in ATLAS_FIT:
        return False
    if not required_rank:
        return True
    fit = ATLAS_FIT[world_type][ATLAS_BIOMES.index(biome)]
    return fit != "S" and (
        world_type in objective.get("worlds", [])
        or biome in objective.get("biomes", []) and fit == "N"
    )


def effective_roles(roles: Iterable[str]) -> frozenset[str]:
    aliases = {"Forge Master": "Forgemaster", "Hunt Master": "Huntmaster", "Watch Sister": "Watch Brother"}
    result = {aliases.get(role, role) for role in roles}
    if "High Command Equerry" in result:
        result.update(title for base, title in EQUERRY_TITLES.items() if base in result)
    return frozenset(result)


def specialist_requirement(roll: int) -> str:
    if type(roll) is not int or not 0 <= roll < 200:
        raise ValueError("Requirement roll must be an integer from 0 to 199")
    boundary = 0
    for rank, weight in REQUIREMENT_WEIGHTS.items():
        boundary += weight
        if roll < boundary:
            return rank
    raise ValueError("Requirement weights must total 100")


def meets_specialist_requirement(member: WarMember, rank: str) -> bool:
    roles = effective_roles(member.roles)
    for track in SPECIALIST_TRACKS:
        if rank in track:
            return bool(roles.intersection(track[track.index(rank):]))
    return False


def utc_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware timestamp is required")
    return value.astimezone(timezone.utc)


def renown_tier(score: Fraction | float | int) -> int:
    return sum(score >= threshold for threshold in RENOWN_THRESHOLDS)


@dataclass(frozen=True)
class Contribution:
    strike_id: str
    member_id: str
    formation_id: str
    completed_at: datetime
    full_credit: bool


@dataclass(frozen=True)
class Renown:
    score: Fraction = Fraction(0)
    member_count: int = 0

    @property
    def tier(self) -> int:
        return renown_tier(self.score)

    def as_dict(self) -> dict:
        return {
            "score": float(self.score),
            "tier_index": self.tier,
            "tier": ("Unearned", "I", "II", "III", "IV")[self.tier],
            "unlocks": RENOWN_UNLOCKS[self.tier],
            "member_count": self.member_count,
        }


def formation_renown(
    rosters: Mapping[str, Iterable[str]],
    contributions: Iterable[Contribution],
    now: datetime,
) -> dict[str, Renown]:
    now = utc_time(now)
    cutoff = now - RENOWN_WINDOW
    totals: dict[str, Fraction] = {}
    seen: set[tuple[str, str]] = set()
    for contribution in contributions:
        completed_at = utc_time(contribution.completed_at)
        if not cutoff < completed_at <= now:
            continue
        identity = (contribution.strike_id, contribution.member_id)
        if identity in seen:
            raise ValueError("Duplicate strike participation")
        seen.add(identity)
        formation = contribution.formation_id
        totals[formation] = totals.get(formation, Fraction(0)) + (
            1 if contribution.full_credit else ASSIST_CREDIT
        )
    result = {}
    for formation, members in rosters.items():
        count = len(set(members))
        result[formation] = Renown(
            totals.get(formation, Fraction(0)) / count if count else Fraction(0), count
        )
    return result


def command_renown(kill_teams: Iterable[Renown]) -> Renown:
    teams = list(kill_teams)
    return Renown(
        sum((team.score for team in teams), Fraction(0)) / len(teams)
        if teams else Fraction(0)
    )


def lifetime_laurel(completions: int) -> str:
    if completions >= 300:
        return "Gold"
    if completions >= 200:
        return "Silver"
    if completions >= 100:
        return "Bronze"
    return ""


@dataclass(frozen=True)
class WarMember:
    member_id: str
    roles: frozenset[str]
    company_id: str = ""
    kill_team_id: str = ""
    cadre_id: str = ""
    active: bool = True
    platform: str = ""
    can_review: bool = False
    watch_brother: bool = False

    @property
    def formation_id(self) -> str:
        return self.cadre_id or self.kill_team_id

    def has_role(self, *names: str) -> bool:
        return bool(self.roles.intersection(names))


def full_commitment_credit(
    member: WarMember, subsector_id: str, system_id: str,
    commitments: Mapping[str, str], assignments: Mapping[str, str],
    specialist_ids: Iterable[str] = (),
) -> bool:
    if member.cadre_id:
        return member.member_id in specialist_ids
    if commitments.get(member.company_id) != subsector_id:
        return False
    if member.has_role("Veteran Sergeant", "Watch Lieutenant", "Watch Captain"):
        return True
    return bool(member.kill_team_id and assignments.get(member.kill_team_id) == system_id)


def pressure(hours_without_success: float) -> float:
    return 1 + 0.5 * min(1, max(0, (hours_without_success - 24) / 48))


def pressure_integral(hours_without_success: float) -> float:
    hours = max(0, hours_without_success)
    ramp = min(48, max(0, hours - 24))
    return hours + ramp * ramp / 192 + max(0, hours - 72) / 2


def advance_balance(campaign: dict, now: datetime) -> None:
    now = min(utc_time(now), datetime.fromisoformat(campaign["deadline"]))
    previous = datetime.fromisoformat(campaign["calculated_at"])
    if now <= previous:
        return
    last_success = datetime.fromisoformat(campaign["last_success_at"])
    start_hours = (previous - last_success).total_seconds() / 3600
    end_hours = (now - last_success).total_seconds() / 3600
    drain = 2 * (pressure_integral(end_hours) - pressure_integral(start_hours)) / 24
    campaign["balance"] = max(0.0, campaign["balance"] - drain)
    campaign["calculated_at"] = now.isoformat()