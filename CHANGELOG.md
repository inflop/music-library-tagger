# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- `--restore` now puts back the embedded cover art each file originally had. Previously the
  backup recorded only a `had_apic` flag and never the image bytes, so restoring stripped
  the user's own artwork along with the tool's — irreversibly.
- `--restore` no longer drops frames it had itself backed up. It rebuilt tags from a
  hand-kept map of eight frame classes, silently discarding everything else (`TCOM`,
  `TPUB`, `TOPE`, `TXXX`—); it now uses mutagen's own frame registry.
- `--dry-run` reports the cover work it would do. Cover processing was skipped wholesale in
  dry mode, so the preview always claimed `covers_embedded: 0`, hiding the one step that
  overwrites existing artwork.

- `--restore` no longer wipes frames it never backed up. It rebuilt each tag from an empty
  `ID3()`, so `POPM` ratings and `UFID` identifiers — which `apply` never touches — were
  destroyed by undoing a run. It now replaces only text frames and artwork in the tag that
  is already on disk.
- `--restore` keeps `TXXX` and `COMM` descriptors that contain a colon. Their mutagen keys
  are `TXXX:<desc>` and `COMM:<desc>:<lang>`, and splitting on the first colon truncated the
  descriptor — or, for a comment, shifted part of the description into the language field,
  which made the frame fail to rebuild and vanish entirely.
- Backup and restore skip non-text frames instead of mangling them. `USLT.text` is a string,
  so iterating it stored lyrics as a list of single characters; restoring that produced a
  lyrics frame reading `['w', 'e', 'r', ...]` with its language lost.

### Security
- `--restore` refuses paths from a backup file that resolve outside the backup folder or the
  recorded root. An absolute or `..`-prefixed path could previously make it read an
  arbitrary local file and embed it as cover art.

### Added
- `tests/` — stdlib `unittest` round-trip suite (apply/restore fidelity, artwork
  deduplication, dry-run parity, non-text frame preservation, path containment), run in CI
  along with the runtime dependencies.

## [1.0.0] - 2026-08-30

First public release.

### Added
- Seven-phase skill workflow: analyze → decide → verify online → plan → covers → apply → verify.
- `analyze.py` — read-only library scan: album/disc detection (incl. `CD1`/`Disc 2`/`Vol. II`
  subfolders), current tags, distinct `COMM`/`TENC` frames, cover pixel dimensions.
- `apply_plan.py` — applies `plan.json`, with dry run, automatic full text-tag backup and
  `--restore`.
- `fetch_cover.py` — Cover Art Archive front-cover fetcher with MusicBrainz release-group
  matching, rate limiting and 503 back-off.
- `references/conventions.md` — domain knowledge on album grouping, year conventions,
  cover quality, title styling and MusicBrainz usage.
- Packaged as a Claude Code plugin with its own marketplace manifest.

### Changed
- The MusicBrainz/Cover Art Archive User-Agent now identifies the application and links to
  the project repository, as the MusicBrainz API terms require, while still carrying no
  personal data.
