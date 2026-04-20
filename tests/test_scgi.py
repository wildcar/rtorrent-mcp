"""Low-level SCGI framing tests — run against an asyncio TCP echo server."""

from __future__ import annotations

import asyncio

import pytest

from rtorrent_mcp.clients.scgi import AsyncSCGIClient, SCGIError, _encode_request, _parse_scgi_url


def test_parse_scgi_url_tcp() -> None:
    ep = _parse_scgi_url("scgi://127.0.0.1:5000")
    assert ep.host == "127.0.0.1" and ep.port == 5000 and ep.path is None


def test_parse_scgi_url_unix() -> None:
    ep = _parse_scgi_url("scgi:///var/run/rtorrent.sock")
    assert ep.host is None and ep.path == "/var/run/rtorrent.sock"


def test_encode_request_framing() -> None:
    wire = _encode_request(b"<body/>")
    # netstring header length + colon + headers + comma + body
    assert wire.startswith(b"24:CONTENT_LENGTH\x007\x00SCGI\x001\x00,")
    assert wire.endswith(b",<body/>")


async def test_roundtrip_against_local_echo() -> None:
    """Spin up a tiny TCP server that returns a well-formed SCGI response
    and verify our client strips the framing correctly."""
    response = b"Status: 200 OK\r\nContent-Type: text/xml\r\n\r\n<hello/>"

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # Drain the request; we don't validate it in this test.
        await reader.read(4096)
        writer.write(response)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        client = AsyncSCGIClient(f"scgi://127.0.0.1:{port}", timeout=2.0)
        body = await client.call(b"<request/>")
        assert body == b"<hello/>"
    finally:
        server.close()
        await server.wait_closed()


async def test_connection_refused_is_scgi_error() -> None:
    # Port 1 reliably refuses on Linux without tripping IPv6 weirdness.
    client = AsyncSCGIClient("scgi://127.0.0.1:1", timeout=1.0)
    with pytest.raises(SCGIError):
        await client.call(b"<x/>")
