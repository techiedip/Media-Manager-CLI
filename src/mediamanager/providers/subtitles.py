"""Subtitle providers: OpenSubtitles.com (needs API key), YIFY (movies), Addic7ed (TV)."""
from __future__ import annotations

import base64
import io
import logging
import re
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ..models import EpisodeMeta, MovieMeta, ShowMeta
from ..naming import normalize_for_match
from .http import Http, ProviderError

log = logging.getLogger(__name__)
LANG_NAMES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian",
    "pt": "Portuguese", "nl": "Dutch", "sv": "Swedish", "da": "Danish", "no": "Norwegian",
    "fi": "Finnish", "pl": "Polish", "ru": "Russian", "ja": "Japanese", "ko": "Korean",
    "zh": "Chinese", "hi": "Hindi", "ar": "Arabic", "tr": "Turkish", "cs": "Czech",
    "hu": "Hungarian", "el": "Greek", "he": "Hebrew", "ro": "Romanian", "bn": "Bengali",
}
TEXT_SUB_EXTS = (".srt", ".ass", ".ssa", ".vtt", ".sub")


@dataclass
class SubCandidate:
    provider: str
    language: str
    ref: str
    release: str = ""
    score: float = 0.0
    hearing_impaired: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class SubFile:
    content: bytes  # UTF-8
    ext: str        # ".srt"


def release_score(video: Path, release: str) -> float:
    a = set(normalize_for_match(video.stem).split())
    b = set(normalize_for_match(release).split())
    return len(a & b) / max(len(b), 1)


def to_utf8(raw: bytes) -> bytes:
    encodings = ["utf-16"] if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else []
    for enc in [*encodings, "utf-8-sig", "cp1252", "latin-1"]:
        try:
            return raw.decode(enc).encode("utf-8")
        except UnicodeDecodeError:
            continue
    return raw


def from_zip(raw: bytes) -> SubFile | None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        return None
    names = [n for n in zf.namelist() if n.lower().endswith(TEXT_SUB_EXTS) and not n.startswith("__MACOSX")]
    if not names:
        return None
    names.sort(key=lambda n: (not n.lower().endswith(".srt"), -zf.getinfo(n).file_size))
    return SubFile(to_utf8(zf.read(names[0])), Path(names[0]).suffix.lower())


def opensubtitles_hash(path: Path) -> str | None:
    """The 64-bit OpenSubtitles hash: size + checksum of first/last 64 KiB."""
    try:
        size = path.stat().st_size
        if size < 131072:
            return None
        h = size
        with path.open("rb") as f:
            for offset in (0, size - 65536):
                f.seek(offset)
                buf = f.read(65536)
                for (q,) in struct.iter_unpack("<Q", buf):
                    h = (h + q) & 0xFFFFFFFFFFFFFFFF
        return f"{h:016x}"
    except OSError:
        return None


class SubtitleProvider:
    name = "base"

    def search_movie(self, meta: MovieMeta, video: Path, lang: str) -> list[SubCandidate]:
        return []

    def search_episode(self, show: ShowMeta, ep: EpisodeMeta, video: Path, lang: str) -> list[SubCandidate]:
        return []

    def download(self, cand: SubCandidate) -> SubFile | None:
        raise NotImplementedError


# --------------------------------------------------------------------------- OpenSubtitles
class OpenSubtitles(SubtitleProvider):
    name = "opensubtitles"
    API = "https://api.opensubtitles.com/api/v1"

    def __init__(self, http: Http, api_key: str, username: str = "", password: str = ""):
        if not api_key:
            raise ProviderError("OpenSubtitles.com API (consumer) key missing")
        self.http, self.key, self.user, self.password = http, api_key, username, password
        self.base = self.API
        self.token: str | None = None

    def _headers(self) -> dict:
        h = {"Api-Key": self.key, "User-Agent": "MediaManager v0.1", "Accept": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _login(self) -> None:
        if self.token or not (self.user and self.password):
            return
        r = self.http.request("POST", f"{self.API}/login", headers=self._headers(),
                              json={"username": self.user, "password": self.password})
        if r.status_code != 200:
            raise ProviderError(f"OpenSubtitles login failed (HTTP {r.status_code})")
        j = r.json()
        self.token = j.get("token")
        if j.get("base_url"):
            self.base = f"https://{j['base_url']}/api/v1"

    def _search(self, params: dict, video: Path, lang: str) -> list[SubCandidate]:
        params = {**params, "languages": lang, "order_by": "download_count"}
        mh = opensubtitles_hash(video)
        if mh:
            params["moviehash"] = mh
        data = self.http.get_json(f"{self.API}/subtitles", params=params, headers=self._headers()) or {}
        out = []
        for item in data.get("data", []):
            a = item.get("attributes", {})
            files = a.get("files") or []
            if not files:
                continue
            rel = a.get("release", "")
            s = release_score(video, rel) + (1.0 if a.get("moviehash_match") else 0)
            s += min(a.get("download_count", 0), 50000) / 100000
            out.append(SubCandidate(self.name, lang, str(files[0]["file_id"]), rel, s,
                                    bool(a.get("hearing_impaired"))))
        return out

    def search_movie(self, meta, video, lang):
        return self._search({"tmdb_id": meta.tmdb_id, "type": "movie"}, video, lang)

    def search_episode(self, show, ep, video, lang):
        p = {"season_number": ep.season, "episode_number": ep.episode, "type": "episode"}
        p.update({"parent_tmdb_id": show.tmdb_id} if show.tmdb_id else {"parent_imdb_id": show.imdb_id.lstrip("t")})
        return self._search(p, video, lang)

    def download(self, cand):
        self._login()
        r = self.http.request("POST", f"{self.base}/download", headers=self._headers(),
                              json={"file_id": int(cand.ref), "sub_format": "srt"})
        if r.status_code != 200:
            raise ProviderError(f"OpenSubtitles download refused (HTTP {r.status_code}): {r.text[:120]}")
        link = r.json().get("link")
        return SubFile(to_utf8(self.http.fetch_bytes(link)), ".srt") if link else None


# --------------------------------------------------------------------------- YIFY (movies)
class Yify(SubtitleProvider):
    name = "yifysubtitles"
    BASE = "https://yts-subs.com"

    def __init__(self, http: Http):
        self.http = http

    def search_movie(self, meta, video, lang):
        if not meta.imdb_id:
            return []
        r = self.http.request("GET", f"{self.BASE}/movie-imdb/{meta.imdb_id}")
        if r.status_code != 200:
            return []
        lname = LANG_NAMES.get(lang, lang)
        out = []
        for row in re.findall(r"<tr[^>]*data-id=\"\d+\"[^>]*>(.*?)</tr>", r.text, re.S):
            m_lang = re.search(r'class="sub-lang">([^<]+)<', row)
            if not m_lang or m_lang.group(1).strip().lower() != lname.lower():
                continue
            href = re.search(r'href="(/subtitles/[^"]+)"', row)
            rating = re.search(r'class="label[^"]*">(-?\d+)<', row)
            title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", row))
            if href:
                rel = title.split("subtitle", 1)[-1]
                out.append(SubCandidate(self.name, lang, href.group(1), rel.strip(),
                                        release_score(video, rel) + int(rating.group(1) if rating else 0) / 10))
        return out

    def download(self, cand):
        r = self.http.request("GET", f"{self.BASE}{cand.ref}")
        m = re.search(r'data-link="([^"]+)"', r.text)
        if not m:
            return None
        url = base64.b64decode(m.group(1)).decode()
        return from_zip(self.http.fetch_bytes(url, headers={"Referer": f"{self.BASE}{cand.ref}"}))


# --------------------------------------------------------------------------- Addic7ed (TV)
class Addic7ed(SubtitleProvider):
    name = "addic7ed"
    BASE = "https://www.addic7ed.com"
    UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) MediaManager"}

    def __init__(self, http: Http):
        self.http = http
        self._slugs: dict[int, str | None] = {}

    def _slug(self, show: ShowMeta) -> str | None:
        if show.tvdb_id in self._slugs:
            return self._slugs[show.tvdb_id]
        slug = None
        for q in ([f"{show.title} ({show.year})"] if show.year else []) + [show.title]:
            r = self.http.request("GET", f"{self.BASE}/search.php",
                                  params={"search": q, "Submit": "Search"}, headers=self.UA)
            slugs = re.findall(r'href="/?serie/([^/"]+)/\d+/\d+/', r.text)
            if slugs:
                want = normalize_for_match(show.title)
                slugs.sort(key=lambda s: (normalize_for_match(re.sub(r"_\(\d{4}\)$", "", s.replace("%28", "(").replace("%29", ")"))) != want,
                                          str(show.year or "") not in s))
                slug = slugs[0]
                break
        self._slugs[show.tvdb_id] = slug
        return slug

    def search_episode(self, show, ep, video, lang):
        slug = self._slug(show)
        if not slug:
            return []
        page = f"{self.BASE}/serie/{slug}/{ep.season}/{ep.episode}/x"
        r = self.http.request("GET", page, headers=self.UA)
        if r.status_code != 200:
            return []
        lname = LANG_NAMES.get(lang, lang).lower()
        out = []
        for block in r.text.split('class="NewsTitle"')[1:]:
            ver = re.search(r"Version\s+([^,<]+)", block)
            release = ver.group(1).strip() if ver else ""
            for m in re.finditer(r'class="language">([^<]+)<(.*?)(?=class="language"|$)', block, re.S):
                if m.group(1).strip().lower() != lname or "Completed" not in m.group(2):
                    continue
                links = re.findall(r'href="(/(?:updated|original)/[^"]+)"', m.group(2))
                if not links:
                    continue
                link = next((l for l in links if l.startswith("/updated/")), links[0])
                hi = 'title="Hearing Impaired"' in m.group(2)
                out.append(SubCandidate(self.name, lang, link, release,
                                        release_score(video, release) - (0.2 if hi else 0), hi,
                                        {"referer": page}))
        return out

    def download(self, cand):
        raw = self.http.fetch_bytes(f"{self.BASE}{cand.ref}",
                                    headers={**self.UA, "Referer": cand.extra.get("referer", self.BASE)})
        if raw.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
            raise ProviderError("Addic7ed returned a page instead of a subtitle (daily limit reached?)")
        return SubFile(to_utf8(raw), ".srt")
