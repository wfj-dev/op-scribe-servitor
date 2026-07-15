import asyncio
import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from opscribe import _bot_globals as _g
from opscribe.constants import CRUX_TERMINATUS_ROLE_ID


class _FakeTree:
    def command(self, *args, **kwargs):
        def _decorator(func):
            return func

        return _decorator


class _FakeBot:
    def __init__(self):
        self.tree = _FakeTree()


_g.bot = _FakeBot()
sys.modules.pop("opscribe.terminus_ops", None)
terminus_ops = importlib.import_module("opscribe.terminus_ops")


def _run(coro):
    return asyncio.run(coro)


def _make_interaction(target_role_ids: list[int], user_id: int = 333):
    roles = [SimpleNamespace(id=role_id, name=f"Role{role_id}") for role_id in target_role_ids]
    user = SimpleNamespace(
        id=user_id,
        display_name=f"Member{user_id}",
        mention=f"<@{user_id}>",
        roles=roles,
    )
    return SimpleNamespace(
        user=user,
        guild=MagicMock(),
        response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            defer=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def _extract_crux_block(embed) -> str:
    for field in embed.fields:
        value = field.value or ""
        if "**Crux Terminatus**" in value:
            start = value.index("**Crux Terminatus**")
            next_idx = value.find("\n\n**", start + 1)
            return value[start:] if next_idx == -1 else value[start:next_idx]
    return ""


def test_challenge_progress_crux_role_holder_shows_completed_checklist():
    interaction = _make_interaction(target_role_ids=[CRUX_TERMINATUS_ROLE_ID])

    with (
        patch.object(terminus_ops, "_load_state", return_value={"progress": {}}),
        patch.object(terminus_ops._g, "DATASTORE", None),
        patch("opscribe.terminus_ops.os.path.exists", return_value=False),
    ):
        _run(terminus_ops._challenge_progress_inner(interaction, member=None, verbose=True))

    interaction.followup.send.assert_awaited_once()
    embed = interaction.followup.send.await_args.kwargs["embed"]
    crux_block = _extract_crux_block(embed)

    assert crux_block
    assert "✅ Black Laurels — baseline missions, Rank A" in crux_block
    assert "✅ Distinguished SOK-G: Pipehitter" in crux_block
    assert "✅ Terminus Slayer roles held: 2/2" in crux_block
