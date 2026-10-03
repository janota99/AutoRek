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
 * Opened on its own (not inside Streamlit) it does nothing.
 */
(function () {
  'use strict';

  if (window.parent === window) return;

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
