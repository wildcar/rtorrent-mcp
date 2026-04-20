"""Shared fixtures.

Tests run against a fake rtorrent that short-circuits the SCGI transport
and dispatches XML-RPC method calls to Python handlers. Keeps us from
needing a real rtorrent in CI.
"""

from __future__ import annotations

import xmlrpc.client
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
import pytest_asyncio

from rtorrent_mcp.clients.rtorrent import RtorrentClient
from rtorrent_mcp.config import Settings
from rtorrent_mcp.context import AppContext


class FakeRtorrent:
    """Stand-in for the SCGI layer. Handlers are registered per XML-RPC
    method and returned as plain Python values; we serialise them back to
    XML so the RtorrentClient code path is exercised unchanged."""

    def __init__(self) -> None:
        self._handlers: dict[str, Callable[..., Any]] = {}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def on(self, method: str, handler: Callable[..., Any]) -> None:
        self._handlers[method] = handler

    async def call(self, body: bytes) -> bytes:
        params, method = xmlrpc.client.loads(body.decode("utf-8"))
        assert method is not None
        self.calls.append((method, params))
        handler = self._handlers.get(method)
        if handler is None:
            # rtorrent returns a fault for unknown methods; mirror that.
            return xmlrpc.client.dumps(
                xmlrpc.client.Fault(-506, f"Method '{method}' not defined"),
                methodresponse=True,
            ).encode("utf-8")
        try:
            result = handler(*params)
        except xmlrpc.client.Fault as fault:
            return xmlrpc.client.dumps(fault, methodresponse=True).encode("utf-8")
        # rtorrent responses always wrap a single value in a 1-tuple.
        return xmlrpc.client.dumps((result,), methodresponse=True).encode("utf-8")


@pytest.fixture
def fake_rtorrent() -> FakeRtorrent:
    return FakeRtorrent()


@pytest_asyncio.fixture
async def app_ctx(fake_rtorrent: FakeRtorrent) -> AsyncIterator[AppContext]:
    settings = Settings(
        rtorrent_scgi_url="scgi://127.0.0.1:5000",
        rtorrent_download_dir_movies="/test/Movies/",
        rtorrent_download_dir_series="/test/Series/",
    )
    client = RtorrentClient(scgi_url=settings.rtorrent_scgi_url)
    # Replace the real SCGI transport with our fake; the client never opens
    # a real socket during unit tests.
    client._scgi = fake_rtorrent  # type: ignore[assignment]
    yield AppContext(settings=settings, rtorrent=client)
