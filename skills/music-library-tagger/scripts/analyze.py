# -*- coding: utf-8 -*-
"""
Read-only analysis of a music library folder (one artist / band).

Detects albums (including multi-disc sets), dumps current tags (ID3 for MP3,
Vorbis comments for FLAC), finds all distinct comment/encoder fields, and
measures the pixel dimensions of every candidate cover image so the caller can
judge quality. Audio in other formats is not read; it is listed as skipped.

Usage:
    python analyze.py "<ROOT>" [--json <out.json>]

Prints a human-readable report to stdout and (optionally) a machine-readable
JSON summary that the agent uses to build the change plan.

Dependencies: mutagen (required), Pillow (optional, for image dimensions).
"""
import os, sys, io, re, json, argparse
from collections import defaultdict

# Not every launcher (runpy.run_path, for one) puts the script's folder on sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import mutagen  # noqa: F401  (the backends need it)
except Exception:
    sys.stderr.write("ERROR: mutagen not installed. Run: python -m pip install mutagen\n")
    raise

import tagio

try:
    from PIL import Image
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")
AUDIO_EXT = tagio.AUDIO_EXT  # formats whose tags can be read and written
SKIPPED_AUDIO_EXT = tagio.SKIPPED_AUDIO_EXT  # recognised, never touched, only reported

# A disc subfolder name must START with a disc token (CD1, CD 1, Disc 2, DVD 1,
# Vol. I, Volume 2, CD One …). Anchoring at the start avoids false matches inside
# catalog numbers ("EGCD 2", "SANCD-155") or title words ("Three Of A Perfect Pair").
ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8,
         "ix": 9, "x": 10}
WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
           "seven": 7, "eight": 8, "nine": 9, "ten": 10}
DISC_ANCHOR = re.compile(
    r"(?i)^\s*(?:cd|dis[ck]|dvd|vol(?:ume)?)\s*[-_.#]?\s*"
    r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten|[ivx]+)\b")


def disc_number_from_name(name):
    m = DISC_ANCHOR.match(name)
    if not m:
        return None
    tok = m.group(1).lower()
    if tok.isdigit():
        return int(tok)
    if tok in WORDNUM:
        return WORDNUM[tok]
    if tok in ROMAN:
        return ROMAN[tok]
    return None


def is_disc_folder_name(name):
    return DISC_ANCHOR.match(name) is not None


def list_images(folder):
    out = []
    try:
        for f in sorted(os.listdir(folder)):
            p = os.path.join(folder, f)
            if os.path.isfile(p) and f.lower().endswith(IMG_EXT):
                out.append(f)
    except Exception:
        pass
    return out


def img_dims(path):
    if not HAVE_PIL:
        return None
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:
        return None


def cover_dirs(folder):
    """Folders that may hold artwork for a disc/album folder (deduped by real path;
    filesystems like NTFS are case-insensitive so 'covers'/'Covers' are the same dir)."""
    dirs = []
    seen = set()
    for cand in (".", "covers", "Covers", "COVERS", "Scans", "scans", "Cover", "cover", "Artwork", "artwork"):
        p = folder if cand == "." else os.path.join(folder, cand)
        if os.path.isdir(p):
            key = os.path.normcase(os.path.realpath(p))
            if key not in seen:
                seen.add(key)
                dirs.append(p)
    return dirs


def gather_covers(folder, root):
    items = []
    for d in cover_dirs(folder):
        for f in list_images(d):
            p = os.path.join(d, f)
            w = img_dims(p)
            items.append({
                "file": os.path.relpath(p, root).replace("\\", "/"),
                "name": f,
                "kb": os.path.getsize(p) // 1024,
                "w": w[0] if w else None,
                "h": w[1] if w else None,
            })
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    root = os.path.abspath(args.root)

    # 1) find every folder that directly contains audio files ("disc folders")
    disc_folders = []
    skipped = defaultdict(lambda: {"count": 0, "folders": set()})
    for dp, dns, fns in os.walk(root):
        # Do not descend into hidden folders such as .music-tagger or .git.
        dns[:] = [name for name in dns if not name.startswith(".")]
        if any(f.lower().endswith(AUDIO_EXT) for f in fns):
            disc_folders.append(dp)
        for f in fns:
            ext = os.path.splitext(f)[1].lower()
            if ext in SKIPPED_AUDIO_EXT:
                skipped[ext]["count"] += 1
                skipped[ext]["folders"].add(
                    os.path.relpath(dp, root).replace("\\", "/"))
    skipped_audio = {ext: {"count": v["count"], "folders": sorted(v["folders"])}
                     for ext, v in sorted(skipped.items())}

    # 2) group disc folders into albums
    #    album key = parent folder if this disc folder is named like a disc, else itself
    albums = defaultdict(list)
    for df in disc_folders:
        name = os.path.basename(df)
        parent = os.path.dirname(df)
        if is_disc_folder_name(name) and os.path.abspath(parent) != os.path.abspath(root):
            albums[parent].append(df)
        else:
            albums[df].append(df)

    all_comments = defaultdict(list)
    all_frames = defaultdict(int)
    formats = defaultdict(int)
    total_tracks = 0

    report = []
    result_albums = []

    for album_path in sorted(albums.keys()):
        discs = sorted(albums[album_path], key=lambda p: (disc_number_from_name(os.path.basename(p)) or 0, p))
        multi = len(discs) > 1 or (len(discs) == 1 and is_disc_folder_name(os.path.basename(discs[0])))
        rel_album = os.path.relpath(album_path, root).replace("\\", "/")

        disc_objs = []
        album_talb = set(); album_year = set(); album_genre = set(); album_artist = set(); album_aa = set()
        for df in discs:
            files = sorted(f for f in os.listdir(df) if f.lower().endswith(AUDIO_EXT))
            tracks = []
            for fn in files:
                total_tracks += 1
                backend = tagio.backend_for(fn)
                fmt = backend.NAME
                formats[fmt] += 1
                path = os.path.join(df, fn)
                try:
                    s = backend.read_summary(path)
                except Exception:
                    tracks.append({"file": fn, "format": fmt, "error": True}); continue
                for field in s["fields"]:
                    all_frames[field] += 1
                for comment in s["comments"]:
                    all_comments[comment].append(rel_album)
                album_talb.add(s["album"]); album_artist.add(s["artist"])
                album_aa.add(s["album_artist"]); album_genre.add(s["genre"])
                album_year.add(s["year"])
                tracks.append({
                    "file": fn,
                    "format": fmt,
                    "title": s["title"],
                    "track": s["track"],
                    "disc": s["disc"],
                    "has_cover": s["n_pictures"] > 0,
                })
            disc_objs.append({
                "path": os.path.relpath(df, root).replace("\\", "/"),
                "name": os.path.basename(df),
                "disc_guess": disc_number_from_name(os.path.basename(df)),
                "n_tracks": len(files),
                "covers": gather_covers(df, root),
                "tracks": tracks,
            })

        album_obj = {
            "album_path": rel_album,
            "multi_disc": multi,
            "n_discs": len(discs),
            "current_album_names": sorted(x for x in album_talb if x),
            "current_years": sorted(x for x in album_year if x),
            "current_genres": sorted(x for x in album_genre if x),
            "current_artists": sorted(x for x in album_artist if x),
            "current_album_artists": sorted(x for x in album_aa if x),
            "album_level_covers": gather_covers(album_path, root) if multi else [],
            "discs": disc_objs,
        }
        result_albums.append(album_obj)

        # human report
        report.append("=" * 100)
        report.append("ALBUM: %s   %s" % (rel_album, "[MULTI-DISC x%d]" % len(discs) if multi else ""))
        report.append("  album tag: %s" % album_obj["current_album_names"])
        report.append("  year: %s  genre: %s  artist: %s  albumartist: %s"
                      % (album_obj["current_years"], album_obj["current_genres"],
                         album_obj["current_artists"], album_obj["current_album_artists"]))
        for d in disc_objs:
            best = max(d["covers"], key=lambda c: (c["w"] or 0) * (c["h"] or 0), default=None)
            bstr = ""
            if best:
                bstr = "  best-cover=%s (%sx%s)" % (best["name"], best["w"], best["h"])
            embedded = sum(1 for t in d["tracks"] if t.get("has_cover"))
            report.append("   disc '%s' guess#=%s  tracks=%d  embedded_covers=%d%s"
                          % (d["name"], d["disc_guess"], d["n_tracks"], embedded, bstr))

    report.append("\n" + "=" * 100)
    report.append("SUMMARY: %d albums, %d tracks total" % (len(result_albums), total_tracks))
    report.append("FORMATS: %s" % dict(sorted(formats.items())))
    report.append("TAG FIELDS PRESENT (ID3 frames / Vorbis fields): %s"
                  % dict(sorted(all_frames.items(), key=lambda x: -x[1])))
    report.append("\nDISTINCT COMMENT VALUES (ID3 COMM / Vorbis COMMENT):")
    for val, folders in sorted(all_comments.items(), key=lambda x: -len(x[1])):
        report.append("  %r  -> %d files, e.g. %s" % (val, len(folders), sorted(set(folders))[:3]))
    if skipped_audio:
        report.append("\nSKIPPED AUDIO (format not supported, left untouched):")
        for ext, info in skipped_audio.items():
            report.append("  %s: %d files in %d folders, e.g. %s"
                          % (ext, info["count"], len(info["folders"]), info["folders"][:3]))

    print("\n".join(report))

    if args.json:
        jdir = os.path.dirname(os.path.abspath(args.json))
        if jdir:
            os.makedirs(jdir, exist_ok=True)
        out = {
            "root": root,
            "n_albums": len(result_albums),
            "n_tracks": total_tracks,
            "formats": dict(formats),
            "frame_types": dict(all_frames),
            "comments": {k: len(v) for k, v in all_comments.items()},
            "skipped_audio": skipped_audio,
            "albums": result_albums,
        }
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        sys.stderr.write("\nJSON written to %s\n" % args.json)


if __name__ == "__main__":
    main()
