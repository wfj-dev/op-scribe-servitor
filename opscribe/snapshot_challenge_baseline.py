"""Admin command to snapshot current challenge role holders for baseline tracking."""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
import sys as _sys
from . import _bot_globals as _g
from . import constants as _constants


# Maps challenge name -> role ID for snapshotting (lazy-resolved on command execution)
def _get_snapshot_roles():
    """Get role ID mapping at command time (after bot initialization)."""
    return {
        "Black Laurels": _constants.BLACK_LAURELS_ROLE_ID,
        "Order Omega": _constants.THE_ORDER_OMEGA_ROLE_ID,
        "Dual Vigil": _constants.DUAL_VIGIL_AWARD_ROLE_ID,
        "Crux Terminatus": _constants.CRUX_TERMINATUS_ROLE_ID,
    }


async def _snapshot_challenge_baseline_command(interaction: discord.Interaction, challenge: str):
    """Snapshot all current holders of a challenge role."""
    if not _g.check_command_permission(interaction.user, "snapshot_challenge_baseline"):
        await interaction.response.send_message("Access denied.", ephemeral=True)
        return

    snapshot_roles = _get_snapshot_roles()
    if challenge not in snapshot_roles:
        known = ", ".join(f"`{k}`" for k in sorted(snapshot_roles.keys()))
        await interaction.response.send_message(
            f"Unknown challenge `{challenge}`.\nKnown challenges: {known}",
            ephemeral=True,
        )
        return

    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("Must be used in a server.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    role_id = snapshot_roles[challenge]
    role = guild.get_role(role_id)
    if not role:
        await interaction.followup.send(
            f"Role ID {role_id} for '{challenge}' not found in this guild.", ephemeral=True
        )
        return

    # Collect all current holders
    holders = [m for m in role.members if not m.bot]
    if not holders:
        await interaction.followup.send(
            f"No members hold the **{challenge}** role currently.",
            ephemeral=True,
        )
        return

    now_iso = datetime.now(timezone.utc).isoformat()

    # Record acquisition date for each holder
    count = 0
    if _g.DATASTORE:
        for member in holders:
            try:
                await _g.DATASTORE.set_role_acquisition_date(str(member.id), challenge, now_iso)
                count += 1
            except Exception as e:
                _g.logger.warning(f"snapshot_challenge_baseline: failed to record {member.id} for {challenge}: {e}")

    await interaction.followup.send(
        f"✅ Snapshot recorded for **{challenge}**\n"
        f"• Members: **{len(holders)}**\n"
        f"• Baseline date: `{now_iso}`\n"
        f"• Recorded: **{count}** members\n\n"
        f"Members with this role will now use their current acquisition date as the baseline for grace-period calculations.",
        ephemeral=True,
    )


def setup(bot: commands.Bot):
    """Register the snapshot command with the bot."""
    @bot.tree.command(
        name="snapshot_challenge_baseline",
        description="Record current challenge role holders as baseline for grace-period enforcement (admin).",
    )
    @app_commands.describe(
        challenge="Challenge role to snapshot (Black Laurels, Order Omega, Dual Vigil, or Crux Terminatus).",
    )
    async def snapshot_challenge_baseline_cmd(interaction: discord.Interaction, challenge: str):
        await _snapshot_challenge_baseline_command(interaction, challenge)
