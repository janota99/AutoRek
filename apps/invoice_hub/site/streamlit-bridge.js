/*
 * Lets this site run inside the combined Streamlit app, which serves this folder as a
 * Streamlit component (see ../component.py). Each page tells Streamlit it is ready and sizes its
 * frame to fill the browser window, so the site scrolls inside the frame just as it would in
 * its own tab (modal dialogs and #links then land where they should).
 *
 * Opened on its own (not inside Streamlit) it does nothing.
 */
(function () {
  'use strict';

  if (window.parent === window) return;

  var TOP_OFFSET = 120;  // Streamlit's top navigation bar plus page padding
  var MIN_HEIGHT = 560;

  function send(type, extra) {
    var message = { isStreamlitMessage: true, type: type };
    Object.keys(extra || {}).forEach(function (k) { message[k] = extra[k]; });
    window.parent.postMessage(message, '*');
  }

  function fitToWindow() {
    var available;
    try {
      available = window.parent.innerHeight - TOP_OFFSET;
    } catch (e) {
      available = 900;  // parent not readable; fall back to a sensible fixed height
    }
    send('streamlit:setFrameHeight', { height: Math.max(MIN_HEIGHT, available) });
  }

  send('streamlit:componentReady', { apiVersion: 1 });
  fitToWindow();
  try {
    window.parent.addEventListener('resize', fitToWindow);
    window.addEventListener('pagehide', function () {
      window.parent.removeEventListener('resize', fitToWindow);
    });
  } catch (e) { /* parent not readable; keep the first height */ }
})();
