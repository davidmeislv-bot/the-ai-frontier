#!/usr/bin/env python3
"""Print the URLs to submit for indexing, straight from the manifest.

Never type indexing URLs by hand: article URLs follow each article's own
date (/YYYY/MM/DD/<slug>/), which is not always the edition page's path.

    python3 build.py                       # build first
    python3 tools/index_urls.py            # newest edition
    python3 tools/index_urls.py --edition 14
    python3 tools/index_urls.py --edition 14 --live   # also check wirewillow.com

Output: one absolute URL per line — the homepage, the edition page, then
every article in the edition, in edition order. Paste it as is.

Every URL is checked against the built _site/ first; the script prints
nothing and exits 1 if any page is missing, so a wrong URL can't reach
Google. --live additionally requests each URL from the live site and
fails on anything but HTTP 200 (run it after the Actions deploy finishes).
Redirect pages are never listed.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "_site"


def built_file(path):
    rel = path.lstrip("/")
    return SITE / (rel + "index.html" if rel == "" or rel.endswith("/") else rel)


def live_status(url):
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "wirewillow-index-check"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except urllib.error.URLError as exc:
        return str(exc.reason)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--edition", type=int, help="edition number (seq); default: newest")
    parser.add_argument("--live", action="store_true", help="also require HTTP 200 from the live site")
    args = parser.parse_args(argv)

    manifest = json.loads((ROOT / "src/manifest.json").read_text(encoding="utf-8"))
    base = manifest["site"]["base_url"]
    editions = {e["seq"]: e for e in manifest["editions"]}
    seq = args.edition if args.edition is not None else max(editions)
    if seq not in editions:
        print(f"No edition with seq {seq}. Known: {sorted(editions)}", file=sys.stderr)
        return 1
    edition = editions[seq]
    articles = {a["slug"]: a for a in manifest["articles"]}

    paths = ["/", edition["url"]] + [articles[s]["url"] for s in edition.get("article_slugs", [])]

    if not SITE.is_dir():
        print("_site/ not found — run python3 build.py first.", file=sys.stderr)
        return 1
    problems = [p for p in paths if not built_file(p).is_file()]
    if problems:
        print("Not in the built site (fix before submitting):", file=sys.stderr)
        for p in problems:
            print("  " + base + p, file=sys.stderr)
        return 1

    urls = [base + p for p in paths]
    if args.live:
        bad = [(u, s) for u in urls if (s := live_status(u)) != 200]
        if bad:
            print("Live site did not return 200 (wait for the deploy, then retry):", file=sys.stderr)
            for u, s in bad:
                print(f"  {s}  {u}", file=sys.stderr)
            return 1

    print("\n".join(urls))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
