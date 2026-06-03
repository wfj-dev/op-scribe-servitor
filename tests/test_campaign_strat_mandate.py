"""Unit tests for campaign strat mandate scoring and derivation.

Covers:
- score_strats_against_aggregate: blacklisted strats excluded
- score_strats_against_aggregate: terminus doctrine gets 1.5x on terminus tags
- score_strats_against_aggregate: excluded strats (excluded=True) never appear
- derive_strat_mandate: theatre mandate is highest-scoring strat in pool
- derive_strat_mandate: company mandate differs from theatre mandate
- derive_strat_mandate: conflict stripping prevents conflicting mandates
- _build_conflict_set: category_group conflicts bidirectional
- _build_conflict_set: specific_conflicts are bidirectional
"""

import sys
import types

import pytest

# ---------------------------------------------------------------------------
# Module-level mock setup
# ---------------------------------------------------------------------------

def _setup_mock_bot():
    if "opscribe._bot_globals" not in sys.modules:
        bg = types.ModuleType("opscribe._bot_globals")
        class FakeBot:
            class tree:
                @staticmethod
                def command(**kw):
                    def dec(fn):
                        return fn
                    return dec
        bg.bot = FakeBot()
        sys.modules["opscribe._bot_globals"] = bg


_setup_mock_bot()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_refs():
    """Reset cached reference data before each test."""
    from opscribe import campaign_ops as c
    c._STRATAGEMS = []
    c._DOCTRINE_STRAT_MAP = {}
    c._SCENARIO_GEN = {}
    c._CASCADE_OPTIONS = {}
    c._REWARDS = {}
    c._MILESTONES = {}
    c._STRAT_MANDATE = {}
    yield


# ---------------------------------------------------------------------------
# score_strats_against_aggregate
# ---------------------------------------------------------------------------

class TestScoreStratsAgainstAggregate:
    def test_blacklisted_strats_excluded_by_default(self):
        from opscribe import campaign_ops as c
        agg = {"aggressive": 10.0}
        results = c.score_strats_against_aggregate(agg)
        names = [name for name, _, _ in results]
        blacklist_entries = c._DOCTRINE_STRAT_MAP.get("pool_blacklist", {}).get("entries", {})
        for blacklisted in blacklist_entries:
            assert blacklisted not in names, f"Blacklisted strat '{blacklisted}' appeared in results"

    def test_blacklisted_strats_included_when_flag_set(self):
        from opscribe import campaign_ops as c
        agg = {"aggressive": 10.0}
        results_default = c.score_strats_against_aggregate(agg)
        results_include = c.score_strats_against_aggregate(agg, include_blacklisted=True)
        assert len(results_include) >= len(results_default)

    def test_excluded_strats_never_appear(self):
        from opscribe import campaign_ops as c
        agg = {"aggressive": 10.0}
        results = c.score_strats_against_aggregate(agg, include_blacklisted=True)
        names = [name for name, _, _ in results]
        c._ensure_refs_loaded()
        for strat in c._STRATAGEMS:
            if strat.get("excluded"):
                assert strat["name"] not in names, (
                    f"Excluded strat '{strat['name']}' appeared in scoring results"
                )

    def test_returns_sorted_by_score_descending(self):
        from opscribe import campaign_ops as c
        agg = {"aggressive": 5.0, "terminus": 8.0}
        results = c.score_strats_against_aggregate(agg)
        scores = [score for _, score, _ in results]
        assert scores == sorted(scores, reverse=True)

    def test_terminus_doctrine_scores_higher_for_terminus_strats(self):
        """Strats with terminus tags should benefit from the 1.5x terminus multiplier."""
        from opscribe import campaign_ops as c
        # Aggressive only — no terminus
        agg_no_terminus = {"aggressive": 10.0, "terminus": 0.0}
        # With terminus — should boost terminus-tagged strats
        agg_with_terminus = {"aggressive": 10.0, "terminus": 10.0}
        results_no = {name: score for name, score, _ in c.score_strats_against_aggregate(agg_no_terminus)}
        results_with = {name: score for name, score, _ in c.score_strats_against_aggregate(agg_with_terminus)}

        # "Supremacy of the Strong" has terminus tags — should gain more with terminus doctrine
        if "Supremacy of the Strong" in results_no and "Supremacy of the Strong" in results_with:
            assert results_with["Supremacy of the Strong"] > results_no["Supremacy of the Strong"]

    def test_zero_doctrine_aggregate_all_score_zero(self):
        from opscribe import campaign_ops as c
        agg = {}
        results = c.score_strats_against_aggregate(agg)
        for name, score, _ in results:
            assert score == 0.0

    def test_returns_list_of_tuples(self):
        from opscribe import campaign_ops as c
        results = c.score_strats_against_aggregate({"aggressive": 1.0})
        assert isinstance(results, list)
        for item in results:
            assert len(item) == 3
            name, score, strat = item
            assert isinstance(name, str)
            assert isinstance(score, float)
            assert isinstance(strat, dict)


# ---------------------------------------------------------------------------
# _build_conflict_set
# ---------------------------------------------------------------------------

class TestBuildConflictSet:
    def test_category_group_conflict_bidirectional(self):
        from opscribe import campaign_ops as c
        # Fatality and You Only Live Once are in respawn_consequence group
        pool = ["Fatality", "You Only Live Once", "Unleashed Fury"]
        conflicts = c._build_conflict_set(pool)
        assert "You Only Live Once" in conflicts.get("Fatality", set())
        assert "Fatality" in conflicts.get("You Only Live Once", set())

    def test_specific_conflict_bidirectional(self):
        from opscribe import campaign_ops as c
        # Corrupted Relic blocks You Only Live Once (and vice versa)
        pool = ["Corrupted Relic", "You Only Live Once"]
        conflicts = c._build_conflict_set(pool)
        assert "You Only Live Once" in conflicts.get("Corrupted Relic", set())
        assert "Corrupted Relic" in conflicts.get("You Only Live Once", set())

    def test_non_conflicting_strats_have_empty_conflict_sets(self):
        from opscribe import campaign_ops as c
        # Pick two strats known to have no conflicts with each other or their group
        pool = ["Larraman Cells", "Extreme Challenge"]
        conflicts = c._build_conflict_set(pool)
        # Neither of these two strats conflict with the other
        assert "Extreme Challenge" not in conflicts.get("Larraman Cells", set())
        assert "Larraman Cells" not in conflicts.get("Extreme Challenge", set())

    def test_strats_not_in_pool_not_in_conflict_set(self):
        from opscribe import campaign_ops as c
        pool = ["Unleashed Fury"]
        conflicts = c._build_conflict_set(pool)
        # Only pool members should be in the conflict set
        assert set(conflicts.keys()).issubset(set(pool))


# ---------------------------------------------------------------------------
# derive_strat_mandate
# ---------------------------------------------------------------------------

class TestDeriveStratMandate:
    def _base_state(self):
        return {
            "campaign": {"phase": "ops", "beat": 1},
            "enlistment": {},
            "companies": {
                "primus": {"prestige_window_total": 0},
                "secundus": {"prestige_window_total": 0},
            },
            "kill_teams": {},
            "lore_priority": {},
            "ops_window": {},
            "strat_pool": {},
            "campaign_log": {},
            "beat_scenarios": {},
            "pressure": {},
            "cascade": {},
            "beat_record": {},
        }

    def test_theatre_mandate_is_highest_scoring_in_pool(self):
        from opscribe import campaign_ops as c
        agg = {"aggressive": 10.0}
        # Get top 5 non-blacklisted strats
        scored = c.score_strats_against_aggregate(agg)
        pool = [name for name, _, _ in scored[:5]]
        state = self._base_state()
        mandate = c.derive_strat_mandate(agg, pool, state)
        assert mandate["theatre_mandate"] == pool[0]

    def test_company_mandate_differs_from_theatre(self):
        from opscribe import campaign_ops as c
        agg = {"aggressive": 10.0}
        scored = c.score_strats_against_aggregate(agg)
        pool = [name for name, _, _ in scored[:5]]
        state = self._base_state()
        mandate = c.derive_strat_mandate(agg, pool, state)
        theatre = mandate["theatre_mandate"]
        for co_id, co_strat in mandate["company_mandates"].items():
            assert co_strat != theatre or co_strat is None

    def test_mandate_strats_are_from_pool(self):
        from opscribe import campaign_ops as c
        agg = {"aggressive": 10.0, "terminus": 5.0}
        scored = c.score_strats_against_aggregate(agg)
        pool = [name for name, _, _ in scored[:10]]
        pool_set = set(pool)
        state = self._base_state()
        mandate = c.derive_strat_mandate(agg, pool, state)
        if mandate["theatre_mandate"]:
            assert mandate["theatre_mandate"] in pool_set
        for co_strat in mandate["company_mandates"].values():
            if co_strat:
                assert co_strat in pool_set

    def test_no_conflicting_mandates(self):
        from opscribe import campaign_ops as c
        # Use a pool that contains known conflicts
        pool = ["Fatality", "You Only Live Once", "Unleashed Fury", "Extreme Challenge", "Larraman Cells"]
        agg = {"aggressive": 5.0, "elimination": 8.0}
        state = self._base_state()
        mandate = c.derive_strat_mandate(agg, pool, state)
        conflicts = c._build_conflict_set(pool)
        mandated = [m for m in [
            mandate.get("theatre_mandate"),
        ] + list(mandate.get("company_mandates", {}).values()) if m]

        for i, a in enumerate(mandated):
            for b in mandated[i + 1:]:
                assert b not in conflicts.get(a, set()), (
                    f"Conflicting mandates: {a} and {b}"
                )

    def test_empty_pool_returns_none_mandates(self):
        from opscribe import campaign_ops as c
        agg = {"aggressive": 5.0}
        state = self._base_state()
        mandate = c.derive_strat_mandate(agg, [], state)
        assert mandate["theatre_mandate"] is None
