# -*- coding: utf-8 -*-
"""Tiny synthetic audio files shared by the tests (no real encoder needed).

FACTORIES maps a backend name to (extension, audio payload, writer). The contract
tests in test_backends.py run against every entry, so a new format is covered by
adding one writer here.
"""
import io
import struct
from pathlib import Path

from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TALB, TCOM, TIT2, TPE1
from PIL import Image

# Enough MPEG frame headers that mutagen accepts the file as audio.
MP3_BYTES = (b"\xff\xfb\x90\x64" + b"\x00" * 413) * 20

AUDIO_TAIL = bytes(range(256)) * 4  # stands in for the encoded FLAC frames


def flac_bytes():
    """A FLAC stream mutagen accepts: marker + STREAMINFO + fake audio."""
    # min/max block size, min/max frame size (24 bit each)
    streaminfo = struct.pack(">HH", 4096, 4096) + b"\x00" * 6
    # 20 bit sample rate, 3 bit channels-1, 5 bit bps-1, 36 bit total samples
    packed = (44100 << 44) | (1 << 41) | (15 << 36) | 44100
    streaminfo += packed.to_bytes(8, "big") + b"\x00" * 16  # + MD5
    header = bytes([0x80]) + len(streaminfo).to_bytes(3, "big")  # last block
    return b"fLaC" + header + streaminfo + AUDIO_TAIL


def jpeg(color, size=(64, 64)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


ARTISTS = ["Artysta A", "Artysta B"]


def write_mp3(path, *, album, title, cover=None, tagged=True):
    Path(path).write_bytes(MP3_BYTES)
    if not tagged:
        return
    tags = ID3()
    tags.add(TALB(encoding=3, text=[album]))
    tags.add(TIT2(encoding=3, text=[title]))
    tags.add(TPE1(encoding=3, text=ARTISTS))
    tags.add(TCOM(encoding=3, text=["Robert Fripp"]))
    if cover:
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Front",
                      data=cover))
    tags.save(str(path), v2_version=3)


def write_flac(path, *, album, title, cover=None, tagged=True):
    Path(path).write_bytes(flac_bytes())
    if not tagged:
        return
    audio = FLAC(str(path))
    audio.add_tags()
    audio.tags["ALBUM"] = [album]
    audio.tags["TITLE"] = [title]
    audio.tags["ARTIST"] = ARTISTS
    audio.tags["COMPOSER"] = ["Robert Fripp"]
    if cover:
        pic = Picture()
        pic.type, pic.mime, pic.desc, pic.data = 3, "image/jpeg", "Front", cover
        audio.add_picture(pic)
    audio.save()


FACTORIES = {
    "mp3": (".mp3", MP3_BYTES, write_mp3),
    "flac": (".flac", AUDIO_TAIL, write_flac),
}
