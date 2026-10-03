/* Suite account strip: injected on every page by shared/layout.py:apply_template().

   Shows the person signed in to the Invoice Hub's SIMULATED, browser-only sign-in: a greeting, their
   role, and an avatar menu (profile, invoice workspace, sign out). Signed out it shows a Sign in
   button. It reads sessionStorage written by apps/invoice_hub/site/streamlit-bridge.js, which the
   browser keeps while switching pages, so the same person follows you through every application.
   It restricts nothing: every application stays open whether or not anyone is signed in.

   Keep this file free of less-than signs: Streamlit's HTML sanitizer drops a script element whose
   text has one followed by a letter (even in a comment). */
(function () {
  'use strict';

  var SESSION_KEY = 'invoiceHub.session.v1';   // hub-access.js: the signed-in email
  var PROFILE_KEY = 'pp.suite.profile.v1';     // streamlit-bridge.js: name, role line, intro
  var HUB_PAGE_KEY = 'pp.suite.hubPage';       // read by streamlit-bridge.js: which hub page to open first
  var HUB_PATH = '/invoice-hub';

  // A rerun can re-mount the strip; bind the page-wide listeners only once.
  if (window.ppSuiteBanner) { window.ppSuiteBanner.render(); return; }

  function read(key) {
    try { return window.sessionStorage.getItem(key); } catch (e) { return null; }
  }

  function profile() {
    if (!read(SESSION_KEY)) return null;
    try { return JSON.parse(read(PROFILE_KEY) || 'null'); } catch (e) { return null; }
  }

  // Same rule as the Invoice Hub dashboard: US Central time; 1 AM-11:59 AM morning,
  // noon-5:59 PM afternoon, otherwise evening.
  var centralClock = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Chicago', hour: 'numeric', hourCycle: 'h23' });
  function greeting() {
    var hour = Number(centralClock.format(new Date()));
    if (hour >= 1 && 12 > hour) return 'Good morning';
    if (hour >= 12 && 18 > hour) return 'Good afternoon';
    return 'Good evening';
  }

  function setSlot(banner, name, text) {
    Array.prototype.forEach.call(banner.querySelectorAll('[data-slot="' + name + '"]'), function (node) {
      node.textContent = text;
    });
  }

  function initials(name) {
    var parts = String(name || '').split(/\s+/).filter(Boolean);
    if (!parts.length) return '?';
    return (parts[0].charAt(0) + (parts.length > 1 ? parts[parts.length - 1].charAt(0) : '')).toUpperCase();
  }

  // Keep the strip just left of Streamlit's own toolbar (Share, star, edit, menu) so it never covers it.
  // Use the leftmost control right of the navigation links; Share and star can mount after the strip does (re-placed every second below).
  function place(banner) {
    var left = Infinity;
    document.querySelectorAll('[data-testid="stToolbar"] button, [data-testid="stToolbar"] a')
      .forEach(function (el) {
        if (el.closest('[data-testid^="stTopNav"], [data-testid="stHeaderLogo"]')) return;
        var r = el.getBoundingClientRect();
        if (r.width > 0) left = Math.min(left, r.left);
      });
    var right = isFinite(left) ? window.innerWidth - left + 16 : 16;
    banner.style.setProperty('--pp-sb-right', Math.max(16, right) + 'px');
  }

  function closeMenu(banner) {
    var popover = banner.querySelector('.pp-sb-popover');
    var toggle = banner.querySelector('[data-action="menu"]');
    if (popover) popover.hidden = true;
    if (toggle) toggle.setAttribute('aria-expanded', 'false');
  }

  function render() {
    var banner = document.querySelector('.pp-suite-banner');
    if (!banner) return;
    place(banner);
    var person = profile();
    banner.setAttribute('data-state', person ? 'in' : 'out');
    if (!person) { closeMenu(banner); return; }
    setSlot(banner, 'greeting', greeting() + ', ' + String(person.name || '').split(/\s+/)[0]);
    setSlot(banner, 'eyebrow', person.eyebrow || '');
    setSlot(banner, 'name', person.name || '');
    setSlot(banner, 'email', person.email || '');
    setSlot(banner, 'initials', initials(person.name));
  }

  function hubFrames() {
    return Array.prototype.filter.call(document.querySelectorAll('iframe'), function (frame) {
      try { return /[.]html$/.test(frame.contentWindow.location.pathname); } catch (e) { return false; }
    });
  }

  function signOut() {
    try {
      window.sessionStorage.removeItem(SESSION_KEY);
      window.sessionStorage.removeItem(PROFILE_KEY);
    } catch (e) { /* ignore */ }
    render();
    // A hub frame on this page is still drawn signed in; reload it to show the sign-in panel.
    hubFrames().forEach(function (frame) {
      try { frame.contentWindow.location.reload(); } catch (e) { /* not a hub frame */ }
    });
  }

  function navLink(path) {
    return Array.prototype.find.call(document.querySelectorAll('a[href]'), function (a) {
      return !a.closest('.pp-suite-banner') && a.getAttribute('href').charAt(0) !== '#' &&
        new URL(a.href, location.href).pathname.replace(/\/$/, '') === path;
    });
  }

  // Streamlit moves pages that don't fit into a "more" menu, and renders their links only while it
  // is open. Open it to reach the link, so the visit keeps the session instead of reloading the page.
  function visit(path) {
    var link = navLink(path);
    var more = document.querySelector('[data-testid="stTopNavSection"]');
    if (!link && more) {
      more.click();
      setTimeout(function () {
        link = navLink(path);
        if (link) link.click(); else location.href = path;
      }, 250);
      return;
    }
    if (link) link.click(); else location.href = path;
  }

  function scrollToTop() {
    var main = document.querySelector('[data-testid="stMain"]');
    if (main) main.scrollTo({ top: 0, behavior: 'smooth' });
  }

  // Sign in / My invoice workspace: open the Invoice Lifecycle Hub on its dashboard tab.
  function openWorkspace() {
    var onHub = location.pathname.replace(/\/$/, '') === HUB_PATH;
    var frame = onHub ? hubFrames()[0] : null;
    if (frame) {
      // Already on the hub page: switch its frame to the dashboard (keeps the Streamlit session).
      try {
        var url = new URL(frame.contentWindow.location.href);
        url.pathname = url.pathname.replace(/[^/]*$/, 'dashboard.html');
        frame.contentWindow.location.href = url.href;
        scrollToTop();
        return;
      } catch (e) { /* fall through to a normal visit */ }
    }
    // Another page: the hub's first render opens the page named here (streamlit-bridge.js).
    try { window.sessionStorage.setItem(HUB_PAGE_KEY, 'dashboard.html'); } catch (e) { /* ignore */ }
    visit(HUB_PATH);
  }

  document.addEventListener('click', function (event) {
    var banner = document.querySelector('.pp-suite-banner');
    if (!banner) return;
    var button = event.target.closest && event.target.closest('.pp-suite-banner [data-action]');
    if (!button) { closeMenu(banner); return; }   // a click anywhere else closes the menu
    var action = button.getAttribute('data-action');
    if (action === 'menu') {
      var popover = banner.querySelector('.pp-sb-popover');
      var opening = popover.hidden;
      popover.hidden = !opening;
      button.setAttribute('aria-expanded', opening ? 'true' : 'false');
      return;
    }
    closeMenu(banner);
    if (action === 'signout') signOut(); else openWorkspace();
  });

  document.addEventListener('keydown', function (event) {
    var banner = document.querySelector('.pp-suite-banner');
    if (event.key === 'Escape' && banner) closeMenu(banner);
  });

  // Hub frames announce sign-in and sign-out; also refresh when the tab regains focus, when the
  // window resizes, and once a minute, so the greeting follows the clock.
  window.addEventListener('message', function (event) {
    if (event.origin === location.origin && event.data && event.data.type === 'pp-suite:session') render();
  });
  window.addEventListener('focus', render);
  window.addEventListener('resize', render);
  // Streamlit mounts and removes toolbar controls (Deploy, Share, star) on its own schedule: keep the strip clear of them.
  setInterval(function () {
    var banner = document.querySelector('.pp-suite-banner');
    if (banner) place(banner);
  }, 1000);
  setInterval(render, 60000);

  window.ppSuiteBanner = { render: render };
  render();
})();
