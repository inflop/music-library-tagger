# -*- coding: utf-8 -*-
"""--albums / --limit: apply (and back up) only part of a plan without editing plan.json.

The documented pilot used to mean trimming the generated JSON by hand right before the
first write to someone's library, so the plan that was reviewed was not the plan that ran.

Run with:  python -m unittest discover -s tests -v
"""
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
from unittest import mock

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

    def test_an_empty_selection_is_an_error_not_a_no_op(self):
        # An empty shell variable (--albums "$PILOT") must not turn "apply these albums"
        # into a successful run that applies none.
        for value in ("", "   ", ",", " , ,"):
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                apply_plan.select_albums(self.plan, [value], None)
            self.assertIn("names no album", str(caught.exception))

    def test_a_filter_that_leaves_nothing_is_an_error(self):
        empty = {"root": "x", "options": {}, "albums": []}
        with self.assertRaises(ValueError):
            apply_plan.select_albums(empty, None, 1)
        self.assertEqual(apply_plan.select_albums(empty, None, None), empty)   # no filter: as before

    def test_a_limit_below_one_is_an_error_that_lists_the_albums(self):
        for bad in (0, -1):
            with self.subTest(limit=bad), self.assertRaises(ValueError) as caught:
                apply_plan.select_albums(self.plan, None, bad)
            self.assertIn("Discipline", str(caught.exception))


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

    def test_an_empty_albums_value_changes_nothing(self):
        done = self.run_script("--albums", "")
        self.assertEqual(done.returncode, 2)
        self.assertIn("names no album", done.stderr)
        self.assertEqual(self.tagged(), [])
        self.assertIsNone(self.backed_up())

    def test_a_bad_limit_changes_nothing(self):
        done = self.run_script("--limit", "0")
        self.assertEqual(done.returncode, 2)
        self.assertIn("Discipline", done.stderr)          # what the plan does contain
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


class TestFailedBackup(Library):
    def test_a_backup_that_fails_leaves_no_reserved_empty_file_behind(self):
        # The name is reserved before the backup is written. If writing fails, an empty
        # tags_backup_*.json would look like a backup and fail --restore confusingly.
        argv = ["apply_plan.py", "--plan", str(self.plan_path), "--albums", "Red"]
        with mock.patch.object(sys, "argv", argv),                 mock.patch.object(apply_plan, "backup_tags", side_effect=OSError("disk full")),                 contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(OSError):
                apply_plan.main()
        self.assertEqual(list(self.backups.glob("tags_backup_*")), [])
        self.assertEqual(self.tagged(), [])                  # and nothing was applied


class TestBackupNames(Library):
    def test_a_new_backup_never_reuses_an_existing_name(self):
        self.backups.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(apply_plan.time, "strftime", return_value="20260101_000000"):
            first = apply_plan.new_backup_path(str(self.backups))
            Path(first).write_text("{}", encoding="utf-8")
            second = apply_plan.new_backup_path(str(self.backups))
            Path(second).write_text("{}", encoding="utf-8")
            (self.backups / "tags_backup_20260101_000000_3_art").mkdir()   # a leftover art folder
            third = apply_plan.new_backup_path(str(self.backups))
        self.assertEqual(len({first, second, third}), 3)
        self.assertTrue(first.endswith("tags_backup_20260101_000000.json"))

    def test_the_name_is_reserved_so_concurrent_runs_cannot_share_it(self):
        # Checking that a name is free and creating the file later leaves a window in which
        # two simultaneous runs both pick the same name and the second overwrites the first.
        # The file is created, exclusively, as part of choosing the name.
        self.backups.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(apply_plan.time, "strftime", return_value="20260101_000000"):
            first = apply_plan.new_backup_path(str(self.backups))
            second = apply_plan.new_backup_path(str(self.backups))   # nothing written in between
        self.assertNotEqual(first, second)
        self.assertTrue(os.path.exists(first) and os.path.exists(second))

    def test_the_reserved_file_gets_ordinary_permissions_not_executable_ones(self):
        # os.open without a mode creates 0o777 before the umask, so a backup would be
        # born executable (0o775 under a 002 umask), unlike one written by open(..., "w").
        self.backups.mkdir(parents=True, exist_ok=True)
        modes = []
        real_open = os.open

        def recording_open(path, flags, mode=0o777, *args, **kwargs):
            modes.append(mode)
            return real_open(path, flags, mode, *args, **kwargs)

        with mock.patch.object(apply_plan.os, "open", recording_open):
            apply_plan.new_backup_path(str(self.backups))
        self.assertEqual(modes, [0o666])

    def test_two_pilots_in_the_same_second_keep_both_backups(self):
        # Pilots make several runs in quick succession likely. A shared name would let the
        # second overwrite the first backup, and with it the way back from the first run.
        def run(*args):
            argv = ["apply_plan.py", "--plan", str(self.plan_path), *args]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(apply_plan.time, "strftime", return_value="20260101_000000"), \
                    contextlib.redirect_stdout(io.StringIO()):
                apply_plan.main()

        run("--albums", "Red")
        run("--albums", "Beat")
        backups = sorted(self.backups.glob("tags_backup_*.json"))
        self.assertEqual(len(backups), 2)
        contents = [sorted(json.loads(b.read_text(encoding="utf-8"))["files"]) for b in backups]
        self.assertEqual(sorted(contents), [["1974 - Red/01.mp3"], ["1982 - Beat/01.mp3"]])


if __name__ == "__main__":
    unittest.main()
