"""High-level rtorrent client built on top of the async SCGI transport.

Exposes the handful of XML-RPC calls we actually need for the bot — adding
torrents, listing downloads, controlling state, removing. Returns plain
Python values / dicts; the MCP tool layer wraps them into Pydantic models.
"""

from __future__ import annotations

import xmlrpc.client
from typing import Any

import structlog

from .scgi import AsyncSCGIClient, SCGIError

log = structlog.get_logger(__name__)


class RtorrentError(RuntimeError):
    """Raised when rtorrent returns an XML-RPC fault or the transport fails."""


# Per-download field fetcher list used by d.multicall2 and our single-hash
# status path. Order here defines the tuple layout returned by multicall —
# keep in sync with ``_row_to_dict``.
_MULTICALL_METHODS: tuple[str, ...] = (
    "d.hash=",
    "d.name=",
    "d.size_bytes=",
    "d.bytes_done=",
    "d.down.rate=",
    "d.up.rate=",
    "d.ratio=",
    "d.directory=",
    "d.state=",
    "d.is_active=",
    "d.complete=",
)


def _row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    hash_, name, size_b, done_b, down_r, up_r, ratio, directory, state, active, complete = row
    if complete:
        label = "complete"
    elif not state:
        label = "stopped"
    elif not active:
        label = "paused"
    else:
        label = "active"
    return {
        "hash": str(hash_).upper(),
        "name": str(name),
        "size_bytes": int(size_b),
        "completed_bytes": int(done_b),
        "down_rate": int(down_r),
        "up_rate": int(up_r),
        # rtorrent returns ratio as permille (1000 = 1:1), not a float.
        "ratio": round(int(ratio) / 1000.0, 3),
        "directory": str(directory),
        "state": label,
    }


class RtorrentClient:
    def __init__(self, scgi_url: str, *, timeout: float = 30.0) -> None:
        self._scgi = AsyncSCGIClient(scgi_url, timeout=timeout)

    async def call(self, method: str, *params: Any) -> Any:
        body = xmlrpc.client.dumps(params, methodname=method).encode("utf-8")
        try:
            raw = await self._scgi.call(body)
        except SCGIError as exc:
            raise RtorrentError(str(exc)) from exc
        try:
            result, _ = xmlrpc.client.loads(raw)
        except xmlrpc.client.Fault as exc:
            raise RtorrentError(f"rtorrent fault {exc.faultCode}: {exc.faultString}") from exc
        except Exception as exc:  # malformed XML
            raise RtorrentError(f"rtorrent returned malformed XML-RPC: {exc}") from exc
        # xmlrpc.client.loads returns a single-element tuple even for void.
        return result[0] if result else None

    # -- ingestion -------------------------------------------------------

    async def add_torrent_file(
        self, content: bytes, *, download_dir: str | None, start: bool, comment: str | None = None
    ) -> str:
        """Load a .torrent from raw bytes. Returns the info-hash.

        We use ``load.raw_start_verbose`` / ``load.raw_verbose`` depending on
        whether the caller wants the download to start immediately. The
        ``d.directory.set=`` command piggybacks so rtorrent assigns the
        destination BEFORE hash-check picks a default.
        """
        method = "load.raw_start_verbose" if start else "load.raw_verbose"
        extra: list[str] = []
        if download_dir:
            extra.append(f"d.directory.set={download_dir}")
        if comment:
            extra.append(f"d.custom.set=comment,{comment}")
        await self.call(method, "", xmlrpc.client.Binary(content), *extra)
        # rtorrent's load.* doesn't return the hash; derive it ourselves from
        # the torrent's info dict so we can report it to the caller.
        return _info_hash_from_torrent(content)

    async def add_magnet(self, magnet: str, *, download_dir: str | None, start: bool, comment: str | None = None) -> str | None:
        method = "load.start_verbose" if start else "load.verbose"
        extra: list[str] = []
        if download_dir:
            extra.append(f"d.directory.set={download_dir}")
        if comment:
            extra.append(f"d.custom.set=comment,{comment}")
        await self.call(method, "", magnet, *extra)
        # Magnet hash is in the URI as ``xt=urn:btih:<hash>``; extract it so
        # callers can poll status without guessing.
        return _info_hash_from_magnet(magnet)

    # -- listing / status ------------------------------------------------

    async def list_downloads(self, view: str = "main") -> list[dict[str, Any]]:
        rows = await self.call("d.multicall2", "", view, *_MULTICALL_METHODS)
        return [_row_to_dict(tuple(r)) for r in rows or []]

    async def get_download(self, hash_: str) -> dict[str, Any] | None:
        h = hash_.upper()
        # Fetch each field one-by-one; rtorrent doesn't have a
        # d.multicall-by-hash, so we just do N calls. Still single-digit ms
        # on a local SCGI, and keeps the parsing code unified.
        try:
            vals: list[Any] = []
            for m in _MULTICALL_METHODS:
                # trim trailing '=' — one-shot calls want the bare method name
                method = m.rstrip("=")
                vals.append(await self.call(method, h))
        except RtorrentError as exc:
            # rtorrent faults with "Could not find info-hash" for unknown
            # downloads; treat as not-found so the tool can return a clean
            # structured error.
            if "info-hash" in str(exc).lower() or "Not Found" in str(exc):
                return None
            raise
        return _row_to_dict(tuple(vals))

    # -- control ---------------------------------------------------------

    async def pause(self, hash_: str) -> None:
        await self.call("d.pause", hash_.upper())

    async def resume(self, hash_: str) -> None:
        await self.call("d.resume", hash_.upper())

    async def set_directory(self, hash_: str, directory: str) -> None:
        await self.call("d.directory.set", hash_.upper(), directory)

    async def remove(self, hash_: str) -> None:
        """Erase the download from rtorrent's session. Files on disk are
        left untouched — rtorrent's ``d.erase`` never deletes payload data,
        and we deliberately don't add that capability to the MCP."""
        await self.call("d.erase", hash_.upper())


# -- helpers ------------------------------------------------------------


def _info_hash_from_magnet(magnet: str) -> str | None:
    # Accept ``urn:btih:<hex-or-base32>``; return uppercase hex for hex
    # hashes, leave base32 as-is (rtorrent normalises internally).
    marker = "urn:btih:"
    idx = magnet.find(marker)
    if idx == -1:
        return None
    tail = magnet[idx + len(marker) :]
    end = min((tail.find(c) for c in "&?#" if tail.find(c) != -1), default=len(tail))
    hash_part = tail[:end].strip()
    if len(hash_part) == 40 and all(c in "0123456789abcdefABCDEF" for c in hash_part):
        return hash_part.upper()
    return hash_part or None


def _info_hash_from_torrent(content: bytes) -> str:
    """Compute the info-hash of a .torrent payload.

    Implements just enough bencoding to find the raw bytes of the ``info``
    dict, then sha1's them. Pulling a full torrent-parsing dep for one hash
    is overkill.
    """
    import hashlib

    info_slice = _extract_bencoded_value(content, key=b"info")
    if info_slice is None:
        raise RtorrentError(".torrent payload is missing the 'info' dict")
    return hashlib.sha1(info_slice).hexdigest().upper()


def _extract_bencoded_value(data: bytes, *, key: bytes) -> bytes | None:
    """Return the raw byte slice of the value under ``key`` in the top-level
    bencoded dict, or ``None`` if absent."""
    if not data.startswith(b"d"):
        return None
    i = 1
    while i < len(data) and data[i : i + 1] != b"e":
        # Each dict key is itself a bencoded string: <len>:<bytes>
        klen, sep, _rest = data[i:].partition(b":")
        if not sep:
            return None
        klen_i = int(klen)
        key_start = i + len(klen) + 1
        key_end = key_start + klen_i
        key_bytes = data[key_start:key_end]
        value_start = key_end
        value_end = _skip_bencoded(data, value_start)
        if key_bytes == key:
            return data[value_start:value_end]
        i = value_end
    return None


def _skip_bencoded(data: bytes, i: int) -> int:
    """Return the index past the bencoded element starting at ``i``."""
    tag = data[i : i + 1]
    if tag == b"i":
        end = data.index(b"e", i)
        return end + 1
    if tag in (b"l", b"d"):
        j = i + 1
        while data[j : j + 1] != b"e":
            j = _skip_bencoded(data, j)
        return j + 1
    # string: <len>:<bytes>
    colon = data.index(b":", i)
    length = int(data[i:colon])
    return colon + 1 + length


__all__ = ["RtorrentClient", "RtorrentError"]
