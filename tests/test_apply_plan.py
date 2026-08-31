# -*- coding: utf-8 -*-
"""Round-trip tests for apply_plan.py: apply must be undoable by --restore.

Run with:  python -m unittest discover -s tests -v

Uses the standard library's unittest so the test suite needs nothing beyond the
runtime dependencies the scripts already require (mutagen, Pillow).
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                       / "skills" / "music-library-tagger" / "scripts"))

import apply_plan  # noqa: E402
from mutagen.id3 import (ID3, APIC, COMM, POPM, TALB, TCOM, TIT2, TPE1,  # noqa: E402
                         TPUB, TXXX, UFID, USLT)
from mutagen.mp3 import MP3  # noqa: E402
from PIL import Image  # noqa: E402

# Enough MPEG frame headers that mutagen accepts the file as audio.
MP3_BYTES = (b"\xff\xfb\x90\x64" + b"\x00" * 413) * 20


def jpeg(color, size=(600, 600)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def pixel(blob):
    with Image.open(io.BytesIO(blob)) as im:
        return im.convert("RGB").getpixel((10, 10))


def close_to(got, want, tol=25):
    return all(abs(a - b) <= tol for a, b in zip(got, want))


class TempLibrary(unittest.TestCase):
    """One album, one disc, two tracks, both carrying the same original cover."""

    ORIGINAL = (200, 0, 0)
    REPLACEMENT = (0, 160, 0)
    LYRICS = "wers pierwszy\nwers drugi"
    ID3_VERSION = 3
    ARTISTS = ["Artysta A", "Artysta B"]

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mlt-test-")
        self.addCleanup(self._cleanup)
        self.root = os.path.join(self.tmp, "Band")
        self.disc = os.path.join(self.root, "1974 - Red")
        os.makedirs(self.disc)

        self.tracks = ["01 - Red.mp3", "02 - Fallen Angel.mp3"]
        art = jpeg(self.ORIGINAL)
        for i, name in enumerate(self.tracks, 1):
            path = os.path.join(self.disc, name)
            with open(path, "wb") as f:
                f.write(MP3_BYTES)
            tags = ID3()
            tags.add(TALB(encoding=3, text=["old album name"]))
            tags.add(TIT2(encoding=3, text=["track %d" % i]))
            tags.add(TCOM(encoding=3, text=["Robert Fripp"]))
            tags.add(TPUB(encoding=3, text=["Island Records"]))
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Front",
                          data=art))
            # Frames the tool never writes: they must come through a round trip
            # untouched rather than be wiped or mangled.
            tags.add(USLT(encoding=3, lang="eng", desc="",
                          text=self.LYRICS))
            tags.add(POPM(email="rating@example.com", rating=196, count=3))
            tags.add(UFID(owner="http://musicbrainz.org", data=b"mbid-123"))
            tags.add(TPE1(encoding=3, text=self.ARTISTS))
            tags.save(path, v2_version=self.ID3_VERSION)

        self.new_cover = os.path.join(self.disc, "new_cover.jpg")
        with open(self.new_cover, "wb") as f:
            f.write(jpeg(self.REPLACEMENT))

        self.backup_dir = os.path.join(self.root, ".music-tagger")
        os.makedirs(self.backup_dir)
        self.backup = os.path.join(self.backup_dir, "tags_backup_test.json")

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------
    def plan(self):
        return {
            "root": self.root,
            "options": {"cover_embed": True, "cover_folder_jpg": True,
                        "cover_max_px": 1400,
                        "id3_version": self.ID3_VERSION},
            "albums": [{
                "album": "Red", "year": 1974, "album_path": "1974 - Red",
                "discs": [{
                    "path": "1974 - Red",
                    "cover": "1974 - Red/new_cover.jpg",
                    "tracks": [{"file": n, "track": i, "track_total": 2,
                                "title": "Track %d" % i}
                               for i, n in enumerate(self.tracks, 1)],
                }],
            }],
        }

    def run_apply(self, dry=False):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            apply_plan.apply(self.plan(), dry)
        return buf.getvalue()

    def summary(self, output):
        line = [l for l in output.splitlines() if "SUMMARY:" in l][-1]
        return json.loads(line.split("SUMMARY:", 1)[1].strip())

    def tags_of(self, name):
        return ID3(os.path.join(self.disc, name))

    def artwork_of(self, name):
        pics = self.tags_of(name).getall("APIC")
        return pics[0].data if pics else None


class TestRestoreFidelity(TempLibrary):

    def test_restore_reinstates_the_original_embedded_artwork(self):
        """The cover the user already had must come back, not just disappear."""
        apply_plan.backup_tags(self.root, self.plan(), self.backup)
        self.run_apply()
        self.assertTrue(close_to(pixel(self.artwork_of(self.tracks[0])),
                                 self.REPLACEMENT),
                        "apply should have embedded the new cover")

        with contextlib.redirect_stdout(io.StringIO()):
            apply_plan.restore(self.backup)

        for name in self.tracks:
            blob = self.artwork_of(name)
            self.assertIsNotNone(blob, "%s lost its artwork on restore" % name)
            self.assertTrue(close_to(pixel(blob), self.ORIGINAL),
                            "%s did not get the ORIGINAL cover back" % name)

    def test_restore_keeps_frames_outside_the_common_set(self):
        """TCOM/TPUB are captured by the backup, so they must survive restore."""
        apply_plan.backup_tags(self.root, self.plan(), self.backup)
        self.run_apply()
        with contextlib.redirect_stdout(io.StringIO()):
            apply_plan.restore(self.backup)

        tags = self.tags_of(self.tracks[0])
        self.assertEqual(str(tags["TCOM"]), "Robert Fripp")
        self.assertEqual(str(tags["TPUB"]), "Island Records")
        self.assertEqual(str(tags["TALB"]), "old album name")

    def test_backup_deduplicates_identical_artwork(self):
        """Both tracks share one cover -- it should be stored once, not twice."""
        apply_plan.backup_tags(self.root, self.plan(), self.backup)
        art_dir = os.path.splitext(self.backup)[0] + "_art"
        self.assertEqual(len(os.listdir(art_dir)), 1)

        with open(self.backup, encoding="utf-8") as f:
            data = json.load(f)
        refs = [e["sha1"] for f in data["files"].values() for e in f["apic"]]
        self.assertEqual(len(refs), 2, "both tracks should reference the image")
        self.assertEqual(len(set(refs)), 1, "and it should be the same image")

    def test_legacy_backup_without_artwork_is_reported(self):
        """An old-format backup cannot restore art; it must say so, not stay silent."""
        apply_plan.backup_tags(self.root, self.plan(), self.backup)
        with open(self.backup, encoding="utf-8") as f:
            data = json.load(f)
        for info in data["files"].values():
            info.pop("apic")
            info["had_apic"] = True
        with open(self.backup, "w", encoding="utf-8") as f:
            json.dump(data, f)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            apply_plan.restore(self.backup)
        self.assertIn("could not be restored", buf.getvalue())


class TestNonTextFrames(TempLibrary):
    """apply() never writes these, so restore() must not destroy them."""

    def test_rating_and_identifier_survive_a_restore(self):
        apply_plan.backup_tags(self.root, self.plan(), self.backup)
        self.run_apply()
        with contextlib.redirect_stdout(io.StringIO()):
            apply_plan.restore(self.backup)

        tags = self.tags_of(self.tracks[0])
        self.assertEqual(tags["POPM:rating@example.com"].rating, 196)
        self.assertEqual(tags["UFID:http://musicbrainz.org"].data, b"mbid-123")

    def test_lyrics_come_back_verbatim(self):
        """USLT.text is a string; iterating it would store it character by character."""
        apply_plan.backup_tags(self.root, self.plan(), self.backup)
        self.run_apply()
        with contextlib.redirect_stdout(io.StringIO()):
            apply_plan.restore(self.backup)

        tags = self.tags_of(self.tracks[0])
        key = [k for k in tags.keys() if k.startswith("USLT")][0]
        self.assertEqual(key, "USLT::eng", "the language tag was lost")
        self.assertEqual(tags[key].text, self.LYRICS)

    def test_non_text_frames_are_not_written_to_the_backup(self):
        apply_plan.backup_tags(self.root, self.plan(), self.backup)
        with open(self.backup, encoding="utf-8") as f:
            data = json.load(f)
        keys = list(data["files"].values())[0]["frames"]
        for unwanted in ("USLT::eng", "POPM:rating@example.com"):
            self.assertNotIn(unwanted, keys)

    def test_restore_drops_text_frames_the_tool_added(self):
        """Merging into the existing tag must not leave the tool's own frames behind."""
        self.assertNotIn("TRCK", self.tags_of(self.tracks[0]))
        apply_plan.backup_tags(self.root, self.plan(), self.backup)
        self.run_apply()
        self.assertIn("TRCK", self.tags_of(self.tracks[0]))

        with contextlib.redirect_stdout(io.StringIO()):
            apply_plan.restore(self.backup)
        self.assertNotIn("TRCK", self.tags_of(self.tracks[0]))


class TestFrameDescriptors(TempLibrary):
    """Descriptors may contain ':', which is also mutagen's key separator."""

    def add_frames(self):
        for name in self.tracks:
            path = os.path.join(self.disc, name)
            tags = ID3(path)
            tags.add(TXXX(encoding=3, desc="Rating:WMP", text=["5"]))
            tags.add(TXXX(encoding=3, desc="MusicBrainz Album Id", text=["m-1"]))
            tags.add(COMM(encoding=3, lang="eng", desc="Songs-DB:Custom1",
                          text=["note"]))
            tags.save(path, v2_version=3)

    def round_trip(self):
        self.add_frames()
        apply_plan.backup_tags(self.root, self.plan(), self.backup)
        self.run_apply()
        with contextlib.redirect_stdout(io.StringIO()):
            apply_plan.restore(self.backup)
        return self.tags_of(self.tracks[0])

    def test_txxx_descriptor_with_a_colon_is_not_truncated(self):
        tags = self.round_trip()
        self.assertIn("TXXX:Rating:WMP", tags)
        self.assertEqual(tags["TXXX:Rating:WMP"].desc, "Rating:WMP")
        self.assertEqual(tags["TXXX:MusicBrainz Album Id"].text, ["m-1"])

    def test_comment_descriptor_with_a_colon_keeps_desc_and_language(self):
        """Splitting from the left put 'Custom1' in lang, so the frame vanished."""
        tags = self.round_trip()
        key = "COMM:Songs-DB:Custom1:eng"
        self.assertIn(key, tags)
        self.assertEqual(tags[key].desc, "Songs-DB:Custom1")
        self.assertEqual(tags[key].lang, "eng")
        self.assertEqual(tags[key].text, ["note"])


class TestId3Version(TempLibrary):
    """A restore must not quietly rewrite a v2.4 library as v2.3."""

    ID3_VERSION = 4

    def test_restore_keeps_the_tag_version(self):
        path = os.path.join(self.disc, self.tracks[0])
        self.assertEqual(ID3(path).version[1], 4)

        apply_plan.backup_tags(self.root, self.plan(), self.backup)
        self.run_apply()
        with contextlib.redirect_stdout(io.StringIO()):
            apply_plan.restore(self.backup)

        self.assertEqual(ID3(path).version[1], 4)

    def test_multi_value_frame_keeps_its_values(self):
        """v2.3 joins values with '/'; v2.4 keeps them, so the version matters."""
        apply_plan.backup_tags(self.root, self.plan(), self.backup)
        self.run_apply()
        with contextlib.redirect_stdout(io.StringIO()):
            apply_plan.restore(self.backup)

        self.assertEqual(list(self.tags_of(self.tracks[0])["TPE1"].text),
                         self.ARTISTS)


def _symlinks_work():
    import tempfile as _t
    d = _t.mkdtemp()
    try:
        os.symlink(os.path.join(d, "target"), os.path.join(d, "link"))
        return True
    except (OSError, NotImplementedError, AttributeError):
        return False
    finally:
        import shutil as _s
        _s.rmtree(d, ignore_errors=True)


class TestTagLayout(unittest.TestCase):
    """A restore should leave the file shaped the way it found it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mlt-layout-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp,
                                                            ignore_errors=True))
        self.root = os.path.join(self.tmp, "Band")
        self.disc = os.path.join(self.root, "Album")
        os.makedirs(self.disc)
        self.path = os.path.join(self.disc, "01.mp3")
        with open(self.path, "wb") as f:
            f.write(MP3_BYTES)
        self.backup = os.path.join(self.tmp, "backup.json")

    def plan(self):
        return {"root": self.root,
                "options": {"cover_embed": False, "cover_folder_jpg": False},
                "albums": [{"album": "Red", "year": 1974, "discs": [
                    {"path": "Album", "tracks": [
                        {"file": "01.mp3", "track": 1, "title": "Red"}]}]}]}

    def cycle(self):
        with contextlib.redirect_stdout(io.StringIO()):
            apply_plan.backup_tags(self.root, self.plan(), self.backup)
            apply_plan.apply(self.plan(), False)
            apply_plan.restore(self.backup)

    def has_v2(self):
        with open(self.path, "rb") as f:
            return f.read(3) == b"ID3"

    def has_v1(self):
        with open(self.path, "rb") as f:
            f.seek(-128, os.SEEK_END)
            return f.read(3) == b"TAG"

    def test_a_file_that_had_no_tag_ends_up_with_no_tag(self):
        self.assertFalse(self.has_v2())
        self.cycle()
        self.assertFalse(self.has_v2(), "restore left an empty ID3v2 tag behind")
        self.assertGreater(MP3(self.path).info.length, 0, "audio was damaged")

    def test_a_v1_only_file_keeps_v1_and_gains_no_v2(self):
        tags = ID3()
        tags.add(TALB(encoding=3, text=["stary album"]))
        tags.save(self.path, v1=2, v2_version=3)
        tags.delete(self.path, delete_v1=False, delete_v2=True)
        self.assertTrue(self.has_v1())
        self.assertFalse(self.has_v2())

        self.cycle()

        self.assertTrue(self.has_v1(), "the ID3v1 tag was lost")
        self.assertFalse(self.has_v2(), "restore added an ID3v2 tag it never had")
        self.assertEqual(str(ID3(self.path)["TALB"]), "stary album")


class TestDamagedBackup(TempLibrary):
    """A hand-edited backup must cost one entry, not the whole restore."""

    def restore_with(self, mutate):
        apply_plan.backup_tags(self.root, self.plan(), self.backup)
        self.run_apply()
        with open(self.backup, encoding="utf-8") as f:
            data = json.load(f)
        mutate(data)
        with open(self.backup, "w", encoding="utf-8") as f:
            json.dump(data, f)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            apply_plan.restore(self.backup)
        return buf.getvalue()

    def test_artwork_entry_without_a_path_is_skipped(self):
        def mutate(data):
            first = data["files"][sorted(data["files"])[0]]
            for entry in first["apic"]:
                entry.pop("file")
        out = self.restore_with(mutate)
        self.assertIn("no usable path", out)
        # the other track is still restored, tags and all
        second = self.tags_of(self.tracks[1])
        self.assertEqual(str(second["TALB"]), "old album name")
        self.assertIsNotNone(self.artwork_of(self.tracks[1]))

    def test_non_integer_picture_type_falls_back(self):
        def mutate(data):
            for info in data["files"].values():
                for entry in info["apic"]:
                    entry["type"] = "front"
        self.restore_with(mutate)
        for name in self.tracks:
            pics = self.tags_of(name).getall("APIC")
            self.assertEqual(len(pics), 1)
            self.assertEqual(pics[0].type, 3)

    def test_one_broken_entry_does_not_abort_the_others(self):
        def mutate(data):
            first = sorted(data["files"])[0]
            data["files"][first]["frames"] = "not a mapping"
        out = self.restore_with(mutate)
        self.assertIn("could not restore", out)
        self.assertIn("1 file(s) could not be restored", out)
        self.assertEqual(str(self.tags_of(self.tracks[1])["TALB"]),
                         "old album name")


class TestPathContainment(TempLibrary):
    """Paths inside a backup file are data. They must not steer a restore."""

    def craft(self, mutate):
        apply_plan.backup_tags(self.root, self.plan(), self.backup)
        with open(self.backup, encoding="utf-8") as f:
            data = json.load(f)
        mutate(data)
        with open(self.backup, "w", encoding="utf-8") as f:
            json.dump(data, f)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            apply_plan.restore(self.backup)
        return buf.getvalue()

    def test_artwork_path_outside_the_backup_folder_is_refused(self):
        outside = os.path.join(self.tmp, "outside.jpg")
        with open(outside, "wb") as f:
            f.write(jpeg((7, 7, 7)))

        def mutate(data):
            for info in data["files"].values():
                info["apic"] = [{"sha1": "x", "file": "../../outside.jpg",
                                 "mime": "image/jpeg", "type": 3, "desc": "F"}]
        out = self.craft(mutate)
        self.assertIn("refusing artwork path", out)
        self.assertIsNone(self.artwork_of(self.tracks[0]),
                          "artwork from outside the backup folder was embedded")

    def test_absolute_artwork_path_is_refused(self):
        outside = os.path.join(self.tmp, "outside.jpg")
        with open(outside, "wb") as f:
            f.write(jpeg((7, 7, 7)))

        def mutate(data):
            for info in data["files"].values():
                info["apic"] = [{"sha1": "x", "file": outside.replace("\\", "/"),
                                 "mime": "image/jpeg", "type": 3, "desc": "F"}]
        out = self.craft(mutate)
        self.assertIn("refusing artwork path", out)

    def test_a_base_that_is_a_filesystem_root_still_contains_its_children(self):
        """A root already ends with a separator, so a prefix test matched nothing."""
        drive = os.path.splitdrive(os.path.abspath(self.tmp))[0]
        fs_root = drive + os.sep if drive else os.sep
        rel = os.path.relpath(self.backup_dir, fs_root)
        self.assertIsNotNone(apply_plan.within(fs_root, rel),
                             "a path under the root was refused")

    @unittest.skipUnless(_symlinks_work(), "symlinks unavailable on this machine")
    def test_symlink_out_of_the_backup_folder_is_refused(self):
        """Containment resolves links, so one planted in the folder is not a way out."""
        outside = os.path.join(self.tmp, "outside.jpg")
        with open(outside, "wb") as f:
            f.write(jpeg((7, 7, 7)))
        art_dir = os.path.splitext(self.backup)[0] + "_art"
        os.makedirs(art_dir, exist_ok=True)
        os.symlink(outside, os.path.join(art_dir, "link.jpg"))
        rel = os.path.basename(art_dir) + "/link.jpg"

        def mutate(data):
            for info in data["files"].values():
                info["apic"] = [{"sha1": "x", "file": rel, "mime": "image/jpeg",
                                 "type": 3, "desc": "F"}]
        out = self.craft(mutate)
        self.assertIn("refusing artwork path", out)

    def test_track_path_outside_the_root_is_refused(self):
        stray = os.path.join(self.tmp, "stray.mp3")
        with open(stray, "wb") as f:
            f.write(MP3_BYTES)
        ID3().save(stray, v2_version=3)

        def mutate(data):
            info = list(data["files"].values())[0]
            data["files"] = {"../stray.mp3": info}
        out = self.craft(mutate)
        self.assertIn("refusing path outside the backup root", out)
        self.assertNotIn("TALB", ID3(stray))


class TestDryRun(TempLibrary):

    def test_dry_run_counts_the_cover_work_it_would_do(self):
        """A preview that reports 0 covers hides the most destructive step."""
        dry = self.summary(self.run_apply(dry=True))
        wet = self.summary(self.run_apply(dry=False))
        self.assertEqual(dry, wet)
        self.assertEqual(dry["covers_embedded"], 2)
        self.assertEqual(dry["cover_jpgs"], 1)

    def test_dry_run_changes_nothing_on_disk(self):
        before = {n: self.tags_of(n).getall("APIC")[0].data for n in self.tracks}
        self.run_apply(dry=True)
        self.assertFalse(os.path.exists(os.path.join(self.disc, "cover.jpg")))
        for name in self.tracks:
            self.assertEqual(self.artwork_of(name), before[name])
            self.assertEqual(str(self.tags_of(name)["TALB"]), "old album name")


class TestApplyThroughMain(TempLibrary):

    def test_full_round_trip_via_cli(self):
        """The path a user actually takes: apply, then restore from the backup."""
        plan_path = os.path.join(self.tmp, "plan.json")
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(self.plan(), f)

        argv = sys.argv
        try:
            sys.argv = ["apply_plan.py", "--plan", plan_path]
            with contextlib.redirect_stdout(io.StringIO()):
                apply_plan.main()

            self.assertEqual(str(self.tags_of(self.tracks[0])["TALB"]), "Red")
            self.assertTrue(os.path.exists(os.path.join(self.disc, "cover.jpg")))

            backups = [f for f in os.listdir(self.backup_dir)
                       if f.startswith("tags_backup_") and f.endswith(".json")]
            self.assertEqual(len(backups), 1)

            sys.argv = ["apply_plan.py", "--restore",
                        os.path.join(self.backup_dir, backups[0])]
            with contextlib.redirect_stdout(io.StringIO()):
                apply_plan.main()
        finally:
            sys.argv = argv

        self.assertEqual(str(self.tags_of(self.tracks[0])["TALB"]),
                         "old album name")
        self.assertTrue(close_to(pixel(self.artwork_of(self.tracks[0])),
                                 self.ORIGINAL))


if __name__ == "__main__":
    unittest.main()
