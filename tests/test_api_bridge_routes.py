import asyncio
import json
from types import SimpleNamespace

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
    bridge = JerichoAPIBridge(DummyBot(), cfg, logger=SimpleNamespace(info=lambda *a, **k: None))
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


def test_link_callback_failed_attempt_is_retryable(tmp_path):
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
