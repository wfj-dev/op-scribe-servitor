"""Unit tests for is_allowed_channel() in bot.py.

Covers:
- channel_policies allow/deny semantics
- channel key matching by name vs. by ID string
- fallback to allowed_command_channel_ids
- fallback to default_allowed_channels config key
- final fallback to the DEFAULT_ALLOWED_CHANNELS constant
"""

import types
import unittest.mock

import opscribe.bot as bot
from opscribe.bot import is_allowed_channel, DEFAULT_ALLOWED_CHANNELS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeChannel:
    """Minimal stand-in for a discord.TextChannel."""

    def __init__(self, name: str, channel_id: int, parent_id: int | None = None):
        self.name = name
        self.id = channel_id
        self.parent_id = parent_id


def _make_interaction(channel_name: str, channel_id: int, command_name: str, parent_id: int | None = None):
    """Return a simple namespace that mimics a discord.Interaction."""
    interaction = types.SimpleNamespace()
    interaction.channel = FakeChannel(channel_name, channel_id, parent_id=parent_id)
    interaction.command = types.SimpleNamespace(name=command_name)
    interaction.data = {"name": command_name}
    return interaction


# ---------------------------------------------------------------------------
# channel_policies – name-based matching
# ---------------------------------------------------------------------------


def test_allow_policy_by_name_permits_listed_command():
    """A command in the allow list for a named channel should be permitted."""
    config = {
        "channel_policies": {
            "arming-chamber": {"allow": ["forge_rite", "set_rite"]},
        }
    }
    with unittest.mock.patch.dict(bot.CONFIG, config, clear=True):
        ix = _make_interaction("arming-chamber", 111, "forge_rite")
        assert is_allowed_channel(ix) is True


def test_allow_policy_by_name_blocks_unlisted_command():
    """A command NOT in the allow list for a named channel should be denied."""
    config = {
        "channel_policies": {
            "arming-chamber": {"allow": ["forge_rite", "set_rite"]},
        }
    }
    with unittest.mock.patch.dict(bot.CONFIG, config, clear=True):
        ix = _make_interaction("arming-chamber", 111, "tally_deeds")
        assert is_allowed_channel(ix) is False


def test_deny_policy_by_name_blocks_listed_command():
    """A command in the deny list for a named channel should be denied."""
    config = {
        "channel_policies": {
            "data-vault": {"deny": ["forge_rite", "set_rite"]},
        }
    }
    with unittest.mock.patch.dict(bot.CONFIG, config, clear=True):
        ix = _make_interaction("data-vault", 222, "forge_rite")
        assert is_allowed_channel(ix) is False


def test_deny_policy_by_name_permits_unlisted_command():
    """A command NOT in the deny list for a named channel should be allowed."""
    config = {
        "channel_policies": {
            "data-vault": {"deny": ["forge_rite", "set_rite"]},
        }
    }
    with unittest.mock.patch.dict(bot.CONFIG, config, clear=True):
        ix = _make_interaction("data-vault", 222, "tally_deeds")
        assert is_allowed_channel(ix) is True


# ---------------------------------------------------------------------------
# channel_policies – ID-based matching
# ---------------------------------------------------------------------------


def test_allow_policy_by_id_permits_listed_command():
    """Policy keyed by channel ID (as string) should match by channel ID."""
    channel_id = 1430055064969674777
    config = {
        "channel_policies": {
            str(channel_id): {"allow": ["tally_deeds"]},
        }
    }
    with unittest.mock.patch.dict(bot.CONFIG, config, clear=True):
        # Use a channel name that does NOT appear in policies to confirm
        # that the ID path is taken.
        ix = _make_interaction("some-other-name", channel_id, "tally_deeds")
        assert is_allowed_channel(ix) is True


def test_allow_policy_by_id_blocks_unlisted_command():
    """A command NOT in the ID-keyed allow list should be denied."""
    channel_id = 1430055064969674777
    config = {
        "channel_policies": {
            str(channel_id): {"allow": ["tally_deeds"]},
        }
    }
    with unittest.mock.patch.dict(bot.CONFIG, config, clear=True):
        ix = _make_interaction("some-other-name", channel_id, "forge_rite")
        assert is_allowed_channel(ix) is False


def test_name_policy_takes_precedence_over_id_policy():
    """When both name and ID keys exist, the name match should win."""
    channel_id = 9999
    config = {
        "channel_policies": {
            "special-channel": {"allow": ["cmd_a"]},
            str(channel_id): {"allow": ["cmd_b"]},
        }
    }
    with unittest.mock.patch.dict(bot.CONFIG, config, clear=True):
        # The channel has both matching name AND matching ID.
        # Name policy allows only cmd_a, so cmd_b should be denied.
        ix = _make_interaction("special-channel", channel_id, "cmd_b")
        assert is_allowed_channel(ix) is False

        ix2 = _make_interaction("special-channel", channel_id, "cmd_a")
        assert is_allowed_channel(ix2) is True


# ---------------------------------------------------------------------------
# Fallback – allowed_command_channel_ids
# ---------------------------------------------------------------------------


def test_fallback_allowed_channel_ids_permits_matching_id():
    """When no channel_policies match, allowed_command_channel_ids is checked."""
    allowed_id = 5555
    config = {"allowed_command_channel_ids": [allowed_id]}
    with unittest.mock.patch.dict(bot.CONFIG, config, clear=True):
        ix = _make_interaction("random-channel", allowed_id, "any_command")
        assert is_allowed_channel(ix) is True


def test_fallback_allowed_channel_ids_blocks_non_matching_id():
    """Channel not in allowed_command_channel_ids should be denied."""
    config = {"allowed_command_channel_ids": [5555]}
    with unittest.mock.patch.dict(bot.CONFIG, config, clear=True):
        ix = _make_interaction("random-channel", 9999, "any_command")
        assert is_allowed_channel(ix) is False


def test_fallback_allowed_channel_ids_permits_thread_under_matching_parent_id():
    """A thread should be allowed when its parent forum ID is in allowed_command_channel_ids."""
    config = {"allowed_command_channel_ids": [5555]}
    with unittest.mock.patch.dict(bot.CONFIG, config, clear=True):
        ix = _make_interaction("forum-post", 9999, "any_command", parent_id=5555)
        assert is_allowed_channel(ix) is True


def test_fallback_allowed_channel_ids_blocks_thread_when_parent_not_listed():
    """A thread should be denied when neither thread ID nor parent ID is allowlisted."""
    config = {"allowed_command_channel_ids": [5555]}
    with unittest.mock.patch.dict(bot.CONFIG, config, clear=True):
        ix = _make_interaction("forum-post", 9999, "any_command", parent_id=7777)
        assert is_allowed_channel(ix) is False


def test_fallback_kt_forum_parent_ids_permits_thread_under_allowed_parent():
    """Forum posts under configured KT parents should inherit broad command access."""
    config = {"target_packages": {"kt_forum_parent_ids": [7777]}}
    with unittest.mock.patch.dict(bot.CONFIG, config, clear=True):
        with unittest.mock.patch.object(bot, "ALLOWED_KT_FORUM_PARENT_IDS", {7777}):
            ix = _make_interaction("kt-post", 8888, "any_command", parent_id=7777)
            assert is_allowed_channel(ix) is True


def test_command_restrictions_still_override_allowed_kt_forum_parent_thread():
    """Command-level locks should still win inside approved KT forum posts."""
    config = {
        "command_channel_restrictions": {"submit_kill_log": [5555]},
        "target_packages": {"kt_forum_parent_ids": [7777]},
    }
    with unittest.mock.patch.dict(bot.CONFIG, config, clear=True):
        with unittest.mock.patch.object(bot, "ALLOWED_KT_FORUM_PARENT_IDS", {7777}):
            ix = _make_interaction("kt-post", 8888, "submit_kill_log", parent_id=7777)
            assert is_allowed_channel(ix) is False


# ---------------------------------------------------------------------------
# Fallback – default_allowed_channels config key
# ---------------------------------------------------------------------------


def test_fallback_default_allowed_channels_config_permits_named_channel():
    """When no ID list is set, default_allowed_channels names are checked."""
    config = {"default_allowed_channels": ["ops-channel"]}
    with unittest.mock.patch.dict(bot.CONFIG, config, clear=True):
        ix = _make_interaction("ops-channel", 1234, "any_command")
        assert is_allowed_channel(ix) is True


def test_fallback_default_allowed_channels_config_blocks_other_channel():
    """A channel not in default_allowed_channels should be denied."""
    config = {"default_allowed_channels": ["ops-channel"]}
    with unittest.mock.patch.dict(bot.CONFIG, config, clear=True):
        ix = _make_interaction("general", 1234, "any_command")
        assert is_allowed_channel(ix) is False


# ---------------------------------------------------------------------------
# Final fallback – DEFAULT_ALLOWED_CHANNELS constant
# ---------------------------------------------------------------------------


def test_final_fallback_default_constant_permits_known_channel():
    """With empty config the DEFAULT_ALLOWED_CHANNELS constant is the last resort."""
    known_channel = next(iter(DEFAULT_ALLOWED_CHANNELS))
    with unittest.mock.patch.dict(bot.CONFIG, {}, clear=True):
        ix = _make_interaction(known_channel, 9876, "any_command")
        assert is_allowed_channel(ix) is True


def test_final_fallback_default_constant_blocks_unknown_channel():
    """With empty config, a channel not in DEFAULT_ALLOWED_CHANNELS is denied."""
    with unittest.mock.patch.dict(bot.CONFIG, {}, clear=True):
        ix = _make_interaction("unknown-channel", 9876, "any_command")
        assert is_allowed_channel(ix) is False


# ---------------------------------------------------------------------------
# Edge-cases
# ---------------------------------------------------------------------------


def test_no_channel_returns_false():
    """If the interaction has no channel, is_allowed_channel should return False."""
    with unittest.mock.patch.dict(bot.CONFIG, {}, clear=True):
        ix = types.SimpleNamespace()
        ix.channel = None
        ix.command = types.SimpleNamespace(name="cmd")
        ix.data = {"name": "cmd"}
        assert is_allowed_channel(ix) is False


def test_exception_in_interaction_returns_false():
    """An unexpected exception inside is_allowed_channel should return False."""
    with unittest.mock.patch.dict(bot.CONFIG, {}, clear=True):
        # Passing a completely broken object exercises the outer except clause.
        assert is_allowed_channel(object()) is False
