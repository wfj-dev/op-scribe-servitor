import asyncio
import importlib
import sys
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from opscribe import _bot_globals as _g
from opscribe.constants import (
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
    message = SimpleNamespace(delete=AsyncMock())
    return SimpleNamespace(
        user=user,
        guild=MagicMock(),
        message=message,
        response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            defer=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
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


def _submit(*, video=None, video_url=None, send_errors=None):
    interaction = _make_interaction(["Watch Brother"])
    channel = SimpleNamespace(send=AsyncMock())
    message = SimpleNamespace(id=456, attachments=[SimpleNamespace(url="https://cdn.example/reuploaded.mp4")])
    channel.send.return_value = message
    if send_errors:
        channel.send.side_effect = send_errors
    interaction.guild.get_channel.return_value = channel
    interaction.guild.roles = []
    interaction.guild.filesize_limit = 10
    state = {"entries": {}, "progress": {}, "verifier_actions": {}, "next_id": 1}
    role_id = next(iter(terminus_ops.KILL_LOG_CLASS_ROLES))
    with ExitStack() as stack:
        stack.enter_context(patch.object(terminus_ops._g, "DEBUG_MODE", False))
        stack.enter_context(patch.object(terminus_ops._g, "TERMINUS_SLAYER_LOCK", asyncio.Lock()))
        stack.enter_context(patch.object(terminus_ops, "_b", return_value=lambda interaction: True))
        stack.enter_context(patch.object(terminus_ops, "_validate_aar_link", new=AsyncMock(return_value=None)))
        stack.enter_context(patch.object(terminus_ops, "_load_state", return_value=state))
        stack.enter_context(patch.object(terminus_ops, "_save_state"))
        stack.enter_context(patch.object(terminus_ops, "_VIDEO_DOWNLOAD_TIMEOUT_SECONDS", 0.01))
        stack.enter_context(patch.object(terminus_ops, "_KILL_LOG_POST_TIMEOUT_SECONDS", 0.01))
        _run(terminus_ops.submit_kill_log(
            interaction, SimpleNamespace(id=role_id), SimpleNamespace(value="Helbrute"),
            "https://discord.com/channels/1/2/3", video_url=video_url, video=video,
        ))
    return interaction, channel, state


def _video(*, size=1):
    return SimpleNamespace(
        size=size, url="https://cdn.example/original.mp4", to_file=AsyncMock(return_value=MagicMock())
    )


def _http_error(status, code=0):
    return discord.HTTPException(SimpleNamespace(status=status, reason="Rejected"), {"code": code, "message": "Rejected"})


def test_submit_video_url_completes_without_download():
    video = _video()
    interaction, channel, state = _submit(video=video, video_url="https://youtu.be/example")
    video.to_file.assert_not_awaited()
    channel.send.assert_awaited_once()
    assert state["entries"]["KL-0001"]["embed_message_id"] == "456"
    assert "submitted" in interaction.followup.send.await_args.args[0]


def test_submit_attachment_preserves_reuploaded_url_and_closes_file():
    video = _video()
    _, _, state = _submit(video=video)
    assert state["entries"]["KL-0001"]["video_attachment_url"] == "https://cdn.example/reuploaded.mp4"
    video.to_file.return_value.close.assert_called_once()


def test_submit_oversized_attachment_skips_download_and_keeps_original_link():
    video = _video(size=11)
    interaction, channel, state = _submit(video=video)
    video.to_file.assert_not_awaited()
    assert channel.send.await_args.kwargs["file"] is None
    assert state["entries"]["KL-0001"]["video_attachment_url"] == video.url
    assert "original attachment link" in interaction.followup.send.await_args.args[0]
    assert "Re-submit" not in interaction.followup.send.await_args.args[0]


def test_submit_stalled_attachment_download_falls_back_to_original_link():
    async def stalled():
        await asyncio.Future()

    video = _video()
    video.to_file.side_effect = stalled
    interaction, channel, state = _submit(video=video)
    channel.send.assert_awaited_once()
    assert state["entries"]["KL-0001"]["video_attachment_url"] == video.url
    assert "submitted" in interaction.followup.send.await_args.args[0]


def test_submit_attachment_download_error_falls_back_to_original_link():
    video = _video()
    video.to_file.side_effect = OSError("CDN unavailable")
    interaction, channel, state = _submit(video=video)
    channel.send.assert_awaited_once()
    assert state["entries"]["KL-0001"]["video_attachment_url"] == video.url
    assert "original attachment link" in interaction.followup.send.await_args.args[0]


@pytest.mark.parametrize("status,code", [(413, 0), (400, 40005)])
def test_submit_upload_size_rejection_retries_without_file(status, code):
    message = SimpleNamespace(id=456, attachments=[])
    video = _video()
    interaction, channel, state = _submit(video=video, send_errors=[_http_error(status, code), message])
    assert channel.send.await_count == 2
    assert "file" not in channel.send.await_args.kwargs
    assert state["entries"]["KL-0001"]["video_attachment_url"] == video.url
    assert "submitted" in interaction.followup.send.await_args.args[0]
    video.to_file.return_value.close.assert_called_once()


def test_submit_permission_error_does_not_retry_and_removes_entry():
    video = _video()
    interaction, channel, state = _submit(video=video, send_errors=[_http_error(403)])
    channel.send.assert_awaited_once()
    assert state["entries"] == {}
    assert "Nothing was submitted" in interaction.followup.send.await_args.args[0]
    video.to_file.return_value.close.assert_called_once()


def test_submit_failed_attachment_free_retry_removes_entry():
    interaction, channel, state = _submit(video=_video(), send_errors=[_http_error(413), _http_error(403)])
    assert channel.send.await_count == 2
    assert state["entries"] == {}
    assert "Nothing was submitted" in interaction.followup.send.await_args.args[0]


@pytest.mark.parametrize("error", [OSError("Connection lost"), _http_error(500)])
def test_submit_uncertain_delivery_keeps_dossier_and_does_not_retry(error):
    interaction, channel, state = _submit(video=_video(), send_errors=[error])
    channel.send.assert_awaited_once()
    assert "publication_error" in state["entries"]["KL-0001"]
    assert "before retrying" in interaction.followup.send.await_args.args[0]


def test_submit_stalled_post_reports_uncertain_delivery_without_retry():
    async def stalled(**kwargs):
        await asyncio.Future()

    interaction, channel, state = _submit(video_url="https://youtu.be/example", send_errors=stalled)
    channel.send.assert_awaited_once()
    assert "publication_error" in state["entries"]["KL-0001"]
    assert "before retrying" in interaction.followup.send.await_args.args[0]


def test_aar_fetch_timeout_returns_actionable_error():
    async def stalled(message_id):
        await asyncio.Future()

    guild = MagicMock()
    guild.get_channel.return_value.fetch_message = AsyncMock(side_effect=stalled)
    link = f"https://discord.com/channels/1/{terminus_ops.AAR_CHANNEL_ID}/3"
    with patch.object(terminus_ops, "_AAR_FETCH_TIMEOUT_SECONDS", 0.01):
        error = _run(terminus_ops._validate_aar_link(link, guild, "222"))
    assert "Nothing was submitted" in error


def test_startup_restores_only_confirmed_posts_bound_to_original_message():
    state, entry = _make_state(submitted_minutes_ago=0)
    entry.update(kill_log_id="KL-0001", embed_message_id="456")
    state["entries"]["KL-0002"] = {**entry, "kill_log_id": "KL-0002", "embed_message_id": ""}
    bot = MagicMock()
    with (
        patch.object(terminus_ops._g, "bot", bot),
        patch.object(terminus_ops, "_load_state", return_value=state),
    ):
        _run(terminus_ops.register_persistent_views())
    bot.add_view.assert_called_once()
    assert bot.add_view.call_args.kwargs["message_id"] == 456


def test_reminders_skip_unconfirmed_posts():
    state, entry = _make_state(submitted_minutes_ago=10000)
    entry.update(kill_log_id="KL-0001", embed_message_id="")
    guild = MagicMock()
    with (
        patch.object(terminus_ops, "_b", return_value=lambda: guild),
        patch.object(terminus_ops._g, "TERMINUS_SLAYER_LOCK", asyncio.Lock()),
        patch.object(terminus_ops, "_load_state", return_value=state),
    ):
        _run(terminus_ops.check_stale_kill_logs())
    guild.get_channel.assert_not_called()


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


def test_verify_third_confirmation_deletes_kill_log_message_immediately():
    state, entry = _make_state(submitted_minutes_ago=0)
    interaction = _make_interaction(["Watch Veteran"])
    entry["verifications"] = ["9001", "9002"]

    with (
        patch.object(terminus_ops._g, "TERMINUS_SLAYER_LOCK", asyncio.Lock()),
        patch.object(terminus_ops, "_load_state", return_value=state),
        patch.object(terminus_ops, "_save_state"),
        patch.object(terminus_ops, "_verifier_in_aar", return_value=False),
        patch.object(terminus_ops, "_notify_class_complete", new=AsyncMock()) as notify_complete,
    ):
        _run(terminus_ops._handle_verify(interaction, "KL-0001"))

    assert entry["status"] == "verified"
    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.message.delete.assert_awaited_once()
    interaction.followup.send.assert_awaited_once_with(
        "✅ Kill log **KL-0001** confirmed and archived.",
        ephemeral=True,
    )
    interaction.response.edit_message.assert_not_awaited()
    notify_complete.assert_not_awaited()


def test_verify_is_unlimited_for_non_apothecary_veterans_even_after_many_reviews_in_same_utc_day():
    state, entry = _make_state(submitted_minutes_ago=120)
    interaction = _make_interaction(["Watch Veteran"])
    user_id = str(interaction.user.id)
    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
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

    interaction.response.send_message.assert_not_awaited()
    interaction.response.edit_message.assert_awaited_once()
    assert entry["verifications"] == [str(interaction.user.id)]


def test_verify_allows_again_when_prior_actions_are_from_previous_utc_day():
    state, entry = _make_state(submitted_minutes_ago=120)
    interaction = _make_interaction(["Watch Veteran"])
    user_id = str(interaction.user.id)
    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
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
            "timestamp": (now - timedelta(days=1)).isoformat(),
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


def test_watch_apothecary_is_limited_to_three_verifies_per_utc_day():
    state, entry = _make_state(submitted_minutes_ago=0)
    interaction = _make_interaction(["Watch Apothecary"])
    user_id = str(interaction.user.id)
    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
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


def test_chief_apothecary_without_watch_apothecary_role_is_limited_to_three_verifies_per_utc_day():
    state, entry = _make_state(submitted_minutes_ago=0)
    interaction = _make_interaction(["Chief Apothecary"])
    user_id = str(interaction.user.id)
    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    state["verifier_actions"][user_id] = [
        {
            "action": "verify",
            "kill_log_id": "KL-9251",
            "timestamp": (now - timedelta(hours=1)).isoformat(),
        },
        {
            "action": "verify",
            "kill_log_id": "KL-9252",
            "timestamp": (now - timedelta(hours=2)).isoformat(),
        },
        {
            "action": "verify",
            "kill_log_id": "KL-9253",
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


def test_verify_is_blocked_after_mixed_three_reviews_in_same_utc_day():
    state, entry = _make_state(submitted_minutes_ago=120)
    interaction = _make_interaction(["Watch Apothecary"])
    user_id = str(interaction.user.id)
    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
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


def test_deny_is_blocked_after_mixed_three_reviews_in_same_utc_day():
    state, entry = _make_state(submitted_minutes_ago=KILL_LOG_REVIEW_DELAY_MINUTES + 5)
    interaction = _make_interaction(["Chief Apothecary"])
    user_id = str(interaction.user.id)
    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
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


def test_force_approve_deletes_apothecary_review_message_after_ruling():
    state, entry = _make_state(submitted_minutes_ago=10)
    entry["status"] = "under_review"
    entry["apo_notification_message_id"] = "555"
    interaction = _make_interaction(["Watch Apothecary"])

    with (
        patch.object(terminus_ops._g, "TERMINUS_SLAYER_LOCK", asyncio.Lock()),
        patch.object(terminus_ops, "_load_state", return_value=state),
        patch.object(terminus_ops, "_save_state"),
        patch.object(terminus_ops, "_delete_kill_log_message", new=AsyncMock()) as delete_kill_log,
        patch.object(terminus_ops, "_notify_class_complete", new=AsyncMock()) as notify_complete,
    ):
        _run(terminus_ops._handle_force_approve(interaction, "KL-0001"))

    assert entry["status"] == "force_approved"
    interaction.message.delete.assert_awaited_once()
    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once_with("✅ Kill log force-approved.", ephemeral=True)
    delete_kill_log.assert_awaited_once_with(
        interaction,
        entry,
        prefer_interaction_message=False,
    )
    notify_complete.assert_not_awaited()


def test_remove_entry_deletes_apothecary_review_message_and_posts_denial_notice():
    state, entry = _make_state(submitted_minutes_ago=10)
    entry["status"] = "under_review"
    entry["apo_notification_message_id"] = "556"
    interaction = _make_interaction(["Watch Apothecary"])

    with (
        patch.object(terminus_ops._g, "TERMINUS_SLAYER_LOCK", asyncio.Lock()),
        patch.object(terminus_ops, "_load_state", return_value=state),
        patch.object(terminus_ops, "_save_state"),
        patch.object(terminus_ops, "_delete_kill_log_message", new=AsyncMock()) as delete_kill_log,
        patch.object(terminus_ops, "_notify_kill_log_denied", new=AsyncMock()) as notify_denied,
    ):
        _run(terminus_ops._handle_remove_entry(interaction, "KL-0001"))

    assert entry["status"] == "rejected"
    interaction.message.delete.assert_awaited_once()
    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once_with("❌ Kill log entry removed from record.", ephemeral=True)
    delete_kill_log.assert_awaited_once_with(
        interaction,
        entry,
        prefer_interaction_message=False,
    )
    notify_denied.assert_awaited_once_with(interaction.guild, entry)
