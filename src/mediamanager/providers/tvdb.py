"""TheTVDB v4 API."""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..matching import rank, score
from ..models import Artwork, EpisodeMeta, Person, SearchResult, SeasonMeta, ShowMeta
from .http import Http, ProviderError

API = "https://api4.thetvdb.com/v4"
ART = "https://artworks.thetvdb.com"
LANG3 = {"en": "eng", "de": "deu", "fr": "fra", "es": "spa", "it": "ita", "pt": "por", "nl": "nld",
         "sv": "swe", "da": "dan", "no": "nor", "fi": "fin", "pl": "pol", "ru": "rus", "ja": "jpn",
         "ko": "kor", "zh": "zho", "hi": "hin", "ar": "ara", "tr": "tur", "cs": "ces", "hu": "hun"}
LANG2 = {v: k for k, v in LANG3.items()}
# artwork type ids (GET /artwork/types)
SERIES_ART = {1: "banner", 2: "poster", 3: "fanart", 22: "clearart", 23: "clearlogo"}
SEASON_ART = {6: "banner", 7: "poster", 8: "fanart"}


def _img(path: str | None) -> str:
    if not path:
        return ""
    return path if path.startswith("http") else f"{ART}{path}"


def _year(date: str | None) -> int | None:
    return int(date[:4]) if date and date[:4].isdigit() else None


class Tvdb:
    def __init__(self, http: Http, api_key: str, pin: str = "", language: str = "en",
                 cache_dir: Path | None = None):
        if not api_key:
            raise ProviderError("TVDB API key missing")
        self.http, self.key, self.pin = http, api_key, pin
        self.lang2 = language
        self.lang3 = LANG3.get(language, "eng")
        self._token_file = (cache_dir or http.cache_dir.parent) / "tvdb_token.json"
        self._token: str | None = None

    # ---------------------------------------------------------------- auth
    def _auth(self) -> dict:
        if not self._token and self._token_file.is_file():
            try:
                t = json.loads(self._token_file.read_text())
                if t.get("expires", 0) > time.time():
                    self._token = t["token"]
            except (json.JSONDecodeError, KeyError):
                pass
        if not self._token:
            body = {"apikey": self.key, **({"pin": self.pin} if self.pin else {})}
            r = self.http.request("POST", f"{API}/login", json=body)
            if r.status_code != 200:
                raise ProviderError(f"TVDB login failed (HTTP {r.status_code})")
            self._token = r.json()["data"]["token"]
            self._token_file.write_text(json.dumps({"token": self._token, "expires": time.time() + 25 * 86400}))
            self._token_file.chmod(0o600)
        return {"Authorization": f"Bearer {self._token}"}

    def _get(self, path: str, **params):
        try:
            data = self.http.get_json(f"{API}{path}", params=params or None, headers=self._auth())
        except ProviderError as e:
            if "HTTP 401" in str(e):  # stale token
                self._token = None
                self._token_file.unlink(missing_ok=True)
                data = self.http.get_json(f"{API}{path}", params=params or None, headers=self._auth())
            else:
                raise
        return (data or {}).get("data") if isinstance(data, dict) else None

    # ---------------------------------------------------------------- search
    def search_series(self, title: str, year: int | None) -> list[SearchResult]:
        seen: dict[int, SearchResult] = {}
        attempts = [{"year": year}] if year else []
        attempts.append({})
        for extra in attempts:
            for x in (self._get("/search", query=title, type="series", **extra) or [])[:10]:
                tid = int(x["tvdb_id"])
                if tid in seen:
                    continue
                name = (x.get("translations") or {}).get(self.lang3) or x.get("name", "")
                y = int(x["year"]) if str(x.get("year", "")).isdigit() else None
                s = score(title, year, name, y, [x.get("name", ""), *(x.get("aliases") or [])])
                ov = (x.get("overviews") or {}).get(self.lang3) or x.get("overview", "")
                seen[tid] = SearchResult(tid, name, y, ov, s)
            if seen and max(r.score for r in seen.values()) > 0.9:
                break
        return rank(list(seen.values()))

    # ---------------------------------------------------------------- exact lookups
    def _result(self, d: dict | None) -> SearchResult | None:
        if not d or not d.get("id"):
            return None
        y = int(d["year"]) if str(d.get("year", "")).isdigit() else _year(d.get("firstAired"))
        return SearchResult(int(d["id"]), d.get("name", ""), y, (d.get("overview") or "").strip(), 1.0)

    def lookup_series(self, tvdb_id: int) -> SearchResult | None:
        return self._result(self._get(f"/series/{tvdb_id}"))

    def lookup_slug(self, slug: str) -> SearchResult | None:
        return self._result(self._get(f"/series/slug/{slug}"))

    def find_imdb(self, imdb_id: str) -> int | None:
        for hit in self._get(f"/search/remoteid/{imdb_id}") or []:
            if hit.get("series"):
                return int(hit["series"]["id"])
        return None

    # ---------------------------------------------------------------- details
    def series(self, tvdb_id: int, seasons_needed: set[int] | None = None,
               episodes_needed: set[tuple[int, int]] | None = None) -> ShowMeta:
        d = self._get(f"/series/{tvdb_id}/extended", short="false")
        if not d:
            raise ProviderError(f"TVDB series {tvdb_id} not found")
        tr = self._get(f"/series/{tvdb_id}/translations/{self.lang3}") or {}
        status = (d.get("status") or {}).get("name", "")
        show = ShowMeta(
            tvdb_id=tvdb_id, title=tr.get("name") or d.get("name", ""), original_title=d.get("name", ""),
            year=_year(d.get("firstAired")) or (int(d["year"]) if str(d.get("year", "")).isdigit() else None),
            premiered=d.get("firstAired") or "", status=status,
            enddate=(d.get("lastAired") or "") if status == "Ended" else "",
            plot=tr.get("overview") or d.get("overview") or "",
            runtime=d.get("averageRuntime") or 0,
            genres=sorted(g["name"] for g in d.get("genres") or []),
        )
        net = d.get("originalNetwork") or d.get("latestNetwork")
        if net:
            show.studios.append(net["name"])
        if d.get("originalCountry"):
            show.countries.append(d["originalCountry"].upper())
        for cr in d.get("contentRatings") or []:
            if cr.get("country") == "usa":
                show.mpaa = cr["name"]
                break
        for rid in d.get("remoteIds") or []:
            src = (rid.get("sourceName") or "").lower()
            if src == "imdb":
                show.imdb_id = rid["id"]
            elif "themoviedb" in src and str(rid["id"]).isdigit():
                show.tmdb_id = int(rid["id"])
        actors = [c for c in d.get("characters") or [] if c.get("peopleType") == "Actor"]
        actors.sort(key=lambda c: (c.get("sort") or 999))
        show.actors = [Person(c.get("personName") or "", c.get("name") or "", i,
                              _img(c.get("image") or c.get("personImgURL")))
                       for i, c in enumerate(actors[:30])]

        for a in d.get("artworks") or []:
            kind = SERIES_ART.get(a.get("type"))
            if not kind:
                continue
            art = Artwork(_img(a["image"]), LANG2.get(a.get("language") or "", a.get("language")),
                          float(a.get("score") or 0), "tvdb")
            if kind == "poster" and a.get("includesText") is False:
                show.artwork.setdefault("keyart", []).append(art)
            show.artwork.setdefault(kind, []).append(art)

        official = [s for s in d.get("seasons") or [] if (s.get("type") or {}).get("type") == "official"]
        for s in official:
            n = s["number"]
            if seasons_needed is not None and n not in seasons_needed:
                continue
            show.seasons[n] = self._season(s["id"], n)

        self._episodes(show, episodes_needed)
        return show

    def _season(self, season_id: int, number: int) -> SeasonMeta:
        d = self._get(f"/seasons/{season_id}/extended") or {}
        tr = {}
        # TVDB sometimes returns ["pt,por,eng"] instead of ["pt", "por", "eng"]
        avail = {x.strip() for v in (d.get("overviewTranslations") or []) + (d.get("nameTranslations") or [])
                 for x in str(v).split(",")}
        if self.lang3 in avail:
            tr = self._get(f"/seasons/{season_id}/translations/{self.lang3}") or {}
        sm = SeasonMeta(number=number, tvdb_id=season_id,
                        title=tr.get("name") or ("Specials" if number == 0 else f"Season {number}"),
                        plot=tr.get("overview") or "",
                        year=int(d["year"]) if str(d.get("year", "")).isdigit() else None)
        eps = sorted((e.get("aired") or "") for e in d.get("episodes") or [] if e.get("aired"))
        sm.premiered = eps[0] if eps else ""
        for a in d.get("artwork") or []:
            kind = SEASON_ART.get(a.get("type"))
            if kind:
                sm.artwork.setdefault(kind, []).append(Artwork(
                    _img(a["image"]), LANG2.get(a.get("language") or "", a.get("language")),
                    float(a.get("score") or 0), "tvdb", number))
        return sm

    def _episodes(self, show: ShowMeta, needed: set[tuple[int, int]] | None) -> None:
        page = 0
        while page < 50:
            data = self.http.get_json(
                f"{API}/series/{show.tvdb_id}/episodes/default/{self.lang3}",
                params={"page": page}, headers=self._auth(),
            ) or {}
            for e in (data.get("data") or {}).get("episodes") or []:
                key = (e.get("seasonNumber") or 0, e.get("number") or 0)
                show.episodes[key] = EpisodeMeta(
                    season=key[0], episode=key[1], title=e.get("name") or "", tvdb_id=e.get("id"),
                    plot=e.get("overview") or "", aired=e.get("aired") or "",
                    runtime=e.get("runtime") or 0, thumb=_img(e.get("image")),
                )
            if not (data.get("links") or {}).get("next"):
                break
            page += 1
        for key in needed or set():
            ep = show.episodes.get(key)
            if not ep or not ep.tvdb_id:
                continue
            x = self._get(f"/episodes/{ep.tvdb_id}/extended") or {}
            for c in x.get("characters") or []:
                pt, name = c.get("peopleType"), c.get("personName") or ""
                if pt == "Director":
                    ep.directors.append(name)
                elif pt == "Writer":
                    ep.writers.append(name)
                elif pt == "Guest Star":
                    ep.guests.append(Person(name, c.get("name") or "", len(ep.guests),
                                            _img(c.get("image") or c.get("personImgURL"))))
