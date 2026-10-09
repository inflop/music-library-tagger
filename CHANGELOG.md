# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `apply_plan.py --albums "Red,Discipline"` and `--limit N` apply (and back up) only part of a plan
  (#6). The documented pilot used to mean trimming `plan.json` by hand right before the first write,
  so the plan that was reviewed was not the plan that ran. The file is never modified; names match an
  album title or `album_path`, ignoring case; an unknown name is an error that lists the albums in
  the plan; and the flags are refused with `--restore` instead of being ignored. The filter works with
  `--dry-run`.

### Fixed
- `apply_plan.py --restore <backup> --dry-run` no longer performs a real restore (#4). `main()` returned
  as soon as it saw `--restore`, so the flag was accepted and ignored. A dry restore now changes
  nothing and reports how many files would be restored, how many artwork images reinstated and
  which entries would fail. It runs the restore on a temporary copy of each file, so it applies
  the same checks a real restore does, and it also opens the real file for update (writing
  nothing) so a target the process could not overwrite is reported rather than counted as restorable.
  Restore, dry or real, now also counts and reports every entry it skips (a path outside the
  backup root, an unsupported target, a file that no longer exists); the last two were skipped
  without being counted, and a missing file without a word.
- `fetch_cover.py` keeps at least 1.1 s between any two requests to MusicBrainz (#5). The strict
  query and its loose fallback ran back to back, which broke the ~1 request per second rule
  that `CONTRIBUTING.md` treats as non-negotiable. The wait now lives in `_get()`, so retries
  and the release lookup are covered too; the Cover Art Archive is not throttled.

## [1.1.0] - 2026-10-09

### Added
- FLAC support (#9). `analyze.py` reads FLAC Vorbis comments and embedded pictures next to
  MP3/ID3, and `apply_plan.py` writes, backs up and restores them. A folder may mix both
  formats. `plan.json` is unchanged; `id3_version` is ignored for FLAC and `strip_frames`
  accepts ID3 frame ids (`COMM`, `TENC`, ...) or Vorbis field names.
- `analyze.py` lists audio it cannot tag (for example `.wav`, `.wv`, `.ape`) under
  `skipped_audio` in the JSON and in a `SKIPPED AUDIO` section of the report, instead of
  silently leaving those albums out. The JSON also gains `formats` and a per-track `format`.

- Ogg Vorbis and Opus support (#13): `.ogg`, `.oga` and `.opus` are read, tagged, backed up and
  restored like FLAC, sharing its Vorbis field mapping. The cover is a base64
  `METADATA_BLOCK_PICTURE` comment; the backup keeps the image in its sidecar folder, not in the
  JSON. Speex, Ogg FLAC and Theora streams are left alone and shown as unreadable.

- M4A support (#14): AAC and Apple Lossless `.m4a` files are read, tagged, backed up and restored
  through MP4 atoms (`©alb`, `aART`, `trkn`/`disk` pairs, `covr`). Free-form atoms, integers and
  booleans are backed up with their value types and restored exactly; the audio data is unchanged and
  the chunk offsets (`stco` and `co64`) are rewritten to keep pointing at it. `.mp4` and `.m4b` stay reported as skipped.

- `apply_plan.py` skips a file its backend cannot read (a Speex stream named `.ogg`, a truncated or
  mislabelled file) in the backup, the dry run and the real run alike: it is reported as `cannot read`,
  counted as `unreadable` in the summary, never written, and no longer aborts the run. This includes a file whose
  content is another format than its extension says (a FLAC renamed `.mp3`), which the MP3 backend
  used to tag by prepending an ID3 header; restore refuses such a file too.

### Changed
- Tag formats are now backends behind one interface (#12): `scripts/id3_tags.py` and
  `scripts/flac_tags.py`, registered in `scripts/tagio.py`. `apply_plan.py` and `analyze.py` no
  longer contain format-specific code; the MP3 behaviour and backup layout are unchanged. The
  restore message for an unsupported target now reads "not a supported audio file".
- Backup entries for FLAC files carry `"format": "flac"` and a `vorbis` list of
  `[name, value]` pairs. Entries without `format` are read as ID3, so existing backups
  still restore.
- `analyze.py` labels the album line `album tag:` instead of `TALB:` and reports
  `TAG FIELDS PRESENT` for both ID3 frames and Vorbis fields.

## [1.0.1] - 2026-08-31

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

- `--restore` writes the tag back in the ID3v2 version the file had. It always saved v2.3,
  so restoring a v2.4 library silently downgraded it — and v2.3 cannot hold several
  values in one frame, so a multi-value artist came back joined with "/".

- `--restore` returns a file to the tag layout it started with. A file that had no ID3v2
  tag was left carrying an empty one plus its padding, and a file that had only ID3v1 came
  back with a v2 tag it never had.
- `--restore` no longer aborts on a damaged backup entry. A missing artwork path raised
  `KeyError` and a non-integer picture type raised `TypeError`, either of which stopped the
  run partway and left the library half reverted. Bad entries are now reported and stepped
  over, and a file that fails is counted in the summary.

### Security
- `--restore` refuses paths from a backup file that resolve outside the backup folder or the
  recorded root. An absolute or `..`-prefixed path could previously make it read an
  arbitrary local file and embed it as cover art. A target that is not an `.mp3` is refused
  outright, so a backup entry cannot make mutagen prepend an ID3 tag to some other file. Paths are resolved through symlinks, so a
  link planted inside the backup folder is not a way out either, and containment is tested
  with `os.path.commonpath`, which a base that is a filesystem or drive root does not break.

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
