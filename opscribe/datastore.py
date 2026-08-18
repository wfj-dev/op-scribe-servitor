import os
import json
import asyncio
import math
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterator, Optional

from .constants import (
    OP_RATING_BASELINE,
    OP_RATING_DECAY_HALF_LIFE_DAYS,
    OP_RATING_MAX,
    OP_RATING_MIN,
    OP_RATING_SCALE_K,
    OP_RATING_SOFT_CAP_ENABLED,
    OP_RATING_SOFT_CAP_TAU,
    OP_RATING_STRIKE_BONUS_FACTOR,
    OP_RATING_VOLUME_BETA,
    OP_RATING_WEIGHT_ABSOLUTE_OPS,
    OP_RATING_WEIGHT_HARD_SIEGE,
    OP_RATING_WEIGHT_HARD_STRATAGEM,
    OP_RATING_WEIGHT_LETHAL_OPS,
    OP_RATING_WEIGHT_NORMAL_SIEGE,
    OP_RATING_WEIGHT_NORMAL_STRATAGEM,
    OP_RATING_WEIGHT_OMEGA_OPS,
    OP_RATING_WEIGHT_OMEGA_STRAT,
    OP_RATING_WEIGHT_RUTHLESS_OPS,
    OP_RATING_WINDOW_DAYS,
)


def _parse_timestamp_utc(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value)
    except Exception:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    try:
        return ts.astimezone(timezone.utc)
    except Exception:
        return None


def _event_bucket_weight(record: dict) -> Optional[float]:
    dclass = str(record.get("difficulty_class") or "").lower()
    if dclass == "absolute_ops":
        return OP_RATING_WEIGHT_ABSOLUTE_OPS
    if dclass == "hard_siege":
        return OP_RATING_WEIGHT_HARD_SIEGE
    if dclass == "hard_stratagem":
        return OP_RATING_WEIGHT_HARD_STRATAGEM
    if dclass == "omega_ops":
        if bool(record.get("omega_strat_difficulty_role_present")):
            return OP_RATING_WEIGHT_OMEGA_STRAT
        return OP_RATING_WEIGHT_OMEGA_OPS
    if dclass == "normal_siege":
        return OP_RATING_WEIGHT_NORMAL_SIEGE
    if dclass == "normal_stratagem":
        return OP_RATING_WEIGHT_NORMAL_STRATAGEM
    if dclass == "lethal_ops":
        return OP_RATING_WEIGHT_LETHAL_OPS
    if dclass == "ruthless_ops":
        return OP_RATING_WEIGHT_RUTHLESS_OPS
    return None


def _record_is_strike_linked(record: dict) -> bool:
    if record.get("target_package_id"):
        return True
    pkg_ids = record.get("target_package_ids") or []
    if isinstance(pkg_ids, list) and len(pkg_ids) > 0:
        return True
    return False


def _apply_operational_soft_cap(raw_score: float) -> float:
    """Apply a smooth asymptotic ceiling above baseline to reduce top-end crowding.

    Scores at or below baseline are left unchanged. Above baseline, the score
    is mapped toward OP_RATING_MAX using an exponential approach curve.
    """
    if (not OP_RATING_SOFT_CAP_ENABLED) or raw_score <= float(OP_RATING_BASELINE):
        return raw_score
    span = float(OP_RATING_MAX - OP_RATING_BASELINE)
    if span <= 0.0:
        return raw_score
    tau = max(1.0, float(OP_RATING_SOFT_CAP_TAU))
    return float(OP_RATING_BASELINE) + (span * (1.0 - math.exp(-(raw_score - float(OP_RATING_BASELINE)) / tau)))


def _compute_operational_rating_for_user_from_records(user_id: str, records: list[dict]) -> dict:
    events: list[tuple[datetime, float, int, bool]] = []
    for record in records:
        if str(record.get("aar_type") or "pve").lower() != "pve":
            continue
        brother_ids = [str(x) for x in (record.get("brother_ids") or [])]
        if user_id not in brother_ids:
            continue
        ts = _parse_timestamp_utc(record.get("timestamp"))
        if ts is None:
            continue
        bucket_weight = _event_bucket_weight(record)
        if bucket_weight is None:
            continue
        team_size = max(1, len(brother_ids))
        strike_linked = _record_is_strike_linked(record)
        events.append((ts, bucket_weight, team_size, strike_linked))

    if not events:
        return {
            "operational_rating": OP_RATING_BASELINE,
            "operational_rating_raw": float(OP_RATING_BASELINE),
            "operational_rating_delta": 0.0,
            "operational_rating_events": 0,
        }

    events.sort(key=lambda x: x[0])
    rolling_start = 0
    signed_sum = 0.0

    for idx, (event_ts, bucket_weight, team_size, strike_linked) in enumerate(events):
        cutoff = event_ts - timedelta(days=OP_RATING_WINDOW_DAYS)
        while rolling_start < idx and events[rolling_start][0] < cutoff:
            rolling_start += 1
        n_prior = idx - rolling_start
        volume_factor = 1.0 / (1.0 + (OP_RATING_VOLUME_BETA * n_prior))
        strike_factor = 1.0
        if bucket_weight > 0.0 and strike_linked:
            strike_factor = OP_RATING_STRIKE_BONUS_FACTOR
        contribution_share = 1.0 / float(team_size)
        signed_sum += bucket_weight * contribution_share * strike_factor * volume_factor

    raw_score = OP_RATING_BASELINE + (OP_RATING_SCALE_K * signed_sum)
    raw_score = _apply_operational_soft_cap(float(raw_score))
    raw_score = max(float(OP_RATING_MIN), min(float(OP_RATING_MAX), raw_score))

    now_utc = datetime.now(timezone.utc)
    last_event_ts = events[-1][0]
    idle_days = max(0.0, (now_utc - last_event_ts).total_seconds() / 86400.0)
    decay_lambda = math.log(2.0) / OP_RATING_DECAY_HALF_LIFE_DAYS
    decayed_score = OP_RATING_BASELINE + ((raw_score - OP_RATING_BASELINE) * math.exp(-decay_lambda * idle_days))
    decayed_score = max(float(OP_RATING_MIN), min(float(OP_RATING_MAX), decayed_score))

    return {
        "operational_rating": int(round(decayed_score)),
        "operational_rating_raw": float(raw_score),
        "operational_rating_delta": float(signed_sum),
        "operational_rating_events": len(events),
    }


def _compute_stats_for_user_from_records(user_id: str, records: list[dict]) -> dict:
    ops = 0
    aar_points = 0
    armory_raw = 0
    armory_points = 0
    gene_carries = 0
    gene_seed_points = 0
    waves_participated = 0
    last_aar_ts: Optional[str] = None
    for record in records:
        brother_ids = [str(x) for x in (record.get("brother_ids") or [])]
        if user_id in brother_ids:
            ops += 1
            difficulty_class = record.get("difficulty_class")

            # Track most recent AAR timestamp for this user
            ts = record.get("timestamp")
            if ts:
                if last_aar_ts is None or ts > last_aar_ts:
                    last_aar_ts = ts

            # Get armor + warp penalties for this user in this record
            armor_penalties = record.get("armor_penalties") or {}
            warp_penalties = record.get("warp_penalties") or {}
            user_penalty = int(armor_penalties.get(user_id, 0) or 0) + int(warp_penalties.get(user_id, 0) or 0)

            if difficulty_class in ("normal_siege", "hard_siege"):
                bw = record.get("brother_waves") or {}
                try:
                    my_waves = int(bw.get(user_id, 0) or 0)
                except Exception:
                    my_waves = 0
                if my_waves <= 0:
                    try:
                        my_waves = int(record.get("waves") or 0)
                    except Exception:
                        my_waves = 0
                if difficulty_class == "normal_siege":
                    base_pts = 3 * (my_waves // 5)
                else:
                    base_pts = 4 * (my_waves // 5)
                # Apply penalty (min 1 point if base > 0)
                if base_pts > 0:
                    aar_points += max(1, base_pts - user_penalty)
                else:
                    aar_points += base_pts
                waves_participated += my_waves
            else:
                base_pts = record.get("points_for_op", 0)
                # Apply penalty (min 1 point if base > 0)
                if base_pts > 0:
                    aar_points += max(1, base_pts - user_penalty)
                else:
                    aar_points += base_pts
            armory_data = record.get("armory_data")
            try:
                armory_raw += int(armory_data) if armory_data is not None else 0
            except ValueError:
                armory_raw += 0
            armory_points += record.get("armory_challenge_points", 0)
        status = (record.get("gene_seed_status") or "").lower()
        gene_carrier = record.get("gene_seed_carrier_id")
        effective_carried = status == "carried" or (gene_carrier is not None and status != "lost")
        if effective_carried:
            if gene_carrier == user_id:
                gene_carries += 1
                gene_seed_points += record.get("gene_seed_base_points_for_carrier", 0)
            elif user_id in brother_ids:
                gene_seed_points += 1
    op_rating = _compute_operational_rating_for_user_from_records(user_id, records)
    return {
        "ops": ops,
        "aar_points": aar_points,
        "armory_raw": armory_raw,
        "armory_points": armory_points,
        "gene_carries": gene_carries,
        "gene_seed_points": gene_seed_points,
        "waves_participated": waves_participated,
        "last_aar_ts": last_aar_ts,
        "operational_rating": op_rating["operational_rating"],
        "operational_rating_raw": op_rating["operational_rating_raw"],
        "operational_rating_delta": op_rating["operational_rating_delta"],
        "operational_rating_events": op_rating["operational_rating_events"],
    }


class DataStore:
    """
    In-memory data store for AAR records and processed IDs.
    Loads data once at startup and provides read/write accessors.
    Writes are write-behind: in-memory state is updated immediately, and disk is flushed in a background task every N seconds and on shutdown. Atomic writes and .bak backup are used. All writes are protected by an asyncio.Lock.
    """

    def _init_cache_stats(self):
        self._home_chapter_cache_hits = 0
        self._home_chapter_cache_misses = 0
        self._last_flush_time = None

    HOME_CHAPTER_TTL = 60 * 60 * 24 * 7  # 7 days in seconds

    def _now(self):
        import time

        return int(time.time())

    def _init_home_chapter_cache(self):
        self._home_chapter_cache: dict[str, tuple[str, int]] = {}  # user_id -> (chapter, expires_at)
        self._init_cache_stats()

    def _home_chapter_cache_get(self, user_id: str) -> Optional[str]:
        entry = self._home_chapter_cache.get(str(user_id))
        if entry:
            chapter, expires_at = entry
            if expires_at > self._now():
                self._home_chapter_cache_hits += 1
                return chapter
            else:
                del self._home_chapter_cache[str(user_id)]
        self._home_chapter_cache_misses += 1
        return None

    def _home_chapter_cache_set(self, user_id: str, chapter: str):
        self._home_chapter_cache[str(user_id)] = (
            chapter,
            self._now() + self.HOME_CHAPTER_TTL,
        )

    def get_home_chapter(self, user_id: str, force_refresh: bool = False) -> Optional[str]:
        """
        Return the cached home chapter for user_id if present and not expired.
        If not cached or force_refresh is True, scan history, cache, and return.
        Logs timing for expensive scan.
        """
        import time

        if not hasattr(self, "_home_chapter_cache"):
            self._init_home_chapter_cache()
        if not force_refresh:
            cached = self._home_chapter_cache_get(user_id)
            if cached is not None:
                return cached
        # Scan history for earliest home chapter
        t0 = time.perf_counter()
        chapter = None
        earliest_ts = None
        for rec in self._records.values():
            if str(user_id) in rec.get("brother_ids", []):
                ch = rec.get("home_chapter")
                ts = rec.get("timestamp")
                if ch and ts:
                    try:
                        from datetime import datetime

                        t = datetime.fromisoformat(ts)
                    except Exception:
                        continue
                    if earliest_ts is None or t < earliest_ts:
                        earliest_ts = t
                        chapter = ch
        t1 = time.perf_counter()
        if t1 - t0 > 0.05:
            import logging

            logging.getLogger("op-scribe-servitor").info(f"Home chapter scan for {user_id} took {t1 - t0:.3f}s")
        if chapter:
            self._home_chapter_cache_set(user_id, chapter)
        return chapter

    FLUSH_INTERVAL = 60  # seconds between background flushes

    def __init__(self, aar_records_path: str, processed_ids_path: str, acquisitions_path: str = None):
        self._aar_records_path = aar_records_path
        self._processed_ids_path = processed_ids_path
        self._acquisitions_path = acquisitions_path or os.path.join(os.path.dirname(aar_records_path), "challenge_role_acquisitions.json")
        self._records: Dict[str, dict] = {}
        self._processed_ids: set[str] = set()
        self._acquisitions: Dict[str, Dict[str, str]] = {}  # user_id -> {role_name -> iso8601_timestamp}
        self._dirty_records = False
        self._dirty_ids = False
        self._dirty_acquisitions = False
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._shutdown = False
        self.user_stats_cache: dict[str, dict] = {}
        self._user_record_ids: Dict[str, set[str]] = {}
        # Combat cache: span_days (str) -> dict with keys 'pair_counts','triples','spreads','ts'
        self._combat_cache: Dict[str, dict] = {}
        # Timestamp when user_stats_cache was last (re)built
        self._user_stats_cache_built_ts: Optional[int] = None
        self._load()
        self._build_user_stats_cache()
        self._init_home_chapter_cache()
        # Start background flush task
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                self._flush_task = loop.create_task(self._background_flush())
        except Exception:
            pass

    def _load(self):
        # Load AAR records
        try:
            with open(self._aar_records_path, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    self._records = data
                else:
                    self._records = {}
        except Exception:
            self._records = {}
        # Load processed IDs
        try:
            with open(self._processed_ids_path, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    self._processed_ids = set(str(x) for x in data)
                else:
                    self._processed_ids = set()
        except Exception:
            self._processed_ids = set()
        # Load challenge role acquisitions
        try:
            with open(self._acquisitions_path, "r") as f:
                data = json.load(f)
                if isinstance(data, dict) and "by_user" in data:
                    self._acquisitions = data.get("by_user", {})
                else:
                    self._acquisitions = {}
        except Exception:
            self._acquisitions = {}

    @staticmethod
    def _record_brother_ids(record: Optional[dict]) -> set[str]:
        if not isinstance(record, dict):
            return set()
        return {str(x) for x in (record.get("brother_ids") or []) if str(x)}

    def _records_for_user(self, user_id: str) -> list[dict]:
        rec_ids = self._user_record_ids.get(str(user_id), set())
        if not rec_ids:
            return []
        valid_ids = [rid for rid in rec_ids if rid in self._records]
        if len(valid_ids) != len(rec_ids):
            if valid_ids:
                self._user_record_ids[str(user_id)] = set(valid_ids)
            else:
                self._user_record_ids.pop(str(user_id), None)
        return [self._records[rid] for rid in valid_ids]

    def _build_user_stats_cache(self):
        # Build stats for all users from all records
        self._user_record_ids = {}
        for rec_id, rec in self._records.items():
            sid = str(rec_id)
            for uid in self._record_brother_ids(rec):
                self._user_record_ids.setdefault(uid, set()).add(sid)

        user_to_records: dict[str, list] = {
            uid: [self._records[rid] for rid in rec_ids if rid in self._records]
            for uid, rec_ids in self._user_record_ids.items()
        }
        self.user_stats_cache = {
            uid: _compute_stats_for_user_from_records(uid, recs) for uid, recs in user_to_records.items()
        }
        try:
            self._user_stats_cache_built_ts = self._now()
        except Exception:
            self._user_stats_cache_built_ts = None

    async def _clear_combat_cache_locked(self):
        # internal: must be called with self._lock held or awaited
        self._combat_cache = {}

    def get_combat_cache(self, span_days: int) -> Optional[dict]:
        """Return cached combat data for the given span_days, or None if missing."""
        try:
            return self._combat_cache.get(str(span_days))
        except Exception:
            return None

    async def set_combat_cache(self, span_days: int, data: dict):
        """Store computed combat cache (async-safe)."""
        async with self._lock:
            try:
                self._combat_cache[str(span_days)] = {"data": data, "ts": self._now()}
            except Exception:
                pass

    def get_record(self, aar_id: str | int) -> Optional[dict]:
        return self._records.get(str(aar_id))

    def get_all_records(self) -> Dict[str, dict]:
        """Return a shallow copy of all records as a dict keyed by aar_id."""
        return dict(self._records)

    def iter_records(self) -> Iterator[dict]:
        return iter(self._records.values())

    def is_processed(self, aar_id: str | int) -> bool:
        return str(aar_id) in self._processed_ids

    async def set_record(self, aar_id: str | int, record: dict):
        sid = str(aar_id)
        async with self._lock:
            # Capture previous record BEFORE overwriting so we can invalidate
            # stats for users who were removed from the record on an edit.
            prev = self._records.get(sid)
            prev_users = self._record_brother_ids(prev)
            new_users = self._record_brother_ids(record)
            # Update record
            self._records[sid] = record
            self._dirty_records = True

            # Maintain a per-user index of record IDs so we can avoid
            # scanning all records each time a single record changes.
            removed_users = prev_users - new_users
            added_or_retained_users = new_users
            for uid in removed_users:
                rec_ids = self._user_record_ids.get(uid)
                if rec_ids is None:
                    continue
                rec_ids.discard(sid)
                if not rec_ids:
                    self._user_record_ids.pop(uid, None)
                    self.user_stats_cache.pop(uid, None)

            for uid in added_or_retained_users:
                self._user_record_ids.setdefault(uid, set()).add(sid)

            # Update user_stats_cache for all affected users
            affected_users = prev_users | new_users
            for uid in affected_users:
                user_recs = self._records_for_user(uid)
                if user_recs:
                    self.user_stats_cache[uid] = _compute_stats_for_user_from_records(uid, user_recs)
                else:
                    self.user_stats_cache.pop(uid, None)
            # Invalidate any cached combat computations when records change
            try:
                self._combat_cache = {}
            except Exception:
                pass

    def get_user_stats(self, user_id: str) -> dict:
        """Get cached stats for a user (empty dict if not present)."""
        return self.user_stats_cache.get(
            str(user_id),
            {
                "ops": 0,
                "aar_points": 0,
                "armory_raw": 0,
                "armory_points": 0,
                "gene_carries": 0,
                "gene_seed_points": 0,
                "waves_participated": 0,
                "last_aar_ts": None,
                "operational_rating": OP_RATING_BASELINE,
                "operational_rating_raw": float(OP_RATING_BASELINE),
                "operational_rating_delta": 0.0,
                "operational_rating_events": 0,
            },
        )

    async def add_processed_id(self, aar_id: str | int):
        sid = str(aar_id)
        async with self._lock:
            if sid not in self._processed_ids:
                self._processed_ids.add(sid)
                self._dirty_ids = True

    async def flush(self):
        """Flush dirty data to disk. Safe to call at shutdown."""
        async with self._lock:
            await self._flush_locked()

    async def _flush_locked(self):
        import time

        t0 = time.perf_counter()
        # Write records if dirty
        if self._dirty_records:
            self._atomic_write_with_backup(self._aar_records_path, self._records)
            self._dirty_records = False
        # Write processed IDs if dirty
        if self._dirty_ids:
            sorted_ids = sorted(self._processed_ids, key=lambda x: int(x) if x.isdigit() else x)
            self._atomic_write_with_backup(self._processed_ids_path, sorted_ids)
            self._dirty_ids = False
        # Write acquisitions if dirty
        if self._dirty_acquisitions:
            from datetime import datetime
            acq_data = {
                "_meta": {
                    "description": "Per-user baseline snapshot dates for challenge role grace-period enforcement",
                    "format": "user_id -> {role_name -> iso8601_timestamp}",
                    "roles": ["Black Laurels", "Order Omega", "Dual Vigil", "Crux Terminatus"],
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                },
                "by_user": self._acquisitions,
            }
            self._atomic_write_with_backup(self._acquisitions_path, acq_data)
            self._dirty_acquisitions = False
        t1 = time.perf_counter()
        self._last_flush_time = time.time()
        if t1 - t0 > 0.05:
            import logging

            logging.getLogger("op-scribe-servitor").info(f"Flush took {t1 - t0:.3f}s")

    def get_cache_stats(self) -> dict:
        """Return cache and flush stats for admin diagnostics."""
        try:
            combat_size = len(self._combat_cache) if hasattr(self, "_combat_cache") else 0
            combat_spans = sorted(list(self._combat_cache.keys())) if hasattr(self, "_combat_cache") else []
        except Exception:
            combat_size = 0
            combat_spans = []
        return {
            "user_stats_cache_size": len(self.user_stats_cache),
            "user_record_index_size": len(self._user_record_ids),
            "user_stats_cache_built_ts": self._user_stats_cache_built_ts,
            "combat_cache_size": combat_size,
            "combat_cache_spans": combat_spans,
            "dirty_records": self._dirty_records,
            "dirty_ids": self._dirty_ids,
            "last_flush_time": self._last_flush_time,
        }

    def get_role_acquisition_date(self, user_id: str, role_name: str) -> Optional[str]:
        """Get ISO8601 acquisition timestamp for a user's challenge role, or None if not recorded."""
        user_acq = self._acquisitions.get(str(user_id), {})
        return user_acq.get(role_name)

    async def set_role_acquisition_date(self, user_id: str, role_name: str, iso8601_timestamp: str):
        """Record the acquisition date for a user's challenge role (ISO8601 format)."""
        async with self._lock:
            sid = str(user_id)
            if sid not in self._acquisitions:
                self._acquisitions[sid] = {}
            # Only set if not already recorded (acquisition is one-time event)
            if role_name not in self._acquisitions[sid]:
                self._acquisitions[sid][role_name] = iso8601_timestamp
                self._dirty_acquisitions = True

    async def snapshot_role_holders(self, role_holders: Dict[str, str]) -> int:
        """Snapshot current role holders (user_id -> iso8601_timestamp). Returns count set."""
        role_name = "manual_snapshot"  # Placeholder; caller specifies actual role
        async with self._lock:
            count = 0
            for user_id, timestamp in role_holders.items():
                sid = str(user_id)
                if sid not in self._acquisitions:
                    self._acquisitions[sid] = {}
                # Only set if not already recorded
                if role_name not in self._acquisitions[sid]:
                    self._acquisitions[sid][role_name] = timestamp
                    self._dirty_acquisitions = True
                    count += 1
            return count

    async def _background_flush(self):
        while not self._shutdown:
            await asyncio.sleep(self.FLUSH_INTERVAL)
            try:
                await self.flush()
            except Exception:
                pass

    def _atomic_write_with_backup(self, path: str, data):
        # Write to temp file, then rename, and keep .bak backup
        tmp_path = path + ".tmp"
        bak_path = path + ".bak"
        try:
            # Write new data to tmp
            with open(tmp_path, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            # Backup current file if it exists
            if os.path.exists(path):
                try:
                    os.replace(path, bak_path)
                except Exception:
                    pass
            # Move tmp to main file
            os.replace(tmp_path, path)
        except Exception:
            pass

    async def shutdown(self):
        """Flush all data and stop background task. Call on bot shutdown."""
        self._shutdown = True
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except Exception:
                pass
        await self.flush()
