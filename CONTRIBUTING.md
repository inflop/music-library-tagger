# Contributing

Thanks for helping out. This repo is one Claude Code plugin containing one Agent Skill.

## Layout rules

- The skill lives in `skills/music-library-tagger/`. The folder name and the `name:` in
  `SKILL.md`'s frontmatter **must stay identical** — it is the invocation name.
- `plugin.json`'s `name` is an **immutable slug** once published to a directory. Change
  `displayName` instead if the branding changes.
- Helper scripts stay dependency-light: standard library plus `mutagen` and `Pillow`.
  Anything else needs a good reason.

## Before opening a PR

```bash
python -m compileall -q skills/music-library-tagger/scripts
python -c "import json;[json.load(open(p,encoding='utf-8')) for p in ['.claude-plugin/plugin.json','.claude-plugin/marketplace.json']]"
claude plugin validate . --strict     # if you have Claude Code installed
```

Test tag changes against a **copy** of a real album folder, never your library.

## Non-negotiables

Any change must preserve these, or it will not be merged:

1. The audio stream is never re-encoded or rewritten — tags and artwork only.
2. `apply_plan.py` writes a full text-tag backup before the first write.
3. No personal data (name, e-mail, username, machine paths) is ever written into tags,
   filenames or image metadata.
4. Network calls stay within MusicBrainz rate limits (~1 req/s) and send the identifying,
   non-personal User-Agent.
5. Nothing is applied without explicit user approval of the plan.

## Scope

In scope: MP3/ID3, FLAC, Ogg Vorbis/Opus (Vorbis comments) and M4A (MP4 atoms) correctness, cover art, multi-disc handling,
music-server compatibility. Each tag format is a backend module (`scripts/id3_tags.py`,
`scripts/flac_tags.py`, `scripts/ogg_tags.py`, `scripts/mp4_tags.py`; the Vorbis field mapping is shared in `vorbis_common.py`) registered in `scripts/tagio.py`, which also documents the interface.
`apply_plan.py` and `analyze.py` must stay free of format-specific tag names. To add a format:
one backend module, one line in `BACKENDS`, one fixture writer in `tests/audio_fixtures.py`
(`tests/test_backends.py` then runs the shared contract tests against it).

Out of scope (for now): other tag formats (WavPack, Monkey's Audio, WAV, ...) (they are reported as
skipped by `analyze.py`, never touched), transcoding, downloading music, library-wide
multi-artist runs. A new tag format needs its own backend module — open an issue first.
