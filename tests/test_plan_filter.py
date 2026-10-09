# -*- coding: utf-8 -*-
"""--albums / --limit: apply (and back up) only part of a plan without editing plan.json.

The documented pilot used to mean trimming the generated JSON by hand right before the
first write to someone's library, so the plan that was reviewed was not the plan that ran.

Run with:  python -m unittest discover -s tests -v
"""
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

# Importing the script swaps sys.stdout for a new TextIOWrapper around the same buffer.
# Dropping a wrapper closes that buffer, so keep the new one alive and restore the stream.
_original_stdout = sys.stdout
import apply_plan  # noqa: E402
_wrapper_made_by_the_script = sys.stdout
sys.stdout = _original_stdout

from audio_fixtures import write_mp3  # noqa: E402
from mutagen.id3 import ID3  # noqa: E402

SCRIPT = Path(apply_plan.__file__)
ALBUMS = [("Red", "1974 - Red"), ("Discipline", "1981 - Discipline"),
          ("Beat", "1982 - Beat"), ("Red, White & Blue", "1999 - Red White Blue")]


def plan_for(root):
    return {
        "root": str(root),
        "options": {"cover_embed": False, "cover_folder_jpg": False},
        "albums": [{
            "album": title, "year": 1974, "album_path": folder,
            "discs": [{"path": folder,
                       "tracks": [{"file": "01.mp3", "track": 1, "track_total": 1,
                                   "title": "New %s" % title}]}],
        } for title, folder in ALBUMS],
    }


class Library(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mlt-filter-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = Path(self.tmp) / "Band"
        for title, folder in ALBUMS:
            (self.root / folder).mkdir(parents=True)
            write_mp3(self.root / folder / "01.mp3", album="old", title="old", cover=None)
        self.plan_path = Path(self.tmp) / "plan.json"
        self.plan_path.write_text(json.dumps(plan_for(self.root)), encoding="utf-8")
        self.backups = self.root / ".music-tagger"

    def run_script(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), "--plan", str(self.plan_path), *args],
                              capture_output=True, text=True, encoding="utf-8")

    def album_tag(self, folder):
        return str(ID3(self.root / folder / "01.mp3")["TALB"])

    def tagged(self):
        """Folders whose first track now carries the planned album tag."""
        return [folder for title, folder in ALBUMS if self.album_tag(folder) == title]

    def backed_up(self):
        files = sorted(self.backups.glob("tags_backup_*.json"))
        if not files:
            return None
        return sorted(json.loads(files[-1].read_text(encoding="utf-8"))["files"])


class TestSelectAlbums(unittest.TestCase):
    plan = {"root": "x", "options": {}, "albums": [
        {"album": t, "album_path": f} for t, f in ALBUMS]}

    def titles(self, plan):
        return [a["album"] for a in plan["albums"]]

    def test_no_filter_returns_the_plan_unchanged(self):
        self.assertEqual(apply_plan.select_albums(self.plan, None, None), self.plan)

    def test_by_title_ignoring_case_and_spaces(self):
        chosen = apply_plan.select_albums(self.plan, ["  discipline ,RED"], None)
        self.assertEqual(self.titles(chosen), ["Red", "Discipline"])   # plan order, not typed order

    def test_by_album_path(self):
        chosen = apply_plan.select_albums(self.plan, ["1982 - Beat"], None)
        self.assertEqual(self.titles(chosen), ["Beat"])

    def test_a_title_containing_a_comma_matches_as_a_whole(self):
        chosen = apply_plan.select_albums(self.plan, ["Red, White & Blue"], None)
        self.assertEqual(self.titles(chosen), ["Red, White & Blue"])

    def test_the_flag_can_be_repeated(self):
        chosen = apply_plan.select_albums(self.plan, ["Red", "Beat"], None)
        self.assertEqual(self.titles(chosen), ["Red", "Beat"])

    def test_limit_takes_the_first_n(self):
        self.assertEqual(self.titles(apply_plan.select_albums(self.plan, None, 2)),
                         ["Red", "Discipline"])

    def test_limit_applies_after_the_name_filter(self):
        chosen = apply_plan.select_albums(self.plan, ["Beat,Discipline,Red"], 2)
        self.assertEqual(self.titles(chosen), ["Red", "Discipline"])

    def test_a_limit_larger_than_the_plan_takes_everything(self):
        self.assertEqual(len(apply_plan.select_albums(self.plan, None, 99)["albums"]), 4)

    def test_the_original_plan_is_not_modified(self):
        before = json.dumps(self.plan)
        apply_plan.select_albums(self.plan, ["Red"], 1)
        self.assertEqual(json.dumps(self.plan), before)

    def test_an_unknown_name_is_an_error_that_lists_what_exists(self):
        with self.assertRaises(ValueError) as caught:
            apply_plan.select_albums(self.plan, ["Red,Discipline,Lizard"], None)
        message = str(caught.exception)
        self.assertIn("Lizard", message)
        self.assertIn("Discipline", message)   # the choices

    def test_a_limit_below_one_is_an_error(self):
        for bad in (0, -1):
            with self.subTest(limit=bad), self.assertRaises(ValueError):
                apply_plan.select_albums(self.plan, None, bad)


class TestCommandLine(Library):
    def test_only_the_named_albums_are_changed_and_backed_up(self):
        done = self.run_script("--albums", "Red,Discipline")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.tagged(), ["1974 - Red", "1981 - Discipline"])
        self.assertEqual(self.backed_up(), ["1974 - Red/01.mp3", "1981 - Discipline/01.mp3"])
        self.assertIn("Selected 2 of 4 albums: Red, Discipline", done.stdout)

    def test_limit_applies_to_the_first_albums(self):
        done = self.run_script("--limit", "1")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.tagged(), ["1974 - Red"])
        self.assertEqual(self.backed_up(), ["1974 - Red/01.mp3"])

    def test_a_dry_run_reports_only_the_selection_and_writes_nothing(self):
        done = self.run_script("--albums", "Beat", "--dry-run")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('"tracks": 1', done.stdout)
        self.assertEqual(self.tagged(), [])
        self.assertIsNone(self.backed_up())

    def test_the_plan_file_is_never_touched(self):
        before = self.plan_path.read_bytes()
        self.run_script("--albums", "Red")
        self.assertEqual(self.plan_path.read_bytes(), before)

    def test_a_wrong_name_changes_nothing_and_says_what_exists(self):
        done = self.run_script("--albums", "Red,Lizard")
        self.assertEqual(done.returncode, 2)
        self.assertIn("Lizard", done.stderr)
        self.assertIn("Discipline", done.stderr)
        self.assertEqual(self.tagged(), [])
        self.assertIsNone(self.backed_up())

    def test_a_bad_limit_changes_nothing(self):
        done = self.run_script("--limit", "0")
        self.assertEqual(done.returncode, 2)
        self.assertEqual(self.tagged(), [])

    def test_the_filter_is_refused_with_restore_instead_of_being_ignored(self):
        self.run_script("--albums", "Red")                      # makes a backup
        backup = sorted(self.backups.glob("tags_backup_*.json"))[-1]
        for flag in (["--albums", "Red"], ["--limit", "1"]):
            with self.subTest(flag=flag):
                done = subprocess.run([sys.executable, str(SCRIPT), "--restore", str(backup), *flag],
                                      capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(done.returncode, 2)
                self.assertIn("--restore", done.stderr)
        self.assertEqual(self.tagged(), ["1974 - Red"])          # nothing was restored


if __name__ == "__main__":
    unittest.main()
