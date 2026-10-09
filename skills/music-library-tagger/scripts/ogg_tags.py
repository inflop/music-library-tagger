# -*- coding: utf-8 -*-
"""
Ogg Vorbis and Opus tag backend; see tagio.py for the interface.

Both use Vorbis comments, so the field mapping is shared with FLAC
(vorbis_common.py). What differs is the cover: there is no picture block, it is a
base64-encoded FLAC Picture structure in a METADATA_BLOCK_PICTURE comment.

- snapshot() takes those comments out of the stored comment list and returns them as
  pictures, so the backup keeps the (large) images in its sidecar folder rather than
  in the JSON. A METADATA_BLOCK_PICTURE that cannot be decoded stays in the comment
  list verbatim, so a restore does not lose it.
- Legacy COVERART / COVERARTMIME comments (a bare base64 image) are left alone until
  a new cover is written, which removes them so no stale picture remains.
- Only Ogg Vorbis and Opus streams are handled. Speex, Ogg FLAC and Theora files
  share the .ogg/.oga extensions; they raise ValueError and show up as unreadable
  instead of being touched.
"""
import base64
import binascii

from mutagen import File as MFile
from mutagen.flac import Picture
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

import vorbis_common as vc

NAME = "ogg"
EXTENSIONS = (".ogg", ".oga", ".opus")

PICTURE_FIELD = "metadata_block_picture"
LEGACY_COVER_FIELDS = ("COVERART", "COVERARTMIME")


def _open(path):
    audio = MFile(path)
    if not isinstance(audio, (OggVorbis, OggOpus)):
        raise ValueError("not an Ogg Vorbis or Opus stream: %s" % path)
    return audio


def _decode(value):
    """The Picture inside one METADATA_BLOCK_PICTURE value, or None if damaged."""
    try:
        return Picture(base64.b64decode(value, validate=True))
    except (binascii.Error, ValueError, Exception):
        return None


def _encode(pic):
    return base64.b64encode(pic.write()).decode("ascii")


def _split(tags):
    """([[name, value], ...] without usable pictures, [Picture, ...]).

    A picture comment that cannot be decoded, or decodes to an empty image, stays in
    the comment list verbatim: backup_tags() does not store images without data, so
    treating it as a picture would make it vanish on restore.
    """
    comments, pictures = [], []
    for key, value in tags:
        if str(key).lower() == PICTURE_FIELD:
            pic = _decode(value)
            if pic is not None and pic.data:
                pictures.append(pic)
                continue
        comments.append([key, value])
    return comments, pictures


def read_summary(path):
    audio = _open(path)
    _, pictures = _split(audio.tags)
    legacy = 1 if audio.tags.get("COVERART") else 0
    summary = vc.summarize(audio.tags, len(pictures) + legacy)
    # The picture comments are covers, not metadata worth listing as fields.
    summary["fields"] = [f for f in summary["fields"] if f != PICTURE_FIELD.upper()]
    return summary


def snapshot(path):
    audio = _open(path)
    comments, pictures = _split(audio.tags)
    return {"vorbis": comments}, [vc.picture_dict(p) for p in pictures]


def restore(path, entry, pictures):
    comments = vc.comments_of(entry)
    if comments is None:
        raise ValueError("an Ogg file always has a comment header; the backup says it had none")
    audio = _open(path)
    audio.tags.clear()
    for key, value in comments:
        audio.tags.append((key, value))
    for pic in pictures:
        audio.tags.append((PICTURE_FIELD, _encode(vc.picture_from_backup(pic))))
    audio.save()


def write(path, fields, strip, cover, options=None):
    """Apply the plan's fields to one file; `cover` is JPEG bytes or None."""
    audio = _open(path)
    tags = audio.tags
    vc.apply_fields(tags, fields, strip)
    if cover is not None:
        vc.drop(tags, (PICTURE_FIELD,) + LEGACY_COVER_FIELDS)
        tags[PICTURE_FIELD] = [_encode(vc.front_cover(cover))]
    audio.save()
