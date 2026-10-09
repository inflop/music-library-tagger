# -*- coding: utf-8 -*-
"""
Registry of tag backends: the one place that knows which audio formats exist.

A backend is a module with this interface (see id3_tags.py for the reference):

    NAME                                  short id, also the "format" in backups
    EXTENSIONS                            lower-case file extensions it handles
    read_summary(path) -> dict            title, track, disc, album, artist,
                                          album_artist, genre, year, n_pictures,
                                          fields (list of tag names), comments
    snapshot(path) -> (payload, pictures) payload: JSON-able dict stored in the
                                          backup; pictures: dicts with data, mime,
                                          type, desc (+ any extra keys to keep)
    restore(path, entry, pictures)        undo a run for one file; `entry` is the
                                          backup entry, pictures were read from the
                                          sidecar folder as dicts with data, mime,
                                          type, desc and item (the raw entry)
    write(path, fields, strip, cover, options)
                                          fields: album, title, artist, album_artist,
                                          year, genre, track, track_total, disc,
                                          disc_total (None/"" = leave alone);
                                          strip: ID3 frame ids or native field names
                                          to delete; cover: JPEG bytes or None

Adding a format = one module like that, one line in BACKENDS, and a fixture
writer in tests/audio_fixtures.py (the contract tests then cover it).
"""
import os

import id3_tags
import flac_tags
import ogg_tags
import mp4_tags

BACKENDS = (id3_tags, flac_tags, ogg_tags, mp4_tags)

# Backups written before other formats existed have no "format" on their entries;
# that has always meant MP3/ID3. New entries omit it for this backend too.
LEGACY_FORMAT = id3_tags.NAME

AUDIO_EXT = tuple(ext for b in BACKENDS for ext in b.EXTENSIONS)

# Audio this tool cannot tag. It is never read or touched, only reported.
_KNOWN_AUDIO_EXT = (".m4a", ".mp4", ".aac", ".ogg", ".oga", ".opus", ".wav",
                    ".wv", ".ape", ".wma", ".aiff", ".aif", ".dsf", ".dff",
                    ".mpc", ".m4b")
SKIPPED_AUDIO_EXT = tuple(e for e in _KNOWN_AUDIO_EXT if e not in AUDIO_EXT)


def backend_for(path):
    """The backend that handles `path` by extension, or None."""
    ext = os.path.splitext(str(path))[1].lower()
    for backend in BACKENDS:
        if ext in backend.EXTENSIONS:
            return backend
    return None
