import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
from opscribe import api_bridge as bridge_mod
from opscribe.api_bridge import JerichoAPIBridge


class DummyRequest:
    def __init__(self, *, headers=None, content_type="application/json", body=None, match_info=None, query=None):
        self.headers = dict(headers or {})
        self.headers.setdefault("Content-Type", content_type)
        self.content_type = content_type
        self._body = {} if body is None else body
        self.match_info = match_info or {}
        self.query = query or {}

    async def json(self):
        return self._body


class DummyBot:
    def __init__(self):
        self.guilds = []

    def is_ready(self):
        return True

    def get_guild(self, _gid):
        return None


class DummyMember:
    def __init__(self, user_id, display_name):
        self.id = user_id
        self.display_name = display_name
        self.mention = f"<@{user_id}>"


class DummyMessage:
    def __init__(self, message_id=12345):
        self.id = message_id
        self.edits = []
        self.deleted = False

    async def edit(self, **kwargs):
        self.edits.append(kwargs)

    async def delete(self):
        self.deleted = True


class DummyChannel:
    def __init__(self, channel_id=999):
        self.id = channel_id
        self.sent = []
        self._messages = {}

    async def send(self, content=None, embed=None, allowed_mentions=None):
        message_id = 12345 + len(self.sent)
        msg = DummyMessage(message_id=message_id)
        self.sent.append(
            {
                "content": content,
                "embed": embed,
                "allowed_mentions": allowed_mentions,
                "message": msg,
            }
        )
        self._messages[message_id] = msg
        return msg

    async def fetch_message(self, message_id):
        return self._messages[message_id]


class DummyGuild:
    def __init__(self, channel, members):
        self._channel = channel
        self._members = {m.id: m for m in members}

    def get_channel(self, channel_id):
        if int(channel_id) == int(self._channel.id):
            return self._channel
        return None

    def get_member(self, user_id):
        return self._members.get(int(user_id))


def _mk_bridge(tmp_path, cfg_overrides=None):
    cfg = {
        "enabled": True,
        "public_base_url": "https://example.invalid",
        "oauth_client_id": "client-id",
        "oauth_redirect_path": "/v1/link/callback",
        "mission_default_expire_minutes": 30,
    }
    if cfg_overrides:
        cfg.update(cfg_overrides)
    bridge = JerichoAPIBridge(
        DummyBot(),
        cfg,
        logger=SimpleNamespace(info=lambda *a, **k: None, exception=lambda *a, **k: None),
    )
    bridge.state.path = str(tmp_path / "api_state.json")
    return bridge


def _json(resp):
    return json.loads(resp.text)


def test_health_returns_ready_and_version(tmp_path):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        resp = await bridge.handle_health(DummyRequest())
        payload = _json(resp)
        assert resp.status == 200
        assert payload["ok"] is True
        assert payload["ready"] is True
        assert payload["version"] >= 4

    asyncio.run(_run())


def test_link_start_rejects_non_empty_body(tmp_path):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        resp = await bridge.handle_link_start(DummyRequest(body={"x": 1}))
        payload = _json(resp)
        assert resp.status == 400
        assert payload["error"] == "invalid_body"

    asyncio.run(_run())


def test_link_start_returns_link_payload(tmp_path):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        resp = await bridge.handle_link_start(DummyRequest(body={}))
        payload = _json(resp)
        assert resp.status == 200
        assert payload["ok"] is True
        assert "link_id" in payload
        assert "authorize_url" in payload
        assert payload["expires_in"] == 300

    asyncio.run(_run())


def test_link_status_missing_returns_404(tmp_path):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        req = DummyRequest(match_info={"link_id": "does-not-exist"})
        resp = await bridge.handle_link_status(req)
        payload = _json(resp)
        assert resp.status == 404
        assert payload["error"] == "link_missing"

    asyncio.run(_run())


def test_link_callback_failed_attempt_is_retryable(tmp_path, monkeypatch):
    monkeypatch.delenv("DISCORD_OAUTH_CLIENT_SECRET", raising=False)
    bridge = _mk_bridge(tmp_path)

    async def _run():
        link = await bridge.state.create_link(
            public_base_url="https://example.invalid",
            redirect_path="/v1/link/callback",
            ttl_seconds=300,
        )
        req = DummyRequest(query={"code": "bad-code", "state": link["state"]})

        first = await bridge.handle_link_callback(req)
        second = await bridge.handle_link_callback(req)

        assert first.status == 503
        assert second.status == 503

    asyncio.run(_run())


def test_me_requires_bearer(tmp_path):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        resp = await bridge.handle_me(DummyRequest(headers={}))
        payload = _json(resp)
        assert resp.status == 401
        assert payload["error"] == "unauthorized"

    asyncio.run(_run())


def test_me_returns_user_for_valid_token(tmp_path):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        token = await bridge.state.issue_token_for_user(42, "Brother FortyTwo")
        bridge._resolve_member = lambda _uid: SimpleNamespace(id=42, display_name="Brother FortyTwo")
        req = DummyRequest(headers={"Authorization": f"Bearer {token}"})
        resp = await bridge.handle_me(req)
        payload = _json(resp)
        assert resp.status == 200
        assert payload["user"]["user_id"] == "42"
        assert payload["user"]["display_name"] == "Brother FortyTwo"

    asyncio.run(_run())


def test_missions_requires_bearer(tmp_path):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        resp = await bridge.handle_missions(DummyRequest(headers={}))
        payload = _json(resp)
        assert resp.status == 401
        assert payload["error"] == "unauthorized"

    asyncio.run(_run())


def test_missions_filters_non_omega_queues(tmp_path, monkeypatch):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        token = await bridge.state.issue_token_for_user(100, "Brother OneHundred")
        monkeypatch.setattr(
            bridge_mod,
            "_load_lfg_queues",
            lambda: {
                "1": {
                    "queue_type": "omega",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "expires_at": "2026-01-01T00:30:00+00:00",
                    "players": [{"user_id": 100, "platform": "pc"}],
                    "message": "omega run",
                    "initiation_trial": False,
                },
                "2": {
                    "queue_type": "operation",
                    "players": [{"user_id": 101, "platform": "pc"}],
                },
            },
        )
        req = DummyRequest(headers={"Authorization": f"Bearer {token}"})
        resp = await bridge.handle_missions(req)
        payload = _json(resp)
        assert resp.status == 200
        assert len(payload["missions"]) == 1
        assert payload["missions"][0]["queue_id"] == "1"

    asyncio.run(_run())


def test_missions_include_guild_display_name_for_each_player(tmp_path, monkeypatch):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        token = await bridge.state.issue_token_for_user(100, "Brother OneHundred")
        monkeypatch.setattr(
            bridge_mod,
            "_load_lfg_queues",
            lambda: {
                "1": {
                    "queue_type": "omega",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "expires_at": "2026-01-01T00:30:00+00:00",
                    "players": [{"user_id": 100, "platform": "pc"}],
                    "message": "omega run",
                    "initiation_trial": False,
                }
            },
        )
        guild = DummyGuild(channel=DummyChannel(), members=[DummyMember(100, "Brother Vigil")])
        bridge._resolve_guild = lambda: guild
        req = DummyRequest(headers={"Authorization": f"Bearer {token}"})
        resp = await bridge.handle_missions(req)
        payload = _json(resp)
        assert resp.status == 200
        assert payload["missions"][0]["players"][0]["display_name"] == "Brother Vigil"

    asyncio.run(_run())


def test_mission_start_rejects_non_integer_expiry(tmp_path):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        token = await bridge.state.issue_token_for_user(1001, "Brother Expiry")
        req = DummyRequest(
            headers={"Authorization": f"Bearer {token}"},
            body={"expire_minutes": "not-an-int"},
        )
        resp = await bridge.handle_mission_start(req)
        payload = _json(resp)
        assert resp.status == 400
        assert payload["error"] == "invalid_expiry"

    asyncio.run(_run())


def test_mission_start_rejects_non_boolean_initiation_trial(tmp_path):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        token = await bridge.state.issue_token_for_user(1002, "Brother Trial")
        req = DummyRequest(
            headers={"Authorization": f"Bearer {token}"},
            body={"initiation_trial": "yes"},
        )
        resp = await bridge.handle_mission_start(req)
        payload = _json(resp)
        assert resp.status == 400
        assert payload["error"] == "invalid_initiation_trial"

    asyncio.run(_run())


def test_mission_start_accepts_json_charset_content_type(tmp_path):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        token = await bridge.state.issue_token_for_user(1005, "Brother Charset")
        req = DummyRequest(
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            body={"expire_minutes": "abc"},
        )
        resp = await bridge.handle_mission_start(req)
        payload = _json(resp)
        assert resp.status == 400
        assert payload["error"] == "invalid_expiry"

    asyncio.run(_run())


def test_mission_join_missing_queue_returns_404(tmp_path, monkeypatch):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        token = await bridge.state.issue_token_for_user(1003, "Brother Join")
        bridge._resolve_member = lambda _uid: SimpleNamespace(id=1003, display_name="Brother Join")
        monkeypatch.setattr(bridge_mod, "_get_player_platform", lambda _member: "pc")
        monkeypatch.setattr(bridge_mod, "_load_lfg_queues", lambda: {})
        bridge_mod._g.LFG_QUEUE_LOCK = asyncio.Lock()

        req = DummyRequest(
            headers={"Authorization": f"Bearer {token}"},
            match_info={"queue_id": "99999"},
        )
        resp = await bridge.handle_mission_join(req)
        payload = _json(resp)
        assert resp.status == 404
        assert payload["error"] == "queue_missing"

    asyncio.run(_run())


def test_mission_leave_requires_bearer(tmp_path):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        req = DummyRequest(headers={}, match_info={"queue_id": "777"})
        resp = await bridge.handle_mission_leave(req)
        payload = _json(resp)
        assert resp.status == 401
        assert payload["error"] == "unauthorized"

    asyncio.run(_run())


def test_mission_leave_missing_queue_returns_404(tmp_path, monkeypatch):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        token = await bridge.state.issue_token_for_user(1013, "Brother Leave")
        bridge._resolve_member = lambda _uid: SimpleNamespace(id=1013, display_name="Brother Leave")
        monkeypatch.setattr(bridge_mod, "_load_lfg_queues", lambda: {})
        bridge_mod._g.LFG_QUEUE_LOCK = asyncio.Lock()

        req = DummyRequest(
            headers={"Authorization": f"Bearer {token}"},
            match_info={"queue_id": "777"},
        )
        resp = await bridge.handle_mission_leave(req)
        payload = _json(resp)
        assert resp.status == 404
        assert payload["error"] == "queue_missing"

    asyncio.run(_run())


def test_mission_leave_invalid_queue_id_returns_400(tmp_path):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        token = await bridge.state.issue_token_for_user(1014, "Brother Invalid Queue")
        req = DummyRequest(
            headers={"Authorization": f"Bearer {token}"},
            match_info={"queue_id": "not-a-number"},
        )
        resp = await bridge.handle_mission_leave(req)
        payload = _json(resp)
        assert resp.status == 400
        assert payload["error"] == "invalid_queue"

    asyncio.run(_run())


def test_mission_leave_not_in_queue_returns_409(tmp_path, monkeypatch):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        token = await bridge.state.issue_token_for_user(1015, "Brother NotQueued")
        member = DummyMember(1015, "Brother NotQueued")

        bridge._resolve_member = lambda _uid: member
        monkeypatch.setattr(
            bridge_mod,
            "_load_lfg_queues",
            lambda: {
                "777": {
                    "queue_type": "omega",
                    "players": [{"user_id": 2000, "platform": "pc"}],
                }
            },
        )
        bridge_mod._g.LFG_QUEUE_LOCK = asyncio.Lock()

        req = DummyRequest(
            headers={"Authorization": f"Bearer {token}"},
            match_info={"queue_id": "777"},
        )
        resp = await bridge.handle_mission_leave(req)
        payload = _json(resp)
        assert resp.status == 409
        assert payload["error"] == "not_in_queue"

    asyncio.run(_run())


def test_mission_leave_updates_queue_and_message(tmp_path, monkeypatch):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        token = await bridge.state.issue_token_for_user(6001, "Brother Leaver")
        leaver = DummyMember(6001, "Brother Leaver")
        other = DummyMember(6002, "Brother Other")
        channel = DummyChannel(channel_id=555)
        guild = DummyGuild(channel=channel, members=[leaver, other])
        msg = DummyMessage(message_id=777)
        channel._messages[777] = msg

        store = {
            "data": {
                "777": {
                    "queue_type": "omega",
                    "creator_id": other.id,
                    "channel_id": channel.id,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "expires_at": "2026-01-01T00:30:00+00:00",
                    "players": [
                        {"user_id": leaver.id, "platform": "pc"},
                        {"user_id": other.id, "platform": "console"},
                    ],
                },
            }
        }

        def _load():
            return dict(store["data"])

        def _save(data):
            store["data"] = dict(data)

        bridge._resolve_member = lambda _uid: leaver
        bridge._resolve_guild = lambda: guild
        monkeypatch.setattr(bridge_mod, "_load_lfg_queues", _load)
        monkeypatch.setattr(bridge_mod, "_save_lfg_queues", _save)
        monkeypatch.setattr(bridge_mod, "_build_lfg_embed", lambda _q, _g: {"embed": True})
        monkeypatch.setattr(bridge_mod, "LFGQueueView", lambda _qid: "view")
        bridge_mod._g.LFG_QUEUE_LOCK = asyncio.Lock()
        bridge_mod._g.LFG_ACTIVE_QUEUES = {}

        req = DummyRequest(
            headers={"Authorization": f"Bearer {token}"},
            match_info={"queue_id": "777"},
        )
        resp = await bridge.handle_mission_leave(req)
        payload = _json(resp)

        assert resp.status == 200
        assert payload["ok"] is True
        assert payload["closed"] is False
        assert payload["mission"]["queue_id"] == "777"
        assert len(payload["mission"]["players"]) == 1
        assert payload["mission"]["players"][0]["user_id"] == "6002"
        assert len(store["data"]["777"]["players"]) == 1
        assert store["data"]["777"]["players"][0]["user_id"] == 6002
        assert msg.deleted is False
        assert msg.edits

    asyncio.run(_run())


def test_mission_leave_last_player_closes_queue(tmp_path, monkeypatch):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        token = await bridge.state.issue_token_for_user(6101, "Brother Last")
        last = DummyMember(6101, "Brother Last")
        channel = DummyChannel(channel_id=556)
        guild = DummyGuild(channel=channel, members=[last])
        msg = DummyMessage(message_id=778)
        channel._messages[778] = msg

        store = {
            "data": {
                "778": {
                    "queue_type": "omega",
                    "creator_id": last.id,
                    "channel_id": channel.id,
                    "players": [{"user_id": last.id, "platform": "pc"}],
                },
            }
        }

        def _load():
            return dict(store["data"])

        def _save(data):
            store["data"] = dict(data)

        bridge._resolve_member = lambda _uid: last
        bridge._resolve_guild = lambda: guild
        monkeypatch.setattr(bridge_mod, "_load_lfg_queues", _load)
        monkeypatch.setattr(bridge_mod, "_save_lfg_queues", _save)
        bridge_mod._g.LFG_QUEUE_LOCK = asyncio.Lock()
        bridge_mod._g.LFG_ACTIVE_QUEUES = {778: dict(store["data"]["778"])}

        req = DummyRequest(
            headers={"Authorization": f"Bearer {token}"},
            match_info={"queue_id": "778"},
        )
        resp = await bridge.handle_mission_leave(req)
        payload = _json(resp)

        assert resp.status == 200
        assert payload["ok"] is True
        assert payload["closed"] is True
        assert payload["mission"] is None
        assert "778" not in store["data"]
        assert 778 not in bridge_mod._g.LFG_ACTIVE_QUEUES
        assert msg.deleted is True

    asyncio.run(_run())


def test_mission_start_success_posts_queue_message(tmp_path, monkeypatch):
    bridge = _mk_bridge(tmp_path, cfg_overrides={"queue_channel_id": 999})

    async def _run():
        token = await bridge.state.issue_token_for_user(2001, "Brother Start")
        member = DummyMember(2001, "Brother Start")
        channel = DummyChannel(channel_id=999)
        guild = DummyGuild(channel=channel, members=[member])

        bridge._resolve_member = lambda _uid: member
        bridge._resolve_guild = lambda: guild

        monkeypatch.setattr(bridge_mod, "_get_player_platform", lambda _member: "pc")
        monkeypatch.setattr(bridge_mod, "_load_lfg_queues", lambda: {})
        monkeypatch.setattr(bridge_mod, "_save_lfg_queues", lambda _data: None)
        monkeypatch.setattr(bridge_mod, "_build_lfg_embed", lambda _q, _g: {"embed": True})
        monkeypatch.setattr(bridge_mod, "LFGQueueView", lambda _qid: "view")
        bridge_mod._g.LFG_QUEUE_LOCK = asyncio.Lock()
        bridge_mod._g.LFG_ACTIVE_QUEUES = {}

        req = DummyRequest(
            headers={"Authorization": f"Bearer {token}"},
            body={"message": "teaching run", "expire_minutes": 20, "initiation_trial": False},
        )
        resp = await bridge.handle_mission_start(req)
        payload = _json(resp)

        assert resp.status == 201
        assert payload["ok"] is True
        assert payload["mission"]["queue_type"] == "omega"
        assert len(channel.sent) == 1
        assert channel.sent[0]["message"].edits

    asyncio.run(_run())


def test_mission_join_full_queue_deletes_message(tmp_path, monkeypatch):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        token = await bridge.state.issue_token_for_user(3001, "Brother Joiner")
        creator = DummyMember(3002, "Brother Creator")
        joiner = DummyMember(3001, "Brother Joiner")
        channel = DummyChannel(channel_id=555)
        guild = DummyGuild(channel=channel, members=[creator, joiner])
        msg = DummyMessage(message_id=777)
        channel._messages[777] = msg

        queue_row = {
            "queue_type": "omega",
            "creator_id": creator.id,
            "channel_id": channel.id,
            "players": [
                {"user_id": 4001, "platform": "pc"},
                {"user_id": 4002, "platform": "pc"},
                {"user_id": 4003, "platform": "pc"},
                {"user_id": 4004, "platform": "console"},
            ],
        }

        bridge._resolve_member = lambda _uid: joiner
        bridge._resolve_guild = lambda: guild

        removed = {"called": False}

        async def _removed(_qid):
            removed["called"] = True

        monkeypatch.setattr(bridge_mod, "_get_player_platform", lambda _member: "pc")
        monkeypatch.setattr(
            bridge_mod,
            "_get_lfg_queue_types",
            lambda: {"omega": {"max_players": 5, "max_console": 2}},
        )
        monkeypatch.setattr(bridge_mod, "_load_lfg_queues", lambda: {"777": dict(queue_row)})
        monkeypatch.setattr(bridge_mod, "_save_lfg_queues", lambda _data: None)
        monkeypatch.setattr(bridge_mod, "_remove_lfg_queue_from_storage", _removed)
        monkeypatch.setattr(bridge_mod, "_queue_player_mentions", lambda _g, _p: "<@a>, <@b>")
        bridge_mod._g.LFG_QUEUE_LOCK = asyncio.Lock()
        bridge_mod._g.LFG_ACTIVE_QUEUES = {}

        req = DummyRequest(
            headers={"Authorization": f"Bearer {token}"},
            match_info={"queue_id": "777"},
        )
        resp = await bridge.handle_mission_join(req)
        payload = _json(resp)

        assert resp.status == 200
        assert payload["full"] is True
        assert msg.deleted is True
        assert removed["called"] is True

    asyncio.run(_run())


def test_unlink_revokes_token(tmp_path):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        token = await bridge.state.issue_token_for_user(404, "Brother Unlink")
        req = DummyRequest(headers={"Authorization": f"Bearer {token}"})
        resp = await bridge.handle_unlink(req)
        payload = _json(resp)
        assert resp.status == 200
        assert payload["unlinked"] is True
        assert await bridge.state.resolve_token(token) is None

    asyncio.run(_run())


def test_expired_token_is_rejected(tmp_path):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        token = await bridge.state.issue_token_for_user(5050, "Brother Expired")
        raw = bridge.state._load_unsafe()
        token_id = raw["issued_for_user"]["5050"]
        raw["tokens"][token_id]["expires_at"] = "2000-01-01T00:00:00+00:00"
        bridge.state._save_unsafe(raw)

        bridge._resolve_member = lambda _uid: SimpleNamespace(id=5050, display_name="Brother Expired")
        req = DummyRequest(headers={"Authorization": f"Bearer {token}"})
        resp = await bridge.handle_me(req)
        payload = _json(resp)
        assert resp.status == 401
        assert payload["error"] == "unauthorized"

    asyncio.run(_run())


class AARTestMember:
    def __init__(self, user_id, name):
        self.id = user_id
        self.name = name
        self.display_name = name
        self.nick = name
        self.mention = f"<@{user_id}>"
        self.bot = False
        self.roles = []


class AARTestGuild(DummyGuild):
    def __init__(self, channel, members):
        super().__init__(channel, members)
        self.id = 1429264578440597517

    def get_role(self, _role_id):
        return None


def test_web_aar_builder_creates_valid_canonical_record(tmp_path):
    bridge = _mk_bridge(tmp_path)
    channel = DummyChannel(channel_id=1429318686447108300)
    submitter = AARTestMember(101, "Brother One")
    teammate = AARTestMember(102, "Brother Two")
    guild = AARTestGuild(channel, [submitter, teammate])
    submission = {
        "mode": "ops_strat",
        "mission": "pve_inferno",
        "difficulty": "@Ruthless",
        "rank": "A",
        "brother_ids": ["101", "102"],
        "tags": [],
        "armory_data": 0,
        "gene_seed_status": "unknown",
    }

    async def _build():
        return bridge._build_web_aar_record(
            submission, guild, channel, submitter, 123456, bridge_mod._utcnow()
        )

    record, _view, participants = asyncio.run(_build())

    assert record["aar_id"] == 123456
    assert record["mission"] == "Inferno"
    assert record["difficulty_class"] == "ruthless_ops"
    assert record["points_for_op"] == 2
    assert record["brother_ids"] == ["101", "102"]
    assert record["submitter_id"] == "101"
    assert record["content"].startswith("++ MISSION REPORT ++")
    assert not record["content"].startswith("++ TEST MISSION REPORT ++")
    assert record["message_url"] == f"https://discord.com/channels/{guild.id}/{channel.id}/123456"
    assert len(participants) == 2


def test_web_aar_receipt_embeds_do_not_contain_ingest_marker(tmp_path):
    bridge = _mk_bridge(tmp_path)
    participants = [AARTestMember(101, "Brother One"), AARTestMember(102, "Brother Two")]
    view = SimpleNamespace(
        mission="Inferno",
        difficulty="@Ruthless",
        rank="A",
        tags=[],
        aar_type="pve",
        armory_data=0,
        mode_config={},
        gene_seed_status="unknown",
    )
    screenshots = [
        {"data": b"image", "content_type": "image/png"}
        for _ in range(10)
    ]

    embeds, files = bridge._web_aar_embeds(view, participants, screenshots, "key-1234567890")

    assert len(embeds) == 10
    assert len(files) == 10
    assert all("++ MISSION REPORT ++" not in (embed.description or "") for embed in embeds)
    assert "STRATEGIUM WEB AAR key-1234567890" in embeds[0].footer.text
    for file in files:
        file.close()


def _multipart_aar_body(image: bytes, *, content_type: str = "image/png", submission: dict | None = None, boundary: str = "aar-boundary") -> tuple[bytes, str]:
    submission_json = json.dumps(submission or {})
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"submission\"\r\nContent-Type: application/json\r\n\r\n{submission_json}\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"screenshots\"; filename=\"proof.png\"\r\nContent-Type: {content_type}\r\n\r\n".encode() + image + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def test_web_aar_multipart_parser_accepts_image_and_submission():
    image = bridge_mod.base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/pKcAAAAASUVORK5CYII="
    )
    body, content_type = _multipart_aar_body(image)

    submission, screenshots = bridge_mod._parse_web_aar_multipart(body, content_type)

    assert submission == {}
    assert len(screenshots) == 1
    assert screenshots[0]["filename"] == "proof.png"
    assert screenshots[0]["data"] == image


def test_web_aar_multipart_parser_rejects_mislabeled_image():
    body, content_type = _multipart_aar_body(b"not an image")

    try:
        bridge_mod._parse_web_aar_multipart(body, content_type)
    except ValueError as error:
        assert "valid PNG, JPEG, or WebP" in str(error)
    else:
        raise AssertionError("expected invalid image rejection")


def test_web_aar_intake_is_disabled_by_default(tmp_path):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        response = await bridge.handle_web_aar_submission(DummyRequest())
        assert response.status == 503
        assert _json(response)["error"] == "not_configured"

    asyncio.run(_run())


def test_web_aar_staff_access_and_explicit_all_members_mode(tmp_path):
    bridge = _mk_bridge(tmp_path)
    member = SimpleNamespace(bot=False, roles=[SimpleNamespace(id=1, name="Watch Brother")])
    assert not bridge._web_aar_member_allowed(member)
    member.roles = [SimpleNamespace(id=bridge_mod.HIGH_COMMAND_ROLE_ID, name="High Command")]
    assert bridge._web_aar_member_allowed(member)
    member.roles = [SimpleNamespace(id=2, name="Watch Techmarine")]
    assert bridge._web_aar_member_allowed(member)
    member.roles = []
    bridge.config["web_submission"] = {"access_mode": "members"}
    assert bridge._web_aar_member_allowed(member)
    member.bot = True
    assert not bridge._web_aar_member_allowed(member)
    member.bot = False
    bridge.config["web_submission"]["access_mode"] = "invalid"
    assert not bridge._web_aar_member_allowed(member)


def test_web_aar_upload_limit_does_not_expand_regular_api_body_limit(tmp_path):
    bridge = _mk_bridge(tmp_path)

    regular_limit = bridge.config.get("max_body_bytes") or bridge_mod.DEFAULT_MAX_BODY_BYTES
    assert bridge.app._client_max_size == regular_limit
    assert bridge.web_aar_app._client_max_size == bridge_mod.MAX_WEB_AAR_REQUEST_BYTES


def test_web_aar_upload_budget_bounds_inflight_bytes_and_count(tmp_path):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        assert await bridge._reserve_web_aar_upload(32 * 1024 * 1024)
        assert not await bridge._reserve_web_aar_upload(17 * 1024 * 1024)
        assert await bridge._reserve_web_aar_upload(16 * 1024 * 1024)
        assert not await bridge._reserve_web_aar_upload(1)
        await bridge._release_web_aar_upload(32 * 1024 * 1024)
        assert await bridge._reserve_web_aar_upload(1)
        await bridge._release_web_aar_upload(16 * 1024 * 1024)
        await bridge._release_web_aar_upload(1)

    asyncio.run(_run())


def test_web_aar_journal_prunes_old_complete_and_cooldown_entries_but_keeps_pending():
    now = datetime(2026, 10, 6, tzinfo=timezone.utc)
    old = (now - timedelta(days=31)).isoformat()
    recent = (now - timedelta(days=1)).isoformat()
    journal = {
        "old_complete": {"status": "complete", "created_at": old},
        "recent_complete": {"status": "complete", "created_at": recent},
        "newest_complete": {"status": "complete", "created_at": now.isoformat()},
        "pending": {"status": "record_saved", "created_at": old},
        "_last_submission_by_user": {"101": int((now - timedelta(days=2)).timestamp())},
    }

    changed, pending_count = bridge_mod._prune_web_aar_submissions(
        journal,
        now=now,
        retention_days=30,
        cooldown_seconds=60,
        max_completed_entries=1,
    )

    assert changed
    assert "old_complete" not in journal
    assert "recent_complete" not in journal
    assert "newest_complete" in journal
    assert "pending" in journal
    assert journal["_last_submission_by_user"] == {}
    assert pending_count == 1


def test_web_aar_route_is_registered_but_disabled_by_default(tmp_path):
    bridge = _mk_bridge(tmp_path, {"host": "127.0.0.1", "port": 0})

    async def _run():
        await bridge.start()
        try:
            port = bridge.site._server.sockets[0].getsockname()[1]
            async with aiohttp.ClientSession() as session:
                async with session.post(f"http://127.0.0.1:{port}/v1/aar/submissions") as response:
                    payload = await response.json()
                    assert response.status == 503
                    assert payload["error"] == "not_configured"
        finally:
            await bridge.stop()

    asyncio.run(_run())


class MultipartAARRequest:
    def __init__(self, body, content_type, headers):
        self._body = body
        self.content_type = content_type
        self.content_length = len(body)
        self.headers = {"Content-Type": content_type, **headers}

    async def read(self):
        return self._body


def test_web_aar_access_requires_signature_and_returns_fresh_guild_name(tmp_path, monkeypatch):
    secret = "access-test-secret"
    monkeypatch.setenv("STRATEGIUM_BOT_AAR_SHARED_SECRET", secret)
    bridge = _mk_bridge(tmp_path)
    member = SimpleNamespace(bot=False, display_name="Watch Techmarine Jules", roles=[SimpleNamespace(id=1, name="Watch Techmarine")])
    bridge._fresh_web_member = AsyncMock(return_value=member)
    body = b'{"user_id":"101"}'
    timestamp = str(int(bridge_mod._utcnow().timestamp()))
    signed = f"{timestamp}\naccess-101\n101\n{bridge_mod.hashlib.sha256(body).hexdigest()}".encode()
    signature = bridge_mod.hmac.new(secret.encode(), signed, bridge_mod.hashlib.sha256).hexdigest()
    request = MultipartAARRequest(body, "application/json", {
        "X-Strategium-User-ID": "101", "X-Strategium-AAR-Timestamp": timestamp,
        "X-Strategium-AAR-Signature": "invalid",
    })

    async def _run():
        denied = await bridge.handle_web_aar_access(request)
        assert denied.status == 401
        bridge._fresh_web_member.assert_not_awaited()
        request.headers["X-Strategium-AAR-Signature"] = signature
        accepted = await bridge.handle_web_aar_access(request)
        assert _json(accepted) == {"allowed": True, "guild_member": True, "display_name": "Watch Techmarine Jules"}

    asyncio.run(_run())
    bridge._fresh_web_member.assert_awaited_once_with(101)


class AARReceiptChannel:
    def __init__(self, channel_id=1429318686447108300):
        self.id = channel_id
        self.sent = []
        self._messages = {}

    async def send(self, *, embeds, files, allowed_mentions):
        message_id = 1460000000000000000 + len(self.sent)
        attachments = [
            SimpleNamespace(filename=file.filename, url=f"https://cdn.discordapp.com/attachments/{message_id}/{file.filename}", size=12)
            for file in files
        ]
        message = SimpleNamespace(
            id=message_id,
            jump_url=f"https://discord.com/channels/1429264578440597517/{self.id}/{message_id}",
            created_at=bridge_mod._utcnow(),
            attachments=attachments,
            embeds=embeds,
        )
        self.sent.append({"embeds": embeds, "allowed_mentions": allowed_mentions, "message": message})
        self._messages[message_id] = message
        return message

    async def fetch_message(self, message_id):
        return self._messages[int(message_id)]

    async def history(self, limit=100):
        for message in list(self._messages.values())[-limit:]:
            yield message


class AtomicAARStore:
    def __init__(self):
        self.records = {}
        self.processed = set()

    async def set_record_and_processed_id(self, aar_id, record):
        key = str(aar_id)
        self.records[key] = record
        self.processed.add(key)


def test_web_aar_intake_posts_receipt_saves_once_and_processes_once(tmp_path, monkeypatch):
    from opscribe import aar_ops, loa_ops

    secret = "test-web-aar-secret"
    monkeypatch.setenv("STRATEGIUM_BOT_AAR_SHARED_SECRET", secret)
    monkeypatch.setattr(bridge_mod, "WEB_AAR_SUBMISSIONS_PATH", str(tmp_path / "web_submissions.json"))
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", AtomicAARStore(), raising=False)
    monkeypatch.setattr(aar_ops, "_resolve_aar_submission_channel", lambda _guild: channel)
    challenge_hook = AsyncMock(side_effect=[RuntimeError("transient test failure"), []])
    monkeypatch.setattr(aar_ops, "_process_challenge_tracking", challenge_hook)
    monkeypatch.setattr(loa_ops, "_get_active_loa", lambda _user_id: False)
    milestone_calls = []

    async def _milestones(member_ids, _guild):
        milestone_calls.append(member_ids)

    monkeypatch.setitem(__import__("sys").modules, "opscribe.bot", SimpleNamespace(_check_award_milestones_for_members=_milestones))
    channel = AARReceiptChannel()
    submitter = AARTestMember(101, "Brother One")
    teammate = AARTestMember(102, "Brother Two")
    guild = AARTestGuild(channel, [submitter, teammate])
    bridge = _mk_bridge(tmp_path, {"web_submission": {"enabled": True, "access_mode": "members"}})
    bridge._resolve_guild = lambda: guild
    bridge._resolve_member = lambda user_id: guild.get_member(user_id)
    submission = {
        "mode": "ops_strat", "mission": "pve_inferno", "difficulty": "@Ruthless", "rank": "A",
        "brother_ids": ["101", "102"], "tags": [], "armory_data": 0, "gene_seed_status": "unknown",
    }
    image = bridge_mod.base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/pKcAAAAASUVORK5CYII="
    )
    body, content_type = _multipart_aar_body(image, submission=submission)
    key = "retry-safe-key-123456789"
    timestamp = str(int(bridge_mod._utcnow().timestamp()))
    user_id = str(submitter.id)
    body_hash = bridge_mod.hashlib.sha256(body).hexdigest()
    signed = f"{timestamp}\n{key}\n{user_id}\n{body_hash}".encode("utf-8")
    signature = bridge_mod.hmac.new(secret.encode(), signed, bridge_mod.hashlib.sha256).hexdigest()
    request = MultipartAARRequest(body, content_type, {
        "X-Strategium-AAR-Timestamp": timestamp,
        "X-Strategium-AAR-Idempotency-Key": key,
        "X-Strategium-User-ID": user_id,
        "X-Strategium-AAR-Signature": signature,
    })
    retry_body, retry_content_type = _multipart_aar_body(image, submission=submission, boundary="retry-boundary")
    retry_timestamp = str(int(bridge_mod._utcnow().timestamp()))
    retry_hash = bridge_mod.hashlib.sha256(retry_body).hexdigest()
    retry_signed = f"{retry_timestamp}\n{key}\n{user_id}\n{retry_hash}".encode("utf-8")
    retry_signature = bridge_mod.hmac.new(secret.encode(), retry_signed, bridge_mod.hashlib.sha256).hexdigest()
    retry_request = MultipartAARRequest(retry_body, retry_content_type, {
        "X-Strategium-AAR-Timestamp": retry_timestamp,
        "X-Strategium-AAR-Idempotency-Key": key,
        "X-Strategium-User-ID": user_id,
        "X-Strategium-AAR-Signature": retry_signature,
    })
    limited_key = "second-submission-key-12345"
    limited_timestamp = str(int(bridge_mod._utcnow().timestamp()))
    limited_signed = f"{limited_timestamp}\n{limited_key}\n{user_id}\n{retry_hash}".encode("utf-8")
    limited_signature = bridge_mod.hmac.new(secret.encode(), limited_signed, bridge_mod.hashlib.sha256).hexdigest()
    limited_request = MultipartAARRequest(retry_body, retry_content_type, {
        "X-Strategium-AAR-Timestamp": limited_timestamp,
        "X-Strategium-AAR-Idempotency-Key": limited_key,
        "X-Strategium-User-ID": user_id,
        "X-Strategium-AAR-Signature": limited_signature,
    })

    async def _run():
        first = await bridge.handle_web_aar_submission(request)
        first_payload = _json(first)
        await bridge._retry_pending_web_aar_submissions()
        second = await bridge.handle_web_aar_submission(retry_request)
        second_payload = _json(second)
        limited = await bridge.handle_web_aar_submission(limited_request)
        bridge.config["web_submission"]["access_mode"] = "staff"
        denied = await bridge.handle_web_aar_submission(retry_request)
        assert denied.status == 403
        assert _json(denied)["error"] == "aar_access_denied"
        return first, first_payload, second_payload, limited

    response, first_payload, second_payload, limited_response = asyncio.run(_run())

    store = bridge_mod._g.DATASTORE
    assert response.status == 202
    assert first_payload["processing_pending"] is True
    assert first_payload["receipt_url"].endswith(str(first_payload["record_id"]))
    assert second_payload["duplicate"] is True
    assert limited_response.status == 429
    assert len(channel.sent) == 1
    assert str(first_payload["record_id"]) in store.records
    record = store.records[str(first_payload["record_id"])]
    assert record["source"] == "strategium_web"
    assert record["message_url"] == first_payload["receipt_url"]
    assert record["screenshots"][0]["url"].startswith("https://cdn.discordapp.com/")
    assert str(first_payload["record_id"]) in store.processed
    assert challenge_hook.await_count == 2
    assert len(milestone_calls) == 1
