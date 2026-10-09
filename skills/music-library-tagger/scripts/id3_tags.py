# -*- coding: utf-8 -*-
"""
MP3 / ID3 tag backend.

Implements the interface every backend in tagio.py provides:

    NAME, EXTENSIONS
    read_summary(path)                       -> dict
    snapshot(path)                           -> (payload, pictures)
    restore(path, entry, pictures)
    write(path, fields, strip, cover, options)

Frames that are not text frames (POPM ratings, UFID identifiers, USLT lyrics,
PRIV...) are neither backed up nor rewritten: write() does not touch them and
restore() leaves them in place. The exception is a non-text frame named in
`strip` -- deleting that is deliberate and cannot be undone.

A restore writes the tag back in the ID3v2 version the file had, as far as
mutagen can write one: v2.3 or v2.4. A v2.2 tag is read but cannot be written
back (write() has already rewritten it as v2.3 by then), so it restores as v2.3.
A file that had no ID3v2 tag ends up with none again rather than an empty one,
and a file that had only ID3v1 keeps just its ID3v1.
Note that ID3v2.3 cannot store several values in one frame, so writing a v2.3
tag joins them with "/" -- a property of the format, not of the backup, which
keeps the values apart.
"""
from mutagen import File as MFile
from mutagen.id3 import (ID3, ID3NoHeaderError, Frames, TextFrame, TALB, TPE1,
                         TPE2, TIT2, TCON, TDRC, TRCK, TPOS, APIC)

NAME = "mp3"
EXTENSIONS = (".mp3",)


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


def load_id3(path):
    try:
        return ID3(path)
    except ID3NoHeaderError:
        return ID3()


def read_summary(path):
    """Tags of one MP3 file in the shape every backend returns."""
    tg = MFile(path).tags
    fields = []
    comments = []
    n_pictures = 0
    if tg is not None:
        for k in tg.keys():
            base = k.split(":")[0]
            fields.append(base)
            if base == "APIC":
                n_pictures += 1
            if base == "COMM":
                comments.append(txt(tg, k))
    return {
        "title": txt(tg, "TIT2"),
        "track": txt(tg, "TRCK"),
        "disc": txt(tg, "TPOS"),
        "album": txt(tg, "TALB"),
        "artist": txt(tg, "TPE1"),
        "album_artist": txt(tg, "TPE2"),
        "genre": txt(tg, "TCON"),
        "year": txt(tg, "TDRC") or txt(tg, "TYER"),
        "n_pictures": n_pictures,
        "fields": fields,
        "comments": comments,
    }


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


def snapshot(path):
    """Return (payload, pictures): every text frame, and every embedded cover."""
    try:
        tags = ID3(path)
        # Remember the tag version so a restore does not quietly rewrite a v2.4
        # library as v2.3 (which cannot hold multiple values per frame and
        # would join them with "/").
        id3v = tags.version[1]
    except ID3NoHeaderError:
        tags = ID3()
        id3v = None
    frames = {}
    pictures = []
    for key in list(tags.keys()):
        base = key.split(":")[0]
        fr = tags[key]
        if base == "APIC":
            blob = getattr(fr, "data", None)
            if not blob:
                continue
            pictures.append({
                "data": blob,
                "mime": getattr(fr, "mime", "") or "image/jpeg",
                "type": int(getattr(fr, "type", 3)),
                "desc": getattr(fr, "desc", "") or "",
            })
            continue
        if not isinstance(fr, TextFrame):
            # POPM/UFID/USLT/PRIV: iterating .text would mangle them (USLT.text
            # is a string, so it splits into characters). write() never writes
            # them and restore() leaves them alone, so they need no backup.
            continue
        try:
            frames[key] = [str(x) for x in fr.text]
        except Exception:
            pass
    return {"frames": frames, "id3_version": id3v}, pictures


def restore(path, entry, pictures):
    """Rewrite one file from its backup entry and the pictures read for it."""
    frames = entry.get("frames")
    if not isinstance(frames, dict):
        # A missing snapshot is a damaged entry, not "the file had no frames" ({}):
        # reading it that way would wipe the tags the file has now. Every valid
        # backup, old layouts included, carries "frames".
        raise ValueError("the MP3 backup entry has no frame snapshot")
    # Start from what is on disk and replace only what this tool manages: text
    # frames and artwork. Frames it never wrote -- POPM ratings, UFID
    # identifiers, USLT lyrics -- stay untouched instead of being wiped by a
    # rebuild from scratch.
    tags = load_id3(path)
    for key in list(tags.keys()):
        if isinstance(tags[key], TextFrame) or key.split(":")[0] == "APIC":
            del tags[key]
    for key, vals in frames.items():
        fr = rebuild_frame(key, vals)
        if fr is not None:
            tags.add(fr)
    for pic in pictures:
        tags.add(APIC(encoding=3, mime=pic["mime"], type=pic["type"],
                      desc=pic["desc"], data=pic["data"]))

    ver = entry.get("id3_version")
    if ver is None and not tags:
        # The file carried no ID3 tag at all before the run. Leave it that way
        # instead of parking an empty tag and its padding on it. An ID3v1 tag,
        # if one somehow exists, is never this tool's to remove.
        tags.delete(path, delete_v1=False, delete_v2=True)
    elif ver == 1:
        # ID3v1 only. Both steps are needed. mutagen's save() defaults to v1=1,
        # "update the v1 tag if one is present", so write() has already rewritten
        # the v1 fields with its own values -- deleting the added v2 tag alone
        # would leave those in place and revert nothing. Write v1 back from the
        # backup first, then take the v2 tag away.
        tags.save(path, v1=2, v2_version=3)
        tags.delete(path, delete_v1=False, delete_v2=True)
    else:
        tags.save(path, v2_version=ver if ver in (3, 4) else 3)


def write(path, fields, strip, cover, options):
    """Apply the plan's fields to one file; `cover` is JPEG bytes or None."""
    tags = load_id3(path)

    # strip unwanted frames
    for key in list(tags.keys()):
        if key.split(":")[0] in strip:
            del tags[key]

    def setf(cls, val):
        if val is None or val == "":
            return
        tags.setall(cls.__name__, [cls(encoding=3, text=[str(val)])])

    setf(TALB, fields.get("album"))
    setf(TIT2, fields.get("title"))
    setf(TPE1, fields.get("artist"))
    setf(TPE2, fields.get("album_artist"))
    setf(TDRC, fields.get("year"))
    setf(TCON, fields.get("genre"))
    track = fields.get("track")
    if track is not None:
        total = fields.get("track_total")
        setf(TRCK, "%s/%s" % (track, total) if total else str(track))
    disc = fields.get("disc")
    if disc:
        total = fields.get("disc_total")
        setf(TPOS, "%s/%s" % (disc, total) if total else str(disc))

    if cover is not None:
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Front",
                      data=cover))

    tags.save(path, v2_version=(options or {}).get("id3_version", 3))
