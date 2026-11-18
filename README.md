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
  js/main.js             The one shared script (ES module): section filter,
                         search, ticker pause, newsletter form fallback.
  fonts/                 Archivo variable font, self-hosted (SIL OFL).
tools/
  make_images.py         Makes responsive image variants and missing
                         social cards. Needs Pillow; build.py does not.
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
   homepage at it (the build refuses a homepage that isn't showing the
   newest edition and newest briefs).
   Then run `python3 tools/make_images.py` (one-time `pip install Pillow`)
   to make the `-640/-1024/-1600.webp` variants and, if the article's
   `og_image` file doesn't exist yet, a 1200×630 social card cropped from
   the lead image. Image dimensions are read from the files, never typed
   into the manifest.
2. **Build.** Run `python3 build.py` from the repository root. It needs
   only the Python standard library. The build validates everything and
   fails loudly (exit 1, errors listed, nothing written) if anything is
   wrong: every internal link and image must resolve, every page needs
   exactly one `<h1>` plus a title, canonical URL and description, no
   template placeholders, base64 images or inline `style=` attributes may
   survive, heading levels may not skip, social images must be 1200×630
   and unique, and the sitemap and feed must match the manifest exactly. Use
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
- **Responsive images.** `build.py` emits `srcset`/`sizes` for every
  image variant it finds, with real `width`/`height` read from the file.
- **Cache-busting.** CSS and JS are linked as `style.css?v=<hash>`; the
  hash changes only when the file does.
- **Newsletter.** `site.newsletter` in the manifest: set `action` to your
  newsletter provider's form URL (Buttondown, Beehiiv, etc.) and the form
  posts there, no JavaScript needed. While `action` is empty, the form
  falls back to opening a pre-filled signup email to `fallback_email`.
- **Accessibility.** Skip link, visible `:focus-visible` styles, a pause
  button on the headline ticker, and colors that meet WCAG AA. Filter
  buttons are only shown for desks that have a story that day.
- **Light-only design.** The site forces the light color scheme
  (`color-scheme: light`); there is no dark variant.
- **No build step on GitHub's side.** GitHub Pages serves the committed
  files as-is. The generated HTML, sitemap and feed are committed to the
  repository, so the live site is always exactly what is in `main`.
