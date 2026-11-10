"""Turn a scanned item into a Plan: identify, scrape, then lay out renames/NFOs/artwork/subtitles."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import artwork, mediainfo, nfo
from .chooser import MatchRequest
from .idlookup import LookupError_, make_lookup
from .config import Settings
from .models import (Artwork, EpisodeMeta, MovieItem, MovieMeta, MovieSet, SearchResult,
                     SeasonMeta, ShowItem, ShowMeta)
from .naming import (clean_name, episode_basename, movie_basename, season_art_prefix,
                     season_folder, show_folder)
from .parsing import SUB_EXTS, cd_part
from .planner import Plan
from .providers.fanarttv import FanartTv
from .providers.http import Http, ProviderError
from .providers.subtitles import SubtitleProvider
from .providers.tmdb import Tmdb
from .providers.tvdb import Tvdb

log = logging.getLogger(__name__)

MOVIE_ART = ("poster", "fanart", "banner", "clearart", "clearlogo", "discart", "keyart", "landscape")
SHOW_ART = ("poster", "fanart", "banner", "thumb", "keyart", "clearart", "clearlogo", "characterart")
SEASON_ART = ("poster", "banner", "thumb")
LANG3_TO_2 = {"eng": "en", "spa": "es", "fre": "fr", "fra": "fr", "ger": "de", "deu": "de",
              "ita": "it", "por": "pt", "dut": "nl", "nld": "nl", "jpn": "ja", "kor": "ko",
              "chi": "zh", "zho": "zh", "hin": "hi", "rus": "ru", "swe": "sv", "ara": "ar"}
Chooser = Callable[[MatchRequest, list[SearchResult]], int | None]


@dataclass
class Options:
    rename: bool = True
    artwork: bool = True
    nfo: bool = True
    subtitles: bool = True
    force: bool = False
    match_ref: str | None = None  # --id/--tmdb-id/--tvdb-id/--imdb-id: an ID, IMDb id or URL
    set_name: str | None = None


@dataclass
class Context:
    settings: Settings
    http: Http
    tmdb: Tmdb | None
    tvdb: Tvdb | None
    fanart: FanartTv | None
    movie_subs: list[SubtitleProvider]
    tv_subs: list[SubtitleProvider]
    opts: Options
    choose: Chooser
    dead_providers: set[str] = field(default_factory=set)

    @property
    def lang(self) -> str:
        return self.settings.lang2


# ---------------------------------------------------------------------------- helpers
_ART_SUFFIX = re.compile(r"^-(poster|fanart|banner|clearart|clearlogo|discart|keyart|landscape|thumb|logo|disc)\.(jpe?g|png|tbn)$", re.I)
_SIDE_EXTS = {*SUB_EXTS, ".nfo", ".jpg", ".jpeg", ".png", ".tbn"}


def sidecars(video: Path) -> list[Path]:
    """Files next to `video` that belong to it: 'Name.en.srt', 'Name.nfo', 'Name-poster.jpg' …"""
    stem = video.stem
    out = []
    try:
        for p in video.parent.iterdir():
            if p == video or not p.is_file() or p.name.startswith("._") or not p.name.startswith(stem):
                continue
            rest = p.name[len(stem):]
            if rest.startswith(".") and p.suffix.lower() in _SIDE_EXTS:
                out.append(p)
            elif _ART_SUFFIX.match(rest):
                out.append(p)
    except OSError:
        pass
    return sorted(out)


def normalize_sub_suffix(suffix: str) -> str:
    """'.eng.srt' -> '.en.srt', '.English.forced.srt' stays readable."""
    parts = suffix.split(".")
    return ".".join(LANG3_TO_2.get(p.lower(), p) for p in parts)


def _fetcher(http: Http, url: str):
    return lambda: http.fetch_bytes(url)


def _subtitle_producer(ctx: Context, providers: list[SubtitleProvider], lang: str, video: Path,
                       search) -> Callable[[], bytes | None]:
    def produce() -> bytes | None:
        for prov in providers:
            if prov.name in ctx.dead_providers:
                continue
            try:
                cands = search(prov, video, lang)
                for c in sorted(cands, key=lambda c: -c.score)[:3]:
                    sub = prov.download(c)
                    if sub and sub.ext == ".srt" and sub.content.strip():
                        log.info("subtitle %s for %s from %s", lang, video.name, prov.name)
                        return sub.content
            except ProviderError as e:
                log.warning("%s: %s", prov.name, e)
                if "refused" in str(e) or "limit" in str(e) or "request failed" in str(e):
                    ctx.dead_providers.add(prov.name)
            except Exception as e:  # scraping providers can break in odd ways
                log.warning("%s failed: %s", prov.name, e)
        return None
    return produce


def _has_sub(plan: Plan, folder: Path, stem: str, lang: str) -> bool:
    if plan.exists(folder / f"{stem}.{lang}.srt"):
        return True
    real = plan.real(folder)
    if real.is_dir():
        pat = re.compile(re.escape(stem) + rf"\.({lang}|{_lang3(lang)})(\.[a-z]+)*\.(srt|ass|ssa|sub|vtt)$", re.I)
        return any(pat.match(p.name) for p in real.iterdir())
    return False


def _lang3(lang2: str) -> str:
    return next((k for k, v in LANG3_TO_2.items() if v == lang2), lang2)


def _move_with_sidecars(plan: Plan, video: Path, dst_folder: Path, new_stem: str,
                        nfo_target: Path | None = None) -> Path:
    """Move a video and its sidecars to dst_folder/new_stem.*; returns the video's new path."""
    real_video = plan.real(video)
    for s in sidecars(real_video):
        cur = video.parent / s.name  # planned (virtual) path of the sidecar
        suffix = normalize_sub_suffix(s.name[len(real_video.stem):])
        if nfo_target is not None and suffix.lower() == ".nfo":
            if not plan.exists(nfo_target):
                plan.move(cur, nfo_target)
            continue
        plan.move(cur, dst_folder / f"{new_stem}{suffix}")
    dst = dst_folder / f"{new_stem}{video.suffix.lower()}"
    moved = plan.move(video, dst)
    return dst if moved or video == dst else video


# ---------------------------------------------------------------------------- movies
def _forced(lookup, ref: str) -> int:
    """Resolve an ID given on the command line; a bad one stops this item with a clear message."""
    try:
        return lookup(ref).id
    except LookupError_ as e:
        raise ProviderError(f"{ref}: {e}") from e


def identify_movie(item: MovieItem, ctx: Context) -> int | None:
    lookup = make_lookup("movie", ctx.tmdb, ctx.tvdb)
    if ctx.opts.match_ref:
        return _forced(lookup, ctx.opts.match_ref)
    if item.tmdb_id:
        return item.tmdb_id
    if item.imdb_id:
        found = ctx.tmdb.find_imdb(item.imdb_id)
        if found:
            return found
    req = MatchRequest("movie", item.guess_title, item.guess_year, item.location, ctx.tmdb.search_movie, lookup)
    return ctx.choose(req, req.search(item.guess_title, item.guess_year))


def plan_movie(item: MovieItem, ctx: Context) -> tuple[Plan, MovieMeta | None]:
    plan = Plan(title=str(item.location))
    if not ctx.tmdb:
        plan.warnings.append("TMDB not configured; skipped")
        return plan, None
    tmdb_id = identify_movie(item, ctx)
    if not tmdb_id:
        plan.warnings.append("no match chosen; skipped")
        return plan, None
    meta = ctx.tmdb.movie(tmdb_id)
    if ctx.fanart:
        try:
            meta.artwork = artwork.merge(meta.artwork, ctx.fanart.movie(meta.tmdb_id))
        except ProviderError as e:
            plan.warnings.append(f"fanart.tv: {e}")
    plan.title = f"{meta.title} ({meta.year})  [tmdb {meta.tmdb_id}]"

    s, o = ctx.settings, ctx.opts
    base = movie_basename(meta.title, meta.year, s.mm_ascii_names)
    videos = sorted(item.videos, key=lambda v: (cd_part(v) or 0, v.name))
    multipart = len(videos) > 1
    own_folder = item.folder is not None or o.rename

    # ---- folder
    if item.folder is not None:
        folder = item.folder
        if o.rename and item.folder.name != base:
            target = item.folder.parent / base
            if plan.move(item.folder, target, note="rename folder"):
                folder = target
        vids = [folder / v.relative_to(item.folder) for v in videos]
    else:
        folder = videos[0].parent / base if o.rename else videos[0].parent
        if o.rename:
            plan.mkdir(folder)
        vids = list(videos)

    # ---- videos + sidecars
    final: list[Path] = []
    for i, v in enumerate(vids):
        stem = (f"{base}-cd{cd_part(videos[i]) or i + 1}" if multipart else base) if o.rename else v.stem
        if o.rename:
            nfo_target = folder / "movie.nfo" if not multipart or i == 0 else None
            final.append(_move_with_sidecars(plan, v, folder, stem, nfo_target))
        else:
            final.append(v)
    art_stem = base if o.rename else final[0].stem

    # ---- artwork
    chosen: dict[str, str] = {}
    if o.artwork:
        for kind in MOVIE_ART:
            a = artwork.pick(meta.artwork, kind, ctx.lang)
            if not a:
                continue
            chosen[kind] = a.url
            prefixed = folder / f"{art_stem}-{kind}.{a.ext}"
            plan.fetch(prefixed, _fetcher(ctx.http, a.url), note=f"{kind} ({a.source})", force=o.force)
            if kind in ("poster", "fanart") and own_folder:
                plan.copy(prefixed, folder / f"{kind}.{a.ext}", force=o.force)
        if s.mm_extrafanart and own_folder:
            extra = artwork.ranked(meta.artwork.get("fanart", []), "fanart", ctx.lang)[1:s.mm_extrafanart + 1]
            for n, a in enumerate(extra, 1):
                plan.fetch(folder / "extrafanart" / f"fanart{n}.{a.ext}", _fetcher(ctx.http, a.url),
                           note="extrafanart", force=o.force)
    else:
        for kind in MOVIE_ART:
            a = artwork.pick(meta.artwork, kind, ctx.lang)
            if a:
                chosen[kind] = a.url

    # ---- movie set
    mset = MovieSet(None, o.set_name) if o.set_name else meta.movie_set
    if mset and o.artwork and s.mm_movieset_dir:
        set_dir = Path(s.mm_movieset_dir).expanduser() / clean_name(mset.name, s.mm_ascii_names)
        plan.mkdir(set_dir)
        for kind in ("poster", "fanart", "clearlogo", "keyart"):
            a = artwork.pick(mset.artwork, kind, ctx.lang)
            if a:
                plan.fetch(set_dir / f"{kind}.{a.ext}", _fetcher(ctx.http, a.url), note=f"set {kind}",
                           force=o.force)

    # ---- NFO
    if o.nfo:
        nfo_path = folder / "movie.nfo" if own_folder else folder / f"{art_stem}.nfo"
        video0 = final[0]
        plan.write(nfo_path, lambda: nfo.movie_nfo(meta, chosen, mediainfo.probe(video0), nfo_path, mset),
                   note="movie.nfo")

    # ---- subtitles
    if o.subtitles and ctx.movie_subs:
        for v in final:
            for lang in s.subtitle_languages:
                if _has_sub(plan, folder, v.stem, lang):
                    continue
                plan.fetch(folder / f"{v.stem}.{lang}.srt",
                           _subtitle_producer(ctx, ctx.movie_subs, lang, v,
                                              lambda p, vid, lg: p.search_movie(meta, vid, lg)),
                           note=f"subtitle {lang} (search)")
    if item.folder is not None:
        _cleanup_empty_dirs(plan, folder)
    return plan, meta


# ---------------------------------------------------------------------------- TV
def identify_show(item: ShowItem, ctx: Context) -> int | None:
    lookup = make_lookup("tv", ctx.tmdb, ctx.tvdb)
    if ctx.opts.match_ref:
        return _forced(lookup, ctx.opts.match_ref)
    if item.tvdb_id:
        return item.tvdb_id
    req = MatchRequest("tv", item.guess_title, item.guess_year, item.folder, ctx.tvdb.search_series, lookup)
    return ctx.choose(req, req.search(item.guess_title, item.guess_year))


def plan_show(item: ShowItem, ctx: Context) -> tuple[Plan, ShowMeta | None]:
    plan = Plan(title=str(item.folder))
    if not ctx.tvdb:
        plan.warnings.append("TVDB not configured; skipped")
        return plan, None
    tvdb_id = identify_show(item, ctx)
    if not tvdb_id:
        plan.warnings.append("no match chosen; skipped")
        return plan, None
    seasons = {e.season for e in item.episodes}
    wanted = {(e.season, n) for e in item.episodes for n in e.episodes}
    show = ctx.tvdb.series(tvdb_id, seasons, wanted if ctx.opts.nfo else set())
    _enrich_show(show, ctx, plan)
    plan.title = f"{show.title} ({show.year})  [tvdb {show.tvdb_id}]"

    s, o = ctx.settings, ctx.opts
    root = item.folder
    if o.rename:
        target = item.folder.parent / show_folder(show.title, show.year, s.mm_ascii_names)
        if item.folder.name != target.name and plan.move(item.folder, target, note="rename show folder"):
            root = target

    # ---- episodes
    season_dirs: set[Path] = set()
    for ef in item.episodes:
        cur = root / ef.path.relative_to(item.folder)
        metas = [show.episodes[(ef.season, n)] for n in ef.episodes if (ef.season, n) in show.episodes]
        if not metas:
            plan.warnings.append(f"no TVDB episode S{ef.season:02d}E{ef.episodes[0]:02d}: {ef.path.name}")
        sdir = root / season_folder(ef.season)
        season_dirs.add(sdir)
        if o.rename:
            plan.mkdir(sdir)
            stem = (episode_basename(show.title, ef.season, ef.episodes, [m.title for m in metas],
                                     s.mm_ascii_names) if metas else cur.stem)
            final = _move_with_sidecars(plan, cur, sdir, stem)
        else:
            final = cur
        folder, stem = final.parent, final.stem
        if not metas:
            continue
        if o.artwork and metas[0].thumb:
            ext = Artwork(metas[0].thumb).ext
            plan.fetch(folder / f"{stem}-thumb.{ext}", _fetcher(ctx.http, metas[0].thumb),
                       note="episode thumb", force=o.force)
        if o.nfo:
            nfo_path = folder / f"{stem}.nfo"
            plan.write(nfo_path, (lambda ms=metas, f=final, p=nfo_path:
                                  nfo.episode_nfo(show, ms, mediainfo.probe(f), p)), note="episode nfo")
        if o.subtitles and ctx.tv_subs:
            for lang in s.subtitle_languages:
                if _has_sub(plan, folder, stem, lang):
                    continue
                ep0 = metas[0]
                plan.fetch(folder / f"{stem}.{lang}.srt",
                           _subtitle_producer(ctx, ctx.tv_subs, lang, final,
                                              lambda p, vid, lg, e=ep0: p.search_episode(show, e, vid, lg)),
                           note=f"subtitle {lang} (search)")
    for v in item.unmatched:
        plan.warnings.append(f"could not read season/episode from: {v.name}")

    # ---- show + season artwork and NFOs
    chosen: dict = {}
    for kind in SHOW_ART:
        a = artwork.pick(show.artwork, kind, ctx.lang, tv=True)
        if not a:
            continue
        chosen[kind] = a.url
        if o.artwork:
            plan.fetch(root / f"{kind}.{a.ext}", _fetcher(ctx.http, a.url), note=f"{kind} ({a.source})",
                       force=o.force)
    for n in sorted(seasons):
        sm = show.seasons.get(n) or SeasonMeta(number=n)
        for kind in SEASON_ART:
            a = artwork.pick(sm.artwork, kind, ctx.lang, tv=True)
            if not a:
                continue
            chosen[(n, kind)] = a.url
            if o.artwork:
                plan.fetch(root / f"{season_art_prefix(n)}-{kind}.{a.ext}", _fetcher(ctx.http, a.url),
                           note=f"season {n} {kind}", force=o.force)
        if o.nfo:
            p = root / season_folder(n) / "season.nfo"
            if o.rename or plan.exists(p.parent):
                plan.write(p, lambda sm=sm, p=p: nfo.season_nfo(sm, p), note="season.nfo")
    if o.nfo:
        p = root / "tvshow.nfo"
        plan.write(p, lambda: nfo.tvshow_nfo(show, chosen, p), note="tvshow.nfo")
    if o.rename:
        _cleanup_empty_dirs(plan, root, keep=season_dirs)
    return plan, show


def _enrich_show(show: ShowMeta, ctx: Context, plan: Plan) -> None:
    if ctx.tmdb:
        try:
            if not show.tmdb_id:
                show.tmdb_id = ctx.tmdb.tv_by_tvdb(show.tvdb_id)
            if show.tmdb_id:
                show.artwork = artwork.merge(show.artwork, ctx.tmdb.tv_images(show.tmdb_id))
                show.rating, _ = ctx.tmdb.tv_rating(show.tmdb_id)
        except ProviderError as e:
            plan.warnings.append(f"tmdb: {e}")
    if ctx.fanart:
        try:
            art, seasons = ctx.fanart.tv(show.tvdb_id)
            show.artwork = artwork.merge(show.artwork, art)
            for n, sart in seasons.items():
                if n in show.seasons:
                    show.seasons[n].artwork = artwork.merge(show.seasons[n].artwork, sart)
        except ProviderError as e:
            plan.warnings.append(f"fanart.tv: {e}")


def _cleanup_empty_dirs(plan: Plan, root: Path, keep: set[Path] = frozenset()) -> None:
    """Queue removal of sub-directories that become empty after the moves (deepest first)."""
    real_root = plan.real(root)
    if not real_root.is_dir():
        return
    dirs = sorted((d for d in real_root.rglob("*") if d.is_dir()), key=lambda d: -len(d.parts))
    for d in dirs:
        virtual = root / d.relative_to(real_root)
        if virtual not in keep:
            plan.rmdir_if_empty(virtual)
