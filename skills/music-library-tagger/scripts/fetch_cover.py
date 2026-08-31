# -*- coding: utf-8 -*-
"""
Fetch a front cover from the Cover Art Archive (via MusicBrainz).

Privacy: uses a NEUTRAL User-Agent with no personal data.

Usage:
    python fetch_cover.py --artist "King Crimson" --album "Red" [--year 1974] \
        [--size 1200] --out "cover.jpg"
    python fetch_cover.py --artist "King Crimson" --album "Red" --info-only

Prints a JSON line: {"found": bool, "url": ..., "width":, "height":, "path":,
                     "release_group": ..., "candidates": [...]}
Exit code 0 if a cover was saved (or found with --info-only), 1 otherwise.

Dependencies: urllib (stdlib), Pillow (optional, for dimensions of --out).
"""
import sys, io, json, time, argparse, urllib.request, urllib.parse, difflib

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# MusicBrainz requires a User-Agent that identifies the application and offers a
# contact. We satisfy that with the project URL only -- no personal data ever
# leaves the machine. Keep VERSION in sync with .claude-plugin/plugin.json.
VERSION = "1.0.1"
CONTACT = "https://github.com/inflop/music-library-tagger"
UA = "MusicLibraryTagger/%s ( %s )" % (VERSION, CONTACT)
MB = "https://musicbrainz.org/ws/2"
CAA = "https://coverartarchive.org"

ALBUMISH = {"Album", "EP", "Single", "Other", "Broadcast"}


def _get(url, accept="application/json", tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read(), r.status
        except Exception as e:
            last = e
            code = getattr(e, "code", None)
            if code == 404:
                raise
            time.sleep(1.5 * (i + 1))  # back off; also respects MB ~1 req/s
    raise last


def _mb_query(q):
    url = "%s/release-group/?query=%s&fmt=json&limit=8" % (MB, urllib.parse.quote(q))
    data, _ = _get(url)
    return json.loads(data).get("release-groups", [])


def mb_release_groups(artist, album):
    # Strict phrase query first.
    rgs = _mb_query('artist:"%s" AND releasegroup:"%s"' % (artist, album))
    if rgs:
        return rgs
    # Fallback: punctuation (apostrophes, colons…) can break exact phrase matching
    # in MusicBrainz's tokenizer. Retry with a loose, unqualified album term.
    import re as _re
    words = _re.sub(r"[^\w\s]", " ", album).strip()
    if words:
        rgs = _mb_query('artist:"%s" AND (%s)' % (artist, words))
    return rgs


def score_rg(rg, album, year):
    title = rg.get("title", "")
    s = difflib.SequenceMatcher(None, title.lower(), album.lower()).ratio() * 100
    ptype = rg.get("primary-type")
    if ptype == "Album":
        s += 15
    elif ptype in ("EP",):
        s += 8
    elif ptype == "Single":
        s -= 10
    frd = rg.get("first-release-date", "") or ""
    if year and frd[:4].isdigit():
        s -= min(abs(int(frd[:4]) - int(year)), 20) * 0.5
    return s


def caa_front(entity, mbid, size):
    """Return (image_url_for_download, meta) for the front image, or (None, None)."""
    url = "%s/%s/%s" % (CAA, entity, mbid)
    try:
        data, _ = _get(url)
    except Exception:
        return None, None
    j = json.loads(data)
    for img in j.get("images", []):
        if img.get("front"):
            thumbs = img.get("thumbnails", {}) or {}
            # Prefer the ORIGINAL upload (highest quality); the caller downscales
            # to its cover_max_px anyway. Fall back to the largest thumbnail.
            if img.get("image"):
                return img["image"], img
            for key in (str(size), "1200", "large", "500", "250"):
                if key in thumbs:
                    return thumbs[key], img
    return None, None


def download(url, out):
    data, _ = _get(url, accept="image/*")
    with open(out, "wb") as f:
        f.write(data)
    dims = None
    try:
        from PIL import Image
        with Image.open(out) as im:
            dims = im.size
    except Exception:
        pass
    return dims


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--artist", required=True)
    ap.add_argument("--album", required=True)
    ap.add_argument("--year", type=int, default=None)
    ap.add_argument("--size", type=int, default=1200, help="preferred CAA thumbnail edge px")
    ap.add_argument("--out", default=None)
    ap.add_argument("--info-only", action="store_true")
    args = ap.parse_args()

    result = {"found": False, "artist": args.artist, "album": args.album}
    try:
        rgs = mb_release_groups(args.artist, args.album)
    except Exception as e:
        result["error"] = "musicbrainz: %s" % e
        print(json.dumps(result, ensure_ascii=False)); sys.exit(1)

    rgs = sorted(rgs, key=lambda r: -score_rg(r, args.album, args.year))
    result["candidates"] = [
        {"title": r.get("title"), "type": r.get("primary-type"),
         "date": r.get("first-release-date"), "id": r.get("id")}
        for r in rgs[:5]
    ]

    time.sleep(1.1)  # be polite to MB between calls
    for rg in rgs[:5]:
        url, meta = caa_front("release-group", rg["id"], args.size)
        if not url:
            # try individual releases in this group
            try:
                data, _ = _get("%s/release-group/%s?fmt=json&inc=releases" % (MB, rg["id"]))
                rels = json.loads(data).get("releases", [])
            except Exception:
                rels = []
            for rel in rels[:6]:
                url, meta = caa_front("release", rel["id"], args.size)
                if url:
                    break
        if url:
            result.update({"found": True, "release_group": rg.get("title"),
                           "release_group_id": rg["id"], "url": url})
            if args.info_only or not args.out:
                print(json.dumps(result, ensure_ascii=False)); sys.exit(0)
            try:
                dims = download(url, args.out)
                result["path"] = args.out
                if dims:
                    result["width"], result["height"] = dims
                print(json.dumps(result, ensure_ascii=False)); sys.exit(0)
            except Exception as e:
                result["error"] = "download: %s" % e
                print(json.dumps(result, ensure_ascii=False)); sys.exit(1)
        time.sleep(1.1)

    print(json.dumps(result, ensure_ascii=False)); sys.exit(1)


if __name__ == "__main__":
    main()
