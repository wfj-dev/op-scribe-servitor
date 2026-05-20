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
    KILL_LOG_CHANNEL_ID,
    KILL_LOG_CLASS_ROLES,
    KILL_LOG_REMINDER_HOURS,
    TERMINUS_SLAYER_PATH,
    TERMINUS_TYPES,
    TERMINUS_VERIFIER_RANKS,
    VERIFIER_TIER_THRESHOLDS,
)
from . import _bot_globals as _g


def _b(name):
    """Resolve name via bot module (test-mock compatibility)."""
    m = _sys.modules.get("opscribe.bot") or _sys.modules.get("bot")
    return getattr(m, name) if (m is not None and hasattr(m, name)) else globals().get(name)


_AAR_LINK_RE = re.compile(r"^https://discord\.com/channels/\d+/(\d+)/(\d+)$")


async def _validate_aar_link(aar_link: str, guild: discord.Guild) -> Optional[str]:
    """Return an error string if aar_link is not a real Absolute/Omega AAR."""
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

    # Terminus kills must be from an Absolute or Omega difficulty operation.
    # Check the ingested DATASTORE record first; fall back to raw message content
    # for AARs that haven't been processed yet.
    record = _g.DATASTORE.get_record(message_id_str) if _g.DATASTORE else None
    if record:
        diff_class = record.get("difficulty_class") or ""
        if diff_class != "absolute_ops":
            return "The linked AAR must be an Absolute difficulty operation."
    else:
        content = (msg.content or "").lower()
        if not re.search(r"\babsolute\b", content):
            return "The linked AAR must be an Absolute difficulty operation."
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


def _build_reminder_embed(stale_entries: list[dict]) -> discord.Embed:
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
        embed.add_field(
            name=f"{entry['kill_log_id']} — {entry['class_name']} / {entry['terminus_type']}",
            value=(
                f"<@{entry['brother_id']}> · Submitted <t:{ts}:R> · "
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
    if guild is None:
        return
    channel = guild.get_channel(APOTHECARY_STAFF_CHANNEL_ID)
    if channel is None:
        return
    try:
        await channel.send(embed=_build_completion_embed(brother_id, class_name))
    except Exception as exc:
        if _g.logger:
            _g.logger.warning(f"terminus_ops: failed to send completion notification: {exc}")


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
            embed=_build_reminder_embed(stale),
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

    # Validate AAR link — must be a real message in the AAR channel
    aar_error = await _validate_aar_link(aar_link, guild)
    if aar_error:
        await interaction.response.send_message(aar_error, ephemeral=True)
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
    msg = await channel.send(embed=embed, view=view)

    # Store the message ID so we can edit it later
    async with _g.TERMINUS_SLAYER_LOCK:
        state = _load_state()
        if kill_log_id in state["entries"]:
            state["entries"][kill_log_id]["embed_message_id"] = str(msg.id)
            _save_state(state)

    await interaction.response.send_message(
        f"✅ Kill log **{kill_log_id}** submitted. Watch Veterans will verify it shortly.",
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
