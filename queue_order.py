"""Stable, human-readable processing order for Jellyfin movies and episodes."""
from __future__ import annotations


def _text(value: object) -> str:
    return str(value or "").strip().casefold()


def _number(value: object) -> tuple[int, int]:
    """Known season/episode numbers sort before unknown numbers."""
    try:
        return (0, int(value)) if value is not None else (1, 0)
    except (TypeError, ValueError):
        return (1, 0)


def item_sort_key(item: dict) -> tuple:
    """Alphabetize titles; group each TV series and sort its episodes in order.

    Jellyfin's IndexNumber/ParentIndexNumber are the standard episode and
    season fields. Id is a deterministic tiebreaker when metadata is missing.
    """
    episode = item.get("Type") == "Episode"
    title = _text(item.get("SeriesName") if episode else item.get("Name"))
    if not title:
        title = _text(item.get("Name"))
    return (
        title,
        1 if episode else 0,
        _number(item.get("ParentIndexNumber")) if episode else (0, 0),
        _number(item.get("IndexNumber")) if episode else (0, 0),
        _text(item.get("Name")),
        _text(item.get("Id")),
    )
