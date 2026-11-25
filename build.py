#!/usr/bin/env python3
"""Build the Wire Willow static site.

Single source of truth: src/manifest.json (articles, editions, briefs,
page metadata). Templates live in src/templates/, article bodies in
src/content/. The shared stylesheet is assets/css/style.css and the
shared script is assets/js/main.js; both are linked, never inlined.

Everything that can be worked out is worked out here, never typed into
the manifest: edition numbers and labels, every formatted date, canonical
URLs, share images for edition pages, the sitemap, feed dates and the
footer desk list. The build refuses a manifest that still types them, so
a date can only ever be entered once.

Output goes to _site/ (git-ignored), together with the static files
(assets/, images/, favicons, robots.txt, CNAME). GitHub Actions runs this
script and publishes _site/, so only the finished site is public; source
files, notes and the manifest never are.

Usage:
  python3 build.py            build + validate + write _site/
  python3 build.py --check    build + validate, write nothing

Validation fails loudly (exit 1, errors listed) and nothing is written
when any check fails. Among the checks: no date in the future and every
weekday right; edition URLs follow /YYYY/MM/DD/ or /YYYY/MM/DD-<part>/;
every internal link and image resolves; one H1 per page and no skipped
heading levels; no inline styles; image files within budget with all
AVIF sizes present; social images 1200x630 and unique; the homepage
shows the newest edition; the sitemap and feed list exactly the pages.

Images: run tools/make_images.py (needs Pillow) after adding an image.
"""

import datetime as dt
import email.utils
import hashlib
import html as htmlmod
import json
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
TEMPLATES = SRC / "templates"
OUT = ROOT / "_site"

# Copied as-is into _site/.
STATIC_PATHS = ("assets", "images", "favicon.ico", "favicon.png",
                "apple-touch-icon.png", "robots.txt", "CNAME")

# Generated files that must never be committed at the repo root again.
GENERATED_AT_ROOT = ("index.html", "404.html", "sitemap.xml", "feed.xml",
                     "about", "archive", "2026", "2027")

NEWSROOM_TZ = ZoneInfo("America/Los_Angeles")

# Desks in display order: filter value -> label.
DESKS = [("ai", "AI"), ("robotics", "Robotics"), ("drones", "Drones"),
         ("fintech", "Fintech"), ("gossip", "The Gossip"),
         ("finance", "Tech Finance"), ("industry", "Tech Industry")]
DESK_LABELS = dict(DESKS)

# Same-day editions after the first get one of these URL suffixes.
EDITION_PARTS = ("morning", "midday", "afternoon", "evening", "night")
EDITION_URL_RE = re.compile(r"^/(\d{4})/(\d{2})/(\d{2})(?:-(%s))?/$" % "|".join(EDITION_PARTS))

# Fields the build derives. Typing them into the manifest is an error.
DERIVED_EDITION_FIELDS = ("label", "number", "date_long", "date_short", "date_upper",
                          "breadcrumb_name", "ticker_label", "ticker_aria",
                          "archive_eyebrow", "footer_edition", "lastmod")
DERIVED_EDITION_HEAD_FIELDS = ("canonical", "og_type", "og_image", "og_image_alt")
DERIVED_ARTICLE_FIELDS = ("date_short", "canonical", "feed_pubdate", "read_short",
                          "section", "cat_label", "og_image_alt", "og_type")

# Image budgets (bytes). tools/make_images.py stays well inside these.
MASTER_MAX_WIDTH = 1600
MASTER_MAX_BYTES = 700_000
AVIF_WIDTHS = (640, 800, 1024, 1600)
OG_MAX_BYTES = 300_000

RAW_AMPERSAND_RE = re.compile(r"&(?!(?:[a-zA-Z][a-zA-Z0-9]*|#\d+|#x[0-9a-fA-F]+);)")

ERRORS = []


def fail(msg):
    ERRORS.append(msg)


def esc(text):
    """Escape text for an HTML text node or double-quoted attribute."""
    return htmlmod.escape(str(text), quote=True)


def today_in_newsroom():
    return dt.datetime.now(NEWSROOM_TZ).date()


# ---------------------------------------------------------------- dates

def fmt_long(d):
    return "%s, %s %d, %d" % (d.strftime("%A"), d.strftime("%B"), d.day, d.year)


def fmt_short(d):
    return "%s %d, %d" % (d.strftime("%b"), d.day, d.year)


def parse_date(value, where):
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        fail(f"{where}: {value!r} is not a YYYY-MM-DD date")
        return None


def parse_datetime(value, where):
    try:
        stamp = dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        fail(f"{where}: {value!r} is not an ISO 8601 date-time")
        return None
    if stamp.tzinfo is None:
        fail(f"{where}: {value!r} needs a UTC offset, e.g. 2026-11-24T09:00:00-08:00")
        return None
    return stamp


# ---------------------------------------------------------------- templates

def load_template(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


def render(template_text, tokens):
    out = template_text
    for key, value in tokens.items():
        out = out.replace("{{" + key + "}}", value)
    return out


# ---------------------------------------------------------------- manifest

def load_manifest():
    with open(SRC / "manifest.json", encoding="utf-8") as f:
        return json.load(f)


MONTH_DAY_RE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.? (\d{1,2}), (\d{4})\b")


def dates_in(text):
    """Every 'Nov 24, 2026' / 'November 24, 2026' style date in a string."""
    found = []
    for mon, day, year in MONTH_DAY_RE.findall(text or ""):
        try:
            found.append(dt.datetime.strptime("%s %s %s" % (mon[:3], day, year), "%b %d %Y").date())
        except ValueError:
            pass
    return found


def validate_manifest(m):
    """Checks on the hand-written manifest, before anything is derived."""
    site = m["site"]
    today = today_in_newsroom()
    for key in ("base_url", "logo_url", "org_description", "founding_date",
                "default_og_image", "default_og_image_alt", "footer_note"):
        if not site.get(key):
            fail(f"manifest: site.{key} is missing")
    if "sitemap" in m:
        fail("manifest: remove the 'sitemap' list — build.py now generates the sitemap")
    if "lastmod" in m.get("homepage", {}):
        fail("manifest: remove homepage.lastmod — build.py works it out")

    articles = {a["slug"]: a for a in m["articles"]}
    editions = {e["id"]: e for e in m["editions"]}
    seen_urls, seen_ids = set(), set()

    for a in m["articles"]:
        where = f"manifest: article {a.get('slug')}"
        for field in ("id", "slug", "edition", "url", "title", "dek", "category", "date",
                      "published", "modified", "read_time", "image", "image_alt",
                      "og_image", "caption", "body_file", "head_title"):
            if a.get(field) in (None, ""):
                fail(f"{where} is missing {field}")
        for field in DERIVED_ARTICLE_FIELDS:
            if field in a:
                fail(f"{where}: remove '{field}' — build.py derives it")
        if a["slug"] in seen_ids:
            fail(f"manifest: duplicate article slug {a['slug']}")
        seen_ids.add(a["slug"])
        if a["url"] in seen_urls:
            fail(f"manifest: duplicate article url {a['url']}")
        seen_urls.add(a["url"])
        if a.get("category") not in DESK_LABELS:
            fail(f"{where} has unknown category {a.get('category')!r}")
        if a.get("edition") not in editions:
            fail(f"{where} belongs to unknown edition {a.get('edition')!r}")
        body_path = SRC / a["body_file"]
        if not body_path.is_file():
            fail(f"{where} body file missing: {a['body_file']}")
        elif RAW_AMPERSAND_RE.search(body_path.read_text(encoding="utf-8")):
            fail(f"{a['body_file']}: write a bare & as &amp; (e.g. S&amp;P 500)")
        for label, url in a.get("sources", []):
            if not label or not url.startswith("http"):
                fail(f"{where} has a bad source entry: {label!r} {url!r}")
        # dates: real, not in the future, consistent with each other and the URL
        day = parse_date(a.get("date"), where)
        published = parse_datetime(a.get("published"), where + " published")
        modified = parse_datetime(a.get("modified"), where + " modified")
        if day:
            if day > today:
                fail(f"{where}: date {day} is in the future (today is {today})")
            if not a["url"].startswith(day.strftime("/%Y/%m/%d/")):
                fail(f"{where}: url {a['url']} does not start with its date {day:/%Y/%m/%d/}")
        if published and day:
            pub_day = published.astimezone(NEWSROOM_TZ).date()
            if pub_day < day:
                fail(f"{where}: published {pub_day} is before its date {day}")
            if pub_day > today:
                fail(f"{where}: published {pub_day} is in the future")
        if published and modified and modified < published:
            fail(f"{where}: modified is earlier than published")
        # images and credits
        credit = a.get("credit")
        if credit:
            for field in ("kind", "text", "license", "license_url"):
                if not credit.get(field):
                    fail(f"{where}: credit is missing {field}")
            if credit.get("kind") not in ("photo", "illustration"):
                fail(f"{where}: credit.kind must be 'photo' or 'illustration'")
        elif not a.get("caption", "").startswith("Illustration: Wire Willow."):
            fail(f"{where}: an image not made by Wire Willow needs a 'credit' (author, license, license_url)")

    seen_seq, seen_edition_urls = {}, set()
    for e in m["editions"]:
        where = f"manifest: edition {e.get('id')}"
        for field in ("id", "seq", "kind", "date", "url", "head", "briefs"):
            if e.get(field) in (None, ""):
                fail(f"{where} is missing {field}")
        for field in DERIVED_EDITION_FIELDS:
            if field in e:
                fail(f"{where}: remove '{field}' — build.py derives it")
        for field in DERIVED_EDITION_HEAD_FIELDS:
            if field in e.get("head", {}):
                fail(f"{where}: remove 'head.{field}' — build.py derives it")
        if not isinstance(e.get("seq"), int):
            fail(f"{where} is missing an integer seq")
            continue
        # Editions are ordered by seq (the edition number), never by date:
        # several editions can share a date.
        if e["seq"] in seen_seq:
            fail(f"manifest: editions {seen_seq[e['seq']]} and {e['id']} share seq {e['seq']}")
        seen_seq[e["seq"]] = e["id"]
        if e["url"] in seen_edition_urls:
            fail(f"{where}: url {e['url']} is used by another edition")
        seen_edition_urls.add(e["url"])
        day = parse_date(e.get("date"), where)
        if day:
            if day > today:
                fail(f"{where}: date {day} is in the future (today is {today})")
            match = EDITION_URL_RE.match(e["url"])
            if not match:
                fail(f"{where}: url {e['url']} must look like /YYYY/MM/DD/ or "
                     f"/YYYY/MM/DD-<{'|'.join(EDITION_PARTS)}>/")
            elif dt.date(*map(int, match.groups()[:3])) != day:
                fail(f"{where}: url {e['url']} does not match its date {day}")
            for text_field in ("title", "description"):
                for mentioned in dates_in(e.get("head", {}).get(text_field)):
                    if mentioned != day:
                        fail(f"{where}: head.{text_field} says {fmt_short(mentioned)}, "
                             f"but the edition is dated {fmt_short(day)}")
        if e.get("briefs") not in m["brief_sets"]:
            fail(f"{where} references unknown brief set {e.get('briefs')}")
        for slug in e.get("article_slugs", []):
            if slug not in articles:
                fail(f"{where} references unknown article {slug}")
            elif articles[slug]["edition"] != e["id"]:
                fail(f"{where} lists {slug}, but that article says it belongs to "
                     f"{articles[slug]['edition']}")
            elif day and parse_date(articles[slug]["date"], "") and \
                    dt.date.fromisoformat(articles[slug]["date"]) > day:
                fail(f"{where} is dated {day} but contains {slug}, dated {articles[slug]['date']}")
        if e.get("lead_slug") and e["lead_slug"] not in articles:
            fail(f"{where} references unknown lead {e['lead_slug']}")

    for name, bset in m["brief_sets"].items():
        for b in bset["briefs"]:
            if not b.get("title") or not b.get("body") or not b.get("sources"):
                fail(f"manifest: brief set {name} has an incomplete brief: {b.get('title')!r}")

    hp = m["homepage"]
    if hp["edition"] not in editions:
        fail("manifest: homepage references an unknown edition")
    if hp["briefs"] not in m["brief_sets"]:
        fail("manifest: homepage references an unknown brief set")
    full = [e for e in m["editions"] if e.get("kind") == "edition" and isinstance(e.get("seq"), int)]
    if full:
        newest = max(full, key=lambda e: e["seq"])
        if hp["edition"] != newest["id"]:
            fail(f"manifest: homepage shows edition {hp['edition']}, "
                 f"but the newest edition is {newest['id']}")
        if hp["briefs"] != newest["briefs"]:
            fail(f"manifest: homepage shows brief set {hp['briefs']}, "
                 f"but the newest edition uses {newest['briefs']}")

    page_urls = {e["url"] for e in m["editions"]} | {a["url"] for a in m["articles"]} | \
        {p["url"] for p in m["pages"].values()} | {"/"}
    for r in m.get("redirects", []):
        if r.get("from") in page_urls:
            fail(f"manifest: redirect from {r.get('from')} would overwrite a real page")
        if r.get("to") not in page_urls:
            fail(f"manifest: redirect to {r.get('to')} points at no page")
        if not str(r.get("from", "")).endswith("/"):
            fail(f"manifest: redirect from {r.get('from')!r} must end with /")

    validate_images(m)
    newsletter = site.get("newsletter", {})
    if not newsletter.get("action") and "@" not in newsletter.get("fallback_email", ""):
        fail("manifest: site.newsletter needs an action URL or a fallback_email")
    for name in GENERATED_AT_ROOT:
        if (ROOT / name).exists():
            fail(f"{name} at the repo root is generated output — delete it; the build writes to _site/")


def validate_images(m):
    site = m["site"]
    og_hashes = {}
    for url in [site.get("default_og_image", "")] + [a["og_image"] for a in m["articles"]]:
        path = ROOT / url[len(site["base_url"]):].lstrip("/")
        if not path.is_file():
            fail(f"manifest: social image missing: {url}")
            continue
        if image_size(path) != (1200, 630):
            fail(f"manifest: social image is {image_size(path)}, expected 1200x630: {url}")
        if path.stat().st_size > OG_MAX_BYTES:
            fail(f"manifest: social image is {path.stat().st_size // 1024} KB, budget "
                 f"{OG_MAX_BYTES // 1024} KB: {url}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in og_hashes and og_hashes[digest] != url:
            fail(f"manifest: social image {url} is a byte-for-byte copy of {og_hashes[digest]}")
        og_hashes.setdefault(digest, url)
    checked = set()
    for a in m["articles"]:
        if a["image"] in checked:
            continue
        checked.add(a["image"])
        path = ROOT / a["image"].lstrip("/")
        if not path.is_file():
            fail(f"manifest: article {a['slug']} image missing: {a['image']}")
            continue
        width, _ = image_size(path)
        if width > MASTER_MAX_WIDTH or path.stat().st_size > MASTER_MAX_BYTES:
            fail(f"{a['image']}: {width}px / {path.stat().st_size // 1024} KB is over budget "
                 f"({MASTER_MAX_WIDTH}px, {MASTER_MAX_BYTES // 1024} KB) — run tools/make_images.py")
        for w in avif_widths(width):
            if not (ROOT / avif_url(a["image"], w).lstrip("/")).is_file():
                fail(f"{a['image']}: missing {avif_url(a['image'], w)} — run tools/make_images.py")


# ---------------------------------------------------------------- derived fields

def derive(m):
    """Fill in every field the manifest no longer types by hand."""
    site = m["site"]
    base = site["base_url"]
    articles = {a["slug"]: a for a in m["articles"]}
    for a in m["articles"]:
        day = dt.date.fromisoformat(a["date"])
        published = dt.datetime.fromisoformat(a["published"])
        a["date_short"] = fmt_short(day)
        a["published_short"] = fmt_short(published.astimezone(NEWSROOM_TZ).date())
        a["canonical"] = base + a["url"]
        a["feed_pubdate"] = email.utils.format_datetime(published)
        a["read_short"] = re.sub(r"\s*read$", "", a["read_time"], flags=re.I).upper()
        a["cat_label"] = DESK_LABELS[a["category"]]
        a["section"] = a["cat_label"]
        a["og_image_alt"] = a["image_alt"]
        a["og_type"] = "article"
        a.setdefault("head_description", a["dek"])
    for e in m["editions"]:
        day = dt.date.fromisoformat(e["date"])
        e["label"] = "Edition No. %03d" % e["seq"]
        e["number"] = "No. %03d" % e["seq"]
        e["date_long"] = fmt_long(day)
        e["date_short"] = fmt_short(day)
        e["date_upper"] = e["date_short"].upper()
        e["breadcrumb_name"] = "%s — %s" % (e["label"], e["date_short"])
        e["ticker_label"] = "%s %d Headlines" % (day.strftime("%b"), day.day)
        e["ticker_aria"] = "Top headlines, %s" % fmt_long(day).replace(",", "", 1)
        e["archive_eyebrow"] = "%s · %s" % (e["date_long"], e["label"])
        e["footer_edition"] = "%s — %s. %s" % (e["label"], e["date_long"], site["footer_note"])
        modified = [dt.datetime.fromisoformat(articles[s]["modified"]).astimezone(NEWSROOM_TZ).date()
                    for s in e.get("article_slugs", [])]
        e["lastmod"] = max([day] + modified).isoformat()
        lead = articles.get(e.get("lead_slug"))
        e["head"]["canonical"] = base + e["url"]
        e["head"]["og_type"] = "website"
        e["head"]["og_image"] = lead["og_image"] if lead else site["default_og_image"]
        e["head"]["og_image_alt"] = lead["image_alt"] if lead else site["default_og_image_alt"]
    m["homepage"]["lastmod"] = max(e["lastmod"] for e in m["editions"])
    m["desks_in_use"] = [label for value, label in DESKS
                         if any(a["category"] == value for a in m["articles"])]


# ---------------------------------------------------------------- JSON-LD

def org_block(site):
    return {
        "@context": "https://schema.org",
        "@type": "NewsMediaOrganization",
        "@id": site["base_url"] + "/#organization",
        "name": site["name"],
        "url": site["base_url"] + "/",
        "logo": site["logo_url"],
        "description": site["org_description"],
        "foundingDate": site["founding_date"],
        "areaServed": site["area_served"],
    }


def website_block(site):
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "@id": site["base_url"] + "/#website",
        "name": site["name"],
        "url": site["base_url"] + "/",
        "inLanguage": "en-US",
        "publisher": {"@id": site["base_url"] + "/#organization"},
    }


def json_ld_script(obj):
    return '<script type="application/ld+json">\n' + json.dumps(obj, indent=2, ensure_ascii=False) + "\n</script>\n"


def breadcrumb_block(crumbs):
    items = []
    for i, (name, url) in enumerate(crumbs, start=1):
        item = {"@type": "ListItem", "position": i, "name": name}
        if url:
            item["item"] = url
        items.append(item)
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": items}


def image_object(site, article):
    width, height = image_size(ROOT / article["image"].lstrip("/"))
    obj = {
        "@type": "ImageObject",
        "url": site["base_url"] + article["image"],
        "width": width,
        "height": height,
        "caption": article["image_alt"],
    }
    credit = article.get("credit")
    if credit:
        obj["creditText"] = credit["text"]
        obj["license"] = credit["license_url"]
        if credit.get("source_url"):
            obj["acquireLicensePage"] = credit["source_url"]
    else:
        obj["creditText"] = site["name"]
        obj["copyrightNotice"] = "© %s" % site["name"]
    return obj


def news_article_block(site, article):
    org_id = site["base_url"] + "/#organization"
    return {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": article["title"],
        "description": article["head_description"],
        "image": [image_object(site, article), article["og_image"]],
        "datePublished": article["published"],
        "dateModified": article["modified"],
        "author": {"@type": "Organization", "@id": org_id, "name": site["name"]},
        "publisher": {
            "@type": "Organization",
            "@id": org_id,
            "name": site["name"],
            "logo": {"@type": "ImageObject", "url": site["logo_url"]},
        },
        "mainEntityOfPage": article["canonical"],
        "articleSection": article["section"],
        "isAccessibleForFree": True,
        "inLanguage": "en-US",
    }


def edition_block(site, edition, items):
    return {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": "%s — %s" % (edition["label"], edition["date_long"]),
        "url": edition["head"]["canonical"],
        "datePublished": edition["date"],
        "isPartOf": {"@id": site["base_url"] + "/#website"},
        "mainEntity": {
            "@type": "ItemList",
            "itemListElement": [
                {"@type": "ListItem", "position": i, "url": a["canonical"], "name": a["title"]}
                for i, a in enumerate(items, start=1)
            ],
        },
    }


# ---------------------------------------------------------------- images

def image_size(path):
    """Return (width, height) of a WebP, PNG or JPEG file, stdlib only."""
    data = Path(path).read_bytes()
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8X":
            w = int.from_bytes(data[24:27], "little") + 1
            h = int.from_bytes(data[27:30], "little") + 1
            return w, h
        if chunk == b"VP8 ":
            return (int.from_bytes(data[26:28], "little") & 0x3FFF,
                    int.from_bytes(data[28:30], "little") & 0x3FFF)
        if chunk == b"VP8L":
            b = data[21:25]
            w = 1 + (((b[1] & 0x3F) << 8) | b[0])
            h = 1 + (((b[3] & 0x0F) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
            return w, h
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
            i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    raise ValueError(f"unsupported or corrupt image: {path}")


def avif_widths(master_width):
    return [w for w in AVIF_WIDTHS if w <= master_width] or [master_width]


def avif_url(src, width):
    stem = src.rpartition(".")[0]
    return "%s-%d.avif" % (stem, width)


def responsive_img(src, alt, sizes, css_class="", lazy=False, priority=False):
    """A <picture>: AVIF at several widths for modern browsers, the WebP
    master as the fallback, with real dimensions so nothing shifts."""
    width, height = image_size(ROOT / src.lstrip("/"))
    srcset = ", ".join("%s %dw" % (avif_url(src, w), w) for w in avif_widths(width))
    attrs = []
    if css_class:
        attrs.append('class="%s"' % css_class)
    attrs.append('src="%s"' % esc(src))
    attrs.append('alt="%s"' % esc(alt))
    attrs.append('width="%d" height="%d"' % (width, height))
    if lazy:
        attrs.append('loading="lazy"')
    if priority:
        attrs.append('fetchpriority="high"')
    else:
        attrs.append('decoding="async"')
    return ('<picture><source type="image/avif" srcset="%s" sizes="%s" /><img %s /></picture>'
            % (esc(srcset), esc(sizes), " ".join(attrs)))


SIZES_HERO = "(min-width: 1240px) 1200px, calc(100vw - 40px)"
SIZES_CARD = "(min-width: 1240px) 380px, (min-width: 900px) 30vw, (min-width: 560px) 45vw, calc(100vw - 40px)"
SIZES_STORY = "(min-width: 820px) 780px, calc(100vw - 40px)"


def render_caption(article):
    """Caption sentence plus, for outside images, a linked credit line that
    meets the license terms (author, source, license link, changes)."""
    caption = esc(article["caption"])
    credit = article.get("credit")
    if not credit:
        return caption
    who = esc(credit["text"])
    if credit.get("source_url"):
        who = '<a href="%s" rel="noopener">%s</a>' % (esc(credit["source_url"]), who)
    label = credit.get("label") or ("Photo" if credit["kind"] == "photo" else "Image")
    line = '%s: %s, <a href="%s" rel="license noopener">%s</a>' % (
        esc(label), who,
        esc(credit["license_url"]), esc(credit["license"]))
    if credit.get("changes"):
        line += ", " + esc(credit["changes"])
    return "%s %s." % (caption, line)


# ---------------------------------------------------------------- fragments

def search_text(title, dek, cat_label):
    """The text a front-page search matches against (cards and lead)."""
    return (title + " " + dek + " " + cat_label).lower()


def render_card(article, edition, extra_class=""):
    thumb = responsive_img(article["image"], article["image_alt"], SIZES_CARD,
                           css_class="card-thumb", lazy=True)
    return (
        '<article class="card%s" data-cat="%s" data-search="%s">'
        "%s"
        '<div class="card-body"><p class="eyebrow accent">%s</p>'
        '<h3 class="card-title"><a class="card-link" href="%s">%s</a></h3>'
        '<p class="card-dek">%s</p>'
        '<p class="card-meta"><span>%s · %s</span>'
        '<span class="card-cta" aria-hidden="true">Read →</span></p></div></article>'
        % (
            extra_class,
            esc(article["category"]),
            esc(search_text(article["title"], article["dek"], article["cat_label"])),
            thumb,
            esc(article["cat_label"]),
            esc(article["url"]),
            esc(article["title"]),
            esc(article["dek"]),
            esc(edition["date_upper"]),
            esc(article["read_short"]),
        )
    )


def last_card_class(index, total):
    """Mark a card that would sit alone on the grid's last row, so CSS can
    widen it: .is-last-odd for the 2-column grid, .is-last-third for 3."""
    if index != total - 1:
        return ""
    classes = []
    if total % 2 == 1 and total > 1:
        classes.append("is-last-odd")
    if total % 3 == 1 and total > 1:
        classes.append("is-last-third")
    return "".join(" " + c for c in classes)


def render_filter_buttons(categories):
    """'Front Page' plus one button per desk that has a story today, so no
    filter ever leads to an empty page."""
    buttons = [("all", "Front Page")] + [d for d in DESKS if d[0] in categories]
    return "".join(
        '      <button class="fbtn" type="button" data-filter="%s" aria-pressed="%s" '
        'aria-controls="cardGrid">%s</button>\n'
        % (esc(value), "true" if value == "all" else "false", esc(label))
        for value, label in buttons)


def render_ticker(ticker):
    """Ticker items as a list, plus an aria-hidden copy so the CSS loop is
    seamless without screen readers hearing every headline twice."""
    parts = []
    for label, text, extra, cls in ticker:
        s = "<li><b>%s</b> %s" % (esc(label), esc(text))
        if extra:
            s += ' <span class="%s">%s</span>' % (esc(cls), esc(extra))
        s += "</li>"
        parts.append(s)
    once = "".join(parts)
    return ('        <ul class="ticker-set">%s</ul>\n'
            '        <ul class="ticker-set" aria-hidden="true">%s</ul>\n' % (once, once))


def render_briefs(briefs, with_eyebrows, separator, level=3):
    out = []
    for b in briefs:
        links = separator.join(
            '<a href="%s" target="_blank" rel="noopener">%s</a>' % (esc(url), esc(label))
            for label, url in b["sources"]
        )
        word = "Source" if len(b["sources"]) == 1 else "Sources"
        out.append('      <article class="brief">')
        if with_eyebrows and b.get("eyebrow"):
            out.append('        <p class="eyebrow accent">%s</p>' % esc(b["eyebrow"]))
        out.append("        <h%d>%s</h%d>" % (level, esc(b["title"]), level))
        out.append("        <p>%s</p>" % esc(b["body"]))
        out.append('        <p class="src">%s: %s</p>' % (word, links))
        out.append("      </article>")
    return "\n".join(out) + "\n" if out else ""


def render_factbox(factbox, indent="    "):
    if not factbox:
        return ""
    lines = [indent + '<aside class="factbox">',
             indent + "  <h2>%s</h2>" % esc(factbox["heading"]),
             indent + "  <ul>"]
    for strong, rest in factbox["items"]:
        lines.append(indent + "    <li><strong>%s</strong>%s</li>" % (esc(strong), esc(rest)))
    lines.append(indent + "  </ul>")
    lines.append(indent + "</aside>")
    return "\n".join(lines) + "\n"


def indent_block(text, spaces):
    pad = " " * spaces
    return "".join(pad + line + "\n" for line in text.strip("\n").split("\n"))


def asset_href(rel):
    """Site path with a short content hash, so browsers re-fetch an asset
    exactly when it changes (GitHub Pages can't set long cache headers)."""
    digest = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()[:10]
    return "/%s?v=%s" % (rel, digest)


# ---------------------------------------------------------------- pages

class Builder:
    def __init__(self, manifest):
        self.m = manifest
        self.site = manifest["site"]
        self.articles = {a["slug"]: a for a in manifest["articles"]}
        self.editions = {e["id"]: e for e in manifest["editions"]}
        self.base = load_template("base.html")
        self.header_filter = load_template("partials/header_filter.html").rstrip("\n")
        self.header_nav = load_template("partials/header_nav.html").rstrip("\n")
        self.footer_tpl = load_template("partials/footer.html").rstrip("\n")
        self.subscribe = self.render_subscribe()
        self.org_json = json.dumps(org_block(self.site), indent=2, ensure_ascii=False)
        self.css_href = asset_href("assets/css/style.css")
        self.js_href = asset_href("assets/js/main.js")

    def render_subscribe(self):
        """Newsletter form. With site.newsletter.action set (e.g. a Substack,
        Buttondown or Beehiiv form URL) the form posts there and works
        without JS. Otherwise main.js opens a pre-filled signup email."""
        cfg = self.site.get("newsletter", {})
        if cfg.get("action"):
            attrs = ' action="%s" method="post" target="_blank"' % esc(cfg["action"])
            note = cfg.get("note", "You’ll get one confirmation email. Unsubscribe any time.")
        else:
            user, _, domain = cfg.get("fallback_email", "").partition("@")
            attrs = ' data-mailto-user="%s" data-mailto-domain="%s"' % (esc(user), esc(domain))
            note = "Opens your email app with a ready-to-send signup note — you stay in control of sending it."
        return render(load_template("partials/subscribe.html").rstrip("\n"),
                      {"form_attrs": attrs, "subscribe_note": esc(note)})

    def footer(self, edition_line, fine_line):
        return render(self.footer_tpl, {
            "footer_intro": esc(self.site["footer_intro"]),
            "footer_edition": esc(edition_line),
            "footer_fine": esc(fine_line),
            "footer_desks": " · ".join(esc(d) for d in self.m["desks_in_use"]),
        })

    def default_footer(self):
        """Site-wide footer, always taken from the edition the homepage shows,
        so it can never drift out of date."""
        edition = self.editions[self.m["homepage"]["edition"]]
        return self.footer(edition["footer_edition"], edition["footer_fine"])

    def page(self, head, header, body, footer, scripts=True, article_meta="", extra_jsonld="",
             noindex=False):
        tokens = {
            "head_title": esc(head["title"]),
            "head_description": esc(head["description"]),
            "canonical": esc(head["canonical"]),
            "og_type": esc(head["og_type"]),
            "og_image": esc(head["og_image"]),
            "og_image_alt": esc(head["og_image_alt"]),
            "article_meta": article_meta,
            "org_json": self.org_json,
            "extra_jsonld": extra_jsonld,
            "header": header,
            "body": body,
            "footer": footer,
            "css_href": self.css_href,
            "script_tag": ('<script type="module" src="%s"></script>\n' % self.js_href) if scripts else "",
            "robots_meta": '<meta name="robots" content="noindex" />\n' if noindex else "",
        }
        return render(self.base, tokens)

    def article_meta_tags(self, article):
        return (
            '<meta property="article:published_time" content="%s" />\n'
            '<meta property="article:modified_time" content="%s" />\n'
            '<meta property="article:section" content="%s" />\n'
            % (esc(article["published"]), esc(article["modified"]), esc(article["section"]))
        )

    def story_jsonld(self, article, edition):
        crumbs = [
            ("Home", self.site["base_url"] + "/"),
            (edition["breadcrumb_name"], self.site["base_url"] + edition["url"]),
            (article["title"], None),
        ]
        return json_ld_script(news_article_block(self.site, article)) + \
            json_ld_script(breadcrumb_block(crumbs))

    def edition_jsonld(self, edition):
        crumbs = [
            ("Home", self.site["base_url"] + "/"),
            ("Archive", self.site["base_url"] + "/archive/"),
            (edition["breadcrumb_name"], None),
        ]
        items = [self.articles[s] for s in edition.get("article_slugs", [])]
        return json_ld_script(edition_block(self.site, edition, items)) + \
            json_ld_script(breadcrumb_block(crumbs))

    # ---- front page (homepage + frozen edition pages) ----

    def front_page(self, edition, briefs_key, briefs_label, briefs_links, crumbs_html):
        briefs = self.m["brief_sets"][briefs_key]["briefs"]
        lead = self.articles[edition["lead_slug"]]
        cards = [self.articles[s] for s in edition["article_slugs"] if s != edition["lead_slug"]]
        card_html = "\n".join("      " + render_card(a, edition, last_card_class(i, len(cards)))
                              for i, a in enumerate(cards)) + "\n"
        count = len(cards) + 1
        header = render(self.header_filter, {
            "filter_buttons": render_filter_buttons(
                {lead["category"]} | {a["category"] for a in cards}),
        })
        panel = render(load_template("partials/panel.html").rstrip("\n"), {
            "watchbox": "".join("          <li>%s</li>\n" % esc(w) for w in edition["watchbox"]),
        })
        if briefs_links:
            links = self.m["homepage"]["briefs_link_lines"]
            links_html = "      " + '<p class="briefs-links">' + " &middot; ".join(
                '<a href="%s">%s</a>' % (esc(link["url"]), esc(link["text"])) for link in links
            ) + "</p>\n"
        else:
            links_html = ""
        body = render(load_template("front.html").rstrip("\n"), {
            "ticker_aria": esc(edition["ticker_aria"]),
            "ticker_label": esc(edition["ticker_label"]),
            "ticker_items": render_ticker(edition["ticker"]),
            "crumbs": crumbs_html,
            "date_iso": esc(edition["date"]),
            "date_long": esc(edition["date_long"]),
            "edition_number": esc(edition["number"]),
            "lead_search": esc(search_text(lead["title"], lead["dek"], lead["cat_label"])),
            "lead_cat": esc(lead["category"]),
            "lead_image_tag": responsive_img(lead["image"], lead["image_alt"], SIZES_HERO, priority=True),
            "lead_cat_label": esc(lead["cat_label"]),
            "lead_title": esc(lead["title"]),
            "lead_dek": esc(lead["dek"]),
            "date_short": esc(edition["date_short"]),
            "lead_read_time": esc(lead["read_time"]),
            "lead_url": esc(lead["url"]),
            "lead_caption": render_caption(lead),
            "lead_factbox": render_factbox(lead.get("factbox")),
            "count_label": "%d %s · Updated %s" % (
                count, "story" if count == 1 else "stories", esc(edition["date_short"])),
            "cards": card_html,
            "band_stats": "".join(
                '      <div class="stat"><p class="stat-big">%s</p><p class="stat-label">%s</p></div>\n'
                % (esc(big), esc(lbl)) for big, lbl in edition["band_stats"]),
            "briefs_label": esc(briefs_label),
            "briefs": render_briefs(briefs, with_eyebrows=False, separator=" · "),
            "briefs_links": links_html,
            "panel": panel,
            "subscribe": self.subscribe,
        })
        return header, body

    def build_homepage(self):
        cfg = self.m["homepage"]
        edition = self.editions[cfg["edition"]]
        header, body = self.front_page(edition, cfg["briefs"], cfg["briefs_label"],
                                       cfg["briefs_links"], "")
        return self.page(cfg["head"], header, body, self.default_footer(),
                         extra_jsonld=json_ld_script(website_block(self.site)))

    def build_edition_page(self, edition):
        crumbs = '  <p class="crumbs"><a href="/">Home</a> &middot; <a href="/archive/">Archive</a> &middot; %s</p>\n' \
            % esc(edition["label"])
        header, body = self.front_page(edition, edition["briefs"], edition["briefs_label"],
                                       edition["briefs_links"], crumbs)
        return self.page(edition["head"], header, body,
                         self.footer(edition["footer_edition"], edition["footer_fine"]),
                         extra_jsonld=self.edition_jsonld(edition))

    # ---- story pages ----

    def build_story(self, article):
        edition = self.editions[article["edition"]]
        siblings = [self.articles[s] for s in edition["article_slugs"]]
        idx = [a["slug"] for a in siblings].index(article["slug"])
        prev_a = siblings[idx - 1] if idx > 0 else None
        next_a = siblings[idx + 1] if idx + 1 < len(siblings) else None
        body_text = (SRC / article["body_file"]).read_text(encoding="utf-8")
        sources = "".join(
            '        <li><a href="%s" target="_blank" rel="noopener">%s</a></li>\n'
            % (esc(url), esc(label)) for label, url in article["sources"])
        related = "".join(
            '        <li><a href="%s">%s</a></li>\n' % (esc(a["url"]), esc(a["title"]))
            for a in siblings if a["slug"] != article["slug"])
        related += '        <li><a href="%s">Full %s — %s</a></li>\n' % (
            esc(edition["url"]), esc(edition["label"]), esc(edition["date_long"]))
        head = {
            "title": article["head_title"],
            "description": article["head_description"],
            "canonical": article["canonical"],
            "og_type": article["og_type"],
            "og_image": article["og_image"],
            "og_image_alt": article["og_image_alt"],
        }
        published_note = ""
        if article["published_short"] != article["date_short"]:
            published_note = ' &middot; published <time datetime="%s">%s</time>' % (
                esc(article["published"]), esc(article["published_short"]))
        body = render(load_template("story.html").rstrip("\n"), {
            "edition_url": esc(edition["url"]),
            "edition_label": esc(edition["label"]),
            "cat_label": esc(article["cat_label"]),
            "date_short": esc(article["date_short"]),
            "published_note": published_note,
            "title": esc(article["title"]),
            "dek": esc(article["dek"]),
            "date_iso": esc(article["date"]),
            "read_time": esc(article["read_time"]),
            "image_tag": responsive_img(article["image"], article["image_alt"], SIZES_STORY, priority=True),
            "caption": render_caption(article),
            "factbox": render_factbox(article.get("factbox")),
            "body": indent_block(body_text, 6),
            "sources": sources,
            "related": related,
            "prev_link": ('<a href="%s">&larr; %s</a>' % (esc(prev_a["url"]), esc(prev_a["title"]))) if prev_a else "",
            "next_link": ('<a href="%s">%s &rarr;</a>' % (esc(next_a["url"]), esc(next_a["title"]))) if next_a else "",
            "subscribe": self.subscribe,
        })
        return self.page(head, self.header_nav, body, self.default_footer(),
                         article_meta=self.article_meta_tags(article),
                         extra_jsonld=self.story_jsonld(article, edition))

    # ---- briefs pages ----

    def build_briefs_page(self, edition):
        earlier = sorted(
            (e for e in self.m["editions"] if e["seq"] < edition["seq"]),
            key=lambda e: e["seq"])
        prev_ed = earlier[-1] if earlier else None
        briefs = self.m["brief_sets"][edition["briefs"]]["briefs"]
        body = render(load_template("briefs.html").rstrip("\n"), {
            "label": esc(edition["label"]),
            "date_iso": esc(edition["date"]),
            "date_long": esc(edition["date_long"]),
            "dek": esc(edition["dek"]),
            "briefs": render_briefs(briefs, with_eyebrows=True, separator=" &middot; ", level=2),
            "prev_url": esc(prev_ed["url"]) if prev_ed else "/archive/",
            "prev_label": esc(prev_ed["label"]) if prev_ed else "",
            "prev_date_short": esc(prev_ed["date_short"]) if prev_ed else "",
            "subscribe": self.subscribe,
        })
        return self.page(edition["head"], self.header_nav, body,
                         self.footer(edition["footer_edition"], edition["footer_fine"]),
                         extra_jsonld=self.edition_jsonld(edition))

    # ---- static pages ----

    def build_about(self):
        cfg = self.m["pages"]["about"]
        body_text = (SRC / cfg["body_file"]).read_text(encoding="utf-8")
        body = render(load_template("about.html").rstrip("\n"), {
            "body": indent_block(body_text, 6),
            "subscribe": self.subscribe,
        })
        return self.page(cfg["head"], self.header_nav, body, self.default_footer())

    def build_archive(self):
        cfg = self.m["pages"]["archive"]
        entries = []
        for edition in sorted(self.m["editions"], key=lambda e: e["seq"], reverse=True):
            lines = ["      <li>",
                     '        <p class="eyebrow accent">%s</p>' % esc(edition["archive_eyebrow"]),
                     '        <h2><a class="archive-headline" href="%s">%s</a></h2>'
                     % (esc(edition["url"]), esc(edition["archive_headline"])),
                     "        <p>%s</p>" % esc(edition["archive_summary"])]
            if edition.get("article_slugs"):
                lines.append("        <ul>")
                for slug in edition["article_slugs"]:
                    a = self.articles[slug]
                    lines.append('          <li><a href="%s">%s</a> <span class="archive-desk">— %s</span></li>'
                                 % (esc(a["url"]), esc(a["title"]), esc(a["cat_label"])))
                lines.append("        </ul>")
            lines.append('        <p><a href="%s">%s</a></p>' % (esc(edition["url"]), esc(edition["archive_read_text"])))
            lines.append("      </li>")
            entries.append("\n".join(lines))
        body = render(load_template("archive.html").rstrip("\n"), {
            "entries": "\n".join(entries) + "\n",
            "subscribe": self.subscribe,
        })
        return self.page(cfg["head"], self.header_nav, body, self.default_footer())

    def build_notfound(self):
        cfg = self.m["pages"]["notfound"]
        body = load_template("notfound.html").rstrip("\n")
        return self.page(cfg["head"], self.header_nav, body, self.default_footer(),
                         scripts=False, noindex=True)

    def build_redirect(self, source, target):
        """A moved page: points browsers and search engines at the new URL."""
        url = self.site["base_url"] + target
        return render(load_template("redirect.html"), {"url": esc(url), "path": esc(target)})

    # ---- sitemap + feed ----

    def sitemap_entries(self):
        base = self.site["base_url"]
        newest_edition_day = max(e["lastmod"] for e in self.m["editions"])
        entries = [(base + "/", self.m["homepage"]["lastmod"])]
        for e in sorted(self.m["editions"], key=lambda e: e["seq"]):
            entries.append((base + e["url"], e["lastmod"]))
        for a in sorted(self.m["articles"], key=lambda a: (a["published"], a["slug"])):
            entries.append((a["canonical"],
                            dt.datetime.fromisoformat(a["modified"]).astimezone(NEWSROOM_TZ).date().isoformat()))
        entries.append((base + self.m["pages"]["archive"]["url"], newest_edition_day))
        entries.append((base + self.m["pages"]["about"]["url"], self.m["pages"]["about"]["lastmod"]))
        return entries

    def build_sitemap(self):
        lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for url, lastmod in self.sitemap_entries():
            lines.append("  <url><loc>%s</loc><lastmod>%s</lastmod></url>"
                         % (xml_escape(url), xml_escape(lastmod)))
        lines.append("</urlset>")
        return "\n".join(lines) + "\n"

    def feed_items(self):
        items = []
        for edition in self.m["editions"]:
            if edition["kind"] == "briefs":
                items.append({
                    "title": edition["feed_title"],
                    "link": self.site["base_url"] + edition["url"],
                    "description": edition["feed_description"],
                    "published": dt.datetime.fromisoformat(edition["published"]),
                    "categories": edition["feed_categories"],
                })
        for a in self.m["articles"]:
            items.append({
                "title": a["title"],
                "link": a["canonical"],
                "description": a["dek"],
                "published": dt.datetime.fromisoformat(a["published"]),
                "categories": [a["section"]],
            })
        items.sort(key=lambda i: (i["published"], i["link"]), reverse=True)
        return items

    def build_feed(self):
        items = self.feed_items()
        last_build = email.utils.format_datetime(items[0]["published"]) if items else ""
        lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
                 "<channel>",
                 "  <title>%s</title>" % xml_escape(self.site["name"]),
                 "  <link>%s</link>" % xml_escape(self.site["base_url"] + "/"),
                 "  <description>%s</description>" % xml_escape(self.site["feed_description"]),
                 "  <language>%s</language>" % xml_escape(self.site["feed_language"]),
                 "  <lastBuildDate>%s</lastBuildDate>" % xml_escape(last_build),
                 '  <atom:link href="%s" rel="self" type="application/rss+xml" />'
                 % xml_escape(self.site["base_url"] + "/feed.xml")]
        for item in items:
            lines.append("  <item>")
            lines.append("    <title>%s</title>" % xml_escape(item["title"]))
            lines.append("    <link>%s</link>" % xml_escape(item["link"]))
            lines.append('    <guid isPermaLink="true">%s</guid>' % xml_escape(item["link"]))
            lines.append("    <description>%s</description>" % xml_escape(item["description"]))
            lines.append("    <pubDate>%s</pubDate>" % email.utils.format_datetime(item["published"]))
            for cat in item["categories"]:
                lines.append("    <category>%s</category>" % xml_escape(cat))
            lines.append("  </item>")
        lines.append("</channel>")
        lines.append("</rss>")
        return "\n".join(lines) + "\n"

    def build_all(self):
        out = {}
        out["index.html"] = self.build_homepage()
        out["404.html"] = self.build_notfound()
        out["about/index.html"] = self.build_about()
        out["archive/index.html"] = self.build_archive()
        for edition in self.m["editions"]:
            rel = edition["url"].lstrip("/") + "index.html"
            if edition["kind"] == "edition":
                out[rel] = self.build_edition_page(edition)
            else:
                out[rel] = self.build_briefs_page(edition)
        for article in self.m["articles"]:
            rel = article["url"].lstrip("/") + "index.html"
            out[rel] = self.build_story(article)
        for r in self.m.get("redirects", []):
            out[r["from"].lstrip("/") + "index.html"] = self.build_redirect(r["from"], r["to"])
        out["sitemap.xml"] = self.build_sitemap()
        out["feed.xml"] = self.build_feed()
        return out


# ---------------------------------------------------------------- validation

class PageInspector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.h1_count = 0
        self.title = ""
        self.in_title = False
        self.has_canonical = False
        self.has_description = False
        self.noindex = False
        self.ids = []
        self.links = []
        self.images = []
        self.meta_images = []
        self.headings = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "h1":
            self.h1_count += 1
        if len(tag) == 2 and tag[0] == "h" and tag[1] in "123456":
            self.headings.append(int(tag[1]))
        if tag == "title":
            self.in_title = True
        if tag == "link" and d.get("rel") == "canonical":
            self.has_canonical = True
        if tag == "meta" and d.get("name") == "description" and d.get("content", "").strip():
            self.has_description = True
        if tag == "meta" and d.get("name") == "robots" and "noindex" in d.get("content", ""):
            self.noindex = True
        if tag == "meta" and d.get("property") in ("og:image",) and d.get("content"):
            self.meta_images.append(d["content"])
        if tag == "meta" and d.get("name") == "twitter:image" and d.get("content"):
            self.meta_images.append(d["content"])
        if "id" in d:
            self.ids.append(d["id"])
        if tag == "a" and d.get("href"):
            self.links.append(d["href"])
        if tag == "img" and d.get("src"):
            self.images.append(d["src"])
        if tag == "source" and d.get("srcset"):
            self.images.extend(part.strip().split(" ")[0] for part in d["srcset"].split(","))

    def handle_data(self, data):
        if self.in_title:
            self.title += data

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False


def resolve_url_path(url_path):
    """Map a site-absolute URL path to a site-relative file path."""
    path = url_path.split("#", 1)[0].split("?", 1)[0]
    if not path.startswith("/"):
        return None
    rel = path.lstrip("/")
    if rel == "":
        rel = "index.html"
    elif rel.endswith("/"):
        rel += "index.html"
    return rel


def exists(target, generated):
    return target in generated or (ROOT / target).is_file()


def validate_outputs(outputs, manifest, builder):
    site = manifest["site"]
    base = site["base_url"]
    generated = set(outputs)
    redirect_files = {r["from"].lstrip("/") + "index.html" for r in manifest.get("redirects", [])}
    for rel, content in outputs.items():
        if "{{" in content or "}}" in content:
            fail(f"{rel}: leftover template placeholder")
        if "data:image" in content:
            fail(f"{rel}: embedded base64 image found")
    for rel, content in outputs.items():
        if not rel.endswith(".html"):
            continue
        insp = PageInspector()
        insp.feed(content)
        if insp.h1_count != 1:
            fail(f"{rel}: has {insp.h1_count} <h1> elements, expected exactly 1")
        if not insp.title.strip():
            fail(f"{rel}: missing or empty <title>")
        if not insp.has_canonical:
            fail(f"{rel}: missing canonical link")
        if not insp.has_description:
            fail(f"{rel}: missing meta description")
        if rel in redirect_files and not insp.noindex:
            fail(f"{rel}: redirect page must be noindex")
        if insp.headings and insp.headings[0] != 1:
            fail(f"{rel}: first heading is h{insp.headings[0]}, expected h1")
        for prev, cur in zip(insp.headings, insp.headings[1:]):
            if cur > prev + 1:
                fail(f"{rel}: heading level skips from h{prev} to h{cur}")
                break
        if "style=" in content:
            fail(f"{rel}: inline style attribute found (use a class in style.css)")
        dupes = sorted({i for i in insp.ids if insp.ids.count(i) > 1})
        if dupes:
            fail(f"{rel}: duplicate id attributes: {dupes}")
        page_dir = str(Path(rel).parent)
        for href in insp.links:
            if href.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if href.startswith("/"):
                target = resolve_url_path(href)
            else:
                target = resolve_url_path("/" + str(Path(page_dir, href).as_posix()))
            if target is None:
                continue
            if not exists(target, generated):
                fail(f"{rel}: internal link target missing: {href} -> {target}")
        for src in insp.images:
            if src.startswith("data:"):
                fail(f"{rel}: embedded base64 image: {src[:40]}")
                continue
            if src.startswith(("http://", "https://")):
                continue
            target = resolve_url_path(src)
            if not exists(target, generated):
                fail(f"{rel}: image file missing: {src} -> {target}")
        for img_url in insp.meta_images:
            if img_url.startswith(base):
                target = resolve_url_path(img_url[len(base):])
                if not exists(target, generated):
                    fail(f"{rel}: social image file missing: {img_url}")
    # sitemap: every real page exactly once, nothing else
    locs = re.findall(r"<loc>(.*?)</loc>", outputs["sitemap.xml"])
    lastmods = re.findall(r"<lastmod>(.*?)</lastmod>", outputs["sitemap.xml"])
    today = today_in_newsroom().isoformat()
    if len(locs) != len(set(locs)):
        fail("sitemap.xml: duplicate URLs")
    for lastmod in lastmods:
        if lastmod > today:
            fail(f"sitemap.xml: lastmod {lastmod} is in the future")
    page_urls = {base + "/"}
    page_urls |= {base + e["url"] for e in manifest["editions"]}
    page_urls |= {base + a["url"] for a in manifest["articles"]}
    page_urls |= {base + manifest["pages"][k]["url"] for k in ("about", "archive")}
    if set(locs) != page_urls:
        fail(f"sitemap.xml: extra {sorted(set(locs) - page_urls)}, missing {sorted(page_urls - set(locs))}")
    for loc in locs:
        target = resolve_url_path(loc[len(base):])
        if not exists(target, generated):
            fail(f"sitemap.xml: URL has no file: {loc}")
    # feed: well-formed, newest first, every item resolves
    try:
        feed_root = ET.fromstring(outputs["feed.xml"])
    except ET.ParseError as exc:
        fail(f"feed.xml: not well-formed XML: {exc}")
        return
    feed_links = [i.findtext("link") for i in feed_root.iter("item")]
    if feed_links != [i["link"] for i in builder.feed_items()]:
        fail("feed.xml: items do not match the manifest")
    for link in feed_links:
        if not exists(resolve_url_path(link[len(base):]), generated):
            fail(f"feed.xml: item link has no file: {link}")
    for rel in ("assets/css/style.css", "assets/js/main.js",
                "assets/fonts/archivo-latin-wght-normal.woff2",
                "assets/fonts/archivo-latin-ext-wght-normal.woff2"):
        if not (ROOT / rel).is_file():
            fail(f"{rel} is missing")


# ---------------------------------------------------------------- output

def write_site(outputs):
    """Replace _site/ with the static files plus the generated pages."""
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    for name in STATIC_PATHS:
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, OUT / name)
        elif src.is_file():
            shutil.copy2(src, OUT / name)
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    for rel, content in outputs.items():
        dest = OUT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")


def report_errors(heading):
    print(heading, file=sys.stderr)
    for e in ERRORS:
        print("  -", e, file=sys.stderr)


def main(argv):
    check_only = "--check" in argv
    manifest = load_manifest()
    validate_manifest(manifest)
    if ERRORS:
        report_errors("MANIFEST ERRORS:")
        return 1
    derive(manifest)
    builder = Builder(manifest)
    outputs = builder.build_all()
    validate_outputs(outputs, manifest, builder)
    if ERRORS:
        report_errors("BUILD FAILED — validation errors (nothing written):")
        return 1
    if check_only:
        print(f"CHECK OK: {len(outputs)} pages and files would be written, all checks passed.")
        return 0
    write_site(outputs)
    on_disk = {rel: (OUT / rel).read_text(encoding="utf-8") for rel in outputs}
    validate_outputs(on_disk, manifest, builder)
    if ERRORS:
        report_errors("POST-WRITE VALIDATION FAILED:")
        return 1
    print(f"BUILD OK: {len(outputs)} generated files written to _site/, all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
