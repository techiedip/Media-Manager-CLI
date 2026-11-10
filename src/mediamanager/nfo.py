"""Kodi-compatible NFO writers (also read by Jellyfin, Emby and Plex agents)."""
from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path

from lxml import etree

from .models import EpisodeMeta, MovieMeta, MovieSet, Person, SeasonMeta, ShowMeta
from .naming import sort_title

HEADER = b'<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n'
# user-owned fields kept from an existing NFO
PRESERVE = ("userrating", "playcount", "lastplayed", "watched", "dateadded", "resume", "lockdata")


def _sub(parent, tag: str, text=None, **attrs):
    el = etree.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text not in (None, ""):
        el.text = str(text)
    return el


def _existing(path: Path | None) -> dict[str, etree._Element]:
    if not path or not path.is_file():
        return {}
    try:
        root = etree.parse(str(path), etree.XMLParser(recover=True, remove_blank_text=True)).getroot()
    except (etree.XMLSyntaxError, OSError):
        return {}
    if root is None:
        return {}
    return {t: el for t in PRESERVE if (el := root.find(t)) is not None}


def _finish(root, existing: dict) -> bytes:
    for tag in PRESERVE:
        if tag in existing:
            old = root.find(tag)
            new = copy.deepcopy(existing[tag])
            if old is not None:
                root.replace(old, new)
            else:
                root.append(new)
    if root.find("dateadded") is None:
        _sub(root, "dateadded", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    etree.indent(root, space="  ")
    return HEADER + etree.tostring(root, encoding="utf-8")


def _actors(root, actors: list[Person]) -> None:
    for a in actors:
        el = _sub(root, "actor")
        _sub(el, "name", a.name)
        _sub(el, "role", a.role)
        _sub(el, "order", a.order)
        if a.thumb:
            _sub(el, "thumb", a.thumb)
        if a.tmdb_id:
            _sub(el, "tmdbid", a.tmdb_id)


def _fileinfo(root, stream: dict | None) -> None:
    if not stream:
        return
    sd = _sub(_sub(root, "fileinfo"), "streamdetails")
    for v in stream.get("video", [])[:1]:
        el = _sub(sd, "video")
        for k in ("codec", "aspect", "width", "height", "framerate", "durationinseconds", "scantype"):
            _sub(el, k, v.get(k))
        if v.get("hdrtype"):
            _sub(el, "hdrtype", v["hdrtype"])
    for a in stream.get("audio", []):
        el = _sub(sd, "audio")
        for k in ("codec", "language", "channels"):
            _sub(el, k, a.get(k))
    for s in stream.get("subtitle", []):
        _sub(_sub(sd, "subtitle"), "language", s.get("language"))


def _ratings(root, source: str, rating: float, votes: int) -> None:
    if not rating:
        return
    r = _sub(_sub(root, "ratings"), "rating", name=source, max="10", default="true")
    _sub(r, "value", rating)
    _sub(r, "votes", votes)
    _sub(root, "rating", rating)


def movie_nfo(m: MovieMeta, art: dict[str, str], stream: dict | None,
              existing_path: Path | None, movie_set: MovieSet | None) -> bytes:
    keep = _existing(existing_path)
    root = etree.Element("movie")
    _sub(root, "title", m.title)
    _sub(root, "originaltitle", m.original_title or m.title)
    _sub(root, "sorttitle", sort_title(m.title))
    _ratings(root, "themoviedb", m.rating, m.votes)
    _sub(root, "userrating")
    _sub(root, "outline")
    _sub(root, "plot", m.plot)
    _sub(root, "tagline", m.tagline)
    _sub(root, "runtime", m.runtime or None)
    for kind in ("poster", "banner", "clearart", "clearlogo", "discart", "keyart", "landscape"):
        if art.get(kind):
            aspect = {"landscape": "landscape"}.get(kind, kind)
            _sub(root, "thumb", art[kind], aspect=aspect)
    if art.get("fanart"):
        _sub(_sub(root, "fanart"), "thumb", art["fanart"])
    _sub(root, "mpaa", m.mpaa)
    _sub(root, "playcount")
    _sub(root, "lastplayed")
    _sub(root, "id", m.tmdb_id)
    _sub(root, "uniqueid", m.tmdb_id, type="tmdb", default="true")
    if m.imdb_id:
        _sub(root, "uniqueid", m.imdb_id, type="imdb")
    for g in m.genres:
        _sub(root, "genre", g)
    for c in m.countries:
        _sub(root, "country", c)
    if movie_set:
        s = _sub(root, "set")
        _sub(s, "name", movie_set.name)
        _sub(s, "overview", movie_set.overview)
        if movie_set.tmdb_id:
            _sub(root, "tmdbCollectionId", movie_set.tmdb_id)
    for t in m.tags:
        _sub(root, "tag", t)
    for w in m.writers:
        _sub(root, "credits", w.name)
    for d in m.directors:
        _sub(root, "director", d.name)
    _sub(root, "premiered", m.premiered)
    _sub(root, "year", m.year)
    for st in m.studios:
        _sub(root, "studio", st)
    _sub(root, "trailer", m.trailer)
    _fileinfo(root, stream)
    _actors(root, m.actors)
    return _finish(root, keep)


def tvshow_nfo(s: ShowMeta, art: dict[str, str], existing_path: Path | None) -> bytes:
    keep = _existing(existing_path)
    root = etree.Element("tvshow")
    _sub(root, "title", s.title)
    _sub(root, "originaltitle", s.original_title or s.title)
    _sub(root, "sorttitle", sort_title(s.title))
    _ratings(root, "themoviedb", s.rating, 0)
    _sub(root, "plot", s.plot)
    _sub(root, "runtime", s.runtime or None)
    for kind in ("poster", "banner", "clearart", "clearlogo", "characterart", "keyart", "thumb"):
        if art.get(kind):
            _sub(root, "thumb", art[kind], aspect="landscape" if kind == "thumb" else kind)
    for (n, kind), url in sorted((k, v) for k, v in art.items() if isinstance(k, tuple)):
        _sub(root, "thumb", url, aspect=kind, type="season", season=n)
    if art.get("fanart"):
        _sub(_sub(root, "fanart"), "thumb", art["fanart"])
    _sub(root, "mpaa", s.mpaa)
    _sub(root, "id", s.tvdb_id)
    _sub(root, "uniqueid", s.tvdb_id, type="tvdb", default="true")
    if s.imdb_id:
        _sub(root, "uniqueid", s.imdb_id, type="imdb")
    if s.tmdb_id:
        _sub(root, "uniqueid", s.tmdb_id, type="tmdb")
    for g in s.genres:
        _sub(root, "genre", g)
    for c in s.countries:
        _sub(root, "country", c)
    _sub(root, "status", s.status)
    _sub(root, "premiered", s.premiered)
    _sub(root, "year", s.year)
    if s.enddate:
        _sub(root, "enddate", s.enddate)
    for st in s.studios:
        _sub(root, "studio", st)
    _actors(root, s.actors)
    return _finish(root, keep)


def season_nfo(season: SeasonMeta, existing_path: Path | None) -> bytes:
    keep = _existing(existing_path)
    root = etree.Element("season")
    _sub(root, "title", season.title or f"Season {season.number}")
    _sub(root, "sorttitle", season.title or f"Season {season.number}")
    _sub(root, "plot", season.plot)
    _sub(root, "outline", season.plot)
    _sub(root, "year", season.year)
    _sub(root, "premiered", season.premiered)
    _sub(root, "releasedate", season.premiered)
    if season.tvdb_id:
        _sub(root, "tvdbid", season.tvdb_id)
        _sub(root, "uniqueid", season.tvdb_id, type="tvdb", default="true")
    _sub(root, "seasonnumber", season.number)
    return _finish(root, keep)


def episode_nfo(show: ShowMeta, eps: list[EpisodeMeta], stream: dict | None,
                existing_path: Path | None) -> bytes:
    """One <episodedetails> per episode; multi-episode files get several, as Kodi expects."""
    keep = _existing(existing_path)
    chunks = []
    for ep in eps:
        root = etree.Element("episodedetails")
        _sub(root, "title", ep.title)
        _sub(root, "showtitle", show.title)
        _sub(root, "season", ep.season)
        _sub(root, "episode", ep.episode)
        _sub(root, "plot", ep.plot)
        _sub(root, "runtime", ep.runtime or None)
        if ep.thumb:
            _sub(root, "thumb", ep.thumb)
        if ep.tvdb_id:
            _sub(root, "id", ep.tvdb_id)
            _sub(root, "uniqueid", ep.tvdb_id, type="tvdb", default="true")
        _sub(root, "aired", ep.aired)
        _sub(root, "premiered", ep.aired)
        for w in ep.writers:
            _sub(root, "credits", w)
        for d in ep.directors:
            _sub(root, "director", d)
        _sub(root, "watched", "false")
        _fileinfo(root, stream)
        _actors(root, ep.guests)
        chunks.append(_finish(root, keep)[len(HEADER):])
    return HEADER + b"\n".join(chunks)
