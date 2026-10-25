/* Sales Tax Calculator, in the browser (injected by tax_calculator_ui.py).
   1. Live price: a moment after each keystroke in the "Price (pre-tax)" field it sends the Enter key the field already
      understands, so Streamlit recalculates without the user pressing Enter. The number is still computed in Python.
   2. Copy snip: the button carries the tape picture; a click puts it on the clipboard as an image to paste.
   Keep this file free of the less-than character: the page sanitizer drops a script that contains one. */
(function () {
  if (window.__stcLive) return;
  window.__stcLive = true;
  var LABEL = "Price (pre-tax)";

  function priceInput() { return document.querySelector('input[aria-label="' + LABEL + '"]'); }
  function snipButton() { return document.querySelector("[data-stc-snip]"); }

  function commit(el) {
    ["keydown", "keypress", "keyup"].forEach(function (type) {
      el.dispatchEvent(new KeyboardEvent(type, { key: "Enter", code: "Enter", keyCode: 13, which: 13, charCode: 13, bubbles: true }));
    });
  }

  var timer = null;
  document.addEventListener("input", function (ev) {
    var el = ev.target;
    if (!el || !el.getAttribute || el.getAttribute("aria-label") !== LABEL) return;
    clearTimeout(timer);
    timer = setTimeout(function () { commit(el); }, 250);
  }, true);

  function say(text) {
    var btn = snipButton();
    var note = btn && btn.parentElement.querySelector("[data-stc-status]");
    if (note) note.textContent = text;
  }

  function waitFor(test, ms) {
    return new Promise(function (resolve) {
      var start = Date.now();
      (function poll() {
        if (test() || Date.now() - start > ms) return resolve();
        setTimeout(poll, 60);
      })();
    });
  }

  async function copySnip() {
    var input = priceInput();
    if (input && snipButton() && snipButton().getAttribute("data-for") !== input.value) {
      commit(input);   // the picture on the page is for older text: apply what is typed, then copy
      await waitFor(function () { var b = snipButton(); return b && b.getAttribute("data-for") === input.value; }, 3000);
    }
    var btn = snipButton();
    if (!btn) return;
    try {
      var blob = await (await fetch(btn.getAttribute("data-png"))).blob();
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
      say("Copied. Paste it into a document or email.");
    } catch (err) {
      say("This browser blocked copying. Use Save snip as image instead.");
    }
  }

  document.addEventListener("click", function (ev) {
    if (ev.target && ev.target.closest && ev.target.closest("[data-stc-snip]")) { ev.preventDefault(); copySnip(); }
  });
})();
