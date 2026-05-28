"""Terminus Kill Log subsystem.

Tracks kill log entries for the Terminus Slayer challenge:
  - /submit_kill_log  — submit a Terminus kill (video required)
  - /verifier_standing — ephemeral leaderboard of rolling 7-day verifier activity
  - Persistent Verify/Deny buttons on each kill log embed
  - Apothecary notification + Force Approve/Remove buttons on denial
  - 72-hour reminder for stale pending entries
  - Verifier tier system: rolling 7-day verify+deny count → +1/+2/+3 AAR bonus
"""

import json
import os
import re
import shutil
import sys as _sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands

from .constants import (  # noqa: F401
    AAR_CHANNEL_ID,
    APOTHECARY_STAFF_CHANNEL_ID,
    BLACK_LAURELS_ROLE_ID,
    BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID,
    BLACK_REEF_REQUIRED_MISSIONS,
    BLACK_LAURELS_REQUIRED_MISSIONS,
    CHALLENGE_PROGRESS_PATH,
    CRUX_TERMINATUS_ROLE_ID,
    DISTINGUISHED_BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID,
    DISTINGUISHED_PIPEHITTER_ROLE_ID,
    DUAL_VIGIL_ROLE_ID,
    KADAKU_CAMPAIGN_MEDAL_ROLE_ID,
    KADAKU_CAMPAIGN_REQUIRED_MISSIONS,
    KILL_LOG_CHANNEL_ID,
    KILL_LOG_CLASS_ROLES,
    KILL_LOG_REMINDER_HOURS,
    MASTER_TERMINUS_SLAYER_ROLE_ID,
    ORDER_OMEGA_REQUIRED_MISSIONS,
    PIPEHITTER_ROLE_ID,
    THE_ORDER_OMEGA_ROLE_ID,
    TERMINUS_SLAYER_CLASS_AWARD_TYPES,
    TERMINUS_SLAYER_PATH,
    TERMINUS_SLAYER_ROLE_IDS,
    TERMINUS_TYPES,
    TERMINUS_VERIFIER_RANKS,
    VERIFIER_TIER_THRESHOLDS,
)
from .permissions import BATTLE_LINE_RANKS, CHAMPION_RANKS, SPECIALIST_RANKS, HIGH_COMMAND_RANKS, WATCH_COMMAND_ROLES
from . import _bot_globals as _g

# Any role that counts as a server member (Watch Brother or higher on any track)
_MEMBER_RANKS = BATTLE_LINE_RANKS | CHAMPION_RANKS | SPECIALIST_RANKS | HIGH_COMMAND_RANKS


def _b(name):
    """Resolve name via bot module (test-mock compatibility)."""
    m = _sys.modules.get("opscribe.bot") or _sys.modules.get("bot")
    return getattr(m, name) if (m is not None and hasattr(m, name)) else globals().get(name)


_AAR_LINK_RE = re.compile(r"^https://discord\.com/channels/\d+/(\d+)/(\d+)$")


async def _validate_aar_link(
    aar_link: str, guild: discord.Guild, brother_id: str
) -> Optional[str]:
    """Return an error string if aar_link fails any kill log pre-condition."""
    m = _AAR_LINK_RE.match(aar_link.strip())
    if not m:
        return "The AAR link must be a Discord message URL (`https://discord.com/channels/…`)."
    channel_id_str, message_id_str = m.groups()
    if int(channel_id_str) != AAR_CHANNEL_ID:
        return f"The AAR link must point to a message in <#{AAR_CHANNEL_ID}>."
    aar_ch = guild.get_channel(AAR_CHANNEL_ID)
    if aar_ch is None:
        return "AAR channel not accessible. Contact a Forgemaster."
    try:
        msg = await aar_ch.fetch_message(int(message_id_str))
    except discord.NotFound:
        return "That AAR message was not found. Double-check the link."
    except discord.Forbidden:
        return "Bot lacks permission to read the AAR channel."

    # Check difficulty and participation. Use the ingested DATASTORE record when
    # available; fall back to raw message content for un-ingested AARs.
    record = _g.DATASTORE.get_record(message_id_str) if _g.DATASTORE else None
    if record:
        if brother_id not in [str(b) for b in record.get("brother_ids", [])]:
            return "You must have participated in the linked AAR to submit a kill log for it."
    else:
        content = msg.content or ""
        if not re.search(rf"<@!?{re.escape(brother_id)}>", content):
            return "You must have participated in the linked AAR to submit a kill log for it."
    return None


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

_EMPTY_STATE: dict = {
    "entries": {},
    "progress": {},
    "verifier_actions": {},
    "next_id": 1,
}


def _load_state() -> dict:
    if not os.path.exists(TERMINUS_SLAYER_PATH):
        return {k: (v.copy() if isinstance(v, dict) else v) for k, v in _EMPTY_STATE.items()}
    with open(TERMINUS_SLAYER_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Ensure all keys exist for forward compatibility
    for k, v in _EMPTY_STATE.items():
        if k not in data:
            data[k] = v.copy() if isinstance(v, dict) else v
    return data


def _save_state(state: dict) -> None:
    bak = TERMINUS_SLAYER_PATH + ".bak"
    if os.path.exists(TERMINUS_SLAYER_PATH):
        shutil.copy2(TERMINUS_SLAYER_PATH, bak)
    tmp = TERMINUS_SLAYER_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, TERMINUS_SLAYER_PATH)


def _next_kill_log_id(state: dict) -> str:
    n = state.get("next_id", 1)
    state["next_id"] = n + 1
    return f"KL-{n:04d}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------

def _get_verified_count(state: dict, brother_id: str, class_role_id: int, terminus_type: str) -> int:
    prog = state.get("progress", {})
    return prog.get(str(brother_id), {}).get(str(class_role_id), {}).get(terminus_type, 0)


def _increment_progress(state: dict, brother_id: str, class_role_id: int, terminus_type: str) -> int:
    """Increment verified kill count and return new total."""
    prog = state.setdefault("progress", {})
    bid = str(brother_id)
    cid = str(class_role_id)
    prog.setdefault(bid, {}).setdefault(cid, {k: 0 for k in TERMINUS_TYPES})
    current = prog[bid][cid].get(terminus_type, 0)
    new_val = min(current + 1, 3)
    prog[bid][cid][terminus_type] = new_val
    return new_val


def _class_is_complete(state: dict, brother_id: str, class_role_id: int) -> bool:
    """Return True if all 3 terminus types have 3 verified kills for this class."""
    prog = state.get("progress", {})
    class_data = prog.get(str(brother_id), {}).get(str(class_role_id), {})
    return all(class_data.get(t, 0) >= 3 for t in TERMINUS_TYPES)


# ---------------------------------------------------------------------------
# Verifier tier helpers
# ---------------------------------------------------------------------------

def _record_verifier_action(state: dict, vet_id: str, action: str, kill_log_id: str) -> None:
    """Record a verify or deny action for tier calculation."""
    va = state.setdefault("verifier_actions", {})
    vid = str(vet_id)
    va.setdefault(vid, []).append({
        "action": action,
        "kill_log_id": kill_log_id,
        "timestamp": _now_iso(),
    })
    # Prune entries older than 8 days to keep file size bounded
    cutoff = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    va[vid] = [a for a in va[vid] if a["timestamp"] >= cutoff]


def get_verifier_tier(vet_id: str) -> int:
    """Return 0-3 verifier tier for a vet based on rolling 7-day action count."""
    try:
        state = _load_state()
        actions = state.get("verifier_actions", {}).get(str(vet_id), [])
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        recent = sum(1 for a in actions if _parse_dt(a["timestamp"]) >= cutoff)
        for threshold, tier, _bonus in VERIFIER_TIER_THRESHOLDS:
            if recent >= threshold:
                return tier
        return 0
    except Exception:
        return 0


def get_verifier_tier_bonus(vet_id: str) -> int:
    """Return AAR point bonus (0-3) for a vet based on their verifier tier."""
    tier = get_verifier_tier(vet_id)
    for _threshold, t, bonus in VERIFIER_TIER_THRESHOLDS:
        if tier == t:
            return bonus
    return 0


def _get_rolling_action_count(state: dict, vet_id: str) -> int:
    actions = state.get("verifier_actions", {}).get(str(vet_id), [])
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    return sum(1 for a in actions if _parse_dt(a["timestamp"]) >= cutoff)


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------

def _status_line(entry: dict) -> str:
    status = entry.get("status", "pending")
    verifications = entry.get("verifications", [])
    if status == "pending":
        return f"⏳ Awaiting Verification ({len(verifications)}/3)"
    if status == "verified":
        return "✅ Kill Confirmed (3/3 verified)"
    if status == "force_approved":
        return "✅ Kill Confirmed (Apothecary Approved)"
    if status == "under_review":
        return "⚠️ Under Review — Awaiting Apothecary Decision"
    if status == "rejected":
        return "❌ Rejected by Apothecary"
    return status


def _format_verifications_field(entry: dict) -> str:
    """Return a human-readable verifier list with timestamps for the kill log embed.

    Uses the per-verifier ``verification_log`` if present; falls back to a
    bare list of vet_ids from ``verifications`` for legacy entries without
    per-verifier timestamps.
    """
    log = entry.get("verification_log") or []
    if log:
        lines = []
        for rec in log:
            vid = rec.get("vet_id")
            at = rec.get("at") or ""
            if not vid:
                continue
            try:
                ts = int(_parse_dt(at).timestamp()) if at else None
            except Exception:
                ts = None
            if ts is not None:
                lines.append(f"<@{vid}> — <t:{ts}:f>")
            else:
                lines.append(f"<@{vid}>")
        return "\n".join(lines)
    # Legacy fallback
    verifications = entry.get("verifications") or []
    if not verifications:
        return ""
    return "\n".join(f"<@{v}>" for v in verifications)


def _build_kill_log_embed(entry: dict, guild: Optional[discord.Guild] = None) -> discord.Embed:
    brother_id = entry["brother_id"]
    class_name = entry["class_name"]
    terminus = entry["terminus_type"]
    kill_number = entry["kill_number"]
    aar_link = entry["aar_link"]
    video_url = entry.get("video_url") or entry.get("video_attachment_url") or ""
    verified_count = entry.get("verified_prior_count", 0)

    colour = {
        "pending": discord.Colour.from_rgb(80, 80, 80),
        "verified": discord.Colour.green(),
        "force_approved": discord.Colour.green(),
        "under_review": discord.Colour.orange(),
        "rejected": discord.Colour.red(),
    }.get(entry.get("status", "pending"), discord.Colour.from_rgb(80, 80, 80))

    embed = discord.Embed(
        title="⚔️ Terminus Kill Report",
        colour=colour,
    )
    embed.add_field(name="Brother", value=f"<@{brother_id}>", inline=True)
    embed.add_field(name="Class", value=class_name, inline=True)
    embed.add_field(name="Terminus", value=terminus, inline=True)
    embed.add_field(name="Kill Attempt", value=f"{kill_number}/3", inline=True)
    embed.add_field(
        name="Prior Verified",
        value=f"{verified_count}/3 confirmed before this submission",
        inline=True,
    )
    embed.add_field(name="AAR", value=f"[View Report]({aar_link})", inline=True)
    if video_url:
        embed.add_field(name="Recording", value=f"[Watch]({video_url})", inline=True)
    embed.add_field(
        name="Status",
        value=_status_line(entry),
        inline=False,
    )
    verifications_field = _format_verifications_field(entry)
    if verifications_field:
        embed.add_field(name="Verifications", value=verifications_field, inline=False)
    embed.set_footer(text=f"Kill Log ID: {entry['kill_log_id']}")
    embed.timestamp = _parse_dt(entry["submitted_at"])
    return embed


def _build_apo_notification_embed(entry: dict) -> discord.Embed:
    embed = discord.Embed(
        title="⚠️ Terminus Kill Log — Under Review",
        description=(
            "A kill log entry has been **denied** by a Watch Veteran. "
            "Apothecary action required."
        ),
        colour=discord.Colour.orange(),
    )
    embed.add_field(name="Kill Log", value=entry["kill_log_id"], inline=True)
    embed.add_field(name="Brother", value=f"<@{entry['brother_id']}>", inline=True)
    embed.add_field(
        name="Class / Terminus",
        value=f"{entry['class_name']} / {entry['terminus_type']}",
        inline=True,
    )
    denied_by = entry.get("denied_by")
    denied_at = entry.get("denied_at", "")
    if denied_by:
        embed.add_field(name="Denied By", value=f"<@{denied_by}>", inline=True)
    if denied_at:
        ts = int(_parse_dt(denied_at).timestamp())
        embed.add_field(name="Denied At", value=f"<t:{ts}:f>", inline=True)
    video_url = entry.get("video_url") or entry.get("video_attachment_url") or ""
    if video_url:
        embed.add_field(name="Recording", value=f"[Watch]({video_url})", inline=False)
    embed.set_footer(text="Use the buttons below to make a final ruling.")
    return embed


def _build_completion_embed(brother_id: str, class_name: str) -> discord.Embed:
    embed = discord.Embed(
        title="🏆 Terminus Slayer — Class Cleared",
        description=(
            f"<@{brother_id}> has confirmed all 3 kills on every current Terminus type "
            f"as **{class_name}**.\n\n"
            "The Terminus Slayer award for this class may now be granted."
        ),
        colour=discord.Colour.gold(),
    )
    embed.add_field(name="Brother", value=f"<@{brother_id}>", inline=True)
    embed.add_field(name="Class", value=class_name, inline=True)
    for t in TERMINUS_TYPES:
        embed.add_field(name=t, value="✅ 3/3", inline=True)
    embed.timestamp = datetime.now(timezone.utc)
    return embed


def _build_reminder_embed(stale_entries: list[dict], guild_id: int) -> discord.Embed:
    embed = discord.Embed(
        title="📋 Kill Log — Pending Verification",
        description=(
            "The following kill log entries have been awaiting verification "
            f"for over {KILL_LOG_REMINDER_HOURS} hours. "
            "Watch Veterans, please review."
        ),
        colour=discord.Colour.from_rgb(180, 140, 60),
    )
    for entry in stale_entries[:10]:  # cap at 10 lines
        ts = int(_parse_dt(entry["submitted_at"]).timestamp())
        embed_msg_id = entry.get("embed_message_id")
        if embed_msg_id:
            link = (
                f"https://discord.com/channels/{guild_id}/"
                f"{KILL_LOG_CHANNEL_ID}/{embed_msg_id}"
            )
            entry_label = f"[{entry['kill_log_id']}]({link})"
        else:
            entry_label = entry["kill_log_id"]
        embed.add_field(
            name=f"{entry['kill_log_id']} — {entry['class_name']} / {entry['terminus_type']}",
            value=(
                f"{entry_label} · <@{entry['brother_id']}> · "
                f"Submitted <t:{ts}:R> · "
                f"{len(entry.get('verifications', []))}/3 verified"
            ),
            inline=False,
        )
    if len(stale_entries) > 10:
        embed.set_footer(text=f"…and {len(stale_entries) - 10} more. Check the kill log channel.")
    return embed


# ---------------------------------------------------------------------------
# Persistent View: kill log entry (Verify / Deny)
# ---------------------------------------------------------------------------

class TerminusKillLogView(discord.ui.View):
    """Persistent verify/deny buttons for a single kill log entry."""

    def __init__(self, kill_log_id: str):
        super().__init__(timeout=None)
        self.kill_log_id = kill_log_id
        self._verify_btn.custom_id = f"terminus_verify:{kill_log_id}"
        self._deny_btn.custom_id = f"terminus_deny:{kill_log_id}"

    @discord.ui.button(
        label="Verify",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="terminus_verify:__placeholder__",
    )
    async def _verify_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_verify(interaction, self.kill_log_id)

    @discord.ui.button(
        label="Deny",
        style=discord.ButtonStyle.danger,
        emoji="❌",
        custom_id="terminus_deny:__placeholder__",
    )
    async def _deny_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_deny(interaction, self.kill_log_id)


# ---------------------------------------------------------------------------
# Persistent View: apothecary ruling (Force Approve / Remove Entry)
# ---------------------------------------------------------------------------

class TerminusApoView(discord.ui.View):
    """Persistent force-approve / remove buttons on the apo notification."""

    def __init__(self, kill_log_id: str):
        super().__init__(timeout=None)
        self.kill_log_id = kill_log_id
        self._approve_btn.custom_id = f"terminus_force_approve:{kill_log_id}"
        self._remove_btn.custom_id = f"terminus_remove:{kill_log_id}"

    @discord.ui.button(
        label="Force Approve",
        style=discord.ButtonStyle.success,
        emoji="⚔️",
        custom_id="terminus_force_approve:__placeholder__",
    )
    async def _approve_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_force_approve(interaction, self.kill_log_id)

    @discord.ui.button(
        label="Remove Entry",
        style=discord.ButtonStyle.danger,
        emoji="🗑️",
        custom_id="terminus_remove:__placeholder__",
    )
    async def _remove_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_remove_entry(interaction, self.kill_log_id)


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------

def _is_verifier(member: discord.Member) -> bool:
    return any(r.name in TERMINUS_VERIFIER_RANKS for r in member.roles)


def _is_apothecary(member: discord.Member) -> bool:
    apothecary_ranks = {"Watch Apothecary", "Chief Apothecary"}
    return any(r.name in apothecary_ranks for r in member.roles)


# ---------------------------------------------------------------------------
# Button handlers
# ---------------------------------------------------------------------------

async def _handle_verify(interaction: discord.Interaction, kill_log_id: str) -> None:
    if not _is_verifier(interaction.user):
        await interaction.response.send_message(
            "Only Watch Veterans and above may verify kill log entries.",
            ephemeral=True,
        )
        return

    # Collect outcome inside the lock; send Discord responses outside.
    error_msg: Optional[str] = None
    entry: Optional[dict] = None
    newly_confirmed = False
    class_complete = False
    brother_id: Optional[str] = None

    async with _g.TERMINUS_SLAYER_LOCK:
        state = _load_state()
        entry = state["entries"].get(kill_log_id)
        if entry is None:
            error_msg = "Kill log entry not found."
        elif entry["status"] != "pending":
            error_msg = f"This entry is no longer pending (status: {entry['status']})."
        else:
            vet_id = str(interaction.user.id)
            brother_id = str(entry["brother_id"])

            if vet_id == brother_id:
                error_msg = "You cannot verify your own kill log entry."
            else:
                verifications = entry.setdefault("verifications", [])
                if vet_id in verifications:
                    error_msg = "You have already verified this entry."
                else:
                    verifications.append(vet_id)
                    entry.setdefault("verification_log", []).append(
                        {"vet_id": vet_id, "at": _now_iso()}
                    )
                    _record_verifier_action(state, vet_id, "verify", kill_log_id)

                    newly_confirmed = len(verifications) >= 3
                    if newly_confirmed:
                        entry["status"] = "verified"
                        entry["verified_at"] = _now_iso()
                        # Update progress
                        new_count = _increment_progress(
                            state,
                            brother_id,
                            entry["class_role_id"],
                            entry["terminus_type"],
                        )
                        class_complete = new_count >= 3 and _class_is_complete(
                            state, brother_id, entry["class_role_id"]
                        )
                    _save_state(state)

    # All Discord API calls happen outside the lock.
    if error_msg:
        await interaction.response.send_message(error_msg, ephemeral=True)
        return

    # Update embed in-place
    guild = interaction.guild
    embed = _build_kill_log_embed(entry, guild)
    view = TerminusKillLogView(kill_log_id) if entry["status"] == "pending" else None
    await interaction.response.edit_message(embed=embed, view=view)

    # If confirmed, send completion notification if class is done
    if newly_confirmed and class_complete:
        await _notify_class_complete(guild, brother_id, entry["class_role_id"], entry["class_name"])


async def _handle_deny(interaction: discord.Interaction, kill_log_id: str) -> None:
    if not _is_verifier(interaction.user):
        await interaction.response.send_message(
            "Only Watch Veterans and above may deny kill log entries.",
            ephemeral=True,
        )
        return

    # Collect outcome inside the lock; send Discord responses outside.
    error_msg: Optional[str] = None
    entry: Optional[dict] = None

    async with _g.TERMINUS_SLAYER_LOCK:
        state = _load_state()
        entry = state["entries"].get(kill_log_id)
        if entry is None:
            error_msg = "Kill log entry not found."
        elif entry["status"] != "pending":
            error_msg = f"This entry is no longer pending (status: {entry['status']})."
        else:
            vet_id = str(interaction.user.id)
            brother_id = str(entry["brother_id"])

            if vet_id == brother_id:
                error_msg = "You cannot deny your own kill log entry."
            else:
                entry["status"] = "under_review"
                entry["denied_by"] = vet_id
                entry["denied_at"] = _now_iso()
                _record_verifier_action(state, vet_id, "deny", kill_log_id)
                _save_state(state)

    # All Discord API calls happen outside the lock.
    if error_msg:
        await interaction.response.send_message(error_msg, ephemeral=True)
        return

    # Update embed — remove buttons
    guild = interaction.guild
    embed = _build_kill_log_embed(entry, guild)
    await interaction.response.edit_message(embed=embed, view=None)

    # Notify apothecary channel
    await _notify_apo_denial(guild, entry)


async def _handle_force_approve(interaction: discord.Interaction, kill_log_id: str) -> None:
    if not _is_apothecary(interaction.user):
        await interaction.response.send_message(
            "Only Watch Apothecaries may force-approve kill log entries.",
            ephemeral=True,
        )
        return

    # Collect outcome inside the lock; send Discord responses outside.
    error_msg: Optional[str] = None
    entry: Optional[dict] = None
    class_complete = False
    brother_id: Optional[str] = None

    async with _g.TERMINUS_SLAYER_LOCK:
        state = _load_state()
        entry = state["entries"].get(kill_log_id)
        if entry is None:
            error_msg = "Kill log entry not found."
        elif entry["status"] != "under_review":
            error_msg = f"Entry is not under review (status: {entry['status']})."
        else:
            entry["status"] = "force_approved"
            entry["apo_action"] = "force_approved"
            entry["apo_actor_id"] = str(interaction.user.id)
            entry["apo_acted_at"] = _now_iso()

            brother_id = str(entry["brother_id"])
            new_count = _increment_progress(
                state,
                brother_id,
                entry["class_role_id"],
                entry["terminus_type"],
            )
            class_complete = new_count >= 3 and _class_is_complete(
                state, brother_id, entry["class_role_id"]
            )
            _save_state(state)

    # All Discord API calls happen outside the lock.
    if error_msg:
        await interaction.response.send_message(error_msg, ephemeral=True)
        return

    # Disable buttons on the apo notification
    await interaction.response.edit_message(
        content="✅ Kill log force-approved.",
        embed=_build_apo_notification_embed(entry),
        view=None,
    )

    # Update the original kill log embed if we can find it
    await _refresh_kill_log_embed(interaction.guild, entry)

    if class_complete:
        await _notify_class_complete(
            interaction.guild, brother_id, entry["class_role_id"], entry["class_name"]
        )


async def _handle_remove_entry(interaction: discord.Interaction, kill_log_id: str) -> None:
    if not _is_apothecary(interaction.user):
        await interaction.response.send_message(
            "Only Watch Apothecaries may remove kill log entries.",
            ephemeral=True,
        )
        return

    # Collect outcome inside the lock; send Discord responses outside.
    error_msg: Optional[str] = None
    entry: Optional[dict] = None

    async with _g.TERMINUS_SLAYER_LOCK:
        state = _load_state()
        entry = state["entries"].get(kill_log_id)
        if entry is None:
            error_msg = "Kill log entry not found."
        elif entry["status"] != "under_review":
            error_msg = f"Entry is not under review (status: {entry['status']})."
        else:
            entry["status"] = "rejected"
            entry["apo_action"] = "rejected"
            entry["apo_actor_id"] = str(interaction.user.id)
            entry["apo_acted_at"] = _now_iso()
            _save_state(state)

    # All Discord API calls happen outside the lock.
    if error_msg:
        await interaction.response.send_message(error_msg, ephemeral=True)
        return

    await interaction.response.edit_message(
        content="❌ Kill log entry removed from record.",
        embed=_build_apo_notification_embed(entry),
        view=None,
    )

    await _refresh_kill_log_embed(interaction.guild, entry)


# ---------------------------------------------------------------------------
# Notification helpers
# ---------------------------------------------------------------------------

async def _notify_apo_denial(guild: Optional[discord.Guild], entry: dict) -> None:
    if guild is None:
        return
    channel = guild.get_channel(APOTHECARY_STAFF_CHANNEL_ID)
    if channel is None:
        return
    view = TerminusApoView(entry["kill_log_id"])
    try:
        msg = await channel.send(embed=_build_apo_notification_embed(entry), view=view)
        async with _g.TERMINUS_SLAYER_LOCK:
            state = _load_state()
            if entry["kill_log_id"] in state["entries"]:
                state["entries"][entry["kill_log_id"]]["apo_notification_message_id"] = str(msg.id)
                _save_state(state)
    except Exception as exc:
        if _g.logger:
            _g.logger.warning(f"terminus_ops: failed to send apo denial notification: {exc}")


async def _notify_class_complete(
    guild: Optional[discord.Guild],
    brother_id: str,
    class_role_id: int,
    class_name: str,
) -> None:
    """Assign the class Terminus Slayer role, enqueue a public award announcement,
    and check whether Master Terminus Slayer has now been earned."""
    if guild is None:
        return

    member = guild.get_member(int(brother_id))
    if member is None:
        if _g.logger:
            _g.logger.warning(f"terminus_ops: member {brother_id} not found in guild for class completion")
        return

    # Assign the class role
    class_role = guild.get_role(class_role_id)
    if class_role is not None and class_role not in member.roles:
        try:
            await member.add_roles(class_role, reason=f"Auto-award: Terminus Slayer ({class_name})")
        except Exception as exc:
            if _g.logger:
                _g.logger.warning(f"terminus_ops: failed to assign {class_name} role to {brother_id}: {exc}")

    # Determine member chapter
    home_chapters = _b("HOME_CHAPTERS") or []
    member_chapter = "Unknown"
    for r in getattr(member, "roles", []):
        if getattr(r, "name", "") in home_chapters:
            member_chapter = r.name
            break

    # Enqueue public announcement for this class award
    award_type = TERMINUS_SLAYER_CLASS_AWARD_TYPES.get(class_role_id)
    if award_type:
        try:
            ann_channel = await _b("_get_award_announcement_channel")(member, guild)
            if ann_channel:
                _b("_enqueue_award_announcement")(
                    str(member.id), award_type, member_chapter, str(ann_channel.id), str(guild.id)
                )
            else:
                if _g.logger:
                    _g.logger.warning(
                        f"terminus_ops: no announcement channel for {brother_id} {award_type}; role assigned"
                    )
        except Exception as exc:
            if _g.logger:
                _g.logger.warning(f"terminus_ops: failed to enqueue announcement for {award_type}: {exc}")

    # Check if all 7 class roles are now held → award Master Terminus Slayer
    class_role_ids = set(TERMINUS_SLAYER_CLASS_AWARD_TYPES.keys())
    member_role_ids = {r.id for r in getattr(member, "roles", [])}
    # Re-include the just-assigned role in case Discord hasn't reflected it yet
    if class_role is not None:
        member_role_ids.add(class_role_id)
    all_classes_done = class_role_ids <= member_role_ids

    if all_classes_done:
        master_role = guild.get_role(MASTER_TERMINUS_SLAYER_ROLE_ID)
        if master_role is not None and master_role not in member.roles:
            try:
                await member.add_roles(master_role, reason="Auto-award: Master Terminus Slayer")
            except Exception as exc:
                if _g.logger:
                    _g.logger.warning(f"terminus_ops: failed to assign Master Terminus Slayer to {brother_id}: {exc}")
            try:
                ann_channel = await _b("_get_award_announcement_channel")(member, guild)
                if ann_channel:
                    _b("_enqueue_award_announcement")(
                        str(member.id), "master_terminus_slayer", member_chapter, str(ann_channel.id), str(guild.id)
                    )
            except Exception as exc:
                if _g.logger:
                    _g.logger.warning(f"terminus_ops: failed to enqueue master announcement for {brother_id}: {exc}")


async def _refresh_kill_log_embed(guild: Optional[discord.Guild], entry: dict) -> None:
    """Edit the original kill log channel message to reflect the new status."""
    if guild is None:
        return
    msg_id = entry.get("embed_message_id")
    if not msg_id:
        return
    channel = guild.get_channel(KILL_LOG_CHANNEL_ID)
    if channel is None:
        return
    try:
        msg = await channel.fetch_message(int(msg_id))
        await msg.edit(embed=_build_kill_log_embed(entry, guild), view=None)
    except Exception as exc:
        if _g.logger:
            _g.logger.debug(f"terminus_ops: could not refresh kill log embed: {exc}")


# ---------------------------------------------------------------------------
# Startup: re-register persistent views for all non-final entries
# ---------------------------------------------------------------------------

async def register_persistent_views() -> None:
    """Call from on_ready to restore persistent buttons after restart."""
    try:
        state = _load_state()
        pending_count = 0
        review_count = 0
        for entry in state.get("entries", {}).values():
            status = entry.get("status", "pending")
            kill_log_id = entry["kill_log_id"]
            if status == "pending":
                _g.bot.add_view(TerminusKillLogView(kill_log_id))
                pending_count += 1
            elif status == "under_review":
                apo_msg_id = entry.get("apo_notification_message_id")
                if apo_msg_id:
                    _g.bot.add_view(TerminusApoView(kill_log_id))
                    review_count += 1
        if _g.logger:
            _g.logger.info(
                f"terminus_ops: registered {pending_count} pending + "
                f"{review_count} under-review persistent views"
            )
    except Exception as exc:
        if _g.logger:
            _g.logger.warning(f"terminus_ops: register_persistent_views failed: {exc}")


# ---------------------------------------------------------------------------
# 72-hour reminder task (called from bot.py scheduled task)
# ---------------------------------------------------------------------------

async def check_stale_kill_logs() -> None:
    """Post a reminder for kill log entries pending > KILL_LOG_REMINDER_HOURS hours."""
    try:
        guild = _b("_resolve_notification_guild")()
        if guild is None:
            return

        cutoff = datetime.now(timezone.utc) - timedelta(hours=KILL_LOG_REMINDER_HOURS)
        async with _g.TERMINUS_SLAYER_LOCK:
            state = _load_state()
            stale = [
                e for e in state["entries"].values()
                if e.get("status") == "pending"
                and not e.get("reminder_sent")
                and _parse_dt(e["submitted_at"]) < cutoff
            ]
            if not stale:
                return
            stale_ids = [e["kill_log_id"] for e in stale]

        channel = guild.get_channel(KILL_LOG_CHANNEL_ID)
        if channel is None:
            return

        # Mention @Watch Veteran role if resolvable; fall back to plain text
        vet_role = discord.utils.find(
            lambda r: r.name == "Watch Veteran", guild.roles
        )
        mention = vet_role.mention if vet_role else "Watch Veterans"
        await channel.send(
            content=f"{mention} — kill log entries require verification:",
            embed=_build_reminder_embed(stale, guild.id),
        )

        # Mark as reminder_sent only after the message was successfully sent.
        async with _g.TERMINUS_SLAYER_LOCK:
            state = _load_state()
            for eid in stale_ids:
                if eid in state["entries"]:
                    state["entries"][eid]["reminder_sent"] = True
            _save_state(state)
    except Exception as exc:
        if _g.logger:
            _g.logger.warning(f"terminus_ops: check_stale_kill_logs failed: {exc}")


# ---------------------------------------------------------------------------
# /submit_kill_log  command
# ---------------------------------------------------------------------------

@_g.bot.tree.command(
    name="submit_kill_log",
    description="Submit a Terminus kill log entry for the Terminus Slayer challenge.",
)
@app_commands.describe(
    slayer_class="Your class for this kill (must match the challenge class).",
    terminus="Which Terminus enemy was killed.",
    aar_link="Link to the AAR for the mission where this kill occurred.",
    video_url="URL to your kill recording (YouTube, Medal, Streamable, etc.).",
    video="Direct video upload (attach a file). Use video_url for large recordings.",
)
@app_commands.rename(slayer_class="class")
@app_commands.choices(
    terminus=[
        app_commands.Choice(name=t, value=t) for t in TERMINUS_TYPES
    ]
)
async def submit_kill_log(
    interaction: discord.Interaction,
    slayer_class: discord.Role,
    terminus: app_commands.Choice[str],
    aar_link: str,
    video_url: Optional[str] = None,
    video: Optional[discord.Attachment] = None,
):
    if not _b("is_allowed_channel")(interaction):
        await interaction.response.send_message(
            f"This command can only be used in <#{KILL_LOG_CHANNEL_ID}>.",
            ephemeral=True,
        )
        return

    # Member check — must hold at least Watch Brother (or any equivalent rank)
    member = interaction.user
    if not any(r.name in _MEMBER_RANKS for r in getattr(member, "roles", [])):
        await interaction.response.send_message(
            "Only Watch Brothers and above may submit kill logs.",
            ephemeral=True,
        )
        return

    # Validate class role
    if slayer_class.id not in KILL_LOG_CLASS_ROLES:
        await interaction.response.send_message(
            "Invalid class role. Please select one of the 7 Terminus Slayer class roles.",
            ephemeral=True,
        )
        return

    # Require at least one video source
    if not video_url and not video:
        await interaction.response.send_message(
            "A recording is required. Attach a video file or provide a `video_url`.",
            ephemeral=True,
        )
        return

    guild = interaction.guild
    channel = guild.get_channel(KILL_LOG_CHANNEL_ID) if guild else None
    if channel is None:
        await interaction.response.send_message(
            "Kill log channel not found. Contact a Forgemaster.",
            ephemeral=True,
        )
        return

    # Defer now — AAR validation and video download can exceed the 3-second response window
    await interaction.response.defer(ephemeral=True)

    # Validate AAR link — must be a real Absolute AAR the submitter participated in
    aar_error = await _validate_aar_link(aar_link, guild, str(interaction.user.id))
    if aar_error:
        await interaction.followup.send(aar_error, ephemeral=True)
        return

    class_name = KILL_LOG_CLASS_ROLES[slayer_class.id]
    brother_id = str(interaction.user.id)

    async with _g.TERMINUS_SLAYER_LOCK:
        state = _load_state()
        kill_log_id = _next_kill_log_id(state)

        verified_prior = _get_verified_count(
            state, brother_id, slayer_class.id, terminus.value
        )
        kill_number = min(verified_prior + 1, 3)

        entry: dict = {
            "kill_log_id": kill_log_id,
            "brother_id": brother_id,
            "class_role_id": slayer_class.id,
            "class_name": class_name,
            "terminus_type": terminus.value,
            "kill_number": kill_number,
            "aar_link": aar_link,
            "video_url": video_url or "",
            "video_attachment_url": video.url if video else "",
            "embed_message_id": "",
            "channel_id": KILL_LOG_CHANNEL_ID,
            "submitted_at": _now_iso(),
            "status": "pending",
            "verifications": [],
            "denied_by": None,
            "denied_at": None,
            "apo_action": None,
            "apo_actor_id": None,
            "apo_acted_at": None,
            "apo_notification_message_id": None,
            "reminder_sent": False,
            "verified_at": None,
            "verified_prior_count": verified_prior,
        }
        state["entries"][kill_log_id] = entry
        _save_state(state)

    # Post embed to kill log channel
    view = TerminusKillLogView(kill_log_id)
    _g.bot.add_view(view)

    embed = _build_kill_log_embed(entry, guild)
    vet_role = discord.utils.find(lambda r: r.name == "Watch Veteran", guild.roles) if guild else None
    vet_mention = vet_role.mention if vet_role else "Watch Veterans"

    # Re-upload attached video as a file so Discord renders it inline rather than as a download link
    video_file = None
    if video is not None:
        try:
            video_file = await video.to_file()
        except Exception as exc:
            # Roll back the saved entry before aborting
            async with _g.TERMINUS_SLAYER_LOCK:
                state = _load_state()
                state["entries"].pop(kill_log_id, None)
                _save_state(state)
            await interaction.followup.send(
                f"❌ Could not process your video attachment: `{exc}`\n"
                "Use `video_url` with a YouTube, Medal, or Streamable link for recordings over 8 MB.",
                ephemeral=True,
            )
            return

    video_too_large = False
    try:
        msg = await channel.send(
            content=f"{vet_mention} — new kill log submitted for verification:",
            embed=embed,
            view=view,
            file=video_file,
        )
    except discord.HTTPException as exc:
        if video_file is not None:
            # File exceeds server upload limit — retry without it and warn the user
            video_file = None
            video_too_large = True
            msg = await channel.send(
                content=f"{vet_mention} — new kill log submitted for verification:",
                embed=embed,
                view=view,
            )
        else:
            # Unrelated channel send failure — roll back and abort
            async with _g.TERMINUS_SLAYER_LOCK:
                state = _load_state()
                state["entries"].pop(kill_log_id, None)
                _save_state(state)
            await interaction.followup.send(
                f"❌ Failed to post to the kill log channel: `{exc}`",
                ephemeral=True,
            )
            return

    # Store the message ID and (if re-uploaded) the channel attachment URL
    async with _g.TERMINUS_SLAYER_LOCK:
        state = _load_state()
        if kill_log_id in state["entries"]:
            state["entries"][kill_log_id]["embed_message_id"] = str(msg.id)
            if video_file and msg.attachments:
                state["entries"][kill_log_id]["video_attachment_url"] = msg.attachments[0].url
            _save_state(state)

    await interaction.followup.send(
        f"✅ Kill log **{kill_log_id}** submitted. Watch Veterans will verify it shortly."
        + (
            "\n\n⚠️ Your video attachment was too large to upload directly — it was not included. "
            "Re-submit using `video_url` with a YouTube, Medal, or Streamable link."
            if video_too_large else ""
        ),
        ephemeral=True,
    )


# ---------------------------------------------------------------------------
# /verifier_standing  command
# ---------------------------------------------------------------------------

@_g.bot.tree.command(
    name="verifier_standing",
    description="[Vet+] View the rolling 7-day verifier activity leaderboard.",
)
async def verifier_standing(interaction: discord.Interaction):
    if not _b("is_allowed_channel")(interaction):
        await interaction.response.send_message(
            f"This command can only be used in <#{KILL_LOG_CHANNEL_ID}>.",
            ephemeral=True,
        )
        return

    if not _is_verifier(interaction.user):
        await interaction.response.send_message(
            "This command is restricted to Watch Veterans and above.",
            ephemeral=True,
        )
        return

    state = _load_state()
    va = state.get("verifier_actions", {})
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    rows = []
    for vet_id, actions in va.items():
        count = sum(1 for a in actions if _parse_dt(a["timestamp"]) >= cutoff)
        if count > 0:
            tier = 0
            for threshold, t, _bonus in VERIFIER_TIER_THRESHOLDS:
                if count >= threshold:
                    tier = t
                    break
            rows.append((vet_id, count, tier))

    rows.sort(key=lambda x: x[1], reverse=True)

    embed = discord.Embed(
        title="⚔️ Verifier Standing — Rolling 7 Days",
        colour=discord.Colour.from_rgb(120, 80, 160),
    )

    if not rows:
        embed.description = "No verification activity in the past 7 days."
    else:
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, (vet_id, count, tier) in enumerate(rows):
            medal = medals[i] if i < 3 else "  "
            tier_str = f"[Tier {tier}  +{tier}]" if tier > 0 else "[—]"
            bar = "█" * min(count, 15)
            lines.append(f"{medal} <@{vet_id}> `{bar}` {count} actions  {tier_str}")
        embed.description = "\n".join(lines)

    embed.set_footer(text="Verify or deny kill logs to build your standing. Resets on a rolling 7-day window.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# /challenge-progress command
# ---------------------------------------------------------------------------

@_g.bot.tree.command(
    name="challenge-progress",
    description="View your challenge progress — mission awards and Terminus Slayer kills.",
)
@app_commands.describe(
    member="[Watch Command+] View another member's challenge progress.",
)
async def challenge_progress(
    interaction: discord.Interaction,
    member: Optional[discord.Member] = None,
):
    # Only Watch Command+ may query other members
    if member is not None:
        if not any(r.name in WATCH_COMMAND_ROLES for r in interaction.user.roles):
            await interaction.response.send_message(
                "Only Watch Command and above may view another member's challenge progress.",
                ephemeral=True,
            )
            return

    target = member or interaction.user
    user_id_str = str(target.id)

    # Load challenge progress
    challenge_all: dict = {}
    if os.path.exists(CHALLENGE_PROGRESS_PATH):
        with open(CHALLENGE_PROGRESS_PATH, "r", encoding="utf-8") as f:
            challenge_all = json.load(f)
    user_progress = challenge_all.get(user_id_str, {})

    # Load Terminus Slayer state
    ts_state = _load_state()
    ts_progress = ts_state.get("progress", {}).get(user_id_str, {})

    # --- Helpers ---
    def _unique_mission_count(key: str) -> int:
        return len({m["mission"] for m in user_progress.get(key, [])})

    # Collect target's role IDs for completed-role detection.
    target_role_ids: set[int] = {r.id for r in getattr(target, "roles", [])}

    def _bar(current: int, total: int, role_id: Optional[int] = None) -> str:
        # If the member already holds the award role, treat as fully complete.
        if role_id is not None and role_id in target_role_ids:
            current = total
        filled = min(current, total)
        empty = max(total - filled, 0)
        check = "✅" if current >= total else "🔲"
        blocks = "█" * filled + "░" * empty
        return f"{check} `{blocks}` {current}/{total}"

    # --- Section 1: Mission Challenges ---
    # Tuples: (label, current, total, role_id)
    challenge_rows = [
        (
            "Kadaku Campaign Medal",
            _unique_mission_count("kadaku_campaign"),
            len(KADAKU_CAMPAIGN_REQUIRED_MISSIONS),
            KADAKU_CAMPAIGN_MEDAL_ROLE_ID,
        ),
        (
            "Black Reef Campaign Medal",
            _unique_mission_count("black_reef"),
            len(BLACK_REEF_REQUIRED_MISSIONS),
            BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID,
        ),
        (
            "Distinguished Black Reef Campaign Medal",
            _unique_mission_count("distinguished_black_reef"),
            len(BLACK_REEF_REQUIRED_MISSIONS),
            DISTINGUISHED_BLACK_REEF_CAMPAIGN_MEDAL_ROLE_ID,
        ),
        (
            "Black Laurels",
            _unique_mission_count("black_laurels"),
            len(BLACK_LAURELS_REQUIRED_MISSIONS),
            BLACK_LAURELS_ROLE_ID,
        ),
        (
            "Dual Vigil",
            _unique_mission_count("dual_vigil"),
            len(BLACK_LAURELS_REQUIRED_MISSIONS),
            DUAL_VIGIL_ROLE_ID,
        ),
        (
            "Distinguished SOK-G: Pipehitter",
            _unique_mission_count("sok_g_pipehitter"),
            2,
            DISTINGUISHED_PIPEHITTER_ROLE_ID,
        ),
        (
            "SOK-G: Pipehitter",
            _unique_mission_count("sok_g_pipehitter"),
            1,
            PIPEHITTER_ROLE_ID,
        ),
        (
            "Order Omega",
            _unique_mission_count("order_omega"),
            len(ORDER_OMEGA_REQUIRED_MISSIONS),
            THE_ORDER_OMEGA_ROLE_ID,
        ),
    ]

    challenge_lines = []
    for label, current, total, role_id in challenge_rows:
        challenge_lines.append(f"**{label}**\n{_bar(current, total, role_id)}")

    # --- Crux Terminatus eligibility checklist ---
    # Requirement 1: All Black Laurels missions completed with Rank A.
    has_bl_role = BLACK_LAURELS_ROLE_ID in target_role_ids
    bl_aars = user_progress.get("crux_bl_aars", [])
    bl_missions_logged = {m["mission"] for m in bl_aars}
    all_bl_rank_a = (
        has_bl_role
        and BLACK_LAURELS_REQUIRED_MISSIONS <= bl_missions_logged
        and all((m.get("rank") or "A").upper() == "A" for m in bl_aars)
    )
    # If they already hold the Crux role, treat everything as complete.
    has_crux = CRUX_TERMINATUS_ROLE_ID in target_role_ids
    if has_crux:
        all_bl_rank_a = True

    # Requirement 2: Distinguished SOK-G Pipehitter role.
    has_distinguished = DISTINGUISHED_PIPEHITTER_ROLE_ID in target_role_ids
    if has_crux:
        has_distinguished = True

    # Requirement 3: 2+ Terminus Slayer class completions.
    ts_class_count = sum(1 for rid in KILL_LOG_CLASS_ROLES if rid in target_role_ids)
    ts_slays_met = ts_class_count >= 2
    if has_crux:
        ts_slays_met = True

    bl_check = "✅" if all_bl_rank_a else "🔲"
    dist_check = "✅" if has_distinguished else "🔲"
    ts_check = "✅" if ts_slays_met else "🔲"
    challenge_lines.append(
        f"**Crux Terminatus**\n"
        f"{bl_check} Black Laurels — all missions, Rank A\n"
        f"{dist_check} Distinguished SOK-G: Pipehitter\n"
        f"{ts_check} Terminus Slayer classes completed: {ts_class_count}/2"
    )

    def _add_chunked_fields(embed: discord.Embed, name: str, items: list[str], sep: str = "\n\n") -> None:
        """Add items joined by sep across as many fields as needed, each ≤ 1024 chars."""
        current = ""
        first = True
        for item in items:
            prefix = "" if current == "" else sep
            candidate = current + prefix + item
            if len(candidate) > 1024:
                embed.add_field(name=name if first else "\u200b", value=current, inline=False)
                first = False
                current = item
            else:
                current = candidate
        if current:
            embed.add_field(name=name if first else "\u200b", value=current, inline=False)

    embed = discord.Embed(
        title=f"Challenge Progress — {target.display_name}",
        colour=discord.Colour.from_rgb(80, 140, 200),
    )
    _add_chunked_fields(embed, "⚔️ Mission Awards", challenge_lines)

    # --- Section 2: Terminus Slayer Kill Grid ---
    ts_lines = []
    for class_role_id, class_name in KILL_LOG_CLASS_ROLES.items():
        class_prog = ts_progress.get(str(class_role_id), {})
        has_class_role = class_role_id in target_role_ids
        type_parts = []
        for t_type in TERMINUS_TYPES:
            count = class_prog.get(t_type, 0)
            # If the member already holds the class completion role, treat all types as done.
            if has_class_role:
                count = 3
            check = "✅" if count >= 3 else "🔲"
            type_parts.append(f"{check} {t_type}: {count}/3")
        ts_lines.append(f"**{class_name}**\n" + "  |  ".join(type_parts))

    _add_chunked_fields(embed, "💀 Terminus Slayer Kills", ts_lines, sep="\n")

    embed.set_footer(text="Progress updates automatically as AARs and kill logs are processed.")
    await interaction.response.send_message(embed=embed, ephemeral=True)
