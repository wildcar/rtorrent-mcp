"""Tool-level tests backed by FakeRtorrent (no real SCGI)."""

from __future__ import annotations

import base64
import hashlib

from rtorrent_mcp.context import AppContext
from rtorrent_mcp.tools import (
    add_torrent_impl,
    get_download_status_impl,
    list_downloads_impl,
    pause_impl,
    remove_impl,
    set_download_dir_impl,
)

from .conftest import FakeRtorrent

# A minimal valid .torrent payload: single-file info dict with arbitrary
# name / length / pieces. Produced inline so the test file stays
# self-contained.
_INFO = b"d6:lengthi1024e4:name4:demo12:piece lengthi16384e6:pieces20:" + (b"\x00" * 20) + b"e"
# The "announce" string must declare the EXACT byte length of the URL that
# follows, or our bencoded scanner will misalign and read a value length out
# of a value's payload.
_TRACKER = b"http://t/a"  # 10 bytes
_TORRENT = b"d8:announce" + str(len(_TRACKER)).encode() + b":" + _TRACKER + b"4:info" + _INFO + b"e"
_INFO_HASH = hashlib.sha1(_INFO).hexdigest().upper()


def _row(hash_: str = _INFO_HASH, name: str = "demo", complete: int = 0) -> list:
    # Must match _MULTICALL_METHODS ordering in clients/rtorrent.py.
    return [hash_, name, 1024, 0, 0, 0, 0, "/downloads/demo", 1, 1, complete]


async def test_add_torrent_file_passes_directory_and_returns_hash(
    app_ctx: AppContext, fake_rtorrent: FakeRtorrent
) -> None:
    fake_rtorrent.on("load.raw_start_verbose", lambda target, data, *cmds: 0)
    fake_rtorrent.on(
        "d.multicall2",
        lambda target, view, *methods: [],  # unused in this path
    )
    # Per-field fetchers for get_download after add.
    for field, val in zip(
        (
            "d.hash",
            "d.name",
            "d.size_bytes",
            "d.bytes_done",
            "d.down.rate",
            "d.up.rate",
            "d.ratio",
            "d.directory",
            "d.state",
            "d.is_active",
            "d.complete",
        ),
        _row(),
        strict=True,
    ):
        fake_rtorrent.on(field, lambda _h, v=val: v)

    resp = await add_torrent_impl(
        app_ctx,
        torrent_file_base64=base64.b64encode(_TORRENT).decode(),
        kind="movie",
    )
    assert resp.error is None
    assert resp.download is not None
    assert resp.download.hash == _INFO_HASH

    # The movie download dir must have been forwarded to rtorrent as a
    # piggy-back command on the load call.
    method, params = fake_rtorrent.calls[0]
    assert method == "load.raw_start_verbose"
    assert any(isinstance(p, str) and p.startswith("d.directory.set=/test/Movies/") for p in params)


async def test_add_torrent_rejects_both_inputs(app_ctx: AppContext) -> None:
    resp = await add_torrent_impl(
        app_ctx,
        torrent_file_base64=base64.b64encode(_TORRENT).decode(),
        magnet="magnet:?xt=urn:btih:ABC",
    )
    assert resp.error is not None
    assert resp.error.code == "invalid_argument"


async def test_add_magnet_returns_hash_from_uri(
    app_ctx: AppContext, fake_rtorrent: FakeRtorrent
) -> None:
    fake_rtorrent.on("load.start_verbose", lambda *args: 0)
    # Make get_download return a stub row with a blank name (magnet hasn't
    # resolved metadata yet) — bind value at registration time to avoid
    # capturing the loop variable by reference.
    for field, val in (
        ("d.hash", "A" * 40),
        ("d.name", ""),
        ("d.size_bytes", 0),
        ("d.bytes_done", 0),
        ("d.down.rate", 0),
        ("d.up.rate", 0),
        ("d.ratio", 0),
        ("d.directory", ""),
        ("d.state", 0),
        ("d.is_active", 0),
        ("d.complete", 0),
    ):
        fake_rtorrent.on(field, lambda _h, v=val: v)

    magnet = "magnet:?xt=urn:btih:" + "a" * 40 + "&dn=demo"
    resp = await add_torrent_impl(app_ctx, magnet=magnet, kind="series")
    assert resp.error is None
    assert resp.download is not None
    assert resp.download.hash == "A" * 40

    _, params = fake_rtorrent.calls[0]
    assert any(isinstance(p, str) and p.startswith("d.directory.set=/test/Series/") for p in params)


async def test_list_downloads_maps_rows(app_ctx: AppContext, fake_rtorrent: FakeRtorrent) -> None:
    fake_rtorrent.on(
        "d.multicall2",
        lambda target, view, *methods: [_row(name="A"), _row(hash_="B" * 40, name="B")],
    )
    resp = await list_downloads_impl(app_ctx)
    assert resp.error is None
    assert [d.name for d in resp.downloads] == ["A", "B"]
    assert resp.downloads[0].state == "active"


async def test_get_download_status_not_found(
    app_ctx: AppContext, fake_rtorrent: FakeRtorrent
) -> None:
    import xmlrpc.client

    def _fault(*_: object) -> object:
        raise xmlrpc.client.Fault(-506, "Could not find info-hash")

    for field in (
        "d.hash",
        "d.name",
        "d.size_bytes",
        "d.bytes_done",
        "d.down.rate",
        "d.up.rate",
        "d.ratio",
        "d.directory",
        "d.state",
        "d.is_active",
        "d.complete",
    ):
        fake_rtorrent.on(field, _fault)

    resp = await get_download_status_impl(app_ctx, hash="0" * 40)
    assert resp.error is not None
    assert resp.error.code == "not_found"


async def test_pause_and_resume_forward_hash(
    app_ctx: AppContext, fake_rtorrent: FakeRtorrent
) -> None:
    fake_rtorrent.on("d.pause", lambda h: 0)
    resp = await pause_impl(app_ctx, hash="abcdef1234" * 4)
    assert resp.ok is True and resp.error is None
    assert fake_rtorrent.calls[-1][1] == (("abcdef1234" * 4).upper(),)


async def test_set_download_dir_requires_both_args(app_ctx: AppContext) -> None:
    resp = await set_download_dir_impl(app_ctx, hash="", directory="/x")
    assert resp.error is not None and resp.error.code == "invalid_argument"


async def test_remove_without_delete_data_skips_rmtree(
    app_ctx: AppContext, fake_rtorrent: FakeRtorrent, tmp_path, monkeypatch
) -> None:
    fake_rtorrent.on("d.erase", lambda h: 0)
    # Ensure rmtree is NOT called when delete_data=False.
    called: list[str] = []
    monkeypatch.setattr(
        "rtorrent_mcp.clients.rtorrent.shutil.rmtree",
        lambda *a, **kw: called.append("yes"),
    )
    resp = await remove_impl(app_ctx, hash="A" * 40, delete_data=False)
    assert resp.ok is True and not called
