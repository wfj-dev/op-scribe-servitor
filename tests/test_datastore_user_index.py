import asyncio
import json
import pytest

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


def test_set_record_and_processed_id_commits_record_id_and_stats_together(tmp_path):
    records_path = tmp_path / "aar_records.json"
    processed_path = tmp_path / "processed_ids.json"
    acquisitions_path = tmp_path / "challenge_role_acquisitions.json"
    _write_json(records_path, {})
    _write_json(processed_path, [])
    _write_json(acquisitions_path, {"by_user": {}})
    ds = DataStore(str(records_path), str(processed_path), str(acquisitions_path))

    record = _record("2026-10-05T00:00:00+00:00", ["u7"])
    record["aar_id"] = 700
    asyncio.run(ds.set_record_and_processed_id(700, record))

    assert ds.get_record(700) == record
    assert ds.is_processed(700)
    assert ds._user_record_ids["u7"] == {"700"}
    assert ds.get_user_stats("u7")["ops"] == 1
    assert ds._dirty_records is True
    assert ds._dirty_ids is True


def test_delete_record_and_processed_id_invalidates_user_indexes_and_stats(tmp_path):
    records_path = tmp_path / "aar_records.json"
    processed_path = tmp_path / "processed_ids.json"
    acquisitions_path = tmp_path / "challenge_role_acquisitions.json"
    record = _record("2026-10-05T00:00:00+00:00", ["u1", "u2"])
    _write_json(records_path, {"700": record})
    _write_json(processed_path, ["700"])
    _write_json(acquisitions_path, {"by_user": {}})
    ds = DataStore(str(records_path), str(processed_path), str(acquisitions_path))

    removed = asyncio.run(ds.delete_record_and_processed_id("700"))

    assert removed == record
    assert ds.get_record("700") is None
    assert not ds.is_processed("700")
    assert "u1" not in ds._user_record_ids
    assert "u2" not in ds._user_record_ids
    assert ds.get_user_stats("u1")["ops"] == 0
    assert ds.get_user_stats("u2")["ops"] == 0
    assert ds._dirty_records is True
    assert ds._dirty_ids is True


def test_remove_role_acquisition_date_clears_only_recorded_provenance(tmp_path):
    ds = DataStore(str(tmp_path / "aar_records.json"), str(tmp_path / "processed_ids.json"), str(tmp_path / "acquisitions.json"))
    asyncio.run(ds.set_role_acquisition_date("u1", "Black Laurels", "2026-10-01T00:00:00+00:00"))

    removed = asyncio.run(ds.remove_role_acquisition_date("u1", "Black Laurels"))

    assert removed is True
    assert ds.get_role_acquisition_date("u1", "Black Laurels") is None
    assert asyncio.run(ds.remove_role_acquisition_date("u1", "Black Laurels")) is False
    assert ds._dirty_acquisitions is True


def test_latest_ingested_message_id_ignores_web_receipts(tmp_path):
    records_path = tmp_path / "aar_records.json"
    processed_path = tmp_path / "processed_ids.json"
    acquisitions_path = tmp_path / "challenge_role_acquisitions.json"
    _write_json(
        records_path,
        {
            "100": _record("2026-10-05T00:00:00+00:00", ["u1"]),
            "900": {**_record("2026-10-05T00:00:00+00:00", ["u2"]), "source": "strategium_web"},
        },
    )
    _write_json(processed_path, ["100", "900"])
    _write_json(acquisitions_path, {"by_user": {}})
    ds = DataStore(str(records_path), str(processed_path), str(acquisitions_path))

    assert ds.latest_ingested_message_id() == 100


def test_recent_teammate_ids_are_distinct_and_ordered_by_latest_shared_aar(tmp_path):
    records_path = tmp_path / "aar_records.json"
    processed_path = tmp_path / "processed_ids.json"
    acquisitions_path = tmp_path / "challenge_role_acquisitions.json"
    records = {
        "1": _record("2026-10-05T00:00:00+00:00", ["101", "202"]),
        "2": _record("2026-10-04T00:00:00+00:00", ["101", "303"]),
        "3": _record("2026-10-03T00:00:00+00:00", ["101", "202", "404"]),
        "4": _record("2026-10-02T00:00:00+00:00", ["101", "303"]),
    }
    _write_json(records_path, records)
    _write_json(processed_path, list(records))
    _write_json(acquisitions_path, {"by_user": {}})
    ds = DataStore(str(records_path), str(processed_path), str(acquisitions_path))

    assert ds.get_recent_teammate_ids("101") == ["202", "303", "404"]


def test_failed_flush_keeps_record_dirty_and_raises(tmp_path):
    ds = DataStore(str(tmp_path / "missing" / "aar_records.json"), str(tmp_path / "processed_ids.json"), str(tmp_path / "acquisitions.json"))

    async def _run():
        await ds.set_record_and_processed_id("700", _record("2026-10-05T00:00:00+00:00", ["101", "102"]))
        with pytest.raises(OSError):
            await ds.flush()

    asyncio.run(_run())
    assert ds._dirty_records
    assert ds._dirty_ids


def test_failed_archive_replacement_preserves_existing_file(tmp_path, monkeypatch):
    from opscribe import datastore as datastore_module

    records_path = tmp_path / "aar_records.json"
    original = {"100": _record("2026-10-04T00:00:00+00:00", ["101", "102"])}
    _write_json(records_path, original)
    ds = DataStore(str(records_path), str(tmp_path / "processed_ids.json"), str(tmp_path / "acquisitions.json"))

    def fail_replace(_source, _target):
        raise OSError("test disk replacement failure")

    monkeypatch.setattr(datastore_module.os, "replace", fail_replace)

    async def _run():
        await ds.set_record_and_processed_id("700", _record("2026-10-05T00:00:00+00:00", ["101", "102"]))
        with pytest.raises(OSError):
            await ds.flush()

    asyncio.run(_run())
    assert json.loads(records_path.read_text()) == original
    assert ds._dirty_records


def test_flush_writes_files_off_the_event_loop(tmp_path, monkeypatch):
    import threading

    from opscribe import datastore as datastore_module

    records_path = tmp_path / "aar_records.json"
    ds = DataStore(str(records_path), str(tmp_path / "processed_ids.json"), str(tmp_path / "acquisitions.json"))
    writer_threads = []
    original_write = ds._atomic_write_with_backup

    def tracking_write(path, data):
        writer_threads.append(threading.current_thread())
        return original_write(path, data)

    monkeypatch.setattr(ds, "_atomic_write_with_backup", tracking_write)
    record = _record("2026-10-05T00:00:00+00:00", ["101", "102"])
    second = _record("2026-10-06T00:00:00+00:00", ["101"])

    async def _run():
        await ds.set_record_and_processed_id("700", record)
        await ds.set_record_and_processed_id("701", second)
        await ds.flush()

    asyncio.run(_run())
    assert writer_threads and all(thread is not threading.main_thread() for thread in writer_threads)
    assert json.loads(records_path.read_text()) == {"700": record, "701": second}
    assert json.loads((tmp_path / "processed_ids.json").read_text()) == ["700", "701"]
    assert not ds._dirty_records
    assert datastore_module.os.path.exists(str(tmp_path / "processed_ids.json"))
