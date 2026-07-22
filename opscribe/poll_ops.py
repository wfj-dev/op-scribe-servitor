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
from .permissions import HIGH_COMMAND_RANKS


_POLL_LOCK = asyncio.Lock()


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


def _normal_pass_percent() -> float:
    cfg = _poll_cfg()
    try:
        return float(cfg.get("normal_pass_percent", 0.66) or 0.66)
    except Exception:
        return 0.66


def _high_command_pass_percent() -> float:
    cfg = _poll_cfg()
    try:
        return float(cfg.get("high_command_pass_percent", 0.75) or 0.75)
    except Exception:
        return 0.75


def _abstain_revote_percent() -> float:
    cfg = _poll_cfg()
    try:
        return float(cfg.get("abstain_revote_percent", 0.35) or 0.35)
    except Exception:
        return 0.35


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


def _revote_reminder_days() -> int:
    cfg = _poll_cfg()
    try:
        return int(cfg.get("revote_reminder_days", 7) or 7)
    except Exception:
        return 7


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


def _target_is_high_command(target_role_or_rank: str) -> bool:
    target = (target_role_or_rank or "").strip().lower()
    if not target:
        return False
    high = {(r or "").strip().lower() for r in HIGH_COMMAND_RANKS}
    return target in high


def _classification_label(classification: str) -> str:
    if str(classification or "").strip().lower() == "high_command":
        return "High Command threshold"
    return "Standard Watch Command threshold"


def _subject_line(poll: dict) -> str:
    subject_user_id = str(poll.get("subject_user_id") or "").strip()
    if not subject_user_id:
        return "-# **Subject Member:** None specified"
    return f"-# **Subject Member:** <@{subject_user_id}>"


def _build_active_poll_embed(poll: dict) -> discord.Embed:
    yes_votes = list(poll.get("votes", {}).get("yay", []))
    no_votes = list(poll.get("votes", {}).get("nay", []))
    abstain_votes = list(poll.get("votes", {}).get("abstain", []))
    votes_cast = len(yes_votes) + len(no_votes) + len(abstain_votes)
    electorate = max(0, int(poll.get("electorate_size") or 0))

    threshold = float(poll.get("pass_threshold") or _normal_pass_percent())
    quorum_pct = float(poll.get("quorum_percent") or _quorum_percent())
    quorum_required = math.ceil(electorate * quorum_pct)
    classification = str(poll.get("classification") or "normal")
    includes_abstain = bool(poll.get("include_abstain"))
    class_label = _classification_label(classification)

    embed = discord.Embed(
        title="`ɢᴏᴠᴇʀɴᴀɴᴄᴇ ᴠᴏᴛᴇ`",
        description=(
            f"-# **Vote Subject:** {poll.get('title', 'Untitled Vote')}\n"
            f"{_subject_line(poll)}\n"
            f"-# **Target Role/Rank:** {poll.get('target_role', 'Unknown')}\n"
            f"-# **Threshold Rule:** {class_label}\n"
            "-# Vote identities and per-option totals are anonymous until close."
        ),
        color=0x3498DB,
    )

    embed.add_field(
        name="`ᴘᴀʀᴛɪᴄɪᴘᴀᴛɪᴏɴ`",
        value=(
            f"-# Ballots cast: **{votes_cast}/{electorate}**\n"
            f"-# Remaining: **{max(0, electorate - votes_cast)}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="`ᴛʜʀᴇsʜᴏʟᴅ ʀᴜʟᴇs`",
        value=(
            f"-# Quorum: **{quorum_required}/{electorate}** ({quorum_pct * 100:.0f}%)\n"
            f"-# Pass: **{threshold * 100:.0f}% yes** of yes+nay\n"
            f"-# Abstain check: **{_abstain_revote_percent() * 100:.0f}%** triggers revote"
        ),
        inline=False,
    )
    exp_ts = int(_parse_iso(poll.get("expires_at")).timestamp())
    if hasattr(embed, "set_footer"):
        embed.set_footer(text=f"Poll ID: {poll.get('poll_id', 'unknown')}")
    embed.add_field(name="`ᴄʟᴏsᴇs`", value=f"-# <t:{exp_ts}:R>", inline=False)
    return embed


def _mentions_from_ids(ids: list[str]) -> str:
    if not ids:
        return "-# None"
    return "\n".join(f"-# <@{uid}>" for uid in ids)


def _parse_iso(raw: Optional[str]) -> datetime:
    try:
        dt = datetime.fromisoformat(str(raw))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


def _abstain_revote_triggered(evaluation: dict) -> bool:
    reasons = evaluation.get("revote_reasons") or []
    return any("Abstain threshold" in str(reason) for reason in reasons)


def _evaluate_poll(poll: dict) -> dict:
    votes = poll.get("votes") or {}
    yes_count = len(votes.get("yay") or [])
    no_count = len(votes.get("nay") or [])
    abstain_count = len(votes.get("abstain") or [])

    votes_cast = yes_count + no_count + abstain_count
    electorate = max(0, int(poll.get("electorate_size") or 0))

    quorum_pct = float(poll.get("quorum_percent") or _quorum_percent())
    pass_threshold = float(poll.get("pass_threshold") or _normal_pass_percent())
    abstain_threshold = float(poll.get("abstain_revote_percent") or _abstain_revote_percent())
    close_margin = float(poll.get("close_margin_percent") or _close_margin_percent())

    quorum_required = math.ceil(electorate * quorum_pct)
    quorum_met = votes_cast >= quorum_required

    yes_no_total = yes_count + no_count
    yes_rate = (yes_count / yes_no_total) if yes_no_total > 0 else 0.0
    abstain_rate = (abstain_count / votes_cast) if votes_cast > 0 else 0.0
    close_margin_hit = yes_no_total > 0 and abs(yes_rate - pass_threshold) <= close_margin

    revote_reasons: list[str] = []
    if not quorum_met:
        revote_reasons.append(
            f"Quorum not met ({votes_cast}/{quorum_required} ballots required)."
        )
    if abstain_rate >= abstain_threshold:
        revote_reasons.append(
            f"Abstain threshold reached ({abstain_rate * 100:.2f}% >= {abstain_threshold * 100:.0f}%)."
        )
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
        "no_count": no_count,
        "abstain_count": abstain_count,
        "yes_no_total": yes_no_total,
        "votes_cast": votes_cast,
        "electorate": electorate,
        "quorum_required": quorum_required,
        "quorum_met": quorum_met,
        "yes_rate": yes_rate,
        "abstain_rate": abstain_rate,
        "pass_threshold": pass_threshold,
        "abstain_threshold": abstain_threshold,
        "close_margin": close_margin,
        "close_margin_hit": close_margin_hit,
        "outcome": outcome,
        "revote_required": revote_required,
        "revote_reasons": revote_reasons,
    }


def _non_voter_ids(poll: dict) -> list[str]:
    electorate = [str(uid) for uid in (poll.get("electorate_ids") or []) if str(uid).strip()]
    votes = poll.get("votes") or {}
    voted = {
        str(uid)
        for key in ("yay", "nay", "abstain")
        for uid in (votes.get(key) or [])
        if str(uid).strip()
    }
    return [uid for uid in electorate if uid not in voted]


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
    abstain_count = int(evaluation.get("abstain_count") or 0)
    yes_rate = float(evaluation.get("yes_rate") or 0.0)
    threshold = float(evaluation.get("pass_threshold") or _normal_pass_percent())
    quorum_required = int(evaluation.get("quorum_required") or 0)
    votes_cast = int(evaluation.get("votes_cast") or 0)
    electorate = int(evaluation.get("electorate") or 0)
    class_label = _classification_label(poll.get("classification") or "normal")

    embed = discord.Embed(
        title="`ɢᴏᴠᴇʀɴᴀɴᴄᴇ ᴠᴏᴛᴇ · ᴄʟᴏsᴇᴅ`",
        description=(
            f"-# **Vote Subject:** {poll.get('title', 'Untitled Vote')}\n"
            f"{_subject_line(poll)}\n"
            f"-# **Target Role/Rank:** {poll.get('target_role', 'Unknown')}\n"
            f"-# **Threshold Rule:** {class_label}\n"
            f"-# **Outcome:** **{outcome_line}**"
        ),
        color=color,
    )
    embed.add_field(name="`ᴍᴇᴛʀɪᴄs`", value=(
        f"-# Electorate: **{electorate}**\n"
        f"-# Ballots cast: **{votes_cast}**\n"
        f"-# Quorum: **{votes_cast}/{quorum_required}**\n"
        f"-# Yes Rate (yes+nay): **{yes_rate * 100:.2f}%** (needed {threshold * 100:.0f}%)"
    ), inline=False)

    votes = poll.get("votes") or {}
    embed.add_field(name="`ʏᴀʏ ᴠᴏᴛᴇʀs`", value=_mentions_from_ids(list(votes.get("yay") or [])), inline=False)
    embed.add_field(name="`ɴᴀʏ ᴠᴏᴛᴇʀs`", value=_mentions_from_ids(list(votes.get("nay") or [])), inline=False)
    if bool(poll.get("include_abstain")):
        embed.add_field(name="`ᴀʙsᴛᴀɪɴ ᴠᴏᴛᴇʀs`", value=_mentions_from_ids(list(votes.get("abstain") or [])), inline=False)

    no_show_ids = _non_voter_ids(poll)
    embed.add_field(name="`ɴᴏ-sʜᴏᴡs`", value=_mentions_from_ids(no_show_ids), inline=False)

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
            "abstain": "Abstain",
        }
        style_map = {
            "yay": discord.ButtonStyle.success,
            "nay": discord.ButtonStyle.danger,
            "abstain": discord.ButtonStyle.secondary,
        }
        emoji_map = {
            "yay": "✅",
            "nay": "❌",
            "abstain": "⚪",
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


class GovernancePollView(discord.ui.View):
    def __init__(self, poll_id: str, include_abstain: bool):
        super().__init__(timeout=None)
        self.poll_id = poll_id
        self.add_item(GovernanceVoteButton(poll_id, "yay"))
        self.add_item(GovernanceVoteButton(poll_id, "nay"))
        if include_abstain:
            self.add_item(GovernanceVoteButton(poll_id, "abstain"))


async def _refresh_active_poll_message(guild: discord.Guild, poll: dict) -> None:
    channel = guild.get_channel(int(poll.get("channel_id") or 0))
    if channel is None or not hasattr(channel, "fetch_message"):
        return
    try:
        msg = await channel.fetch_message(int(poll.get("message_id") or 0))
    except Exception:
        return

    view = GovernancePollView(str(poll.get("poll_id") or ""), bool(poll.get("include_abstain")))
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
    if eval_data.get("outcome") == "revote_required" and _abstain_revote_triggered(eval_data):
        poll["revote_reason"] = "abstain_threshold"
        poll["revote_due_at"] = (now + timedelta(days=max(1, _revote_reminder_days()))).isoformat()
        poll["revote_reminder_sent_at"] = None
    else:
        poll.pop("revote_reason", None)
        poll.pop("revote_due_at", None)
        poll.pop("revote_reminder_sent_at", None)

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
    if eval_data.get("outcome") == "revote_required" and _abstain_revote_triggered(eval_data):
        result_lines.append("Revote required without yay/nay outcome (abstain threshold reached).")

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


async def _send_due_revote_reminders(guild: discord.Guild) -> None:
    now = datetime.now(timezone.utc)

    async with _POLL_LOCK:
        state = _load_polls_state()
        polls = state.get("polls") or {}
        due_polls: list[dict] = []
        for poll_id, poll in polls.items():
            if not isinstance(poll, dict):
                continue
            if str(poll.get("status") or "") != "closed":
                continue
            evaluation = poll.get("evaluation") or {}
            if str(evaluation.get("outcome") or "") != "revote_required":
                continue
            if str(poll.get("revote_reason") or "") != "abstain_threshold":
                continue
            if poll.get("revote_reminder_sent_at"):
                continue
            due_at = _parse_iso(poll.get("revote_due_at"))
            if now < due_at:
                continue
            due_polls.append({
                "poll_id": str(poll_id),
                "title": str(poll.get("title") or "Untitled Vote"),
                "channel_id": int(poll.get("channel_id") or 0),
            })

    if not due_polls:
        return

    for item in due_polls:
        channel = guild.get_channel(item["channel_id"])
        if channel is None:
            try:
                channel = await _g.bot.fetch_channel(item["channel_id"])
            except Exception:
                channel = None
        if channel is None:
            continue

        sent = False
        try:
            await channel.send(
                (
                    f"This poll requires a revote: **{item['title']}** (`{item['poll_id']}`).\n"
                    "Revote required without yay/nay outcome due to abstain threshold."
                )
            )
            sent = True
        except Exception:
            sent = False

        if sent:
            async with _POLL_LOCK:
                state = _load_polls_state()
                polls = state.get("polls") or {}
                stored = polls.get(item["poll_id"])
                if isinstance(stored, dict) and not stored.get("revote_reminder_sent_at"):
                    stored["revote_reminder_sent_at"] = datetime.now(timezone.utc).isoformat()
                    polls[item["poll_id"]] = stored
                    state["polls"] = polls
                    _save_polls_state(state)


@tasks.loop(minutes=2)
async def _governance_poll_expiry_loop():
    try:
        await _close_expired_polls()
        guild = _b("_resolve_notification_guild")()
        if guild is not None:
            await _send_due_revote_reminders(guild)
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
            view = GovernancePollView(poll_id, bool(poll.get("include_abstain")))
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
    if option not in {"yay", "nay", "abstain"}:
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

        votes = poll.setdefault("votes", {"yay": [], "nay": [], "abstain": []})
        for key in ("yay", "nay", "abstain"):
            votes.setdefault(key, [])
            votes[key] = [str(uid) for uid in votes[key] if str(uid) != user_id]

        if option == "abstain" and not bool(poll.get("include_abstain")):
            await interaction.response.send_message("Abstain is not enabled on this poll.", ephemeral=True)
            return

        votes[option].append(user_id)
        poll["votes"] = votes

        state["polls"][poll_id] = poll
        _save_polls_state(state)

    await _refresh_active_poll_message(guild, poll)
    await interaction.response.send_message(f"Vote recorded as **{option.upper()}**.", ephemeral=True)


@_g.bot.tree.command(
    name="generate_poll",
    description="Create a governance poll for Watch Command voting.",
)
@app_commands.describe(
    title="Poll title/subject line (e.g., promotion for Brother X to Rank Y)",
    target_role_or_rank="Role or rank being voted on (used for threshold rules)",
    subject_member="Member the vote concerns (recused if in electorate)",
    include_abstain="Add abstain option to the poll",
)
async def generate_poll(
    interaction: discord.Interaction,
    title: str,
    target_role_or_rank: str,
    subject_member: Optional[discord.Member] = None,
    include_abstain: bool = False,
):
    if not _b("check_command_permission")(interaction.user, "generate_poll"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        return

    clean_title = (title or "").strip()
    clean_target = (target_role_or_rank or "").strip()
    if not clean_title or not clean_target:
        await interaction.response.send_message("Title and target role/rank are required.", ephemeral=True)
        return

    recuse_id = int(subject_member.id) if subject_member is not None else None
    electorate = _eligible_electorate_snapshot(guild, recuse_id)
    electorate_size = len(electorate)
    if electorate_size <= 0:
        await interaction.response.send_message("No eligible Watch Command voters were found.", ephemeral=True)
        return

    classification = "high_command" if _target_is_high_command(clean_target) else "normal"
    pass_threshold = _high_command_pass_percent() if classification == "high_command" else _normal_pass_percent()

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=max(1, _poll_duration_hours()))

    async with _POLL_LOCK:
        state = _load_polls_state()
        poll_id = _next_poll_id(state)
        poll = {
            "poll_id": poll_id,
            "title": clean_title,
            "target_role": clean_target,
            "classification": classification,
            "include_abstain": bool(include_abstain),
            "quorum_percent": _quorum_percent(),
            "pass_threshold": pass_threshold,
            "abstain_revote_percent": _abstain_revote_percent(),
            "close_margin_percent": _close_margin_percent(),
            "electorate_ids": electorate,
            "electorate_size": electorate_size,
            "subject_user_id": str(recuse_id) if recuse_id else None,
            "votes": {"yay": [], "nay": [], "abstain": []},
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
    view = GovernancePollView(poll_id, bool(include_abstain))
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
