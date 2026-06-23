# History — rtorrent-mcp

Newest first. Each entry ≤5 lines using the format in `AGENTS.md`. Repo-local log;
cross-repo entries live in `../AGENTS/HISTORY.md`.

---

## 2026-06-23 · Migrate to agent-template harness
- What: Added `AGENTS.md`, `CLAUDE.md` pointer, `AGENTS/{SPEC,STATE,HISTORY,MEMORY,ENV}.md`, `docs/adr/TEMPLATE.md`; retired `history.md` (no `env.md` existed).
- Why: Adopt the standard `wildcar/agent-template` harness in every repo.
- Files: `AGENTS.md`, `CLAUDE.md`, `AGENTS/*`, `docs/adr/TEMPLATE.md`.
- Next: Reactive maintenance only.

## 2026-04-27 · kind=cartoon routing
- What: `MediaKind` gains `"cartoon"`; new `rtorrent_download_dir_cartoons` (default `/mnt/storage/Media/Video/Cartoon/`); `_resolve_dir` maps it.
- Why: Cross-repo cartoon flow — animated movies route to a dedicated Plex Cartoon library; animated series stay in Series/.
- Files: `src/rtorrent_mcp/{models,config,tools}.py`.
- Next: Covered by existing routing tests by analogy (no new test).

## 2026-04-25 · Expose base_path on the Download model
- What: Added `d.base_path=` to the multicall fetcher and `Download.base_path`.
- Why: `directory` is the shared parent dir; callers (media-watch register) need the actual content path — single file or data folder.
- Files: `src/rtorrent_mcp/clients/rtorrent.py`, `src/rtorrent_mcp/models.py`.
- Next: Switch the bot to `base_path`; prod bug had every movie after the 2nd resolving to the largest video in the shared dir.

## 2026-04-20 · Scaffold rtorrent-mcp (priority 5)
- What: Fifth MCP server — controls rtorrent over XML-RPC-in-SCGI; add/list/status/pause/resume/set-dir/remove; hand-rolled async SCGI + local info-hash; ruTorrent VRS24mrker comment.
- Why: Final server in the download pipeline; colocated with the rtorrent daemon.
- Files: `rtorrent-mcp/` (new repo).
- Next: Wire the bot's download confirm to push torrents to rtorrent.
