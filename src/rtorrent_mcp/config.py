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
    # a ``kind`` hint ("movie" / "series" / "cartoon") to the matching
    # directory; callers can always override with an explicit ``download_dir``.
    rtorrent_download_dir_movies: str = "/mnt/storage/Media/Video/Movie/"
    rtorrent_download_dir_series: str = "/mnt/storage/Media/Video/Series/"
    rtorrent_download_dir_cartoons: str = "/mnt/storage/Media/Video/Cartoon/"

    # Comma-separated allowlist for payload deletion / renames. Empty →
    # the three per-kind dirs above. A path is touched only when it lies
    # strictly *inside* one of these roots (never the root itself).
    rtorrent_delete_roots: str = ""

    mcp_auth_token: str | None = None

    def delete_roots(self) -> list[str]:
        raw = self.rtorrent_delete_roots or ",".join(
            (
                self.rtorrent_download_dir_movies,
                self.rtorrent_download_dir_series,
                self.rtorrent_download_dir_cartoons,
            )
        )
        return [r.strip() for r in raw.split(",") if r.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
