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

In scope: MP3/ID3 correctness, cover art, multi-disc handling, music-server compatibility.

Out of scope (for now): FLAC/Vorbis/MP4 tag formats, transcoding, downloading music,
library-wide multi-artist runs. FLAC support is welcome as a PR but needs its own tag
abstraction — open an issue first.
