/*
 * Inbox panel on the dashboard: folder mapping and message search.
 * Loaded before dashboard.js, which calls InboxUI.render() when a person signs in
 * and InboxUI.clear() when they sign out. All logic lives in hub-inbox.js; this
 * file only draws the panel and passes what the person typed to it.
 */
(function () {
  'use strict';

  var Hub = window.InvoiceHub;
  var Inbox = Hub.Inbox;

  var $ = function (id) { return document.getElementById(id); };
  var MAP_FIELDS = ['mailbox', 'folder', 'queue', 'sender', 'recipient', 'subject'];

  // Vendor-bill lifecycle view
  var TAB_NAMES = { received: 'received', approved: 'approved', sent: 'GL coded and sent to AP' };
  var STATUS_TEXT = { received: 'Awaiting approval', approved: 'Approved, ready for GL coding', sent: 'GL coded, sent to AP' };
  var STEP_TEXT = { received: 'Received', approved: 'Approved', sent: 'GL coded, sent to AP' };

  // ctx comes from dashboard.js: { rows, openInvoice(id), requireUser(), changed() }
  var state = { email: null, ctx: null };

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // Adds text to a node with search words wrapped in <mark>. Built from text nodes, never HTML.
  function appendHighlighted(parent, text, terms) {
    Inbox.highlightParts(text, terms).forEach(function (part) {
      parent.appendChild(part.match ? el('mark', '', part.text) : document.createTextNode(part.text));
    });
    return parent;
  }

  function formatDate(date) {
    return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  }

  function formatSize(kb) {
    return kb >= 1024 ? (kb / 1024).toFixed(1) + ' MB' : kb + ' KB';
  }

  function cap(key) { return key.charAt(0).toUpperCase() + key.slice(1); }

  /* ---------- mapping form ---------- */

  function setMapError(key, message) {
    var input = $('map' + cap(key));
    var box = $('map' + cap(key) + '-error');
    box.textContent = message || '';
    box.hidden = !message;
    if (message) input.setAttribute('aria-invalid', 'true'); else input.removeAttribute('aria-invalid');
  }

  function showMapStatus(message, kind) {
    var box = $('mapStatus');
    box.textContent = message;
    box.className = 'form-status' + (kind ? ' is-' + kind : '');
    box.hidden = !message;
  }

  function currentUser() {
    return state.ctx ? state.ctx.requireUser() : null;
  }

  function renderMailboxOptions(user) {
    var list = $('mailboxOptions');
    list.replaceChildren();
    Inbox.accessibleMailboxes(user).forEach(function (m) {
      var option = el('option');
      option.value = m.email;
      option.label = m.name;
      list.appendChild(option);
    });
    renderFolderOptions(user);
  }

  // Folder suggestions follow whichever mailbox is typed in.
  function renderFolderOptions(user) {
    var list = $('folderOptions');
    list.replaceChildren();
    var typed = $('mapMailbox').value.trim().toLowerCase();
    var box = Inbox.accessibleMailboxes(user).filter(function (m) { return m.email === typed; })[0];
    (box ? box.folders : []).forEach(function (f) {
      var option = el('option');
      option.value = f;
      list.appendChild(option);
    });
  }

  function renderMappings(user) {
    var list = $('mappingList');
    list.replaceChildren();
    var maps = Inbox.mappings(user);
    $('mappingEmpty').hidden = maps.length > 0;

    maps.forEach(function (m) {
      var li = el('li', 'mapping-item');
      var info = el('div');
      info.appendChild(el('strong', '', Inbox.longLabel(m)));
      info.appendChild(el('span', 'mapping-subject', Inbox.describe(m)));
      li.appendChild(info);
      li.appendChild(el('span', 'badge badge-' + m.queue.toLowerCase(), Inbox.shortLabel(m)));

      if (m.derived) {
        li.appendChild(el('span', 'from-settings', 'From settings'));
      } else {
        var btn = el('button', 'link-button', 'Remove');
        btn.type = 'button';
        btn.setAttribute('aria-label', 'Remove rule: ' + Inbox.longLabel(m) + ', ' + Inbox.describe(m));
        btn.addEventListener('click', function () {
          var actor = currentUser();
          if (!actor) return;
          Inbox.remove(actor, m.id);
          showMapStatus('Rule removed.', 'success');
          changed(actor);
        });
        li.appendChild(btn);
      }
      list.appendChild(li);
    });
  }

  function renderFolderFilter(user) {
    var select = $('inboxFolder');
    var previous = select.value;
    select.length = 1;
    Inbox.mappings(user).forEach(function (m) {
      select.appendChild(new Option(Inbox.longLabel(m) + ': ' + Inbox.describe(m), m.id));
    });
    if ([].some.call(select.options, function (o) { return o.value === previous; })) select.value = previous;
  }

  /* ---------- search results ---------- */

  function linkedInvoice(attachment) {
    var name = attachment.name.toLowerCase();
    var base = name.replace(/\.[^.]+$/, '');
    return state.ctx.rows.filter(function (r) { return r.document.toLowerCase() === name || r.id.toLowerCase() === base; })[0] || null;
  }

  function renderResults(user) {
    var terms = Inbox.termsOf($('inboxQuery').value);
    var maps = Inbox.mappings(user);
    var list = $('inboxResults');
    var empty = $('inboxEmpty');
    list.replaceChildren();

    $('inboxMore').hidden = true;
    if (!maps.length) {
      $('inboxCount').textContent = '';
      empty.textContent = 'Map at least one mailbox folder to start searching.';
      empty.hidden = false;
      return;
    }

    var results = Inbox.search(user, { query: $('inboxQuery').value, mappingId: $('inboxFolder').value, pdfOnly: $('inboxPdfOnly').checked });
    $('inboxCount').textContent = results.length + (results.length === 1 ? ' message' : ' messages') + (terms.length ? ' found' : '');
    empty.hidden = results.length > 0;
    if (!results.length) empty.textContent = 'No messages match. Try fewer words, or clear the PDF-only filter.';

    var moreLeft = results.length - resultsLimit;
    $('inboxMore').hidden = moreLeft <= 0;
    $('inboxMore').textContent = 'Show ' + Math.min(PAGE_STEP, Math.max(moreLeft, 0)) + ' more (' + Math.max(moreLeft, 0) + ' not shown)';
    results.slice(0, resultsLimit).forEach(function (r) {
      var m = r.message;
      var card = el('li', 'message-card');

      var top = el('div', 'message-top');
      top.appendChild(appendHighlighted(el('h4', 'message-subject'), m.subject, terms));
      top.appendChild(el('span', 'message-date', formatDate(m.receivedOn)));
      card.appendChild(top);

      var meta = el('p', 'message-meta');
      var sender = el('span', '', 'From ');
      appendHighlighted(sender, m.from, terms);
      meta.appendChild(sender);
      meta.appendChild(el('span', 'mono', m.mailbox + ' › ' + m.folder));
      meta.appendChild(el('span', 'badge badge-' + r.mapping.queue.toLowerCase(), Inbox.shortLabel(r.mapping)));
      card.appendChild(meta);

      card.appendChild(appendHighlighted(el('p', 'message-preview'), m.preview, terms));

      if (m.attachments.length) {
        var files = el('ul', 'attachment-list');
        m.attachments.forEach(function (a) {
          var li = el('li');
          li.appendChild(el('span', 'badge ' + (Inbox.isPdf(a) ? 'badge-pdf' : 'badge-file'), Inbox.isPdf(a) ? 'PDF' : 'File'));
          li.appendChild(appendHighlighted(el('span', 'mono'), a.name, terms));
          li.appendChild(el('span', 'attachment-size', formatSize(a.sizeKb)));

          var invoice = Inbox.isPdf(a) ? linkedInvoice(a) : null;
          if (invoice) {
            var open = el('button', 'link-chip', 'Open ' + invoice.id);
            open.type = 'button';
            open.addEventListener('click', function () { state.ctx.openInvoice(invoice.id); });
            li.appendChild(open);
          }
          files.appendChild(li);
        });
        card.appendChild(files);
      }

      r.pdfMatches.forEach(function (pm) {
        var note = el('p', 'pdf-snippet');
        note.appendChild(el('span', 'snippet-label', 'Found in ' + pm.attachment.name + ': '));
        appendHighlighted(note, pm.snippet, terms);
        card.appendChild(note);
      });

      list.appendChild(card);
    });
  }

  /* ---------- vendor bill lifecycle ---------- */

  var lifecycleTab = 'received';
  var PAGE_STEP = 25;               // items shown before "Show more", and how many each click adds
  var lifecycleLimit = PAGE_STEP;
  var resultsLimit = PAGE_STEP;

  // The date used for the date range: when the bill first arrived (or its earliest message).
  function billDate(bill) {
    if (bill.when.received) return bill.when.received;
    return Object.keys(bill.when).map(function (s) { return bill.when[s]; }).sort(function (a, b) { return a - b; })[0];
  }

  function chosenRange() {
    return {
      from: $('rangeFrom').value ? new Date($('rangeFrom').value + 'T00:00:00') : null,
      to: $('rangeTo').value ? new Date($('rangeTo').value + 'T23:59:59.999') : null
    };
  }

  function billCard(bill) {
    var card = el('li', 'bill-card');

    var top = el('div', 'bill-top');
    var who = el('div');
    who.appendChild(el('strong', 'bill-vendor', bill.vendor));
    who.appendChild(el('span', 'mono', bill.recognized ? 'Invoice ' + bill.invoice + ' · BOL ' + bill.bol : bill.fileName));
    top.appendChild(who);
    var pills = el('div', 'pill-stack');
    pills.appendChild(el('span', 'status-pill status-' + bill.status, STATUS_TEXT[bill.status]));
    if (!bill.recognized) pills.appendChild(el('span', 'status-pill status-unknown', 'Filename not recognized'));
    top.appendChild(pills);
    card.appendChild(top);

    // Three steps: which of them have a message, and when.
    var steps = el('ol', 'life-steps');
    Inbox.STAGES.forEach(function (stage) {
      var done = !!bill.when[stage];
      var step = el('li', 'life-step' + (done ? ' is-done' : '') + (stage === lifecycleTab ? ' is-current-tab' : ''));
      step.appendChild(el('span', 'life-dot', done ? '✓' : ''));
      var text = el('span', 'life-text');
      text.appendChild(el('strong', '', STEP_TEXT[stage]));
      text.appendChild(el('span', '', done ? formatDate(bill.when[stage]) : 'Not yet'));
      step.appendChild(text);
      steps.appendChild(step);
    });
    card.appendChild(steps);

    var invoice = linkedInvoice({ name: bill.fileName });
    if (invoice) {
      var open = el('button', 'link-chip', 'Open ' + invoice.id);
      open.type = 'button';
      open.addEventListener('click', function () { state.ctx.openInvoice(invoice.id); });
      card.appendChild(open);
    }

    // How each stage was worked out: the sender, subject line and folder of every message found.
    var proof = el('details', 'bill-proof');
    proof.appendChild(el('summary', '', 'How this was tracked (' + bill.messages.length + (bill.messages.length === 1 ? ' message)' : ' messages)')));
    var list = el('ul');
    bill.messages.forEach(function (found) {
      var li = el('li');
      li.appendChild(el('strong', '', STEP_TEXT[found.stage] + ': '));
      li.appendChild(document.createTextNode('“' + found.message.subject + '”'));
      li.appendChild(el('span', 'mono', 'From ' + found.message.from.split(' <')[0] + ' · ' + found.message.folder + ' · ' + formatDate(found.message.receivedOn)));
      list.appendChild(li);
    });
    proof.appendChild(list);
    var fileLine = el('p', 'proof-file');
    fileLine.appendChild(el('strong', '', 'PDF filename: '));
    fileLine.appendChild(el('span', 'mono', bill.fileName));
    proof.appendChild(fileLine);
    card.appendChild(proof);
    return card;
  }

  function renderLifecycle(user) {
    var role = Hub.ROLES[user.role] || {};
    var range = chosenRange();
    var maps = Inbox.mappings(user).filter(function (m) { return m.queue === 'AP'; });

    // Bills inside the chosen dates, then (optionally) only the ones waiting on this person.
    var inRange = Inbox.lifecycle(user).filter(function (b) {
      var d = billDate(b);
      return d && (!range.from || d >= range.from) && (!range.to || d <= range.to);
    });
    $('rangeClear').hidden = !(range.from || range.to);

    var waitingBox = $('waitingLabel');
    waitingBox.hidden = !role.waitsAt;
    var waitingCount = role.waitsAt ? inRange.filter(function (b) { return b.status === role.waitsAt; }).length : 0;
    if (role.waitsAt) $('waitingText').textContent = 'Only bills waiting on me: ' + role.waitsText + ' (' + waitingCount + ')';
    var waitingOnly = !!role.waitsAt && $('waitingOnly').checked;
    var bills = waitingOnly ? inRange.filter(function (b) { return b.status === role.waitsAt; }) : inRange;

    var counts = { received: 0, approved: 0, sent: 0 };
    var byStatus = { received: 0, approved: 0, sent: 0 };
    bills.forEach(function (b) {
      Object.keys(b.when).forEach(function (stage) { counts[stage] += 1; });
      byStatus[b.status] += 1;
    });
    var unrecognized = bills.filter(function (b) { return !b.recognized; }).length;

    document.querySelectorAll('#lifecycleTabs button').forEach(function (btn) {
      btn.querySelector('.tab-count').textContent = counts[btn.dataset.stage];
      btn.setAttribute('aria-pressed', String(btn.dataset.stage === lifecycleTab));
    });

    $('lifecycleSummary').textContent = bills.length
      ? byStatus.received + ' awaiting approval · ' + byStatus.approved + ' approved, ready for GL coding · ' + byStatus.sent + ' GL coded and sent to AP' +
        (unrecognized ? ' · ' + unrecognized + (unrecognized === 1 ? ' filename' : ' filenames') + ' not recognized' : '')
      : '';

    var list = $('lifecycleList');
    var empty = $('lifecycleEmpty');
    list.replaceChildren();

    var shown = bills.filter(function (b) { return b.when[lifecycleTab]; })
      .sort(function (a, b) { return b.when[lifecycleTab] - a.when[lifecycleTab]; });

    if (!shown.length) {
      var hasRule = maps.some(function (m) { return (m.stage || 'received') === lifecycleTab; });
      var filtered = range.from || range.to || waitingOnly;
      var mail = Hub.Mail.status(user);
      empty.textContent = mail.loading ? 'Loading your mail…'
        : mail.error ? 'Your mail could not be read, so no bills can be shown. Use Refresh to try again.'
        : !maps.length ? 'No rules are switched on. Open Settings (the gear icon) to turn one on.'
        : !hasRule ? 'You have no rule for “' + TAB_NAMES[lifecycleTab] + '” yet. Turn one on in Settings.'
        : filtered ? 'No bills match the dates or filters you chose.'
        : 'No bills found for your “' + TAB_NAMES[lifecycleTab] + '” rule.';
    }
    empty.hidden = shown.length > 0;
    var billsLeft = shown.length - lifecycleLimit;
    $('lifecycleMore').hidden = billsLeft <= 0;
    $('lifecycleMore').textContent = 'Show ' + Math.min(PAGE_STEP, Math.max(billsLeft, 0)) + ' more (' + Math.max(billsLeft, 0) + ' not shown)';
    shown.slice(0, lifecycleLimit).forEach(function (bill) { list.appendChild(billCard(bill)); });
  }

  // Where the mail comes from and when it was last read.
  function renderMailStatus(user) {
    var mail = Hub.Mail.status(user);
    var text = '';
    if (mail.loading) text = 'Loading your mail from ' + mail.providerName + '…';
    else if (mail.error) text = 'Could not read your mail: ' + mail.error;
    else if (mail.loadedAt) {
      text = 'Mail source: ' + mail.providerName + ' · updated ' + mail.loadedAt.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }) +
        (mail.skipped ? ' · ' + mail.skipped + ' unreadable ' + (mail.skipped === 1 ? 'message' : 'messages') + ' skipped' : '');
    }
    $('mailSource').textContent = text;
    $('mailSource').className = mail.error ? 'is-error' : '';
    $('mailRefresh').disabled = mail.loading;
  }

  function isoDay(date) {
    function pad(n) { return (n < 10 ? '0' : '') + n; }
    return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate());
  }

  // Sets the date range to this person's default: the last N days from their settings, or no range.
  function applyDefaultRange(user) {
    var days = Hub.Settings.get(user).defaultRangeDays;
    var from = new Date();
    from.setDate(from.getDate() - days);
    $('rangeFrom').value = days > 0 ? isoDay(from) : '';
    $('rangeTo').value = '';
  }

  function refresh(user) {
    renderMailStatus(user);
    renderMailboxOptions(user);
    renderMappings(user);
    renderFolderFilter(user);
    renderResults(user);
    renderLifecycle(user);
  }

  // After a rule is added or removed, let the dashboard redraw too (its summary figures use the rules).
  function changed(user) {
    if (state.ctx && state.ctx.changed) state.ctx.changed(); else refresh(user);
  }

  /* ---------- public ---------- */

  function clear() {
    state.email = null;
    state.ctx = null;
    ['mailboxOptions', 'folderOptions', 'mappingList', 'inboxResults', 'lifecycleList'].forEach(function (id) { $(id).replaceChildren(); });
    $('lifecycleSummary').textContent = '';
    $('lifecycleEmpty').hidden = true;
    lifecycleTab = 'received';
    lifecycleLimit = PAGE_STEP;
    resultsLimit = PAGE_STEP;
    $('rangeFrom').value = '';
    $('rangeTo').value = '';
    $('waitingOnly').checked = false;
    $('waitingLabel').hidden = true;
    $('inboxFolder').length = 1;
    $('mappingForm').reset();
    $('inboxSearchForm').reset();
    MAP_FIELDS.forEach(function (k) { setMapError(k, ''); });
    showMapStatus('');
    $('inboxCount').textContent = '';
  }

  /** Draws the panels for this person. Pass null to hide them. */
  function render(user, ctx) {
    var panels = [$('lifecyclePanel'), $('inboxPanel')];
    if (!user) { clear(); panels.forEach(function (p) { p.hidden = true; }); return; }
    if (state.email !== user.email) {
      clear();
      // Open on the stage that is this person's job (for example "approved" for the GL accountant).
      lifecycleTab = (Hub.ROLES[user.role] && Hub.ROLES[user.role].waitsAt) || 'received';
      applyDefaultRange(user);
    }
    state.email = user.email;
    state.ctx = ctx;
    panels.forEach(function (p) { p.hidden = false; });
    refresh(user);
    if (!$('mapMailbox').value) { $('mapMailbox').value = user.email; renderFolderOptions(user); } // their own mailbox
  }

  /* ---------- events ---------- */

  $('mapMailbox').addEventListener('input', function () {
    var user = currentUser();
    if (user) renderFolderOptions(user);
  });

  $('mappingForm').addEventListener('input', function (event) {
    var key = event.target.id.replace(/^map/, '').toLowerCase();
    if (MAP_FIELDS.indexOf(key) !== -1) setMapError(key, '');
  });

  $('mappingForm').addEventListener('submit', function (event) {
    event.preventDefault();
    var user = currentUser();
    if (!user) return;
    showMapStatus('');
    MAP_FIELDS.forEach(function (k) { setMapError(k, ''); });

    var result = Inbox.add(user, {
      mailbox: $('mapMailbox').value, folder: $('mapFolder').value, kind: $('mapQueue').value,
      sender: $('mapSender').value, recipient: $('mapRecipient').value, subject: $('mapSubject').value
    });
    if (!result.ok) {
      var first;
      MAP_FIELDS.forEach(function (k) {
        if (result.errors[k]) { setMapError(k, result.errors[k]); first = first || k; }
      });
      if (first) $('map' + cap(first)).focus();
      return;
    }

    $('mappingForm').reset();
    showMapStatus('Added rule: ' + Inbox.longLabel(result.mapping) + ' (' + Inbox.describe(result.mapping) + ').', 'success');
    changed(user);
  });

  // Reads the person's mail again from the mail source.
  $('mailRefresh').addEventListener('click', function () {
    var user = currentUser();
    if (!user) return;
    var reload = Hub.Mail.load(user, { force: true });
    renderMailStatus(user);
    reload.then(function () {
      var current = Hub.Access.currentUser();
      if (current && current.email === user.email) changed(current);
    });
  });

  document.querySelectorAll('#lifecycleTabs button').forEach(function (btn) {
    btn.addEventListener('click', function () {
      lifecycleTab = btn.dataset.stage;
      lifecycleLimit = PAGE_STEP;
      var user = currentUser();
      if (user) renderLifecycle(user);
    });
  });

  ['rangeFrom', 'rangeTo', 'waitingOnly'].forEach(function (id) {
    $(id).addEventListener('change', function () {
      lifecycleLimit = PAGE_STEP;
      var user = currentUser();
      if (user) renderLifecycle(user);
    });
  });
  $('lifecycleMore').addEventListener('click', function () {
    lifecycleLimit += PAGE_STEP;
    var user = currentUser();
    if (user) renderLifecycle(user);
  });
  $('inboxMore').addEventListener('click', function () {
    resultsLimit += PAGE_STEP;
    var user = currentUser();
    if (user) renderResults(user);
  });

  $('rangeClear').addEventListener('click', function () {
    $('rangeFrom').value = '';
    $('rangeTo').value = '';
    lifecycleLimit = PAGE_STEP;
    var user = currentUser();
    if (user) renderLifecycle(user);
  });

  ['input', 'change'].forEach(function (type) {
    $('inboxSearchForm').addEventListener(type, function () {
      resultsLimit = PAGE_STEP;
      var user = currentUser();
      if (user) renderResults(user);
    });
  });
  $('inboxSearchForm').addEventListener('submit', function (event) { event.preventDefault(); });

  Hub.InboxUI = { render: render, clear: clear, applyDefaultRange: applyDefaultRange };
})();
