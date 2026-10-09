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


def _ogg_crc_table():
    table = []
    for i in range(256):
        r = i << 24
        for _ in range(8):
            r = ((r << 1) ^ 0x04C11DB7) if r & 0x80000000 else (r << 1)
        table.append(r & 0xFFFFFFFF)
    return table


_OGG_CRC = _ogg_crc_table()


def ogg_page(packets, *, seq, granule, flags=0, serial=0x1234):
    """One Ogg page holding whole packets (each shorter than 255 * 255 bytes)."""
    lacing = bytearray()
    for packet in packets:
        lacing += b"\xff" * (len(packet) // 255) + bytes([len(packet) % 255])
    header = (b"OggS" + bytes([0, flags]) + struct.pack("<qII", granule, serial, seq)
              + b"\x00\x00\x00\x00" + bytes([len(lacing)]) + bytes(lacing))
    page = header + b"".join(packets)
    crc = 0
    for byte in page:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _OGG_CRC[((crc >> 24) & 0xFF) ^ byte]
    return page[:22] + struct.pack("<I", crc) + page[26:]


OGG_AUDIO = bytes(range(256)) * 3  # one fake audio packet


def ogg_vorbis_bytes():
    ident = (b"\x01vorbis" + struct.pack("<IBIiii", 0, 2, 44100, 0, 128000, 0)
             + b"\xb8\x01")
    comment = b"\x03vorbis" + struct.pack("<I", 4) + b"test" + struct.pack("<I", 0) + b"\x01"
    setup = b"\x05vorbis" + b"\x00" * 20
    return (ogg_page([ident], seq=0, granule=0, flags=2)
            + ogg_page([comment, setup], seq=1, granule=0)
            + ogg_page([OGG_AUDIO], seq=2, granule=44100, flags=4))


def opus_bytes():
    head = b"OpusHead" + bytes([1, 2]) + struct.pack("<HIhB", 312, 48000, 0, 0)
    tags = b"OpusTags" + struct.pack("<I", 4) + b"test" + struct.pack("<I", 0)
    return (ogg_page([head], seq=0, granule=0, flags=2)
            + ogg_page([tags], seq=1, granule=0)
            + ogg_page([OGG_AUDIO], seq=2, granule=48312, flags=4))


def _write_ogg(path, raw, mutagen_cls, *, album, title, cover, tagged):
    import base64
    Path(path).write_bytes(raw)
    if not tagged:
        return
    audio = mutagen_cls(str(path))
    audio["ALBUM"] = [album]
    audio["TITLE"] = [title]
    audio["ARTIST"] = ARTISTS
    audio["COMPOSER"] = ["Robert Fripp"]
    if cover:
        pic = Picture()
        pic.type, pic.mime, pic.desc, pic.data = 3, "image/jpeg", "Front", cover
        audio["METADATA_BLOCK_PICTURE"] = [base64.b64encode(pic.write()).decode("ascii")]
    audio.save()


def write_ogg_vorbis(path, *, album, title, cover=None, tagged=True):
    from mutagen.oggvorbis import OggVorbis
    _write_ogg(path, ogg_vorbis_bytes(), OggVorbis, album=album, title=title,
               cover=cover, tagged=tagged)


def write_opus(path, *, album, title, cover=None, tagged=True):
    from mutagen.oggopus import OggOpus
    _write_ogg(path, opus_bytes(), OggOpus, album=album, title=title,
               cover=cover, tagged=tagged)


def _atom(kind, payload=b""):
    return struct.pack(">I", 8 + len(payload)) + kind + payload


M4A_AUDIO = bytes(range(256)) * 5  # the mdat payload


def m4a_bytes():
    """An MP4 audio file mutagen accepts: ftyp, moov (with a chunk offset table), mdat."""
    ftyp = _atom(b"ftyp", b"M4A " + struct.pack(">I", 0) + b"M4A mp42isom")

    def moov(mdat_offset):
        mvhd = _atom(b"mvhd", struct.pack(">IIIIIIH", 0, 0, 0, 1000, 5000, 0x10000, 0x100)
                     + b"\x00" * 10 + struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
                     + b"\x00" * 24 + struct.pack(">I", 2))
        mdhd = _atom(b"mdhd", struct.pack(">IIIIIHH", 0, 0, 0, 44100, 220500, 0x55C4, 0))
        hdlr = _atom(b"hdlr", struct.pack(">II", 0, 0) + b"soun" + b"\x00" * 12 + b"\x00")
        sample = (b"\x00" * 6 + struct.pack(">H", 1)            # reserved, data ref index
                  + struct.pack(">HHIHHHH", 0, 0, 0, 2, 16, 0, 0)  # version.. packet size
                  + struct.pack(">I", 44100 << 16))
        stsd = _atom(b"stsd", struct.pack(">II", 0, 1) + _atom(b"mp4a", sample + _atom(b"free")))
        stco = _atom(b"stco", struct.pack(">III", 0, 1, mdat_offset))
        stbl = _atom(b"stbl", stsd + stco)
        trak = _atom(b"trak", _atom(b"mdia", mdhd + hdlr + _atom(b"minf", stbl)))
        return _atom(b"moov", mvhd + trak)

    mdat_offset = len(ftyp) + len(moov(0)) + 8
    return ftyp + moov(mdat_offset) + _atom(b"mdat", M4A_AUDIO)


def write_m4a(path, *, album, title, cover=None, tagged=True):
    from mutagen.mp4 import MP4, MP4Cover
    Path(path).write_bytes(m4a_bytes())
    if not tagged:
        return
    audio = MP4(str(path))
    audio.add_tags()
    audio["\xa9alb"] = [album]
    audio["\xa9nam"] = [title]
    audio["\xa9ART"] = ARTISTS
    audio["\xa9wrt"] = ["Robert Fripp"]
    if cover:
        audio["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
    audio.save()


# variant id -> (extension, audio payload at the end of the file, writer, backend name)
FACTORIES = {
    "m4a": (".m4a", M4A_AUDIO, write_m4a, "m4a"),
    "mp3": (".mp3", MP3_BYTES, write_mp3, "mp3"),
    "flac": (".flac", AUDIO_TAIL, write_flac, "flac"),
    "ogg-vorbis": (".ogg", OGG_AUDIO, write_ogg_vorbis, "ogg"),
    "ogg-opus": (".opus", OGG_AUDIO, write_opus, "ogg"),
}
