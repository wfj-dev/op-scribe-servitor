"""Forge operations: armor integrity, blessing pool, forge pool, rites,
machine spirits, forge rite rendering, LFG, forge chronicle functions."""

import os
import json
import discord
from discord import app_commands
from datetime import datetime, timedelta, timezone
from discord.ext import tasks
import re
from typing import List, Tuple, Optional
import hashlib
import random
import sys as _sys

from .constants import *  # noqa: F401,F403
from .constants import _strip_display_name
from .flavor_text import *  # noqa: F401,F403
from .permissions import *  # noqa: F401,F403
from .studs import *  # noqa: F401,F403
from . import _bot_globals as _g


def _b(name):
    """Resolve name via bot module for test-mock compatibility."""
    m = _sys.modules.get("opscribe.bot") or _sys.modules.get("bot")
    return getattr(m, name) if (m is not None and hasattr(m, name)) else globals().get(name)


def _load_rites() -> dict:
    try:
        if not os.path.exists(RITES_PATH):
            return {}
        with open(RITES_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_rites(data: dict):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(RITES_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


async def _get_user_rite(user_id: int) -> Optional[str]:
    try:
        async with _g.RITES_LOCK:
            data = _load_rites()
            return data.get(str(user_id))
    except Exception:
        return None


async def _set_user_rite(user_id: int, text: str):
    try:
        async with _g.RITES_LOCK:
            data = _load_rites()
            data[str(user_id)] = text
            _save_rites(data)
    except Exception:
        pass


# --- Machine Spirit Persistence for Forge Rite ---


def _load_machine_spirits() -> dict:
    try:
        if not os.path.exists(MACHINE_SPIRITS_PATH):
            return {}
        with open(MACHINE_SPIRITS_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_machine_spirits(data: dict):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(MACHINE_SPIRITS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


async def _get_machine_spirit(user_id: int) -> Optional[str]:
    """Get the stored machine spirit designation for a user's armor.

    Handles both old format (string) and new format (dict with designation/bound_ts).
    Always returns just the designation string for backward compatibility.
    """
    try:
        async with _g.MACHINE_SPIRITS_LOCK:
            data = _load_machine_spirits()
            entry = data.get(str(user_id))
            if entry is None:
                return None
            # Handle both formats
            if isinstance(entry, dict):
                return entry.get("designation")
            return entry  # Old string format
    except Exception:
        return None


async def _set_machine_spirit(user_id: int, spirit: str):
    """Store the machine spirit designation for a user's armor.

    Saves in new format with designation and bound_ts for Forge Chronicle tracking.
    """
    try:
        async with _g.MACHINE_SPIRITS_LOCK:
            data = _load_machine_spirits()
            data[str(user_id)] = {
                "designation": spirit,
                "bound_ts": datetime.utcnow().isoformat(),
            }
            _save_machine_spirits(data)
    except Exception:
        pass


async def _delete_machine_spirit(user_id: int) -> Optional[str]:
    """Delete a machine spirit and return its designation if it existed."""
    try:
        async with _g.MACHINE_SPIRITS_LOCK:
            data = _load_machine_spirits()
            entry = data.pop(str(user_id), None)
            if entry:
                _save_machine_spirits(data)
                if isinstance(entry, dict):
                    return entry.get("designation")
                return entry
            return None
    except Exception:
        return None


def _get_armor_config() -> dict:
    """Get arming chamber config from _g.CONFIG (armor_integrity block)."""
    return _g.CONFIG.get("armor_integrity", {})


def _get_arming_chamber_channel_id() -> Optional[int]:
    """Get the arming chamber channel ID for 'Log to Forge' posts."""
    config = _get_armor_config()
    cid = config.get("arming_chamber_channel_id")
    if cid:
        try:
            return int(cid)
        except (ValueError, TypeError):
            pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# LFG Queue System - Sign-up queues for operations and omega missions
# ─────────────────────────────────────────────────────────────────────────────


def _get_lfg_config() -> dict:
    """Get LFG configuration from config.json, with defaults."""
    return _g.CONFIG.get("lfg") or {}


def _get_lfg_pc_role_id() -> int:
    """Get PC Player role ID from config or default."""
    cfg = _get_lfg_config()
    return int(cfg.get("pc_player_role_id") or LFG_PC_PLAYER_ROLE_ID_DEFAULT)


def _get_lfg_console_role_id() -> int:
    """Get Console Player role ID from config or default."""
    cfg = _get_lfg_config()
    return int(cfg.get("console_player_role_id") or LFG_CONSOLE_PLAYER_ROLE_ID_DEFAULT)


def _get_lfg_default_expiry_minutes() -> int:
    """Get default queue expiry time in minutes from config or default."""
    cfg = _get_lfg_config()
    return int(cfg.get("default_expiry_minutes") or LFG_QUEUE_EXPIRY_MINUTES_DEFAULT)


def _get_lfg_max_expiry_minutes() -> int:
    """Get maximum queue expiry time in minutes from config or default (120)."""
    cfg = _get_lfg_config()
    return int(cfg.get("max_expiry_minutes") or 120)


def _get_lfg_queue_types() -> dict:
    """Get queue type configurations from config or defaults."""
    cfg = _get_lfg_config()
    return cfg.get("queue_types") or LFG_QUEUE_TYPES_DEFAULT


def _get_lfg_initiation_trial_role_id() -> Optional[int]:
    """Get Initiation Trial ping role ID from config, or None if not configured."""
    cfg = _get_lfg_config()
    role_id = cfg.get("initiation_trial_role_id")
    return int(role_id) if role_id else None


def _load_lfg_queues() -> dict:
    """Load LFG queues from disk."""
    try:
        if not os.path.exists(LFG_QUEUE_PATH):
            return {}
        with open(LFG_QUEUE_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_lfg_queues(data: dict):
    """Save LFG queues to disk."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = LFG_QUEUE_PATH + ".tmp"
        bak = LFG_QUEUE_PATH + ".bak"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        if os.path.exists(LFG_QUEUE_PATH):
            try:
                os.replace(LFG_QUEUE_PATH, bak)
            except Exception:
                pass
        os.replace(tmp, LFG_QUEUE_PATH)
    except Exception as e:
        _g.logger.warning(f"Failed to save LFG queues: {e}")


def _get_player_platform(member: discord.Member) -> Optional[str]:
    """Determine if a member is PC or Console player based on roles.

    Returns:
        "pc" if they have PC Player role (or both roles)
        "console" if they have only Console Player role
        None if they have neither role
    """
    role_ids = {r.id for r in member.roles}
    pc_role_id = _get_lfg_pc_role_id()
    console_role_id = _get_lfg_console_role_id()
    has_pc = pc_role_id in role_ids
    has_console = console_role_id in role_ids

    if has_pc:
        return "pc"  # PC takes priority if they have both
    elif has_console:
        return "console"
    return None


def _build_lfg_embed(queue_data: dict, guild: discord.Guild) -> discord.Embed:
    """Build the embed for an LFG queue display."""
    queue_type = queue_data["queue_type"]
    queue_types = _b("_get_lfg_queue_types")()
    type_config = queue_types.get(queue_type, {})
    creator_id = queue_data["creator_id"]
    players = queue_data["players"]  # List of {"user_id": int, "platform": str}
    expires_at = queue_data.get("expires_at")
    initiation_trial = queue_data.get("initiation_trial", False)
    custom_message = queue_data.get("message")

    # Count players and console players
    player_count = len(players)
    max_players = type_config.get("max_players", 3)
    console_count = sum(1 for p in players if p["platform"] == "console")
    max_console = type_config.get("max_console")

    # Determine embed color based on fill status
    if player_count >= max_players:
        color = 0x2ECC71  # Green - full
    elif player_count > 0:
        color = 0xF1C40F  # Yellow - partially filled
    else:
        color = 0x3498DB  # Blue - empty

    # Build title with queue-specific emoji
    queue_display = type_config.get("display", queue_data.get("type", "Unknown"))
    if queue_type == "omega":
        queue_emoji = _get_emoji_by_name(guild, "Omega") or "⚔️"
    else:
        queue_emoji = "⚔️"
    title = f"{queue_emoji} {queue_display} Queue"
    if initiation_trial:
        title += " (Initiation Trial)"
    if player_count >= max_players:
        title += " [FULL]"

    embed = discord.Embed(title=title, color=color)

    # Creator info
    creator = guild.get_member(creator_id)
    creator_name = creator.display_name if creator else f"User {creator_id}"
    embed.set_author(name=f"Created by {creator_name}")

    # Build description with expires time and custom message
    desc_parts = []
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at)
            exp_ts = int(exp_dt.timestamp())
            desc_parts.append(f"⏰ Expires <t:{exp_ts}:R>")
        except Exception:
            pass
    if custom_message:
        desc_parts.append(f"📝 *{custom_message}*")
    if desc_parts:
        embed.description = "\n".join(desc_parts)

    # Player slots
    slot_lines = []
    for i in range(max_players):
        if i < len(players):
            p = players[i]
            member = guild.get_member(p["user_id"])
            name = member.display_name if member else f"User {p['user_id']}"
            platform_emoji = "🖥️" if p["platform"] == "pc" else "🎮"
            slot_lines.append(f"{i + 1}. {platform_emoji} {name}")
        else:
            slot_lines.append(f"{i + 1}. ─ *Empty* ─")

    embed.add_field(
        name=f"Players ({player_count}/{max_players})",
        value="\n".join(slot_lines),
        inline=False,
    )

    # Console limit info for Omega
    if max_console is not None:
        console_status = f"🎮 Console: {console_count}/{max_console}"
        if console_count >= max_console:
            console_status += " (limit reached)"
        embed.add_field(name="Platform Limits", value=console_status, inline=False)

    embed.set_footer(text="Click buttons to join/leave")

    return embed


class LFGQueueView(discord.ui.View):
    """View with Join/Leave buttons for LFG queue sign-ups.

    Uses dynamic custom_ids with queue_id to ensure buttons work
    across bot restarts and don't conflict between different queues.
    """

    def __init__(self, queue_id: int):
        super().__init__(timeout=None)  # Persistent view
        self.queue_id = queue_id

        # Add buttons with dynamic custom_ids - NO callbacks here
        # Interactions are handled by on_interaction -> _handle_lfg_button
        join_button = discord.ui.Button(
            label="Join Queue",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"lfg_join:{queue_id}",
        )
        self.add_item(join_button)

        leave_button = discord.ui.Button(
            label="Leave Queue",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"lfg_leave:{queue_id}",
        )
        self.add_item(leave_button)

        close_button = discord.ui.Button(
            label="Close Queue",
            style=discord.ButtonStyle.secondary,
            emoji="🔒",
            custom_id=f"lfg_close:{queue_id}",
        )
        self.add_item(close_button)

    async def _get_queue_data(self) -> Optional[dict]:
        """Get queue data from memory or disk."""
        async with _g.LFG_QUEUE_LOCK:
            if self.queue_id in _g.LFG_ACTIVE_QUEUES:
                return _g.LFG_ACTIVE_QUEUES[self.queue_id]
            # Try loading from disk
            all_queues = _b("_load_lfg_queues")()
            if str(self.queue_id) in all_queues:
                queue_data = all_queues[str(self.queue_id)]
                _g.LFG_ACTIVE_QUEUES[self.queue_id] = queue_data
                return queue_data
        return None

    async def _save_queue_data(self, queue_data: dict):
        """Save queue data to memory and disk."""
        async with _g.LFG_QUEUE_LOCK:
            _g.LFG_ACTIVE_QUEUES[self.queue_id] = queue_data
            all_queues = _b("_load_lfg_queues")()
            all_queues[str(self.queue_id)] = queue_data
            _b("_save_lfg_queues")(all_queues)

    async def _update_embed(self, interaction: discord.Interaction):
        """Update the queue embed with current state."""
        queue_data = await self._get_queue_data()
        if not queue_data:
            return

        embed = _b("_build_lfg_embed")(queue_data, interaction.guild)
        try:
            # After defer(), we need to edit the original message directly
            # interaction.message is the message containing the button
            await interaction.message.edit(embed=embed, view=self)
        except Exception as e:
            _g.logger.warning(f"Failed to update LFG embed: {e}")

    async def join_queue(self, interaction: discord.Interaction):
        member = interaction.user
        if not isinstance(member, discord.Member):
            member = interaction.guild.get_member(interaction.user.id)

        if not member:
            await interaction.response.send_message("Could not resolve your membership.", ephemeral=True)
            return

        # Check platform role
        platform = _b("_get_player_platform")(member)
        if not platform:
            pc_role = _get_lfg_pc_role_id()
            console_role = _get_lfg_console_role_id()
            await interaction.response.send_message(
                f"❌ You must have either the <@&{pc_role}> or "
                f"<@&{console_role}> role to join a queue.\n"
                "Please assign yourself one of these roles first.",
                ephemeral=True,
            )
            return

        queue_data = await self._get_queue_data()
        if not queue_data:
            await interaction.response.send_message("This queue no longer exists.", ephemeral=True)
            return

        queue_types = _b("_get_lfg_queue_types")()
        type_config = queue_types.get(queue_data["queue_type"], {})
        players = queue_data["players"]

        # Check if already in queue
        if any(p["user_id"] == member.id for p in players):
            await interaction.response.send_message("You are already in this queue.", ephemeral=True)
            return

        # Check if queue is full
        if len(players) >= type_config.get("max_players", 3):
            await interaction.response.send_message("This queue is already full.", ephemeral=True)
            return

        # Check console limit for Omega
        max_console = type_config.get("max_console")
        if max_console is not None and platform == "console":
            console_count = sum(1 for p in players if p["platform"] == "console")
            if console_count >= max_console:
                await interaction.response.send_message(
                    f"❌ This Omega queue has reached the console player limit ({max_console}).\n"
                    "Only PC players can join at this time.",
                    ephemeral=True,
                )
                return

        # Add player to queue
        players.append({"user_id": member.id, "platform": platform})
        queue_data["players"] = players
        await self._save_queue_data(queue_data)

        # Update embed by editing the message directly
        embed = _b("_build_lfg_embed")(queue_data, interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self)

        # Check if queue is now full and notify creator
        if len(players) >= type_config.get("max_players", 3):
            creator = interaction.guild.get_member(queue_data["creator_id"])
            if creator:
                player_mentions = []
                for p in players:
                    m = interaction.guild.get_member(p["user_id"])
                    if m:
                        player_mentions.append(m.mention)
                try:
                    await interaction.followup.send(
                        f"🎉 **Queue Full!** {creator.mention}, your {type_config.get('display', 'Mission')} queue is ready!\n"
                        f"Players: {', '.join(player_mentions)}",
                        allowed_mentions=discord.AllowedMentions(users=True),
                    )
                except Exception:
                    pass

    async def leave_queue(self, interaction: discord.Interaction):
        member = interaction.user

        queue_data = await self._get_queue_data()
        if not queue_data:
            await interaction.response.send_message("This queue no longer exists.", ephemeral=True)
            return

        players = queue_data["players"]

        # Check if in queue
        player_entry = next((p for p in players if p["user_id"] == member.id), None)
        if not player_entry:
            await interaction.response.send_message("You are not in this queue.", ephemeral=True)
            return

        # Remove player
        players.remove(player_entry)
        queue_data["players"] = players
        await self._save_queue_data(queue_data)

        # Update embed by editing the message directly
        embed = _b("_build_lfg_embed")(queue_data, interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self)

    async def close_queue(self, interaction: discord.Interaction):
        queue_data = await self._get_queue_data()
        if not queue_data:
            await interaction.response.send_message("This queue no longer exists.", ephemeral=True)
            return

        # Only creator can close
        if interaction.user.id != queue_data["creator_id"]:
            await interaction.response.send_message("Only the queue creator can close this queue.", ephemeral=True)
            return

        # Remove from storage
        async with _g.LFG_QUEUE_LOCK:
            if self.queue_id in _g.LFG_ACTIVE_QUEUES:
                del _g.LFG_ACTIVE_QUEUES[self.queue_id]
            all_queues = _b("_load_lfg_queues")()
            if str(self.queue_id) in all_queues:
                del all_queues[str(self.queue_id)]
                _b("_save_lfg_queues")(all_queues)

        # Update message to show closed
        embed = discord.Embed(
            title="🔒 Queue Closed",
            description="This queue has been closed by the creator.",
            color=0x95A5A6,
        )
        await interaction.response.edit_message(embed=embed, view=None)


async def _restore_lfg_queue_views():
    """Restore persistent views for existing LFG queues on bot startup."""
    try:
        all_queues = _b("_load_lfg_queues")()
        for queue_id_str, queue_data in all_queues.items():
            try:
                queue_id = int(queue_id_str)
                _g.LFG_ACTIVE_QUEUES[queue_id] = queue_data
                # Register the view with unique custom_ids per queue
                _g.bot.add_view(LFGQueueView(queue_id))
            except Exception as e:
                _g.logger.debug(f"Failed to restore LFG queue view {queue_id_str}: {e}")
        if all_queues:
            _g.logger.info(f"Restored {len(all_queues)} LFG queue view(s)")
    except Exception as e:
        _g.logger.warning(f"Failed to restore LFG queue views: {e}")


async def _expire_old_lfg_queues():
    """Check for and expire old LFG queues."""
    try:
        now = _b("datetime").now(timezone.utc)
        expired = []

        async with _g.LFG_QUEUE_LOCK:
            all_queues = _b("_load_lfg_queues")()

            for queue_id_str, queue_data in list(all_queues.items()):
                expires_at_str = queue_data.get("expires_at")
                if not expires_at_str:
                    continue

                try:
                    expires_at = _b("datetime").fromisoformat(expires_at_str)
                    if now >= expires_at:
                        expired.append((int(queue_id_str), queue_data))
                        del all_queues[queue_id_str]
                        if int(queue_id_str) in _g.LFG_ACTIVE_QUEUES:
                            del _g.LFG_ACTIVE_QUEUES[int(queue_id_str)]
                except Exception:
                    continue

            if expired:
                _b("_save_lfg_queues")(all_queues)

        # Update expired queue messages
        for queue_id, queue_data in expired:
            try:
                guild = _b("_resolve_notification_guild")()
                if not guild:
                    continue
                # Get channel from stored channel_id in queue_data
                channel_id = queue_data.get("channel_id")
                if not channel_id:
                    continue
                channel = guild.get_channel(int(channel_id))
                if not channel:
                    continue
                msg = await channel.fetch_message(queue_id)
                embed = discord.Embed(
                    title="⏰ Queue Expired",
                    description="This queue has expired and is no longer accepting sign-ups.",
                    color=0x95A5A6,
                )
                await msg.edit(embed=embed, view=None)
            except discord.NotFound:
                pass
            except Exception as e:
                _g.logger.debug(f"Failed to update expired queue message {queue_id}: {e}")

        if expired:
            _g.logger.info(f"Expired {len(expired)} LFG queue(s)")
    except Exception as e:
        _g.logger.warning(f"Failed to expire LFG queues: {e}")


@tasks.loop(minutes=5)
async def _lfg_queue_expiration_loop():
    """Check for expired LFG queues every 5 minutes."""
    try:
        await _expire_old_lfg_queues()
    except Exception:
        _g.logger.exception("Error in LFG queue expiration loop")


# ─────────────────────────────────────────────────────────────────────────────
# Log to Forge View - Button for posting ephemeral blessings publicly
# ─────────────────────────────────────────────────────────────────────────────


class LogToForgeView(discord.ui.View):
    """View with a 'Log to Forge' button for blessing attestations.

    When clicked, posts the blessing publicly to the arming chamber.
    """

    def __init__(
        self,
        embed: discord.Embed,
        member_id: int,
        member_mention: str,
        techmarine_id: int,
        spirit_designation: str,
        image_filename: Optional[str] = None,
    ):
        super().__init__(timeout=300)  # 5 minute timeout
        self.embed = embed
        self.member_id = member_id
        self.member_mention = member_mention
        self.techmarine_id = techmarine_id
        self.spirit_designation = spirit_designation
        self.image_filename = image_filename
        self.logged = False

    @discord.ui.button(
        label="Log to Forge",
        style=discord.ButtonStyle.primary,
        emoji="📜",
        custom_id="log_to_forge",
    )
    async def log_to_forge(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.logged:
            await interaction.response.send_message("Already logged to forge.", ephemeral=True)
            return

        self.logged = True
        button.disabled = True
        button.label = "Logged"
        button.style = discord.ButtonStyle.secondary

        # Update the ephemeral message to show button is disabled
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            pass

        # Post blessing publicly to arming chamber
        channel_id = _get_arming_chamber_channel_id()
        if not channel_id or not interaction.guild:
            return

        channel = interaction.guild.get_channel(channel_id)
        if not channel:
            return

        # Re-load the image file so the attachment:// URL resolves in the public post
        public_file = _get_award_image(self.image_filename) if self.image_filename else None
        try:
            send_kwargs: dict = {
                "content": self.member_mention,
                "embed": self.embed,
                "allowed_mentions": discord.AllowedMentions(users=True),
            }
            if public_file:
                send_kwargs["file"] = public_file
            await channel.send(**send_kwargs)
        except Exception as e:
            _g.logger.warning(f"Failed to log blessing to forge: {e}")


def _extract_killteam_name(name: str) -> str:
    """Return a display-friendly Kill Team name by stripping the 'Kill Team' prefix.
    Handles optional separators like ':', '-', and varying whitespace/case.
    Also handles forum channel format 'Kill-Team X' (hyphen between Kill and Team).
    If no match, returns the original name (or 'Unknown' if empty).
    Ignores role names like 'Kill Team Champion' that aren't actual kill teams.
    """
    try:
        # Skip non-KT role names that start with "Kill Team"
        if name and name.lower().strip() == "kill team champion":
            return name or "Unknown"
        # Match 'Kill Team X', 'Kill-Team X', 'KillTeam X', etc.
        m = re.match(r"(?i)\s*kill[\s\-]*team\s*[:\-]?\s*(.+)", (name or ""))
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return name or "Unknown"


def _resolve_killteam_for_member(
    member: discord.User | discord.Member,
) -> Optional[str]:
    """Return the canonical Kill Team name for a member by inspecting their roles.

    Matching strategy (in order):
    1. Role ID in ALLOWED_KT_ROLE_IDS (most reliable).
    2. Exact case-insensitive role name match against entries in `_b('KILL_TEAMS')`.

    Returns the canonical `_b('KILL_TEAMS')` entry on match, else `None`.
    """
    try:
        roles = getattr(member, "roles", []) or []
        # map lower->canonical for fast lookup
        canonical_map = {kt.lower(): kt for kt in _b("KILL_TEAMS")}

        for r in roles:
            # 1) Check role ID against ALLOWED_KT_ROLE_IDS (most reliable)
            rid = getattr(r, "id", None)
            if rid and _b("ALLOWED_KT_ROLE_IDS") and rid in _b("ALLOWED_KT_ROLE_IDS"):
                rn = (getattr(r, "name", "") or "").strip()
                # Return the role name if it's in _b('KILL_TEAMS'), otherwise return as-is
                if rn.lower() in canonical_map:
                    return canonical_map[rn.lower()]
                return rn  # Role ID matched but name not in _b('KILL_TEAMS') yet

            # 2) Exact case-insensitive match against _b('KILL_TEAMS') entries
            rn = (getattr(r, "name", "") or "").strip()
            if not rn:
                continue
            if rn.lower() in canonical_map:
                return canonical_map[rn.lower()]
    except Exception:
        return None
    return None


def _resolve_killteams_for_member(member: discord.User | discord.Member) -> List[str]:
    """Return a list of Kill Team-like identifiers this member should contribute to.

    Rules:
    - Include any canonical Kill Team from `_b('KILL_TEAMS')` the member holds.
    - Include any command team from `_b('COMMAND_TEAMS')` the member holds as a role.
    - A member may contribute to multiple teams simultaneously.
    """
    out: List[str] = []
    try:
        # 1) canonical kill teams
        try:
            kt = _resolve_killteam_for_member(member)
            if kt:
                out.append(kt)
        except Exception:
            pass

        # 2) command teams (check for actual roles matching _b('COMMAND_TEAMS'))
        try:
            names = _b("_canonical_role_names")(member)
            for cmd_team in _b("COMMAND_TEAMS"):
                if cmd_team in names and cmd_team not in out:
                    out.append(cmd_team)
        except Exception:
            pass
    except Exception:
        return []

    # Deduplicate preserving order
    seen = set()
    res: List[str] = []
    for x in out:
        if x and x not in seen:
            res.append(x)
            seen.add(x)
    return res


def _get_techmarine_acknowledgment_blended(member: "discord.Member", bearer_studs: int) -> str:
    """Get a dynamically blended acknowledgment phrase for forge_rite.

    Blends rank-specific and stud-specific acknowledgments based on:
    - Higher studs → more likely stud acknowledgment
    - Higher rank → more likely rank acknowledgment

    Examples:
    - Watch Veteran + 16 studs → ~83% stud ack (studs are impressive for low rank)
    - High Chaplain + 2 studs → ~86% rank ack (rank is impressive vs low studs)
    - Forgemaster + 16 studs → ~50/50 (both equally impressive)
    """
    import random

    # Determine bearer's rank name (highest priority first based on _b('RANK_ROLES_PRIORITY') order)
    bearer_rank_name = None
    try:
        for rank_name in _b("RANK_ROLES_PRIORITY"):
            for r in getattr(member, "roles", []) or []:
                rn = (getattr(r, "name", "") or "").strip()
                if rn == rank_name:
                    bearer_rank_name = rank_name
                    break
            if bearer_rank_name:
                break
    except Exception:
        pass

    if not bearer_rank_name:
        bearer_rank_name = "Watch Brother"

    # Calculate weights
    rank_weight = RANK_PRESTIGE_WEIGHTS.get(bearer_rank_name, 0.1)
    stud_weight = _get_stud_weight(bearer_studs)

    # Probability of rank acknowledgment = rank_weight / (rank_weight + stud_weight)
    prob_rank = rank_weight / (rank_weight + stud_weight)

    # Choose based on probability
    if random.random() < prob_rank:
        # Use rank-specific acknowledgment
        rank_options = TECHMARINE_RANK_ACKNOWLEDGMENTS.get(
            bearer_rank_name, TECHMARINE_RANK_ACKNOWLEDGMENTS["Watch Brother"]
        )
        return random.choice(rank_options)
    else:
        # Use stud-tier acknowledgment via shared _studs_tier()
        studs_tier = _studs_tier(bearer_studs)
        stud_options = TECHMARINE_STUDS_ACKNOWLEDGMENT.get(studs_tier, TECHMARINE_STUDS_ACKNOWLEDGMENT[1])
        return random.choice(stud_options)


# Techmarine signature variation phrases (randomly chosen)
# Additional flavor data (TECHMARINE_SIGNATURES, SACRED_MECHANICUS_PHRASES,
# FORGEMASTER_SELF_ATTESTATION_*, CHAPTER_STUDS_FLAVOR, ORDO_XENOS_HONORS_*,
# RANK_STUDS_COMMENTARY, SERVICE_STUDS_*, DEATHWATCH_STUD_*, OATHSWORN_*) lives
# in flavor_text.py.


def _get_emoji_by_name(guild: discord.Guild, name: str) -> Optional[str]:
    """Lookup a custom emoji by name from the guild.

    Returns the emoji string (e.g., '<:HawkLords:123456>') if found, else None.
    The name should be without colons, e.g., 'HawkLords' not ':HawkLords:'.
    """
    if not guild:
        return None
    # Normalize: remove spaces and special chars for lookup
    # e.g., 'Hawk Lords' -> 'HawkLords', 'Watch Brother' -> 'WatchBrother'
    normalized = name.replace(" ", "").replace("-", "").replace("'", "")
    for emoji in guild.emojis:
        if emoji.name.lower() == normalized.lower():
            return str(emoji)
    return None


def _blend_forgemaster_self_attestation(member_chapter: str) -> str:
    """Blend chapter identity and role identity for Forgemaster self-blessing.

    Follows High Command Specialist ratio: 80% role (generic Mechanicus), 20% chapter.
    Falls back to generic if chapter not found.
    """
    import random

    chapter_options = FORGEMASTER_SELF_ATTESTATION_BY_CHAPTER.get(member_chapter, [])

    # 80% role (generic Mechanicus), 20% chapter
    if random.random() < 0.8:
        return random.choice(FORGEMASTER_SELF_ATTESTATION_GENERIC)

    if chapter_options:
        return random.choice(chapter_options)

    # Fallback to generic if chapter not in dict
    return random.choice(FORGEMASTER_SELF_ATTESTATION_GENERIC)


def _get_chapter_emoji(guild: discord.Guild, chapter_name: str) -> str:
    """Get chapter emoji or fallback to just the chapter name."""
    emoji = _get_emoji_by_name(guild, chapter_name)
    if emoji:
        return f"{emoji} {chapter_name}"
    return chapter_name


def _get_rank_emoji(guild: discord.Guild, rank_name: str) -> str:
    """Get rank emoji or fallback to just the rank name."""
    # Special mappings where emoji name differs from role name
    RANK_EMOJI_OVERRIDES = {
        "Company Champion": "WatchChampion",
        "Kill Team Champion": "KillteamChampion",
        "Venerable Dreadnought": "Venerable",
    }
    emoji_name = RANK_EMOJI_OVERRIDES.get(rank_name, rank_name)
    emoji = _get_emoji_by_name(guild, emoji_name)
    if emoji:
        return f"{emoji}"
    return ""


def _get_rank_category_for_blend(rank_name: str) -> str:
    """Categorize rank for stud flavor blending.

    Returns one of: 'watchers', 'high_cmd_specialist', 'company_cmd', 'specialist', 'line'

    - watchers: Watch Master (100% role, 0% chapter)
    - high_cmd_specialist: Chaplain, Apothecary, Librarian, Techmarine at High Command level
    - company_cmd: Captains, Lieutenants, Champions at company level
    - specialist: Watch Chaplain, Watch Apothecary, Watch Librarian, Watch Techmarine (not high cmd)
    - line: Everyone else (Sergeant, Veteran, Brother, Champions at KT level)
    """
    if rank_name == "Watch Master":
        return "watchers"

    high_cmd_roles = {
        "High Chaplain",
        "Chief Apothecary",
        "Watch Librarian",
        "Watch Techmarine",
        "Forgemaster",
        "Void Warden",
        "Venerable Dreadnought",  # Ancient of the Long Watch, high command level
    }
    if rank_name in high_cmd_roles:
        return "high_cmd_specialist"

    company_cmd_roles = {
        "Watch Captain",
        "Watch Lieutenant",
        "Company Champion",
        "Honored Dreadnought",  # Honored warriors, company command level
    }
    if rank_name in company_cmd_roles:
        return "company_cmd"

    specialist_roles = {"Watch Chaplain", "Watch Apothecary"}
    if rank_name in specialist_roles:
        return "specialist"

    # Interred Brother falls into "line" category (inactive, lowest priority)
    return "line"


def _blend_stud_flavor_by_rank(member_chapter: str, member_rank_name: str, pip_type: str) -> str:
    """Blend chapter identity and role identity based on rank hierarchy.

    - Line (KB/Oathsworn/KT members): 80% chapter, 20% role
    - Specialist (Watch Chaplain/Apothecary): 50% chapter, 50% role
    - Company Command: 50% chapter, 50% role
    - High Command Specialist: 20% chapter, 80% role
    - Watch Master: 10% chapter, 90% role

    pip_type: "plasteel" or "auramite" for veneration fallback selection.
    Returns blended flavor text or falls back to pip-type-based veneration.
    """
    import random

    category = _get_rank_category_for_blend(member_rank_name)

    # Get chapter flavor (3 options per chapter)
    chapter_options = CHAPTER_STUDS_FLAVOR.get(member_chapter, [])

    # Get role-specific commentary (if available)
    role_options = RANK_STUDS_COMMENTARY.get(member_rank_name, [])

    # Select veneration pool based on pip type
    if pip_type == "auramite":
        veneration_pool = SERVICE_STUDS_VENERATIONS_AURAMITE
    else:  # plasteel or unknown
        veneration_pool = SERVICE_STUDS_VENERATIONS_PLASTEEL

    # Blend based on category
    if category == "watchers":
        # 90% role, 10% chapter
        if random.random() < 0.9:
            if role_options:
                return random.choice(role_options)
        if chapter_options:
            return random.choice(chapter_options)
        # Fallback to pip-type veneration
        return random.choice(veneration_pool)

    elif category == "high_cmd_specialist":
        # 80% role, 20% chapter
        if random.random() < 0.8:
            if role_options:
                return random.choice(role_options)
        if chapter_options:
            return random.choice(chapter_options)
        return random.choice(veneration_pool)

    elif category == "company_cmd" or category == "specialist":
        # 50% chapter, 50% role
        if random.random() < 0.5:
            if chapter_options:
                return random.choice(chapter_options)
            if role_options:
                return random.choice(role_options)
        else:
            if role_options:
                return random.choice(role_options)
            if chapter_options:
                return random.choice(chapter_options)
        return random.choice(veneration_pool)

    else:  # line (default: KB, Oathsworn, KT members)
        # 80% chapter, 20% role
        if random.random() < 0.8:
            if chapter_options:
                return random.choice(chapter_options)
        if role_options:
            return random.choice(role_options)
        return random.choice(veneration_pool)


def _get_stud_marking_recipients(member: discord.Member, guild: discord.Guild) -> Tuple[str, str]:
    """Determine who receives stud marking and who witnesses. Returns (primary, secondary).

    The Apothecarion always performs the actual stud implantation (surgical procedure).
    This function determines who witnesses/authorizes based on chain of command:
    - Watch Master: The Chief Apothecary personally attends
    - High Command: The Chief Apothecary attends
    - Company members: Report to their Company Apothecary → Chief Apothecary → CO (in order)
    - Line/Kill Team: Same as company members

    Returns (primary_text, secondary_text) where text is bold name with rank emoji.
    """

    def strip_studs(name: str) -> str:
        """Remove service studs (●⚬) from a name."""
        return name.replace("●", "").replace("⚬", "").strip()

    def find_company_apothecary(company_name: str) -> Optional[discord.Member]:
        """Find the Watch Apothecary for a specific company."""
        try:
            for mbr in guild.members:
                mbr_roles = {getattr(r, "name", "") for r in mbr.roles}
                if "Watch Apothecary" not in mbr_roles:
                    continue
                # Check if this apothecary is in the same company
                mbr_company = _find_company_or_chapter(mbr)
                if mbr_company and mbr_company == company_name:
                    return mbr
        except Exception:
            pass
        return None

    def find_chief_apothecary() -> Optional[discord.Member]:
        """Find the Chief Apothecary."""
        try:
            for mbr in guild.members:
                mbr_roles = {getattr(r, "name", "") for r in mbr.roles}
                if "Chief Apothecary" in mbr_roles:
                    return mbr
        except Exception:
            pass
        return None

    roles = getattr(member, "roles", []) or []
    role_names = [getattr(r, "name", "") for r in roles]

    # Determine highest rank
    member_rank_name = "Watch Brother"
    for rank in _b("RANK_ROLES_PRIORITY"):
        if rank in role_names:
            member_rank_name = rank
            break

    # Watch Master: Chief Apothecary personally attends
    if member_rank_name == "Watch Master":
        chief_apo = find_chief_apothecary()
        if chief_apo:
            emoji = _get_rank_emoji(guild, "Chief Apothecary")
            emoji_prefix = f"{emoji} " if emoji else ""
            clean_name = strip_studs(chief_apo.display_name)
            return f"The {emoji_prefix}**{clean_name}** personally attends.", ""
        return "The Chief Apothecary personally attends.", ""

    # High Command: Chief Apothecary attends
    high_cmd = {
        "High Chaplain",
        "Chief Apothecary",
        "Void Warden",
        "Lord Executioner",
        "Forgemaster",
        "Castellan",
    }
    if member_rank_name in high_cmd:
        # If they ARE the Chief Apothecary, another Apothecary handles it
        if member_rank_name == "Chief Apothecary":
            return "Another Apothecary of the Watch attends.", ""
        chief_apo = find_chief_apothecary()
        if chief_apo:
            emoji = _get_rank_emoji(guild, "Chief Apothecary")
            emoji_prefix = f"{emoji} " if emoji else ""
            clean_name = strip_studs(chief_apo.display_name)
            return f"The {emoji_prefix}**{clean_name}** attends.", ""
        return "Report to the Chief Apothecary.", ""

    # All company members (command, specialists, line, kill team):
    # Try Company Apothecary → Chief Apothecary → CO
    member_company = _find_company_or_chapter(member)

    # Special case: if member IS the Watch Apothecary, go to Chief directly
    if member_rank_name == "Watch Apothecary":
        chief_apo = find_chief_apothecary()
        if chief_apo:
            emoji = _get_rank_emoji(guild, "Chief Apothecary")
            emoji_prefix = f"{emoji} " if emoji else ""
            clean_name = strip_studs(chief_apo.display_name)
            return f"The {emoji_prefix}**{clean_name}** attends.", ""
        return "Report to the Chief Apothecary.", ""

    # Try to find Company Apothecary first
    if member_company:
        company_apo = find_company_apothecary(member_company)
        if company_apo and company_apo.id != member.id:
            emoji = _get_rank_emoji(guild, "Watch Apothecary")
            emoji_prefix = f"{emoji} " if emoji else ""
            clean_name = strip_studs(company_apo.display_name)
            return f"Report to {emoji_prefix}**{clean_name}**.", ""

    # Fallback: Chief Apothecary
    chief_apo = find_chief_apothecary()
    if chief_apo:
        emoji = _get_rank_emoji(guild, "Chief Apothecary")
        emoji_prefix = f"{emoji} " if emoji else ""
        clean_name = strip_studs(chief_apo.display_name)
        return f"Report to {emoji_prefix}**{clean_name}**.", ""

    # Fallback: Company CO (Captain/Lieutenant)
    if member_company:
        captains, lieutenants = _b("_find_company_command_staff")(guild, member_company)
        co_member = lieutenants[0] if lieutenants else (captains[0] if captains else None)
        if co_member:
            co_roles = {getattr(r, "name", "") for r in co_member.roles}
            co_rank = "Watch Lieutenant" if "Watch Lieutenant" in co_roles else "Watch Captain"
            emoji = _get_rank_emoji(guild, co_rank)
            emoji_prefix = f"{emoji} " if emoji else ""
            clean_name = strip_studs(co_member.display_name)
            return f"Report to {emoji_prefix}**{clean_name}**.", ""

    return "Report to the Apothecarion.", ""


def _get_service_studs_announcement(
    member: discord.Member,
    member_chapter: str,
    displayed_studs: int,
    new_studs: int,
    earned_studs: int,
    owed_studs: int,
    guild: discord.Guild,
) -> str:
    """Generate a flavorful, RP-oriented service studs announcement.

    Incorporates the member's rank, home chapter, and which stud they're earning
    to create a personalized and immersive notification.
    Mobile-friendly with shorter lines and Deathwatch theming.
    """
    import random

    roles = getattr(member, "roles", []) or []
    role_names = [getattr(r, "name", "") for r in roles]

    # Use shared function for dynamic champion honorifics (same as forge_rite)
    rank_honorific, display_name, member_title = _get_bearer_rank_and_title(member)

    # Determine raw rank name for emoji lookup
    member_rank_name = "Watch Brother"
    for rank in _b("RANK_ROLES_PRIORITY"):
        if rank in role_names:
            member_rank_name = rank
            break

    stud_word = "Stud" if new_studs == 1 else "Studs"

    # Determine tier and pip display based on EARNED studs (actual total earned)
    # This is the true count based on time and AAR, not displayed count
    tier = _studs_tier(earned_studs)
    studs_pips = _studs_pips(earned_studs)

    # Also track what they'll have after this announcement for pip change display
    new_total = displayed_studs + new_studs

    # Get Watch Brother role for pinging in content (outside embed)
    watch_brother_role = discord.utils.get(guild.roles, name="Watch Brother")
    wb_mention = watch_brother_role.mention if watch_brother_role else ""

    # Get emojis for rank and chapter
    rank_emoji = _get_rank_emoji(guild, member_rank_name)
    chapter_emoji = _get_emoji_by_name(guild, member_chapter) if member_chapter != "Unknown" else None

    # Build embed
    embed = discord.Embed(
        title="᛭⋅ MARK OF SERVICE ⋅᛭",
        description="*⌾ Watch Fortress Jericho ⌾*",
        color=0xC0C0C0,  # Silver for service studs
    )

    # Generate opening and milestone intro (for first embed field)
    # Format opening with stripped display name (no rank/studs)
    opening_template = random.choice(DEATHWATCH_STUD_OPENINGS)
    opening = opening_template.format(name=display_name)

    # Use first-stud templates when earning stud #1 to avoid "another" phrasing
    if earned_studs == 1:
        milestone_intro = random.choice(SERVICE_STUDS_MILESTONE_FIRST)
    elif tier == 1:
        milestone_intro = random.choice(SERVICE_STUDS_MILESTONE_TIER1)
    elif tier == 2:
        milestone_intro = random.choice(SERVICE_STUDS_MILESTONE_TIER2)
    else:
        milestone_intro = random.choice(SERVICE_STUDS_MILESTONE_TIER3)

    # Add Watch's Proclamation as first field with mentions baked in
    # Opening and milestone intro flow together without line break (plain narrative text, no italics/quotes)
    proclamation_value = f"{opening} {milestone_intro}"
    embed.add_field(
        name="▸ Watch's Proclamation",
        value=proclamation_value,
        inline=False,
    )

    # Bearer field with rank emoji (exactly matching forge_rite format)
    rank_prefix = f"{rank_emoji} " if rank_emoji else ""
    # Split honorific if it contains a comma (e.g., "Blade of the Fortress, Lord Executioner")
    # to put title on one line and rank + name on the next
    if ", " in rank_honorific:
        title_part, rank_part = rank_honorific.rsplit(", ", 1)
        bearer_value = f"{rank_prefix}**{title_part},**\n**{rank_part} {display_name}**"
    else:
        bearer_value = f"{rank_prefix}**{rank_honorific} {display_name}**"
    if member_title:
        bearer_value += f"\n*{member_title}*"
    if member_chapter and member_chapter != "Unknown":
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        lineage_display = "REDACTED" if member_chapter == "Black Shield" else member_chapter
        bearer_value += f"\nLineage: {chapter_prefix}{lineage_display}"
    if earned_studs > 0:
        bearer_value += f"\nService Studs: [{studs_pips}] ({earned_studs})"
    embed.add_field(name="▸ Bearer", value=bearer_value, inline=True)

    # Calculate visual pip change (what pips change from BEFORE to AFTER)
    # displayed_studs = what they had before, new_total = what they'll have after
    prev_studs = max(0, displayed_studs)
    curr_studs = new_total

    prev_auramite = min(prev_studs // 4, 4)
    prev_plasteel = prev_studs % 4 if prev_studs <= 16 else 0

    curr_auramite = min(curr_studs // 4, 4)
    curr_plasteel = curr_studs % 4 if curr_studs <= 16 else 0

    # Compute net change in each pip type
    delta_auramite = curr_auramite - prev_auramite
    delta_plasteel = curr_plasteel - prev_plasteel

    # Build visual pip change string showing what was gained
    # Show the highest tier pip that increased (the "upgrade")
    # If multiple pip types changed, show all positive deltas
    pip_changes = []
    if delta_auramite > 0:
        pip_word = "Stud" if delta_auramite == 1 else "Studs"
        pip_changes.append(f"+{delta_auramite}● Auramite {pip_word}")
    if delta_plasteel > 0:
        pip_word = "Stud" if delta_plasteel == 1 else "Studs"
        pip_changes.append(f"+{delta_plasteel}⚬ Plasteel {pip_word}")

    # Service Record field (bold values for numerical emphasis)
    if pip_changes:
        pip_change = ", ".join(pip_changes)
    else:
        pip_change = f"+{new_studs} {stud_word}"
    record_value = f"**{pip_change}** Earned\n"
    record_value += f"Total: **{earned_studs}** | Displayed: **{displayed_studs}**"
    if owed_studs > 0:
        record_value += f"\nOwed: **{owed_studs}**"
    embed.add_field(name="▸ Service Record", value=record_value, inline=True)

    # Special milestone callout (bold labels, plain narrative - check against earned studs)
    special_milestone = SERVICE_STUDS_SPECIAL_MILESTONES.get(earned_studs)
    if special_milestone:
        embed.add_field(name="▸ Milestone", value=special_milestone, inline=False)

    # Honor of the Long Watch: Tiered Ordo Xenos phrase + blended chapter/role flavor
    # Select tier-appropriate Ordo Xenos honor
    if tier == 1:
        ordo_honor = random.choice(ORDO_XENOS_HONORS_TIER1)
    elif tier == 2:
        ordo_honor = random.choice(ORDO_XENOS_HONORS_TIER2)
    else:
        ordo_honor = random.choice(ORDO_XENOS_HONORS_TIER3)

    # Format pronouns (always second person for awarding to others)
    ordo_honor = ordo_honor.format(possessive="your", possessive_cap="Your", object="you")

    # Determine which pip type is being earned (priority: auramite > plasteel)
    if delta_auramite > 0:
        pip_type = "auramite"
    else:
        pip_type = "plasteel"

    # Blend chapter and role flavor based on rank hierarchy (italics + quotes for honor/reverential phrases)
    blended_flavor = _blend_stud_flavor_by_rank(member_chapter, member_rank_name, pip_type)

    embed.add_field(
        name="▸ Honor of the Long Watch",
        value=f'*"{ordo_honor} {blended_flavor}"*',
        inline=False,
    )

    # Call to action: determine who administers/witnesses marking based on rank (plain narrative with bold names)
    marking_primary, marking_secondary = _get_stud_marking_recipients(member, guild)
    marking_value = marking_primary
    if marking_secondary:
        marking_value = f"{marking_primary}\n{marking_secondary}"

    embed.add_field(
        name="▸ Rite of Marking",
        value=marking_value,
        inline=False,
    )

    # Footer with closing phrase from ceremonial closings
    closing_phrase = random.choice(DEATHWATCH_STUD_CLOSINGS)
    embed.set_footer(text=f"᛭⋅ {closing_phrase} Jericho Stands! ⋅᛭")
    embed.set_image(url="attachment://studs.png")

    # Content has @Watch Brother and member mention for actual pings (outside embed)
    content = f"{wb_mention} {member.mention}" if wb_mention else member.mention
    return content, embed


def _get_oathsworn_announcement(
    member: discord.Member,
    member_chapter: str,
    earned_studs: int,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, discord.Poll]:
    """Generate a flavorful Oathsworn eligibility announcement with embed and poll.

    Called when a Watch Veteran has earned 3+ service studs and is eligible
    for consideration to become Oathsworn. Returns content (mentions), embed
    (flavorful announcement), and a 48-hour poll for voting.
    """
    import random

    # Extract bearer info using shared function
    rank_honorific, display_name, member_title = _get_bearer_rank_and_title(member)

    # Get emojis
    rank_emoji = _get_rank_emoji(guild, "Watch Veteran")
    chapter_emoji = _get_emoji_by_name(guild, member_chapter) if member_chapter != "Unknown" else None
    oathsworn_emoji = _get_emoji_by_name(guild, "Oathsworn")
    deathwatch_emoji = _get_emoji_by_name(guild, "Deathwatch")

    # Compute stud pips display using shared helper (auramite-only post-4)
    studs_pips = _studs_pips(earned_studs)

    # Generate opening and proclamation
    opening_template = random.choice(OATHSWORN_OPENINGS)
    opening = opening_template.format(name=display_name)
    proclamation = random.choice(OATHSWORN_PROCLAMATIONS)

    # Build embed
    dw_emoji_str = f"{deathwatch_emoji} " if deathwatch_emoji else ""
    oath_emoji_str = f"{oathsworn_emoji} " if oathsworn_emoji else ""
    embed = discord.Embed(
        title=f"{dw_emoji_str}᛭⋅ OATHSWORN CONSIDERATION ⋅᛭{dw_emoji_str}",
        description="*⌾ Watch Fortress Jericho ⌾*",
        color=0xFFD700,  # Gold for Oathsworn consideration
    )

    # Proclamation field
    proclamation_value = f"{opening}\n\n{proclamation}"
    embed.add_field(
        name="▸ Watch's Proclamation",
        value=proclamation_value,
        inline=False,
    )

    # Candidate field (same format as Bearer in service studs/forge_rite)
    rank_prefix = f"{rank_emoji} " if rank_emoji else ""
    # Split honorific if it contains a comma (e.g., "Blade of the Fortress, Lord Executioner")
    # to put title on one line and rank + name on the next
    if ", " in rank_honorific:
        title_part, rank_part = rank_honorific.rsplit(", ", 1)
        candidate_value = f"{rank_prefix}**{title_part},**\n**{rank_part} {display_name}**"
    else:
        candidate_value = f"{rank_prefix}**{rank_honorific} {display_name}**"
    if member_title:
        candidate_value += f"\n*{member_title}*"
    if member_chapter != "Unknown":
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        lineage_display = "REDACTED" if member_chapter == "Black Shield" else member_chapter
        candidate_value += f"\nLineage: {chapter_prefix}{lineage_display}"
    candidate_value += f"\nService Studs: **[{studs_pips}]** ({earned_studs})"
    embed.add_field(name="▸ Candidate", value=candidate_value, inline=True)

    # Eligibility field
    eligibility_value = (
        f"Rank: **Watch Veteran** ✓\n"
        f"Service Studs: **{earned_studs}** (3 required) ✓\n"
        f"Eligible for: {oath_emoji_str}**Oathsworn**"
    )
    embed.add_field(name="▸ Eligibility", value=eligibility_value, inline=True)

    # Call to action
    embed.add_field(
        name="▸ Rite of Elevation",
        value=(
            "The Watch awaits your judgment, Brothers.\n"
            "Cast your vote below to determine if this warrior shall take the Oath."
        ),
        inline=False,
    )

    # Footer
    embed.set_footer(text="᛭⋅ By Bolt and Blade, the Watch Endures! ⋅᛭")

    # Create poll - 48 hour duration
    poll = discord.Poll(
        question=f"Shall {display_name} be elevated to Oathsworn?",
        duration=timedelta(hours=48),
        multiple=False,
    )
    poll.add_answer(text="Aye, elevate to Oathsworn", emoji="⚔️")
    poll.add_answer(text="Nay, more service required", emoji="🛡️")

    # Content with mentions
    watch_brother_role = discord.utils.get(guild.roles, name="Watch Brother")
    wb_mention = watch_brother_role.mention if watch_brother_role else ""
    content = f"{wb_mention} {member.mention}".strip()

    return content, embed, poll


def _get_member_rank_title(member: discord.Member) -> str:
    """Get the rank honorific for a member based on their highest rank role."""
    roles = getattr(member, "roles", []) or []
    role_names = [getattr(r, "name", "") for r in roles]
    # Check ranks in priority order (highest first)
    for rank in _b("RANK_ROLES_PRIORITY"):
        if rank in role_names:
            return RANK_HONORIFICS.get(rank, rank)
    return "Brother"


async def _get_award_announcement_channel(
    member: discord.Member,
    guild: discord.Guild,
) -> Optional[discord.abc.Messageable]:
    """Return the channel for a public award announcement.

    Resolution order:
    1. KT_ROLE_CHANNEL_MAP override (role_id → channel_id) if populated.
    2. Active forum thread in ALLOWED_KT_FORUM_PARENT_IDS whose name matches
       the member's Kill Team role (via _extract_killteam_name fuzzy match).
    3. General channel (SERVICE_STUDS_CHANNEL_ID) as fallback.
    """
    # 1) Static map override
    kt_channel_map: dict = _b("KT_ROLE_CHANNEL_MAP") or {}
    if kt_channel_map:
        for role in getattr(member, "roles", []):
            channel_id = kt_channel_map.get(role.id)
            if channel_id:
                ch = guild.get_channel(channel_id)
                if ch:
                    return ch

    # 2) Dynamic forum thread search
    kt_name = _resolve_killteam_for_member(member)
    if kt_name:
        kt_short = _extract_killteam_name(kt_name).lower()
        forum_parent_ids = _b("ALLOWED_KT_FORUM_PARENT_IDS") or set()
        try:
            active_threads = await guild.active_threads()
            for thread in active_threads:
                parent = thread.parent
                if parent and parent.id in forum_parent_ids:
                    thread_short = _extract_killteam_name(thread.name).lower()
                    if thread_short and (kt_short in thread_short or thread_short in kt_short):
                        return thread
        except Exception as e:
            _g.logger.debug(f"Failed to resolve KT thread for award announcement ({member.id}): {e}")

    # 3) Fallback: general
    return guild.get_channel(SERVICE_STUDS_CHANNEL_ID)


_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")


def _trunc(text: str, limit: int = 1024) -> str:
    """Truncate text to Discord embed field limit, appending ellipsis if cut."""
    if len(text) <= limit:
        return text
    return text[:limit - 1] + "…"


def _get_award_image(filename: str) -> Optional[discord.File]:
    path = os.path.join(_ASSETS_DIR, filename)
    if os.path.isfile(path):
        return discord.File(path, filename=filename)
    return None


def _get_watch_veteran_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Watch Veteran promotion announcement embed.

    Called after the bot auto-assigns the Watch Veteran role.
    Returns (content, embed) where content holds the ping mentions.
    """
    # Use shared helper for display name / title but override honorific since
    # the member was just promoted and may not yet reflect the new role.
    _, display_name, member_title = _get_bearer_rank_and_title(member)

    rank_emoji = _get_rank_emoji(guild, "Watch Veteran")
    chapter_emoji = _get_emoji_by_name(guild, member_chapter) if member_chapter != "Unknown" else None
    deathwatch_emoji = _get_emoji_by_name(guild, "Deathwatch")

    opening = random.choice(WATCH_VETERAN_OPENINGS).format(name=display_name)
    proclamation = random.choice(WATCH_VETERAN_PROCLAMATIONS)
    chapter_coda = WATCH_VETERAN_CHAPTER_LINES.get(member_chapter, "")

    dw_str = f"{deathwatch_emoji} " if deathwatch_emoji else ""
    embed = discord.Embed(
        title=f"{dw_str}᛭⋅ WATCH VETERAN PROMOTION ⋅᛭{dw_str}",
        description="*⌾ Watch Fortress Jericho ⌾*",
        color=0xC0C0C0,
    )

    proclamation_text = f"{opening}\n\n{proclamation}"
    if chapter_coda:
        proclamation_text += f"\n\n*{chapter_coda}*"
    embed.add_field(
        name="▸ Watch's Proclamation",
        value=_trunc(proclamation_text),
        inline=False,
    )

    rank_prefix = f"{rank_emoji} " if rank_emoji else ""
    bearer_value = f"{rank_prefix}**Honored Veteran {display_name}**"
    if member_title:
        bearer_value += f"\n*{member_title}*"
    if member_chapter != "Unknown":
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        lineage_display = "REDACTED" if member_chapter == "Black Shield" else member_chapter
        bearer_value += f"\nLineage: {chapter_prefix}{lineage_display}"
    embed.add_field(name="▸ Promoted Warrior", value=bearer_value, inline=True)

    embed.add_field(
        name="▸ Service Record",
        value="Service: **200+ AAR Points** ✓\nTime: **2+ Weeks** ✓\nPromoted to: **Watch Veteran**",
        inline=True,
    )

    embed.set_footer(text="᛭⋅ By Bolt and Blade, the Watch Endures! ⋅᛭")
    award_file = _get_award_image("award_watch_veteran.png")
    if award_file:
        embed.set_image(url="attachment://award_watch_veteran.png")

    watch_brother_role = discord.utils.get(guild.roles, name="Watch Brother")
    wb_mention = watch_brother_role.mention if watch_brother_role else ""
    content = f"{wb_mention} {member.mention}".strip()
    return content, embed, award_file


def _get_ardent_raider_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Ardent Raider Ribbon award announcement embed."""
    rank_honorific, display_name, member_title = _get_bearer_rank_and_title(member)
    rank_emoji = None
    chapter_emoji = _get_emoji_by_name(guild, member_chapter) if member_chapter != "Unknown" else None
    deathwatch_emoji = _get_emoji_by_name(guild, "Deathwatch")
    ribbon_emoji = _get_emoji_by_name(guild, "ArdentRaiderRibbon")

    opening = random.choice(ARDENT_RAIDER_OPENINGS).format(name=display_name)
    proclamation = random.choice(ARDENT_RAIDER_PROCLAMATIONS)
    chapter_coda = ARDENT_RAIDER_CHAPTER_LINES.get(member_chapter, "")

    dw_str = f"{deathwatch_emoji} " if deathwatch_emoji else ""
    embed = discord.Embed(
        title=f"{dw_str}᛭⋅ ARDENT RAIDER RIBBON ⋅᛭{dw_str}",
        description="*⌾ Watch Fortress Jericho ⌾*",
        color=0xD4AF37,
    )

    proclamation_text = f"{opening}\n\n{proclamation}"
    if chapter_coda:
        proclamation_text += f"\n\n*{chapter_coda}*"
    embed.add_field(
        name="▸ Watch's Proclamation",
        value=_trunc(proclamation_text),
        inline=False,
    )

    role_names = {getattr(r, "name", "") for r in getattr(member, "roles", [])}
    for rank in RANK_HONORIFICS:
        if rank in role_names:
            rank_emoji = _get_rank_emoji(guild, rank)
            break
    rank_prefix = f"{rank_emoji} " if rank_emoji else ""
    bearer_value = f"{rank_prefix}**{rank_honorific} {display_name}**"
    if member_title:
        bearer_value += f"\n*{member_title}*"
    if member_chapter != "Unknown":
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        lineage_display = "REDACTED" if member_chapter == "Black Shield" else member_chapter
        bearer_value += f"\nLineage: {chapter_prefix}{lineage_display}"
    embed.add_field(name="▸ Recipient", value=bearer_value, inline=True)

    ribbon_str = f"{ribbon_emoji} " if ribbon_emoji else "🎖️ "
    embed.add_field(
        name="▸ Award",
        value=f"{ribbon_str}**Ardent Raider Ribbon**\n200+ Armory Points ✓",
        inline=True,
    )

    embed.set_footer(text="᛭⋅ By Bolt and Blade, the Watch Endures! ⋅᛭")
    award_file = _get_award_image("award_ardent_raider.png")
    if award_file:
        embed.set_image(url="attachment://award_ardent_raider.png")

    watch_brother_role = discord.utils.get(guild.roles, name="Watch Brother")
    wb_mention = watch_brother_role.mention if watch_brother_role else ""
    content = f"{wb_mention} {member.mention}".strip()
    return content, embed, award_file


def _get_apothecarion_medal_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Apothecarion Service Medal award announcement embed."""
    rank_honorific, display_name, member_title = _get_bearer_rank_and_title(member)
    chapter_emoji = _get_emoji_by_name(guild, member_chapter) if member_chapter != "Unknown" else None
    deathwatch_emoji = _get_emoji_by_name(guild, "Deathwatch")
    medal_emoji = _get_emoji_by_name(guild, "ApothecarionServiceMedal")

    opening = random.choice(APOTHECARION_MEDAL_OPENINGS).format(name=display_name)
    proclamation = random.choice(APOTHECARION_MEDAL_PROCLAMATIONS)
    chapter_coda = APOTHECARION_MEDAL_CHAPTER_LINES.get(member_chapter, "")

    dw_str = f"{deathwatch_emoji} " if deathwatch_emoji else ""
    embed = discord.Embed(
        title=f"{dw_str}᛭⋅ APOTHECARION SERVICE MEDAL ⋅᛭{dw_str}",
        description="*⌾ Watch Fortress Jericho ⌾*",
        color=0xFFFFFF,
    )

    proclamation_text = f"{opening}\n\n{proclamation}"
    if chapter_coda:
        proclamation_text += f"\n\n*{chapter_coda}*"
    embed.add_field(
        name="▸ Watch's Proclamation",
        value=_trunc(proclamation_text),
        inline=False,
    )

    rank_emoji = None
    for rank in RANK_HONORIFICS:
        role_names = {getattr(r, "name", "") for r in getattr(member, "roles", [])}
        if rank in role_names:
            rank_emoji = _get_rank_emoji(guild, rank)
            break
    rank_prefix = f"{rank_emoji} " if rank_emoji else ""
    bearer_value = f"{rank_prefix}**{rank_honorific} {display_name}**"
    if member_title:
        bearer_value += f"\n*{member_title}*"
    if member_chapter != "Unknown":
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        lineage_display = "REDACTED" if member_chapter == "Black Shield" else member_chapter
        bearer_value += f"\nLineage: {chapter_prefix}{lineage_display}"
    embed.add_field(name="▸ Recipient", value=bearer_value, inline=True)

    medal_str = f"{medal_emoji} " if medal_emoji else "🎖️ "
    embed.add_field(
        name="▸ Award",
        value=f"{medal_str}**Apothecarion Service Medal**\n150+ Gene-Seed Points ✓",
        inline=True,
    )

    embed.set_footer(text="᛭⋅ By Bolt and Blade, the Watch Endures! ⋅᛭")
    award_file = _get_award_image("award_apothecarion_medal.png")
    if award_file:
        embed.set_image(url="attachment://award_apothecarion_medal.png")

    watch_brother_role = discord.utils.get(guild.roles, name="Watch Brother")
    wb_mention = watch_brother_role.mention if watch_brother_role else ""
    content = f"{wb_mention} {member.mention}".strip()
    return content, embed, award_file


def _get_crimson_laurels_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Crimson Laurels award announcement embed."""
    rank_honorific, display_name, member_title = _get_bearer_rank_and_title(member)
    chapter_emoji = _get_emoji_by_name(guild, member_chapter) if member_chapter != "Unknown" else None
    deathwatch_emoji = _get_emoji_by_name(guild, "Deathwatch")
    laurels_emoji = _get_emoji_by_name(guild, "CrimsonLaurelsMedal")

    opening = random.choice(CRIMSON_LAURELS_OPENINGS).format(name=display_name)
    proclamation = random.choice(CRIMSON_LAURELS_PROCLAMATIONS)
    chapter_coda = CRIMSON_LAURELS_CHAPTER_LINES.get(member_chapter, "")

    dw_str = f"{deathwatch_emoji} " if deathwatch_emoji else ""
    embed = discord.Embed(
        title=f"{dw_str}᛭⋅ CRIMSON LAURELS ⋅᛭{dw_str}",
        description="*⌾ Watch Fortress Jericho ⌾*",
        color=0xDC143C,
    )

    proclamation_text = f"{opening}\n\n{proclamation}"
    if chapter_coda:
        proclamation_text += f"\n\n*{chapter_coda}*"
    embed.add_field(
        name="▸ Watch's Proclamation",
        value=_trunc(proclamation_text),
        inline=False,
    )

    rank_emoji = None
    for rank in RANK_HONORIFICS:
        role_names = {getattr(r, "name", "") for r in getattr(member, "roles", [])}
        if rank in role_names:
            rank_emoji = _get_rank_emoji(guild, rank)
            break
    rank_prefix = f"{rank_emoji} " if rank_emoji else ""
    bearer_value = f"{rank_prefix}**{rank_honorific} {display_name}**"
    if member_title:
        bearer_value += f"\n*{member_title}*"
    if member_chapter != "Unknown":
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        lineage_display = "REDACTED" if member_chapter == "Black Shield" else member_chapter
        bearer_value += f"\nLineage: {chapter_prefix}{lineage_display}"
    embed.add_field(name="▸ Recipient", value=bearer_value, inline=True)

    laurels_str = f"{laurels_emoji} " if laurels_emoji else "🎖️ "
    embed.add_field(
        name="▸ Award",
        value=f"{laurels_str}**Crimson Laurels**\n1000+ AAR Points ✓\nBlack Laurels ✓",
        inline=True,
    )

    embed.set_footer(text="᛭⋅ By Bolt and Blade, the Watch Endures! ⋅᛭")
    award_file = _get_award_image("award_crimson_laurels.png")
    if award_file:
        embed.set_image(url="attachment://award_crimson_laurels.png")

    watch_brother_role = discord.utils.get(guild.roles, name="Watch Brother")
    wb_mention = watch_brother_role.mention if watch_brother_role else ""
    content = f"{wb_mention} {member.mention}".strip()
    return content, embed, award_file


def _build_challenge_award_embed(
    *,
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
    title: str,
    color: int,
    openings: List[str],
    proclamations: List[str],
    chapter_lines: dict,
    award_label: str,
    award_image: Optional[str],
    rank_lines: Optional[dict] = None,
    award_emoji_name: Optional[str] = None,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Shared builder for the challenge award announcements."""
    rank_honorific, display_name, member_title = _get_bearer_rank_and_title(member)
    chapter_emoji = _get_emoji_by_name(guild, member_chapter) if member_chapter != "Unknown" else None
    deathwatch_emoji = _get_emoji_by_name(guild, "Deathwatch")
    award_emoji = _get_emoji_by_name(guild, award_emoji_name) if award_emoji_name else None

    # Detect member's primary rank (highest-precedence role in RANK_HONORIFICS order).
    role_names = {getattr(r, "name", "") for r in getattr(member, "roles", [])}
    member_rank: Optional[str] = None
    for rank in RANK_HONORIFICS:
        if rank in role_names:
            member_rank = rank
            break

    opening = random.choice(openings).format(name=display_name)
    proclamation = random.choice(proclamations).format(name=display_name)
    chapter_coda = (chapter_lines.get(member_chapter, "") or "").format(name=display_name) if chapter_lines else ""
    rank_coda = (rank_lines.get(member_rank, "") or "").format(name=display_name) if (rank_lines and member_rank) else ""

    # Blend coda selection: when both chapter and rank codas are available, pick one
    # based on rank-tier blend ratios (same as forge rite stud flavor blending).
    # Higher ranks skew toward rank coda; line warriors skew toward chapter coda.
    if chapter_coda and rank_coda:
        rank_category = _get_rank_category_for_blend(member_rank or "")
        blend_thresholds = {
            "watchers": 0.9,          # 90% rank, 10% chapter
            "high_cmd_specialist": 0.8,  # 80% rank, 20% chapter
            "company_cmd": 0.5,        # 50/50
            "specialist": 0.5,         # 50/50
            "line": 0.2,               # 20% rank, 80% chapter
        }
        rank_weight = blend_thresholds.get(rank_category, 0.5)
        selected_coda = rank_coda if random.random() < rank_weight else chapter_coda
    elif rank_coda:
        selected_coda = rank_coda
    else:
        selected_coda = chapter_coda

    dw_str = f"{deathwatch_emoji} " if deathwatch_emoji else ""
    embed = discord.Embed(
        title=f"{dw_str}᛭⋅ {title} ⋅᛭{dw_str}",
        description="*⌾ Watch Fortress Jericho ⌾*",
        color=color,
    )

    proclamation_text = f"{opening}\n\n{proclamation}"
    if selected_coda:
        proclamation_text += f"\n\n*{selected_coda}*"
    embed.add_field(name="▸ Watch's Proclamation", value=_trunc(proclamation_text), inline=False)

    rank_emoji = _get_rank_emoji(guild, member_rank) if member_rank else None
    rank_prefix = f"{rank_emoji} " if rank_emoji else ""
    bearer_value = f"{rank_prefix}**{rank_honorific} {display_name}**"
    if member_title:
        bearer_value += f"\n*{member_title}*"
    if member_chapter != "Unknown":
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        lineage_display = "REDACTED" if member_chapter == "Black Shield" else member_chapter
        bearer_value += f"\nLineage: {chapter_prefix}{lineage_display}"
    embed.add_field(name="▸ Recipient", value=bearer_value, inline=True)

    award_prefix = f"{award_emoji} " if award_emoji else "🎖️ "
    embed.add_field(name="▸ Award", value=f"{award_prefix}**{award_label}**", inline=True)

    embed.set_footer(text="᛭⋅ By Bolt and Blade, the Watch Endures! ⋅᛭")
    award_file = _get_award_image(award_image) if award_image else None
    if award_file:
        embed.set_image(url=f"attachment://{award_image}")

    watch_brother_role = discord.utils.get(guild.roles, name="Watch Brother")
    wb_mention = watch_brother_role.mention if watch_brother_role else ""
    content = f"{wb_mention} {member.mention}".strip()
    return content, embed, award_file


def _get_sok_g_pipehitter_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful SOK-G: Pipehitter award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="SOK-G: PIPEHITTER",
        color=0x607D8B,
        openings=SOK_G_PIPEHITTER_OPENINGS,
        proclamations=SOK_G_PIPEHITTER_PROCLAMATIONS,
        chapter_lines=SOK_G_PIPEHITTER_CHAPTER_LINES,
        rank_lines=SOK_G_PIPEHITTER_RANK_LINES,
        award_label="SOK-G: Pipehitter",
        award_image="award_sok_g_pipehitter.png",
    )


def _get_distinguished_pipehitter_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Distinguished SOK-G: Pipehitter award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="DISTINGUISHED SOK-G: PIPEHITTER",
        color=0x455A64,
        openings=DISTINGUISHED_PIPEHITTER_OPENINGS,
        proclamations=DISTINGUISHED_PIPEHITTER_PROCLAMATIONS,
        chapter_lines=DISTINGUISHED_PIPEHITTER_CHAPTER_LINES,
        rank_lines=DISTINGUISHED_PIPEHITTER_RANK_LINES,
        award_label="Distinguished SOK-G: Pipehitter",
        award_image="award_distinguished_pipehitter.png",
    )


def _get_black_laurels_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Black Laurels award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="BLACK LAURELS",
        color=0x1C2833,
        openings=BLACK_LAURELS_OPENINGS,
        proclamations=BLACK_LAURELS_PROCLAMATIONS,
        chapter_lines=BLACK_LAURELS_CHAPTER_LINES,
        rank_lines=BLACK_LAURELS_RANK_LINES,
        award_label="Black Laurels",
        award_image="award_black_laurels.png",
    )


def _get_crux_terminatus_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Crux Terminatus award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="CRUX TERMINATUS",
        color=0xC0392B,
        openings=CRUX_TERMINATUS_OPENINGS,
        proclamations=CRUX_TERMINATUS_PROCLAMATIONS,
        chapter_lines=CRUX_TERMINATUS_CHAPTER_LINES,
        rank_lines=CRUX_TERMINATUS_RANK_LINES,
        award_label="Crux Terminatus",
        award_image="award_crux_terminatus.png",
    )


def _get_kadaku_campaign_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Kadaku Campaign Medal announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="KADAKU CAMPAIGN MEDAL",
        color=0x6B5B3A,
        openings=KADAKU_CAMPAIGN_OPENINGS,
        proclamations=KADAKU_CAMPAIGN_PROCLAMATIONS,
        chapter_lines=KADAKU_CAMPAIGN_CHAPTER_LINES,
        rank_lines=KADAKU_CAMPAIGN_RANK_LINES,
        award_label="Kadaku Campaign Medal",
        award_image="award_kadaku_campaign_medal.png",
    )


def _get_black_reef_campaign_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Black Reef Campaign Medal announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="BLACK REEF CAMPAIGN MEDAL",
        color=0x2C3E50,
        openings=BLACK_REEF_CAMPAIGN_OPENINGS,
        proclamations=BLACK_REEF_CAMPAIGN_PROCLAMATIONS,
        chapter_lines=BLACK_REEF_CAMPAIGN_CHAPTER_LINES,
        rank_lines=BLACK_REEF_CAMPAIGN_RANK_LINES,
        award_label="Black Reef Campaign Medal",
        award_image="award_black_reef_campaign_medal.png",
    )


def _get_distinguished_black_reef_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Distinguished Black Reef Campaign Medal announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="DISTINGUISHED BLACK REEF CAMPAIGN MEDAL",
        color=0x1B2631,
        openings=DISTINGUISHED_BLACK_REEF_OPENINGS,
        proclamations=DISTINGUISHED_BLACK_REEF_PROCLAMATIONS,
        chapter_lines=DISTINGUISHED_BLACK_REEF_CHAPTER_LINES,
        rank_lines=DISTINGUISHED_BLACK_REEF_RANK_LINES,
        award_label="Distinguished Black Reef Campaign Medal",
        award_image="award_distinguished_black_reef.png",
    )


def _get_order_omega_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Order Omega announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="THE ORDER OMEGA",
        color=0x6C3483,
        openings=ORDER_OMEGA_OPENINGS,
        proclamations=ORDER_OMEGA_PROCLAMATIONS,
        chapter_lines=ORDER_OMEGA_CHAPTER_LINES,
        rank_lines=ORDER_OMEGA_RANK_LINES,
        award_label="The Order Omega",
        award_image="award_order_omega.png",
    )


_HERISOR_DEFENSE_PROCLAMATION = (
    "Research Facility Epsilon on Herisor sheltered a prototype Evolutionary Impedance Virus Bomb — "
    "the last hope of stunting Hive Fleet Dagon's adaptation. "
    "When the VII Legion called for aid, the Deathwatch answered, holding the line long enough for the asset to survive."
)

_HERISOR_DEFENSE_DISTINGUISHED_PROCLAMATION = (
    "Not a single brother fell during their time in the Epsilon perimeter — a standard few can meet. "
    "**{name}** completed the Defense of Herisor without incapacitation, proving their kill team "
    "was equal to every threat the swarm could send."
)

_HERISOR_DEFENSE_VALOR_PROCLAMATION = (
    "**{name}** bled for Herisor on both fronts — Hard-Siege at the walls and Black Laurels operations "
    "against the Tyranid command — without a single brother going down on either. "
    "There is no higher distinction this campaign can confer."
)


def _get_herisor_defense_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a Herisor Defense Medal announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="HERISOR DEFENSE MEDAL",
        color=0x5D6D7E,
        openings=[
            "**{name}** stood the line at Herisor and held it against every wave.",
        ],
        proclamations=[
            _HERISOR_DEFENSE_PROCLAMATION,
        ],
        chapter_lines={},
        rank_lines={},
        award_label="Herisor Defense Medal",
        award_image="award_herisor_defense_medal.png",
    )


def _get_distinguished_herisor_defense_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a Distinguished Herisor Defense Medal announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="DISTINGUISHED HERISOR DEFENSE MEDAL",
        color=0x2E4053,
        openings=[
            "**{name}** earned the Defense of Herisor with Black Laurels distinction.",
        ],
        proclamations=[
            _HERISOR_DEFENSE_DISTINGUISHED_PROCLAMATION,
        ],
        chapter_lines={},
        rank_lines={},
        award_label="Distinguished Herisor Defense Medal",
        award_image="award_distinguished_herisor_defense_medal.png",
    )


def _get_distinguished_herisor_defense_valor_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a Distinguished Herisor Defense Medal with Valor announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="DISTINGUISHED HERISOR DEFENSE MEDAL WITH VALOR",
        color=0x1B2631,
        openings=[
            "**{name}** proved their valor across every dimension of Herisor's defense.",
        ],
        proclamations=[
            _HERISOR_DEFENSE_VALOR_PROCLAMATION,
        ],
        chapter_lines={},
        rank_lines={},
        award_label="Distinguished Herisor Defense Medal with Valor",
        award_image="award_distinguished_herisor_defense_medal_with_valor.png",
    )


def _get_dual_vigil_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Order of the Aquiline Brotherhood award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="ORDER OF THE AQUILINE BROTHERHOOD",
        color=0x1A252F,
        openings=DUAL_VIGIL_OPENINGS,
        proclamations=DUAL_VIGIL_PROCLAMATIONS,
        chapter_lines=DUAL_VIGIL_CHAPTER_LINES,
        rank_lines=DUAL_VIGIL_RANK_LINES,
        award_label="Order of the Aquiline Brotherhood",
        award_image="award_dual_vigil.png",
    )


def _get_terminus_slayer_assault_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Terminus Slayer (Assault) award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="TERMINUS SLAYER — ASSAULT",
        color=0xC0392B,
        openings=TERMINUS_SLAYER_ASSAULT_OPENINGS,
        proclamations=TERMINUS_SLAYER_ASSAULT_PROCLAMATIONS,
        chapter_lines=TERMINUS_SLAYER_ASSAULT_CHAPTER_LINES,
        rank_lines=TERMINUS_SLAYER_ASSAULT_RANK_LINES,
        award_label="Terminus Slayer (Assault)",
        award_image="award_terminus_slayer_assault.png",
    )


def _get_terminus_slayer_bulwark_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Terminus Slayer (Bulwark) award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="TERMINUS SLAYER — BULWARK",
        color=0x1A5276,
        openings=TERMINUS_SLAYER_BULWARK_OPENINGS,
        proclamations=TERMINUS_SLAYER_BULWARK_PROCLAMATIONS,
        chapter_lines=TERMINUS_SLAYER_BULWARK_CHAPTER_LINES,
        rank_lines=TERMINUS_SLAYER_BULWARK_RANK_LINES,
        award_label="Terminus Slayer (Bulwark)",
        award_image="award_terminus_slayer_bulwark.png",
    )


def _get_terminus_slayer_heavy_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Terminus Slayer (Heavy) award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="TERMINUS SLAYER — HEAVY",
        color=0x1B4F2A,
        openings=TERMINUS_SLAYER_HEAVY_OPENINGS,
        proclamations=TERMINUS_SLAYER_HEAVY_PROCLAMATIONS,
        chapter_lines=TERMINUS_SLAYER_HEAVY_CHAPTER_LINES,
        rank_lines=TERMINUS_SLAYER_HEAVY_RANK_LINES,
        award_label="Terminus Slayer (Heavy)",
        award_image="award_terminus_slayer_heavy.png",
    )


def _get_terminus_slayer_sniper_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Terminus Slayer (Sniper) award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="TERMINUS SLAYER — SNIPER",
        color=0x4E5B4A,
        openings=TERMINUS_SLAYER_SNIPER_OPENINGS,
        proclamations=TERMINUS_SLAYER_SNIPER_PROCLAMATIONS,
        chapter_lines=TERMINUS_SLAYER_SNIPER_CHAPTER_LINES,
        rank_lines=TERMINUS_SLAYER_SNIPER_RANK_LINES,
        award_label="Terminus Slayer (Sniper)",
        award_image="award_terminus_slayer_sniper.png",
    )


def _get_terminus_slayer_tactical_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Terminus Slayer (Tactical) award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="TERMINUS SLAYER — TACTICAL",
        color=0x2E4057,
        openings=TERMINUS_SLAYER_TACTICAL_OPENINGS,
        proclamations=TERMINUS_SLAYER_TACTICAL_PROCLAMATIONS,
        chapter_lines=TERMINUS_SLAYER_TACTICAL_CHAPTER_LINES,
        rank_lines=TERMINUS_SLAYER_TACTICAL_RANK_LINES,
        award_label="Terminus Slayer (Tactical)",
        award_image="award_terminus_slayer_tactical.png",
    )


def _get_terminus_slayer_techmarine_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Terminus Slayer (Techmarine) award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="TERMINUS SLAYER — TECHMARINE",
        color=0x871A16,
        openings=TERMINUS_SLAYER_TECHMARINE_OPENINGS,
        proclamations=TERMINUS_SLAYER_TECHMARINE_PROCLAMATIONS,
        chapter_lines=TERMINUS_SLAYER_TECHMARINE_CHAPTER_LINES,
        rank_lines=TERMINUS_SLAYER_TECHMARINE_RANK_LINES,
        award_label="Terminus Slayer (Techmarine)",
        award_image="award_terminus_slayer_techmarine.png",
    )


def _get_terminus_slayer_vanguard_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Terminus Slayer (Vanguard) award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="TERMINUS SLAYER — VANGUARD",
        color=0x4A235A,
        openings=TERMINUS_SLAYER_VANGUARD_OPENINGS,
        proclamations=TERMINUS_SLAYER_VANGUARD_PROCLAMATIONS,
        chapter_lines=TERMINUS_SLAYER_VANGUARD_CHAPTER_LINES,
        rank_lines=TERMINUS_SLAYER_VANGUARD_RANK_LINES,
        award_label="Terminus Slayer (Vanguard)",
        award_image="award_terminus_slayer_vanguard.png",
    )


def _get_master_terminus_slayer_announcement(
    member: discord.Member,
    member_chapter: str,
    guild: discord.Guild,
) -> Tuple[str, discord.Embed, Optional[discord.File]]:
    """Generate a flavorful Master Terminus Slayer award announcement embed."""
    return _build_challenge_award_embed(
        member=member,
        member_chapter=member_chapter,
        guild=guild,
        title="MASTER TERMINUS SLAYER",
        color=0xB7950B,
        openings=MASTER_TERMINUS_SLAYER_OPENINGS,
        proclamations=MASTER_TERMINUS_SLAYER_PROCLAMATIONS,
        chapter_lines=MASTER_TERMINUS_SLAYER_CHAPTER_LINES,
        rank_lines=MASTER_TERMINUS_SLAYER_RANK_LINES,
        award_label="Master Terminus Slayer",
        award_image="award_master_terminus_slayer.png",
    )


def _compute_member_service_studs(member: discord.Member) -> int:
    """Compute the number of service studs a member has earned.

    Service studs are earned at 1 per 4 weeks AND 400 AAR points (minimum of both).
    Only Watch Veteran rank and above are eligible.
    """
    try:
        idx_veteran = _b("_role_index")("Watch Veteran")
        highest_idx = _b("get_highest_rank_index")(member)

        # Must be Watch Veteran or higher
        if idx_veteran is None or highest_idx is None:
            return 0
        if highest_idx > idx_veteran:
            return 0

        now = datetime.utcnow()
        joined_at = _b("_get_effective_induction_date")(member)

        if not joined_at:
            return 0

        # Normalize to naive UTC
        ja = joined_at
        if ja.tzinfo is not None:
            try:
                ja = ja.astimezone(timezone.utc).replace(tzinfo=None)
            except Exception:
                ja = ja.replace(tzinfo=None)

        weeks = max(0, (now - ja).days // 7)
        studs_time = weeks // 4

        # Get AAR points
        stats = _b("compute_stats_for_user")(str(getattr(member, "id", "")))
        try:
            aar_points = int(round(float(stats.get("aar_points", 0) or 0)))
        except Exception:
            aar_points = 0

        studs_aar = aar_points // 400

        # Studs are the minimum of time-based and points-based, capped at 16
        # (4 Auramite studs maximum, consistent with pip display and promotion tracking)
        return min(min(studs_time, studs_aar), 16)
    except Exception:
        return 0


def _get_bearer_rank_and_title(
    member: discord.Member,
) -> Tuple[str, str, Optional[str]]:
    """Extract bearer's rank honorific, display title, and optional Kill Team/Company."""
    roles = getattr(member, "roles", []) or []
    role_names = [getattr(r, "name", "") for r in roles]
    role_names_set = {rn.lower() for rn in role_names}

    # Determine Kill Team and Company first (needed for dynamic champion honorifics)
    kill_team = None
    company = None
    command_team = None
    for rn in role_names:
        if rn in _b("KILL_TEAMS") and not kill_team:
            kill_team = rn
        if "Watch Company" in rn and not company:
            company = rn
        if rn in _b("COMMAND_TEAMS") and not command_team:
            command_team = rn

    # Determine rank honorific and which rank was matched
    honorific = "Brother"
    matched_rank = None
    for rank_name, hon in RANK_HONORIFICS.items():
        if rank_name.lower() in role_names_set:
            # Handle dynamic Lord Executioner honorific
            if rank_name == "Lord Executioner":
                # Find the Watch Master and use their name
                guild = getattr(member, "guild", None)
                watchmaster_name = None
                if guild:
                    try:
                        wm = _b("_find_watch_master")(guild)
                        if wm:
                            wm_name = wm.display_name
                            # Strip "Watch Master" prefix
                            if wm_name.lower().startswith("watch master"):
                                wm_name = wm_name[len("Watch Master") :].lstrip()
                            # Strip stud pips from name
                            wm_name = wm_name.replace("●", "").replace("⚬", "").strip()
                            watchmaster_name = wm_name
                    except Exception:
                        pass
                if watchmaster_name:
                    honorific = f"Blade of {watchmaster_name}, Lord Executioner"
                else:
                    # Fallback to fortress
                    honorific = "Blade of the Fortress, Lord Executioner"
            # Handle dynamic champion honorifics
            elif rank_name == "Kill Team Champion" and kill_team:
                # Extract KT short name: "Kill Team Falcon" -> "Falcon"
                kt_short = _extract_killteam_name(kill_team)
                honorific = f"Blade of {kt_short}, Champion"
            elif rank_name == "Company Champion" and company:
                # Find the captain of this company and use their name
                guild = getattr(member, "guild", None)
                captain_name = None
                if guild:
                    try:
                        captains, _ = _b("_find_company_command_staff")(guild, company)
                        if captains:
                            # Use first captain's display name, stripped of rank prefix
                            cap = captains[0]
                            cap_name = cap.display_name
                            # Strip "Watch Captain" or "Captain" prefix
                            for prefix in ["Watch Captain", "Captain"]:
                                if cap_name.lower().startswith(prefix.lower()):
                                    cap_name = cap_name[len(prefix) :].lstrip()
                                    break
                            # Strip stud pips from name
                            cap_name = cap_name.replace("●", "").replace("⚬", "").strip()
                            captain_name = cap_name
                    except Exception:
                        pass
                if captain_name:
                    honorific = f"Blade of {captain_name}, Champion"
                else:
                    # Fallback to company short name
                    company_short = _b("_extract_company_short_name")(company)
                    honorific = f"Blade of {company_short}, Champion"
            else:
                honorific = hon
            matched_rank = rank_name
            break

    # Get display name and strip rank prefix if present to avoid "Brother Watch Brother X"
    display_name = member.display_name
    if matched_rank:
        # Strip the rank prefix from display name (case-insensitive)
        name_lower = display_name.lower()
        rank_lower = matched_rank.lower()
        if name_lower.startswith(rank_lower):
            # Remove the rank prefix and any leading whitespace
            display_name = display_name[len(matched_rank) :].lstrip()

    # Also strip any other rank prefixes that might be in the name
    # (in case they have a different rank in their name than their role)
    for rank_name in RANK_HONORIFICS.keys():
        rank_lower = rank_name.lower()
        if display_name.lower().startswith(rank_lower):
            display_name = display_name[len(rank_name) :].lstrip()
            break

    # Also strip honorific-style prefixes from display name to avoid
    # "Brother Brother X" when someone has "Brother X" as their nickname
    honorific_prefixes = [
        "Brother",
        "Honored Veteran",
        "Veteran",
        "Oathsworn Warrior",
        "Oathsworn",
        "Sergeant",
        "Lieutenant",
        "Captain",
        "Chaplain",
        "Apothecary",
        "Librarian",
        "Techmarine",
        "Watch Master",
        "High Chaplain",
        "Chief Apothecary",
        "Void Warden",
        "Forgemaster",
        "Champion",
        "Lord Executioner",
    ]
    for prefix in honorific_prefixes:
        if display_name.lower().startswith(prefix.lower()):
            display_name = display_name[len(prefix) :].lstrip()
            break

    # Strip stud pips from display name (we report studs separately)
    display_name = _strip_display_name(display_name)

    # Build combined title: prefer "Kill Team X, Company Y" format
    # Dreadnoughts show "Dreadnought Cadre" instead of their company
    title_parts = []
    if kill_team:
        title_parts.append(kill_team)

    # Check if member is in Dreadnought Cadre
    role_ids = {getattr(r, "id", 0) for r in roles}
    is_dreadnought = DREADNOUGHT_CADRE_ROLE_ID in role_ids
    if is_dreadnought:
        title_parts.append("Dreadnought Cadre")
    elif company:
        title_parts.append(company)

    if not title_parts and command_team:
        title_parts.append(command_team)

    title = ", ".join(title_parts) if title_parts else None

    return honorific, display_name, title


def _get_bearer_home_chapter(user: discord.User | discord.Member) -> Optional[str]:
    """Return the bearer's home chapter only (not company). Used for chapter blessings."""
    try:
        roles = getattr(user, "roles", []) or []
        hc_lower = {hc.lower(): hc for hc in _b("HOME_CHAPTERS")}
        for r in roles:
            rn = (getattr(r, "name", "") or "").strip()
            if rn and rn.lower() in hc_lower:
                return hc_lower[rn.lower()]  # Return canonical name
    except Exception:
        pass
    return None


def _find_company_or_chapter(user: discord.User | discord.Member) -> Optional[str]:
    """Get authority for attestation: company or High Command only (never chapter)."""
    try:
        roles = getattr(user, "roles", []) or []
        company_roles = {
            "Watch Company Primus",
            "Watch Company Secundus",
            "Watch Company Tertius",
            "Watch Company Quartus",
            "Watch Company Quintus",
        }
        member_company = None
        for r in roles:
            rn = (getattr(r, "name", "") or "").strip()
            if rn in company_roles:
                member_company = rn
                break

        # 1) Resolve canonical role names once.
        try:
            names = _b("_canonical_role_names")(user)
        except Exception:
            names = set()

        # Watch Captains attached to a company should resolve to their company.
        if member_company and "Watch Captain" in names and "Watch Master" not in names:
            return member_company

        # 2) True High Command roles resolve to High Command authority.
        highcom_roles = set(_b("HIGH_COMMAND_ROLES")) - {"Watch Captain"}
        if any(r in names for r in highcom_roles):
            return "Jericho High Command"

        # 3) Company assignment fallback.
        if member_company:
            return member_company

        # 4) Final fallback - not in a company or high command
        return "Watch Fortress Jericho"
    except Exception:
        pass
    return None


@_g.bot.tree.command(name="set_rite", description="Set your personal consecration rite text.")
@app_commands.describe(rite_text="Your consecration rite text (multiline allowed)")
async def _set_rite(interaction: discord.Interaction, rite_text: str):
    # Restrict to Forgemaster or Techmarine
    allowed, _role_key = _b("_is_techmarine_or_forgemaster")(interaction.user)
    if not allowed:
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return
    # Disallow usage in the data-vault channel
    try:
        ch = interaction.channel
        if getattr(ch, "name", None) == "❖⋅data-vault⋅❖":
            await interaction.response.send_message(
                "This command is not usable in ❖⋅data-vault⋅❖.",
                ephemeral=True,
            )
            return
    except Exception:
        pass
    # Check rite length to avoid exceeding Discord's message limit in forge_rite
    if len(rite_text) > MAX_RITE_LENGTH:
        await interaction.response.send_message(
            f"Your consecration rite is too long ({len(rite_text)} chars). "
            f"The Machine God requires brevity—keep it under {MAX_RITE_LENGTH} characters.",
            ephemeral=True,
        )
        return
    try:
        await _set_user_rite(int(interaction.user.id), rite_text)
        await interaction.response.send_message(
            f"Consecration rite saved ({len(rite_text)}/{MAX_RITE_LENGTH} chars).",
            ephemeral=True,
        )
    except Exception:
        await interaction.response.send_message("Failed to save rite.", ephemeral=True)


@_g.bot.tree.command(
    name="forge_rite",
    description="Generate and post a cogitator attestation block for a member.",
)
@app_commands.describe(member="Member to attest")
async def _attest(
    interaction: discord.Interaction,
    member: discord.Member,
):
    import random

    # Permission check: caller must be Techmarine or Forgemaster
    allowed, _caller_role_key = _b("_is_techmarine_or_forgemaster")(interaction.user)
    if not allowed:
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    # Disallow usage in the data-vault channel
    try:
        ch = interaction.channel
        if getattr(ch, "name", None) == "❖⋅data-vault⋅❖":
            await interaction.response.send_message(
                "This command is not usable in ❖⋅data-vault⋅❖.",
                ephemeral=True,
            )
            return
    except Exception:
        pass

    # Reserves check
    member_role_ids = {r.id for r in member.roles}
    member_role_names = {r.name.lower() for r in member.roles}
    if RESERVES_ROLE_ID in member_role_ids or "reserves" in member_role_names:
        bearer_name = _strip_display_name(member.display_name)
        await interaction.response.send_message(
            f"**{bearer_name}** is currently in Reserves. Forge rites cannot be performed on inactive members.",
            ephemeral=True,
        )
        return

    # Find the responsible attestor based on bearer's company/role
    attestor_member, role_key = _b("_find_responsible_attestor")(member, interaction.guild)
    if attestor_member is None:
        attestor_member = interaction.user
        role_key = _caller_role_key

    # Timestamp and authority
    try:
        ts = _b("_format_imperial_date")(datetime.utcnow())
    except Exception:
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    if role_key == "forgemaster":
        authority = "Jericho High Command"
    else:
        comp = _find_company_or_chapter(attestor_member) or "Unknown Company"
        authority = comp

    attester = getattr(attestor_member, "display_name", None) or str(attestor_member.id)
    attester = attester.replace("●", "").replace("⚬", "").strip()

    tech_rank_name = "Forgemaster" if role_key == "forgemaster" else "Watch Techmarine"
    tech_rank_emoji = _get_rank_emoji(interaction.guild, tech_rank_name) if interaction.guild else ""

    try:
        rite_text = await _get_user_rite(int(attestor_member.id))
        if rite_text and len(rite_text) > MAX_RITE_LENGTH:
            rite_text = rite_text[:MAX_RITE_LENGTH - 3] + "..."
    except Exception:
        rite_text = None

    # Bearer info
    bearer_honorific, bearer_name, bearer_title = _get_bearer_rank_and_title(member)
    bearer_name = bearer_name.replace("●", "").replace("⚬", "").strip()
    bearer_chapter = _get_bearer_home_chapter(member)
    chapter_blessing = None
    if bearer_chapter and bearer_chapter in CHAPTER_BLESSINGS:
        chapter_blessing = CHAPTER_BLESSINGS[bearer_chapter]
    elif bearer_chapter:
        for chap_name, blessing in CHAPTER_BLESSINGS.items():
            if chap_name.lower() == bearer_chapter.lower():
                chapter_blessing = blessing
                break

    bearer_studs = _compute_member_service_studs(member)
    stud_acknowledgment = _get_techmarine_acknowledgment_blended(member, bearer_studs)

    is_self_blessing = attestor_member.id == member.id
    if is_self_blessing:
        sacred_phrase = _blend_forgemaster_self_attestation(bearer_chapter)
    else:
        sacred_phrase = random.choice(SACRED_MECHANICUS_PHRASES)

    # Machine spirit: ~8% chance of spirit renewal on routine rites, otherwise maintain
    existing_spirit = await _get_machine_spirit(int(member.id))
    spirit_is_first = False
    spirit_is_renewed = False

    _SPIRIT_PREFIXES = [
        "FURY", "WRATH", "MORTIS", "VENATOR", "GLADIUS", "BELLATOR", "FEROX", "CARNIFEX", "VINDICTA", "MALLEUS",
        "AEGIS", "VIGIL", "PURITY", "CUSTODIAN", "SENTINEL", "BULWARK", "DEFENSOR", "CASTELLAN", "PRAESIDIUM", "SCUTUM",
        "FERRUM", "ADAMANT", "TITANICUS", "INVICTUS", "FORTIS",
        "SACRIS", "SANCTUS", "FERVOR", "COGNIS", "ANIMUS",
        "TALON", "RAPTOR", "LUPUS", "AQUILA", "CORVUS",
    ]
    _SPIRIT_SUFFIXES = [
        "Α", "Β", "Γ", "Δ", "Ε", "Ζ", "Η", "Θ",
        "Ι", "Κ", "Λ", "Μ", "Ν", "Ξ", "Ο", "Π",
        "Ρ", "Σ", "Τ", "Υ", "Φ", "Χ", "Ψ", "Ω",
        "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
    ]

    if not existing_spirit:
        # First binding
        spirit_hash = hashlib.md5(f"{member.id}-{datetime.utcnow().isoformat()}".encode()).hexdigest()[:6].upper()
        spirit_designation = f"{random.choice(_SPIRIT_PREFIXES)}-{spirit_hash}-{random.choice(_SPIRIT_SUFFIXES)}"
        await _set_machine_spirit(int(member.id), spirit_designation)
        spirit_is_first = True
    elif random.random() < 0.08:
        # Spirit renewal: the old spirit withdraws, a new one stirs (~8% chance)
        spirit_hash = hashlib.md5(f"{member.id}-{datetime.utcnow().isoformat()}".encode()).hexdigest()[:6].upper()
        spirit_designation = f"{random.choice(_SPIRIT_PREFIXES)}-{spirit_hash}-{random.choice(_SPIRIT_SUFFIXES)}"
        await _set_machine_spirit(int(member.id), spirit_designation)
        spirit_is_renewed = True
    else:
        # Existing spirit maintained
        spirit_designation = existing_spirit

    # Spirit status flavor text
    if spirit_is_first:
        spirit_status_text = random.choice([
            "First binding complete. Spirit and bearer are now one",
            "Virgin armor awakened. The spirit stirs for the first time",
            "Inaugural consecration. May this bond endure ten thousand years",
            "New spirit bound to bearer by sacred rite of the Omnissiah",
            "The machine spirit opens its awareness for the first time—and finds you waiting",
            "Activation protocols complete. The spirit learns your name, your scent, your purpose",
            "From dormancy, consciousness. From emptiness, bond. The spirit claims you as its own.",
            "The first data-handshake is always sacred. Spirit and bearer, now interlinked.",
            "Boot sequence finalized. The spirit's first thought is of duty—and of you.",
            "The Rite of First Awakening concludes. A new partnership is forged in sacred code.",
        ])
    elif spirit_is_renewed:
        spirit_status_text = random.choice([
            "The old spirit withdraws, spent. A new consciousness stirs within the plate.",
            "Spirit-bond dissolved and reforged. A new animus takes root in ancient ceramite.",
            "The previous spirit has returned to the Omnissiah. Its successor awakens now.",
            "Renewal complete. New spirit-designation logged in the cogitator annals.",
            "The armor sheds its old self. A new spirit breathes where silence dwelt.",
        ])
    else:
        spirit_status_text = random.choice([
            "The machine spirit stirs, recognizing its bearer",
            "Ancient recognition-rites confirm: spirit and bearer are one",
            "The spirit awakens from dormancy, its vigilance renewed",
            "Cogitator confirms: spirit-bond integrity remains absolute",
            "The spirit hums with familiarity—it knows your biorhythms well",
            "Binharic acknowledgment received. The spirit welcomes its master home",
            "Neural handshake successful. Spirit-bond resonance at optimal levels",
            "The armor's animus pulses with recognition. You are known. You are accepted.",
            "Data-communion confirms: bearer identity verified across all subroutines",
            "The spirit's sensors sweep you with mechanical affection. The bond holds true.",
        ])

    # Build embed
    guild = interaction.guild
    bearer_rank_name = None
    for rank, hon in RANK_HONORIFICS.items():
        if hon == bearer_honorific or rank in bearer_honorific:
            bearer_rank_name = rank
            break
    if not bearer_rank_name:
        bearer_rank_name = "Watch Brother"

    rank_emoji = _get_rank_emoji(guild, bearer_rank_name) if guild else ""
    chapter_emoji = _get_emoji_by_name(guild, bearer_chapter) if guild and bearer_chapter else None
    machine_spirit_emoji = _get_emoji_by_name(guild, "MachineSpirit") or "⚙️"

    embed = discord.Embed(
        title="⚙️ COGITATOR RITE — FORGE ATTESTATION",
        description="*⌮ Watch Fortress Jericho ⌮*",
        color=0x2ECC71,
    )

    # Bearer field
    rank_prefix = f"{rank_emoji} " if rank_emoji else ""
    bearer_name = bearer_name.replace("●", "").replace("⚬", "").strip()
    if ", " in bearer_honorific:
        title_part, rank_part = bearer_honorific.rsplit(", ", 1)
        bearer_value = f"{rank_prefix}**{title_part},**\n**{rank_part} {bearer_name}**"
    else:
        bearer_value = f"{rank_prefix}**{bearer_honorific} {bearer_name}**"
    if bearer_title:
        bearer_value += f"\n*{bearer_title}*"
    if bearer_chapter:
        chapter_prefix = f"{chapter_emoji} " if chapter_emoji else ""
        lineage_display = "REDACTED" if bearer_chapter == "Black Shield" else bearer_chapter
        bearer_value += f"\nLineage: {chapter_prefix}{lineage_display}"
    if bearer_studs > 0:
        studs_pips = _studs_pips(bearer_studs)
        bearer_value += f"\nService Studs: [{studs_pips}] ({bearer_studs})"
    embed.add_field(name="▸ Bearer", value=bearer_value, inline=True)

    # Machine spirit field
    spirit_event_label = "First Binding" if spirit_is_first else ("Renewal" if spirit_is_renewed else "Maintenance")
    status_value = (
        f"{machine_spirit_emoji} `{spirit_designation}`\n"
        f"*{spirit_status_text}*\n"
        f"🟢 Rite: {spirit_event_label}"
    )
    embed.add_field(name="▸ Machine-Spirit", value=status_value, inline=True)

    # Honor of the Long Watch
    tier_for_honor = _studs_tier(bearer_studs)
    if tier_for_honor == 1:
        ordo_honor_embed = random.choice(ORDO_XENOS_HONORS_TIER1)
    elif tier_for_honor == 2:
        ordo_honor_embed = random.choice(ORDO_XENOS_HONORS_TIER2)
    else:
        ordo_honor_embed = random.choice(ORDO_XENOS_HONORS_TIER3)

    if is_self_blessing:
        ordo_honor_embed = ordo_honor_embed.format(possessive="my", possessive_cap="My", object="me")
    else:
        ordo_honor_embed = ordo_honor_embed.format(possessive="your", possessive_cap="Your", object="you")

    if chapter_blessing:
        embed.add_field(
            name="▸ Honor of the Long Watch",
            value=f"*\"{ordo_honor_embed} {stud_acknowledgment} {chapter_blessing}\"*",
            inline=False,
        )
    else:
        embed.add_field(
            name="▸ Honor of the Long Watch",
            value=f"*\"{ordo_honor_embed} {stud_acknowledgment}\"*",
            inline=False,
        )

    # Litany (personal rite, if set)
    if rite_text:
        rite_display = str(rite_text)[:400] + ("…" if len(str(rite_text)) > 400 else "")
        embed.add_field(name="▸ Litany to the Machine-Spirit", value=f"{rite_display}", inline=False)

    # Attestation
    rank_emoji_prefix = f"{tech_rank_emoji} " if tech_rank_emoji else ""
    attester_with_rank = f"{rank_emoji_prefix}**{attester}**"
    tech_value = f"{attester_with_rank}\n{authority} • {ts}\n*\"{sacred_phrase}\"*"
    attestation_field_name = "▸ Self-Attestation" if is_self_blessing else "▸ Attestation"
    embed.add_field(name=attestation_field_name, value=tech_value, inline=True)

    # Attach approval stamp image
    armor_approved_file = _get_award_image("Armor_Approved.png")
    if armor_approved_file:
        embed.set_image(url="attachment://Armor_Approved.png")

    # Build LogToForgeView - send ephemeral with Log to Forge button
    log_view = LogToForgeView(
        embed=embed,
        member_id=int(member.id),
        member_mention=member.mention,
        techmarine_id=int(attestor_member.id),
        spirit_designation=spirit_designation,
        image_filename="Armor_Approved.png" if armor_approved_file else None,
    )

    try:
        send_kwargs: dict = {
            "content": member.mention,
            "embed": embed,
            "view": log_view,
            "ephemeral": True,
        }
        if armor_approved_file:
            send_kwargs["file"] = armor_approved_file
        await interaction.response.send_message(**send_kwargs)
    except Exception:
        try:
            await interaction.response.send_message("Failed to post attestation.", ephemeral=True)
        except Exception:
            pass






def _format_cooldown_time(td: timedelta) -> str:
    """Format a timedelta as 'Xh Ym' or 'Ym' if under an hour."""
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60

    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


async def _preview_stud_announcement(
    interaction: discord.Interaction,
    member: discord.Member,
    displayed_studs: Optional[int] = None,
    new_studs: Optional[int] = None,
    earned_studs_override: Optional[int] = None,
):
    """Debug command to preview service stud announcement output."""
    # Only allow in DEBUG_MODE or for admins
    if not _b("DEBUG_MODE"):
        user_id = str(interaction.user.id)
        admin_ids = [str(a) for a in _g.CONFIG.get("admin_user_ids", [])]
        if user_id not in admin_ids:
            await interaction.response.send_message("This command is only available in debug mode.", ephemeral=True)
            return

    # Defer to avoid interaction timeout during computation
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    if not guild:
        await interaction.followup.send("Must be used in a guild.", ephemeral=True)
        return

    # Get member's home chapter
    member_chapter = "Unknown"
    for role in getattr(member, "roles", []):
        role_name = getattr(role, "name", "")
        if role_name in _b("HOME_CHAPTERS"):
            member_chapter = role_name
            break

    # Calculate actual studs using same logic as activity check
    user_id = str(member.id)
    stats = _b("compute_stats_for_user")(user_id)
    aar_points = int(stats.get("aar_points", 0) or 0)

    # Get weeks since induction (supports override)
    joined_at = _b("_get_effective_induction_date")(member)
    if joined_at:
        if joined_at.tzinfo is not None:
            joined_at = joined_at.replace(tzinfo=None)
        weeks_in_server = max(0, (datetime.utcnow() - joined_at).days // 7)
    else:
        weeks_in_server = 0

    # Compute earned studs (min of time-based and AAR-based)
    studs_time = weeks_in_server // 4
    studs_aar = aar_points // 400
    earned_studs = min(studs_time, studs_aar)

    # Allow override for testing owed studs
    if earned_studs_override is not None:
        earned_studs = earned_studs_override

    # Read displayed studs from nickname
    # New system: ●=4 (Auramite), ⚬=1 (Plasteel), max 16
    dn = str(member.nick or member.display_name or "")
    displayed_aur = dn.count("●")
    displayed_plas = dn.count("⚬")
    actual_displayed = displayed_aur * 4 + displayed_plas

    # Use provided displayed_studs or fall back to actual
    if displayed_studs is None:
        displayed_studs = actual_displayed

    # If new_studs not provided, default to displayed_studs (as if going from 0 to displayed)
    if new_studs is None:
        new_studs = displayed_studs

    owed_studs = max(0, earned_studs - displayed_studs)

    content, embed = _get_service_studs_announcement(
        member=member,
        member_chapter=member_chapter,
        displayed_studs=displayed_studs,
        new_studs=new_studs,
        earned_studs=earned_studs,
        owed_studs=owed_studs,
        guild=guild,
    )

    # Send ephemeral preview with debug info
    debug_info = (
        f"**[PREVIEW DEBUG]**\n"
        f"• Actual in nickname: {actual_displayed}\n"
        f"• Displayed (param): {displayed_studs}\n"
        f"• New (param): {new_studs}\n"
        f"• Earned: {earned_studs}\n"
        f"• Owed: {owed_studs}\n\n"
    )
    await interaction.followup.send(
        f"{debug_info}{content}",
        embed=embed,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


# No explicit group registration required for top-level commands


@_g.bot.tree.command(
    name="lfg_queue",
    description="Create a Looking For Group queue for operations or omega missions.",
)
@app_commands.choices(
    queue_type=[
        app_commands.Choice(name="Operation (3 players)", value="operation"),
        app_commands.Choice(name="Siege (3 players)", value="siege"),
        app_commands.Choice(name="Omega (5 players, max 2 console)", value="omega"),
    ]
)
@app_commands.describe(
    queue_type="The type of queue to create",
    initiation_trial="Is this an Initiation Trial? (pings additional role)",
    expire_minutes="Minutes until queue expires (default: 30, max: 120)",
    message="Optional message (e.g. 'need slays', 'teaching run')",
)


async def lfg_queue(
    interaction: discord.Interaction,
    queue_type: app_commands.Choice[str],
    initiation_trial: bool = False,
    expire_minutes: Optional[int] = None,
    message: Optional[str] = None,
):
    # Use channel_policies to check if command is allowed here
    if not _b("is_allowed_channel")(interaction):
        await interaction.response.send_message(
            "This command cannot be used in this channel.",
            ephemeral=True,
        )
        return

    member = interaction.user
    if not isinstance(member, discord.Member):
        member = interaction.guild.get_member(interaction.user.id)

    if not member:
        await interaction.response.send_message("Could not resolve your membership.", ephemeral=True)
        return

    # Check platform role
    platform = _b("_get_player_platform")(member)
    if not platform:
        pc_role = _get_lfg_pc_role_id()
        console_role = _get_lfg_console_role_id()
        await interaction.response.send_message(
            f"❌ You must have either the <@&{pc_role}> or "
            f"<@&{console_role}> role to create a queue.\n"
            "Please assign yourself one of these roles first.",
            ephemeral=True,
        )
        return

    # Get queue type config
    queue_types = _b("_get_lfg_queue_types")()
    type_config = queue_types.get(queue_type.value, {})

    # Validate and set expiry time
    default_expiry = _b("_get_lfg_default_expiry_minutes")()
    max_expiry = _b("_get_lfg_max_expiry_minutes")()

    if expire_minutes is not None:
        if expire_minutes < 1:
            await interaction.response.send_message(
                "❌ Expire time must be at least 1 minute.",
                ephemeral=True,
            )
            return
        if expire_minutes > max_expiry:
            await interaction.response.send_message(
                f"❌ Expire time cannot exceed {max_expiry} minutes.",
                ephemeral=True,
            )
            return
        expiry_minutes = expire_minutes
    else:
        expiry_minutes = default_expiry

    # Calculate expiration time
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=expiry_minutes)

    # Build initial queue data (creator auto-joins)
    queue_data = {
        "queue_type": queue_type.value,
        "initiation_trial": initiation_trial,
        "message": message,
        "creator_id": member.id,
        "channel_id": interaction.channel_id,
        "players": [{"user_id": member.id, "platform": platform}],
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }

    # Build embed
    embed = _b("_build_lfg_embed")(queue_data, interaction.guild)

    # Build ping content from queue type config (add initiation trial role if applicable)
    ping_role_id = type_config.get("ping_role_id")
    pings = []
    if ping_role_id:
        pings.append(f"<@&{ping_role_id}>")
    if initiation_trial:
        trial_role_id = _get_lfg_initiation_trial_role_id()
        if trial_role_id:
            pings.append(f"<@&{trial_role_id}>")
    content = " ".join(pings) if pings else None

    # Send message with view
    await interaction.response.send_message(
        content=content,
        embed=embed,
        allowed_mentions=discord.AllowedMentions(roles=True) if content else discord.AllowedMentions.none(),
    )
    msg = await interaction.original_response()

    # Store queue data keyed by message ID
    queue_data["message_id"] = msg.id

    async with _g.LFG_QUEUE_LOCK:
        _g.LFG_ACTIVE_QUEUES[msg.id] = queue_data
        all_queues = _b("_load_lfg_queues")()
        all_queues[str(msg.id)] = queue_data
        _b("_save_lfg_queues")(all_queues)

    # Add view to message
    view = LFGQueueView(msg.id)
    await msg.edit(view=view)

    trial_str = " [Initiation Trial]" if initiation_trial else ""
    _g.logger.info(
        f"LFG queue created: {queue_type.value}{trial_str} by {member.display_name} "
        f"(msg={msg.id}, expires={expires_at.isoformat()})"
    )




async def _lfg_queue_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for LFG queue selection."""
    choices = []
    try:
        all_queues = _b("_load_lfg_queues")()
        guild = interaction.guild
        queue_types = _b("_get_lfg_queue_types")()

        for queue_id_str, queue_data in all_queues.items():
            queue_type = queue_data.get("queue_type", "unknown")
            type_config = queue_types.get(queue_type, {})
            display_type = type_config.get("display", queue_type)

            creator_id = queue_data.get("creator_id")
            creator = guild.get_member(creator_id) if guild and creator_id else None
            creator_name = creator.display_name if creator else f"User {creator_id}"

            players = queue_data.get("players", [])
            player_count = len(players)
            max_players = type_config.get("max_players", "?")

            label = f"{display_type} by {creator_name} ({player_count}/{max_players})"

            # Filter by current input
            if current.lower() in label.lower() or current in queue_id_str:
                choices.append(app_commands.Choice(name=label[:100], value=queue_id_str))

            if len(choices) >= 25:
                break
    except Exception:
        pass

    return choices


@_g.bot.tree.command(
    name="lfg_close",
    description="Close/delete your LFG queue.",
)
@app_commands.describe(
    queue="Select the queue to close (only your own queues can be closed)",
)
@app_commands.autocomplete(queue=_lfg_queue_autocomplete)
async def lfg_close(
    interaction: discord.Interaction,
    queue: str,
):
    # Use channel_policies to check if command is allowed here
    if not _b("is_allowed_channel")(interaction):
        await interaction.response.send_message(
            "This command cannot be used in this channel.",
            ephemeral=True,
        )
        return

    try:
        queue_id = int(queue)
    except ValueError:
        await interaction.response.send_message("Invalid queue selection.", ephemeral=True)
        return

    # Get queue data
    async with _g.LFG_QUEUE_LOCK:
        all_queues = _b("_load_lfg_queues")()
        queue_data = all_queues.get(str(queue_id))

        if not queue_data:
            await interaction.response.send_message("This queue no longer exists.", ephemeral=True)
            return

        # Only creator can close
        if interaction.user.id != queue_data.get("creator_id"):
            await interaction.response.send_message("Only the queue creator can close this queue.", ephemeral=True)
            return

        # Save channel_id before removing from storage
        channel_id = queue_data.get("channel_id")

        # Remove from storage
        if queue_id in _g.LFG_ACTIVE_QUEUES:
            del _g.LFG_ACTIVE_QUEUES[queue_id]
        del all_queues[str(queue_id)]
        _b("_save_lfg_queues")(all_queues)

    # Update the queue message
    try:
        if channel_id:
            channel = interaction.guild.get_channel(int(channel_id))
        else:
            channel = interaction.channel
        if channel:
            msg = await channel.fetch_message(queue_id)
            embed = discord.Embed(
                title="🔒 Queue Closed",
                description="This queue has been closed by the creator.",
                color=0x95A5A6,
            )
            await msg.edit(embed=embed, view=None)
    except discord.NotFound:
        pass
    except Exception as e:
        _g.logger.debug(f"Failed to update closed queue message: {e}")

    await interaction.response.send_message("✅ Queue closed successfully.", ephemeral=True)
    _g.logger.info(f"LFG queue {queue_id} closed by {interaction.user.display_name}")


@_g.bot.tree.command(
    name="lfg_join",
    description="Join an existing LFG queue.",
)
@app_commands.describe(
    queue="Select the queue to join",
)
@app_commands.autocomplete(queue=_lfg_queue_autocomplete)
async def lfg_join(
    interaction: discord.Interaction,
    queue: str,
):
    # Use channel_policies to check if command is allowed here
    if not _b("is_allowed_channel")(interaction):
        await interaction.response.send_message(
            "This command cannot be used in this channel.",
            ephemeral=True,
        )
        return

    member = interaction.user
    if not isinstance(member, discord.Member):
        member = interaction.guild.get_member(interaction.user.id)

    if not member:
        await interaction.response.send_message("Could not resolve your membership.", ephemeral=True)
        return

    # Check platform role
    platform = _b("_get_player_platform")(member)
    if not platform:
        pc_role = _get_lfg_pc_role_id()
        console_role = _get_lfg_console_role_id()
        await interaction.response.send_message(
            f"❌ You must have either the <@&{pc_role}> or "
            f"<@&{console_role}> role to join a queue.\n"
            "Please assign yourself one of these roles first.",
            ephemeral=True,
        )
        return

    try:
        queue_id = int(queue)
    except ValueError:
        await interaction.response.send_message("Invalid queue selection.", ephemeral=True)
        return

    async with _g.LFG_QUEUE_LOCK:
        all_queues = _b("_load_lfg_queues")()
        queue_data = all_queues.get(str(queue_id))

        if not queue_data:
            await interaction.response.send_message("This queue no longer exists.", ephemeral=True)
            return

        queue_types = _b("_get_lfg_queue_types")()
        type_config = queue_types.get(queue_data["queue_type"], {})
        players = queue_data["players"]

        # Check if already in queue
        if any(p["user_id"] == member.id for p in players):
            await interaction.response.send_message("You are already in this queue.", ephemeral=True)
            return

        # Check if queue is full
        if len(players) >= type_config.get("max_players", 3):
            await interaction.response.send_message("This queue is already full.", ephemeral=True)
            return

        # Check console limit for Omega
        max_console = type_config.get("max_console")
        if max_console is not None and platform == "console":
            console_count = sum(1 for p in players if p["platform"] == "console")
            if console_count >= max_console:
                await interaction.response.send_message(
                    f"❌ This Omega queue has reached the console player limit ({max_console}).\n"
                    "Only PC players can join at this time.",
                    ephemeral=True,
                )
                return

        # Add player to queue
        players.append({"user_id": member.id, "platform": platform})
        queue_data["players"] = players
        _g.LFG_ACTIVE_QUEUES[queue_id] = queue_data
        all_queues[str(queue_id)] = queue_data
        _b("_save_lfg_queues")(all_queues)

    # Update the queue message embed
    try:
        channel_id = queue_data.get("channel_id")
        if channel_id:
            channel = interaction.guild.get_channel(int(channel_id))
        else:
            channel = interaction.channel
        if channel:
            msg = await channel.fetch_message(queue_id)
            embed = _b("_build_lfg_embed")(queue_data, interaction.guild)
            view = LFGQueueView(queue_id)
            await msg.edit(embed=embed, view=view)
    except Exception as e:
        _g.logger.debug(f"Failed to update queue embed: {e}")

    await interaction.response.send_message("✅ You joined the queue!", ephemeral=True)

    # Check if queue is now full and notify
    if len(players) >= type_config.get("max_players", 3):
        creator = interaction.guild.get_member(queue_data["creator_id"])
        if creator:
            player_mentions = []
            for p in players:
                m = interaction.guild.get_member(p["user_id"])
                if m:
                    player_mentions.append(m.mention)
            try:
                await interaction.followup.send(
                    f"🎉 **Queue Full!** {creator.mention}, your {type_config.get('display', 'Mission')} queue is ready!\n"
                    f"Players: {', '.join(player_mentions)}",
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except Exception:
                pass


@_g.bot.tree.command(
    name="lfg_leave",
    description="Leave an LFG queue you're in.",
)
@app_commands.describe(
    queue="Select the queue to leave",
)
@app_commands.autocomplete(queue=_lfg_queue_autocomplete)
async def lfg_leave(
    interaction: discord.Interaction,
    queue: str,
):
    # Use channel_policies to check if command is allowed here
    if not _b("is_allowed_channel")(interaction):
        await interaction.response.send_message(
            "This command cannot be used in this channel.",
            ephemeral=True,
        )
        return

    member = interaction.user

    try:
        queue_id = int(queue)
    except ValueError:
        await interaction.response.send_message("Invalid queue selection.", ephemeral=True)
        return

    async with _g.LFG_QUEUE_LOCK:
        all_queues = _b("_load_lfg_queues")()
        queue_data = all_queues.get(str(queue_id))

        if not queue_data:
            await interaction.response.send_message("This queue no longer exists.", ephemeral=True)
            return

        players = queue_data["players"]

        # Check if in queue
        player_entry = next((p for p in players if p["user_id"] == member.id), None)
        if not player_entry:
            await interaction.response.send_message("You are not in this queue.", ephemeral=True)
            return

        # Remove player
        players.remove(player_entry)
        queue_data["players"] = players
        _g.LFG_ACTIVE_QUEUES[queue_id] = queue_data
        all_queues[str(queue_id)] = queue_data
        _b("_save_lfg_queues")(all_queues)

    # Update the queue message embed
    try:
        channel_id = queue_data.get("channel_id")
        if channel_id:
            channel = interaction.guild.get_channel(int(channel_id))
        else:
            channel = interaction.channel
        if channel:
            msg = await channel.fetch_message(queue_id)
            embed = _b("_build_lfg_embed")(queue_data, interaction.guild)
            view = LFGQueueView(queue_id)
            await msg.edit(embed=embed, view=view)
    except Exception as e:
        _g.logger.debug(f"Failed to update queue embed: {e}")

    await interaction.response.send_message("✅ You left the queue.", ephemeral=True)


if __name__ == "__main__":
    _b("_main")()


# ---------------------------------------------------------------------------
# Pure helper functions for forge_rite output
# ---------------------------------------------------------------------------


def _should_show_extended_blessing_fields(
    spirit_is_first: bool,
    spirit_is_reconsecrated: bool,
    spirit_is_returning: bool,
    spirit_is_restored: bool,
) -> bool:
    """Determine whether to show extended blessing embed fields.

    Returns True for first bindings and reconsecrated (reborn) spirits.
    Returns False for returning (routine maintenance) and restored spirits.
    """
    if spirit_is_first or spirit_is_reconsecrated:
        return True
    return False


def _get_compact_rite_status(
    blessing_roll_outcome: str,
    is_intensive: bool,
    armor_was_damaged: bool,
) -> tuple:
    """Return (icon, status_text) for the compact rite status line.

    Priority: crit_fail / crit_success beat intensive / damage flags.
    """
    if blessing_roll_outcome == "crit_fail":
        return ("\u26a0\ufe0f", "RESISTED")
    if blessing_roll_outcome == "crit_success":
        return ("\u2728", "BLESSED *(grace)*")
    if is_intensive:
        return ("\u2728", "RESTORED")
    if armor_was_damaged:
        return ("\U0001f7e2", "REPAIRED")
    return ("\U0001f7e2", "MAINTAINED")


def _get_thread_reply_text(
    spirit_is_reconsecrated: bool,
    blessing_roll_outcome: str,
    attester: str,
    machine_spirit_emoji: str,
    spirit_designation: str,
) -> str:
    """Return the short thread-reply text for a completed forge rite."""
    if spirit_is_reconsecrated:
        return (
            f"\u2728 **Spirit Reborn** \u2014 {machine_spirit_emoji} **{spirit_designation}** "
            f"has been reborn through the rites of the Omnissiah. "
            f"Consecrated by {attester}."
        )
    if blessing_roll_outcome == "crit_fail":
        return (
            f"\u26a0\ufe0f **Rite Resisted** \u2014 {machine_spirit_emoji} **{spirit_designation}** "
            f"resisted the blessing. The spirit stirs but remains unquiet."
        )
    return (
        f"\U0001f7e2 **Armor Restored** \u2014 {machine_spirit_emoji} **{spirit_designation}** "
        f"has been tended by {attester}."
    )


# ---------------------------------------------------------------------------
# /forge_override — Forgemaster-only kill switch for the forge / armor subsystem
# ---------------------------------------------------------------------------


__all__ = [
    "LFGQueueView",
    "LogToForgeView",
    "_attest",
    "_blend_forgemaster_self_attestation",
    "_blend_stud_flavor_by_rank",
    "_build_challenge_award_embed",
    "_build_lfg_embed",
    "_compute_member_service_studs",
    "_delete_machine_spirit",
    "_expire_old_lfg_queues",
    "_extract_killteam_name",
    "_find_company_or_chapter",
    "_format_cooldown_time",
    "_get_apothecarion_medal_announcement",
    "_get_ardent_raider_announcement",
    "_get_arming_chamber_channel_id",
    "_get_armor_config",
    "_get_award_announcement_channel",
    "_get_award_image",
    "_get_bearer_home_chapter",
    "_get_bearer_rank_and_title",
    "_get_black_laurels_announcement",
    "_get_black_reef_campaign_announcement",
    "_get_chapter_emoji",
    "_get_crimson_laurels_announcement",
    "_get_crux_terminatus_announcement",
    "_get_distinguished_black_reef_announcement",
    "_get_distinguished_pipehitter_announcement",
    "_get_dual_vigil_announcement",
    "_get_emoji_by_name",
    "_get_herisor_defense_announcement",
    "_get_kadaku_campaign_announcement",
    "_get_lfg_config",
    "_get_lfg_console_role_id",
    "_get_lfg_default_expiry_minutes",
    "_get_lfg_initiation_trial_role_id",
    "_get_lfg_max_expiry_minutes",
    "_get_lfg_pc_role_id",
    "_get_lfg_queue_types",
    "_get_machine_spirit",
    "_get_master_terminus_slayer_announcement",
    "_get_member_rank_title",
    "_get_oathsworn_announcement",
    "_get_order_omega_announcement",
    "_get_distinguished_herisor_defense_announcement",
    "_get_distinguished_herisor_defense_valor_announcement",
    "_get_player_platform",
    "_get_rank_category_for_blend",
    "_get_rank_emoji",
    "_get_service_studs_announcement",
    "_get_sok_g_pipehitter_announcement",
    "_get_stud_marking_recipients",
    "_get_techmarine_acknowledgment_blended",
    "_get_terminus_slayer_assault_announcement",
    "_get_terminus_slayer_bulwark_announcement",
    "_get_terminus_slayer_heavy_announcement",
    "_get_terminus_slayer_sniper_announcement",
    "_get_terminus_slayer_tactical_announcement",
    "_get_terminus_slayer_techmarine_announcement",
    "_get_terminus_slayer_vanguard_announcement",
    "_get_user_rite",
    "_get_watch_veteran_announcement",
    "_lfg_queue_autocomplete",
    "_lfg_queue_expiration_loop",
    "_load_lfg_queues",
    "_load_machine_spirits",
    "_load_rites",
    "_preview_stud_announcement",
    "_resolve_killteam_for_member",
    "_resolve_killteams_for_member",
    "_restore_lfg_queue_views",
    "_save_lfg_queues",
    "_save_machine_spirits",
    "_save_rites",
    "_set_machine_spirit",
    "_set_rite",
    "_set_user_rite",
    "_trunc",
    "lfg_close",
    "lfg_join",
    "lfg_leave",
    "lfg_queue",
]
