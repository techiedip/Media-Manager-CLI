"""Settings: .env / environment first, then media-manager/api-keys/*.txt as a fallback."""
from __future__ import annotations

import re
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_DIR = Path(__file__).resolve().parents[2]  # media-manager/
KEYS_DIR = PROJECT_DIR / "api-keys"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_DIR / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    tmdb_api_key: str = ""
    tvdb_api_key: str = ""
    tvdb_pin: str = ""
    fanarttv_api_key: str = ""
    fanarttv_username: str = ""  # not usable by the API; kept only to explain failures
    opensubtitles_api_key: str = ""
    opensubtitles_username: str = ""
    opensubtitles_password: str = ""

    mm_language: str = "en-US"
    mm_subtitle_languages: str = "en"
    mm_cache_dir: Path = Field(default=Path.home() / ".cache" / "mediamanager")
    mm_ascii_names: bool = False
    mm_extrafanart: int = 0  # number of extrafanart images (0 = off)
    mm_movieset_dir: str = ""  # folder for movie-set artwork; empty = disabled

    @property
    def subtitle_languages(self) -> list[str]:
        return [s.strip().lower() for s in self.mm_subtitle_languages.split(",") if s.strip()]

    @property
    def lang2(self) -> str:
        return self.mm_language.split("-")[0].lower()


def _parse_label_file(path: Path) -> dict[str, dict[str, str]]:
    """Parse 'Label: value' lines, grouped by the most recent section header line."""
    out: dict[str, dict[str, str]] = {}
    section = ""
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^([^:]+):\s*(.*)$", line)
        if m and m.group(2):
            out.setdefault(section, {})[m.group(1).strip().lower()] = m.group(2).strip()
        else:
            section = line.lower()
    return out


def load_settings() -> Settings:
    s = Settings()
    keys = _parse_label_file(KEYS_DIR / "Keys.txt")
    flat = {k: v for sec in keys.values() for k, v in sec.items()}
    if not s.tmdb_api_key:
        s.tmdb_api_key = flat.get("tmdb api key", "")
    if not s.tvdb_api_key:
        s.tvdb_api_key = flat.get("tvdb api key", "")
    if not s.fanarttv_api_key:
        s.fanarttv_api_key = flat.get("fanart.tv api key", "") or flat.get("fanarttv api key", "")
    if not s.opensubtitles_api_key:
        s.opensubtitles_api_key = flat.get("opensubtitles api key", "")

    creds = _parse_label_file(KEYS_DIR / "Credentials.txt")
    for section, vals in creds.items():
        if "opensubtitles" in section:
            s.opensubtitles_username = s.opensubtitles_username or vals.get("username", "")
            s.opensubtitles_password = s.opensubtitles_password or vals.get("password", "")
            s.opensubtitles_api_key = s.opensubtitles_api_key or vals.get("api key", "")
        elif "fanart" in section:
            s.fanarttv_username = vals.get("username", "")
            s.fanarttv_api_key = s.fanarttv_api_key or vals.get("api key", "")
    s.mm_cache_dir.mkdir(parents=True, exist_ok=True)
    return s


def mask(value: str) -> str:
    return (value[:3] + "…") if value else "(unset)"
