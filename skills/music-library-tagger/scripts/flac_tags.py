# -*- coding: utf-8 -*-
"""
FLAC tag access (Vorbis comments + picture blocks) for analyze.py / apply_plan.py.

Everything FLAC-specific lives here so the ID3 code paths stay untouched. Only the
metadata blocks are rewritten; mutagen leaves the audio frames alone.

Field mapping used when writing (MusicBrainz Picard convention):
    album -> ALBUM            artist -> ARTIST        album artist -> ALBUMARTIST
    title -> TITLE            genre  -> GENRE         year -> DATE
    track -> TRACKNUMBER + TOTALTRACKS               disc -> DISCNUMBER + TOTALDISCS
    cover -> one FLAC Picture block, type 3 (front)
The legacy spellings TRACKTOTAL / DISCTOTAL / YEAR are removed when the matching
new field is written, so two fields can never disagree.
"""
import io

from mutagen.flac import FLAC, Picture

FLAC_EXT = (".flac",)

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


def is_flac(path):
    return str(path).lower().endswith(FLAC_EXT)


def _first(tags, *names):
    if tags is None:
        return ""
    for name in names:
        values = tags.get(name)
        if values:
            return "; ".join(str(v) for v in values)
    return ""


def _number(number, total):
    # "3/12" already carries its total; otherwise join the two fields the way
    # ID3 spells it so callers see one shape for both formats.
    if total and number and "/" not in number:
        return "%s/%s" % (number, total)
    return number


def read_summary(path):
    """Tags of one FLAC file in the shape analyze.py reports for any format."""
    audio = FLAC(path)
    tags = audio.tags
    comments = []
    fields = []
    if tags is not None:
        fields = sorted({str(k).upper() for k in tags.keys()})
        for name in ("COMMENT", "DESCRIPTION"):
            comments.extend(str(v) for v in (tags.get(name) or []))
    return {
        "title": _first(tags, "TITLE"),
        "track": _number(_first(tags, "TRACKNUMBER"),
                         _first(tags, "TOTALTRACKS", "TRACKTOTAL")),
        "disc": _number(_first(tags, "DISCNUMBER"),
                        _first(tags, "TOTALDISCS", "DISCTOTAL")),
        "album": _first(tags, "ALBUM"),
        "artist": _first(tags, "ARTIST"),
        "album_artist": _first(tags, "ALBUMARTIST"),
        "genre": _first(tags, "GENRE"),
        "year": _first(tags, "DATE", "YEAR"),
        "n_pictures": len(audio.pictures),
        "fields": fields,
        "comments": comments,
    }


def snapshot(path):
    """Return (comments, pictures) exactly as stored, for the backup.

    comments is a list of [name, value] pairs (order, case and repeated names
    kept) or None when the file has no Vorbis comment block at all.
    """
    audio = FLAC(path)
    comments = None if audio.tags is None else [[k, v] for k, v in audio.tags]
    pictures = [{"data": p.data, "mime": p.mime, "type": int(p.type),
                 "desc": p.desc, "width": p.width, "height": p.height,
                 "depth": p.depth, "colors": p.colors}
                for p in audio.pictures]
    return comments, pictures


def _picture(data, mime="image/jpeg", type=3, desc="Front",
             width=0, height=0, depth=0, colors=0):
    pic = Picture()
    pic.data = data
    pic.mime, pic.type, pic.desc = mime, int(type), desc
    pic.width, pic.height, pic.depth, pic.colors = width, height, depth, colors
    return pic


def rewrite(path, comments, pictures):
    """Put back what snapshot() returned: replace every comment and picture."""
    audio = FLAC(path)
    audio.clear_pictures()
    for item in pictures:
        audio.add_picture(_picture(**item))
    if comments is None:
        # The file had no Vorbis block before the run; do not leave an empty one.
        audio.save()
        audio = FLAC(path)
        if audio.tags is not None:
            audio.delete()
        return
    if audio.tags is None:
        audio.add_tags()
    audio.tags.clear()
    for key, value in comments:
        audio.tags.append((key, value))
    audio.save()


def _drop(tags, names):
    for name in names:
        if name in tags:
            del tags[name]


def write(path, *, album=None, title=None, artist=None, album_artist=None,
          year=None, genre=None, track=None, track_total=None, disc=None,
          disc_total=None, strip=(), cover=None):
    """Apply the plan's fields to one FLAC file; `cover` is JPEG bytes or None."""
    audio = FLAC(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags

    for name in strip:
        _drop(tags, STRIP_ALIASES.get(name, (name,)))

    def put(name, value, drop=()):
        if value is None or value == "":
            return
        tags[name] = [str(value)]
        _drop(tags, drop)

    put("ALBUM", album)
    put("TITLE", title)
    put("ARTIST", artist)
    put("ALBUMARTIST", album_artist)
    put("GENRE", genre)
    put("DATE", year, drop=("YEAR",))
    if track is not None:
        put("TRACKNUMBER", track)
        _drop(tags, ("TOTALTRACKS", "TRACKTOTAL"))
        put("TOTALTRACKS", track_total)
    if disc:
        put("DISCNUMBER", disc)
        _drop(tags, ("TOTALDISCS", "DISCTOTAL"))
        put("TOTALDISCS", disc_total)

    if cover is not None:
        audio.clear_pictures()
        width = height = 0
        try:
            from PIL import Image
            with Image.open(io.BytesIO(cover)) as im:
                width, height = im.size
        except Exception:
            pass
        audio.add_picture(_picture(cover, width=width, height=height, depth=24))
    audio.save()
