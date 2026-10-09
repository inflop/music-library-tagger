# -*- coding: utf-8 -*-
"""MusicBrainz asks for about one request per second; CONTRIBUTING.md makes that a rule.

No network is used: urlopen and the clock are replaced, so the tests see exactly when each
request would have gone out.

Run with:  python -m unittest discover -s tests -v
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                       / "skills" / "music-library-tagger" / "scripts"))

# Importing the script swaps sys.stdout for a new TextIOWrapper around the same buffer.
# Dropping a wrapper closes that buffer, which would break every test that runs after
# this one, so keep the new wrapper alive and put the original stream back.
_original_stdout = sys.stdout
import fetch_cover  # noqa: E402
_wrapper_made_by_the_script = sys.stdout
sys.stdout = _original_stdout


MINIMUM = 1.1   # seconds between MusicBrainz requests: what the fix promises and the docs say
EPS = 1e-9      # float rounding in the fake clock


class FakeClock:
    """A clock that only moves when the code under test sleeps."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def tick(self, seconds):
        self.now += seconds


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


class RateLimit(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.requests = []   # (time, url)
        self.answers = []    # what each successive request returns

        def urlopen(req, timeout=None):
            self.requests.append((self.clock.now, req.full_url))
            answer = self.answers.pop(0) if self.answers else {"release-groups": []}
            if isinstance(answer, Exception):
                raise answer
            return FakeResponse(answer)

        patches = [
            mock.patch.object(fetch_cover.time, "sleep", self.clock.sleep),
            mock.patch.object(fetch_cover.time, "monotonic", self.clock.monotonic),
            mock.patch.object(fetch_cover.urllib.request, "urlopen", urlopen),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        fetch_cover._last_mb_request = None   # each test starts with a quiet history

    def mb_gaps(self):
        times = [t for t, url in self.requests if url.startswith(fetch_cover.MB)]
        return [b - a for a, b in zip(times, times[1:])]

    def test_the_configured_interval_is_the_documented_one(self):
        self.assertGreaterEqual(fetch_cover.MB_MIN_INTERVAL, MINIMUM)

    def test_the_fallback_query_waits_for_the_rate_limit(self):
        # The strict query finds nothing, so the loose fallback follows at once.
        self.answers = [{"release-groups": []}, {"release-groups": [{"id": "x"}]}]
        result = fetch_cover.mb_release_groups("Some Band", "It's: Complicated")
        self.assertEqual(result, [{"id": "x"}])
        self.assertEqual(len(self.requests), 2)
        self.assertGreaterEqual(self.mb_gaps()[0] + EPS, MINIMUM)

    def test_any_two_musicbrainz_requests_are_spaced_out(self):
        for _ in range(4):
            fetch_cover._mb_query('artist:"a" AND releasegroup:"b"')
        self.assertEqual(len(self.mb_gaps()), 3)
        self.assertTrue(all(gap + EPS >= MINIMUM for gap in self.mb_gaps()), self.mb_gaps())

    def test_time_already_spent_counts_towards_the_wait(self):
        fetch_cover._mb_query("first")
        self.clock.tick(5)                       # the caller was busy for a while
        fetch_cover._mb_query("second")
        self.assertEqual(self.clock.sleeps, [])  # no needless pause

    def test_a_retry_after_a_failure_is_spaced_out_too(self):
        self.answers = [OSError("boom"), {"release-groups": []}]
        fetch_cover._mb_query("flaky")
        self.assertEqual(len(self.mb_gaps()), 1)
        self.assertGreaterEqual(self.mb_gaps()[0] + EPS, MINIMUM)

    def test_cover_art_archive_requests_are_not_throttled(self):
        for _ in range(3):
            fetch_cover._get(fetch_cover.CAA + "/release-group/abc")
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.clock.sleeps, [])


if __name__ == "__main__":
    unittest.main()
