from opscribe.flavor_text import ranks
from opscribe.flavor_text import awards
from opscribe.flavor_text import chapters
import ast
from pathlib import Path


def test_champion_track_aliases_share_honorifics_with_legacy_blade_roles():
    assert ranks.RANK_HONORIFICS["Lord Executioner"] == ranks.RANK_HONORIFICS["Blade Master"]
    assert ranks.RANK_HONORIFICS["Company Champion"] == ranks.RANK_HONORIFICS["First Blade"]
    assert ranks.RANK_HONORIFICS["Kill Team Champion"] == ranks.RANK_HONORIFICS["Bladeguard"]


def test_champion_track_aliases_share_award_flavor_with_legacy_blade_roles():
    assert ranks.BLACK_LAURELS_RANK_LINES["Lord Executioner"] == ranks.BLACK_LAURELS_RANK_LINES["Blade Master"]
    assert ranks.BLACK_LAURELS_RANK_LINES["Company Champion"] == ranks.BLACK_LAURELS_RANK_LINES["First Blade"]
    assert ranks.BLACK_LAURELS_RANK_LINES["Kill Team Champion"] == ranks.BLACK_LAURELS_RANK_LINES["Bladeguard"]
    assert ranks.MASTER_TERMINUS_SLAYER_RANK_LINES["Lord Executioner"] == ranks.MASTER_TERMINUS_SLAYER_RANK_LINES["Blade Master"]
    assert ranks.MASTER_TERMINUS_SLAYER_RANK_LINES["Company Champion"] == ranks.MASTER_TERMINUS_SLAYER_RANK_LINES["First Blade"]
    assert ranks.MASTER_TERMINUS_SLAYER_RANK_LINES["Kill Team Champion"] == ranks.MASTER_TERMINUS_SLAYER_RANK_LINES["Bladeguard"]


def test_master_terminus_award_and_rank_prose_is_count_free_lore():
    lines = [
        *awards.MASTER_TERMINUS_SLAYER_OPENINGS,
        *awards.MASTER_TERMINUS_SLAYER_PROCLAMATIONS,
        *ranks.MASTER_TERMINUS_SLAYER_RANK_LINES.values(),
        *chapters.MASTER_TERMINUS_SLAYER_CHAPTER_LINES.values(),
    ]
    for line in lines:
        rendered = line.format(name="Brother Nyx")
        assert "{" not in rendered
        assert "class" not in rendered.casefold()
        assert not any(word in rendered.casefold() for word in ("six", "seven", "nine", "fifty", "sixty"))
        assert not any(character.isdigit() for character in rendered)
    for armour in ("Phobos", "Gravis", "Tacticus"):
        assert any(armour in line for line in lines)
    assert set(chapters.KILL_TEAM_WELCOME_CHAPTER_LINES) <= set(chapters.MASTER_TERMINUS_SLAYER_CHAPTER_LINES)


def test_doom_eagles_has_authored_flavor_in_every_chapter_map():
    source = ast.parse(Path(chapters.__file__).read_text(encoding="utf-8"))
    authored = {
        statement.target.id: {
            ast.literal_eval(key) for key in statement.value.keys
        }
        for statement in source.body
        if isinstance(statement, ast.AnnAssign)
        and isinstance(statement.target, ast.Name)
        and isinstance(statement.value, ast.Dict)
    }
    required_maps = {
        name: mapping for name, mapping in vars(chapters).items()
        if isinstance(mapping, dict)
        and (name.endswith("_CHAPTER_LINES") or name in {
            "CHAPTER_BLESSINGS", "CHAPTER_STUDS_FLAVOR", "FORGEMASTER_SELF_ATTESTATION_BY_CHAPTER",
            "_CHAPTER_MOTIFS", "_CHAPTER_EPITHETS",
        })
    }
    assert len(required_maps) == 31
    assert "Doom Eagles" in chapters._CANONICAL_HOME_CHAPTERS
    for name, mapping in required_maps.items():
        assert "Doom Eagles" in authored[name], name
        entry = mapping["Doom Eagles"]
        lines = entry if isinstance(entry, list) else [entry]
        assert lines
        for line in lines:
            assert line.strip()
            assert "{" not in line.format(name="Brother Aquila")
    assert len(chapters.CHAPTER_STUDS_FLAVOR["Doom Eagles"]) == 3
    assert len(chapters.FORGEMASTER_SELF_ATTESTATION_BY_CHAPTER["Doom Eagles"]) == 3
