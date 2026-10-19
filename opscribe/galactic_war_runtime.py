"""Discord membership and deployment configuration for the Galactic War service."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord.ext import tasks

from . import _bot_globals as _g
from .constants import _is_member_in_reserves, WATCH_CAPTAIN_ROLE_ID, WATCH_LIEUTENANT_ROLE_ID, HIGH_COMMAND_ROLE_ID, WATCH_BROTHER_ROLE_ID
from .galactic_war import WarMember, effective_roles
from .galactic_war_store import GalacticWarStore, SPECIALIST_TRACKS

ROOT = Path(__file__).resolve().parents[1]
WORLD_TYPES = {
    "hive_world": "hive", "forge_world": "forge", "agri_world": "agri",
    "feral_world": "feral", "death_world": "death", "dead_world": "dead",
}


def vanilla_operations() -> list[dict]:
    from . import aar_ops

    reference = json.loads((ROOT / "reference" / "operations.json").read_text(encoding="utf-8"))
    missions = {
        operation["name"].casefold(): operation for operation in reference["operations"]
        if operation.get("faction_listed") in {"tyranid", "mixed"}
        and operation["name"] != "Siege" and not operation.get("intel_lapse_forced")
    }
    result = []
    for mode in ("ops_strat", "omega"):
        for mission in aar_ops._mission_options_for_mode(mode):
            operation = missions.get(mission.label.casefold())
            if operation is None:
                continue
            for difficulty in aar_ops._difficulty_options_for_mode(mode):
                result.append({
                    "mode": mode, "capacity": aar_ops._max_brothers_for_mode(mode),
                    "mission": mission.label, "mission_value": mission.value,
                    "difficulty": difficulty.value,
                    "difficulty_class": aar_ops.classify_difficulty(difficulty.value),
                    "game_planet": operation["planet"],
                })
    return result


def get_store() -> GalacticWarStore:
    config = (_g.CONFIG or {}).get("galactic_war") or {}
    definition_path = Path(config.get("definition_path") or ROOT / "reference" / "galactic_war.json")
    definition = json.loads(definition_path.read_text(encoding="utf-8"))
    geography_path = Path(config.get("geography_path") or ROOT.parent.parent / "strategium" / "data" / "reach_geography.json")
    raw = json.loads(geography_path.read_text(encoding="utf-8"))
    systems = {system["id"]: system for system in raw["systems"]}
    neighbors = definition.get("neighbors", {})
    sector_ids = {sector["id"] for sector in raw["sectors"]}
    for source, targets in neighbors.items():
        if source not in sector_ids or len(targets) != len(set(targets)):
            raise ValueError("Invalid subsector adjacency")
        for target in targets:
            if target not in sector_ids or target == source or source not in neighbors.get(target, []):
                raise ValueError("Subsector adjacency must be valid and symmetric")
    planets = []
    for body in raw["bodies"]:
        if body.get("battleEligible") is not True:
            continue
        metadata = definition.get("planets", {}).get(body["id"], {})
        planets.append({
            "id": body["id"], "name": body["name"], "system_id": body["systemId"],
            "subsector_id": systems[body["systemId"]]["sectorId"],
            "world_type": metadata.get("world_type") or WORLD_TYPES.get(body["kind"], ""),
            "biome": metadata.get("biome", ""),
            "terrain_provisional": metadata.get("provisional") is True,
        })
    data_dir = Path((_g.CONFIG or {}).get("data_dir") or ROOT / "data")
    return GalacticWarStore(data_dir / "galactic_war.sqlite3", {
        "sectors": raw["sectors"], "systems": raw["systems"],
        "planets": planets, "neighbors": neighbors,
    }, definition["objectives"], vanilla_operations())


def directory(guild) -> tuple[dict[str, WarMember], dict[str, list[str]], dict[str, list[str]], list[dict]]:
    from . import strategium_publish as publisher
    from .forge_ops import _get_player_platform

    catalogue, _slots = publisher._resolve_company_kill_teams(guild)
    team_ids = {}
    teams = []
    rosters = {key: [] for key in set(publisher.FORMATION_BY_RANK.values())}
    company_teams = {}
    for company, entries in catalogue.items():
        company_teams[company] = []
        for entry in entries:
            identity = f"{company}-{entry['roleId']}"
            team_ids[entry["name"].casefold()] = identity
            teams.append({"id": identity, "name": entry["name"], "company_id": company, "display_id": entry["id"]})
            rosters[identity] = []
            company_teams[company].append(identity)
    members = {}
    for member in guild.members:
        if getattr(member, "bot", False):
            continue
        roles = effective_roles(role.name for role in member.roles)
        company = str(publisher._company(member) or "")
        rank = publisher.RANK_KEYS.get(publisher._role_name(member) or "", "")
        cadre = publisher.FORMATION_BY_RANK.get(rank, "")
        for track, formation in zip(SPECIALIST_TRACKS, ("hall_of_blades", "reclusiam", "apothecarion", "librarius", "armory", "black_vault")):
            if roles.intersection(track):
                cadre = formation
                break
        if not cadre:
            for role in roles:
                candidate = publisher.FORMATION_BY_RANK.get(publisher.RANK_KEYS.get(role, ""))
                if candidate:
                    cadre = candidate
                    break
        kill_team = team_ids.get((publisher._kill_team(member) or "").casefold(), "")
        can_review = HIGH_COMMAND_ROLE_ID in {role.id for role in member.roles} or bool(roles.intersection({"Watch Techmarine", "Forgemaster"}))
        watch_brother = WATCH_BROTHER_ROLE_ID in {role.id for role in member.roles}
        actor = WarMember(str(member.id), roles, company, kill_team, cadre, not _is_member_in_reserves(member), _get_player_platform(member) or "", can_review, watch_brother)
        members[actor.member_id] = actor
        if actor.formation_id:
            rosters.setdefault(actor.formation_id, []).append(actor.member_id)
    return members, rosters, company_teams, teams


def web_snapshot(guild, member_id: str, planet_id: str = "") -> dict:
    store = get_store()
    now = datetime.now(timezone.utc)
    members, rosters, company_teams, teams = directory(guild)
    actor = members.get(member_id)
    if actor is None:
        raise PermissionError("Guild membership is required")
    state = store.snapshot(now)
    can_review = actor.can_review or actor.has_role("Watch Master", "Forgemaster")
    reviews = []
    if can_review:
        for review in state["reviews"].values():
            if review["status"] != "pending" and not review.get("archive_pending"):
                continue
            record = review["record"]
            url = str(record.get("receipt_url") or record.get("message_url") or "")
            reviews.append({
                "strike_id": review["strike_id"], "aar_id": review["aar_id"], "status": review["status"],
                "reason": review["reason"], "mission": record.get("mission", ""),
                "difficulty": record.get("difficulty", ""), "participants": record.get("brother_ids", []),
                "receipt_url": url if url.startswith("https://discord.com/channels/") else "",
            })
    results = []
    if can_review:
        for identity, result in sorted(state["settlements"].items(), key=lambda item: item[1].get("processed_at", ""), reverse=True)[:50]:
            attempt = state["attempts"][identity]
            results.append({"strike_id": identity, "aar_id": result["aar_id"], "mission": attempt["offer"]["aar_mission"], "participants": result["participant_ids"], "reversed": result.get("reversed", False)})
    return {
        "version": state["version"], "campaigns": state["campaigns"],
        "commitments": state["commitments"], "assignments": state["assignments"],
        "proposals": [proposal for proposal in state["proposals"].values() if proposal["status"] == "pending" and (actor.has_role("Watch Master") or proposal["company_id"] == actor.company_id)],
        "moves": state["moves"], "kill_teams": teams,
        "planets": store.geography["planets"],
        "boards": {planet_id: state["boards"].get(planet_id, [])} if planet_id else {},
        "recognition": store.recognition(rosters, company_teams, now),
        "actor": {"id": actor.member_id, "company_id": actor.company_id, "kill_team_id": actor.kill_team_id, "roles": sorted(actor.roles), "active": actor.active, "platform": actor.platform, "can_review": can_review, "watch_brother": actor.watch_brother},
        "reviews": reviews, "recent_results": results,
        "summary": {"locked": sum(offer["status"] in {"locked", "review_pending", "deployed"} for board in state["boards"].values() for offer in board)},
        "participant_names": {identity: str(getattr(guild.get_member(int(identity)), "display_name", identity)) for identity in members if identity.isdigit()},
        "content": {"objectives": len(store.catalogue), "operation_formats": len(store.operations), "kind": "vanilla"},
    }


async def recover_review_archives(guild, store: GalacticWarStore) -> None:
    from . import aar_ops
    from .galactic_war import WarReportPending

    if _g.DATASTORE is None:
        return
    for identity, review in store.snapshot(datetime.now(timezone.utc))["reviews"].items():
        if review["status"] != "approved" or not review.get("archive_pending"):
            continue
        try:
            await aar_ops.save_aar_record(review["record"])
            flush = getattr(_g.DATASTORE, "flush", None)
            if callable(flush):
                await flush()
            notifications = await aar_ops._process_challenge_tracking(review["record"], guild)
            if notifications:
                await aar_ops._send_challenge_eligibility_notifications(notifications, guild)
            await asyncio.to_thread(store.acknowledge_review_archive, identity)
        except (OSError, ValueError, WarReportPending):
            _g.logger.exception("Approved Galactic War AAR archive retry failed strike=%s", identity)


async def deliver_notices(guild, store: GalacticWarStore, config: dict) -> None:
    channel_id = config.get("notification_channel_id")
    if not channel_id:
        return
    for notice_id, notice in store.snapshot(datetime.now(timezone.utc))["outbox"].items():
        if notice["delivered"]:
            continue
        company_target = notice["target"] if notice["event"] in {"company_committed", "approval_requested"} else ""
        destination = config.get("company_channel_ids", {}).get(company_target) or channel_id
        channel = guild.get_channel(int(destination))
        if channel is None:
            continue
        marker = f"galactic-war:{notice_id}"
        existing = None
        async for message in channel.history(limit=100):
            if any(getattr(getattr(embed, "footer", None), "text", "") == marker for embed in getattr(message, "embeds", [])):
                existing = message
                break
        if existing is None:
            target_name = next((sector["name"] for sector in store.geography["sectors"] if sector["id"] == notice["target"]), notice["target"])
            url = str(config.get("strategium_url") or "").strip()
            description = f"{target_name}\n[Report to Strategium]({url})" if url.startswith("https://") else target_name
            embed = discord.Embed(title=f"Galactic War: {notice['event'].replace('_', ' ').title()}", description=description, color=0x4F8A62)
            embed.set_footer(text=marker)
            pings = ""
            role_ids = [WATCH_CAPTAIN_ROLE_ID, WATCH_LIEUTENANT_ROLE_ID, HIGH_COMMAND_ROLE_ID]
            if company_target:
                from .strategium_publish import COMPANY_NAMES
                company_name = f"Watch Company {COMPANY_NAMES.get(int(company_target), '')}"
                role_ids = [role.id for role in guild.roles if role.name == company_name]
            if notice["event"] in {"invasion", "lost", "company_committed", "approval_requested"}:
                pings = " ".join(f"<@&{identity}>" for identity in role_ids)
            existing = await channel.send(content=pings or None, embed=embed, allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=[discord.Object(id=identity) for identity in role_ids]))
        await asyncio.to_thread(store.acknowledge_notice, notice_id, str(existing.id))


@tasks.loop(minutes=1)
async def campaign_loop():
    try:
        from .strategium_publish import _select_guild
        store = get_store()
        await asyncio.to_thread(store.tick, datetime.now(timezone.utc))
        guild = _select_guild(_g.bot)
        if guild:
            await recover_review_archives(guild, store)
            await deliver_notices(guild, store, (_g.CONFIG or {}).get("galactic_war") or {})
    except (OSError, ValueError, discord.HTTPException):
        _g.logger.exception("Galactic War campaign tick failed")