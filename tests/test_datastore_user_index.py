import asyncio
import json

from opscribe.datastore import DataStore


def _write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def _record(ts: str, brother_ids: list[str], difficulty_class: str = "absolute_ops") -> dict:
    return {
        "aar_id": 1,
        "timestamp": ts,
        "aar_type": "pve",
        "difficulty_class": difficulty_class,
        "brother_ids": brother_ids,
        "points_for_op": 4,
        "armory_data": 0,
        "armory_challenge_points": 0,
        "gene_seed_status": "unknown",
    }


def test_datastore_builds_user_record_index(tmp_path):
    records_path = tmp_path / "aar_records.json"
    processed_path = tmp_path / "processed_ids.json"
    acquisitions_path = tmp_path / "challenge_role_acquisitions.json"

    _write_json(
        records_path,
        {
            "1": _record("2026-08-01T00:00:00+00:00", ["u1", "u2"]),
            "2": _record("2026-08-02T00:00:00+00:00", ["u1"]),
        },
    )
    _write_json(processed_path, [])
    _write_json(acquisitions_path, {"by_user": {}})

    ds = DataStore(str(records_path), str(processed_path), str(acquisitions_path))

    assert ds._user_record_ids["u1"] == {"1", "2"}
    assert ds._user_record_ids["u2"] == {"1"}
    assert ds.get_user_stats("u1")["ops"] == 2
    assert ds.get_user_stats("u2")["ops"] == 1


def test_set_record_updates_index_and_stats_for_affected_users(tmp_path):
    records_path = tmp_path / "aar_records.json"
    processed_path = tmp_path / "processed_ids.json"
    acquisitions_path = tmp_path / "challenge_role_acquisitions.json"

    _write_json(
        records_path,
        {
            "1": _record("2026-08-01T00:00:00+00:00", ["u1", "u2"]),
            "2": _record("2026-08-02T00:00:00+00:00", ["u1"]),
        },
    )
    _write_json(processed_path, [])
    _write_json(acquisitions_path, {"by_user": {}})

    ds = DataStore(str(records_path), str(processed_path), str(acquisitions_path))

    updated = _record("2026-08-02T00:00:00+00:00", ["u3"])
    asyncio.run(ds.set_record("2", updated))

    assert ds._user_record_ids["u1"] == {"1"}
    assert ds._user_record_ids["u2"] == {"1"}
    assert ds._user_record_ids["u3"] == {"2"}

    assert ds.get_user_stats("u1")["ops"] == 1
    assert ds.get_user_stats("u2")["ops"] == 1
    assert ds.get_user_stats("u3")["ops"] == 1
