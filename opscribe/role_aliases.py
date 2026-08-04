"""Shared role name normalization and alias canonicalization helpers."""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence


_DEFAULT_ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "Forgemaster": ("Forge Master",),
    "Huntmaster": ("Hunt Master",),
    "Blade Master": ("Blademaster",),
}


def _normalize_key(value: str) -> str:
    """Normalize a role string for case-insensitive alias matching."""
    return " ".join(str(value or "").strip().lower().split())


def _normalize_label(value: str) -> str:
    """Normalize role label whitespace while preserving original case."""
    return " ".join(str(value or "").strip().split())


def _alias_lookup(role_aliases: Mapping[str, Sequence[str]] | None = None) -> dict[str, str]:
    """Build normalized alias->canonical lookup from defaults plus config aliases."""
    out: dict[str, str] = {}

    def _register(canonical: str, aliases: Iterable[str]) -> None:
        canon_label = _normalize_label(canonical)
        if not canon_label:
            return
        out[_normalize_key(canon_label)] = canon_label
        for alias in aliases:
            alias_label = _normalize_label(str(alias or ""))
            if alias_label:
                out[_normalize_key(alias_label)] = canon_label

    for canonical, aliases in _DEFAULT_ROLE_ALIASES.items():
        _register(canonical, aliases)

    for canonical, aliases in (role_aliases or {}).items():
        if not isinstance(aliases, (list, tuple, set)):
            continue
        _register(str(canonical or ""), [str(alias) for alias in aliases])

    return out


def canonicalize_role_name(value: str, role_aliases: Mapping[str, Sequence[str]] | None = None) -> str:
    """Return canonical role label for known aliases, else normalized input label."""
    label = _normalize_label(value)
    if not label:
        return ""
    lookup = _alias_lookup(role_aliases)
    return lookup.get(_normalize_key(label), label)


def expand_role_names(
    role_names: Iterable[str],
    role_aliases: Mapping[str, Sequence[str]] | None = None,
) -> set[str]:
    """Return normalized role-name set including canonical aliases for each role."""
    out: set[str] = set()
    for role_name in role_names:
        label = _normalize_label(role_name)
        if not label:
            continue
        out.add(label)
        out.add(canonicalize_role_name(label, role_aliases=role_aliases))
    return out
