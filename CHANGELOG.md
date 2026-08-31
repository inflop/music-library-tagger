# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
