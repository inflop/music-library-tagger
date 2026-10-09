# -*- coding: utf-8 -*-
"""M4A (AAC / ALAC) specifics: atoms, value types, chunk offsets.

The behaviour shared with every format (apply, backup, restore, dry run, untagged
files) is covered by test_backends.py.

Run with:  python -m unittest discover -s tests -v
"""
import contextlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                       / "skills" / "music-library-tagger" / "scripts"))

import apply_plan  # noqa: E402
import mp4_tags  # noqa: E402
from audio_fixtures import (M4A_AUDIO, M4A_CHUNK_STARTS, jpeg, m4a_bytes,  # noqa: E402
                            m4a_chunk_offsets, write_m4a, write_m4a_co64)
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm  # noqa: E402
from PIL import Image  # noqa: E402

ANALYZE = (Path(__file__).resolve().parents[1]
           / "skills" / "music-library-tagger" / "scripts" / "analyze.py")


def png(color=(5, 6, 7)):
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(buf, "PNG")
    return buf.getvalue()


class M4aLibrary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mlt-m4a-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = os.path.join(self.tmp, "Band")
        self.disc = os.path.join(self.root, "1974 - Red")
        os.makedirs(self.disc)
        os.makedirs(os.path.join(self.root, ".music-tagger"))
        self.backup = os.path.join(self.root, ".music-tagger", "backup.json")
        Path(self.disc, "new.jpg").write_bytes(jpeg((0, 160, 0), size=(600, 600)))

    def track(self, name="01 - Red.m4a", _writer=write_m4a, **kw):
        path = os.path.join(self.disc, name)
        args = dict(album="old", title="old title", cover=jpeg((200, 0, 0)))
        args.update(kw)
        _writer(path, **args)
        return path

    def plan(self, *paths, strip=(), genre="Prog"):
        return {
            "root": self.root,
            "options": {"cover_embed": True, "cover_folder_jpg": False,
                        "cover_max_px": 1400, "genre": genre,
                        "strip_frames": list(strip)},
            "albums": [{"album": "Red", "year": 1974, "album_path": "1974 - Red",
                        "discs": [{"path": "1974 - Red", "disc": 1, "disc_total": 2,
                                   "cover": "1974 - Red/new.jpg",
                                   "tracks": [{"file": os.path.basename(p), "track": i,
                                               "track_total": len(paths),
                                               "title": "Track %d" % i}
                                              for i, p in enumerate(paths, 1)]}]}],
        }

    @staticmethod
    def quiet(fn, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn(*args)
        return buf.getvalue()


class TestAtoms(M4aLibrary):
    def test_apply_writes_the_standard_atoms(self):
        path = self.track()
        self.quiet(apply_plan.apply, self.plan(path, self.track("02.m4a")), False)
        tags = MP4(path).tags
        self.assertEqual(tags["\xa9alb"], ["Red"])
        self.assertEqual(tags["\xa9nam"], ["Track 1"])
        self.assertEqual(tags["\xa9day"], ["1974"])
        self.assertEqual(tags["\xa9gen"], ["Prog"])
        self.assertEqual(tags["trkn"], [(1, 2)])
        self.assertEqual(tags["disk"], [(1, 2)])

    def test_track_without_a_total_is_written_with_total_zero(self):
        path = self.track()
        plan = self.plan(path)
        del plan["albums"][0]["discs"][0]["tracks"][0]["track_total"]
        self.quiet(apply_plan.apply, plan, False)
        self.assertEqual(MP4(path).tags["trkn"], [(1, 0)])
        self.assertEqual(mp4_tags.read_summary(path)["track"], "1")

    def test_the_cover_is_one_jpeg_atom_and_audio_is_untouched(self):
        path = self.track()
        self.quiet(apply_plan.apply, self.plan(path), False)
        covers = MP4(path).tags["covr"]
        self.assertEqual(len(covers), 1)
        self.assertEqual(covers[0].imageformat, MP4Cover.FORMAT_JPEG)
        data = Path(path).read_bytes()
        self.assertEqual(data[-len(M4A_AUDIO):], M4A_AUDIO)

    def test_every_chunk_offset_still_points_at_its_audio_after_a_save(self):
        # Start from a file with no tags at all: mutagen has to add the whole
        # udta/meta/ilst tree to moov, which moves mdat, so the offsets must be
        # rewritten. A file that already carries (padded) tags can pass without that.
        for writer in (write_m4a, write_m4a_co64):
            with self.subTest(table=writer.__name__):
                path = self.track(name=writer.__name__ + ".m4a", tagged=False,
                                  _writer=writer)
                before = Path(path).read_bytes()
                self.assertIsNone(MP4(path).tags)
                self.quiet(apply_plan.apply, self.plan(path), False)
                data = Path(path).read_bytes()
                self.assertGreater(len(data), len(before))   # moov grew
                offsets = m4a_chunk_offsets(data)
                self.assertEqual(len(offsets), len(M4A_CHUNK_STARTS))
                self.assertNotEqual(offsets, m4a_chunk_offsets(before))  # they moved
                for offset, start in zip(offsets, M4A_CHUNK_STARTS):
                    self.assertEqual(data[offset:offset + 16], M4A_AUDIO[start:start + 16])
                self.assertEqual(data[-len(M4A_AUDIO):], M4A_AUDIO)
                self.assertEqual(MP4(path).info.length, 5.0)

    def test_strip_frames_accepts_id3_ids_and_native_atoms(self):
        path = self.track()
        audio = MP4(path)
        audio["\xa9cmt"] = ["ripped with junk"]
        audio["\xa9too"] = ["Some Encoder 1.0"]
        audio.save()
        self.quiet(apply_plan.apply, self.plan(path, strip=["COMM", "TENC", "\xa9WRT"]), False)
        tags = MP4(path).tags
        for gone in ("\xa9cmt", "\xa9too", "\xa9wrt"):
            self.assertNotIn(gone, tags)


class TestRestoreFidelity(M4aLibrary):
    def test_value_types_survive_apply_and_restore(self):
        path = self.track()
        audio = MP4(path)
        audio["tmpo"] = [128]
        audio["cpil"] = True
        audio["rtng"] = [1]
        audio["\xa9cmt"] = ["liner notes"]
        audio["----:com.apple.iTunes:LABEL"] = [MP4FreeForm(b"Island", dataformat=1)]
        audio["----:com.apple.iTunes:BLOB"] = [MP4FreeForm(b"\x00\x01\xff", dataformat=0)]
        audio.save()
        before = {k: MP4(path).tags[k] for k in MP4(path).tags}

        plan = self.plan(path)
        self.quiet(apply_plan.backup_tags, self.root, plan, self.backup)
        self.quiet(apply_plan.apply, plan, False)
        self.quiet(apply_plan.restore, self.backup)

        after = MP4(path).tags
        self.assertEqual(sorted(after.keys()), sorted(before))
        for key, value in before.items():
            self.assertEqual(after[key], value, key)
        label = after["----:com.apple.iTunes:LABEL"][0]
        blob = after["----:com.apple.iTunes:BLOB"][0]
        self.assertEqual((bytes(label), label.dataformat), (b"Island", 1))
        self.assertEqual((bytes(blob), blob.dataformat), (b"\x00\x01\xff", 0))
        self.assertEqual(after["covr"][0], before["covr"][0])

    def test_atoms_the_tool_cannot_represent_are_left_alone(self):
        path = self.track()
        audio = MP4(path)
        audio["----:com.apple.iTunes:EMPTY"] = []
        audio.save()
        self.quiet(apply_plan.backup_tags, self.root, self.plan(path), self.backup)
        self.quiet(apply_plan.apply, self.plan(path), False)
        self.quiet(apply_plan.restore, self.backup)
        self.assertEqual(MP4(path).tags["\xa9alb"], ["old"])

    def test_png_covers_keep_their_format(self):
        path = self.track()
        audio = MP4(path)
        audio["covr"] = [MP4Cover(png(), imageformat=MP4Cover.FORMAT_PNG)]
        audio.save()
        self.assertEqual(MP4(path).tags["covr"][0].imageformat, MP4Cover.FORMAT_PNG)
        self.quiet(apply_plan.backup_tags, self.root, self.plan(path), self.backup)
        self.quiet(apply_plan.apply, self.plan(path), False)
        self.assertEqual(MP4(path).tags["covr"][0].imageformat, MP4Cover.FORMAT_JPEG)
        self.quiet(apply_plan.restore, self.backup)
        cover = MP4(path).tags["covr"][0]
        self.assertEqual(cover.imageformat, MP4Cover.FORMAT_PNG)
        self.assertEqual(bytes(cover), png())

    def test_backup_keeps_images_out_of_the_json(self):
        a, b = self.track("01.m4a"), self.track("02.m4a")  # same cover on both
        self.quiet(apply_plan.backup_tags, self.root, self.plan(a, b), self.backup)
        raw = Path(self.backup).read_text(encoding="utf-8")
        entry = json.loads(raw)["files"]["1974 - Red/01.m4a"]
        self.assertEqual(entry["format"], "m4a")
        self.assertEqual(len(entry["apic"]), 1)
        self.assertLess(len(raw), 4000)
        art_dir = os.path.splitext(self.backup)[0] + "_art"
        self.assertEqual(len(os.listdir(art_dir)), 1)

    def test_a_damaged_backup_entry_costs_only_that_file(self):
        a, b = self.track("01.m4a"), self.track("02.m4a")
        plan = self.plan(a, b)
        self.quiet(apply_plan.backup_tags, self.root, plan, self.backup)
        data = json.loads(Path(self.backup).read_text(encoding="utf-8"))
        data["files"]["1974 - Red/01.m4a"]["mp4"] = [["\xa9alb", "oops"]]
        Path(self.backup).write_text(json.dumps(data), encoding="utf-8")
        self.quiet(apply_plan.apply, plan, False)
        out = self.quiet(apply_plan.restore, self.backup)
        self.assertIn("could not restore", out)
        self.assertEqual(MP4(b).tags["\xa9alb"], ["old"])


class TestAnalyzeM4a(M4aLibrary):
    def run_analyze(self):
        out = Path(self.tmp) / "out.json"
        done = subprocess.run([sys.executable, str(ANALYZE), self.root, "--json", str(out)],
                              capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(out.read_text(encoding="utf-8"))

    def test_m4a_is_read_and_no_longer_reported_as_skipped(self):
        path = self.track("01.m4a", album="Red", title="Starless")
        audio = MP4(path)
        audio["trkn"] = [(3, 12)]
        audio["disk"] = [(1, 2)]
        audio["\xa9day"] = ["1974"]
        audio.save()
        result = self.run_analyze()
        self.assertEqual(result["formats"], {"m4a": 1})
        self.assertEqual(result["skipped_audio"], {})
        track = result["albums"][0]["discs"][0]["tracks"][0]
        self.assertEqual((track["title"], track["track"], track["disc"], track["has_cover"]),
                         ("Starless", "3/12", "1/2", True))
        self.assertEqual(result["albums"][0]["current_years"], ["1974"])

    def test_mp4_video_and_m4b_stay_skipped(self):
        self.track("01.m4a")
        Path(self.disc, "clip.mp4").write_bytes(m4a_bytes())
        Path(self.disc, "book.m4b").write_bytes(m4a_bytes())
        result = self.run_analyze()
        self.assertEqual(sorted(result["skipped_audio"]), [".m4b", ".mp4"])

    def test_an_unreadable_m4a_is_an_error_entry(self):
        Path(self.disc, "01.m4a").write_bytes(b"not an mp4 file")
        track = self.run_analyze()["albums"][0]["discs"][0]["tracks"][0]
        self.assertEqual(track, {"file": "01.m4a", "format": "m4a", "error": True})


if __name__ == "__main__":
    unittest.main()
