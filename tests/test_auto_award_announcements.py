import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import opscribe.bot as bot
import opscribe.forge_ops as forge_ops
import opscribe.roster_ops as roster_ops
from opscribe.constants import CHALLENGE_ROLES, DISTINGUISHED_OCTAVIAN_OPERATION_MEDAL_ROLE_ID


def _role(role_id: int, name: str):
    return SimpleNamespace(id=role_id, name=name, mention=f"<@&{role_id}>")


class _AsyncLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_website_replaced_submit_aar_is_not_registered():
    assert bot.bot.tree.get_command("submit_aar") is None


def test_website_replaced_tally_deeds_is_not_registered():
    assert bot.bot.tree.get_command("tally_deeds") is None
    assert not hasattr(roster_ops, "tally_deeds")
    assert "tally_deeds" not in roster_ops.__all__
    for helper in ("_forum_post_autocomplete", "_completed_challenge_labels",
                   "_compute_killteam_sendto_snapshot_7d", "_get_killteam_renown_summary",
                   "_record_has_black_laurels_tag", "_embed_from_ansi"):
        assert not hasattr(roster_ops, helper)
    config = json.loads((Path(__file__).resolve().parents[1] / "config/config.json").read_text())
    assert "tally_deeds" not in config["permissions"]
    assert callable(roster_ops.combat_bonds)
    assert callable(roster_ops.submit_portrait)


def test_watch_veteran_attachment_uses_new_rank_badge_on_every_open():
    expected = Path(forge_ops._ASSETS_DIR) / "New Rank Badges" / "2-Watch Veteran.png"
    for _attempt in range(2):
        attachment = forge_ops._get_award_image("award_watch_veteran.png")
        assert attachment is not None
        try:
            assert attachment.filename == "award_watch_veteran.png"
            assert Path(attachment.fp.name) == expected
            assert attachment.fp.read() == expected.read_bytes()
        finally:
            attachment.close()


def test_award_queue_deduplicates_automatic_jobs_but_allows_staff_requeue(tmp_path, monkeypatch):
    monkeypatch.setattr(roster_ops, "AWARD_QUEUE_PATH", str(tmp_path / "queue.json"))
    args = ("42", "watch_veteran", "Unknown", "99", "10")
    roster_ops._enqueue_award_announcement(*args)
    roster_ops._enqueue_award_announcement(*args)
    assert len(roster_ops._load_award_queue()) == 1
    roster_ops._enqueue_award_announcement(*args, force=True)
    jobs = roster_ops._load_award_queue()
    assert len(jobs) == 2
    assert jobs[0]["job_id"] != jobs[1]["job_id"]


def test_award_queue_write_failure_is_observable(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(roster_ops, "AWARD_QUEUE_PATH", str(tmp_path / "missing" / "queue.json"))
    with pytest.raises(OSError):
        roster_ops._enqueue_award_announcement("42", "watch_veteran", "Unknown", "99", "10")


def test_award_queue_invalid_shape_is_not_overwritten(tmp_path, monkeypatch):
    import pytest
    path = tmp_path / "queue.json"
    monkeypatch.setattr(roster_ops, "AWARD_QUEUE_PATH", str(path))
    for data in ({"jobs": []}, [None]):
        path.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="list of job objects"):
            roster_ops._enqueue_award_announcement("42", "watch_veteran", "Unknown", "99", "10")
        assert json.loads(path.read_text()) == data


def test_award_dispatch_preserves_jobs_enqueued_while_sending(tmp_path, monkeypatch):
    monkeypatch.setattr(roster_ops, "AWARD_QUEUE_PATH", str(tmp_path / "queue.json"))
    roster_ops._enqueue_award_announcement("42", "watch_veteran", "Unknown", "99", "10")

    async def send(*_args, **_kwargs):
        roster_ops._enqueue_award_announcement("43", "watch_veteran", "Unknown", "99", "10")

    channel = SimpleNamespace(id=99, send=AsyncMock(side_effect=send))
    guild = SimpleNamespace(get_member=lambda _uid: SimpleNamespace(id=42), get_channel=lambda _cid: channel)
    monkeypatch.setattr(roster_ops._g.bot, "get_guild", lambda _gid: guild)
    monkeypatch.setattr(bot, "_get_watch_veteran_announcement", lambda **_kwargs: ("award", MagicMock(), None))
    asyncio.run(roster_ops._award_announcement_dispatch_loop.coro())
    channel.send.assert_awaited_once()
    assert [job["member_id"] for job in roster_ops._load_award_queue()] == ["43"]


def test_award_dispatch_retains_unavailable_guild_for_review(tmp_path, monkeypatch):
    monkeypatch.setattr(roster_ops, "AWARD_QUEUE_PATH", str(tmp_path / "queue.json"))
    roster_ops._enqueue_award_announcement("42", "watch_veteran", "Unknown", "99", "10")
    monkeypatch.setattr(roster_ops._g.bot, "get_guild", lambda _gid: None)
    monkeypatch.setattr(roster_ops, "_dm_award_failure", AsyncMock())
    for _attempt in range(6):
        asyncio.run(roster_ops._award_announcement_dispatch_loop.coro())
    jobs = roster_ops._load_award_queue()
    assert len(jobs) == 1
    assert jobs[0]["attempts"] == 5
    assert jobs[0]["status"] == "needs_review"


def test_challenge_sweep_restores_dependencies_before_auditing_crux(monkeypatch):
    guild = SimpleNamespace(id=10)
    calls = []

    async def restore_terminus(actual_guild):
        assert actual_guild is guild
        calls.append("terminus")

    async def sweep(actual_guild):
        assert actual_guild is guild
        calls.append("challenges")
        return 0

    async def restore_crux(actual_guild):
        assert actual_guild is guild
        calls.append("crux")

    monkeypatch.setattr(bot, "_resolve_notification_guild", lambda: guild)
    monkeypatch.setattr(bot._terminus_ops, "restore_completed_award_roles", restore_terminus)
    monkeypatch.setattr(bot, "_sweep_challenge_completions", sweep)
    monkeypatch.setattr(bot, "_restore_crux_terminatus_roles", restore_crux)

    asyncio.run(bot._challenge_sweep_loop.coro())

    assert calls == ["terminus", "challenges", "crux"]


def test_get_award_announcement_channel_prefers_kt_role_channel_map():
    mapped_channel = MagicMock()
    guild = MagicMock()
    guild.get_channel = MagicMock(side_effect=lambda cid: mapped_channel if cid == 999 else None)
    guild.active_threads = AsyncMock(return_value=[])
    member = SimpleNamespace(id=42, roles=[SimpleNamespace(id=1234, name="Kill Team Example")])

    with patch.object(bot, "KT_ROLE_CHANNEL_MAP", {1234: 999}):
        resolved = asyncio.run(bot._get_award_announcement_channel(member, guild))

    assert resolved is mapped_channel
    guild.active_threads.assert_not_awaited()


def test_get_award_announcement_channel_falls_back_to_service_studs_channel():
    fallback_channel = MagicMock()
    guild = MagicMock()
    guild.get_channel = MagicMock(side_effect=lambda cid: fallback_channel if cid == bot.SERVICE_STUDS_CHANNEL_ID else None)
    guild.active_threads = AsyncMock(return_value=[])
    member = SimpleNamespace(id=7, roles=[])

    with (
        patch.object(bot, "KT_ROLE_CHANNEL_MAP", {}),
        patch.object(bot, "ALLOWED_KT_FORUM_PARENT_IDS", set()),
        patch("opscribe.forge_ops._resolve_killteam_for_member", return_value=None),
    ):
        resolved = asyncio.run(bot._get_award_announcement_channel(member, guild))

    assert resolved is fallback_channel
    guild.get_channel.assert_called_with(bot.SERVICE_STUDS_CHANNEL_ID)


def test_award_destination_resolves_mapped_thread():
    thread = SimpleNamespace(id=999)
    guild = SimpleNamespace(
        get_channel=lambda _cid: None,
        get_thread=lambda cid: thread if cid == 999 else None,
        active_threads=AsyncMock(return_value=[]),
    )
    member = SimpleNamespace(id=42, roles=[SimpleNamespace(id=1234, name="Kill Team Example")])
    with patch.object(bot, "KT_ROLE_CHANNEL_MAP", {1234: 999}):
        assert asyncio.run(bot._get_award_announcement_channel(member, guild)) is thread
    guild.active_threads.assert_not_awaited()


def test_new_kill_team_role_is_configured_and_recognized_before_name_cache_refresh():
    config = json.loads((Path(__file__).resolve().parents[1] / "config/config.json").read_text())
    role_id = 1559221547590221965
    assert role_id in config["kill_teams"]["role_ids"]
    assert 1458255656682258504 in config["kill_teams"]["forum_parent_ids"]
    member = SimpleNamespace(roles=[SimpleNamespace(id=role_id, name="Kill Team New Vigil")])
    with (
        patch.object(bot, "ALLOWED_KT_ROLE_IDS", set(config["kill_teams"]["role_ids"])),
        patch.object(bot, "KILL_TEAMS", []),
    ):
        assert forge_ops._resolve_killteam_for_member(member) == "Kill Team New Vigil"


def test_award_destination_scopes_duplicate_names_to_members_company(monkeypatch):
    primus = SimpleNamespace(id=800)
    secundus = SimpleNamespace(id=801)
    wrong = SimpleNamespace(id=1, name="Kill Team Alpha", parent=primus)
    correct = SimpleNamespace(id=2, name="Kill Team Alpha", parent=secundus)
    guild = SimpleNamespace(active_threads=AsyncMock(return_value=[wrong, correct]))
    member = SimpleNamespace(id=42, roles=[SimpleNamespace(name="Watch Company Secundus")])
    monkeypatch.setattr(forge_ops._g, "CONFIG", {
        "kill_teams": {"forum_parent_by_company": {"Watch Company Secundus": 801}},
    })
    with (
        patch.object(bot, "KT_ROLE_CHANNEL_MAP", {}),
        patch.object(bot, "ALLOWED_KT_FORUM_PARENT_IDS", {800, 801}),
        patch("opscribe.forge_ops._resolve_killteam_for_member", return_value="Kill Team Alpha"),
    ):
        assert asyncio.run(bot._get_award_announcement_channel(member, guild)) is correct


def test_award_destination_prefers_exact_team_over_substring():
    parent = SimpleNamespace(id=800)
    partial = SimpleNamespace(id=1, name="Kill Team Alpha Prime", parent=parent)
    exact = SimpleNamespace(id=2, name="Kill Team Alpha", parent=parent)
    guild = SimpleNamespace(active_threads=AsyncMock(return_value=[partial, exact]))
    member = SimpleNamespace(id=42, roles=[])
    with (
        patch.object(bot, "KT_ROLE_CHANNEL_MAP", {}),
        patch.object(bot, "ALLOWED_KT_FORUM_PARENT_IDS", {800}),
        patch("opscribe.forge_ops._resolve_killteam_for_member", return_value="Kill Team Alpha"),
    ):
        assert asyncio.run(bot._get_award_announcement_channel(member, guild)) is exact


def test_award_destination_finds_archived_team_thread():
    import discord

    parent = MagicMock(spec=discord.ForumChannel)
    parent.id = 800
    thread = SimpleNamespace(id=900, name="Kill Team Alpha", parent=parent)

    async def archived_threads(**_kwargs):
        yield thread

    parent.archived_threads = archived_threads
    guild = SimpleNamespace(
        active_threads=AsyncMock(return_value=[]),
        get_channel=lambda cid: parent if cid == parent.id else None,
    )
    member = SimpleNamespace(id=42, roles=[])
    with (
        patch.object(bot, "KT_ROLE_CHANNEL_MAP", {}),
        patch.object(bot, "ALLOWED_KT_FORUM_PARENT_IDS", {800}),
        patch("opscribe.forge_ops._resolve_killteam_for_member", return_value="Kill Team Alpha"),
    ):
        assert asyncio.run(bot._get_award_announcement_channel(member, guild)) is thread


def test_award_destination_does_not_choose_ambiguous_team():
    parent = SimpleNamespace(id=800)
    threads = [SimpleNamespace(id=cid, name="Kill Team Alpha", parent=parent) for cid in (1, 2)]
    general = SimpleNamespace(id=bot.SERVICE_STUDS_CHANNEL_ID)
    guild = SimpleNamespace(
        active_threads=AsyncMock(return_value=threads),
        get_channel=lambda _cid: general,
    )
    member = SimpleNamespace(id=42, roles=[])
    with (
        patch.object(bot, "KT_ROLE_CHANNEL_MAP", {}),
        patch.object(bot, "ALLOWED_KT_FORUM_PARENT_IDS", {800}),
        patch("opscribe.forge_ops._resolve_killteam_for_member", return_value="Kill Team Alpha"),
        patch.object(forge_ops._g, "logger", MagicMock()) as logger,
    ):
        assert asyncio.run(bot._get_award_announcement_channel(member, guild)) is general
    logger.warning.assert_called_once()


def _make_promotion_fixture():
    watch_brother = _role(1, "Watch Brother")
    black_laurels = _role(2, "Black Laurels")
    watch_veteran = _role(bot.WATCH_VETERAN_ROLE_ID, "Watch Veteran")
    crimson_laurels = _role(bot.CRIMSON_LAURELS_ROLE_ID, "Crimson Laurels")
    watch_captain = _role(5, "Watch Captain")
    watch_lieutenant = _role(6, "Watch Lieutenant")
    watch_sergeant = _role(bot.WATCH_SERGEANT_ROLE_ID, "Watch Sergeant")
    watch_command = _role(bot.WATCH_COMMAND_ROLE_ID, "Watch Command")
    ardent_raider = _role(bot.ARDENT_RAIDER_ROLE_ID, "Ardent Raider Ribbon")
    apothecarion_medal = _role(bot.APOTHECARION_SERVICE_MEDAL_ROLE_ID, "Apothecarion Service Medal")

    all_roles = [
        watch_brother,
        black_laurels,
        watch_veteran,
        crimson_laurels,
        watch_captain,
        watch_lieutenant,
        watch_sergeant,
        watch_command,
        ardent_raider,
        apothecarion_medal,
    ]
    by_id = {r.id: r for r in all_roles}

    member = SimpleNamespace(
        id=42,
        bot=False,
        mention="<@42>",
        nick="Brother Test",
        display_name="Brother Test",
        roles=[watch_brother, black_laurels],
        add_roles=AsyncMock(),
    )

    studs_channel = MagicMock(send=AsyncMock())
    black_laurels_channel = MagicMock(send=AsyncMock())
    oathsworn_channel = MagicMock(send=AsyncMock())
    guild = SimpleNamespace(
        id=12345,
        roles=all_roles,
        members=[member],
        get_role=MagicMock(side_effect=lambda rid: by_id.get(rid)),
        get_channel=MagicMock(
            side_effect=lambda cid: {
                bot.SERVICE_STUDS_CHANNEL_ID: studs_channel,
                bot.BLACK_LAURELS_CHANNEL_ID: black_laurels_channel,
                bot.OATHSWORN_CHANNEL_ID: oathsworn_channel,
            }.get(cid)
        ),
    )

    return member, guild


def test_check_promotion_milestones_sets_flags_and_resolves_award_channel_once():
    member, guild = _make_promotion_fixture()
    ann_channel = MagicMock(send=AsyncMock())
    save_tracking = MagicMock()
    resolve_ann_channel = AsyncMock(return_value=ann_channel)

    with (
        patch.object(bot, "_resolve_notification_guild", return_value=guild),
        patch("opscribe.roster_ops._load_promotion_tracking", return_value={}),
        patch("opscribe.roster_ops._save_promotion_tracking", save_tracking),
        patch("opscribe.roster_ops.compute_stats_for_user", return_value={"aar_points": 1200, "armory_points": 200, "gene_seed_points": 150}),
        patch.object(bot, "_get_award_announcement_channel", resolve_ann_channel),
        patch("opscribe.roster_ops._get_effective_induction_date", return_value=datetime.now(timezone.utc) - timedelta(days=30)),
        patch.object(bot, "_get_watch_veteran_announcement", return_value=("v", MagicMock(), None)),
        patch.object(bot, "_get_ardent_raider_announcement", return_value=("a", MagicMock(), None)),
        patch.object(bot, "_get_apothecarion_medal_announcement", return_value=("f", MagicMock(), None)),
        patch.object(bot, "_get_crimson_laurels_announcement", return_value=("c", MagicMock(), None)),
        patch.object(bot._g, "DATASTORE", SimpleNamespace(iter_records=lambda: [])),
        patch.object(bot._g, "PROMOTION_TRACKING_LOCK", _AsyncLock()),
        patch.object(bot._g, "logger", MagicMock()),
        patch("opscribe.roster_ops.asyncio.sleep", AsyncMock(return_value=None)),
    ):
        asyncio.run(bot._check_promotion_milestones())

    tracking = save_tracking.call_args.args[0][str(member.id)]
    assert tracking["veteran_assigned"] is True
    assert tracking["ardent_raider_notified"] is True
    assert tracking["for_the_fallen_notified"] is True
    assert tracking["crimson_laurels_notified"] is True
    assert resolve_ann_channel.await_count == 1


def test_check_promotion_milestones_does_not_set_flags_when_role_assignment_fails():
    member, guild = _make_promotion_fixture()
    member.add_roles = AsyncMock(side_effect=RuntimeError("assignment failed"))
    save_tracking = MagicMock()
    resolve_ann_channel = AsyncMock(return_value=MagicMock(send=AsyncMock()))

    with (
        patch.object(bot, "_resolve_notification_guild", return_value=guild),
        patch("opscribe.roster_ops._load_promotion_tracking", return_value={}),
        patch("opscribe.roster_ops._save_promotion_tracking", save_tracking),
        patch("opscribe.roster_ops.compute_stats_for_user", return_value={"aar_points": 1200, "armory_points": 200, "gene_seed_points": 150}),
        patch.object(bot, "_get_award_announcement_channel", resolve_ann_channel),
        patch("opscribe.roster_ops._get_effective_induction_date", return_value=datetime.now(timezone.utc) - timedelta(days=30)),
        patch.object(bot, "_get_watch_veteran_announcement", return_value=("v", MagicMock(), None)),
        patch.object(bot, "_get_ardent_raider_announcement", return_value=("a", MagicMock(), None)),
        patch.object(bot, "_get_apothecarion_medal_announcement", return_value=("f", MagicMock(), None)),
        patch.object(bot, "_get_crimson_laurels_announcement", return_value=("c", MagicMock(), None)),
        patch.object(bot._g, "DATASTORE", SimpleNamespace(iter_records=lambda: [])),
        patch.object(bot._g, "PROMOTION_TRACKING_LOCK", _AsyncLock()),
        patch.object(bot._g, "logger", MagicMock()),
        patch("opscribe.roster_ops.asyncio.sleep", AsyncMock(return_value=None)),
    ):
        asyncio.run(bot._check_promotion_milestones())

    all_tracking = save_tracking.call_args.args[0]
    assert str(member.id) not in all_tracking
    assert resolve_ann_channel.await_count == 0


def test_octavian_announcement_uses_custom_emoji_and_existing_asset_candidates():
    member = SimpleNamespace(id=42, mention="<@42>", display_name="Brother Test", nick="Brother Test", roles=[])
    guild = SimpleNamespace(roles=[])

    fake_file = SimpleNamespace(filename="award_ocatavian_operation_medal.png")

    with (
        patch("opscribe.forge_ops._get_bearer_rank_and_title", return_value=("Brother", "Brother Test", None)),
        patch("opscribe.forge_ops._get_rank_emoji", return_value=None),
        patch("opscribe.forge_ops._get_emoji_by_name", side_effect=lambda _guild, name: ":OctavianMedal:" if name == "OctavianMedal" else None),
        patch("opscribe.forge_ops._get_award_image", return_value=fake_file),
        patch("opscribe.forge_ops.random.choice", side_effect=lambda seq: seq[0]),
    ):
        _content, embed, award_file = forge_ops._get_octavian_operation_announcement(member, "Unknown", guild)

    assert award_file is fake_file
    assert embed.image.url == "attachment://award_ocatavian_operation_medal.png"
    assert any(":OctavianMedal:" in (field.value or "") for field in embed.fields)


def test_award_announcements_ping_only_the_recipient():
    watch_brother = _role(1, "Watch Brother")
    member = SimpleNamespace(
        id=42,
        mention="<@42>",
        display_name="Brother Test",
        nick="Brother Test",
        roles=[watch_brother],
    )
    guild = SimpleNamespace(roles=[watch_brother], emojis=[])

    with (
        patch("opscribe.forge_ops._get_bearer_rank_and_title", return_value=("Brother", "Brother Test", None)),
        patch("opscribe.forge_ops._get_emoji_by_name", return_value=None),
        patch("opscribe.forge_ops._get_rank_emoji", return_value=None),
        patch("opscribe.forge_ops._get_award_image", return_value=None),
        patch("opscribe.forge_ops.random.choice", side_effect=lambda choices: choices[0]),
    ):
        announcements = [
            forge_ops._get_watch_veteran_announcement(member, "Unknown", guild),
            forge_ops._get_ardent_raider_announcement(member, "Unknown", guild),
            forge_ops._get_apothecarion_medal_announcement(member, "Unknown", guild),
            forge_ops._get_crimson_laurels_announcement(member, "Unknown", guild),
            forge_ops._build_challenge_award_embed(
                member=member,
                member_chapter="Unknown",
                guild=guild,
                title="Test Award",
                color=0,
                openings=["{name}"],
                proclamations=["Awarded"],
                chapter_lines={},
                award_label="Test Award",
                award_image=None,
            ),
        ]

    assert [announcement[0] for announcement in announcements] == [member.mention] * len(announcements)


def test_master_terminus_announcement_uses_supplied_art_and_both_lore_branches():
    member = SimpleNamespace(id=42, mention="<@42>", roles=[_role(1, "Watch Brother")])
    guild = SimpleNamespace(roles=[], emojis=[])
    for roll, coda in (
        (0.99, forge_ops.MASTER_TERMINUS_SLAYER_CHAPTER_LINES["Consecrators"]),
        (0.0, forge_ops.MASTER_TERMINUS_SLAYER_RANK_LINES["Watch Brother"]),
    ):
        with (
            patch("opscribe.forge_ops._get_bearer_rank_and_title", return_value=("Brother", "Nyx", None)),
            patch("opscribe.forge_ops._get_emoji_by_name", return_value=None),
            patch("opscribe.forge_ops._get_rank_emoji", return_value=None),
            patch("opscribe.forge_ops.random.choice", side_effect=lambda choices: choices[0]),
            patch("opscribe.forge_ops.random.random", return_value=roll),
        ):
            content, embed, award_file = forge_ops._get_master_terminus_slayer_announcement(member, "Consecrators", guild)
        assert content == member.mention
        assert coda.format(name="Nyx") in embed.fields[0].value
        assert all(len(field.value) <= 1024 for field in embed.fields)
        assert "Phobos" in embed.fields[0].value
        assert "Gravis" in embed.fields[0].value
        assert "Tacticus" in embed.fields[0].value
        assert award_file is not None
        try:
            assert award_file.filename == "award_master_terminus_slayer.png"
            assert embed.image.url == f"attachment://{award_file.filename}"
            assert hashlib.sha256(award_file.fp.read()).hexdigest() == (
                "2c01b8fdec25d90ee6962e311b7e57529205972df5a04e91b304cd226e0609e2"
            )
        finally:
            award_file.close()


def test_service_studs_announcement_keeps_watch_brother_and_recipient_mentions():
    watch_brother = _role(1, "Watch Brother")
    member = SimpleNamespace(
        id=42,
        mention="<@42>",
        display_name="Brother Test",
        nick="Brother Test",
        roles=[watch_brother],
    )
    guild = SimpleNamespace(roles=[watch_brother], emojis=[])

    with (
        patch("opscribe.forge_ops._get_bearer_rank_and_title", return_value=("Brother", "Brother Test", None)),
        patch("opscribe.forge_ops._get_rank_emoji", return_value=None),
        patch("opscribe.forge_ops._get_emoji_by_name", return_value=None),
        patch("opscribe.forge_ops._studs_tier", return_value=1),
        patch("opscribe.forge_ops._studs_pips", return_value="pip"),
        patch("opscribe.forge_ops._blend_stud_flavor_by_rank", return_value="service"),
        patch("opscribe.forge_ops._get_stud_marking_recipients", return_value=("Marking", "")),
        patch("opscribe.forge_ops.random.choice", side_effect=lambda choices: choices[0]),
    ):
        content, _embed = forge_ops._get_service_studs_announcement(
            member,
            "Unknown",
            displayed_studs=0,
            new_studs=1,
            earned_studs=1,
            owed_studs=0,
            guild=guild,
        )

    assert content == f"{watch_brother.mention} {member.mention}"


def test_distinguished_octavian_emoji_resolves_for_ledger_and_announcement():
    guild = SimpleNamespace(emojis=[], roles=[])
    member = SimpleNamespace(id=42, mention="<@42>", display_name="Brother Test", roles=[])
    hint = next(hint for role_id, _, hint in CHALLENGE_ROLES if role_id == DISTINGUISHED_OCTAVIAN_OPERATION_MEDAL_ROLE_ID)
    token = "<:DistinguishedOctavianMedal:1538311585200865361>"
    assert forge_ops._get_emoji_by_name(guild, hint) == token

    with (
        patch("opscribe.forge_ops._get_bearer_rank_and_title", return_value=("Brother", "Brother Test", None)),
        patch("opscribe.forge_ops._get_award_image", return_value=None),
        patch("opscribe.forge_ops.random.choice", side_effect=lambda seq: seq[0]),
    ):
        _content, embed, _award_file = forge_ops._get_distinguished_octavian_operation_announcement(
            member, "Unknown", guild
        )
    assert any(token in field.value for field in embed.fields)




def test_supplied_rank_emojis_resolve_by_id_without_guild_cache():
    guild = SimpleNamespace(emojis=[])
    expected = {
        "Watch Brother": "WatchBrother:1435655975414796479",
        "Watch Veteran": "WatchVeteran:1435656436792430752",
        "Oathsworn": "Oathsworn:1463731905466994698",
        "Watch Sergeant": "WatchSergeant:1435655991214477493",
        "Veteran Sergeant": "VeteranSergeant:1553447900955025539",
        "Watch Lieutenant": "WatchLieutenant:1435655993651499029",
        "Watch Captain": "WatchCaptain:1435655998064033863",
        "Watch Master": "WatchMaster:1523373886723461180",
        "Bladeguard": "Bladeguard:1553447677327573022",
        "First Blade": "FirstBlade:1553447689524609134",
        "Blade Master": "BladeMaster:1523373695333175497",
        "Watch Techmarine": "Techmarine:1553447296081993748",
        "Forgemaster": "Forgemaster:1455049547435737118",
        "Watch Librarian": "Librarian:1553447420871057512",
        "Void Warden": "VoidWarden:1455049545888043090",
        "Watch Apothecary": "Apothecary:1553447458582044782",
        "Chief Apothecary": "ChiefApothecary:1455049544269041684",
        "Watch Chaplain": "Chaplain:1435656003009118381",
        "High Chaplain": "HighChaplain:1455291872908939549",
        "Kill-Marine": "KillMarine:1553447765525139556",
        "Huntmaster": "Huntmaster:1511137862450417825",
        "Venerable Dreadnought": "Dreadnought:1504269556959678554",
        "Honored Dreadnought": "Dreadnought:1504269556959678554",
    }
    for rank, token in expected.items():
        assert forge_ops._get_rank_emoji(guild, rank) == f"<:{token}>"
    assert forge_ops._get_rank_emoji(guild, "Interred Brother") == ""


def test_equerry_rank_emojis_only_replace_selected_rank():
    guild = SimpleNamespace(emojis=[])
    variants = {
        "First Blade": "HighBlade:1553447700278550528",
        "Watch Techmarine": "ForgeAdept:1553447321444818944",
        "Watch Librarian": "LexicanumPrimus:1553447432359116870",
        "Watch Apothecary": "PrimusMedicae:1553447469579370718",
        "Watch Chaplain": "Reclusiarch:1553447538080616468",
        "Kill-Marine": "VenatorPrimus:1553447775599857715",
    }
    for rank, token in variants.items():
        assert forge_ops._get_rank_emoji(guild, rank, role_names={rank, "High Command Equerry"}) == f"<:{token}>"
        assert forge_ops._get_rank_emoji(guild, rank, role_names={rank}) == forge_ops._get_rank_emoji(guild, rank)
        assert forge_ops._get_rank_emoji(guild, rank, role_names={"High Command Equerry"}) == forge_ops._get_rank_emoji(guild, rank)
    assert forge_ops._get_rank_emoji(
        guild, "Watch Captain", role_names={"Watch Captain", "First Blade", "High Command Equerry"}
    ) == "<:WatchCaptain:1435655998064033863>"


def test_equerry_award_recipient_uses_composite_emoji():
    guild = SimpleNamespace(emojis=[], roles=[])
    member = SimpleNamespace(
        id=42, mention="<@42>", display_name="Brother Test",
        roles=[_role(1, "Watch Techmarine"), _role(2, "High Command Equerry")],
    )
    with (
        patch("opscribe.forge_ops._get_bearer_rank_and_title", return_value=("Techmarine", "Brother Test", None)),
        patch("opscribe.forge_ops._get_award_image", return_value=None),
        patch("opscribe.forge_ops.random.choice", side_effect=lambda seq: seq[0]),
    ):
        _content, embed, _award_file = forge_ops._get_distinguished_octavian_operation_announcement(
            member, "Unknown", guild
        )
    assert any("<:ForgeAdept:1553447321444818944>" in field.value for field in embed.fields)


def test_equerry_member_label_uses_composite_emoji_without_changing_rank():
    member = SimpleNamespace(
        nick="First Blade Test", display_name="First Blade Test",
        roles=[_role(1, "First Blade"), _role(2, "High Command Equerry")],
    )
    guild = SimpleNamespace(emojis=[], get_member=lambda _member_id: member)
    label = roster_ops._format_member_styled(guild, "42")
    assert label == "<:HighBlade:1553447700278550528> Test"
