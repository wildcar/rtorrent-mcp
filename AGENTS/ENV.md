# Environment — rtorrent-mcp

Repo-local env. Cross-repo host map, dev-box tool versions, deploy cheat-sheet, and
credentials layout live in `../AGENTS/ENV.md`. This file holds only what is specific
to this server.

## Where it runs

- **Media host** (`v.wildcar.ru`): systemd unit on the same box as the `rtorrent`
  daemon and the `/mnt/storage/Media/Video/{Movie,Series,Cartoon}` dirs it writes to.
  Colocated by necessity — `base_path` and the per-kind dirs are local paths.
- MCP port **8768**; default transport `streamable-http` + Bearer `MCP_AUTH_TOKEN`.
  The bot reaches it at `http://wildcar.ru:8768/mcp` (shared token).
- Dev box: `uv run rtorrent-mcp` over stdio; tests fake the SCGI transport, so no
  live rtorrent is needed locally.

## Deploy

`deploy/DEPLOY.md` is the step-by-step (remote host + bot wiring);
`deploy/rtorrent-mcp.service` is the systemd unit. Update on the host with
`git pull --ff-only` then `systemctl restart rtorrent-mcp` (`uv sync --no-dev` if
deps changed).

## Environment variables (`.env`; see `.env.example`)

| Var | Default | Notes |
|-----|---------|-------|
| `RTORRENT_SCGI_URL` | `scgi://127.0.0.1:5000` | TCP (`scgi://host:port`) or unix socket (`scgi:///path/to/sock`). |
| `RTORRENT_TIMEOUT_SECONDS` | `30.0` | Upper bound per XML-RPC call. |
| `RTORRENT_DOWNLOAD_DIR_MOVIES` | `/mnt/storage/Media/Video/Movie/` | `kind="movie"`. Absolute, must exist on the rtorrent host. |
| `RTORRENT_DOWNLOAD_DIR_SERIES` | `/mnt/storage/Media/Video/Series/` | `kind="series"`. |
| `RTORRENT_DOWNLOAD_DIR_CARTOONS` | `/mnt/storage/Media/Video/Cartoon/` | `kind="cartoon"`. |
| `MCP_AUTH_TOKEN` | — | Bearer token for HTTP/SSE; empty for stdio-only dev. |
| `MCP_TRANSPORT` | `stdio` (prod sets `streamable-http`) | `stdio` \| `sse` \| `streamable-http`. |
| `MCP_HTTP_HOST` / `MCP_HTTP_PORT` | `127.0.0.1` / `8768` | HTTP bind (prod host `0.0.0.0`). |

Secrets only via env / `.env` (gitignored), never tool arguments. `.env.example`
ships placeholders.

## Gotchas

- The download-dir env vars must point at paths that exist **on the rtorrent host**;
  rtorrent assigns the destination at hash-check time via `d.directory.set=`.
- See `AGENTS/SPEC.md` "Gotchas" for `base_path` vs `directory`, the data-safe
  `remove`, and the ruTorrent `VRS24mrker` comment sentinel.
