"""Unit tests for the 7-day per-member snapshot helpers introduced in the
send_to roster embed path.

Covers:
- compute_stats_for_user_in_records: correct counting when brother_ids / brother_waves
  carry mixed int/str types (ID-normalization regression).
- _record_has_black_laurels_tag: detection of BL markers.
- _extract_directive_ids_from_record: directive-ID extraction from records.
- _member_completed_strike_directive_count_recent: completed_at-window SD counting.
- _compute_killteam_sendto_snapshot_7d: full aggregation with mocked data
    sources asserts member_rows fields (AAR delta, omega ops, BL count,
    dual-vigil count, strike-directive count, combat-bond score).
"""

import importlib
import sys
import types
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import mock_open, patch


# ---------------------------------------------------------------------------
# Minimal Discord / bot stubs so roster_ops can be imported
# ---------------------------------------------------------------------------

def _install_discord_stub():
    try:
        import discord as _real_discord  # type: ignore
        import discord.app_commands  # type: ignore  # noqa: F401
        import discord.ext.tasks  # type: ignore  # noqa: F401
        import discord.ui  # type: ignore  # noqa: F401
        sys.modules.setdefault("discord", _real_discord)
        return
    except Exception:
        pass

    discord_stub = sys.modules.get("discord") or types.ModuleType("discord")
    _compat_type = type("_CompatType", (), {"__init__": lambda self, *args, **kwargs: [setattr(self, k, v) for k, v in kwargs.items()] and None})
    discord_stub.Embed = _compat_type
    discord_stub.Intents = type(
        "Intents", (), {"default": classmethod(lambda _cls: SimpleNamespace(message_content=False, members=False))}
    )
    discord_stub.Client = type("Client", (), {"__init__": lambda self, *a, **kw: None})
    discord_stub.Member = object
    discord_stub.User = object
    discord_stub.Guild = object
    discord_stub.Role = object
    discord_stub.TextChannel = object
    discord_stub.Emoji = object
    discord_stub.Interaction = object
    discord_stub.AllowedMentions = _compat_type
    discord_stub.SelectOption = _compat_type
    discord_stub.Thread = type("Thread", (), {})
    discord_stub.ForumChannel = type("ForumChannel", (), {})
    discord_stub.File = _compat_type
    discord_stub.Object = _compat_type
    discord_stub.NotFound = Exception
    discord_stub.Forbidden = Exception
    discord_stub.Color = type("Color", (), {"from_rgb": classmethod(lambda cls, *a, **kw: cls())})
    discord_stub.utils = types.SimpleNamespace(
        get=lambda items, **kw: next(
            (i for i in items if all(getattr(i, k, None) == v for k, v in kw.items())), None
        )
    )
    discord_stub.ButtonStyle = types.SimpleNamespace(secondary=2, success=3, danger=4, primary=1)
    discord_stub.TextStyle = types.SimpleNamespace(paragraph=2, short=1)
    discord_stub.abc = types.SimpleNamespace(Messageable=object, GuildChannel=object, MessageableChannel=object)
    discord_stub.__getattr__ = lambda name: type(name, (), {})

    app_commands_mod = types.ModuleType("discord.app_commands")
    _fallback = type(
        "_F", (), {"__init__": lambda self, *a, **kw: None, "__class_getitem__": classmethod(lambda cls, _i: cls)}
    )
    app_commands_mod.CommandTree = type("CommandTree", (), {"__init__": lambda self, bot: None})
    app_commands_mod.command = lambda **_kw: (lambda f: f)
    app_commands_mod.describe = lambda **_kw: (lambda f: f)
    app_commands_mod.choices = lambda **_kw: (lambda f: f)
    app_commands_mod.autocomplete = lambda **_kw: (lambda f: f)
    app_commands_mod.rename = lambda **_kw: (lambda f: f)
    app_commands_mod.Choice = _fallback
    app_commands_mod.__getattr__ = lambda name: type(name, (), {})
    discord_stub.app_commands = app_commands_mod

    ui_mod = types.ModuleType("discord.ui")
    ui_mod.View = type("View", (), {"__init_subclass__": classmethod(lambda cls, **_kw: None)})
    ui_mod.Modal = type("Modal", (), {"__init_subclass__": classmethod(lambda cls, **_kw: None)})
    ui_mod.TextInput = type("TextInput", (), {"__init__": lambda self, *a, **kw: None})
    ui_mod.Button = _compat_type
    ui_mod.Select = _compat_type
    ui_mod.UserSelect = _compat_type
    ui_mod.RoleSelect = _compat_type
    ui_mod.button = lambda **_kw: (lambda f: f)
    ui_mod.select = lambda **_kw: (lambda f: f)
    discord_stub.ui = ui_mod

    tasks_mod = types.ModuleType("discord.ext.tasks")
    _loop_stub = type("_L", (), {"before_loop": lambda _self, f: f, "after_loop": lambda _self, f: f,
                                  "__getattr__": lambda _self, _n: (lambda *a, **kw: None)})
    tasks_mod.loop = lambda **_kw: (lambda f: _loop_stub())
    ext_mod = types.ModuleType("discord.ext")
    ext_mod.tasks = tasks_mod
    discord_stub.ext = ext_mod

    sys.modules.setdefault("discord", discord_stub)
    sys.modules["discord.app_commands"] = app_commands_mod
    sys.modules["discord.ext"] = ext_mod
    sys.modules["discord.ext.tasks"] = tasks_mod
    sys.modules["discord.ui"] = ui_mod


_install_discord_stub()

import opscribe._bot_globals as _g  # noqa: E402

_bot_stub = types.ModuleType("opscribe.bot")
_bot_stub.bot = SimpleNamespace(tree=SimpleNamespace(command=lambda **_kw: (lambda f: f)))
_bot_stub.tree = SimpleNamespace()
_bot_stub.CONFIG = {}
_bot_stub.DEBUG_MODE = False
_bot_stub.RANK_ROLES_PRIORITY = []
_bot_stub._resolve_notification_guild = lambda: None
_bot_stub._induction_count_for_user = lambda *_args, **_kwargs: 0
_bot_stub.__getattr__ = lambda _name: (lambda *args, **kwargs: None)
_g.bot = _bot_stub.bot
sys.modules.setdefault("opscribe.bot", _bot_stub)
sys.modules.setdefault("bot", _bot_stub)

roster_ops = importlib.import_module("opscribe.roster_ops")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _op(brother_ids, *, difficulty="mission", points=10, waves=None, brother_waves=None,
         black_laurels=False, dual_vigil=False, target_id=None, gene_carrier=None,
         gene_status=None):
    """Build a minimal AAR record dict."""
    rec = {
        "brother_ids": brother_ids,
        "difficulty_class": difficulty,
        "points_for_op": points,
        "black_laurels_in_mission": black_laurels,
        "dual_vigil_in_mission": dual_vigil,
    }
    if waves is not None:
        rec["waves"] = waves
    if brother_waves is not None:
        rec["brother_waves"] = brother_waves
    if target_id is not None:
        rec["target_package_id"] = target_id
    if gene_carrier is not None:
        rec["gene_seed_carrier_id"] = gene_carrier
        rec["gene_seed_status"] = gene_status or "carried"
        rec["gene_seed_base_points_for_carrier"] = 5
    return rec


# ---------------------------------------------------------------------------
# compute_stats_for_user_in_records
# ---------------------------------------------------------------------------

class TestComputeStatsForUserInRecords:
    """Regression tests focusing on mixed int/str ID types."""

    def test_string_ids_counted_correctly(self):
        records = [_op(["101", "102"], points=5), _op(["102", "103"], points=7)]
        result = roster_ops.compute_stats_for_user_in_records("101", records)
        assert result["ops"] == 1
        assert result["aar_points"] == 5

    def test_int_brother_ids_are_normalised_to_string(self):
        """brother_ids carrying ints must still match a str user_id."""
        records = [_op([101, 102], points=8), _op([103, 104], points=3)]
        result = roster_ops.compute_stats_for_user_in_records("101", records)
        assert result["ops"] == 1
        assert result["aar_points"] == 8

    def test_int_brother_waves_keys_are_normalised(self):
        """brother_waves dict with int keys must resolve correctly for the user."""
        # difficulty_class normal_siege: points come from waves // 5 * 3
        records = [_op([101, 102], difficulty="normal_siege", brother_waves={101: 10, 102: 5})]
        result = roster_ops.compute_stats_for_user_in_records("101", records)
        assert result["ops"] == 1
        # 10 waves // 5 * 3 = 6 aar_points
        assert result["aar_points"] == 6
        assert result["waves_participated"] == 10

    def test_mixed_int_str_ids_in_same_record(self):
        """A record with a mix of int and str ids in brother_ids is handled."""
        records = [_op([101, "102", 103], points=4)]
        assert roster_ops.compute_stats_for_user_in_records("101", records)["ops"] == 1
        assert roster_ops.compute_stats_for_user_in_records("102", records)["ops"] == 1

    def test_user_not_in_record_yields_zero(self):
        records = [_op([101, 102], points=5)]
        result = roster_ops.compute_stats_for_user_in_records("999", records)
        assert result["ops"] == 0
        assert result["aar_points"] == 0

    def test_gene_seed_carrier_points_accumulate(self):
        records = [_op(["101", "102"], gene_carrier="101", gene_status="carried")]
        result = roster_ops.compute_stats_for_user_in_records("101", records)
        assert result["gene_carries"] == 1
        assert result["gene_seed_points"] == 5

    def test_gene_seed_participant_gets_one_point(self):
        records = [_op(["101", "102"], gene_carrier="102", gene_status="carried")]
        result = roster_ops.compute_stats_for_user_in_records("101", records)
        assert result["gene_seed_points"] == 1


# ---------------------------------------------------------------------------
# _record_has_black_laurels_tag
# ---------------------------------------------------------------------------

class TestRecordHasBlackLaurelsTag:
    def test_returns_true_when_flag_set(self):
        assert roster_ops._record_has_black_laurels_tag({"black_laurels_in_mission": True})
        assert roster_ops._record_has_black_laurels_tag({"black_laurels_in_difficulty": True})
        assert roster_ops._record_has_black_laurels_tag({"black_laurels_mentioned_elsewhere": True})

    def test_returns_false_when_no_flag(self):
        assert not roster_ops._record_has_black_laurels_tag({})
        assert not roster_ops._record_has_black_laurels_tag({"black_laurels_in_mission": False})


# ---------------------------------------------------------------------------
# _extract_directive_ids_from_record
# ---------------------------------------------------------------------------

class TestExtractDirectiveIdsFromRecord:
    def test_single_id(self):
        ids = roster_ops._extract_directive_ids_from_record({"target_package_id": "tp1"})
        assert ids == {"tp1"}

    def test_list_ids(self):
        ids = roster_ops._extract_directive_ids_from_record({"target_package_ids": ["tp2", "tp3"]})
        assert ids == {"tp2", "tp3"}

    def test_both_single_and_list_merged(self):
        ids = roster_ops._extract_directive_ids_from_record(
            {"target_package_id": "tp1", "target_package_ids": ["tp2", "tp3"]}
        )
        assert ids == {"tp1", "tp2", "tp3"}

    def test_int_ids_coerced_to_str(self):
        ids = roster_ops._extract_directive_ids_from_record({"target_package_ids": [42, 99]})
        assert ids == {"42", "99"}

    def test_empty_record(self):
        assert roster_ops._extract_directive_ids_from_record({}) == set()


class TestMemberCompletedStrikeDirectiveCount:
    def test_counts_only_completed_for_signed_member(self):
        tp_data = {
            "packages": {
                "p1": {"status": "completed", "signed_up": [101], "assigned_specialist_ids": []},
                "p2": {"status": "failed", "signed_up": [101], "assigned_specialist_ids": []},
                "p3": {"status": "lapsed", "signed_up": [101], "assigned_specialist_ids": []},
                "p4": {"status": "deployed", "signed_up": [101], "assigned_specialist_ids": []},
            }
        }
        tp_stub = types.SimpleNamespace(_load_tp=lambda: tp_data)

        with patch.dict(sys.modules, {"opscribe.target_packages_ops": tp_stub}):
            result = roster_ops._member_completed_strike_directive_count("101")

        assert result == 1

    def test_counts_specialist_membership(self):
        tp_data = {
            "packages": {
                "p1": {"status": "completed", "signed_up": [], "assigned_specialist_ids": [202]},
                "p2": {"status": "failed", "signed_up": [], "assigned_specialist_ids": [202]},
            }
        }
        tp_stub = types.SimpleNamespace(_load_tp=lambda: tp_data)

        with patch.dict(sys.modules, {"opscribe.target_packages_ops": tp_stub}):
            result = roster_ops._member_completed_strike_directive_count("202")

        assert result == 1

    def test_package_counted_once_when_member_is_signed_and_specialist(self):
        tp_data = {
            "packages": {
                "p1": {"status": "completed", "signed_up": [303], "assigned_specialist_ids": [303]},
            }
        }
        tp_stub = types.SimpleNamespace(_load_tp=lambda: tp_data)

        with patch.dict(sys.modules, {"opscribe.target_packages_ops": tp_stub}):
            result = roster_ops._member_completed_strike_directive_count("303")

        assert result == 1


class TestMemberCompletedStrikeDirectiveCountRecent:
    def test_counts_completed_package_in_last_7_days(self):
        now = datetime.now(timezone.utc)
        tp_data = {
            "packages": {
                "p1": {
                    "status": "completed",
                    "completed_at": (now - timedelta(days=2)).isoformat(),
                    "signed_up": [101],
                    "assigned_specialist_ids": [],
                    "assigned_captain_id": None,
                    "submitted_by": None,
                },
                "p2": {
                    "status": "completed",
                    "completed_at": (now - timedelta(days=9)).isoformat(),
                    "signed_up": [101],
                    "assigned_specialist_ids": [],
                },
            }
        }
        tp_stub = types.SimpleNamespace(_load_tp=lambda: tp_data)

        with patch.dict(sys.modules, {"opscribe.target_packages_ops": tp_stub}):
            result = roster_ops._member_completed_strike_directive_count_recent("101", window_days=7)

        assert result == 1

    def test_excludes_non_completed_packages(self):
        now = datetime.now(timezone.utc)
        tp_data = {
            "packages": {
                "p1": {
                    "status": "failed",
                    "completed_at": (now - timedelta(days=1)).isoformat(),
                    "signed_up": [202],
                },
                "p2": {
                    "status": "lapsed",
                    "completed_at": (now - timedelta(days=1)).isoformat(),
                    "signed_up": [202],
                },
            }
        }
        tp_stub = types.SimpleNamespace(_load_tp=lambda: tp_data)

        with patch.dict(sys.modules, {"opscribe.target_packages_ops": tp_stub}):
            result = roster_ops._member_completed_strike_directive_count_recent("202", window_days=7)

        assert result == 0

    def test_accepts_zulu_completed_at_timestamp(self):
        now = datetime.now(timezone.utc)
        completed_z = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        tp_data = {
            "packages": {
                "p1": {
                    "status": "completed",
                    "completed_at": completed_z,
                    "signed_up": [303],
                    "assigned_specialist_ids": [],
                }
            }
        }
        tp_stub = types.SimpleNamespace(_load_tp=lambda: tp_data)

        with patch.dict(sys.modules, {"opscribe.target_packages_ops": tp_stub}):
            result = roster_ops._member_completed_strike_directive_count_recent("303", window_days=7)

        assert result == 1

    def test_counts_submitted_by_and_participants_shape(self):
        now = datetime.now(timezone.utc)
        tp_data = {
            "packages": {
                "p1": {
                    "status": "completed",
                    "completed_at": (now - timedelta(days=1)).isoformat(),
                    "signed_up": [],
                    "assigned_specialist_ids": [],
                    "submitted_by": 404,
                },
                "p2": {
                    "status": "completed",
                    "completed_at": (now - timedelta(days=1)).isoformat(),
                    "signed_up": [],
                    "assigned_specialist_ids": [],
                    "participants": [{"user_id": "404"}],
                },
            }
        }
        tp_stub = types.SimpleNamespace(_load_tp=lambda: tp_data)

        with patch.dict(sys.modules, {"opscribe.target_packages_ops": tp_stub}):
            result = roster_ops._member_completed_strike_directive_count_recent("404", window_days=7)

        assert result == 2


# ---------------------------------------------------------------------------
# _compute_killteam_sendto_snapshot_7d
# ---------------------------------------------------------------------------

class TestComputeKillteamSendtoSnapshot7d:
    """Full aggregation: patch _get_missions_last_days and _build_pair_counts."""

    def _run(self, member_ids, missions, pair_counts=None, label_map=None, lifetime_aar_map=None, tp_packages=None):
        if pair_counts is None:
            pair_counts = {}
        if label_map is None:
            label_map = {}
        if lifetime_aar_map is None:
            lifetime_aar_map = {}
        if tp_packages is None:
            tp_packages = {}

        def _fake_label(guild, uid, **_kw):
            return label_map.get(uid, uid)

        def _fake_lifetime(uid):
            return {"aar_points": int(lifetime_aar_map.get(uid, 0) or 0)}

        tp_stub = types.SimpleNamespace(_load_tp=lambda: {"packages": tp_packages})

        with (
            patch.dict(sys.modules, {"opscribe.target_packages_ops": tp_stub}),
            patch.object(roster_ops, "_get_missions_last_days", return_value=missions),
            patch.object(roster_ops, "_build_pair_counts", return_value=pair_counts),
            patch.object(roster_ops, "_format_member_styled", side_effect=_fake_label),
            patch.object(roster_ops, "compute_stats_for_user", side_effect=_fake_lifetime),
        ):
            return roster_ops._compute_killteam_sendto_snapshot_7d(member_ids, guild=None)

    def test_empty_member_list_returns_empty_rows(self):
        result = self._run([], [])
        assert result["member_rows"] == []
        assert result["window_days"] == 7

    def test_aar_delta_counts_op_points(self):
        missions = [_op(["u1", "u2"], points=10), _op(["u1"], points=5)]
        result = self._run(["u1", "u2"], missions)
        rows = {r["member_id"]: r for r in result["member_rows"]}
        assert rows["u1"]["aar_delta"] == 15
        assert rows["u2"]["aar_delta"] == 10

    def test_int_brother_ids_counted_via_normalisation(self):
        """Records with int brother_ids are matched to str member_ids."""
        missions = [_op([1, 2], points=7)]
        result = self._run(["1", "2"], missions)
        rows = {r["member_id"]: r for r in result["member_rows"]}
        assert rows["1"]["aar_delta"] == 7
        assert rows["2"]["aar_delta"] == 7

    def test_omega_ops_counted(self):
        missions = [_op(["u1"], difficulty="omega_ops", points=20)]
        result = self._run(["u1"], missions)
        assert result["member_rows"][0]["omega_count"] == 1

    def test_black_laurels_counted(self):
        missions = [_op(["u1"], black_laurels=True), _op(["u1"], black_laurels=False)]
        result = self._run(["u1"], missions)
        assert result["member_rows"][0]["black_laurels_count"] == 1

    def test_dual_vigil_counted(self):
        missions = [_op(["u1"], dual_vigil=True), _op(["u1"], dual_vigil=True)]
        result = self._run(["u1"], missions)
        assert result["member_rows"][0]["dual_vigil_count"] == 2

    def test_strike_directive_ids_counted(self):
        now = datetime.now(timezone.utc)
        missions = [
            _op(["u1"], target_id="tp1"),
            _op(["u1"], target_id="tp2"),
            _op(["u1"], target_id="tp1"),  # duplicate → still 2 unique
        ]
        tp_packages = {
            "tp1": {"status": "completed", "completed_at": (now - timedelta(days=1)).isoformat(), "signed_up": ["u1"], "assigned_specialist_ids": []},
            "tp2": {"status": "completed", "completed_at": (now - timedelta(days=2)).isoformat(), "signed_up": ["u1"], "assigned_specialist_ids": []},
        }
        result = self._run(["u1"], missions, tp_packages=tp_packages)
        assert result["member_rows"][0]["strike_directives_count"] == 2

    def test_completed_directives_outside_window_are_excluded(self):
        now = datetime.now(timezone.utc)
        missions = [_op(["u1"], points=0)]
        tp_packages = {
            "tp1": {"status": "completed", "completed_at": (now - timedelta(days=10)).isoformat(), "signed_up": ["u1"], "assigned_specialist_ids": []},
        }
        result = self._run(["u1"], missions, tp_packages=tp_packages)
        assert result["member_rows"] == []

    def test_combat_bond_score_from_pair_counts(self):
        missions = [_op(["u1", "u2"], points=5)]
        pair_counts = {("u1", "u2"): 3}
        result = self._run(["u1", "u2"], missions, pair_counts=pair_counts)
        rows = {r["member_id"]: r for r in result["member_rows"]}
        assert rows["u1"]["cb_score"] == 3
        assert rows["u2"]["cb_score"] == 3

    def test_member_rows_sorted_by_aar_delta_desc(self):
        missions = [_op(["u1"], points=20), _op(["u2"], points=5)]
        result = self._run(["u1", "u2"], missions)
        ids = [r["member_id"] for r in result["member_rows"]]
        assert ids[0] == "u1"

    def test_member_not_in_any_record_gets_zero_stats(self):
        missions = [_op(["u2"], points=10)]
        result = self._run(["u1", "u2"], missions)
        rows = {r["member_id"]: r for r in result["member_rows"]}
        assert "u1" not in rows
        assert rows["u2"]["aar_delta"] == 10

    def test_member_label_used_from_format_helper(self):
        missions = [_op(["u1"], points=3)]
        result = self._run(["u1"], missions, label_map={"u1": "Brother Test"})
        assert result["member_rows"][0]["member_label"] == "Brother Test"

    def test_lifetime_aar_total_is_included(self):
        missions = [_op(["u1"], points=3)]
        result = self._run(["u1"], missions, lifetime_aar_map={"u1": 42})
        assert result["member_rows"][0]["lifetime_aar_total"] == 42

    def test_sendto_sd_count_uses_kt_intersection_across_completed_directives(self):
        now = datetime.now(timezone.utc)
        missions = [_op(["u1", "u2"], points=3)]
        tp_packages = {
            "tp1": {
                "status": "completed",
                "completed_at": (now - timedelta(days=1)).isoformat(),
                "signed_up": ["u1", "outsider"],
                "assigned_specialist_ids": ["u2"],
            },
            "tp2": {
                "status": "completed",
                "completed_at": (now - timedelta(days=1)).isoformat(),
                "signed_up": ["u2"],
                "assigned_specialist_ids": [],
            },
        }
        result = self._run(["u1", "u2"], missions, tp_packages=tp_packages)
        rows = {r["member_id"]: r for r in result["member_rows"]}
        assert rows["u1"]["strike_directives_count"] == 1
        assert rows["u2"]["strike_directives_count"] == 2


# ---------------------------------------------------------------------------
# _get_killteam_renown_summary
# ---------------------------------------------------------------------------

class TestGetKillteamRenownSummary:
    def test_missing_honors_file_returns_unproven_defaults(self):
        with patch("opscribe.roster_ops.os.path.exists", return_value=False):
            result = roster_ops._get_killteam_renown_summary("Alpha")

        assert result == {
            "tier": "Unproven",
            "tier_index": 0,
            "completions_28d": 0,
            "rep_earned_28d": 0.0,
            "unlocks": "No KT renown unlocks yet",
        }

    def test_sworn_tier_returns_expected_unlocks_and_stats(self):
        payload = {
            "kill_teams": {
                "Alpha": {
                    "tier": "Sworn",
                    "tier_index": 3,
                    "completions_28d": 6,
                    "rep_earned_28d": 15.0,
                }
            }
        }

        with (
            patch("opscribe.roster_ops.os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data="{}")),
            patch("opscribe.roster_ops.json.load", return_value=payload),
        ):
            result = roster_ops._get_killteam_renown_summary("Alpha")

        assert result == {
            "tier": "Sworn",
            "tier_index": 3,
            "completions_28d": 6,
            "rep_earned_28d": 15.0,
            "unlocks": "Cloaks, Iron Halos",
        }

    def test_eternal_tier_returns_jericho_lore_unlock(self):
        payload = {
            "kill_teams": {
                "Alpha": {
                    "tier": "Eternal",
                    "completions_28d": 12,
                    "rep_earned_28d": 38.0,
                }
            }
        }

        with (
            patch("opscribe.roster_ops.os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data="{}")),
            patch("opscribe.roster_ops.json.load", return_value=payload),
        ):
            result = roster_ops._get_killteam_renown_summary("Alpha")

        assert result["tier"] == "Eternal"
        assert result["tier_index"] == 5
        assert result["unlocks"] == "Featured in Jericho lore"

    def test_short_name_resolves_full_kill_team_key(self):
        payload = {
            "kill_teams": {
                "Kill Team Alpha": {
                    "tier": "Vigilant",
                    "tier_index": 2,
                    "completions_28d": 4,
                    "rep_earned_28d": 9.25,
                }
            }
        }

        with (
            patch("opscribe.roster_ops.os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data="{}")),
            patch("opscribe.roster_ops.json.load", return_value=payload),
        ):
            result = roster_ops._get_killteam_renown_summary("Alpha")

        assert result == {
            "tier": "Vigilant",
            "tier_index": 2,
            "completions_28d": 4,
            "rep_earned_28d": 9.25,
            "unlocks": "Cloaks",
        }

    def test_full_name_resolves_short_kill_team_key(self):
        payload = {
            "kill_teams": {
                "Alpha": {
                    "tier": "Initiated",
                    "tier_index": 1,
                    "completions_28d": 1,
                    "rep_earned_28d": 2.0,
                }
            }
        }

        with (
            patch("opscribe.roster_ops.os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data="{}")),
            patch("opscribe.roster_ops.json.load", return_value=payload),
        ):
            result = roster_ops._get_killteam_renown_summary("Kill Team Alpha")

        assert result == {
            "tier": "Initiated",
            "tier_index": 1,
            "completions_28d": 1,
            "rep_earned_28d": 2.0,
            "unlocks": "No KT renown unlocks yet",
        }

    def test_hyphenated_kill_team_name_resolves_short_key(self):
        payload = {
            "kill_teams": {
                "Alpha": {
                    "tier": "Vigilant",
                    "tier_index": 2,
                    "completions_28d": 4,
                    "rep_earned_28d": 9.25,
                }
            }
        }

        with (
            patch("opscribe.roster_ops.os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data="{}")),
            patch("opscribe.roster_ops.json.load", return_value=payload),
        ):
            result = roster_ops._get_killteam_renown_summary("Kill-Team Alpha")

        assert result == {
            "tier": "Vigilant",
            "tier_index": 2,
            "completions_28d": 4,
            "rep_earned_28d": 9.25,
            "unlocks": "Cloaks",
        }
