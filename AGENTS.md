# Agent Instructions — rtorrent-mcp

Primary entrypoint for any agent (Claude, Codex, DeepSeek, etc.) working **inside
this repo**. Read this first.

## Workspace

`rtorrent-mcp` is one repo in the **`movie_handler`** workspace. Cross-repo
architecture, end-to-end download flows, hosts, and shared agreements live in
`../AGENTS.md` + `../AGENTS/SPEC.md`. **This file is authoritative for everything
inside this repo** — open the most specific harness for the files you touch.

This server is the **rtorrent control bridge** (priority 5, MCP port 8768). It
deploys on the **media host** (`v.wildcar.ru`) as a systemd unit, colocated with
the `rtorrent` daemon it drives — it must run on the same host so the per-kind
download directories and `d.base_path` it reports are real local paths. Default
transport is **HTTP+SSE with a Bearer `MCP_AUTH_TOKEN`** (the bot reaches it at
`http://wildcar.ru:8768/mcp`); `stdio` is for local dev and MCP Inspector.

## Project

MCP server that drives a local `rtorrent` instance over **XML-RPC wrapped in
SCGI** (not HTTP). It exposes add / list / status / pause / resume /
set-download-dir / remove tools to the Telegram bot, which pushes rutracker
`.torrent` bytes (or magnets) here after a download is confirmed and then polls
`get_download_status` for completion.

## Document Map

| File | Role |
|------|------|
| `AGENTS.md` | This entrypoint. Repo-local map, workflow, rules. |
| `CLAUDE.md` | Compatibility pointer to `AGENTS.md`. |
| `AGENTS/SPEC.md` | This repo's functional + technical spec: tools, transport, gotchas, layout. |
| `AGENTS/STATE.md` | Current snapshot: goal, now, next, open, deferred. Overwritten each iteration. |
| `AGENTS/HISTORY.md` | Append-only iteration log, newest first. |
| `AGENTS/MEMORY.md` | Durable repo-local facts + working agreements. |
| `AGENTS/ENV.md` | Repo-local env vars, host, deploy notes. Points to `../AGENTS/ENV.md` for cross-repo. |
| `README.md` | User-facing tool list, env vars, run/deploy quickstart. |
| `deploy/` | `DEPLOY.md` step-by-step + `rtorrent-mcp.service` systemd unit. |
| `docs/adr/` | Architecture Decision Records (`docs/adr/TEMPLATE.md`). |

## Startup Checklist

1. Read `AGENTS.md` (this file).
2. Read `AGENTS/SPEC.md` for the tool surface, SCGI transport, and gotchas.
3. Read `AGENTS/STATE.md` for the live snapshot.
4. Read top 3–5 entries in `AGENTS/HISTORY.md`.
5. Read `AGENTS/MEMORY.md` (durable facts + agreements).
6. `git status --short` before editing. Open `AGENTS/ENV.md` for host/deploy detail.

For the cross-repo picture (how the bot, the other MCPs, and media-watch-web fit
together) switch up to `../AGENTS.md`.

## Change Workflow

1. If the tool contract changes — update `AGENTS/SPEC.md` first (and `../AGENTS/SPEC.md`
   if the cross-repo contract shifts, e.g. the `media_id`/`info_hash` agreement).
2. Make the change; keep `ruff` + `mypy --strict` + `pytest` green.
3. Overwrite `AGENTS/STATE.md`; prepend a `≤5-line` entry to `AGENTS/HISTORY.md`.
4. Commit and push to `main` after verification (see Project Rules).

### `AGENTS/HISTORY.md` entry format (≤5 lines, newest first)

```
## YYYY-MM-DD · <short iteration title>
- What: <one line — what changed>
- Why: <one line — reason / task>
- Files: <key paths, comma-separated>
- Next: <one line — what was planned right after>
```

## Memory

`AGENTS/MEMORY.md` is the single store of durable agent memory for this repo —
repo-local facts and agreements that don't duplicate the root. Read it at session
start; append a short bullet when you learn something durable and commit it with
the related change. Durable facts → `MEMORY.md`; current snapshot → `STATE.md`;
iteration log → `HISTORY.md`.

## Language Rules

- Source code, technical docs, code comments: **English**.
- Conversation with the user: **Russian**.
- End-user UI text: **Russian** (this server has none — bot-facing only).
- Docs already in another language stay in that language; don't silently translate.

## Project Rules

- **Structured error returns, not exceptions** across the MCP boundary. Every tool
  returns a `{result, error}` envelope; `error.code` ∈ `invalid_argument`,
  `not_found`, `rtorrent_unreachable`, `rtorrent_error`, `internal_error`.
- **Pydantic models** for all tool I/O. **Secrets only via env vars**
  (`pydantic-settings`, `env_file=".env"`); never tool arguments. Ship `.env.example`.
- **Transport:** `stdio` for local dev / Inspector; HTTP+SSE with Bearer
  `MCP_AUTH_TOKEN` in prod (default `streamable-http` on `:8768`).
- **`d.erase` never deletes payload data** — this is a deliberate safety property
  of `remove`; do not add a data-deleting code path without an explicit decision.
- **Every commit passes `ruff` + `mypy --strict` + `pytest` locally before push.**
  Commit + push to `main` directly after verification — no feature branch, no asking.
- **`git pull --ff-only` on the media host** — never create surprise merge commits.

## Stack & Commands

Python ≥ 3.11, `asyncio`, official Anthropic `mcp` SDK (`FastMCP`), `pydantic` +
`pydantic-settings`, `structlog` (JSON to stderr). No external HTTP client — the
SCGI transport is hand-rolled on `asyncio` streams. `uv` for deps.

```bash
uv sync                                   # install / sync deps
uv run rtorrent-mcp                        # run over stdio (MCP_TRANSPORT=stdio)
uv run pytest && uv run ruff check && uv run mypy src
npx @modelcontextprotocol/inspector --cli uv run rtorrent-mcp --method tools/list  # verify
```

Tests short-circuit the SCGI transport with a fake — no live rtorrent required.

## Project Structure

```
rtorrent-mcp/
├── AGENTS.md / CLAUDE.md / AGENTS/   # this harness
├── README.md                         # user-facing quickstart
├── pyproject.toml / uv.lock / Dockerfile
├── deploy/        DEPLOY.md, rtorrent-mcp.service
├── docs/adr/      ADR template + records
├── src/rtorrent_mcp/
│   ├── server.py     # FastMCP entrypoint, tool registration, transport select
│   ├── tools.py      # tool impls → {result, error} envelopes
│   ├── context.py    # AppContext (settings + RtorrentClient)
│   ├── config.py     # pydantic-settings Settings
│   ├── models.py     # Pydantic I/O models + MediaKind/DownloadState literals
│   └── clients/
│       ├── scgi.py       # hand-rolled async XML-RPC-over-SCGI transport
│       └── rtorrent.py   # high-level rtorrent client + local info-hash derivation
└── tests/
```

## Code Style

- Match surrounding conventions. `ruff` format + lint (line-length 100), `mypy --strict`.
- Keep `_MULTICALL_METHODS` in `clients/rtorrent.py` in lock-step with `_row_to_dict`.
