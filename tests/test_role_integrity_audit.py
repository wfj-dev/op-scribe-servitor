import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import opscribe.bot as bot
import opscribe.roster_ops as ro


def _run(coro):
    return asyncio.run(coro)


def _make_member(member_id: int, role_names: list[str]):
    roles = [SimpleNamespace(name=name, id=idx + 1000) for idx, name in enumerate(role_names)]
    return SimpleNamespace(id=member_id, display_name=f"Member{member_id}", name=f"Member{member_id}", bot=False, roles=roles)


def test_save_role_integrity_state_creates_parent_directory(monkeypatch, tmp_path: Path):
    state_path = tmp_path / "nested" / "audit" / "state.json"
    monkeypatch.setattr(ro._g, "CONFIG", {"role_integrity_audit": {"state_path": str(state_path)}})

    ro._save_role_integrity_state({"last_run_date": "2026-06-26"})

    assert state_path.exists()
    assert json.loads(state_path.read_text())["last_run_date"] == "2026-06-26"


def test_post_role_integrity_findings_accepts_preformatted_role_mention(monkeypatch):
    sent_messages = []

    class _Channel:
        async def send(self, content=None, embed=None):
            sent_messages.append({"content": content, "embed": embed})

    channel = _Channel()
    guild = SimpleNamespace(get_channel=lambda _cid: channel)
    monkeypatch.setattr(
        ro._g,
        "CONFIG",
        {"target_packages": {"highcom_audit_channel_id": "123", "highcom_role_id": "<@&456>"}},
    )

    findings = [{"member_id": 1, "code": "watch_command_missing", "detail": "Expected Watch Command role is missing."}]
    posted = _run(ro._post_role_integrity_findings(guild, findings))

    assert posted is True
    assert sent_messages[0]["content"] == "<@&456>"


def test_collect_role_integrity_findings_reports_track_and_prereq_conflicts(monkeypatch):
    member = _make_member(7, ["Watch Brother", "Watch Captain", "Oathsworn"])
    guild = SimpleNamespace(members=[member], get_role=lambda _rid: None)

    monkeypatch.setattr(ro._g, "CONFIG", {"role_integrity_audit": {}, "companies": {}})
    monkeypatch.setattr(bot, "ALLOWED_KT_ROLE_IDS", set())

    findings = _run(ro._collect_role_integrity_findings(guild))
    codes = {f["code"] for f in findings}

    assert "command_skip" in codes
    assert "oathsworn_skip" in codes
    assert "track_mixing" in codes
    assert "oathsworn_terminal" in codes


def test_role_integrity_audit_loop_respects_hour_gate(monkeypatch):
    now = datetime(2026, 6, 26, 9, 0, tzinfo=timezone.utc)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    loop_globals = ro._role_integrity_audit_loop.coro.__globals__
    monkeypatch.setitem(loop_globals, "datetime", _FixedDateTime)
    monkeypatch.setattr(ro, "_role_integrity_cfg", lambda: {})
    monkeypatch.setattr(ro, "_schedule_role_audit_hour_utc", lambda: 12)
    collect_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(ro, "_collect_role_integrity_findings", collect_mock)

    _run(ro._role_integrity_audit_loop.coro())

    collect_mock.assert_not_awaited()


def test_role_integrity_audit_loop_skips_if_already_ran_today(monkeypatch):
    now = datetime(2026, 6, 26, 15, 0, tzinfo=timezone.utc)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    loop_globals = ro._role_integrity_audit_loop.coro.__globals__
    monkeypatch.setitem(loop_globals, "datetime", _FixedDateTime)
    monkeypatch.setattr(ro, "_role_integrity_cfg", lambda: {})
    monkeypatch.setattr(ro, "_schedule_role_audit_hour_utc", lambda: 12)
    monkeypatch.setattr(ro, "_load_role_integrity_state", lambda: {"last_run_date": "2026-06-26"})
    collect_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(ro, "_collect_role_integrity_findings", collect_mock)

    _run(ro._role_integrity_audit_loop.coro())

    collect_mock.assert_not_awaited()
