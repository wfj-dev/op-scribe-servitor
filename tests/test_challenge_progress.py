import asyncio
import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from opscribe import _bot_globals as _g
from opscribe.constants import (
    BLACK_LAURELS_ROLE_ID,
    CRUX_TERMINATUS_ROLE_ID,
    OCTAVIAN_OPERATION_MEDAL_ROLE_ID,
)
from opscribe import aar_ops


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


def test_progress_save_streams_json_without_a_complete_serialized_copy(tmp_path, monkeypatch):
    path = tmp_path / "progress.json"
    monkeypatch.setattr(aar_ops, "CHALLENGE_PROGRESS_PATH", str(path))
    progress = {"42": {"notified": [], "black_laurels": [{"aar_id": str(index)} for index in range(1000)]}}
    with patch.object(aar_ops.json, "dumps", side_effect=AssertionError("Full JSON string allocation")):
        aar_ops._save_challenge_progress(progress)
    assert aar_ops._load_challenge_progress() == progress


def test_failed_progress_replace_leaves_current_archive_readable(tmp_path, monkeypatch):
    path = tmp_path / "progress.json"
    monkeypatch.setattr(aar_ops, "CHALLENGE_PROGRESS_PATH", str(path))
    monkeypatch.setattr(_g, "logger", MagicMock())
    original = {"42": {"notified": ["earned"]}}
    aar_ops._save_challenge_progress(original)
    monkeypatch.setattr(aar_ops.os, "replace", MagicMock(side_effect=OSError("replacement failed")))
    with pytest.raises(OSError, match="replacement failed"):
        aar_ops._save_challenge_progress({"42": {"notified": []}})
    assert aar_ops._load_challenge_progress() == original


@pytest.mark.parametrize("content", ['{"42":', '[]', '{"42":[]}'])
def test_corrupt_progress_is_not_treated_as_an_empty_archive(tmp_path, monkeypatch, content):
    path = tmp_path / "progress.json"
    path.write_text(content)
    monkeypatch.setattr(aar_ops, "CHALLENGE_PROGRESS_PATH", str(path))
    monkeypatch.setattr(_g, "logger", MagicMock())
    with pytest.raises(ValueError):
        aar_ops._load_challenge_progress()
    assert path.read_text() == content


def test_replay_buffer_never_loads_or_saves_progress_per_record(monkeypatch):
    load = MagicMock(side_effect=AssertionError("Unexpected archive read"))
    save = MagicMock(side_effect=AssertionError("Unexpected archive write"))
    monkeypatch.setattr(aar_ops, "_load_challenge_progress", load)
    monkeypatch.setattr(aar_ops, "_save_challenge_progress", save)
    member = SimpleNamespace(id=42, display_name="Synthetic Brother", roles=[SimpleNamespace(id=1, name="Watch Brother")])
    guild = SimpleNamespace(get_member=lambda _identity: member)
    progress = {}
    record = {"mission": "inferno", "brother_ids": ["42"], "aar_id": "10", "difficulty_class": "ruthless_ops"}
    _run(aar_ops._process_challenge_tracking(record, guild, track_announcements=False, progress_override=progress))
    assert progress["42"]["notified"] == []
    load.assert_not_called()
    save.assert_not_called()


def test_pending_award_intents_do_not_duplicate_evidence_urls():
    progress = {}
    urls = [f"https://discord.com/channels/1/2/{index}" for index in range(1000)]
    notification = ("42", "Crux Terminatus", aar_ops.CRUX_TERMINATUS_ROLE_ID, "crux_terminatus", urls)
    aar_ops._remember_pending_challenge_awards(progress, [notification])
    assert progress["42"]["pending_awards"]["crux_terminatus"][-1] == []
    assert len(urls) == 1000


def test_failed_rebuild_keeps_existing_progress_and_skips_unrelated_records(monkeypatch):
    original = {"42": {"notified": ["earned"]}, "99": {"notified": ["unrelated"]}}
    load = MagicMock(return_value=original)
    save = MagicMock()
    records = [{"aar_id": "unrelated", "brother_ids": ["99"]}, {"aar_id": "affected", "brother_ids": ["42"]}]
    monkeypatch.setattr(_g, "DATASTORE", SimpleNamespace(iter_records=lambda: iter(records)))
    monkeypatch.setattr(_g, "CHALLENGE_PROGRESS_LOCK", asyncio.Lock())
    monkeypatch.setattr(aar_ops, "_load_challenge_progress", load)
    monkeypatch.setattr(aar_ops, "_save_challenge_progress", save)
    process = AsyncMock(side_effect=RuntimeError("replay failed"))
    monkeypatch.setattr(aar_ops, "_process_challenge_tracking", process)
    with pytest.raises(RuntimeError, match="replay failed"):
        _run(aar_ops.rebuild_challenge_progress_for_users({"42"}, SimpleNamespace()))
    assert process.await_args.args[0]["aar_id"] == "affected"
    assert original == {"42": {"notified": ["earned"]}, "99": {"notified": ["unrelated"]}}
    load.assert_not_called()
    save.assert_not_called()


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


def test_rebuild_challenge_progress_revokes_only_bot_attributed_roles(monkeypatch):
    role = SimpleNamespace(id=aar_ops.KADAKU_CAMPAIGN_MEDAL_ROLE_ID, name="Kadaku Campaign Medal")

    class Member:
        def __init__(self, user_id):
            self.id = user_id
            self.bot = False
            self._roles = [role.id]
            self.removed = []

        @property
        def roles(self):
            return [role for role_id in self._roles if role_id == role.id]

        async def remove_roles(self, removed_role, reason=None):
            self.removed.append((removed_role.id, reason))
            self._roles = [role_id for role_id in self._roles if role_id != removed_role.id]

        async def add_roles(self, added_role, reason=None):
            self._roles.append(added_role.id)

    members = {"100": Member(100), "200": Member(200)}
    role_dates = {("100", "Kadaku Campaign Medal"): "2026-10-01T00:00:00+00:00"}
    progress = {"100": {"stale": [1]}, "200": {"stale": [2]}}
    processed = []

    class Records:
        def iter_records(self):
            return iter([
                {"aar_id": 1, "timestamp": "2026-10-01", "brother_ids": ["100", "200"]},
                {"aar_id": 2, "timestamp": "2026-10-02", "brother_ids": ["200"]},
            ])

        def get_role_acquisition_date(self, user_id, challenge_name):
            return role_dates.get((str(user_id), challenge_name))

        async def remove_role_acquisition_date(self, user_id, challenge_name):
            return role_dates.pop((str(user_id), challenge_name), None) is not None

        async def set_role_acquisition_date(self, user_id, challenge_name, timestamp):
            role_dates[(str(user_id), challenge_name)] = timestamp

        async def flush(self):
            return None

    class Guild:
        roles = [role]

        def get_member(self, user_id):
            return members.get(str(user_id))

        def get_role(self, role_id):
            return role if int(role_id) == role.id else None

    monkeypatch.setattr(_g, "DATASTORE", Records())
    monkeypatch.setattr(_g, "CHALLENGE_PROGRESS_LOCK", asyncio.Lock())
    load = MagicMock(return_value=progress)
    save = MagicMock(side_effect=lambda value: progress.update(value))
    monkeypatch.setattr(aar_ops, "_load_challenge_progress", load)
    monkeypatch.setattr(aar_ops, "_save_challenge_progress", save)

    async def process(record, guild, *, user_id_filter=None, track_announcements=True, progress_override=None):
        assert track_announcements is False
        for user_id in record["brother_ids"]:
            if user_id_filter is not None and user_id not in user_id_filter:
                continue
            if user_id == "100":
                assert all(item.id != role.id for item in guild.get_member(user_id).roles)
            aar_ids = progress_override.setdefault(str(user_id), {}).setdefault("aar_ids", [])
            if record["aar_id"] not in aar_ids:
                aar_ids.append(record["aar_id"])
        processed.append((record["aar_id"], list(record["brother_ids"])))
        return []

    monkeypatch.setattr(aar_ops, "_process_challenge_tracking", process)

    heartbeat_progress = []
    async def rebuild_with_heartbeat():
        async def heartbeat():
            while True:
                heartbeat_progress.append(len(processed))
                await asyncio.sleep(0)
        task = asyncio.create_task(heartbeat())
        try:
            return await aar_ops.rebuild_challenge_progress_for_users({"100", "200"}, Guild())
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    result = asyncio.run(rebuild_with_heartbeat())

    assert processed == [(1, ["100", "200"]), (2, ["200"]), (1, ["100", "200"]), (2, ["200"])]
    assert 1 in heartbeat_progress
    assert 3 in heartbeat_progress
    load.assert_called_once()
    save.assert_called_once()
    assert progress == {"100": {"aar_ids": [1]}, "200": {"aar_ids": [1, 2]}}
    assert members["100"].roles == []
    assert members["200"].roles == [role]
    assert role_dates == {}
    assert result["revoked"] == [{"user_id": "100", "challenge": "Kadaku Campaign Medal"}]
    assert result["review_required"] == [{"user_id": "200", "challenge": "Kadaku Campaign Medal"}]


def test_rebuild_challenge_progress_preserves_crux_prerequisite_roles(monkeypatch):
    black_laurels = SimpleNamespace(id=aar_ops.BLACK_LAURELS_ROLE_ID, name="Black Laurels")
    crux = SimpleNamespace(id=aar_ops.CRUX_TERMINATUS_ROLE_ID, name="Crux Terminatus")

    class Member:
        id = 100
        bot = False
        _roles = [black_laurels.id, crux.id]

        @property
        def roles(self):
            return [role for role in (black_laurels, crux) if role.id in self._roles]

        async def add_roles(self, role, reason=None):
            self._roles.append(role.id)

        async def remove_roles(self, role, reason=None):
            self._roles = [role_id for role_id in self._roles if role_id != role.id]

    member = Member()
    role_dates = {("100", "Crux Terminatus"): "2026-10-01T00:00:00+00:00"}
    progress = {}

    class Records:
        def iter_records(self):
            return iter([{"aar_id": 1, "brother_ids": ["100"], "timestamp": "2026-10-01"}])

        def get_role_acquisition_date(self, user_id, challenge_name):
            return role_dates.get((str(user_id), challenge_name))

        async def remove_role_acquisition_date(self, user_id, challenge_name):
            return role_dates.pop((str(user_id), challenge_name), None) is not None

        async def set_role_acquisition_date(self, user_id, challenge_name, timestamp):
            role_dates[(str(user_id), challenge_name)] = timestamp

        async def flush(self):
            return None

    class Guild:
        roles = [black_laurels, crux]

        def get_member(self, user_id):
            return member if int(user_id) == 100 else None

        def get_role(self, role_id):
            return next((role for role in self.roles if role.id == int(role_id)), None)

    monkeypatch.setattr(_g, "DATASTORE", Records())
    monkeypatch.setattr(_g, "CHALLENGE_PROGRESS_LOCK", asyncio.Lock())
    monkeypatch.setattr(aar_ops, "_load_challenge_progress", lambda: progress)
    monkeypatch.setattr(aar_ops, "_save_challenge_progress", lambda value: progress.update(value))

    async def process(_record, guild, *, user_id_filter=None, track_announcements=True, progress_override=None):
        assert track_announcements is False
        if user_id_filter and "100" not in user_id_filter:
            return []
        member_roles = {role.id for role in guild.get_member(100).roles}
        if black_laurels.id in member_roles:
            return [("100", "Crux Terminatus", crux.id, "crux_terminatus", [])]
        return []

    monkeypatch.setattr(aar_ops, "_process_challenge_tracking", process)

    result = asyncio.run(aar_ops.rebuild_challenge_progress_for_users({"100"}, Guild()))

    assert crux.id in member._roles
    assert role_dates[("100", "Crux Terminatus")]
    assert result["revoked"] == []
    assert result["review_required"] == [{"user_id": "100", "challenge": "Black Laurels"}]


def test_rebuild_challenge_progress_keeps_pipehitter_team_prerequisite(monkeypatch):
    pipehitter = SimpleNamespace(id=aar_ops.PIPEHITTER_ROLE_ID, name="Pipehitter")
    veteran = SimpleNamespace(id=1429340812902273044, name="Watch Veteran")

    class Member:
        id = 100
        bot = False
        _roles = [pipehitter.id, veteran.id]

        @property
        def roles(self):
            return [role for role in (pipehitter, veteran) if role.id in self._roles]

        async def add_roles(self, role, reason=None):
            self._roles.append(role.id)

        async def remove_roles(self, role, reason=None):
            self._roles = [role_id for role_id in self._roles if role_id != role.id]

    member = Member()
    role_dates = {("100", "SOK-G: Pipehitter"): "2026-10-01T00:00:00+00:00"}
    progress = {}

    class Records:
        def iter_records(self):
            return iter([{"aar_id": 1, "brother_ids": ["100"], "timestamp": "2026-10-01"}])

        def get_role_acquisition_date(self, user_id, challenge_name):
            return role_dates.get((str(user_id), challenge_name))

        async def remove_role_acquisition_date(self, user_id, challenge_name):
            return role_dates.pop((str(user_id), challenge_name), None) is not None

        async def set_role_acquisition_date(self, user_id, challenge_name, timestamp):
            role_dates[(str(user_id), challenge_name)] = timestamp

        async def flush(self):
            return None

    class Guild:
        roles = [pipehitter, veteran]

        def get_member(self, user_id):
            return member if int(user_id) == 100 else None

        def get_role(self, role_id):
            return next((role for role in self.roles if role.id == int(role_id)), None)

    monkeypatch.setattr(_g, "DATASTORE", Records())
    monkeypatch.setattr(_g, "CHALLENGE_PROGRESS_LOCK", asyncio.Lock())
    monkeypatch.setattr(aar_ops, "_load_challenge_progress", lambda: progress)
    monkeypatch.setattr(aar_ops, "_save_challenge_progress", lambda value: progress.update(value))

    async def process(record, guild, *, user_id_filter=None, track_announcements=True, progress_override=None):
        assert track_announcements is False
        candidate = guild.get_member(100)
        assert pipehitter.id in {role.id for role in candidate.roles}
        entries = progress_override.setdefault("100", {}).setdefault("sok_g_pipehitter", [])
        if not any(item.get("aar_id") == record["aar_id"] for item in entries):
            entries.append({"aar_id": record["aar_id"], "mission": "inferno"})
        return []

    monkeypatch.setattr(aar_ops, "_process_challenge_tracking", process)

    result = asyncio.run(aar_ops.rebuild_challenge_progress_for_users({"100"}, Guild()))

    assert progress["100"]["sok_g_pipehitter"] == [{"aar_id": 1, "mission": "inferno"}]
    assert pipehitter.id in member._roles
    assert result["revoked"] == []
    assert result["review_required"] == []


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


def test_challenge_progress_crux_rank_a_uses_any_post_enforcement_rank_a_reclamation_run():
    uid = 993706776920854578

    class _FakeDataStore:
        def __init__(self, records):
            self._records = records

        def iter_records(self):
            return iter(self._records)

    records = [
        {
            "mission": "Inferno",
            "rank": "A",
            "black_laurels_in_mission": True,
            "brother_ids": [uid],
            "timestamp": "2026-06-10T00:00:00+00:00",
        },
        {
            "mission": "Decapitation",
            "rank": "A",
            "black_laurels_in_mission": True,
            "brother_ids": [uid],
            "timestamp": "2026-06-11T00:00:00+00:00",
        },
        {
            "mission": "Vox Liberatis",
            "rank": "A",
            "black_laurels_in_mission": True,
            "brother_ids": [uid],
            "timestamp": "2026-06-12T00:00:00+00:00",
        },
        {
            "mission": "Ballistic Engine",
            "rank": "A",
            "black_laurels_in_mission": True,
            "brother_ids": [uid],
            "timestamp": "2026-06-13T00:00:00+00:00",
        },
        {
            "mission": "Exfiltration",
            "rank": "A",
            "black_laurels_in_mission": True,
            "brother_ids": [uid],
            "timestamp": "2026-06-14T00:00:00+00:00",
        },
        {
            "mission": "Termination",
            "rank": "A",
            "black_laurels_in_mission": True,
            "brother_ids": [uid],
            "timestamp": "2026-06-15T00:00:00+00:00",
        },
        {
            "mission": "Reclamation",
            "rank": "B",
            "black_laurels_in_mission": True,
            "brother_ids": [uid],
            "timestamp": "2026-06-20T00:00:00+00:00",
        },
        {
            "mission": "Reclamation",
            "rank": "A",
            "black_laurels_in_mission": True,
            "brother_ids": [uid],
            "timestamp": "2026-07-05T00:00:00+00:00",
        },
        {
            "mission": "Disruption",
            "rank": "A",
            "black_laurels_in_mission": True,
            "brother_ids": [uid],
            "timestamp": "2026-07-10T00:00:00+00:00",
        },
        {
            "mission": "Purgation",
            "rank": "A",
            "black_laurels_in_mission": True,
            "brother_ids": [uid],
            "timestamp": "2026-07-11T00:00:00+00:00",
        },
    ]

    interaction = _make_interaction(target_role_ids=[BLACK_LAURELS_ROLE_ID], user_id=uid)

    with (
        patch.object(terminus_ops, "_load_state", return_value={"progress": {}}),
        patch.object(terminus_ops._g, "DATASTORE", _FakeDataStore(records)),
        patch("opscribe.terminus_ops.os.path.exists", return_value=False),
    ):
        _run(terminus_ops._challenge_progress_inner(interaction, member=None, verbose=True))

    interaction.followup.send.assert_awaited_once()
    embed = interaction.followup.send.await_args.kwargs["embed"]
    crux_block = _extract_crux_block(embed)

    assert crux_block
    assert "✅ Black Laurels — baseline missions, Rank A" in crux_block
    assert "Non-Rank A (post-enforcement): Reclamation" not in crux_block


def test_challenge_progress_octavian_role_holder_shows_completed_bar():
    interaction = _make_interaction(target_role_ids=[OCTAVIAN_OPERATION_MEDAL_ROLE_ID], user_id=444)

    with (
        patch.object(terminus_ops, "_load_state", return_value={"progress": {}}),
        patch.object(terminus_ops._g, "DATASTORE", None),
        patch("opscribe.terminus_ops.os.path.exists", return_value=False),
    ):
        _run(terminus_ops._challenge_progress_inner(interaction, member=None, verbose=True))

    interaction.followup.send.assert_awaited_once()
    embed = interaction.followup.send.await_args.kwargs["embed"]
    joined_values = "\n".join(field.value or "" for field in embed.fields)

    assert "Octavian Operation Medal" in joined_values
    assert "4/4" in joined_values
