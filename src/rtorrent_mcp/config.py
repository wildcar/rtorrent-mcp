"""Runtime configuration."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ``scgi://host:port`` (TCP) or ``scgi:///path/to/socket`` (unix socket).
    # rtorrent's SCGI listens on 127.0.0.1:5000 by default.
    rtorrent_scgi_url: str = "scgi://127.0.0.1:5000"
    rtorrent_timeout_seconds: float = 30.0

    # Default destinations by media kind. The ``add_torrent`` tool resolves
    # a ``kind`` hint ("movie" / "series") to the matching directory; callers
    # can always override with an explicit ``download_dir``.
    rtorrent_download_dir_movies: str = "/mnt/storage/Media/Video/Movie/"
    rtorrent_download_dir_series: str = "/mnt/storage/Media/Video/Series/"

    mcp_auth_token: str | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
