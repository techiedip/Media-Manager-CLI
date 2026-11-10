"""Choose the best artwork for each kind from merged provider lists."""
from __future__ import annotations

from .models import Artwork

# kinds where text-free images are preferred
TEXTLESS_FIRST = {"fanart", "keyart"}
# provider priority per kind (lower = better); unknown providers rank last
SOURCE_PRIORITY = {
    "poster": ("tmdb", "tvdb", "fanart.tv"),
    "fanart": ("tmdb", "tvdb", "fanart.tv"),
    "keyart": ("fanart.tv", "tmdb", "tvdb"),
    "landscape": ("fanart.tv", "tmdb"),
    "thumb": ("fanart.tv", "tmdb", "tvdb"),
    "banner": ("fanart.tv", "tvdb"),
    "clearlogo": ("fanart.tv", "tvdb", "tmdb"),
    "clearart": ("fanart.tv", "tvdb"),
    "discart": ("fanart.tv",),
    "characterart": ("fanart.tv",),
}


def merge(*sources: dict[str, list[Artwork]]) -> dict[str, list[Artwork]]:
    out: dict[str, list[Artwork]] = {}
    for src in sources:
        for kind, arts in (src or {}).items():
            out.setdefault(kind, []).extend(arts)
    return out


TV_PRIORITY = {**SOURCE_PRIORITY, "poster": ("tvdb", "tmdb", "fanart.tv"),
               "fanart": ("tvdb", "fanart.tv", "tmdb"), "keyart": ("fanart.tv", "tvdb", "tmdb")}


def ranked(arts: list[Artwork], kind: str, lang: str, tv: bool = False) -> list[Artwork]:
    prio = (TV_PRIORITY if tv else SOURCE_PRIORITY).get(kind, ())

    def key(a: Artwork):
        if kind in TEXTLESS_FIRST:
            lang_rank = 0 if a.language is None else (1 if a.language == lang else 2)
        else:
            lang_rank = 0 if a.language == lang else (1 if a.language is None else 2)
        src_rank = prio.index(a.source) if a.source in prio else len(prio)
        return (lang_rank, src_rank, -a.score)

    seen, out = set(), []
    for a in sorted(arts, key=key):
        if a.url and a.url not in seen:
            seen.add(a.url)
            out.append(a)
    return out


def pick(arts: dict[str, list[Artwork]], kind: str, lang: str, tv: bool = False) -> Artwork | None:
    r = ranked(arts.get(kind, []), kind, lang, tv)
    return r[0] if r else None
