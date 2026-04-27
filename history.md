# history — rtorrent-mcp

Per-repo task log. Each code-change task adds a short entry **before**
work starts.

---

## 2026-04-27

### Add `kind=cartoon` routing → `/mnt/storage/Media/Video/Cartoon/`

**Why.** Cross-repo cartoon flow (see metadata-mcp / clients / web
history). Animated movies route to a dedicated dir so Plex picks them
up into a Cartoon library. Series animation keeps going to Series/.

**What.** `MediaKind` literal gains `"cartoon"`. New
`rtorrent_download_dir_cartoons` setting (default
`/mnt/storage/Media/Video/Cartoon/`); `_resolve_dir` maps `cartoon`
→ that path. Three-line change overall, no test added — the existing
routing tests cover the pattern by analogy.

---

## 2026-04-25

### Expose `base_path` on the Download model

- Add `d.base_path=` to the multicall fetcher list, populate
  `Download.base_path` from it, document it in the model.
- Why: `directory` is just rtorrent's parent download directory and is
  shared by every torrent that landed in the same `download_dir`. For
  callers that want to find the actual content (e.g. media-watch-web's
  `/api/register`) this isn't enough — they need the path of the
  data, which `d.base_path` gives: a single file for single-file
  torrents, the data folder for multi-file ones.
- Caught in prod: every movie registered after the second one had its
  `file_path` resolved to whatever the largest video file in the
  shared `/mnt/.../Movie/` directory happened to be (a 4K Matrix
  release in this case). Switching the bot to `base_path` will fix
  this end-to-end.

