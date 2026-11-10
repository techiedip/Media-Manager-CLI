"""Fanart.tv v3 API (needs a personal API key)."""
from __future__ import annotations

from ..models import Artwork
from .http import Http, ProviderError

API = "https://webservice.fanart.tv/v3"

MOVIE_MAP = {
    "movieposter": "poster", "moviebackground": "fanart", "moviebanner": "banner",
    "hdmovielogo": "clearlogo", "movielogo": "clearlogo", "hdmovieclearart": "clearart",
    "movieart": "clearart", "moviedisc": "discart", "moviethumb": "landscape",
}
TV_MAP = {
    "tvposter": "poster", "showbackground": "fanart", "tvbanner": "banner",
    "hdtvlogo": "clearlogo", "clearlogo": "clearlogo", "hdclearart": "clearart",
    "clearart": "clearart", "characterart": "characterart", "tvthumb": "thumb",
}
SEASON_MAP = {"seasonposter": "poster", "seasonbanner": "banner", "seasonthumb": "thumb"}
HD = {"hdmovielogo", "hdmovieclearart", "hdtvlogo", "hdclearart"}


def _lang(v: str | None) -> str | None:
    return None if v in (None, "", "00") else v


class FanartTv:
    def __init__(self, http: Http, api_key: str):
        if not api_key:
            raise ProviderError("Fanart.tv API key missing")
        self.http, self.key = http, api_key

    def _get(self, path: str) -> dict:
        return self.http.get_json(f"{API}{path}", params={"api_key": self.key}) or {}

    def movie(self, tmdb_or_imdb: str | int) -> dict[str, list[Artwork]]:
        out: dict[str, list[Artwork]] = {}
        data = self._get(f"/movies/{tmdb_or_imdb}")
        for key, kind in MOVIE_MAP.items():
            for i in data.get(key, []):
                score = int(i.get("likes") or 0) + (1000 if key in HD else 0)
                a = Artwork(i["url"], _lang(i.get("lang")), score, "fanart.tv")
                if key == "movieposter" and a.language is None:
                    out.setdefault("keyart", []).append(a)
                out.setdefault(kind, []).append(a)
        return out

    def tv(self, tvdb_id: int) -> tuple[dict[str, list[Artwork]], dict[int, dict[str, list[Artwork]]]]:
        data = self._get(f"/tv/{tvdb_id}")
        show: dict[str, list[Artwork]] = {}
        seasons: dict[int, dict[str, list[Artwork]]] = {}
        for key, kind in TV_MAP.items():
            for i in data.get(key, []):
                score = int(i.get("likes") or 0) + (1000 if key in HD else 0)
                a = Artwork(i["url"], _lang(i.get("lang")), score, "fanart.tv")
                if key == "tvposter" and a.language is None:
                    show.setdefault("keyart", []).append(a)
                show.setdefault(kind, []).append(a)
        for key, kind in SEASON_MAP.items():
            for i in data.get(key, []):
                s = str(i.get("season", ""))
                if not s.isdigit():
                    continue
                a = Artwork(i["url"], _lang(i.get("lang")), int(i.get("likes") or 0), "fanart.tv", int(s))
                seasons.setdefault(int(s), {}).setdefault(kind, []).append(a)
        return show, seasons
