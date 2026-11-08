# State — rtorrent-mcp

Repo-local snapshot. Overwrite each iteration. Cross-repo view → `../AGENTS/STATE.md`.

## Goal

MCP bridge that drives the media host's `rtorrent` over XML-RPC/SCGI: add a
.torrent/magnet with a `kind` hint, list/status/pause/resume/move/erase downloads,
and report `base_path` so the bot can register completed files.

## Now

- All seven tools live and deployed on the media host (`v.wildcar.ru`, port 8768,
  HTTP+SSE + Bearer token). `kind=cartoon` routing and `base_path` exposure shipped.
- Harness migrated to the `agent-template` layout.
- 2026-11-08: `remove(delete_data)` + `replace_torrent` + `Download.comment` pushed
  for the bot's `/torrents`; **not deployed yet** — `homesrv` needs pull + unit
  reinstall (new `ReadWritePaths=/mnt/storage/Media/Video`, else deletion fails
  with a read-only filesystem error).

## Next

- Deploy the 2026-11-08 changes on `homesrv` (operator; no ssh route from dev).

## Open questions

- —

## Deferred

- —
