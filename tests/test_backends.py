# -*- coding: utf-8 -*-
"""Contract tests: every registered tag backend must behave the same way.

Run with:  python -m unittest discover -s tests -v

The suite walks audio_fixtures.FACTORIES, so supporting a new format means adding
a fixture writer there; nothing in this file names a format.
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
import tagio  # noqa: E402
from audio_fixtures import FACTORIES, jpeg  # noqa: E402

SUMMARY_KEYS = {"title", "track", "disc", "album", "artist", "album_artist",
                "genre", "year", "n_pictures", "fields", "comments"}


class BackendContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mlt-contract-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def library(self, kind, tagged=True):
        """One album, two tracks of `kind`: (plan, track paths, payload, backup)."""
        ext, payload, writer = FACTORIES[kind]
        root = os.path.join(self.tmp, kind, "Band")
        disc = os.path.join(root, "1974 - Red")
        os.makedirs(disc)
        os.makedirs(os.path.join(root, ".music-tagger"))
        names = ["01 - Red" + ext, "02 - Fallen Angel" + ext]
        for i, name in enumerate(names, 1):
            writer(os.path.join(disc, name), album="old album",
                   title="old title %d" % i, cover=jpeg((200, 0, 0)),
                   tagged=tagged)
        with open(os.path.join(disc, "new.jpg"), "wb") as f:
            f.write(jpeg((0, 160, 0), size=(600, 600)))
        plan = {
            "root": root,
            "options": {"cover_embed": True, "cover_folder_jpg": True,
                        "cover_max_px": 1400, "artist": "Band",
                        "album_artist": "Band", "genre": "Prog"},
            "albums": [{
                "album": "Red", "year": 1974, "album_path": "1974 - Red",
                "discs": [{
                    "path": "1974 - Red", "disc": 1, "disc_total": 2,
                    "cover": "1974 - Red/new.jpg",
                    "tracks": [{"file": n, "track": i, "track_total": 2,
                                "title": "Track %d" % i}
                               for i, n in enumerate(names, 1)],
                }],
            }],
        }
        paths = [os.path.join(disc, n) for n in names]
        backup = os.path.join(root, ".music-tagger", "backup.json")
        return plan, paths, payload, backup

    @staticmethod
    def quiet(fn, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return fn(*args)

    @staticmethod
    def summary(path):
        s = tagio.backend_for(path).read_summary(path)
        return dict(s, fields=sorted(s["fields"]), comments=sorted(s["comments"]))

    def each_kind(self):
        for kind in FACTORIES:
            with self.subTest(kind=kind):
                yield kind

    # -- registry ----------------------------------------------------------
    def test_registry_resolves_every_fixture_extension(self):
        for kind, (ext, _, _) in FACTORIES.items():
            backend = tagio.backend_for("Some Track" + ext.upper())
            self.assertIsNotNone(backend, kind)
            self.assertEqual(backend.NAME, kind)
            self.assertIn(ext, tagio.AUDIO_EXT)

    def test_registry_rejects_other_files(self):
        for name in ("cover.jpg", "notes.txt", "noext", "01.xyz"):
            self.assertIsNone(tagio.backend_for(name), name)

    def test_skipped_and_supported_extensions_never_overlap(self):
        self.assertFalse(set(tagio.AUDIO_EXT) & set(tagio.SKIPPED_AUDIO_EXT))

    # -- behaviour every backend shares -----------------------------------------
    def test_summary_has_the_shared_shape(self):
        for kind in self.each_kind():
            _, paths, _, _ = self.library(kind)
            s = tagio.backend_for(paths[0]).read_summary(paths[0])
            self.assertEqual(set(s), SUMMARY_KEYS)
            self.assertEqual(s["album"], "old album")
            self.assertEqual(s["title"], "old title 1")
            self.assertEqual(s["n_pictures"], 1)

    def test_apply_writes_the_planned_fields(self):
        for kind in self.each_kind():
            plan, paths, _, _ = self.library(kind)
            self.quiet(apply_plan.apply, plan, False)
            s = self.summary(paths[1])
            self.assertEqual(
                (s["album"], s["title"], s["artist"], s["album_artist"],
                 s["genre"], s["year"], s["track"], s["disc"], s["n_pictures"]),
                ("Red", "Track 2", "Band", "Band", "Prog", "1974", "2/2",
                 "1/2", 1))

    def test_apply_never_touches_the_audio_payload(self):
        for kind in self.each_kind():
            plan, paths, payload, _ = self.library(kind)
            self.quiet(apply_plan.apply, plan, False)
            for p in paths:
                self.assertEqual(Path(p).read_bytes()[-len(payload):], payload)

    def test_dry_run_changes_nothing(self):
        for kind in self.each_kind():
            plan, paths, _, _ = self.library(kind)
            before = [Path(p).read_bytes() for p in paths]
            self.quiet(apply_plan.apply, plan, True)
            self.assertEqual([Path(p).read_bytes() for p in paths], before)

    def test_backup_then_restore_returns_the_original_tags(self):
        for kind in self.each_kind():
            plan, paths, payload, backup = self.library(kind)
            before = [self.summary(p) for p in paths]
            self.quiet(apply_plan.backup_tags, plan["root"], plan, backup)
            self.quiet(apply_plan.apply, plan, False)
            self.assertNotEqual(self.summary(paths[0]), before[0])
            self.quiet(apply_plan.restore, backup)
            self.assertEqual([self.summary(p) for p in paths], before)
            for p in paths:
                self.assertEqual(Path(p).read_bytes()[-len(payload):], payload)

    def test_untagged_files_end_up_untagged_after_a_restore(self):
        for kind in self.each_kind():
            plan, paths, _, backup = self.library(kind, tagged=False)
            self.quiet(apply_plan.backup_tags, plan["root"], plan, backup)
            self.quiet(apply_plan.apply, plan, False)
            self.assertEqual(self.summary(paths[0])["album"], "Red")
            self.quiet(apply_plan.restore, backup)
            s = self.summary(paths[0])
            self.assertEqual((s["album"], s["title"], s["n_pictures"]),
                             ("", "", 0))

    def test_backup_marks_the_format_and_keeps_mp3_entries_unmarked(self):
        for kind in self.each_kind():
            plan, paths, _, backup = self.library(kind)
            self.quiet(apply_plan.backup_tags, plan["root"], plan, backup)
            data = json.loads(Path(backup).read_text(encoding="utf-8"))
            entry = next(iter(data["files"].values()))
            if kind == "mp3":
                self.assertNotIn("format", entry)  # the layout older backups use
            else:
                self.assertEqual(entry["format"], kind)
            self.assertTrue(entry["had_apic"])

    def test_restore_skips_an_entry_written_for_another_format(self):
        kinds = list(FACTORIES)
        a, b = kinds[0], kinds[1]
        plan, paths, _, backup = self.library(a)
        self.quiet(apply_plan.backup_tags, plan["root"], plan, backup)
        data = json.loads(Path(backup).read_text(encoding="utf-8"))
        rel = next(iter(data["files"]))
        entry = data["files"].pop(rel)
        # the same entry, but now claimed by a file of the other format
        other_rel = os.path.splitext(rel)[0] + FACTORIES[b][0]
        data["files"][other_rel] = entry
        Path(backup).write_text(json.dumps(data), encoding="utf-8")
        other = os.path.join(plan["root"], other_rel.replace("/", os.sep))
        FACTORIES[b][2](other, album="keep", title="keep", cover=None)
        before = Path(other).read_bytes()
        self.quiet(apply_plan.restore, backup)
        self.assertEqual(Path(other).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
