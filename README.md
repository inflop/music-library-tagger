# Music Library Tagger

> A Claude Code **Agent Skill** (packaged as a plugin) that turns a messy single-artist
> album folder into a clean, consistent library that Navidrome, Plex, Jellyfin or any
> Subsonic server displays correctly — correct album titles, years, track titles,
> genres, multi-disc numbering and cover art, verified against MusicBrainz.

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Claude Code Plugin](https://img.shields.io/badge/Claude%20Code-plugin-6b5bd6)](https://code.claude.com/docs/en/plugins)
[![Agent Skill](https://img.shields.io/badge/Agent-Skill-orange)](https://code.claude.com/docs/en/skills)

---

## Install

**As a plugin (recommended)**

```
/plugin marketplace add inflop/music-library-tagger
/plugin install music-library-tagger@music-tools
```

**As a plain skill** — copy the skill folder into your skills directory:

```bash
git clone https://github.com/inflop/music-library-tagger
cp -r music-library-tagger/skills/music-library-tagger ~/.claude/skills/
```

**Dependencies** (Python 3.9+):

```bash
python -m pip install mutagen Pillow
```

## Use

Open Claude Code **inside a folder that holds one band's albums** and just say what you
want:

```
fix the tags in this folder for Navidrome
```

Other phrasings that trigger the skill: *"organize these albums for Plex/Jellyfin"*,
*"add cover art"*, *"fix the multi-disc albums"*, *"clean up my ID3 tags"*.

Expected input layout (anything roughly like this works — the skill detects multi-disc
sets from `CD1`/`Disc 2`/`Vol. II` subfolders):

```
G:/Music/King Crimson/
├── 1974 - Red/
│   └── 01 - Red.mp3
└── 1969 - In The Court Of The Crimson King (40th Anniversary)/
    ├── CD 1/
    ├── CD 2/
    └── covers/
```

## What it does

| Area | Fixed |
|---|---|
| **Album titles** | Canonical spelling/casing, catalog junk (`[EGCD 51]`, `(2003) [FLAC]`, `(Disc 1)`) removed, editions labelled consistently |
| **Years** | Release vs. recording year for live/archival albums, real reissue years, typo repair (`1082` → `1982`) |
| **Track titles** | Canonical titles, double spaces, stray backslashes, title case |
| **Multi-disc** | One shared album title + proper `TPOS` disc numbers (`1/3`, `2/3`, `3/3`) so servers stop splitting box sets into three albums |
| **Album artist** | Explicit `TPE2` so albums group reliably |
| **Genre** | Unified across the collection (or left alone — your call) |
| **Cover art** | Best square front from the Cover Art Archive or your local scans, embedded **and** written as `cover.jpg`, per-disc art distributed to disc folders |
| **Junk frames** | `COMM` / `TENC` ripper leftovers stripped (after showing you what they contain) |

## How it works

1. **Analyze** — read-only scan: albums, discs, current tags, distinct comment/encoder
   frames, cover pixel dimensions.
2. **Decide** — a short set of questions (edition naming, year convention, cover source,
   genre, comments, title case).
3. **Verify** — album titles, years and track lists checked against public sources
   (MusicBrainz, Cover Art Archive, discography info) rather than trusting your tags.
4. **Plan** — a human-readable `PLAN-TAGI.md` plus a machine-readable `plan.json`.
   **Nothing is written until you approve it.**
5. **Apply** — dry run, then a 2-album pilot, then the full run.
6. **Verify & report** — re-scan and tell you to trigger a server rescan.

## Safety

- **The audio stream is never touched** — only ID3 tags and artwork.
- **Every run backs up all text tags first** to `.music-tagger/tags_backup_<ts>.json`.
- **Reversible**: `python apply_plan.py --restore .music-tagger/tags_backup_<ts>.json`
- **Approval gate**: the plan is shown and must be accepted; a dry run and a pilot come first.
- **Privacy**: no personal data (name, e-mail, username, local paths) is ever written into
  tags, filenames or image metadata; covers are re-encoded so source EXIF is dropped.
  See [SECURITY.md](SECURITY.md).

## Repository layout

```
.claude-plugin/
  plugin.json                 # plugin manifest
  marketplace.json            # lets this repo act as its own marketplace
skills/music-library-tagger/
  SKILL.md                    # the skill (7-phase workflow)
  references/conventions.md   # domain knowledge: grouping, years, cover quality, styling
  scripts/analyze.py          # read-only library analysis -> JSON
  scripts/apply_plan.py       # applies plan.json, backs up + restores
  scripts/fetch_cover.py      # Cover Art Archive front-cover fetcher
docs/USAGE.md                 # full walkthrough
docs/PUBLISHING.md            # how this repo gets published to plugin directories
```

## Data sources

[MusicBrainz](https://musicbrainz.org/) and the [Cover Art Archive](https://coverartarchive.org/),
used through their public APIs at ~1 request/second with an identifying, non-personal
User-Agent. No account or API key needed. Cover images stay on your machine; their
licensing is whatever the Cover Art Archive states for that release.

## Contributing

Issues and PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) © Rafał Klepacz
