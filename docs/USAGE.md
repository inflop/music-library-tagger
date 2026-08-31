# Usage walkthrough

## 0. Prepare

```bash
python -m pip install mutagen Pillow
```

Point Claude Code at a folder that contains **one artist's** albums. Multi-artist roots are
not supported — run it once per band.

Work on a copy the first time if you want to be certain.

## 1. Start

```
fix the tags in this folder for Navidrome
```

The skill runs `analyze.py` read-only and reports what is wrong: inconsistent album names,
wrong or missing years, box sets with no disc numbers, missing covers, genre spread, junk
comments, title glitches.

## 2. Answer the questions

You will be asked roughly six things, adapted to what was actually found:

| Question | Typical answer |
|---|---|
| Multiple editions of one album | Append the edition to the title (keeps them separate in the server) |
| Year field | Edition's real release year, or always the original album year |
| Cover source | Cover Art Archive first, local scans as fallback |
| Genre | One value for the whole artist, or leave as-is |
| `COMM`/`TENC` frames | Strip (they are usually ripper junk — you see the values first) |
| Title case | Canonical (`In the Court of the Crimson King`) |

## 3. Review the plan

You get `PLAN-TAGI.md`: per-album target table (title, year, disc numbering, genre), cover
strategy, disc-art redistribution map, and `⚠` on every value that differs from what your
files currently say. Read it. Change anything you disagree with before approving.

## 4. Apply

```bash
python apply_plan.py --plan .music-tagger/plan.json --dry-run   # nothing written
python apply_plan.py --plan .music-tagger/plan.json             # pilot, then full run
```

A backup lands in `.music-tagger/tags_backup_<timestamp>.json` before the first write.

## 5. Rescan your server

Trigger a full rescan. Navidrome tip — if you want the folder `cover.jpg` to win over
embedded art for album thumbnails:

```
CoverArtPriority = cover.*, front.*, embedded
```

## Undo

```bash
python apply_plan.py --restore .music-tagger/tags_backup_<timestamp>.json
```

Text tags are reverted and tool-added artwork removed. Copied images and `cover.jpg` files
are listed in the log; delete them manually if you want a completely clean revert.

## Troubleshooting

**A box set still shows as three albums.** All discs need the *same* `TALB` and the *same*
`TPE2`, plus `TPOS` values. Re-run `analyze.py` and check those three frames.

**Two copies of the same album appear.** Different album titles or differing album artist
casing. Pick one edition-naming convention and re-run.

**Cover is a wide double image.** That is a gatefold spread, not a front. Let the skill pull
a square front from the Cover Art Archive instead.

**MusicBrainz returns nothing for an album.** Punctuation in the title can break exact
phrase matching; `fetch_cover.py` already retries loosely. Otherwise pass the cover path
manually in `plan.json`.
