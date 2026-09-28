import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from opscribe import strategium_publish as publisher


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


def test_is_equerry_matches_role_name():
    assert publisher._is_equerry(SimpleNamespace(roles=[SimpleNamespace(name="First Blade"), SimpleNamespace(name="High Command Equerry")]))
    assert not publisher._is_equerry(SimpleNamespace(roles=[SimpleNamespace(name="First Blade")]))


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


def _write_packages(tmp_path, monkeypatch, packages):
    (tmp_path / "target_packages.json").write_text(json.dumps({"packages": packages, "rep": 0.5}), encoding="utf-8")
    monkeypatch.setattr(publisher._g, "CONFIG", {"data_dir": str(tmp_path)})


def test_reach_directives_filters_statuses_and_history(tmp_path, monkeypatch):
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    _write_packages(tmp_path, monkeypatch, {
        "a": {"id": "a", "node": "Avarax", "status": "deployed"},
        "b": {"id": "b", "node": "Kadaku", "status": "pending_sgt"},
        "c": {"id": "c", "node": "Kadaku", "status": "completed", "completed_at": (now - timedelta(days=2)).isoformat()},
        "d": {"id": "d", "node": "Kadaku", "status": "completed", "completed_at": (now - timedelta(days=9)).isoformat()},
        "e": {"id": "e", "node": "Kadaku", "status": "lapsed", "deadline": (now - timedelta(days=1)).isoformat()},
        "f": {"id": "f", "status": "deployed"},
    })

    directives = {d["id"]: d for d in publisher._reach_directives(now)}

    assert set(directives) == {"a", "b", "c", "e"}
    assert directives["b"]["status"] == "distributed"


def test_reach_directives_strip_discord_references(tmp_path, monkeypatch):
    _write_packages(tmp_path, monkeypatch, {
        "a": {
            "id": "a", "node": "Avarax", "status": "recruiting", "mission_id": 1,
            "assigned_company": "Watch Company Secundus", "assigned_kt": "Kill Team Alpha",
            "signed_up": [111, 222], "assigned_specialist_ids": [222, 333],
            "assigned_captain_id": 999, "forum_thread_id": 5, "aar_link": "https://discord.com/x",
            "stratagems": {"core": [{"name": "Buffed Enemies", "type": "debuff"}, {"name": "Old Buff", "type": "buff"}],
                           "dynamic_positive": [{"name": "The Emperor Protects", "type": "buff"}]},
        },
    })

    [directive] = publisher._reach_directives()

    assert directive["participants"] == ["111", "222", "333"]
    assert directive["company"] == 2
    assert directive["mission"] == "Inferno"
    assert directive["stratagems"] == {"positive": ["The Emperor Protects"], "negative": ["Buffed Enemies"]}
    serialized = json.dumps(directive)
    assert "999" not in serialized and "forum" not in serialized and "discord.com" not in serialized


def test_reach_graph_loads_reference_nodes_and_edges():
    graph = publisher._reach_graph()
    ids = {node["id"] for node in graph["nodes"]}

    assert {"Avarax", "Kadaku", "Demerium"} <= ids
    assert all(edge["source"] in ids and edge["target"] in ids for edge in graph["edges"])