"""
Pre-import roster_ops at conftest module load time (before any test stubs are
installed) so that monkeypatch teardown restores the real cached module instead
of evicting it and forcing a bare reimport against incomplete discord stubs.
"""
import sys
import types
from types import SimpleNamespace

import pytest

# ── Pre-cache at module-load time (real discord still intact) ─────────────────
try:
    import opscribe._bot_globals as _g_preload

    if _g_preload.bot is None:
        _preload_tree = SimpleNamespace(command=lambda **_kw: (lambda f: f))
        _g_preload.bot = SimpleNamespace(tree=_preload_tree)

    import opscribe.roster_ops  # noqa: F401
except Exception:
    pass
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True, scope="session")
def _ensure_roster_ops_cached():
    """Belt-and-suspenders: patch discord stubs then re-import if pre-cache failed."""
    if "opscribe.roster_ops" in sys.modules:
        yield
        return

    try:
        discord = sys.modules.get("discord")
        if discord is not None:
            if not hasattr(discord, "TextStyle"):
                discord.TextStyle = SimpleNamespace(paragraph=2, short=1)
            if not hasattr(discord, "Attachment"):
                discord.Attachment = object
            if not hasattr(discord, "__getattr__"):
                discord.__getattr__ = lambda name: type(name, (), {})
            ac = sys.modules.get("discord.app_commands")
            if ac is not None and not hasattr(ac, "Choice"):
                ac.Choice = type("Choice", (), {"__init__": lambda self, *a, **kw: None})
            ui = sys.modules.get("discord.ui") or getattr(discord, "ui", None)
            if ui is not None:
                for attr in ("Modal", "TextInput"):
                    if not hasattr(ui, attr):
                        setattr(ui, attr, type(attr, (), {"__init__": lambda self, *a, **kw: None, "__init_subclass__": classmethod(lambda cls, **_kw: None)}))

        import opscribe._bot_globals as _g
        if _g.bot is None:
            _tree = SimpleNamespace(command=lambda **_kw: (lambda f: f))
            _g.bot = SimpleNamespace(tree=_tree)

        import opscribe.roster_ops  # noqa: F401
    except Exception:
        pass
    yield
