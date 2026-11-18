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
placeholders or base64 images may survive, and the sitemap and feed must
match the manifest exactly.
"""

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
    for e in m["editions"]:
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
    """The text a front-page search matches against (same formula the
    old inline script used for cards, now also used for the lead)."""
    return (title + " " + dek + " " + cat_label).lower()


def render_card(article, edition):
    if article.get("image"):
        thumb = ('<img class="thumb" src="%s" alt="%s" loading="lazy" width="1920" height="1280" />'
                 % (esc(article["image"]), esc(article["image_alt"])))
    else:
        thumb = ('<div class="thumb-fallback" aria-hidden="true">%s</div>'
                 % esc(article.get("emoji", "")))
    return (
        '<a class="card" href="%s" data-cat="%s" data-id="%s" data-search="%s">'
        "%s"
        '<div class="card-body"><span class="eyebrow accent">%s</span>'
        "<h3>%s</h3><p>%s</p>"
        '<div class="meta"><span>%s · %s</span>'
        '<span style="color:var(--accent);font-weight:800">READ →</span></div></div></a>'
        % (
            esc(article["url"]),
            esc(article["category"]),
            esc(article["id"]),
            esc(search_text(article["title"], article["dek"], article["cat_label"])),
            thumb,
            esc(article["cat_label"]),
            esc(article["title"]),
            esc(article["dek"]),
            esc(edition["date_upper"]),
            esc(article["read_short"]),
        )
    )


def render_ticker(ticker):
    """Ticker items, duplicated for the seamless CSS loop, exactly as
    the old inline script built them."""
    parts = []
    for label, text, extra, cls in ticker:
        s = "<span><b>%s</b> %s" % (esc(label), esc(text))
        if extra:
            s += ' <span class="%s">%s</span>' % (esc(cls), esc(extra))
        s += "</span>"
        parts.append(s)
    once = "".join(parts)
    return once + once


def render_briefs(briefs, with_eyebrows, separator):
    out = []
    for b in briefs:
        links = separator.join(
            '<a href="%s" target="_blank" rel="noopener">%s</a>' % (esc(url), esc(label))
            for label, url in b["sources"]
        )
        word = "Source" if len(b["sources"]) == 1 else "Sources"
        out.append('      <div class="brief">')
        if with_eyebrows and b.get("eyebrow"):
            out.append('        <p class="eyebrow accent">%s</p>' % esc(b["eyebrow"]))
        out.append("        <h3>%s</h3>" % esc(b["title"]))
        out.append("        <p>%s</p>" % esc(b["body"]))
        out.append('        <p class="src">%s: %s</p>' % (word, links))
        out.append("      </div>")
    return "\n".join(out) + "\n" if out else ""


def render_factbox(factbox, indent="    "):
    if not factbox:
        return ""
    lines = [indent + '<div class="factbox">',
             indent + "  <h3>%s</h3>" % esc(factbox["heading"]),
             indent + "  <ul>"]
    for strong, rest in factbox["items"]:
        lines.append(indent + "    <li><strong>%s</strong>%s</li>" % (esc(strong), esc(rest)))
    lines.append(indent + "  </ul>")
    lines.append(indent + "</div>")
    return "\n".join(lines) + "\n"


def indent_block(text, spaces):
    pad = " " * spaces
    return "".join(pad + line + "\n" for line in text.strip("\n").split("\n"))


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
        self.subscribe = load_template("partials/subscribe.html").rstrip("\n")
        self.org_json = json.dumps(org_block(self.site), indent=2, ensure_ascii=False)

    def footer(self, edition_line, fine_line):
        return render(self.footer_tpl, {
            "footer_intro": esc(self.site["footer_intro"]),
            "footer_edition": esc(edition_line),
            "footer_fine": esc(fine_line),
        })

    def default_footer(self):
        return self.footer(self.site["footer_default_edition"], self.site["footer_default_fine"])

    def page(self, head, header, body, footer, scripts=True, article_meta="", extra_jsonld=""):
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
            "scripts": '\n<script src="/assets/js/main.js"></script>' if scripts else "",
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
        panel = render(load_template("partials/panel.html").rstrip("\n"), {
            "watchbox": "".join("          <li>%s</li>\n" % esc(w) for w in edition["watchbox"]),
        })
        if briefs_links:
            links = self.m["homepage"]["briefs_link_lines"]
            links_html = "      " + '<p class="src" style="margin-top:4px">' + " &middot; ".join(
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
            "lead_image": esc(lead["image"]),
            "lead_image_alt": esc(lead["image_alt"]),
            "lead_image_width": str(lead["image_width"]),
            "lead_image_height": str(lead["image_height"]),
            "lead_cat_label": esc(lead["cat_label"]),
            "lead_title": esc(lead["title"]),
            "lead_dek": esc(lead["dek"]),
            "date_short": esc(edition["date_short"]),
            "lead_read_time": esc(lead["read_time"]),
            "lead_url": esc(lead["url"]),
            "lead_caption": esc(lead["caption"]),
            "lead_factbox": render_factbox(lead.get("factbox")),
            "count_label": "%d %s · Updated %s" % (
                count, "story" if count == 1 else "stories", edition["date_short"]),
            "date_upper": esc(edition["date_upper"]),
            "cards": card_html,
            "band_stats": "".join(
                '      <div class="stat"><div class="big">%s</div><div class="lbl">%s</div></div>\n'
                % (esc(big), esc(lbl)) for big, lbl in edition["band_stats"]),
            "briefs_label": esc(briefs_label),
            "briefs": render_briefs(briefs, with_eyebrows=False, separator=" · "),
            "briefs_links": links_html,
            "panel": panel,
            "subscribe": self.subscribe,
        })
        return body

    def build_homepage(self):
        cfg = self.m["homepage"]
        edition = self.editions[cfg["edition"]]
        body = self.front_page(edition, cfg["briefs"], cfg["briefs_label"],
                               cfg["briefs_links"], "", cfg["head"])
        return self.page(cfg["head"], self.header_filter, body, self.default_footer())

    def build_edition_page(self, edition):
        crumbs = '  <p class="crumbs"><a href="/">Home</a> &middot; <a href="/archive/">Archive</a> &middot; %s</p>\n' \
            % esc(edition["label"])
        body = self.front_page(edition, edition["briefs"], edition["briefs_label"],
                               edition["briefs_links"], crumbs, edition["head"])
        return self.page(edition["head"], self.header_filter, body,
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
            "image": esc(article["image"]),
            "image_alt": esc(article["image_alt"]),
            "image_width": str(article["image_width"]),
            "image_height": str(article["image_height"]),
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
            (e for e in self.m["editions"] if e["date"] < edition["date"]),
            key=lambda e: e["date"])
        prev_ed = earlier[-1] if earlier else None
        briefs = self.m["brief_sets"][edition["briefs"]]["briefs"]
        body = render(load_template("briefs.html").rstrip("\n"), {
            "label": esc(edition["label"]),
            "date_iso": esc(edition["date"]),
            "date_long": esc(edition["date_long"]),
            "dek": esc(edition["dek"]),
            "briefs": render_briefs(briefs, with_eyebrows=True, separator=" &middot; "),
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
        for edition in sorted(self.m["editions"], key=lambda e: e["date"], reverse=True):
            lines = ["      <li>",
                     '        <p class="eyebrow accent">%s</p>' % esc(edition["archive_eyebrow"]),
                     '        <h2><a href="%s" style="color:var(--ink);text-decoration:none">%s</a></h2>'
                     % (esc(edition["url"]), esc(edition["archive_headline"])),
                     "        <p>%s</p>" % esc(edition["archive_summary"])]
            if edition.get("article_slugs"):
                lines.append("        <ul>")
                for slug in edition["article_slugs"]:
                    a = self.articles[slug]
                    lines.append('          <li><a href="%s">%s</a> <span style="color:var(--ink3)">— %s</span></li>'
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
        return self.page(cfg["head"], self.header_nav, body, self.default_footer(), scripts=False)

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

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "h1":
            self.h1_count += 1
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
