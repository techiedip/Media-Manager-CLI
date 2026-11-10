"""Shared HTTP client: retries, polite rate limiting, and an on-disk JSON cache."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)
USER_AGENT = "MediaManager/0.1 (personal tinyMediaManager-style CLI)"
SECRET_PARAMS = {"api_key", "apikey", "pin", "password", "token"}


class ProviderError(RuntimeError):
    pass


class Http:
    def __init__(self, cache_dir: Path, ttl_hours: float = 72, min_interval: float = 0.0):
        self.cache_dir = cache_dir / "http"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_hours * 3600
        self.min_interval = min_interval
        self._last = 0.0
        self.client = httpx.Client(
            timeout=httpx.Timeout(30.0, connect=15.0),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )

    # -- cache ---------------------------------------------------------------
    def _key(self, url: str, params: dict | None, extra: str = "") -> Path:
        clean = {k: v for k, v in sorted((params or {}).items()) if k.lower() not in SECRET_PARAMS}
        h = hashlib.sha1(f"{url}|{json.dumps(clean, default=str)}|{extra}".encode()).hexdigest()
        return self.cache_dir / h[:2] / f"{h}.json"

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    # -- requests ------------------------------------------------------------
    def request(self, method: str, url: str, *, retries: int = 3, **kw: Any) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            self._throttle()
            try:
                r = self.client.request(method, url, **kw)
            except httpx.TransportError as e:
                last_exc = e
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                delay = float(r.headers.get("Retry-After", 2 * (attempt + 1)) or 2)
                time.sleep(min(delay, 30))
                last_exc = ProviderError(f"HTTP {r.status_code} for {r.request.url.host}")
                continue
            return r
        raise ProviderError(f"request failed: {_redact(url)} ({last_exc})")

    def get_json(
        self, url: str, params: dict | None = None, headers: dict | None = None,
        cache: bool = True, cache_extra: str = "",
    ) -> Any:
        path = self._key(url, params, cache_extra)
        if cache and path.is_file() and time.time() - path.stat().st_mtime < self.ttl:
            try:
                return json.loads(path.read_text("utf-8"))
            except json.JSONDecodeError:
                pass
        r = self.request("GET", url, params=params, headers=headers)
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise ProviderError(f"HTTP {r.status_code} from {r.request.url.host}: {r.text[:200]}")
        data = r.json()
        if cache:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data), "utf-8")
        return data

    def download(self, url: str, dest: Path, headers: dict | None = None) -> int:
        """Stream url to dest atomically. Returns bytes written."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        self._throttle()
        with self.client.stream("GET", url, headers=headers) as r:
            if r.status_code >= 400:
                raise ProviderError(f"HTTP {r.status_code} downloading {_redact(url)}")
            n = 0
            with tmp.open("wb") as fh:
                for chunk in r.iter_bytes(65536):
                    fh.write(chunk)
                    n += len(chunk)
        if n == 0:
            tmp.unlink(missing_ok=True)
            raise ProviderError(f"empty download from {_redact(url)}")
        tmp.replace(dest)
        return n

    def fetch_bytes(self, url: str, headers: dict | None = None) -> bytes:
        r = self.request("GET", url, headers=headers)
        if r.status_code >= 400:
            raise ProviderError(f"HTTP {r.status_code} fetching {_redact(url)}")
        return r.content


def _redact(url: str) -> str:
    import re
    return re.sub(r"(api_key|apikey|token|password)=[^&]+", r"\1=***", url)
