"""Resolve what a user typed — an ID, a prefixed ID, an IMDb id or a website URL — to one exact title.

Accepted (case-insensitive):
  603                     bare number: TMDB id for movies, TVDB id for TV
  tmdb:603  tvdb 371028   explicit provider (TMDB TV ids are converted to TVDB for shows)
  tt0133093               IMDb id (also imdb:tt0133093)
  https://www.themoviedb.org/movie/603-the-matrix
  https://www.themoviedb.org/tv/94605-arcane
  https://thetvdb.com/series/arcane          (slug)   …/dereferrer/series/371028  …?id=371028
  https://www.imdb.com/title/tt0133093/
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .models import SearchResult
from .providers.http import ProviderError

RefKind = Literal["number", "tmdb", "tmdb_tv", "tvdb", "tvdb_slug", "imdb"]


class LookupError_(Exception):
    """The reference was understood but leads nowhere usable (wrong type, not found …)."""


@dataclass(frozen=True)
class Ref:
    kind: RefKind
    value: str


_PATTERNS: list[tuple[str, RefKind]] = [
    (r"imdb\.com/(?:[a-z-]+/)?title/(tt\d{5,})", "imdb"),
    (r"themoviedb\.org/(?:[a-z-]+/)?movie/(\d+)", "tmdb"),
    (r"themoviedb\.org/(?:[a-z-]+/)?tv/(\d+)", "tmdb_tv"),
    (r"thetvdb\.com/.*[?&](?:id|seriesid)=(\d+)", "tvdb"),
    (r"thetvdb\.com/dereferrer/series/(\d+)", "tvdb"),
    # website URLs use slugs, and some slugs are numbers (the show "1923" is /series/1923)
    (r"thetvdb\.com/series/([a-z0-9][a-z0-9-]*)", "tvdb_slug"),
    (r"^(?:imdb\s*[: ]\s*)?(tt\d{5,})$", "imdb"),
    (r"^tmdb\s*[: ]\s*(?:tv\s*[: ]\s*)?(\d+)$", "tmdb"),
    (r"^tvdb\s*[: ]\s*(\d+)$", "tvdb"),
    (r"^(\d+)$", "number"),
]


def parse_ref(text: str) -> Ref | None:
    t = text.strip().strip("<>\"'")
    low = t.lower()
    for pat, kind in _PATTERNS:
        m = re.search(pat, low)
        if m:
            if kind == "tmdb" and re.match(r"^tmdb\s*[: ]\s*tv", low):
                kind = "tmdb_tv"
            return Ref(kind, m.group(1))
    return None


def make_lookup(kind: str, tmdb, tvdb):
    """A MatchRequest.lookup for movies (TMDB) or TV (TVDB, converting TMDB/IMDb ids)."""
    def lookup(text: str) -> SearchResult:
        ref = parse_ref(text)
        if ref is None:
            raise LookupError_(f"'{text}' isn't an ID, IMDb id (tt…) or TMDB/TVDB/IMDb URL")
        return resolve_movie(ref, tmdb) if kind == "movie" else resolve_show(ref, tvdb, tmdb)
    return lookup


def looks_like_ref(text: str) -> bool:
    """True for input that is unmistakably an id/URL (not a row number or a title)."""
    r = parse_ref(text)
    return r is not None and r.kind != "number"


def resolve_movie(ref: Ref, tmdb) -> SearchResult:
    try:
        if ref.kind in ("number", "tmdb"):
            tmdb_id = int(ref.value)
        elif ref.kind == "imdb":
            tmdb_id = tmdb.find_imdb(ref.value)
            if not tmdb_id:
                raise LookupError_(f"IMDb {ref.value} isn't a movie on TMDB (is it a TV show?)")
        else:
            raise LookupError_("that's a TV show reference; this item is a movie")
        res = tmdb.lookup_movie(tmdb_id)
    except ProviderError as e:
        raise LookupError_(f"lookup failed: {e}") from e
    if not res:
        raise LookupError_(f"TMDB has no movie with id {tmdb_id}")
    return res


def resolve_show(ref: Ref, tvdb, tmdb=None) -> SearchResult:
    try:
        if ref.kind in ("number", "tvdb"):
            res = tvdb.lookup_series(int(ref.value))
            if not res:
                raise LookupError_(f"TVDB has no series with id {ref.value}")
            return res
        if ref.kind == "tvdb_slug":
            res = tvdb.lookup_slug(ref.value)
            if not res and ref.value.isdigit():
                res = tvdb.lookup_series(int(ref.value))
            if not res:
                raise LookupError_(f"TVDB has no series '{ref.value}'")
            return res
        if ref.kind == "imdb":
            tvdb_id = tvdb.find_imdb(ref.value)
            if not tvdb_id and tmdb:
                tv = tmdb.find_imdb_tv(ref.value)
                tvdb_id = tmdb.tv_to_tvdb(tv) if tv else None
            if not tvdb_id:
                raise LookupError_(f"IMDb {ref.value} isn't a TV series on TVDB (is it a movie?)")
        elif ref.kind in ("tmdb", "tmdb_tv"):
            if not tmdb:
                raise LookupError_("TMDB isn't configured, so a TMDB id can't be converted")
            tvdb_id = tmdb.tv_to_tvdb(int(ref.value))
            if not tvdb_id:
                raise LookupError_(f"TMDB TV {ref.value} has no TVDB link (or is a movie id); "
                                   "use the TVDB id instead")
        else:
            raise LookupError_("unsupported reference")
        res = tvdb.lookup_series(tvdb_id)
    except ProviderError as e:
        raise LookupError_(f"lookup failed: {e}") from e
    if not res:
        raise LookupError_(f"TVDB has no series with id {tvdb_id}")
    return res
