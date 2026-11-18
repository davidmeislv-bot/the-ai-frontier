# Wire Willow

A daily tech newspaper covering AI, robotics, drones, fintech and Silicon
Valley gossip. Live at <https://wirewillow.com>.

Hosting: GitHub Pages, deploying from the `main` branch (repository root).
Custom domain via the `CNAME` file, HTTPS enforced. The site is plain static
files — no framework, no database, no server-side code.

## Layout

```
build.py                 The builder. Run it to regenerate the site.
README.md                This file.
src/
  manifest.json          Single source of truth: articles, editions,
                         briefs, page metadata, sitemap order.
  templates/             One master layout (base.html), one template per
    base.html            page type, and shared partials (header, footer,
    front.html           subscribe form, workflow panel).
    story.html
    briefs.html
    about.html
    archive.html
    notfound.html
    partials/
  content/               Article bodies and the About page body, as
    articles/<slug>.html small HTML fragments. Referenced from the manifest.
    pages/about.html
assets/
  css/style.css          The one shared stylesheet, linked by every page.
  js/main.js             The one shared script: section filter, search,
                         and the mailto subscribe form.
```

Generated files — do not edit by hand, they are overwritten by the build:

```
index.html               Homepage (current edition + latest briefs)
404.html
about/index.html
archive/index.html       Generated from the editions in the manifest
2026/11/13/index.html    Frozen edition page
2026/11/13/<slug>/       One page per story
2026/11/18/index.html    Briefs page
sitemap.xml              Generated from the manifest sitemap list
feed.xml                 Generated from the manifest (briefs + stories)
```

Unmanaged files (left alone by the build): `CNAME`, `robots.txt`, favicons,
and everything in `images/`.

## Publish flow

1. **Add the content.** Write the article body as a new file in
   `src/content/articles/<slug>.html` (plain `<p>` paragraphs, same as the
   others), add its images to `images/`, and add one entry to the
   `articles` list in `src/manifest.json` — slug, title, dek, date,
   category, image + alt text, read time, sources, and the body file
   reference. For a new edition, add an edition entry and point the
   homepage at it.
2. **Build.** Run `python3 build.py` from the repository root. It needs
   only the Python standard library. The build validates everything and
   fails loudly (exit 1, errors listed, nothing written) if anything is
   wrong: every internal link and image must resolve, every page needs
   exactly one `<h1>` plus a title, canonical URL and description, no
   template placeholders or base64 images may survive, and the sitemap
   and feed must match the manifest exactly. Use
   `python3 build.py --check` to validate without writing.
3. **Review the diff.** Check that only the intended pages changed.
4. **Push to `main`.** GitHub Pages publishes automatically, usually
   within a minute.
5. **Verify live.** Open the changed URLs on wirewillow.com and confirm
   the content, then check `/sitemap.xml` and `/feed.xml`.

Workflow rule: drafts are approved by David before anything is published.
This repository only ever holds approved, published content.

## Design notes

- **One stylesheet, one script.** Every page links the same
  `assets/css/style.css` and `assets/js/main.js`. A design change is made
  once, in one file, and reaches every page. Nothing is inlined per page.
- **Content is rendered at build time.** Story cards and the headline
  ticker are plain HTML in the served pages, so stories are visible to
  search engines and readers with JavaScript off. The script only
  filters, searches, and handles the subscribe form.
- **Light-only design.** The site forces the light color scheme
  (`color-scheme: light`); there is no dark variant.
- **No build step on GitHub's side.** GitHub Pages serves the committed
  files as-is. The generated HTML, sitemap and feed are committed to the
  repository, so the live site is always exactly what is in `main`.
