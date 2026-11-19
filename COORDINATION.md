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
- Build: the site is built from src/ templates plus a manifest via build.py; shared stylesheet at assets/css/style.css and shared script; sitemap, feed, and archive are auto-generated; run the build checks before publishing.
- The dead reader-overlay CSS (.reader/.sheet rules) was removed Nov 18 — do not re-add it.
- Site is light-only, Mashable-style; story-card images 1200×630, unique per story.
- Newsletter: mailto fallback is live; David chose to retry Substack later (no Buttondown, no self-sign-in).
- David signs into GitHub with Google. No card numbers in chat.

## Current state (Nov 18, 2026)

- Live: Edition No. 004 at https://wirewillow.com.
- Pending: Edition No. 003 (Nov 14 draft) awaiting David's approve/edit/cut; Substack retry later; Wire Willow X/LinkedIn accounts unconfirmed.
- Draft scan runs every other day ~8:30 a.m. PT; drafts go to David for approve/edit/cut.

## Handoff log

- 2026-11-18 — Tars: created this file. Roles set by David: Tars writes the code and the articles; Claude checks everything.
