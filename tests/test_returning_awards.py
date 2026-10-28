import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from opscribe import aar_ops, terminus_ops


class _AsyncLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


@pytest.fixture
def returning_member(monkeypatch):
    rank = SimpleNamespace(id=1, name="Watch Brother")
    member = SimpleNamespace(id=42, display_name="Returning Brother", roles=[rank])

    async def add_roles(*roles, **_kwargs):
        member.roles.extend(role for role in roles if role not in member.roles)

    member.add_roles = AsyncMock(side_effect=add_roles)
    roles = {}
    guild = SimpleNamespace(
        id=10, get_member=lambda identity: member if int(identity) == member.id else None,
        get_role=lambda identity: roles.get(identity),
        fetch_member=AsyncMock(return_value=member),
    )
    progress = {str(member.id): {"notified": []}}
    datastore = SimpleNamespace(
        get_role_acquisition_date=MagicMock(return_value="2026-01-01T00:00:00Z"),
        set_role_acquisition_date=AsyncMock(),
        iter_records=lambda: iter([]),
    )
    monkeypatch.setattr(aar_ops._g, "CHALLENGE_PROGRESS_LOCK", _AsyncLock())
    monkeypatch.setattr(aar_ops._g, "DATASTORE", datastore)
    monkeypatch.setattr(aar_ops._g, "logger", MagicMock())
    monkeypatch.setattr(aar_ops._g, "TERMINUS_SLAYER_LOCK", _AsyncLock())
    monkeypatch.setattr(aar_ops, "_load_challenge_progress", lambda: progress)
    monkeypatch.setattr(aar_ops, "_save_challenge_progress", MagicMock())
    record_announcement = AsyncMock()
    send = aar_ops._send_challenge_eligibility_notifications

    async def send_or_restore(notifications, guild, *, announce=True):
        if announce:
            await record_announcement(notifications, guild)
        else:
            await send(notifications, guild, announce=False)

    monkeypatch.setattr(aar_ops, "_send_challenge_eligibility_notifications", send_or_restore)
    return SimpleNamespace(
        member=member, guild=guild, roles=roles, progress=progress["42"],
        datastore=datastore, announce=record_announcement, send=send,
    )


@pytest.mark.parametrize("spec", aar_ops._SIMPLE_CHALLENGE_SPECS)
def test_sweep_restores_previously_announced_completed_medal_without_reannouncement(returning_member, spec):
    env = returning_member
    key, required, role_id, name, _award_type, notified_key = spec
    role = env.roles[role_id] = SimpleNamespace(id=role_id, name=name)
    env.progress.update({
        key: [{"mission": mission} for mission in required],
        "notified": [notified_key],
    })

    assert asyncio.run(aar_ops._sweep_challenge_completions(env.guild)) == 0
    assert role in env.member.roles
    env.announce.assert_not_awaited()
    assert env.progress["notified"] == [notified_key]
    env.datastore.set_role_acquisition_date.assert_not_awaited()

    asyncio.run(aar_ops._sweep_challenge_completions(env.guild))
    env.member.add_roles.assert_awaited_once()


def test_notified_flag_alone_does_not_restore_incomplete_award(returning_member):
    env = returning_member
    env.progress["notified"] = ["kadaku_campaign", "herisor_defense", "crux_terminatus"]
    asyncio.run(aar_ops._sweep_challenge_completions(env.guild))
    env.member.add_roles.assert_not_awaited()
    env.announce.assert_not_awaited()


def test_new_completion_still_uses_normal_announcement_path(returning_member):
    env = returning_member
    role_id = aar_ops.HERISOR_DEFENSE_MEDAL_ROLE_ID
    env.roles[role_id] = SimpleNamespace(id=role_id, name="Herisor Defense Medal")
    env.progress["herisor_defense_siege"] = [{}]

    assert asyncio.run(aar_ops._sweep_challenge_completions(env.guild)) == 1
    env.announce.assert_awaited_once()
    assert env.announce.await_args.args[0][0][2] == role_id
    assert env.progress["notified"] == ["herisor_defense"]


@pytest.mark.parametrize("failure", ["role", "channel", "queue"])
def test_failed_new_challenge_delivery_remains_pending_and_retries(returning_member, monkeypatch, failure):
    env = returning_member
    role_id = aar_ops.HERISOR_DEFENSE_MEDAL_ROLE_ID
    env.roles[role_id] = SimpleNamespace(id=role_id, name="Herisor Defense Medal")
    env.progress["herisor_defense_siege"] = [{}]
    queue = MagicMock()
    channel = AsyncMock(return_value=SimpleNamespace(id=99))
    monkeypatch.setattr(aar_ops, "_b", lambda name: {
        "HOME_CHAPTERS": [], "_enqueue_award_announcement": queue,
        "_get_award_announcement_channel": channel,
    }.get(name))
    monkeypatch.setattr(aar_ops, "_send_challenge_eligibility_notifications", env.send)
    monkeypatch.setattr(aar_ops.asyncio, "sleep", AsyncMock())
    if failure == "role":
        env.member.add_roles.side_effect = RuntimeError("permissions")
    elif failure == "channel":
        channel.return_value = None
    else:
        queue.side_effect = OSError("disk full")

    asyncio.run(aar_ops._sweep_challenge_completions(env.guild))
    assert "herisor_defense_medal" in env.progress["pending_awards"]

    env.member.add_roles.side_effect = lambda *roles, **_kwargs: env.member.roles.extend(roles)
    channel.return_value = SimpleNamespace(id=99)
    queue.side_effect = None
    queue.reset_mock()
    asyncio.run(aar_ops._sweep_challenge_completions(env.guild))
    queue.assert_called_once()
    assert env.progress["pending_awards"] == {}
    assert env.progress["notified"] == ["herisor_defense"]
    asyncio.run(aar_ops._sweep_challenge_completions(env.guild))
    queue.assert_called_once()


@pytest.mark.parametrize(("key", "notified_key", "role_id", "entries"), [
    ("sok_g_pipehitter", "sok_g_pipehitter", aar_ops.PIPEHITTER_ROLE_ID, [{"mission": "inferno"}]),
    ("sok_g_pipehitter", "distinguished_sok_g_pipehitter", aar_ops.DISTINGUISHED_PIPEHITTER_ROLE_ID, [{"mission": "inferno"}, {"mission": "termination"}]),
    ("herisor_defense_siege", "herisor_defense", aar_ops.HERISOR_DEFENSE_MEDAL_ROLE_ID, [{"black_laurels": True}]),
    ("herisor_defense_siege", "distinguished_herisor_defense", aar_ops.DISTINGUISHED_HERISOR_DEFENSE_MEDAL_ROLE_ID, [{"black_laurels": True}]),
])
def test_nonstandard_medals_are_restored_quietly(returning_member, key, notified_key, role_id, entries):
    env = returning_member
    role = env.roles[role_id] = SimpleNamespace(id=role_id, name="Medal")
    env.progress.update({key: entries, "notified": [notified_key]})
    asyncio.run(aar_ops._sweep_challenge_completions(env.guild))
    assert role in env.member.roles
    for call in env.announce.await_args_list:
        assert all(item[2] != role_id for item in call.args[0])


def test_restoration_failure_is_logged_and_retried(returning_member, monkeypatch):
    import discord

    env = returning_member
    role_id = aar_ops.HERISOR_DEFENSE_MEDAL_ROLE_ID
    role = env.roles[role_id] = SimpleNamespace(id=role_id, name="Herisor Defense Medal")
    env.progress.update({"herisor_defense_siege": [{}], "notified": ["herisor_defense"]})
    response = SimpleNamespace(status=403, reason="Forbidden")
    env.member.add_roles.side_effect = discord.Forbidden(response, "Missing permissions")
    logger = MagicMock()
    monkeypatch.setattr(aar_ops._g, "logger", logger)

    asyncio.run(aar_ops._sweep_challenge_completions(env.guild))
    assert role not in env.member.roles
    logger.warning.assert_called()
    env.announce.assert_not_awaited()

    env.member.add_roles.side_effect = lambda *roles, **_kwargs: env.member.roles.extend(roles)
    asyncio.run(aar_ops._sweep_challenge_completions(env.guild))
    assert role in env.member.roles


def test_returning_inductee_does_not_receive_medals_before_rank_assignment(returning_member):
    env = returning_member
    env.member.roles = [SimpleNamespace(id=2, name="Watch Inductee")]
    env.progress.update({"herisor_defense_siege": [{}], "notified": ["herisor_defense"]})
    asyncio.run(aar_ops._sweep_challenge_completions(env.guild))
    env.member.add_roles.assert_not_awaited()


@pytest.mark.parametrize("complete", [True, False])
def test_terminus_class_restoration_uses_verified_kills_not_role_history(returning_member, monkeypatch, complete):
    env = returning_member
    role_id = next(iter(terminus_ops.TERMINUS_SLAYER_CLASS_AWARD_TYPES))
    role = env.roles[role_id] = SimpleNamespace(id=role_id, name="Terminus Slayer (Assault)")
    class_progress = {name: 3 for name in terminus_ops.TERMINUS_TYPES}
    if not complete:
        class_progress[terminus_ops.TERMINUS_TYPES[-1]] = 2
    state = {"progress": {"42": {str(role_id): class_progress}}}
    monkeypatch.setattr(terminus_ops, "_load_state", lambda: state)

    asyncio.run(terminus_ops.restore_completed_award_roles(env.guild))
    assert (role in env.member.roles) is complete
    env.announce.assert_not_awaited()


def test_master_terminus_requires_all_classes_verified_even_if_roles_are_held(returning_member, monkeypatch):
    env = returning_member
    for role_id in terminus_ops.TERMINUS_SLAYER_CLASS_AWARD_TYPES:
        env.roles[role_id] = SimpleNamespace(id=role_id, name="Terminus Slayer")
    master_id = terminus_ops.MASTER_TERMINUS_SLAYER_ROLE_ID
    master = env.roles[master_id] = SimpleNamespace(id=master_id, name="Master Terminus Slayer")
    progress = {
        str(role_id): {name: 3 for name in terminus_ops.TERMINUS_TYPES}
        for role_id in terminus_ops.TERMINUS_SLAYER_CLASS_AWARD_TYPES
    }
    missing_class = next(iter(progress))
    progress[missing_class][terminus_ops.TERMINUS_TYPES[-1]] = 2
    env.member.roles.extend(env.roles[role_id] for role_id in terminus_ops.TERMINUS_SLAYER_CLASS_AWARD_TYPES)
    monkeypatch.setattr(terminus_ops, "_load_state", lambda: {"progress": {"42": progress}})

    asyncio.run(terminus_ops.restore_completed_award_roles(env.guild))
    assert master not in env.member.roles

    progress[missing_class][terminus_ops.TERMINUS_TYPES[-1]] = 3
    asyncio.run(terminus_ops.restore_completed_award_roles(env.guild))
    assert master in env.member.roles
    env.announce.assert_not_awaited()


@pytest.mark.parametrize("audit_complete", [True, False])
def test_crux_restoration_requires_current_rank_a_audit(returning_member, monkeypatch, audit_complete):
    from opscribe import challenge_policy

    env = returning_member
    crux_id = aar_ops.CRUX_TERMINATUS_ROLE_ID
    crux = env.roles[crux_id] = SimpleNamespace(id=crux_id, name="Crux Terminatus")
    env.member.roles = [
        SimpleNamespace(id=1, name="Watch Veteran"),
        SimpleNamespace(id=aar_ops.BLACK_LAURELS_ROLE_ID, name="Black Laurels"),
        *(SimpleNamespace(id=role_id, name="Terminus Slayer") for role_id in list(aar_ops.TERMINUS_SLAYER_ROLE_IDS)[:2]),
    ]
    env.progress.update({"notified": ["crux_terminatus"], "sok_g_pipehitter": [{}, {}]})
    audit = MagicMock(return_value={"all_rank_a": audit_complete})
    monkeypatch.setattr(challenge_policy, "evaluate_crux_bl_rank_a", audit)

    asyncio.run(aar_ops._restore_crux_terminatus_roles(env.guild))

    assert (crux in env.member.roles) is audit_complete
    audit.assert_called_once_with("42", [])
    env.announce.assert_not_awaited()


def test_crux_skips_members_without_prerequisite_rank_and_progress(returning_member):
    env = returning_member
    asyncio.run(aar_ops._restore_crux_terminatus_roles(env.guild))
    env.member.add_roles.assert_not_awaited()
    env.guild.fetch_member.assert_not_awaited()


@pytest.mark.parametrize("complete", [True, False])
def test_first_crux_is_evaluated_without_another_aar_using_fresh_roles(returning_member, complete):
    env = returning_member
    env.member.roles = [SimpleNamespace(id=1, name="Watch Veteran")]
    env.progress["sok_g_pipehitter"] = [{"aar_id": "10"}, {"aar_id": "11"}]
    fresh_roles = [
        *env.member.roles,
        SimpleNamespace(id=aar_ops.BLACK_LAURELS_ROLE_ID, name="Black Laurels"),
        *(SimpleNamespace(id=role_id, name="Terminus Slayer") for role_id in list(aar_ops.TERMINUS_SLAYER_ROLE_IDS)[:2]),
    ]
    env.guild.fetch_member.return_value = SimpleNamespace(id=42, roles=fresh_roles)
    records = [
        {"brother_ids": ["42"], "mission": mission, "rank": "A",
         "black_laurels_in_mission": True, "timestamp": "2026-10-27T00:00:00Z"}
        for mission in sorted(aar_ops.BLACK_LAURELS_REQUIRED_MISSIONS)
    ]
    if not complete:
        records[-1]["rank"] = "B"
    env.datastore.iter_records = lambda: iter(records)

    asyncio.run(aar_ops._restore_crux_terminatus_roles(env.guild))

    env.guild.fetch_member.assert_awaited_once_with(42)
    if complete:
        env.announce.assert_awaited_once()
        assert env.announce.await_args.args[0] == [
            ("42", "Crux Terminatus", aar_ops.CRUX_TERMINATUS_ROLE_ID, "crux_terminatus", []),
        ]
    else:
        env.announce.assert_not_awaited()


def test_quiet_role_restoration_never_resolves_announcement_channels_or_enqueues(returning_member, monkeypatch):
    env = returning_member
    role_id = aar_ops.HERISOR_DEFENSE_MEDAL_ROLE_ID
    env.roles[role_id] = SimpleNamespace(id=role_id, name="Herisor Defense Medal")
    env.progress.update({"herisor_defense_siege": [{}], "notified": ["herisor_defense"]})
    queue = MagicMock()
    channel = AsyncMock()
    monkeypatch.setattr(aar_ops, "_b", lambda name: {
        "HOME_CHAPTERS": [], "_enqueue_award_announcement": queue,
        "_get_award_announcement_channel": channel,
    }.get(name))

    asyncio.run(aar_ops._sweep_challenge_completions(env.guild))
    env.member.add_roles.assert_awaited_once()
    queue.assert_not_called()
    channel.assert_not_awaited()


@pytest.mark.parametrize("complete", [True, False])
def test_crux_restoration_runs_real_archive_audit(returning_member, complete):
    env = returning_member
    crux_id = aar_ops.CRUX_TERMINATUS_ROLE_ID
    crux = env.roles[crux_id] = SimpleNamespace(id=crux_id, name="Crux Terminatus")
    env.member.roles = [
        SimpleNamespace(id=1, name="Watch Veteran"),
        SimpleNamespace(id=aar_ops.BLACK_LAURELS_ROLE_ID, name="Black Laurels"),
        *(SimpleNamespace(id=role_id, name="Terminus Slayer") for role_id in list(aar_ops.TERMINUS_SLAYER_ROLE_IDS)[:2]),
    ]
    env.progress.update({"notified": ["crux_terminatus"], "sok_g_pipehitter": [{}, {}]})
    records = [
        {
            "brother_ids": ["42"], "mission": mission, "rank": "A",
            "black_laurels_in_mission": True, "timestamp": "2026-10-22T00:00:00Z",
        }
        for mission in sorted(aar_ops.BLACK_LAURELS_REQUIRED_MISSIONS)
    ]
    if not complete:
        records.pop()
    env.datastore.iter_records = lambda: iter(records)

    asyncio.run(aar_ops._restore_crux_terminatus_roles(env.guild))
    assert (crux in env.member.roles) is complete
    env.announce.assert_not_awaited()


def test_restoration_records_provenance_only_when_absent(returning_member):
    env = returning_member
    env.datastore.get_role_acquisition_date.return_value = None
    role_id = aar_ops.HERISOR_DEFENSE_MEDAL_ROLE_ID
    env.roles[role_id] = SimpleNamespace(id=role_id, name="Herisor Defense Medal")
    env.progress.update({"herisor_defense_siege": [{}], "notified": ["herisor_defense"]})

    asyncio.run(aar_ops._sweep_challenge_completions(env.guild))
    env.datastore.set_role_acquisition_date.assert_awaited_once()
    assert env.datastore.set_role_acquisition_date.await_args.args[:2] == ("42", "Herisor Defense Medal")


def test_herisor_valor_restores_only_with_both_black_laurels_fronts(returning_member):
    env = returning_member
    role_id = aar_ops.DISTINGUISHED_HERISOR_DEFENSE_MEDAL_WITH_VALOR_ROLE_ID
    role = env.roles[role_id] = SimpleNamespace(id=role_id, name="Distinguished Herisor Defense Medal with Valor")
    env.progress.update({
        "notified": ["herisor_defense", "distinguished_herisor_defense", "distinguished_herisor_defense_valor"],
        "herisor_defense_siege": [{"black_laurels": True}],
        "herisor_defense_termination": [{"black_laurels": True}],
    })

    asyncio.run(aar_ops._sweep_challenge_completions(env.guild))
    assert role not in env.member.roles
    env.progress["herisor_defense_reclamation"] = [{"black_laurels": True}]
    asyncio.run(aar_ops._sweep_challenge_completions(env.guild))
    assert role in env.member.roles
    env.announce.assert_not_awaited()


def test_legacy_herisor_evidence_still_restores_previously_awarded_medal(returning_member):
    env = returning_member
    role_id = aar_ops.DISTINGUISHED_HERISOR_DEFENSE_MEDAL_WITH_VALOR_ROLE_ID
    role = env.roles[role_id] = SimpleNamespace(id=role_id, name="Distinguished Herisor Defense Medal with Valor")
    env.progress.update({
        "notified": ["herisor_defense", "distinguished_herisor_defense", "distinguished_herisor_defense_valor"],
        "defense_of_herisor_submissions": [{"distinguished_with_valor": True}],
    })

    asyncio.run(aar_ops._sweep_challenge_completions(env.guild))
    assert role in env.member.roles
    env.announce.assert_not_awaited()
