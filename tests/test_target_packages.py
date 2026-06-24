"""Unit tests for target_packages_ops pure-logic functions.

Tests cover:
- _draw_requirements: tier weights, HC cap, Hard-Strat vs Omega-Strat caps
- _draw_strats: buff/debuff counts per rep tier, mode exclusions, conflict rules
- _check_deployed: sign-up thresholds, specialist coverage
- _is_eligible_to_sign_up: unit scope, double-sign-up, debug bypass
- LOA role filtering via _get_active_roles_in_guild logic (unit-level)
- loa_ops._get_active_loa: expiry logic
"""

import asyncio
import sys
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Discord stub — must be installed before importing opscribe modules
# ---------------------------------------------------------------------------

def _install_discord_stub():
    if "discord" in sys.modules:
        return
    discord_stub = types.ModuleType("discord")
    discord_stub.Member = object
    discord_stub.User = object
    discord_stub.Guild = object
    discord_stub.Embed = object
    discord_stub.File = object
    discord_stub.Object = object
    discord_stub.Role = object
    discord_stub.Interaction = object
    discord_stub.AllowedMentions = object
    discord_stub.SelectOption = object
    discord_stub.Forbidden = Exception
    discord_stub.NotFound = Exception
    discord_stub.utils = types.ModuleType("discord.utils")

    ac = types.ModuleType("discord.app_commands")
    ac.command = lambda **kw: (lambda f: f)
    ac.describe = lambda **kw: (lambda f: f)
    ac.CommandTree = object
    discord_stub.app_commands = ac

    ext = types.ModuleType("discord.ext")
    tasks = types.ModuleType("discord.ext.tasks")
    tasks.loop = lambda **kw: (lambda f: f)
    ext.tasks = tasks
    discord_stub.ext = ext

    ui = types.ModuleType("discord.ui")
    ui.View = type("View", (), {"__init_subclass__": classmethod(lambda cls, **kw: None)})
    ui.Button = object
    ui.Select = object
    ui.UserSelect = object
    ui.RoleSelect = object
    ui.button = lambda **kw: (lambda f: f)
    ui.select = lambda **kw: (lambda f: f)
    discord_stub.ui = ui
    discord_stub.ButtonStyle = types.SimpleNamespace(secondary=2, success=3, danger=4, primary=1)

    sys.modules["discord"] = discord_stub
    sys.modules["discord.app_commands"] = ac
    sys.modules["discord.ext"] = ext
    sys.modules["discord.ext.tasks"] = tasks
    sys.modules["discord.ui"] = ui


_install_discord_stub()

from opscribe.target_packages_ops import (  # noqa: E402
    _draw_requirements,
    _draw_strats,
    _check_deployed,
    _compute_honors,
    _post_batch_summary,
    _batch_company_stats,
    _select_package_multiplier,
    expire_packages,
    _STRAT_TABLE,
    _CADRE_SPECIALIST_ROLES,
    _REQ_TIER_NO_REQ,
    _REQ_TIER_HC,
    _REQ_TIER_COMPANY_COMMAND,
    STATUS_RECRUITING,
    STATUS_DEPLOYED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_LAPSED,
    _can_actor_remove_attached_target,
    _remove_target_from_package,
    _generate_single_package,
    _TIER_ROLES,
    _generate_unique_batch_id,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pkg(status=STATUS_RECRUITING, mode="Hard-Strat", signed_up=None,
               required_roles=None, assigned_specialist_ids=None):
    return {
        "id": "OX-TEST1",
        "status": status,
        "mode": mode,
        "signed_up": signed_up or [],
        "required_roles": required_roles or [],
        "assigned_specialist_ids": assigned_specialist_ids or [],
        "assigned_kt": "Kill Team Alpha",
        "assigned_company": "Primus",
    }


def _make_member(role_names=(), member_id=12345):
    m = MagicMock()
    m.id = member_id
    m.bot = False
    roles = []
    for rn in role_names:
        r = MagicMock()
        r.name = rn
        r.id = hash(rn) & 0xFFFFFFFF
        roles.append(r)
    m.roles = roles
    m.display_name = f"M{member_id}"
    return m


def _make_guild(members):
    g = MagicMock()
    g.members = members
    g.get_member = lambda uid: next((m for m in members if m.id == uid), None)
    return g


def _with_company_role(member, company_name="Watch Company Primus"):
    r = MagicMock()
    r.name = company_name
    r.id = hash(company_name) & 0xFFFFFFFF
    member.roles = list(member.roles) + [r]
    return member


class TestRemoveAuthority:
    def test_highcom_no_requirement_self_attached_removable_by_self(self):
        actor = _make_member(["Watch Techmarine"], member_id=10)
        pkg = _make_pkg(
            status=STATUS_RECRUITING,
            signed_up=[],
            required_roles=[],
            assigned_specialist_ids=[10],
        )
        ok, kinds, _ = _can_actor_remove_attached_target(actor, actor, 10, pkg, _make_guild([actor]))
        assert ok is True
        assert "specialist" in kinds

    def test_highcom_no_requirement_self_attached_not_cadre_path(self):
        actor = _make_member(["Watch Captain"], member_id=1)
        target = _make_member(["Watch Techmarine"], member_id=2)
        actor = _with_company_role(actor)
        target = _with_company_role(target)
        pkg = _make_pkg(
            status=STATUS_RECRUITING,
            signed_up=[],
            required_roles=[],
            assigned_specialist_ids=[2],
        )
        pkg["assigned_company"] = "Watch Company Primus"
        ok, kinds, _ = _can_actor_remove_attached_target(actor, target, 2, pkg, _make_guild([actor, target]))
        assert ok is True
        # Allowed here through company command scope, not specialist-only cadre scope.
        assert kinds == {"specialist"}

    def test_company_specialist_no_requirement_removable_by_company_command(self):
        cpt = _with_company_role(_make_member(["Watch Captain"], member_id=11))
        target = _with_company_role(_make_member(["Watch Techmarine"], member_id=22))
        pkg = _make_pkg(
            status=STATUS_RECRUITING,
            signed_up=[22],
            required_roles=[],
            assigned_specialist_ids=[],
        )
        pkg["assigned_company"] = "Watch Company Primus"
        ok, kinds, _ = _can_actor_remove_attached_target(cpt, target, 22, pkg, _make_guild([cpt, target]))
        assert ok is True
        assert kinds == {"signed"}

    def test_ktc_no_requirement_lord_executioner_not_cadre_override(self):
        actor = _make_member(["Lord Executioner"], member_id=30)
        target = _make_member(["Kill Team Champion", "Kill Team Alpha"], member_id=31)
        pkg = _make_pkg(
            status=STATUS_RECRUITING,
            signed_up=[31],
            required_roles=[],
            assigned_specialist_ids=[],
        )
        pkg["assigned_kt"] = "Kill Team Alpha"
        ok, _kinds, reason = _can_actor_remove_attached_target(actor, target, 31, pkg, _make_guild([actor, target]))
        assert ok is False
        assert "not authorized" in reason.lower()

    def test_sgt_own_kt_scope(self):
        sgt = _make_member(["Watch Sergeant", "Kill Team Alpha"], member_id=40)
        target = _make_member(["Watch Brother", "Kill Team Alpha"], member_id=41)
        pkg = _make_pkg(status=STATUS_RECRUITING, signed_up=[41], required_roles=[], assigned_specialist_ids=[])
        pkg["assigned_kt"] = "Kill Team Alpha"
        ok, kinds, _ = _can_actor_remove_attached_target(sgt, target, 41, pkg, _make_guild([sgt, target]))
        assert ok is True
        assert kinds == {"signed"}

    def test_forgemaster_cannot_remove_required_ktc(self):
        actor = _make_member(["Forgemaster"], member_id=50)
        target = _make_member(["Kill Team Champion", "Kill Team Alpha"], member_id=51)
        pkg = _make_pkg(
            status=STATUS_RECRUITING,
            signed_up=[51],
            required_roles=["Kill Team Champion"],
            assigned_specialist_ids=[],
        )
        pkg["assigned_kt"] = "Kill Team Alpha"
        ok, _kinds, reason = _can_actor_remove_attached_target(actor, target, 51, pkg, _make_guild([actor, target]))
        assert ok is False
        assert "required specialist role" in reason.lower()

    def test_lord_executioner_can_remove_required_ktc(self):
        actor = _make_member(["Lord Executioner"], member_id=52)
        target = _make_member(["Kill Team Champion", "Kill Team Alpha"], member_id=53)
        pkg = _make_pkg(
            status=STATUS_RECRUITING,
            signed_up=[53],
            required_roles=["Kill Team Champion"],
            assigned_specialist_ids=[],
        )
        pkg["assigned_kt"] = "Kill Team Alpha"
        ok, kinds, _reason = _can_actor_remove_attached_target(actor, target, 53, pkg, _make_guild([actor, target]))
        assert ok is True
        assert "signed" in kinds


class TestRemoveOperation:
    def test_remove_target_from_package_both_lists(self):
        target = _make_member(["Watch Techmarine"], member_id=77)
        pkg = _make_pkg(
            status=STATUS_DEPLOYED,
            mode="Hard-Strat",
            signed_up=[77, 2, 3],
            required_roles=["Watch Techmarine"],
            assigned_specialist_ids=[77],
        )
        pkg["specialist_assigners"] = {"77": 9001}
        ok, msg = _remove_target_from_package(pkg, 77, {"signed", "specialist"}, _make_guild([target]))
        assert ok is True
        assert "sign-up" in msg.lower() and "specialist" in msg.lower()
        assert 77 not in pkg["signed_up"]
        assert 77 not in pkg["assigned_specialist_ids"]
        assert "77" not in pkg.get("specialist_assigners", {})
        assert pkg["status"] == STATUS_RECRUITING


# ---------------------------------------------------------------------------
# _draw_requirements
# ---------------------------------------------------------------------------

class TestDrawRequirements:
    ALL_ROLES = {
        "Watch Veteran", "Oathsworn", "Watch Sergeant", "Kill Team Champion",
        "Watch Captain", "Watch Lieutenant", "Company Champion",
        "Watch Techmarine", "Watch Apothecary", "Watch Chaplain",
        "Watch Librarian", "Watch Keeper", "Honored Dreadnought",
        "Watch Master", "Lord Executioner", "Forgemaster", "Chief Apothecary",
        "High Chaplain", "Huntmaster", "Void Warden", "Castellan",
        "Venerable Dreadnought",
    }
    # Count dict with 1 of each role — same as the set but in dict form
    ALL_ROLES_DICT = {r: 1 for r in ALL_ROLES}
    # Count dict with 3 of each role — allows duplicates
    ALL_ROLES_MULTI = {r: 3 for r in ALL_ROLES}

    def test_no_req_possible(self):
        results = [_draw_requirements(self.ALL_ROLES) for _ in range(200)]
        assert any(r[0] == _REQ_TIER_NO_REQ for r in results)

    def test_hard_strat_max_3_reqs(self):
        for _ in range(100):
            _, roles = _draw_requirements(self.ALL_ROLES, mode="Hard-Strat")
            assert len(roles) <= 3

    def test_omega_strat_max_5_reqs(self):
        for _ in range(100):
            _, roles = _draw_requirements(self.ALL_ROLES, mode="Omega-Strat")
            assert len(roles) <= 5

    def test_omega_strat_max_2_omega_tier_reqs(self):
        omega_roles = set(_TIER_ROLES[_REQ_TIER_COMPANY_COMMAND] + _TIER_ROLES[_REQ_TIER_HC])
        for _ in range(200):
            _, roles = _draw_requirements(self.ALL_ROLES_MULTI, mode="Omega-Strat")
            omega_count = sum(1 for role in roles if role in omega_roles)
            assert omega_count <= 2

    def test_max_1_hc_role(self):
        hc_roles = {
            "Watch Master", "Lord Executioner", "Forgemaster", "Chief Apothecary",
            "High Chaplain", "Huntmaster", "Void Warden", "Castellan", "Venerable Dreadnought",
        }
        for _ in range(200):
            _, roles = _draw_requirements(self.ALL_ROLES_MULTI, mode="Omega-Strat")
            hc_count = sum(1 for r in roles if r in hc_roles)
            assert hc_count <= 1

    def test_no_duplicate_with_count_1(self):
        """When each role has count=1, no duplicates should appear."""
        for _ in range(100):
            _, roles = _draw_requirements(self.ALL_ROLES_DICT)
            assert len(roles) == len(set(roles))

    def test_duplicates_allowed_with_count_gt_1(self):
        """When roles have count>1, duplicates can appear on Omega-Strat."""
        # With 3 of each role and 5 slots, we may get duplicates eventually
        found_duplicate = False
        for _ in range(500):
            _, roles = _draw_requirements(self.ALL_ROLES_MULTI, mode="Omega-Strat")
            if len(roles) != len(set(roles)):
                found_duplicate = True
                break
        assert found_duplicate, "Expected at least one duplicate draw with multi-count roles"

    def test_duplicate_capped_by_count(self):
        """A role with count=2 cannot appear 3 times."""
        limited = {r: 2 for r in self.ALL_ROLES}
        for _ in range(200):
            _, roles = _draw_requirements(limited, mode="Omega-Strat")
            from collections import Counter
            counts = Counter(roles)
            for role, cnt in counts.items():
                assert cnt <= 2, f"{role} drawn {cnt} times but count=2"

    def test_empty_available_roles_returns_no_req(self):
        tier, roles = _draw_requirements(set())
        assert tier == _REQ_TIER_NO_REQ
        assert roles == []

    def test_roles_subset_of_available(self):
        limited = {"Watch Veteran", "Watch Sergeant"}
        for _ in range(50):
            _, roles = _draw_requirements(limited)
            for r in roles:
                assert r in limited


# ---------------------------------------------------------------------------
# _draw_strats
# ---------------------------------------------------------------------------

def _make_strats():
    """Build a minimal stratagem list for testing."""
    strats = []
    for i in range(10):
        strats.append({"name": f"Buff_{i}", "type": "buff", "restriction_categories": [], "specific_conflicts": []})
    for i in range(10):
        strats.append({"name": f"Debuff_{i}", "type": "debuff", "restriction_categories": [], "specific_conflicts": []})
    # Add the excluded strats
    strats.append({"name": "Great Responsibility", "type": "debuff", "restriction_categories": [], "specific_conflicts": []})
    strats.append({"name": "Fatality", "type": "debuff", "restriction_categories": [], "specific_conflicts": []})
    strats.append({"name": "You Only Live Once", "type": "debuff", "restriction_categories": [], "specific_conflicts": []})
    return strats


class TestDrawStrats:
    STRATS = _make_strats()

    def test_rep_neg3_counts(self):
        result = _draw_strats(-3.0, self.STRATS)
        core = result["core"]
        buffs = [s for s in core if s["type"] == "buff"]
        debuffs = [s for s in core if s["type"] == "debuff"]
        pos, neg = _STRAT_TABLE[-3]
        assert len(buffs) == pos
        assert len(debuffs) == neg

    def test_rep_0_counts(self):
        result = _draw_strats(0.0, self.STRATS)
        core = result["core"]
        buffs = [s for s in core if s["type"] == "buff"]
        debuffs = [s for s in core if s["type"] == "debuff"]
        pos, neg = _STRAT_TABLE[0]
        assert len(buffs) == pos
        assert len(debuffs) == neg

    def test_rep_3_counts(self):
        result = _draw_strats(60.0, self.STRATS)
        core = result["core"]
        buffs = [s for s in core if s["type"] == "buff"]
        debuffs = [s for s in core if s["type"] == "debuff"]
        pos, neg = _STRAT_TABLE[3]
        assert len(buffs) == pos
        assert len(debuffs) == neg

    def test_great_responsibility_excluded(self):
        result = _draw_strats(0.0, self.STRATS)
        names = [s["name"] for s in result["core"]]
        assert "Great Responsibility" not in names

    def test_fatality_excluded(self):
        result = _draw_strats(0.0, self.STRATS)
        names = [s["name"] for s in result["core"]]
        assert "Fatality" not in names

    def test_yolo_excluded_omega(self):
        result = _draw_strats(0.0, self.STRATS, mode="Omega-Strat")
        names = [s["name"] for s in result["core"]]
        assert "You Only Live Once" not in names

    def test_yolo_allowed_hard_strat(self):
        # YOLO is only excluded in Omega mode; in Hard-Strat it can appear
        found = False
        for _ in range(100):
            result = _draw_strats(0.0, self.STRATS, mode="Hard-Strat")
            if "You Only Live Once" in [s["name"] for s in result["core"]]:
                found = True
                break
        assert found, "You Only Live Once should be drawable in Hard-Strat mode"

    def test_conflict_respected(self):
        cat_strats = [
            {"name": "A", "type": "buff", "restriction_categories": ["cat_x"], "specific_conflicts": []},
            {"name": "B", "type": "buff", "restriction_categories": ["cat_x"], "specific_conflicts": []},
        ] + [{"name": f"Buff_{i}", "type": "buff", "restriction_categories": [], "specific_conflicts": []} for i in range(5)]
        for _ in range(50):
            result = _draw_strats(3.0, cat_strats)
            names = [s["name"] for s in result["core"]]
            assert not ("A" in names and "B" in names), "Conflicting strats A and B both drawn"


class TestGenerateSinglePackage:
    def test_mission_selection_uses_global_operations_pool(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        graph = {
            "nodes": [{"id": "Kastorel", "type": "dead_world"}],
            "world_type_missions": {"dead_world": [99]},
        }
        ops_list = [
            {"id": 1, "objective_type": "assassination"},
            {"id": 2, "objective_type": "recon"},
            {"id": 3, "objective_type": "recovery"},
        ]

        monkeypatch.setattr(tp, "ENABLE_OMEGA_PACKAGES", False)
        monkeypatch.setattr(tp, "_draw_requirement_tier", lambda *_args, **_kwargs: (_REQ_TIER_NO_REQ, []))
        monkeypatch.setattr(tp, "_draw_strats", lambda *_args, **_kwargs: {"core": [], "wildcards": []})
        monkeypatch.setattr(tp, "_build_briefing", lambda *_args, **_kwargs: "briefing")
        monkeypatch.setattr(tp, "_generate_package_id", lambda *_args, **_kwargs: "TP-1")
        monkeypatch.setattr(tp, "_generate_directive_code", lambda *_args, **_kwargs: "OX-1")
        monkeypatch.setattr(tp, "_generate_directive_name", lambda *_args, **_kwargs: "Directive")

        def choose_last(seq):
            return seq[-1]

        monkeypatch.setattr(tp.random, "choice", choose_last)

        pkg = _generate_single_package(
            existing_ids=set(),
            existing_codes=set(),
            existing_names=set(),
            rep=0.0,
            graph=graph,
            active_strats=[],
            templates={},
            available_roles=set(),
            ops_list=ops_list,
        )

        assert pkg["node"] == "Kastorel"
        assert pkg["world_type"] == "dead_world"
        assert pkg["mission_id"] == 3


# ---------------------------------------------------------------------------
# _check_deployed
# ---------------------------------------------------------------------------

class TestCheckDeployed:
    def test_not_deployed_not_enough_signed_up_hard(self):
        pkg = _make_pkg(mode="Hard-Strat", signed_up=[1, 2])
        guild = _make_guild([])
        assert _check_deployed(pkg, guild) is False

    def test_deployed_enough_hard(self):
        pkg = _make_pkg(mode="Hard-Strat", signed_up=[1, 2, 3])
        guild = _make_guild([])
        assert _check_deployed(pkg, guild) is True

    def test_not_deployed_not_enough_omega(self):
        pkg = _make_pkg(mode="Omega-Strat", signed_up=[1, 2, 3, 4])
        guild = _make_guild([])
        assert _check_deployed(pkg, guild) is False

    def test_deployed_enough_omega(self):
        pkg = _make_pkg(mode="Omega-Strat", signed_up=[1, 2, 3, 4, 5])
        guild = _make_guild([])
        assert _check_deployed(pkg, guild) is True

    def test_specialist_required_not_attached(self):
        pkg = _make_pkg(
            mode="Hard-Strat",
            signed_up=[1, 2, 3],
            required_roles=["Watch Apothecary"],
            assigned_specialist_ids=[],
        )
        specialist = _make_member(["Watch Apothecary"], member_id=99)
        guild = _make_guild([specialist])
        assert _check_deployed(pkg, guild) is False

    def test_specialist_required_and_attached(self):
        pkg = _make_pkg(
            mode="Hard-Strat",
            signed_up=[1, 2, 3],
            required_roles=["Watch Apothecary"],
            assigned_specialist_ids=[99],
        )
        specialist = _make_member(["Watch Apothecary"], member_id=99)
        guild = _make_guild([specialist])
        assert _check_deployed(pkg, guild) is True

    def test_line_role_requires_rank_coverage(self):
        # Watch Veteran is a line role — someone in signed_up must have Veteran+
        veteran = _make_member(["Watch Veteran"], member_id=10)
        pkg = _make_pkg(
            mode="Hard-Strat",
            signed_up=[1, 2, 10],  # 10 is the veteran
            required_roles=["Watch Veteran"],
        )
        guild = _make_guild([veteran])
        assert _check_deployed(pkg, guild) is True

    def test_line_role_not_covered_not_deployed(self):
        # All 3 signed up but none holds Watch Veteran rank
        pkg = _make_pkg(
            mode="Hard-Strat",
            signed_up=[1, 2, 3],
            required_roles=["Watch Veteran"],
        )
        guild = _make_guild([])  # guild has no members to resolve rank
        assert _check_deployed(pkg, guild) is False


# ---------------------------------------------------------------------------
# _is_eligible_to_sign_up
# ---------------------------------------------------------------------------

class TestIsEligibleToSignUp:
    def _base_pkg(self, signed_up=None):
        return {
            "id": "OX-TEST1",
            "status": STATUS_RECRUITING,
            "signed_up": signed_up or [],
            "required_roles": [],
            "assigned_kt": "Kill Team Alpha",
            "assigned_company": "Primus",
        }

    def test_already_signed_up(self):
        from opscribe.target_packages_ops import _is_eligible_to_sign_up
        pkg = self._base_pkg(signed_up=[100])
        member = _make_member(["Watch Brother", "Kill Team Alpha"], member_id=100)
        guild = _make_guild([member])
        ok, reason = _is_eligible_to_sign_up(member, pkg, guild)
        assert not ok
        assert "already" in reason.lower()

    def test_below_watch_brother_rank(self):
        from opscribe.target_packages_ops import _is_eligible_to_sign_up
        import opscribe.target_packages_ops as tp
        # Patch _is_debug_mode to return False and _is_admin to return False
        orig_debug = tp._is_debug_mode
        orig_admin = tp._is_admin
        tp._is_debug_mode = lambda: False
        tp._is_admin = lambda m: False
        try:
            pkg = self._base_pkg()
            member = _make_member([], member_id=200)  # no roles at all
            guild = _make_guild([member])
            ok, reason = _is_eligible_to_sign_up(member, pkg, guild)
            assert not ok
            assert "watch brother" in reason.lower()
        finally:
            tp._is_debug_mode = orig_debug
            tp._is_admin = orig_admin

    def test_debug_admin_bypass(self):
        from opscribe.target_packages_ops import _is_eligible_to_sign_up
        import opscribe.target_packages_ops as tp
        orig_debug = tp._is_debug_mode
        orig_admin = tp._is_admin
        tp._is_debug_mode = lambda: True
        tp._is_admin = lambda m: True
        try:
            pkg = self._base_pkg()
            member = _make_member([], member_id=999)  # no roles, still passes in debug
            guild = _make_guild([member])
            ok, reason = _is_eligible_to_sign_up(member, pkg, guild)
            assert ok
        finally:
            tp._is_debug_mode = orig_debug
            tp._is_admin = orig_admin


# ---------------------------------------------------------------------------
# LOA expiry logic (loa_ops)
# ---------------------------------------------------------------------------

class TestGetActiveLoa:
    def test_active_loa_returns_record(self, tmp_path, monkeypatch):
        import opscribe.loa_ops as loa
        future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        data = {"records": {"42": {"user_id": 42, "start": datetime.now(timezone.utc).isoformat(), "end": future, "set_by": 1}}}
        loa_file = tmp_path / "loa_records.json"
        loa_file.write_text(__import__("json").dumps(data))
        monkeypatch.setattr(loa, "LOA_RECORDS_PATH", str(loa_file))
        rec = loa._get_active_loa(42)
        assert rec is not None
        assert rec["user_id"] == 42

    def test_expired_loa_returns_none(self, tmp_path, monkeypatch):
        import opscribe.loa_ops as loa
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        data = {"records": {"42": {"user_id": 42, "start": past, "end": past, "set_by": 1}}}
        loa_file = tmp_path / "loa_records.json"
        loa_file.write_text(__import__("json").dumps(data))
        monkeypatch.setattr(loa, "LOA_RECORDS_PATH", str(loa_file))
        assert loa._get_active_loa(42) is None

    def test_future_start_loa_returns_none_until_start(self, tmp_path, monkeypatch):
        import opscribe.loa_ops as loa
        start = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        end = (datetime.now(timezone.utc) + timedelta(days=4)).isoformat()
        data = {"records": {"42": {"user_id": 42, "start": start, "end": end, "set_by": 1}}}
        loa_file = tmp_path / "loa_records.json"
        loa_file.write_text(__import__("json").dumps(data))
        monkeypatch.setattr(loa, "LOA_RECORDS_PATH", str(loa_file))
        assert loa._get_active_loa(42) is None

    def test_no_record_returns_none(self, tmp_path, monkeypatch):
        import opscribe.loa_ops as loa
        loa_file = tmp_path / "loa_records.json"
        loa_file.write_text('{"records": {}}')
        monkeypatch.setattr(loa, "LOA_RECORDS_PATH", str(loa_file))
        assert loa._get_active_loa(999) is None


# ---------------------------------------------------------------------------
# CADRE_SPECIALIST_ROLES membership
# ---------------------------------------------------------------------------

class TestCadreSpecialistRoles:
    def test_watch_chaplain_is_cadre(self):
        assert "Watch Chaplain" in _CADRE_SPECIALIST_ROLES

    def test_watch_apothecary_is_cadre(self):
        assert "Watch Apothecary" in _CADRE_SPECIALIST_ROLES

    def test_ktc_is_cadre(self):
        assert "Kill Team Champion" in _CADRE_SPECIALIST_ROLES

    def test_company_champion_is_cadre(self):
        assert "Company Champion" in _CADRE_SPECIALIST_ROLES

    def test_forgemaster_is_cadre(self):
        assert "Forgemaster" in _CADRE_SPECIALIST_ROLES

    def test_watch_veteran_not_cadre(self):
        # Line role — signs up via Comply, not assigned by cadre leader
        assert "Watch Veteran" not in _CADRE_SPECIALIST_ROLES

    def test_oathsworn_not_cadre(self):
        assert "Oathsworn" not in _CADRE_SPECIALIST_ROLES

    def test_watch_sergeant_not_cadre(self):
        assert "Watch Sergeant" not in _CADRE_SPECIALIST_ROLES


# ---------------------------------------------------------------------------
# _compute_honors
# ---------------------------------------------------------------------------

def _make_completed_pkg(pkg_id, kt, company, rep_before, rep_after, completed_at):
    """Build a minimal completed-package dict for honors scoring tests."""
    return {
        "id": pkg_id,
        "status": STATUS_COMPLETED,
        "mode": "Hard-Strat",
        "assigned_kt": kt,
        "assigned_company": company,
        "rep_before": rep_before,
        "rep_after": rep_after,
        "completed_at": completed_at,
    }


class TestComputeHonors:
    """Tests for _compute_honors() tier assignment logic."""

    # Fixed "now" used across tests so the 28-day cutoff is deterministic.
    _NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    _WITHIN = (datetime(2025, 5, 15, 12, 0, 0, tzinfo=timezone.utc)).isoformat()   # 17 days ago
    _OUTSIDE = (datetime(2025, 4, 1, 12, 0, 0, tzinfo=timezone.utc)).isoformat()   # 61 days ago
    _CUTOFF  = (datetime(2025, 5, 4, 12, 0, 0, tzinfo=timezone.utc)).isoformat()   # exactly 28 days ago

    def _call(self, packages: dict) -> dict:
        import opscribe.target_packages_ops as tp
        from unittest.mock import patch
        with patch.object(tp, "datetime") as mock_dt:
            mock_dt.now.return_value = self._NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            return _compute_honors({"packages": packages})

    def test_empty_data_returns_empty_dicts(self):
        result = self._call({})
        assert result == {"kill_teams": {}, "companies": {}}

    def test_failed_and_lapsed_packages_ignored(self):
        """Failed/lapsed directives don't contribute to honors scoring."""
        pkgs = {
            "p1": {
                "id": "p1",
                "status": STATUS_FAILED,
                "assigned_kt": "Alpha",
                "assigned_company": "Primus",
                "rep_before": 10.0,
                "rep_after": 8.0,
                "completed_at": self._WITHIN,
            },
            "p2": {
                "id": "p2",
                "status": STATUS_LAPSED,
                "assigned_kt": "Alpha",
                "assigned_company": "Primus",
                "rep_before": 8.0,
                "rep_after": 7.0,
                "completed_at": self._WITHIN,
            },
        }
        result = self._call(pkgs)
        assert result["kill_teams"] == {}
        assert result["companies"] == {}

    def test_package_outside_28_day_window_excluded(self):
        """Completions older than 28 days must not count towards tier."""
        pkgs = {
            "p1": _make_completed_pkg("p1", "Alpha", "Primus", 10.0, 12.0, self._OUTSIDE),
        }
        result = self._call(pkgs)
        assert result["kill_teams"] == {}
        assert result["companies"] == {}

    def test_package_at_cutoff_boundary_excluded(self):
        """A package completed strictly before the cutoff (28+ days ago) is excluded."""
        # The cutoff is `now - 28 days`; completed_at < cutoff means strictly before → excluded.
        # A package completed exactly 29 days ago is outside the window.
        _29_days_ago = (self._NOW - timedelta(days=29)).isoformat()
        pkgs = {
            "p1": _make_completed_pkg("p1", "Alpha", "Primus", 10.0, 12.0, _29_days_ago),
        }
        result = self._call(pkgs)
        assert result["kill_teams"] == {}
        assert result["companies"] == {}

    def test_kt_one_completion_low_rep_gives_initiated(self):
        """1 completion with rep_delta < 4 → Initiated (tier index 1)."""
        # rep delta = 1.0 → ri = 1 (≥1.0 threshold), ci = 1 (1 completion)
        # final = round(0.75*1 + 0.25*1) = round(1.0) = 1 → "Initiated"
        pkgs = {
            "p1": _make_completed_pkg("p1", "Alpha", "Primus", 10.0, 11.0, self._WITHIN),
        }
        result = self._call(pkgs)
        assert result["kill_teams"]["Alpha"]["tier"] == "Initiated"
        assert result["kill_teams"]["Alpha"]["completions_28d"] == 1

    def test_kt_high_rep_and_completions_gives_higher_tier(self):
        """Many completions with high rep delta escalates the tier."""
        # 6 completions, rep_delta 2 each = 12 total
        # ri: 12 >= 8.0 → index 3; ci: 6 completions >= 6 → index 3
        # final = round(0.75*3 + 0.25*3) = 3 → "Sworn"
        pkgs = {
            f"p{i}": _make_completed_pkg(f"p{i}", "Bravo", "Secundus", float(10 + i*2), float(12 + i*2), self._WITHIN)
            for i in range(6)
        }
        result = self._call(pkgs)
        assert result["kill_teams"]["Bravo"]["tier_index"] == 3
        assert result["kill_teams"]["Bravo"]["tier"] == "Sworn"

    def test_company_contributor_gate_caps_tier(self):
        """Company tier is capped when too few distinct KTs contributed."""
        # 3 completions each contributing 2 rep → co_rep=6, co_comp=3
        # ri: 6 >= 6.0 → index 2; ci: 3 >= 3 → index 2
        # raw_final = round(0.75*2 + 0.25*2) = 2 → "Recognized"
        # Tier index 2 needs _CO_KT_GATES[2] = 2 distinct KTs.
        # Only 1 KT contributed → gate triggers → raw_final drops to 1 → "Marked"
        pkgs = {
            f"p{i}": _make_completed_pkg(f"p{i}", "Alpha", "Primus", float(i*2), float(i*2+2), self._WITHIN)
            for i in range(3)
        }
        result = self._call(pkgs)
        tier_idx = result["companies"]["Primus"]["tier_index"]
        assert tier_idx < 2, f"Expected gate to cap tier below 2 but got {tier_idx}"
        assert result["companies"]["Primus"]["contributing_kts"] == 1

    def test_company_multi_kt_unlocks_higher_tier(self):
        """Two contributing KTs satisfy the gate for tier index 2."""
        # Same total stats as test_company_contributor_gate_caps_tier but split across 2 KTs
        pkgs = {
            "p0": _make_completed_pkg("p0", "Alpha", "Primus", 0.0, 2.0, self._WITHIN),
            "p1": _make_completed_pkg("p1", "Alpha", "Primus", 2.0, 4.0, self._WITHIN),
            "p2": _make_completed_pkg("p2", "Bravo", "Primus", 4.0, 6.0, self._WITHIN),
        }
        result = self._call(pkgs)
        assert result["companies"]["Primus"]["contributing_kts"] == 2
        assert result["companies"]["Primus"]["tier_index"] >= 2


class TestPostBatchSummaryBatchSelection:
    def test_prefers_latest_non_unknown_batch(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        selected_ids = []

        monkeypatch.setattr(tp, "_is_debug_mode", lambda: False)
        monkeypatch.setattr(tp, "_b", lambda *_args, **_kwargs: {})
        monkeypatch.setattr(tp, "_load_honors", lambda: {"kill_teams": {}, "companies": {}})
        monkeypatch.setattr(tp, "_save_honors", lambda _data: None)
        monkeypatch.setattr(tp, "_compute_honors", lambda _data: {"kill_teams": {}, "companies": {}})
        monkeypatch.setattr(tp, "_rep_delta_for_package", lambda pkg, _status: selected_ids.append(pkg["id"]) or 0)
        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(
                _get_award_announcement_channel=lambda _member: None,
                _resolve_killteam_for_member=lambda _member: None,
            ),
        )

        data = {
            "rep": 0.0,
            "entity_stats": {},
            "cycle": {},
            "packages": {
                "unknown": {"id": "unknown", "status": STATUS_COMPLETED, "generated_at": "not-a-date"},
                "legacy_old": {"id": "legacy_old", "status": STATUS_COMPLETED, "generated_at": "2026-06-01T12:00:00"},
                "legacy_new": {"id": "legacy_new", "status": STATUS_COMPLETED, "generated_at": "2026-06-08T12:00:00"},
            },
        }

        asyncio.run(_post_batch_summary(None, data))
        assert selected_ids == ["legacy_new"]

    def test_uses_unknown_when_only_unknown_exists(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        selected_ids = []

        monkeypatch.setattr(tp, "_is_debug_mode", lambda: False)
        monkeypatch.setattr(tp, "_b", lambda *_args, **_kwargs: {})
        monkeypatch.setattr(tp, "_load_honors", lambda: {"kill_teams": {}, "companies": {}})
        monkeypatch.setattr(tp, "_save_honors", lambda _data: None)
        monkeypatch.setattr(tp, "_compute_honors", lambda _data: {"kill_teams": {}, "companies": {}})
        monkeypatch.setattr(tp, "_rep_delta_for_package", lambda pkg, _status: selected_ids.append(pkg["id"]) or 0)
        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(
                _get_award_announcement_channel=lambda _member: None,
                _resolve_killteam_for_member=lambda _member: None,
            ),
        )

        data = {
            "rep": 0.0,
            "entity_stats": {},
            "cycle": {},
            "packages": {
                "unknown": {"id": "unknown", "status": STATUS_COMPLETED, "generated_at": "not-a-date"},
            },
        }

        asyncio.run(_post_batch_summary(None, data))
        assert selected_ids == ["unknown"]


class TestStrikeDirectiveMultiplier:
    def test_low_rep_band_uses_low_weights(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        seen = {}

        def fake_choices(population, weights=None, k=1):
            seen["population"] = population
            seen["weights"] = weights
            return [2]

        monkeypatch.setattr(tp.random, "choices", fake_choices)
        assert _select_package_multiplier(5.0) == 2
        assert seen["population"] == [1, 2, 3, 4]
        assert seen["weights"] == [65, 25, 10, 0]

    def test_high_rep_band_uses_high_weights(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        seen = {}

        def fake_choices(population, weights=None, k=1):
            seen["population"] = population
            seen["weights"] = weights
            return [4]

        monkeypatch.setattr(tp.random, "choices", fake_choices)
        assert _select_package_multiplier(52.0) == 4
        assert seen["weights"] == [10, 20, 35, 35]


class TestBatchIdGeneration:
    def test_generate_unique_batch_id_first_of_day(self):
        now = datetime(2026, 6, 24, 10, 0, 0, tzinfo=timezone.utc)
        data = {"packages": {}}
        assert _generate_unique_batch_id(data, now) == "BATCH-20260624-01"

    def test_generate_unique_batch_id_increments_same_day(self):
        now = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)
        data = {
            "packages": {
                "p1": {"batch_id": "BATCH-20260624-01"},
                "p2": {"batch_id": "BATCH-20260624-02"},
            }
        }
        assert _generate_unique_batch_id(data, now) == "BATCH-20260624-03"

    def test_generate_unique_batch_id_handles_legacy_unsuffixed(self):
        now = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)
        data = {
            "packages": {
                "p1": {"batch_id": "BATCH-20260624"},
                "p2": {"batch_id": "BATCH-20260624-02"},
            }
        }
        assert _generate_unique_batch_id(data, now) == "BATCH-20260624-03"


class TestHighcomBatchCompanyStats:
    def test_company_stats_only_use_current_batch(self):
        batch_pkgs = [
            {"assigned_company": "Primus", "status": STATUS_COMPLETED},
            {"assigned_company": "Primus", "status": STATUS_FAILED},
            {"assigned_company": "Secundus", "status": STATUS_LAPSED},
        ]
        stats = _batch_company_stats(batch_pkgs)
        assert stats == {
            "Primus": {"completed": 1, "failed": 1},
            "Secundus": {"completed": 0, "failed": 1},
        }


class TestExpiryWarnings:
    def test_warning_does_not_send_before_24h_window(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        sent = []
        store = {
            "rep": 30.0,
            "rep_scale_version": 2,
            "cycle": {
                "generated_at": None,
                "total": 0,
                "completed": 0,
                "failed": 0,
                "lapsed": 0,
                "batch_id": "BATCH-20260623",
            },
            "entity_stats": {"companies": {}, "kill_teams": {}, "cadres": {}},
            "packages": {
                "OX-1": {
                    "id": "OX-1",
                    "status": "unassigned",
                    "deadline": (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat(),
                    "batch_id": "BATCH-20260623",
                    "assigned_kt": None,
                    "assigned_company": None,
                }
            },
            "rep_embed_message_id": None,
        }

        monkeypatch.setattr(tp, "_load_tp", lambda: store)
        monkeypatch.setattr(tp, "_save_tp", lambda _data: None)
        monkeypatch.setattr(
            tp,
            "_b",
            lambda *_args, **_kwargs: {"target_packages": {"general_channel_id": 123}},
        )
        monkeypatch.setattr(tp, "_is_debug_mode", lambda: True)

        async def fake_notify_send(*args, **kwargs):
            sent.append(kwargs.get("content"))
            return object()

        monkeypatch.setattr(tp, "_notify_send", fake_notify_send)
        monkeypatch.setattr(tp.discord, "Embed", lambda **kwargs: MagicMock())
        guild = MagicMock()
        guild.get_channel = lambda _cid: object()
        guild.roles = []

        asyncio.run(expire_packages(guild))
        asyncio.run(expire_packages(guild))

        assert len(sent) == 0
        assert store["cycle"].get("general_warning_sent_at", {}) == {}

    def test_warning_sends_once_within_24h_window(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        sent = []
        store = {
            "rep": 30.0,
            "rep_scale_version": 2,
            "cycle": {
                "generated_at": None,
                "total": 0,
                "completed": 0,
                "failed": 0,
                "lapsed": 0,
                "batch_id": "BATCH-20260623",
            },
            "entity_stats": {"companies": {}, "kill_teams": {}, "cadres": {}},
            "packages": {
                "OX-1": {
                    "id": "OX-1",
                    "status": "unassigned",
                    "deadline": (datetime.now(timezone.utc) + timedelta(hours=23)).isoformat(),
                    "batch_id": "BATCH-20260623",
                    "assigned_kt": None,
                    "assigned_company": None,
                }
            },
            "rep_embed_message_id": None,
        }

        monkeypatch.setattr(tp, "_load_tp", lambda: store)
        monkeypatch.setattr(tp, "_save_tp", lambda _data: None)
        monkeypatch.setattr(
            tp,
            "_b",
            lambda *_args, **_kwargs: {"target_packages": {"general_channel_id": 123}},
        )
        monkeypatch.setattr(tp, "_is_debug_mode", lambda: True)

        async def fake_notify_send(*args, **kwargs):
            sent.append(kwargs.get("content"))
            return object()

        monkeypatch.setattr(tp, "_notify_send", fake_notify_send)
        monkeypatch.setattr(tp.discord, "Embed", lambda **kwargs: MagicMock())
        guild = MagicMock()
        guild.get_channel = lambda _cid: object()
        guild.roles = []

        asyncio.run(expire_packages(guild))
        asyncio.run(expire_packages(guild))

        assert len(sent) == 1
        assert sent[0] == f"<@&{tp.WATCH_BROTHER_ROLE_ID}>"
        assert store["cycle"].get("general_warning_sent_at", {}).get("BATCH-20260623") is not None
        assert store["cycle"]["last_general_warning_batch_id"] == "BATCH-20260623"


class TestDirectiveForumLifecycle:
    def test_config_company_forum_mapping_includes_primus_and_secundus(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        cfg = {
            "target_packages": {
                "directive_forum_parent_by_company": {
                    "Watch Company Primus": 1433351293103112202,
                    "Watch Company Secundus": 1458255656682258504,
                }
            }
        }
        monkeypatch.setattr(tp, "_b", lambda name: cfg if name == "CONFIG" else None)
        mapping = tp._directive_forum_parent_map()
        assert mapping["watch company primus"] == 1433351293103112202
        assert mapping["watch company secundus"] == 1458255656682258504
        assert "watch company tertius" not in mapping

    def test_resolve_directive_forum_parent_prefers_explicit_mapping(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        class FakeForumChannel:
            def __init__(self, cid):
                self.id = cid

        monkeypatch.setattr(tp.discord, "ForumChannel", FakeForumChannel, raising=False)

        primus_parent = FakeForumChannel(1433351293103112202)
        guild = MagicMock()
        guild.get_channel = lambda cid: primus_parent if int(cid) == 1433351293103112202 else None
        guild.channels = [primus_parent]

        monkeypatch.setattr(
            tp,
            "_b",
            lambda name: (
                {
                    "target_packages": {
                        "directive_forum_parent_by_company": {
                            "Watch Company Primus": 1433351293103112202,
                            "Watch Company Secundus": 1458255656682258504,
                        }
                    }
                }
                if name == "CONFIG"
                else ({1433351293103112202, 1458255656682258504} if name == "ALLOWED_KT_FORUM_PARENT_IDS" else None)
            ),
        )

        resolved = asyncio.run(tp._resolve_directive_forum_parent(guild, "Watch Company Primus"))
        assert resolved is primus_parent

    def test_delete_package_messages_deletes_forum_thread_and_clears_fields(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        class FakeThread:
            def __init__(self, tid):
                self.id = tid
                self.deleted = False

            async def delete(self):
                self.deleted = True

        monkeypatch.setattr(tp.discord, "Thread", FakeThread, raising=False)

        store = {
            "rep": 30.0,
            "rep_scale_version": 2,
            "cycle": {"generated_at": None, "total": 0, "completed": 0, "failed": 0, "lapsed": 0},
            "entity_stats": {"companies": {}, "kill_teams": {}, "cadres": {}},
            "packages": {
                "OX-1": {
                    "id": "OX-1",
                    "sgt_accept_channel_id": None,
                    "sgt_accept_message_id": None,
                    "signup_channel_id": None,
                    "signup_message_id": None,
                    "specialist_notification_msgs": [],
                    "message_refs": [],
                    "forum_thread_id": 999,
                    "forum_parent_id": 1433351293103112202,
                    "forum_created_at": "2026-06-23T00:00:00+00:00",
                }
            },
            "rep_embed_message_id": None,
        }

        fake_thread = FakeThread(999)
        monkeypatch.setattr(tp, "_load_tp", lambda: store)
        monkeypatch.setattr(tp, "_save_tp", lambda _data: None)

        async def _fake_resolve(_guild, _channel_id):
            return fake_thread

        monkeypatch.setattr(tp, "_resolve_channel", _fake_resolve)

        deleted = asyncio.run(tp._delete_package_messages("OX-1", MagicMock()))
        pkg = store["packages"]["OX-1"]

        assert deleted == 1
        assert fake_thread.deleted is True
        assert pkg["forum_thread_id"] is None
        assert pkg["forum_parent_id"] is None
        assert pkg["forum_created_at"] is None


class TestWeeklyRequestQuota:
    """Test weekly request quota limiting."""

    def test_can_request_when_no_timestamps(self):
        """First request should always be allowed."""
        import opscribe.target_packages_ops as tp
        cycle = {
            "batch_generation_timestamps": [],
        }
        now = datetime.now(timezone.utc)
        
        can_request, msg = tp._can_request_strike_directives(cycle, now, max_per_week=2)
        
        assert can_request is True
        assert msg == ""

    def test_can_request_under_quota(self):
        """Requests under quota should be allowed."""
        import opscribe.target_packages_ops as tp
        now = datetime.now(timezone.utc)
        one_day_ago = (now - timedelta(days=1)).isoformat()
        
        cycle = {
            "batch_generation_timestamps": [one_day_ago],
        }
        
        can_request, msg = tp._can_request_strike_directives(cycle, now, max_per_week=2)
        
        assert can_request is True
        assert msg == ""

    def test_cannot_request_at_quota_limit(self):
        """Requests at quota limit should be blocked."""
        import opscribe.target_packages_ops as tp
        now = datetime.now(timezone.utc)
        one_day_ago = (now - timedelta(days=1)).isoformat()
        two_days_ago = (now - timedelta(days=2)).isoformat()
        
        cycle = {
            "batch_generation_timestamps": [two_days_ago, one_day_ago],
        }
        
        can_request, msg = tp._can_request_strike_directives(cycle, now, max_per_week=2)
        
        assert can_request is False
        assert "quota reached" in msg.lower()
        assert "2 per week" in msg

    def test_cannot_request_over_quota(self):
        """Requests exceeding quota should be blocked."""
        import opscribe.target_packages_ops as tp
        now = datetime.now(timezone.utc)
        one_day_ago = (now - timedelta(days=1)).isoformat()
        two_days_ago = (now - timedelta(days=2)).isoformat()
        three_days_ago = (now - timedelta(days=3)).isoformat()
        
        cycle = {
            "batch_generation_timestamps": [three_days_ago, two_days_ago, one_day_ago],
        }
        
        can_request, msg = tp._can_request_strike_directives(cycle, now, max_per_week=2)
        
        assert can_request is False
        assert "quota reached" in msg.lower()

    def test_quota_reset_after_week(self):
        """Requests should be allowed after 7+ days."""
        import opscribe.target_packages_ops as tp
        now = datetime.now(timezone.utc)
        eight_days_ago = (now - timedelta(days=8)).isoformat()
        
        cycle = {
            "batch_generation_timestamps": [eight_days_ago],
        }
        
        can_request, msg = tp._can_request_strike_directives(cycle, now, max_per_week=2)
        
        assert can_request is True
        assert msg == ""

    def test_quota_filters_old_timestamps(self):
        """Timestamps older than 7 days should not count against quota."""
        import opscribe.target_packages_ops as tp
        now = datetime.now(timezone.utc)
        eight_days_ago = (now - timedelta(days=8)).isoformat()
        six_days_ago = (now - timedelta(days=6)).isoformat()
        
        cycle = {
            "batch_generation_timestamps": [eight_days_ago, six_days_ago],
        }
        
        # Only 1 timestamp in last 7 days, so can request (quota is 2)
        can_request, msg = tp._can_request_strike_directives(cycle, now, max_per_week=2)
        
        assert can_request is True

    def test_record_batch_generation_time(self):
        """Recording a batch generation time should add to the timestamps list."""
        import opscribe.target_packages_ops as tp
        cycle = {
            "batch_generation_timestamps": [],
        }
        now = datetime.now(timezone.utc)
        
        tp._record_batch_generation_time(cycle, now)
        
        assert len(cycle["batch_generation_timestamps"]) == 1
        assert cycle["batch_generation_timestamps"][0] == now.isoformat()

    def test_custom_quota_limit(self):
        """Quota limit should be configurable."""
        import opscribe.target_packages_ops as tp
        now = datetime.now(timezone.utc)
        one_day_ago = (now - timedelta(days=1)).isoformat()
        two_days_ago = (now - timedelta(days=2)).isoformat()
        
        cycle = {
            "batch_generation_timestamps": [two_days_ago, one_day_ago],
        }
        
        # With max_per_week=2, 2 requests are at quota (next request blocked)
        can_request, msg = tp._can_request_strike_directives(cycle, now, max_per_week=2)
        assert can_request is False
        
        # With max_per_week=3, 2 requests are under quota (next request allowed)
        can_request, msg = tp._can_request_strike_directives(cycle, now, max_per_week=3)
        assert can_request is True

    def test_error_message_includes_hours_remaining(self):
        """Error message should include hours remaining until next request."""
        import opscribe.target_packages_ops as tp
        now = datetime.now(timezone.utc)
        one_day_ago = (now - timedelta(days=1)).isoformat()
        two_days_ago = (now - timedelta(days=2)).isoformat()
        
        cycle = {
            "batch_generation_timestamps": [two_days_ago, one_day_ago],
        }
        
        can_request, msg = tp._can_request_strike_directives(cycle, now, max_per_week=2)
        
        assert can_request is False
        assert "hours" in msg.lower()
        assert "available" in msg.lower()
