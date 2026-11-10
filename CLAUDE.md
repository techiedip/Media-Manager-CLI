# MediaManager — CLAUDE.md

A lightweight, **command-line** Python clone of tinyMediaManager (TMM). The user points it at one or
more folders containing movies and/or TV shows; it identifies each item, scrapes metadata, downloads
artwork and subtitles, writes Kodi-style NFOs, and renames/organizes everything **in place** using
the same layout TMM produces. No GUI. Personal use only.

Full feature list: [Features.txt](Features.txt).

## Layout

```
Media Manager/                 ← outer folder (not in git)
├── Samples/                   ← macOS Finder *aliases* to reference folders (see "Reference samples")
└── media-manager/             ← the git repo (this file lives here)
    ├── .venv/                 ← project venv, Python 3.12 (Homebrew). System python3 is 3.9 — don't use it.
    ├── pyproject.toml         ← console script `mm`
    ├── .env.example           ← copy to .env; .env overrides api-keys/*.txt
    ├── Features.txt
    ├── src/mediamanager/
    │   ├── cli.py             typer app: scan | run | scrape | rename | subtitles | undo | providers
    │   ├── config.py          pydantic-settings; .env first, then api-keys/Keys.txt + Credentials.txt
    │   ├── models.py          scanned items (MovieItem/ShowItem/EpisodeFile) + metadata dataclasses
    │   ├── parsing.py         filename → title/year/SxxEyy (guessit + regexes), video/sub extensions
    │   ├── scanner.py         classify a path as movie folder / show folder / library root
    │   ├── naming.py          TMM naming templates + character cleanup
    │   ├── matching.py        search-result scoring (title similarity + year)
    │   ├── chooser.py         confident-or-ask match selection, prompt UI, skipped report
    │   ├── idlookup.py        exact match from an ID / IMDb id / TMDB-TVDB-IMDb URL
    │   ├── pipeline.py        item → Plan (identify, scrape, rename, NFO, artwork, subtitles)
    │   ├── planner.py         Plan ops w/ virtual FS, apply(), Journal (undo log), undo()
    │   ├── artwork.py         merge + rank artwork across providers
    │   ├── nfo.py             movie/tvshow/season/episode NFO writers
    │   ├── mediainfo.py       ffprobe → streamdetails
    │   └── providers/         http.py (cache/retry), tmdb.py, tvdb.py, fanarttv.py, subtitles.py
    ├── tests/                 offline tests (no network): test_core, test_chooser, test_idlookup
    └── api-keys/              ← SECRETS. Never print, log, commit or copy values out of here.
```

## Environment & commands

- Venv: `.venv` in the repo (not committed; only this project's deps). Its console-script
  wrappers use `#!/bin/sh` because the path contains a space — that's normal.
- Install/refresh: `/opt/homebrew/bin/python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"`
- Tests: `.venv/bin/python -m pytest -q`
- CLI: `.venv/bin/mm --help`. Every command is a **dry run unless `--apply`**.
- `ffprobe`/`ffmpeg` are available (Homebrew); `mediainfo` is not.
- Development fixtures: build tiny real videos in the scratchpad with
  `ffmpeg -f lavfi -i testsrc=size=640x360:rate=24 -f lavfi -i sine -t 1 -c:v libx264 -c:a aac -shortest tiny.mkv`
  and copy them into a fake library. Never `--apply` against `/Volumes/Media` while developing
  (read-only dry runs there are fine and are a good regression check: the samples should produce
  no video/folder renames).

## Behaviour decisions (confirmed with the user)

1. **Rename in place.** Items are reorganized inside the input folder they were found in. A loose
   movie file (no own folder) gets a new `Title (Year)/` folder created next to it.
2. **Dry-run by default.** Every command prints the planned changes (rich table); `--apply`
   commits them. Never overwrite an existing different file without `--force`.
3. **Providers:** TMDB (movies, and secondary IDs for TV), TVDB v4 (TV shows/seasons/episodes),
   Fanart.tv (clearlogo, clearart, discart, banner, keyart, characterart, landscape),
   OpenSubtitles.com for subtitles. If a provider's credentials are missing or fail, skip it with a
   warning and fall back to keyless sources (TMDB/TVDB artwork only; Podnapisi for subtitles).
   - The Fanart.tv API needs a *personal API key* (fanart.tv profile page); username/password
     can't be used. OpenSubtitles.com needs an *API consumer key* plus username/password.
     Set `FANARTTV_API_KEY` / `OPENSUBTITLES_API_KEY` in `.env` (or add `API Key: …` lines to the
     matching section of Credentials.txt). Until then artwork comes from TMDB/TVDB only.
   - Network findings (2026-11-07, user's connection): `api.opensubtitles.com` resets TLS and
     `podnapisi.net` doesn't resolve (likely ISP blocking), so the keyless fallbacks are
     **YIFY subtitles** (yts-subs.com, movies, by IMDb id) and **Addic7ed** (TV, scraped, has a
     daily download limit). Both are HTML scrapers — if they break, check the page markup first.
4. **Artwork names match the sample exactly** (both plain and title-prefixed files for movies).
5. **Subtitles:** 2-letter ISO 639-1 suffix, e.g. `.en.srt`, for movies *and* TV. Languages are
   configurable; default `en` only.
6. **Match selection** lives in `chooser.py` (`Chooser`, `is_confident`, `auto_pick`). Modes:
   `ask` (default: prompt only when ambiguous), `confirm` (`--confirm`, prompt always), `auto`
   (`--auto`, or stdin isn't a TTY: never prompt; skipped items are listed by `Chooser.report()`
   with a `mm run <path> --tmdb-id/--tvdb-id <ID>` fix line). Confident = with a year, ≥0.92 and
   ≥0.05 ahead; without a year, an exact title match with no *namesake* (another result with the
   same base title, e.g. remakes or "The Office (US)"). Prompt commands: `#`, `s <title> [year]`,
   `id <ID>`, `i <#>`, Enter (default), `q`, `?`. Rich markup: escape literal brackets as `\\[`.
   An existing NFO id bypasses searching.
8. **Exact-ID matching** lives in `idlookup.py`: `parse_ref` reads bare ids, `tmdb:`/`tvdb:`/
   `imdb:` prefixes, `tt…`, and TMDB/TVDB/IMDb URLs (TVDB website URLs are *slugs*, even numeric
   ones like `/series/1923`); `resolve_movie`/`resolve_show` turn them into one `SearchResult`
   (TV converts TMDB TV ids and IMDb ids to TVDB). Used by the prompt (`id <ref>` or a pasted
   ref → show it → `Use this? [Y/n]`) and by `--id/--tmdb-id/--tvdb-id/--imdb-id`, which become
   `Options.match_ref` and are only allowed when the paths resolve to a single item. `mm scan`
   takes the same flags; it is offline unless an ID is given, and then only looks it up
   (`cli._resolve_ref`) to show an "ID match" column.
7. `--type movie` skips any folder with season subfolders (at any level); `--type tv` treats
   single-video folders as shows and ignores loose files in library roots (`scanner._resolve`).

## Naming conventions (derived from the reference samples)

### Movies — `<Movies root>/<Title> (<Year>)/`

```
A Million Ways to Die in the West (2014)/
  A Million Ways to Die in the West (2014).mp4        ← video renamed to folder name
  A Million Ways to Die in the West (2014).en.srt
  movie.nfo
  poster.jpg                                          ← plain copies
  fanart.jpg
  A Million Ways to Die in the West (2014)-poster.jpg ← prefixed set
  A Million Ways to Die in the West (2014)-fanart.jpg
  A Million Ways to Die in the West (2014)-banner.jpg
  A Million Ways to Die in the West (2014)-clearart.png
  A Million Ways to Die in the West (2014)-clearlogo.png
  A Million Ways to Die in the West (2014)-discart.png
  A Million Ways to Die in the West (2014)-keyart.jpg
  A Million Ways to Die in the West (2014)-landscape.jpg
```
Optional: `extrafanart/fanart1.jpg…` (`MM_EXTRAFANART=n`). Multi-part files become `<base>-cd1`, `-cd2`.
Movie sets: `<set>` in movie.nfo from the TMDB collection (or `--set NAME`); set artwork goes to
`$MM_MOVIESET_DIR/<Set Name>/poster.jpg…` when that setting is configured.

### TV — `<TV root>/<Show> (<Year>)/Season <N>/`

```
Arcane (2021)/
  tvshow.nfo
  poster.jpg  fanart.jpg  banner.jpg  thumb.jpg  keyart.jpg
  clearart.png  clearlogo.png  characterart.png
  season01-poster.jpg  season01-banner.jpg  season01-thumb.jpg   ← season art in show root
  Season 1/
    season.nfo
    Arcane - S01E01 - Welcome to the Playground.mkv
    Arcane - S01E01 - Welcome to the Playground.nfo
    Arcane - S01E01 - Welcome to the Playground-thumb.jpg
    Arcane - S01E01 - Welcome to the Playground.en.srt
```
- Season folder: `Season <N>` (no zero padding). Season art prefix: `seasonNN` (2-digit).
- Specials: season 0 → folder `Specials`, art `season-specials-poster.jpg` (TMM convention).
- Multi-episode file: `Show - S01E01E02 - Title1 & Title2`.
- `theme.mp3` exists in the sample but has no free source — out of scope.

### Character cleanup
- Replace `:` with ` -` (e.g. `Alien: Covenant` → `Alien - Covenant`); strip `\ / * ? " < > |`.
- Keep apostrophes, `!`, `&`, and Unicode (`Alien³`, accented letters). ASCII transliteration is an
  opt-in setting. Collapse repeated spaces; trim trailing dots/spaces.
- Year in folder name comes from release date; omit `( )` if unknown.

## NFO formats (Kodi-compatible; mirror the samples)

- **movie.nfo** `<movie>`: title, originaltitle, sorttitle (leading article dropped, lowercase),
  `<ratings>` (imdb default, themoviedb, rottentomatoes if available) + `<rating>`, plot, outline,
  tagline, runtime, `<thumb aspect="poster">`, `<fanart><thumb>`, mpaa, `<id>` = tmdb id,
  `<uniqueid type="tmdb" default="true">`, `<uniqueid type="imdb">`, genre*, country, `<set><name><overview>`,
  tag*, director, credits, premiered, year, studio, `<fileinfo><streamdetails>` (ffprobe),
  `<actor>` (name, role, order, thumb).
- **tvshow.nfo** `<tvshow>`: title, rating, plot, mpaa, `<id>` = tvdb id, uniqueids
  (tvdb default, imdb, tmdb), genre*, status, premiered, enddate, studio, actor*.
- **season.nfo** `<season>`: plot, outline, title (`Season N`), year, sorttitle, tvdbid, premiered,
  releasedate, `<uniqueid type="tvdb">`, seasonnumber.
- **episode .nfo** `<episodedetails>`: title, season, episode, aired, plot,
  `<uniqueid type="tvdb" default="true">`, thumb, watched, `<fileinfo><streamdetails>` (video codec,
  width/height, aspect, framerate, duration; audio; subtitle tracks).
- Write UTF-8 with `<?xml version="1.0" encoding="utf-8" standalone="yes"?>`. When an NFO already
  exists, preserve user fields (playcount, lastplayed, watched, userrating, dateadded).

## Reference samples

`../Samples/sample-movie/…` and `../Samples/sample-tv/…` (outside the repo) are Finder aliases (not symlinks) resolving to:
- `/Volumes/Media/Movies/A Million Ways to Die in the West (2014)/`
- `/Volumes/Media/TV Shows/Arcane (2021)/`

These are the user's real library — **read only**. Resolve aliases with
`osascript -e 'tell application "Finder" to get POSIX path of (original item of (POSIX file "<abs path>" as alias) as alias)'`.
The volume may be unmounted; code and tests must not depend on it.

## Secrets & safety

- Load keys from `.env` first; fall back to parsing `api-keys/Keys.txt` /
  `Credentials.txt` (`Label: value` lines). Never echo secret values in output, logs or exceptions
  (mask them). Keep `.env` and `api-keys/` in `.gitignore`.
- Respect provider rate limits (TMDB ~40 req/10s; OpenSubtitles daily download quota). Cache API
  responses on disk (`~/.cache/mediamanager/`) to avoid repeated calls.
- All filesystem changes go through `planner.Plan` so dry-run and apply share code. `--apply`
  writes an undo journal to `~/.cache/mediamanager/undo/<timestamp>.json` (overwritten files are
  backed up beside it); `mm undo` reverts the latest run. API responses are cached in
  `~/.cache/mediamanager/http/` for 72h; the TVDB token in `tvdb_token.json`.
- Multi-episode NFOs deliberately contain several `<episodedetails>` roots (Kodi format), so they
  are not single-root XML.
