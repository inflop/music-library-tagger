---
name: music-library-tagger
description: >
  Clean up an artist's album collection (MP3/ID3) for a self-hosted music
  server such as Navidrome: correct album names, release years, track titles,
  genres, multi-disc numbering (disc 1/2/3…), album artist, and cover art
  (embedded + cover.jpg), verifying facts against public sources (MusicBrainz /
  Cover Art Archive / discography info). Run it inside a folder that holds one
  band's albums. It analyzes current tags, asks a few decisions, writes a
  reviewable Markdown plan, and applies it reversibly. Use when the user wants
  to "fix tags", "organize albums for Navidrome/Plex/Jellyfin", "add covers",
  "fix multi-disc albums", or similar.
---

# Music Library Tagger

Turn a messy single-artist album folder into a clean, consistent library that a
music server (Navidrome, Plex, Jellyfin, Subsonic…) displays correctly.

**Golden rules**
- **Never touch the audio stream** — only ID3 tags and artwork.
- **Always back up first** (`apply_plan.py` writes a full text-tag backup before any change).
- **Verify against public sources** — don't trust existing tags or folder names blindly.
- **Privacy**: never write personally identifying data (name, email, username, local
  paths) into tags, `cover.jpg`, filenames, or image metadata. Web requests use a
  User-Agent that identifies the tool and links to the project, never the user.
  See `references/conventions.md`.
- **Approval gate**: present the plan and get a green light before applying; offer a
  2-album pilot first.

The helper scripts live in the `scripts/` folder next to this file. Resolve that
absolute directory once (call it `$SKILL`) and run scripts with the machine's Python
(e.g. `python "$SKILL/scripts/analyze.py" …`). When this skill is installed as a plugin,
`$SKILL` is `${CLAUDE_PLUGIN_ROOT}/skills/music-library-tagger`. Read
`references/conventions.md` before building the plan.

---

## Phase 0 — Environment

Confirm Python and libraries; install what's missing:
`python -m pip install mutagen Pillow`
(`mutagen` = tag read/write, required; `Pillow` = image dimensions + clean re-encode.)

## Phase 1 — Analyze (read-only)

Run:
`python "$SKILL/scripts/analyze.py" "<ROOT>" --json "<ROOT>/.music-tagger/analysis.json"`

This detects albums (incl. multi-disc sets grouped from `CD1/CD2/Vol. I…` subfolders),
dumps current tags, lists every distinct comment/encoder frame, and measures cover
dimensions. Read the report. Note: file **size in KB is not quality** — check pixel
dimensions, and beware "front" scans that are really 2:1 gatefold spreads.

Summarize for the user what's wrong: inconsistent/garbage album names, wrong/missing
years, empty disc numbers on multi-disc sets, missing/embedded covers, genre spread,
junk comments, title glitches (double spaces, backslashes, casing).

## Phase 2 — Ask the decisions

Use `AskUserQuestion`. Standard set (adapt to what analysis found):
1. **Multiple editions** of the same album (Original / remaster / anniversary): append an
   edition suffix to the album title (keeps them separate) vs. clean canonical title.
2. **Year field**: the edition's actual release year vs. the original album year.
3. **Cover source**: web-first (Cover Art Archive) vs. local scans; and embed-in-MP3 +
   `cover.jpg` vs. one of them.
4. **Genre**: unify to one value vs. leave as-is.
5. **Comments/encoder frames**: strip vs. keep (show the distinct COMM values first — some
   may be useful liner notes, most are ripper junk).
6. **Title case**: canonical (lowercase articles/prepositions) vs. preserve.
7. **Multi-disc art**: if a shared `covers/` folder holds per-disc images (`Disc 1`, `CD One`,
   `front CD2`…), distribute them into the disc subfolders so each disc gets dedicated art.
8. **Missing covers**: for albums with no good local front, download from the web vs. skip.

Also flag anything non-obvious you found and ask what to do (per the user's own data).

## Phase 3 — Verify facts online

For each album, confirm the **canonical album title, release year, and track titles** against
public sources (MusicBrainz, Wikipedia, Discogs search results). Pay special attention to:
- reissues/anniversary editions (real release year vs. original),
- live albums (recording year vs. release year),
- box sets (unified album title + disc subtitles),
- typos in existing tags (e.g. a year of `1082`).
Note per-album `⚠` where your corrected value differs from the current tag/folder.
Respect MusicBrainz rate limits (~1 req/s) and the tool's declared User-Agent.

## Phase 4 — Write the plan + build plan.json

Write a human Markdown plan to `<ROOT>/PLAN-TAGI.md` (or similar) with: decisions taken,
per-album target table (album title, year, disc numbering, genre), cover strategy, disc-art
redistribution map, flagged anomalies, privacy note, and execution order.

Then build the machine-readable `<ROOT>/.music-tagger/plan.json` that
`apply_plan.py` consumes (schema documented at the top of `apply_plan.py`). Fill titles,
years, `track`/`track_total`, `disc`/`disc_total`, chosen `cover` per disc, optional
`move_images`, and the global `options` (artist, album_artist, genre, strip_frames,
id3_version=3, cover flags, move_images_mode).

## Phase 5 — Covers

For each album, pick the best front:
- Try `python "$SKILL/scripts/fetch_cover.py" --artist "<Band>" --album "<Title>" [--year N]
  --out "<ROOT>/.music-tagger/covers/<slug>.jpg"` (Cover Art Archive, 1200px, neutral UA).
- Compare with the best local scan (dimensions + that it's a real square front, not a spread).
- Choose the better one; record its path as the disc/album `cover` in plan.json.
Build a small **preview/contact sheet** (an HTML file or a table of paths + dimensions +
source) and show the user before embedding. `apply_plan.py` re-encodes every cover to a
clean JPEG (RGB, EXIF stripped) automatically.

## Phase 6 — Apply (reversible)

Dry run first: `python "$SKILL/scripts/apply_plan.py" --plan "<ROOT>/.music-tagger/plan.json" --dry-run`
Then, after approval, run a **pilot** on 1 simple album + 1 box (temporarily trim plan.json or
keep a small pilot plan), show before/after tags and file layout, and only then apply the full
plan (the backup is written automatically to `<ROOT>/.music-tagger/tags_backup_*.json`).

## Phase 7 — Verify & report

Re-run `analyze.py` and confirm names/years/discs/covers are consistent. Tell the user:
- to trigger a **full rescan** in their server,
- optional Navidrome tip: set `CoverArtPriority = cover.*, front.*, embedded` if they want the
  folder cover.jpg to win over embedded art for album thumbnails.

## Restore

`python "$SKILL/scripts/apply_plan.py" --restore "<ROOT>/.music-tagger/tags_backup_<ts>.json"`
reverts text tags and removes tool-added artwork. (Copied/moved image files and `cover.jpg`
are logged but not auto-deleted — remove them manually if needed. Prefer
`move_images_mode: "copy"` unless the user explicitly wants files moved.)
