"""Behavior checks for the read-only library scanner."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ANALYZE = (Path(__file__).resolve().parents[1]
           / "skills" / "music-library-tagger" / "scripts" / "analyze.py")


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
