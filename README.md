# Wire Willow

A daily tech newspaper covering AI, robotics, drones, fintech and Silicon
Valley news. Live at <https://wirewillow.com>.

Hosting: GitHub Pages, published by GitHub Actions
(`.github/workflows/site.yml`). Custom domain via the `CNAME` file, HTTPS
enforced. The site is plain static files — no framework, no database, no
server-side code. Only the finished site in `_site/` is published; this
repository's source files, notes and manifest are never served.

## Layout

```
build.py                 The builder. Writes the finished site to _site/.
src/
  manifest.json          Single source of truth: articles, editions,
                         briefs, page metadata, redirects.
  templates/             One master layout (base.html), one template per
                         page type (front, story, briefs, about, archive,
                         notfound, redirect) and shared partials.
  content/               Article bodies and the About page body, as
    articles/<slug>.html small HTML fragments. Referenced from the manifest.
    pages/about.html
assets/
  css/style.css          The one shared stylesheet, linked by every page.
  js/main.js             The one shared script (ES module): section filter,
                         search, ticker pause, newsletter form fallback,
                         filter-row scroll hint.
  fonts/                 Archivo variable font, self-hosted (SIL OFL).
images/                  <name>.webp masters (≤1600px) + <name>-640/-800/
                         -1024/-1600.avif sizes + og-<name>.jpg share cards.
tools/
  make_images.py         Makes the AVIF sizes and missing share cards and
                         caps masters at 1600px. Needs Pillow ≥ 11.3.
.github/workflows/       Build, check and publish (see below).
package.json             Lint tools only (stylelint, eslint, html-validate).
```

`_site/` is generated and git-ignored. Never commit `index.html`, `2026/`,
`sitemap.xml` or other generated files at the repository root — the build
refuses to run if it finds them there.

## Publish flow

1. **Add the content.** Write the article body as
   `src/content/articles/<slug>.html` (plain `<p>` paragraphs; write a bare
   `&` as `&amp;`), add the image master to `images/`, and add one entry to
   the `articles` list in `src/manifest.json`. For a new edition, add an
   edition entry and point the homepage at it.
2. **Make the image sizes.** `python3 tools/make_images.py`
   (one-time: `pip install "Pillow>=11.3"`).
3. **Build and check.** `python3 build.py` (or `--check` to validate
   without writing). Preview with `python3 -m http.server -d _site`.
   Optional: `npm ci && npm run lint`.
4. **Push to `main`.** GitHub Actions rebuilds the site, runs every check
   and linter, and publishes only if all of them pass — usually within two
   minutes. A failed run leaves the live site untouched; the Actions tab
   shows why.
5. **Verify live.** Open the changed URLs on wirewillow.com.

Workflow rule: drafts are approved by David before anything is published.

## What the manifest holds (and what it must not)

Type each fact once. The build works out everything else and **fails if
any of these are typed by hand**:

- Editions: `label`, `number`, every formatted date (`date_long`,
  `date_short`, `date_upper`), `breadcrumb_name`, `ticker_label`,
  `ticker_aria`, `archive_eyebrow`, `footer_edition`, `lastmod`, and
  `head.canonical` / `og_type` / `og_image` / `og_image_alt` (an edition
  shares its lead story's card).
- Articles: `date_short`, `canonical`, `feed_pubdate`, `read_short`,
  `section`, `cat_label` (from `category`), `og_image_alt`, `og_type`.
  `head_description` defaults to the `dek`.
- The sitemap (generated from every page) and `homepage.lastmod`.

What you do type:

- **Edition:** `id`, `seq` (the edition number; order is by `seq`, never by
  date), `date` (the real publish day, `YYYY-MM-DD`), `url`, `head.title`,
  `head.description`, `lead_slug`, `article_slugs`, ticker, stats, briefs.
- **Edition URL rule:** `/YYYY/MM/DD/` for the first edition of a day;
  `/YYYY/MM/DD-morning/`, `-midday/`, `-afternoon/`, `-evening/` or
  `-night/` for later ones that day. The date in the URL must equal `date`.
- **Article:** `date` (`YYYY-MM-DD`), `published` and `modified` (ISO
  date-times with offset, e.g. `2026-11-24T09:16:00-08:00`). If `published`
  falls on a later day than `date`, the page says so in the byline.
- **Images not made by Wire Willow** need a `credit`:
  `{"kind": "photo", "text": "Author / Source", "license": "CC BY-SA 4.0",
  "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
  "changes": "cropped", "source_url": "<file page>"}` (`label` and
  `source_url` optional). Captions of Wire Willow illustrations start with
  `Illustration: Wire Willow.`
- **Moved pages:** add `{"from": "/old/", "to": "/new/"}` to `redirects`.
  The old URL gets a no-index redirect page.

## Checks (all run locally and in GitHub Actions)

`build.py` fails, writing nothing, if: a date is in the future or a URL
doesn't match its date; a date in an edition's title disagrees with the
edition; an edition lists an article from a later day; any internal link,
image or AVIF size is missing; an image master is over 1600px / 700 KB or
a share card isn't 1200×630, ≤ 300 KB and unique; a page lacks exactly one
`<h1>`, a title, canonical URL or description; heading levels skip; an
inline `style=` or leftover `{{placeholder}}` survives; a raw `&` appears in
an article body; the homepage isn't showing the newest edition; or the
sitemap and feed don't list exactly the site's pages.

`npm run lint` adds stylelint (CSS), eslint (JavaScript) and html-validate
(every generated page).

## Design notes

- **One stylesheet, one script.** Every page links the same
  `assets/css/style.css` and `assets/js/main.js`, cache-busted with a
  content hash (`style.css?v=<hash>`).
- **Content is rendered at build time.** Story cards and the headline
  ticker are plain HTML, so stories are visible to search engines and to
  readers with JavaScript off. The script only enhances.
- **Images.** `<picture>` with AVIF at 640/800/1024/1600px and the WebP
  master as the fallback, real `width`/`height`, lazy loading below the
  fold and `fetchpriority="high"` on the lead.
- **Real people.** Use real, licensed photos (with a `credit`) or
  illustrations that don't depict a real person's likeness — never a
  realistic generated likeness of a real person.
- **Structured data.** NewsArticle (with image credit and license),
  CollectionPage for editions, BreadcrumbList, WebSite and
  NewsMediaOrganization.
- **Newsletter.** `site.newsletter.action`: set it to the provider's form
  URL (Substack, Buttondown, Beehiiv…) and the form posts there. While it
  is empty the form opens a pre-filled signup email to `fallback_email`.
- **Accessibility.** Skip link, `:focus-visible` styles, a pause button on
  the ticker, WCAG AA colours, filter buttons only for desks with stories.
- **Light-only design** (`color-scheme: light`).
