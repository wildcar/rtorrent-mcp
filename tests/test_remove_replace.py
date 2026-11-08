"""remove(delete_data=True) path guards and replace_torrent."""

from __future__ import annotations

import base64
import hashlib
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

from rtorrent_mcp.clients.rtorrent import RtorrentClient
from rtorrent_mcp.config import Settings
from rtorrent_mcp.context import AppContext
from rtorrent_mcp.tools import remove_impl, replace_torrent_impl

from .conftest import FakeRtorrent

_FIELDS = (
    "d.hash",
    "d.name",
    "d.size_bytes",
    "d.bytes_done",
    "d.down.rate",
    "d.up.rate",
    "d.ratio",
    "d.directory",
    "d.base_path",
    "d.state",
    "d.is_active",
    "d.complete",
    "d.custom2",
)

OLD = "A" * 40


def _torrent(name: bytes, *, multi: bool) -> bytes:
    if multi:
        info = (
            b"d5:filesld6:lengthi1e4:pathl5:a.mkveee4:name"
            + str(len(name)).encode()
            + b":"
            + name
            + b"12:piece lengthi16384e6:pieces20:"
            + b"\x00" * 20
            + b"e"
        )
    else:
        info = (
            b"d6:lengthi1e4:name"
            + str(len(name)).encode()
            + b":"
            + name
            + b"12:piece lengthi16384e6:pieces20:"
            + b"\x00" * 20
            + b"e"
        )
    return b"d4:info" + info + b"e"


def _info_hash(content: bytes) -> str:
    return hashlib.sha1(content[len(b"d4:info") : -1]).hexdigest().upper()


def _ctx(fake: FakeRtorrent, root: Path) -> AppContext:
    settings = Settings(rtorrent_delete_roots=str(root))
    client = RtorrentClient(scgi_url="scgi://127.0.0.1:5000")
    client._scgi = fake  # type: ignore[assignment]
    return AppContext(settings=settings, rtorrent=client)


def _serve(
    fake: FakeRtorrent, rows: dict[str, dict[str, Any]], *, multi: bool, files: list[str]
) -> None:
    """Wire per-hash d.* getters + list/multicall from ``rows``."""

    def values(r: dict[str, Any], h: str) -> list[Any]:
        return [
            h,
            r.get("name", "x"),
            1,
            1,
            0,
            0,
            0,
            r.get("directory", ""),
            r.get("base_path", ""),
            1,
            1,
            1,
            r.get("custom2", ""),
        ]

    for i, field in enumerate(_FIELDS):

        def getter(h: str, i: int = i) -> Any:
            if h not in rows:
                import xmlrpc.client

                raise xmlrpc.client.Fault(-501, "Could not find info-hash.")
            return values(rows[h], h)[i]

        fake.on(field, getter)
    fake.on("d.multicall2", lambda *_: [values(r, h) for h, r in rows.items()])
    fake.on("d.is_multi_file", lambda _h: int(multi))
    fake.on("f.multicall", lambda *_: [[f] for f in files])
    for m in ("d.stop", "d.close"):
        fake.on(m, lambda _h: 0)

    def erase(h: str) -> int:
        rows.pop(h, None)
        return 0

    fake.on("d.erase", erase)


async def test_delete_data_removes_only_torrent_files(tmp_path: Path) -> None:
    root = tmp_path / "Series"
    payload = root / "Show S01"
    (payload / "sub").mkdir(parents=True)
    (payload / "e1.mkv").write_bytes(b"1")
    (payload / "sub" / "e2.mkv").write_bytes(b"2")
    (root / "Other").mkdir()
    (root / "Other" / "keep.mkv").write_bytes(b"k")

    fake = FakeRtorrent()
    rows = {OLD: {"base_path": str(payload), "directory": str(payload)}}
    _serve(fake, rows, multi=True, files=["e1.mkv", "sub/e2.mkv"])

    resp = await remove_impl(_ctx(fake, root), hash=OLD, delete_data=True)
    assert resp.error is None and resp.ok
    assert resp.deleted_files == 2
    assert not payload.exists()
    assert (root / "Other" / "keep.mkv").exists()
    assert OLD not in rows


async def test_delete_data_keeps_foreign_files_in_payload_dir(tmp_path: Path) -> None:
    root = tmp_path / "Movie"
    payload = root / "Film"
    payload.mkdir(parents=True)
    (payload / "film.mkv").write_bytes(b"1")
    (payload / "plex.nfo").write_bytes(b"n")
    fake = FakeRtorrent()
    _serve(fake, {OLD: {"base_path": str(payload)}}, multi=True, files=["film.mkv"])

    resp = await remove_impl(_ctx(fake, root), hash=OLD, delete_data=True)
    assert resp.ok
    assert not (payload / "film.mkv").exists()
    assert (payload / "plex.nfo").exists()


async def test_delete_data_single_file(tmp_path: Path) -> None:
    root = tmp_path / "Movie"
    root.mkdir()
    f = root / "film.mkv"
    f.write_bytes(b"1")
    fake = FakeRtorrent()
    _serve(fake, {OLD: {"base_path": str(f)}}, multi=False, files=[])

    resp = await remove_impl(_ctx(fake, root), hash=OLD, delete_data=True)
    assert resp.ok and resp.deleted_files == 1
    assert not f.exists()
    assert root.exists()


@pytest.mark.parametrize("where", ["root_itself", "outside", "escape"])
async def test_delete_data_refuses_unsafe_paths(tmp_path: Path, where: str) -> None:
    root = tmp_path / "Movie"
    root.mkdir()
    outside = tmp_path / "etc"
    outside.mkdir()
    (outside / "passwd").write_bytes(b"x")
    files: list[str] = []
    if where == "root_itself":
        base = root
        files = ["a.mkv"]
    elif where == "outside":
        base = outside
        files = ["passwd"]
    else:
        base = root / "Film"
        base.mkdir()
        files = ["../../etc/passwd"]
    fake = FakeRtorrent()
    rows = {OLD: {"base_path": str(base)}}
    _serve(fake, rows, multi=True, files=files)

    resp = await remove_impl(_ctx(fake, root), hash=OLD, delete_data=True)
    assert resp.error is not None and resp.error.code == "unsafe_path"
    assert OLD in rows  # nothing erased
    assert (outside / "passwd").exists()


async def test_delete_data_refuses_shared_base_path(tmp_path: Path) -> None:
    root = tmp_path / "Movie"
    payload = root / "Film"
    payload.mkdir(parents=True)
    (payload / "film.mkv").write_bytes(b"1")
    fake = FakeRtorrent()
    rows = {OLD: {"base_path": str(payload)}, "B" * 40: {"base_path": str(payload)}}
    _serve(fake, rows, multi=True, files=["film.mkv"])

    resp = await remove_impl(_ctx(fake, root), hash=OLD, delete_data=True)
    assert resp.error is not None and resp.error.code == "unsafe_path"
    assert (payload / "film.mkv").exists()


async def test_remove_without_data_only_erases(tmp_path: Path) -> None:
    fake = FakeRtorrent()
    fake.on("d.erase", lambda h: 0)
    resp = await remove_impl(_ctx(fake, tmp_path), hash=OLD)
    assert resp.ok
    assert [c[0] for c in fake.calls] == ["d.erase"]


async def test_replace_same_hash_is_noop(tmp_path: Path) -> None:
    content = _torrent(b"Show", multi=True)
    h = _info_hash(content)
    fake = FakeRtorrent()
    rows = {h: {"base_path": str(tmp_path / "Show")}}
    _serve(fake, rows, multi=True, files=[])

    resp = await replace_torrent_impl(
        _ctx(fake, tmp_path), hash=h, torrent_file_base64=base64.b64encode(content).decode()
    )
    assert resp.error is None and resp.changed is False
    assert not any(c[0].startswith("load.") for c in fake.calls)


async def test_replace_loads_new_torrent_and_renames_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    async def _no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    root = tmp_path / "Series"
    old_payload = root / "Show [1-8]"
    old_payload.mkdir(parents=True)
    (old_payload / "a.mkv").write_bytes(b"1")
    content = _torrent(b"Show [1-9]", multi=True)
    new_hash = _info_hash(content)
    comment = "https://rutracker.org/forum/viewtopic.php?t=42"

    fake = FakeRtorrent()
    rows: dict[str, dict[str, Any]] = {
        OLD: {
            "base_path": str(old_payload),
            "directory": str(old_payload),
            "custom2": "VRS24mrker" + urllib.parse.quote(comment, safe=""),
        }
    }
    _serve(fake, rows, multi=True, files=[])

    def load(_target: str, _data: Any, *cmds: str) -> int:
        rows[new_hash] = {"base_path": str(root / "Show [1-9]")}
        return 0

    fake.on("load.raw_start_verbose", load)
    custom2: list[tuple[str, str]] = []
    fake.on("d.custom2.set", lambda h, v: custom2.append((h, v)) or 0)

    resp = await replace_torrent_impl(
        _ctx(fake, root), hash=OLD, torrent_file_base64=base64.b64encode(content).decode()
    )
    assert resp.error is None, resp.error
    assert resp.changed is True
    assert resp.old_hash == OLD
    assert resp.download is not None and resp.download.hash == new_hash
    assert OLD not in rows
    # Payload moved under the new name so hash-check reuses it.
    assert (root / "Show [1-9]" / "a.mkv").exists()
    load_call = next(c for c in fake.calls if c[0] == "load.raw_start_verbose")
    assert f"d.directory.set={root}" in load_call[1]
    assert custom2 and urllib.parse.unquote(custom2[0][1][len("VRS24mrker") :]) == comment


async def test_comment_decoded_from_custom2(tmp_path: Path) -> None:
    from rtorrent_mcp.tools import get_download_status_impl

    fake = FakeRtorrent()
    url = "https://rutracker.org/forum/viewtopic.php?t=7"
    _serve(
        fake,
        {OLD: {"custom2": "VRS24mrker" + urllib.parse.quote(url, safe="")}},
        multi=False,
        files=[],
    )
    resp = await get_download_status_impl(_ctx(fake, tmp_path), hash=OLD)
    assert resp.download is not None and resp.download.comment == url
