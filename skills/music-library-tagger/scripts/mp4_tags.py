# -*- coding: utf-8 -*-
"""
M4A (AAC / Apple Lossless) tag backend; see tagio.py for the interface.

MP4 tags are atoms in moov/udta/meta/ilst. Field mapping used when writing:
    album -> (c)alb           artist -> (c)ART        album artist -> aART
    title -> (c)nam           genre  -> (c)gen        year -> (c)day
    track -> trkn (number, total)                     disc -> disk (number, total)
    cover -> covr, one JPEG
where (c) is the copyright sign. A track or disc total that is not known is stored
as 0, the MP4 way of saying "no total". (mutagen turns the numeric `gnre` genre atom
into a (c)gen value when it reads a file, so there is no second genre to conflict.)

Backup and restore are faithful for the value types an ilst holds: text, integer
lists (tmpo, rtng...), (number, total) pairs, booleans (cpil, pgap), and
free-form `----` atoms with their data type. Covers go to the sidecar folder as
pictures. An atom of any other shape is neither backed up nor rewritten: a restore
replaces the atoms it can represent and leaves the rest where they are, like the ID3
backend does with non-text frames.

Only moov is rewritten (mutagen rewrites the stco / co64 chunk offsets so they keep
pointing at mdat when moov grows); the audio
data is never touched. .mp4 (may be video), .m4b and raw .aac are not handled.
"""
import base64
import binascii

from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm

NAME = "m4a"
EXTENSIONS = (".m4a",)

ALBUM, TITLE, ARTIST, ALBUM_ARTIST = "\xa9alb", "\xa9nam", "\xa9ART", "aART"
GENRE, YEAR, TRACK, DISC, COVER = "\xa9gen", "\xa9day", "trkn", "disk", "covr"
COMMENT = "\xa9cmt"

# options.strip_frames holds ID3 frame ids. Map the ones that have an MP4
# equivalent; any other name is taken as an atom name (case-insensitive).
STRIP_ALIASES = {
    "COMM": (COMMENT,),
    "TENC": ("\xa9too",),
    "TSSE": ("\xa9too",),
    "TCOP": ("cprt",),
    "TCOM": ("\xa9wrt",),
}

# The only two image formats the MP4 covr atom defines.
MIME_BY_FORMAT = {MP4Cover.FORMAT_JPEG: "image/jpeg", MP4Cover.FORMAT_PNG: "image/png"}
FORMAT_BY_MIME = {mime: fmt for fmt, mime in MIME_BY_FORMAT.items()}


def _text(tags, key):
    if tags is None:
        return ""
    return "; ".join(str(v) for v in (tags.get(key) or []))


def _pair(tags, key):
    values = None if tags is None else tags.get(key)
    if not values:
        return ""
    number, total = values[0]
    if not number:
        return ""
    return "%d/%d" % (number, total) if total else str(number)


def read_summary(path):
    tags = MP4(path).tags
    return {
        "title": _text(tags, TITLE),
        "track": _pair(tags, TRACK),
        "disc": _pair(tags, DISC),
        "album": _text(tags, ALBUM),
        "artist": _text(tags, ARTIST),
        "album_artist": _text(tags, ALBUM_ARTIST),
        "genre": _text(tags, GENRE),
        "year": _text(tags, YEAR),
        "n_pictures": 0 if tags is None else len(tags.get(COVER) or []),
        "fields": [] if tags is None else sorted(tags.keys()),
        "comments": [] if tags is None else [str(v) for v in (tags.get(COMMENT) or [])],
    }


def _encode(key, value):
    """(kind, JSON-able value) for an atom this backend can restore, else None."""
    if key == COVER:
        return None
    if isinstance(value, bool):
        return "bool", value
    if not isinstance(value, list) or not value:
        return None
    if all(isinstance(v, MP4FreeForm) for v in value):
        return "freeform", [[base64.b64encode(bytes(v)).decode("ascii"),
                             int(v.dataformat)] for v in value]
    if all(isinstance(v, str) for v in value):
        return "text", list(value)
    if all(isinstance(v, int) and not isinstance(v, bool) for v in value):
        return "ints", list(value)
    if all(isinstance(v, tuple) and len(v) == 2
           and all(isinstance(x, int) for x in v) for v in value):
        return "pairs", [list(v) for v in value]
    return None


def _decode(item):
    """(key, value) from a backup item; ValueError if it is malformed."""
    try:
        key, kind, value = item["key"], item["kind"], item["value"]
        if not isinstance(key, str):
            raise ValueError
        if kind == "bool" and isinstance(value, bool):
            return key, value
        if kind == "text" and isinstance(value, list) and all(isinstance(v, str) for v in value):
            return key, list(value)
        if kind == "ints" and isinstance(value, list) and all(
                isinstance(v, int) and not isinstance(v, bool) for v in value):
            return key, list(value)
        if kind == "pairs" and isinstance(value, list) and all(
                isinstance(v, list) and len(v) == 2 and all(isinstance(x, int) for x in v)
                for v in value):
            return key, [tuple(v) for v in value]
        if kind == "freeform" and isinstance(value, list):
            return key, [MP4FreeForm(base64.b64decode(data, validate=True),
                                     dataformat=int(fmt)) for data, fmt in value]
    except (KeyError, TypeError, ValueError, binascii.Error):
        pass
    raise ValueError("the MP4 atoms in the backup are malformed")


def snapshot(path):
    """Return (payload, pictures): every atom this backend can restore, and the covers.

    payload["mp4"] is a list of {key, kind, value} items, or None when the file has
    no tags at all.
    """
    tags = MP4(path).tags
    if tags is None:
        return {"mp4": None}, []
    items = []
    for key in tags.keys():
        encoded = _encode(key, tags[key])
        if encoded is not None:
            items.append({"key": key, "kind": encoded[0], "value": encoded[1]})
    pictures = [{"data": bytes(c), "mime": MIME_BY_FORMAT.get(c.imageformat, "image/jpeg"),
                 "type": 3, "desc": ""} for c in tags.get(COVER) or []]
    return {"mp4": items}, pictures


def restore(path, entry, pictures):
    if "mp4" not in entry:
        # Not the same as "the file had no tags" (None): reading it that way would
        # wipe the atoms the file has now.
        raise ValueError("the backup entry has no MP4 atoms snapshot")
    items = entry["mp4"]
    decoded = None
    if items is not None:
        if not isinstance(items, list):
            raise ValueError("the MP4 atoms in the backup are malformed")
        decoded = [_decode(item) for item in items]  # all checked before the file changes
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    for key in list(tags.keys()):
        if key == COVER or _encode(key, tags[key]) is not None:
            del tags[key]
    for key, value in decoded or []:
        tags[key] = value
    if pictures:
        tags[COVER] = [MP4Cover(p["data"], imageformat=FORMAT_BY_MIME.get(
            p["mime"], MP4Cover.FORMAT_JPEG)) for p in pictures]
    audio.save()
    if decoded is None:
        # The file had no tags before the run; do not leave an empty ilst behind.
        audio = MP4(path)
        if audio.tags is not None and not audio.tags:
            audio.delete()


def _int(value):
    try:
        return int(str(value).split("/")[0])
    except ValueError:
        return 0


def write(path, fields, strip, cover, options=None):
    """Apply the plan's fields to one file; `cover` is JPEG bytes or None."""
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags

    doomed = set()
    for name in strip:
        doomed.update(a.lower() for a in STRIP_ALIASES.get(name, (name,)))
    for key in list(tags.keys()):
        if key.lower() in doomed:
            del tags[key]

    def put(key, value):
        if value is None or value == "":
            return
        tags[key] = [str(value)]

    put(ALBUM, fields.get("album"))
    put(TITLE, fields.get("title"))
    put(ARTIST, fields.get("artist"))
    put(ALBUM_ARTIST, fields.get("album_artist"))
    put(YEAR, fields.get("year"))
    put(GENRE, fields.get("genre"))
    if fields.get("track") is not None:
        tags[TRACK] = [(_int(fields["track"]), _int(fields.get("track_total") or 0))]
    if fields.get("disc"):
        tags[DISC] = [(_int(fields["disc"]), _int(fields.get("disc_total") or 0))]
    if cover is not None:
        tags[COVER] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
    audio.save()
