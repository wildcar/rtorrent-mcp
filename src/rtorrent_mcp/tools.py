"""Tool implementations. Each returns a Pydantic response envelope;
exceptions become ``ToolError`` entries so callers can dispatch on
``error.code``.
"""

from __future__ import annotations

import asyncio
import base64
import os

import structlog

from .clients.rtorrent import RtorrentError, torrent_info
from .context import AppContext
from .models import (
    AckResponse,
    AddTorrentResponse,
    Download,
    DownloadStatusResponse,
    ListDownloadsResponse,
    MediaKind,
    RemoveResponse,
    ReplaceTorrentResponse,
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
    if kind == "cartoon":
        return ctx.settings.rtorrent_download_dir_cartoons
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
            magnet_hash = await ctx.rtorrent.add_magnet(
                magnet, download_dir=directory, start=start, comment=comment
            )
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


def _is_inside_roots(ctx: AppContext, path: str) -> bool:
    """True when ``path`` resolves strictly *inside* one of the delete
    roots. The root itself, ``..`` escapes and symlinks pointing out of
    the media tree are all rejected."""
    if not path or not os.path.isabs(path):
        return False
    real = os.path.realpath(path)
    for root in ctx.settings.delete_roots():
        real_root = os.path.realpath(root)
        if real != real_root and real.startswith(real_root.rstrip(os.sep) + os.sep):
            return True
    return False


def _delete_payload(base_path: str, files: list[str], is_multi: bool) -> int:
    """Unlink the torrent's own files, then prune directories it left
    empty (bottom-up, never above ``base_path``). Unrelated files that
    happen to share the folder survive. Returns the number unlinked."""
    if not is_multi:
        targets = [base_path]
    else:
        targets = [os.path.join(base_path, rel) for rel in files]
    deleted = 0
    for target in targets:
        try:
            os.unlink(target)
            deleted += 1
        except FileNotFoundError:
            continue
    if is_multi:
        dirs = {os.path.dirname(t) for t in targets} | {base_path}
        # Add every intermediate directory between a file and base_path.
        for d in list(dirs):
            while d.startswith(base_path + os.sep):
                d = os.path.dirname(d)
                dirs.add(d)
        for d in sorted(dirs, key=len, reverse=True):
            try:
                os.rmdir(d)
            except OSError:
                pass  # not empty / already gone
    return deleted


def _rename_payload(ctx: AppContext, src: str, dst: str) -> None:
    """Best-effort rename inside the media roots; never overwrites."""
    if not (_is_inside_roots(ctx, src) and _is_inside_roots(ctx, dst)):
        return
    if not os.path.exists(src) or os.path.exists(dst):
        return
    try:
        os.rename(src, dst)
    except OSError as exc:
        log.warning("rtorrent.replace_rename_failed", src=src, error=str(exc))


async def remove_impl(ctx: AppContext, *, hash: str, delete_data: bool = False) -> RemoveResponse:
    """Erase the download; with ``delete_data`` also unlink its payload.

    Data deletion is guarded: every target must lie inside the configured
    delete roots, and a ``base_path`` shared with another download in the
    session is refused — otherwise erasing a duplicate would eat the
    files of a live torrent. All checks run before anything is erased.
    """
    if not hash:
        return RemoveResponse(error=_err("invalid_argument", "hash is required"))
    if not delete_data:
        ack = await _simple_ack(ctx.rtorrent.remove, hash)
        return RemoveResponse(ok=ack.ok, error=ack.error)

    try:
        row = await ctx.rtorrent.get_download(hash)
        if row is None:
            return RemoveResponse(error=_err("not_found", f"no download with hash {hash}"))
        base_path = str(row.get("base_path") or "").rstrip(os.sep)
        files: list[str] = []
        is_multi = False
        others: list[dict[str, object]] = []
        if base_path:
            is_multi = await ctx.rtorrent.is_multi_file(hash)
            if is_multi:
                files = await ctx.rtorrent.list_file_paths(hash)
            others = await ctx.rtorrent.list_downloads()
    except RtorrentError as exc:
        log.warning("rtorrent.remove_prepare_failed", hash=hash, error=str(exc))
        return RemoveResponse(error=_err(_rtorrent_err_code(exc), str(exc)))

    if base_path:
        if not _is_inside_roots(ctx, base_path):
            return RemoveResponse(
                error=_err("unsafe_path", f"payload {base_path!r} is outside the delete roots")
            )
        for rel in files:
            if not _is_inside_roots(ctx, os.path.join(base_path, rel)):
                return RemoveResponse(
                    error=_err("unsafe_path", f"file {rel!r} escapes {base_path!r}")
                )
        for other in others:
            if other["hash"] != hash.upper() and (
                str(other.get("base_path") or "").rstrip(os.sep) == base_path
            ):
                return RemoveResponse(
                    error=_err(
                        "unsafe_path",
                        f"payload {base_path!r} is shared with download {other['hash']}",
                    )
                )

    try:
        await ctx.rtorrent.close_and_erase(hash)
    except RtorrentError as exc:
        log.warning("rtorrent.remove_failed", hash=hash, error=str(exc))
        return RemoveResponse(error=_err(_rtorrent_err_code(exc), str(exc)))

    deleted = 0
    if base_path:
        try:
            deleted = await asyncio.to_thread(_delete_payload, base_path, files, is_multi)
        except OSError as exc:
            log.warning("rtorrent.delete_data_failed", hash=hash, path=base_path, error=str(exc))
            return RemoveResponse(
                error=_err(
                    "internal_error", f"erased from rtorrent, but deleting files failed: {exc}"
                )
            )
    log.info("rtorrent.removed_with_data", hash=hash, path=base_path, files=deleted)
    return RemoveResponse(ok=True, deleted_files=deleted)


async def replace_torrent_impl(
    ctx: AppContext, *, hash: str, torrent_file_base64: str, start: bool = True
) -> ReplaceTorrentResponse:
    """Swap a download for an updated .torrent of the same release.

    Same info-hash → no-op (``changed=False``). Otherwise the old entry is
    erased (files kept), its payload renamed to the new torrent's name if
    the tracker renamed the release, and the new .torrent loaded into the
    same parent directory with the old comment — rtorrent then hash-checks
    the files already on disk and downloads only what is new or changed.
    """
    if not hash or not torrent_file_base64:
        return ReplaceTorrentResponse(
            error=_err("invalid_argument", "hash and torrent_file_base64 are required")
        )
    try:
        content = base64.b64decode(torrent_file_base64, validate=True)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        return ReplaceTorrentResponse(
            error=_err("invalid_argument", f"torrent_file_base64 is not valid base64: {exc}")
        )
    old_hash = hash.upper()
    try:
        new_hash, new_name, _new_multi = torrent_info(content)
    except (RtorrentError, ValueError) as exc:
        return ReplaceTorrentResponse(
            old_hash=old_hash, error=_err("invalid_argument", f"bad .torrent: {exc}")
        )

    try:
        old = await ctx.rtorrent.get_download(old_hash)
        if old is None:
            return ReplaceTorrentResponse(
                old_hash=old_hash, error=_err("not_found", f"no download with hash {hash}")
            )
        if new_hash == old_hash:
            return ReplaceTorrentResponse(
                changed=False, old_hash=old_hash, download=Download.model_validate(old)
            )

        base_path = str(old.get("base_path") or "").rstrip(os.sep)
        parent = os.path.dirname(base_path) if base_path else str(old.get("directory") or "")
        comment = str(old.get("comment") or "") or None

        await ctx.rtorrent.close_and_erase(old_hash)

        # The tracker often renames the release on update (e.g. «1-8 из 10»
        # → «1-9 из 10»). Move the old payload under the new name so the
        # hash-check finds it instead of starting from scratch.
        if base_path and new_name and os.path.basename(base_path) != new_name:
            new_path = os.path.join(parent, new_name)
            await asyncio.to_thread(_rename_payload, ctx, base_path, new_path)

        await ctx.rtorrent.add_torrent_file(
            content, download_dir=parent or None, start=start, comment=comment
        )
        row = await ctx.rtorrent.get_download(new_hash)
    except RtorrentError as exc:
        log.warning("rtorrent.replace_failed", hash=old_hash, error=str(exc))
        return ReplaceTorrentResponse(
            old_hash=old_hash, error=_err(_rtorrent_err_code(exc), str(exc))
        )
    download = Download.model_validate(row) if row else Download(hash=new_hash, name=new_name)
    log.info("rtorrent.replaced", old_hash=old_hash, new_hash=new_hash)
    return ReplaceTorrentResponse(changed=True, old_hash=old_hash, download=download)


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
    "replace_torrent_impl",
    "resume_impl",
    "set_download_dir_impl",
]
