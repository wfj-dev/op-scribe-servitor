import opscribe.target_packages_ops as tp


def test_build_package_embed_truncation_avoids_four_trailing_dots(monkeypatch):
    monkeypatch.setattr(tp, "_load_operations", lambda: [{"id": 42, "name": "Operation Test"}])

    briefing = ("A" * 379) + ".tail"
    pkg = {
        "id": "TP-1",
        "directive_code": "OX-001",
        "node": "Nexus",
        "mission_id": 42,
        "mode": "Hard-Strat",
        "status": tp.STATUS_RECRUITING,
        "briefing": briefing,
        "stratagems": {},
    }

    embed = tp._build_package_embed(pkg, rep=0.0)
    briefing_field = next(field for field in embed.fields if field.name == "▸ Field Briefing")

    assert briefing_field.value.endswith("...")
    assert not briefing_field.value.endswith("....")
