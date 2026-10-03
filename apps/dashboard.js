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

  // Open an app through Streamlit's own top navigation link so the session is kept and the page
  // does not reload. If that link cannot be found, the card's href navigates normally.
  cards.forEach(function (card) {
    card.addEventListener("click", function (ev) {
      if (ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey) return;
      var path = new URL(card.href, location.href).pathname.replace(/\/$/, "");
      var link = Array.prototype.slice.call(document.querySelectorAll("a[href]")).find(function (a) {
        return !root.contains(a) && new URL(a.href, location.href).pathname.replace(/\/$/, "") === path;
      });
      if (link) { ev.preventDefault(); link.click(); }
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
