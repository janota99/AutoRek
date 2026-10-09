/* Landing page behaviour. Injected by apps/landing.py via st.html(unsafe_allow_javascript=True).
   Handlers are delegated from the document and bound once, because the page is several HTML blocks and
   Streamlit re-runs this script on every interaction. No "<" may be followed by a letter in this file:
   Streamlit's sanitizer would drop the whole script. */
(function () {
  if (window.__ppLanding) return;
  window.__ppLanding = true;

  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function byId(id) { return document.getElementById(id); }
  function activePanel() { return document.querySelector(".pp-l-panel:not([hidden])"); }

  function scrollToId(id) {
    var el = byId(id);
    if (el) el.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
  }

  // Count a KPI up from zero to its final text. Whole numbers and dollar amounts animate; anything else just appears.
  function countUp(el) {
    var final = el.getAttribute("data-final") || el.textContent;
    var m = /^(\$?)([\d,]+)(\.\d+)?$/.exec(final);
    if (!m || reduced) { el.textContent = final; return; }
    var dollars = m[1] === "$";
    var target = parseFloat(m[2].replace(/,/g, "") + (m[3] || ""));
    var places = m[3] ? m[3].length - 1 : 0;
    var start = null;
    function frame(t) {
      if (start === null) start = t;
      var p = Math.min(1, (t - start) / 700);
      var v = target * (1 - Math.pow(1 - p, 3));
      var text = v.toLocaleString("en-US", { minimumFractionDigits: places, maximumFractionDigits: places });
      el.textContent = (dollars ? "$" : "") + (p === 1 ? final.replace("$", "") : text);
      if (p === 1 && dollars) el.textContent = final;
      if (p !== 1) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
    // A background or throttled tab may never run the frames; the final value must still land.
    setTimeout(function () { el.textContent = final; }, 900);
  }

  function rerun(panel) {
    if (!panel) return;
    panel.setAttribute("data-mode", "results");
    showMode(panel, "results");
    panel.classList.remove("pp-l-run");
    void panel.offsetWidth;                 // restart the CSS animation
    panel.classList.add("pp-l-run");
    panel.querySelectorAll(".pp-l-kpi-v").forEach(countUp);
  }

  function showMode(panel, mode) {
    panel.setAttribute("data-mode", mode);
    panel.querySelectorAll(".pp-l-view").forEach(function (v) { v.hidden = v.getAttribute("data-view") !== mode; });
    panel.querySelectorAll(".pp-l-seg[data-mode]").forEach(function (b) {
      b.setAttribute("aria-pressed", b.getAttribute("data-mode") === mode ? "true" : "false");
    });
  }

  function selectTab(tab) {
    var id = tab.getAttribute("data-tab");
    document.querySelectorAll(".pp-l-tab").forEach(function (t) {
      var on = t === tab;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
    });
    document.querySelectorAll(".pp-l-panel").forEach(function (p) { p.hidden = p.id !== "pp-l-panel-" + id; });
    rerun(byId("pp-l-panel-" + id));
  }

  function signIn() {
    var btn = document.querySelector(".pp-sb-signin") || document.querySelector(".pp-sb-account");
    if (btn) btn.click();
  }

  document.addEventListener("click", function (ev) {
    var t = ev.target;
    if (!t || !t.closest) return;
    var scroll = t.closest("[data-scroll]");
    if (scroll) { ev.preventDefault(); scrollToId(scroll.getAttribute("data-scroll")); return; }
    var tab = t.closest(".pp-l-tab");
    if (tab) { selectTab(tab); return; }
    var seg = t.closest(".pp-l-seg[data-mode]");
    if (seg) { showMode(seg.closest(".pp-l-panel"), seg.getAttribute("data-mode")); return; }
    if (t.closest("[data-rerun]")) { rerun(t.closest(".pp-l-panel")); return; }
    if (t.closest("[data-demo]")) { scrollToId("pp-l-solutions"); setTimeout(function () { rerun(activePanel()); }, reduced ? 0 : 450); return; }
    if (t.closest("[data-signin]")) { signIn(); return; }
    var route = t.closest("a[data-route]");
    if (route && ev.button === 0 && !ev.metaKey && !ev.ctrlKey && !ev.shiftKey) {
      var path = new URL(route.href, location.href).pathname.replace(/\/$/, "");
      var link = Array.prototype.slice.call(document.querySelectorAll("a[href]")).find(function (a) {
        return !route.contains(a) && !a.closest(".pp-land") && new URL(a.href, location.href).pathname.replace(/\/$/, "") === path;
      });
      if (link) { ev.preventDefault(); link.click(); }
    }
  });

  // Arrow keys move between tabs, as a tab list should.
  document.addEventListener("keydown", function (ev) {
    var tab = ev.target && ev.target.closest && ev.target.closest(".pp-l-tab");
    if (!tab || (ev.key !== "ArrowRight" && ev.key !== "ArrowLeft")) return;
    var tabs = Array.prototype.slice.call(document.querySelectorAll(".pp-l-tab"));
    var next = tabs[(tabs.indexOf(tab) + (ev.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
    selectTab(next);
    next.focus();
  });

  // Scroll reveal: elements marked data-reveal fade up once as they enter the screen; figures marked data-count count up.
  // Streamlit may replace the markup, so unseen elements are picked up again every half second.
  if ("IntersectionObserver" in window && !reduced) {
    document.documentElement.classList.add("pp-js");
    var reveal = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        reveal.unobserve(e.target);
        e.target.classList.add("pp-in");
        e.target.querySelectorAll("[data-count]").forEach(countUp);
        if (e.target.hasAttribute("data-count")) countUp(e.target);
      });
    }, { threshold: 0.15 });
    setInterval(function () {
      document.querySelectorAll("[data-reveal]:not([data-seen])").forEach(function (el) {
        el.setAttribute("data-seen", "1");
        reveal.observe(el);
      });
    }, 500);
  }

  // Entrance: the first panel plays once when it scrolls into view.
  if ("IntersectionObserver" in window) {
    var seen = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { seen.disconnect(); rerun(activePanel()); }
      });
    }, { threshold: 0.25 });
    var start = byId("pp-l-solutions");
    if (start) seen.observe(start);
  }
})();
