# -*- coding: utf-8 -*-
"""FLAC support: apply, backup and restore must work like they do for MP3.

Run with:  python -m unittest discover -s tests -v
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                       / "skills" / "music-library-tagger" / "scripts"))

import apply_plan  # noqa: E402
from audio_fixtures import AUDIO_TAIL, MP3_BYTES, flac_bytes  # noqa: E402
from mutagen.flac import FLAC, Picture  # noqa: E402
from mutagen.id3 import ID3, TALB  # noqa: E402
from PIL import Image  # noqa: E402

def jpeg(color, size=(600, 600)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def audio_tail(path):
    with open(path, "rb") as f:
        return f.read()[-len(AUDIO_TAIL):]


class FlacLibrary(unittest.TestCase):
    ORIGINAL = (200, 0, 0)
    REPLACEMENT = (0, 160, 0)

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mlt-flac-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = os.path.join(self.tmp, "Band")
        self.disc = os.path.join(self.root, "1974 - Red")
        os.makedirs(self.disc)
        self.tracks = ["01 - Red.flac", "02 - Fallen Angel.flac"]
        for i, name in enumerate(self.tracks, 1):
            path = os.path.join(self.disc, name)
            with open(path, "wb") as f:
                f.write(flac_bytes())
            audio = FLAC(path)
            audio.add_tags()
            audio.tags["ALBUM"] = ["old album name"]
            audio.tags["TITLE"] = ["track %d" % i]
            audio.tags["ARTIST"] = ["Artysta A", "Artysta B"]
            audio.tags["COMMENT"] = ["ripped with junk"]
            audio.tags["COMPOSER"] = ["Robert Fripp"]
            audio.tags["MUSICBRAINZ_TRACKID"] = ["mbid-%d" % i]
            audio.tags["TRACKTOTAL"] = ["9"]
            audio.tags["YEAR"] = ["1999"]
            pic = Picture()
            pic.type, pic.mime, pic.desc = 3, "image/jpeg", "Front"
            pic.data = jpeg(self.ORIGINAL)
            audio.add_picture(pic)
            audio.save()

        self.new_cover = os.path.join(self.disc, "new_cover.jpg")
        with open(self.new_cover, "wb") as f:
            f.write(jpeg(self.REPLACEMENT))
        self.backup_dir = os.path.join(self.root, ".music-tagger")
        os.makedirs(self.backup_dir)
        self.backup = os.path.join(self.backup_dir, "tags_backup_test.json")

    def plan(self, strip=()):
        return {
            "root": self.root,
            "options": {"cover_embed": True, "cover_folder_jpg": True,
                        "cover_max_px": 1400, "artist": "Band",
                        "album_artist": "Band", "genre": "Progressive Rock",
                        "strip_frames": list(strip)},
            "albums": [{
                "album": "Red", "year": 1974, "album_path": "1974 - Red",
                "discs": [{
                    "path": "1974 - Red", "disc": 1, "disc_total": 2,
                    "cover": "1974 - Red/new_cover.jpg",
                    "tracks": [{"file": n, "track": i, "track_total": 2,
                                "title": "Track %d" % i}
                               for i, n in enumerate(self.tracks, 1)],
                }],
            }],
        }

    def run_apply(self, plan=None, dry=False):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            apply_plan.apply(plan or self.plan(), dry)
        return buf.getvalue()

    def run_backup(self, plan=None):
        with contextlib.redirect_stdout(io.StringIO()):
            apply_plan.backup_tags(self.root, plan or self.plan(), self.backup)

    def run_restore(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            apply_plan.restore(self.backup)
        return buf.getvalue()

    def flac(self, name=None):
        return FLAC(os.path.join(self.disc, name or self.tracks[0]))


class TestApplyFlac(FlacLibrary):
    def test_apply_writes_vorbis_fields(self):
        self.run_apply()
        t = self.flac().tags
        self.assertEqual(t["ALBUM"], ["Red"])
        self.assertEqual(t["TITLE"], ["Track 1"])
        self.assertEqual(t["ARTIST"], ["Band"])
        self.assertEqual(t["ALBUMARTIST"], ["Band"])
        self.assertEqual(t["GENRE"], ["Progressive Rock"])
        self.assertEqual(t["DATE"], ["1974"])
        self.assertEqual(t["TRACKNUMBER"], ["1"])
        self.assertEqual(t["TOTALTRACKS"], ["2"])
        self.assertEqual(t["DISCNUMBER"], ["1"])
        self.assertEqual(t["TOTALDISCS"], ["2"])

    def test_apply_removes_conflicting_legacy_fields(self):
        self.run_apply()
        t = self.flac().tags
        self.assertNotIn("TRACKTOTAL", t)
        self.assertNotIn("YEAR", t)

    def test_apply_keeps_fields_it_does_not_manage(self):
        self.run_apply()
        t = self.flac().tags
        self.assertEqual(t["MUSICBRAINZ_TRACKID"], ["mbid-1"])
        self.assertEqual(t["COMPOSER"], ["Robert Fripp"])
        self.assertEqual(t["COMMENT"], ["ripped with junk"])

    def test_strip_frames_accepts_id3_ids_and_vorbis_names(self):
        self.run_apply(self.plan(strip=["COMM", "composer"]))
        t = self.flac().tags
        self.assertNotIn("COMMENT", t)
        self.assertNotIn("COMPOSER", t)
        self.assertEqual(t["MUSICBRAINZ_TRACKID"], ["mbid-1"])

    def test_apply_replaces_cover_with_a_single_front_picture(self):
        self.run_apply()
        pics = self.flac().pictures
        self.assertEqual(len(pics), 1)
        self.assertEqual((pics[0].type, pics[0].mime), (3, "image/jpeg"))
        with Image.open(io.BytesIO(pics[0].data)) as im:
            px = im.convert("RGB").getpixel((10, 10))
        self.assertTrue(all(abs(a - b) <= 25
                            for a, b in zip(px, self.REPLACEMENT)), px)
        self.assertTrue(os.path.isfile(os.path.join(self.disc, "cover.jpg")))

    def test_audio_bytes_are_untouched(self):
        self.run_apply()
        for name in self.tracks:
            self.assertEqual(audio_tail(os.path.join(self.disc, name)),
                             AUDIO_TAIL)

    def test_dry_run_changes_nothing(self):
        path = os.path.join(self.disc, self.tracks[0])
        before = Path(path).read_bytes()
        out = self.run_apply(dry=True)
        self.assertEqual(Path(path).read_bytes(), before)
        self.assertIn("covers_embedded", out)
        self.assertFalse(os.path.exists(os.path.join(self.disc, "cover.jpg")))

    def test_track_without_total_drops_stale_total(self):
        plan = self.plan()
        plan["albums"][0]["discs"][0]["tracks"][0].pop("track_total")
        self.run_apply(plan)
        self.assertNotIn("TOTALTRACKS", self.flac().tags)


class TestBackupRestoreFlac(FlacLibrary):
    def snapshot(self, name):
        a = self.flac(name)
        return ([(k, v) for k, v in a.tags],
                [(p.type, p.mime, p.desc, p.data) for p in a.pictures])

    def test_round_trip_restores_tags_and_pictures(self):
        before = {n: self.snapshot(n) for n in self.tracks}
        self.run_backup()
        self.run_apply()
        self.assertNotEqual(self.snapshot(self.tracks[0]), before[self.tracks[0]])
        out = self.run_restore()
        self.assertIn("Restored tags on 2 files", out)
        for n in self.tracks:
            self.assertEqual(self.snapshot(n), before[n])
            self.assertEqual(audio_tail(os.path.join(self.disc, n)), AUDIO_TAIL)

    def test_backup_marks_flac_entries_and_stores_art_once(self):
        self.run_backup()
        data = json.loads(Path(self.backup).read_text(encoding="utf-8"))
        entry = data["files"]["1974 - Red/01 - Red.flac"]
        self.assertEqual(entry["format"], "flac")
        self.assertTrue(entry["had_apic"])
        art_dir = os.path.splitext(self.backup)[0] + "_art"
        self.assertEqual(len(os.listdir(art_dir)), 1)  # same cover on 2 tracks

    def test_flac_without_vorbis_block_stays_without_one(self):
        path = os.path.join(self.disc, self.tracks[0])
        # mutagen always writes a comment block on save(), so start from raw bytes.
        Path(path).write_bytes(flac_bytes())
        self.assertIsNone(FLAC(path).tags)
        self.run_backup()
        self.run_apply()
        self.assertEqual(self.flac().tags["ALBUM"], ["Red"])
        self.run_restore()
        restored = FLAC(path)
        self.assertIsNone(restored.tags)
        self.assertEqual(restored.pictures, [])
        self.assertEqual(audio_tail(path), AUDIO_TAIL)

    def test_multi_value_fields_survive_a_round_trip(self):
        self.run_backup()
        self.run_apply()
        self.run_restore()
        self.assertEqual(self.flac().tags["ARTIST"], ["Artysta A", "Artysta B"])

    def test_restore_leaves_a_non_flac_target_named_flac_alone(self):
        self.run_backup()
        path = os.path.join(self.disc, self.tracks[1])
        Path(path).write_bytes(b"not audio at all")
        out = self.run_restore()
        self.assertIn("could not restore", out)
        self.assertEqual(Path(path).read_bytes(), b"not audio at all")
        self.assertEqual(self.flac(self.tracks[0]).tags["ALBUM"],
                         ["old album name"])

    def test_restore_does_not_write_flac_data_into_an_mp3(self):
        self.run_backup()
        data = json.loads(Path(self.backup).read_text(encoding="utf-8"))
        entry = data["files"].pop("1974 - Red/02 - Fallen Angel.flac")
        data["files"]["1974 - Red/02 - Fallen Angel.mp3"] = entry
        Path(self.backup).write_text(json.dumps(data), encoding="utf-8")
        mp3 = os.path.join(self.disc, "02 - Fallen Angel.mp3")
        Path(mp3).write_bytes(b"\xff\xfb\x90\x64" + b"\x00" * 413)
        self.run_restore()
        # a flac-format entry must not be applied through the ID3 path
        with self.assertRaises(Exception):
            ID3(mp3)


class TestMixedFolder(FlacLibrary):
    def test_mp3_and_flac_are_handled_in_one_run(self):
        mp3 =os.path.join(self.disc, "03 - One More.mp3")
        Path(mp3).write_bytes(MP3_BYTES)
        tags = ID3()
        tags.add(TALB(encoding=3, text=["old"]))
        tags.save(mp3, v2_version=3)

        plan = self.plan()
        plan["albums"][0]["discs"][0]["tracks"].append(
            {"file": "03 - One More.mp3", "track": 3, "track_total": 3,
             "title": "One More"})
        self.run_backup(plan)
        self.run_apply(plan)
        self.assertEqual(self.flac().tags["ALBUM"], ["Red"])
        self.assertEqual(str(ID3(mp3)["TALB"]), "Red")
        self.run_restore()
        self.assertEqual(str(ID3(mp3)["TALB"]), "old")
        self.assertEqual(self.flac().tags["ALBUM"], ["old album name"])


if __name__ == "__main__":
    unittest.main()
