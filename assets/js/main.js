/* Wire Willow — site behavior.
 *
 * Loaded on every page as a module (deferred, strict mode by default).
 * Story cards and the ticker are static HTML from build.py; this script
 * only enhances them. Each feature checks its elements exist first, and
 * every page works without JavaScript.
 *
 *   1. Section filter + search (front pages)
 *   2. Ticker pause control (front pages)
 *   3. Newsletter form mailto fallback (all pages)
 */

/* ---------- 1. Section filter + search ---------- */
function initFilter() {
  const grid = document.getElementById("cardGrid");
  const search = document.getElementById("q");
  const countLabel = document.getElementById("countLabel");
  if (!grid || !search || !countLabel) return;

  const lead = document.getElementById("leadSection");
  const buttons = [...document.querySelectorAll(".fbtn")];
  const cards = [...grid.querySelectorAll(".card")];
  const emptyState = grid.querySelector(".noresults");
  const updated = countLabel.dataset.updated || "";
  let filter = "all";

  const matches = (el, query) =>
    (filter === "all" || el.dataset.cat === filter) &&
    (!query || (el.dataset.search || "").includes(query));

  function apply() {
    const query = search.value.trim().toLowerCase();
    let visible = 0;
    for (const el of lead ? [lead, ...cards] : cards) {
      const show = matches(el, query);
      el.hidden = !show;
      if (show) visible++;
    }
    if (emptyState) emptyState.hidden = visible > 0;
    countLabel.textContent =
      `${visible} ${visible === 1 ? "story" : "stories"} · Updated ${updated}`;
  }

  for (const button of buttons) {
    button.addEventListener("click", () => {
      for (const b of buttons) b.setAttribute("aria-pressed", String(b === button));
      filter = button.dataset.filter;
      apply();
    });
  }
  search.addEventListener("input", apply);
}

/* ---------- 2. Ticker pause control (WCAG 2.2.2) ---------- */
function initTicker() {
  const ticker = document.querySelector(".ticker");
  const toggle = ticker && ticker.querySelector(".ticker-toggle");
  if (!toggle) return;
  toggle.hidden = false;
  toggle.addEventListener("click", () => {
    const paused = ticker.classList.toggle("is-paused");
    toggle.setAttribute("aria-pressed", String(paused));
    toggle.textContent = paused ? "Play" : "Pause";
  });
}

/* ---------- 3. Newsletter form ----------
 * When site.newsletter.action is set in the manifest the form posts to the
 * newsletter provider and this does nothing. Otherwise it opens a
 * pre-filled signup email the reader sends themselves. */
function initSubscribe() {
  const form = document.getElementById("subForm");
  if (!form || form.getAttribute("action")) return;
  const { mailtoUser, mailtoDomain } = form.dataset;
  if (!mailtoUser || !mailtoDomain) return;

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const email = form.elements.email.value.trim();
    if (!email) return;
    const subject = encodeURIComponent("Sign me up for Wire Willow daily edition");
    const body = encodeURIComponent(
      `Hi — please add this email to the Wire Willow daily edition list:\n\n${email}\n\nThanks!`
    );
    window.location.href = `mailto:${mailtoUser}@${mailtoDomain}?subject=${subject}&body=${body}`;
  });
}

initFilter();
initTicker();
initSubscribe();
