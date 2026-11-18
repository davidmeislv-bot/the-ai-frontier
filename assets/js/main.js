/* Wire Willow front-page behavior.
 *
 * Story cards and the ticker are rendered as static HTML at build time;
 * this script only filters/searches them and handles the subscribe form.
 * It is safe to load on every page: each section checks its elements
 * exist before wiring anything up.
 */
(function () {
  "use strict";

  /* ---------- section filter + search (front pages only) ---------- */
  var grid = document.getElementById("cardGrid");
  var searchInput = document.getElementById("q");
  var countLabel = document.getElementById("countLabel");
  var lead = document.getElementById("leadSection");

  if (grid && searchInput && countLabel) {
    var currentFilter = "all";
    var updated = countLabel.getAttribute("data-updated") || "";

    function applyFilter() {
      var q = searchInput.value.trim().toLowerCase();
      var visible = 0;
      var cards = grid.querySelectorAll(".card");
      cards.forEach(function (c) {
        var okCat = currentFilter === "all" || c.dataset.cat === currentFilter;
        var okQ = !q || c.dataset.search.indexOf(q) !== -1;
        var show = okCat && okQ;
        c.classList.toggle("hidden", !show);
        if (show) visible++;
      });
      if (lead) {
        var leadSearch = (lead.getAttribute("data-search") || "").toLowerCase();
        var leadOk = (currentFilter === "all" || currentFilter === "ai") &&
          (!q || leadSearch.indexOf(q) !== -1);
        lead.style.display = leadOk ? "" : "none";
        if (leadOk) visible++;
      }
      var old = grid.querySelector(".noresults");
      if (old) old.remove();
      if (visible === 0) {
        var div = document.createElement("div");
        div.className = "noresults";
        div.textContent = "Nothing in today’s edition matches that — try another section or search.";
        grid.appendChild(div);
      }
      countLabel.textContent = visible + (visible === 1 ? " STORY" : " STORIES") +
        " · UPDATED " + updated;
    }

    document.querySelectorAll(".fbtn").forEach(function (b) {
      b.addEventListener("click", function () {
        document.querySelectorAll(".fbtn").forEach(function (x) { x.setAttribute("aria-pressed", "false"); });
        b.setAttribute("aria-pressed", "true");
        currentFilter = b.dataset.filter;
        applyFilter();
      });
    });
    searchInput.addEventListener("input", applyFilter);
  }

  /* ---------- subscribe via mailto (no fake signup) ---------- */
  var subForm = document.getElementById("subForm");
  if (subForm) {
    subForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var email = document.getElementById("subEmail").value.trim();
      if (!email) return;
      var subject = encodeURIComponent("Sign me up for Wire Willow daily edition");
      var body = encodeURIComponent("Hi — please add this email to the Wire Willow daily edition list:\n\n" + email + "\n\nThanks!");
      var listOwner = ["davidmeislv", "gmail.com"].join("@");
      window.location.href = "mailto:" + listOwner + "?subject=" + subject + "&body=" + body;
    });
  }
})();
