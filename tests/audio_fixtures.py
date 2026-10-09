# -*- coding: utf-8 -*-
"""Tiny synthetic audio files shared by the tests (no real encoder needed).

FACTORIES maps a fixture variant id ("flac", "ogg-vorbis", "m4a-co64", ...) to
(extension, audio payload, writer, backend name); several variants may share one
backend. The contract tests in test_backends.py run against every entry, and
check that every registered backend has at least one, so a new format is covered by
adding a writer and an entry here.
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


def ogg_pages(data):
    """[(header_type, granule, sequence, body), ...] for the pages of an Ogg file."""
    pages, at = [], 0
    while at < len(data):
        assert data[at:at + 4] == b"OggS", "not an Ogg page at %d" % at
        flags = data[at + 5]
        granule, _serial, seq = struct.unpack("<qII", data[at + 6:at + 22])
        count = data[at + 26]
        body_len = sum(data[at + 27:at + 27 + count])
        start = at + 27 + count
        pages.append((flags, granule, seq, data[start:start + body_len]))
        at = start + body_len
    return pages


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


# Where the chunks start inside the mdat payload (the offset table lists all three).
M4A_CHUNK_STARTS = (0, 400, 900)


# A pgap (boolean) atom whose 3-byte payload is not a valid boolean: mutagen cannot parse it,
# keeps the raw atom and writes it back on every save.
M4A_UNPARSEABLE_ATOM = _atom(b"pgap", _atom(b"data", struct.pack(">II", 21, 0) + b"\x01\x02\x03"))


def m4a_bytes(table="stco", ilst_extra=b""):
    """An MP4 audio file mutagen accepts: ftyp, moov (with a chunk offset table), mdat.

    `table` picks the offset atom: "stco" (32 bit) or "co64" (64 bit).
    """
    ftyp = _atom(b"ftyp", b"M4A " + struct.pack(">I", 0) + b"M4A mp42isom")

    def moov(mdat_data):
        mvhd = _atom(b"mvhd", struct.pack(">IIIIIIH", 0, 0, 0, 1000, 5000, 0x10000, 0x100)
                     + b"\x00" * 10 + struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
                     + b"\x00" * 24 + struct.pack(">I", 2))
        mdhd = _atom(b"mdhd", struct.pack(">IIIIIHH", 0, 0, 0, 44100, 220500, 0x55C4, 0))
        hdlr = _atom(b"hdlr", struct.pack(">II", 0, 0) + b"soun" + b"\x00" * 12 + b"\x00")
        sample = (b"\x00" * 6 + struct.pack(">H", 1)            # reserved, data ref index
                  + struct.pack(">HHIHHHH", 0, 0, 0, 2, 16, 0, 0)  # version.. packet size
                  + struct.pack(">I", 44100 << 16))
        stsd = _atom(b"stsd", struct.pack(">II", 0, 1) + _atom(b"mp4a", sample + _atom(b"free")))
        starts = [mdat_data + s for s in M4A_CHUNK_STARTS]
        if table == "co64":
            offsets = _atom(b"co64", struct.pack(">II", 0, len(starts))
                            + b"".join(struct.pack(">Q", o) for o in starts))
        else:
            offsets = _atom(b"stco", struct.pack(">II", 0, len(starts))
                            + b"".join(struct.pack(">I", o) for o in starts))
        stbl = _atom(b"stbl", stsd + offsets)
        trak = _atom(b"trak", _atom(b"mdia", mdhd + hdlr + _atom(b"minf", stbl)))
        udta = b""
        if ilst_extra:
            hdlr_meta = _atom(b"hdlr", struct.pack(">II", 0, 0) + b"mdir" + b"appl" + b"\x00" * 9)
            udta = _atom(b"udta", _atom(b"meta", struct.pack(">I", 0) + hdlr_meta
                                        + _atom(b"ilst", ilst_extra)))
        return _atom(b"moov", mvhd + trak + udta)

    mdat_data = len(ftyp) + len(moov(0)) + 8
    return ftyp + moov(mdat_data) + _atom(b"mdat", M4A_AUDIO)


def m4a_chunk_offsets(data):
    """Every entry of the first stco / co64 table in an MP4 file's bytes."""
    for kind, width, code in ((b"stco", 4, ">I"), (b"co64", 8, ">Q")):
        at = data.find(kind)
        if at >= 0:
            count = struct.unpack(">I", data[at + 8:at + 12])[0]
            return [struct.unpack(code, data[at + 12 + i * width:at + 12 + (i + 1) * width])[0]
                    for i in range(count)]
    raise AssertionError("no chunk offset table found")


def write_m4a(path, *, album, title, cover=None, tagged=True, table="stco", ilst_extra=b""):
    from mutagen.mp4 import MP4, MP4Cover
    Path(path).write_bytes(m4a_bytes(table, ilst_extra))
    if not tagged:
        return
    audio = MP4(str(path))
    if audio.tags is None:
        audio.add_tags()
    audio["\xa9alb"] = [album]
    audio["\xa9nam"] = [title]
    audio["\xa9ART"] = ARTISTS
    audio["\xa9wrt"] = ["Robert Fripp"]
    if cover:
        audio["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
    audio.save()


def write_m4a_co64(path, **kw):
    write_m4a(path, table="co64", **kw)


# variant id -> (extension, audio payload at the end of the file, writer, backend name)
FACTORIES = {
    "m4a": (".m4a", M4A_AUDIO, write_m4a, "m4a"),
    "m4a-co64": (".m4a", M4A_AUDIO, write_m4a_co64, "m4a"),
    "mp3": (".mp3", MP3_BYTES, write_mp3, "mp3"),
    "flac": (".flac", AUDIO_TAIL, write_flac, "flac"),
    "ogg-vorbis": (".ogg", OGG_AUDIO, write_ogg_vorbis, "ogg"),
    "ogg-opus": (".opus", OGG_AUDIO, write_opus, "ogg"),
}
