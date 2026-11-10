"""TheMovieDB v3 API."""
from __future__ import annotations

from ..matching import rank, score
from ..models import Artwork, MovieMeta, MovieSet, Person, SearchResult
from .http import Http, ProviderError

API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/original"
PROFILE = "https://image.tmdb.org/t/p/h632"


def _year(date: str | None) -> int | None:
    return int(date[:4]) if date and len(date) >= 4 and date[:4].isdigit() else None


class Tmdb:
    def __init__(self, http: Http, api_key: str, language: str = "en-US"):
        if not api_key:
            raise ProviderError("TMDB API key missing")
        self.http, self.key, self.language = http, api_key, language
        self.lang2 = language.split("-")[0]
        self.region = language.split("-")[1] if "-" in language else "US"

    def _get(self, path: str, **params):
        params = {"api_key": self.key, "language": self.language, **params}
        return self.http.get_json(f"{API}{path}", params=params)

    # ---------------------------------------------------------------- movies
    def search_movie(self, title: str, year: int | None) -> list[SearchResult]:
        seen: dict[int, SearchResult] = {}
        attempts = [{"query": title, "year": year}] if year else []
        attempts.append({"query": title})
        for p in attempts:
            data = self._get("/search/movie", include_adult="false", **{k: v for k, v in p.items() if v})
            for m in (data or {}).get("results", [])[:10]:
                if m["id"] in seen:
                    continue
                y = _year(m.get("release_date"))
                s = score(title, year, m.get("title", ""), y, [m.get("original_title", "")])
                seen[m["id"]] = SearchResult(m["id"], m.get("title", ""), y, m.get("overview", ""), s)
            if seen and max(r.score for r in seen.values()) > 0.9:
                break
        return rank(list(seen.values()))

    def lookup_movie(self, tmdb_id: int) -> SearchResult | None:
        """Exact lookup by id; score 1.0 because the user asked for it."""
        d = self._get(f"/movie/{tmdb_id}")
        if not d or not d.get("id"):
            return None
        return SearchResult(d["id"], d.get("title", ""), _year(d.get("release_date")), d.get("overview", ""), 1.0)

    def find_imdb(self, imdb_id: str) -> int | None:
        data = self._get(f"/find/{imdb_id}", external_source="imdb_id")
        res = (data or {}).get("movie_results") or []
        return res[0]["id"] if res else None

    def movie(self, tmdb_id: int) -> MovieMeta:
        d = self._get(
            f"/movie/{tmdb_id}",
            append_to_response="credits,images,release_dates,external_ids,keywords,videos",
            include_image_language=f"{self.lang2},null",
        )
        if not d:
            raise ProviderError(f"TMDB movie {tmdb_id} not found")
        m = MovieMeta(
            tmdb_id=d["id"], title=d.get("title", ""), imdb_id=d.get("imdb_id") or "",
            original_title=d.get("original_title", ""), year=_year(d.get("release_date")),
            premiered=d.get("release_date", ""), plot=d.get("overview", ""),
            tagline=d.get("tagline", ""), runtime=d.get("runtime") or 0,
            rating=round(float(d.get("vote_average") or 0), 1), votes=d.get("vote_count") or 0,
            genres=[g["name"] for g in d.get("genres", [])],
            studios=[c["name"] for c in d.get("production_companies", [])],
            countries=[c["name"] for c in d.get("production_countries", [])],
            tags=[k["name"] for k in (d.get("keywords") or {}).get("keywords", [])],
        )
        m.mpaa = self._certification(d.get("release_dates", {}).get("results", []))
        credits = d.get("credits") or {}
        m.actors = [
            Person(c["name"], c.get("character", ""), c.get("order", i),
                   f"{PROFILE}{c['profile_path']}" if c.get("profile_path") else "", c.get("id"))
            for i, c in enumerate(credits.get("cast", [])[:30])
        ]
        for c in credits.get("crew", []):
            if c.get("job") == "Director":
                m.directors.append(Person(c["name"], tmdb_id=c.get("id")))
            elif c.get("department") == "Writing" and c["name"] not in {w.name for w in m.writers}:
                m.writers.append(Person(c["name"], tmdb_id=c.get("id")))
        for v in (d.get("videos") or {}).get("results", []):
            if v.get("site") == "YouTube" and v.get("type") == "Trailer":
                m.trailer = f"plugin://plugin.video.youtube/play/?video_id={v['key']}"
                break
        m.artwork = self._artwork(d.get("images") or {})
        col = d.get("belongs_to_collection")
        if col:
            m.movie_set = self.collection(col["id"], col.get("name", ""))
        return m

    def collection(self, cid: int, name: str = "") -> MovieSet:
        d = self._get(f"/collection/{cid}", append_to_response="images",
                      include_image_language=f"{self.lang2},null") or {}
        return MovieSet(cid, d.get("name") or name, d.get("overview", ""),
                        self._artwork(d.get("images") or {}))

    def _certification(self, results: list[dict]) -> str:
        by_country = {r["iso_3166_1"]: r.get("release_dates", []) for r in results}
        for country in (self.region, "US"):
            rels = sorted(by_country.get(country, []), key=lambda r: (r.get("type") != 3, r.get("type")))
            for r in rels:
                if r.get("certification"):
                    return r["certification"]
        return ""

    def _artwork(self, images: dict) -> dict[str, list[Artwork]]:
        def arts(items, kind_lang=True):
            out = []
            for i in items:
                lang = i.get("iso_639_1") or None
                out.append(Artwork(f"{IMG}{i['file_path']}", lang,
                                   float(i.get("vote_average", 0)) * 10 + i.get("vote_count", 0) / 100,
                                   "tmdb"))
            return out
        posters = arts(images.get("posters", []))
        backdrops = arts(images.get("backdrops", []))
        logos = [a for a in arts(images.get("logos", [])) if a.ext == "png"]
        return {
            "poster": [a for a in posters if a.language],
            "keyart": [a for a in posters if not a.language],
            "fanart": [a for a in backdrops if not a.language] or backdrops,
            "landscape": [a for a in backdrops if a.language],
            "clearlogo": logos,
        }

    # ---------------------------------------------------------------- TV helpers
    def tv_by_tvdb(self, tvdb_id: int) -> int | None:
        data = self._get(f"/find/{tvdb_id}", external_source="tvdb_id")
        res = (data or {}).get("tv_results") or []
        return res[0]["id"] if res else None

    def tv_to_tvdb(self, tmdb_tv_id: int) -> int | None:
        d = self._get(f"/tv/{tmdb_tv_id}/external_ids") or {}
        return d.get("tvdb_id") or None

    def find_imdb_tv(self, imdb_id: str) -> int | None:
        data = self._get(f"/find/{imdb_id}", external_source="imdb_id")
        res = (data or {}).get("tv_results") or []
        return res[0]["id"] if res else None

    def tv_images(self, tmdb_tv_id: int) -> dict[str, list[Artwork]]:
        d = self._get(f"/tv/{tmdb_tv_id}/images", include_image_language=f"{self.lang2},null") or {}
        a = self._artwork(d)
        return {"thumb": a["landscape"], "clearlogo": a["clearlogo"], "keyart": a["keyart"],
                "fanart": a["fanart"], "poster": a["poster"]}

    def tv_rating(self, tmdb_tv_id: int) -> tuple[float, int]:
        d = self._get(f"/tv/{tmdb_tv_id}") or {}
        return round(float(d.get("vote_average") or 0), 1), d.get("vote_count") or 0
