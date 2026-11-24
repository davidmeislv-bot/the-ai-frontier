#!/usr/bin/env python3
"""Build the Wire Willow static site.

Single source of truth: src/manifest.json (articles, editions, briefs,
page metadata). Templates live in src/templates/, article bodies in
src/content/. The shared stylesheet is assets/css/style.css and the
shared script is assets/js/main.js; both are linked, never inlined.

Outputs (written in place, next to this script):
  index.html, 404.html, about/index.html, archive/index.html,
  2026/<month>/<day>/index.html (edition + briefs pages),
  2026/<month>/<day>/<slug>/index.html (story pages),
  sitemap.xml, feed.xml

Usage:
  python3 build.py            build + validate + write
  python3 build.py --check    build + validate, write nothing

Validation fails loudly (exit 1, errors listed) and nothing is written
when any check fails: every internal link and image must resolve, every
page needs exactly one H1 plus title/canonical/description, no template
placeholders, base64 images or inline styles may survive, heading levels
may not skip, social images must be 1200x630 and unique, the homepage must
show the newest edition and briefs, and the sitemap and feed must match
the manifest exactly.

Images: run tools/make_images.py (needs Pillow) after adding an image; it
writes the -640/-1024/-1600 variants this script turns into srcset.
"""

import hashlib
import html as htmlmod
import json
import re
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
TEMPLATES = SRC / "templates"

ERRORS = []


def fail(msg):
    ERRORS.append(msg)


def esc(text):
    """Escape text for an HTML text node or double-quoted attribute."""
    return htmlmod.escape(str(text), quote=True)


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


def validate_manifest(m):
    site = m["site"]
    for key in ("base_url", "logo_url", "org_description", "founding_date"):
        if not site.get(key):
            fail(f"manifest: site.{key} is missing")
    articles = {a["slug"]: a for a in m["articles"]}
    seen_urls, seen_ids = set(), set()
    for a in m["articles"]:
        for field in ("id", "slug", "url", "title", "dek", "category", "cat_label",
                      "section", "date", "published", "read_time", "read_short",
                      "image", "image_alt", "og_image", "caption", "body_file",
                      "head_title", "head_description", "canonical"):
            if a.get(field) in (None, ""):
                fail(f"manifest: article {a.get('slug')} is missing {field}")
        if a["slug"] in seen_ids:
            fail(f"manifest: duplicate article slug {a['slug']}")
        seen_ids.add(a["slug"])
        if a["url"] in seen_urls:
            fail(f"manifest: duplicate article url {a['url']}")
        seen_urls.add(a["url"])
        body_path = SRC / a["body_file"]
        if not body_path.is_file():
            fail(f"manifest: article {a['slug']} body file missing: {a['body_file']}")
        for label, url in a.get("sources", []):
            if not label or not url.startswith("http"):
                fail(f"manifest: article {a['slug']} has a bad source entry: {label!r} {url!r}")
    editions = {e["id"]: e for e in m["editions"]}
    # Editions are ordered by seq (the edition number), NOT by date.
    # The site publishes up to 3 editions a day, all dated the real
    # publish day, so several editions can share one date and the date
    # can be earlier than an older edition's date. Ordering by date
    # would pick a stale edition as "newest" and run the cover date
    # ahead of the calendar. seq always increases (No. 001 = 1, ...).
    seen_seq = {}
    for e in m["editions"]:
        if not isinstance(e.get("seq"), int):
            fail(f"manifest: edition {e['id']} is missing an integer seq")
            continue
        if e["seq"] in seen_seq:
            fail(f"manifest: editions {seen_seq[e['seq']]} and {e['id']} share seq {e['seq']}")
        seen_seq[e["seq"]] = e["id"]
        if e["briefs"] not in m["brief_sets"]:
            fail(f"manifest: edition {e['id']} references unknown brief set {e['briefs']}")
        for slug in e.get("article_slugs", []):
            if slug not in articles:
                fail(f"manifest: edition {e['id']} references unknown article {slug}")
        if e.get("lead_slug") and e["lead_slug"] not in articles:
            fail(f"manifest: edition {e['id']} references unknown lead {e['lead_slug']}")
    for name, bset in m["brief_sets"].items():
        for b in bset["briefs"]:
            if not b.get("title") or not b.get("body") or not b.get("sources"):
                fail(f"manifest: brief set {name} has an incomplete brief: {b.get('title')!r}")
    if m["homepage"]["edition"] not in editions:
        fail("manifest: homepage references an unknown edition")
    if m["homepage"]["briefs"] not in m["brief_sets"]:
        fail("manifest: homepage references an unknown brief set")
    # The homepage must always show the newest content, never a stale edition.
    # Newest means highest seq, not latest date (see the seq note above).
    full = [e for e in m["editions"] if e["kind"] == "edition"]
    if full:
        newest = max(full, key=lambda e: e["seq"])
        if m["homepage"]["edition"] != newest["id"]:
            fail(f"manifest: homepage shows edition {m['homepage']['edition']}, "
                 f"but the newest edition is {newest['id']}")
    newest_briefs = max(m["editions"], key=lambda e: e["seq"])["briefs"]
    if m["homepage"]["briefs"] != newest_briefs:
        fail(f"manifest: homepage shows brief set {m['homepage']['briefs']}, "
             f"but the newest is {newest_briefs}")
    # Images: lead images exist; social cards exist, are 1200x630 and unique.
    og_hashes = {}
    default_og = site.get("default_og_image", "")
    for url in [default_og] + [a["og_image"] for a in m["articles"]]:
        path = ROOT / url[len(site["base_url"]):].lstrip("/")
        if not path.is_file():
            fail(f"manifest: social image missing: {url}")
            continue
        if image_size(path) != (1200, 630):
            fail(f"manifest: social image is {image_size(path)}, expected 1200x630: {url}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in og_hashes and og_hashes[digest] != url:
            fail(f"manifest: social image {url} is a byte-for-byte copy of {og_hashes[digest]}")
        og_hashes.setdefault(digest, url)
    for a in m["articles"]:
        if not (ROOT / a["image"].lstrip("/")).is_file():
            fail(f"manifest: article {a['slug']} image missing: {a['image']}")
        if a["category"] not in dict(DESKS):
            fail(f"manifest: article {a['slug']} has unknown category {a['category']!r}")
    newsletter = site.get("newsletter", {})
    if not newsletter.get("action") and "@" not in newsletter.get("fallback_email", ""):
        fail("manifest: site.newsletter needs an action URL or a fallback_email")
    return articles, editions


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


def json_ld_script(obj):
    return '<script type="application/ld+json">\n' + json.dumps(obj, indent=2, ensure_ascii=False) + "\n</script>\n"


def breadcrumb_block(site, crumbs):
    items = []
    for i, (name, url) in enumerate(crumbs, start=1):
        item = {"@type": "ListItem", "position": i, "name": name}
        if url:
            item["item"] = url
        items.append(item)
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": items}


def news_article_block(site, article):
    org_id = site["base_url"] + "/#organization"
    return {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": article["title"],
        "description": article["head_description"],
        "image": [site["base_url"] + article["image"]],
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
        "inLanguage": "en-US",
    }


# ---------------------------------------------------------------- fragments

def search_text(title, dek, cat_label):
    """The text a front-page search matches against (cards and lead)."""
    return (title + " " + dek + " " + cat_label).lower()


# Desks in display order: filter value -> button label.
DESKS = [("ai", "AI"), ("robotics", "Robotics"), ("drones", "Drones"),
         ("fintech", "Fintech"), ("gossip", "The Gossip"),
         ("finance", "Tech Finance"), ("industry", "Tech Industry")]


# ---------------------------------------------------------------- images

RESPONSIVE_WIDTHS = (640, 1024, 1600)


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


def variant_url(src, width):
    stem, dot, ext = src.rpartition(".")
    return "%s-%d.%s" % (stem, width, ext)


def responsive_img(src, alt, sizes, css_class="", lazy=False, priority=False):
    """An <img> with real dimensions and a srcset of the variants made by
    tools/make_images.py. Variants that don't exist are simply skipped."""
    width, height = image_size(ROOT / src.lstrip("/"))
    candidates = [(variant_url(src, w), w) for w in RESPONSIVE_WIDTHS
                  if w < width and (ROOT / variant_url(src, w).lstrip("/")).is_file()]
    candidates.append((src, width))
    attrs = []
    if css_class:
        attrs.append('class="%s"' % css_class)
    # src falls back to a mid-size variant; srcset-aware browsers pick their own
    attrs.append('src="%s"' % esc(candidates[min(1, len(candidates) - 1)][0]))
    if len(candidates) > 1:
        attrs.append('srcset="%s"' % esc(", ".join("%s %dw" % c for c in candidates)))
        attrs.append('sizes="%s"' % esc(sizes))
    attrs.append('alt="%s"' % esc(alt))
    attrs.append('width="%d" height="%d"' % (width, height))
    if lazy:
        attrs.append('loading="lazy"')
    if priority:
        attrs.append('fetchpriority="high"')
    else:
        attrs.append('decoding="async"')
    return "<img %s />" % " ".join(attrs)


SIZES_HERO = "(min-width: 1240px) 1200px, calc(100vw - 40px)"
SIZES_CARD = "(min-width: 1240px) 380px, (min-width: 900px) 30vw, (min-width: 560px) 45vw, calc(100vw - 40px)"
SIZES_STORY = "(min-width: 820px) 780px, calc(100vw - 40px)"


# ---------------------------------------------------------------- fragments

def render_card(article, edition):
    thumb = responsive_img(article["image"], article["image_alt"], SIZES_CARD,
                           css_class="card-thumb", lazy=True)
    return (
        '<article class="card" data-cat="%s" data-search="%s">'
        "%s"
        '<div class="card-body"><p class="eyebrow accent">%s</p>'
        '<h3 class="card-title"><a class="card-link" href="%s">%s</a></h3>'
        '<p class="card-dek">%s</p>'
        '<p class="card-meta"><span>%s · %s</span>'
        '<span class="card-cta" aria-hidden="true">Read →</span></p></div></article>'
        % (
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


def render_filter_buttons(categories):
    """'Front Page' plus one button per desk that has a story today, so no
    filter ever leads to an empty page."""
    buttons = [("all", "Front Page")] + [d for d in DESKS if d[0] in categories]
    return "".join(
        '      <button class="fbtn" type="button" data-filter="%s" aria-pressed="%s">%s</button>\n'
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
        """Newsletter form. With site.newsletter.action set (e.g. a Buttondown
        or Beehiiv embed URL) the form posts there and works without JS.
        Otherwise main.js opens a pre-filled signup email."""
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
            json_ld_script(breadcrumb_block(self.site, crumbs))

    def edition_jsonld(self, edition):
        crumbs = [
            ("Home", self.site["base_url"] + "/"),
            ("Archive", self.site["base_url"] + "/archive/"),
            (edition["breadcrumb_name"], None),
        ]
        return json_ld_script(breadcrumb_block(self.site, crumbs))

    # ---- front page (homepage + frozen edition pages) ----

    def front_page(self, edition, briefs_key, briefs_label, briefs_links, crumbs_html, head):
        briefs = self.m["brief_sets"][briefs_key]["briefs"]
        lead = self.articles[edition["lead_slug"]]
        cards = [self.articles[s] for s in edition["article_slugs"] if s != edition["lead_slug"]]
        card_html = "\n".join("      " + render_card(a, edition) for a in cards) + "\n"
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
            "lead_caption": esc(lead["caption"]),
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
                                       cfg["briefs_links"], "", cfg["head"])
        return self.page(cfg["head"], header, body, self.default_footer())

    def build_edition_page(self, edition):
        crumbs = '  <p class="crumbs"><a href="/">Home</a> &middot; <a href="/archive/">Archive</a> &middot; %s</p>\n' \
            % esc(edition["label"])
        header, body = self.front_page(edition, edition["briefs"], edition["briefs_label"],
                                       edition["briefs_links"], crumbs, edition["head"])
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
        body = render(load_template("story.html").rstrip("\n"), {
            "edition_url": esc(edition["url"]),
            "edition_label": esc(edition["label"]),
            "cat_label": esc(article["cat_label"]),
            "date_short": esc(article["date_short"]),
            "title": esc(article["title"]),
            "dek": esc(article["dek"]),
            "date_iso": esc(article["date"]),
            "read_time": esc(article["read_time"]),
            "image_tag": responsive_img(article["image"], article["image_alt"], SIZES_STORY, priority=True),
            "caption": esc(article["caption"]),
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

    # ---- sitemap + feed ----

    def build_sitemap(self):
        lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for entry in self.m["sitemap"]:
            line = "  <url><loc>%s</loc><lastmod>%s</lastmod>" % (
                xml_escape(entry["url"]), xml_escape(entry["lastmod"]))
            if entry.get("changefreq"):
                line += "<changefreq>%s</changefreq>" % xml_escape(entry["changefreq"])
            line += "</url>"
            lines.append(line)
        lines.append("</urlset>")
        return "\n".join(lines) + "\n"

    def build_feed(self):
        items = []
        for edition in self.m["editions"]:
            if edition["kind"] == "briefs":
                items.append({
                    "title": edition["feed_title"],
                    "link": self.site["base_url"] + edition["url"],
                    "description": edition["feed_description"],
                    "pubdate": edition["feed_pubdate"],
                    "categories": edition["feed_categories"],
                    "sort_date": edition["date"],
                })
        for a in self.m["articles"]:
            items.append({
                "title": a["title"],
                "link": self.site["base_url"] + a["url"],
                "description": a["dek"],
                "pubdate": a["feed_pubdate"],
                "categories": [a["section"]],
                "sort_date": a["date"],
            })
        items.sort(key=lambda i: i["sort_date"], reverse=True)  # stable: keeps edition order on ties
        last_build = items[0]["pubdate"] if items else ""
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
            lines.append("    <pubDate>%s</pubDate>" % xml_escape(item["pubdate"]))
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

    def handle_data(self, data):
        if self.in_title:
            self.title += data

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False


def resolve_url_path(url_path, generated):
    """Map a site-absolute URL path to a repo-relative file path."""
    path = url_path.split("#", 1)[0].split("?", 1)[0]
    if not path.startswith("/"):
        return None
    rel = path.lstrip("/")
    if rel == "":
        rel = "index.html"
    elif rel.endswith("/"):
        rel += "index.html"
    return rel


def validate_outputs(outputs, manifest):
    site = manifest["site"]
    base = site["base_url"]
    generated = set(outputs)
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
                target = resolve_url_path(href, generated)
            else:
                target = str(Path(page_dir, href).as_posix())
                target = resolve_url_path("/" + target, generated)
            if target is None:
                continue
            if target not in generated and not (ROOT / target).is_file():
                fail(f"{rel}: internal link target missing: {href} -> {target}")
        for src in insp.images:
            if src.startswith(("http://", "https://", "data:")):
                if src.startswith("data:"):
                    fail(f"{rel}: embedded base64 image: {src[:40]}")
                continue
            target = resolve_url_path(src, generated)
            if target not in generated and not (ROOT / target).is_file():
                fail(f"{rel}: image file missing: {src} -> {target}")
        for img_url in insp.meta_images:
            if img_url.startswith(base):
                target = resolve_url_path(img_url[len(base):], generated)
                if target not in generated and not (ROOT / target).is_file():
                    fail(f"{rel}: social image file missing: {img_url}")
    # sitemap: every URL resolves, and the set matches the manifest list
    sitemap = outputs["sitemap.xml"]
    locs = re.findall(r"<loc>(.*?)</loc>", sitemap)
    expected = [e["url"] for e in manifest["sitemap"]]
    if locs != expected:
        fail(f"sitemap.xml: URLs do not match the manifest list: {locs} != {expected}")
    for loc in locs:
        if not loc.startswith(base):
            fail(f"sitemap.xml: URL outside this site: {loc}")
            continue
        target = resolve_url_path(loc[len(base):], generated)
        if target not in generated and not (ROOT / target).is_file():
            fail(f"sitemap.xml: URL has no file: {loc}")
    # every generatable page URL must appear in the sitemap
    page_urls = {base + "/"}
    for e in manifest["editions"]:
        page_urls.add(base + e["url"])
    for a in manifest["articles"]:
        page_urls.add(base + a["url"])
    for key in ("about", "archive"):
        page_urls.add(base + manifest["pages"][key]["url"])
    missing = sorted(page_urls - set(locs))
    if missing:
        fail(f"sitemap.xml: missing pages: {missing}")
    # feed: items match the manifest-derived set
    try:
        feed_root = ET.fromstring(outputs["feed.xml"])
    except ET.ParseError as exc:
        fail(f"feed.xml: not well-formed XML: {exc}")
        return
    feed_links = [i.findtext("link") for i in feed_root.iter("item")]
    expected_links = []
    for e in manifest["editions"]:
        if e["kind"] == "briefs":
            expected_links.append((e["date"], base + e["url"]))
    for a in manifest["articles"]:
        expected_links.append((a["date"], base + a["url"]))
    expected_links.sort(key=lambda t: t[0], reverse=True)
    if feed_links != [u for _, u in expected_links]:
        fail(f"feed.xml: items do not match the manifest: {feed_links}")
    for link in feed_links:
        target = resolve_url_path(link[len(base):], generated)
        if target not in generated and not (ROOT / target).is_file():
            fail(f"feed.xml: item link has no file: {link}")
    # shared assets exist
    if not (ROOT / "assets/css/style.css").is_file():
        fail("assets/css/style.css is missing")
    if not (ROOT / "assets/js/main.js").is_file():
        fail("assets/js/main.js is missing")
    for font in ("archivo-latin-wght-normal.woff2", "archivo-latin-ext-wght-normal.woff2"):
        if not (ROOT / "assets/fonts" / font).is_file():
            fail(f"assets/fonts/{font} is missing")


def main(argv):
    check_only = "--check" in argv
    manifest = load_manifest()
    validate_manifest(manifest)
    if ERRORS:
        print("MANIFEST ERRORS:", file=sys.stderr)
        for e in ERRORS:
            print("  -", e, file=sys.stderr)
        return 1
    builder = Builder(manifest)
    outputs = builder.build_all()
    validate_outputs(outputs, manifest)
    if ERRORS:
        print("BUILD FAILED — validation errors (nothing written):", file=sys.stderr)
        for e in ERRORS:
            print("  -", e, file=sys.stderr)
        return 1
    if check_only:
        print(f"CHECK OK: {len(outputs)} files would be written, all checks passed.")
        return 0
    for rel, content in outputs.items():
        dest = ROOT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    # Re-read from disk and re-validate what was actually written.
    on_disk = {rel: (ROOT / rel).read_text(encoding="utf-8") for rel in outputs}
    validate_outputs(on_disk, manifest)
    if ERRORS:
        print("POST-WRITE VALIDATION FAILED:", file=sys.stderr)
        for e in ERRORS:
            print("  -", e, file=sys.stderr)
        return 1
    for rel in sorted(outputs):
        print(f"  wrote {rel} ({len(outputs[rel])} bytes)")
    print(f"BUILD OK: {len(outputs)} files, all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
