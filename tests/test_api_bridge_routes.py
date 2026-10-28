import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
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
        self.user = SimpleNamespace(id=9000)

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


def test_api_bridge_uses_configured_guild_instead_of_first_joined_guild(tmp_path):
    jericho = SimpleNamespace(id=1429264578440597517)
    other_guild = SimpleNamespace(id=1)
    bridge = _mk_bridge(tmp_path, {"guild_id": jericho.id})
    bridge.bot.guilds = [other_guild, jericho]
    bridge.bot.get_guild = lambda guild_id: jericho if int(guild_id) == jericho.id else None

    assert bridge._resolve_guild() is jericho


def _json(resp):
    return json.loads(resp.text)


@pytest.mark.parametrize("actor_id", [1, 2])
def test_member_aar_correction_request_is_queued_without_editing_record(tmp_path, monkeypatch, actor_id):
    from opscribe import aar_ops
    bridge = _mk_bridge(tmp_path)
    record = {"source": "strategium_web", "submitter_id": "1", "brother_ids": ["2"], "mission": "Inferno", "web_submission": {"armory_data": 2}}
    mutations = []
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace(get_record=lambda identity: record))
    monkeypatch.setattr(bridge_mod, "_load_web_aar_mutations", lambda: mutations)
    monkeypatch.setattr(bridge_mod, "_save_web_aar_mutations", lambda payload: mutations.__setitem__(slice(None), payload))
    payload = {"record_id": "123", "submission": {"armory_data": 3}, "original_record_hash": bridge_mod.hashlib.sha256(json.dumps(record, sort_keys=True, default=str).encode()).hexdigest()}
    authorization = AsyncMock(return_value=(SimpleNamespace(id=actor_id, display_name="Brother Two"), payload))
    bridge._authorize_web_aar_management = authorization
    bridge._resolve_guild = lambda: SimpleNamespace()
    bridge._build_web_aar_record = lambda *args, **kwargs: None
    monkeypatch.setattr(aar_ops, "_resolve_aar_submission_channel", lambda guild: SimpleNamespace())
    response = asyncio.run(bridge.handle_web_aar_edit_request(DummyRequest()))
    assert response.status == 201
    assert record["mission"] == "Inferno"
    assert mutations[0]["operation"] == "edit_request"
    assert mutations[0]["status"] == "pending"
    assert mutations[0]["requester_id"] == str(actor_id)
    assert mutations[0]["submission"] == {"armory_data": 3}
    assert record["web_submission"] == {"armory_data": 2}
    assert authorization.await_args.kwargs["capability"] is None
    duplicate = asyncio.run(bridge.handle_web_aar_edit_request(DummyRequest()))
    assert _json(duplicate)["duplicate"] is True
    assert len(mutations) == 1
    payload["original_record_hash"] = "0" * 64
    assert asyncio.run(bridge.handle_web_aar_edit_request(DummyRequest())).status == 409


def test_member_cannot_request_correction_to_an_unrelated_aar(tmp_path, monkeypatch):
    bridge = _mk_bridge(tmp_path)
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace(get_record=lambda identity: {"source": "strategium_web", "submitter_id": "1", "brother_ids": ["1", "2"]}))
    bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=3, display_name="Unrelated"), {"record_id": "123", "submission": {"armory_data": 3}}))
    response = asyncio.run(bridge.handle_web_aar_edit_request(DummyRequest()))
    assert response.status == 403


def test_member_edit_rejects_nonwebsite_aar(tmp_path, monkeypatch):
    bridge = _mk_bridge(tmp_path)
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace(get_record=lambda identity: {"source": "discord", "submitter_id": "1"}))
    bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=1), {"record_id": "123", "submission": {"armory_data": 3}}))
    assert asyncio.run(bridge.handle_web_aar_edit_request(DummyRequest())).status == 404


@pytest.mark.parametrize(("actor_id", "source", "expected"), [(1, "strategium_web", 200), (2, "strategium_web", 200), (3, "strategium_web", 403), (1, "discord", 404)])
def test_member_record_requires_stored_ownership(tmp_path, monkeypatch, actor_id, source, expected):
    bridge = _mk_bridge(tmp_path)
    record = {"source": source, "submitter_id": "1", "brother_ids": ["2"], "web_submission": {"armory_data": 2}}
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace(get_record=lambda identity: record))
    monkeypatch.setattr(bridge_mod, "_load_web_aar_mutations", lambda: [])
    bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=actor_id), {}))
    response = asyncio.run(bridge.handle_web_aar_member_record(DummyRequest(match_info={"record_id": "123"})))
    assert response.status == expected
    assert bridge._authorize_web_aar_management.await_args.args[1:] == ("own-record", "123")
    assert bridge._authorize_web_aar_management.await_args.kwargs["capability"] is None
    if expected == 200:
        assert _json(response)["original_record_hash"]


@pytest.mark.parametrize(("actor_id", "source", "expected"), [(1, "strategium_web", 202), (2, "strategium_web", 202), (3, "strategium_web", 403), (1, "discord", 404)])
def test_member_deletion_request_requires_ownership_without_delete_authority(tmp_path, monkeypatch, actor_id, source, expected):
    bridge = _mk_bridge(tmp_path)
    record = {"source": source, "submitter_id": "1", "brother_ids": ["2"]}
    mutations = []
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace(get_record=lambda identity: record))
    monkeypatch.setattr(bridge_mod, "_load_web_aar_mutations", lambda: mutations)
    monkeypatch.setattr(bridge_mod, "_upsert_web_aar_mutation", lambda mutation: mutations.append(mutation))
    bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=actor_id), {"reason": "Duplicate website report"}))
    response = asyncio.run(bridge.handle_web_aar_member_delete_request(DummyRequest(match_info={"record_id": "123"})))
    assert response.status == expected
    if expected == 202:
        assert mutations[0]["status"] == "requested"
        assert "confirm_actor_id" not in mutations[0]
    else:
        assert mutations == []


def test_review_queue_pages_large_deletion_reasons_without_losing_items(tmp_path, monkeypatch):
    bridge = _mk_bridge(tmp_path)
    payload = {"queue_offset": 0}
    mutations = [{"operation": "delete", "record_id": str(1000 + index), "status": "requested", "reason": "\U0001f600" * 400} for index in range(30)]
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace(get_all_records=lambda: {}))
    monkeypatch.setattr(bridge_mod, "_load_web_aar_mutations", lambda: mutations)
    bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=9), payload))
    seen = []
    for offset in (0, 10, 20):
        payload["queue_offset"] = offset
        response = asyncio.run(bridge.handle_web_aar_management_list(DummyRequest()))
        page = _json(response)
        assert len(response.text.encode()) < 131072
        assert len(page["pending_deletions"]) == 10
        assert page["queue_total"] == 30
        assert page["queue_next_offset"] == offset + 10
        assert page["queue_has_more"] is (offset < 20)
        seen.extend(item["id"] for item in page["pending_deletions"])
    assert seen == [str(1000 + index) for index in range(30)]


def test_review_queue_enforces_byte_budget_and_reports_next_offset(tmp_path, monkeypatch):
    bridge = _mk_bridge(tmp_path)
    payload = {"queue_offset": 0}
    mutations = [{"operation": "delete", "record_id": str(1000 + index), "status": "requested", "reason": "x" * 100000} for index in range(5)]
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace(get_all_records=lambda: {}))
    monkeypatch.setattr(bridge_mod, "_load_web_aar_mutations", lambda: mutations)
    bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=9), payload))
    first = asyncio.run(bridge.handle_web_aar_management_list(DummyRequest()))
    assert len(first.text.encode()) < 480 * 1024
    assert _json(first)["queue_next_offset"] == 4
    payload["queue_offset"] = 4
    second = asyncio.run(bridge.handle_web_aar_management_list(DummyRequest()))
    assert _json(second)["pending_deletions"][0]["id"] == "1004"
    assert _json(second)["queue_has_more"] is False


def test_deletion_decisions_reject_stale_request_tokens(tmp_path, monkeypatch):
    bridge = _mk_bridge(tmp_path)
    entry = {"operation": "delete", "request_id": "delete-123", "record_id": "123", "status": "requested", "request_token": "new-request"}
    monkeypatch.setattr(bridge_mod, "_load_web_aar_mutations", lambda: [entry])
    bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=9), {"note": "Keep the valid report", "request_token": "old-request"}))
    request = DummyRequest(match_info={"record_id": "123"})
    for handler in (bridge.handle_web_aar_delete_confirm, bridge.handle_web_aar_delete_reject):
        response = asyncio.run(handler(request))
        assert response.status == 409
        assert _json(response)["error"] == "stale_deletion_request"
    assert entry["status"] == "requested"


@pytest.mark.parametrize(("count", "expected"), [(10, 429), (1000, 503)])
def test_member_deletion_request_quotas_are_enforced(tmp_path, monkeypatch, count, expected):
    bridge = _mk_bridge(tmp_path)
    mutations = [{"operation": "delete", "request_id": f"delete-{1000 + index}", "status": "requested", "request_actor_id": "2" if count == 10 else "3"} for index in range(count)]
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace(get_record=lambda identity: {"source": "strategium_web", "submitter_id": "2"}))
    monkeypatch.setattr(bridge_mod, "_load_web_aar_mutations", lambda: mutations)
    bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=2), {"reason": "Duplicate website report"}))
    response = asyncio.run(bridge.handle_web_aar_member_delete_request(DummyRequest(match_info={"record_id": "123"})))
    assert response.status == expected
    assert len(mutations) == count


def test_staff_can_decline_deletion_and_unlock_record_with_retained_audit(tmp_path, monkeypatch):
    bridge = _mk_bridge(tmp_path)
    record = {"source": "strategium_web", "submitter_id": "2", "web_submission": {"armory_data": 2}}
    entry = {"operation": "delete", "request_id": "delete-123", "record_id": "123", "status": "requested", "reason": "Duplicate report", "request_actor_id": "2"}
    mutations = [entry]
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace(get_record=lambda identity: record))
    monkeypatch.setattr(bridge_mod, "_load_web_aar_mutations", lambda: mutations)
    def upsert(value):
        mutations[:] = [item for item in mutations if item.get("request_id") != value["request_id"]] + [value]
    monkeypatch.setattr(bridge_mod, "_upsert_web_aar_mutation", upsert)
    bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=9), {"note": "Valid report; keep the archive entry"}))
    request = DummyRequest(match_info={"record_id": "123"})
    assert asyncio.run(bridge.handle_web_aar_delete_reject(request)).status == 200
    assert entry["status"] == "cancelled"
    assert entry["declined_by"] == "9"
    assert entry["decline_reason"] == "Valid report; keep the archive entry"
    assert _json(asyncio.run(bridge.handle_web_aar_delete_reject(request)))["duplicate"] is True
    bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=2), {}))
    assert _json(asyncio.run(bridge.handle_web_aar_member_record(request)))["delete_request"] is None
    bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=2), {"reason": "New duplicate report assessment"}))
    assert asyncio.run(bridge.handle_web_aar_member_delete_request(request)).status == 202
    assert any(item.get("declined_by") == "9" and item["status"] == "cancelled" for item in mutations)
    assert next(item for item in mutations if item["request_id"] == "delete-123")["status"] == "requested"


@pytest.mark.parametrize("status", ["deleting", "receipt_deleted", "record_deleted", "reconciling", "complete"])
def test_deletion_decline_refuses_started_operations(tmp_path, monkeypatch, status):
    bridge = _mk_bridge(tmp_path)
    entry = {"operation": "delete", "request_id": "delete-123", "record_id": "123", "status": status}
    monkeypatch.setattr(bridge_mod, "_load_web_aar_mutations", lambda: [entry])
    bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=9), {"note": "Keep this report instead"}))
    assert asyncio.run(bridge.handle_web_aar_delete_reject(DummyRequest(match_info={"record_id": "123"}))).status == 409
    assert entry["status"] == status


def test_deletion_decline_requires_editor_authorization(tmp_path):
    bridge = _mk_bridge(tmp_path)
    bridge._authorize_web_aar_management = AsyncMock(return_value=bridge_mod._json_error("forbidden", "Staff required", 403))
    assert asyncio.run(bridge.handle_web_aar_delete_reject(DummyRequest(match_info={"record_id": "123"}))).status == 403


@pytest.mark.parametrize(("kind", "identity"), [("edit", "a" * 24), ("delete", "b" * 24), ("recovery", "proposal-recovery-123456")])
def test_staff_detail_resolves_exact_review_outside_queue_page(tmp_path, monkeypatch, kind, identity):
    bridge = _mk_bridge(tmp_path)
    record = {"source": "strategium_web", "web_submission": {"mode": "ops_strat", "mission": "pve_inferno"}}
    mutation = {"record_id": "123", "operation": {"edit": "edit_request", "delete": "delete", "recovery": "edit"}[kind], "request_id": "delete-123" if kind == "delete" else identity, "request_token": identity if kind == "delete" else None, "status": "requested" if kind == "delete" else "pending", "submission": {"armory_data": 3}}
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace(get_record=lambda key: record))
    monkeypatch.setattr(bridge_mod, "_load_web_aar_mutations", lambda: [mutation])
    payload = {"review_type": kind, "review_id": identity}
    bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=9), payload))
    request = DummyRequest(match_info={"record_id": "123"})
    response = asyncio.run(bridge.handle_web_aar_management_detail(request))
    assert response.status == 200
    assert _json(response)["review"]["kind"] == kind
    assert _json(response)["review"]["record_id"] == "123"
    mutation["record_id"] = "999"
    assert asyncio.run(bridge.handle_web_aar_management_detail(request)).status == 404


def test_deleted_record_review_retains_its_recovery_context(tmp_path, monkeypatch):
    bridge = _mk_bridge(tmp_path)
    token = "d" * 24
    mutation = {"operation": "delete", "record_id": "123", "request_id": "delete-123", "request_token": token, "status": "record_deleted", "reason": "Duplicate report"}
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace(get_record=lambda key: None))
    monkeypatch.setattr(bridge_mod, "_load_web_aar_mutations", lambda: [mutation])
    bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=9), {"review_type": "delete", "review_id": token}))
    response = asyncio.run(bridge.handle_web_aar_management_detail(DummyRequest(match_info={"record_id": "123"})))
    assert response.status == 200
    assert _json(response)["record_removed"] is True
    assert _json(response)["delete_request"]["request_token"] == token


def test_member_detail_does_not_expose_staff_review_metadata(tmp_path, monkeypatch):
    bridge = _mk_bridge(tmp_path)
    record = {"source": "strategium_web", "submitter_id": "2", "web_submission": {"armory_data": 2}}
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace(get_record=lambda key: record))
    monkeypatch.setattr(bridge_mod, "_load_web_aar_mutations", lambda: [{"record_id": "123", "operation": "edit_request", "request_id": "a" * 24, "status": "pending"}])
    bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=2), {"review_type": "edit", "review_id": "a" * 24}))
    response = asyncio.run(bridge.handle_web_aar_member_record(DummyRequest(match_info={"record_id": "123"})))
    assert response.status == 200
    assert "review" not in _json(response)


def test_review_kind_filter_is_applied_before_pagination(tmp_path, monkeypatch):
    bridge = _mk_bridge(tmp_path)
    payload = {"queue_kind": "delete", "queue_offset": 20}
    mutations = [{"operation": "delete", "record_id": str(1000 + index), "status": "requested"} for index in range(23)]
    mutations.append({"operation": "edit", "record_id": "123", "request_id": "recovery", "status": "archive_saved"})
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace(get_all_records=lambda: {}))
    monkeypatch.setattr(bridge_mod, "_load_web_aar_mutations", lambda: mutations)
    bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=9), payload))
    response = asyncio.run(bridge.handle_web_aar_management_list(DummyRequest()))
    result = _json(response)
    assert [item["id"] for item in result["pending_deletions"]] == ["1020", "1021", "1022"]
    assert result["pending_edits"] == []
    assert result["queue_total"] == 23
    assert result["queue_counts"]["recovery"] == 1
    payload.update(queue_kind="requests", queue_offset=0)
    normal = _json(asyncio.run(bridge.handle_web_aar_management_list(DummyRequest())))
    assert normal["pending_edits"] == []
    assert normal["reconciliation_reviews"] == []
    assert normal["queue_total"] == 23
    payload["queue_kind"] = "invalid"
    assert asyncio.run(bridge.handle_web_aar_management_list(DummyRequest())).status == 400


@pytest.mark.parametrize("query", [
    "black laurels", "black_laurels", "101", "123", "watch member",
    "watch ruthless", "siege black laurels", "secured", "2/123",
])
def test_match_history_searches_structured_fields_participants_and_tags(tmp_path, monkeypatch, query):
    bridge = _mk_bridge(tmp_path)
    record = {
        "source": "strategium_web", "mission": "Siege", "difficulty": "Ruthless",
        "timestamp": "2026-10-22T12:00:00Z", "brother_names": ["Watch Member"],
        "brother_ids": ["101"], "receipt_url": "https://discord.com/channels/1/2/123",
        "web_submission": {
            "mode": "siege", "tags": ["black_laurels"], "waves": 5,
            "rank": "Brother", "gene_seed_status": "secured",
        },
    }
    records = {"123": record, "456": {"source": "discord", "mission": "Siege"}}
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace(get_all_records=lambda: records))
    monkeypatch.setattr(bridge_mod, "_load_web_aar_mutations", lambda: [])
    bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=9), {"query": query}))

    result = _json(asyncio.run(bridge.handle_web_aar_management_list(DummyRequest())))

    assert result["total"] == 1
    assert result["records"][0]["id"] == "123"
    assert result["records"][0]["tags"] == ["black_laurels"]
    assert result["records"][0]["tag_labels"] == ["Black Laurels"]
    assert result["records"][0]["mode"] == "siege"
    assert result["records"][0]["mode_label"] == "Siege"
    assert result["records"][0]["brother_ids"] == ["101"]
    assert result["records"][0]["waves"] == 5


def test_match_history_filters_before_pagination_and_requires_all_search_terms(tmp_path, monkeypatch):
    bridge = _mk_bridge(tmp_path)
    records = {
        str(index): {
            "source": "strategium_web", "mission": "Inferno", "difficulty": "Ruthless",
            "timestamp": f"2026-10-{index:02}T12:00:00Z",
            "web_submission": {"tags": ["black_laurels"] if index == 1 else []},
        }
        for index in range(1, 29)
    }
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace(get_all_records=lambda: records))
    monkeypatch.setattr(bridge_mod, "_load_web_aar_mutations", lambda: [])
    payload = {"query": "inferno black laurels"}
    bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=9), payload))
    result = _json(asyncio.run(bridge.handle_web_aar_management_list(DummyRequest())))
    assert [item["id"] for item in result["records"]] == ["1"]
    assert result["total"] == 1
    assert result["has_more"] is False
    payload["query"] = "inferno no-such-participant"
    assert _json(asyncio.run(bridge.handle_web_aar_management_list(DummyRequest())))["total"] == 0


def test_review_tickets_are_ordered_oldest_first_across_kinds_before_pagination(tmp_path, monkeypatch):
    bridge = _mk_bridge(tmp_path)
    mutations = [{
        "operation": "edit_request", "request_id": f"{index:024x}", "record_id": str(index),
        "status": "pending", "requested_at": f"2026-10-{index:02}T00:00:00Z",
        "requester_id": "9", "requested_changes": "waves",
    } for index in range(2, 12)]
    mutations.append({
        "operation": "delete", "record_id": "1", "status": "requested",
        "requested_at": "2026-10-01T00:00:00Z",
    })
    records = {str(index): {"source": "strategium_web"} for index in range(1, 12)}
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace(get_all_records=lambda: records))
    monkeypatch.setattr(bridge_mod, "_load_web_aar_mutations", lambda: mutations)
    bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=9), {"queue_kind": "requests"}))

    result = _json(asyncio.run(bridge.handle_web_aar_management_list(DummyRequest())))
    assert [item["id"] for item in result["pending_deletions"]] == ["1"]
    assert [item["record_id"] for item in result["edit_requests"]] == [str(index) for index in range(2, 11)]
    assert result["queue_total"] == 11
    assert result["queue_next_offset"] == 10
    assert result["queue_has_more"] is True


def test_award_reviews_are_unique_per_edit_and_acknowledge_independently(tmp_path, monkeypatch):
    bridge = _mk_bridge(tmp_path)
    member = SimpleNamespace(display_name="Forge Master Jules")
    guild = SimpleNamespace(get_member=lambda user_id: member if int(user_id) == 55 else None)
    mutations = [{
        "request_id": f"edit-{index}", "operation": "edit", "record_id": "123",
        "status": "complete", "actor_id": "99",
        "reconciliation": {"review_required": [{"user_id": "55", "challenge": "Black Laurels"}]},
    } for index in range(3)]
    record = {"source": "strategium_web", "submitter_id": "99", "mission": "Inferno", "brother_ids": ["55"], "brother_names": ["Forge Master Jules"], "web_submission": {"mode": "ops_strat", "mission": "pve_inferno"}}
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace(get_all_records=lambda: {"123": record}, get_record=lambda identity: record))
    monkeypatch.setattr(bridge_mod, "_load_web_aar_mutations", lambda: mutations)

    def upsert(updated):
        existing = next(item for item in mutations if item["request_id"] == updated["request_id"])
        existing.update(updated)

    monkeypatch.setattr(bridge_mod, "_upsert_web_aar_mutation", upsert)
    bridge._resolve_guild = lambda: guild
    bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=9), {"queue_kind": "recovery", "queue_offset": 0}))

    async def run():
        response = await bridge.handle_web_aar_management_list(DummyRequest())
        reviews = _json(response)["reconciliation_reviews"]
        identities = [item["review_id"] for item in reviews]
        assert len(set(identities)) == 3
        assert all(item["member_name"] == "Forge Master Jules" for item in reviews)
        bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=9), {"review_type": "award", "review_id": identities[1]}))
        detail = await bridge.handle_web_aar_management_detail(DummyRequest(match_info={"record_id": "123"}))
        assert _json(detail)["review"]["member_name"] == "Forge Master Jules"
        assert _json(detail)["review"]["challenge"] == "Black Laurels"
        bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=9), {"note": "Reviewed this member award history"}))
        for identity in identities:
            request = DummyRequest(match_info={"record_id": "123", "review_id": identity})
            assert (await bridge.handle_web_aar_reconciliation_acknowledge(request)).status == 200
        assert all(item["reconciliation"]["review_required"][0].get("acknowledged_by") == "9" for item in mutations)

    asyncio.run(run())


def test_staff_must_save_correction_before_resolving_request(tmp_path, monkeypatch):
    import hashlib
    bridge = _mk_bridge(tmp_path)
    record = {"mission": "Inferno", "armory_data": 2}
    identity = "a" * 24
    request = {"request_id": identity, "operation": "edit_request", "record_id": "123", "status": "pending", "original_record_hash": hashlib.sha256(json.dumps(record, sort_keys=True, default=str).encode()).hexdigest()}
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace(get_record=lambda key: record))
    monkeypatch.setattr(bridge_mod, "_load_web_aar_mutations", lambda: [request])
    monkeypatch.setattr(bridge_mod, "_save_web_aar_mutations", lambda payload: None)
    bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=9), {"note": "Corrected armory data"}))
    req = DummyRequest(match_info={"request_id": identity, "decision": "resolve"})
    assert asyncio.run(bridge.handle_web_aar_edit_request_decision(req)).status == 409
    record["armory_data"] = 3
    assert asyncio.run(bridge.handle_web_aar_edit_request_decision(req)).status == 200
    assert request["status"] == "resolved"
    assert request["reviewed_by"] == "9"
    assert request["editor_note"] == "Corrected armory data"


def test_edit_request_decision_uses_existing_editor_authorization(tmp_path):
    bridge = _mk_bridge(tmp_path)
    bridge._authorize_web_aar_management = AsyncMock(return_value=bridge_mod._json_error("forbidden", "Editor permission required", 403))
    response = asyncio.run(bridge.handle_web_aar_edit_request_decision(DummyRequest(match_info={"request_id": "a" * 24, "decision": "reject"})))
    assert response.status == 403
    assert "capability" not in bridge._authorize_web_aar_management.await_args.kwargs


def test_war_vanilla_pool_uses_hard_recipes_and_preserves_omega_exclusions():
    from opscribe import galactic_war_runtime as war
    from opscribe import aar_ops

    operations = war.vanilla_operations()
    assert {operation["mode"] for operation in operations} == {"ops_strat", "omega"}
    assert {operation["capacity"] for operation in operations} == {3, 5}
    for operation in operations:
        assert operation["mission_value"] in {option.value for option in aar_ops._mission_options_for_mode(operation["mode"])}
        assert operation["difficulty"] in {option.value for option in aar_ops._difficulty_options_for_mode(operation["mode"])}
        assert operation["difficulty_class"] == aar_ops.classify_difficulty(operation["difficulty"])
        assert operation["mission"] != "Siege"
        if operation["mode"] == "omega":
            assert operation["mission"] not in {"Reliquary", "Fall of Atreus", "Obelisk", "Vortex"}
        else:
            assert operation["difficulty_class"] == "hard_stratagem"
    assert any(operation["mission"] == "Reclamation" for operation in operations)
    assert {"Reliquary", "Fall of Atreus", "Obelisk", "Vortex"}.issubset({operation["mission"] for operation in operations if operation["mode"] == "ops_strat"})


def test_approved_strike_review_archive_recovers_after_failure(tmp_path, monkeypatch):
    from opscribe import aar_ops, galactic_war_runtime as war
    from opscribe.galactic_war_store import GalacticWarStore

    store = GalacticWarStore(tmp_path / "war.sqlite3", {}, [])
    with store._connection() as connection:
        state = store._load(connection)
        state["reviews"]["strike"] = {"status": "approved", "archive_pending": True, "record": {"aar_id": "record"}}
        store._save(connection, state)
    saver = AsyncMock(side_effect=[OSError("Archive unavailable"), None])
    monkeypatch.setattr(aar_ops, "save_aar_record", saver)
    monkeypatch.setattr(aar_ops, "_process_challenge_tracking", AsyncMock(return_value=[]))
    monkeypatch.setattr(war._g, "DATASTORE", SimpleNamespace(flush=AsyncMock()))
    monkeypatch.setattr(war._g, "logger", SimpleNamespace(exception=lambda *args, **kwargs: None))
    asyncio.run(war.recover_review_archives(SimpleNamespace(), store))
    assert store.snapshot(bridge_mod._utcnow())["reviews"]["strike"]["archive_pending"] is True
    asyncio.run(war.recover_review_archives(SimpleNamespace(), store))
    assert store.snapshot(bridge_mod._utcnow())["reviews"]["strike"]["archive_pending"] is False
    assert saver.await_count == 2


def test_real_war_reference_can_generate_both_pools_for_every_planet():
    from opscribe import galactic_war_runtime as war
    store = war.get_store()
    assert len(store.geography["sectors"]) == 29
    assert len(store.geography["planets"]) == 473
    assert all(store.geography["neighbors"][sector["id"]] for sector in store.geography["sectors"])
    for planet in store.geography["planets"]:
        for pool, capacity in (("vanilla", 3), ("omega", 5)):
            offer = store._offer("preflight-only", planet, 0, 0, pool=pool)
            assert offer["pool"] == pool
            assert offer["capacity"] == capacity
            assert offer["content_kind"] == "vanilla"


def test_evidence_review_hold_cannot_write_ordinary_aar_rewards(monkeypatch):
    from opscribe import aar_ops, galactic_war_runtime as war
    from opscribe.galactic_war import WarReportPending
    archive = AsyncMock()
    store = SimpleNamespace(validate_report=lambda *args: None, submit_report=lambda *args: (_ for _ in ()).throw(WarReportPending("Independent review required")))
    monkeypatch.setattr(war, "get_store", lambda: store)
    monkeypatch.setattr(war._g, "DATASTORE", SimpleNamespace(get_record=lambda key: None, set_record_and_processed_id=archive))
    with pytest.raises(WarReportPending):
        asyncio.run(aar_ops.save_aar_record({"aar_id": "record", "strike_id": "strike", "strike_success": True, "brother_ids": ["1", "2", "3"]}))
    archive.assert_not_awaited()


def test_aar_edit_builder_cannot_silently_unlink_a_strike(tmp_path, monkeypatch):
    bridge = _mk_bridge(tmp_path)
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace(get_record=lambda key: {"strike_id": "GW-original"}))
    with pytest.raises(ValueError, match="cannot be removed"):
        bridge._build_web_aar_record({}, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), 123456, bridge_mod._utcnow(), require_submitter=False)


def test_linked_aar_deletion_requires_audited_credit_reversal_first(tmp_path, monkeypatch):
    from opscribe import galactic_war_runtime as war
    bridge = _mk_bridge(tmp_path)
    bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=42), {}))
    monkeypatch.setattr(bridge_mod, "_load_web_aar_mutations", lambda: [{"request_id": "delete-123", "status": "requested"}])
    delete = AsyncMock()
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace(get_record=lambda key: {"source": "strategium_web", "strike_id": "strike"}, delete_record_and_processed_id=delete))
    monkeypatch.setattr(war, "get_store", lambda: SimpleNamespace(snapshot=lambda now: {"settlements": {"strike": {"reversed": False}}}))
    response = asyncio.run(bridge._handle_web_aar_delete_confirm_locked(DummyRequest(match_info={"record_id": "123"})))
    assert response.status == 409
    assert _json(response)["error"] == "strike_credit_active"
    delete.assert_not_awaited()


def test_war_endpoint_rejects_unsigned_requests_before_membership_lookup(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGIUM_BOT_AAR_SHARED_SECRET", "war-test-secret")
    monkeypatch.delenv("STRATEGIUM_BOT_SHARED_SECRET", raising=False)
    bridge = _mk_bridge(tmp_path)
    bridge._fresh_web_member = AsyncMock(side_effect=AssertionError("Unsigned request reached member lookup"))
    body = b'{"action":"invade","subsector_id":"alpha"}'
    request = SimpleNamespace(
        headers={"X-Strategium-User-ID": "42", "X-Strategium-AAR-Timestamp": str(int(datetime.now(timezone.utc).timestamp()))},
        content_length=len(body), read=AsyncMock(return_value=body),
    )
    response = asyncio.run(bridge.handle_web_war(request))
    assert response.status == 401
    bridge._fresh_web_member.assert_not_called()


def test_war_endpoint_uses_authoritative_actor_not_browser_roles(tmp_path, monkeypatch):
    import hashlib
    import hmac
    from opscribe import galactic_war_runtime as war
    from opscribe.galactic_war import WarMember

    secret = "war-test-secret"
    monkeypatch.setenv("STRATEGIUM_BOT_AAR_SHARED_SECRET", secret)
    monkeypatch.delenv("STRATEGIUM_BOT_SHARED_SECRET", raising=False)
    bridge = _mk_bridge(tmp_path)
    bridge._fresh_web_member = AsyncMock(return_value=SimpleNamespace(id=42))
    actor = WarMember("42", frozenset({"Watch Brother"}), "1", "1-1")
    monkeypatch.setattr(war, "directory", lambda _guild: ({"42": actor}, {}, {}, []))
    captured = {}

    def execute(action, actual_actor, payload, members, now):
        captured["actor"] = actual_actor
        raise PermissionError("Only Watch Master or Forge Master may declare an invasion")

    monkeypatch.setattr(war, "get_store", lambda: SimpleNamespace(execute=execute))
    body = json.dumps({"action": "invade", "roles": ["Watch Master"], "version": 0}).encode()
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    signed = f"{timestamp}\nwar-42\n42\n{hashlib.sha256(body).hexdigest()}".encode()
    request = SimpleNamespace(headers={
        "X-Strategium-User-ID": "42", "X-Strategium-AAR-Timestamp": timestamp,
        "X-Strategium-AAR-Signature": hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest(),
    }, content_length=len(body), read=AsyncMock(return_value=body))
    response = asyncio.run(bridge.handle_web_war(request))
    assert response.status == 403
    assert captured["actor"] is actor


def test_signed_test_invasion_requests_recheck_roles_and_preserve_live_state(tmp_path, monkeypatch):
    import hashlib
    import hmac
    from dataclasses import replace
    from opscribe import galactic_war_runtime as war
    from opscribe.galactic_war import WarMember

    secret = "test-invasion-secret"
    monkeypatch.setenv("STRATEGIUM_BOT_AAR_SHARED_SECRET", secret)
    monkeypatch.delenv("STRATEGIUM_BOT_SHARED_SECRET", raising=False)
    monkeypatch.setattr(war._g, "CONFIG", {"data_dir": str(tmp_path)})
    war._reference_store.cache_clear()
    live = war.get_store()
    before = live.snapshot(bridge_mod._utcnow())
    actors = {"42": WarMember("42", frozenset({"Forgemaster"}))}
    monkeypatch.setattr(war, "directory", lambda guild: (actors, {}, {}, []))
    bridge = _mk_bridge(tmp_path)
    bridge._resolve_guild = lambda: SimpleNamespace(get_member=lambda identity: SimpleNamespace(display_name="Test Forge Master"))
    bridge._fresh_web_member = AsyncMock(return_value=SimpleNamespace(id=42))

    def request(payload):
        body = json.dumps(payload).encode()
        timestamp = str(int(datetime.now(timezone.utc).timestamp()))
        signed = f"{timestamp}\nwar-42\n42\n{hashlib.sha256(body).hexdigest()}".encode()
        req = SimpleNamespace(headers={
            "X-Strategium-User-ID": "42", "X-Strategium-AAR-Timestamp": timestamp,
            "X-Strategium-AAR-Signature": hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest(),
        }, content_length=len(body), read=AsyncMock(return_value=body))
        return asyncio.run(bridge.handle_web_war(req))

    try:
        response = request({"action": "start_test_invasion", "subsector_id": "sector_29"})
        assert response.status == 200
        session = _json(response)["sandbox"]["id"]
        assert _json(response)["sandbox"]["viewing"] is True
        assert request({"action": "snapshot", "sandbox_id": session}).status == 200
        actors["42"] = replace(actors["42"], roles=frozenset({"Watch Brother"}))
        assert request({"action": "snapshot", "sandbox_id": session, "roles": ["Watch Master"]}).status == 403
        assert request({"action": "start_test_invasion", "subsector_id": "sector_29"}).status == 403
        actors["42"] = replace(actors["42"], roles=frozenset({"Forgemaster"}))
        assert request({"action": "revert_test_invasion", "sandbox_id": session}).status == 200
        assert request({"action": "snapshot", "sandbox_id": session}).status == 409
        assert live.snapshot(bridge_mod._utcnow()) == before
    finally:
        war._reference_store.cache_clear()


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


@pytest.mark.parametrize("recipe_based", [False, True])
def test_vanilla_strike_locks_on_signup_and_settles_through_normal_aar(tmp_path, monkeypatch, recipe_based):
    from opscribe import aar_ops, galactic_war_runtime as war
    from opscribe.galactic_war import WarMember, WarReportPending
    from opscribe.galactic_war_store import GalacticWarStore

    monkeypatch.setattr("opscribe.galactic_war_store.specialist_requirement", lambda roll: "")
    bridge = _mk_bridge(tmp_path)
    now = bridge_mod._utcnow()
    geography = {"sectors": [{"id": "alpha"}], "planets": [{"id": "world", "system_id": "system", "subsector_id": "alpha", "world_type": "hive", "biome": "industrial"}]}
    store = GalacticWarStore(tmp_path / "war.sqlite3", geography, [
        {"id": "purge", "name": "Purgation"},
        {"id": "special", "name": "Support", "required_rank": "Watch Apothecary", "worlds": ["hive"]},
    ], [
        {"mode": "ops_strat", "capacity": 3, "mission": "Inferno", "mission_value": "pve_inferno", "difficulty": "@Ruthless", "difficulty_class": "ruthless_ops"},
        {"mode": "omega", "capacity": 5, "mission": "Inferno", "mission_value": "pve_inferno", "difficulty": "@Omega", "difficulty_class": "omega_ops"},
    ])
    if recipe_based:
        from pathlib import Path
        from opscribe.galactic_war_recipes import VanillaRecipes

        reference = Path(__file__).resolve().parents[1] / "reference"
        definition = json.loads((reference / "galactic_war_vanilla_recipes.json").read_text())
        definition["objectives"] = {"purge": definition["objectives"]["purge"], "special": definition["objectives"]["purge"]}
        for objective in store.catalogue:
            objective["settings"] = ["hive|industrial"]
        store.recipes = VanillaRecipes(
            definition, json.loads((reference / "operations.json").read_text()),
            json.loads((reference / "stratagems.json").read_text()), store.catalogue,
        )
        store.operations = war.vanilla_operations()
    members = {identity: WarMember(identity, frozenset({"Watch Brother"}), watch_brother=True) for identity in ("101", "102", "103")}
    store.execute("invade", WarMember("wm", frozenset({"Watch Master"})), {"version": 0, "subsector_id": "alpha"}, members, now)
    offer_id = store.snapshot(now)["boards"]["world"][0]["id"]
    for actor in members.values():
        store.execute("join", actor, {"version": store.snapshot(now)["version"], "strike_id": offer_id}, members, now)
    attempt_id = store.snapshot(now)["boards"]["world"][0]["attempt_id"]
    monkeypatch.setattr(war, "get_store", lambda: store)
    channel = DummyChannel(channel_id=1429318686447108300)
    brothers = [AARTestMember(int(identity), f"Brother {identity}") for identity in members]
    guild = AARTestGuild(channel, brothers)
    submission = {"mode": "ops_strat", "mission": "pve_inferno", "difficulty": "@Hard-Stratagem" if recipe_based else "@Ruthless", "rank": "A", "brother_ids": list(members), "tags": [], "armory_data": 0, "gene_seed_status": "unknown", "strike_id": attempt_id, "strike_success": True}
    submission.pop("strike_success")

    async def build_report(data):
        return bridge._build_web_aar_record(data, guild, channel, brothers[0], 123456, now)

    record, _view, _participants = asyncio.run(build_report(submission))
    assert record["strike_success"] is True
    with pytest.raises(ValueError, match="Only completed"):
        asyncio.run(build_report({**submission, "strike_success": False}))
    with pytest.raises(ValueError, match="mission or difficulty"):
        asyncio.run(build_report({**submission, "mission": "pve_decapitation"}))
    with pytest.raises(ValueError, match="mission or difficulty"):
        asyncio.run(build_report({**submission, "difficulty": "@Lethal"}))
    with pytest.raises(ValueError, match="signup roster"):
        asyncio.run(build_report({**submission, "brother_ids": ["101", "102"]}))
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace(get_record=lambda key: None, set_record_and_processed_id=AsyncMock()))
    if recipe_based:
        with pytest.raises(WarReportPending):
            asyncio.run(aar_ops.save_aar_record(record))
        bridge_mod._g.DATASTORE.set_record_and_processed_id.assert_not_awaited()
        assert not store.snapshot(now)["contributions"]
        reviewer = WarMember("reviewer", frozenset({"Watch Master"}))
        store.execute("review", reviewer, {
            "version": store.snapshot(now)["version"], "strike_id": attempt_id,
            "decision": "approve", "reason": "Selection and result evidence match the saved recipe",
        }, members, now)
    asyncio.run(aar_ops.save_aar_record(record))
    asyncio.run(aar_ops.save_aar_record(record))
    state = store.snapshot(bridge_mod._utcnow())
    assert len(state["settlements"]) == 1
    assert len(state["contributions"]) == 3
    assert state["boards"]["world"][0]["generation"] == 1


@pytest.mark.parametrize(
    ("mode", "mission", "difficulty", "inductee_ids", "expected_inductees"),
    [
        ("induction_ops_strat", "pve_inferno", "@Ruthless", ["103"], ["103"]),
        ("induction_ops_strat", "pve_inferno", "@Ruthless", ["102", "103"], ["102", "103"]),
        ("induction_siege", "siege_template", "@Hard-Siege", ["103"], ["103"]),
        ("induction_siege", "siege_template", "@Hard-Siege", ["102", "103"], ["102", "103"]),
        ("induction_omega", "pve_inferno", "@Omega", ["103"], ["103"]),
        ("induction_omega", "pve_inferno", "@Omega", ["102", "103"], ["102", "103"]),
    ],
)
def test_web_aar_builder_uses_explicit_inductees(
    tmp_path, mode, mission, difficulty, inductee_ids, expected_inductees
):
    from opscribe import aar_ops

    bridge = _mk_bridge(tmp_path)
    channel = DummyChannel(channel_id=1429318686447108300)
    members = [AARTestMember(101, "Brother One"), AARTestMember(102, "Brother Two"), AARTestMember(103, "Brother Three")]
    guild = AARTestGuild(channel, members)
    guild.get_role = lambda role_id: SimpleNamespace(
        id=role_id,
        name="Initiation Trial" if role_id == aar_ops._INITIATION_TRIAL_ROLE_ID else "Watch Command",
    )
    submission = {
        "mode": mode,
        "mission": mission,
        "difficulty": difficulty,
        "rank": "A",
        "brother_ids": ["101", "102", "103"],
        "inductee_ids": inductee_ids,
        "tags": [],
        "armory_data": 0,
        "waves": 15,
        "kia": 0,
        "gene_seed_status": "unknown",
    }

    async def _build():
        return bridge._build_web_aar_record(
            submission, guild, channel, members[0], 123457, bridge_mod._utcnow()
        )

    record, _view, _participants = asyncio.run(_build())

    assert record["initiation_trial"] is True
    assert record["initiate_ids"] == expected_inductees
    initiate_mentions = " ".join(f"<@{uid}>" for uid in expected_inductees)
    assert f"<@&1434942334914662501>: {initiate_mentions}" in record["content"]


@pytest.mark.parametrize(
    "inductee_ids",
    [None, [], ["101", "101"], ["999"], ["101", "102", "103"]],
)
def test_web_aar_builder_rejects_invalid_inductees(tmp_path, inductee_ids):
    bridge = _mk_bridge(tmp_path)
    channel = DummyChannel(channel_id=1429318686447108300)
    members = [AARTestMember(101, "Brother One"), AARTestMember(102, "Brother Two"), AARTestMember(103, "Brother Three")]
    guild = AARTestGuild(channel, members)
    submission = {
        "mode": "induction_ops_strat",
        "mission": "pve_inferno",
        "difficulty": "@Ruthless",
        "rank": "A",
        "brother_ids": ["101", "102", "103"],
        "inductee_ids": inductee_ids,
        "tags": [],
        "armory_data": 0,
        "gene_seed_status": "unknown",
    }

    with pytest.raises(ValueError, match="(?i)inductee"):
        bridge._build_web_aar_record(
            submission, guild, channel, members[0], 123458, bridge_mod._utcnow()
        )


def test_web_aar_receipt_uses_one_components_v2_gallery(tmp_path):
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

    layout, files = asyncio.run(bridge._web_aar_layout(view, participants, screenshots, "key-1234567890"))
    components = layout.to_components()

    assert layout.has_components_v2()
    assert len(components) == 1
    assert components[0]["type"] == 17
    text, separator, gallery = components[0]["components"]
    assert text["type"] == 10
    assert text["content"].splitlines()[0] == "# /// 𝐌𝐢𝐬𝐬𝐢𝐨𝐧 𝐂𝐡𝐫𝐨𝐧𝐢𝐜𝐥𝐞"
    assert text["content"].splitlines()[1] == "-# ⌮ Watch Fortress Jericho ⌮"
    assert "**`ᴏᴘᴇʀᴀᴛɪᴏɴ`** Inferno" in text["content"]
    assert "**`ʙᴀᴛᴛʟᴇ-ʙʀᴏᴛʜᴇʀs`** Brother One, Brother Two" in text["content"]
    assert text["content"].splitlines()[-1] == "-# STRATEGIUM WEB AAR key-1234567890"
    assert separator["type"] == 14
    assert gallery["type"] == 12
    assert len(gallery["items"]) == 10
    assert all(item["media"]["url"].startswith("attachment://jericho-aar-key-1234-") for item in gallery["items"])
    assert len(files) == 10
    for file in files:
        file.close()


def test_web_aar_receipt_names_gene_seed_carrier(tmp_path):
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
        gene_seed_status="carried",
        gene_seed_carrier_id="102",
    )

    layout, files = asyncio.run(bridge._web_aar_layout(view, participants, [], "key-1234567890"))
    text = layout.to_components()[0]["components"][0]["content"]

    assert "**`ɢᴇɴᴇ-sᴇᴇᴅ`** Carried by Brother Two" in text
    assert files == []


def _multipart_aar_body(image: bytes | None, *, content_type: str = "image/png", submission: dict | None = None, boundary: str = "aar-boundary") -> tuple[bytes, str]:
    submission_json = json.dumps(submission or {})
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"submission\"\r\nContent-Type: application/json\r\n\r\n{submission_json}\r\n".encode(),
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"keep_screenshots\"\r\n\r\ntrue\r\n".encode() if image is None else f"--{boundary}\r\nContent-Disposition: form-data; name=\"screenshots\"; filename=\"proof.png\"\r\nContent-Type: {content_type}\r\n\r\n".encode() + image + b"\r\n"),
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


def test_web_aar_multipart_parser_handles_quoted_boundary_and_keep_flag():
    boundary = "----WebKitFormBoundaryAbC123"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"submission\"\r\n\r\n{{\"mode\":\"ops\"}}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"keep_screenshots\"\r\n\r\ntrue\r\n"
        f"--{boundary}--\r\n"
    ).encode()

    submission, screenshots = bridge_mod._parse_web_aar_multipart(
        body, f'multipart/form-data; boundary="{boundary}"', allow_kept_screenshots=True
    )

    assert submission == {"mode": "ops"}
    assert screenshots == []


@pytest.mark.parametrize("mutate", [
    lambda body: body[:-30],
    lambda body: body.replace(b"--aar-boundary--", b""),
    lambda body: b"garbage",
])
def test_web_aar_multipart_parser_rejects_truncated_bodies(mutate):
    image = bridge_mod.base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/pKcAAAAASUVORK5CYII="
    )
    body, content_type = _multipart_aar_body(image)

    with pytest.raises(ValueError):
        bridge_mod._parse_web_aar_multipart(mutate(body), content_type)


def test_web_aar_multipart_parser_memory_stays_near_body_size():
    import tracemalloc

    image = b"\x89PNG\r\n\x1a\n" + bytes(3_000_000) + b"IEND\xaeB`\x82"
    boundary = "aar-boundary"
    parts = [f"--{boundary}\r\nContent-Disposition: form-data; name=\"submission\"\r\n\r\n{{}}\r\n".encode()]
    for index in range(10):
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"screenshots\"; filename=\"s{index}.png\"\r\n"
            "Content-Type: image/png\r\n\r\n".encode() + image + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)

    tracemalloc.start()
    try:
        _, screenshots = bridge_mod._parse_web_aar_multipart(body, f"multipart/form-data; boundary={boundary}")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(screenshots) == 10
    assert peak < len(body) * 1.5


def test_web_aar_intake_is_disabled_by_default(tmp_path):
    bridge = _mk_bridge(tmp_path)

    async def _run():
        response = await bridge.handle_web_aar_submission(DummyRequest())
        assert response.status == 503
        assert _json(response)["error"] == "not_configured"

    asyncio.run(_run())


def test_web_aar_access_allows_every_non_bot_guild_member(tmp_path):
    bridge = _mk_bridge(tmp_path)
    member = SimpleNamespace(bot=False, roles=[])
    bridge.config["web_submission"] = {"access_mode": "staff"}
    assert bridge._web_aar_member_allowed(member)
    member.bot = True
    assert not bridge._web_aar_member_allowed(member)
    member.bot = False
    bridge.config["web_submission"]["access_mode"] = "invalid"
    assert bridge._web_aar_member_allowed(member)


def test_web_aar_upload_limit_does_not_expand_regular_api_body_limit(tmp_path):
    bridge = _mk_bridge(tmp_path)

    regular_limit = bridge.config.get("max_body_bytes") or bridge_mod.DEFAULT_MAX_BODY_BYTES
    assert bridge.app._client_max_size == regular_limit
    assert bridge.web_aar_app._client_max_size == bridge_mod.MAX_WEB_AAR_REQUEST_BYTES
    assert bridge_mod.MAX_WEB_AAR_FILE_BYTES == 32 * 1024 * 1024
    assert bridge_mod.MAX_WEB_AAR_TOTAL_BYTES == 32 * 1024 * 1024


def test_web_aar_request_size_error_reports_measured_body_size():
    message = bridge_mod._web_aar_request_size_error(35 * 1024 * 1024)

    assert "35.00 MiB" in message
    assert "maximum request size is 34 MiB" in message


def test_web_aar_routed_upload_uses_aar_limit_not_regular_api_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGIUM_BOT_SHARED_SECRET", "test-upload-secret")
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace())
    bridge = _mk_bridge(tmp_path, {
        "host": "127.0.0.1", "port": 0,
        "web_submission": {"enabled": True},
    })

    async def _run():
        await bridge.start()
        try:
            port = bridge.site._server.sockets[0].getsockname()[1]
            headers = {
                "Content-Type": "multipart/form-data; boundary=test",
                "X-Strategium-AAR-Timestamp": str(int(bridge_mod._utcnow().timestamp())),
                "X-Strategium-AAR-Idempotency-Key": "large-body-test-123456",
                "X-Strategium-User-ID": "101",
                "X-Strategium-AAR-Signature": "invalid",
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"http://127.0.0.1:{port}/v1/aar/submissions",
                    data=b"x" * int(7.11 * 1024 * 1024), headers=headers,
                ) as response:
                    payload = await response.json()
                    assert response.status == 401, payload
                    assert payload["error"] == "unauthorized"
            assert bridge.app._client_max_size == bridge_mod.DEFAULT_MAX_BODY_BYTES
            assert bridge.web_aar_inflight_requests == 0
            assert bridge.web_aar_inflight_bytes == 0
        finally:
            await bridge.stop()

    asyncio.run(_run())


def test_web_aar_max_file_limit_tracks_discord_guild_limit(tmp_path):
    bridge = _mk_bridge(tmp_path, {"web_submission": {"max_file_bytes": 32 * 1024 * 1024}})

    assert bridge._web_aar_max_file_bytes(SimpleNamespace(filesize_limit=25 * 1024 * 1024)) == 25 * 1024 * 1024
    assert bridge._web_aar_max_file_bytes(SimpleNamespace(filesize_limit=50 * 1024 * 1024)) == 32 * 1024 * 1024


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


def test_web_aar_shared_secret_prefers_existing_bot_variable(monkeypatch):
    monkeypatch.setenv("STRATEGIUM_BOT_SHARED_SECRET", "existing-bot-secret")
    monkeypatch.setenv("STRATEGIUM_BOT_AAR_SHARED_SECRET", "aar-specific-secret")

    assert bridge_mod._web_aar_shared_secret() == "existing-bot-secret"


def test_web_aar_access_requires_signature_and_returns_fresh_guild_name(tmp_path, monkeypatch):
    secret = "access-test-secret"
    monkeypatch.delenv("STRATEGIUM_BOT_AAR_SHARED_SECRET", raising=False)
    monkeypatch.setenv("STRATEGIUM_BOT_SHARED_SECRET", secret)
    bridge = _mk_bridge(tmp_path)
    member = SimpleNamespace(bot=False, display_name="Watch Member Jules", roles=[])
    bridge._fresh_web_member = AsyncMock(return_value=member)
    bridge._resolve_guild = lambda: SimpleNamespace(filesize_limit=24 * 1024 * 1024)
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
        assert _json(accepted) == {
            "allowed": True, "guild_member": True,
            "display_name": "Watch Member Jules",
            "max_file_bytes": 24 * 1024 * 1024,
            "aar_management": {"can_edit": False, "can_delete": False},
        }

        member.roles = [SimpleNamespace(id=bridge_mod.HIGH_COMMAND_ROLE_ID, name="High Command")]
        assert bridge._web_aar_staff_capabilities(member) == {"can_edit": True, "can_delete": False}
        member.roles = [SimpleNamespace(id=0, name="Watch Techmarine")]
        assert bridge._web_aar_staff_capabilities(member) == {"can_edit": True, "can_delete": False}
        member.roles = [SimpleNamespace(id=0, name="Forgemaster")]
        assert bridge._web_aar_staff_capabilities(member) == {"can_edit": True, "can_delete": True}
        member.roles = [SimpleNamespace(id=0, name="Forge Master")]
        assert bridge._web_aar_staff_capabilities(member) == {"can_edit": True, "can_delete": True}
        member.roles = [SimpleNamespace(id=0, name="  FORGE   MASTER  ")]
        assert bridge._web_aar_staff_capabilities(member) == {"can_edit": True, "can_delete": True}
        member.display_name = "Forge Master Jules"
        member.roles = [SimpleNamespace(id=0, name="Watch Brother")]
        assert bridge._web_aar_staff_capabilities(member) == {"can_edit": False, "can_delete": False}

    asyncio.run(_run())
    bridge._fresh_web_member.assert_awaited_once_with(101)


def test_fresh_web_member_prefers_gateway_cache_over_rest(tmp_path):
    bridge = _mk_bridge(tmp_path)
    cached = SimpleNamespace(id=101)
    fetched = SimpleNamespace(id=202)
    guild = SimpleNamespace(
        get_member=lambda user_id: cached if user_id == 101 else None,
        fetch_member=AsyncMock(return_value=fetched),
    )
    bridge._resolve_guild = lambda: guild

    async def _run():
        assert await bridge._fresh_web_member(101) is cached
        guild.fetch_member.assert_not_awaited()
        assert await bridge._fresh_web_member(202) is fetched
        guild.fetch_member.assert_awaited_once_with(202)

    asyncio.run(_run())


def test_web_aar_member_search_includes_full_guild_and_caches_directory(tmp_path, monkeypatch):
    secret = "member-search-secret"
    monkeypatch.setenv("STRATEGIUM_BOT_SHARED_SECRET", secret)
    class Records:
        def get_all_records(self):
            return {}
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", Records())
    bridge = _mk_bridge(tmp_path, {"web_submission": {"enabled": True}})
    actor = SimpleNamespace(id=101, bot=False)
    bridge._fresh_web_member = AsyncMock(return_value=actor)
    fetch_count = 0

    class DirectoryGuild:
        id = 1429264578440597517

        async def fetch_members(self, *, limit):
            nonlocal fetch_count
            assert limit is None
            fetch_count += 1
            for member in [
                SimpleNamespace(id=201, bot=False, display_name="Jules Reserve"),
                SimpleNamespace(id=202, bot=False, display_name="Brother Jules"),
                SimpleNamespace(id=203, bot=True, display_name="Jules Bot"),
            ]:
                yield member

    guild = DirectoryGuild()
    bridge._resolve_guild = lambda: guild

    def make_request(query):
        body = json.dumps({"query": query}).encode()
        timestamp = str(int(bridge_mod._utcnow().timestamp()))
        signed = f"{timestamp}\nmembers-101\n101\n{bridge_mod.hashlib.sha256(body).hexdigest()}".encode()
        signature = bridge_mod.hmac.new(secret.encode(), signed, bridge_mod.hashlib.sha256).hexdigest()
        return MultipartAARRequest(body, "application/json", {
            "X-Strategium-User-ID": "101",
            "X-Strategium-AAR-Timestamp": timestamp,
            "X-Strategium-AAR-Signature": signature,
        })

    async def _run():
        first = await bridge.handle_web_aar_member_search(make_request("jules"))
        assert first.status == 200
        assert _json(first) == {
            "members": [
                {"id": "201", "name": "Jules Reserve"},
                {"id": "202", "name": "Brother Jules"},
            ],
            "has_more": False,
        }
        second = await bridge.handle_web_aar_member_search(make_request("brother"))
        assert _json(second)["members"] == [{"id": "202", "name": "Brother Jules"}]
        assert fetch_count == 1

    asyncio.run(_run())


def test_web_aar_management_list_and_detail_require_staff_and_web_source(tmp_path, monkeypatch):
    secret = "management-test-secret"
    monkeypatch.setenv("STRATEGIUM_BOT_SHARED_SECRET", secret)
    bridge = _mk_bridge(tmp_path, {"web_submission": {"enabled": True}})
    web_record = {
        "aar_id": 123, "source": "strategium_web", "mission": "Inferno",
        "difficulty": "Ruthless", "timestamp": "2026-10-12T12:00:00+00:00",
        "brother_names": ["Watch Member"], "submitter_id": "101",
        "message_url": "https://discord.com/channels/1/2/123",
        "web_submission": {"mode": "ops_strat", "mission": "pve_inferno"},
    }
    manual_record = {"aar_id": 456, "source": "discord", "mission": "Inferno"}

    class Records:
        def get_all_records(self):
            return {"123": web_record, "456": manual_record}

        def get_record(self, record_id):
            return self.get_all_records().get(str(record_id))

    monkeypatch.setattr(bridge_mod._g, "DATASTORE", Records())
    monkeypatch.setattr(bridge_mod, "_load_web_aar_mutations", lambda: [
        {
            "record_id": "123", "operation": "edit", "request_id": "edit-retry-123456789", "status": "archive_saved",
            "submission": {"mission": "pve_inferno"},
            "reconciliation": {"review_required": [{"user_id": "55", "challenge": "Black Laurels"}]},
        },
        {"record_id": "456", "operation": "edit", "request_id": "edit-pending-1234567", "status": "pending"},
        {"record_id": "123", "operation": "delete", "status": "requested", "reason": "Duplicate report"},
        {"record_id": "789", "operation": "delete", "status": "reconciling", "receipt_url": "https://discord.com/channels/1/2/789"},
        {"operation": "edit_request", "request_id": "a" * 24, "record_id": "123", "requester_id": "101", "requester_name": "Watch Member", "requested_changes": "Correct armory data to three.", "requested_at": "2026-10-19T00:00:00+00:00", "status": "pending"},
    ])
    member = SimpleNamespace(id=101, bot=False, roles=[])
    bridge._fresh_web_member = AsyncMock(return_value=member)

    def request_for(action, record_id="", body=None):
        request_body = json.dumps(body or {}).encode()
        timestamp = str(int(bridge_mod._utcnow().timestamp()))
        key = f"management-{action}-{record_id or 'list'}"
        signed = f"{timestamp}\n{key}\n101\n{bridge_mod.hashlib.sha256(request_body).hexdigest()}".encode()
        signature = bridge_mod.hmac.new(secret.encode(), signed, bridge_mod.hashlib.sha256).hexdigest()
        return MultipartAARRequest(request_body, "application/json", {
            "X-Strategium-User-ID": "101",
            "X-Strategium-AAR-Timestamp": timestamp,
            "X-Strategium-AAR-Signature": signature,
        }), signature

    async def _run():
        request, _ = request_for("list")
        denied = await bridge.handle_web_aar_management_list(request)
        assert denied.status == 403
        assert "edit_requests" not in _json(denied)

        member.roles = [SimpleNamespace(id=bridge_mod.HIGH_COMMAND_ROLE_ID, name="High Command")]
        request, _ = request_for("list", body={"query": "watch member", "offset": 0})
        listed = await bridge.handle_web_aar_management_list(request)
        assert listed.status == 200
        assert _json(listed)["total"] == 1
        assert _json(listed)["edit_request_count"] == 1
        assert _json(listed)["edit_requests"][0]["requested_changes"] == "Correct armory data to three."
        assert _json(listed)["edit_requests"][0]["requester_id"] == "101"
        assert _json(listed)["records"][0]["id"] == "123"
        assert [item["receipt_url"] for item in _json(listed)["pending_deletions"]] == [
            "https://discord.com/channels/1/2/123", "https://discord.com/channels/1/2/789",
        ]
        review_mutation = bridge_mod._load_web_aar_mutations()[0]
        review = review_mutation["reconciliation"]["review_required"][0]
        assert _json(listed)["reconciliation_reviews"] == [{
            "record_id": "123", "operation": "edit", "user_id": "55", "member_name": "55", "challenge": "Black Laurels",
            "review_id": bridge_mod._reconciliation_review_id(review_mutation, review),
        }]
        assert _json(listed)["pending_edits"] == [{
            "id": "123", "request_id": "edit-retry-123456789", "status": "archive_saved",
            "mission": "pve_inferno", "receipt_url": "",
        }]

        request, _ = request_for("records", "123")
        request.match_info = {"record_id": "123"}
        detail = await bridge.handle_web_aar_management_detail(request)
        assert detail.status == 200
        assert _json(detail)["submission"]["mission"] == "pve_inferno"

        request, _ = request_for("records", "456")
        request.match_info = {"record_id": "456"}
        not_web = await bridge.handle_web_aar_management_detail(request)
        assert not_web.status == 404

    asyncio.run(_run())


@pytest.mark.parametrize("stored_screenshots", [None, [], [{"filename": "old.png", "url": "https://cdn.discordapp.com/old.png", "size": 10}]])
def test_web_aar_management_detail_reads_current_receipt_screenshots(tmp_path, monkeypatch, stored_screenshots):
    from opscribe import aar_ops

    bridge = _mk_bridge(tmp_path, {"web_submission": {"enabled": True}})
    record = {
        "aar_id": 123, "source": "strategium_web", "web_submission_key": "receipt-key",
        "web_submission": {"mode": "ops_strat", "mission": "pve_inferno"},
    }
    if stored_screenshots is not None:
        record["screenshots"] = stored_screenshots
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace(get_record=lambda _record_id: record))
    monkeypatch.setattr(bridge_mod, "_load_web_aar_mutations", lambda: [])
    bridge._authorize_web_aar_management = AsyncMock(return_value=(SimpleNamespace(id=101), {}))
    guild = object()
    channel = object()
    bridge._resolve_guild = lambda: guild
    monkeypatch.setattr(aar_ops, "_resolve_aar_submission_channel", lambda _guild: channel)
    screenshot = {"filename": "proof.png", "url": "https://cdn.discordapp.com/proof.png", "size": 12}
    bridge._find_web_aar_receipt = AsyncMock(return_value=SimpleNamespace(attachments=[SimpleNamespace(**screenshot)]))
    request = DummyRequest(match_info={"record_id": "123"})

    response = asyncio.run(bridge.handle_web_aar_management_detail(request))

    assert response.status == 200
    assert _json(response)["screenshots"] == [screenshot]
    bridge._find_web_aar_receipt.assert_awaited_once_with(channel, "receipt-key", "123")

    bridge._find_web_aar_receipt.return_value = None
    response = asyncio.run(bridge.handle_web_aar_management_detail(request))
    assert _json(response)["screenshots"] == (stored_screenshots or [])


def test_receipt_screenshots_read_components_v2_gallery_when_attachments_are_empty():
    gallery_url = "https://cdn.discordapp.com/attachments/1/2/jericho-aar-key-01.png?ex=1&hm=2"
    raw_message = SimpleNamespace(attachments=[], components=[{"type": 17, "components": [
        {"type": 10, "content": "receipt"},
        {"type": 12, "items": [
            {"media": {"url": gallery_url}},
            {"media": {"url": gallery_url}},
            {"media": {"url": "https://evil.example/x.png"}},
            {"media": {"url": "attachment://pending.png"}},
        ]},
    ]}])
    object_message = SimpleNamespace(attachments=[], components=[SimpleNamespace(children=[
        SimpleNamespace(items=[SimpleNamespace(media=SimpleNamespace(url=gallery_url))]),
    ])])

    for message in (raw_message, object_message):
        assert bridge_mod._public_screenshots(bridge_mod._web_aar_receipt_screenshots(message)) == [
            {"filename": "jericho-aar-key-01.png", "url": gallery_url, "size": 0},
        ]

    attachment = SimpleNamespace(filename="proof.png", url="https://cdn.discordapp.com/proof.png", size=12)
    with_attachment = SimpleNamespace(attachments=[attachment], components=raw_message.components)
    assert bridge_mod._public_screenshots(bridge_mod._web_aar_receipt_screenshots(with_attachment)) == [
        {"filename": "proof.png", "url": "https://cdn.discordapp.com/proof.png", "size": 12},
    ]


def test_discord_media_download_rejects_non_discord_hosts():
    with pytest.raises(ValueError):
        asyncio.run(bridge_mod._download_discord_media("https://evil.example/x.png", 1024))
    with pytest.raises(ValueError):
        asyncio.run(bridge_mod._download_discord_media("http://cdn.discordapp.com/x.png", 1024))


def test_web_aar_reconciliation_acknowledgement_is_forgemaster_only_and_idempotent(tmp_path, monkeypatch):
    secret = "review-ack-secret"
    monkeypatch.setenv("STRATEGIUM_BOT_SHARED_SECRET", secret)
    monkeypatch.setattr(bridge_mod, "WEB_AAR_MUTATIONS_PATH", str(tmp_path / "web_aar_mutations.json"))
    class EmptyRecords:
        def get_all_records(self):
            return {}
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", EmptyRecords())
    bridge = _mk_bridge(tmp_path, {"web_submission": {"enabled": True}})
    mutation = {
        "request_id": "edit-review-123456789", "operation": "edit", "record_id": "123",
        "reconciliation": {"review_required": [{"user_id": "55", "challenge": "Black Laurels"}]},
    }
    bridge_mod._save_web_aar_mutations([mutation])
    review_id = bridge_mod._reconciliation_review_id(mutation, mutation["reconciliation"]["review_required"][0])
    member = SimpleNamespace(id=909, bot=False, roles=[SimpleNamespace(id=bridge_mod.HIGH_COMMAND_ROLE_ID, name="High Command")])
    bridge._fresh_web_member = AsyncMock(return_value=member)

    def make_request():
        body = json.dumps({"note": "Verified manual award remains valid."}).encode()
        timestamp = str(int(bridge_mod._utcnow().timestamp()))
        action = f"reviews/{review_id}/acknowledge"
        key = f"management-{action}-123"
        signed = f"{timestamp}\n{key}\n909\n{bridge_mod.hashlib.sha256(body).hexdigest()}".encode()
        signature = bridge_mod.hmac.new(secret.encode(), signed, bridge_mod.hashlib.sha256).hexdigest()
        request = MultipartAARRequest(body, "application/json", {
            "X-Strategium-User-ID": "909", "X-Strategium-AAR-Timestamp": timestamp,
            "X-Strategium-AAR-Signature": signature,
        })
        request.match_info = {"record_id": "123", "review_id": review_id}
        return request

    async def _run():
        denied = await bridge.handle_web_aar_reconciliation_acknowledge(make_request())
        assert denied.status == 403
        member.roles = [SimpleNamespace(id=0, name="Forgemaster")]
        acknowledged = await bridge.handle_web_aar_reconciliation_acknowledge(make_request())
        assert acknowledged.status == 200
        assert bridge_mod._load_web_aar_mutations()[0]["reconciliation"]["review_required"][0]["acknowledged_by"] == "909"
        duplicate = await bridge.handle_web_aar_reconciliation_acknowledge(make_request())
        assert duplicate.status == 200
        assert _json(duplicate)["duplicate"] is True
        body = json.dumps({"query": "", "offset": 0}).encode()
        timestamp = str(int(bridge_mod._utcnow().timestamp()))
        key = "management-list-list"
        signed = f"{timestamp}\n{key}\n909\n{bridge_mod.hashlib.sha256(body).hexdigest()}".encode()
        signature = bridge_mod.hmac.new(secret.encode(), signed, bridge_mod.hashlib.sha256).hexdigest()
        listed = await bridge.handle_web_aar_management_list(MultipartAARRequest(body, "application/json", {
            "X-Strategium-User-ID": "909", "X-Strategium-AAR-Timestamp": timestamp,
            "X-Strategium-AAR-Signature": signature,
        }))
        assert listed.status == 200
        assert _json(listed)["reconciliation_reviews"] == []

    asyncio.run(_run())


@pytest.mark.parametrize(("proposed_edit", "reviewer_role"), [(False, "techmarine"), (True, "techmarine"), (True, "high_command")])
def test_web_aar_management_edit_updates_receipt_and_preserves_record_identity(tmp_path, monkeypatch, proposed_edit, reviewer_role):
    from opscribe import aar_ops

    secret = "edit-management-secret"
    monkeypatch.setenv("STRATEGIUM_BOT_SHARED_SECRET", secret)
    monkeypatch.setattr(bridge_mod, "WEB_AAR_SUBMISSIONS_PATH", str(tmp_path / "web_aar_submissions.json"))
    monkeypatch.setattr(bridge_mod, "WEB_AAR_MUTATIONS_PATH", str(tmp_path / "web_aar_mutations.json"))
    bridge = _mk_bridge(tmp_path, {"web_submission": {"enabled": True}})
    channel = AARReceiptChannel()
    participants = [AARTestMember(101, "Brother One"), AARTestMember(102, "Brother Two")]
    guild = AARTestGuild(channel, participants)
    staff = AARTestMember(999, "Watch Techmarine Jules")
    reviewer_roles = [SimpleNamespace(id=bridge_mod.HIGH_COMMAND_ROLE_ID, name="High Command")] if reviewer_role == "high_command" else [SimpleNamespace(id=0, name="Watch Techmarine")]
    staff.roles = reviewer_roles
    bridge._fresh_web_member = AsyncMock(return_value=staff)
    bridge._resolve_guild = lambda: guild
    monkeypatch.setattr(aar_ops, "_resolve_aar_submission_channel", lambda _guild: channel)
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", SimpleNamespace(flush=AsyncMock()))
    old_submission = {
        "mode": "ops_strat", "mission": "pve_inferno", "difficulty": "@Ruthless", "rank": "A",
        "brother_ids": ["101", "102"], "tags": [], "armory_data": 0, "gene_seed_status": "unknown",
    }
    initial_view = SimpleNamespace(
        mission="Inferno", difficulty="@Ruthless", rank="A", tags=[], aar_type="pve",
        armory_data=0, mode_config={}, gene_seed_status="unknown",
    )
    layout, initial_files = asyncio.run(bridge._web_aar_layout(initial_view, participants, [], "edit-key-123456789"))
    message = asyncio.run(channel.send(view=layout, files=initial_files, allowed_mentions=bridge_mod.discord.AllowedMentions.none()))
    for file in initial_files:
        file.close()
    message.edits = []
    async def edit_receipt(*, view, attachments, allowed_mentions):
        message.edits.append({"view": view, "attachments": attachments, "allowed_mentions": allowed_mentions})
        message.components = view.to_components()
        message.attachments = [SimpleNamespace(filename=file.filename, url=f"https://cdn.discordapp.com/attachments/{message.id}/{file.filename}", size=12) for file in attachments]
        return message
    message.edit = edit_receipt
    old_timestamp = "2026-10-12T10:00:00+00:00"
    old_record = {
        "aar_id": message.id, "source": "strategium_web", "timestamp": old_timestamp,
        "submitter_id": "101", "web_submission_key": "edit-key-123456789",
        "web_submission": old_submission, "brother_ids": ["101", "102"], "brother_names": ["Brother One", "Brother Two"],
        "receipt_url": message.jump_url, "message_url": message.jump_url,
        "strike_evidence_hashes": ["a" * 64],
    }
    stored = {str(message.id): old_record}
    class Records:
        def get_record(self, record_id):
            return stored.get(str(record_id))
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", Records())
    bridge._resolve_guild = lambda: guild
    journal = {"edit-key-123456789": {"status": "complete", "record_id": str(message.id), "submitter_id": "101", "submission": old_submission}}
    monkeypatch.setattr(bridge_mod, "_load_web_aar_submissions", lambda: journal)
    monkeypatch.setattr(bridge_mod, "_save_web_aar_submissions", lambda payload: journal.update(payload))
    async def save_record(record):
        stored[str(record["aar_id"])] = record
    monkeypatch.setattr(aar_ops, "save_aar_record", save_record)
    reconciliation_calls = 0
    async def reconcile(_user_ids, _guild):
        nonlocal reconciliation_calls
        reconciliation_calls += 1
        if reconciliation_calls == 1:
            raise RuntimeError("simulated post-receipt reconciliation failure")
        return {"revoked": [], "review_required": []}
    monkeypatch.setattr(aar_ops, "rebuild_challenge_progress_for_users", reconcile)
    new_submission = {**old_submission, "mission": "pve_decapitation", "brother_ids": ["101", "102"]}
    image = bridge_mod.base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/pKcAAAAASUVORK5CYII=")
    wire_submission = dict(new_submission)
    proposal_id = None
    if proposed_edit:
        message.attachments = [SimpleNamespace(filename="proof.png", url="https://cdn.discordapp.com/proof.png", size=len(image), content_type="image/png", read=AsyncMock(return_value=image))]
        original_authorizer = bridge._authorize_web_aar_management
        bridge._authorize_web_aar_management = AsyncMock(return_value=(participants[0], {
            "record_id": str(message.id), "submission": new_submission,
            "original_record_hash": bridge_mod.hashlib.sha256(json.dumps(old_record, sort_keys=True, default=str).encode()).hexdigest(),
        }))
        queued = asyncio.run(bridge.handle_web_aar_edit_request(DummyRequest()))
        assert queued.status == 201
        proposal_id = _json(queued)["request_id"]
        bridge._authorize_web_aar_management = original_authorizer
        wire_submission.update(edit_proposal_id=proposal_id, edit_proposal_note="Validated proposed mission correction")
        assert stored[str(message.id)] is old_record
        assert not message.edits
    body, content_type = _multipart_aar_body(None if proposed_edit else image, submission=wire_submission)
    request_id = "staff-edit-request-123456"
    timestamp = str(int(bridge_mod._utcnow().timestamp()))
    request_key = f"edit-{message.id}-{request_id}"
    signed = f"{timestamp}\n{request_key}\n999\n{bridge_mod.hashlib.sha256(body).hexdigest()}".encode()
    signature = bridge_mod.hmac.new(secret.encode(), signed, bridge_mod.hashlib.sha256).hexdigest()
    request = MultipartAARRequest(body, content_type, {
        "X-Strategium-User-ID": "999", "X-Strategium-AAR-Timestamp": timestamp,
        "X-AAR-Management-Request-ID": request_id, "X-Strategium-AAR-Signature": signature,
    })
    request.match_info = {"record_id": str(message.id)}

    if proposed_edit:
        old_record["temporary_external_change"] = True
        assert asyncio.run(bridge.handle_web_aar_management_edit(request)).status == 409
        old_record.pop("temporary_external_change")
        staff.roles = []
        assert asyncio.run(bridge.handle_web_aar_management_edit(request)).status == 403
        staff.roles = reviewer_roles
        assert not message.edits
    response = asyncio.run(bridge.handle_web_aar_management_edit(request))
    updated = stored[str(message.id)]
    assert response.status == 202
    assert _json(response)["processing_pending"] is True
    assert updated["mission"] == "Decapitation"
    assert updated["submitter_id"] == "101"
    assert updated["timestamp"] == old_timestamp
    assert updated["source"] == "strategium_web"
    assert updated["web_submission"]["mission"] == "pve_decapitation"
    assert updated["edited_at"]
    if proposed_edit:
        assert updated["strike_evidence_hashes"] == ["a" * 64]
    assert message.edits
    mutation = next(item for item in bridge_mod._load_web_aar_mutations() if item.get("request_id") == request_id)
    assert mutation["status"] == "archive_saved"
    assert mutation["submission"] == new_submission
    if proposed_edit:
        assert next(item for item in bridge_mod._load_web_aar_mutations() if item.get("request_id") == proposal_id)["status"] == "pending"
        original_authorizer = bridge._authorize_web_aar_management
        bridge._authorize_web_aar_management = AsyncMock(return_value=(staff, {"note": "Decline during processing"}))
        declined = asyncio.run(bridge.handle_web_aar_edit_request_decision(DummyRequest(match_info={"request_id": proposal_id, "decision": "reject"})))
        assert declined.status == 409
        assert _json(declined)["error"] == "approval_processing"
        bridge._authorize_web_aar_management = original_authorizer

    resume_body = json.dumps({"request_id": request_id}).encode()
    resume_timestamp = str(int(bridge_mod._utcnow().timestamp()))
    resume_key = f"management-edit-resume-{message.id}"
    resume_signed = f"{resume_timestamp}\n{resume_key}\n999\n{bridge_mod.hashlib.sha256(resume_body).hexdigest()}".encode()
    resume_signature = bridge_mod.hmac.new(secret.encode(), resume_signed, bridge_mod.hashlib.sha256).hexdigest()
    resume = MultipartAARRequest(resume_body, "application/json", {
        "X-Strategium-User-ID": "999", "X-Strategium-AAR-Timestamp": resume_timestamp,
        "X-Strategium-AAR-Signature": resume_signature,
    })
    resume.match_info = {"record_id": str(message.id)}
    resumed = asyncio.run(bridge.handle_web_aar_management_edit_resume(resume))
    assert resumed.status == 200
    assert _json(resumed)["resumed"] is True
    if proposed_edit:
        proposal = next(item for item in bridge_mod._load_web_aar_mutations() if item.get("request_id") == proposal_id)
        assert stored[str(message.id)]["strike_evidence_hashes"] == ["a" * 64]
        assert proposal["status"] == "resolved"
        assert proposal["reviewed_by"] == "999"
        assert proposal["editor_note"] == "Validated proposed mission correction"

    retry_body, retry_content_type = _multipart_aar_body(None if proposed_edit else image, submission=wire_submission, boundary="different-browser-boundary")
    retry_timestamp = str(int(bridge_mod._utcnow().timestamp()))
    retry_signed = f"{retry_timestamp}\n{request_key}\n999\n{bridge_mod.hashlib.sha256(retry_body).hexdigest()}".encode()
    retry_signature = bridge_mod.hmac.new(secret.encode(), retry_signed, bridge_mod.hashlib.sha256).hexdigest()
    retry = MultipartAARRequest(retry_body, retry_content_type, {
        "X-Strategium-User-ID": "999", "X-Strategium-AAR-Timestamp": retry_timestamp,
        "X-AAR-Management-Request-ID": request_id, "X-Strategium-AAR-Signature": retry_signature,
    })
    retry.match_info = {"record_id": str(message.id)}
    duplicate = asyncio.run(bridge.handle_web_aar_management_edit(retry))
    assert duplicate.status == 200
    assert _json(duplicate)["duplicate"] is True
    assert len(message.edits) == 1


@pytest.mark.parametrize("forgemaster_role", ["Forgemaster", "Forge Master"])
def test_web_aar_deletion_requires_forgemaster_and_removes_receipt_and_record(tmp_path, monkeypatch, forgemaster_role):
    from opscribe import aar_ops

    secret = "delete-management-secret"
    monkeypatch.setenv("STRATEGIUM_BOT_SHARED_SECRET", secret)
    monkeypatch.setattr(bridge_mod, "WEB_AAR_SUBMISSIONS_PATH", str(tmp_path / "web_aar_submissions.json"))
    monkeypatch.setattr(bridge_mod, "WEB_AAR_MUTATIONS_PATH", str(tmp_path / "web_aar_mutations.json"))
    bridge = _mk_bridge(tmp_path, {"web_submission": {"enabled": True}})
    channel = AARReceiptChannel()
    participants = [AARTestMember(101, "Brother One"), AARTestMember(102, "Brother Two")]
    guild = AARTestGuild(channel, participants)
    layout, files = asyncio.run(bridge._web_aar_layout(SimpleNamespace(
        mission="Inferno", difficulty="@Ruthless", rank="A", tags=[], aar_type="pve",
        armory_data=0, mode_config={}, gene_seed_status="unknown",
    ), participants, [], "delete-key-123456789"))
    message = asyncio.run(channel.send(view=layout, files=files, allowed_mentions=bridge_mod.discord.AllowedMentions.none()))
    for file in files:
        file.close()
    message.deleted = False
    delete_calls = 0
    async def delete_receipt():
        nonlocal delete_calls
        delete_calls += 1
        message.deleted = True
        channel._messages.pop(message.id, None)
    message.delete = delete_receipt
    web_key = "delete-key-123456789"
    original_record = {"aar_id": message.id, "source": "strategium_web", "web_submission_key": web_key, "receipt_url": message.jump_url}
    stored = {str(message.id): original_record.copy()}
    record_delete_calls = 0
    flush_calls = 0
    class Records:
        def get_record(self, record_id):
            return stored.get(str(record_id))

        async def delete_record_and_processed_id(self, record_id):
            nonlocal record_delete_calls
            record_delete_calls += 1
            return stored.pop(str(record_id), None)

        async def flush(self):
            nonlocal flush_calls
            flush_calls += 1
            if flush_calls == 1:
                stored[str(message.id)] = original_record.copy()
                raise OSError("simulated archive replacement failure")
            return None
    monkeypatch.setattr(bridge_mod._g, "DATASTORE", Records())
    bridge._resolve_guild = lambda: guild
    monkeypatch.setattr(aar_ops, "_resolve_aar_submission_channel", lambda _guild: channel)
    web_journal = {web_key: {"record_id": str(message.id), "status": "complete", "submission": {"private": "must be erased"}}}
    monkeypatch.setattr(bridge_mod, "_load_web_aar_submissions", lambda: web_journal)
    monkeypatch.setattr(bridge_mod, "_save_web_aar_submissions", lambda payload: web_journal.update(payload))
    reconciliation_calls = 0
    async def reconcile(_user_ids, _guild):
        nonlocal reconciliation_calls
        reconciliation_calls += 1
        if reconciliation_calls == 1:
            raise RuntimeError("simulated award reconciliation interruption")
        return {"revoked": [], "review_required": []}
    monkeypatch.setattr(aar_ops, "rebuild_challenge_progress_for_users", reconcile)
    member = AARTestMember(999, "Watch Techmarine Jules")
    member.roles = [SimpleNamespace(id=0, name="Watch Techmarine")]
    bridge._fresh_web_member = AsyncMock(return_value=member)

    def make_request(action, body):
        if action == "delete-confirm":
            body = {**body, "request_token": bridge_mod._load_web_aar_mutations()[-1].get("request_token")}
        request_body = json.dumps(body).encode()
        timestamp = str(int(bridge_mod._utcnow().timestamp()))
        key = f"management-{action}-{message.id}"
        signed = f"{timestamp}\n{key}\n999\n{bridge_mod.hashlib.sha256(request_body).hexdigest()}".encode()
        signature = bridge_mod.hmac.new(secret.encode(), signed, bridge_mod.hashlib.sha256).hexdigest()
        request = MultipartAARRequest(request_body, "application/json", {
            "X-Strategium-User-ID": "999", "X-Strategium-AAR-Timestamp": timestamp,
            "X-Strategium-AAR-Signature": signature,
        })
        request.match_info = {"record_id": str(message.id)}
        return request

    async def _run():
        requested = await bridge.handle_web_aar_delete_request(make_request("delete-request", {"reason": "Duplicate report submitted in error."}))
        assert requested.status == 202
        assert _json(requested)["reason"] == "Duplicate report submitted in error."
        assert bridge_mod._load_web_aar_mutations()[-1]["receipt_url"] == message.jump_url
        duplicate_request = await bridge.handle_web_aar_delete_request(make_request("delete-request", {"reason": "A different deletion reason."}))
        assert _json(duplicate_request)["reason"] == "Duplicate report submitted in error."
        denied = await bridge.handle_web_aar_delete_confirm(make_request("delete-confirm", {}))
        assert denied.status == 403
        member.roles = [SimpleNamespace(id=0, name=forgemaster_role)]
        pending = await bridge.handle_web_aar_delete_confirm(make_request("delete-confirm", {}))
        assert pending.status == 202
        assert _json(pending)["resume_status"] == "record_deleted"
        pending = await bridge.handle_web_aar_delete_confirm(make_request("delete-confirm", {}))
        assert pending.status == 202
        assert _json(pending)["resume_status"] == "reconciling"
        assert bridge_mod._load_web_aar_mutations()[-1]["receipt_url"] == message.jump_url
        confirmed = await bridge.handle_web_aar_delete_confirm(make_request("delete-confirm", {}))
        assert confirmed.status == 200
        assert message.deleted is True
        assert delete_calls == 1
        assert record_delete_calls == 2
        assert str(message.id) not in stored
        assert web_key not in web_journal
        assert "private" not in json.dumps(bridge_mod._load_web_aar_mutations())
        assert bridge_mod._load_web_aar_mutations()[-1]["status"] == "complete"

    asyncio.run(_run())


class AARReceiptChannel:
    def __init__(self, channel_id=1429318686447108300):
        self.id = channel_id
        self.sent = []
        self._messages = {}

    async def send(self, *, view, files, allowed_mentions):
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
            embeds=[],
            components=view.to_components(),
            flags=bridge_mod.discord.MessageFlags(components_v2=True),
            author=SimpleNamespace(id=9000),
        )
        self.sent.append({"components": message.components, "allowed_mentions": allowed_mentions, "message": message})
        self._messages[message_id] = message
        return message

    async def fetch_message(self, message_id):
        return self._messages[int(message_id)]

    async def history(self, limit=100):
        for message in list(self._messages.values())[-limit:]:
            yield message


def test_web_aar_receipt_recovery_requires_bot_author_and_exact_marker(tmp_path):
    bridge = _mk_bridge(tmp_path)
    bridge.bot.user = SimpleNamespace(id=9000)
    channel = AARReceiptChannel()
    key = "receipt-recovery-key-12345"
    marker = f"STRATEGIUM WEB AAR {key}"
    for message_id, author_id, footer in (
        (1, 9001, marker),
        (2, 9000, f"Copied marker: {marker}"),
        (3, 9000, marker),
    ):
        channel._messages[message_id] = SimpleNamespace(
            id=message_id,
            author=SimpleNamespace(id=author_id),
            components=[{"type": 17, "components": [{"type": 10, "content": f"-# {footer}"}]}],
        )
    legacy_embed = bridge_mod.discord.Embed()
    legacy_embed.set_footer(text=marker)
    channel._messages[4] = SimpleNamespace(
        id=4, author=SimpleNamespace(id=9000), embeds=[legacy_embed], components=[],
    )

    async def _run():
        for message_id in (None, 1, 2, 3):
            message = await bridge._find_web_aar_receipt(channel, key, message_id)
            assert message is channel._messages[3]
        channel._messages.pop(3)
        for message_id in (None, 1, 2, 4):
            message = await bridge._find_web_aar_receipt(channel, key, message_id)
            assert message is channel._messages[4]
        channel._messages.pop(4)
        assert await bridge._find_web_aar_receipt(channel, key) is None

    asyncio.run(_run())


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
    bridge = _mk_bridge(tmp_path, {"web_submission": {"enabled": True}})
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
        original_send = channel.send

        async def _uncertain_send(**kwargs):
            assert bridge_mod._load_web_aar_submissions()[key]["status"] == "posting"
            await original_send(**kwargs)
            raise RuntimeError("Receipt posted but response lost")

        with monkeypatch.context() as retry_patch:
            retry_patch.setattr(channel, "send", _uncertain_send)
            uncertain = await bridge.handle_web_aar_submission(request)
            assert uncertain.status == 503
            assert bridge_mod._load_web_aar_submissions()[key]["status"] == "posting"
            assert len(channel.sent) == 1
        first = await bridge.handle_web_aar_submission(request)
        first_payload = _json(first)
        original_journal = bridge_mod._load_web_aar_submissions()
        with monkeypatch.context() as retry_patch:
            retry_patch.setattr(bridge, "_find_web_aar_receipt", AsyncMock(return_value=None))
            for status in ("posting", "posted"):
                journal = bridge_mod._load_web_aar_submissions()
                journal[key]["status"] = status
                if status == "posting":
                    journal[key].pop("message_id", None)
                bridge_mod._save_web_aar_submissions(journal)
                unverified = await bridge.handle_web_aar_submission(retry_request)
                assert unverified.status == 503
                assert _json(unverified)["error"] == "receipt_pending"
                assert len(channel.sent) == 1
        bridge_mod._save_web_aar_submissions(original_journal)
        await bridge._retry_pending_web_aar_submissions()
        second = await bridge.handle_web_aar_submission(retry_request)
        second_payload = _json(second)
        limited = await bridge.handle_web_aar_submission(limited_request)
        bridge.config["web_submission"]["access_mode"] = "staff"
        member_duplicate = await bridge.handle_web_aar_submission(retry_request)
        assert member_duplicate.status == 200
        assert _json(member_duplicate)["duplicate"] is True
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
