"""Tool implementations. Each returns a Pydantic response envelope;
exceptions become ``ToolError`` entries so callers can dispatch on
``error.code``.
"""

from __future__ import annotations

import base64

import structlog

from .clients.rtorrent import RtorrentError
from .context import AppContext
from .models import (
    AckResponse,
    AddTorrentResponse,
    Download,
    DownloadStatusResponse,
    ListDownloadsResponse,
    MediaKind,
    ToolError,
)

log = structlog.get_logger(__name__)


def _resolve_dir(
    ctx: AppContext, *, download_dir: str | None, kind: MediaKind | None
) -> str | None:
    """Explicit ``download_dir`` always wins; otherwise map ``kind`` to the
    configured per-kind default. Returning ``None`` means "let rtorrent pick",
    which for our setup means falling back to ``session.directory``."""
    if download_dir:
        return download_dir
    if kind == "movie":
        return ctx.settings.rtorrent_download_dir_movies
    if kind == "series":
        return ctx.settings.rtorrent_download_dir_series
    return None


def _err(code: str, message: str) -> ToolError:
    return ToolError.model_validate({"code": code, "message": message})


def _rtorrent_err_code(exc: RtorrentError) -> str:
    text = str(exc).lower()
    if "unreachable" in text or "timed out" in text or "refused" in text:
        return "rtorrent_unreachable"
    return "rtorrent_error"


async def add_torrent_impl(
    ctx: AppContext,
    *,
    torrent_file_base64: str | None = None,
    magnet: str | None = None,
    download_dir: str | None = None,
    kind: MediaKind | None = None,
    start: bool = True,
    comment: str | None = None,
) -> AddTorrentResponse:
    """Add a torrent by either raw bytes or magnet URI.

    Exactly one of ``torrent_file_base64`` / ``magnet`` must be set. We
    intentionally don't accept plain URLs — the bot fetches .torrent files
    from rutracker itself and passes us the bytes, so the extra round trip
    (and exposed cookie trust) lives there, not here.
    """
    provided = sum(1 for v in (torrent_file_base64, magnet) if v)
    if provided != 1:
        return AddTorrentResponse(
            error=_err(
                "invalid_argument",
                "exactly one of torrent_file_base64, magnet must be provided",
            )
        )

    directory = _resolve_dir(ctx, download_dir=download_dir, kind=kind)
    try:
        if torrent_file_base64 is not None:
            try:
                content = base64.b64decode(torrent_file_base64, validate=True)
            except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
                return AddTorrentResponse(
                    error=_err(
                        "invalid_argument", f"torrent_file_base64 is not valid base64: {exc}"
                    )
                )
            hash_ = await ctx.rtorrent.add_torrent_file(
                content, download_dir=directory, start=start, comment=comment
            )
        else:
            assert magnet is not None
            magnet_hash = await ctx.rtorrent.add_magnet(magnet, download_dir=directory, start=start, comment=comment)
            if magnet_hash is None:
                return AddTorrentResponse(
                    error=_err("invalid_argument", "magnet URI missing xt=urn:btih:<hash>")
                )
            hash_ = magnet_hash
    except RtorrentError as exc:
        log.warning("rtorrent.add_failed", error=str(exc))
        return AddTorrentResponse(error=_err(_rtorrent_err_code(exc), str(exc)))

    # Fetch fresh state so the caller gets the real name/size as rtorrent
    # sees it. Magnets haven't resolved metadata yet on add, so the row may
    # come back with an empty name — that's fine, the status tool will pick
    # it up once the swarm answers.
    try:
        row = await ctx.rtorrent.get_download(hash_)
    except RtorrentError as exc:
        log.warning("rtorrent.post_add_status_failed", hash=hash_, error=str(exc))
        row = None

    download = Download.model_validate(row) if row else Download(hash=hash_, name="")
    return AddTorrentResponse(download=download)


async def list_downloads_impl(
    ctx: AppContext, *, active_only: bool = False
) -> ListDownloadsResponse:
    view = "started" if active_only else "main"
    try:
        rows = await ctx.rtorrent.list_downloads(view=view)
    except RtorrentError as exc:
        log.warning("rtorrent.list_failed", error=str(exc))
        return ListDownloadsResponse(error=_err(_rtorrent_err_code(exc), str(exc)))
    return ListDownloadsResponse(downloads=[Download.model_validate(r) for r in rows])


async def get_download_status_impl(ctx: AppContext, *, hash: str) -> DownloadStatusResponse:
    if not hash:
        return DownloadStatusResponse(error=_err("invalid_argument", "hash is required"))
    try:
        row = await ctx.rtorrent.get_download(hash)
    except RtorrentError as exc:
        log.warning("rtorrent.status_failed", hash=hash, error=str(exc))
        return DownloadStatusResponse(error=_err(_rtorrent_err_code(exc), str(exc)))
    if row is None:
        return DownloadStatusResponse(error=_err("not_found", f"no download with hash {hash}"))
    return DownloadStatusResponse(download=Download.model_validate(row))


async def pause_impl(ctx: AppContext, *, hash: str) -> AckResponse:
    return await _simple_ack(ctx.rtorrent.pause, hash)


async def resume_impl(ctx: AppContext, *, hash: str) -> AckResponse:
    return await _simple_ack(ctx.rtorrent.resume, hash)


async def set_download_dir_impl(ctx: AppContext, *, hash: str, directory: str) -> AckResponse:
    if not hash or not directory:
        return AckResponse(error=_err("invalid_argument", "hash and directory are required"))
    try:
        await ctx.rtorrent.set_directory(hash, directory)
    except RtorrentError as exc:
        log.warning("rtorrent.set_dir_failed", hash=hash, directory=directory, error=str(exc))
        return AckResponse(error=_err(_rtorrent_err_code(exc), str(exc)))
    return AckResponse(ok=True)


async def remove_impl(ctx: AppContext, *, hash: str) -> AckResponse:
    return await _simple_ack(ctx.rtorrent.remove, hash)


async def _simple_ack(fn, hash_: str) -> AckResponse:  # type: ignore[no-untyped-def]
    if not hash_:
        return AckResponse(error=_err("invalid_argument", "hash is required"))
    try:
        await fn(hash_)
    except RtorrentError as exc:
        log.warning("rtorrent.call_failed", fn=getattr(fn, "__name__", "?"), error=str(exc))
        return AckResponse(error=_err(_rtorrent_err_code(exc), str(exc)))
    return AckResponse(ok=True)


__all__ = [
    "add_torrent_impl",
    "get_download_status_impl",
    "list_downloads_impl",
    "pause_impl",
    "remove_impl",
    "resume_impl",
    "set_download_dir_impl",
]
