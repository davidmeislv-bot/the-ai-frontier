#!/usr/bin/env python3
"""Generate responsive image variants and social cards for Wire Willow.

Run this once whenever you add or replace an image in images/:

    pip install Pillow
    python3 tools/make_images.py

For every source image  images/<name>.webp  it writes smaller copies
images/<name>-640.webp, -1024.webp and -1600.webp (only widths smaller
than the source). build.py finds these automatically and emits srcset.

It also (re)creates the 1200x630 social card for any article whose
og_image file is missing, cropped from the article's lead image.
Existing social cards are left alone, so hand-made cards are never
overwritten. Pass --force-og to regenerate them all.

Pillow is only needed here; build.py itself stays dependency-free.
"""

import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "images"
WIDTHS = (640, 1024, 1600)
VARIANT_SUFFIXES = tuple("-%d" % w for w in WIDTHS)
OG_SIZE = (1200, 630)


def is_variant(path):
    return path.stem.endswith(VARIANT_SUFFIXES)


def make_variants(src):
    made = []
    with Image.open(src) as im:
        im.load()
        for w in WIDTHS:
            if w >= im.width:
                continue
            dest = src.with_name("%s-%d.webp" % (src.stem, w))
            h = round(im.height * w / im.width)
            im.resize((w, h), Image.LANCZOS).save(dest, "WEBP", quality=80, method=6)
            made.append(dest.name)
    return made


def make_og(src, dest):
    """Center-crop the source to 1.905:1 and resize to 1200x630 JPEG."""
    with Image.open(src) as im:
        im = im.convert("RGB")
        target = OG_SIZE[0] / OG_SIZE[1]
        if im.width / im.height > target:
            new_w = round(im.height * target)
            left = (im.width - new_w) // 2
            im = im.crop((left, 0, left + new_w, im.height))
        else:
            new_h = round(im.width / target)
            top = (im.height - new_h) // 2
            im = im.crop((0, top, im.width, top + new_h))
        im.resize(OG_SIZE, Image.LANCZOS).save(dest, "JPEG", quality=84, optimize=True, progressive=True)


def main(argv):
    force_og = "--force-og" in argv
    for src in sorted(IMAGES.glob("*.webp")):
        if is_variant(src):
            continue
        made = make_variants(src)
        if made:
            print("variants:", ", ".join(made))
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
