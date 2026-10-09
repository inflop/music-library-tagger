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
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        ext, payload, writer, _ = FACTORIES[kind]
        self._libraries = getattr(self, "_libraries", 0) + 1
        root = os.path.join(self.tmp, "%s-%d" % (kind, self._libraries), "Band")
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
    def output_of(fn, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn(*args)
        return buf.getvalue()

    @staticmethod
    def summary(path):
        s = tagio.backend_for(path).read_summary(path)
        return dict(s, fields=sorted(s["fields"]), comments=sorted(s["comments"]))

    def each_kind(self):
        for kind in FACTORIES:
            with self.subTest(kind=kind):
                yield kind

    # -- registry ----------------------------------------------------------
    def test_every_registered_backend_has_a_fixture(self):
        # The contract tests only run for formats that have a factory, so a backend
        # added to BACKENDS without one would silently skip all of them.
        self.assertEqual({b.NAME for b in tagio.BACKENDS},
                         {name for *_, name in FACTORIES.values()})

    def test_registry_resolves_every_fixture_extension(self):
        for kind, (ext, _, _, name) in FACTORIES.items():
            backend = tagio.backend_for("Some Track" + ext.upper())
            self.assertIsNotNone(backend, kind)
            self.assertEqual(backend.NAME, name)
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

    def test_apply_refuses_a_target_that_is_not_audio(self):
        # mutagen would happily prepend an ID3 tag to any file it is handed.
        for kind in self.each_kind():
            plan, paths, _, _ = self.library(kind)
            victim = os.path.join(os.path.dirname(paths[0]), "settings.cfg")
            Path(victim).write_bytes(b"key = value")
            plan["albums"][0]["discs"][0]["tracks"].append(
                {"file": "settings.cfg", "track": 3, "title": "x"})
            out = self.output_of(apply_plan.apply, plan, False)
            self.assertIn("not a supported audio file", out)
            self.assertEqual(Path(victim).read_bytes(), b"key = value")

    def test_a_file_its_backend_cannot_read_is_skipped_everywhere(self):
        # A name with a supported extension is not proof the file is readable: the
        # dry run must not count it, and a real run must neither crash on it nor touch it.
        for kind in self.each_kind():
            plan, paths, _, backup = self.library(kind)
            junk = os.path.join(os.path.dirname(paths[0]), "99 - Broken" + FACTORIES[kind][0])
            Path(junk).write_bytes(b"this is not audio")
            plan["albums"][0]["discs"][0]["tracks"].append(
                {"file": os.path.basename(junk), "track": 3, "title": "x"})

            dry = self.output_of(apply_plan.apply, plan, True)
            counts = json.loads(dry.split("SUMMARY:", 1)[1].strip())
            self.assertEqual((counts["tracks"], counts["unreadable"]), (2, 1))
            self.assertIn("cannot read", dry)

            self.quiet(apply_plan.backup_tags, plan["root"], plan, backup)
            self.assertNotIn("Broken", Path(backup).read_text(encoding="utf-8"))
            self.quiet(apply_plan.apply, plan, False)
            self.assertEqual(Path(junk).read_bytes(), b"this is not audio")
            self.assertEqual(self.summary(paths[0])["album"], "Red")

    def test_a_file_of_another_format_is_never_written_under_this_extension(self):
        # mutagen detects the format from the content, so a FLAC named .mp3 is
        # "readable" to a naive check and would get an ID3 tag prepended.
        formats = {}
        for kind, spec in FACTORIES.items():
            formats.setdefault(spec[3], kind)
        for a in formats.values():
            for b in formats.values():
                if FACTORIES[a][3] == FACTORIES[b][3]:
                    continue
                with self.subTest(named_as=a, really=b):
                    plan, paths, _, backup = self.library(a)
                    self.quiet(apply_plan.backup_tags, plan["root"], plan, backup)  # valid
                    FACTORIES[b][2](paths[1], album="x", title="x", cover=jpeg((1, 2, 3)))
                    mislabelled = Path(paths[1]).read_bytes()

                    dry = self.output_of(apply_plan.apply, plan, True)
                    counts = json.loads(dry.split("SUMMARY:", 1)[1].strip())
                    self.assertEqual((counts["tracks"], counts["unreadable"]), (1, 1))

                    self.quiet(apply_plan.apply, plan, False)
                    self.assertEqual(Path(paths[1]).read_bytes(), mislabelled)
                    out = self.output_of(apply_plan.restore, backup)
                    self.assertEqual(Path(paths[1]).read_bytes(), mislabelled)
                    self.assertIn("could not restore", out)

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

    def test_restore_dry_run_changes_nothing_and_says_what_it_would_do(self):
        for kind in self.each_kind():
            plan, paths, _, backup = self.library(kind)
            before = [self.summary(p) for p in paths]
            self.quiet(apply_plan.backup_tags, plan["root"], plan, backup)
            self.quiet(apply_plan.apply, plan, False)
            applied = [Path(p).read_bytes() for p in paths]
            folder = sorted(os.listdir(os.path.dirname(paths[0])))

            out = self.output_of(apply_plan.restore, backup, True)

            self.assertEqual([Path(p).read_bytes() for p in paths], applied)  # untouched
            self.assertEqual(sorted(os.listdir(os.path.dirname(paths[0]))), folder)
            self.assertIn("DRY-RUN", out)
            self.assertIn("would restore tags on 2 files", out)
            self.assertIn("2 artwork images would be reinstated", out)
            self.assertNotIn("Restored tags on", out)
            # and the real restore still brings the originals back afterwards
            self.quiet(apply_plan.restore, backup)
            self.assertEqual([self.summary(p) for p in paths], before)

    def test_restore_dry_run_reports_an_entry_that_would_fail(self):
        for kind in self.each_kind():
            plan, paths, _, backup = self.library(kind)
            self.quiet(apply_plan.backup_tags, plan["root"], plan, backup)
            self.quiet(apply_plan.apply, plan, False)
            data = json.loads(Path(backup).read_text(encoding="utf-8"))
            rel = next(iter(data["files"]))
            data["files"][rel] = {k: v for k, v in data["files"][rel].items()
                                  if k in ("format", "had_apic", "apic")}
            Path(backup).write_text(json.dumps(data), encoding="utf-8")
            applied = [Path(p).read_bytes() for p in paths]

            out = self.output_of(apply_plan.restore, backup, True)

            self.assertIn("could not restore", out)
            self.assertIn("would restore tags on 1 file,", out)
            self.assertIn("1 artwork image would be reinstated", out)
            self.assertEqual([Path(p).read_bytes() for p in paths], applied)

    def test_a_dry_run_notices_a_target_it_could_not_write(self):
        # The probe is a copy, which the process owns and can always write; the real
        # file may not be writable, and a preview that says "restorable" would be wrong.
        for kind in self.each_kind():
            plan, paths, _, backup = self.library(kind)
            self.quiet(apply_plan.backup_tags, plan["root"], plan, backup)
            self.quiet(apply_plan.apply, plan, False)
            os.chmod(paths[0], stat.S_IREAD)
            self.addCleanup(os.chmod, paths[0], stat.S_IREAD | stat.S_IWRITE)
            if os.access(paths[0], os.W_OK):
                continue   # running as a user that ignores file permissions (root)
            before = Path(paths[0]).read_bytes()

            # What makes the probe misleading is that the copy is newly created by this
            # process, so it is writable even when the original is not (another owner,
            # say). copy2 keeps the read-only bit, which would hide that: make the copy
            # writable the way a change of ownership does.
            real_copy2 = shutil.copy2

            def copy_owned_by_us(src, dst, *args, **kwargs):
                out = real_copy2(src, dst, *args, **kwargs)
                os.chmod(dst, stat.S_IREAD | stat.S_IWRITE)
                return out

            with mock.patch.object(apply_plan.shutil, "copy2", copy_owned_by_us):
                dry = self.output_of(apply_plan.restore, backup, True)
            real = self.output_of(apply_plan.restore, backup)

            self.assertIn("would restore tags on 1 file,", dry)
            self.assertIn("1 file(s) would not be restored", dry)
            self.assertIn("1 file(s) could not be restored", real)   # the preview was right
            self.assertEqual(Path(paths[0]).read_bytes(), before)

    def test_a_dry_run_is_not_failed_by_the_permissions_of_its_own_copy(self):
        # A file owned by someone else and writable through its group bits (0460) is
        # writable for the real restore. Its copy belongs to this process, so the copied
        # owner bits (read-only) would stop the probe: a false "would not be restored".
        for kind in self.each_kind():
            plan, paths, _, backup = self.library(kind)
            self.quiet(apply_plan.backup_tags, plan["root"], plan, backup)
            self.quiet(apply_plan.apply, plan, False)
            real_copy2 = shutil.copy2

            def copy_with_unwritable_owner_bits(src, dst, *args, **kwargs):
                out = real_copy2(src, dst, *args, **kwargs)
                os.chmod(dst, stat.S_IREAD)
                return out

            with mock.patch.object(apply_plan.shutil, "copy2", copy_with_unwritable_owner_bits):
                dry = self.output_of(apply_plan.restore, backup, True)

            self.assertIn("would restore tags on 2 files,", dry)
            self.assertNotIn("would not be restored", dry)

    def test_every_entry_a_restore_skips_is_reported_and_counted(self):
        # Refused paths, unsupported targets and missing files used to be skipped
        # without being counted (and a missing file without a word), so the summary
        # could look complete when entries had been left out.
        for kind in self.each_kind():
            plan, paths, _, backup = self.library(kind)
            self.quiet(apply_plan.backup_tags, plan["root"], plan, backup)
            self.quiet(apply_plan.apply, plan, False)
            data = json.loads(Path(backup).read_text(encoding="utf-8"))
            template = next(iter(data["files"].values()))
            ext = FACTORIES[kind][0]
            data["files"]["../outside" + ext] = template                  # escapes the root
            data["files"]["settings.cfg"] = template                      # not audio
            gone = os.path.relpath(paths[1], plan["root"]).replace("\\", "/")
            data["files"][gone] = template                                # file was removed
            os.remove(paths[1])
            Path(backup).write_text(json.dumps(data), encoding="utf-8")

            dry = self.output_of(apply_plan.restore, backup, True)
            real = self.output_of(apply_plan.restore, backup)

            for out, footer in ((dry, "3 file(s) would not be restored"),
                                (real, "3 file(s) could not be restored")):
                self.assertIn("refusing path outside the backup root", out)
                self.assertIn("refusing a target that is not a supported audio file", out)
                self.assertIn("is missing, nothing to restore", out)
                self.assertIn(footer, out)
            self.assertIn("would restore tags on 1 file,", dry)
            self.assertIn("Restored tags on 1 file,", real)

    def test_the_restore_summary_uses_the_singular_for_one(self):
        for kind in self.each_kind():
            plan, paths, _, backup = self.library(kind)
            plan["albums"][0]["discs"][0]["tracks"].pop()      # a one-track album
            self.quiet(apply_plan.backup_tags, plan["root"], plan, backup)
            self.quiet(apply_plan.apply, plan, False)
            dry = self.output_of(apply_plan.restore, backup, True)
            real = self.output_of(apply_plan.restore, backup)
            self.assertIn("would restore tags on 1 file, 1 artwork image would be reinstated", dry)
            self.assertIn("Restored tags on 1 file, 1 artwork image reinstated", real)
            for text in (dry, real):
                self.assertNotIn("1 files", text)
                self.assertNotIn("1 artwork images", text)

    def test_the_command_line_honours_dry_run_with_restore(self):
        # The bug was in main(): --restore returned before --dry-run was looked at.
        plan, paths, _, backup = self.library("mp3")
        self.quiet(apply_plan.backup_tags, plan["root"], plan, backup)
        self.quiet(apply_plan.apply, plan, False)
        applied = [Path(p).read_bytes() for p in paths]
        script = Path(apply_plan.__file__)
        done = subprocess.run([sys.executable, str(script), "--restore", backup, "--dry-run"],
                              capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("DRY-RUN", done.stdout)
        self.assertEqual([Path(p).read_bytes() for p in paths], applied)
        done = subprocess.run([sys.executable, str(script), "--restore", backup],
                              capture_output=True, text=True, encoding="utf-8")
        self.assertIn("Restored tags on 2 files", done.stdout)
        self.assertNotEqual([Path(p).read_bytes() for p in paths], applied)

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
            name = FACTORIES[kind][3]
            if name == tagio.LEGACY_FORMAT:
                self.assertNotIn("format", entry)  # the layout older backups use
            else:
                self.assertEqual(entry["format"], name)
            self.assertTrue(entry["had_apic"])

    def test_restore_refuses_an_entry_that_lost_its_tag_payload(self):
        # A backup entry with no tag snapshot must not be read as "the file had no
        # tags", which would wipe the tags the file has now.
        for kind in self.each_kind():
            plan, paths, _, backup = self.library(kind)
            self.quiet(apply_plan.backup_tags, plan["root"], plan, backup)
            self.quiet(apply_plan.apply, plan, False)
            data = json.loads(Path(backup).read_text(encoding="utf-8"))
            for rel, entry in data["files"].items():
                data["files"][rel] = {k: v for k, v in entry.items()
                                      if k in ("format", "had_apic", "apic")}
            Path(backup).write_text(json.dumps(data), encoding="utf-8")
            before = [Path(p).read_bytes() for p in paths]
            out = self.output_of(apply_plan.restore, backup)
            self.assertIn("could not restore", out)
            self.assertEqual([Path(p).read_bytes() for p in paths], before)

    def test_restore_skips_an_entry_written_for_another_format(self):
        # two variants that belong to different backends
        a = next(iter(FACTORIES))
        b = next(k for k, v in FACTORIES.items() if v[3] != FACTORIES[a][3])
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
