/* Dashboard behaviour. Injected by apps/dashboard.py via st.html(unsafe_allow_javascript=True).
   Runs in the main Streamlit document. Everything degrades to plain links if it fails.
   Never write a less-than sign followed by a letter in this file (not even in a string): Streamlit's sanitizer
   drops the whole script. Build elements with createElement. */
(function () {
  var root = document.querySelector(".pp-dash");
  if (!root || root.dataset.bound) return;   // Streamlit reruns re-mount the element; bind once
  root.dataset.bound = "1";
  root.classList.add("pp-js");

  // Icons arrive as data because Streamlit strips inline SVG from the HTML block.
  root.querySelectorAll(".pp-icon[data-icon]").forEach(function (tile) {
    tile.innerHTML = (window.PP_ICONS || {})[tile.dataset.icon] || "";
  });

  // Dismissible onboarding bar; the choice is remembered for this browser tab only.
  var bar = root.querySelector(".pp-onboard");
  var closeBtn = bar && bar.querySelector(".pp-notice-close");
  try { if (sessionStorage.getItem("pp-onboard-dismissed")) bar.hidden = true; } catch (e) {}
  if (closeBtn) closeBtn.addEventListener("click", function () {
    bar.hidden = true;
    try { sessionStorage.setItem("pp-onboard-dismissed", "1"); } catch (e) {}
  });

  var cards = Array.prototype.slice.call(root.querySelectorAll(".pp-card"));
  var reveal = Array.prototype.slice.call(root.querySelectorAll(".pp-hero, .pp-onboard")).concat(cards);

  // Staggered entrance as items scroll into view.
  var show = function (el, i) { setTimeout(function () { el.classList.add("pp-in"); }, i * 90); };
  if ("IntersectionObserver" in window) {
    var seen = 0;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { show(e.target, seen++); io.unobserve(e.target); }
      });
    }, { threshold: 0.1 });
    reveal.forEach(function (el) { io.observe(el); });
  } else {
    reveal.forEach(show);
  }

  // Open an app through Streamlit's own top navigation link so the session is kept and the page
  // does not reload. If that link cannot be found, the href navigates normally.
  function navTo(path) {
    path = path.replace(/\/$/, "");
    var link = Array.prototype.slice.call(document.querySelectorAll("a[href]")).find(function (a) {
      return !root.contains(a) && new URL(a.href, location.href).pathname.replace(/\/$/, "") === path;
    });
    if (link) { link.click(); return true; }
    return false;
  }
  function plain(ev) { return ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey; }
  root.querySelectorAll("a[data-route]").forEach(function (a) {
    a.addEventListener("click", function (ev) {
      if (plain(ev)) return;
      if (navTo(new URL(a.href, location.href).pathname)) ev.preventDefault();
    });
  });
  // The card's primary action is stretched over the whole card in CSS, so a tap anywhere opens it; this keeps
  // that working through Streamlit's own navigation when the click lands on the card itself.
  cards.forEach(function (card) {
    card.addEventListener("click", function (ev) {
      if (plain(ev) || ev.target.closest("a, button")) return;
      if (!navTo(card.dataset.href)) location.href = card.dataset.href;
    });
  });

  // Slide-over drawer: one pane per module, plus the sample templates and the data-governance panes.
  var drawer = root.querySelector(".pp-drawer");
  var scrim = root.querySelector(".pp-scrim");
  var opener = null;
  function openPane(name, from) {
    var found = false;
    drawer.querySelectorAll(".pp-pane").forEach(function (p) {
      var on = p.dataset.pane === name;
      p.hidden = !on;
      found = found || on;
    });
    if (!found) return;
    opener = from || null;
    drawer.hidden = false;
    scrim.hidden = false;
    drawer.querySelector(".pp-drawer-body").scrollTop = 0;
    requestAnimationFrame(function () { root.classList.add("pp-drawer-open"); drawer.focus(); });
  }
  function closePane() {
    root.classList.remove("pp-drawer-open");
    setTimeout(function () { drawer.hidden = true; scrim.hidden = true; }, 220);
    if (opener && opener.focus) opener.focus();
  }
  root.querySelectorAll("[data-open-pane]").forEach(function (b) {
    b.addEventListener("click", function () { openPane(b.dataset.openPane, b); });
  });
  drawer.querySelector(".pp-drawer-close").addEventListener("click", closePane);
  scrim.addEventListener("click", closePane);
  drawer.querySelectorAll("a[data-route]").forEach(function (a) {
    a.addEventListener("click", function () { root.classList.remove("pp-drawer-open"); drawer.hidden = true; scrim.hidden = true; });
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && !drawer.hidden) closePane();
  });

  // Sample templates: the files travel as base64 (a data: link is stripped from the page); a click builds the download.
  drawer.querySelectorAll(".pp-tpl").forEach(function (b) {
    b.addEventListener("click", function () {
      var parts = b.dataset.tpl.split(":");
      var item = ((window.PP_TEMPLATES || {})[parts[0]] || [])[Number(parts[1])];
      if (!item) return;
      var raw = atob(item.b64), bytes = new Uint8Array(raw.length);
      for (var i = raw.length; i--;) bytes[i] = raw.charCodeAt(i);
      var url = URL.createObjectURL(new Blob([bytes], { type: item.name.slice(-4) === ".zip" ? "application/zip" :
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }));
      var a = document.createElement("a");
      a.href = url; a.download = item.name; document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    });
  });

  // "Upgrade to Pro" presses the page's own View Plans button, which opens the pricing page.
  root.querySelectorAll("[data-open-plans]").forEach(function (b) {
    b.addEventListener("click", function () {
      var plans = document.querySelector(".st-key-pp-open-pricing button");
      if (plans) plans.click();
    });
  });

  // Interactive tour: three highlighted stops, each with a short note. Esc or "Skip" ends it.
  var STOPS = [
    { find: function () { return document.querySelector(".st-key-pp-fiscal"); },
      title: "Set the fiscal period once",
      text: "Pick a period end date here and it fills the fiscal period in the tools that take one." },
    { find: function () { return root.querySelector(".pp-card .pp-ghost"); },
      title: "Try a demo",
      text: "Each card opens with sample data already loaded, so no company files are needed." },
    { find: function () { return root.querySelector(".pp-card .pp-link"); },
      title: "View details",
      text: "See each tool's workflow and column layout, and download a sample template." }
  ];
  var tour = document.createElement("div");
  tour.className = "pp-tour";
  tour.hidden = true;
  function part(tag, cls, parent) { var n = document.createElement(tag); if (cls) n.className = cls; parent.appendChild(n); return n; }
  var tStep = part("p", "pp-tour-step", tour), tTitle = part("h4", "", tour), tText = part("p", "pp-tour-text", tour);
  var tActs = part("div", "pp-tour-actions", tour);
  var tSkip = part("button", "pp-link", tActs); tSkip.type = "button"; tSkip.textContent = "Skip";
  var tNext = part("button", "pp-action pp-tour-next", tActs); tNext.type = "button";
  root.appendChild(tour);
  var at = 0, lit = null;
  function unlight() { if (lit) { lit.classList.remove("pp-tour-target"); lit = null; } }
  function endTour() { unlight(); tour.hidden = true; }
  function showStop() {
    unlight();
    var stop = STOPS[at], el = stop.find();
    if (el) { lit = el; el.classList.add("pp-tour-target"); el.scrollIntoView({ block: "center", behavior: "smooth" }); }
    tStep.textContent = "Step " + (at + 1) + " of " + STOPS.length;
    tTitle.textContent = stop.title;
    tText.textContent = stop.text;
    tNext.textContent = at === STOPS.length - 1 ? "Done" : "Next";
    tour.hidden = false;
  }
  tSkip.addEventListener("click", endTour);
  tNext.addEventListener("click", function () {
    if (at >= STOPS.length - 1) return endTour();
    at += 1; showStop();
  });
  document.addEventListener("keydown", function (ev) { if (ev.key === "Escape" && !tour.hidden) endTour(); });
  root.querySelectorAll("[data-tour]").forEach(function (b) {
    b.addEventListener("click", function () { at = 0; showStop(); });
  });
})();
