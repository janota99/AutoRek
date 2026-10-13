/* Contractor-bill decision guide. Injected by tax_rules_ui.py via st.html(unsafe_allow_javascript=True) next to
   window.PP_CG_TREE (the tree, with each outcome's references). It only draws and animates; it computes nothing.
   Every string is set with textContent, never innerHTML. */
(function () {
  var root = document.querySelector(".cg-root");
  if (!root || root.dataset.bound) return;          // Streamlit reruns re-mount the element; bind once
  root.dataset.bound = "1";
  var tree = window.PP_CG_TREE;
  if (!tree) return;

  var stage = root.querySelector(".cg-stage");
  var trail = root.querySelector(".cg-trail");
  var live = root.querySelector(".cg-live");
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var KEY = "pp-cg-path";
  var path = [];                                    // indexes of the options chosen so far

  try { path = JSON.parse(sessionStorage.getItem(KEY) || "[]"); } catch (e) { path = []; }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function nodeFor(indexes) {
    var node = tree;
    for (var i = 0; i < indexes.length; i++) {
      if (!node.options || !node.options[indexes[i]]) return null;
      node = node.options[indexes[i]].next;
    }
    return node;
  }

  function save() { try { sessionStorage.setItem(KEY, JSON.stringify(path)); } catch (e) {} }

  // A ripple that starts where the pointer (or the keyboard focus) landed.
  function ripple(button, ev) {
    if (reduced) return;
    var r = button.getBoundingClientRect();
    var dot = el("span", "cg-ripple");
    var size = Math.max(r.width, r.height) * 1.6;
    dot.style.width = dot.style.height = size + "px";
    var x = ev && ev.clientX ? ev.clientX - r.left : r.width / 2;
    var y = ev && ev.clientY ? ev.clientY - r.top : r.height / 2;
    dot.style.left = (x - size / 2) + "px";
    dot.style.top = (y - size / 2) + "px";
    button.appendChild(dot);
    setTimeout(function () { if (dot.parentNode) dot.parentNode.removeChild(dot); }, 650);
  }

  function choose(button, index, ev) {
    ripple(button, ev);
    var group = button.parentNode;
    Array.prototype.forEach.call(group.children, function (b) {
      b.disabled = true;
      b.classList.add(b === button ? "cg-picked" : "cg-dim");
      b.setAttribute("aria-pressed", b === button ? "true" : "false");
    });
    var go = function () { path.push(index); save(); draw(true); };
    if (reduced) go(); else setTimeout(go, 320);   // let the pick animation read before the next step arrives
  }

  function drawTrail() {
    trail.textContent = "";
    var node = tree;
    path.forEach(function (idx, depth) {
      var option = node.options[idx];
      var chip = el("button", "cg-chip");
      chip.type = "button";
      chip.appendChild(el("span", "cg-chip-q", node.question));
      chip.appendChild(el("span", "cg-chip-a", option.label));
      chip.title = "Go back to this question";
      chip.addEventListener("click", function () { path = path.slice(0, depth); save(); draw(true); });
      trail.appendChild(chip);
      node = option.next;
    });
  }

  function questionCard(node, animate) {
    var card = el("section", "cg-card cg-question" + (animate ? " cg-enter" : ""));
    card.appendChild(el("h4", "cg-q", node.question));
    var group = el("div", "cg-options");
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", node.question);
    node.options.forEach(function (option, i) {
      var b = el("button", "cg-option");
      b.type = "button";
      b.style.setProperty("--d", (i * 70) + "ms");
      b.appendChild(el("span", "cg-option-label", option.label));
      b.appendChild(el("span", "cg-arrow", "→"));
      b.addEventListener("click", function (ev) { choose(b, i, ev); });
      group.appendChild(b);
    });
    card.appendChild(group);
    return card;
  }

  function outcomeCard(outcome, animate) {
    var card = el("section", "cg-card cg-outcome cg-tone-" + outcome.tone + (animate ? " cg-enter cg-pop" : ""));
    card.tabIndex = -1;
    var mark = outcome.tone === "tax" ? "!" : outcome.tone === "free" ? "✓" : "≈";
    var head = el("div", "cg-outcome-head");
    head.appendChild(el("span", "cg-mark", mark));
    head.appendChild(el("h4", "cg-headline", outcome.headline));
    card.appendChild(head);
    card.appendChild(el("p", "cg-detail", outcome.detail));
    if (outcome.references && outcome.references.length) {
      card.appendChild(el("p", "cg-refs-label", "Related scenarios in the rules below"));
      var list = el("ul", "cg-refs");
      outcome.references.forEach(function (ref) {
        var li = el("li");
        li.appendChild(el("b", null, "#" + ref.no + " " + ref.title));
        li.appendChild(el("span", null, " · " + ref.authority));
        list.appendChild(li);
      });
      card.appendChild(list);
    }
    var actions = el("div", "cg-actions");
    var back = el("button", "cg-btn", "Back one step");
    back.type = "button";
    back.addEventListener("click", function () { path.pop(); save(); draw(true); });
    var again = el("button", "cg-btn cg-btn-primary", "Start over");
    again.type = "button";
    again.addEventListener("click", function () { path = []; save(); draw(true); });
    actions.appendChild(back);
    actions.appendChild(again);
    card.appendChild(actions);
    return card;
  }

  function draw(userAction) {
    var node = nodeFor(path);
    if (!node) { path = []; save(); node = tree; }
    drawTrail();
    stage.textContent = "";
    if (node.outcome) {
      var card = outcomeCard(node.outcome, userAction);
      stage.appendChild(card);
      live.textContent = "Result: " + node.outcome.headline + " " + node.outcome.detail;
      if (userAction) card.focus({ preventScroll: false });
    } else {
      stage.appendChild(questionCard(node, userAction));
      live.textContent = node.question;
      if (userAction && path.length) {
        var first = stage.querySelector(".cg-option");
        if (first) first.focus({ preventScroll: true });
      }
    }
    root.setAttribute("data-depth", String(path.length));
  }

  var reset = root.querySelector(".cg-reset");
  if (reset) reset.addEventListener("click", function () { path = []; save(); draw(true); });

  draw(false);
})();
