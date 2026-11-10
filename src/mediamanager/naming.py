"""Naming templates and character cleanup, matching tinyMediaManager's defaults."""
from __future__ import annotations

import re
import unicodedata

ARTICLES = ("the ", "a ", "an ")
_ILLEGAL = re.compile(r'[\\/*?"<>|\x00-\x1f]')


def clean_name(text: str, ascii_only: bool = False) -> str:
    """Make a string safe for a file/folder name. `Alien: Covenant` -> `Alien - Covenant`."""
    s = unicodedata.normalize("NFC", text or "")
    s = re.sub(r"\s*:\s*", " - ", s)
    s = _ILLEGAL.sub("", s)
    if ascii_only:
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"(\s-)+$", "", s)
    return s.rstrip(". ").strip()


def with_year(title: str, year: int | None, ascii_only: bool = False) -> str:
    base = clean_name(title, ascii_only)
    return f"{base} ({year})" if year else base


def movie_basename(title: str, year: int | None, ascii_only: bool = False) -> str:
    return with_year(title, year, ascii_only)


def show_folder(title: str, year: int | None, ascii_only: bool = False) -> str:
    return with_year(title, year, ascii_only)


def season_folder(season: int) -> str:
    return "Specials" if season == 0 else f"Season {season}"


def season_art_prefix(season: int) -> str:
    return "season-specials" if season == 0 else f"season{season:02d}"


def episode_basename(
    show: str, season: int, episodes: list[int], titles: list[str], ascii_only: bool = False
) -> str:
    nums = f"S{season:02d}" + "".join(f"E{e:02d}" for e in episodes)
    uniq: list[str] = []
    for t in titles:
        t = clean_name(t, ascii_only)
        if t and t not in uniq:
            uniq.append(t)
    name = f"{clean_name(show, ascii_only)} - {nums}"
    if uniq:
        name += " - " + " & ".join(uniq)
    if len(name) > 200:  # keep well under the 255-byte filename limit
        name = name[:200].rstrip(" -&")
    return name


def sort_title(title: str) -> str:
    t = title.lower()
    for a in ARTICLES:
        if t.startswith(a):
            return t[len(a):]
    return t


def normalize_for_match(text: str) -> str:
    s = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode("ascii")
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"^(the|a|an) ", "", s.strip())
    return s.strip()
