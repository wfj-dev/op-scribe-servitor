import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import opscribe.bot as bot


def _role(role_id: int, name: str):
    return SimpleNamespace(id=role_id, name=name, mention=f"<@&{role_id}>")


class _AsyncLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


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
        patch.object(bot, "_get_watch_veteran_announcement", return_value=("v", MagicMock())),
        patch.object(bot, "_get_ardent_raider_announcement", return_value=("a", MagicMock())),
        patch.object(bot, "_get_apothecarion_medal_announcement", return_value=("f", MagicMock())),
        patch.object(bot, "_get_crimson_laurels_announcement", return_value=("c", MagicMock())),
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
        patch.object(bot, "_get_watch_veteran_announcement", return_value=("v", MagicMock())),
        patch.object(bot, "_get_ardent_raider_announcement", return_value=("a", MagicMock())),
        patch.object(bot, "_get_apothecarion_medal_announcement", return_value=("f", MagicMock())),
        patch.object(bot, "_get_crimson_laurels_announcement", return_value=("c", MagicMock())),
        patch.object(bot._g, "DATASTORE", SimpleNamespace(iter_records=lambda: [])),
        patch.object(bot._g, "PROMOTION_TRACKING_LOCK", _AsyncLock()),
        patch.object(bot._g, "logger", MagicMock()),
        patch("opscribe.roster_ops.asyncio.sleep", AsyncMock(return_value=None)),
    ):
        asyncio.run(bot._check_promotion_milestones())

    all_tracking = save_tracking.call_args.args[0]
    assert str(member.id) not in all_tracking
    assert resolve_ann_channel.await_count == 0
