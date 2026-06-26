import importlib
from types import SimpleNamespace

import opscribe._bot_globals as _g
from opscribe.constants import (
    MASTER_TERMINUS_SLAYER_ROLE_ID,
    TERMINUS_SLAYER_CLASS_AWARD_TYPES,
    WATCH_VETERAN_ROLE_ID,
)


class _DummyTree:
    def command(self, *args, **kwargs):
        def _decorator(func):
            return func

        return _decorator


_g.bot = SimpleNamespace(tree=_DummyTree())
roster_ops = importlib.import_module("opscribe.roster_ops")


def test_requeue_role_map_covers_all_dispatch_award_types():
    missing = set(roster_ops._AWARD_DISPATCH_FN_MAP) - set(roster_ops._REQUEUE_AWARD_ROLE_MAP)
    assert missing == set()


def test_terminus_slayer_awards_resolve_to_class_roles_without_notified_key():
    for role_id, award_type in TERMINUS_SLAYER_CLASS_AWARD_TYPES.items():
        resolved_role_id, notified_key = roster_ops._get_requeue_award_role_and_notified_key(award_type)
        assert resolved_role_id == role_id
        assert notified_key is None


def test_master_terminus_slayer_resolves_role_without_notified_key():
    resolved_role_id, notified_key = roster_ops._get_requeue_award_role_and_notified_key("master_terminus_slayer")
    assert resolved_role_id == MASTER_TERMINUS_SLAYER_ROLE_ID
    assert notified_key is None


def test_watch_veteran_resolves_role_without_challenge_notified_key():
    resolved_role_id, notified_key = roster_ops._get_requeue_award_role_and_notified_key("watch_veteran")
    assert resolved_role_id == WATCH_VETERAN_ROLE_ID
    assert notified_key is None


def test_challenge_award_preserves_notified_key():
    resolved_role_id, notified_key = roster_ops._get_requeue_award_role_and_notified_key("black_laurels")
    expected_role_id, expected_notified_key = roster_ops._CHALLENGE_AWARD_ROLE_MAP["black_laurels"]
    assert resolved_role_id == expected_role_id
    assert notified_key == expected_notified_key
