"""Async SCGI transport for rtorrent's XML-RPC endpoint.

rtorrent exposes XML-RPC wrapped in SCGI rather than HTTP. The protocol is
dead simple:

    <netstring-headers>,<body>

where ``<netstring-headers>`` is ``<byte-len>:<null-delimited key/value
pairs>`` and ``CONTENT_LENGTH`` MUST be the first header. The response is a
mini HTTP message — ``Status: …`` headers, a blank line, then the XML-RPC
payload.

We support two URL shapes:
  * ``scgi://host:port``     → TCP
  * ``scgi:///path/to/sock`` → unix socket (empty host triggers unix path)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlparse


class SCGIError(RuntimeError):
    """SCGI-level failure (connection refused, malformed framing, etc.)."""


@dataclass(frozen=True)
class _Endpoint:
    host: str | None  # ``None`` → unix socket
    port: int | None
    path: str | None


def _parse_scgi_url(url: str) -> _Endpoint:
    parsed = urlparse(url)
    if parsed.scheme != "scgi":
        raise ValueError(f"expected scgi:// URL, got {parsed.scheme!r}")
    # scgi:///path/to/sock → unix socket (netloc empty, path holds the socket)
    if not parsed.netloc:
        if not parsed.path:
            raise ValueError("scgi:// URL needs either host:port or a unix path")
        return _Endpoint(host=None, port=None, path=parsed.path)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 5000
    return _Endpoint(host=host, port=port, path=None)


def _encode_request(body: bytes) -> bytes:
    # CONTENT_LENGTH must be the first header per the SCGI spec; rtorrent
    # refuses the request otherwise. Keys/values are null-terminated and the
    # whole block is wrapped in a netstring.
    headers = b"CONTENT_LENGTH\x00" + str(len(body)).encode() + b"\x00SCGI\x001\x00"
    return str(len(headers)).encode() + b":" + headers + b"," + body


def _strip_scgi_response_headers(raw: bytes) -> bytes:
    # Response looks like ``Status: 200 OK\r\nContent-Type: text/xml\r\n\r\n<xml>``.
    # Older rtorrent builds emit ``\n\n`` instead of ``\r\n\r\n`` — handle both.
    sep_crlf = raw.find(b"\r\n\r\n")
    sep_lf = raw.find(b"\n\n")
    if sep_crlf != -1 and (sep_lf == -1 or sep_crlf < sep_lf):
        return raw[sep_crlf + 4 :]
    if sep_lf != -1:
        return raw[sep_lf + 2 :]
    raise SCGIError("SCGI response missing header terminator")


class AsyncSCGIClient:
    """One-shot XML-RPC-over-SCGI exchange. Connection per request is
    standard practice here — rtorrent's SCGI handler closes after each
    reply anyway, so pooling buys nothing."""

    def __init__(self, url: str, *, timeout: float = 30.0) -> None:
        self._endpoint = _parse_scgi_url(url)
        self._timeout = timeout

    async def call(self, body: bytes) -> bytes:
        """Send ``body`` (an XML-RPC request) and return the XML response
        with the SCGI/HTTP-style framing stripped."""
        try:
            raw = await asyncio.wait_for(self._exchange(body), timeout=self._timeout)
        except TimeoutError as exc:
            raise SCGIError(f"rtorrent SCGI timed out after {self._timeout}s") from exc
        except OSError as exc:
            raise SCGIError(f"rtorrent SCGI unreachable: {exc}") from exc
        return _strip_scgi_response_headers(raw)

    async def _exchange(self, body: bytes) -> bytes:
        ep = self._endpoint
        if ep.path is not None:
            reader, writer = await asyncio.open_unix_connection(ep.path)
        else:
            assert ep.host is not None and ep.port is not None
            reader, writer = await asyncio.open_connection(ep.host, ep.port)
        try:
            writer.write(_encode_request(body))
            await writer.drain()
            # rtorrent closes the socket after the response, so read-to-EOF
            # gives us the full payload without needing to parse chunk sizes.
            return await reader.read()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


__all__ = ["AsyncSCGIClient", "SCGIError"]
