# Wire Willow — shared notes for Tars and Claude

## Who does what

- **Tars (Muse)** writes the code and the articles.
- **Claude** checks everything: reviews Tars's code changes and article drafts for accuracy, consistency, and quality before they go to David.
- **David** approves everything. Nothing publishes without his yes. If Tars and Claude disagree, David decides.

## Workflow

1. Tars drafts the articles and makes the site changes.
2. Tars triple-checks every article (see below).
3. Claude reviews the work — the code diff and the article drafts — and lists what it found: problems, fixes, or a clean bill.
4. Tars fixes what Claude found.
5. David approves, edits, or cuts each piece.
6. Tars publishes and confirms it live.

## Triple-check (Tars, every article, no exceptions)

- Pass 1 — Accuracy: every number, date, name, and quote traced to a cited source; single-source claims flagged; no claim carried over from an earlier edition without re-checking it against its source.
- Pass 2 — Consistency: no contradictions within the article or across the edition; dates, numbers, names, and titles match everywhere, including headlines and briefs; caveats from the sourcing stay in the draft.
- Pass 3 — AI slop: no filler, hype superlatives, or vague claims unless quoted and attributed; original wording throughout; no repeated phrasing or padded restatement.
- A draft is not ready if any pass finds an uncorrected problem. Keep a short check log with each draft naming what each pass caught or changed.

## Claude's review — what to check

- Articles: the same three passes (accuracy, consistency, AI slop), plus: does the draft match its sources, are the numbers and dates right, is anything invented or overstated?
- Code: does the change do what it claims, does it break any existing page, are links, images, sitemap, and feed still valid, do the build checks still pass?
- Report findings plainly: what is wrong, where, and what the fix is. If it is clean, say so.

## Standing rules

- Original wording only; cite sources; label proposals, betas, planned services, and allegations as such — never present plans as finished facts.
- Edition numbering: No. 003 is reserved for the unpublished Nov 14 draft; No. 004 published Nov 18 (drone lead, GPT-6.2, California v. OpenAI, SoftBank-ABB). The next new edition is No. 005 unless 003 publishes first.
- Build: the site is built from src/ templates plus a manifest via build.py into _site/ (git-ignored). GitHub Actions builds, checks and publishes on every push to main; nothing publishes if a check fails. Never commit generated files (index.html, 2026/, sitemap.xml, feed.xml) at the repo root — the build refuses them. See README "What the manifest holds" before editing the manifest.
- Dates: type each date once. Editions: `date` = the real publish day, `seq` = edition number. Labels, formatted dates, ticker labels, footer lines, canonicals, sitemap and feed dates are derived; typing them fails the build. Same-day editions after the first use /YYYY/MM/DD-morning|midday|afternoon|evening|night/. No future dates.
- Images: masters ≤1600px WebP; run tools/make_images.py for the AVIF sizes and share card. Share cards 1200×630, unique, ≤300 KB.
- Real people: never a realistic generated likeness of a real person. Use a real licensed photo with a `credit` (license link + changes), or an illustration that doesn't depict the person.
- The dead reader-overlay CSS (.reader/.sheet rules) was removed Nov 18 — do not re-add it.
- Site is light-only.
- Newsletter: mailto fallback is live; David chose to retry Substack later (no Buttondown, no self-sign-in).
- David signs into GitHub with Google. No card numbers in chat.

## Current state (Nov 25, 2026)

- Live: Edition No. 015 at https://wirewillow.com.
- Editions 010–012 were re-dated to Nov 23 (their real publish day); old /2026/11/24/ and /2026/11/26/ URLs redirect to /2026/11/23-morning/ and -night/. The /2026/11/25/ redirect (Edition 011's old address) was removed when Edition 015 took its real publish-day URL /2026/11/25/.
- Pending: Edition No. 003 (Nov 14 draft) awaiting David's approve/edit/cut; Substack retry later; Wire Willow X/LinkedIn accounts unconfirmed.
- Draft scan runs every other day ~8:30 a.m. PT; drafts go to David for approve/edit/cut.

## Handoff log

- 2026-11-18 — Tars: created this file. Roles set by David: Tars writes the code and the articles; Claude checks everything.
- 2026-11-24 — Claude: publishing moved to GitHub Actions (only _site/ is public); manifest date fields are now derived by build.py; editions 010–012 re-dated with redirects; images moved to AVIF + 1600px masters; Huang all-hands illustration replaced with the licensed photo; lint tools added (npm run lint).
- 2026-11-24 — Tars: Edition 014 lead photo swapped to the Stanford Apr 30, 2026 close-up (007) with structured credit; build OK, lint OK, Actions run passed, verified live.
- 2026-11-25 — Tars: Edition No. 015 published (9 stories + 2 briefs; lead: Microsoft Code Red Outlook outage). Briefs are text-only in this system — no brief images. The /2026/11/25/ re-dating redirect was removed so Edition 015 could take its real publish-day URL. Build OK, lint OK, Actions run #8 passed, verified live.
