import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands


@pytest.mark.parametrize("deferred", [False, True])
def test_tree_error_handler_replies_and_clears_invocation(deferred):
    source = Path(__file__).parents[1] / "opscribe" / "bot.py"
    parsed = ast.parse(source.read_text(encoding="utf-8"))
    handler = next(
        node for node in parsed.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "on_app_command_error"
    )
    bot = discord.Client(intents=discord.Intents.none())
    bot.tree = app_commands.CommandTree(bot)
    logger = MagicMock()
    invocations = {123: 1.0}
    namespace = {
        "bot": bot, "discord": discord, "app_commands": app_commands,
        "logger": logger, "_CMD_INVOCATIONS": invocations,
        "_user_label": lambda user: str(user.id),
    }
    exec(compile(ast.Module(body=[handler], type_ignores=[]), str(source), "exec"), namespace)
    assert bot.tree.on_error is namespace["on_app_command_error"]
    interaction = SimpleNamespace(
        id=123, user=SimpleNamespace(id=222),
        command=SimpleNamespace(name="submit_kill_log"),
        response=SimpleNamespace(is_done=lambda: deferred, send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    original = RuntimeError("upload failed")
    error = app_commands.CommandInvokeError(interaction.command, original)
    asyncio.run(bot.tree.on_error(interaction, error))
    assert not invocations
    reply = interaction.followup.send if deferred else interaction.response.send_message
    reply.assert_awaited_once_with(
        "Command failed due to an internal servitor fault. The issue has been logged.",
        ephemeral=True,
    )
    logger.warning.assert_called_once()
    assert logger.warning.call_args.kwargs["exc_info"][1] is original