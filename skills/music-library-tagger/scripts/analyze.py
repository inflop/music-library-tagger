# -*- coding: utf-8 -*-
"""
Read-only analysis of a music library folder (one artist / band).

Detects albums (including multi-disc sets), dumps current ID3 tags, finds all
distinct comment/encoder frames, and measures the pixel dimensions of every
candidate cover image so the caller can judge quality.

Usage:
    python analyze.py "<ROOT>" [--json <out.json>]

Prints a human-readable report to stdout and (optionally) a machine-readable
JSON summary that the agent uses to build the change plan.

Dependencies: mutagen (required), Pillow (optional, for image dimensions).
"""
import os, sys, io, re, json, argparse
from collections import defaultdict

try:
    from mutagen import File as MFile
except Exception:
    sys.stderr.write("ERROR: mutagen not installed. Run: python -m pip install mutagen\n")
    raise

try:
    from PIL import Image
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")
AUDIO_EXT = (".mp3",)  # extend here if needed (.flac etc. would need different tag handling)

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


def txt(tags, key):
    if tags is None:
        return ""
    fr = tags.get(key)
    if fr is None:
        return ""
    try:
        return "; ".join(str(x) for x in fr.text)
    except Exception:
        return str(fr)


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
    for dp, dns, fns in os.walk(root):
        if os.sep + "." in dp or "/." in dp.replace("\\", "/"):
            # skip dotfolders like .music-tagger / .claude
            parts = os.path.relpath(dp, root).split(os.sep)
            if any(p.startswith(".") for p in parts):
                continue
        if any(f.lower().endswith(AUDIO_EXT) for f in fns):
            disc_folders.append(dp)

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
            mp3s = sorted(f for f in os.listdir(df) if f.lower().endswith(AUDIO_EXT))
            tracks = []
            for fn in mp3s:
                total_tracks += 1
                try:
                    a = MFile(os.path.join(df, fn)); tg = a.tags
                except Exception:
                    tracks.append({"file": fn, "error": True}); continue
                apic = 0
                if tg is not None:
                    for k in tg.keys():
                        base = k.split(":")[0]
                        all_frames[base] += 1
                        if base == "APIC":
                            apic += 1
                        if base == "COMM":
                            all_comments[txt(tg, k)].append(rel_album)
                album_talb.add(txt(tg, "TALB")); album_artist.add(txt(tg, "TPE1"))
                album_aa.add(txt(tg, "TPE2")); album_genre.add(txt(tg, "TCON"))
                album_year.add(txt(tg, "TDRC") or txt(tg, "TYER"))
                tracks.append({
                    "file": fn,
                    "title": txt(tg, "TIT2"),
                    "track": txt(tg, "TRCK"),
                    "disc": txt(tg, "TPOS"),
                    "has_cover": apic > 0,
                })
            disc_objs.append({
                "path": os.path.relpath(df, root).replace("\\", "/"),
                "name": os.path.basename(df),
                "disc_guess": disc_number_from_name(os.path.basename(df)),
                "n_tracks": len(mp3s),
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
        report.append("  TALB: %s" % album_obj["current_album_names"])
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
    report.append("FRAME TYPES PRESENT: %s" % dict(sorted(all_frames.items(), key=lambda x: -x[1])))
    report.append("\nDISTINCT COMMENT (COMM) VALUES:")
    for val, folders in sorted(all_comments.items(), key=lambda x: -len(x[1])):
        report.append("  %r  -> %d files, e.g. %s" % (val, len(folders), sorted(set(folders))[:3]))

    print("\n".join(report))

    if args.json:
        jdir = os.path.dirname(os.path.abspath(args.json))
        if jdir:
            os.makedirs(jdir, exist_ok=True)
        out = {
            "root": root,
            "n_albums": len(result_albums),
            "n_tracks": total_tracks,
            "frame_types": dict(all_frames),
            "comments": {k: len(v) for k, v in all_comments.items()},
            "albums": result_albums,
        }
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        sys.stderr.write("\nJSON written to %s\n" % args.json)


if __name__ == "__main__":
    main()
