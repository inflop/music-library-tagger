# -*- coding: utf-8 -*-
"""
Apply a tagging plan (plan.json) to an MP3 library, or restore from a backup.

Always makes a full text-tag backup BEFORE writing, so changes are reversible.
Only ID3 tags and artwork are touched -- the audio stream is never re-encoded.

Usage:
    python apply_plan.py --plan plan.json [--dry-run]
    python apply_plan.py --restore <backup.json>

plan.json schema (all paths are RELATIVE to "root", forward slashes ok):
{
  "root": "G:/Music/Some Band",
  "options": {
     "artist": "Some Band",              # -> TPE1 on every track (optional)
     "album_artist": "Some Band",        # -> TPE2 on every track (optional)
     "genre": "Progressive Rock",        # -> TCON default (optional)
     "strip_frames": ["COMM", "TENC"],   # frame base names to delete
     "id3_version": 3,                   # save as ID3v2.3 (widest compatibility)
     "cover_embed": true,
     "cover_folder_jpg": true,
     "cover_max_px": 1400,               # downscale embedded/cover.jpg if larger
     "move_images_mode": "copy"          # "copy" (safe) or "move"
  },
  "albums": [
    {
      "album": "Red",
      "year": 1974,                       # TDRC
      "genre": "Progressive Rock",        # optional per-album override
      "album_path": "ORIGINAL/1974 - Red",# where album-level cover.jpg is written
      "album_cover": "ORIGINAL/1974 - Red/covers/Cover.jpg",  # optional
      "discs": [
        {
          "path": "ORIGINAL/1974 - Red",
          "disc": 1, "disc_total": 1,     # omit / null for single-disc (no TPOS)
          "cover": "ORIGINAL/1974 - Red/covers/Cover.jpg",     # embedded + cover.jpg here
          "move_images": [["…/covers/Disc 1.jpg", "…/CD 1"]],  # optional [src, dst_dir]
          "tracks": [
            {"file": "01 - Red.mp3", "track": 1, "track_total": 5, "title": "Red"}
          ]
        }
      ]
    }
  ]
}
"""
import os, sys, io, json, time, argparse, shutil

from mutagen.mp3 import MP3
from mutagen.id3 import (ID3, ID3NoHeaderError, TALB, TPE1, TPE2, TIT2, TCON,
                         TDRC, TRCK, TPOS, APIC)

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def log(msg):
    print(msg)


def rp(root, rel):
    if not rel:
        return None
    if os.path.isabs(rel):
        return rel
    return os.path.join(root, rel.replace("/", os.sep))


def load_id3(path):
    try:
        return ID3(path)
    except ID3NoHeaderError:
        return ID3()


def process_cover_bytes(img_path, max_px):
    """Return clean JPEG bytes (RGB, no EXIF) for embedding / cover.jpg."""
    from PIL import Image
    with Image.open(img_path) as im:
        im = im.convert("RGB")
        if max_px and max(im.size) > max_px:
            im.thumbnail((max_px, max_px), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=90, optimize=True)  # no exif passed => stripped
        return buf.getvalue()


# ------------------------------- backup / restore -------------------------------

def backup_tags(root, plan, backup_path):
    data = {"root": root, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "files": {}}
    for alb in plan["albums"]:
        for disc in alb["discs"]:
            dpath = rp(root, disc["path"])
            for tr in disc["tracks"]:
                fpath = os.path.join(dpath, tr["file"])
                if not os.path.isfile(fpath):
                    continue
                tags = load_id3(fpath)
                frames = {}
                had_apic = False
                for key in list(tags.keys()):
                    base = key.split(":")[0]
                    if base == "APIC":
                        had_apic = True
                        continue
                    fr = tags[key]
                    try:
                        frames[key] = [str(x) for x in fr.text]
                    except Exception:
                        pass
                data["files"][os.path.relpath(fpath, root).replace("\\", "/")] = {
                    "frames": frames, "had_apic": had_apic}
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    log("Backup of %d files -> %s" % (len(data["files"]), backup_path))


def restore(backup_path):
    with open(backup_path, encoding="utf-8") as f:
        data = json.load(f)
    root = data["root"]
    FRAME = {"TALB": TALB, "TPE1": TPE1, "TPE2": TPE2, "TIT2": TIT2, "TCON": TCON,
             "TDRC": TDRC, "TRCK": TRCK, "TPOS": TPOS}
    n = 0
    for rel, info in data["files"].items():
        fpath = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(fpath):
            continue
        tags = ID3()
        for key, vals in info["frames"].items():
            base = key.split(":")[0]
            cls = FRAME.get(base)
            if cls is None:
                continue
            try:
                tags.add(cls(encoding=3, text=vals))
            except Exception:
                pass
        tags.save(fpath, v2_version=3)
        n += 1
    log("Restored text tags on %d files (tool-added artwork removed; moved image files NOT reverted)." % n)


# ------------------------------- apply -------------------------------

def apply(plan, dry):
    root = os.path.abspath(rp(os.getcwd(), plan["root"]) if not os.path.isabs(plan["root"]) else plan["root"])
    opt = plan.get("options", {})
    strip = set(opt.get("strip_frames", []))
    id3v = opt.get("id3_version", 3)
    embed = opt.get("cover_embed", True)
    folder_jpg = opt.get("cover_folder_jpg", True)
    max_px = opt.get("cover_max_px", 1400)
    move_mode = opt.get("move_images_mode", "copy")
    def_artist = opt.get("artist")
    def_aa = opt.get("album_artist")
    def_genre = opt.get("genre")

    changes = {"tracks": 0, "covers_embedded": 0, "cover_jpgs": 0, "moved": 0}

    for alb in plan["albums"]:
        album = alb["album"]
        year = str(alb["year"]) if alb.get("year") is not None else None
        genre = alb.get("genre", def_genre)
        aa = alb.get("album_artist", def_aa)
        artist = alb.get("artist", def_artist)

        # pre-process album-level cover once
        album_cover_bytes = None
        if alb.get("album_cover"):
            src = rp(root, alb["album_cover"])
            if src and os.path.isfile(src):
                album_cover_bytes = None if dry else process_cover_bytes(src, max_px)

        for disc in alb["discs"]:
            dpath = rp(root, disc["path"])
            disc_no = disc.get("disc")
            disc_total = disc.get("disc_total")

            # disc cover bytes
            cover_bytes = None
            csrc = rp(root, disc["cover"]) if disc.get("cover") else None
            if csrc and os.path.isfile(csrc):
                cover_bytes = None if dry else process_cover_bytes(csrc, max_px)

            # relocate extra named scans into this disc folder
            for pair in disc.get("move_images", []):
                s, ddir = rp(root, pair[0]), rp(root, pair[1])
                if s and os.path.isfile(s) and ddir:
                    dst = os.path.join(ddir, os.path.basename(s))
                    log("  %s image: %s -> %s" % (move_mode, pair[0], pair[1]))
                    if not dry:
                        os.makedirs(ddir, exist_ok=True)
                        if move_mode == "move":
                            shutil.move(s, dst)
                        else:
                            shutil.copy2(s, dst)
                    changes["moved"] += 1

            for tr in disc["tracks"]:
                fpath = os.path.join(dpath, tr["file"])
                if not os.path.isfile(fpath):
                    log("  !! missing file: %s" % fpath); continue
                tags = load_id3(fpath)

                # strip unwanted frames
                for key in list(tags.keys()):
                    if key.split(":")[0] in strip:
                        del tags[key]

                def setf(cls, val):
                    if val is None or val == "":
                        return
                    tags.setall(cls.__name__, [cls(encoding=3, text=[str(val)])])

                setf(TALB, album)
                setf(TIT2, tr.get("title"))
                if artist: setf(TPE1, artist)
                if aa: setf(TPE2, aa)
                if year: setf(TDRC, year)
                if genre: setf(TCON, genre)
                if tr.get("track") is not None:
                    trck = "%s/%s" % (tr["track"], tr["track_total"]) if tr.get("track_total") else str(tr["track"])
                    setf(TRCK, trck)
                if disc_no:
                    pos = "%s/%s" % (disc_no, disc_total) if disc_total else str(disc_no)
                    setf(TPOS, pos)

                # embed cover
                if embed and cover_bytes is not None:
                    tags.delall("APIC")
                    tags.add(APIC(encoding=3, mime="image/jpeg", type=3,
                                  desc="Front", data=cover_bytes))
                    changes["covers_embedded"] += 1

                if not dry:
                    tags.save(fpath, v2_version=id3v)
                changes["tracks"] += 1

            # write disc-level cover.jpg
            if folder_jpg and cover_bytes is not None:
                out = os.path.join(dpath, "cover.jpg")
                if not dry:
                    with open(out, "wb") as f:
                        f.write(cover_bytes)
                changes["cover_jpgs"] += 1
                log("  cover.jpg -> %s" % os.path.relpath(out, root))

        # album-level cover.jpg (multi-disc parent)
        if folder_jpg and album_cover_bytes is not None and alb.get("album_path"):
            apath = rp(root, alb["album_path"])
            out = os.path.join(apath, "cover.jpg")
            if not dry:
                os.makedirs(apath, exist_ok=True)
                with open(out, "wb") as f:
                    f.write(album_cover_bytes)
            changes["cover_jpgs"] += 1
            log("  album cover.jpg -> %s" % os.path.relpath(out, root))

        log("Album done: %s" % album)

    log("\n%s SUMMARY: %s" % ("DRY-RUN" if dry else "APPLIED", json.dumps(changes)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--restore")
    ap.add_argument("--backup-dir", default=None,
                    help="where to write the pre-change backup (default: <root>/.music-tagger)")
    args = ap.parse_args()

    if args.restore:
        restore(args.restore); return

    if not args.plan:
        ap.error("--plan or --restore required")
    with open(args.plan, encoding="utf-8") as f:
        plan = json.load(f)
    root = os.path.abspath(plan["root"])
    plan["root"] = root

    if not args.dry_run:
        bdir = args.backup_dir or os.path.join(root, ".music-tagger")
        os.makedirs(bdir, exist_ok=True)
        bpath = os.path.join(bdir, "tags_backup_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
        backup_tags(root, plan, bpath)

    apply(plan, args.dry_run)


if __name__ == "__main__":
    main()
