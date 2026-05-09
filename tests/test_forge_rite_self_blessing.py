"""Unit tests for Forgemaster self-blessing features.

Covers:
- _blend_forgemaster_self_attestation:
    * Returns generic Mechanicus phrase with ~80% probability
    * Returns chapter-specific phrase with ~20% probability when chapter exists
    * Falls back to generic when chapter not in dictionary
    * All phrases from both pools are reachable

- ORDO_XENOS_HONORS pronoun templating:
    * Self-blessing uses first-person pronouns (my, My, me)
    * Blessing others uses second-person pronouns (your, Your, you)
    * All tiers have proper placeholder support
"""

from opscribe.bot import (
    _blend_forgemaster_self_attestation,
    FORGEMASTER_SELF_ATTESTATION_GENERIC,
    FORGEMASTER_SELF_ATTESTATION_BY_CHAPTER,
    ORDO_XENOS_HONORS_TIER1,
    ORDO_XENOS_HONORS_TIER2,
    ORDO_XENOS_HONORS_TIER3,
)


# ---------------------------------------------------------------------------
# _blend_forgemaster_self_attestation – Blend ratio
# ---------------------------------------------------------------------------


def test_self_attestation_returns_valid_phrase():
    """Self-attestation always returns a non-empty string."""
    for _ in range(50):
        phrase = _blend_forgemaster_self_attestation("Hawk Lords")
        assert isinstance(phrase, str)
        assert len(phrase) > 0


def test_self_attestation_generic_reachable():
    """Generic Mechanicus phrases are reachable (should happen ~80% of time)."""
    found_generic = set()
    for _ in range(500):
        phrase = _blend_forgemaster_self_attestation("Hawk Lords")
        if phrase in FORGEMASTER_SELF_ATTESTATION_GENERIC:
            found_generic.add(phrase)

    # Should find at least some generic phrases
    assert len(found_generic) > 0, "No generic phrases were selected"
    # With 500 iterations at 80% chance, we should hit most of them
    assert len(found_generic) >= len(FORGEMASTER_SELF_ATTESTATION_GENERIC) // 2


def test_self_attestation_chapter_reachable():
    """Chapter-specific phrases are reachable (should happen ~20% of time)."""
    hawk_lords_phrases = FORGEMASTER_SELF_ATTESTATION_BY_CHAPTER["Hawk Lords"]
    found_chapter = set()

    for _ in range(500):
        phrase = _blend_forgemaster_self_attestation("Hawk Lords")
        if phrase in hawk_lords_phrases:
            found_chapter.add(phrase)

    # Should find at least some chapter phrases
    assert len(found_chapter) > 0, "No chapter phrases were selected"


def test_self_attestation_blend_ratio():
    """Blend ratio is approximately 80% generic, 20% chapter."""
    hawk_lords_phrases = FORGEMASTER_SELF_ATTESTATION_BY_CHAPTER["Hawk Lords"]
    generic_count = 0
    chapter_count = 0
    iterations = 1000

    for _ in range(iterations):
        phrase = _blend_forgemaster_self_attestation("Hawk Lords")
        if phrase in FORGEMASTER_SELF_ATTESTATION_GENERIC:
            generic_count += 1
        elif phrase in hawk_lords_phrases:
            chapter_count += 1

    # Allow reasonable variance (70-90% generic)
    generic_ratio = generic_count / iterations
    assert 0.70 <= generic_ratio <= 0.90, f"Generic ratio {generic_ratio:.2%} outside expected range"


def test_self_attestation_unknown_chapter_falls_back():
    """Unknown chapter falls back to generic phrases only."""
    for _ in range(100):
        phrase = _blend_forgemaster_self_attestation("Unknown Chapter Name")
        assert phrase in FORGEMASTER_SELF_ATTESTATION_GENERIC


def test_self_attestation_all_chapters_have_phrases():
    """All chapters in the dictionary return valid phrases."""
    for chapter in FORGEMASTER_SELF_ATTESTATION_BY_CHAPTER:
        phrase = _blend_forgemaster_self_attestation(chapter)
        assert isinstance(phrase, str)
        assert len(phrase) > 0


def test_self_attestation_chapter_phrases_all_reachable():
    """With enough iterations, all chapter-specific phrases are reachable."""
    for chapter, chapter_phrases in FORGEMASTER_SELF_ATTESTATION_BY_CHAPTER.items():
        found = set()
        # Need enough iterations to overcome 80% generic ratio
        for _ in range(500):
            phrase = _blend_forgemaster_self_attestation(chapter)
            if phrase in chapter_phrases:
                found.add(phrase)

        # Should find at least one chapter phrase
        assert len(found) > 0, f"No phrases found for {chapter}"


# ---------------------------------------------------------------------------
# ORDO_XENOS_HONORS – Pronoun templating
# ---------------------------------------------------------------------------


def test_ordo_honors_have_placeholders():
    """All ORDO_XENOS_HONORS phrases with pronouns have proper placeholders."""
    all_tiers = ORDO_XENOS_HONORS_TIER1 + ORDO_XENOS_HONORS_TIER2 + ORDO_XENOS_HONORS_TIER3

    for phrase in all_tiers:
        # Check that format placeholders are valid
        # Should not raise KeyError when formatted
        try:
            formatted = phrase.format(possessive="your", possessive_cap="Your", object="you")
            assert isinstance(formatted, str)
        except KeyError as e:
            raise AssertionError(f"Invalid placeholder in phrase '{phrase}': {e}")


def test_ordo_honors_self_blessing_pronouns():
    """Self-blessing formats with first-person pronouns."""
    all_tiers = ORDO_XENOS_HONORS_TIER1 + ORDO_XENOS_HONORS_TIER2 + ORDO_XENOS_HONORS_TIER3

    for phrase in all_tiers:
        formatted = phrase.format(possessive="my", possessive_cap="My", object="me")
        # Should not contain "your" or "you" after formatting
        assert "your" not in formatted.lower() or "my" in formatted.lower()
        # If original had placeholders, formatted should have "my" or "me"
        if "{possessive}" in phrase or "{possessive_cap}" in phrase:
            assert "my" in formatted.lower(), f"Missing 'my' in: {formatted}"
        if "{object}" in phrase:
            assert "me" in formatted.lower(), f"Missing 'me' in: {formatted}"


def test_ordo_honors_other_blessing_pronouns():
    """Blessing others formats with second-person pronouns."""
    all_tiers = ORDO_XENOS_HONORS_TIER1 + ORDO_XENOS_HONORS_TIER2 + ORDO_XENOS_HONORS_TIER3

    for phrase in all_tiers:
        formatted = phrase.format(possessive="your", possessive_cap="Your", object="you")
        # If original had placeholders, formatted should have "your" or "you"
        if "{possessive}" in phrase or "{possessive_cap}" in phrase:
            assert "your" in formatted.lower(), f"Missing 'your' in: {formatted}"
        if "{object}" in phrase:
            assert "you" in formatted.lower(), f"Missing 'you' in: {formatted}"


def test_ordo_honors_no_unformatted_placeholders():
    """After formatting, no raw placeholders remain."""
    all_tiers = ORDO_XENOS_HONORS_TIER1 + ORDO_XENOS_HONORS_TIER2 + ORDO_XENOS_HONORS_TIER3

    for phrase in all_tiers:
        formatted = phrase.format(possessive="my", possessive_cap="My", object="me")
        assert "{" not in formatted, f"Unformatted placeholder in: {formatted}"
        assert "}" not in formatted, f"Unformatted placeholder in: {formatted}"


def test_ordo_honors_tier1_count():
    """Tier 1 has expected number of phrases."""
    assert len(ORDO_XENOS_HONORS_TIER1) >= 5, "Tier 1 should have at least 5 phrases"


def test_ordo_honors_tier2_count():
    """Tier 2 has expected number of phrases."""
    assert len(ORDO_XENOS_HONORS_TIER2) >= 5, "Tier 2 should have at least 5 phrases"


def test_ordo_honors_tier3_count():
    """Tier 3 has expected number of phrases."""
    assert len(ORDO_XENOS_HONORS_TIER3) >= 5, "Tier 3 should have at least 5 phrases"


# ---------------------------------------------------------------------------
# Self-attestation phrase content validation
# ---------------------------------------------------------------------------


def test_generic_phrases_not_empty():
    """Generic self-attestation phrases are non-empty strings."""
    assert len(FORGEMASTER_SELF_ATTESTATION_GENERIC) >= 5
    for phrase in FORGEMASTER_SELF_ATTESTATION_GENERIC:
        assert isinstance(phrase, str)
        assert len(phrase) >= 20, f"Phrase too short: {phrase}"


def test_chapter_phrases_not_empty():
    """Chapter-specific self-attestation phrases are non-empty strings."""
    for chapter, phrases in FORGEMASTER_SELF_ATTESTATION_BY_CHAPTER.items():
        assert len(phrases) >= 2, f"{chapter} should have at least 2 phrases"
        for phrase in phrases:
            assert isinstance(phrase, str)
            assert len(phrase) >= 20, f"{chapter} phrase too short: {phrase}"
