import os
import json
import asyncio
from typing import Dict, Iterator, Optional


def _compute_stats_for_user_from_records(user_id: str, records: list[dict]) -> dict:
    ops = 0
    aar_points = 0
    armory_raw = 0
    armory_points = 0
    gene_carries = 0
    gene_seed_points = 0
    waves_participated = 0
    for record in records:
        brother_ids = record.get("brother_ids", [])
        if user_id in brother_ids:
            ops += 1
            difficulty_class = record.get("difficulty_class")
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
                    aar_points += 3 * (my_waves // 5)
                else:
                    aar_points += 4 * (my_waves // 5)
                waves_participated += my_waves
            else:
                aar_points += record.get("points_for_op", 0)
            armory_data = record.get("armory_data")
            try:
                armory_raw += int(armory_data) if armory_data is not None else 0
            except ValueError:
                armory_raw += 0
            armory_points += record.get("armory_challenge_points", 0)
        status = (record.get("gene_seed_status") or "").lower()
        gene_carrier = record.get("gene_seed_carrier_id")
        effective_carried = status == "carried" or (
            gene_carrier is not None and status != "lost"
        )
        if effective_carried:
            if gene_carrier == user_id:
                gene_carries += 1
                gene_seed_points += record.get("gene_seed_base_points_for_carrier", 0)
            elif user_id in brother_ids:
                gene_seed_points += 1
    return {
        "ops": ops,
        "aar_points": aar_points,
        "armory_raw": armory_raw,
        "armory_points": armory_points,
        "gene_carries": gene_carries,
        "gene_seed_points": gene_seed_points,
        "waves_participated": waves_participated,
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
        self._home_chapter_cache: dict[
            str, tuple[str, int]
        ] = {}  # user_id -> (chapter, expires_at)
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

    def get_home_chapter(
        self, user_id: str, force_refresh: bool = False
    ) -> Optional[str]:
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

            logging.getLogger("op-scribe-servitor").info(
                f"Home chapter scan for {user_id} took {t1 - t0:.3f}s"
            )
        if chapter:
            self._home_chapter_cache_set(user_id, chapter)
        return chapter

    def force_refresh_home_chapter(self, user_id: str) -> Optional[str]:
        """Admin-only: force a rescan and refresh of the user's home chapter."""
        return self.get_home_chapter(user_id, force_refresh=True)

    FLUSH_INTERVAL = 60  # seconds between background flushes

    def __init__(self, aar_records_path: str, processed_ids_path: str):
        self._aar_records_path = aar_records_path
        self._processed_ids_path = processed_ids_path
        self._records: Dict[str, dict] = {}
        self._processed_ids: set[str] = set()
        self._dirty_records = False
        self._dirty_ids = False
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._shutdown = False
        self.user_stats_cache: dict[str, dict] = {}
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

    def _build_user_stats_cache(self):
        # Build stats for all users from all records
        user_to_records: dict[str, list] = {}
        for rec in self._records.values():
            for uid in rec.get("brother_ids", []):
                user_to_records.setdefault(uid, []).append(rec)
        self.user_stats_cache = {
            uid: _compute_stats_for_user_from_records(uid, recs)
            for uid, recs in user_to_records.items()
        }

    def get_record(self, aar_id: str | int) -> Optional[dict]:
        return self._records.get(str(aar_id))

    def iter_records(self) -> Iterator[dict]:
        return iter(self._records.values())

    def is_processed(self, aar_id: str | int) -> bool:
        return str(aar_id) in self._processed_ids

    async def set_record(self, aar_id: str | int, record: dict):
        sid = str(aar_id)
        async with self._lock:
            # Update record
            self._records[sid] = record
            self._dirty_records = True
            # Update user_stats_cache for all affected users
            # Find all users in this record
            affected_users = set(record.get("brother_ids", []))
            # Also, if this is an edit, find users in the previous record
            prev = self._records.get(sid)
            if prev:
                affected_users.update(prev.get("brother_ids", []))
            # For each affected user, gather all records for that user
            for uid in affected_users:
                user_recs = [
                    r for r in self._records.values() if uid in r.get("brother_ids", [])
                ]
                self.user_stats_cache[uid] = _compute_stats_for_user_from_records(
                    uid, user_recs
                )

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
            sorted_ids = sorted(
                self._processed_ids, key=lambda x: int(x) if x.isdigit() else x
            )
            self._atomic_write_with_backup(self._processed_ids_path, sorted_ids)
            self._dirty_ids = False
        t1 = time.perf_counter()
        self._last_flush_time = time.time()
        if t1 - t0 > 0.05:
            import logging

            logging.getLogger("op-scribe-servitor").info(f"Flush took {t1 - t0:.3f}s")

    def get_cache_stats(self) -> dict:
        """Return cache and flush stats for admin diagnostics."""
        return {
            "user_stats_cache_size": len(self.user_stats_cache),
            "home_chapter_cache_size": len(self._home_chapter_cache),
            "dirty_records": self._dirty_records,
            "dirty_ids": self._dirty_ids,
            "last_flush_time": self._last_flush_time,
            "home_chapter_cache_hits": self._home_chapter_cache_hits,
            "home_chapter_cache_misses": self._home_chapter_cache_misses,
        }

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
