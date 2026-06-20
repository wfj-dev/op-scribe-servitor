"""Unit tests for _enforce_challenge_grace_periods.

Covers:
- No action when challenge_grace_periods config is absent or empty
- No revocation while still inside the grace window (today < deadline)
- Revocation of Black Laurels after deadline when member lacks new mission
- No revocation of Black Laurels after deadline when member already has all missions
- Black Laurels notified flag cleared to False so re-earn works
- Revocation of Order Omega (challenge_progress.json path) after deadline
- Order Omega notified entry removed from list so re-earn works
- No revocation of Order Omega when member is already fully qualified
- Unknown challenge name in config is silently skipped
- Revocation count is returned correctly
"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import opscribe._bot_globals as _g_module
from opscribe.constants import (
    BLACK_LAURELS_ROLE_ID,
    BLACK_LAURELS_REQUIRED_MISSIONS,
    THE_ORDER_OMEGA_ROLE_ID,
    ORDER_OMEGA_REQUIRED_MISSIONS,
    CRUX_TERMINATUS_ROLE_ID,
    DISTINGUISHED_PIPEHITTER_ROLE_ID,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PAST_DATE = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
FUTURE_DATE = (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d")


def _make_member(uid: int, roles: list) -> MagicMock:
    m = MagicMock(spec=discord.Member)
    m.id = uid
    m.display_name = f"Member{uid}"
    m.bot = False
    m.roles = roles
    m.remove_roles = AsyncMock()
    return m


def _make_role(role_id: int, name: str = "TestRole") -> MagicMock:
    r = MagicMock(spec=discord.Role)
    r.id = role_id
    r.name = name
    return r


def _make_guild(members: list, roles: list) -> MagicMock:
    g = MagicMock(spec=discord.Guild)
    g.members = members
    g.roles = roles
    return g


def _run(coro):
    return asyncio.run(coro)


def _grace(*args, **kwargs):
    """Import _enforce_challenge_grace_periods lazily via opscribe.bot so that
    _g.bot is initialised before the sub-module chain is imported."""
    import opscribe.bot as _bot_mod  # noqa: PLC0415
    return _bot_mod._enforce_challenge_grace_periods(*args, **kwargs)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _patch_config(cfg: dict):
    return patch.object(_g_module, "CONFIG", cfg)


def _patch_lock():
    import asyncio as _asyncio

    class _FakeLock:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    return patch.object(_g_module, "CHALLENGE_PROGRESS_LOCK", _FakeLock())


# ---------------------------------------------------------------------------
# Empty / no-op cases
# ---------------------------------------------------------------------------


class TestGracePeriodNoOp:
    def test_no_config_returns_zero(self):
        with _patch_config({}):
            guild = _make_guild([], [])
            result = _run(
                _grace(guild, {}, {})
            )
        assert result == 0

    def test_empty_grace_periods_returns_zero(self):
        with _patch_config({"challenge_grace_periods": {}}):
            guild = _make_guild([], [])
            result = _run(
                _grace(guild, {}, {})
            )
        assert result == 0

    def test_within_grace_window_no_revocation(self):
        """Before the deadline, no roles are touched."""
        bl_role = _make_role(BLACK_LAURELS_ROLE_ID, "Black Laurels")
        member = _make_member(1, [bl_role])
        guild = _make_guild([member], [bl_role])

        with (
            _patch_config({"challenge_grace_periods": {"black_laurels": FUTURE_DATE}}),
            patch("discord.utils.get", return_value=bl_role),
        ):
            result = _run(
                _grace(guild, {}, {})
            )

        assert result == 0
        member.remove_roles.assert_not_called()

    def test_unknown_challenge_name_skipped(self):
        with _patch_config({"challenge_grace_periods": {"made_up_challenge": PAST_DATE}}):
            guild = _make_guild([], [])
            # Should not raise and returns 0
            result = _run(
                _grace(guild, {}, {})
            )
        assert result == 0

    def test_invalid_date_format_skipped(self):
        with _patch_config({"challenge_grace_periods": {"black_laurels": "not-a-date"}}):
            guild = _make_guild([], [])
            result = _run(
                _grace(guild, {}, {})
            )
        assert result == 0


# ---------------------------------------------------------------------------
# Black Laurels enforcement
# ---------------------------------------------------------------------------


class TestBlackLaurelsGrace:
    def _setup(self, uid: int, has_bl_role: bool, completed_missions: set):
        bl_role = _make_role(BLACK_LAURELS_ROLE_ID, "Black Laurels")
        roles = [bl_role] if has_bl_role else []
        member = _make_member(uid, roles)
        guild = _make_guild([member], [bl_role])
        user_bl_missions = {str(uid): completed_missions}
        tracking = {str(uid): {"black_laurels_notified": True}}

        return member, guild, user_bl_missions, tracking, bl_role

    def test_revokes_when_missing_new_mission(self):
        """Member holds BL role but lacks the newly-required mission → revoked."""
        uid = 100
        # Give them all BUT one mission
        partial = set(BLACK_LAURELS_REQUIRED_MISSIONS) - {"purgation"}
        member, guild, user_bl_missions, tracking, bl_role = self._setup(uid, True, partial)

        with (
            _patch_config({"challenge_grace_periods": {"black_laurels": PAST_DATE}}),
            patch("discord.utils.get", return_value=bl_role),
        ):
            result = _run(
                _grace(guild, user_bl_missions, tracking)
            )

        assert result == 1
        member.remove_roles.assert_called_once()
        # Notified flag cleared so they can re-earn
        assert tracking[str(uid)]["black_laurels_notified"] is False

    def test_no_revocation_when_fully_qualified(self):
        """Member holds BL role and has ALL missions → untouched."""
        uid = 200
        full = set(BLACK_LAURELS_REQUIRED_MISSIONS)
        member, guild, user_bl_missions, tracking, bl_role = self._setup(uid, True, full)

        with (
            _patch_config({"challenge_grace_periods": {"black_laurels": PAST_DATE}}),
            patch("discord.utils.get", return_value=bl_role),
        ):
            result = _run(
                _grace(guild, user_bl_missions, tracking)
            )

        assert result == 0
        member.remove_roles.assert_not_called()
        # Flag untouched
        assert tracking[str(uid)]["black_laurels_notified"] is True

    def test_no_revocation_when_member_lacks_role(self):
        """Member does NOT hold the BL role → nothing happens."""
        uid = 300
        member, guild, user_bl_missions, tracking, bl_role = self._setup(uid, False, set())

        with (
            _patch_config({"challenge_grace_periods": {"black_laurels": PAST_DATE}}),
            patch("discord.utils.get", return_value=bl_role),
        ):
            result = _run(
                _grace(guild, user_bl_missions, tracking)
            )

        assert result == 0
        member.remove_roles.assert_not_called()

    def test_skips_bots(self):
        bl_role = _make_role(BLACK_LAURELS_ROLE_ID, "Black Laurels")
        bot_member = _make_member(999, [bl_role])
        bot_member.bot = True
        guild = _make_guild([bot_member], [bl_role])

        with (
            _patch_config({"challenge_grace_periods": {"black_laurels": PAST_DATE}}),
            patch("discord.utils.get", return_value=bl_role),
        ):
            result = _run(
                _grace(guild, {}, {})
            )

        assert result == 0
        bot_member.remove_roles.assert_not_called()


# ---------------------------------------------------------------------------
# Order Omega enforcement (challenge_progress.json path)
# ---------------------------------------------------------------------------


class TestOrderOmegaGrace:
    def _setup(self, uid: int, logged_missions: set):
        oo_role = _make_role(THE_ORDER_OMEGA_ROLE_ID, "The Order Omega")
        member = _make_member(uid, [oo_role])
        guild = _make_guild([member], [oo_role])

        cp_data = {
            str(uid): {
                "notified": ["order_omega"],
                "order_omega": [
                    {"mission": m, "aar_id": 1, "message_url": "", "timestamp": ""}
                    for m in logged_missions
                ],
            }
        }
        return member, guild, oo_role, cp_data

    def test_revokes_when_missing_new_mission(self):
        uid = 400
        partial = set(ORDER_OMEGA_REQUIRED_MISSIONS) - {"purgation"}
        member, guild, oo_role, cp_data = self._setup(uid, partial)

        save_mock = MagicMock()
        with (
            _patch_config({"challenge_grace_periods": {"order_omega": PAST_DATE}}),
            _patch_lock(),
            patch("discord.utils.get", return_value=oo_role),
            patch("opscribe.roster_ops._b") as mock_b,
        ):
            def _b_side(name):
                if name == "_load_challenge_progress":
                    return lambda: cp_data
                if name == "_save_challenge_progress":
                    return save_mock
                return MagicMock()

            mock_b.side_effect = _b_side

            result = _run(
                _grace(guild, {}, {})
            )

        assert result == 1
        member.remove_roles.assert_called_once()
        # Notified entry removed from list
        assert "order_omega" not in cp_data[str(uid)]["notified"]
        save_mock.assert_called_once_with(cp_data)

    def test_no_revocation_when_fully_qualified(self):
        uid = 500
        full = set(ORDER_OMEGA_REQUIRED_MISSIONS)
        member, guild, oo_role, cp_data = self._setup(uid, full)

        save_mock = MagicMock()
        with (
            _patch_config({"challenge_grace_periods": {"order_omega": PAST_DATE}}),
            _patch_lock(),
            patch("discord.utils.get", return_value=oo_role),
            patch("opscribe.roster_ops._b") as mock_b,
        ):
            def _b_side(name):
                if name == "_load_challenge_progress":
                    return lambda: cp_data
                if name == "_save_challenge_progress":
                    return save_mock
                return MagicMock()

            mock_b.side_effect = _b_side

            result = _run(
                _grace(guild, {}, {})
            )

        assert result == 0
        member.remove_roles.assert_not_called()


class TestCruxGrace:
    class _FakeDataStore:
        def __init__(self, records):
            self._records = records

        def iter_records(self):
            return iter(self._records)

    def test_master_plus_one_class_meets_terminus_requirement(self):
        """Crux grace check should use the same Terminus role counting as auto-award.

        Regression: using class-only counting revoked members who held Master
        Terminus Slayer plus one class role.
        """
        crux_role = _make_role(CRUX_TERMINATUS_ROLE_ID, "Crux Terminatus")
        bl_role = _make_role(BLACK_LAURELS_ROLE_ID, "Black Laurels")
        distinguished_role = _make_role(DISTINGUISHED_PIPEHITTER_ROLE_ID, "Distinguished SOK-G: Pipehitter")
        master_terminus = _make_role(1452803611477147668, "Master Terminus Slayer")
        assault_terminus = _make_role(1449257352112111646, "Terminus Slayer (Assault)")

        member = _make_member(
            600,
            [crux_role, bl_role, distinguished_role, master_terminus, assault_terminus],
        )
        crux_role.members = [member]

        guild = _make_guild([member], [crux_role, bl_role, distinguished_role, master_terminus, assault_terminus])
        guild.get_role = MagicMock(side_effect=lambda rid: crux_role if rid == CRUX_TERMINATUS_ROLE_ID else None)

        with (
            _patch_config({"challenge_grace_periods": {"crux_terminatus": PAST_DATE}}),
            _patch_lock(),
            patch.object(_g_module, "DATASTORE", None),
            patch("opscribe.roster_ops._b") as mock_b,
        ):
            def _b_side(name):
                if name == "_load_challenge_progress":
                    return lambda: {}
                if name == "_save_challenge_progress":
                    return MagicMock()
                return MagicMock()

            mock_b.side_effect = _b_side

            result = _run(_grace(guild, {}, {}))

        assert result == 0
        member.remove_roles.assert_not_called()

    def test_grandfathered_baseline_not_revoked_on_new_mission_non_a(self):
        """Grandfathered BL holders should not lose Crux due to expanded mission set.

        User completed the legacy BL baseline pre-enforcement, but later submits a
        non-A run on a newly-required mission. Crux should remain because the
        grandfathered baseline is used for retention.
        """
        crux_role = _make_role(CRUX_TERMINATUS_ROLE_ID, "Crux Terminatus")
        bl_role = _make_role(BLACK_LAURELS_ROLE_ID, "Black Laurels")
        distinguished_role = _make_role(DISTINGUISHED_PIPEHITTER_ROLE_ID, "Distinguished SOK-G: Pipehitter")
        master_terminus = _make_role(1452803611477147668, "Master Terminus Slayer")
        assault_terminus = _make_role(1449257352112111646, "Terminus Slayer (Assault)")

        uid = 601
        member = _make_member(uid, [crux_role, bl_role, distinguished_role, master_terminus, assault_terminus])
        crux_role.members = [member]
        guild = _make_guild([member], [crux_role, bl_role, distinguished_role, master_terminus, assault_terminus])
        guild.get_role = MagicMock(side_effect=lambda rid: crux_role if rid == CRUX_TERMINATUS_ROLE_ID else None)

        legacy_missions = [
            "inferno",
            "decapitation",
            "vox liberatis",
            "ballistic engine",
            "exfiltration",
            "termination",
            "reclamation",
        ]
        records = [
            {
                "black_laurels_in_mission": True,
                "mission": m,
                "brother_ids": [uid],
                "timestamp": "2026-05-20T12:00:00+00:00",
            }
            for m in legacy_missions
        ]
        # Added mission after enforcement, non-A rank; should not affect
        # grandfathered baseline retention checks.
        records.append(
            {
                "black_laurels_in_mission": True,
                "mission": "purgation",
                "brother_ids": [uid],
                "rank": "C",
                "timestamp": "2026-06-10T12:00:00+00:00",
            }
        )

        with (
            _patch_config({"challenge_grace_periods": {"crux_terminatus": PAST_DATE}}),
            _patch_lock(),
            patch.object(_g_module, "DATASTORE", self._FakeDataStore(records)),
            patch("opscribe.roster_ops._b") as mock_b,
        ):
            def _b_side(name):
                if name == "_load_challenge_progress":
                    return lambda: {}
                if name == "_save_challenge_progress":
                    return MagicMock()
                return MagicMock()

            mock_b.side_effect = _b_side
            result = _run(_grace(guild, {}, {}))

        assert result == 0
        member.remove_roles.assert_not_called()

    def test_non_grandfathered_post_enforcement_non_a_is_revoked(self):
        """Non-grandfathered users are still subject to strict post-cutoff rank checks."""
        crux_role = _make_role(CRUX_TERMINATUS_ROLE_ID, "Crux Terminatus")
        bl_role = _make_role(BLACK_LAURELS_ROLE_ID, "Black Laurels")
        distinguished_role = _make_role(DISTINGUISHED_PIPEHITTER_ROLE_ID, "Distinguished SOK-G: Pipehitter")
        master_terminus = _make_role(1452803611477147668, "Master Terminus Slayer")
        assault_terminus = _make_role(1449257352112111646, "Terminus Slayer (Assault)")

        uid = 602
        member = _make_member(uid, [crux_role, bl_role, distinguished_role, master_terminus, assault_terminus])
        crux_role.members = [member]
        guild = _make_guild([member], [crux_role, bl_role, distinguished_role, master_terminus, assault_terminus])
        guild.get_role = MagicMock(side_effect=lambda rid: crux_role if rid == CRUX_TERMINATUS_ROLE_ID else None)

        records = [
            {
                "black_laurels_in_mission": True,
                "mission": "inferno",
                "brother_ids": [uid],
                "rank": "B",
                "timestamp": "2026-06-10T12:00:00+00:00",
            }
        ]

        with (
            _patch_config({"challenge_grace_periods": {"crux_terminatus": PAST_DATE}}),
            _patch_lock(),
            patch.object(_g_module, "DATASTORE", self._FakeDataStore(records)),
            patch("opscribe.roster_ops._b") as mock_b,
        ):
            def _b_side(name):
                if name == "_load_challenge_progress":
                    return lambda: {}
                if name == "_save_challenge_progress":
                    return MagicMock()
                return MagicMock()

            mock_b.side_effect = _b_side
            result = _run(_grace(guild, {}, {}))

        assert result == 1
        member.remove_roles.assert_called_once()
