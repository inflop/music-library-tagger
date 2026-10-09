# -*- coding: utf-8 -*-
"""Ogg Vorbis / Opus specifics: the base64 picture comment and what is not Ogg Vorbis.

The behaviour shared with every format (apply, backup, restore, dry run, untagged
files) is covered by test_backends.py.

Run with:  python -m unittest discover -s tests -v
"""
import base64
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                       / "skills" / "music-library-tagger" / "scripts"))

import apply_plan  # noqa: E402
import ogg_tags  # noqa: E402
from audio_fixtures import (OGG_AUDIO, jpeg, ogg_page, write_ogg_vorbis,  # noqa: E402
                            write_opus)
from mutagen import File as MFile  # noqa: E402
from mutagen.flac import Picture  # noqa: E402

ANALYZE = (Path(__file__).resolve().parents[1]
           / "skills" / "music-library-tagger" / "scripts" / "analyze.py")
WRITERS = {".ogg": write_ogg_vorbis, ".opus": write_opus}


class OggLibrary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mlt-ogg-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = os.path.join(self.tmp, "Band")
        self.disc = os.path.join(self.root, "1974 - Red")
        os.makedirs(self.disc)
        os.makedirs(os.path.join(self.root, ".music-tagger"))
        self.backup = os.path.join(self.root, ".music-tagger", "backup.json")
        Path(self.disc, "new.jpg").write_bytes(jpeg((0, 160, 0), size=(600, 600)))

    def track(self, ext, name="01 - Red", **kw):
        path = os.path.join(self.disc, name + ext)
        args = dict(album="old", title="old title", cover=jpeg((200, 0, 0)),
                    tagged=True)
        args.update(kw)
        WRITERS[ext](path, **args)
        return path

    def plan(self, *paths, strip=()):
        return {
            "root": self.root,
            "options": {"cover_embed": True, "cover_folder_jpg": False,
                        "cover_max_px": 1400, "strip_frames": list(strip)},
            "albums": [{"album": "Red", "year": 1974, "album_path": "1974 - Red",
                        "discs": [{"path": "1974 - Red", "cover": "1974 - Red/new.jpg",
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


class TestOggCover(OggLibrary):
    def test_cover_is_a_front_picture_in_a_base64_comment(self):
        for ext in WRITERS:
            with self.subTest(ext=ext):
                path = self.track(ext)
                self.quiet(apply_plan.apply, self.plan(path), False)
                values = MFile(path).tags["METADATA_BLOCK_PICTURE"]
                self.assertEqual(len(values), 1)
                pic = Picture(base64.b64decode(values[0]))
                self.assertEqual((pic.type, pic.mime), (3, "image/jpeg"))
                self.assertEqual((pic.width, pic.height), (600, 600))

    def test_a_new_cover_replaces_a_legacy_coverart_comment(self):
        path = self.track(".ogg")
        audio = MFile(path)
        audio["COVERART"] = [base64.b64encode(jpeg((1, 2, 3))).decode("ascii")]
        audio["COVERARTMIME"] = ["image/jpeg"]
        audio.save()
        self.assertEqual(ogg_tags.read_summary(path)["n_pictures"], 2)
        self.quiet(apply_plan.apply, self.plan(path), False)
        tags = MFile(path).tags
        self.assertNotIn("COVERART", tags)
        self.assertNotIn("COVERARTMIME", tags)
        self.assertEqual(ogg_tags.read_summary(path)["n_pictures"], 1)

    def test_picture_comments_are_not_listed_as_tag_fields(self):
        path = self.track(".opus")
        summary = ogg_tags.read_summary(path)
        self.assertEqual(summary["n_pictures"], 1)
        self.assertNotIn("METADATA_BLOCK_PICTURE", summary["fields"])


class TestOggBackup(OggLibrary):
    def test_backup_keeps_images_in_the_sidecar_not_in_the_json(self):
        a = self.track(".ogg", "01")
        b = self.track(".opus", "02")  # same cover on both
        plan = self.plan(a, b)
        self.quiet(apply_plan.backup_tags, self.root, plan, self.backup)
        raw = Path(self.backup).read_text(encoding="utf-8")
        self.assertNotIn("metadata_block_picture", raw.lower())
        entry = json.loads(raw)["files"]["1974 - Red/01.ogg"]
        self.assertEqual(entry["format"], "ogg")
        self.assertEqual(len(entry["apic"]), 1)
        art_dir = os.path.splitext(self.backup)[0] + "_art"
        self.assertEqual(len(os.listdir(art_dir)), 1)

    def test_picture_dimensions_survive_a_round_trip(self):
        path = self.track(".opus")
        before = MFile(path).tags["METADATA_BLOCK_PICTURE"]
        self.quiet(apply_plan.backup_tags, self.root, self.plan(path), self.backup)
        self.quiet(apply_plan.apply, self.plan(path), False)
        self.quiet(apply_plan.restore, self.backup)
        after = MFile(path).tags["METADATA_BLOCK_PICTURE"]
        self.assertEqual(len(after), 1)
        old, new = Picture(base64.b64decode(before[0])), Picture(base64.b64decode(after[0]))
        self.assertEqual((new.type, new.mime, new.desc, new.data),
                         (old.type, old.mime, old.desc, old.data))

    def test_an_undecodable_picture_comment_is_kept_as_it_was(self):
        path = self.track(".ogg")
        audio = MFile(path)
        audio["METADATA_BLOCK_PICTURE"] = ["!!! not base64 !!!"]
        audio.save()
        self.quiet(apply_plan.backup_tags, self.root, self.plan(path), self.backup)
        self.quiet(apply_plan.apply, self.plan(path), False)
        self.quiet(apply_plan.restore, self.backup)
        self.assertEqual(MFile(path).tags["METADATA_BLOCK_PICTURE"],
                         ["!!! not base64 !!!"])

    def test_values_it_never_wrote_survive_apply_and_restore(self):
        path = self.track(".opus")
        audio = MFile(path)
        audio["R128_TRACK_GAIN"] = ["-512"]
        audio.save()
        self.quiet(apply_plan.backup_tags, self.root, self.plan(path), self.backup)
        self.quiet(apply_plan.apply, self.plan(path), False)
        self.assertEqual(MFile(path).tags["R128_TRACK_GAIN"], ["-512"])
        self.quiet(apply_plan.restore, self.backup)
        self.assertEqual(MFile(path).tags["R128_TRACK_GAIN"], ["-512"])

    def test_strip_frames_accepts_id3_ids_for_ogg_too(self):
        path = self.track(".ogg")
        audio = MFile(path)
        audio["COMMENT"] = ["ripped with junk"]
        audio.save()
        self.quiet(apply_plan.apply, self.plan(path, strip=["COMM", "composer"]), False)
        tags = MFile(path).tags
        self.assertNotIn("COMMENT", tags)
        self.assertNotIn("COMPOSER", tags)


class TestStreamsThatAreNotOggVorbis(OggLibrary):
    def unknown_codec(self, name):
        path = os.path.join(self.disc, name)
        Path(path).write_bytes(
            ogg_page([b"NotACodec" + b"\x00" * 40], seq=0, granule=0, flags=2)
            + ogg_page([OGG_AUDIO], seq=1, granule=10, flags=4))
        return path

    def test_an_ogg_stream_of_an_unknown_codec_is_refused_untouched(self):
        path = self.unknown_codec("01.ogg")
        before = Path(path).read_bytes()
        with self.assertRaises(ValueError):
            ogg_tags.read_summary(path)
        with self.assertRaises(ValueError):
            ogg_tags.write(path, {"album": "x"}, set(), None, {})
        self.assertEqual(Path(path).read_bytes(), before)

    def test_analyze_lists_such_a_file_as_unreadable(self):
        self.unknown_codec("01.ogg")
        out = Path(self.tmp) / "out.json"
        done = subprocess.run([sys.executable, str(ANALYZE), self.root, "--json", str(out)],
                              capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stderr)
        track = json.loads(out.read_text(encoding="utf-8"))["albums"][0]["discs"][0]["tracks"][0]
        self.assertEqual(track, {"file": "01.ogg", "format": "ogg", "error": True})


class TestAnalyzeOgg(OggLibrary):
    def test_ogg_and_opus_are_read_and_counted(self):
        self.track(".ogg", "01", album="Red", title="Starless")
        self.track(".opus", "02", album="Red", title="Fallen Angel")
        out = Path(self.tmp) / "out.json"
        done = subprocess.run([sys.executable, str(ANALYZE), self.root, "--json", str(out)],
                              capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stderr)
        result = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(result["formats"], {"ogg": 2})
        self.assertEqual(result["skipped_audio"], {})
        self.assertEqual(result["albums"][0]["current_album_names"], ["Red"])
        titles = [t["title"] for t in result["albums"][0]["discs"][0]["tracks"]]
        self.assertEqual(titles, ["Starless", "Fallen Angel"])


if __name__ == "__main__":
    unittest.main()
