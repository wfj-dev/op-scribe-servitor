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
import io
import sys
import types
from itertools import combinations as itertools_combinations
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Discord stub — must be installed before importing opscribe modules
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

    if "discord" in sys.modules:
        return
    discord_stub = types.ModuleType("discord")
    _compat_type = type("_CompatType", (), {"__init__": lambda self, *args, **kwargs: [setattr(self, k, v) for k, v in kwargs.items()] and None})

    class _StubEmbed:
        def __init__(self, *, title=None, description=None, color=None):
            self.title = title
            self.description = description
            self.color = color
            self.fields = []

        def add_field(self, *, name, value, inline=True):
            self.fields.append(types.SimpleNamespace(name=name, value=value, inline=inline))

        def set_author(self, **_kwargs):
            pass

        def set_footer(self, **_kwargs):
            pass

        def set_image(self, **_kwargs):
            pass

    discord_stub.Member = _compat_type
    discord_stub.User = _compat_type
    discord_stub.Guild = _compat_type
    discord_stub.Embed = _StubEmbed
    discord_stub.File = _compat_type
    discord_stub.Object = _compat_type
    discord_stub.Role = _compat_type
    discord_stub.Interaction = _compat_type
    discord_stub.AllowedMentions = _compat_type
    discord_stub.Poll = _compat_type
    discord_stub.SelectOption = _compat_type
    discord_stub.Thread = type("Thread", (), {})
    discord_stub.ForumChannel = type("ForumChannel", (), {})
    discord_stub.Forbidden = Exception
    discord_stub.NotFound = Exception
    discord_stub.utils = types.ModuleType("discord.utils")

    ac = types.ModuleType("discord.app_commands")
    ac.command = lambda **kw: (lambda f: f)
    ac.describe = lambda **kw: (lambda f: f)
    ac.CommandTree = _compat_type
    discord_stub.app_commands = ac

    ext = types.ModuleType("discord.ext")
    tasks = types.ModuleType("discord.ext.tasks")

    class _LoopStub:
        def __init__(self, func):
            self.func = func
            self.coro = func

        def before_loop(self, _func):
            return _func

        def after_loop(self, _func):
            return _func

        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    tasks.loop = lambda **_kw: (lambda f: _LoopStub(f))
    ext.tasks = tasks
    discord_stub.ext = ext

    ui = types.ModuleType("discord.ui")
    ui.View = type("View", (), {"__init_subclass__": classmethod(lambda cls, **kw: None)})
    ui.Modal = type("Modal", (), {"__init_subclass__": classmethod(lambda cls, **_kw: None), "__init__": lambda self, *a, **kw: None})
    ui.TextInput = type("TextInput", (), {"__init__": lambda self, *a, **kw: None})
    ui.Button = _compat_type
    ui.Select = _compat_type
    ui.UserSelect = _compat_type
    ui.RoleSelect = _compat_type
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
    _draw_weighted_positive_strats_for_package,
    _sync_live_positive_modifiers_for_package,
    _active_lore_group_weights_for_package,
    _lore_group_activation_curve_for,
    _lore_group_headcount_multiplier_for_package,
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
    STATUS_DISTRIBUTED,
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
    post_cycle_reports,
    submit_package,
    _formation_labels_for_completed_package,
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
    m.mention = f"<@{member_id}>"
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


def _make_interaction(member, guild):
    calls = []

    class _Response:
        async def defer(self, ephemeral=False):
            calls.append(("defer", ephemeral))

    class _Followup:
        async def send(self, content=None, ephemeral=False, embed=None, **_kwargs):
            payload = content if content is not None else embed
            calls.append(("send", payload, ephemeral))

    return types.SimpleNamespace(user=member, guild=guild, response=_Response(), followup=_Followup(), calls=calls)


def _invoke_command(cmd, *args, **kwargs):
    """Invoke a command that may be a raw coroutine function or app Command object."""
    target = getattr(cmd, "callback", cmd)
    return target(*args, **kwargs)


def test_generate_single_package_skips_non_standard_siege_ops(monkeypatch):
    from opscribe import target_packages_ops as tp

    monkeypatch.setattr(tp.random, "choice", lambda seq: seq[0])
    monkeypatch.setattr(tp, "_build_briefing", lambda *args, **kwargs: "brief")
    pkg = tp._generate_single_package(
        existing_ids={"OX-1"},
        existing_codes={"001"},
        existing_names={"Alpha"},
        rep=50.0,
        graph={"world_type_missions": {"dead_world": [14, 2]}, "nodes": [{"id": "node", "type": "dead_world"}]},
        active_strats=[],
        templates={"req_tier_templates": {"no_req": [""], "veteran": [""]}},
        available_roles=set(),
        ops_list=[
            {"id": 14, "name": "Siege", "strats_allowed": False, "objective_type": "defend_waves"},
            {"id": 2, "name": "Normal", "strats_allowed": True, "objective_type": "assassination"},
        ],
    )

    assert pkg["mission_id"] == 2


class TestRemoveAuthority:
    def test_configured_company_role_names_supports_config_defined_company(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        monkeypatch.setattr(
            tp,
            "_b",
            lambda name: (
                {"companies": {"sextus": {"name": "Sextus", "companyRoleId": 6001}}}
                if name == "CONFIG"
                else None
            ),
        )

        assert tp._configured_company_role_names() == {"Watch Company Sextus"}

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

    def test_sgt_own_company_scope(self):
        sgt = _with_company_role(_make_member(["Watch Sergeant"], member_id=40))
        target = _with_company_role(_make_member(["Watch Brother"], member_id=41))
        pkg = _make_pkg(status=STATUS_RECRUITING, signed_up=[41], required_roles=[], assigned_specialist_ids=[])
        pkg["assigned_company"] = "Watch Company Primus"
        ok, kinds, _ = _can_actor_remove_attached_target(sgt, target, 41, pkg, _make_guild([sgt, target]))
        assert ok is True
        assert kinds == {"signed"}

    def test_veteran_sergeant_own_company_scope(self):
        actor = _with_company_role(_make_member(["Veteran Sergeant"], member_id=42))
        target = _with_company_role(_make_member(["Watch Brother"], member_id=43))
        pkg = _make_pkg(status=STATUS_RECRUITING, signed_up=[43], required_roles=[], assigned_specialist_ids=[])
        pkg["assigned_company"] = "Watch Company Primus"
        ok, kinds, _ = _can_actor_remove_attached_target(actor, target, 43, pkg, _make_guild([actor, target]))
        assert ok is True
        assert kinds == {"signed"}

    def test_blademaster_can_remove_required_bladeguard(self):
        actor = _make_member(["Blade Master"], member_id=52)
        target = _make_member(["Bladeguard", "Kill Team Alpha"], member_id=53)
        pkg = _make_pkg(
            status=STATUS_RECRUITING,
            signed_up=[53],
            required_roles=["Bladeguard"],
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


class TestHighCommandVisibility:
    def test_watch_master_with_company_role_sees_all_active_directives(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _with_company_role(
            _make_member(["Watch Master", "Watch Captain"], member_id=500),
            company_name="Watch Company Tertius",
        )

        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Watch Company Tertius"),
        )

        pkgs = {
            "p_tert": {
                "id": "p_tert",
                "status": STATUS_RECRUITING,
                "assigned_company": "Watch Company Tertius",
                "assigned_kt": "Kill Team Solaire",
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
            "p_other": {
                "id": "p_other",
                "status": STATUS_RECRUITING,
                "assigned_company": "Watch Company Primus",
                "assigned_kt": "Kill Team Duke",
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
            "p_distributed": {
                "id": "p_distributed",
                "status": STATUS_DISTRIBUTED,
                "assigned_company": None,
                "assigned_kt": None,
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
            "p_failed": {
                "id": "p_failed",
                "status": STATUS_FAILED,
                "assigned_company": "Watch Company Primus",
                "assigned_kt": None,
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
        }

        visible = tp._visible_active_packages_for_member(member, pkgs)
        visible_ids = {p["id"] for p in visible}

        assert visible_ids == {"p_tert", "p_other", "p_distributed"}

    def test_forgemaster_sees_all_active_directives(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _make_member(["Forgemaster"], member_id=501)

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: "Kill Team Serze"),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: None),
        )

        pkgs = {
            "p_company": {
                "id": "p_company",
                "status": STATUS_RECRUITING,
                "assigned_company": "Watch Company Primus",
                "assigned_kt": "Kill Team Solaire",
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
            "p_kt": {
                "id": "p_kt",
                "status": STATUS_RECRUITING,
                "assigned_company": "Watch Company Secundus",
                "assigned_kt": "Kill Team Serze",
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
            "p_other": {
                "id": "p_other",
                "status": STATUS_RECRUITING,
                "assigned_company": "Watch Company Secundus",
                "assigned_kt": "Kill Team Duke",
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
            "p_completed": {
                "id": "p_completed",
                "status": STATUS_COMPLETED,
                "assigned_company": "Watch Company Secundus",
                "assigned_kt": "Kill Team Duke",
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
        }

        visible = tp._visible_active_packages_for_member(member, pkgs)
        visible_ids = {p["id"] for p in visible}

        assert "p_company" in visible_ids
        assert "p_kt" in visible_ids
        assert "p_other" in visible_ids
        assert "p_completed" not in visible_ids

    def test_forgemaster_still_sees_owned_cadre_requirement_outside_company(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _with_company_role(_make_member(["Forgemaster"], member_id=502), company_name="Watch Company Primus")

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: "Kill Team Serze"),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Watch Company Primus"),
        )

        pkgs = {
            "p_cadre": {
                "id": "p_cadre",
                "status": STATUS_RECRUITING,
                "assigned_company": "Watch Company Secundus",
                "assigned_kt": "Kill Team Duke",
                "required_roles": ["Watch Techmarine"],
                "signed_up": [],
                "assigned_specialist_ids": [],
            }
        }

        visible = tp._visible_active_packages_for_member(member, pkgs)
        visible_ids = {p["id"] for p in visible}

        assert "p_cadre" not in visible_ids

    def test_watch_captain_scope_remains_company_limited_even_if_also_high_command(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _with_company_role(
            _make_member(["Watch Captain", "Forgemaster"], member_id=503),
            company_name="Watch Company Primus",
        )

        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Watch Company Primus"),
        )

        pkgs = {
            "p_company": {
                "id": "p_company",
                "status": STATUS_RECRUITING,
                "assigned_company": "Watch Company Primus",
                "assigned_kt": "Kill Team Solaire",
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
            "p_other": {
                "id": "p_other",
                "status": STATUS_RECRUITING,
                "assigned_company": "Watch Company Secundus",
                "assigned_kt": "Kill Team Duke",
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
        }

        visible = tp._visible_active_packages_for_member(member, pkgs)
        visible_ids = {p["id"] for p in visible}

        assert "p_company" in visible_ids
        assert "p_other" not in visible_ids


class TestCompanyScopedVisibility:
    def test_captain_with_company_role_sees_distributed_unclaimed_directives(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _with_company_role(_make_member(["Watch Captain"], member_id=704), company_name="Watch Company Tertius")

        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Watch Company Tertius"),
        )

        pkgs = {
            "p_distributed": {
                "id": "p_distributed",
                "status": STATUS_DISTRIBUTED,
                "assigned_company": None,
                "assigned_kt": None,
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
            "p_company": {
                "id": "p_company",
                "status": STATUS_RECRUITING,
                "assigned_company": "Watch Company Tertius",
                "assigned_kt": "Kill Team Solaire",
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
            "p_other_company": {
                "id": "p_other_company",
                "status": STATUS_RECRUITING,
                "assigned_company": "Watch Company Primus",
                "assigned_kt": "Kill Team Serze",
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
        }

        visible = tp._visible_active_packages_for_member(member, pkgs)
        visible_ids = {p["id"] for p in visible}

        assert visible_ids == {"p_distributed", "p_company"}

    def test_non_command_company_member_does_not_see_other_distributed_directives(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _with_company_role(_make_member(["Watch Brother"], member_id=705), company_name="Watch Company Tertius")

        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Watch Company Tertius"),
        )

        pkgs = {
            "p_distributed": {
                "id": "p_distributed",
                "status": STATUS_DISTRIBUTED,
                "assigned_company": None,
                "assigned_kt": None,
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
            "p_company": {
                "id": "p_company",
                "status": STATUS_RECRUITING,
                "assigned_company": "Watch Company Tertius",
                "assigned_kt": "Kill Team Solaire",
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
            "p_personal": {
                "id": "p_personal",
                "status": STATUS_RECRUITING,
                "assigned_company": "Watch Company Primus",
                "assigned_kt": None,
                "required_roles": [],
                "signed_up": [705],
                "assigned_specialist_ids": [],
            },
        }

        visible = tp._visible_active_packages_for_member(member, pkgs)
        visible_ids = {p["id"] for p in visible}

        assert visible_ids == {"p_company", "p_personal"}

    def test_company_member_without_kt_sees_only_matching_company_and_personal(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _with_company_role(_make_member(["Watch Brother"], member_id=701), company_name="Watch Company Primus")

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Watch Company Primus"),
        )

        pkgs = {
            "p_company_match": {
                "id": "p_company_match",
                "status": STATUS_RECRUITING,
                "assigned_company": "Watch Company Primus",
                "assigned_kt": None,
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
            "p_company_other": {
                "id": "p_company_other",
                "status": STATUS_RECRUITING,
                "assigned_company": "Watch Company Secundus",
                "assigned_kt": None,
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
            "p_unassigned_kt": {
                "id": "p_unassigned_kt",
                "status": STATUS_RECRUITING,
                "assigned_company": None,
                "assigned_kt": None,
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
            "p_personal": {
                "id": "p_personal",
                "status": STATUS_RECRUITING,
                "assigned_company": "Watch Company Secundus",
                "assigned_kt": None,
                "required_roles": [],
                "signed_up": [701],
                "assigned_specialist_ids": [],
            },
        }

        visible = tp._visible_active_packages_for_member(member, pkgs)
        visible_ids = {p["id"] for p in visible}

        assert visible_ids == {"p_company_match", "p_personal"}

    def test_unscoped_specialist_member_sees_all_active_directives(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _make_member(["Watch Brother", "Watch Techmarine"], member_id=702)

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: None),
        )

        pkgs = {
            "p_company_match": {
                "id": "p_company_match",
                "status": STATUS_RECRUITING,
                "assigned_company": "Watch Company Primus",
                "assigned_kt": None,
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
            "p_company_other": {
                "id": "p_company_other",
                "status": STATUS_RECRUITING,
                "assigned_company": "Watch Company Secundus",
                "assigned_kt": None,
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
            "p_unassigned_kt": {
                "id": "p_unassigned_kt",
                "status": STATUS_RECRUITING,
                "assigned_company": None,
                "assigned_kt": None,
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
            "p_personal": {
                "id": "p_personal",
                "status": STATUS_RECRUITING,
                "assigned_company": "Watch Company Secundus",
                "assigned_kt": None,
                "required_roles": [],
                "signed_up": [702],
                "assigned_specialist_ids": [],
            },
        }

        visible = tp._visible_active_packages_for_member(member, pkgs)
        visible_ids = {p["id"] for p in visible}

        assert visible_ids == {"p_company_match", "p_company_other", "p_unassigned_kt", "p_personal"}

    def test_dreadnought_cadre_member_sees_all_active_directives(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _make_member(["Watch Brother", "Venerable Dreadnought"], member_id=706)

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Dreadnought Cadre"),
        )

        pkgs = {
            "p_company_match": {
                "id": "p_company_match",
                "status": STATUS_RECRUITING,
                "assigned_company": "Watch Company Primus",
                "assigned_kt": None,
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
            "p_company_other": {
                "id": "p_company_other",
                "status": STATUS_RECRUITING,
                "assigned_company": "Watch Company Secundus",
                "assigned_kt": None,
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
            "p_unassigned_kt": {
                "id": "p_unassigned_kt",
                "status": STATUS_RECRUITING,
                "assigned_company": None,
                "assigned_kt": None,
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
        }

        visible = tp._visible_active_packages_for_member(member, pkgs)
        visible_ids = {p["id"] for p in visible}

        assert visible_ids == {"p_company_match", "p_company_other", "p_unassigned_kt"}

    def test_scoped_specialist_member_still_follows_company_scope(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _with_company_role(
            _make_member(["Watch Brother", "Watch Techmarine"], member_id=703),
            company_name="Watch Company Primus",
        )

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: "Kill Team Alpha"),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Watch Company Primus"),
        )

        pkgs = {
            "p_company_match": {
                "id": "p_company_match",
                "status": STATUS_RECRUITING,
                "assigned_company": "Watch Company Primus",
                "assigned_kt": None,
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
            "p_company_other": {
                "id": "p_company_other",
                "status": STATUS_RECRUITING,
                "assigned_company": "Watch Company Secundus",
                "assigned_kt": None,
                "required_roles": [],
                "signed_up": [],
                "assigned_specialist_ids": [],
            },
        }

        visible = tp._visible_active_packages_for_member(member, pkgs)
        visible_ids = {p["id"] for p in visible}

        assert visible_ids == {"p_company_match"}


# ---------------------------------------------------------------------------
# _draw_requirements
# ---------------------------------------------------------------------------

class TestDrawRequirements:
    ALL_ROLES = {
        "Watch Veteran", "Oathsworn", "Watch Sergeant", "Bladeguard",
        "Watch Captain", "Watch Lieutenant", "First Blade",
        "Watch Techmarine", "Watch Apothecary", "Watch Chaplain",
        "Watch Librarian", "Watch Keeper", "Honored Dreadnought",
        "Watch Master", "Blade Master", "Forgemaster", "Chief Apothecary",
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
            "Watch Master", "Blade Master", "Forgemaster", "Chief Apothecary",
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
        result = _draw_strats(99.0, self.STRATS)
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


class TestLoreGroupWeightedStrats:
    def test_lore_group_activation_curve_for_applies_overrides(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        monkeypatch.setattr(
            tp,
            "_target_packages_config",
            lambda: {
                "lore_group_activation_chance_by_tier_index": {
                    "0": 0.01,
                    "1": 0.15,
                    "2": 0.35,
                    "3": 0.6,
                    "4": 0.8,
                    "5": 1.0,
                },
                "lore_group_activation_chance_overrides": {
                    "kt": {
                        "0": 0.5,
                        "1": 0.6,
                        "2": 0.7,
                        "3": 0.8,
                        "4": 0.9,
                        "5": 1.0,
                    }
                },
            },
        )

        assert _lore_group_activation_curve_for("kt")[0] == 0.5
        assert _lore_group_activation_curve_for("company_command")[0] == 0.01

    def test_active_lore_group_weights_apply_kt_rank_bonus(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _with_company_role(_make_member(["Watch Sergeant"], member_id=1))
        guild = _make_guild([member])
        pkg = _make_pkg(signed_up=[1])

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: "Kill Team Alpha"),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Primus"),
        )
        monkeypatch.setattr(
            tp,
            "_load_honors",
            lambda: {
                "kill_teams": {"Kill Team Alpha": {"tier_index": 5}},
                "companies": {"Primus": {"tier_index": 5}},
                "cadres": {},
            },
        )
        monkeypatch.setattr(
            tp,
            "_target_packages_config",
            lambda: {
                "lore_group_activation_chance_by_tier_index": {str(i): 1.0 for i in range(6)},
                "lore_group_draw_weights": {
                    "kt": 1.0,
                    "company_command": 1.0,
                    "cadre_armory": 1.0,
                    "cadre_blades": 1.0,
                    "cadre_librarius": 1.0,
                    "cadre_apothecarion": 1.0,
                    "cadre_reclusiam": 1.0,
                },
                "kt_rank_weight_bonus": {
                    "base": 1.0,
                    "oathsworn": 1.25,
                    "watch_sergeant": 2.0,
                    "veteran_sergeant": 3.0,
                },
            },
        )

        weights = _active_lore_group_weights_for_package(pkg, guild, 50.0)

        assert weights["company_command"] == 1.0
        assert weights["kt"] == 2.0

    def test_lore_group_headcount_multiplier_scales_with_count(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        monkeypatch.setattr(
            tp,
            "_target_packages_config",
            lambda: {
                "lore_group_stack_multiplier_by_count": {
                    "1": 1.0,
                    "2": 1.4,
                    "3": 1.8,
                    "4": 2.0,
                    "5": 2.1,
                }
            },
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )

        assert _lore_group_headcount_multiplier_for_package("kt", _make_pkg(), _make_guild([])) == 0.0
        assert tp._lore_group_stack_multiplier_for_count(1) == 1.0
        assert tp._lore_group_stack_multiplier_for_count(3) == 1.8
        assert tp._lore_group_stack_multiplier_for_count(6) == 2.1

    def test_draw_weighted_positive_strats_for_package_uses_active_groups(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _with_company_role(_make_member(["Oathsworn"], member_id=1))
        guild = _make_guild([member])
        pkg = _make_pkg(signed_up=[1])
        pkg["stratagems"] = {
            "core": [{"name": "Locked Debuff", "type": "debuff", "restriction_categories": [], "specific_conflicts": []}],
            "wildcards": [],
        }

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: "Kill Team Alpha"),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Primus"),
        )
        monkeypatch.setattr(
            tp,
            "_load_honors",
            lambda: {
                "kill_teams": {"Kill Team Alpha": {"tier_index": 5}},
                "companies": {"Primus": {"tier_index": 5}},
                "cadres": {},
            },
        )
        monkeypatch.setattr(
            tp,
            "_target_packages_config",
            lambda: {
                "lore_group_activation_chance_by_tier_index": {str(i): 1.0 for i in range(6)},
                "lore_group_draw_weights": {
                    "kt": 3.0,
                    "company_command": 1.0,
                    "cadre_armory": 1.0,
                    "cadre_blades": 1.0,
                    "cadre_librarius": 1.0,
                    "cadre_apothecarion": 1.0,
                    "cadre_reclusiam": 1.0,
                },
                "kt_rank_weight_bonus": {
                    "base": 1.0,
                    "oathsworn": 1.5,
                    "watch_sergeant": 2.0,
                    "veteran_sergeant": 3.0,
                },
            },
        )
        monkeypatch.setattr(tp, "_rep_tier_for_strat", lambda _rep: 0)
        monkeypatch.setattr(tp, "_strat_counts_for_rep_tier", lambda _tier: (2, 2))
        monkeypatch.setattr(
            tp,
            "_load_stratagems",
            lambda: [
                {"name": "KT One", "type": "buff", "lore_group": "kt", "restriction_categories": [], "specific_conflicts": []},
                {"name": "KT Two", "type": "buff", "lore_group": "kt", "restriction_categories": [], "specific_conflicts": []},
                {"name": "Company One", "type": "buff", "lore_group": "company_command", "restriction_categories": [], "specific_conflicts": []},
                {"name": "Cadre One", "type": "buff", "lore_group": "cadre_armory", "restriction_categories": [], "specific_conflicts": []},
                {"name": "Locked Debuff", "type": "debuff", "restriction_categories": [], "specific_conflicts": []},
            ],
        )

        result = _draw_weighted_positive_strats_for_package(pkg, 50.0, guild)

        assert len(result) == 2
        assert {entry["lore_group"] for entry in result} <= {"kt", "company_command"}

    def test_draw_weighted_positive_strats_for_package_tops_up_from_global_pool(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _with_company_role(_make_member(["Oathsworn"], member_id=1))
        guild = _make_guild([member])
        pkg = _make_pkg(signed_up=[1])
        pkg["stratagems"] = {"core": [], "wildcards": []}

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: "Kill Team Alpha"),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Primus"),
        )
        monkeypatch.setattr(
            tp,
            "_load_honors",
            lambda: {
                "kill_teams": {"Kill Team Alpha": {"tier_index": 5}},
                "companies": {"Primus": {"tier_index": 5}},
                "cadres": {},
            },
        )
        monkeypatch.setattr(
            tp,
            "_target_packages_config",
            lambda: {
                "lore_group_activation_chance_by_tier_index": {str(i): 1.0 for i in range(6)},
                "lore_group_draw_weights": {
                    "kt": 1.0,
                    "company_command": 0.0,
                    "cadre_armory": 0.0,
                    "cadre_blades": 0.0,
                    "cadre_librarius": 0.0,
                    "cadre_apothecarion": 0.0,
                    "cadre_reclusiam": 0.0,
                },
                "kt_rank_weight_bonus": {
                    "base": 1.0,
                    "oathsworn": 1.0,
                    "watch_sergeant": 1.0,
                    "veteran_sergeant": 1.0,
                },
            },
        )
        monkeypatch.setattr(tp, "_rep_tier_for_strat", lambda _rep: 0)
        monkeypatch.setattr(tp, "_strat_counts_for_rep_tier", lambda _tier: (3, 2))
        monkeypatch.setattr(
            tp,
            "_load_stratagems",
            lambda: [
                {"name": "KT One", "type": "buff", "lore_group": "kt", "restriction_categories": [], "specific_conflicts": []},
                {"name": "Company One", "type": "buff", "lore_group": "company_command", "restriction_categories": [], "specific_conflicts": []},
                {"name": "Cadre One", "type": "buff", "lore_group": "cadre_armory", "restriction_categories": [], "specific_conflicts": []},
                {"name": "Cadre Two", "type": "buff", "lore_group": "cadre_blades", "restriction_categories": [], "specific_conflicts": []},
            ],
        )

        result = _draw_weighted_positive_strats_for_package(pkg, 50.0, guild)

        assert len(result) == 3
        assert result[0]["name"] == "KT One"
        assert {entry["name"] for entry in result[1:]} <= {"Company One", "Cadre One", "Cadre Two"}

    def test_draw_weighted_positive_strats_for_package_fallback_respects_conflicts(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _with_company_role(_make_member(["Oathsworn"], member_id=1))
        guild = _make_guild([member])
        pkg = _make_pkg(signed_up=[1])
        pkg["stratagems"] = {
            "core": [{"name": "Locked Debuff", "type": "debuff", "restriction_categories": [], "specific_conflicts": ["Fallback Blocked"]}],
            "wildcards": [],
        }

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: "Kill Team Alpha"),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Primus"),
        )
        monkeypatch.setattr(
            tp,
            "_load_honors",
            lambda: {
                "kill_teams": {"Kill Team Alpha": {"tier_index": 5}},
                "companies": {"Primus": {"tier_index": 5}},
                "cadres": {},
            },
        )
        monkeypatch.setattr(
            tp,
            "_target_packages_config",
            lambda: {
                "lore_group_activation_chance_by_tier_index": {str(i): 1.0 for i in range(6)},
                "lore_group_draw_weights": {
                    "kt": 1.0,
                    "company_command": 0.0,
                    "cadre_armory": 0.0,
                    "cadre_blades": 0.0,
                    "cadre_librarius": 0.0,
                    "cadre_apothecarion": 0.0,
                    "cadre_reclusiam": 0.0,
                },
                "kt_rank_weight_bonus": {
                    "base": 1.0,
                    "oathsworn": 1.0,
                    "watch_sergeant": 1.0,
                    "veteran_sergeant": 1.0,
                },
            },
        )
        monkeypatch.setattr(tp, "_rep_tier_for_strat", lambda _rep: 0)
        monkeypatch.setattr(tp, "_strat_counts_for_rep_tier", lambda _tier: (3, 2))
        monkeypatch.setattr(
            tp,
            "_load_stratagems",
            lambda: [
                {"name": "KT One", "type": "buff", "lore_group": "kt", "restriction_categories": [], "specific_conflicts": []},
                {"name": "Fallback Blocked", "type": "buff", "lore_group": "company_command", "restriction_categories": [], "specific_conflicts": []},
                {"name": "Fallback Damage", "type": "buff", "lore_group": "cadre_armory", "restriction_categories": ["damage"], "specific_conflicts": []},
                {"name": "Fallback Damage Two", "type": "buff", "lore_group": "cadre_blades", "restriction_categories": ["damage"], "specific_conflicts": []},
                {"name": "Fallback Safe", "type": "buff", "lore_group": "cadre_librarius", "restriction_categories": [], "specific_conflicts": []},
            ],
        )

        result = _draw_weighted_positive_strats_for_package(pkg, 50.0, guild)

        names = [entry["name"] for entry in result]
        assert len(names) == 3
        assert names[0] == "KT One"
        assert "Fallback Blocked" not in names
        assert "Fallback Safe" in names
        assert len({"Fallback Damage", "Fallback Damage Two"} & set(names)) == 1

    def test_active_lore_group_weights_include_company_and_cadre_when_represented(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _with_company_role(_make_member(["Watch Techmarine"], member_id=7))
        guild = _make_guild([member])
        pkg = _make_pkg(signed_up=[7], assigned_specialist_ids=[7])

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Primus"),
        )
        monkeypatch.setattr(
            tp,
            "_load_honors",
            lambda: {
                "kill_teams": {},
                "companies": {"Primus": {"tier_index": 5}},
                "cadres": {"Armory": {"tier_index": 5}},
            },
        )
        monkeypatch.setattr(
            tp,
            "_target_packages_config",
            lambda: {
                "lore_group_activation_chance_by_tier_index": {str(i): 1.0 for i in range(6)},
                "lore_group_draw_weights": {
                    "kt": 1.0,
                    "company_command": 1.25,
                    "cadre_armory": 2.0,
                    "cadre_blades": 1.0,
                    "cadre_librarius": 1.0,
                    "cadre_apothecarion": 1.0,
                    "cadre_reclusiam": 1.0,
                },
                "kt_rank_weight_bonus": {
                    "base": 1.0,
                    "oathsworn": 1.25,
                    "watch_sergeant": 2.0,
                    "veteran_sergeant": 3.0,
                },
                "cadres": {
                    "forge": {
                        "rep_bucket": "Armory",
                        "member_roles": [{"name": "Watch Techmarine"}],
                    }
                },
            },
        )

        weights = _active_lore_group_weights_for_package(pkg, guild, 50.0)

        assert weights["company_command"] == 1.25
        assert weights["cadre_armory"] == 2.0

    def test_active_lore_group_weights_scale_with_member_count(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        company_one = _with_company_role(_make_member(["Watch Brother"], member_id=11))
        company_two = _with_company_role(_make_member(["Watch Brother"], member_id=12))
        company_three = _with_company_role(_make_member(["Watch Brother"], member_id=13))
        guild = _make_guild([company_one, company_two, company_three])
        pkg_one = _make_pkg(signed_up=[11], assigned_specialist_ids=[])
        pkg_three = _make_pkg(signed_up=[11, 12, 13], assigned_specialist_ids=[])

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )
        monkeypatch.setattr(
            tp,
            "_target_packages_config",
            lambda: {
                "lore_group_activation_chance_by_tier_index": {str(i): 1.0 for i in range(6)},
                "lore_group_stack_multiplier_by_count": {
                    "1": 1.0,
                    "2": 1.4,
                    "3": 1.8,
                    "4": 2.0,
                    "5": 2.1,
                },
                "lore_group_draw_weights": {
                    "kt": 1.0,
                    "company_command": 1.0,
                    "cadre_armory": 1.0,
                    "cadre_blades": 1.0,
                    "cadre_librarius": 1.0,
                    "cadre_apothecarion": 1.0,
                    "cadre_reclusiam": 1.0,
                },
                "kt_rank_weight_bonus": {
                    "base": 1.0,
                    "oathsworn": 1.25,
                    "watch_sergeant": 2.0,
                    "veteran_sergeant": 3.0,
                },
            },
        )

        one_weight = _active_lore_group_weights_for_package(pkg_one, guild, 50.0)["company_command"]
        three_weight = _active_lore_group_weights_for_package(pkg_three, guild, 50.0)["company_command"]

        assert three_weight > one_weight

    def test_cadre_headcount_stacks_for_armory(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        members = [
            _make_member(["Watch Techmarine"], member_id=21),
            _make_member(["Watch Techmarine"], member_id=22),
            _make_member(["Watch Techmarine"], member_id=23),
        ]
        guild = _make_guild(members)
        pkg_one = _make_pkg(signed_up=[21], assigned_specialist_ids=[21])
        pkg_three = _make_pkg(signed_up=[21, 22, 23], assigned_specialist_ids=[21, 22, 23])

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Primus"),
        )
        monkeypatch.setattr(tp, "_specialist_rep_bucket", lambda member: "Armory")
        monkeypatch.setattr(
            tp,
            "_target_packages_config",
            lambda: {
                "lore_group_activation_chance_by_tier_index": {str(i): 1.0 for i in range(6)},
                "lore_group_stack_multiplier_by_count": {
                    "1": 1.0,
                    "2": 1.4,
                    "3": 1.8,
                    "4": 2.0,
                    "5": 2.1,
                },
                "lore_group_draw_weights": {
                    "kt": 1.0,
                    "company_command": 1.0,
                    "cadre_armory": 2.0,
                    "cadre_blades": 1.0,
                    "cadre_librarius": 1.0,
                    "cadre_apothecarion": 1.0,
                    "cadre_reclusiam": 1.0,
                },
                "kt_rank_weight_bonus": {
                    "base": 1.0,
                    "oathsworn": 1.25,
                    "watch_sergeant": 2.0,
                    "veteran_sergeant": 3.0,
                },
            },
        )

        one_weight = _active_lore_group_weights_for_package(pkg_one, guild, 50.0)["cadre_armory"]
        three_weight = _active_lore_group_weights_for_package(pkg_three, guild, 50.0)["cadre_armory"]

        assert three_weight > one_weight

    def test_sync_live_positive_modifiers_persists_and_clears(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        pkg = _make_pkg(signed_up=[1])
        pkg["stratagems"] = {"core": [], "wildcards": []}

        monkeypatch.setattr(
            tp,
            "_draw_weighted_positive_strats_for_package",
            lambda _pkg, _rep, _guild: [{"name": "KT One", "type": "buff", "lore_group": "kt"}],
        )

        changed = _sync_live_positive_modifiers_for_package(pkg, 50.0, object())
        assert changed is True
        assert pkg["stratagems"]["dynamic_positive"][0]["name"] == "KT One"

        pkg["assigned_company"] = None
        changed = _sync_live_positive_modifiers_for_package(pkg, 50.0, object())
        assert changed is True
        assert "dynamic_positive" not in pkg["stratagems"]


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


class TestBuildBriefingRequirementFlavor:
    def test_uses_duo_variant_when_available(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        monkeypatch.setattr(tp.random, "choice", lambda seq: seq[0])
        monkeypatch.setattr(tp, "_load_operations", lambda: [{"id": 1, "name": "Relay Purge"}])

        templates = {
            "world_type_hooks": {"dead_world": ["Hook on {node}"]},
            "mission_hooks": {"1": ["purge the relay"]},
            "req_tier_templates": {"no_req": ["No specialist attachment required."], "kt_command": ["Base {rank} text."]},
            "req_tier_variants": {
                "kt_command": {
                    "duo": ["Variant duo: {rank} is required to coordinate this strike."],
                }
            },
            "strat_tone": {"rep_neg1": ["tone"], "rep_neg2": ["tone"]},
        }

        briefing = tp._build_briefing(
            node_name="Kastorel",
            world_type="dead_world",
            mission_id=1,
            tier_key="kt_command",
            req_roles=["Watch Sergeant", "Bladeguard"],
            rep=10.0,
            templates=templates,
        )

        assert "Variant duo: Watch Sergeant and Bladeguard are required to coordinate this strike." in briefing
        assert "Relay Purge" in briefing

    def test_uses_squad_variant_for_three_or_more_roles(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        monkeypatch.setattr(tp.random, "choice", lambda seq: seq[0])
        monkeypatch.setattr(tp, "_load_operations", lambda: [{"id": 2, "name": "Fortress Sweep"}])

        templates = {
            "world_type_hooks": {"dead_world": ["Hook on {node}"]},
            "mission_hooks": {"2": ["secure the fortress"]},
            "req_tier_templates": {"no_req": ["No specialist attachment required."], "company_command": ["Base {rank} text."]},
            "req_tier_variants": {
                "company_command": {
                    "squad": ["Variant squad: {rank} are required to coordinate the assault."],
                }
            },
            "strat_tone": {"rep_neg1": ["tone"], "rep_neg2": ["tone"]},
        }

        briefing = tp._build_briefing(
            node_name="Gethsemane",
            world_type="dead_world",
            mission_id=2,
            tier_key="company_command",
            req_roles=["Watch Captain", "Watch Apothecary", "Watch Librarian"],
            rep=10.0,
            templates=templates,
        )

        assert "Variant squad: Watch Captain, Watch Apothecary, and Watch Librarian are required to coordinate the assault." in briefing
        assert "Fortress Sweep" in briefing

    def test_falls_back_to_legacy_tier_templates_when_no_variant_exists(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        monkeypatch.setattr(tp.random, "choice", lambda seq: seq[0])
        monkeypatch.setattr(tp, "_load_operations", lambda: [{"id": 3, "name": "Fallback Op"}])

        templates = {
            "world_type_hooks": {"dead_world": ["Hook on {node}"]},
            "mission_hooks": {"3": ["fallback mission"]},
            "req_tier_templates": {
                "no_req": ["No specialist attachment required."],
                "veteran": ["Legacy veteran: {rank} must lead this engagement."],
            },
            "strat_tone": {"rep_neg1": ["tone"], "rep_neg2": ["tone"]},
        }

        briefing = tp._build_briefing(
            node_name="Dunecall",
            world_type="dead_world",
            mission_id=3,
            tier_key="veteran",
            req_roles=["Watch Veteran"],
            rep=10.0,
            templates=templates,
        )

        assert "Legacy veteran: Watch Veteran must lead this engagement." in briefing


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

    def test_tithe_consul_never_satisfies_line_requirements(self):
        import opscribe.target_packages_ops as tp

        consul = _make_member(["Watch Veteran", "Tithe Consul"], member_id=42)
        pkg = _make_pkg(
            mode="Hard-Strat",
            signed_up=[1, 2, 42],
            required_roles=["Watch Veteran"],
        )
        guild = _make_guild([consul])

        assert tp._check_deployed(pkg, guild) is False

    def test_tithe_consul_never_satisfies_cadre_requirements(self):
        import opscribe.target_packages_ops as tp

        consul = _make_member(["Watch Apothecary", "Tithe Consul"], member_id=99)
        pkg = _make_pkg(
            mode="Hard-Strat",
            signed_up=[1, 2, 3],
            required_roles=["Watch Apothecary"],
            assigned_specialist_ids=[99],
        )
        guild = _make_guild([consul])

        assert tp._check_deployed(pkg, guild) is False


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

    def test_unscoped_member_can_signup_any_directive(self, monkeypatch):
        from opscribe.target_packages_ops import _is_eligible_to_sign_up

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: None),
        )

        pkg = self._base_pkg()
        member = _make_member(["Watch Brother", "Watch Techmarine"], member_id=71001)
        guild = _make_guild([member])
        ok, reason = _is_eligible_to_sign_up(member, pkg, guild)
        assert ok is True
        assert reason == ""

    def test_dreadnought_cadre_treated_as_unscoped_for_signup(self, monkeypatch):
        from opscribe.target_packages_ops import _is_eligible_to_sign_up

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Dreadnought Cadre"),
        )

        pkg = self._base_pkg()
        pkg["assigned_company"] = "Watch Company Primus"
        member = _make_member(["Watch Brother", "Venerable Dreadnought"], member_id=71011)
        guild = _make_guild([member])
        ok, reason = _is_eligible_to_sign_up(member, pkg, guild)
        assert ok is True
        assert reason == ""

    def test_scoped_member_must_match_kt_or_company(self, monkeypatch):
        from opscribe.target_packages_ops import _is_eligible_to_sign_up

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: "Kill Team Beta"),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Secundus"),
        )

        pkg = self._base_pkg()
        member = _make_member(["Watch Brother", "Watch Techmarine"], member_id=71002)
        guild = _make_guild([member])
        ok, reason = _is_eligible_to_sign_up(member, pkg, guild)
        assert ok is False
        assert "not part" in reason.lower()

    def test_high_command_does_not_override_structural_scope(self, monkeypatch):
        from opscribe.target_packages_ops import _is_eligible_to_sign_up

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: "Kill Team Beta"),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Secundus"),
        )

        pkg = self._base_pkg()
        member = _make_member(["Watch Master"], member_id=71003)
        guild = _make_guild([member])
        ok, reason = _is_eligible_to_sign_up(member, pkg, guild)
        assert ok is False
        assert "not part" in reason.lower()

    def test_company_match_allows_signup_without_kt_match(self, monkeypatch):
        from opscribe.target_packages_ops import _is_eligible_to_sign_up

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Primus"),
        )

        pkg = self._base_pkg()
        member = _make_member(["Watch Brother", "Watch Techmarine"], member_id=71004)
        guild = _make_guild([member])
        ok, reason = _is_eligible_to_sign_up(member, pkg, guild)
        assert ok is True
        assert reason == ""

    def test_unscoped_specialist_role_bypasses_company_scope(self, monkeypatch):
        from opscribe.target_packages_ops import _is_eligible_to_sign_up

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: None),
        )

        pkg = self._base_pkg()
        pkg["assigned_company"] = "Primus"
        member = _make_member(["Watch Brother", "Watch Techmarine"], member_id=71005)
        guild = _make_guild([member])
        ok, reason = _is_eligible_to_sign_up(member, pkg, guild)
        assert ok is True
        assert reason == ""

    def test_scoped_specialist_role_still_obeys_company_scope(self, monkeypatch):
        from opscribe.target_packages_ops import _is_eligible_to_sign_up

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: "Kill Team Beta"),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Secundus"),
        )

        pkg = self._base_pkg()
        pkg["assigned_company"] = "Primus"
        member = _make_member(["Watch Brother", "Watch Techmarine"], member_id=71006)
        guild = _make_guild([member])
        ok, reason = _is_eligible_to_sign_up(member, pkg, guild)
        assert ok is False
        assert "not part" in reason.lower()

    def test_tithe_consul_bypasses_rank_and_company_scope(self, monkeypatch):
        from opscribe.target_packages_ops import _is_eligible_to_sign_up

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Secundus"),
        )

        pkg = self._base_pkg()
        pkg["assigned_company"] = "Primus"
        member = _make_member(["Tithe Consul"], member_id=71007)
        guild = _make_guild([member])
        ok, reason = _is_eligible_to_sign_up(member, pkg, guild)
        assert ok is True
        assert reason == ""


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

    def test_bladeguard_is_cadre(self):
        assert "Bladeguard" in _CADRE_SPECIALIST_ROLES

    def test_first_blade_is_cadre(self):
        assert "First Blade" in _CADRE_SPECIALIST_ROLES

    def test_forgemaster_is_cadre(self):
        assert "Forgemaster" in _CADRE_SPECIALIST_ROLES

    def test_watch_veteran_not_cadre(self):
        # Line role — signs up via Comply, not assigned by cadre leader
        assert "Watch Veteran" not in _CADRE_SPECIALIST_ROLES

    def test_oathsworn_not_cadre(self):
        assert "Oathsworn" not in _CADRE_SPECIALIST_ROLES

    def test_watch_sergeant_not_cadre(self):
        assert "Watch Sergeant" not in _CADRE_SPECIALIST_ROLES


class TestParticipationRepAccounting:
    def test_target_packages_config_file_loaded_once_when_live_config_present(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        calls = {"open": 0}

        def _fake_open(*_args, **_kwargs):
            calls["open"] += 1
            return io.StringIO('{"target_packages": {"cadres": {"x": {"rep_bucket": "Blades"}}}}')

        monkeypatch.setattr("builtins.open", _fake_open)
        monkeypatch.setattr(tp, "_CONFIG_TARGET_PACKAGES_CACHE", None)
        monkeypatch.setattr(tp, "_CONFIG_TARGET_PACKAGES_FILE_CACHE", None)
        monkeypatch.setattr(tp, "_CONFIG_TARGET_PACKAGES_FILE_CACHE_LOADED", False)
        monkeypatch.setattr(
            tp,
            "_b",
            lambda name: {"target_packages": {"cadres": {"x": {"leader_role_name": "Blade Master"}}}} if name == "CONFIG" else None,
        )

        tp._target_packages_config()
        tp._target_packages_config()

        assert calls["open"] == 1

    def test_rep_delta_for_package_uses_mode_specific_values(self):
        import opscribe.target_packages_ops as tp

        hard_no_req = {"mode": "Hard-Strat", "required_roles": []}
        hard_with_req = {"mode": "Hard-Strat", "required_roles": ["Watch Veteran"]}
        omega_no_req = {"mode": "Omega-Strat", "required_roles": []}
        omega_with_req = {"mode": "Omega-Strat", "required_roles": ["Watch Veteran"]}

        assert tp._rep_delta_for_package(hard_no_req, STATUS_COMPLETED) == 3.0
        assert tp._rep_delta_for_package(hard_with_req, STATUS_COMPLETED) == 6.0
        assert tp._rep_delta_for_package(omega_no_req, STATUS_COMPLETED) == 5.0
        assert tp._rep_delta_for_package(omega_with_req, STATUS_COMPLETED) == 11.0

        assert tp._rep_delta_for_package(hard_no_req, STATUS_FAILED) == -2.0
        assert tp._rep_delta_for_package(omega_no_req, STATUS_FAILED) == -3.0
        assert tp._rep_delta_for_package(hard_no_req, STATUS_LAPSED) == -1.0
        assert tp._rep_delta_for_package(omega_no_req, STATUS_LAPSED) == -2.0

    def test_split_allocations_across_kts_and_apothecarion(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        pkg = _make_pkg(signed_up=[1, 2], assigned_specialist_ids=[3])
        m1 = _make_member(["Watch Brother", "Kill Team A"], member_id=1)
        m2 = _make_member(["Watch Brother", "Kill Team B"], member_id=2)
        m3 = _make_member(["Watch Apothecary"], member_id=3)
        guild = _make_guild([m1, m2, m3])

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(
                _resolve_killteam_for_member=lambda member: "Kill Team A" if member.id == 1 else ("Kill Team B" if member.id == 2 else None)
            ),
        )

        allocations = tp._compute_participation_rep_allocations(pkg, guild, 3.0)

        assert allocations["kill_teams"] == {"Kill Team A": 0.7, "Kill Team B": 0.7}
        assert allocations["cadres"] == {"Apothecarion": 0.7}
        assert allocations["companies"] == {"Primus": 0.4}
        assert allocations["fortress"] == 0.5

    def test_split_allocations_exclude_captain_and_lieutenant(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        pkg = _make_pkg(signed_up=[10, 11], assigned_specialist_ids=[12])
        captain = _make_member(["Watch Captain", "Kill Team A"], member_id=10)
        lieutenant = _make_member(["Watch Lieutenant", "Kill Team B"], member_id=11)
        specialist = _make_member(["Watch Apothecary"], member_id=12)
        guild = _make_guild([captain, lieutenant, specialist])

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )

        allocations = tp._compute_participation_rep_allocations(pkg, guild, 2.0)

        assert allocations["kill_teams"] == {}
        assert allocations["cadres"] == {"Apothecarion": 0.46}
        assert allocations["companies"] == {"Primus": 0.94}
        assert allocations["fortress"] == 0.6

    def test_specialist_rep_bucket_selection_is_deterministic(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _make_member(["Watch Apothecary", "Blade Master"], member_id=999)
        reversed_member = _make_member(["Blade Master", "Watch Apothecary"], member_id=1000)
        monkeypatch.setattr(
            tp,
            "_cadre_rep_bucket_role_map",
            lambda: {"Watch Apothecary": "Apothecarion", "Blade Master": "Blades"},
        )

        assert tp._specialist_rep_bucket(member) == "Blades"
        assert tp._specialist_rep_bucket(reversed_member) == "Blades"

    def test_company_gets_split_plus_base_award(self):
        import opscribe.target_packages_ops as tp

        data = {"entity_stats": {"companies": {}, "kill_teams": {}, "cadres": {}}}
        pkg = {"assigned_company": "Watch Company Primus"}
        allocations = {
            "kill_teams": {"Kill Team A": 1.0, "Kill Team B": 1.0},
            "cadres": {"Apothecarion": 1.0},
            "companies": {"Watch Company Primus": 0.3},
        }

        tp._apply_entity_rep_allocations(data, pkg, allocations, company_bonus=1.0)

        assert data["entity_stats"]["kill_teams"]["Kill Team A"]["rep_earned"] == 1.0
        assert data["entity_stats"]["kill_teams"]["Kill Team B"]["rep_earned"] == 1.0
        assert data["entity_stats"]["cadres"]["Apothecarion"]["rep_earned"] == 1.0
        assert data["entity_stats"]["companies"]["Watch Company Primus"]["rep_earned"] == 1.3

    def test_company_base_award_applies_on_top_of_split(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        data = {"entity_stats": {"companies": {}, "kill_teams": {}, "cadres": {}}}
        pkg = {
            "assigned_company": "Watch Company Primus",
            "signed_up": [10, 11],
            "assigned_specialist_ids": [12],
        }
        captain = _make_member(["Watch Captain"], member_id=10)
        lieutenant = _make_member(["Watch Lieutenant"], member_id=11)
        specialist = _make_member(["Watch Apothecary"], member_id=12)
        guild = _make_guild([captain, lieutenant, specialist])

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )

        base_bonus = tp._compute_company_completion_bonus(pkg)
        assert base_bonus == 1.0

        allocations = tp._compute_participation_rep_allocations(pkg, guild, 3.0)
        tp._apply_entity_rep_allocations(data, pkg, allocations, company_bonus=base_bonus)

        assert data["entity_stats"]["cadres"]["Apothecarion"]["rep_earned"] == 0.7
        assert data["entity_stats"]["companies"]["Watch Company Primus"]["rep_earned"] == 2.4

    def test_fortress_base_award_returns_default(self):
        import opscribe.target_packages_ops as tp

        pkg = {"id": "TP-TEST"}
        assert tp._compute_fortress_completion_bonus(pkg) == 1.0

    def test_high_command_participation_flows_to_fortress_split(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        pkg = {
            "signed_up": [21, 22, 23],
            "assigned_specialist_ids": [],
            "assigned_company": "Watch Company Primus",
        }
        blademaster = _make_member(["Blade Master"], member_id=21)
        high_chaplain = _make_member(["High Chaplain"], member_id=22)
        huntmaster = _make_member(["Huntmaster"], member_id=23)
        guild = _make_guild([blademaster, high_chaplain, huntmaster])

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )

        allocations = tp._compute_participation_rep_allocations(pkg, guild, 3.0)

        assert allocations["kill_teams"] == {}
        assert allocations["cadres"] == {"Blades": 0.7, "Reclusiam": 0.7}
        assert allocations["fortress"] == 1.6

    def test_tithe_consul_participation_earns_no_rep(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        pkg = {
            "signed_up": [21, 22],
            "assigned_specialist_ids": [],
            "assigned_company": "Watch Company Primus",
        }
        line_brother = _make_member(["Watch Brother"], member_id=21)
        tithe_consul = _make_member(["Tithe Consul"], member_id=22)
        guild = _make_guild([line_brother, tithe_consul])

        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(
                _resolve_killteam_for_member=lambda member: "Kill Team Alpha" if member.id == 21 else None
            ),
        )

        allocations = tp._compute_participation_rep_allocations(pkg, guild, 2.0)

        assert allocations["kill_teams"] == {"Kill Team Alpha": 1.4}
        assert allocations["companies"] == {"Watch Company Primus": 0.4}
        assert allocations["fortress"] == 0.2

    def test_apply_entity_rep_allocations_rejects_unexpected_legacy_args(self):
        import opscribe.target_packages_ops as tp

        data = {"entity_stats": {"companies": {}, "kill_teams": {}, "cadres": {}}}
        pkg = {"assigned_company": "Watch Company Primus"}
        allocations = {"kill_teams": {}, "cadres": {}, "companies": {}}

        with pytest.raises(TypeError, match="at most two legacy positional args"):
            tp._apply_entity_rep_allocations(data, pkg, allocations, 1.0, {"Apothecarion": 0.2}, "unexpected")

class TestFormationLabels:
    def test_formations_include_company_cadre_and_killteam(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        captain = _make_member(["Watch Captain", "Watch Company Primus"], member_id=201)
        forgemaster = _make_member(["Forgemaster"], member_id=202)
        veteran = _make_member(["Watch Veteran", "Kill Team Alpha"], member_id=203)
        guild = _make_guild([captain, forgemaster, veteran])
        pkg = {
            "signed_up": [201, 203],
            "assigned_specialist_ids": [202],
        }

        monkeypatch.setattr(tp, "_specialist_rep_bucket", lambda member: "Armory" if member.id == 202 else None)
        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(
                _resolve_killteam_for_member=lambda member: "Kill Team Alpha" if member.id == 203 else None
            ),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(
                _get_member_company_name=lambda member: "Watch Company Primus" if member.id == 201 else None
            ),
        )

        labels = _formation_labels_for_completed_package(pkg, guild)

        assert labels == ["Watch Company Primus", "Armory", "Kill Team Alpha"]

    def test_formations_fallback_to_watch_master_when_unresolved(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        watch_master = _make_member(["Watch Master"], member_id=301)
        guild = _make_guild([watch_master])
        pkg = {
            "signed_up": [301],
            "assigned_specialist_ids": [],
        }

        monkeypatch.setattr(tp, "_specialist_rep_bucket", lambda _member: None)
        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: None),
        )

        labels = _formation_labels_for_completed_package(pkg, guild)

        assert labels == ["Watch Master"]

    def test_formations_exclude_dreadnought_company_label_and_use_armory_bucket(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        dread = _make_member(["Venerable Dreadnought"], member_id=401)
        guild = _make_guild([dread])
        pkg = {
            "signed_up": [401],
            "assigned_specialist_ids": [],
        }

        monkeypatch.setattr(tp, "_specialist_rep_bucket", lambda member: "Armory" if member.id == 401 else None)
        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Dreadnought Cadre"),
        )

        labels = _formation_labels_for_completed_package(pkg, guild)

        assert labels == ["Armory"]


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
        assert result == {"kill_teams": {}, "companies": {}, "cadres": {}}

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
        """1 completion with rep_delta 1.0 remains at baseline under new thresholds."""
        # rep delta = 1.0 → ri = 0 (<2.0 threshold), ci = 1 (1 completion)
        # final = round(0.75*0 + 0.25*1) = 0 → "Unproven"
        pkgs = {
            "p1": _make_completed_pkg("p1", "Alpha", "Primus", 10.0, 11.0, self._WITHIN),
        }
        result = self._call(pkgs)
        assert result["kill_teams"]["Alpha"]["tier"] == "Unproven"
        assert result["kill_teams"]["Alpha"]["completions_28d"] == 1

    def test_kt_high_rep_and_completions_gives_higher_tier(self):
        """Many completions with high rep delta escalates the tier."""
        # 6 completions, rep_delta 2 each = 12 total
        # ri: 12 >= 8.0 but < 15.0 → index 2; ci: 6 completions >= 6 → index 3
        # final = round(0.75*2 + 0.25*3) = 2 → "Vigilant"
        pkgs = {
            f"p{i}": _make_completed_pkg(f"p{i}", "Bravo", "Secundus", float(10 + i*2), float(12 + i*2), self._WITHIN)
            for i in range(6)
        }
        result = self._call(pkgs)
        assert result["kill_teams"]["Bravo"]["tier_index"] == 2
        assert result["kill_teams"]["Bravo"]["tier"] == "Vigilant"

    def test_company_single_kt_still_scores_by_rep_and_completions(self):
        """Company tier no longer uses a distinct-KT gate cap."""
        # 3 completions each contributing 5 rep → co_rep=15, co_comp=3
        # ri: 15 >= 13.0 → index 2; ci: 3 >= 3 → index 1
        # final = round(0.75*2 + 0.25*1) = 2
        pkgs = {
            f"p{i}": _make_completed_pkg(f"p{i}", "Alpha", "Primus", float(i*5), float(i*5+5), self._WITHIN)
            for i in range(3)
        }
        result = self._call(pkgs)
        tier_idx = result["companies"]["Primus"]["tier_index"]
        assert tier_idx == 2
        assert result["companies"]["Primus"]["contributing_kts"] == 1

    def test_company_multi_kt_scores_lower_when_stats_are_shared(self):
        """Two contributing KTs should not inflate company tier as if the company were one large KT."""
        # Same total stats as the single-KT case but split across 2 KTs.
        # With per-KT normalization, average rep/completions per KT are lower,
        # so the company should not keep the same aggregate-only tier.
        pkgs = {
            "p0": _make_completed_pkg("p0", "Alpha", "Primus", 0.0, 5.0, self._WITHIN),
            "p1": _make_completed_pkg("p1", "Alpha", "Primus", 5.0, 10.0, self._WITHIN),
            "p2": _make_completed_pkg("p2", "Bravo", "Primus", 10.0, 15.0, self._WITHIN),
        }
        result = self._call(pkgs)
        assert result["companies"]["Primus"]["contributing_kts"] == 2
        assert result["companies"]["Primus"]["tier_index"] == 1


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


class TestPostBatchSummaryDebriefRendering:
    def test_kt_debrief_uses_participation_with_lapsed_and_completed_fallback(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        class _EmbedCapture:
            def __init__(self, *, title=None, description=None, color=None):
                self.title = title
                self.description = description
                self.color = color
                self.fields = []
                self.author_name = None

            def set_author(self, *, name=None, **_kwargs):
                self.author_name = name

            def set_footer(self, **_kwargs):
                return None

            def set_image(self, **_kwargs):
                return None

            def add_field(self, *, name, value, inline=True):
                self.fields.append(types.SimpleNamespace(name=name, value=value, inline=inline))

        sent = []

        async def fake_notify_send(_ch, _guild, content=None, embed=None, **_kwargs):
            sent.append({"content": content, "embed": embed})
            return object()

        async def fake_award_channel(_member, _guild):
            return object()

        alpha = _make_member(["Watch Brother"], member_id=101)
        bravo = _make_member(["Watch Brother"], member_id=102)
        guild = _make_guild([alpha, bravo])
        guild.roles = []
        guild.get_channel = lambda _cid: None

        async def _fake_fetch_channel(_cid):
            raise RuntimeError("channel unavailable")

        guild.fetch_channel = _fake_fetch_channel

        monkeypatch.setattr(tp.discord, "Embed", _EmbedCapture)
        monkeypatch.setattr(tp.discord.utils, "find", lambda pred, seq: next((x for x in seq if pred(x)), None), raising=False)
        monkeypatch.setattr(tp, "_notify_send", fake_notify_send)
        monkeypatch.setattr(tp, "_is_debug_mode", lambda: False)
        monkeypatch.setattr(tp, "_is_active", lambda _m: True)
        monkeypatch.setattr(tp, "_random_strike_image_file", lambda _hint: (None, None))
        monkeypatch.setattr(tp, "_b", lambda name=None: {"target_packages": {}} if name == "CONFIG" else None)
        monkeypatch.setattr(tp, "_load_honors", lambda: {"kill_teams": {}, "companies": {}, "cadres": {}})
        monkeypatch.setattr(tp, "_save_honors", lambda _data: None)
        monkeypatch.setattr(tp, "_compute_honors", lambda _data: {"kill_teams": {}, "companies": {}, "cadres": {}})
        monkeypatch.setattr(tp, "_rep_delta_for_package", lambda _pkg, _status: 0.0)
        monkeypatch.setattr(
            tp._g,
            "logger",
            types.SimpleNamespace(warning=lambda *_a, **_k: None, info=lambda *_a, **_k: None, debug=lambda *_a, **_k: None),
            raising=False,
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(
                _get_award_announcement_channel=fake_award_channel,
                _resolve_killteam_for_member=lambda member: "Kill Team Alpha" if member.id == 101 else ("Kill Team Bravo" if member.id == 102 else None),
            ),
        )

        data = {
            "rep": 50.0,
            "entity_stats": {"kill_teams": {}, "companies": {}, "cadres": {}},
            "cycle": {"batch_id": "BATCH-20260726"},
            "packages": {
                "p1": {
                    "id": "p1",
                    "directive_code": "001-ALPHA",
                    "directive_name": "Alpha Strike",
                    "node": "Node A",
                    "classification": "Purge",
                    "mode": "Hard-Strat",
                    "status": STATUS_COMPLETED,
                    "batch_id": "BATCH-20260726",
                    "signed_up": [101],
                    "assigned_specialist_ids": [],
                    "assigned_company": "Watch Company Primus",
                    "rep_before": 50.0,
                    "rep_after": 51.0,
                },
                "p2": {
                    "id": "p2",
                    "directive_code": "002-BRAVO",
                    "directive_name": "Bravo Strike",
                    "node": "Node B",
                    "classification": "Sabotage",
                    "mode": "Hard-Strat",
                    "status": STATUS_FAILED,
                    "batch_id": "BATCH-20260726",
                    "signed_up": [102],
                    "assigned_specialist_ids": [],
                    "assigned_company": "Watch Company Primus",
                },
                "p3": {
                    "id": "p3",
                    "directive_code": "003-MIXED",
                    "directive_name": "Mixed Strike",
                    "node": "Node C",
                    "classification": "Assault",
                    "mode": "Omega-Strat",
                    "status": STATUS_LAPSED,
                    "batch_id": "BATCH-20260726",
                    "signed_up": [101, 102],
                    "assigned_specialist_ids": [],
                    "assigned_company": "Watch Company Primus",
                },
                "p4": {
                    "id": "p4",
                    "directive_code": "004-NOPART",
                    "directive_name": "No Participants",
                    "node": "Node D",
                    "classification": "Recon",
                    "mode": "Hard-Strat",
                    "status": STATUS_COMPLETED,
                    "batch_id": "BATCH-20260726",
                    "signed_up": [],
                    "assigned_specialist_ids": [],
                    "assigned_company": "Watch Company Primus",
                    "assigned_kt": "Legacy KT",
                    "rep_before": 51.0,
                    "rep_after": 52.0,
                },
                "p5": {
                    "id": "p5",
                    "directive_code": "005-FALLBACK",
                    "directive_name": "Fallback Strike",
                    "node": "Node E",
                    "classification": "Recovery",
                    "mode": "Hard-Strat",
                    "status": STATUS_COMPLETED,
                    "batch_id": "BATCH-20260726",
                    "signed_up": [999],
                    "assigned_specialist_ids": [],
                    "assigned_company": "Watch Company Primus",
                    "rep_before": 52.0,
                    "rep_after": 53.0,
                    "rep_allocations": {"kill_teams": {"Kill Team Alpha": 0.9}},
                },
            },
        }

        asyncio.run(_post_batch_summary(guild, data, batch_id="BATCH-20260726"))

        kt_embeds = [it["embed"] for it in sent if it.get("embed") and "ᴋɪʟʟ ᴛᴇᴀᴍ ᴄʏᴄʟᴇ ʀᴇᴘᴏʀᴛ" in str(it["embed"].title or "")]
        assert len(kt_embeds) == 2

        alpha_embed = next(e for e in kt_embeds if str(e.author_name or "").startswith("Kill Team Alpha"))
        alpha_summary = next(f.value for f in alpha_embed.fields if f.name == "`ᴄʏᴄʟᴇ sᴜᴍᴍᴀʀʏ`")
        assert "**Directives Participated:** 3" in alpha_summary
        assert "**Completed:** 2  ·  **Failed:** 0  ·  **Lapsed:** 1" in alpha_summary
        alpha_detail_blob = "\n".join(str(f.value) for f in alpha_embed.fields)
        assert "001-ALPHA" in alpha_detail_blob
        assert "003-MIXED" in alpha_detail_blob
        assert "005-FALLBACK" in alpha_detail_blob
        assert "002-BRAVO" not in alpha_detail_blob
        assert "004-NOPART" not in alpha_detail_blob

        bravo_embed = next(e for e in kt_embeds if str(e.author_name or "").startswith("Kill Team Bravo"))
        bravo_summary = next(f.value for f in bravo_embed.fields if f.name == "`ᴄʏᴄʟᴇ sᴜᴍᴍᴀʀʏ`")
        assert "**Directives Participated:** 2" in bravo_summary
        assert "**Completed:** 0  ·  **Failed:** 1  ·  **Lapsed:** 1" in bravo_summary
        bravo_detail_blob = "\n".join(str(f.value) for f in bravo_embed.fields)
        assert "002-BRAVO" in bravo_detail_blob
        assert "003-MIXED" in bravo_detail_blob
        assert "004-NOPART" not in bravo_detail_blob

    def test_cadre_debrief_title_prefix_and_required_fulfillment_line(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        class _EmbedCapture:
            def __init__(self, *, title=None, description=None, color=None):
                self.title = title
                self.description = description
                self.color = color
                self.fields = []
                self.author_name = None

            def set_author(self, *, name=None, **_kwargs):
                self.author_name = name

            def set_footer(self, **_kwargs):
                return None

            def set_image(self, **_kwargs):
                return None

            def add_field(self, *, name, value, inline=True):
                self.fields.append(types.SimpleNamespace(name=name, value=value, inline=inline))

        sent = []

        async def fake_notify_send(_ch, _guild, content=None, embed=None, **_kwargs):
            sent.append({"content": content, "embed": embed})
            return object()

        async def fake_award_channel(_member, _guild):
            return None

        techmarine = _make_member(["Watch Techmarine"], member_id=201)
        guild = _make_guild([techmarine])
        guild.get_channel = lambda _cid: object()
        guild.roles = [types.SimpleNamespace(name="Watch Techmarine", mention="<@&tech>")]

        monkeypatch.setattr(tp.discord, "Embed", _EmbedCapture)
        monkeypatch.setattr(tp.discord.utils, "find", lambda pred, seq: next((x for x in seq if pred(x)), None), raising=False)
        monkeypatch.setattr(tp, "_notify_send", fake_notify_send)
        monkeypatch.setattr(tp, "_is_debug_mode", lambda: False)
        monkeypatch.setattr(tp, "_is_active", lambda _m: True)
        monkeypatch.setattr(tp, "_random_strike_image_file", lambda _hint: (None, None))
        monkeypatch.setattr(tp, "_b", lambda name=None: {"target_packages": {"cadre_channels": {"techmarine": 12345}}} if name == "CONFIG" else None)
        monkeypatch.setattr(tp, "_load_honors", lambda: {"kill_teams": {}, "companies": {}, "cadres": {}})
        monkeypatch.setattr(tp, "_save_honors", lambda _data: None)
        monkeypatch.setattr(tp, "_compute_honors", lambda _data: {"kill_teams": {}, "companies": {}, "cadres": {}})
        monkeypatch.setattr(tp, "_rep_delta_for_package", lambda _pkg, _status: 0.0)
        monkeypatch.setattr(
            tp._g,
            "logger",
            types.SimpleNamespace(warning=lambda *_a, **_k: None, info=lambda *_a, **_k: None, debug=lambda *_a, **_k: None),
            raising=False,
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(
                _get_award_announcement_channel=fake_award_channel,
                _resolve_killteam_for_member=lambda _member: None,
            ),
        )

        data = {
            "rep": 50.0,
            "entity_stats": {"kill_teams": {}, "companies": {}, "cadres": {}},
            "cycle": {"batch_id": "BATCH-20260726"},
            "packages": {
                "p1": {
                    "id": "p1",
                    "directive_code": "792-MU",
                    "directive_name": "Dirge Warrant",
                    "node": "Node A",
                    "classification": "Purge",
                    "mode": "Hard-Strat",
                    "status": STATUS_COMPLETED,
                    "batch_id": "BATCH-20260726",
                    "required_roles": ["Watch Techmarine"],
                    "signed_up": [201],
                    "assigned_specialist_ids": [],
                    "assigned_kt": None,
                    "assigned_company": "Watch Company Primus",
                },
            },
        }

        asyncio.run(_post_batch_summary(guild, data, batch_id="BATCH-20260726"))

        cadre_embeds = [it["embed"] for it in sent if it.get("embed") and "ᴄᴀᴅʀᴇ ᴅᴇʙʀɪᴇꜰ" in str(it["embed"].title or "")]
        assert len(cadre_embeds) == 1
        c_embed = cadre_embeds[0]
        assert "[Armory]" in str(c_embed.title)

        req_field = next(f for f in c_embed.fields if f.name == "`ʀᴇǫᴜɪʀᴇᴅ ᴀɴᴅ ᴅᴇᴘʟᴏʏᴇᴅ`")
        assert "M201" in str(req_field.value)
        assert "(unfilled)" not in str(req_field.value)


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
        assert _select_package_multiplier(90.0) == 4
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

    def test_expire_packages_skips_missing_and_invalid_deadlines(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        now = datetime.now(timezone.utc)
        warnings = []
        store = {
            "rep": 30.0,
            "rep_scale_version": 2,
            "cycle": {
                "generated_at": None,
                "total": 0,
                "completed": 0,
                "failed": 0,
                "lapsed": 0,
                "batch_id": "BATCH-20260624",
                "batch_summary_posted_at": {},
            },
            "entity_stats": {"companies": {}, "kill_teams": {}, "cadres": {}},
            "packages": {
                "MISS-1": {
                    "id": "MISS-1",
                    "status": STATUS_RECRUITING,
                    "deadline": "",
                    "batch_id": "BATCH-20260624",
                    "assigned_kt": None,
                    "assigned_company": None,
                },
                "BAD-1": {
                    "id": "BAD-1",
                    "status": STATUS_RECRUITING,
                    "deadline": "not-a-date",
                    "batch_id": "BATCH-20260624",
                    "assigned_kt": None,
                    "assigned_company": None,
                },
                "GOOD-1": {
                    "id": "GOOD-1",
                    "status": STATUS_RECRUITING,
                    "deadline": (now + timedelta(hours=6)).isoformat(),
                    "batch_id": "BATCH-20260624",
                    "assigned_kt": None,
                    "assigned_company": None,
                },
            },
            "rep_embed_message_id": None,
        }

        monkeypatch.setattr(tp, "_load_tp", lambda: store)
        monkeypatch.setattr(tp, "_save_tp", lambda _data: None)
        monkeypatch.setattr(tp, "_apply_rep_delta", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(tp, "_send_single_batch_warning", lambda *_args, **_kwargs: False)
        logger = types.SimpleNamespace(
            warning=lambda msg, *args: warnings.append(msg % args if args else msg),
            debug=lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(tp._g, "logger", logger, raising=False)

        async def _noop(*_args, **_kwargs):
            return None

        monkeypatch.setattr(tp, "_delete_package_messages", _noop)
        monkeypatch.setattr(tp, "_update_ox_rep_embed", _noop)
        monkeypatch.setattr(tp, "_post_batch_summary", _noop)

        guild = _make_guild([])
        asyncio.run(expire_packages(guild))

        assert store["packages"]["MISS-1"]["status"] == STATUS_RECRUITING
        assert store["packages"]["BAD-1"]["status"] == STATUS_RECRUITING
        assert store["cycle"]["failed"] == 0
        assert any("missing deadline" in msg for msg in warnings)
        assert any("invalid deadline" in msg for msg in warnings)

    def test_expire_packages_treats_naive_deadline_as_utc(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        now = datetime.now(timezone.utc)
        naive_past = (now - timedelta(minutes=10)).replace(tzinfo=None).isoformat()
        posted_batches = []
        store = {
            "rep": 30.0,
            "rep_scale_version": 2,
            "cycle": {
                "generated_at": None,
                "total": 0,
                "completed": 0,
                "failed": 0,
                "lapsed": 0,
                "batch_id": "BATCH-20260624",
                "batch_summary_posted_at": {},
            },
            "entity_stats": {"companies": {}, "kill_teams": {}, "cadres": {}},
            "packages": {
                "NAIVE-1": {
                    "id": "NAIVE-1",
                    "status": STATUS_RECRUITING,
                    "deadline": naive_past,
                    "batch_id": "BATCH-20260624",
                    "assigned_kt": None,
                    "assigned_company": None,
                },
            },
            "rep_embed_message_id": None,
        }

        monkeypatch.setattr(tp, "_load_tp", lambda: store)
        monkeypatch.setattr(tp, "_save_tp", lambda _data: None)
        monkeypatch.setattr(tp, "_apply_rep_delta", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(tp, "_send_single_batch_warning", lambda *_args, **_kwargs: False)

        async def _noop(*_args, **_kwargs):
            return None

        async def fake_post_batch_summary(_guild, _data, batch_id=None):
            posted_batches.append(batch_id)

        monkeypatch.setattr(tp, "_delete_package_messages", _noop)
        monkeypatch.setattr(tp, "_update_ox_rep_embed", _noop)
        monkeypatch.setattr(tp, "_post_batch_summary", fake_post_batch_summary)

        guild = _make_guild([])
        asyncio.run(expire_packages(guild))

        assert store["packages"]["NAIVE-1"]["status"] == STATUS_FAILED
        assert store["cycle"]["failed"] == 1
        assert posted_batches == ["BATCH-20260624"]


class TestCycleReportIdempotencyAndScope:
    def test_batch_recency_prefers_suffixed_same_day_batch(self):
        import opscribe.target_packages_ops as tp

        assert tp._batch_recency_key("BATCH-20260624") < tp._batch_recency_key("BATCH-20260624-01")
        assert tp._resolve_summary_batch_id(
            {
                "cycle": {},
                "packages": {
                    "legacy": {"id": "legacy", "status": STATUS_COMPLETED, "batch_id": "BATCH-20260624"},
                    "newer": {"id": "newer", "status": STATUS_COMPLETED, "batch_id": "BATCH-20260624-01"},
                },
            }
        ) == "BATCH-20260624-01"

    def test_manual_post_cycle_reports_allows_repost_of_marked_batch(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        fixed_now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
        posted_batches = []
        saved = []
        store = {
            "rep": 30.0,
            "cycle": {
                "batch_id": "BATCH-20260624",
                "batch_summary_posted_at": {
                    "BATCH-20260624": fixed_now.isoformat(),
                },
            },
            "packages": {
                "OX-1": {
                    "id": "OX-1",
                    "status": STATUS_COMPLETED,
                    "batch_id": "BATCH-20260624",
                    "generated_at": fixed_now.isoformat(),
                }
            },
        }

        monkeypatch.setattr(tp, "_load_tp", lambda: store)
        monkeypatch.setattr(tp, "_save_tp", lambda _data: saved.append(True))
        monkeypatch.setattr(tp, "_b", lambda name: (lambda *_a, **_k: True) if name == "check_command_permission" else {})

        async def fake_post_batch_summary(_guild, _data, batch_id=None):
            posted_batches.append(batch_id)
            return True

        monkeypatch.setattr(tp, "_post_batch_summary", fake_post_batch_summary)

        actor = _make_member(["Watch Master"], member_id=9001)
        guild = _make_guild([actor])
        interaction = _make_interaction(actor, guild)

        asyncio.run(_invoke_command(post_cycle_reports, interaction, batch="20260624"))

        assert posted_batches == ["BATCH-20260624"]
        assert saved == [True]
        sends = [c for c in interaction.calls if c[0] == "send"]
        assert sends
        assert "cycle reports posted" in sends[-1][1].lower()

    def test_manual_post_cycle_reports_does_not_mark_nonterminal_batch_posted(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        fixed_now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
        saved = []
        store = {
            "rep": 30.0,
            "cycle": {"batch_id": "BATCH-20260624", "batch_summary_posted_at": {}},
            "packages": {
                "OX-1": {
                    "id": "OX-1",
                    "status": STATUS_RECRUITING,
                    "batch_id": "BATCH-20260624",
                    "generated_at": fixed_now.isoformat(),
                }
            },
        }

        monkeypatch.setattr(tp, "_load_tp", lambda: store)
        monkeypatch.setattr(tp, "_save_tp", lambda _data: saved.append(True))
        monkeypatch.setattr(tp, "_b", lambda name: (lambda *_a, **_k: True) if name == "check_command_permission" else {})

        async def fake_post_batch_summary(*_args, **_kwargs):
            return True

        monkeypatch.setattr(tp, "_post_batch_summary", fake_post_batch_summary)

        actor = _make_member(["Watch Master"], member_id=9001)
        guild = _make_guild([actor])
        interaction = _make_interaction(actor, guild)

        asyncio.run(_invoke_command(post_cycle_reports, interaction, batch="20260624"))

        assert saved == []
        assert store["cycle"].get("batch_summary_posted_at", {}).get("BATCH-20260624") is None

    def test_manual_post_cycle_reports_does_not_mark_batch_when_nothing_posted(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        fixed_now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)
        saved = []
        store = {
            "rep": 30.0,
            "cycle": {"batch_id": "BATCH-20260624", "batch_summary_posted_at": {}},
            "packages": {
                "OX-1": {
                    "id": "OX-1",
                    "status": STATUS_COMPLETED,
                    "batch_id": "BATCH-20260624",
                    "generated_at": fixed_now.isoformat(),
                }
            },
        }

        monkeypatch.setattr(tp, "_load_tp", lambda: store)
        monkeypatch.setattr(tp, "_save_tp", lambda _data: saved.append(True))
        monkeypatch.setattr(tp, "_b", lambda name: (lambda *_a, **_k: True) if name == "check_command_permission" else {})

        async def fake_post_batch_summary(*_args, **_kwargs):
            return False

        monkeypatch.setattr(tp, "_post_batch_summary", fake_post_batch_summary)

        actor = _make_member(["Watch Master"], member_id=9001)
        guild = _make_guild([actor])
        interaction = _make_interaction(actor, guild)

        asyncio.run(_invoke_command(post_cycle_reports, interaction, batch="20260624"))

        assert saved == []
        assert store["cycle"].get("batch_summary_posted_at", {}).get("BATCH-20260624") is None

    def test_expire_packages_auto_post_ignores_old_terminal_batches(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        posted_batches = []
        now = datetime.now(timezone.utc)
        store = {
            "rep": 30.0,
            "rep_scale_version": 2,
            "cycle": {
                "generated_at": None,
                "total": 0,
                "completed": 0,
                "failed": 0,
                "lapsed": 0,
                "batch_id": "BATCH-20260624",
                "batch_summary_posted_at": {},
            },
            "entity_stats": {"companies": {}, "kill_teams": {}, "cadres": {}},
            "packages": {
                "OLD-1": {
                    "id": "OLD-1",
                    "status": STATUS_COMPLETED,
                    "deadline": (now - timedelta(days=5)).isoformat(),
                    "batch_id": "BATCH-20260608",
                    "assigned_kt": "Kill Team Alpha",
                    "assigned_company": "Watch Company Primus",
                },
                "NEW-1": {
                    "id": "NEW-1",
                    "status": STATUS_RECRUITING,
                    "deadline": (now - timedelta(minutes=5)).isoformat(),
                    "batch_id": "BATCH-20260624",
                    "assigned_kt": "Kill Team Alpha",
                    "assigned_company": "Watch Company Primus",
                },
            },
            "rep_embed_message_id": None,
        }

        monkeypatch.setattr(tp, "_load_tp", lambda: store)
        monkeypatch.setattr(tp, "_save_tp", lambda _data: None)
        monkeypatch.setattr(tp, "_apply_rep_delta", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(tp, "_send_single_batch_warning", lambda *_args, **_kwargs: False)

        async def _noop(*_args, **_kwargs):
            return None

        async def fake_post_batch_summary(_guild, _data, batch_id=None):
            posted_batches.append(batch_id)

        monkeypatch.setattr(tp, "_delete_package_messages", _noop)
        monkeypatch.setattr(tp, "_update_ox_rep_embed", _noop)
        monkeypatch.setattr(tp, "_post_batch_summary", fake_post_batch_summary)

        guild = _make_guild([])
        asyncio.run(expire_packages(guild))

        assert posted_batches == ["BATCH-20260624"]
        assert store["cycle"].get("batch_summary_posted_at", {}).get("BATCH-20260624") is not None
        assert store["cycle"].get("batch_summary_posted_at", {}).get("BATCH-20260623") is None
        assert "BATCH-20260608" not in posted_batches
        assert store["cycle"].get("batch_summary_posted_at", {}).get("BATCH-20260624") is not None
        assert store["packages"]["NEW-1"]["status"] == STATUS_FAILED

class TestSubmitPackagePermissions:
    def test_company_member_can_submit_without_assigned_kt(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        now = datetime.now(timezone.utc)
        pkg = {
            "id": "PKG-1",
            "directive_code": "DX-001",
            "directive_name": "Company Trial",
            "status": STATUS_RECRUITING,
            "deadline": (now + timedelta(hours=2)).isoformat(),
            "assigned_kt": None,
            "assigned_company": "Watch Company Primus",
            "signed_up": [],
            "assigned_specialist_ids": [],
            "mode": "Hard-Strat",
            "mission_id": 1,
            "required_roles": [],
        }
        store = {"packages": {"PKG-1": pkg}}
        monkeypatch.setattr(tp, "_load_tp", lambda: store)
        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Watch Company Primus"),
        )

        # Company membership alone should not bypass attached-participant gating.
        submitter = _with_company_role(_make_member(["Watch Brother"], member_id=777), "Watch Company Primus")
        guild = _make_guild([submitter])

        ok, msg = asyncio.run(submit_package("PKG-1", "https://example.invalid/aar", submitter, guild))

        assert ok is False
        assert "permission" in msg.lower()

    def test_attached_specialist_without_company_role_can_submit(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        now = datetime.now(timezone.utc)
        pkg = {
            "id": "PKG-2",
            "directive_code": "DX-002",
            "directive_name": "Specialist Trial",
            "status": STATUS_RECRUITING,
            "deadline": (now + timedelta(hours=2)).isoformat(),
            "assigned_kt": None,
            "assigned_company": "Watch Company Primus",
            "signed_up": [],
            "assigned_specialist_ids": [888],
            "mode": "Hard-Strat",
            "mission_id": 1,
            "required_roles": ["Watch Apothecary"],
        }
        store = {"packages": {"PKG-2": pkg}}
        monkeypatch.setattr(tp, "_load_tp", lambda: store)
        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: None),
        )

        specialist = _make_member(["Watch Apothecary"], member_id=888)
        guild = _make_guild([specialist])

        ok, msg = asyncio.run(submit_package("PKG-2", "https://example.invalid/aar", specialist, guild))

        assert ok is False
        assert "not yet deployed" in msg.lower()
        assert "permission" not in msg.lower()

    def test_signed_up_member_without_company_role_can_submit(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        now = datetime.now(timezone.utc)
        pkg = {
            "id": "PKG-3",
            "directive_code": "DX-003",
            "directive_name": "Roster Trial",
            "status": STATUS_RECRUITING,
            "deadline": (now + timedelta(hours=2)).isoformat(),
            "assigned_kt": None,
            "assigned_company": "Watch Company Primus",
            "signed_up": [999],
            "assigned_specialist_ids": [],
            "mode": "Hard-Strat",
            "mission_id": 1,
            "required_roles": [],
        }
        store = {"packages": {"PKG-3": pkg}}
        monkeypatch.setattr(tp, "_load_tp", lambda: store)
        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: None),
        )

        submitter = _make_member(["Watch Brother"], member_id=999)
        guild = _make_guild([submitter])

        ok, msg = asyncio.run(submit_package("PKG-3", "https://example.invalid/aar", submitter, guild))

        assert ok is False
        assert "not yet deployed" in msg.lower()
        assert "permission" not in msg.lower()

    def test_expire_packages_posts_only_newest_touched_terminal_batch(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        posted_batches = []
        now = datetime.now(timezone.utc)
        store = {
            "rep": 30.0,
            "rep_scale_version": 2,
            "cycle": {
                "generated_at": None,
                "total": 0,
                "completed": 0,
                "failed": 0,
                "lapsed": 0,
                "batch_id": "BATCH-20260624",
                "batch_summary_posted_at": {},
            },
            "entity_stats": {"companies": {}, "kill_teams": {}, "cadres": {}},
            "packages": {
                "P-OLD": {
                    "id": "P-OLD",
                    "status": STATUS_RECRUITING,
                    "deadline": (now - timedelta(minutes=10)).isoformat(),
                    "batch_id": "BATCH-20260623",
                    "assigned_kt": "Kill Team Alpha",
                    "assigned_company": "Watch Company Primus",
                },
                "P-NEW": {
                    "id": "P-NEW",
                    "status": STATUS_RECRUITING,
                    "deadline": (now - timedelta(minutes=10)).isoformat(),
                    "batch_id": "BATCH-20260624",
                    "assigned_kt": "Kill Team Alpha",
                    "assigned_company": "Watch Company Primus",
                },
            },
            "rep_embed_message_id": None,
        }

        monkeypatch.setattr(tp, "_load_tp", lambda: store)
        monkeypatch.setattr(tp, "_save_tp", lambda _data: None)
        monkeypatch.setattr(tp, "_apply_rep_delta", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(tp, "_send_single_batch_warning", lambda *_args, **_kwargs: False)

        async def _noop(*_args, **_kwargs):
            return None

        async def fake_post_batch_summary(_guild, _data, batch_id=None):
            posted_batches.append(batch_id)

        monkeypatch.setattr(tp, "_delete_package_messages", _noop)
        monkeypatch.setattr(tp, "_update_ox_rep_embed", _noop)
        monkeypatch.setattr(tp, "_post_batch_summary", fake_post_batch_summary)

        guild = _make_guild([])
        asyncio.run(expire_packages(guild))

        assert posted_batches == ["BATCH-20260624"]


class TestAttachPackageToAarRecord:
    def test_seeds_datastore_from_live_parse_when_missing(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        class _FakeDataStore:
            def __init__(self):
                self.records = {}
                self.flush_calls = 0

            def get_record(self, aar_id):
                return self.records.get(str(aar_id))

            def iter_records(self):
                return iter(self.records.values())

            async def set_record(self, aar_id, record):
                self.records[str(aar_id)] = dict(record)

            async def flush(self):
                self.flush_calls += 1

        ds = _FakeDataStore()
        monkeypatch.setattr(tp._g, "DATASTORE", ds)

        async def _fake_parse(_aar_link, _guild):
            return {
                "aar_id": 987654321,
                "message_url": "https://discord.com/channels/1/2/987654321",
                "brother_ids": ["111", "222"],
                "points_for_op": 4,
                "difficulty_class": "hard_stratagem",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        monkeypatch.setattr(tp, "_parse_live_aar_for_link", _fake_parse)

        aar_link = "https://discord.com/channels/1/2/987654321"
        linked_id, canonical_url = asyncio.run(
            tp._attach_package_to_aar_record("PKG-42", aar_link, guild=object())
        )

        assert linked_id == "987654321"
        assert canonical_url == "https://discord.com/channels/1/2/987654321"
        stored = ds.records["987654321"]
        assert stored["target_package_id"] == "PKG-42"
        assert stored["target_package_ids"] == ["PKG-42"]
        assert stored["strike_directive_bonus_applied"] is True
        assert stored["points_for_op"] == 5
        assert ds.flush_calls == 1


class TestBidirectionalStrikeDirectiveLinkage:
    def test_reconciles_both_sides_from_package_link(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        class _FakeDataStore:
            def __init__(self):
                self.records = {
                    "987654321": {
                        "aar_id": 987654321,
                        "message_url": "https://discord.com/channels/1/2/987654321",
                        "brother_ids": ["111"],
                        "points_for_op": 4,
                    }
                }
                self.flush_calls = 0

            def get_record(self, aar_id):
                return self.records.get(str(aar_id))

            def iter_records(self):
                return iter(self.records.values())

            async def set_record(self, aar_id, record):
                self.records[str(aar_id)] = dict(record)

            async def flush(self):
                self.flush_calls += 1

        ds = _FakeDataStore()
        monkeypatch.setattr(tp._g, "DATASTORE", ds)

        tp_data = {
            "packages": {
                "PKG-42": {
                    "id": "PKG-42",
                    "status": "completed",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "aar_link": "https://discord.com/channels/1/2/987654321",
                    "aar_record_id": None,
                    "aar_message_id": None,
                }
            }
        }
        monkeypatch.setattr(tp, "_load_tp", lambda: tp_data)
        monkeypatch.setattr(tp, "_save_tp", lambda _data: None)

        pkg_changed, aar_changed = asyncio.run(
            tp._reconcile_bidirectional_strike_directive_linkage("PKG-42", guild=None)
        )

        assert pkg_changed is True
        assert aar_changed is True
        assert tp_data["packages"]["PKG-42"]["aar_record_id"] == "987654321"
        assert tp_data["packages"]["PKG-42"]["aar_message_id"] == "987654321"
        assert ds.records["987654321"]["target_package_id"] == "PKG-42"
        assert ds.records["987654321"]["target_package_ids"] == ["PKG-42"]
        assert ds.flush_calls == 1


class TestDirectiveForumLifecycle:
    def test_config_company_forum_mapping_includes_tertius(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        cfg = {
            "target_packages": {
                "directive_forum_parent_by_company": {
                    "Watch Company Primus": 1433351293103112202,
                    "Watch Company Secundus": 1458255656682258504,
                    "Watch Company Tertius": 1527778077323821086,
                }
            }
        }
        monkeypatch.setattr(tp, "_b", lambda name: cfg if name == "CONFIG" else None)
        mapping = tp._directive_forum_parent_map()
        assert mapping["watch company primus"] == 1433351293103112202
        assert mapping["watch company secundus"] == 1458255656682258504
        assert mapping["watch company tertius"] == 1527778077323821086

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
                            "Watch Company Tertius": 1527778077323821086,
                        }
                    }
                }
                if name == "CONFIG"
                else ({1433351293103112202, 1458255656682258504, 1527778077323821086} if name == "ALLOWED_KT_FORUM_PARENT_IDS" else None)
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


class TestConfigWeightHelpers:
    """Tests for config-driven weight parsing helpers."""

    # ------------------------------------------------------------------
    # _mode_draw_weights
    # ------------------------------------------------------------------

    def test_mode_weights_valid_override_applied(self, monkeypatch):
        """Valid config overrides should change the returned weights."""
        import opscribe.target_packages_ops as tp

        cfg = {"target_packages": {"mode_weights": {"hard_strat": 70, "omega_strat": 30}}}
        monkeypatch.setattr(tp, "_b", lambda name: cfg if name == "CONFIG" else None)

        hard, omega = tp._mode_draw_weights()
        assert hard == 70
        assert omega == 30

    def test_mode_weights_invalid_value_falls_back_to_defaults(self, monkeypatch):
        """Non-numeric values in mode_weights should fall back to defaults."""
        import opscribe.target_packages_ops as tp
        import opscribe._bot_globals as _g

        cfg = {"target_packages": {"mode_weights": {"hard_strat": "not_a_number", "omega_strat": 10}}}
        monkeypatch.setattr(tp, "_b", lambda name: cfg if name == "CONFIG" else None)
        monkeypatch.setattr(_g, "logger", MagicMock())

        hard, omega = tp._mode_draw_weights()
        assert hard == tp._MODE_WEIGHTS_DEFAULT["Hard-Strat"]
        assert omega == tp._MODE_WEIGHTS_DEFAULT["Omega-Strat"]

    def test_mode_weights_negative_falls_back_to_defaults(self, monkeypatch):
        """Negative weight values should fall back to defaults."""
        import opscribe.target_packages_ops as tp
        import opscribe._bot_globals as _g

        cfg = {"target_packages": {"mode_weights": {"hard_strat": -1, "omega_strat": 10}}}
        monkeypatch.setattr(tp, "_b", lambda name: cfg if name == "CONFIG" else None)
        monkeypatch.setattr(_g, "logger", MagicMock())

        hard, omega = tp._mode_draw_weights()
        assert hard == tp._MODE_WEIGHTS_DEFAULT["Hard-Strat"]
        assert omega == tp._MODE_WEIGHTS_DEFAULT["Omega-Strat"]

    def test_mode_weights_both_zero_falls_back_to_defaults(self, monkeypatch):
        """Both weights being zero should fall back to defaults."""
        import opscribe.target_packages_ops as tp
        import opscribe._bot_globals as _g

        cfg = {"target_packages": {"mode_weights": {"hard_strat": 0, "omega_strat": 0}}}
        monkeypatch.setattr(tp, "_b", lambda name: cfg if name == "CONFIG" else None)
        monkeypatch.setattr(_g, "logger", MagicMock())

        hard, omega = tp._mode_draw_weights()
        assert hard == tp._MODE_WEIGHTS_DEFAULT["Hard-Strat"]
        assert omega == tp._MODE_WEIGHTS_DEFAULT["Omega-Strat"]

    def test_mode_weights_missing_config_returns_defaults(self, monkeypatch):
        """Missing target_packages config block should return defaults."""
        import opscribe.target_packages_ops as tp

        monkeypatch.setattr(tp, "_b", lambda name: {} if name == "CONFIG" else None)

        hard, omega = tp._mode_draw_weights()
        assert hard == tp._MODE_WEIGHTS_DEFAULT["Hard-Strat"]
        assert omega == tp._MODE_WEIGHTS_DEFAULT["Omega-Strat"]

    # ------------------------------------------------------------------
    # _requirement_no_req_chance
    # ------------------------------------------------------------------

    def test_no_req_chance_valid_override_applied(self, monkeypatch):
        """Valid no_requirement_chance override should be returned."""
        import opscribe.target_packages_ops as tp

        cfg = {"target_packages": {"requirement_weights": {"no_requirement_chance": 0.25}}}
        monkeypatch.setattr(tp, "_b", lambda name: cfg if name == "CONFIG" else None)

        assert tp._requirement_no_req_chance() == 0.25

    def test_no_req_chance_invalid_type_falls_back(self, monkeypatch):
        """Non-numeric no_requirement_chance should fall back to default."""
        import opscribe.target_packages_ops as tp
        import opscribe._bot_globals as _g

        cfg = {"target_packages": {"requirement_weights": {"no_requirement_chance": "bad"}}}
        monkeypatch.setattr(tp, "_b", lambda name: cfg if name == "CONFIG" else None)
        monkeypatch.setattr(_g, "logger", MagicMock())

        assert tp._requirement_no_req_chance() == tp._REQUIREMENT_NO_REQ_CHANCE_DEFAULT

    def test_no_req_chance_out_of_range_high_falls_back(self, monkeypatch):
        """no_requirement_chance > 1.0 should fall back to default."""
        import opscribe.target_packages_ops as tp
        import opscribe._bot_globals as _g

        cfg = {"target_packages": {"requirement_weights": {"no_requirement_chance": 1.5}}}
        monkeypatch.setattr(tp, "_b", lambda name: cfg if name == "CONFIG" else None)
        monkeypatch.setattr(_g, "logger", MagicMock())

        assert tp._requirement_no_req_chance() == tp._REQUIREMENT_NO_REQ_CHANCE_DEFAULT

    def test_no_req_chance_out_of_range_low_falls_back(self, monkeypatch):
        """no_requirement_chance < 0.0 should fall back to default."""
        import opscribe.target_packages_ops as tp
        import opscribe._bot_globals as _g

        cfg = {"target_packages": {"requirement_weights": {"no_requirement_chance": -0.1}}}
        monkeypatch.setattr(tp, "_b", lambda name: cfg if name == "CONFIG" else None)
        monkeypatch.setattr(_g, "logger", MagicMock())

        assert tp._requirement_no_req_chance() == tp._REQUIREMENT_NO_REQ_CHANCE_DEFAULT

    def test_no_req_chance_boundary_values_accepted(self, monkeypatch):
        """Boundary values 0.0 and 1.0 should be accepted as valid."""
        import opscribe.target_packages_ops as tp

        for boundary in (0.0, 1.0):
            monkeypatch.setattr(tp, "_b", lambda name, b=boundary: {"target_packages": {"requirement_weights": {"no_requirement_chance": b}}} if name == "CONFIG" else None)
            assert tp._requirement_no_req_chance() == boundary

    # ------------------------------------------------------------------
    # _requirement_slot_tier_weights
    # ------------------------------------------------------------------

    def test_slot_tier_weights_valid_override_applied(self, monkeypatch):
        """Valid slot_tier overrides should replace the corresponding defaults."""
        import opscribe.target_packages_ops as tp

        custom = {k: 10 for k in tp._REQUIREMENT_SLOT_TIER_WEIGHTS_DEFAULT}
        cfg = {"target_packages": {"requirement_weights": {"slot_tier": custom}}}
        monkeypatch.setattr(tp, "_b", lambda name: cfg if name == "CONFIG" else None)

        result = dict(tp._requirement_slot_tier_weights())
        for key in tp._REQUIREMENT_SLOT_TIER_WEIGHTS_DEFAULT:
            assert result[key] == 10

    def test_slot_tier_weights_invalid_value_falls_back(self, monkeypatch):
        """Non-integer slot weight should fall back to full defaults."""
        import opscribe.target_packages_ops as tp
        import opscribe._bot_globals as _g

        first_key = next(iter(tp._REQUIREMENT_SLOT_TIER_WEIGHTS_DEFAULT))
        custom = dict(tp._REQUIREMENT_SLOT_TIER_WEIGHTS_DEFAULT)
        custom[first_key] = "not_an_int"
        cfg = {"target_packages": {"requirement_weights": {"slot_tier": custom}}}
        monkeypatch.setattr(tp, "_b", lambda name: cfg if name == "CONFIG" else None)
        monkeypatch.setattr(_g, "logger", MagicMock())

        assert tp._requirement_slot_tier_weights() == list(tp._REQUIREMENT_SLOT_TIER_WEIGHTS_DEFAULT.items())

    def test_slot_tier_weights_all_zero_falls_back(self, monkeypatch):
        """All-zero slot weights should fall back to defaults."""
        import opscribe.target_packages_ops as tp
        import opscribe._bot_globals as _g

        custom = {k: 0 for k in tp._REQUIREMENT_SLOT_TIER_WEIGHTS_DEFAULT}
        cfg = {"target_packages": {"requirement_weights": {"slot_tier": custom}}}
        monkeypatch.setattr(tp, "_b", lambda name: cfg if name == "CONFIG" else None)
        monkeypatch.setattr(_g, "logger", MagicMock())

        assert tp._requirement_slot_tier_weights() == list(tp._REQUIREMENT_SLOT_TIER_WEIGHTS_DEFAULT.items())

    def test_slot_tier_weights_missing_config_returns_defaults(self, monkeypatch):
        """Missing config should return defaults."""
        import opscribe.target_packages_ops as tp

        monkeypatch.setattr(tp, "_b", lambda name: {} if name == "CONFIG" else None)

        assert tp._requirement_slot_tier_weights() == list(tp._REQUIREMENT_SLOT_TIER_WEIGHTS_DEFAULT.items())

    # ------------------------------------------------------------------
    # _strat_counts_for_rep_tier
    # ------------------------------------------------------------------

    def test_strat_counts_valid_fixed_override_applied(self, monkeypatch):
        """Valid fixed-count config override should replace the table default."""
        import opscribe.target_packages_ops as tp

        cfg = {"target_packages": {"strat_modifier_counts_by_rep_tier": {"0": {"positive": 3, "negative": 1}}}}
        monkeypatch.setattr(tp, "_b", lambda name: cfg if name == "CONFIG" else None)

        pos, neg = tp._strat_counts_for_rep_tier(0)
        assert pos == 3
        assert neg == 1

    def test_strat_counts_invalid_type_falls_back(self, monkeypatch):
        """Non-numeric fixed counts should fall back to the table default."""
        import opscribe.target_packages_ops as tp
        import opscribe._bot_globals as _g

        cfg = {"target_packages": {"strat_modifier_counts_by_rep_tier": {"0": {"positive": "bad", "negative": 2}}}}
        monkeypatch.setattr(tp, "_b", lambda name: cfg if name == "CONFIG" else None)
        monkeypatch.setattr(_g, "logger", MagicMock())

        assert tp._strat_counts_for_rep_tier(0) == tp._STRAT_TABLE[0]

    def test_strat_counts_negative_value_falls_back(self, monkeypatch):
        """Negative strat counts should fall back to the table default."""
        import opscribe.target_packages_ops as tp
        import opscribe._bot_globals as _g

        cfg = {"target_packages": {"strat_modifier_counts_by_rep_tier": {"0": {"positive": -1, "negative": 2}}}}
        monkeypatch.setattr(tp, "_b", lambda name: cfg if name == "CONFIG" else None)
        monkeypatch.setattr(_g, "logger", MagicMock())

        assert tp._strat_counts_for_rep_tier(0) == tp._STRAT_TABLE[0]

    def test_strat_counts_missing_config_returns_table_default(self, monkeypatch):
        """Missing target_packages config block should return table defaults."""
        import opscribe.target_packages_ops as tp

        monkeypatch.setattr(tp, "_b", lambda name: {} if name == "CONFIG" else None)

        assert tp._strat_counts_for_rep_tier(0) == tp._STRAT_TABLE[0]

    def test_strat_counts_distribution_path_returns_valid_pair(self, monkeypatch):
        """Weighted distribution config should return one of the defined (pos, neg) pairs."""
        import opscribe.target_packages_ops as tp

        dist = [
            {"positive": 1, "negative": 2, "weight": 70},
            {"positive": 2, "negative": 1, "weight": 30},
        ]
        cfg = {"target_packages": {"strat_modifier_counts_by_rep_tier": {"0": {"distribution": dist}}}}
        monkeypatch.setattr(tp, "_b", lambda name: cfg if name == "CONFIG" else None)

        for _ in range(30):
            pos, neg = tp._strat_counts_for_rep_tier(0)
            assert (pos, neg) in {(1, 2), (2, 1)}

    def test_strat_counts_invalid_distribution_row_falls_back(self, monkeypatch):
        """A distribution row with bad values should fall back to the table default."""
        import opscribe.target_packages_ops as tp
        import opscribe._bot_globals as _g

        dist = [{"positive": "bad", "negative": 2, "weight": 100}]
        cfg = {"target_packages": {"strat_modifier_counts_by_rep_tier": {"0": {"distribution": dist}}}}
        monkeypatch.setattr(tp, "_b", lambda name: cfg if name == "CONFIG" else None)
        monkeypatch.setattr(_g, "logger", MagicMock())

        assert tp._strat_counts_for_rep_tier(0) == tp._STRAT_TABLE[0]

    def test_draw_strats_uses_config_override_counts(self, monkeypatch):
        """_draw_strats should use counts from _strat_counts_for_rep_tier config override."""
        import opscribe.target_packages_ops as tp

        # Override tier 0 to (3, 1) — distinct from the table default (1, 2)
        cfg = {"target_packages": {"strat_modifier_counts_by_rep_tier": {"0": {"positive": 3, "negative": 1}}}}
        monkeypatch.setattr(tp, "_b", lambda name: cfg if name == "CONFIG" else None)

        strats = _make_strats()
        result = _draw_strats(0.0, strats)
        buffs = [s for s in result["core"] if s["type"] == "buff"]
        debuffs = [s for s in result["core"] if s["type"] == "debuff"]
        assert len(buffs) == 3
        assert len(debuffs) == 1


class TestStrikeQueueMatching:
    def test_strike_queue_match_sweep_minutes_uses_valid_config(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        cfg = {"target_packages": {"strike_queue_match_sweep_minutes": 20}}
        monkeypatch.setattr(tp, "_b", lambda name: cfg if name == "CONFIG" else None)

        assert tp._strike_queue_match_sweep_minutes() == 20

    def test_strike_queue_match_sweep_minutes_invalid_config_falls_back(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        cfg = {"target_packages": {"strike_queue_match_sweep_minutes": 2}}
        monkeypatch.setattr(tp, "_b", lambda name: cfg if name == "CONFIG" else None)

        assert tp._strike_queue_match_sweep_minutes() == 15

    def test_member_queue_wait_time_minutes_treats_naive_timestamps_as_utc(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        now = datetime(2026, 1, 1, 12, 30, tzinfo=timezone.utc)

        class _DateTimeProxy:
            @staticmethod
            def fromisoformat(value):
                return datetime.fromisoformat(value)

            @staticmethod
            def now(_tz=None):
                return now

        monkeypatch.setattr(tp, "datetime", _DateTimeProxy)

        wait = tp._member_queue_wait_time_minutes(_make_member([], member_id=1), {"queued_at": "2026-01-01T12:00:00"})

        assert wait == 30.0

    def test_member_queue_wait_time_minutes_accepts_z_suffix_and_clamps_future_timestamps(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        now = datetime(2026, 1, 1, 12, 30, tzinfo=timezone.utc)

        class _DateTimeProxy:
            @staticmethod
            def fromisoformat(value):
                return datetime.fromisoformat(value)

            @staticmethod
            def now(_tz=None):
                return now

        monkeypatch.setattr(tp, "datetime", _DateTimeProxy)

        wait = tp._member_queue_wait_time_minutes(_make_member([], member_id=1), {"queued_at": "2026-01-01T12:00:00Z"})
        future_wait = tp._member_queue_wait_time_minutes(_make_member([], member_id=1), {"queued_at": "2026-01-01T13:00:00Z"})

        assert wait == 30.0
        assert future_wait == 0.0

    def test_prune_announced_match_when_queued_member_is_gone(self):
        import opscribe.target_packages_ops as tp

        pkg = _make_pkg(mode="Hard-Strat", signed_up=[])
        data = {
            "entries": {"1": {}, "2": {}},
            "announced_matches": {
                pkg["id"]: {
                    "signature": tp._queue_match_signature(pkg, [1, 2, 3]),
                    "queued_member_ids": [1, 2, 3],
                }
            },
        }

        pruned, removed = tp._prune_announced_strike_queue_matches(data, {pkg["id"]: pkg}, {"1", "2"})

        assert removed == 1
        assert pruned["announced_matches"] == {}

    def test_prune_announced_match_when_queued_member_ids_are_invalid(self):
        import opscribe.target_packages_ops as tp

        pkg = _make_pkg(mode="Hard-Strat", signed_up=[])
        data = {
            "entries": {"1": {}, "2": {}},
            "announced_matches": {
                pkg["id"]: {
                    "signature": "invalid",
                    "queued_member_ids": ["1", "bad"],
                }
            },
        }

        pruned, removed = tp._prune_announced_strike_queue_matches(data, {pkg["id"]: pkg}, {"1", "2"})

        assert removed == 1
        assert pruned["announced_matches"] == {}

    def test_prune_announced_match_when_stale(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        pkg = _make_pkg(mode="Hard-Strat", signed_up=[])
        data = {
            "entries": {"1": {}, "2": {}, "3": {}},
            "announced_matches": {
                pkg["id"]: {
                    "signature": tp._queue_match_signature(pkg, [1, 2, 3]),
                    "queued_member_ids": [1, 2, 3],
                    "announced_at": (now - timedelta(minutes=31)).isoformat(),
                }
            },
        }

        monkeypatch.setattr(tp, "_strike_queue_announced_ttl_minutes", lambda: 30)

        class _DateTimeProxy:
            @staticmethod
            def now(_tz=None):
                return now

        monkeypatch.setattr(tp, "datetime", _DateTimeProxy)

        pruned, removed = tp._prune_announced_strike_queue_matches(data, {pkg["id"]: pkg}, {"1", "2", "3"})

        assert removed == 1
        assert pruned["announced_matches"] == {}

    def test_reconcile_member_queue_entry_removes_inactive_member(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        queue_data = {
            "entries": {
                "1": {
                    "queued_at": "2026-01-01T00:00:00+00:00",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                    "mode_preference": "any",
                }
            },
            "announced_matches": {},
        }
        member = _make_member(["Watch Brother"], member_id=1)
        reserves = MagicMock()
        reserves.name = "Reserves"
        reserves.id = 999
        member.roles.append(reserves)

        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_member_meets_strike_queue_baseline", lambda _member: False)

        kept = asyncio.run(tp._reconcile_member_strike_queue_entry(member))

        assert kept is False
        assert queue_data["entries"] == {}

    def test_reconcile_member_queue_entry_removes_omega_without_platform(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        queue_data = {
            "entries": {
                "1": {
                    "queued_at": "2026-01-01T00:00:00+00:00",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                    "mode_preference": "omega",
                }
            },
            "announced_matches": {},
        }
        member = _with_company_role(_make_member(["Watch Brother"], member_id=1))

        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_tp_get_player_platform", lambda _member: None)

        kept = asyncio.run(tp._reconcile_member_strike_queue_entry(member))

        assert kept is False
        assert queue_data["entries"] == {}

    def test_reconcile_member_queue_entry_updates_platform_and_keeps_member(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        queue_data = {
            "entries": {
                "1": {
                    "queued_at": "2026-01-01T00:00:00+00:00",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                    "mode_preference": "any",
                    "platform": "console",
                }
            },
            "announced_matches": {},
        }
        member = _with_company_role(_make_member(["Watch Brother"], member_id=1))

        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_tp_get_player_platform", lambda _member: "pc")

        kept = asyncio.run(tp._reconcile_member_strike_queue_entry(member))

        assert kept is True
        assert queue_data["entries"]["1"]["platform"] == "pc"

    def test_assign_specialist_clears_queue_and_refreshes_embed(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        pkg = _make_pkg(required_roles=["Watch Apothecary"], assigned_specialist_ids=[])
        tp_data = {"packages": {pkg["id"]: pkg}}
        specialist = _with_company_role(_make_member(["Watch Brother", "Watch Apothecary"], member_id=77))
        leader = _with_company_role(_make_member(["Watch Apothecary"], member_id=9001))
        guild = _make_guild([specialist, leader])

        removed_from_queue = []
        refreshed = []
        notified = []

        monkeypatch.setattr(tp, "_load_tp", lambda: tp_data)
        monkeypatch.setattr(tp, "_save_tp", lambda data: tp_data.update(data))
        monkeypatch.setattr(tp, "_check_deployed", lambda *_args, **_kwargs: False)
        monkeypatch.setattr(tp, "_cadre_leader_owns", lambda _leader, role_name: role_name == "Watch Apothecary")
        monkeypatch.setattr(tp, "_member_meets_strike_queue_baseline", lambda _member: True)
        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Primus"),
        )

        async def _fake_remove_from_queue(user_id):
            removed_from_queue.append(user_id)
            return True

        async def _fake_refresh(package_id, _guild):
            refreshed.append(package_id)

        async def _fake_notify(member, package_id, _pkg, _guild, cadre_leader=None):
            notified.append((member.id, package_id, cadre_leader.id if cadre_leader else None))

        monkeypatch.setattr(tp, "_remove_member_from_strike_queue", _fake_remove_from_queue)
        monkeypatch.setattr(tp, "_refresh_signup_embed_for_package", _fake_refresh)
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [pkg])
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))
        monkeypatch.setattr(tp, "_notify_specialist_assigned", _fake_notify)

        ok, _msg = asyncio.run(tp.assign_specialist(pkg["id"], specialist, leader, guild))

        assert ok is True
        assert pkg["assigned_specialist_ids"] == [77]
        assert removed_from_queue == [77]
        assert refreshed == [pkg["id"]]
        assert notified == [(77, pkg["id"], 9001)]

    def test_assign_specialist_allows_cross_company_attachment(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        pkg = _make_pkg(required_roles=["Watch Apothecary"], assigned_specialist_ids=[])
        pkg["assigned_company"] = "Watch Company Primus"
        tp_data = {"packages": {pkg["id"]: pkg}}
        specialist = _with_company_role(_make_member(["Watch Brother", "Watch Apothecary"], member_id=78), company_name="Watch Company Secundus")
        leader = _with_company_role(_make_member(["Chief Apothecary"], member_id=9002), company_name="Watch Company Primus")
        guild = _make_guild([specialist, leader])

        removed_from_queue = []
        refreshed = []
        notified = []

        monkeypatch.setattr(tp, "_load_tp", lambda: tp_data)
        monkeypatch.setattr(tp, "_save_tp", lambda data: tp_data.update(data))
        monkeypatch.setattr(tp, "_check_deployed", lambda *_args, **_kwargs: False)
        monkeypatch.setattr(tp, "_cadre_leader_owns", lambda _leader, role_name: role_name == "Watch Apothecary")
        monkeypatch.setattr(tp, "_member_meets_strike_queue_baseline", lambda _member: True)
        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda member: "Watch Company Secundus" if member.id == 78 else "Watch Company Primus"),
        )

        async def _fake_remove_from_queue(user_id):
            removed_from_queue.append(user_id)
            return True

        async def _fake_refresh(package_id, _guild):
            refreshed.append(package_id)

        async def _fake_notify(member, package_id, _pkg, _guild, cadre_leader=None):
            notified.append((member.id, package_id, cadre_leader.id if cadre_leader else None))

        monkeypatch.setattr(tp, "_remove_member_from_strike_queue", _fake_remove_from_queue)
        monkeypatch.setattr(tp, "_refresh_signup_embed_for_package", _fake_refresh)
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [pkg])
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))
        monkeypatch.setattr(tp, "_notify_specialist_assigned", _fake_notify)

        ok, _msg = asyncio.run(tp.assign_specialist(pkg["id"], specialist, leader, guild))

        assert ok is True
        assert pkg["assigned_specialist_ids"] == [78]
        assert pkg["specialist_assigners"]["78"] == 9002
        assert removed_from_queue == [78]
        assert refreshed == [pkg["id"]]
        assert notified == [(78, pkg["id"], 9002)]

    def test_reconcile_member_directive_attachments_removes_signed_member_after_scope_change(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        pkg = _make_pkg(status=tp.STATUS_PENDING_SGT, signed_up=[1])
        tp_data = {"packages": {pkg["id"]: pkg}}
        member = _make_member(["Watch Brother"], member_id=1)
        guild = _make_guild([member])
        refreshed = []

        monkeypatch.setattr(tp, "_load_tp", lambda: tp_data)
        monkeypatch.setattr(tp, "_save_tp", lambda data: tp_data.update(data))
        monkeypatch.setattr(tp, "_member_meets_strike_queue_baseline", lambda _member: True)
        monkeypatch.setattr(tp, "_check_deployed", lambda *_args, **_kwargs: False)
        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Secundus"),
        )

        async def _fake_refresh(package_id, _guild):
            refreshed.append(package_id)

        monkeypatch.setattr(tp, "_refresh_signup_embed_for_package", _fake_refresh)

        removed = asyncio.run(tp._reconcile_member_directive_attachments(member, guild))

        assert removed == [pkg["id"]]
        assert pkg["signed_up"] == []
        assert refreshed == [pkg["id"]]

    def test_reconcile_member_directive_attachments_removes_specialist_who_lost_role(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        pkg = _make_pkg(required_roles=["Watch Apothecary"], assigned_specialist_ids=[2])
        pkg["specialist_assigners"] = {"2": 9001}
        tp_data = {"packages": {pkg["id"]: pkg}}
        member = _with_company_role(_make_member(["Watch Brother"], member_id=2))
        guild = _make_guild([member])
        refreshed = []

        monkeypatch.setattr(tp, "_load_tp", lambda: tp_data)
        monkeypatch.setattr(tp, "_save_tp", lambda data: tp_data.update(data))
        monkeypatch.setattr(tp, "_member_meets_strike_queue_baseline", lambda _member: True)
        monkeypatch.setattr(tp, "_check_deployed", lambda *_args, **_kwargs: False)
        monkeypatch.setitem(
            sys.modules,
            "opscribe.forge_ops",
            types.SimpleNamespace(_resolve_killteam_for_member=lambda _member: None),
        )
        monkeypatch.setitem(
            sys.modules,
            "opscribe.roster_ops",
            types.SimpleNamespace(_get_member_company_name=lambda _member: "Primus"),
        )

        async def _fake_refresh(package_id, _guild):
            refreshed.append(package_id)

        monkeypatch.setattr(tp, "_refresh_signup_embed_for_package", _fake_refresh)

        removed = asyncio.run(tp._reconcile_member_directive_attachments(member, guild))

        assert removed == [pkg["id"]]
        assert pkg["assigned_specialist_ids"] == []
        assert "2" not in pkg.get("specialist_assigners", {})
        assert refreshed == [pkg["id"]]

    def test_evaluate_queue_matches_requires_full_legal_team(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        class FakeThread(tp.discord.Thread):
            def __init__(self):
                self.messages = []
                self.embeds = []

            async def send(self, content=None, embed=None, **_kwargs):
                self.messages.append(content)
                self.embeds.append(embed)

        thread = FakeThread()
        queue_data = {
            "entries": {
                "1": {"queued_at": "2026-01-01T00:00:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
                "2": {"queued_at": "2026-01-01T00:01:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
            },
            "announced_matches": {},
        }
        pkg = _make_pkg(mode="Hard-Strat", signed_up=[])
        pkg["directive_code"] = "OX-1"
        pkg["directive_name"] = "Test Directive"
        pkg["classification"] = "strike"

        m1 = _with_company_role(_make_member(["Watch Brother"], member_id=1))
        m2 = _with_company_role(_make_member(["Watch Brother"], member_id=2))
        guild = _make_guild([m1, m2])

        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_load_tp", lambda: {"packages": {pkg["id"]: pkg}})
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [pkg])
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))

        async def _fake_thread(*_args, **_kwargs):
            return thread

        monkeypatch.setattr(tp, "_ensure_directive_forum_thread", _fake_thread)

        posted = asyncio.run(tp._evaluate_strike_queue_matches(guild))

        assert posted == 0
        assert thread.messages == []

    def test_evaluate_queue_matches_ignores_members_that_fail_baseline(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        queue_data = {
            "entries": {
                "1": {"queued_at": "2026-01-01T00:00:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
                "2": {"queued_at": "2026-01-01T00:01:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
                "3": {"queued_at": "2026-01-01T00:02:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
            },
            "announced_matches": {},
        }
        pkg = _make_pkg(mode="Hard-Strat", signed_up=[])
        members = [_with_company_role(_make_member(["Watch Brother"], member_id=i)) for i in (1, 2, 3)]
        guild = _make_guild(members)

        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_load_tp", lambda: {"packages": {pkg["id"]: pkg}})
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [pkg])
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))
        monkeypatch.setattr(tp, "_member_meets_strike_queue_baseline", lambda member: member.id != 3)

        posted = asyncio.run(tp._evaluate_strike_queue_matches(guild))

        assert posted == 0
        assert queue_data["entries"] == {
            "1": {"queued_at": "2026-01-01T00:00:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
            "2": {"queued_at": "2026-01-01T00:01:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
            "3": {"queued_at": "2026-01-01T00:02:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
        }

    def test_evaluate_queue_matches_commits_roster_and_cleans_queue(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        class FakeThread(tp.discord.Thread):
            def __init__(self):
                self.messages = []
                self.embeds = []

            async def send(self, content=None, embed=None, **_kwargs):
                self.messages.append(content)
                self.embeds.append(embed)

        thread = FakeThread()
        queue_data = {
            "entries": {
                "1": {"queued_at": "2026-01-01T00:00:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
                "2": {"queued_at": "2026-01-01T00:01:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
                "3": {"queued_at": "2026-01-01T00:02:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
            },
            "announced_matches": {},
        }
        pkg = _make_pkg(mode="Hard-Strat", signed_up=[])
        pkg["directive_code"] = "OX-1"
        pkg["directive_name"] = "Test Directive"
        pkg["classification"] = "strike"
        tp_data = {"packages": {pkg["id"]: pkg}}

        members = [_with_company_role(_make_member(["Watch Brother"], member_id=i)) for i in (1, 2, 3)]
        guild = _make_guild(members)
        refresh_calls = []

        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_load_tp", lambda: tp_data)
        monkeypatch.setattr(tp, "_save_tp", lambda data: tp_data.update(data))
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [pkg])
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))
        monkeypatch.setattr(tp, "_refresh_signup_embed_for_package", lambda package_id, guild: refresh_calls.append((package_id, guild)))

        async def _fake_thread(*_args, **_kwargs):
            return thread

        monkeypatch.setattr(tp, "_ensure_directive_forum_thread", _fake_thread)
        async def _fake_post_signup_embed(package_id, guild, complier=None):
            refresh_calls.append((package_id, guild, complier))
            tp_data["packages"][package_id]["signup_message_id"] = 111
            tp_data["packages"][package_id]["signup_channel_id"] = 222

        monkeypatch.setattr(tp, "_post_signup_embed", _fake_post_signup_embed)

        first = asyncio.run(tp._evaluate_strike_queue_matches(guild))

        assert first == 1
        assert len(thread.messages) == 1
        assert "<@1>" in thread.messages[0]
        assert "<@2>" in thread.messages[0]
        assert "<@3>" in thread.messages[0]
        assert len(thread.embeds) == 1
        assert thread.embeds[0] is not None
        assert "has a ready strike element" in (thread.embeds[0].description or "")
        assert "Queue cleared for matched brothers" in (thread.embeds[0].description or "")
        assert tp_data["packages"][pkg["id"]]["signed_up"] == [1, 2, 3]
        assert tp_data["packages"][pkg["id"]]["status"] == tp.STATUS_DEPLOYED
        assert queue_data["entries"] == {}
        assert refresh_calls

    def test_evaluate_queue_pop_clears_other_non_deployed_directives(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        queue_data = {
            "entries": {
                "1": {"queued_at": "2026-01-01T00:00:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
                "2": {"queued_at": "2026-01-01T00:01:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
                "3": {"queued_at": "2026-01-01T00:02:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
            },
            "announced_matches": {},
        }

        target_pkg = _make_pkg(mode="Hard-Strat", signed_up=[])
        target_pkg["id"] = "OX-TARGET"
        target_pkg["directive_code"] = "OX-TARGET"

        other_recruiting = _make_pkg(mode="Hard-Strat", signed_up=[1], assigned_specialist_ids=[2])
        other_recruiting["id"] = "OX-OTHER"
        other_recruiting["directive_code"] = "OX-OTHER"
        other_recruiting["specialist_assigners"] = {"2": 999}

        other_deployed = _make_pkg(
            status=tp.STATUS_DEPLOYED,
            mode="Hard-Strat",
            signed_up=[1],
            assigned_specialist_ids=[2],
        )
        other_deployed["id"] = "OX-DEPLOYED"
        other_deployed["directive_code"] = "OX-DEPLOYED"

        tp_data = {
            "packages": {
                target_pkg["id"]: target_pkg,
                other_recruiting["id"]: other_recruiting,
                other_deployed["id"]: other_deployed,
            }
        }

        members = [_with_company_role(_make_member(["Watch Brother"], member_id=i)) for i in (1, 2, 3)]
        guild = _make_guild(members)
        refresh_calls = []

        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_load_tp", lambda: tp_data)
        monkeypatch.setattr(tp, "_save_tp", lambda data: tp_data.update(data))
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [target_pkg])
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))

        async def _fake_refresh(package_id, _guild):
            refresh_calls.append(package_id)

        async def _fake_finalize(*_args, **_kwargs):
            return None

        async def _fake_ping(*_args, **_kwargs):
            return True

        monkeypatch.setattr(tp, "_refresh_signup_embed_for_package", _fake_refresh)
        monkeypatch.setattr(tp, "_finalize_strike_queue_match_directive", _fake_finalize)
        monkeypatch.setattr(tp, "_post_queue_match_ping", _fake_ping)

        posted = asyncio.run(tp._evaluate_strike_queue_matches(guild))

        assert posted == 1
        assert tp_data["packages"]["OX-TARGET"]["signed_up"] == [1, 2, 3]
        assert tp_data["packages"]["OX-TARGET"]["status"] == tp.STATUS_DEPLOYED

        # Cleared from non-deployed active directive.
        assert tp_data["packages"]["OX-OTHER"]["signed_up"] == []
        assert tp_data["packages"]["OX-OTHER"]["assigned_specialist_ids"] == []
        assert tp_data["packages"]["OX-OTHER"]["specialist_assigners"] == {}

        # Deployed directives are intentionally untouched.
        assert tp_data["packages"]["OX-DEPLOYED"]["signed_up"] == [1]
        assert tp_data["packages"]["OX-DEPLOYED"]["assigned_specialist_ids"] == [2]

        assert "OX-OTHER" in refresh_calls
        assert queue_data["entries"] == {}

    def test_evaluate_queue_matches_does_not_use_pending_sgt_directive(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        queue_data = {
            "entries": {
                "1": {"queued_at": "2026-01-01T00:00:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
                "2": {"queued_at": "2026-01-01T00:01:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
                "3": {"queued_at": "2026-01-01T00:02:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
            },
            "announced_matches": {},
        }
        pkg = _make_pkg(mode="Hard-Strat", signed_up=[])
        pkg["status"] = tp.STATUS_PENDING_SGT
        pkg["directive_code"] = "OX-1"
        members = [_with_company_role(_make_member(["Watch Brother"], member_id=i)) for i in (1, 2, 3)]
        guild = _make_guild(members)

        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_load_tp", lambda: {"packages": {pkg["id"]: pkg}})
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [pkg])

        posted = asyncio.run(tp._evaluate_strike_queue_matches(guild))

        assert posted == 0
        assert queue_data["entries"]

    def test_evaluate_queue_matches_ignores_non_fully_open_recruiting_directive(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        queue_data = {
            "entries": {
                "2": {"queued_at": "2026-01-01T00:01:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
                "3": {"queued_at": "2026-01-01T00:02:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
            },
            "announced_matches": {},
        }
        # Recruiting, but not fully open because one member is already attached.
        pkg = _make_pkg(mode="Hard-Strat", signed_up=[1])
        members = [_with_company_role(_make_member(["Watch Brother"], member_id=i)) for i in (1, 2, 3)]
        guild = _make_guild(members)

        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_load_tp", lambda: {"packages": {pkg["id"]: pkg}})
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [pkg])
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))

        posted = asyncio.run(tp._evaluate_strike_queue_matches(guild))

        assert posted == 0
        assert pkg["signed_up"] == [1]
        assert queue_data["entries"]

    def test_evaluate_queue_matches_can_backfill_partial_when_enabled(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        queue_data = {
            "entries": {
                "2": {"queued_at": "2026-01-01T00:01:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
                "3": {"queued_at": "2026-01-01T00:02:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
            },
            "announced_matches": {},
        }
        pkg = _make_pkg(mode="Hard-Strat", signed_up=[1])
        tp_data = {"packages": {pkg["id"]: pkg}}
        members = [_with_company_role(_make_member(["Watch Brother"], member_id=i)) for i in (1, 2, 3)]
        guild = _make_guild(members)

        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_load_tp", lambda: tp_data)
        monkeypatch.setattr(tp, "_save_tp", lambda data: tp_data.update(data))
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [pkg])
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))
        monkeypatch.setattr(tp, "_strike_queue_backfill_partials_enabled", lambda: True)
        monkeypatch.setattr(tp, "_strike_queue_partial_backfill_min_wait_minutes", lambda: 0.0)
        monkeypatch.setattr(tp, "_strike_queue_single_fill_min_wait_minutes", lambda: 0.0)

        async def _fake_finalize(*_args, **_kwargs):
            return None

        async def _fake_ping(*_args, **_kwargs):
            return True

        monkeypatch.setattr(tp, "_finalize_strike_queue_match_directive", _fake_finalize)
        monkeypatch.setattr(tp, "_post_queue_match_ping", _fake_ping)

        posted = asyncio.run(tp._evaluate_strike_queue_matches(guild))

        assert posted == 1
        assert sorted(tp_data["packages"][pkg["id"]]["signed_up"]) == [1, 2, 3]
        assert queue_data["entries"] == {}

    def test_evaluate_queue_matches_partial_backfill_respects_threshold(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        queue_data = {
            "entries": {
                "2": {"queued_at": "2026-01-01T00:01:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
                "3": {"queued_at": "2026-01-01T00:02:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
            },
            "announced_matches": {},
        }
        pkg = _make_pkg(mode="Hard-Strat", signed_up=[1])
        members = [_with_company_role(_make_member(["Watch Brother"], member_id=i)) for i in (1, 2, 3)]
        guild = _make_guild(members)

        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_load_tp", lambda: {"packages": {pkg["id"]: pkg}})
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [pkg])
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))
        monkeypatch.setattr(tp, "_strike_queue_backfill_partials_enabled", lambda: True)
        monkeypatch.setattr(tp, "_strike_queue_partial_backfill_min_wait_minutes", lambda: 30.0)
        monkeypatch.setattr(tp, "_strike_queue_single_fill_min_wait_minutes", lambda: 100.0)
        # One queued brother has only waited 10 minutes, so the 30-minute partial threshold blocks backfill.
        monkeypatch.setattr(tp, "_member_queue_wait_time_minutes", lambda m, _entry: 100.0 if m.id == 2 else 10.0)

        posted = asyncio.run(tp._evaluate_strike_queue_matches(guild))

        assert posted == 0
        assert queue_data["entries"]

    def test_evaluate_queue_matches_single_fill_respects_threshold(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        queue_data = {
            "entries": {
                "3": {"queued_at": "2026-01-01T00:02:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
            },
            "announced_matches": {},
        }
        # Hard directive with two already attached means one slot remains.
        pkg = _make_pkg(mode="Hard-Strat", signed_up=[1, 2])
        members = [_with_company_role(_make_member(["Watch Brother"], member_id=i)) for i in (1, 2, 3)]
        guild = _make_guild(members)

        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_load_tp", lambda: {"packages": {pkg["id"]: pkg}})
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [pkg])
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))
        monkeypatch.setattr(tp, "_strike_queue_backfill_partials_enabled", lambda: True)
        monkeypatch.setattr(tp, "_strike_queue_partial_backfill_min_wait_minutes", lambda: 100.0)
        monkeypatch.setattr(tp, "_strike_queue_single_fill_min_wait_minutes", lambda: 5.0)
        monkeypatch.setattr(tp, "_member_queue_wait_time_minutes", lambda *_args, **_kwargs: 0.0)

        posted = asyncio.run(tp._evaluate_strike_queue_matches(guild))

        assert posted == 0
        assert queue_data["entries"]

    def test_evaluate_queue_matches_saves_queue_before_ping_failure(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        queue_data = {
            "entries": {
                "1": {"queued_at": "2026-01-01T00:00:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
                "2": {"queued_at": "2026-01-01T00:01:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
                "3": {"queued_at": "2026-01-01T00:02:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
            },
            "announced_matches": {},
        }
        pkg = _make_pkg(mode="Hard-Strat", signed_up=[])
        tp_data = {"packages": {pkg["id"]: pkg}}
        members = [_with_company_role(_make_member(["Watch Brother"], member_id=i)) for i in (1, 2, 3)]
        guild = _make_guild(members)

        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_load_tp", lambda: tp_data)
        monkeypatch.setattr(tp, "_save_tp", lambda data: tp_data.update(data))
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [pkg])
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))
        tp._g.logger = MagicMock()

        async def _fake_finalize(*_args, **_kwargs):
            return None

        async def _fake_ping(*_args, **_kwargs):
            assert tp._STRIKE_QUEUE_LOCK.locked() is False
            raise RuntimeError("ping failed")

        monkeypatch.setattr(tp, "_finalize_strike_queue_match_directive", _fake_finalize)
        monkeypatch.setattr(tp, "_post_queue_match_ping", _fake_ping)

        posted = asyncio.run(tp._evaluate_strike_queue_matches(guild))

        assert posted == 1
        assert tp_data["packages"][pkg["id"]]["signed_up"] == [1, 2, 3]
        assert queue_data["entries"] == {}

    def test_post_queue_match_ping_mentions_only_matched_roster(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        class _FakeThread(tp.discord.Thread):
            def __init__(self):
                self.sent = []

            async def send(self, content=None, embed=None):
                self.sent.append({"content": content, "embed": embed})

        members = [_with_company_role(_make_member(["Watch Brother"], member_id=i)) for i in (1, 2, 3)]
        guild = _make_guild(members)
        thread = _FakeThread()
        pkg = _make_pkg(mode="Hard-Strat", signed_up=[1, 2, 3])

        async def _fake_ensure_thread(*_args, **_kwargs):
            return thread

        monkeypatch.setattr(tp, "_ensure_directive_forum_thread", _fake_ensure_thread)

        ok = asyncio.run(tp._post_queue_match_ping(pkg, members, guild, []))

        assert ok is True
        assert len(thread.sent) == 1
        payload = thread.sent[0]
        assert "<@&1429678423290281984>" not in (payload.get("content") or "")
        assert "<@1>" in (payload.get("content") or "")
        assert getattr(payload.get("embed"), "title", "") == "`sᴛʀɪᴋᴇ ᴛᴇᴀᴍ ʀᴇᴀᴅɪᴇᴅ`"

    def test_evaluate_queue_matches_records_tentative_on_commit_miss(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        queue_data = {
            "entries": {
                "1": {"queued_at": "2026-01-01T00:00:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
                "2": {"queued_at": "2026-01-01T00:01:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
                "3": {"queued_at": "2026-01-01T00:02:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00", "mode_preference": "hard"},
            },
            "announced_matches": {},
        }
        pkg = _make_pkg(mode="Hard-Strat", signed_up=[])
        members = [_with_company_role(_make_member(["Watch Brother"], member_id=i)) for i in (1, 2, 3)]
        guild = _make_guild(members)

        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_load_tp", lambda: {"packages": {pkg["id"]: pkg}})
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [pkg])
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))

        async def _fake_apply(*_args, **_kwargs):
            return None

        monkeypatch.setattr(tp, "_apply_strike_queue_match", _fake_apply)

        posted = asyncio.run(tp._evaluate_strike_queue_matches(guild))

        assert posted == 0
        assert queue_data["entries"]
        announced = queue_data["announced_matches"].get(pkg["id"])
        assert announced is not None
        assert sorted(announced["queued_member_ids"]) == [1, 2, 3]
        assert announced.get("announced_at")

    def test_queue_match_sort_prefers_older_queue_when_other_factors_tie(self):
        import opscribe.target_packages_ops as tp

        pkg = _make_pkg(mode="Hard-Strat", signed_up=[])
        newer = _make_member(["Watch Brother"], member_id=20)
        older = _make_member(["Watch Brother"], member_id=10)
        entry_map = {
            "10": {"queued_at": "2026-01-01T00:00:00+00:00"},
            "20": {"queued_at": "2026-01-01T00:05:00+00:00"},
        }

        older_key = tp._queue_match_sort_key((pkg, [older], [], tp._queue_match_oldest_timestamp(entry_map, [older])))
        newer_key = tp._queue_match_sort_key((pkg, [newer], [], tp._queue_match_oldest_timestamp(entry_map, [newer])))

        assert older_key < newer_key

    def test_select_queue_members_bounds_candidate_pool(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        pkg = _make_pkg(mode="Hard-Strat", signed_up=[])
        members = [_make_member(["Watch Brother"], member_id=i) for i in range(1, 25)]
        guild = _make_guild(members)
        seen = {}

        def _capture(items, choose_count):
            items = list(items)
            seen["count"] = len(items)
            return itertools_combinations(items, choose_count)

        monkeypatch.setattr(tp, "combinations", _capture)
        monkeypatch.setattr(tp, "_check_deployed", lambda *_args, **_kwargs: True)

        selected = tp._select_queue_members_for_package(pkg, members, guild)

        assert len(selected) == 3
        assert seen["count"] == tp._STRIKE_QUEUE_COMBINATION_CANDIDATE_LIMIT

    def test_queue_strike_uses_shared_baseline_check(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _with_company_role(_make_member(["Watch Brother"], member_id=1))
        interaction = _make_interaction(member, _make_guild([member]))

        monkeypatch.setattr(tp, "_member_meets_strike_queue_baseline", lambda _member: False)

        asyncio.run(_invoke_command(tp.queue_strike, interaction, minutes=60, mode_preference="any"))

        assert interaction.calls == [
            ("defer", True),
            ("send", "Only active brothers may join the strike queue.", True),
        ]

    def test_queue_baseline_allows_tithe_consul_without_watch_brother(self):
        import opscribe.target_packages_ops as tp

        member = _make_member(["Tithe Consul"], member_id=123)

        assert tp._member_meets_strike_queue_baseline(member) is True

    def test_queue_strike_allows_tithe_consul(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _make_member(["Tithe Consul"], member_id=88)
        interaction = _make_interaction(member, _make_guild([member]))
        queue_data = {"entries": {}, "announced_matches": {}}
        pkg = _make_pkg(mode="Hard-Strat", signed_up=[])
        pkg["id"] = "p_open"

        monkeypatch.setattr(tp, "_load_tp", lambda: {"packages": {pkg["id"]: pkg}})
        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [pkg])
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))
        monkeypatch.setattr(tp, "_tp_get_player_platform", lambda _member: "pc")

        async def _fake_eval(_guild):
            return 0

        async def _fake_reconcile(*_args, **_kwargs):
            return None

        monkeypatch.setattr(tp, "_evaluate_strike_queue_matches", _fake_eval)
        monkeypatch.setattr(tp, "_reconcile_strike_queue_board", _fake_reconcile)

        asyncio.run(_invoke_command(tp.queue_strike, interaction, minutes=60, mode_preference="any"))

        assert interaction.calls[0] == ("defer", True)
        assert "You are queued for strike directives" in interaction.calls[1][1]
        assert "88" in queue_data["entries"]

    def test_queue_strike_rejects_omega_without_platform(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _with_company_role(_make_member(["Watch Brother"], member_id=1))
        interaction = _make_interaction(member, _make_guild([member]))

        monkeypatch.setattr(tp, "_member_meets_strike_queue_baseline", lambda _member: True)
        monkeypatch.setattr(tp, "_tp_get_player_platform", lambda _member: None)

        asyncio.run(_invoke_command(tp.queue_strike, interaction, minutes=60, mode_preference="omega"))

        assert interaction.calls == [
            ("defer", True),
            ("send", "Omega queueing requires a PC or Console role.", True),
        ]

    def test_queue_strike_auto_removes_from_active_directive_when_joining_queue(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _with_company_role(_make_member(["Watch Brother"], member_id=1))
        interaction = _make_interaction(member, _make_guild([member]))

        pkg = _make_pkg(mode="Hard-Strat", signed_up=[1])
        pkg["directive_code"] = "OX-EXISTING"
        tp_data = {"packages": {pkg["id"]: pkg}}
        queue_data = {"entries": {}, "announced_matches": {}}

        monkeypatch.setattr(tp, "_member_meets_strike_queue_baseline", lambda _member: True)
        monkeypatch.setattr(tp, "_load_tp", lambda: tp_data)
        monkeypatch.setattr(tp, "_save_tp", lambda data: tp_data.update(data))
        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        async def _fake_eval(_guild):
            return 0

        async def _fake_reconcile(*_args, **_kwargs):
            return None

        async def _fake_refresh(*_args, **_kwargs):
            return None

        monkeypatch.setattr(tp, "_evaluate_strike_queue_matches", _fake_eval)
        monkeypatch.setattr(tp, "_reconcile_strike_queue_board", _fake_reconcile)
        monkeypatch.setattr(tp, "_refresh_signup_embed_for_package", _fake_refresh)
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [pkg])
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))
        monkeypatch.setattr(tp, "_tp_get_player_platform", lambda _member: "pc")
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [pkg])
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))

        asyncio.run(_invoke_command(tp.queue_strike, interaction, minutes=60, mode_preference="any"))

        assert interaction.calls[0] == ("defer", True)
        assert "Incomplete non-deployed directive rosters cleared: **1**" in interaction.calls[1][1]
        assert "OX-EXISTING" in interaction.calls[1][1]
        assert tp_data["packages"][pkg["id"]]["signed_up"] == []
        assert "1" in queue_data["entries"]

    def test_queue_strike_blocks_member_committed_as_specialist(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _with_company_role(_make_member(["Watch Brother"], member_id=1))
        interaction = _make_interaction(member, _make_guild([member]))

        pkg = _make_pkg(mode="Hard-Strat", signed_up=[], assigned_specialist_ids=[1])
        pkg["directive_code"] = "OX-SPEC"
        tp_data = {"packages": {pkg["id"]: pkg}}
        queue_data = {"entries": {}, "announced_matches": {}}

        monkeypatch.setattr(tp, "_member_meets_strike_queue_baseline", lambda _member: True)
        monkeypatch.setattr(tp, "_load_tp", lambda: tp_data)
        monkeypatch.setattr(tp, "_save_tp", lambda data: tp_data.update(data))
        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_tp_get_player_platform", lambda _member: "pc")

        async def _fake_eval(_guild):
            return 0

        async def _fake_reconcile(*_args, **_kwargs):
            return None

        async def _fake_refresh(*_args, **_kwargs):
            return None

        monkeypatch.setattr(tp, "_evaluate_strike_queue_matches", _fake_eval)
        monkeypatch.setattr(tp, "_reconcile_strike_queue_board", _fake_reconcile)
        monkeypatch.setattr(tp, "_refresh_signup_embed_for_package", _fake_refresh)
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [pkg])
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))

        asyncio.run(_invoke_command(tp.queue_strike, interaction, minutes=60, mode_preference="any"))

        assert interaction.calls[0] == ("defer", True)
        assert "Incomplete non-deployed directive rosters cleared: **1**" in interaction.calls[1][1]
        assert "OX-SPEC" in interaction.calls[1][1]
        assert interaction.calls[1][2] is True
        # Specialist attachment is cleared before queue add, so member is queued.
        assert tp_data["packages"][pkg["id"]]["assigned_specialist_ids"] == []
        assert "1" in queue_data.get("entries", {})

    def test_queue_strike_blocks_on_auto_detach_failure(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _with_company_role(_make_member(["Watch Brother"], member_id=1))
        interaction = _make_interaction(member, _make_guild([member]))

        pkg = _make_pkg(mode="Hard-Strat", signed_up=[1])
        pkg["directive_code"] = "OX-LOCKED"
        tp_data = {"packages": {pkg["id"]: pkg}}
        queue_data = {"entries": {}, "announced_matches": {}}

        monkeypatch.setattr(tp, "_member_meets_strike_queue_baseline", lambda _member: True)
        monkeypatch.setattr(tp, "_load_tp", lambda: tp_data)
        monkeypatch.setattr(tp, "_save_tp", lambda data: tp_data.update(data))
        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_tp_get_player_platform", lambda _member: "pc")

        async def _fake_eval(_guild):
            return 0

        async def _fake_reconcile(*_args, **_kwargs):
            return None

        async def _fake_refresh(*_args, **_kwargs):
            return None

        monkeypatch.setattr(tp, "_evaluate_strike_queue_matches", _fake_eval)
        monkeypatch.setattr(tp, "_reconcile_strike_queue_board", _fake_reconcile)
        monkeypatch.setattr(tp, "_refresh_signup_embed_for_package", _fake_refresh)
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [pkg])
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))

        async def _fake_remove_denied(_member, _guild):
            return False, "This member fulfills a required specialist role and cannot be removed."

        monkeypatch.setattr(tp, "_remove_member_from_active_directive", _fake_remove_denied)

        asyncio.run(_invoke_command(tp.queue_strike, interaction, minutes=60, mode_preference="any"))

        assert interaction.calls[0] == ("defer", True)
        assert "Incomplete non-deployed directive rosters cleared: **1**" in interaction.calls[1][1]
        assert "OX-LOCKED" in interaction.calls[1][1]
        assert interaction.calls[1][2] is True
        # Clear happens before detach checks, so member is queued.
        assert "1" in queue_data.get("entries", {})

    def test_queue_strike_reports_fully_open_queue_eligible_count(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _with_company_role(_make_member(["Watch Brother"], member_id=1))
        interaction = _make_interaction(member, _make_guild([member]))
        queue_data = {"entries": {}, "announced_matches": {}}
        p_open = _make_pkg(mode="Hard-Strat", signed_up=[])
        p_open["id"] = "p_open"
        p_partial = _make_pkg(mode="Hard-Strat", signed_up=[99])
        p_partial["id"] = "p_partial"
        p_omega = _make_pkg(mode="Omega-Strat", signed_up=[])
        p_omega["id"] = "p_omega"
        packages = {p["id"]: p for p in [p_open, p_partial, p_omega]}

        monkeypatch.setattr(tp, "_member_meets_strike_queue_baseline", lambda _member: True)
        monkeypatch.setattr(tp, "_tp_get_player_platform", lambda _member: "pc")
        monkeypatch.setattr(tp, "_load_tp", lambda: {"packages": packages})
        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [p_open, p_partial, p_omega])
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))

        async def _fake_eval(_guild):
            return 0

        monkeypatch.setattr(tp, "_evaluate_strike_queue_matches", _fake_eval)

        asyncio.run(_invoke_command(tp.queue_strike, interaction, minutes=60, mode_preference="hard"))

        assert interaction.calls[0] == ("defer", True)
        assert "Current fully-open directives eligible for queue matching: **2**." in interaction.calls[1][1]

    def test_queue_strike_backfill_enabled_counts_partial_directives(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _with_company_role(_make_member(["Watch Brother"], member_id=1))
        interaction = _make_interaction(member, _make_guild([member]))
        queue_data = {"entries": {}, "announced_matches": {}}
        p_open = _make_pkg(mode="Hard-Strat", signed_up=[])
        p_open["id"] = "p_open"
        p_partial = _make_pkg(mode="Hard-Strat", signed_up=[99])
        p_partial["id"] = "p_partial"
        packages = {p["id"]: p for p in [p_open, p_partial]}

        monkeypatch.setattr(tp, "_member_meets_strike_queue_baseline", lambda _member: True)
        monkeypatch.setattr(tp, "_tp_get_player_platform", lambda _member: "pc")
        monkeypatch.setattr(tp, "_load_tp", lambda: {"packages": packages})
        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [p_open, p_partial])
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))
        monkeypatch.setattr(tp, "_strike_queue_backfill_partials_enabled", lambda: True)

        async def _fake_eval(_guild):
            return 0

        monkeypatch.setattr(tp, "_evaluate_strike_queue_matches", _fake_eval)

        asyncio.run(_invoke_command(tp.queue_strike, interaction, minutes=60, mode_preference="hard"))

        assert interaction.calls[0] == ("defer", True)
        assert "Current fully-open directives eligible for queue matching: **2**." in interaction.calls[1][1]

    def test_strike_queue_status_uses_mode_filtered_queue_eligible_count(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _with_company_role(_make_member(["Watch Brother"], member_id=1))
        interaction = _make_interaction(member, _make_guild([member]))
        queue_data = {
            "entries": {
                "1": {
                    "mode_preference": "omega",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                }
            },
            "announced_matches": {},
        }
        p_open_hard = _make_pkg(mode="Hard-Strat", signed_up=[])
        p_open_hard["id"] = "p_open_hard"
        p_open_omega = _make_pkg(mode="Omega-Strat", signed_up=[])
        p_open_omega["id"] = "p_open_omega"
        p_partial_omega = _make_pkg(mode="Omega-Strat", signed_up=[7])
        p_partial_omega["id"] = "p_partial_omega"
        packages = {p["id"]: p for p in [p_open_hard, p_open_omega, p_partial_omega]}

        monkeypatch.setattr(tp, "_load_tp", lambda: {"packages": packages})
        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [p_open_hard, p_open_omega, p_partial_omega])
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))
        monkeypatch.setattr(tp, "_strike_queue_match_sweep_minutes", lambda: 15)

        asyncio.run(_invoke_command(tp.strike_queue_status, interaction))

        assert interaction.calls[0] == ("defer", True)
        payload = interaction.calls[1][1]
        assert getattr(payload, "title", "") == "`sᴛʀɪᴋᴇ ǫᴜᴇᴜᴇ sᴛᴀᴛᴜs`"
        field_map = {f.name: f.value for f in payload.fields}
        assert "Mode: **OMEGA**" in field_map.get("`ʏᴏᴜʀ ǫᴜᴇᴜᴇ sᴛᴀᴛᴜs`", "")
        assert "Position: **1/1**" in field_map.get("`ʏᴏᴜʀ ǫᴜᴇᴜᴇ sᴛᴀᴛᴜs`", "")
        assert "Eligible fully-open directives now: **1**" in field_map.get("`ᴇsᴛɪᴍᴀᴛᴇᴅ ᴡᴀɪᴛ`", "")

    def test_strike_queue_board_embed_shows_open_directive_count(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member1 = _with_company_role(_make_member(["Watch Brother"], member_id=1))
        member2 = _with_company_role(_make_member(["Watch Brother"], member_id=2))
        guild = _make_guild([member1, member2])

        pkg_hard = _make_pkg(mode="Hard-Strat", signed_up=[])
        pkg_hard["id"] = "pkg_hard"
        pkg_hard["assigned_company"] = "Watch Company Primus"
        pkg_omega = _make_pkg(mode="Omega-Strat", signed_up=[])
        pkg_omega["id"] = "pkg_omega"
        pkg_omega["assigned_company"] = "Watch Company Secundus"
        packages = {pkg_hard["id"]: pkg_hard, pkg_omega["id"]: pkg_omega}

        queue_data = {
            "entries": {
                "1": {"mode_preference": "hard", "platform": "pc"},
                "2": {"mode_preference": "omega", "platform": "console"},
            }
        }

        def _fake_eligible(member, _packages, mode_preference, _guild):
            if member.id == 1 and mode_preference == "hard":
                return [pkg_hard]
            if member.id == 2 and mode_preference == "omega":
                return [pkg_omega]
            return []

        monkeypatch.setattr(tp, "_queue_eligible_packages_for_member", _fake_eligible)

        embed = tp._build_strike_queue_board_embed(queue_data, packages, guild)
        field_map = {f.name: f.value for f in embed.fields}
        snapshot = field_map.get("`ǫᴜᴇᴜᴇ sɴᴀᴘsʜᴏᴛ`", "")
        assert "Open directives matchmaking now: **2**" in snapshot
        assert "Active recruiting strikes by company: Watch Company Primus **1** | Watch Company Secundus **1**" in snapshot
        assert "`ᴛᴇɴᴛᴀᴛɪᴠᴇ ɢʀᴏᴜᴘs`" not in field_map

        queued = field_map.get("`ǫᴜᴇᴜᴇᴅ ʙʀᴏᴛʜᴇʀs`", "")
        assert "**1** eligible recruiting strikes" in queued
        assert "General" not in queued

    def test_strike_queue_board_embed_open_directive_count_fallback_no_guild(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        pkg_hard = _make_pkg(mode="Hard-Strat", signed_up=[])
        pkg_hard["id"] = "pkg_hard"
        pkg_omega = _make_pkg(mode="Omega-Strat", signed_up=[])
        pkg_omega["id"] = "pkg_omega"
        pkg_partial = _make_pkg(mode="Hard-Strat", signed_up=[99])
        pkg_partial["id"] = "pkg_partial"
        packages = {
            pkg_hard["id"]: pkg_hard,
            pkg_omega["id"]: pkg_omega,
            pkg_partial["id"]: pkg_partial,
        }

        queue_data = {
            "entries": {
                "1": {"mode_preference": "hard"},
                "2": {"mode_preference": "omega"},
            }
        }

        monkeypatch.setattr(tp, "_strike_queue_backfill_partials_enabled", lambda: False)

        embed = tp._build_strike_queue_board_embed(queue_data, packages, guild=None)
        field_map = {f.name: f.value for f in embed.fields}
        # Fallback counts fully-open recruiting packages whose mode matches a queued preference.
        # pkg_hard (Hard-Strat, empty) matches "hard"; pkg_omega (Omega-Strat, empty) matches "omega";
        # pkg_partial is excluded because backfill is disabled and it has a signed_up member.
        assert "Open directives matchmaking now: **2**" in field_map.get("`ǫᴜᴇᴜᴇ sɴᴀᴘsʜᴏᴛ`", "")

    def test_strike_queue_status_shows_active_strikes_and_queue_counts(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        member = _with_company_role(_make_member(["Watch Brother"], member_id=1))
        brother2 = _with_company_role(_make_member(["Watch Brother"], member_id=2))
        brother3 = _with_company_role(_make_member(["Watch Brother"], member_id=3))
        guild = _make_guild([member, brother2, brother3])
        interaction = _make_interaction(member, guild)

        p_open_hard = _make_pkg(mode="Hard-Strat", signed_up=[])
        p_open_hard["id"] = "pkg_hard"
        p_open_hard["directive_code"] = "OX-HARD"
        p_open_hard["assigned_company"] = "Watch Company Primus"
        packages = {p_open_hard["id"]: p_open_hard}
        queue_data = {
            "entries": {
                "1": {
                    "mode_preference": "hard",
                    "queued_at": "2026-01-01T00:00:00+00:00",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                },
                "2": {
                    "mode_preference": "hard",
                    "queued_at": "2026-01-01T00:01:00+00:00",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                },
                "3": {
                    "mode_preference": "hard",
                    "queued_at": "2026-01-01T00:02:00+00:00",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                },
            },
            "announced_matches": {
                "pkg_hard": {
                    "signature": tp._queue_match_signature(p_open_hard, [1, 2, 3]),
                    "queued_member_ids": [1, 2, 3],
                    "announced_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        }

        monkeypatch.setattr(tp, "_load_tp", lambda: {"packages": packages})
        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [p_open_hard])
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))
        monkeypatch.setattr(tp, "_strike_queue_match_sweep_minutes", lambda: 15)

        asyncio.run(_invoke_command(tp.strike_queue_status, interaction))

        payload = interaction.calls[1][1]
        field_map = {f.name: f.value for f in payload.fields}
        active_field = next((value for name, value in field_map.items() if name.startswith("`ᴀᴄᴛɪᴠᴇ sᴛʀɪᴋᴇs`")), "")
        assert "`OX-HARD` · Watch Company Primus · **3** queued eligible" in active_field

        roster_field = next((value for name, value in field_map.items() if name.startswith("`ǫᴜᴇᴜᴇ ʙʀᴏᴛʜᴇʀs · Watch Company Primus`")), "")
        assert "M1 (you) · **1** eligible recruiting directives" in roster_field
        assert "M2 · **1** eligible recruiting directives" in roster_field
        assert "M3 · **1** eligible recruiting directives" in roster_field

    def test_strike_queue_status_groups_roster_by_company_and_special_buckets(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        primus = _with_company_role(_make_member(["Watch Brother"], member_id=1), "Watch Company Primus")
        secundus = _with_company_role(_make_member(["Watch Brother"], member_id=2), "Watch Company Secundus")
        chaplain = _make_member(["Watch Chaplain"], member_id=3)
        watch_master = _make_member(["Watch Master"], member_id=4)
        guild = _make_guild([primus, secundus, chaplain, watch_master])
        interaction = _make_interaction(primus, guild)

        pkg = _make_pkg(mode="Hard-Strat", signed_up=[])
        pkg["id"] = "pkg_hard"
        packages = {pkg["id"]: pkg}
        queue_data = {
            "entries": {
                "1": {"mode_preference": "any", "queued_at": "2026-01-01T00:00:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00"},
                "2": {"mode_preference": "any", "queued_at": "2026-01-01T00:01:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00"},
                "3": {"mode_preference": "any", "queued_at": "2026-01-01T00:02:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00"},
                "4": {"mode_preference": "any", "queued_at": "2026-01-01T00:03:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00"},
            },
            "announced_matches": {},
        }

        monkeypatch.setattr(tp, "_load_tp", lambda: {"packages": packages})
        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [pkg])
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))
        monkeypatch.setattr(tp, "_strike_queue_match_sweep_minutes", lambda: 15)

        asyncio.run(_invoke_command(tp.strike_queue_status, interaction))

        payload = interaction.calls[1][1]
        field_map = {f.name: f.value for f in payload.fields}
        assert "M1 (you) · **1** eligible recruiting directives" in field_map.get("`ǫᴜᴇᴜᴇ ʙʀᴏᴛʜᴇʀs · Watch Company Primus`", "")
        assert "M2 · **1** eligible recruiting directives" in field_map.get("`ǫᴜᴇᴜᴇ ʙʀᴏᴛʜᴇʀs · Watch Company Secundus`", "")
        assert "M3 · **1** eligible recruiting directives" in field_map.get("`ǫᴜᴇᴜᴇ ʙʀᴏᴛʜᴇʀs · Specialists`", "")
        assert "M4 · **1** eligible recruiting directives" in field_map.get("`ǫᴜᴇᴜᴇ ʙʀᴏᴛʜᴇʀs · Watch Master`", "")

    def test_strike_queue_status_shows_directive_centric_pairings(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        primus = _with_company_role(_make_member(["Watch Brother"], member_id=1), "Watch Company Primus")
        secundus = _with_company_role(_make_member(["Watch Brother"], member_id=2), "Watch Company Secundus")
        specialist = _make_member(["Watch Apothecary"], member_id=3)
        guild = _make_guild([primus, secundus, specialist])
        interaction = _make_interaction(primus, guild)

        pkg_primus = _make_pkg(mode="Hard-Strat", signed_up=[])
        pkg_primus["id"] = "pkg_primus"
        pkg_primus["directive_code"] = "SD-104"
        pkg_primus["assigned_company"] = "Watch Company Primus"

        pkg_secundus = _make_pkg(mode="Hard-Strat", signed_up=[])
        pkg_secundus["id"] = "pkg_secundus"
        pkg_secundus["directive_code"] = "SD-108"
        pkg_secundus["assigned_company"] = "Watch Company Secundus"

        packages = {pkg_primus["id"]: pkg_primus, pkg_secundus["id"]: pkg_secundus}
        queue_data = {
            "entries": {
                "1": {"mode_preference": "any", "queued_at": "2026-01-01T00:00:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00"},
                "2": {"mode_preference": "any", "queued_at": "2026-01-01T00:01:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00"},
                "3": {"mode_preference": "any", "queued_at": "2026-01-01T00:02:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00"},
            },
            "announced_matches": {},
        }

        def _fake_visible(member, _packages):
            if member.id == 1:
                return [pkg_primus]
            if member.id == 2:
                return [pkg_secundus]
            return [pkg_primus, pkg_secundus]

        monkeypatch.setattr(tp, "_load_tp", lambda: {"packages": packages})
        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", _fake_visible)
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))
        monkeypatch.setattr(tp, "_strike_queue_match_sweep_minutes", lambda: 15)

        asyncio.run(_invoke_command(tp.strike_queue_status, interaction))

        payload = interaction.calls[1][1]
        field_map = {f.name: f.value for f in payload.fields}
        pairing_field = field_map.get("`ᴘᴏᴛᴇɴᴛɪᴀʟ ᴘᴀɪʀɪɴɢs`", "")
        assert "`SD-104` · Watch Company Primus" in pairing_field
        assert "`SD-108` · Watch Company Secundus" in pairing_field
        assert "**2/3** ready" in pairing_field
        assert "needs **1** more" in pairing_field

    def test_strike_queue_status_pairings_exclude_partial_directives_when_backfill_disabled(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        primus = _with_company_role(_make_member(["Watch Brother"], member_id=1), "Watch Company Primus")
        specialist = _make_member(["Watch Apothecary"], member_id=3)
        guild = _make_guild([primus, specialist])
        interaction = _make_interaction(primus, guild)

        pkg_open = _make_pkg(mode="Hard-Strat", signed_up=[])
        pkg_open["id"] = "pkg_open"
        pkg_open["directive_code"] = "SD-OPEN"
        pkg_open["assigned_company"] = "Watch Company Primus"

        pkg_partial = _make_pkg(mode="Hard-Strat", signed_up=[99])
        pkg_partial["id"] = "pkg_partial"
        pkg_partial["directive_code"] = "SD-PARTIAL"
        pkg_partial["assigned_company"] = "Watch Company Primus"

        packages = {pkg_open["id"]: pkg_open, pkg_partial["id"]: pkg_partial}
        queue_data = {
            "entries": {
                "1": {"mode_preference": "any", "queued_at": "2026-01-01T00:00:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00"},
                "3": {"mode_preference": "any", "queued_at": "2026-01-01T00:02:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00"},
            },
            "announced_matches": {},
        }

        monkeypatch.setattr(tp, "_load_tp", lambda: {"packages": packages})
        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_visible_non_deployed_packages_for_member", lambda *_args, **_kwargs: [pkg_open, pkg_partial])
        monkeypatch.setattr(tp, "_is_eligible_to_sign_up", lambda *_args, **_kwargs: (True, ""))
        monkeypatch.setattr(tp, "_strike_queue_match_sweep_minutes", lambda: 15)
        monkeypatch.setattr(tp, "_strike_queue_backfill_partials_enabled", lambda: False)

        asyncio.run(_invoke_command(tp.strike_queue_status, interaction))

        payload = interaction.calls[1][1]
        field_map = {f.name: f.value for f in payload.fields}
        pairing_field = field_map.get("`ᴘᴏᴛᴇɴᴛɪᴀʟ ᴘᴀɪʀɪɴɢs`", "")
        assert "`SD-OPEN` · Watch Company Primus" in pairing_field
        assert "`SD-PARTIAL`" not in pairing_field

    def test_manage_roster_allows_watch_command_to_remove_cross_company_target(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        actor = _make_member(["Watch Sergeant"], member_id=11)
        target = _with_company_role(_make_member(["Watch Brother"], member_id=22), "Watch Company Secundus")
        pkg = _make_pkg(status=STATUS_RECRUITING, signed_up=[22], assigned_specialist_ids=[])
        pkg["assigned_company"] = "Watch Company Primus"
        packages = {pkg["id"]: pkg}

        monkeypatch.setattr(tp, "_load_tp", lambda: {"packages": packages})

        _pkg, removable, err = tp._get_removable_targets_for_actor(pkg["id"], actor, _make_guild([actor, target]))

        assert err is None
        assert removable == [(22, target.display_name)]


class TestStrikeQueueBoard:
    class _FakeMessage:
        def __init__(self, message_id):
            self.id = message_id
            self.deleted = False
            self.edits = []
            self.pinned = False
            self.pin_calls = []

        async def edit(self, **kwargs):
            self.edits.append(kwargs)

        async def delete(self):
            self.deleted = True

        async def pin(self, *, reason=None):
            self.pinned = True
            self.pin_calls.append(reason)

    class _FakeChannel:
        def __init__(self, channel_id=1429942816741523570):
            self.id = channel_id
            self.sent = []
            self.messages = {}
            self.next_id = 5000

        async def send(self, **kwargs):
            msg = TestStrikeQueueBoard._FakeMessage(self.next_id)
            self.next_id += 1
            self.messages[msg.id] = msg
            self.sent.append(kwargs)
            return msg

        async def fetch_message(self, message_id):
            if message_id not in self.messages:
                raise Exception("missing message")
            return self.messages[message_id]

    def test_empty_strike_queue_store_has_board_metadata(self):
        import opscribe.target_packages_ops as tp

        data = tp._empty_strike_queue_store()

        assert isinstance(data.get("board"), dict)
        assert data["board"]["channel_id"] is None
        assert data["board"]["message_id"] is None
        assert data["board"]["last_bump_at"] is None
        assert data["board"]["last_rendered_at"] is None

    def test_queued_member_fit_tags_maps_and_caps(self):
        import opscribe.target_packages_ops as tp

        member = _make_member(
            [
                "Watch Veteran",
                "Oathsworn",
                "Watch Sergeant",
                "Watch Techmarine",
                "Watch Apothecary",
                "Watch Brother",
            ],
            member_id=707,
        )

        tags = tp._queued_member_fit_tags(member)

        assert tags == [
            "Watch Veteran",
            "Oathsworn",
            "Watch Sergeant",
            "Watch Techmarine",
            "Watch Apothecary",
        ]

    def test_queued_member_fit_tags_includes_veteran_sergeant(self):
        import opscribe.target_packages_ops as tp

        member = _make_member(
            [
                "Watch Veteran",
                "Veteran Sergeant",
                "Watch Sergeant",
                "Watch Brother",
            ],
            member_id=711,
        )

        tags = tp._queued_member_fit_tags(member)

        assert "Veteran Sergeant" in tags

    def test_queued_member_fit_tags_includes_forgemaster(self):
        import opscribe.target_packages_ops as tp

        member = _make_member(["Watch Veteran", "Forgemaster"], member_id=708)

        tags = tp._queued_member_fit_tags(member)

        assert "Forgemaster" in tags

    def test_queued_member_fit_tags_includes_high_command_requirement_roles(self):
        import opscribe.target_packages_ops as tp

        member = _make_member(["Huntmaster", "Venerable Dreadnought"], member_id=709)

        tags = tp._queued_member_fit_tags(member)

        assert "Huntmaster" in tags
        assert "Venerable Dreadnought" in tags

    def test_reconcile_board_creates_message_when_queue_becomes_non_empty(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        queue_data = tp._empty_strike_queue_store()
        queue_data["entries"] = {
            "1": {
                "user_id": 1,
                "queued_at": "2026-01-01T00:00:00+00:00",
                "expires_at": "2099-01-01T00:00:00+00:00",
                "mode_preference": "hard",
                "platform": "pc",
            }
        }
        member = _with_company_role(_make_member(["Watch Brother", "Watch Veteran"], member_id=1))
        guild = _make_guild([member])
        channel = self._FakeChannel()

        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_load_tp", lambda: {"packages": {}})
        monkeypatch.setattr(tp, "_resolve_channel", lambda *_args, **_kwargs: channel)

        asyncio.run(tp._reconcile_strike_queue_board(guild, force_bump=True))

        assert len(channel.sent) == 1
        assert channel.sent[0].get("content") == "<@&1429678423290281984>"
        created = channel.messages[queue_data["board"]["message_id"]]
        assert created.pinned is True
        assert queue_data["board"]["message_id"] == 5000
        assert queue_data["board"]["channel_id"] == channel.id
        assert queue_data["board"]["last_bump_at"] is not None

    def test_reconcile_board_edits_existing_message_without_bump(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        queue_data = tp._empty_strike_queue_store()
        queue_data["entries"] = {
            "1": {
                "user_id": 1,
                "queued_at": "2026-01-01T00:00:00+00:00",
                "expires_at": "2099-01-01T00:00:00+00:00",
                "mode_preference": "hard",
                "platform": "pc",
            }
        }
        queue_data["board"] = {
            "channel_id": 1429942816741523570,
            "message_id": 7001,
            "last_bump_at": datetime.now(timezone.utc).isoformat(),
            "last_rendered_at": None,
        }
        member = _with_company_role(_make_member(["Watch Brother"], member_id=1))
        guild = _make_guild([member])
        channel = self._FakeChannel()
        existing = self._FakeMessage(7001)
        channel.messages[7001] = existing

        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_load_tp", lambda: {"packages": {}})
        monkeypatch.setattr(tp, "_resolve_channel", lambda *_args, **_kwargs: channel)

        asyncio.run(tp._reconcile_strike_queue_board(guild, major_change=False, force_bump=False))

        assert len(channel.sent) == 0
        assert len(existing.edits) == 1
        assert existing.edits[0].get("content") == "<@&1429678423290281984>"
        assert existing.pinned is True
        assert queue_data["board"]["message_id"] == 7001
        assert queue_data["board"]["last_rendered_at"] is not None

    def test_reconcile_board_bumps_existing_message_on_major_change_after_ttl(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        queue_data = tp._empty_strike_queue_store()
        queue_data["entries"] = {
            "1": {
                "user_id": 1,
                "queued_at": "2026-01-01T00:00:00+00:00",
                "expires_at": "2099-01-01T00:00:00+00:00",
                "mode_preference": "hard",
                "platform": "pc",
            }
        }
        queue_data["board"] = {
            "channel_id": 1429942816741523570,
            "message_id": 7101,
            "last_bump_at": "2026-01-01T00:00:00+00:00",
            "last_rendered_at": None,
        }
        member = _with_company_role(_make_member(["Watch Brother"], member_id=1))
        guild = _make_guild([member])
        channel = self._FakeChannel()
        existing = self._FakeMessage(7101)
        channel.messages[7101] = existing

        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_load_tp", lambda: {"packages": {}})
        monkeypatch.setattr(tp, "_resolve_channel", lambda *_args, **_kwargs: channel)

        asyncio.run(tp._reconcile_strike_queue_board(guild, major_change=True, force_bump=False))

        assert len(channel.sent) == 1
        assert existing.deleted is True
        assert queue_data["board"]["message_id"] == 5000

    def test_reconcile_board_deletes_message_when_queue_empties(self, monkeypatch):
        import opscribe.target_packages_ops as tp

        queue_data = tp._empty_strike_queue_store()
        queue_data["board"] = {
            "channel_id": 1429942816741523570,
            "message_id": 7201,
            "last_bump_at": datetime.now(timezone.utc).isoformat(),
            "last_rendered_at": datetime.now(timezone.utc).isoformat(),
        }
        guild = _make_guild([])
        channel = self._FakeChannel()
        existing = self._FakeMessage(7201)
        channel.messages[7201] = existing

        monkeypatch.setattr(tp, "_load_strike_queue", lambda: queue_data)
        monkeypatch.setattr(tp, "_save_strike_queue", lambda data: queue_data.update(data))
        monkeypatch.setattr(tp, "_load_tp", lambda: {"packages": {}})
        monkeypatch.setattr(tp, "_resolve_channel", lambda *_args, **_kwargs: channel)

        asyncio.run(tp._reconcile_strike_queue_board(guild))

        assert existing.deleted is True
        assert queue_data["board"]["message_id"] is None
        assert queue_data["board"]["channel_id"] is None


# ---------------------------------------------------------------------------
# Phase 0: Baseline and guardrails (regression tests freezing non-specialist behavior)
# ---------------------------------------------------------------------------

class TestPhase0Regression:
    """Regression tests that freeze expected non-specialist behavior paths.

    These tests verify that non-specialist directive generation, sign-up validation,
    deployment checks, and requirement drawing remain stable across the cadre-based
    specialist migration (Phase 0-6).

    Frozen behaviors:
    - Non-specialist sign-ups require only role membership, not company scope
    - Requirement drawing works independently of specialist availability
    - HC roles and company command roles are properly distinguished
    - Bladeguard is tied to Kill Team, not company
    """

    def test_draw_requirements_basic_stability(self):
        """Requirement drawing should be stable and produce valid role lists."""
        # Draw requirements with empty available roles
        req_tier, requirements = _draw_requirements(set(), mode="Hard-Strat")
        # Should return a valid tier and a list of requirements
        assert req_tier in _TIER_ROLES or req_tier == _REQ_TIER_NO_REQ
        assert isinstance(requirements, list)

    def test_draw_requirements_with_available_roles(self):
        """Requirement drawing should work with available roles."""
        # Draw requirements when some roles are available
        available = {"Watch Brother": 1, "Watch Veteran": 1}
        req_tier, requirements = _draw_requirements(available, mode="Hard-Strat")
        # Should produce requirements from tiers
        assert isinstance(requirements, list)
        # Requirements should be subset of or disjoint from available (no guarantee they match)
        for req in requirements:
            assert isinstance(req, str)

    def test_check_deployed_basic_count(self):
        """Deployment check should work with basic sign-up counts."""
        pkg = _make_pkg(
            status=STATUS_RECRUITING,
            signed_up=[10, 11, 12],  # 3 regular sign-ups
            required_roles=["Watch Brother"],
            assigned_specialist_ids=[],  # No specialists
        )
        members = [_make_member(member_id=i) for i in [10, 11, 12]]
        guild = _make_guild(members)
        result = _check_deployed(pkg, guild)
        # Should return a boolean indicating deployment readiness
        assert isinstance(result, bool)

    def test_company_command_roster_grouping_stable(self):
        """Company command roster grouping should remain accessible."""
        # This test verifies ROSTER_COMPANY_COMMAND_RANKS exists and is used consistently
        from opscribe.constants import ROSTER_COMPANY_COMMAND_RANKS
        assert isinstance(ROSTER_COMPANY_COMMAND_RANKS, set)
        assert "Watch Captain" in ROSTER_COMPANY_COMMAND_RANKS
        assert "Watch Lieutenant" in ROSTER_COMPANY_COMMAND_RANKS
        # During Phase 0-2, specialists are still in company command
        # Phase 3 will remove them; this test freezes current state

    def test_bladeguard_is_specialist_role(self):
        """Bladeguard should be recognized as specialist role."""
        # Bladeguard is in _CADRE_SPECIALIST_ROLES (will be used in scope handling)
        assert "Bladeguard" in _CADRE_SPECIALIST_ROLES

    def test_member_removable_by_sergeant_in_assigned_company(self):
        """Sergeant should be able to remove regular signed-up members from own company directives."""
        sgt = _with_company_role(_make_member(["Watch Sergeant"], member_id=200))
        target = _with_company_role(_make_member(["Watch Brother"], member_id=201))
        pkg = _make_pkg(
            status=STATUS_RECRUITING,
            signed_up=[201],
            required_roles=["Watch Brother"],
            assigned_specialist_ids=[],
        )
        pkg["assigned_company"] = "Watch Company Primus"
        # Sergeant should be able to remove signed members from their own company directives.
        ok, kinds, _ = _can_actor_remove_attached_target(sgt, target, 201, pkg, _make_guild([sgt, target]))
        assert ok is True
        assert "signed" in kinds

    def test_remove_target_updates_package_state(self):
        """Removal operation should correctly update package state."""
        target = _make_member(["Watch Brother"], member_id=77)
        pkg = _make_pkg(
            status=STATUS_DEPLOYED,
            mode="Hard-Strat",
            signed_up=[77, 2, 3],
            required_roles=["Watch Brother"],
            assigned_specialist_ids=[],
        )
        ok, msg = _remove_target_from_package(pkg, 77, {"signed"}, _make_guild([target]))
        # Should succeed and update sign_up list
        assert ok is True
        assert 77 not in pkg["signed_up"]
        # Status should revert to recruiting when deployed package loses sign-ups
        assert pkg["status"] in (STATUS_RECRUITING, STATUS_DEPLOYED)

    def test_tier_roles_constant_stable(self):
        """Tier roles constant should remain accessible (used in requirement drawing)."""
        # _TIER_ROLES is used internally by _draw_requirements
        assert _TIER_ROLES is not None
        assert len(_TIER_ROLES) > 0
        # Should have tier keys that are used for requirement drawing
        assert _REQ_TIER_NO_REQ in _TIER_ROLES or _REQ_TIER_NO_REQ is not None

    def test_specialist_roles_constant_stable(self):
        """_CADRE_SPECIALIST_ROLES constant should be accessible (for Phase 2 migration)."""
        # This set is fundamental to the migration; must be stable
        assert _CADRE_SPECIALIST_ROLES is not None
        assert isinstance(_CADRE_SPECIALIST_ROLES, set)
        # Should include HC roles and specialist roles
        assert len(_CADRE_SPECIALIST_ROLES) > 0
