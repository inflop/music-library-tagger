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

### Added
- `tests/` — stdlib `unittest` round-trip suite (apply/restore fidelity, artwork
  deduplication, dry-run parity), run in CI along with the runtime dependencies.

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
