"""Owner-scoped campaign testing; scratch stores never enter the AAR pipeline."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from datetime import datetime, timedelta

from .galactic_war import WarMember, utc_time
from .galactic_war_store import GalacticWarStore, _initial_state


def authorize(actor: WarMember) -> None:
    if not actor.active or not actor.has_role("Watch Master", "Forgemaster"):
        raise PermissionError("Only an active Watch Master or Forge Master may use invasion testing")


class SandboxStore(GalacticWarStore):
    def __init__(self, *args, session_id: str, owner_id: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.session_id = session_id
        self.owner_id = owner_id

    def _load(self, connection):
        state = super()._load(connection)
        session = state.get("sandbox", {})
        if not session.get("active") or session.get("id") != self.session_id or session.get("owner_id") != self.owner_id:
            raise ValueError("Test invasion session has ended or changed; return to the live view")
        return state


class InvasionSandbox:
    def __init__(self, live: GalacticWarStore, actor: WarMember):
        authorize(actor)
        self.actor = actor
        identity = hashlib.sha256(actor.member_id.encode()).hexdigest()
        self.path = live.path.parent / "galactic_war_sandboxes" / f"{identity}.sqlite3"
        self.reference = live

    def _store(self, session_id: str = "") -> GalacticWarStore:
        options = {"recipes": self.reference.recipes}
        store_type = GalacticWarStore
        if session_id:
            if not isinstance(session_id, str) or len(session_id) != 32:
                raise ValueError("Invalid test invasion session")
            store_type = SandboxStore
            options.update(session_id=session_id, owner_id=self.actor.member_id)
        return store_type(self.path, self.reference.geography, self.reference.catalogue, self.reference.operations, **options)

    def current(self) -> dict | None:
        if not self.path.exists():
            return None
        state = self._store().snapshot(datetime.now().astimezone())
        session = state.get("sandbox")
        return session if session and session.get("active") else None

    def start(self, subsector_id: str, now: datetime) -> str:
        store = self._store()
        with store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = store._load(connection)
            if previous.get("sandbox", {}).get("active"):
                raise ValueError("Revert the existing test invasion before starting another")
            state = _initial_state()
            state["test_notices"] = previous.get("test_notices", [])
            state["test_audit"] = previous.get("test_audit", [])
            session_id = uuid.uuid4().hex
            state["sandbox"] = {"id": session_id, "owner_id": self.actor.member_id, "active": True, "clock": utc_time(now).isoformat(), "subsector_id": subsector_id}
            store._open_campaign(state, subsector_id, now)
            state["test_audit"].append({"action": "start", "session_id": session_id, "actor": self.actor.member_id, "at": now.isoformat()})
            store._save(connection, state)
        return session_id

    def context(self, session_id: str, now: datetime) -> tuple[GalacticWarStore, datetime]:
        store = self._store(session_id)
        state = store.snapshot(now)
        return store, max(utc_time(now), datetime.fromisoformat(state["sandbox"]["clock"]))

    def execute(self, session_id: str, action: str, payload: dict, members: dict, now: datetime) -> None:
        if action == "revert_test_invasion":
            self.revert(session_id, now)
            return
        store, clock = self.context(session_id, now)
        if action in {"simulate_completion", "advance_test_time", "send_test_notice"}:
            with store._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                state = store._load(connection)
                if type(payload.get("version")) is not int or payload["version"] != state["version"]:
                    raise ValueError("State changed; refresh before submitting")
                if action == "advance_test_time":
                    if type(payload.get("hours")) is not int or payload["hours"] not in {1, 24, 72, 336}:
                        raise ValueError("Choose a supported test time advance")
                    clock += timedelta(hours=payload["hours"])
                    state["sandbox"]["clock"] = clock.isoformat()
                elif action == "send_test_notice":
                    if not any(job["session_id"] == session_id for job in state.get("test_notices", [])):
                        sector = next(entry for entry in store.geography["sectors"] if entry["id"] == state["sandbox"]["subsector_id"])
                        campaign = state["campaigns"][sector["id"]]
                        state.setdefault("test_notices", []).append({
                            "session_id": session_id, "sector_name": sector.get("name", sector["id"]),
                            "campaign": {key: campaign[key] for key in ("id", "deadline", "balance", "status")},
                            "sent": False, "reverted": False, "closed": False,
                        })
                else:
                    offer = store._find_offer(state, payload.get("strike_id"), clock)
                    if offer["status"] != "available":
                        raise ValueError("Choose an available test strike")
                    team = next((identity for identity, system in state["assignments"].items() if system == offer["system_id"]), None)
                    if team is None:
                        raise ValueError("Assign a test kill team to this system before simulating a completion")
                    roster = [WarMember(f"test-{index}", frozenset({"Watch Brother", offer["required_rank"]} - {""}), team.split("-", 1)[0], team, platform="pc", watch_brother=True) for index in range(offer["capacity"])]
                    offer["participants"] = [member.member_id for member in roster]
                    result = store._lock_roster(state, offer, roster, clock)
                    attempt_id = result["attempt_id"]
                    if offer.get("recipe"):
                        state["reviews"][attempt_id] = {"status": "approved", "aar_id": f"test-{attempt_id}", "archive_pending": False}
                state["version"] += 1
                state.setdefault("test_audit", []).append({"action": action, "actor": self.actor.member_id, "session_id": session_id, "at": clock.isoformat()})
                store._save(connection, state)
            if action == "simulate_completion":
                store.settle(attempt_id, f"test-{attempt_id}", [member.member_id for member in roster], clock)
            elif action == "advance_test_time":
                store.tick(clock)
            return
        if action not in {"commit", "assign", "approve", "reject", "cancel"}:
            raise ValueError("Unsupported test invasion action")
        actor = replace(self.actor, roles=self.actor.roles | {"Watch Master"})
        store.execute(action, actor, payload, members, clock)

    def revert(self, session_id: str, now: datetime) -> None:
        store = self._store()
        with store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = store._load(connection)
            session = previous.get("sandbox", {})
            if session.get("id") != session_id or session.get("owner_id") != self.actor.member_id:
                raise ValueError("Unknown test invasion session")
            if not session.get("active"):
                return
            state = _initial_state()
            state["sandbox"] = {**session, "active": False}
            state["test_notices"] = previous.get("test_notices", [])
            for job in state["test_notices"]:
                if job["session_id"] == session_id:
                    job["reverted"] = True
            state["test_audit"] = previous.get("test_audit", []) + [{"action": "revert", "actor": self.actor.member_id, "session_id": session_id, "at": now.isoformat()}]
            state["version"] = previous["version"] + 1
            store._save(connection, state)