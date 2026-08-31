# Security & privacy

## What this plugin does on your machine

- **Reads** MP3 files and image files under the folder you point it at.
- **Writes** ID3 tags, embedded artwork and `cover.jpg` files in that same folder, plus a
  working directory `.music-tagger/` (analysis, plan, tag backups, downloaded covers).
- **Never** re-encodes or rewrites the audio stream.
- **Never** deletes audio files. Image files are copied by default (`move_images_mode:
  "copy"`); moving happens only if explicitly configured.

## Network access

Two hosts, both unauthenticated public APIs, both read-only:

| Host | Purpose |
|---|---|
| `musicbrainz.org` | release-group lookup (album title, year, track list) |
| `coverartarchive.org` | front cover images |

Requests are rate-limited to roughly one per second and send a User-Agent of the form
`MusicLibraryTagger/<version> ( <project URL> )` — the project URL, never your identity.
No API keys, no accounts, no telemetry, no analytics, nothing is uploaded.

## Privacy guarantees

The skill's hard rule is that **no identifying data is written anywhere**:

- No real name, e-mail, username or machine path in ID3 tags, filenames or `cover.jpg`.
- Environment-revealing frames (`TENC` encoder, `COMM` comments, and optionally `TXXX`,
  `PRIV`, `TSSE`, `WXXX`) are offered for stripping.
- Cover images are re-encoded through Pillow, which drops source EXIF (which can carry a
  scanner owner, GPS or author fields).

## Reversibility

`apply_plan.py` writes `.music-tagger/tags_backup_<timestamp>.json` containing every text
frame of every file it is about to touch, **before** the first write. Non-text frames
(`POPM` ratings, `UFID` identifiers, `USLT` lyrics) are not backed up because nothing
writes them: a restore leaves them exactly as they are. Embedded cover art is
copied out alongside it into `tags_backup_<timestamp>_art/` (deduplicated by SHA-1, since
one cover is normally repeated across every track of an album). Restore with:

```bash
python apply_plan.py --restore .music-tagger/tags_backup_<timestamp>.json
```

This puts back the text tags **and the artwork each file originally carried**, replacing
any cover the tool embedded. Copied image files and `cover.jpg` are logged but not
auto-deleted. A backup folder is only useful together with its `_art` sidecar — move or
delete them as a pair.

## Reporting a vulnerability

Open a GitHub issue for anything non-sensitive. For something you would rather not post
publicly, use GitHub's **Security → Report a vulnerability** private advisory form on this
repository.
