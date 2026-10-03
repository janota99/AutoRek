/*
 * Lets this site run inside the combined Streamlit app, which serves this folder as a
 * Streamlit component (see ../component.py). Each page tells Streamlit it is ready and sizes its
 * frame to the page's full content height, so the Streamlit page scrolls the hub like any other
 * app page instead of hiding most of it inside a small scrolling box.
 *
 * Because the frame is as tall as the page, two things that normally use the frame's own
 * viewport are redirected to the part of the frame the visitor can actually see:
 *   - modal <dialog>s are pinned to the visible area (and follow it while the page scrolls);
 *   - #anchor links scroll the Streamlit page to their target.
 * Moving to another hub page brings the top of the frame back into view.
 *
 * It also runs "suite mode": the Invoice Hub page holds the Service and My Dashboard tabs, the
 * Reviews & Feedback page is its own page in the suite's navigation bar, and the simulated sign-in is
 * shared with the suite's account strip through sessionStorage (see the Suite mode section below).
 *
 * Opened on its own (not inside Streamlit) it does nothing.
 */
(function () {
  'use strict';

  if (window.parent === window) return;

  // ---- Suite mode -------------------------------------------------------------------------------
  // The Invoice Lifecycle Hub page is index.html (Service) and dashboard.html (My Dashboard) with a
  // tab bar between them. feedback.html is the suite's Reviews & Feedback page, opened there with
  // invoice_hub(page="feedback.html"). styles.css keys off these classes ("in-suite" everywhere,
  // plus "workspace" on dashboard.html and feedback.html).
  var WORKSPACE_PAGES = ['dashboard.html', 'feedback.html'];
  var SESSION_KEY = 'invoiceHub.session.v1';  // hub-access.js's signed-in email (sessionStorage)
  var PROFILE_KEY = 'pp.suite.profile.v1';    // read by the suite account strip (shared/suite_banner.js)
  var HUB_PAGE_KEY = 'pp.suite.hubPage';      // set by the account strip: open the hub on this page
  var page = location.pathname.split('/').pop() || 'index.html';
  var root = document.documentElement;
  root.classList.add('in-suite');
  if (WORKSPACE_PAGES.indexOf(page) !== -1) root.classList.add('workspace');

  // The component always opens index.html. On the first render Streamlit sends the page's
  // arguments; a `page` argument naming a hub page opens that page instead, and so does the one
  // page the account strip asked for (consumed here, so it applies once). index.html stays
  // invisible until then so it doesn't flash. Later renders (every Streamlit rerun) are ignored,
  // so moving between hub pages isn't undone.
  if (page === 'index.html') {
    root.style.visibility = 'hidden';
    var reveal = function () { root.style.visibility = ''; };
    var revealFallback = setTimeout(reveal, 1500);
    window.addEventListener('message', function onFirstRender(event) {
      var data = event.data || {};
      if (data.type !== 'streamlit:render') return;
      window.removeEventListener('message', onFirstRender);
      clearTimeout(revealFallback);
      var target = data.args && data.args.page;
      if (!target) {
        try {
          target = window.sessionStorage.getItem(HUB_PAGE_KEY);
          window.sessionStorage.removeItem(HUB_PAGE_KEY);
        } catch (e) { /* storage unavailable */ }
      }
      if (target === 'dashboard.html' || target === 'feedback.html') location.replace(target + location.search);
      else reveal();
    });
  }

  // My Dashboard, signed out: put the cursor in the email box so the sign-in prompt is obvious.
  if (page === 'dashboard.html') {
    window.addEventListener('load', function () {
      var panel = document.getElementById('signInView');
      var email = document.getElementById('signInEmail');
      if (panel && !panel.hidden && email) email.focus({ preventScroll: true });
    });
  }

  // Reviews & Feedback: start the form with the signed-in person's name and role.
  if (page === 'feedback.html') {
    window.addEventListener('DOMContentLoaded', function () {
      var person = null;
      try { person = readSession(SESSION_KEY) && JSON.parse(readSession(PROFILE_KEY) || 'null'); } catch (e) { /* ignore */ }
      if (!person) return;
      var name = document.getElementById('customerName');
      var role = document.getElementById('department');
      if (name && !name.value) name.value = person.name || '';
      if (role && !role.value) role.value = person.eyebrow || '';
    });
  }

  function readSession(key) {
    try { return window.sessionStorage.getItem(key); } catch (e) { return null; }
  }

  // Keep the suite account strip's copy of the signed-in person current, and tell the Streamlit page.
  function syncProfile() {
    var Hub = window.InvoiceHub;
    if (!Hub || !Hub.Access) return;
    var user = Hub.Access.currentUser();
    try {
      if (user) {
        var role = (Hub.ROLES && Hub.ROLES[user.role]) || {};
        var intro = document.getElementById('dashboardIntro');
        window.sessionStorage.setItem(PROFILE_KEY, JSON.stringify({
          name: user.name,
          email: user.email,
          eyebrow: (role.label || '') + (user.department ? ' · ' + user.department : ''),
          intro: intro ? intro.textContent : ''
        }));
      } else {
        window.sessionStorage.removeItem(PROFILE_KEY);
      }
    } catch (e) { /* storage unavailable: the strip just shows Sign in */ }
    try { window.parent.postMessage({ type: 'pp-suite:session' }, location.origin); } catch (e) { /* ignore */ }
  }

  // hub-access.js loads deferred, after this script, so hook it once the page has parsed.
  document.addEventListener('DOMContentLoaded', function () {
    var Hub = window.InvoiceHub;
    if (!Hub || !Hub.Access) return;
    ['signIn', 'signOut'].forEach(function (name) {
      var original = Hub.Access[name];
      Hub.Access[name] = function () {
        var result = original.apply(this, arguments);
        setTimeout(syncProfile, 0);  // after the dashboard has drawn, so its intro line is current
        return result;
      };
    });
    syncProfile();
  });
  window.addEventListener('load', syncProfile);  // the intro line can change once sample mail loads

  // ---- Frame sizing -----------------------------------------------------------------------------
  var MIN_HEIGHT = 400;
  var DIALOG_GAP = 16;  // space kept between a dialog and the edge of the visible area
  var lastHeight = 0;

  function send(type, extra) {
    var message = { isStreamlitMessage: true, type: type };
    Object.keys(extra || {}).forEach(function (k) { message[k] = extra[k]; });
    window.parent.postMessage(message, '*');
  }

  // The root element's box is exactly as tall as the content (body margins included). Unlike
  // scrollHeight it is never padded out to the frame's current height, so the frame can shrink
  // again when the content gets shorter.
  function fitToContent() {
    var height = Math.max(MIN_HEIGHT, Math.ceil(document.documentElement.getBoundingClientRect().height));
    if (height === lastHeight) return;
    lastHeight = height;
    send('streamlit:setFrameHeight', { height: height });
  }

  send('streamlit:componentReady', { apiVersion: 1 });
  fitToContent();
  if (typeof ResizeObserver === 'function') {
    new ResizeObserver(fitToContent).observe(document.documentElement);
  }
  window.addEventListener('load', fitToContent);  // late images and fonts

  // Everything below needs to read the Streamlit page (same origin when served by Streamlit).
  var frame, parentWin;
  try {
    frame = window.frameElement;
    parentWin = window.parent;
    if (!frame || !parentWin.document) return;
  } catch (e) {
    return;  // parent not readable: the frame still fits its content, dialogs just use the default placement
  }

  // Streamlit's top bar floats over the top of the page, so anything under it is hidden.
  function coveredTop() {
    var header = parentWin.document.querySelector('[data-testid="stHeader"]');
    return header ? Math.max(0, header.getBoundingClientRect().bottom) : 0;
  }

  // The element that scrolls the Streamlit page (an inner container, not the window).
  function pageScroller() {
    for (var el = frame.parentElement; el; el = el.parentElement) {
      if (/(auto|scroll)/.test(parentWin.getComputedStyle(el).overflowY) && el.scrollHeight > el.clientHeight) {
        return el;
      }
    }
    return parentWin.document.scrollingElement;
  }

  // Scroll the Streamlit page so `y` (in this document's coordinates) sits just below the top bar.
  function scrollPageTo(y, behavior) {
    var delta = frame.getBoundingClientRect().top + y - coveredTop();
    pageScroller().scrollBy({ top: delta, behavior: behavior || 'auto' });
  }

  // The slice of this frame that is on screen, in this document's coordinates.
  function visibleArea() {
    var rect = frame.getBoundingClientRect();
    var top = Math.max(0, coveredTop() - rect.top);
    var bottom = Math.min(rect.height, parentWin.innerHeight - rect.top);
    return { top: top, height: Math.max(0, bottom - top) };
  }

  // A new hub page (dashboard.html, feedback.html, ...) starts at the top of the frame.
  if (frame.getBoundingClientRect().top < coveredTop()) scrollPageTo(0);

  // #links: there is no inner scrolling, so scroll the Streamlit page to the target instead.
  document.addEventListener('click', function (event) {
    var link = event.target.closest && event.target.closest('a[href^="#"]');
    if (!link || event.defaultPrevented) return;
    var id = decodeURIComponent(link.getAttribute('href').slice(1));
    var target = id && document.getElementById(id);
    if (!target) return;
    event.preventDefault();
    scrollPageTo(target.getBoundingClientRect().top + window.scrollY, 'smooth');
  });

  // Modal dialogs: pin each open one to the visible area instead of the middle of a tall frame.
  var openDialogs = [];

  function placeDialog(dialog) {
    var area = visibleArea();
    dialog.style.position = 'absolute';
    dialog.style.top = (area.top + DIALOG_GAP) + 'px';
    dialog.style.bottom = 'auto';
    dialog.style.margin = '0 auto';
    dialog.style.maxHeight = Math.max(240, area.height - 2 * DIALOG_GAP) + 'px';
  }

  function placeOpenDialogs() { openDialogs.forEach(placeDialog); }

  function releaseDialog(dialog) {
    ['position', 'top', 'bottom', 'margin', 'maxHeight'].forEach(function (p) { dialog.style[p] = ''; });
    openDialogs = openDialogs.filter(function (d) { return d !== dialog; });
  }

  if (window.HTMLDialogElement && HTMLDialogElement.prototype.showModal) {
    var showModal = HTMLDialogElement.prototype.showModal;
    HTMLDialogElement.prototype.showModal = function () {
      placeDialog(this);  // before opening, so focus doesn't scroll to a dialog far down the frame
      showModal.apply(this, arguments);
      if (openDialogs.indexOf(this) === -1) {
        openDialogs.push(this);
        this.addEventListener('close', function onClose() {
          this.removeEventListener('close', onClose);
          releaseDialog(this);
        });
      }
    };
    // Streamlit scrolls an inner container, not its window; capture catches either.
    parentWin.document.addEventListener('scroll', placeOpenDialogs, true);
    parentWin.addEventListener('resize', placeOpenDialogs);
    window.addEventListener('pagehide', function () {
      parentWin.document.removeEventListener('scroll', placeOpenDialogs, true);
      parentWin.removeEventListener('resize', placeOpenDialogs);
    });
  }
})();
