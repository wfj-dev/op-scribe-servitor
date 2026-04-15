import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import bot


def _run(coro):
    return asyncio.run(coro)


def _make_interaction(user_id: int):
    member = SimpleNamespace(id=user_id, display_name=f"User{user_id}", roles=[])
    guild = MagicMock()
    guild.get_member.return_value = member

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        guild=guild,
        channel_id=123,
        channel=MagicMock(),
        response=SimpleNamespace(
            send_message=AsyncMock(),
            defer=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        message=SimpleNamespace(edit=AsyncMock()),
        original_response=AsyncMock(),
    )
    return interaction, member


def test_lfg_queue_creation_uses_default_expiry_when_not_provided():
    interaction, member = _make_interaction(user_id=42)
    msg = SimpleNamespace(id=999, edit=AsyncMock())
    interaction.original_response = AsyncMock(return_value=msg)

    queue_types = {
        "operation": {"max_players": 3, "max_console": None, "display": "Operation", "ping_role_id": None}
    }

    bot.LFG_ACTIVE_QUEUES.clear()

    with (
        patch("bot.is_allowed_channel", return_value=True),
        patch("bot._get_player_platform", return_value="pc"),
        patch("bot._get_lfg_queue_types", return_value=queue_types),
        patch("bot._get_lfg_default_expiry_minutes", return_value=30),
        patch("bot._get_lfg_max_expiry_minutes", return_value=120),
        patch("bot._build_lfg_embed", return_value=discord.Embed(title="x")),
        patch("bot._load_lfg_queues", return_value={}),
        patch("bot._save_lfg_queues"),
    ):
        _run(
            bot.lfg_queue.callback(
                interaction,
                queue_type=discord.app_commands.Choice(name="Operation", value="operation"),
                initiation_trial=False,
                expire_minutes=None,
                message=None,
            )
        )

    stored = bot.LFG_ACTIVE_QUEUES[msg.id]
    created_at = datetime.fromisoformat(stored["created_at"])
    expires_at = datetime.fromisoformat(stored["expires_at"])
    assert expires_at - created_at == timedelta(minutes=30)
    assert stored["players"] == [{"user_id": member.id, "platform": "pc"}]


def test_join_queue_prevents_duplicate_join():
    view = bot.LFGQueueView(queue_id=111)
    interaction, member = _make_interaction(user_id=42)
    interaction.guild.get_member.return_value = member

    queue_data = {
        "queue_type": "operation",
        "creator_id": 7,
        "players": [{"user_id": 42, "platform": "pc"}],
    }

    with (
        patch.object(view, "_get_queue_data", AsyncMock(return_value=queue_data)),
        patch("bot._get_player_platform", return_value="pc"),
        patch("bot._get_lfg_queue_types", return_value={"operation": {"max_players": 3}}),
    ):
        _run(view.join_queue(interaction))

    interaction.response.send_message.assert_awaited_once_with(
        "You are already in this queue.", ephemeral=True
    )


def test_join_queue_enforces_console_limit():
    view = bot.LFGQueueView(queue_id=222)
    interaction, member = _make_interaction(user_id=77)
    interaction.guild.get_member.return_value = member

    queue_data = {
        "queue_type": "omega",
        "creator_id": 7,
        "players": [
            {"user_id": 10, "platform": "console"},
            {"user_id": 11, "platform": "console"},
            {"user_id": 12, "platform": "pc"},
        ],
    }
    original_players = list(queue_data["players"])

    with (
        patch.object(view, "_get_queue_data", AsyncMock(return_value=queue_data)),
        patch("bot._get_player_platform", return_value="console"),
        patch(
            "bot._get_lfg_queue_types",
            return_value={"omega": {"max_players": 5, "max_console": 2, "display": "Omega"}},
        ),
    ):
        _run(view.join_queue(interaction))

    message = interaction.response.send_message.await_args.args[0]
    assert "reached the console player limit (2)" in message
    assert queue_data["players"] == original_players


def test_expire_old_lfg_queues_removes_expired_and_updates_message():
    now = datetime.now(timezone.utc)
    expired_at = (now - timedelta(minutes=1)).isoformat()
    future_at = (now + timedelta(minutes=10)).isoformat()

    all_queues = {
        "101": {"expires_at": expired_at, "channel_id": 321},
        "202": {"expires_at": future_at, "channel_id": 321},
    }

    bot.LFG_ACTIVE_QUEUES.clear()
    bot.LFG_ACTIVE_QUEUES[101] = dict(all_queues["101"])
    bot.LFG_ACTIVE_QUEUES[202] = dict(all_queues["202"])

    msg = SimpleNamespace(edit=AsyncMock())
    channel = SimpleNamespace(fetch_message=AsyncMock(return_value=msg))
    guild = MagicMock()
    guild.get_channel.return_value = channel

    with (
        patch("bot._load_lfg_queues", return_value=dict(all_queues)),
        patch("bot._save_lfg_queues") as mock_save,
        patch("bot._resolve_notification_guild", return_value=guild),
    ):
        _run(bot._expire_old_lfg_queues())

    saved_payload = mock_save.call_args.args[0]
    assert "101" not in saved_payload
    assert "202" in saved_payload
    assert 101 not in bot.LFG_ACTIVE_QUEUES
    assert 202 in bot.LFG_ACTIVE_QUEUES

    edit_kwargs = msg.edit.await_args.kwargs
    assert edit_kwargs["view"] is None
    assert edit_kwargs["embed"].title == "⏰ Queue Expired"
