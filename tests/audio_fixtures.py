# -*- coding: utf-8 -*-
"""Tiny synthetic audio files shared by the tests (no real encoder needed)."""
import struct

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
