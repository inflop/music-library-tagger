# Release & publishing process

How a version of this plugin gets cut and listed. Day-to-day contributors don't need this.

## Versioning

`plugin.json` → `version`, the marketplace entry's `version`, the `VERSION` constant in
`skills/music-library-tagger/scripts/fetch_cover.py` (it goes into the MusicBrainz
User-Agent) and `CHANGELOG.md` all move together. Semantic versioning:

- **patch** — bug fix in a script, wording fix in the skill
- **minor** — new capability, new question, new script
- **major** — a change that would surprise an existing user (different default convention,
  different plan.json schema)

## Cutting a release

```bash
python -m compileall -q skills/
claude plugin validate . --strict
git tag -a v1.0.0 -m "v1.0.0"
git push origin main --tags
```

Then publish a GitHub Release for the tag with the changelog section as its body.
Directories that pin `ref` resolve it to a commit SHA, so a tag must never be moved.

## Where this is listed

| Directory | Mechanism | Update path |
|---|---|---|
| This repo | `.claude-plugin/marketplace.json` | automatic on push |
| [claude-plugins-official](https://github.com/anthropics/claude-plugins-official) | Anthropic-managed entry pinned to a tag | re-submit / open an issue to bump the `ref` |
| awesome-claude-skills lists | a line in a Markdown table | PR |
| Third-party skill indexes | scraped from this repo | nothing to do |

## Names are immutable

`plugin.json`'s `name` (`music-library-tagger`) is a permanent slug once a directory lists
it — renaming breaks every existing install. Change `displayName` instead. A genuinely
unavoidable rename needs a `renames` entry in `.claude-plugin/marketplace.json`:

```json
"renames": { "old-name": "music-library-tagger" }
```
