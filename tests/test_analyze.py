"""Behavior checks for the read-only library scanner."""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audio_fixtures import MP3_BYTES, flac_bytes  # noqa: E402
from mutagen.flac import FLAC, Picture  # noqa: E402
from PIL import Image  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
