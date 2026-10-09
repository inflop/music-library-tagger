# -*- coding: utf-8 -*-
"""
FLAC (Vorbis comments + picture blocks) tag backend; see tagio.py for the interface.

Only the metadata blocks are rewritten; mutagen leaves the audio frames alone. The
field mapping lives in vorbis_common.py. A restore replaces all comments and all
pictures with the backed-up ones; a FLAC that had no comment block ends up without
one. Covers are FLAC Picture blocks, type 3 (front).
"""
from mutagen.flac import FLAC

import vorbis_common as vc

NAME = "flac"
EXTENSIONS = (".flac",)


def read_summary(path):
    audio = FLAC(path)
    return vc.summarize(audio.tags, len(audio.pictures))


def snapshot(path):
    """Return (payload, pictures) exactly as stored, for the backup.

    payload["vorbis"] is a list of [name, value] pairs (order, case and repeated
    names kept) or None when the file has no Vorbis comment block at all.
    """
    audio = FLAC(path)
    comments = None if audio.tags is None else [[k, v] for k, v in audio.tags]
    return {"vorbis": comments}, [vc.picture_dict(p) for p in audio.pictures]


def restore(path, entry, pictures):
    """Put back what snapshot() returned: replace every comment and picture."""
    comments = vc.comments_of(entry)
    audio = FLAC(path)
    audio.clear_pictures()
    for pic in pictures:
        audio.add_picture(vc.picture_from_backup(pic))
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


def write(path, fields, strip, cover, options=None):
    """Apply the plan's fields to one FLAC file; `cover` is JPEG bytes or None."""
    audio = FLAC(path)
    if audio.tags is None:
        audio.add_tags()
    vc.apply_fields(audio.tags, fields, strip)
    if cover is not None:
        audio.clear_pictures()
        audio.add_picture(vc.front_cover(cover))
    audio.save()
