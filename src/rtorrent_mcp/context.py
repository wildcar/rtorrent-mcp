"""App context: holds the rtorrent client and resolved settings."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from .clients.rtorrent import RtorrentClient
from .config import Settings


@dataclass
class AppContext:
    settings: Settings
    rtorrent: RtorrentClient


@asynccontextmanager
async def build_app_context(settings: Settings) -> AsyncIterator[AppContext]:
    client = RtorrentClient(
        scgi_url=settings.rtorrent_scgi_url,
        timeout=settings.rtorrent_timeout_seconds,
    )
    yield AppContext(settings=settings, rtorrent=client)
