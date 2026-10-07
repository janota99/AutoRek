/* Dashboard behaviour. Injected by apps/dashboard.py via st.html(unsafe_allow_javascript=True).
   Runs in the main Streamlit document. Everything degrades to plain links if it fails. */
(function () {
  var root = document.querySelector(".pp-dash");
  if (!root || root.dataset.bound) return;   // Streamlit reruns re-mount the element; bind once
  root.dataset.bound = "1";
  root.classList.add("pp-js");

  // Icons arrive as data because Streamlit strips inline SVG from the HTML block.
  root.querySelectorAll(".pp-icon[data-icon]").forEach(function (tile) {
    tile.innerHTML = (window.PP_ICONS || {})[tile.dataset.icon] || "";
  });

  // Dismissible notice; the choice is remembered for this browser tab only.
  var notice = root.querySelector(".pp-notice");
  var closeBtn = notice && notice.querySelector(".pp-notice-close");
  try { if (sessionStorage.getItem("pp-notice-dismissed")) notice.hidden = true; } catch (e) {}
  if (closeBtn) closeBtn.addEventListener("click", function () {
    notice.hidden = true;
    try { sessionStorage.setItem("pp-notice-dismissed", "1"); } catch (e) {}
  });

  var cards = Array.prototype.slice.call(root.querySelectorAll(".pp-card"));
  var reveal = Array.prototype.slice.call(root.querySelectorAll(".pp-hero, .pp-notice")).concat(cards);

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

  // Pointer spotlight.
  cards.forEach(function (card) {
    card.addEventListener("pointermove", function (ev) {
      var r = card.getBoundingClientRect();
      card.style.setProperty("--mx", (ev.clientX - r.left) + "px");
      card.style.setProperty("--my", (ev.clientY - r.top) + "px");
    });
  });

  // Hover preview: pointing at (or tabbing to) a card fills the preview bar with what that tool takes in
  // and produces, read from the card's data-facts JSON; leaving the card puts the hint back.
  var preview = root.querySelector(".pp-preview");
  var hint = preview ? preview.innerHTML : "";
  function group(label, items) {
    var box = document.createElement("span");
    box.className = "pp-preview-group";
    var strong = document.createElement("b");
    strong.textContent = label;
    box.appendChild(strong);
    box.appendChild(document.createTextNode(" " + items.join("  ·  ")));
    return box;
  }
  function showFacts(card) {
    if (!preview) return;
    var facts;
    try { facts = JSON.parse(card.dataset.facts); } catch (e) { return; }
    card.classList.add("pp-hover");
    preview.textContent = "";
    var title = group("Utility Name:", [facts.title]);
    title.classList.add("pp-preview-title");
    preview.appendChild(title);
    if (facts.inputs.length) preview.appendChild(group("Takes In:", facts.inputs));
    if (facts.outputs.length) preview.appendChild(group("Produces:", facts.outputs));
    preview.classList.add("pp-preview-active");
  }
  function clearFacts(card) {
    card.classList.remove("pp-hover");
    if (!preview) return;
    preview.innerHTML = hint;
    preview.classList.remove("pp-preview-active");
  }
  cards.forEach(function (card) {
    card.addEventListener("mouseenter", function () { showFacts(card); });
    card.addEventListener("mouseleave", function () { clearFacts(card); });
    card.addEventListener("focus", function () { showFacts(card); });
    card.addEventListener("blur", function () { clearFacts(card); });
  });

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
  // Clicking empty space on a card opens it too; its links, buttons, and details keep their own behaviour.
  cards.forEach(function (card) {
    card.addEventListener("click", function (ev) {
      if (plain(ev) || ev.target.closest("a, button, .pp-details")) return;
      if (!navTo(card.dataset.href)) location.href = card.dataset.href;
    });
  });

  // "View details" opens a card's drawer in place.
  root.querySelectorAll(".pp-card .pp-link").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var drawer = btn.closest(".pp-card").querySelector(".pp-details");
      var open = drawer.hidden;
      drawer.hidden = !open;
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      btn.textContent = open ? "Hide details" : "View details";
    });
  });

  // "Compare plans" presses the page's own View Plans button, which opens the pricing page.
  root.querySelectorAll("[data-open-plans]").forEach(function (b) {
    b.addEventListener("click", function () {
      var plans = document.querySelector(".st-key-pp-open-pricing button");
      if (plans) plans.click();
    });
  });

  // Sidebar: Overview and Applications scroll to their section.
  root.querySelectorAll(".pp-rail [data-scroll]").forEach(function (a) {
    a.addEventListener("click", function (ev) {
      var target = document.getElementById(a.dataset.scroll);
      if (!target) return;
      ev.preventDefault();
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  // Clicking the animated logo restarts its loop from the first frame.
  var logo = root.querySelector(".pp-logo");
  if (logo) {
    logo.addEventListener("click", function (ev) {
      ev.preventDefault();                         // replay only; do not open the app
      ev.stopPropagation();
      var parts = logo.querySelectorAll("svg, svg *");
      parts.forEach(function (p) { p.style.animation = "none"; });
      void logo.offsetWidth;                       // force reflow so the animation restarts
      parts.forEach(function (p) { p.style.animation = ""; });
    });
  }
})();
