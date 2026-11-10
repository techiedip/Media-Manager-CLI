"""Command-line interface: `mm scan | run | scrape | rename | subtitles | undo | providers`."""
from __future__ import annotations

import logging
import os
import sys
from enum import Enum
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table
from rich.tree import Tree

from . import planner, scanner
from .chooser import Chooser
from .config import load_settings, mask
from .idlookup import LookupError_, make_lookup, parse_ref
from .models import MovieItem, ShowItem
from .pipeline import Context, Options, plan_movie, plan_show
from .providers.fanarttv import FanartTv
from .providers.http import Http, ProviderError
from .providers.subtitles import Addic7ed, OpenSubtitles, Yify
from .providers.tmdb import Tmdb
from .providers.tvdb import Tvdb

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Lightweight tinyMediaManager-style organizer for movies and TV shows.")
console = Console()
log = logging.getLogger("mediamanager")


class MediaType(str, Enum):
    auto = "auto"
    movie = "movie"
    tv = "tv"


PathsArg = Annotated[list[Path], typer.Argument(help="Movie/TV folders, library roots or movie files.")]
TypeOpt = Annotated[MediaType, typer.Option("--type", "-t", help="Force media type.")]
ApplyOpt = Annotated[bool, typer.Option("--apply", help="Actually make the changes (default is a dry run).")]
AutoOpt = Annotated[bool, typer.Option("--auto", help="Never prompt; take confident matches, skip the rest.")]
ConfirmOpt = Annotated[bool, typer.Option("--confirm", help="Ask about every match, even confident ones.")]
IdOpt = Annotated[Optional[str], typer.Option("--id", help=(
    "Exact match for a single item: an ID, IMDb id (tt…), or a TMDB/TVDB/IMDb URL."))]
TmdbIdOpt = Annotated[Optional[int], typer.Option(help="Exact TMDB id (movie, or TV show → its TVDB id).")]
TvdbIdOpt = Annotated[Optional[int], typer.Option(help="Exact TVDB series id.")]
ImdbIdOpt = Annotated[Optional[str], typer.Option(help="Exact IMDb id, e.g. tt0133093.")]
ForceOpt = Annotated[bool, typer.Option("--force", help="Re-download artwork/subtitles that already exist.")]
VerboseOpt = Annotated[bool, typer.Option("--verbose", "-v")]


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(message)s",
                        handlers=[RichHandler(console=console, show_path=False, show_time=False)])
    logging.getLogger("httpx").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------- context
def _build_context(opts: Options, chooser: Chooser) -> Context:
    s = load_settings()
    http = Http(s.mm_cache_dir, min_interval=0.05)
    tmdb = tvdb = fanart = None
    try:
        tmdb = Tmdb(http, s.tmdb_api_key, s.mm_language)
    except ProviderError as e:
        log.warning("%s — movies cannot be scraped", e)
    try:
        tvdb = Tvdb(http, s.tvdb_api_key, s.tvdb_pin, s.lang2, s.mm_cache_dir)
    except ProviderError as e:
        log.warning("%s — TV shows cannot be scraped", e)
    try:
        fanart = FanartTv(http, s.fanarttv_api_key)
    except ProviderError:
        log.info("Fanart.tv API key not set; using TMDB/TVDB artwork only")
    movie_subs, tv_subs = [], []
    try:
        os_ = OpenSubtitles(http, s.opensubtitles_api_key, s.opensubtitles_username, s.opensubtitles_password)
        movie_subs.append(os_)
        tv_subs.append(os_)
    except ProviderError:
        log.info("OpenSubtitles.com API key not set; using keyless subtitle sources")
    movie_subs.append(Yify(http))
    tv_subs.append(Addic7ed(http))
    return Context(s, http, tmdb, tvdb, fanart, movie_subs, tv_subs, opts, chooser)


# ---------------------------------------------------------------------------- rendering
ICONS = {"mkdir": "📁", "move": "➜ ", "write": "📝", "fetch": "⬇ ", "copy": "⧉ "}


def _rel(p: Path, base: Path) -> str:
    try:
        return str(p.relative_to(base))
    except ValueError:
        return str(p)


def _render(plan: planner.Plan, base: Path) -> None:
    tree = Tree(f"[bold cyan]{plan.title}[/]")
    for op in plan.changes:
        if op.kind == "move":
            line = f"{ICONS['move']} {_rel(op.src, base)}  →  [green]{_rel(op.dst, base)}[/]"
        else:
            verb = {"write": "update" if op.overwrite else "write"}.get(op.kind, op.kind)
            note = f" [dim]({op.note})[/]" if op.note else ""
            line = f"{ICONS.get(op.kind, '•')} {verb} [green]{_rel(op.dst, base)}[/]{note}"
        tree.add(line)
    for w in plan.warnings:
        tree.add(f"[yellow]⚠ {w}[/]")
    if not plan.changes and not plan.warnings:
        tree.add("[dim]nothing to do[/]")
    console.print(tree)


# ---------------------------------------------------------------------------- runner
def _make_chooser(auto: bool, confirm: bool) -> Chooser:
    if auto and confirm:
        console.print("[red]--auto and --confirm can't be combined.[/]")
        raise typer.Exit(2)
    mode = "auto" if auto else ("confirm" if confirm else "ask")
    if mode != "auto" and not sys.stdin.isatty():
        console.print("[yellow]Not running in an interactive terminal: unsure matches will be skipped "
                      "(as with --auto) and listed at the end.[/]")
        mode = "auto"
    return Chooser(console, mode)


def _execute(paths: list[Path], media_type: MediaType, opts: Options, apply: bool, auto: bool,
             confirm: bool = False) -> None:
    chooser = _make_chooser(auto, confirm)
    ctx = _build_context(opts, chooser)
    items: list[MovieItem | ShowItem] = []
    for p in paths:
        items.extend(i for i in scanner.scan(p, media_type.value)
                     if not isinstance(i, ShowItem) or i.episodes or i.unmatched)
    if not items:
        console.print("[yellow]No movies or TV shows found.[/]")
        raise typer.Exit(1)
    if opts.match_ref and len(items) > 1:
        console.print(f"[red]--id/--tmdb-id/--tvdb-id/--imdb-id match one item, but {len(items)} were found. "
                      "Point at a single movie or show folder.[/]")
        raise typer.Exit(2)

    journal = planner.Journal(ctx.settings.mm_cache_dir / "undo") if apply else None
    total_ops = errors = 0
    for item in items:
        try:
            plan, _ = plan_movie(item, ctx) if isinstance(item, MovieItem) else plan_show(item, ctx)
        except ProviderError as e:
            console.print(f"[red]✗ {item.location}: {e}[/]")
            errors += 1
            continue
        _render(plan, item.location.parent)
        if journal and plan.changes:
            def on_error(op, exc):
                nonlocal errors
                errors += 1
                console.print(f"  [red]✗ {op.kind} {op.dst.name}: {exc}[/]")
            with console.status(f"Applying {len(plan.changes)} changes…"):
                total_ops += planner.apply(plan, journal, on_error)
    chooser.report()
    if journal:
        if journal.entries:
            console.print(f"\n[bold green]Done[/]: {total_ops} changes applied, {errors} errors. "
                          f"Undo with: [cyan]mm undo[/]")
        else:
            journal.path.unlink(missing_ok=True)
            console.print(f"\n[bold]No changes were needed[/] ({errors} errors).")
    else:
        console.print("\n[bold yellow]Dry run[/] — nothing was changed. Add [cyan]--apply[/] to do it.")


def _match_ref(match_id: str | None, tmdb_id: int | None, tvdb_id: int | None,
               imdb_id: str | None) -> str | None:
    given = [r for r in (match_id, tmdb_id and f"tmdb:{tmdb_id}", tvdb_id and f"tvdb:{tvdb_id}",
                         imdb_id and (imdb_id if imdb_id.lower().startswith("tt") else f"tt{imdb_id}")) if r]
    if len(given) > 1:
        console.print("[red]Use only one of --id, --tmdb-id, --tvdb-id, --imdb-id.[/]")
        raise typer.Exit(2)
    if given and parse_ref(given[0]) is None:
        console.print(f"[red]Can't read {given[0]!r} as an ID, IMDb id or TMDB/TVDB/IMDb URL.[/]")
        raise typer.Exit(2)
    return given[0] if given else None


@app.command()
def run(paths: PathsArg, media_type: TypeOpt = MediaType.auto, apply: ApplyOpt = False,
        auto: AutoOpt = False, confirm: ConfirmOpt = False, force: ForceOpt = False,
        no_rename: Annotated[bool, typer.Option("--no-rename")] = False,
        no_artwork: Annotated[bool, typer.Option("--no-artwork")] = False,
        no_nfo: Annotated[bool, typer.Option("--no-nfo")] = False,
        no_subtitles: Annotated[bool, typer.Option("--no-subtitles")] = False,
        match_id: IdOpt = None,
        tmdb_id: TmdbIdOpt = None, tvdb_id: TvdbIdOpt = None, imdb_id: ImdbIdOpt = None,
        set_name: Annotated[Optional[str], typer.Option("--set", help="Put the movie in this custom set.")] = None,
        verbose: VerboseOpt = False) -> None:
    """Full pipeline: identify, scrape, write NFOs, download artwork and subtitles, rename."""
    _setup_logging(verbose)
    ref = _match_ref(match_id, tmdb_id, tvdb_id, imdb_id)
    _execute(paths, media_type, Options(not no_rename, not no_artwork, not no_nfo, not no_subtitles,
                                        force, ref, set_name), apply, auto, confirm)


@app.command()
def scrape(paths: PathsArg, media_type: TypeOpt = MediaType.auto, apply: ApplyOpt = False,
           auto: AutoOpt = False, confirm: ConfirmOpt = False, force: ForceOpt = False,
           match_id: IdOpt = None, verbose: VerboseOpt = False) -> None:
    """NFOs + artwork only; no renaming, no subtitles."""
    _setup_logging(verbose)
    _execute(paths, media_type, Options(rename=False, subtitles=False, force=force,
                                        match_ref=_match_ref(match_id, None, None, None)), apply, auto, confirm)


@app.command()
def rename(paths: PathsArg, media_type: TypeOpt = MediaType.auto, apply: ApplyOpt = False,
           auto: AutoOpt = False, confirm: ConfirmOpt = False, match_id: IdOpt = None,
           verbose: VerboseOpt = False) -> None:
    """Rename/organize files and folders (and refresh NFOs); no downloads."""
    _setup_logging(verbose)
    _execute(paths, media_type, Options(artwork=False, subtitles=False,
                                        match_ref=_match_ref(match_id, None, None, None)), apply, auto, confirm)


@app.command()
def subtitles(paths: PathsArg, media_type: TypeOpt = MediaType.auto, apply: ApplyOpt = False,
              auto: AutoOpt = False, confirm: ConfirmOpt = False, match_id: IdOpt = None,
              verbose: VerboseOpt = False) -> None:
    """Download missing subtitles only (files keep their current names)."""
    _setup_logging(verbose)
    _execute(paths, media_type, Options(rename=False, artwork=False, nfo=False,
                                        match_ref=_match_ref(match_id, None, None, None)), apply, auto, confirm)


def _resolve_ref(item: MovieItem | ShowItem, ref: str) -> str:
    """Look up an exact-ID reference for one item and describe what it points to."""
    s = load_settings()
    http = Http(s.mm_cache_dir, min_interval=0.05)
    is_movie = isinstance(item, MovieItem)
    tmdb = tvdb = None
    try:
        tmdb = Tmdb(http, s.tmdb_api_key, s.mm_language)
    except ProviderError:
        pass
    if not is_movie:
        try:
            tvdb = Tvdb(http, s.tvdb_api_key, s.tvdb_pin, s.lang2, s.mm_cache_dir)
        except ProviderError as e:
            raise LookupError_(str(e)) from e
    elif tmdb is None:
        raise LookupError_("TMDB API key missing")
    res = make_lookup("movie" if is_movie else "tv", tmdb, tvdb)(ref)
    year = f" ({res.year})" if res.year else ""
    return f"{res.title}{year} · {'TMDB' if is_movie else 'TVDB'} {res.id}"


@app.command()
def scan(paths: PathsArg, media_type: TypeOpt = MediaType.auto, match_id: IdOpt = None,
         tmdb_id: TmdbIdOpt = None, tvdb_id: TvdbIdOpt = None, imdb_id: ImdbIdOpt = None) -> None:
    """Show what would be detected, without contacting any provider.

    With --id/--tmdb-id/--tvdb-id/--imdb-id on a single item, the ID is looked up online and the
    title it resolves to is shown, so you can check it before `mm run`.
    """
    ref = _match_ref(match_id, tmdb_id, tvdb_id, imdb_id)
    items = [i for p in paths for i in scanner.scan(p, media_type.value)]
    if ref and len(items) != 1:
        console.print(f"[red]--id/--tmdb-id/--tvdb-id/--imdb-id match one item, but {len(items)} were found. "
                      "Point at a single movie or show folder.[/]")
        raise typer.Exit(2)
    match = None
    if ref:
        try:
            with console.status("Looking up ID…"):
                match = _resolve_ref(items[0], ref)
        except LookupError_ as e:
            console.print(f"[red]✗ {ref}: {e}[/]")
            raise typer.Exit(2)
    t = Table("Type", "Guess", "Year", "Files", "Location", *(["ID match"] if match else []))
    for item in items:
        extra_col = [f"[green]{match}[/]"] if match else []
        if isinstance(item, MovieItem):
            t.add_row("movie", item.guess_title, str(item.guess_year or ""), str(len(item.videos)),
                      str(item.location), *extra_col)
        else:
            seasons = sorted({e.season for e in item.episodes})
            extra = f" (+{len(item.unmatched)} unnumbered)" if item.unmatched else ""
            t.add_row("tv", item.guess_title, str(item.guess_year or ""),
                      f"{len(item.episodes)} eps / {len(seasons)} seasons{extra}", str(item.folder), *extra_col)
    console.print(t)
    if not items:
        console.print("[yellow]No movies or TV shows found.[/]")


@app.command()
def undo(log_file: Annotated[Optional[Path], typer.Argument(help="Undo log (default: latest).")] = None) -> None:
    """Revert the last applied run (or a specific undo log)."""
    s = load_settings()
    d = s.mm_cache_dir / "undo"
    if log_file is None:
        logs = sorted(d.glob("*.json")) if d.is_dir() else []
        if not logs:
            console.print("Nothing to undo.")
            raise typer.Exit()
        log_file = logs[-1]
    problems = planner.undo(log_file)
    for p in problems:
        console.print(f"[yellow]{p}[/]")
    console.print(f"Reverted {log_file.name}.")


@app.command()
def providers() -> None:
    """Show which providers are configured (secrets masked)."""
    s = load_settings()
    t = Table("Provider", "Status")
    t.add_row("TMDB", "key " + mask(s.tmdb_api_key))
    t.add_row("TVDB", "key " + mask(s.tvdb_api_key))
    fan = "key " + mask(s.fanarttv_api_key)
    if not s.fanarttv_api_key and s.fanarttv_username:
        fan += " — account found, but the API needs the personal API key from your fanart.tv profile"
    t.add_row("Fanart.tv", fan)
    osub = "key " + mask(s.opensubtitles_api_key) + (", login set" if s.opensubtitles_username else "")
    if not s.opensubtitles_api_key:
        osub += " — needs a consumer API key (opensubtitles.com/consumers)"
    t.add_row("OpenSubtitles.com", osub)
    t.add_row("YIFY subtitles (movies)", "keyless")
    t.add_row("Addic7ed (TV)", "keyless")
    t.add_row("Subtitle languages", ", ".join(s.subtitle_languages))
    t.add_row("Cache", str(s.mm_cache_dir))
    console.print(t)


def main() -> None:  # pragma: no cover
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
