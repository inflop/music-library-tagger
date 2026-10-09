# -*- coding: utf-8 -*-
"""
Apply a tagging plan (plan.json) to an MP3 / FLAC library, or restore from a backup.

Before writing, backs up every text frame and every embedded cover of the files
it is about to touch, so --restore can undo the run. Artwork is copied out to a
sidecar folder next to the backup JSON and re-embedded verbatim.

This script knows nothing about tag formats. Each file is handed to the backend
that tagio.py picks by extension (ID3 for MP3, Vorbis comments for FLAC, ...), and
the format-specific rules -- which frames or fields are backed up, how a restore
treats tags the tool never wrote, ID3 versions -- are documented in that backend's
module. A backup entry carries "format": <backend name>; an entry without it is
ID3 (the layout from before other formats existed), so old backups still restore.
The plan is the same for every format: options.id3_version only applies to MP3,
and options.strip_frames takes ID3 frame ids (mapped to the native field names
where there are any) or native field names.
Only tags and artwork are touched -- the audio stream is never re-encoded.

Usage:
    python apply_plan.py --plan plan.json [--dry-run]
    python apply_plan.py --restore <backup.json> [--dry-run]

plan.json schema (all paths are RELATIVE to "root", forward slashes ok):
{
  "root": "G:/Music/Some Band",
  "options": {
     "artist": "Some Band",              # artist tag on every track (optional)
     "album_artist": "Some Band",        # album artist tag on every track (optional)
     "genre": "Progressive Rock",        # genre default (optional)
     "strip_frames": ["COMM", "TENC"],   # ID3 frame ids (or native field names) to delete
     "id3_version": 3,                   # MP3 only: save as ID3v2.3 (widest compatibility)
     "cover_embed": true,
     "cover_folder_jpg": true,
     "cover_max_px": 1400,               # downscale embedded/cover.jpg if larger
     "move_images_mode": "copy"          # "copy" (safe) or "move"
  },
  "albums": [
    {
      "album": "Red",
      "year": 1974,                       # release year tag
      "genre": "Progressive Rock",        # optional per-album override
      "album_path": "ORIGINAL/1974 - Red",# where album-level cover.jpg is written
      "album_cover": "ORIGINAL/1974 - Red/covers/Cover.jpg",  # optional
      "discs": [
        {
          "path": "ORIGINAL/1974 - Red",
          "disc": 1, "disc_total": 1,     # omit / null for single-disc (no disc tag)
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
import os, sys, io, json, time, argparse, shutil, hashlib, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tagio

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


def unreadable_reason(backend, path):
    """None if the backend can open the file, else why it cannot.

    The extension alone proves nothing: a Speex stream named .ogg, a truncated or
    mislabelled file. Such a file is skipped by the backup and by apply alike, so a
    file that was not backed up is never written, and one bad file does not abort
    the run (or, in a dry run, get counted as taggable).
    """
    try:
        backend.read_summary(path)
    except Exception as e:
        return "%s: %s" % (type(e).__name__, e)
    return None


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

    def stash(blob, mime):
        """Store an image once in the sidecar folder; return (sha1, file name)."""
        digest = hashlib.sha1(blob).hexdigest()
        if digest not in seen:
            os.makedirs(art_dir, exist_ok=True)
            name = digest + ART_EXT.get((mime or "").lower(), ".bin")
            with open(os.path.join(art_dir, name), "wb") as af:
                af.write(blob)
            seen[digest] = name
        return digest, seen[digest]

    data = {"root": root, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "files": {}}
    for alb in plan["albums"]:
        for disc in alb["discs"]:
            dpath = rp(root, disc["path"])
            for tr in disc["tracks"]:
                fpath = os.path.join(dpath, tr["file"])
                if not os.path.isfile(fpath):
                    continue
                backend = tagio.backend_for(fpath)
                if backend is None:
                    log("  !! not a supported audio file, not backed up: %s" % fpath)
                    continue
                why = unreadable_reason(backend, fpath)
                if why:
                    log("  !! cannot read %s (%s): not backed up, and apply will not touch it"
                        % (fpath, why))
                    continue
                payload, pictures = backend.snapshot(fpath)
                art = []
                for pic in pictures:
                    if not pic["data"]:
                        continue
                    digest, name = stash(pic["data"], pic["mime"])
                    item = {k: v for k, v in pic.items() if k != "data"}
                    item.update(sha1=digest, file="%s/%s" % (art_rel, name),
                                mime=pic["mime"] or "image/jpeg",
                                desc=pic["desc"] or "")
                    art.append(item)
                entry = dict(payload, had_apic=bool(art), apic=art)
                if backend.NAME != tagio.LEGACY_FORMAT:
                    entry["format"] = backend.NAME
                data["files"][os.path.relpath(fpath, root).replace("\\", "/")] = entry
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    log("Backup of %d files (%d distinct artwork images) -> %s"
        % (len(data["files"]), len(seen), backup_path))


def load_artwork(art, bdir):
    """Artwork entries of a backup, read back from the sidecar folder.

    Returns dicts with the image bytes (`data`) and its `mime`, `type`, `desc`
    plus the raw entry (`item`). A damaged entry is logged and skipped.
    """
    out = []
    if not isinstance(art, list):
        art = []
    for item in art:
        # Everything here is data from a file a human may have edited -- down to
        # whether the entry is an object at all. Whatever is wrong with it, it
        # costs its own image and nothing more. Guarding field by field would
        # never end; this boundary is what makes that guarantee hold.
        try:
            if not isinstance(item, dict):
                log("  !! artwork entry is not an object, skipped")
                continue
            name = item.get("file")
            if not isinstance(name, str) or not name:
                log("  !! artwork entry with no usable path, skipped")
                continue
            apath = within(bdir, name.replace("/", os.sep))
            if apath is None:
                log("  !! refusing artwork path outside the backup folder: %s"
                    % name)
                continue
            if not os.path.isfile(apath):
                log("  !! missing backup artwork: %s" % name)
                continue
            try:
                pic_type = int(item.get("type", 3))
            except (TypeError, ValueError):
                pic_type = 3
            # mutagen str()s whatever it is given, so a number or a list here
            # would not fail -- it would quietly write "123" as the MIME type of
            # a picture in someone's file. Case is left alone: it is copied from
            # the frame the backup read, and normalising it would be less
            # faithful, not more.
            mime = item.get("mime")
            if not isinstance(mime, str) or not mime:
                mime = "image/jpeg"
            desc = item.get("desc")
            if not isinstance(desc, str):
                desc = ""
            with open(apath, "rb") as af:
                out.append({"data": af.read(), "mime": mime, "type": pic_type,
                            "desc": desc, "item": item})
        except Exception as e:
            log("  !! skipping a damaged artwork entry: %s: %s"
                % (type(e).__name__, e))
    return out


def restore(backup_path, dry=False):
    """Undo a run from its backup. With `dry`, change nothing and say what would happen.

    A dry run restores onto a temporary copy of each file, so it exercises exactly the
    checks a real restore does (entry shape, artwork, the file's real format) and reports
    entries that would fail, while the original is never opened for writing.
    """
    with open(backup_path, encoding="utf-8") as f:
        data = json.load(f)
    # Check the shape once here rather than guarding every field downstream.
    if (not isinstance(data, dict) or not isinstance(data.get("root"), str)
            or not isinstance(data.get("files"), dict)):
        log("!! %s is not a usable backup: expected a root path and a files "
            "mapping." % backup_path)
        return
    root = data["root"]
    bdir = os.path.dirname(os.path.abspath(backup_path))
    n = 0
    n_art = 0
    legacy = 0
    failed = 0
    for rel, info in data["files"].items():
        if not isinstance(info, dict):
            log("  !! skipping %s: its backup entry is not an object" % rel)
            failed += 1
            continue
        fpath = within(root, rel.replace("/", os.sep))
        if fpath is None:
            log("  !! refusing path outside the backup root: %s" % rel)
            continue
        backend = tagio.backend_for(fpath)
        if backend is None:
            log("  !! refusing a target that is not a supported audio file: %s" % rel)
            continue
        if not os.path.isfile(fpath):
            continue
        if info.get("format", tagio.LEGACY_FORMAT) != backend.NAME:
            log("  !! backup entry does not match the file type, skipped: %s" % rel)
            failed += 1
            continue
        if info.get("apic") is None and info.get("had_apic"):
            # Backup written before artwork was stored: the original image is not
            # recoverable, so say so instead of dropping it silently.
            legacy += 1
        try:
            pictures = load_artwork(info.get("apic"), bdir)
            if dry:
                with tempfile.TemporaryDirectory(prefix="mlt-restore-") as tmp:
                    probe = os.path.join(tmp, os.path.basename(fpath))
                    shutil.copy2(fpath, probe)
                    backend.restore(probe, info, pictures)
            else:
                backend.restore(fpath, info, pictures)
            n_art += len(pictures)
        except Exception as e:
            # Undoing a run halfway is worse than skipping one file, so a damaged
            # entry is reported and stepped over rather than aborting the rest.
            failed += 1
            log("  !! could not restore %s: %s: %s" % (rel, type(e).__name__, e))
            continue
        n += 1
    if dry:
        log("DRY-RUN: would restore tags on %d files, %d artwork images would be reinstated "
            "(nothing was changed)." % (n, n_art))
    else:
        log("Restored tags on %d files, %d artwork images reinstated "
            "(moved image files NOT reverted)." % (n, n_art))
    if legacy:
        log("  !! %d file(s) came from an old backup that stored no artwork -- "
            "their original embedded covers could not be restored." % legacy)
    if failed:
        log("  !! %d file(s) %s be restored -- see the lines above."
            % (failed, "would not" if dry else "could not"))


# ------------------------------- apply -------------------------------

def apply(plan, dry):
    root = os.path.abspath(rp(os.getcwd(), plan["root"]) if not os.path.isabs(plan["root"]) else plan["root"])
    opt = plan.get("options", {})
    strip = set(opt.get("strip_frames", []))
    embed = opt.get("cover_embed", True)
    folder_jpg = opt.get("cover_folder_jpg", True)
    max_px = opt.get("cover_max_px", 1400)
    move_mode = opt.get("move_images_mode", "copy")
    def_artist = opt.get("artist")
    def_aa = opt.get("album_artist")
    def_genre = opt.get("genre")

    changes = {"tracks": 0, "covers_embedded": 0, "cover_jpgs": 0, "moved": 0,
               "unreadable": 0}

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

                backend = tagio.backend_for(fpath)
                if backend is None:
                    log("  !! not a supported audio file, skipped: %s" % fpath)
                    continue
                why = unreadable_reason(backend, fpath)
                if why:
                    log("  !! cannot read %s (%s), skipped" % (fpath, why))
                    changes["unreadable"] += 1
                    continue

                use_cover = embed and has_cover
                if not dry:
                    backend.write(fpath, {
                        "album": album, "title": tr.get("title"),
                        "artist": artist, "album_artist": aa, "year": year,
                        "genre": genre, "track": tr.get("track"),
                        "track_total": tr.get("track_total"), "disc": disc_no,
                        "disc_total": disc_total,
                    }, strip, cover_bytes if use_cover else None, opt)
                if use_cover:
                    changes["covers_embedded"] += 1
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
        restore(args.restore, args.dry_run); return

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
