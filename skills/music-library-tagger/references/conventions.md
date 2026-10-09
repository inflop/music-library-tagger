# Conventions & domain knowledge

Read before building a plan. These encode the decisions this skill was designed around.

## Privacy (hard rule)

- Never write identifying data — real name, email, username, machine paths — into ID3
  tags, Vorbis comments, `cover.jpg`, filenames, or image metadata (EXIF).
- Only objective release data goes into tags: album, artist, title, year, track/disc
  numbers, genre, cover.
- Strip environment-revealing frames: `TENC` (encoder), `COMM` (comments), and by default
  also consider `TXXX`, `PRIV`, `TSSE`, `WXXX` if present and non-musical (ask first if a
  `COMM`/`TXXX` might carry real liner notes).
- Web requests (MusicBrainz / Cover Art Archive) use a User-Agent that identifies the
  tool and links to its public repository -- required by the MusicBrainz API terms --
  and carries **no personal data**. `fetch_cover.py` already does this.
- Embedded covers and `cover.jpg` are re-encoded (Pillow), which drops any EXIF/author
  fields from the source image.

## How music servers group albums (Navidrome-centric)

- Tracks are grouped into an album by **tags**, not folders: same **Album** (TALB) + same
  **Album Artist** (TPE2). Set `TPE2` explicitly (= band name) for stable grouping; if empty,
  servers fall back to TPE1 and inconsistent casing can split albums.
- **Multi-disc**: all discs must share the **same Album title** and carry **disc numbers**
  (`TPOS` = `1/3`, `2/3`, `3/3`). Disc-per-subfolder is fine; grouping is by tags. Without a
  unified title + TPOS, each disc becomes its own album.
- **Multiple editions** (Original / 30th / 40th anniversary / remaster): give each a distinct
  Album title (append the edition) OR rely on differing years — distinct titles are safest to
  avoid merges/duplicates. Confirm with the user.
- **Track numbers** (`TRCK`) may be `n` or `n/total`; servers parse either. Normalize.
- **Cover art priority**: servers look at embedded art and/or a folder image
  (`cover.*`, `front.*`, `folder.*`). Embedding + a folder `cover.jpg` is the most portable.
  Navidrome's `CoverArtPriority` controls precedence.

## FLAC and Ogg Vorbis / Opus (Vorbis comments)

FLAC and Ogg files carry Vorbis comments instead of ID3 frames. The plan stays the same; the tool maps
fields as follows when it writes:

| Concept | MP3 (ID3) | FLAC (Vorbis) |
|---|---|---|
| Album / Title / Artist / Genre | `TALB` / `TIT2` / `TPE1` / `TCON` | `ALBUM` / `TITLE` / `ARTIST` / `GENRE` |
| Album artist | `TPE2` | `ALBUMARTIST` |
| Year | `TDRC` | `DATE` (a stale `YEAR` is removed) |
| Track | `TRCK` = `n/total` | `TRACKNUMBER` + `TOTALTRACKS` (stale `TRACKTOTAL` removed) |
| Disc | `TPOS` = `n/total` | `DISCNUMBER` + `TOTALDISCS` (stale `DISCTOTAL` removed) |
| Cover | `APIC` | FLAC: one `Picture` block, type 3 (front). Ogg/Opus: a base64 `METADATA_BLOCK_PICTURE` comment holding the same structure (a legacy `COVERART` comment is dropped when a new cover is written) |

- Track and disc numbers are **two fields** in FLAC, not `n/total` in one. The analysis shows
  them joined as `n/total` for both formats, so the same checks apply.
- Vorbis fields can repeat natively (several `ARTIST` values), and the ID3v2.3 "/" joining
  caveat does not apply. `options.id3_version` is ignored for FLAC and Ogg.
- Comments/encoder junk: `COMM` in `strip_frames` removes `COMMENT` and `DESCRIPTION`, `TENC`
  removes `ENCODEDBY`/`ENCODER`. Any other entry is taken as a Vorbis field name, so rippers'
  custom fields can be named directly (`strip_frames: ["REPLAYGAIN_ALBUM_GAIN"]`).
- A folder may mix MP3, FLAC and Ogg; all are tagged with the same album/year/disc values so the
  server groups them as one album.
- Everything else in the file (FLAC seek table, audio frames, other metadata blocks; Ogg audio pages,
  `R128_*` gain tags) is untouched.
- Ogg Speex, Ogg FLAC and Theora files share the `.ogg` / `.oga` extensions but are not
  handled: the analysis lists them as unreadable and they are left alone.

## Year conventions (decide with the user)

- **Reissue / anniversary editions**: the edition's real release year (e.g. a 40th
  Anniversary Edition = 2009/2010), OR the original album year — pick one convention and
  apply consistently.
- **Live albums / archival releases**: distinguish **recording year** from **release year**.
  Existing tags often carry the recording year by mistake. Prefer the release year for the
  Year field unless the user wants otherwise, and flag each correction.
- **Compilations / box sets**: the compilation's release year; keep per-disc era subtitles in
  the disc subtitle frame `TSST` if desired.

## Cover art quality

- KB size ≠ quality. Check **pixel dimensions**. A good album thumbnail is a **square front**
  ≥ ~1000×1000.
- Many ripped "front" scans are actually **2:1 gatefold/digipak spreads** (front+back in one
  image) or CD-face art — not a clean square cover. A proper square front from Cover Art
  Archive is usually a better album thumbnail even if the local file has more pixels.
- Source order: **Cover Art Archive (via MusicBrainz)** first (`fetch_cover.py`), local scan
  as fallback or when it is a genuinely better square front.
- For multi-disc sets, if the shared `covers/` folder contains per-disc images
  (`Disc 1`, `CD One`, `front CD2`…), place each into its disc subfolder as that disc's
  `cover.jpg` and embed it into that disc's tracks; keep the whole-album front as the album's
  `cover.jpg`.

## Title styling

- Canonical styling matches official releases: lowercase articles/prepositions/conjunctions
  (`in, of, the, and, to, with, a, an, for, from`) unless first/last word — e.g.
  `In the Court of the Crimson King`, not `In The Court Of The Crimson King`.
- Fix glitches: collapse double spaces, replace stray `\` with `/` or `:`, remove catalog
  junk from album titles (`[EGCD 51]`, `.Black Triangle`, `(2003) [FLAC]`, `(Disc 1)`).

## MusicBrainz / Cover Art Archive notes

- MusicBrainz rate limit ≈ 1 request/second; `fetch_cover.py` sleeps and retries on 503.
- Pick the release-group whose primary-type is `Album`/`EP` and whose title/year best match;
  the raw top search hit is sometimes a single or a film.
- Discogs blocks automated fetches (HTTP 403) — use it via web *search results* for facts,
  not `WebFetch`.
