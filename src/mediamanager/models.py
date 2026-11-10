"""Plain data classes shared across the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------- scanned items
@dataclass
class MovieItem:
    videos: list[Path]               # one file, or several for multi-part (cd1/cd2)
    folder: Path | None              # the movie's own folder; None for a loose file
    guess_title: str
    guess_year: int | None = None
    tmdb_id: int | None = None       # from an existing NFO
    imdb_id: str | None = None

    @property
    def location(self) -> Path:
        return self.folder or self.videos[0]


@dataclass
class EpisodeFile:
    path: Path
    season: int
    episodes: list[int]


@dataclass
class ShowItem:
    folder: Path
    guess_title: str
    guess_year: int | None = None
    episodes: list[EpisodeFile] = field(default_factory=list)
    unmatched: list[Path] = field(default_factory=list)   # videos we could not number
    tvdb_id: int | None = None
    imdb_id: str | None = None

    @property
    def location(self) -> Path:
        return self.folder


# ---------------------------------------------------------------- metadata
@dataclass
class Artwork:
    url: str
    language: str | None = None      # ISO 639-1, None = no text / unknown
    score: float = 0.0
    source: str = ""
    season: int | None = None

    @property
    def ext(self) -> str:
        tail = self.url.rsplit("?", 1)[0].rsplit(".", 1)
        ext = tail[-1].lower() if len(tail) == 2 else "jpg"
        return "jpg" if ext == "jpeg" else ext


@dataclass
class Person:
    name: str
    role: str = ""
    order: int = 0
    thumb: str = ""
    tmdb_id: int | None = None


@dataclass
class MovieSet:
    tmdb_id: int | None
    name: str
    overview: str = ""
    artwork: dict[str, list[Artwork]] = field(default_factory=dict)


@dataclass
class MovieMeta:
    tmdb_id: int
    title: str
    imdb_id: str = ""
    original_title: str = ""
    year: int | None = None
    premiered: str = ""
    plot: str = ""
    tagline: str = ""
    runtime: int = 0
    mpaa: str = ""
    rating: float = 0.0
    votes: int = 0
    genres: list[str] = field(default_factory=list)
    studios: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    directors: list[Person] = field(default_factory=list)
    writers: list[Person] = field(default_factory=list)
    actors: list[Person] = field(default_factory=list)
    movie_set: MovieSet | None = None
    trailer: str = ""
    artwork: dict[str, list[Artwork]] = field(default_factory=dict)


@dataclass
class EpisodeMeta:
    season: int
    episode: int
    title: str = ""
    tvdb_id: int | None = None
    plot: str = ""
    aired: str = ""
    runtime: int = 0
    thumb: str = ""
    directors: list[str] = field(default_factory=list)
    writers: list[str] = field(default_factory=list)
    guests: list[Person] = field(default_factory=list)


@dataclass
class SeasonMeta:
    number: int
    tvdb_id: int | None = None
    title: str = ""
    plot: str = ""
    year: int | None = None
    premiered: str = ""
    artwork: dict[str, list[Artwork]] = field(default_factory=dict)


@dataclass
class ShowMeta:
    tvdb_id: int
    title: str
    original_title: str = ""
    imdb_id: str = ""
    tmdb_id: int | None = None
    year: int | None = None
    premiered: str = ""
    enddate: str = ""
    status: str = ""
    plot: str = ""
    mpaa: str = ""
    rating: float = 0.0
    runtime: int = 0
    genres: list[str] = field(default_factory=list)
    studios: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    actors: list[Person] = field(default_factory=list)
    artwork: dict[str, list[Artwork]] = field(default_factory=dict)
    seasons: dict[int, SeasonMeta] = field(default_factory=dict)
    episodes: dict[tuple[int, int], EpisodeMeta] = field(default_factory=dict)


@dataclass
class SearchResult:
    id: int
    title: str
    year: int | None
    overview: str = ""
    score: float = 0.0  # our match confidence, 0..1
