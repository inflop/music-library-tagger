# -*- coding: utf-8 -*-
"""
Apply a tagging plan (plan.json) to an MP3 library, or restore from a backup.

Before writing, backs up every text frame and every embedded cover of the files
it is about to touch, so --restore can undo the run. Artwork is copied out to a
sidecar folder next to the backup JSON and re-embedded verbatim.

Frames that are not text frames (POPM ratings, UFID identifiers, USLT lyrics,
PRIV...) are neither backed up nor rewritten: apply() does not touch them and
restore() leaves them in place. The exception is a non-text frame named in
options.strip_frames -- deleting that is deliberate and cannot be undone.

A restore writes the tag back in the ID3v2 version the file had, as far as
mutagen can write one: v2.3 or v2.4. A v2.2 tag is read but cannot be written
back (apply() has already rewritten it as v2.3 by then), so it restores as v2.3.
A file that had no ID3v2 tag ends up with none again rather than an empty one,
and a file that had only ID3v1 keeps just its ID3v1.
Note that ID3v2.3 cannot store several values in one frame, so writing a v2.3
tag joins them with "/" -- a property of the format, not of the backup, which
keeps the values apart.
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
import os, sys, io, json, time, argparse, shutil, hashlib

from mutagen.mp3 import MP3
from mutagen.id3 import (ID3, ID3NoHeaderError, Frames, TextFrame, TALB, TPE1,
                         TPE2, TIT2, TCON, TDRC, TRCK, TPOS, APIC)

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def log(msg):
    print(msg)


def rp(root, rel):
    if not rel:
        return None
    if os.path.isabs(rel):
        return rel
    return os.path.join(root, rel.replace("/", os.sep))


ART_EXT = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
           "image/gif": ".gif", "image/webp": ".webp"}

# What analyze.py collects and apply() writes. A restore must not stray outside
# it: mutagen will happily prepend an ID3 tag to any file it is handed.
AUDIO_EXT = (".mp3",)


def within(base, rel):
    """Resolve `rel` under `base`, or return None if it escapes.

    Paths in a backup file are data, not instructions: an absolute path would
    make os.path.join discard `base` entirely, and `..` would walk out of it.
    Neither should be able to steer a restore at the rest of the filesystem.
    """
    if not rel:
        return None
    # realpath, not abspath: a symlink inside the folder must not be a way out.
    base = os.path.realpath(base)
    target = os.path.realpath(os.path.join(base, rel))
    n_base, n_target = os.path.normcase(base), os.path.normcase(target)
    try:
        # commonpath rather than a prefix test: a base that is a filesystem or
        # drive root already ends with a separator, and appending another would
        # match nothing.
        if os.path.commonpath([n_base, n_target]) != n_base:
            return None
    except ValueError:
        # Different drives, or a mix of absolute and relative paths.
        return None
    return target


def rebuild_frame(key, vals):
    """Recreate a text frame from its backup key and values.

    Uses mutagen's own frame registry rather than a hand-kept map, so frames
    like TCOM/TPUB/TOPE survive a restore instead of being silently dropped.
    """
    base, _, rest = key.partition(":")
    cls = Frames.get(base)
    # Only genuine text frames take a list of strings. USLT.text, for one, is a
    # plain string -- feeding it a list yields lyrics that read "['w', 'e', ...".
    if cls is None or not issubclass(cls, TextFrame):
        return None
    kw = {"encoding": 3, "text": vals}
    # A descriptor may itself contain ":", so neither key can be split naively.
    if base == "TXXX":
        # Key is TXXX:<desc> -- everything after the frame id is the descriptor.
        kw["desc"] = rest
    elif base == "COMM":
        # Key is COMM:<desc>:<lang>. Split from the right, and only believe the
        # tail if it looks like a language code; otherwise it is part of desc.
        desc, sep, lang = rest.rpartition(":")
        if sep and len(lang) == 3:
            kw["desc"], kw["lang"] = desc, lang
        else:
            kw["desc"], kw["lang"] = rest, "eng"
    try:
        return cls(**kw)
    except Exception:
        return None


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
    # Artwork cannot live in the JSON, so it goes to a sidecar folder named after
    # the backup file. Images are deduplicated by SHA-1: the same front cover is
    # embedded in every track of an album, and storing it once per track would
    # bloat the backup by the track count for no gain.
    art_dir = os.path.splitext(backup_path)[0] + "_art"
    art_rel = os.path.basename(art_dir)
    seen = {}

    data = {"root": root, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "art_dir": art_rel, "files": {}}
    for alb in plan["albums"]:
        for disc in alb["discs"]:
            dpath = rp(root, disc["path"])
            for tr in disc["tracks"]:
                fpath = os.path.join(dpath, tr["file"])
                if not os.path.isfile(fpath):
                    continue
                try:
                    tags = ID3(fpath)
                    # Remember the tag version so a restore does not quietly
                    # rewrite a v2.4 library as v2.3 (which cannot hold multiple
                    # values per frame and would join them with "/").
                    id3v = tags.version[1]
                except ID3NoHeaderError:
                    tags = ID3()
                    id3v = None
                frames = {}
                art = []
                for key in list(tags.keys()):
                    base = key.split(":")[0]
                    fr = tags[key]
                    if base == "APIC":
                        blob = getattr(fr, "data", None)
                        if not blob:
                            continue
                        digest = hashlib.sha1(blob).hexdigest()
                        if digest not in seen:
                            os.makedirs(art_dir, exist_ok=True)
                            mime = (getattr(fr, "mime", "") or "").lower()
                            name = digest + ART_EXT.get(mime, ".bin")
                            with open(os.path.join(art_dir, name), "wb") as af:
                                af.write(blob)
                            seen[digest] = name
                        art.append({
                            "sha1": digest,
                            "file": "%s/%s" % (art_rel, seen[digest]),
                            "mime": getattr(fr, "mime", "") or "image/jpeg",
                            "type": int(getattr(fr, "type", 3)),
                            "desc": getattr(fr, "desc", "") or "",
                        })
                        continue
                    if not isinstance(fr, TextFrame):
                        # POPM/UFID/USLT/PRIV: iterating .text would mangle them
                        # (USLT.text is a string, so it splits into characters).
                        # apply() never writes them and restore() leaves them
                        # alone, so they need no backup.
                        continue
                    try:
                        frames[key] = [str(x) for x in fr.text]
                    except Exception:
                        pass
                data["files"][os.path.relpath(fpath, root).replace("\\", "/")] = {
                    "frames": frames, "had_apic": bool(art), "apic": art,
                    "id3_version": id3v}
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    log("Backup of %d files (%d distinct artwork images) -> %s"
        % (len(data["files"]), len(seen), backup_path))


def restore_file(fpath, info, bdir):
    """Rewrite one file from its backup entry; returns the artwork count."""
    # Start from what is on disk and replace only what this tool manages: text
    # frames and artwork. Frames it never wrote -- POPM ratings, UFID
    # identifiers, USLT lyrics -- stay untouched instead of being wiped by a
    # rebuild from scratch.
    tags = load_id3(fpath)
    for key in list(tags.keys()):
        if isinstance(tags[key], TextFrame) or key.split(":")[0] == "APIC":
            del tags[key]
    for key, vals in (info.get("frames") or {}).items():
        fr = rebuild_frame(key, vals)
        if fr is not None:
            tags.add(fr)

    n_art = 0
    for item in info.get("apic") or []:
        # Every field here is data from a file a human may have edited. A bad
        # entry costs its own image, never the rest of the restore.
        name = item.get("file")
        if not isinstance(name, str) or not name:
            log("  !! artwork entry with no usable path, skipped")
            continue
        apath = within(bdir, name.replace("/", os.sep))
        if apath is None:
            log("  !! refusing artwork path outside the backup folder: %s" % name)
            continue
        if not os.path.isfile(apath):
            log("  !! missing backup artwork: %s" % name)
            continue
        try:
            pic_type = int(item.get("type", 3))
        except (TypeError, ValueError):
            pic_type = 3
        with open(apath, "rb") as af:
            tags.add(APIC(encoding=3, mime=item.get("mime") or "image/jpeg",
                          type=pic_type, desc=item.get("desc") or "",
                          data=af.read()))
        n_art += 1

    ver = info.get("id3_version")
    if ver is None and not tags:
        # The file carried no ID3 tag at all before the run. Leave it that way
        # instead of parking an empty tag and its padding on it. An ID3v1 tag,
        # if one somehow exists, is never this tool's to remove.
        tags.delete(fpath, delete_v1=False, delete_v2=True)
    elif ver == 1:
        # ID3v1 only. Both steps are needed. mutagen's save() defaults to v1=1,
        # "update the v1 tag if one is present", so apply() has already rewritten
        # the v1 fields with its own values -- deleting the added v2 tag alone
        # would leave those in place and revert nothing. Write v1 back from the
        # backup first, then take the v2 tag away.
        tags.save(fpath, v1=2, v2_version=3)
        tags.delete(fpath, delete_v1=False, delete_v2=True)
    else:
        tags.save(fpath, v2_version=ver if ver in (3, 4) else 3)
    return n_art


def restore(backup_path):
    with open(backup_path, encoding="utf-8") as f:
        data = json.load(f)
    root = data["root"]
    bdir = os.path.dirname(os.path.abspath(backup_path))
    n = 0
    n_art = 0
    legacy = 0
    failed = 0
    for rel, info in data["files"].items():
        fpath = within(root, rel.replace("/", os.sep))
        if fpath is None:
            log("  !! refusing path outside the backup root: %s" % rel)
            continue
        if os.path.splitext(fpath)[1].lower() not in AUDIO_EXT:
            log("  !! refusing a target that is not an MP3: %s" % rel)
            continue
        if not os.path.isfile(fpath):
            continue
        if info.get("apic") is None and info.get("had_apic"):
            # Backup written before artwork was stored: the original image is not
            # recoverable, so say so instead of dropping it silently.
            legacy += 1
        try:
            n_art += restore_file(fpath, info, bdir)
        except Exception as e:
            # Undoing a run halfway is worse than skipping one file, so a damaged
            # entry is reported and stepped over rather than aborting the rest.
            failed += 1
            log("  !! could not restore %s: %s: %s" % (rel, type(e).__name__, e))
            continue
        n += 1
    log("Restored tags on %d files, %d artwork images reinstated "
        "(moved image files NOT reverted)." % (n, n_art))
    if legacy:
        log("  !! %d file(s) came from an old backup that stored no artwork -- "
            "their original embedded covers could not be restored." % legacy)
    if failed:
        log("  !! %d file(s) could not be restored -- see the lines above." % failed)


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
        album_src = rp(root, alb["album_cover"]) if alb.get("album_cover") else None
        has_album_cover = bool(album_src and os.path.isfile(album_src))
        album_cover_bytes = (process_cover_bytes(album_src, max_px)
                             if has_album_cover and not dry else None)

        for disc in alb["discs"]:
            dpath = rp(root, disc["path"])
            disc_no = disc.get("disc")
            disc_total = disc.get("disc_total")

            # disc cover bytes
            csrc = rp(root, disc["cover"]) if disc.get("cover") else None
            has_cover = bool(csrc and os.path.isfile(csrc))
            cover_bytes = (process_cover_bytes(csrc, max_px)
                           if has_cover and not dry else None)

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
                if embed and has_cover:
                    if not dry:
                        tags.delall("APIC")
                        tags.add(APIC(encoding=3, mime="image/jpeg", type=3,
                                      desc="Front", data=cover_bytes))
                    changes["covers_embedded"] += 1

                if not dry:
                    tags.save(fpath, v2_version=id3v)
                changes["tracks"] += 1

            # write disc-level cover.jpg
            if folder_jpg and has_cover:
                out = os.path.join(dpath, "cover.jpg")
                if not dry:
                    with open(out, "wb") as f:
                        f.write(cover_bytes)
                changes["cover_jpgs"] += 1
                log("  cover.jpg -> %s" % os.path.relpath(out, root))

        # album-level cover.jpg (multi-disc parent)
        if folder_jpg and has_album_cover and alb.get("album_path"):
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
