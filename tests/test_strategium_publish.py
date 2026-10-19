import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from opscribe import strategium_publish as publisher
from opscribe.constants import RESERVES_ROLE_ID


@pytest.fixture(autouse=True)
def isolated_war_store(tmp_path, monkeypatch):
    from opscribe import galactic_war_runtime as war
    from opscribe.galactic_war_store import GalacticWarStore
    monkeypatch.setattr(war, "get_store", lambda: GalacticWarStore(tmp_path / "war.sqlite3", {}, []))


def test_publish_url_accepts_https_and_loopback_http(monkeypatch):
    monkeypatch.setenv("STRATEGIUM_PUBLISH_URL", "https://example.test/internal/roster/snapshot")
    assert publisher.publish_url().startswith("https://")

    monkeypatch.setenv("STRATEGIUM_PUBLISH_URL", "http://127.0.0.1:8787/internal/roster/snapshot")
    assert publisher.publish_url().startswith("http://127.0.0.1")

    monkeypatch.setenv("STRATEGIUM_PUBLISH_URL", "http://localhost:8787/internal/roster/snapshot")
    assert publisher.publish_url().startswith("http://localhost")

    monkeypatch.setenv("STRATEGIUM_PUBLISH_URL", "http://evil.example/internal/roster/snapshot")
    assert publisher.publish_url() == ""


def test_publish_snapshot_disables_redirects(monkeypatch):
    monkeypatch.setenv("STRATEGIUM_PUBLISH_URL", "http://127.0.0.1:8787/internal/roster/snapshot")
    monkeypatch.setenv("STRATEGIUM_BOT_SHARED_SECRET", "test-secret")
    monkeypatch.setattr(publisher, "build_snapshot", lambda _bot: {"members": []})

    captured = {}

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class Session:
        def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return Response()

    result = asyncio.run(publisher.publish_snapshot(SimpleNamespace(), Session()))

    assert result is True
    assert captured["allow_redirects"] is False


def test_recent_aar_teammates_are_published_from_datastore(monkeypatch):
    class _Datastore:
        def get_recent_teammate_ids(self, user_id, limit):
            assert user_id == "42"
            assert limit == 5
            return ["101", "102", "103"]

    monkeypatch.setattr(publisher._g, "DATASTORE", _Datastore())
    member = SimpleNamespace(id=42)

    assert publisher._recent_aar_teammates(member) == ["101", "102", "103"]


def test_is_equerry_matches_role_name():
    assert publisher._is_equerry(SimpleNamespace(roles=[SimpleNamespace(name="First Blade"), SimpleNamespace(name="High Command Equerry")]))
    assert not publisher._is_equerry(SimpleNamespace(roles=[SimpleNamespace(name="First Blade")]))


def test_war_signup_membership_uses_watch_brother_role_id_not_rank_name(monkeypatch):
    from opscribe import galactic_war_runtime as war
    from opscribe.constants import WATCH_BROTHER_ROLE_ID
    guild = SimpleNamespace(members=[
        SimpleNamespace(id=1, bot=False, roles=[SimpleNamespace(id=WATCH_BROTHER_ROLE_ID, name="Renamed base role")]),
        SimpleNamespace(id=2, bot=False, roles=[SimpleNamespace(id=999, name="Watch Brother"), SimpleNamespace(id=888, name="Watch Captain")]),
    ])
    monkeypatch.setattr(publisher, "_resolve_company_kill_teams", lambda value: ({}, {}))
    monkeypatch.setattr(publisher, "_company", lambda value: None)
    monkeypatch.setattr(publisher, "_role_name", lambda value: "Watch Captain")
    monkeypatch.setattr(publisher, "_kill_team", lambda value: None)
    members, _rosters, _companies, _teams = war.directory(guild)
    assert members["1"].watch_brother is True
    assert members["2"].watch_brother is False


def test_new_chapters_are_recognized_for_roster_publishing():
    for chapter in ("Consecrators", "Doom Eagles"):
        member = SimpleNamespace(roles=[SimpleNamespace(name=chapter)])
        assert publisher._chapter(member) == chapter


def test_reserves_members_are_excluded_from_snapshot(monkeypatch):
    def member(member_id, *roles):
        return SimpleNamespace(id=member_id, bot=False, roles=list(roles))

    active = member(1, SimpleNamespace(id=10, name="Watch Brother"))
    by_id = member(2, SimpleNamespace(id=10, name="Watch Brother"), SimpleNamespace(id=RESERVES_ROLE_ID, name="Renamed"))
    by_name = member(3, SimpleNamespace(id=10, name="Watch Brother"), SimpleNamespace(id=99, name="Reserves"))
    guild = SimpleNamespace(members=[active, by_id, by_name])
    monkeypatch.setattr(publisher, "_select_guild", lambda _bot: guild)
    monkeypatch.setattr(publisher, "_resolve_company_kill_teams", lambda _guild: ({}, {}))
    from opscribe import galactic_war_runtime as war
    monkeypatch.setattr(war, "directory", lambda _guild: ({}, {}, {}, []))
    monkeypatch.setattr(publisher, "_reach_graph", lambda: {"nodes": [], "edges": []})
    seen = []
    monkeypatch.setattr(publisher, "_role_name", lambda m: seen.append(m.id) or None)

    snapshot = publisher.build_snapshot(SimpleNamespace())

    assert snapshot["members"] == []
    assert seen == [1]
    assert snapshot["reach"]["directives"] == []


def test_snapshot_includes_optional_oath_seal_placeholder(monkeypatch):
    member = SimpleNamespace(id=42, bot=False, roles=[])
    monkeypatch.setattr(publisher, "_select_guild", lambda _bot: SimpleNamespace(members=[member]))
    monkeypatch.setattr(publisher, "_resolve_company_kill_teams", lambda _guild: ({}, {}))
    from opscribe import galactic_war_runtime as war
    monkeypatch.setattr(war, "directory", lambda _guild: ({}, {}, {}, []))
    monkeypatch.setattr(publisher, "_role_name", lambda _member: "Watch Brother")
    monkeypatch.setattr(publisher, "_company", lambda _member: None)
    monkeypatch.setattr(publisher, "_stats", lambda _member: {})
    monkeypatch.setattr(publisher, "_service_studs", lambda _member: (0, "—"))
    monkeypatch.setattr(publisher, "_display_name", lambda _member: "Brother")
    monkeypatch.setattr(publisher, "_chapter", lambda _member: None)
    monkeypatch.setattr(publisher, "_is_equerry", lambda _member: False)
    monkeypatch.setattr(publisher, "_joined_at", lambda _member: None)
    monkeypatch.setattr(publisher, "_recent_aar_teammates", lambda _member: [])
    monkeypatch.setattr(publisher, "_awards", lambda _member: [])
    snapshot = publisher.build_snapshot(SimpleNamespace())

    assert snapshot["members"][0]["oathSeal"] is None


def _member_with_roles(*names):
    by_name = {name: role_id for role_id, name, _ in publisher.CHALLENGE_ROLES}
    return SimpleNamespace(roles=[SimpleNamespace(id=by_name[name], name=name) for name in names])


def test_awards_map_to_ribbon_files_in_precedence_order():
    awards = publisher._awards(_member_with_roles("Ardent Raider Ribbon", "The Order Omega", "White Hand of Death"))
    assert [a["ribbon"] for a in awards] == [
        "1-Order Omega.png", "3-Clandestine Ops Medal.png", "14-Ardent Raider Ribbon.png",
    ]


def test_awards_distinguished_supersedes_base():
    awards = publisher._awards(_member_with_roles(
        "Kadaku Campaign Medal", "Distinguished Kadaku Campaign Medal",
        "Herisor Defense Medal", "Distinguished Herisor Defense Medal with Valor",
    ))
    assert [a["name"] for a in awards] == [
        "Distinguished Herisor Defense Medal with Valor", "Distinguished Kadaku Campaign Medal",
    ]


def test_awards_terminus_slayer_counts_variants_and_master_overrides():
    three = publisher._awards(_member_with_roles(
        "Terminus Slayer (Assault)", "Terminus Slayer (Sniper)", "Terminus Slayer (Heavy)",
    ))
    assert three == [{"name": "Terminus Slayer (3rd Award)", "ribbon": "10b-Terminus Slayer 3rd Award.png"}]

    master = publisher._awards(_member_with_roles("Terminus Slayer (Assault)", "Master Terminus Slayer"))
    assert [a["ribbon"] for a in master] == ["10f-Master Terminus Slayer Medal.png"]


def test_every_ribbon_file_exists():
    ribbons_dir = Path(publisher.REFERENCE_DIR).parent / "assets" / "Ribbons"
    files = set(publisher.RIBBON_FILES.values()) | {f for _, f in publisher.TERMINUS_SLAYER_RIBBONS}
    assert all((ribbons_dir / name).is_file() for name in files)


def test_reach_graph_loads_reference_nodes_and_edges():
    graph = publisher._reach_graph()
    ids = {node["id"] for node in graph["nodes"]}

    assert {"Avarax", "Kadaku", "Demerium"} <= ids
    assert all(edge["source"] in ids and edge["target"] in ids for edge in graph["edges"])