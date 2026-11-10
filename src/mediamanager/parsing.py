"""Guess titles, years and episode numbers from file/folder names."""
from __future__ import annotations

import re
from pathlib import Path

from guessit import guessit

VIDEO_EXTS = {
    ".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".mpg", ".mpeg", ".ts", ".m2ts",
    ".webm", ".flv", ".divx", ".ogm", ".vob", ".iso", ".3gp",
}
SUB_EXTS = {".srt", ".sub", ".idx", ".ass", ".ssa", ".vtt", ".smi", ".sup"}
EXTRA_DIRS = {
    "extras", "extra", "featurettes", "featurette", "behind the scenes", "deleted scenes",
    "interviews", "scenes", "shorts", "trailers", "trailer", "other", "samples", "sample",
    "bonus", "specials features", "extrafanart", "extrathumbs", ".actors", "@eadir",
}
_YEAR_PAREN = re.compile(r"^(?P<title>.+?)\s*[\(\[](?P<year>(19|20)\d{2})[\)\]]\s*$")
_SEASON_DIR = re.compile(r"^(?:season|series|staffel|saison|s)[\s._-]*(\d{1,3})\b", re.I)
_SPECIALS_DIR = re.compile(r"^(specials?|season[\s._-]*0+)$", re.I)
_SXXEYY = re.compile(r"(?<![a-z0-9])s(\d{1,3})[\s._-]*((?:e\d{1,4}[\s._-]*-?[\s._-]*)+)", re.I)
_NXNN = re.compile(r"(?<!\d)(\d{1,2})x(\d{2,3})(?:[x-](\d{2,3}))?(?!\d)", re.I)
_CDPART = re.compile(r"[\s._-]*(?:cd|dvd|part|pt|disc|disk)[\s._-]*(\d)$", re.I)


def is_video(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in VIDEO_EXTS and not p.name.startswith("._")


def is_sample(p: Path) -> bool:
    stem = p.stem.lower()
    return bool(re.search(r"(^|[\s._-])sample($|[\s._-])", stem)) or stem.endswith("-trailer")


def split_title_year(name: str) -> tuple[str, int | None]:
    """'Arcane (2021)' -> ('Arcane', 2021). Falls back to guessit for release-style names."""
    m = _YEAR_PAREN.match(name.strip())
    if m:
        return m.group("title").strip(), int(m.group("year"))
    g = guessit(name, {"type": "movie"})
    title = str(g.get("title") or name).strip()
    year = g.get("year")
    return title, int(year) if isinstance(year, int) else None


def guess_movie(path: Path) -> tuple[str, int | None]:
    """Title/year for a movie file or folder name."""
    name = path.stem if path.is_file() else path.name
    name = _CDPART.sub("", name)
    m = _YEAR_PAREN.match(name.strip())
    if m:
        return m.group("title").strip(), int(m.group("year"))
    g = guessit(name, {"type": "movie"})
    title = str(g.get("title") or name)
    if g.get("alternative_title") and " - " in name:
        title = f"{title} - {g['alternative_title']}"
    year = g.get("year")
    return title.strip(), int(year) if isinstance(year, int) else None


def cd_part(path: Path) -> int | None:
    m = _CDPART.search(path.stem)
    return int(m.group(1)) if m else None


def season_from_dir(name: str) -> int | None:
    if _SPECIALS_DIR.match(name.strip()):
        return 0
    m = _SEASON_DIR.match(name.strip())
    return int(m.group(1)) if m else None


def looks_like_episode(name: str) -> bool:
    return bool(_SXXEYY.search(name) or _NXNN.search(name))


def guess_episode(path: Path, season_hint: int | None = None) -> tuple[int, list[int]] | None:
    """Return (season, [episodes]) or None when the file can't be numbered."""
    stem = path.stem
    m = _SXXEYY.search(stem)
    if m:
        eps = [int(x) for x in re.findall(r"e(\d{1,4})", m.group(2), re.I)]
        rng = re.search(r"e(\d{1,4})[\s._]*-[\s._]*e?(\d{1,4})", m.group(2), re.I)
        if rng and len(eps) == 2 and int(rng.group(2)) > int(rng.group(1)):
            eps = list(range(int(rng.group(1)), int(rng.group(2)) + 1))
        return int(m.group(1)), sorted(set(eps))
    m = _NXNN.search(stem)
    if m:
        eps = [int(m.group(2))] + ([int(m.group(3))] if m.group(3) else [])
        return int(m.group(1)), eps
    g = guessit(path.name, {"type": "episode"})
    ep = g.get("episode")
    season = g.get("season", season_hint)
    if ep is None:
        # bare "01 - Title.mkv" inside a season folder
        bare = re.match(r"^(?:e|ep|episode)?[\s._-]*(\d{1,3})(?!\d)", stem, re.I)
        if bare and season_hint is not None:
            return season_hint, [int(bare.group(1))]
        return None
    eps = ep if isinstance(ep, list) else [ep]
    if isinstance(season, list):
        season = season[0]
    if season is None:
        season = 1
    return int(season), sorted({int(e) for e in eps})
