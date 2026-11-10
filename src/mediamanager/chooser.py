"""Decide whether a search result is a safe match, and let the user pick when it isn't."""
from __future__ import annotations

import re
import shlex
from datetime import date
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .idlookup import LookupError_, looks_like_ref
from .models import SearchResult
from .naming import normalize_for_match

Mode = Literal["ask", "confirm", "auto"]
CONFIDENT = 0.92   # top score needed to accept without asking …
MARGIN = 0.05      # … and how far ahead of the runner-up it must be
AUTO_MIN = 0.80    # --auto also accepts this when clearly ahead (2× margin)
SHOWN = 8


@dataclass
class MatchRequest:
    kind: Literal["movie", "tv"]
    guess_title: str
    guess_year: int | None
    location: Path
    search: Callable[[str, int | None], list[SearchResult]]
    # exact lookup of an ID / URL / IMDb id; raises idlookup.LookupError_ with a readable reason
    lookup: Callable[[str], SearchResult] | None = None

    @property
    def provider(self) -> str:
        return "TMDB" if self.kind == "movie" else "TVDB"

    @property
    def id_flag(self) -> str:
        return "--tmdb-id" if self.kind == "movie" else "--tvdb-id"

    def url(self, rid: int) -> str:
        if self.kind == "movie":
            return f"https://www.themoviedb.org/movie/{rid}"
        return f"https://thetvdb.com/dereferrer/series/{rid}"


@dataclass
class Skipped:
    request: MatchRequest
    candidates: list[SearchResult]
    reason: str


NO_YEAR_MIN = 0.88  # without a year the best possible score is ~0.91


def _base(title: str) -> str:
    """'The Office (US)' -> 'office'; 'The Lion King' -> 'lion king'."""
    t = title
    while (stripped := re.sub(r"\s*\([^()]*\)\s*$", "", t)) != t:
        t = stripped
    return normalize_for_match(t)


def has_namesake(results: list[SearchResult]) -> bool:
    """Another result shares the top result's title: a remake, or a regional version (US/UK…)."""
    if not results:
        return False
    top = _base(results[0].title)
    return any(r.id != results[0].id and r.score >= 0.4 and _base(r.title) == top for r in results[1:])


def is_confident(results: list[SearchResult], year_known: bool = True, guess_title: str = "") -> bool:
    if not results:
        return False
    top = results[0].score
    margin = top - (results[1].score if len(results) > 1 else 0.0)
    if year_known:
        return top >= CONFIDENT and margin >= MARGIN
    # No year to tell remakes / regional versions apart: safe only if nothing shares the title,
    # and the title is an exact match (typo-neighbours like "Arcanes" don't count against it)
    # or clearly ahead of everything else.
    if has_namesake(results):
        return False
    exact = bool(guess_title) and normalize_for_match(guess_title) in (
        _base(results[0].title), normalize_for_match(results[0].title))
    return exact or (top >= NO_YEAR_MIN and margin >= 2 * MARGIN)


def auto_pick(results: list[SearchResult], year_known: bool = True,
              guess_title: str = "") -> SearchResult | None:
    if is_confident(results, year_known, guess_title):
        return results[0]
    if not results or (not year_known and has_namesake(results)):
        return None
    margin = results[0].score - (results[1].score if len(results) > 1 else 0.0)
    if results[0].score >= AUTO_MIN and margin >= 2 * MARGIN:
        return results[0]
    return None


def parse_query(text: str) -> tuple[str, int | None]:
    """'Dune 2021', 'Dune (2021)' -> ('Dune', 2021); 'Dune' -> ('Dune', None).

    A bare trailing number might be part of the title ('Blade Runner 2049', 'Wonder Woman 1984');
    `research` also searches the full text so those still turn up.
    """
    m = re.match(r"^(.*?)[\s,]*\(?((?:19|20)\d{2})\)?\s*$", text.strip())
    if m and m.group(1).strip() and int(m.group(2)) <= date.today().year + 3:
        return m.group(1).strip(), int(m.group(2))
    return text.strip(), None


def research(req: "MatchRequest", text: str) -> tuple[tuple[str, int | None], list[SearchResult]]:
    query = parse_query(text)
    results = req.search(*query)
    if query[1] is not None and not re.search(r"\(\s*\d{4}\s*\)\s*$", text):
        merged = {r.id: r for r in results}
        for r in req.search(text.strip(), None):
            if r.id not in merged or r.score > merged[r.id].score:
                merged[r.id] = r
        results = sorted(merged.values(), key=lambda r: -r.score)
    return query, results


@dataclass
class Chooser:
    console: Console
    mode: Mode = "ask"
    input_fn: Callable[[str], str] | None = None
    skipped: list[Skipped] = field(default_factory=list)

    def _ask(self, prompt: str) -> str:
        if self.input_fn:
            return self.input_fn(prompt)
        return self.console.input(prompt)

    # ----------------------------------------------------------------- entry
    def __call__(self, req: MatchRequest, results: list[SearchResult]) -> int | None:
        year_known = req.guess_year is not None
        if self.mode == "auto":
            pick = auto_pick(results, year_known, req.guess_title)
            if pick:
                return pick.id
            reason = ("no results" if not results else
                      "same title, different versions" if has_namesake(results) else "ambiguous")
            self.skipped.append(Skipped(req, results[:3], reason))
            return None
        if self.mode == "ask" and is_confident(results, year_known, req.guess_title):
            return results[0].id
        return self._interactive(req, results)

    # ----------------------------------------------------------------- prompt loop
    def _interactive(self, req: MatchRequest, results: list[SearchResult]) -> int | None:
        query = (req.guess_title, req.guess_year)
        self._render(req, results, query)
        while True:
            default = "1" if results and (self.mode == "confirm" or results[0].score >= 0.75) else ""
            hint = f" \\[{default}]" if default else ""
            try:
                raw = self._ask(f"[bold]Choose[/] (# · s <title> \\[year] · id <ID or URL> · i <#> · ? help){hint}: ")
            except (EOFError, KeyboardInterrupt):
                raw = "q"
            ans = raw.strip() or default
            low = ans.lower()

            if not ans:
                self.skipped.append(Skipped(req, results[:3], "skipped by you"))
                return None
            if low in ("?", "h", "help"):
                self._help(req)
                continue
            if low in ("q", "quit"):
                self.console.print("[yellow]OK — skipping this and any other unsure matches in this run.[/]")
                self.mode = "auto"
                self.skipped.append(Skipped(req, results[:3], "skipped by you"))
                return None
            if ans.isdigit():
                n = int(ans)
                if 1 <= n <= min(len(results), SHOWN):
                    return results[n - 1].id
                if n > SHOWN:  # too big to be a row: almost certainly an id
                    picked = self._lookup(req, ans)
                    if picked is not None:
                        return picked
                    continue
                self.console.print(f"[red]No row {n}.[/]")
                continue
            m = re.match(r"^id(?:\s*:\s*|\s+)(.+)$", ans, re.I)
            if m or looks_like_ref(ans):
                picked = self._lookup(req, m.group(1) if m else ans)
                if picked is not None:
                    return picked
                continue
            m = re.match(r"^i(?:nfo)?\s*(\d+)$", low)
            if m:
                self._info(req, results, int(m.group(1)))
                continue
            m = re.match(r"^(?:s|search)\s+(.+)$", ans, re.I)
            if m:
                text = m.group(1)
                try:
                    text = " ".join(shlex.split(text))
                except ValueError:
                    pass
                query, results = research(req, text)
                self._render(req, results, query)
                continue
            self.console.print("[red]Didn't understand that.[/] Type [bold]?[/] for help.")

    def _lookup(self, req: MatchRequest, text: str) -> int | None:
        """Fetch exactly what the user referenced, show it, and ask for confirmation."""
        if req.lookup is None:
            self.console.print("[red]ID lookup isn't available here.[/]")
            return None
        try:
            with self.console.status(f"Looking up {escape(text)} …"):
                r = req.lookup(text)
        except LookupError_ as e:
            self.console.print(f"[red]{escape(str(e))}[/]")
            return None
        self.console.print(f"Found: [bold green]{escape(r.title)}[/] ({r.year or '—'}) · {req.provider} {r.id}")
        if r.overview:
            self.console.print(f"[dim]{escape(r.overview[:300])}{'…' if len(r.overview) > 300 else ''}[/]",
                               soft_wrap=True)
        self.console.print(f"[link={req.url(r.id)}]{req.url(r.id)}[/link]", highlight=False)
        try:
            ok = self._ask("Use this? \\[Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ok = "n"
        if ok in ("", "y", "yes"):
            return r.id
        self.console.print("[dim]Not used.[/]")
        return None

    # ----------------------------------------------------------------- output
    def _render(self, req: MatchRequest, results: list[SearchResult], query: tuple[str, int | None]) -> None:
        title, year = query
        self.console.rule(f"[bold]{req.kind.upper()}[/] · {escape(req.location.name)}")
        self.console.print(f"[dim]{escape(str(req.location))}[/]", soft_wrap=True, highlight=False)
        self.console.print(f"Searched {req.provider} for [cyan]{escape(title)}[/]"
                           + (f" ([cyan]{year}[/])" if year else " (no year)"))
        if not results:
            self.console.print("[yellow]No results.[/] Try [bold]s <other title> \\[year][/], or paste an "
                               f"ID / {req.provider} or IMDb URL.")
            return
        t = Table(show_edge=False, pad_edge=False, expand=True)
        t.add_column("#", justify="right", no_wrap=True)
        t.add_column("Title", ratio=3)
        t.add_column("Year", no_wrap=True)
        t.add_column(f"{req.provider} ID", no_wrap=True)
        t.add_column("Match", justify="right", no_wrap=True)
        t.add_column("Overview", ratio=5, overflow="ellipsis", no_wrap=True)
        for i, r in enumerate(results[:SHOWN], 1):
            style = "green" if i == 1 and r.score >= 0.75 else ""
            t.add_row(str(i), escape(r.title), str(r.year or "—"), str(r.id), f"{r.score:.0%}",
                      escape((r.overview or "").replace("\n", " ")), style=style)
        self.console.print(t)
        if len(results) > SHOWN:
            self.console.print(f"[dim]…{len(results) - SHOWN} more; narrow with s <title> <year>[/]")

    def _info(self, req: MatchRequest, results: list[SearchResult], n: int) -> None:
        if not 1 <= n <= min(len(results), SHOWN):
            self.console.print(f"[red]No row {n}.[/]")
            return
        r = results[n - 1]
        self.console.print(f"[bold]{escape(r.title)}[/] ({r.year or '—'}) · {req.provider} {r.id}")
        self.console.print(escape(r.overview or "(no overview)"), soft_wrap=True)
        self.console.print(f"[link={req.url(r.id)}]{req.url(r.id)}[/link]")

    def _help(self, req: MatchRequest) -> None:
        self.console.print(
            "  [bold]1-8[/]                pick that row\n"
            "  [bold]s <title> \\[year][/]   search again, e.g. [cyan]s The Office 2005[/]\n"
            f"  [bold]id <ID>[/]            look up an exact {req.provider} ID and confirm it\n"
            "  [bold]<URL> or tt…[/]       paste a TMDB / TVDB / IMDb link or IMDb id the same way\n"
            + ("" if req.kind == "movie" else "  [bold]id tmdb:<ID>[/]       a TMDB TV id is converted to its TVDB id\n")
            +
            "  [bold]i <#>[/]              show the full overview and a link for a row\n"
            "  [bold]Enter[/]              accept the default shown in \\[brackets], or skip if none\n"
            "  [bold]q[/]                  skip this, and don't ask again this run"
        )

    def report(self) -> None:
        if not self.skipped:
            return
        self.console.rule(f"[yellow]{len(self.skipped)} item(s) not matched[/]")
        for s in self.skipped:
            r = s.request
            self.console.print(f"[bold]{escape(str(r.location))}[/]  [dim]({s.reason}; guessed "
                               f"\"{escape(r.guess_title)}\" {r.guess_year or ''})[/]", soft_wrap=True)
            for c in s.candidates:
                self.console.print(f"    {c.score:>4.0%}  {escape(c.title)} ({c.year or '—'})  "
                                   f"{r.provider} {c.id}")
            example = s.candidates[0].id if s.candidates else "<ID>"
            self.console.print(f"    fix: [cyan]mm run {shlex.quote(str(r.location))} --id {example}[/]"
                               f"  [dim](--id also takes a {r.provider} or IMDb URL; or run without --auto "
                               "to choose interactively)[/]",
                               soft_wrap=True, highlight=False)
