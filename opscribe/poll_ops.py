"""Governance poll subsystem for Watch Command voting workflows."""

import asyncio
import json
import math
import os
import sys as _sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import tasks

from . import _bot_globals as _g
from .constants import *  # noqa: F401,F403
from .role_aliases import canonicalize_role_name


_POLL_LOCK = asyncio.Lock()
_UNSPECIFIED_TARGET_ROLE = "Not specified"
_ALLOWED_TARGET_ROLE_NAMES = {
    "First Blade",
    "Blade Master",
    "Blademaster",
    "Huntmaster",
    "Watch Captain",
    "Watch Sergeant",
    "Veteran Sergeant",
    "Watch Lieutenant",
    "Watch Techmarine",
    "Forgemaster",
    "Void Warden",
    "Chief Apothecary",
    "High Chaplain",
    "Watch Chaplain",
    "Watch Librarian",
    "Watch Apothecary",
    "Honored Dreadnought",
    "Venerable Dreadnought",
    "Oathsworn",
    "Watch Master",
    "Kill-Marine",
}

_CADRE_RANKS = {
    "battle_line": {"Watch Sergeant", "Veteran Sergeant", "Watch Lieutenant", "Watch Captain", "Watch Master", "Oathsworn"},
    "armory": {"Watch Techmarine", "Forgemaster"},
    "librarius": {"Watch Librarian", "Void Warden"},
    "reclusiam": {"Watch Chaplain", "High Chaplain"},
    "apothecarion": {"Watch Apothecary", "Chief Apothecary"},
    "blades": {"First Blade", "Blade Master"},
    "black_vault": {"Kill-Marine", "Huntmaster"},
    "dreadnought": {"Honored Dreadnought", "Venerable Dreadnought"},
}
_LEADER_RANKS = {"Watch Captain", "Forgemaster", "Void Warden", "High Chaplain", "Chief Apothecary", "Blade Master", "Huntmaster", "Venerable Dreadnought"}
_EQUERRY_TARGETS = {"Watch Techmarine", "Watch Librarian", "Watch Chaplain", "Watch Apothecary", "First Blade", "Kill-Marine"}
_DEFAULT_WEIGHT_UNITS = {
    "sergeant_specialist": 100,
    "veteran_sergeant": 115,
    "lieutenant_equerry": 130,
    "captain_leader": 150,
    "watch_master": 175,
}


def _normalize_rank_name(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _canonicalize_rank_name(value: str) -> str:
    aliases = ((_g.CONFIG or {}).get("role_aliases") or {})
    canonical = canonicalize_role_name(value, role_aliases=aliases)
    return _normalize_rank_name(canonical)


_ALLOWED_TARGET_ROLE_NORMALIZED = {_canonicalize_rank_name(name) for name in _ALLOWED_TARGET_ROLE_NAMES}


def _b(name):
    """Resolve name via bot module for test-mock compatibility."""
    m = _sys.modules.get("opscribe.bot") or _sys.modules.get("bot")
    return getattr(m, name) if (m is not None and hasattr(m, name)) else globals().get(name)


def _poll_cfg() -> dict:
    cfg = (_g.CONFIG or {}).get("governance_poll") or {}
    return cfg if isinstance(cfg, dict) else {}


def _poll_channel_id() -> int:
    cfg = _poll_cfg()
    raw = cfg.get("channel_id") or GOVERNANCE_POLL_CHANNEL_ID
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(GOVERNANCE_POLL_CHANNEL_ID)


def _quorum_percent() -> float:
    cfg = _poll_cfg()
    try:
        return float(cfg.get("quorum_percent", 0.60) or 0.60)
    except Exception:
        return 0.60


def _pass_percent() -> float:
    cfg = _poll_cfg()
    try:
        return float(cfg.get("pass_percent", 0.80) or 0.80)
    except Exception:
        return 0.80


def _close_margin_percent() -> float:
    cfg = _poll_cfg()
    try:
        return float(cfg.get("close_margin_percent", 0.05) or 0.05)
    except Exception:
        return 0.05


def _poll_duration_hours() -> int:
    cfg = _poll_cfg()
    try:
        return int(cfg.get("duration_hours", 24) or 24)
    except Exception:
        return 24


def _load_polls_state() -> dict:
    try:
        if not os.path.exists(GOVERNANCE_POLLS_PATH):
            return {"next_id": 1, "polls": {}}
        with open(GOVERNANCE_POLLS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
            if not isinstance(data, dict):
                return {"next_id": 1, "polls": {}}
            data.setdefault("next_id", 1)
            data.setdefault("polls", {})
            return data
    except Exception:
        return {"next_id": 1, "polls": {}}


def _save_polls_state(state: dict) -> None:
    tmp = GOVERNANCE_POLLS_PATH + ".tmp"
    try:
        os.makedirs(os.path.dirname(GOVERNANCE_POLLS_PATH), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, GOVERNANCE_POLLS_PATH)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _next_poll_id(state: dict) -> str:
    nid = int(state.get("next_id") or 1)
    state["next_id"] = nid + 1
    return f"gov-{nid:04d}"


def _is_watch_command_member(member: discord.Member) -> bool:
    roles = getattr(member, "roles", []) or []
    return any((getattr(r, "name", "") or "").strip().lower() == "watch command" for r in roles)


def _is_reserves_or_interred(member: discord.Member) -> bool:
    role_ids = {getattr(r, "id", 0) for r in getattr(member, "roles", []) or []}
    role_names = {(getattr(r, "name", "") or "").strip().lower() for r in getattr(member, "roles", []) or []}
    if RESERVES_ROLE_ID in role_ids or "reserves" in role_names:
        return True
    return INTERRED_BROTHER_ROLE_NAME.lower() in role_names


def _eligible_electorate_snapshot(guild: discord.Guild, recuse_user_id: Optional[int]) -> list[str]:
    out: list[str] = []
    for member in getattr(guild, "members", []) or []:
        if getattr(member, "bot", False):
            continue
        if not _is_watch_command_member(member):
            continue
        if _is_reserves_or_interred(member):
            continue
        member_id = int(getattr(member, "id", 0) or 0)
        if recuse_user_id and member_id == int(recuse_user_id):
            continue
        out.append(str(member_id))
    return out


def _is_allowed_target_role_name(target_role_or_rank: str) -> bool:
    return _canonicalize_rank_name(target_role_or_rank) in _ALLOWED_TARGET_ROLE_NORMALIZED


def _destination_cadre(target: str) -> str:
    canonical = _canonicalize_rank_name(target)
    return next((cadre for cadre, ranks in _CADRE_RANKS.items()
                 if canonical in {_canonicalize_rank_name(rank) for rank in ranks}), "")


def _configured_equerry_cadre(member) -> Optional[str]:
    assignments = _poll_cfg().get("equerry_assignments", {})
    if not isinstance(assignments, dict):
        return None
    cadre = assignments.get(str(member.id))
    roles = {_canonicalize_rank_name(role.name) for role in getattr(member, "roles", [])}
    allowed = {_canonicalize_rank_name(rank) for rank in _EQUERRY_TARGETS
               if _destination_cadre(rank) == cadre}
    return cadre if roles & allowed else None


def _weighted_electorate_snapshot(guild, recuse_user_id, target, equerry_appointment=False) -> dict:
    cfg = _poll_cfg()
    overrides = cfg.get("base_weight_units", {})
    if not isinstance(overrides, dict) or set(overrides) - set(_DEFAULT_WEIGHT_UNITS):
        raise ValueError("Invalid governance voting weights or Equerry assignments.")
    units = {**_DEFAULT_WEIGHT_UNITS, **overrides}
    ordered = [units[tier] for tier in _DEFAULT_WEIGHT_UNITS]
    bonus = cfg.get("high_command_bonus_units", 25)
    multiplier = cfg.get("destination_multiplier", 2)
    assignments = cfg.get("equerry_assignments", {})
    if (any(type(value) is not int or value <= 0 for value in ordered)
            or ordered != sorted(set(ordered))
            or type(bonus) is not int or bonus <= 0
            or type(multiplier) is not int or multiplier < 2
            or not isinstance(assignments, dict)
                 or any(not isinstance(user_id, str) or not user_id.isdigit()
                     or not isinstance(value, str) or value not in _CADRE_RANKS
                     or value in {"battle_line", "dreadnought"}
                     for user_id, value in assignments.items())):
        raise ValueError("Invalid governance voting weights or Equerry assignments.")
    destination = _destination_cadre(target)
    if not destination:
        raise ValueError("The destination role has no voting group.")
    high_command_required = (equerry_appointment or _canonicalize_rank_name(target) in
                             {_canonicalize_rank_name(rank) for rank in _LEADER_RANKS | {"Watch Master"}})
    snapshot = {
        "policy_version": 2,
        "destination_cadre": destination,
        "equerry_appointment": equerry_appointment,
        "voter_weights": {},
        "turnout_ids": [],
        "high_command_ids": [],
        "high_command_quorum_required": high_command_required,
    }
    leaders = {_canonicalize_rank_name(rank) for rank in _LEADER_RANKS}
    specialist_ranks = {_canonicalize_rank_name(rank) for cadre, ranks in _CADRE_RANKS.items()
                        if cadre != "battle_line" for rank in ranks}
    for member in getattr(guild, "members", []) or []:
        user_id = str(member.id)
        if getattr(member, "bot", False) or _is_reserves_or_interred(member):
            continue
        if recuse_user_id and user_id == str(recuse_user_id):
            continue
        roles = {_canonicalize_rank_name(role.name) for role in getattr(member, "roles", [])}
        equerry_cadre = _configured_equerry_cadre(member)
        is_master = "watch master" in roles
        is_leader = bool(roles & leaders)
        is_equerry = bool(equerry_cadre)
        cadre = "battle_line" if is_master else equerry_cadre
        if not cadre:
            if is_master:
                cadre = "battle_line"
            else:
                for name, ranks in _CADRE_RANKS.items():
                    if name != "battle_line" and roles & {_canonicalize_rank_name(rank) for rank in ranks}:
                        cadre = name
                        break
                if not cadre and "bladeguard" not in roles:
                    cadre = "battle_line" if roles & {"watch sergeant", "veteran sergeant", "watch lieutenant", "watch captain"} else None
        if not cadre:
            continue
        if not (is_equerry or is_master or is_leader or roles & specialist_ranks
                or roles & {"watch sergeant", "veteran sergeant", "watch lieutenant"}):
            continue
        if is_master:
            tier = "watch_master"
        elif is_leader:
            tier = "captain_leader"
        elif is_equerry or "watch lieutenant" in roles or "honored dreadnought" in roles:
            tier = "lieutenant_equerry"
        elif "veteran sergeant" in roles:
            tier = "veteran_sergeant"
        else:
            tier = "sergeant_specialist"
        is_high_command = is_master or is_leader or is_equerry
        weight = units[tier] + (bonus if is_high_command else 0)
        if cadre == destination:
            weight *= multiplier
            snapshot["turnout_ids"].append(user_id)
        if is_high_command:
            snapshot["high_command_ids"].append(user_id)
        snapshot["voter_weights"][user_id] = weight
    if not snapshot["turnout_ids"]:
        raise ValueError("No eligible destination voters remain after recusal.")
    if high_command_required and not snapshot["high_command_ids"]:
        raise ValueError("No eligible High Command voters remain after recusal.")
    snapshot["electorate_ids"] = list(snapshot["voter_weights"])
    snapshot["electorate_size"] = len(snapshot["electorate_ids"])
    return snapshot


def _turnout_lines(poll: dict, evaluation: dict) -> str:
    required = float(poll.get("quorum_percent") or _quorum_percent()) * 100
    lines = [f"-# Turnout: **{evaluation['turnout_percent']:.2f}%** (required {required:.0f}%)"]
    if poll.get("high_command_quorum_required"):
        lines.append(f"-# High Command turnout: **{evaluation['high_command_turnout_percent']:.2f}%** (required {required:.0f}%)")
    return "\n".join(lines)


def _target_role_line_value(poll: dict) -> str:
    target = str(poll.get("target_role") or "").strip()
    if poll.get("equerry_appointment"):
        return f"{target} - Equerry appointment"
    return target or _UNSPECIFIED_TARGET_ROLE


def _subject_line(poll: dict) -> str:
    subject_user_id = str(poll.get("subject_user_id") or "").strip()
    if not subject_user_id:
        return "-# **Subject Member:** None specified"
    return f"-# **Subject Member:** <@{subject_user_id}>"


def _build_active_poll_embed(poll: dict) -> discord.Embed:
    evaluation = _evaluate_poll(poll)
    votes_cast = evaluation["votes_cast"]
    electorate = max(0, int(poll.get("electorate_size") or 0))

    threshold = float(poll.get("pass_threshold") or _pass_percent())

    embed = discord.Embed(
        title="`ɢᴏᴠᴇʀɴᴀɴᴄᴇ ᴠᴏᴛᴇ`",
        description=(
            f"-# **Vote Subject:** {poll.get('title', 'Untitled Vote')}\n"
            f"{_subject_line(poll)}\n"
            f"-# **Target Role/Rank:** {_target_role_line_value(poll)}\n"
            "-# Vote identities and per-option totals remain anonymous."
        ),
        color=0x3498DB,
    )

    embed.add_field(
        name="`ᴘᴀʀᴛɪᴄɪᴘᴀᴛɪᴏɴ`",
        value=(
            f"{_turnout_lines(poll, evaluation)}\n"
            f"-# Ballots cast: **{votes_cast}**\n"
            f"-# Remaining: **{max(0, electorate - votes_cast)}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="`ᴛʜʀᴇsʜᴏʟᴅ ʀᴜʟᴇs`",
        value=(
            f"-# Pass: **{threshold * 100:.0f}% {'weighted ' if poll.get('policy_version') == 2 else ''}yes** of yes+nay"
        ),
        inline=False,
    )
    exp_ts = int(_parse_iso(poll.get("expires_at")).timestamp())
    if hasattr(embed, "set_footer"):
        embed.set_footer(text=f"Poll ID: {poll.get('poll_id', 'unknown')}")
    embed.add_field(name="`ᴄʟᴏsᴇs`", value=f"-# <t:{exp_ts}:R>", inline=False)
    return embed


def _vote_share_percent(vote_count: int, votes_cast: int) -> float:
    if votes_cast <= 0:
        return 0.0
    return (float(vote_count) / float(votes_cast)) * 100.0


def _vote_share_field_value(vote_count: int, votes_cast: int) -> str:
    return (
        f"-# Ballots: **{vote_count}**\n"
        f"-# Share: **{_vote_share_percent(vote_count, votes_cast):.2f}%**"
    )


def _parse_iso(raw: Optional[str]) -> datetime:
    try:
        dt = datetime.fromisoformat(str(raw))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


def _evaluate_poll(poll: dict) -> dict:
    votes = poll.get("votes") or {}
    yes_count = len(votes.get("yay") or [])
    no_count = len(votes.get("nay") or [])

    votes_cast = yes_count + no_count
    electorate = max(0, int(poll.get("electorate_size") or 0))

    quorum_pct = float(poll.get("quorum_percent") or _quorum_percent())
    pass_threshold = float(poll.get("pass_threshold") or _pass_percent())
    close_margin = float(poll.get("close_margin_percent") or _close_margin_percent())

    quorum_required = math.ceil(electorate * quorum_pct)
    quorum_met = votes_cast >= quorum_required

    yes_no_total = yes_count + no_count
    yes_rate = (yes_count / yes_no_total) if yes_no_total > 0 else 0.0
    turnout_percent = (votes_cast / electorate * 100) if electorate else 0.0
    high_command_turnout_percent = 0.0
    high_command_quorum_met = True
    yes_weight = yes_count
    no_weight = no_count
    if poll.get("policy_version") == 2:
        weights = poll["voter_weights"]
        eligible = set(poll["electorate_ids"])
        yes_ids = set(map(str, votes.get("yay") or [])) & eligible
        no_ids = set(map(str, votes.get("nay") or [])) & eligible
        if yes_ids & no_ids:
            raise ValueError("A voter cannot appear on both sides of a poll.")
        participants = yes_ids | no_ids
        yes_count, no_count = len(yes_ids), len(no_ids)
        votes_cast = yes_no_total = len(participants)
        yes_weight = sum(weights[user_id] for user_id in yes_ids)
        no_weight = sum(weights[user_id] for user_id in no_ids)
        yes_rate = yes_weight / (yes_weight + no_weight) if yes_weight + no_weight else 0.0
        turnout_ids = set(poll["turnout_ids"])
        electorate = len(turnout_ids)
        quorum_required = math.ceil(electorate * quorum_pct)
        turnout_count = len(participants & turnout_ids)
        quorum_met = electorate > 0 and turnout_count >= quorum_required
        turnout_percent = turnout_count / electorate * 100 if electorate else 0.0
        high_command_ids = set(poll["high_command_ids"])
        hc_count = len(participants & high_command_ids)
        high_command_turnout_percent = hc_count / len(high_command_ids) * 100 if high_command_ids else 0.0
        if poll.get("high_command_quorum_required"):
            high_command_quorum_met = bool(high_command_ids) and hc_count >= math.ceil(len(high_command_ids) * quorum_pct)
    close_margin_hit = yes_no_total > 0 and abs(yes_rate - pass_threshold) <= close_margin
    if poll.get("policy_version") == 2 and yes_no_total > 0:
        close_margin_hit = close_margin_hit or math.isclose(abs(yes_rate - pass_threshold), close_margin, abs_tol=1e-12)

    revote_reasons: list[str] = []
    if not quorum_met:
        revote_reasons.append(f"Quorum not met (turnout {turnout_percent:.2f}%; required {quorum_pct * 100:.0f}%).")
    if not high_command_quorum_met:
        revote_reasons.append(f"High Command quorum not met (turnout {high_command_turnout_percent:.2f}%; required {quorum_pct * 100:.0f}%).")
    if close_margin_hit:
        revote_reasons.append(
            f"Result within close margin of pass threshold ({yes_rate * 100:.2f}% vs {pass_threshold * 100:.0f}%)."
        )

    revote_required = len(revote_reasons) > 0
    passed = (not revote_required) and yes_no_total > 0 and yes_rate >= pass_threshold

    if revote_required:
        outcome = "revote_required"
    elif passed:
        outcome = "passed"
    else:
        outcome = "failed"

    return {
        "yes_count": yes_count,
        "yes_weight": yes_weight,
        "no_weight": no_weight,
        "turnout_percent": turnout_percent,
        "high_command_turnout_percent": high_command_turnout_percent,
        "high_command_quorum_met": high_command_quorum_met,
        "no_count": no_count,
        "yes_no_total": yes_no_total,
        "votes_cast": votes_cast,
        "electorate": electorate,
        "quorum_required": quorum_required,
        "quorum_met": quorum_met and high_command_quorum_met,
        "destination_quorum_met": quorum_met,
        "yes_rate": yes_rate,
        "pass_threshold": pass_threshold,
        "close_margin": close_margin,
        "close_margin_hit": close_margin_hit,
        "outcome": outcome,
        "revote_required": revote_required,
        "revote_reasons": revote_reasons,
    }


def _build_final_embed(poll: dict, evaluation: dict) -> discord.Embed:
    outcome = evaluation.get("outcome")
    if outcome == "passed":
        color = 0x2ECC71
        outcome_line = "PASSED"
    elif outcome == "failed":
        color = 0xE74C3C
        outcome_line = "FAILED"
    else:
        color = 0xF1C40F
        outcome_line = "REVOTE REQUIRED"

    yes_count = int(evaluation.get("yes_count") or 0)
    no_count = int(evaluation.get("no_count") or 0)
    yes_rate = float(evaluation.get("yes_rate") or 0.0)
    threshold = float(evaluation.get("pass_threshold") or _pass_percent())
    votes_cast = int(evaluation.get("votes_cast") or 0)
    electorate = int(poll.get("electorate_size") or 0)

    embed = discord.Embed(
        title="`ɢᴏᴠᴇʀɴᴀɴᴄᴇ ᴠᴏᴛᴇ · ᴄʟᴏsᴇᴅ`",
        description=(
            f"-# **Vote Subject:** {poll.get('title', 'Untitled Vote')}\n"
            f"{_subject_line(poll)}\n"
            f"-# **Target Role/Rank:** {_target_role_line_value(poll)}\n"
            f"-# **Outcome:** **{outcome_line}**"
        ),
        color=color,
    )
    embed.add_field(name="`ᴍᴇᴛʀɪᴄs`", value=(
        f"-# Electorate: **{electorate}**\n"
        f"-# Ballots cast: **{votes_cast}**\n"
        f"{_turnout_lines(poll, evaluation)}\n"
        f"-# {'Weighted ' if poll.get('policy_version') == 2 else ''}Yes Rate (yes+nay): **{yes_rate * 100:.2f}%** (needed {threshold * 100:.0f}%)"
    ), inline=False)

    total_weight = evaluation["yes_weight"] + evaluation["no_weight"]
    for name, count, weight in (("`ʏᴀʏ`", yes_count, evaluation["yes_weight"]), ("`ɴᴀʏ`", no_count, evaluation["no_weight"])):
        label = "Weighted share" if poll.get("policy_version") == 2 else "Share"
        value = f"-# Ballots: **{count}**\n-# {label}: **{_vote_share_percent(weight, total_weight):.2f}%**"
        embed.add_field(name=name, value=value, inline=True)

    reasons = evaluation.get("revote_reasons") or []
    if reasons:
        embed.add_field(
            name="`ʀᴇᴠᴏᴛᴇ ʀᴇᴀsᴏɴs`",
            value="\n".join(f"-# {r}" for r in reasons),
            inline=False,
        )

    if hasattr(embed, "set_footer"):
        embed.set_footer(text=f"Poll ID: {poll.get('poll_id', 'unknown')}")
    return embed


class GovernanceVoteButton(discord.ui.Button):
    def __init__(self, poll_id: str, option: str):
        label_map = {
            "yay": "Yay",
            "nay": "Nay",
        }
        style_map = {
            "yay": discord.ButtonStyle.success,
            "nay": discord.ButtonStyle.danger,
        }
        emoji_map = {
            "yay": "✅",
            "nay": "❌",
        }
        super().__init__(
            label=label_map[option],
            style=style_map[option],
            emoji=emoji_map[option],
            custom_id=f"govpoll_vote:{poll_id}:{option}",
        )
        self.poll_id = poll_id
        self.option = option

    async def callback(self, interaction: discord.Interaction):
        await _handle_vote(interaction, self.poll_id, self.option)


class GovernanceDeletePollButton(discord.ui.Button):
    def __init__(self, poll_id: str):
        super().__init__(
            label="Delete Poll",
            style=discord.ButtonStyle.danger,
            emoji="🗑️",
            custom_id=f"govpoll_delete:{poll_id}",
        )
        self.poll_id = poll_id

    async def callback(self, interaction: discord.Interaction):
        await _handle_delete_poll(interaction, self.poll_id)


class GovernancePollView(discord.ui.View):
    def __init__(self, poll_id: str):
        super().__init__(timeout=None)
        self.poll_id = poll_id
        self.add_item(GovernanceVoteButton(poll_id, "yay"))
        self.add_item(GovernanceVoteButton(poll_id, "nay"))
        self.add_item(GovernanceDeletePollButton(poll_id))


async def _refresh_active_poll_message(guild: discord.Guild, poll: dict) -> None:
    channel = guild.get_channel(int(poll.get("channel_id") or 0))
    if channel is None or not hasattr(channel, "fetch_message"):
        return
    try:
        msg = await channel.fetch_message(int(poll.get("message_id") or 0))
    except Exception:
        return

    view = GovernancePollView(str(poll.get("poll_id") or ""))
    try:
        await msg.edit(embed=_build_active_poll_embed(poll), view=view)
    except Exception:
        return


async def _close_poll(guild: discord.Guild, poll: dict) -> None:
    poll_id = str(poll.get("poll_id") or "")
    if not poll_id:
        return

    now = datetime.now(timezone.utc)
    eval_data = _evaluate_poll(poll)
    poll["status"] = "closed"
    poll["closed_at"] = now.isoformat()
    poll["evaluation"] = eval_data

    channel = guild.get_channel(int(poll.get("channel_id") or 0))
    if channel is None or not hasattr(channel, "fetch_message"):
        return

    try:
        msg = await channel.fetch_message(int(poll.get("message_id") or 0))
    except Exception:
        msg = None

    final_embed = _build_final_embed(poll, eval_data)

    if msg is not None:
        try:
            await msg.edit(embed=final_embed, view=None)
        except Exception:
            pass

    outcome = eval_data.get("outcome", "unknown").replace("_", " ").upper()
    result_lines = [
        f"Governance vote closed: **{poll.get('title', 'Untitled Vote')}**",
        f"Outcome: **{outcome}**",
    ]
    reasons = eval_data.get("revote_reasons") or []
    if reasons:
        result_lines.append("Revote-required conditions:")
        result_lines.extend(f"- {r}" for r in reasons)

    try:
        await channel.send("\n".join(result_lines), embed=final_embed)
    except Exception:
        pass


async def _close_expired_polls() -> None:
    guild = _b("_resolve_notification_guild")()
    if guild is None:
        return

    now = datetime.now(timezone.utc)
    to_close: list[str] = []
    async with _POLL_LOCK:
        state = _load_polls_state()
        polls = state.get("polls") or {}
        for poll_id, poll in polls.items():
            if str(poll.get("status") or "open") != "open":
                continue
            expires_at = _parse_iso(poll.get("expires_at"))
            if now >= expires_at:
                to_close.append(poll_id)

        for poll_id in to_close:
            poll = polls.get(poll_id)
            if not isinstance(poll, dict):
                continue
            await _close_poll(guild, poll)
            polls[poll_id] = poll

        if to_close:
            state["polls"] = polls
            _save_polls_state(state)


@tasks.loop(minutes=2)
async def _governance_poll_expiry_loop():
    try:
        await _close_expired_polls()
    except Exception as exc:
        if _g.logger:
            _g.logger.warning(f"poll_ops: expiry loop failed: {exc}")


async def register_persistent_views() -> None:
    """Register persistent governance poll views for open polls."""
    try:
        state = _load_polls_state()
        registered = 0
        for poll in (state.get("polls") or {}).values():
            if str(poll.get("status") or "open") != "open":
                continue
            poll_id = str(poll.get("poll_id") or "")
            if not poll_id:
                continue
            view = GovernancePollView(poll_id)
            msg_id = int(poll.get("message_id") or 0)
            if msg_id:
                _g.bot.add_view(view, message_id=msg_id)
            else:
                _g.bot.add_view(view)
            registered += 1
        if _g.logger:
            _g.logger.info(f"poll_ops: registered {registered} open governance poll view(s)")
    except Exception as exc:
        if _g.logger:
            _g.logger.warning(f"poll_ops: register_persistent_views failed: {exc}")


async def _handle_vote(interaction: discord.Interaction, poll_id: str, option: str) -> None:
    if option not in {"yay", "nay"}:
        await interaction.response.send_message("Invalid vote option.", ephemeral=True)
        return

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("This vote is only available in a server.", ephemeral=True)
        return

    user_id = str(getattr(interaction.user, "id", ""))

    async with _POLL_LOCK:
        state = _load_polls_state()
        poll = (state.get("polls") or {}).get(poll_id)
        if not isinstance(poll, dict):
            await interaction.response.send_message("This poll no longer exists.", ephemeral=True)
            return

        if str(poll.get("status") or "open") != "open":
            await interaction.response.send_message("This poll is already closed.", ephemeral=True)
            return

        if datetime.now(timezone.utc) >= _parse_iso(poll.get("expires_at")):
            await interaction.response.send_message("This poll has expired and is closing.", ephemeral=True)
            await _close_poll(guild, poll)
            state["polls"][poll_id] = poll
            _save_polls_state(state)
            return

        electorate = set(str(uid) for uid in (poll.get("electorate_ids") or []))
        if user_id not in electorate:
            if user_id == str(poll.get("subject_user_id") or ""):
                await interaction.response.send_message(
                    "You are the subject of this poll and are recused from voting.",
                    ephemeral=True,
                )
                return
            await interaction.response.send_message("You are not eligible to vote in this poll.", ephemeral=True)
            return

        votes = poll.setdefault("votes", {"yay": [], "nay": []})
        for key in ("yay", "nay"):
            votes.setdefault(key, [])
            votes[key] = [str(uid) for uid in votes[key] if str(uid) != user_id]

        votes[option].append(user_id)
        poll["votes"] = votes

        state["polls"][poll_id] = poll
        _save_polls_state(state)

    await _refresh_active_poll_message(guild, poll)
    await interaction.response.send_message(f"Vote recorded as **{option.upper()}**.", ephemeral=True)


async def _handle_delete_poll(interaction: discord.Interaction, poll_id: str) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("This action is only available in a server.", ephemeral=True)
        return

    user_id = str(getattr(interaction.user, "id", ""))
    channel_id = 0
    message_id = 0
    poll_title = "Untitled Vote"

    async with _POLL_LOCK:
        state = _load_polls_state()
        polls = state.get("polls") or {}
        poll = polls.get(poll_id)
        if not isinstance(poll, dict):
            await interaction.response.send_message("This poll no longer exists.", ephemeral=True)
            return

        if str(poll.get("status") or "open") != "open":
            await interaction.response.send_message("Only open polls can be deleted.", ephemeral=True)
            return

        if user_id != str(poll.get("created_by") or ""):
            await interaction.response.send_message("Only the poll creator can delete this poll.", ephemeral=True)
            return

        channel_id = int(poll.get("channel_id") or 0)
        message_id = int(poll.get("message_id") or 0)
        poll_title = str(poll.get("title") or "Untitled Vote")

        polls.pop(poll_id, None)
        state["polls"] = polls
        _save_polls_state(state)

    removed_message = False
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel is not None and hasattr(channel, "fetch_message") and message_id:
        try:
            msg = await channel.fetch_message(message_id)
            await msg.delete()
            removed_message = True
        except Exception:
            removed_message = False

    if removed_message:
        await interaction.response.send_message(
            f"Deleted poll **{poll_title}** (`{poll_id}`).",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        f"Deleted poll **{poll_title}** (`{poll_id}`), but I could not remove the poll message.",
        ephemeral=True,
    )


@_g.bot.tree.command(
    name="generate_poll",
    description="Create a governance poll for Watch Command voting.",
)
@app_commands.describe(
    title="Poll title/subject line (e.g., promotion for Brother X to Rank Y)",
    target_role="Required destination role being voted on",
    subject_member="Required member the vote concerns (recused from voting)",
    equerry_appointment="Confirm this vote appoints the subject as destination specialist Equerry",
)
async def generate_poll(
    interaction: discord.Interaction,
    title: str,
    target_role: discord.Role,
    subject_member: discord.Member,
    equerry_appointment: bool = False,
):
    configured_equerry = _configured_equerry_cadre(interaction.user)
    if not _b("check_command_permission")(interaction.user, "generate_poll") and not (
        configured_equerry and not _b("DEBUG_MODE") and not _is_reserves_or_interred(interaction.user)
    ):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    clean_title = (title or "").strip()
    if not clean_title:
        await interaction.response.send_message("Title is required.", ephemeral=True)
        return

    clean_target = ""
    if target_role is None:
        await interaction.response.send_message("A destination role is required.", ephemeral=True)
        return
    if target_role is not None:
        clean_target = str(getattr(target_role, "name", "") or "").strip()
        if not clean_target:
            await interaction.response.send_message("Target role must be a server role.", ephemeral=True)
            return
        if not _is_allowed_target_role_name(clean_target):
            await interaction.response.send_message(
                "Target role is not allowed for governance polls.",
                ephemeral=True,
            )
            return

    if subject_member is None:
        await interaction.response.send_message("A subject member is required.", ephemeral=True)
        return
    if equerry_appointment and (_canonicalize_rank_name(clean_target) not in
                               {_canonicalize_rank_name(rank) for rank in _EQUERRY_TARGETS}):
        await interaction.response.send_message("Equerry appointments require a subject member and a destination specialist role.", ephemeral=True)
        return
    recuse_id = int(subject_member.id)
    try:
        snapshot = _weighted_electorate_snapshot(guild, recuse_id, clean_target, equerry_appointment)
    except (ValueError, TypeError) as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=max(1, _poll_duration_hours()))

    async with _POLL_LOCK:
        state = _load_polls_state()
        poll_id = _next_poll_id(state)
        poll = {
            "poll_id": poll_id,
            "title": clean_title,
            "target_role": clean_target or _UNSPECIFIED_TARGET_ROLE,
            "quorum_percent": _quorum_percent(),
            "pass_threshold": _pass_percent(),
            "close_margin_percent": _close_margin_percent(),
            **snapshot,
            "subject_user_id": str(recuse_id),
            "votes": {"yay": [], "nay": []},
            "status": "open",
            "created_by": str(getattr(interaction.user, "id", "")),
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "channel_id": _poll_channel_id(),
            "message_id": None,
        }
        state.setdefault("polls", {})[poll_id] = poll
        _save_polls_state(state)

    poll_channel = guild.get_channel(_poll_channel_id())
    if poll_channel is None:
        try:
            poll_channel = await _g.bot.fetch_channel(_poll_channel_id())
        except Exception:
            poll_channel = None

    if poll_channel is None:
        await interaction.response.send_message(
            f"Unable to resolve governance poll channel <#{_poll_channel_id()}>.",
            ephemeral=True,
        )
        return

    embed = _build_active_poll_embed(poll)
    view = GovernancePollView(poll_id)
    watch_command_role = discord.utils.get(getattr(guild, "roles", []) or [], name="Watch Command")
    mention = watch_command_role.mention if watch_command_role is not None else "@Watch Command"

    try:
        msg = await poll_channel.send(
            content=f"{mention} Governance vote opened.",
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions(users=False, roles=True, everyone=False),
        )
    except Exception as exc:
        await interaction.response.send_message(f"Failed to create poll: {exc}", ephemeral=True)
        return

    async with _POLL_LOCK:
        state = _load_polls_state()
        stored = (state.get("polls") or {}).get(poll_id)
        if isinstance(stored, dict):
            stored["message_id"] = int(getattr(msg, "id", 0) or 0)
            state["polls"][poll_id] = stored
            _save_polls_state(state)

    _g.bot.add_view(view, message_id=int(getattr(msg, "id", 0) or 0))

    await interaction.response.send_message(
        f"Poll created in <#{_poll_channel_id()}> (ID: `{poll_id}`).",
        ephemeral=True,
    )
