# rtorrent-mcp — functional & technical specification

Source of truth for *what this server does* and *how it is built*. Cross-repo
contract (the `media_id`/`info_hash` agreement, end-to-end download flow) lives in
`../AGENTS/SPEC.md`; this is the repo-local engineering contract.

## Purpose

Drive a single local `rtorrent` daemon over XML-RPC for the Telegram bot's
download pipeline. The bot confirms a release, fetches the `.torrent` bytes from
`rutracker-torrent-mcp` (or has a magnet), and calls `add_torrent` here with a
`kind` hint; it then polls `get_download_status(info_hash)` every 60 s and, on
completion, reads `base_path` to register the file with `media-watch-web`. This
server is path-aware and must run **on the same host as rtorrent** so the
directories and `base_path` it returns are valid local paths.

## Stack

- Python ≥ 3.11, `asyncio`, official Anthropic `mcp` SDK (`FastMCP`).
- `pydantic` v2 models; `pydantic-settings` for config/secrets (`env_file=".env"`).
- `structlog` (JSON to stderr). No HTTP client dep — SCGI transport is hand-rolled.
- `uv` for deps; `ruff`, `mypy --strict`, `pytest` + `pytest-asyncio`. `Dockerfile`
  (`python:3.12-slim`). CI: GitHub Actions (`ruff` → `mypy` → `pytest`).

## Tools

All return a `{result, error}` Pydantic envelope. `error` is a `ToolError`
(`code` ∈ `invalid_argument | not_found | rtorrent_unreachable | rtorrent_error |
internal_error`, plus `message`). Registered in `server.py`; impls in `tools.py`.

| Tool | Signature | Returns |
|------|-----------|---------|
| `add_torrent` | `(torrent_file_base64?, magnet?, download_dir?, kind?, start=True, comment?)` | `AddTorrentResponse{download}` |
| `list_downloads` | `(active_only=False)` | `ListDownloadsResponse{downloads[]}` |
| `get_download_status` | `(hash)` | `DownloadStatusResponse{download}` |
| `pause` | `(hash)` | `AckResponse{ok}` |
| `resume` | `(hash)` | `AckResponse{ok}` |
| `set_download_dir` | `(hash, directory)` | `AckResponse{ok}` |
| `remove` | `(hash)` | `AckResponse{ok}` |

Details:

- **`add_torrent`** — exactly one of `torrent_file_base64` / `magnet` (else
  `invalid_argument`). No plain URLs by design: the bot already holds the
  authenticated rutracker cookie session, so the fetch (and the cookie trust) stays
  there. `kind` ∈ `movie | series | cartoon` maps to the configured per-kind dir
  when `download_dir` is omitted; an explicit `download_dir` always wins; both unset
  → rtorrent's session default. `d.directory.set=` piggybacks on the `load.*` call
  so the destination is assigned **before** hash-check. `start=True` →
  `load.raw_start_verbose` / `load.start_verbose`; `False` → the non-start variants.
  `comment` is written ruTorrent-style (see sentinel note). On add it fetches fresh
  state; a magnet may return an empty `name` until the swarm resolves metadata.
- **`get_download_status`** — fetches each `d.*` field one-shot by hash (rtorrent
  has no multicall-by-hash). An "info-hash not found" fault is mapped to a clean
  `not_found`, not an error.
- **`remove`** — `d.erase` only: drops the download from rtorrent's session and
  **never touches files on disk**. There is intentionally **no `delete_data`
  capability** — payload deletion is not exposed by this MCP. (If a future task adds
  one, it must be an explicit decision with a sentinel-path guard so a misconfigured
  shared `directory` can never be erased.)

`Download` model fields: `hash` (40-char upper hex), `name`, `size_bytes`,
`completed_bytes`, `down_rate`, `up_rate`, `ratio` (float), `directory`,
`base_path`, `state` (`active | stopped | paused | complete`).

## Transport — hand-rolled async SCGI

rtorrent exposes XML-RPC wrapped in **SCGI**, not HTTP, so `clients/scgi.py`
speaks it directly on `asyncio` streams (`AsyncSCGIClient`):

- **Netstring framing**: `<len>:<null-delimited headers>,<body>`, with
  `CONTENT_LENGTH` **first** (rtorrent refuses the request otherwise), then `SCGI 1`.
- **Two URL shapes**: `scgi://host:port` (TCP, default `127.0.0.1:5000`) and
  `scgi:///path/to/sock` (empty netloc → unix socket).
- **Response** is a mini-HTTP message; the header terminator may be `\r\n\r\n` or
  (older builds) `\n\n` — both are stripped before XML-RPC decode.
- **Connection-per-request** — rtorrent closes the socket after each reply, so
  read-to-EOF yields the full payload and pooling buys nothing.
- Transport failures → `SCGIError`; `RtorrentClient` maps them (and XML-RPC faults)
  to `RtorrentError`, which `tools.py` classifies into `rtorrent_unreachable`
  (unreachable/timed out/refused) vs `rtorrent_error`.

`clients/rtorrent.py` keeps `_MULTICALL_METHODS` (the `d.*` fetcher list) in
lock-step with `_row_to_dict`; `ratio` arrives as permille (1000 = 1:1) and is
divided by 1000.

## Local info-hash derivation

`load.*` does **not** return the info-hash, so the client derives it locally:

- **`.torrent`**: a minimal bencode walker (`_extract_bencoded_value` /
  `_skip_bencoded`) slices out the raw `info` dict bytes and `sha1`s them — no full
  torrent-parsing dependency for one hash. Returns uppercase hex.
- **magnet**: extracts `xt=urn:btih:<hash>` from the URI; 40-char hex is
  upper-cased, base32 left as-is (rtorrent normalises internally). Missing `btih`
  → `invalid_argument`.

## Gotchas

- **`base_path` vs `directory`** — `directory` is only rtorrent's *parent* download
  dir, shared by every torrent that landed in the same `download_dir`. To find the
  actual content (single file for single-file torrents, the data folder otherwise)
  callers **must use `d.base_path`**. Caught in prod: every movie after the second
  resolved its `file_path` to the largest video in the shared `Movie/` dir.
  `base_path` is empty until rtorrent resolves metadata.
- **`remove` / `d.erase` is data-safe** — never deletes files; do not add a
  delete-data path casually.
- **ruTorrent comment sentinel** — `_set_comment` writes the comment URL into
  `d.custom2` as `"VRS24mrker" + rawurlencode(comment)`, the exact format ruTorrent
  expects, so the link shows in its UI. It sleeps 1 s first: `d.custom2.set` faults
  if called before the download is registered.
- **Hashes are upper-cased** on every call into rtorrent (`d.pause`, `d.resume`,
  `d.erase`, `d.directory.set`, status reads).

## Configuration

`config.py` (`Settings`, `pydantic-settings`): `rtorrent_scgi_url`,
`rtorrent_timeout_seconds`, `rtorrent_download_dir_{movies,series,cartoons}`,
`mcp_auth_token`. Transport (`MCP_TRANSPORT`, `MCP_HTTP_HOST`/`PORT`) read from env
in `server.py`. See `AGENTS/ENV.md`.

## Project structure

```
src/rtorrent_mcp/
  server.py    FastMCP entrypoint: registers the 7 tools, picks transport
  tools.py     tool impls → {result, error}; error-code classification
  context.py   AppContext = Settings + RtorrentClient (async ctx manager)
  config.py    pydantic-settings Settings
  models.py    ToolError, Download, *Response, MediaKind/DownloadState literals
  clients/scgi.py      AsyncSCGIClient, SCGIError (netstring framing)
  clients/rtorrent.py  RtorrentClient, RtorrentError, info-hash helpers
tests/         pytest, SCGI faked — no live rtorrent
deploy/        DEPLOY.md, rtorrent-mcp.service (systemd unit)
```

## Current state

- ✅ All seven tools implemented, registered, and deployed on the media host (8768).
- ✅ `kind=cartoon` routing → `/mnt/storage/Media/Video/Cartoon/`.
- ✅ `base_path` exposed on `Download` and consumed by the bot's completion poller.
- No open work in this repo. See `AGENTS/STATE.md`.
