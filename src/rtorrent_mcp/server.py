"""MCP entrypoint for rtorrent-mcp."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Final

import structlog
from mcp.server.fastmcp import FastMCP

from . import __version__
from .config import Settings, get_settings
from .context import AppContext, build_app_context
from .models import (
    AckResponse,
    AddTorrentResponse,
    DownloadStatusResponse,
    ListDownloadsResponse,
    MediaKind,
)
from .tools import (
    add_torrent_impl,
    get_download_status_impl,
    list_downloads_impl,
    pause_impl,
    remove_impl,
    resume_impl,
    set_download_dir_impl,
)

_SUPPORTED_TRANSPORTS: Final[frozenset[str]] = frozenset({"stdio", "sse", "streamable-http"})


def _configure_logging() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


def build_server(ctx: AppContext) -> FastMCP:
    mcp = FastMCP(
        name="rtorrent-mcp",
        host=os.environ.get("MCP_HTTP_HOST", "127.0.0.1"),
        port=int(os.environ.get("MCP_HTTP_PORT", "8768")),
        instructions=(
            "Controls a local rtorrent instance over SCGI/XML-RPC. Use "
            "add_torrent to enqueue a .torrent (base64) or magnet URI; "
            "list_downloads / get_download_status / pause / resume / remove "
            "manage existing downloads."
        ),
    )

    async def add_torrent(
        torrent_file_base64: str | None = None,
        magnet: str | None = None,
        download_dir: str | None = None,
        kind: MediaKind | None = None,
        start: bool = True,
    ) -> AddTorrentResponse:
        """Enqueue a torrent. Provide either ``torrent_file_base64`` or ``magnet``."""
        return await add_torrent_impl(
            ctx,
            torrent_file_base64=torrent_file_base64,
            magnet=magnet,
            download_dir=download_dir,
            kind=kind,
            start=start,
        )

    async def list_downloads(active_only: bool = False) -> ListDownloadsResponse:
        """List all downloads, or only currently-active ones if ``active_only``."""
        return await list_downloads_impl(ctx, active_only=active_only)

    async def get_download_status(hash: str) -> DownloadStatusResponse:
        """Return the current state of a single download by its info-hash."""
        return await get_download_status_impl(ctx, hash=hash)

    async def pause(hash: str) -> AckResponse:
        """Pause (``d.pause``) the download identified by ``hash``."""
        return await pause_impl(ctx, hash=hash)

    async def resume(hash: str) -> AckResponse:
        """Resume (``d.resume``) the download identified by ``hash``."""
        return await resume_impl(ctx, hash=hash)

    async def set_download_dir(hash: str, directory: str) -> AckResponse:
        """Change the destination directory of an existing download."""
        return await set_download_dir_impl(ctx, hash=hash, directory=directory)

    async def remove(hash: str) -> AckResponse:
        """Erase the download from rtorrent's session. Files on disk are not touched."""
        return await remove_impl(ctx, hash=hash)

    mcp.tool()(add_torrent)
    mcp.tool()(list_downloads)
    mcp.tool()(get_download_status)
    mcp.tool()(pause)
    mcp.tool()(resume)
    mcp.tool()(set_download_dir)
    mcp.tool()(remove)
    return mcp


async def _run(settings: Settings, transport: str) -> None:
    async with build_app_context(settings) as ctx:
        server = build_server(ctx)
        structlog.get_logger().info(
            "rtorrent_mcp.starting",
            version=__version__,
            transport=transport,
            scgi_url=settings.rtorrent_scgi_url,
        )
        if transport == "stdio":
            await server.run_stdio_async()
        elif transport == "sse":
            await server.run_sse_async()
        else:
            await server.run_streamable_http_async()


def main() -> None:
    _configure_logging()
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    if transport not in _SUPPORTED_TRANSPORTS:
        raise SystemExit(
            f"Unsupported MCP_TRANSPORT={transport!r}; "
            f"expected one of {sorted(_SUPPORTED_TRANSPORTS)}"
        )
    asyncio.run(_run(get_settings(), transport))


if __name__ == "__main__":
    main()
