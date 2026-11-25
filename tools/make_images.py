#!/usr/bin/env python3
"""Prepare Wire Willow images. Run after adding or replacing any image:

    pip install "Pillow>=11.3"      # one time; needs AVIF support
    python3 tools/make_images.py

For every master image  images/<name>.webp  it:

  1. Caps the master at 1600 px wide (re-encoded as WebP quality 78).
     The master is what old browsers without AVIF support get, and what
     social and search previews point at.
  2. Writes AVIF copies at 640, 800, 1024 and 1600 px wide:
     images/<name>-640.avif ... build.py turns these into <picture>
     sources, so phones download ~50–90 KB instead of ~1 MB.
  3. Deletes the old WebP size variants (-640/-1024/-1600.webp), which
     the AVIF copies replace.

It also creates the 1200x630 social card for any article whose og_image
file does not exist yet (cropped from the lead image). Existing cards are
never overwritten unless you pass --force-og.

Work is skipped for images whose outputs are already newer than the
master, so re-running is fast. build.py stays dependency-free; it only
checks that every file this script makes is present and within budget.
"""

import json
import sys
from pathlib import Path

from PIL import Image, features

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "images"
MAX_WIDTH = 1600
MASTER_QUALITY = 78
AVIF_WIDTHS = (640, 800, 1024, 1600)
AVIF_QUALITY = 55
OLD_WEBP_SUFFIXES = ("-640", "-800", "-1024", "-1600")
OG_SIZE = (1200, 630)


def is_variant(path):
    return path.stem.endswith(OLD_WEBP_SUFFIXES)


def avif_widths(master_width):
    return [w for w in AVIF_WIDTHS if w <= master_width] or [master_width]


def cap_master(src):
    """Downscale an oversized master in place. Returns True if changed."""
    with Image.open(src) as im:
        if im.width <= MAX_WIDTH:
            return False
        im.load()
        h = round(im.height * MAX_WIDTH / im.width)
        resized = im.resize((MAX_WIDTH, h), Image.LANCZOS)
    resized.save(src, "WEBP", quality=MASTER_QUALITY, method=6)
    return True


def make_avifs(src):
    made = []
    with Image.open(src) as im:
        im = im.convert("RGB")
        for w in avif_widths(im.width):
            dest = src.with_name("%s-%d.avif" % (src.stem, w))
            if dest.exists() and dest.stat().st_mtime >= src.stat().st_mtime:
                continue
            h = round(im.height * w / im.width)
            im.resize((w, h), Image.LANCZOS).save(dest, "AVIF", quality=AVIF_QUALITY, speed=6)
            made.append(dest.name)
    return made


def remove_old_variants(src):
    removed = []
    for suffix in OLD_WEBP_SUFFIXES:
        old = src.with_name(src.stem + suffix + ".webp")
        if old.exists():
            old.unlink()
            removed.append(old.name)
    return removed


def make_og(src, dest, zoom=1.0):
    """Crop the source to 1.905:1 (optionally zoomed in) and save as a
    1200x630 JPEG."""
    with Image.open(src) as im:
        im = im.convert("RGB")
        target = OG_SIZE[0] / OG_SIZE[1]
        if im.width / im.height > target:
            crop_w, crop_h = im.height * target, im.height
        else:
            crop_w, crop_h = im.width, im.width / target
        crop_w, crop_h = crop_w / zoom, crop_h / zoom
        left = (im.width - crop_w) / 2
        top = (im.height - crop_h) / 2
        im = im.crop((round(left), round(top), round(left + crop_w), round(top + crop_h)))
        im.resize(OG_SIZE, Image.LANCZOS).save(dest, "JPEG", quality=80, optimize=True, progressive=True)


def main(argv):
    if not features.check("avif"):
        print("Pillow was built without AVIF support: pip install -U 'Pillow>=11.3'", file=sys.stderr)
        return 1
    force_og = "--force-og" in argv
    for src in sorted(IMAGES.glob("*.webp")):
        if is_variant(src):
            continue
        notes = []
        if cap_master(src):
            notes.append("master capped at %dpx" % MAX_WIDTH)
        made = make_avifs(src)
        if made:
            notes.append("avif: " + ", ".join(made))
        removed = remove_old_variants(src)
        if removed:
            notes.append("removed: " + ", ".join(removed))
        if notes:
            print("%s — %s" % (src.name, "; ".join(notes)))
    manifest = json.loads((ROOT / "src/manifest.json").read_text(encoding="utf-8"))
    base = manifest["site"]["base_url"]
    for a in manifest["articles"]:
        og_path = ROOT / a["og_image"][len(base):].lstrip("/")
        if og_path.exists() and not force_og:
            continue
        make_og(ROOT / a["image"].lstrip("/"), og_path)
        print("social card:", og_path.name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
