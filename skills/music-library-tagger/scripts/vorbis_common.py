# -*- coding: utf-8 -*-
"""
Vorbis-comment logic shared by the FLAC and Ogg (Vorbis / Opus) backends.

Field mapping used when writing (MusicBrainz Picard convention):
    album -> ALBUM            artist -> ARTIST        album artist -> ALBUMARTIST
    title -> TITLE            genre  -> GENRE         year -> DATE
    track -> TRACKNUMBER + TOTALTRACKS               disc -> DISCNUMBER + TOTALDISCS
The legacy spellings TRACKTOTAL / DISCTOTAL / YEAR are removed when the matching
new field is written, so two fields can never disagree.

Only the picture storage differs between the formats (a FLAC Picture block versus a
base64 METADATA_BLOCK_PICTURE comment), so that stays in the backends.
"""
import io

from mutagen.flac import Picture

# options.strip_frames holds ID3 frame ids. Map the ones that have a Vorbis
# equivalent; any other name is taken as a Vorbis field name (case-insensitive).
STRIP_ALIASES = {
    "COMM": ("COMMENT", "DESCRIPTION"),
    "TENC": ("ENCODEDBY", "ENCODED-BY", "ENCODER"),
    "TSSE": ("ENCODER", "ENCODERSETTINGS", "ENCODING"),
    "TCOP": ("COPYRIGHT",),
    "TPUB": ("ORGANIZATION", "LABEL", "PUBLISHER"),
    "TCOM": ("COMPOSER",),
}


def first(tags, *names):
    if tags is None:
        return ""
    for name in names:
        values = tags.get(name)
        if values:
            return "; ".join(str(v) for v in values)
    return ""


def _number(number, total):
    # "3/12" already carries its total; otherwise join the two fields the way
    # ID3 spells it so callers see one shape for every format.
    if total and number and "/" not in number:
        return "%s/%s" % (number, total)
    return number


def summarize(tags, n_pictures):
    """Tags of one file in the shape every backend's read_summary() returns."""
    comments = []
    fields = []
    if tags is not None:
        fields = sorted({str(k).upper() for k in tags.keys()})
        for name in ("COMMENT", "DESCRIPTION"):
            comments.extend(str(v) for v in (tags.get(name) or []))
    return {
        "title": first(tags, "TITLE"),
        "track": _number(first(tags, "TRACKNUMBER"),
                         first(tags, "TOTALTRACKS", "TRACKTOTAL")),
        "disc": _number(first(tags, "DISCNUMBER"),
                        first(tags, "TOTALDISCS", "DISCTOTAL")),
        "album": first(tags, "ALBUM"),
        "artist": first(tags, "ARTIST"),
        "album_artist": first(tags, "ALBUMARTIST"),
        "genre": first(tags, "GENRE"),
        "year": first(tags, "DATE", "YEAR"),
        "n_pictures": n_pictures,
        "fields": fields,
        "comments": comments,
    }


def drop(tags, names):
    for name in names:
        if name in tags:
            del tags[name]


def apply_fields(tags, fields, strip):
    """Write the plan's fields into a mutagen Vorbis comment dict."""
    for name in strip:
        drop(tags, STRIP_ALIASES.get(name, (name,)))

    def put(name, value, remove=()):
        if value is None or value == "":
            return
        tags[name] = [str(value)]
        drop(tags, remove)

    put("ALBUM", fields.get("album"))
    put("TITLE", fields.get("title"))
    put("ARTIST", fields.get("artist"))
    put("ALBUMARTIST", fields.get("album_artist"))
    put("GENRE", fields.get("genre"))
    put("DATE", fields.get("year"), remove=("YEAR",))
    if fields.get("track") is not None:
        put("TRACKNUMBER", fields["track"])
        drop(tags, ("TOTALTRACKS", "TRACKTOTAL"))
        put("TOTALTRACKS", fields.get("track_total"))
    if fields.get("disc"):
        put("DISCNUMBER", fields["disc"])
        drop(tags, ("TOTALDISCS", "DISCTOTAL"))
        put("TOTALDISCS", fields.get("disc_total"))


def validate_comments(comments):
    """A backup's [[name, value], ...] list, or ValueError if it is malformed."""
    if comments is None:
        return None
    if not isinstance(comments, list) or not all(
            isinstance(c, list) and len(c) == 2
            and all(isinstance(x, str) for x in c) for c in comments):
        raise ValueError("the Vorbis comments in the backup are malformed")
    return comments


def comments_of(entry):
    """The comment snapshot of a backup entry; ValueError if the entry has none.

    A missing "vorbis" key is a damaged entry. It must not be read as "the file had
    no comments" (None), which would wipe the comments the file has now.
    """
    if "vorbis" not in entry:
        raise ValueError("the backup entry has no Vorbis comments snapshot")
    return validate_comments(entry["vorbis"])


def make_picture(data, mime="image/jpeg", type=3, desc="Front",
                 width=0, height=0, depth=0, colors=0):
    pic = Picture()
    pic.data = data
    pic.mime, pic.type, pic.desc = mime, int(type), desc
    pic.width, pic.height, pic.depth, pic.colors = width, height, depth, colors
    return pic


def front_cover(jpeg_bytes):
    """A front-cover Picture for JPEG bytes, with its pixel size when readable."""
    width = height = 0
    try:
        from PIL import Image
        with Image.open(io.BytesIO(jpeg_bytes)) as im:
            width, height = im.size
    except Exception:
        pass
    return make_picture(jpeg_bytes, width=width, height=height, depth=24)


def picture_dict(pic):
    """A Picture as the dict snapshot() hands to the backup."""
    return {"data": pic.data, "mime": pic.mime, "type": int(pic.type),
            "desc": pic.desc, "width": pic.width, "height": pic.height,
            "depth": pic.depth, "colors": pic.colors}


def picture_from_backup(pic):
    """A Picture from a dict load_artwork() read (dimensions come from the raw entry)."""
    extra = {}
    for field in ("width", "height", "depth", "colors"):
        try:
            extra[field] = int(pic["item"].get(field, 0))
        except (TypeError, ValueError):
            extra[field] = 0
    return make_picture(pic["data"], pic["mime"], pic["type"], pic["desc"], **extra)
