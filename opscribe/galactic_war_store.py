"""Transactional campaign state; legacy directive JSON files are never mutated."""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from .galactic_war_recipes import VanillaRecipes
from .galactic_war import (
    CAMPAIGN_DURATION, Contribution, WarMember, advance_balance,
    command_renown, formation_renown, full_commitment_credit, lifetime_laurel,
    pressure, utc_time, Renown, atlas_compatible,
    SPECIALIST_TRACKS, REQUIREMENT_WEIGHTS, specialist_requirement, meets_specialist_requirement,
    WarReportPending,
)

BATTLE_LINE = (
    "Watch Brother", "Watch Veteran", "Oathsworn", "Watch Sergeant",
    "Veteran Sergeant", "Watch Lieutenant", "Watch Captain", "Watch Master",
)
REPORT_GRACE = timedelta(hours=24)


def _identity() -> str:
    return uuid.uuid4().hex


def _meets_rank(member: WarMember, rank: str) -> bool:
    return meets_specialist_requirement(member, rank)


def _initial_state() -> dict:
    return {
        "schema_version": 1, "version": 0, "campaigns": {}, "commitments": {},
        "assignments": {}, "proposals": {}, "moves": {}, "boards": {},
        "attempts": {}, "settlements": {}, "contributions": [], "outbox": {},
        "audit": [], "archive": [], "reviews": {},
    }


class GalacticWarStore:
    def __init__(self, path: str | Path, geography: dict, catalogue: list[dict], operations: list[dict] | None = None, *, recipes: VanillaRecipes | None = None):
        self.path = Path(path)
        self.geography = geography
        self.catalogue = catalogue
        self.operations = list(operations or [])
        self.recipes = recipes

    @contextmanager
    def _connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=15)
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("CREATE TABLE IF NOT EXISTS war_state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _load(self, connection: sqlite3.Connection) -> dict:
        row = connection.execute("SELECT payload FROM war_state WHERE id=1").fetchone()
        state = json.loads(row[0]) if row else _initial_state()
        if state.get("schema_version") != 1:
            raise ValueError("Unsupported Galactic War state version")
        state.setdefault("reviews", {})
        return state

    def snapshot(self, now: datetime) -> dict:
        with self._connection() as connection:
            state = self._load(connection)
        for campaign in state["campaigns"].values():
            if campaign["status"] == "contested":
                advance_balance(campaign, now)
        return state

    def execute(
        self, action: str, actor: WarMember, payload: dict,
        members: Mapping[str, WarMember], now: datetime,
    ) -> dict:
        now = utc_time(now)
        if not actor.active:
            raise PermissionError("An active Watch member is required")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self._load(connection)
            if type(payload.get("version")) is not int or payload["version"] != state["version"]:
                raise ValueError("State changed; refresh before submitting")
            handlers = {
                "invade": self._invade, "commit": self._commit,
                "approve": self._approve, "reject": self._reject,
                "assign": self._assign, "join": self._join, "leave": self._leave,
                "cancel": self._cancel,
                "review": self._review, "reverse": self._reverse,
            }
            if action not in handlers:
                raise ValueError("Unknown Galactic War action")
            result = handlers[action](state, actor, payload, members, now)
            state["version"] += 1
            state["audit"].append({"action": action, "actor": actor.member_id, "at": now.isoformat(), "reason": str(payload.get("reason", ""))[:500], "company_id": payload.get("company_id"), "kill_team_id": payload.get("kill_team_id"), "system_id": payload.get("system_id"), "strike_id": payload.get("strike_id"), "decision": payload.get("decision")})
            self._save(connection, state)
            return {"ok": True, "version": state["version"], **result}

    @staticmethod
    def _save(connection: sqlite3.Connection, state: dict) -> None:
        connection.execute(
            "INSERT INTO war_state(id,payload) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            (json.dumps(state, separators=(",", ":")),),
        )

    @staticmethod
    def _notice(state: dict, event: str, target: str, now: datetime) -> None:
        identity = _identity()
        state["outbox"][identity] = {"event": event, "target": target, "at": now.isoformat(), "delivered": False}
        campaign = state["campaigns"].get(target)
        if campaign:
            state["outbox"][identity]["campaign"] = {key: campaign[key] for key in ("id", "deadline", "balance", "status")}

    def _campaign(self, state: dict, subsector: str, now: datetime) -> dict:
        campaign = state["campaigns"].get(subsector)
        if not campaign or campaign["status"] != "contested" or now >= datetime.fromisoformat(campaign["deadline"]):
            raise ValueError("Subsector has no open campaign")
        return campaign

    def _invade(self, state, actor, payload, members, now):
        if not actor.has_role("Watch Master", "Forgemaster"):
            raise PermissionError("Only Watch Master or Forge Master may declare an invasion")
        subsector = str(payload.get("subsector_id", ""))
        self._open_campaign(state, subsector, now)
        return {"subsector_id": subsector}

    def _open_campaign(self, state: dict, subsector: str, now: datetime, escalation: float = 1) -> None:
        sectors = {sector["id"] for sector in self.geography.get("sectors", [])}
        if subsector not in sectors:
            raise ValueError("Unknown subsector")
        old = state["campaigns"].get(subsector)
        if old and old["status"] == "contested":
            raise ValueError("Subsector is already contested")
        planets = [planet for planet in self.geography.get("planets", []) if planet["subsector_id"] == subsector]
        if not planets:
            raise ValueError("Subsector needs classified playable planets before invasion")
        campaign_id = _identity()
        boards = {}
        for planet in planets:
            boards[planet["id"]] = [
                self._offer(campaign_id, planet, slot, 0, pool=pool)
                for pool in ("vanilla", "omega") for slot in range(4)
            ]
        if old:
            state["archive"].append(old)
        state["campaigns"][subsector] = {
            "id": campaign_id, "subsector_id": subsector, "faction": "tyranids",
            "status": "contested", "started_at": now.isoformat(),
            "deadline": (now + CAMPAIGN_DURATION).isoformat(), "balance": 25.0,
            "last_success_at": now.isoformat(), "calculated_at": now.isoformat(),
            "escalation": old["escalation"] if old and old["status"] == "lost" else escalation,
            "policy_version": 3,
        }
        state["boards"].update(boards)
        self._notice(state, "invasion", subsector, now)

    def _offer(self, campaign_id: str, planet: dict, slot: int, generation: int, *, pool: str = "vanilla") -> dict:
        if pool not in {"vanilla", "omega"}:
            raise ValueError("Unknown strike pool")
        compatible = sorted([
            entry for entry in self.catalogue
            if entry.get("content_kind", "vanilla_briefing") == "vanilla_briefing"
            and atlas_compatible(planet.get("world_type", ""), planet.get("biome", ""), entry)
        ], key=lambda item: item["id"])
        by_rank = {}
        for entry in compatible:
            by_rank.setdefault(entry.get("required_rank", ""), []).append(entry)
        specialist_ranks = [rank for rank in REQUIREMENT_WEIGHTS if rank and rank in by_rank]
        if not by_rank.get("") or not specialist_ranks:
            raise ValueError("Planet needs compatible general and specialist briefings")
        fallback_ranks = [track[0] for track in SPECIALIST_TRACKS if track[0] in by_rank] or specialist_ranks
        mode = "omega" if pool == "omega" else "ops_strat"
        operations = [operation for operation in self.operations if operation.get("mode") == mode]
        if not operations:
            raise ValueError(f"No supported {pool} operation formats are configured")
        seed = f"dual-pool-v4:{campaign_id}:{planet['id']}:{pool}:{slot}:{generation}"
        generator = random.Random(hashlib.sha256(seed.encode()).hexdigest())
        roll = generator.randrange(200)
        rolled_rank = specialist_requirement(roll)
        rank = rolled_rank
        if rank and rank not in by_rank:
            track = next(track for track in SPECIALIST_TRACKS if rank in track)
            lower = [candidate for candidate in reversed(track[:track.index(rank)]) if candidate in by_rank]
            rank = lower[0] if lower else generator.choices(fallback_ranks, weights=[REQUIREMENT_WEIGHTS[item] for item in fallback_ranks], k=1)[0]
        entry = generator.choice(by_rank[rank])
        recipe = None
        if pool == "vanilla" and self.recipes is not None:
            operation, recipe = self.recipes.select(planet["world_type"], planet["biome"], entry, operations)
        else:
            operation = generator.choice(operations)
        if operation.get("mode") not in {"ops_strat", "omega"} or operation.get("capacity") != (5 if operation["mode"] == "omega" else 3):
            raise ValueError("Invalid vanilla operation format")
        return {
            "id": f"GW-{_identity()}", "campaign_id": campaign_id,
            "planet_id": planet["id"], "system_id": planet["system_id"],
            "subsector_id": planet["subsector_id"], "pool": pool, "slot": slot, "generation": generation,
            "objective_id": entry["id"], "name": entry["name"], "content_kind": "vanilla",
            "content_version": entry.get("version", 1), "required_rank": rank,
            "requirement_roll": roll, "rolled_required_rank": rolled_rank,
            "briefing": entry.get("theme", entry["name"]),
            "capacity": operation["capacity"], "status": "available", "participants": [],
            "aar_mission": operation["mission"], "aar_difficulty_class": operation["difficulty_class"],
            "aar_mode": operation["mode"], "aar_mission_value": operation["mission_value"],
            "aar_difficulty": operation["difficulty"], "game_planet": operation.get("game_planet", ""),
            **({"recipe": recipe} if recipe is not None else {}),
        }

    def _move(self, state: dict, key: str, now: datetime) -> None:
        history = state["moves"].setdefault(key, [])
        history[:] = [stamp for stamp in history if datetime.fromisoformat(stamp) > now - CAMPAIGN_DURATION]
        if len(history) >= 2:
            raise ValueError("Two redeployments are already used in the last 14 days")
        history.append(now.isoformat())

    def _apply_commitment(self, state, company, subsector, now):
        current = state["commitments"].get(company)
        if current == subsector:
            raise ValueError("Company is already committed here")
        if current is not None or f"company:{company}" in state["moves"]:
            self._move(state, f"company:{company}", now)
        else:
            state["moves"][f"company:{company}"] = []
        state["commitments"][company] = subsector
        state["assignments"] = {key: value for key, value in state["assignments"].items() if not key.startswith(f"{company}-")}
        self._notice(state, "company_committed", company, now)

    def _commit(self, state, actor, payload, members, now):
        company = str(payload.get("company_id", ""))
        subsector = str(payload.get("subsector_id", ""))
        if not company or not (
            actor.has_role("Watch Master") or actor.company_id == company
            and actor.has_role("Watch Captain", "Watch Lieutenant")
        ):
            raise PermissionError("Own-company Captain or Lieutenant authority is required")
        campaign = state["campaigns"].get(subsector)
        if campaign and campaign["status"] == "lost":
            if actor.has_role("Watch Lieutenant") and not actor.has_role("Watch Captain", "Watch Master"):
                pass
            else:
                self._open_campaign(state, subsector, now)
        else:
            self._campaign(state, subsector, now)
        if actor.has_role("Watch Lieutenant") and not actor.has_role("Watch Captain", "Watch Master"):
            proposal_id = _identity()
            state["proposals"][proposal_id] = {
                "id": proposal_id, "company_id": company, "subsector_id": subsector,
                "requester": actor.member_id, "previous": state["commitments"].get(company),
                "campaign_id": campaign["id"] if campaign else "",
                "status": "pending", "created_at": now.isoformat(),
            }
            self._notice(state, "approval_requested", company, now)
            return {"proposal_id": proposal_id}
        self._apply_commitment(state, company, subsector, now)
        return {}

    def _proposal(self, state, actor, payload):
        proposal = state["proposals"].get(str(payload.get("proposal_id", "")))
        if not proposal or proposal["status"] != "pending":
            raise ValueError("No pending proposal")
        if actor.member_id == proposal["requester"] or not (
            actor.has_role("Watch Master") or actor.company_id == proposal["company_id"] and actor.has_role("Watch Captain")
        ):
            raise PermissionError("Own-company Captain or Watch Master approval is required")
        return proposal

    def _approve(self, state, actor, payload, members, now):
        proposal = self._proposal(state, actor, payload)
        requester = members.get(proposal["requester"])
        if not requester or not requester.active or requester.company_id != proposal["company_id"] or not requester.has_role("Watch Lieutenant"):
            raise ValueError("Requester no longer holds the required company office")
        if state["commitments"].get(proposal["company_id"]) != proposal["previous"]:
            raise ValueError("Company commitment changed since this proposal")
        if now - datetime.fromisoformat(proposal["created_at"]) > timedelta(hours=24):
            raise ValueError("Proposal expired")
        campaign = state["campaigns"].get(proposal["subsector_id"])
        if not campaign or campaign["id"] != proposal["campaign_id"]:
            raise ValueError("Campaign changed since this proposal")
        if campaign and campaign["status"] == "lost":
            self._open_campaign(state, proposal["subsector_id"], now)
        self._campaign(state, proposal["subsector_id"], now)
        self._apply_commitment(state, proposal["company_id"], proposal["subsector_id"], now)
        proposal["status"] = "approved"
        return {}

    def _reject(self, state, actor, payload, members, now):
        proposal = self._proposal(state, actor, payload)
        proposal["status"] = "rejected"
        return {}

    def _assign(self, state, actor, payload, members, now):
        kill_team = str(payload.get("kill_team_id", ""))
        company = kill_team.split("-", 1)[0]
        team_members = [member for member in members.values() if member.kill_team_id == kill_team and member.company_id == company]
        if not team_members:
            raise ValueError("Unknown kill team")
        officer = actor.has_role("Watch Master") or actor.company_id == company and actor.has_role("Watch Captain", "Watch Lieutenant")
        sergeant = actor.kill_team_id == kill_team and actor.has_role("Watch Sergeant", "Veteran Sergeant")
        if not (officer or sergeant):
            raise PermissionError("Kill-team Sergeant or covering officer authority is required")
        if officer and not sergeant and not str(payload.get("reason", "")).strip():
            raise ValueError("A reason for covering the Sergeant is required")
        subsector = state["commitments"].get(company, "")
        self._campaign(state, subsector, now)
        system = str(payload.get("system_id", ""))
        if not any(item["id"] == system and item.get("sectorId") == subsector for item in self.geography.get("systems", [])):
            raise ValueError("System must be inside the committed subsector")
        current = state["assignments"].get(kill_team)
        if current == system:
            raise ValueError("Kill team is already assigned here")
        if current is not None:
            self._move(state, f"kt:{kill_team}", now)
        state["assignments"][kill_team] = system
        self._notice(state, "kill_team_assigned", kill_team, now)
        return {}

    def _find_offer(self, state, offer_id, now):
        for board in state["boards"].values():
            for offer in board:
                if offer["id"] == offer_id:
                    self._campaign(state, offer["subsector_id"], now)
                    return offer
        raise ValueError("Unknown strike")

    def validate_signup(self, state, actor, offer, members, now):
        if not actor.active:
            raise PermissionError("An active Watch member is required")
        if not actor.watch_brother:
            raise PermissionError("The Watch Brother role is required to sign up for a strike")
        self._campaign(state, offer["subsector_id"], now)
        if offer["status"] not in {"available", "recruiting"}:
            raise ValueError("Strike roster is locked")
        if any(actor.member_id in item["participants"] for board in state["boards"].values() for item in board if item["status"] in {"recruiting", "locked", "deployed", "review_pending"}):
            raise ValueError("Already signed up for a strike")
        if len(offer["participants"]) >= offer["capacity"]:
            raise ValueError("Strike is full")
        projected = [members.get(identity) for identity in offer["participants"]] + [actor]
        if any(member is None or not member.active for member in projected):
            raise ValueError("Strike roster membership changed; refresh the signups")
        if any(not member.watch_brother for member in projected):
            raise PermissionError("Every signed-up player must retain the Watch Brother role before the roster locks")
        if offer.get("aar_mode") == "omega":
            if any(member.platform not in {"pc", "console"} for member in projected):
                raise ValueError("Omega signups require a PC or Console platform role")
            if sum(member.platform == "console" for member in projected) > 2:
                raise ValueError("Omega permits at most two console players")
        if len(projected) == offer["capacity"] and offer["required_rank"] and not any(_meets_rank(member, offer["required_rank"]) for member in projected):
            raise ValueError("The remaining slot must fulfill the required rank")
        return projected

    def signup_availability(self, state, actor, offer, members, now) -> dict:
        covered = not offer["required_rank"] or any(
            member is not None and member.active and member.watch_brother and _meets_rank(member, offer["required_rank"])
            for member in (members.get(identity) for identity in offer["participants"])
        )
        if actor.member_id in offer["participants"]:
            return {"can_join": False, "reason": "You are signed up for this strike", "specialist_fulfilled": covered}
        try:
            self.validate_signup(state, actor, offer, members, now)
        except (ValueError, PermissionError) as error:
            return {"can_join": False, "reason": str(error), "specialist_fulfilled": covered}
        return {"can_join": True, "reason": "", "specialist_fulfilled": covered}

    def _join(self, state, actor, payload, members, now):
        if not actor.watch_brother:
            raise PermissionError("The Watch Brother role is required to sign up for a strike")
        offer = self._find_offer(state, payload.get("strike_id"), now)
        projected = self.validate_signup(state, actor, offer, members, now)
        offer["participants"].append(actor.member_id)
        offer["status"] = "recruiting"
        offer.setdefault("recruitment_expires_at", (now + timedelta(hours=1)).isoformat())
        if len(projected) == offer["capacity"]:
            return self._lock_roster(state, offer, projected, now)
        return {}

    def _leave(self, state, actor, payload, members, now):
        offer = self._find_offer(state, payload.get("strike_id"), now)
        if offer["status"] not in {"available", "recruiting"}:
            raise ValueError("Roster is locked; cancel the attempt to release the signups")
        if actor.member_id not in offer["participants"]:
            raise ValueError("Not signed up for this strike")
        offer["participants"].remove(actor.member_id)
        offer["status"] = "recruiting" if offer["participants"] else "available"
        if not offer["participants"]:
            offer.pop("recruitment_expires_at", None)
        return {}

    def _lock_roster(self, state, offer, roster, now):
        required = offer["required_rank"]
        qualified = [member.member_id for member in roster if required and _meets_rank(member, required)]
        if required and not qualified:
            raise ValueError("Required rank is not fulfilled")
        specialist_ids = qualified[:1]
        attempt_id = f"GW-{_identity()}"
        offer["status"] = "locked"
        offer["attempt_id"] = attempt_id
        offer["specialist_id"] = specialist_ids[0] if specialist_ids else ""
        offer.pop("recruitment_expires_at", None)
        state["attempts"][attempt_id] = {
            "id": attempt_id, "offer": dict(offer), "locked_at": now.isoformat(),
            "status": "locked", "participants": [{
                "member_id": member.member_id, "formation_id": member.formation_id,
                "full_credit": full_commitment_credit(member, offer["subsector_id"], offer["system_id"], state["commitments"], state["assignments"], specialist_ids),
            } for member in roster],
        }
        self._notice(state, "roster_locked", attempt_id, now)
        return {"attempt_id": attempt_id}

    def _cancel(self, state, actor, payload, members, now):
        offer = self._find_offer(state, payload.get("strike_id"), now)
        attempt = state["attempts"].get(offer.get("attempt_id"))
        if not attempt or attempt["status"] not in {"locked", "deployed"}:
            raise ValueError("No locked strike roster to cancel")
        if actor.member_id not in offer["participants"] and not actor.has_role("Watch Master"):
            raise PermissionError("Only a signed-up participant or Watch Master may cancel this roster")
        if attempt.get("report_key"):
            raise ValueError("An AAR is already being processed for this strike")
        reason = str(payload.get("reason", "")).strip()
        if not reason:
            raise ValueError("A cancellation reason is required")
        attempt.update(status="cancelled", cancelled_by=actor.member_id, cancelled_at=now.isoformat(), reason=reason[:300])
        offer.update(status="available", participants=[])
        offer.pop("attempt_id", None)
        offer.pop("specialist_id", None)
        self._notice(state, "roster_cancelled", attempt["id"], now)
        return {}

    def validate_report(self, attempt_id: str, record: dict, now: datetime, *, check_identity: bool = True) -> None:
        state = self.snapshot(now)
        attempt = state["attempts"].get(attempt_id)
        if not attempt:
            raise ValueError("Unknown locked strike ID")
        if attempt["status"] in {"cancelled", "rejected", "reversed"}:
            raise ValueError(f"Strike report is {attempt['status']}")
        participant_ids = [str(identity) for identity in record.get("brother_ids", [])]
        if len(participant_ids) != len(set(participant_ids)) or sorted(participant_ids) != sorted(member["member_id"] for member in attempt["participants"]):
            raise ValueError("AAR participants do not match the locked signup roster")
        if record.get("mission", "").casefold() != attempt["offer"]["aar_mission"].casefold() or record.get("difficulty_class") != attempt["offer"]["aar_difficulty_class"]:
            raise ValueError("AAR mission or difficulty does not match the strike")
        if record.get("strike_success", True) is not True:
            raise ValueError("Only completed strikes can be reported; cancel an uncompleted roster instead")
        locked_at = attempt.get("locked_at") or attempt.get("deployed_at")
        if record.get("timestamp") and datetime.fromisoformat(record["timestamp"]) < datetime.fromisoformat(locked_at):
            raise ValueError("AAR predates the locked signup roster")
        if attempt["offer"].get("aar_mode") == "omega" and bool(record.get("omega_strat_difficulty_role_present")) != (attempt["offer"].get("aar_difficulty") == "@Omega-Strat"):
            raise ValueError("AAR Omega difficulty does not match the strike")
        existing = state["settlements"].get(attempt_id)
        if check_identity and existing and existing["aar_id"] != str(record.get("aar_id")):
            raise ValueError("Strike ID has already been reported in another AAR")
        if existing and existing["success"] is not True:
            raise ValueError("A settled outcome requires an audited credit correction")

    def reserve_report(self, attempt_id: str, report_key: str, now: datetime | None = None) -> None:
        received_at = utc_time(now or datetime.now(timezone.utc))
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self._load(connection)
            attempt = state["attempts"].get(attempt_id)
            if not attempt or attempt["status"] not in {"locked", "deployed"}:
                raise ValueError("Unknown locked strike ID")
            if attempt.get("report_key", report_key) != report_key:
                raise ValueError("This strike already has an AAR submission; retry the original report")
            attempt["report_key"] = report_key
            attempt.setdefault("report_received_at", received_at.isoformat())
            self._save(connection, state)

    @staticmethod
    def _report_fingerprint(record: dict) -> str:
        facts = {key: record.get(key) for key in ("aar_id", "strike_id", "mission", "difficulty_class", "strike_success", "omega_strat_difficulty_role_present")}
        facts["brother_ids"] = sorted(str(identity) for identity in record.get("brother_ids", []))
        facts["evidence"] = sorted(record.get("strike_evidence_hashes") or [])
        return hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()

    def submit_report(self, record: dict, now: datetime) -> dict:
        now = utc_time(now)
        record = {"strike_success": True, **record}
        attempt_id = record["strike_id"]
        self.validate_report(attempt_id, record, now)
        fingerprint = self._report_fingerprint(record)
        pending = False
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self._load(connection)
            attempt = state["attempts"][attempt_id]
            existing = state["settlements"].get(attempt_id)
            if existing and existing["success"] != record["strike_success"]:
                raise ValueError("Strike has already been settled with a different result")
            if attempt.get("report_fingerprint", fingerprint) != fingerprint:
                raise ValueError("Strike report facts changed; staff reconciliation is required")
            attempt["report_fingerprint"] = fingerprint
            attempt["report_submitter_id"] = str(record.get("submitter_id") or "")
            attempt.setdefault("report_received_at", now.isoformat())
            review = state["reviews"].get(attempt_id)
            if review and review["status"] == "rejected":
                raise ValueError("Strike report was rejected by evidence review")
            hashes = record.get("strike_evidence_hashes") or []
            reused = any(
                set(hashes).intersection(other.get("evidence_hashes", []))
                for identity, other in state["attempts"].items() if identity != attempt_id
            )
            attempt["evidence_hashes"] = list(hashes)
            needs_recipe_review = bool(attempt["offer"].get("recipe"))
            if review is None and (reused or needs_recipe_review) and existing is None:
                review = {
                    "strike_id": attempt_id, "aar_id": str(record["aar_id"]),
                    "status": "pending", "reason": "Evidence image reused by another strike report" if reused else "Verify the locked Vanilla recipe stratagems and operation result against the evidence",
                    "record": record, "received_at": attempt["report_received_at"],
                    "archive_pending": False,
                }
                state["reviews"][attempt_id] = review
                attempt["status"] = "review_pending"
                for board in state["boards"].values():
                    for offer in board:
                        if offer.get("attempt_id") == attempt_id:
                            offer["status"] = "review_pending"
                self._notice(state, "evidence_review", attempt_id, now)
                state["version"] += 1
            pending = bool(review and review["status"] == "pending")
            self._save(connection, state)
        if pending:
            raise WarReportPending("Evidence is awaiting independent review; rewards have not been applied")
        return self.settle(attempt_id, str(record["aar_id"]), [str(identity) for identity in record["brother_ids"]], now, success=record["strike_success"])

    @staticmethod
    def _review_authority(actor: WarMember, attempt: dict) -> None:
        if not (actor.can_review or actor.has_role("Watch Master", "Forgemaster")):
            raise PermissionError("AAR review staff authority is required")
        if actor.member_id in {member["member_id"] for member in attempt["participants"]} or actor.member_id == attempt.get("report_submitter_id"):
            raise PermissionError("A participant cannot review their own strike")

    def _review(self, state, actor, payload, members, now):
        identity = str(payload.get("strike_id", ""))
        review = state["reviews"].get(identity)
        if not review or review["status"] != "pending":
            raise ValueError("No pending evidence review")
        attempt = state["attempts"][identity]
        self._review_authority(actor, attempt)
        reason = str(payload.get("reason", "")).strip()
        decision = payload.get("decision")
        if not reason or decision not in {"approve", "reject"}:
            raise ValueError("An approval or rejection and review reason are required")
        review.update(status="approved" if decision == "approve" else "rejected", reviewer=actor.member_id, decided_at=now.isoformat(), decision_reason=reason[:500], archive_pending=decision == "approve")
        attempt["status"] = "locked" if decision == "approve" else "rejected"
        if decision == "reject":
            campaign = state["campaigns"][attempt["offer"]["subsector_id"]]
            for offer in state["boards"].get(attempt["offer"]["planet_id"], []):
                if offer.get("attempt_id") == identity:
                    offer.update(status="available" if campaign["status"] == "contested" and now < datetime.fromisoformat(campaign["deadline"]) else "closed", participants=[])
                    offer.pop("attempt_id", None)
                    offer.pop("specialist_id", None)
        self._notice(state, "evidence_reviewed", identity, now)
        return {"review_status": review["status"]}

    def acknowledge_review_archive(self, attempt_id: str) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self._load(connection)
            state["reviews"][attempt_id]["archive_pending"] = False
            self._save(connection, state)

    def _reverse(self, state, actor, payload, members, now):
        identity = str(payload.get("strike_id", ""))
        attempt = state["attempts"].get(identity)
        result = state["settlements"].get(identity)
        if not attempt or not result or result.get("reversed"):
            raise ValueError("No unreversed strike result")
        self._review_authority(actor, attempt)
        reason = str(payload.get("reason", "")).strip()
        if not reason:
            raise ValueError("A reversal reason is required")
        result.update(reversed=True, reversed_by=actor.member_id, reversed_at=now.isoformat(), reversal_reason=reason[:500])
        attempt["status"] = "reversed"
        state["contributions"] = [item for item in state["contributions"] if item["strike_id"] != identity]
        campaign = state["campaigns"][attempt["offer"]["subsector_id"]]
        if campaign["id"] == attempt["offer"]["campaign_id"] and campaign["status"] == "contested":
            self._recalculate_campaign(state, campaign, now)
        self._notice(state, "strike_credit_reversed", identity, now)
        return {"reversed": identity}

    def acknowledge_notice(self, notice_id: str, message_id: str) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self._load(connection)
            notice = state["outbox"][notice_id]
            notice.update(delivered=True, message_id=message_id)
            self._save(connection, state)

    @staticmethod
    def _recalculate_campaign(state: dict, campaign: dict, now: datetime) -> None:
        calculated = {
            **campaign, "balance": 25.0,
            "last_success_at": campaign["started_at"], "calculated_at": campaign["started_at"],
        }
        events = sorted([
            result for result in state["settlements"].values()
            if result.get("campaign_id") == campaign["id"] and result.get("campaign_credit")
            and not result.get("reversed")
        ], key=lambda result: result["strategic_at"])
        for event in events:
            at = datetime.fromisoformat(event["strategic_at"])
            advance_balance(calculated, at)
            gap = (at - datetime.fromisoformat(calculated["last_success_at"])).total_seconds() / 3600
            calculated["balance"] = min(100, calculated["balance"] + 1 / (calculated["escalation"] * pressure(gap)))
            calculated["last_success_at"] = at.isoformat()
        advance_balance(calculated, now)
        for key in ("balance", "last_success_at", "calculated_at"):
            campaign[key] = calculated[key]

    def settle(self, attempt_id: str, aar_id: str, participant_ids: list[str], now: datetime, *, success: bool = True) -> dict:
        now = utc_time(now)
        if len(participant_ids) != len(set(participant_ids)):
            raise ValueError("AAR participants must be unique")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self._load(connection)
            existing = state["settlements"].get(attempt_id)
            if existing:
                if existing["aar_id"] != aar_id or existing["success"] != success or existing["participant_ids"] != sorted(set(participant_ids)):
                    raise ValueError("Strike has already been settled with a different result")
                return existing
            if success is not True:
                raise ValueError("Only completed strikes can be settled")
            if any(result["aar_id"] == aar_id for result in state["settlements"].values()):
                raise ValueError("AAR is already linked to a different strike")
            attempt = state["attempts"].get(attempt_id)
            if not attempt:
                raise ValueError("Unknown locked strike ID")
            if attempt["offer"].get("recipe"):
                review = state["reviews"].get(attempt_id, {})
                if review.get("status") != "approved" or review.get("aar_id") != aar_id:
                    raise WarReportPending("The locked Vanilla recipe requires independent evidence approval before settlement")
            if attempt["status"] not in {"locked", "deployed"}:
                raise ValueError("Unknown locked strike ID")
            if sorted(set(participant_ids)) != sorted(member["member_id"] for member in attempt["participants"]):
                raise ValueError("AAR participants do not match the deployed roster")
            offer = attempt["offer"]
            campaign = state["campaigns"][offer["subsector_id"]]
            received_at = datetime.fromisoformat(attempt.get("report_received_at", now.isoformat()))
            deadline = datetime.fromisoformat(campaign["deadline"])
            active = campaign["status"] == "contested" and campaign["id"] == offer["campaign_id"] and received_at < deadline and now < deadline + REPORT_GRACE
            result = {
                "aar_id": aar_id, "success": success, "participant_ids": sorted(set(participant_ids)),
                "campaign_id": offer["campaign_id"], "campaign_credit": bool(success and active),
                "strategic_at": received_at.isoformat(), "processed_at": now.isoformat(),
            }
            attempt["status"] = "completed"
            state["settlements"][attempt_id] = result
            state["contributions"].extend({
                **member, "strike_id": attempt_id, "completed_at": received_at.isoformat(),
            } for member in attempt["participants"])
            if active:
                board = state["boards"][offer["planet_id"]]
                board_index = next(index for index, current in enumerate(board) if current["id"] == offer["id"])
                self._recalculate_campaign(state, campaign, now)
                if now < deadline:
                    planet = next(planet for planet in self.geography["planets"] if planet["id"] == offer["planet_id"])
                    pool = offer.get("pool", "omega" if offer.get("aar_mode") == "omega" else "vanilla")
                    board[board_index] = self._offer(offer["campaign_id"], planet, offer["slot"], offer["generation"] + 1, pool=pool)
                else:
                    board[board_index]["status"] = "closed"
            state["version"] += 1
            self._save(connection, state)
            return result

    def recognition(self, rosters: dict[str, list[str]], company_teams: dict[str, list[str]], now: datetime) -> dict:
        state = self.snapshot(now)
        contributions = [Contribution(**{**item, "completed_at": datetime.fromisoformat(item["completed_at"])}) for item in state["contributions"]]
        formations = formation_renown(rosters, contributions, now)
        counts: dict[str, int] = {}
        for contribution in contributions:
            counts[contribution.member_id] = counts.get(contribution.member_id, 0) + 1
        return {
            "formations": {key: value.as_dict() for key, value in formations.items()},
            "companies": {key: command_renown(formations.get(team, Renown()) for team in teams).as_dict() for key, teams in company_teams.items()},
            "members": {key: {"completed": count, "laurel": lifetime_laurel(count)} for key, count in counts.items()},
        }

    def tick(self, now: datetime) -> dict:
        now = utc_time(now)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self._load(connection)
            changed = False
            for board in state["boards"].values():
                for offer in board:
                    if offer["status"] == "recruiting" and now >= datetime.fromisoformat(offer["recruitment_expires_at"]):
                        offer.update(status="available", participants=[])
                        offer.pop("coordinator", None)
                        offer.pop("recruitment_expires_at", None)
                        changed = True
            for campaign in list(state["campaigns"].values()):
                if campaign["status"] != "contested":
                    continue
                advance_balance(campaign, now)
                if now < datetime.fromisoformat(campaign["deadline"]):
                    continue
                deadline = datetime.fromisoformat(campaign["deadline"])
                pending = any(
                    attempt["offer"]["campaign_id"] == campaign["id"]
                    and attempt["status"] in {"locked", "deployed", "review_pending"}
                    and attempt.get("report_received_at")
                    and datetime.fromisoformat(attempt["report_received_at"]) < deadline
                    for attempt in state["attempts"].values()
                )
                if pending and now < deadline + REPORT_GRACE:
                    campaign["resolution_pending"] = True
                    continue
                changed = True
                campaign.pop("resolution_pending", None)
                campaign["status"] = "secure" if campaign["balance"] >= 50 else "lost"
                campaign["resolved_at"] = now.isoformat()
                for board in state["boards"].values():
                    for offer in board:
                        if offer["campaign_id"] == campaign["id"]:
                            offer["status"] = "closed"
                self._notice(state, campaign["status"], campaign["subsector_id"], now)
                if campaign["status"] != "lost":
                    continue
                neighbors = self.geography.get("neighbors", {}).get(campaign["subsector_id"], [])
                candidates = [neighbor for neighbor in neighbors if state["campaigns"].get(neighbor, {}).get("status", "secure") == "secure"]
                if not candidates:
                    continue
                destination = random.SystemRandom().choice(sorted(candidates))
                try:
                    self._open_campaign(state, destination, now, min(3.375, campaign["escalation"] * 1.5))
                except ValueError as error:
                    campaign["spread_blocked"] = str(error)
                else:
                    campaign["spread_to"] = destination
            if changed:
                state["version"] += 1
            self._save(connection, state)
            return state