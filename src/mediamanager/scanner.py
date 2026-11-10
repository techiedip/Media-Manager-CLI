"""Walk input paths and turn them into MovieItem / ShowItem objects."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterator, Literal

from lxml import etree

from . import parsing
from .models import EpisodeFile, MovieItem, ShowItem

log = logging.getLogger(__name__)
Kind = Literal["auto", "movie", "tv"]


# ------------------------------------------------------------------ helpers
def _videos_in(d: Path) -> list[Path]:
    try:
        return sorted(p for p in d.iterdir() if parsing.is_video(p) and not parsing.is_sample(p))
    except PermissionError:
        return []


def _subdirs(d: Path) -> list[Path]:
    try:
        return sorted(
            p for p in d.iterdir()
            if p.is_dir() and not p.name.startswith(".") and p.name.lower() not in parsing.EXTRA_DIRS
        )
    except PermissionError:
        return []


def _season_dirs(d: Path) -> list[tuple[int, Path]]:
    out = []
    for sd in d.iterdir():
        if sd.is_dir() and not sd.name.startswith("."):
            n = parsing.season_from_dir(sd.name)
            if n is not None:
                out.append((n, sd))
    return sorted(out)


def _is_multipart(videos: list[Path]) -> bool:
    if len(videos) < 2:
        return False
    parts = [parsing.cd_part(v) for v in videos]
    if any(p is None for p in parts):
        return False
    stems = {re.sub(r"[\s._-]*(?:cd|dvd|part|pt|disc|disk)[\s._-]*\d$", "", v.stem, flags=re.I) for v in videos}
    return len(stems) == 1


def _read_nfo_ids(nfo: Path) -> dict[str, str]:
    ids: dict[str, str] = {}
    if not nfo.is_file():
        return ids
    try:
        root = etree.parse(str(nfo), etree.XMLParser(recover=True)).getroot()
    except (etree.XMLSyntaxError, OSError):
        return ids
    if root is None:
        return ids
    for u in root.findall("uniqueid"):
        if u.get("type") and (u.text or "").strip():
            ids[u.get("type").lower()] = u.text.strip()
    for tag in ("tmdbid", "imdbid", "tvdbid", "imdb_id"):
        el = root.find(tag)
        if el is not None and (el.text or "").strip():
            ids.setdefault(tag.replace("id", "").replace("_", ""), el.text.strip())
    return ids


def classify(d: Path) -> Literal["movie", "tv", "library"]:
    if _season_dirs(d):
        return "tv"
    vids = _videos_in(d)
    if vids and sum(parsing.looks_like_episode(v.name) for v in vids) >= max(1, len(vids) // 2):
        return "tv"
    if (len(vids) == 1 or _is_multipart(vids)) and not any(_has_videos(sd) for sd in _subdirs(d)):
        return "movie"
    return "library"


def _has_videos(d: Path) -> bool:
    try:
        return any(parsing.is_video(p) for p in d.rglob("*"))
    except OSError:
        return False


# ------------------------------------------------------------------ builders
def movie_from_folder(d: Path, videos: list[Path]) -> MovieItem:
    title, year = parsing.guess_movie(d)
    if not year:  # folder name had none: try the file name
        t2, y2 = parsing.guess_movie(videos[0])
        if y2:
            title, year = t2, y2
    item = MovieItem(videos=videos, folder=d, guess_title=title, guess_year=year)
    for nfo in [d / "movie.nfo", *(v.with_suffix(".nfo") for v in videos)]:
        ids = _read_nfo_ids(nfo)
        if ids:
            if ids.get("tmdb", "").isdigit():
                item.tmdb_id = int(ids["tmdb"])
            item.imdb_id = ids.get("imdb") or item.imdb_id
            break
    return item


def movie_from_file(f: Path) -> MovieItem:
    title, year = parsing.guess_movie(f)
    item = MovieItem(videos=[f], folder=None, guess_title=title, guess_year=year)
    ids = _read_nfo_ids(f.with_suffix(".nfo"))
    if ids.get("tmdb", "").isdigit():
        item.tmdb_id = int(ids["tmdb"])
    item.imdb_id = ids.get("imdb")
    return item


def show_from_folder(d: Path) -> ShowItem:
    title, year = parsing.split_title_year(d.name)
    show = ShowItem(folder=d, guess_title=title, guess_year=year)
    ids = _read_nfo_ids(d / "tvshow.nfo")
    if ids.get("tvdb", "").isdigit():
        show.tvdb_id = int(ids["tvdb"])
    show.imdb_id = ids.get("imdb")

    def add(video: Path, hint: int | None) -> None:
        res = parsing.guess_episode(video, hint)
        if res is None:
            show.unmatched.append(video)
            return
        season, eps = res
        if hint is not None and season != hint and not parsing.looks_like_episode(video.name):
            season = hint
        show.episodes.append(EpisodeFile(path=video, season=season, episodes=eps))

    for v in _videos_in(d):
        add(v, None)
    for d2 in sorted(p for p in d.rglob("*") if p.is_dir()):
        rel = d2.relative_to(d).parts
        if any(part.startswith(".") or part.lower() in parsing.EXTRA_DIRS for part in rel):
            continue
        hint = None
        for part in reversed(rel):
            hint = parsing.season_from_dir(part)
            if hint is not None:
                break
        for v in _videos_in(d2):
            add(v, hint)
    show.episodes.sort(key=lambda e: (e.season, e.episodes))
    return show


# ------------------------------------------------------------------ entry point
def scan(path: Path, kind: Kind = "auto") -> Iterator[MovieItem | ShowItem]:
    path = path.expanduser().resolve()
    if path.is_file():
        if not parsing.is_video(path):
            log.warning("Not a video file: %s", path)
            return
        if kind == "tv" or (kind == "auto" and parsing.looks_like_episode(path.name)):
            log.warning("Single episode file given; point at the show folder instead: %s", path)
            return
        yield movie_from_file(path)
        return
    if not path.is_dir():
        log.warning("Path not found: %s", path)
        return

    what = _resolve(path, kind)
    if what == "skip":
        return
    if what == "tv":
        yield show_from_folder(path)
        return
    if what == "movie":
        yield movie_from_folder(path, _videos_in(path))
        return

    # library root: loose files become their own movies, subfolders are classified
    if kind != "tv":
        for v in _videos_in(path):
            yield movie_from_file(v)
    for sd in _subdirs(path):
        sub = _resolve(sd, kind)
        if sub == "tv":
            yield show_from_folder(sd)
        elif sub == "movie":
            yield movie_from_folder(sd, _videos_in(sd))
        elif sub == "library":
            if kind == "tv":
                yield show_from_folder(sd)  # nested/odd layouts: let the show scanner dig
            else:
                yield from scan(sd, kind)


def _resolve(d: Path, kind: Kind) -> Literal["movie", "tv", "library", "skip"]:
    """classify() adjusted for a forced --type. The same rules apply at every level."""
    what = classify(d)
    if kind == "movie":
        if _season_dirs(d):
            log.warning("Skipping %s: it has season folders, so it looks like a TV show "
                        "(drop --type movie to process it)", d.name)
            return "skip"
        if what == "tv":  # episode-looking filenames but no season folders
            return "movie" if len(_videos_in(d)) == 1 else "library"
    elif kind == "tv":
        if what == "movie":
            return "tv"
    return what
