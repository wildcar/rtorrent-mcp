# Memory — rtorrent-mcp

Durable repo-local facts and agreements NOT derivable from code or SPEC/STATE/
HISTORY. Read at session start; append a bullet when you learn something durable and
commit it with the change. Cross-repo facts live in `../AGENTS/MEMORY.md` — don't
duplicate them here.

## Working agreements

- `remove` is `d.erase` only and **must never delete payload data**. Adding a
  `delete_data` path requires an explicit decision (ADR) and a sentinel-path guard
  so a misconfigured shared `directory` can't be wiped. **Why:** all downloads share
  one parent `directory`; an unguarded delete could blow away unrelated content.

## Project facts

- **Always use `d.base_path`, not `directory`, to locate downloaded content.**
  `directory` is the shared parent download dir; using it made every movie after the
  2nd resolve to the largest video in `Movie/` (prod bug, 2026-04-25).
- ruTorrent stores the comment URL in `d.custom2` as `"VRS24mrker" +
  rawurlencode(url)`; `_set_comment` replicates that exact format and sleeps 1 s
  first because `d.custom2.set` faults before the download is registered.
- rtorrent returns `d.ratio` as **permille** (1000 = 1:1), not a float — divide by 1000.
- `CONTENT_LENGTH` must be the **first** SCGI header or rtorrent refuses the request.
- Must run on the **same host as rtorrent** — the directories and `base_path` it
  returns are local paths the bot/media-watch resolve directly.
