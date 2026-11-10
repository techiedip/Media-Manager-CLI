# MediaManager

A small command-line take on tinyMediaManager. Point it at movie or TV folders (or whole library
roots). It identifies each item on TMDB/TVDB, writes Kodi NFOs, downloads artwork and subtitles,
and renames everything in place to the tinyMediaManager layout.

```bash
cd media-manager
.venv/bin/mm providers                       # which sources are configured
.venv/bin/mm scan "/Volumes/Media/Movies"    # what would be detected (offline)
.venv/bin/mm run  "/Volumes/Media/Movies"    # full dry run: shows every planned change
.venv/bin/mm run  "/Volumes/Media/Movies" --apply
.venv/bin/mm undo                            # revert the last --apply
```

Every command is a dry run until you add `--apply`.

| Command | Does |
|---|---|
| `run` | identify, NFO, artwork, subtitles, rename (flags: `--no-rename --no-artwork --no-nfo --no-subtitles`) |
| `scrape` | NFO + artwork, no renaming |
| `rename` | rename/organize + refresh NFOs, no downloads |
| `subtitles` | fetch missing subtitles only |
| `scan` | list detected items, no network (with `--id`: also look the ID up and show its title) |
| `undo` | revert the latest applied run |

Useful options: `--type movie|tv` (force the media type; `movie` skips folders with season
subfolders, `tv` ignores loose files), `--auto` (never prompt, skip unsure matches), `--confirm`
(ask about every match), `--id` / `--tmdb-id` / `--tvdb-id` / `--imdb-id` (exact match for a
single item, see below),
`--set "Name"` (custom movie set), `--force` (re-download artwork/subtitles that already exist), `-v`.

## Choosing the right match

Items with an existing NFO containing a TMDB/TVDB id are matched by that id. Otherwise the app
searches and decides:

- **Confident → no question.** With a year: a ≥92% match clearly ahead of the rest. Without a
  year: an exact title match that no other result shares.
- **Ambiguous → you choose.** Remakes (*The Lion King* 1994/2019), regional versions
  (*The Office* UK/US), weak or close scores. You get a table and a prompt:

  | Type | To |
  |---|---|
  | `2` | pick row 2 |
  | `s The Lion King 2019` | search again with another title and/or year |
  | `id 420818` | look up that exact TMDB/TVDB ID, show it, and confirm (`Use this? [Y/n]`) |
  | a pasted URL or `tt6105098` | same, from a TMDB, TVDB or IMDb link or an IMDb id |
  | `i 2` | full overview + link for row 2 |
  | Enter | accept the default in `[1]` (or skip if there's none) |
  | `q` | skip, and stop asking for the rest of this run |
  | `?` | help |

- **`--auto`, or not in a terminal:** ambiguous items are skipped and listed at the end with
  the top candidates and a ready-to-run fix, e.g. `mm run '/…/The Lion King' --id 420818`.

Putting the year in the folder name (`The Lion King (2019)`) avoids most questions.

## Matching by exact ID

If you already know the right title, skip the search entirely. On the command line (one item at
a time):

```bash
mm run "/Volumes/Media/Movies/The Lion King" --id https://www.themoviedb.org/movie/420818-the-lion-king
mm run "/Volumes/Media/Movies/The Lion King" --tmdb-id 420818
mm run "/Volumes/Media/TV Shows/The Office"  --id https://thetvdb.com/series/the-office-us
mm run "/Volumes/Media/TV Shows/The Office"  --imdb-id tt0386676
mm run "/Volumes/Media/TV Shows/Arcane"      --tmdb-id 94605     # TMDB TV id → converted to TVDB
```

`--id` takes any of: a bare ID (TMDB for movies, TVDB for TV), `tmdb:603`, `tvdb:371028`, an IMDb
id `tt…`, or a TMDB / TVDB / IMDb URL. `scrape`, `rename` and `subtitles` accept `--id` too.
To check what an ID points to before running, use `scan` with the same flags; it looks the ID up
and adds an "ID match" column (`mm scan "/…/The Lion King" --id 420818`), changing nothing.
A wrong-type ID (a TV id for a movie) or an ID that doesn't exist stops that item with a message.
At the prompt, the same inputs work after `id`, or pasted on their own.

## Configuration

Keys are read from `.env` (see `.env.example`), falling back to `api-keys/Keys.txt` and
`api-keys/Credentials.txt`.

- **TMDB** and **TVDB** are required (movies and TV respectively).
- **Fanart.tv** (banner, clearart, discart, characterart, season thumbs) needs `FANARTTV_API_KEY`,
  your personal key from the fanart.tv profile page.
- **OpenSubtitles.com** needs `OPENSUBTITLES_API_KEY` (a consumer key) plus your username/password.
  Without it, subtitles come from YIFY (movies) and Addic7ed (TV).
- `MM_SUBTITLE_LANGUAGES=en,es`, `MM_LANGUAGE=en-US`, `MM_ASCII_NAMES=true`, `MM_EXTRAFANART=3`,
  `MM_MOVIESET_DIR=/Volumes/Media/MovieSets`.
