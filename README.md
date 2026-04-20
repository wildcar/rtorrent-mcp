# rtorrent-mcp

MCP server that drives a local `rtorrent` instance over SCGI/XML-RPC. Part of the
[`movie_handler`](../) project — paired with `rutracker-torrent-mcp` on the bot side.

## Tools

| Tool | Purpose |
|---|---|
| `add_torrent(torrent_file_base64? \| magnet?, download_dir?, kind?, start=True)` | Enqueue a new download. `kind="movie"/"series"` picks a pre-configured destination if `download_dir` is omitted. |
| `list_downloads(active_only=False)` | Every download rtorrent knows about (or just active ones). |
| `get_download_status(hash)` | Fresh state for a single info-hash. |
| `pause(hash)` / `resume(hash)` | `d.pause` / `d.resume`. |
| `set_download_dir(hash, directory)` | Move the payload destination. |
| `remove(hash, delete_data=False)` | `d.erase` the download; optionally `rm -rf` its files. |

All tools return `{result, error}` envelopes — `error.code` is one of
`invalid_argument`, `not_found`, `rtorrent_unreachable`, `rtorrent_error`,
`internal_error`.

## Transport

XML-RPC lives on top of SCGI, not HTTP — we speak it directly. Two URL shapes:

```
RTORRENT_SCGI_URL=scgi://127.0.0.1:5000      # TCP (rutorrent default)
RTORRENT_SCGI_URL=scgi:///tmp/rtorrent.sock  # unix socket
```

## Run locally (stdio)

```bash
uv sync
cp .env.example .env && $EDITOR .env
uv run rtorrent-mcp
```

Inspect tools with MCP Inspector:

```bash
npx @modelcontextprotocol/inspector --cli uv run rtorrent-mcp --method tools/list
```

## Run over HTTP (networked)

```bash
MCP_TRANSPORT=streamable-http \
MCP_HTTP_HOST=0.0.0.0 MCP_HTTP_PORT=8768 \
MCP_AUTH_TOKEN=... \
uv run rtorrent-mcp
```

A sample systemd unit lives in [`deploy/rtorrent-mcp.service`](deploy/rtorrent-mcp.service).

## Environment variables

| Var | Default | Notes |
|---|---|---|
| `RTORRENT_SCGI_URL` | `scgi://127.0.0.1:5000` | TCP or unix socket. |
| `RTORRENT_TIMEOUT_SECONDS` | `30` | Upper bound per XML-RPC call. |
| `RTORRENT_DOWNLOAD_DIR_MOVIES` | `/mnt/storage/Media/Video/Movie/` | Used when `kind="movie"`. |
| `RTORRENT_DOWNLOAD_DIR_SERIES` | `/mnt/storage/Media/Video/Series/` | Used when `kind="series"`. |
| `MCP_AUTH_TOKEN` | — | Bearer token for HTTP/SSE transport. |
| `MCP_TRANSPORT` | `stdio` | `stdio`, `sse`, `streamable-http`. |
| `MCP_HTTP_HOST` / `MCP_HTTP_PORT` | `127.0.0.1` / `8768` | HTTP bind address. |

## Tests

```bash
uv run pytest
```

All unit tests short-circuit the SCGI transport with a fake — no live
rtorrent required.
