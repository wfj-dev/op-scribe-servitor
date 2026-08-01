import asyncio
import importlib
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from opscribe import _bot_globals as _g
from opscribe.constants import (
    KILL_LOG_REVIEW_ACTION_LIMIT,
    KILL_LOG_REVIEW_ACTION_WINDOW_HOURS,
    KILL_LOG_REVIEW_DELAY_MINUTES,
)


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


def _make_interaction(role_names, user_id=222):
    user = SimpleNamespace(
        id=user_id,
        mention=f"<@{user_id}>",
        roles=[SimpleNamespace(name=name) for name in role_names],
    )
    return SimpleNamespace(
        user=user,
        guild=MagicMock(),
        response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            defer=AsyncMock(),
        ),
    )


def _make_state(submitted_minutes_ago):
    entry = {
        "status": "pending",
        "submitted_at": (
            datetime.now(timezone.utc) - timedelta(minutes=submitted_minutes_ago)
        ).isoformat(),
        "brother_id": "111",
        "class_role_id": 1449257352112111646,
        "class_name": "Assault",
        "terminus_type": "Neurothrope",
        "aar_link": "",
        "verifications": [],
        "verification_log": [],
    }
    state = {
        "entries": {"KL-0001": entry},
        "progress": {},
        "verifier_actions": {},
        "next_id": 2,
    }
    return state, entry


def test_verify_inside_previous_cooldown_now_succeeds_when_other_checks_pass():
    state, entry = _make_state(submitted_minutes_ago=0)
    interaction = _make_interaction(["Watch Veteran"])

    with (
        patch.object(terminus_ops._g, "TERMINUS_SLAYER_LOCK", asyncio.Lock()),
        patch.object(terminus_ops, "_load_state", return_value=state),
        patch.object(terminus_ops, "_save_state"),
        patch.object(terminus_ops, "_verifier_in_aar", return_value=False),
        patch.object(terminus_ops, "_build_kill_log_embed", return_value=object()),
        patch.object(terminus_ops, "TerminusKillLogView", return_value=None),
    ):
        _run(terminus_ops._handle_verify(interaction, "KL-0001"))

    assert entry["verifications"] == [str(interaction.user.id)]
    interaction.response.edit_message.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()


def test_deny_inside_cooldown_shows_two_minute_message_for_aar_participant():
    state, _entry = _make_state(submitted_minutes_ago=1)
    interaction = _make_interaction(["Watch Veteran"])

    with (
        patch.object(terminus_ops._g, "TERMINUS_SLAYER_LOCK", asyncio.Lock()),
        patch.object(terminus_ops, "_load_state", return_value=state),
        patch.object(terminus_ops, "_save_state"),
        patch.object(terminus_ops, "_verifier_in_aar", return_value=True),
    ):
        _run(terminus_ops._handle_deny(interaction, "KL-0001"))

    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.await_args
    assert (
        args[0]
        == f"Kill log entries cannot be denied until {KILL_LOG_REVIEW_DELAY_MINUTES} minutes after submission."
    )
    assert kwargs["ephemeral"] is True
    interaction.response.defer.assert_not_awaited()


def test_verify_after_cooldown_succeeds_without_shame():
    state, entry = _make_state(submitted_minutes_ago=KILL_LOG_REVIEW_DELAY_MINUTES + 1)
    interaction = _make_interaction(["Watch Veteran"])

    with (
        patch.object(terminus_ops._g, "TERMINUS_SLAYER_LOCK", asyncio.Lock()),
        patch.object(terminus_ops, "_load_state", return_value=state),
        patch.object(terminus_ops, "_save_state"),
        patch.object(terminus_ops, "_verifier_in_aar", return_value=False),
        patch.object(terminus_ops, "_build_kill_log_embed", return_value=object()),
        patch.object(terminus_ops, "TerminusKillLogView", return_value=None),
    ):
        _run(terminus_ops._handle_verify(interaction, "KL-0001"))

    assert entry["verifications"] == [str(interaction.user.id)]
    interaction.response.edit_message.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()


def test_deny_after_cooldown_marks_under_review_and_notifies_apothecaries():
    state, entry = _make_state(submitted_minutes_ago=KILL_LOG_REVIEW_DELAY_MINUTES + 1)
    interaction = _make_interaction(["Watch Veteran"])

    with (
        patch.object(terminus_ops._g, "TERMINUS_SLAYER_LOCK", asyncio.Lock()),
        patch.object(terminus_ops, "_load_state", return_value=state),
        patch.object(terminus_ops, "_save_state"),
        patch.object(terminus_ops, "_verifier_in_aar", return_value=False),
        patch.object(terminus_ops, "_refresh_kill_log_embed", new=AsyncMock()) as refresh_embed,
        patch.object(terminus_ops, "_notify_apo_denial", new=AsyncMock()) as notify_denial,
    ):
        _run(terminus_ops._handle_deny(interaction, "KL-0001"))

    assert entry["status"] == "under_review"
    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    refresh_embed.assert_awaited_once_with(interaction.guild, entry)
    notify_denial.assert_awaited_once_with(interaction.guild, entry)
    interaction.response.send_message.assert_not_awaited()


def test_apothecary_verify_bypasses_cooldown_window():
    state, entry = _make_state(submitted_minutes_ago=0)
    interaction = _make_interaction(["Watch Apothecary"])

    with (
        patch.object(terminus_ops._g, "TERMINUS_SLAYER_LOCK", asyncio.Lock()),
        patch.object(terminus_ops, "_load_state", return_value=state),
        patch.object(terminus_ops, "_save_state"),
        patch.object(terminus_ops, "_build_kill_log_embed", return_value=object()),
        patch.object(terminus_ops, "TerminusKillLogView", return_value=None),
    ):
        _run(terminus_ops._handle_verify(interaction, "KL-0001"))

    assert entry["verifications"] == [str(interaction.user.id)]
    interaction.response.edit_message.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()


def test_verify_is_blocked_after_three_approvals_in_rolling_24h():
    state, entry = _make_state(submitted_minutes_ago=120)
    interaction = _make_interaction(["Watch Veteran"])
    user_id = str(interaction.user.id)
    now = datetime.now(timezone.utc)
    state["verifier_actions"][user_id] = [
        {
            "action": "verify",
            "kill_log_id": "KL-9001",
            "timestamp": (now - timedelta(hours=1)).isoformat(),
        },
        {
            "action": "verify",
            "kill_log_id": "KL-9002",
            "timestamp": (now - timedelta(hours=2)).isoformat(),
        },
        {
            "action": "verify",
            "kill_log_id": "KL-9003",
            "timestamp": (now - timedelta(hours=3)).isoformat(),
        },
        {
            "action": "deny",
            "kill_log_id": "KL-9004",
            "timestamp": (now - timedelta(hours=1)).isoformat(),
        },
    ]

    with (
        patch.object(terminus_ops._g, "TERMINUS_SLAYER_LOCK", asyncio.Lock()),
        patch.object(terminus_ops, "_load_state", return_value=state),
        patch.object(terminus_ops, "_save_state"),
        patch.object(terminus_ops, "_verifier_in_aar", return_value=False),
        patch.object(terminus_ops, "_build_kill_log_embed", return_value=object()),
        patch.object(terminus_ops, "TerminusKillLogView", return_value=None),
    ):
        _run(terminus_ops._handle_verify(interaction, "KL-0001"))

    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.await_args
    assert "You have reached the review limit" in args[0]
    assert (
        f"{KILL_LOG_REVIEW_ACTION_LIMIT} approvals or denials per {KILL_LOG_REVIEW_ACTION_WINDOW_HOURS} hours"
        in args[0]
    )
    assert kwargs["ephemeral"] is True
    interaction.response.edit_message.assert_not_awaited()
    assert entry["verifications"] == []


def test_verify_allows_again_once_old_actions_fall_outside_window():
    state, entry = _make_state(submitted_minutes_ago=120)
    interaction = _make_interaction(["Watch Veteran"])
    user_id = str(interaction.user.id)
    now = datetime.now(timezone.utc)
    state["verifier_actions"][user_id] = [
        {
            "action": "verify",
            "kill_log_id": "KL-9101",
            "timestamp": (now - timedelta(hours=1)).isoformat(),
        },
        {
            "action": "verify",
            "kill_log_id": "KL-9102",
            "timestamp": (now - timedelta(hours=2)).isoformat(),
        },
        {
            "action": "verify",
            "kill_log_id": "KL-9103",
            "timestamp": (
                now - timedelta(hours=KILL_LOG_REVIEW_ACTION_WINDOW_HOURS + 1)
            ).isoformat(),
        },
    ]

    with (
        patch.object(terminus_ops._g, "TERMINUS_SLAYER_LOCK", asyncio.Lock()),
        patch.object(terminus_ops, "_load_state", return_value=state),
        patch.object(terminus_ops, "_save_state"),
        patch.object(terminus_ops, "_verifier_in_aar", return_value=False),
        patch.object(terminus_ops, "_build_kill_log_embed", return_value=object()),
        patch.object(terminus_ops, "TerminusKillLogView", return_value=None),
    ):
        _run(terminus_ops._handle_verify(interaction, "KL-0001"))

    assert entry["verifications"] == [str(interaction.user.id)]
    interaction.response.edit_message.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()


def test_apothecary_is_also_limited_to_three_verifies_in_24h():
    state, entry = _make_state(submitted_minutes_ago=0)
    interaction = _make_interaction(["Watch Apothecary"])
    user_id = str(interaction.user.id)
    now = datetime.now(timezone.utc)
    state["verifier_actions"][user_id] = [
        {
            "action": "verify",
            "kill_log_id": "KL-9201",
            "timestamp": (now - timedelta(hours=1)).isoformat(),
        },
        {
            "action": "verify",
            "kill_log_id": "KL-9202",
            "timestamp": (now - timedelta(hours=2)).isoformat(),
        },
        {
            "action": "verify",
            "kill_log_id": "KL-9203",
            "timestamp": (now - timedelta(hours=3)).isoformat(),
        },
    ]

    with (
        patch.object(terminus_ops._g, "TERMINUS_SLAYER_LOCK", asyncio.Lock()),
        patch.object(terminus_ops, "_load_state", return_value=state),
        patch.object(terminus_ops, "_save_state"),
        patch.object(terminus_ops, "_build_kill_log_embed", return_value=object()),
        patch.object(terminus_ops, "TerminusKillLogView", return_value=None),
    ):
        _run(terminus_ops._handle_verify(interaction, "KL-0001"))

    interaction.response.send_message.assert_awaited_once()
    interaction.response.edit_message.assert_not_awaited()
    assert entry["verifications"] == []


def test_verify_is_blocked_after_mixed_three_reviews_in_rolling_24h():
    state, entry = _make_state(submitted_minutes_ago=120)
    interaction = _make_interaction(["Watch Veteran"])
    user_id = str(interaction.user.id)
    now = datetime.now(timezone.utc)
    state["verifier_actions"][user_id] = [
        {
            "action": "verify",
            "kill_log_id": "KL-9301",
            "timestamp": (now - timedelta(hours=1)).isoformat(),
        },
        {
            "action": "deny",
            "kill_log_id": "KL-9302",
            "timestamp": (now - timedelta(hours=2)).isoformat(),
        },
        {
            "action": "verify",
            "kill_log_id": "KL-9303",
            "timestamp": (now - timedelta(hours=3)).isoformat(),
        },
    ]

    with (
        patch.object(terminus_ops._g, "TERMINUS_SLAYER_LOCK", asyncio.Lock()),
        patch.object(terminus_ops, "_load_state", return_value=state),
        patch.object(terminus_ops, "_save_state"),
        patch.object(terminus_ops, "_verifier_in_aar", return_value=False),
        patch.object(terminus_ops, "_build_kill_log_embed", return_value=object()),
        patch.object(terminus_ops, "TerminusKillLogView", return_value=None),
    ):
        _run(terminus_ops._handle_verify(interaction, "KL-0001"))

    interaction.response.send_message.assert_awaited_once()
    interaction.response.edit_message.assert_not_awaited()
    assert entry["verifications"] == []


def test_deny_is_blocked_after_mixed_three_reviews_in_rolling_24h():
    state, entry = _make_state(submitted_minutes_ago=KILL_LOG_REVIEW_DELAY_MINUTES + 5)
    interaction = _make_interaction(["Watch Veteran"])
    user_id = str(interaction.user.id)
    now = datetime.now(timezone.utc)
    state["verifier_actions"][user_id] = [
        {
            "action": "deny",
            "kill_log_id": "KL-9401",
            "timestamp": (now - timedelta(hours=1)).isoformat(),
        },
        {
            "action": "verify",
            "kill_log_id": "KL-9402",
            "timestamp": (now - timedelta(hours=2)).isoformat(),
        },
        {
            "action": "deny",
            "kill_log_id": "KL-9403",
            "timestamp": (now - timedelta(hours=3)).isoformat(),
        },
    ]

    with (
        patch.object(terminus_ops._g, "TERMINUS_SLAYER_LOCK", asyncio.Lock()),
        patch.object(terminus_ops, "_load_state", return_value=state),
        patch.object(terminus_ops, "_save_state"),
        patch.object(terminus_ops, "_verifier_in_aar", return_value=False),
        patch.object(terminus_ops, "_refresh_kill_log_embed", new=AsyncMock()) as refresh_embed,
        patch.object(terminus_ops, "_notify_apo_denial", new=AsyncMock()) as notify_denial,
    ):
        _run(terminus_ops._handle_deny(interaction, "KL-0001", reason="bad clip"))

    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.await_args
    assert "You have reached the review limit" in args[0]
    assert kwargs["ephemeral"] is True
    assert entry["status"] == "pending"
    refresh_embed.assert_not_awaited()
    notify_denial.assert_not_awaited()
