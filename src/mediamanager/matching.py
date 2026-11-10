"""Score search results against a guessed title/year."""
from __future__ import annotations

from difflib import SequenceMatcher

from .models import SearchResult
from .naming import normalize_for_match


def score(guess_title: str, guess_year: int | None, cand_title: str, cand_year: int | None,
          aliases: list[str] | None = None) -> float:
    g = normalize_for_match(guess_title)
    best = 0.0
    for t in [cand_title, *(aliases or [])]:
        c = normalize_for_match(t or "")
        if not c:
            continue
        r = 1.0 if g == c else SequenceMatcher(None, g, c).ratio()
        best = max(best, r)
    if guess_year and cand_year:
        diff = abs(guess_year - cand_year)
        best += 0.1 if diff == 0 else (0.0 if diff == 1 else -0.25)
    elif guess_year and not cand_year:
        best -= 0.05
    return max(0.0, min(best, 1.1)) / 1.1


def rank(results: list[SearchResult]) -> list[SearchResult]:
    # stable: provider order (popularity) breaks ties
    return sorted(results, key=lambda r: -r.score)
