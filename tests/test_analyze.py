"""Behavior checks for the read-only library scanner."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audio_fixtures import MP3_BYTES, flac_bytes  # noqa: E402
from mutagen.flac import FLAC, Picture  # noqa: E402
from PIL import Image  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                       / "skills" / "music-library-tagger" / "scripts"))
# Importing the script swaps sys.stdout for a new TextIOWrapper around the same buffer.
# Dropping a wrapper closes that buffer and would break the tests that run after this
# one, so keep the new wrapper alive and put the original stream back.
_original_stdout = sys.stdout
import analyze  # noqa: E402
_wrapper_made_by_the_script = sys.stdout
sys.stdout = _original_stdout

ANALYZE = (Path(__file__).resolve().parents[1]
           / "skills" / "music-library-tagger" / "scripts" / "analyze.py")


def run_analyze(root, out_json):
    completed = subprocess.run(
        [sys.executable, str(ANALYZE), str(root), "--json", str(out_json)],
        capture_output=True, text=True, encoding="utf-8", check=False)
    return completed, (json.loads(out_json.read_text(encoding="utf-8"))
                       if out_json.exists() else None)


def make_flac(path, **fields):
    path.write_bytes(flac_bytes())
    audio = FLAC(str(path))
    audio.add_tags()
    for key, value in fields.items():
        audio.tags[key.upper()] = value if isinstance(value, list) else [value]
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, "JPEG")
    pic = Picture()
    pic.type, pic.mime, pic.data = 3, "image/jpeg", buf.getvalue()
    audio.add_picture(pic)
    audio.save()


class TestFlacAnalysis(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mlt-analyze-flac-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "library"
        self.album = self.root / "1974 - Red"
        self.album.mkdir(parents=True)

    def analyze(self):
        completed, result = run_analyze(self.root, self.tmp / "out.json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout, result

    def test_flac_album_is_read_into_the_same_report_shape(self):
        make_flac(self.album / "03 - Starless.flac", album="Red", date="1974",
                  artist="King Crimson", albumartist="King Crimson",
                  genre="Prog", title="Starless", tracknumber="3",
                  totaltracks="12", discnumber="1", totaldiscs="2",
                  comment="ripped")
        _, result = self.analyze()
        self.assertEqual(result["n_albums"], 1)
        alb = result["albums"][0]
        self.assertEqual(alb["current_album_names"], ["Red"])
        self.assertEqual(alb["current_years"], ["1974"])
        self.assertEqual(alb["current_artists"], ["King Crimson"])
        self.assertEqual(alb["current_album_artists"], ["King Crimson"])
        self.assertEqual(alb["current_genres"], ["Prog"])
        track = alb["discs"][0]["tracks"][0]
        self.assertEqual(track["format"], "flac")
        self.assertEqual(track["title"], "Starless")
        self.assertEqual(track["track"], "3/12")
        self.assertEqual(track["disc"], "1/2")
        self.assertTrue(track["has_cover"])
        self.assertEqual(result["comments"], {"ripped": 1})
        self.assertIn("ALBUM", result["frame_types"])

    def test_formats_are_counted_for_a_mixed_folder(self):
        make_flac(self.album / "01.flac", album="Red")
        (self.album / "02.mp3").write_bytes(MP3_BYTES)
        _, result = self.analyze()
        self.assertEqual(result["n_tracks"], 2)
        self.assertEqual(result["formats"], {"flac": 1, "mp3": 1})

    def test_unreadable_flac_is_an_error_entry_not_a_crash(self):
        (self.album / "01.flac").write_bytes(b"not flac")
        _, result = self.analyze()
        track = result["albums"][0]["discs"][0]["tracks"][0]
        self.assertEqual(track, {"file": "01.flac", "format": "flac",
                                 "error": True})


class TestReportHeadings(unittest.TestCase):
    def test_tag_headings_name_every_supported_tag_family(self):
        with tempfile.TemporaryDirectory(prefix="mlt-analyze-headings-") as temp:
            temp = Path(temp)
            root = temp / "library"
            (root / "A").mkdir(parents=True)
            (root / "A" / "01.mp3").write_bytes(MP3_BYTES)
            completed, _ = run_analyze(root, temp / "out.json")
            heading = [line for line in completed.stdout.splitlines()
                       if line.startswith("TAG FIELDS PRESENT")][0]
            for family in ("ID3", "Vorbis", "MP4"):
                self.assertIn(family, heading)
            comments = [line for line in completed.stdout.splitlines()
                        if line.startswith("DISTINCT COMMENT VALUES")][0]
            for family in ("ID3", "Vorbis", "MP4"):
                self.assertIn(family, comments)


class TestSkippedAudioReport(unittest.TestCase):
    def test_unsupported_audio_is_reported_not_silently_ignored(self):
        with tempfile.TemporaryDirectory(prefix="mlt-analyze-skip-") as temp:
            temp = Path(temp)
            root = temp / "library"
            (root / "A").mkdir(parents=True)
            (root / "B").mkdir()
            (root / "A" / "01.mp3").write_bytes(MP3_BYTES)
            (root / "A" / "02.wv").write_bytes(b"x")
            (root / "B" / "01.WV").write_bytes(b"x")
            (root / "B" / "02.ape").write_bytes(b"x")
            (root / "B" / "notes.txt").write_bytes(b"x")

            completed, result = run_analyze(root, temp / "out.json")
            self.assertEqual(completed.returncode, 0, completed.stderr)

            self.assertEqual(result["n_albums"], 1)  # B has no supported audio
            skipped = result["skipped_audio"]
            self.assertEqual(sorted(skipped), [".ape", ".wv"])
            self.assertEqual(skipped[".wv"]["count"], 2)
            self.assertEqual(skipped[".wv"]["folders"], ["A", "B"])
            self.assertEqual(skipped[".ape"]["count"], 1)
            self.assertIn("SKIPPED", completed.stdout)
            self.assertIn(".wv", completed.stdout)

    def test_nothing_is_reported_when_nothing_was_skipped(self):
        with tempfile.TemporaryDirectory(prefix="mlt-analyze-skip-") as temp:
            temp = Path(temp)
            root = temp / "library"
            (root / "A").mkdir(parents=True)
            (root / "A" / "01.mp3").write_bytes(MP3_BYTES)
            completed, result = run_analyze(root, temp / "out.json")
            self.assertEqual(result["skipped_audio"], {})
            self.assertNotIn("SKIPPED", completed.stdout)


class TestHiddenDirectoryPruning(unittest.TestCase):
    def test_scan_never_enters_hidden_directories(self):
        with tempfile.TemporaryDirectory(prefix="mlt-analyze-") as temp:
            temp = Path(temp)
            root = temp / "library"
            for folder in (root / "Album", root / "Album" / ".cache",
                           root / ".music-tagger" / "covers", root / ".git"):
                folder.mkdir(parents=True)
                (folder / "track.mp3").write_bytes(b"")

            result_file = temp / "result.json"
            visited_file = temp / "visited.json"
            probe = """
import json, os, runpy, sys
script, root, result_file, visited_file = sys.argv[1:]
real_walk = os.walk
visited = []

def tracked_walk(path):
    for current, dirs, files in real_walk(path):
        visited.append(os.path.relpath(current, root))
        yield current, dirs, files

os.walk = tracked_walk
sys.argv = [script, root, '--json', result_file]
runpy.run_path(script, run_name='__main__')
with open(visited_file, 'w', encoding='utf-8') as output:
    json.dump(visited, output)
"""
            completed = subprocess.run(
                [sys.executable, "-c", probe, str(ANALYZE), str(root),
                 str(result_file), str(visited_file)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

            visited = json.loads(visited_file.read_text(encoding="utf-8"))
            self.assertEqual(visited, [".", "Album"])

            result = json.loads(result_file.read_text(encoding="utf-8"))
            self.assertEqual(result["n_albums"], 1)
            self.assertEqual(result["n_tracks"], 1)


class TestDiscNumberParsing(unittest.TestCase):
    def test_digits_words_and_roman_numerals(self):
        cases = {
            "CD1": 1, "CD 1": 1, "cd-2": 2, "CD_3": 3, "CD#4": 4, "Disc 3": 3, "Disk 2": 2,
            "DVD 1": 1, "Volume 3": 3, "Vol. II": 2, "Vol.IV": 4, "Vol IX": 9,
            "CD One": 1, "cd two": 2, "Disc Ten": 10, "Disc 11": 11, "CD 1 - Bonus": 1,
            "  CD 2": 2,
        }
        for name, number in cases.items():
            with self.subTest(name=name):
                self.assertEqual(analyze.disc_number_from_name(name), number)
                self.assertTrue(analyze.is_disc_folder_name(name))

    def test_catalog_numbers_and_title_words_are_not_discs(self):
        # The pattern is anchored at the start so the token inside a catalog number or
        # a title word is never mistaken for a disc.
        for name in ("EGCD 2", "SANCD-155", "Three Of A Perfect Pair", "Discovery",
                     "Voltage", "Civil War", "Discipline", "1974 - Red", "Bonus CD 1",
                     "CD", "Vol", "Disc"):
            with self.subTest(name=name):
                self.assertIsNone(analyze.disc_number_from_name(name))
                self.assertFalse(analyze.is_disc_folder_name(name))


class TestCoverDirs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mlt-coverdirs-")
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name) / "Album"
        self.folder.mkdir()

    def names(self):
        return [Path(d).name for d in analyze.cover_dirs(str(self.folder))]

    def test_the_folder_itself_is_always_searched(self):
        self.assertEqual(self.names(), ["Album"])

    def test_a_covers_folder_is_listed_exactly_once(self):
        # 'covers', 'Covers' and 'COVERS' are the same directory on a case-insensitive
        # filesystem (NTFS, default macOS); one real folder must not be scanned twice.
        for spelling in ("covers", "Covers", "COVERS"):
            with self.subTest(spelling=spelling):
                (self.folder / spelling).mkdir()
                self.assertEqual(len(self.names()), 2)
                self.assertEqual(self.names()[0], "Album")
                (self.folder / spelling).rmdir()

    def test_two_spellings_of_one_real_folder_are_listed_once_on_any_filesystem(self):
        # The loop above only meets a duplicate on a case-insensitive filesystem. Make
        # every filesystem look like one: 'covers' and 'Covers' both exist and resolve to
        # the same real folder, as they do on NTFS. Without the real-path dedup the
        # folder would be scanned (and its images listed) twice.
        shared = str(self.folder / "covers")
        real_isdir, real_realpath = os.path.isdir, os.path.realpath

        def isdir(path):
            return Path(path).name.lower() == "covers" or real_isdir(path)

        def realpath(path, *args, **kwargs):
            if Path(path).name.lower() == "covers":
                return shared
            return real_realpath(path, *args, **kwargs)

        with mock.patch.object(analyze.os.path, "isdir", isdir), \
                mock.patch.object(analyze.os.path, "realpath", realpath):
            found = analyze.cover_dirs(str(self.folder))
        covers = [d for d in found if Path(d).name.lower() == "covers"]
        self.assertEqual(len(covers), 1, found)

    def test_aliases_are_found_when_path_normalisation_does_not_fold_case(self):
        # macOS keeps a case-insensitive volume by default, but posixpath.normcase leaves
        # case alone and realpath does not canonicalise the spelling of existing
        # components, so a key built from the path text sees covers / Covers / COVERS as
        # three folders. Emulate that on any case-insensitive filesystem (NTFS, macOS).
        (self.folder / "covers").mkdir()
        if not (self.folder / "COVERS").is_dir():
            self.skipTest("needs a case-insensitive filesystem; the symlink test covers the rest")
        with mock.patch.object(analyze.os.path, "normcase", lambda path: path),                 mock.patch.object(analyze.os.path, "realpath", lambda path, *a, **k: path):
            found = analyze.cover_dirs(str(self.folder))
        self.assertEqual(len(found), 2, found)           # the folder itself and covers, once

    def test_a_symlink_to_a_covers_folder_is_listed_once(self):
        (self.folder / "covers").mkdir()
        try:
            os.symlink(self.folder / "covers", self.folder / "Artwork", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("this platform does not allow creating symlinks")
        names = self.names()
        self.assertEqual(len(names), 2, names)

    def test_other_artwork_folder_names_are_recognised(self):
        for name in ("Scans", "scans", "Artwork", "artwork", "Cover", "cover"):
            with self.subTest(name=name):
                (self.folder / name).mkdir()
                self.assertEqual(len(self.names()), 2)
                (self.folder / name).rmdir()

    def test_unrelated_folders_are_not_searched(self):
        (self.folder / "booklet").mkdir()
        self.assertEqual(self.names(), ["Album"])


class TestAlbumGrouping(unittest.TestCase):
    """End to end: build a library, run the script, read the JSON."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mlt-grouping-")
        self.addCleanup(self._tmp.cleanup)
        self.temp = Path(self._tmp.name)
        self.root = self.temp / "library"
        self.root.mkdir()

    def make(self, *tracks):
        for rel in tracks:
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(MP3_BYTES)

    def analyze(self):
        completed, result = run_analyze(self.root, self.temp / "out.json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return result

    @staticmethod
    def summary(result):
        return [(a["album_path"], a["n_discs"], a["multi_disc"],
                 [d["name"] for d in a["discs"]]) for a in result["albums"]]

    def test_a_plain_album_folder_is_one_single_disc_album(self):
        self.make("1974 - Red/01.mp3", "1974 - Red/02.mp3")
        self.assertEqual(self.summary(self.analyze()),
                         [("1974 - Red", 1, False, ["1974 - Red"])])

    def test_disc_folders_are_grouped_under_their_parent(self):
        self.make("Box/CD1/01.mp3", "Box/CD2/01.mp3")
        self.assertEqual(self.summary(self.analyze()),
                         [("Box", 2, True, ["CD1", "CD2"])])

    def test_discs_are_ordered_by_number_not_by_name(self):
        self.make("Box/CD10/01.mp3", "Box/CD2/01.mp3", "Box/CD1/01.mp3")
        (album,) = self.analyze()["albums"]
        self.assertEqual([d["name"] for d in album["discs"]], ["CD1", "CD2", "CD10"])
        self.assertEqual([d["disc_guess"] for d in album["discs"]], [1, 2, 10])

    def test_word_and_roman_disc_names_group_too(self):
        self.make("Words/CD One/01.mp3", "Words/CD Two/01.mp3",
                  "Roman/Vol. I/01.mp3", "Roman/Vol. II/01.mp3")
        result = self.summary(self.analyze())
        self.assertEqual(result, [("Roman", 2, True, ["Vol. I", "Vol. II"]),
                                  ("Words", 2, True, ["CD One", "CD Two"])])

    def test_a_disc_named_folder_directly_under_the_root_is_not_grouped(self):
        # Grouping under the parent would make the whole library one "album".
        self.make("CD1/01.mp3", "CD2/01.mp3")
        self.assertEqual(self.summary(self.analyze()),
                         [("CD1", 1, True, ["CD1"]), ("CD2", 1, True, ["CD2"])])

    def test_a_single_disc_folder_that_is_named_like_a_disc_is_multi_disc(self):
        self.make("Live/Disc 1/01.mp3")
        self.assertEqual(self.summary(self.analyze()),
                         [("Live", 1, True, ["Disc 1"])])

    def test_catalog_numbers_and_title_words_do_not_make_albums_discs(self):
        self.make("Band/EGCD 2/01.mp3", "Band/Three Of A Perfect Pair/01.mp3")
        self.assertEqual(self.summary(self.analyze()),
                         [("Band/EGCD 2", 1, False, ["EGCD 2"]),
                          ("Band/Three Of A Perfect Pair", 1, False,
                           ["Three Of A Perfect Pair"])])

    def test_albums_of_different_kinds_sit_side_by_side(self):
        self.make("1969 - Court/CD 1/01.mp3", "1969 - Court/CD 2/01.mp3", "1974 - Red/01.mp3")
        self.assertEqual([(a[0], a[1]) for a in self.summary(self.analyze())],
                         [("1969 - Court", 2), ("1974 - Red", 1)])

    def test_tracks_are_counted_across_discs(self):
        self.make("Box/CD1/01.mp3", "Box/CD1/02.mp3", "Box/CD2/01.mp3")
        result = self.analyze()
        self.assertEqual(result["n_tracks"], 3)
        self.assertEqual([d["n_tracks"] for d in result["albums"][0]["discs"]], [2, 1])

    def test_covers_are_found_once_per_folder_and_album_covers_only_for_box_sets(self):
        self.make("Box/CD1/01.mp3", "Box/CD2/01.mp3", "Single/01.mp3")
        for rel in ("Box/cover.jpg", "Box/CD1/covers/front.jpg", "Single/folder.jpg"):
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (40, 30), (1, 2, 3)).save(path, "JPEG")
        by_path = {a["album_path"]: a for a in self.analyze()["albums"]}
        box, single = by_path["Box"], by_path["Single"]
        self.assertEqual([c["name"] for c in box["album_level_covers"]], ["cover.jpg"])
        album_cover = box["album_level_covers"][0]
        self.assertEqual((album_cover["w"], album_cover["h"]), (40, 30))
        cd1 = box["discs"][0]
        self.assertEqual([c["name"] for c in cd1["covers"]], ["front.jpg"])
        self.assertEqual((cd1["covers"][0]["w"], cd1["covers"][0]["h"]), (40, 30))
        self.assertEqual(single["album_level_covers"], [])           # not a box set
        self.assertEqual([c["name"] for c in single["discs"][0]["covers"]], ["folder.jpg"])


if __name__ == "__main__":
    unittest.main()
