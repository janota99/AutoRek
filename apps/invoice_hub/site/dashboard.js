/*
 * Personal dashboard page: sign-in, role-based dashboard, and (for
 * administrators) the access list.
 *
 * Every dashboard is built from buildDashboard(user), which returns ONLY what
 * that user's role allows. That function is the seam for the real service:
 * later it becomes a request to a server that authenticates the user and
 * returns their data. Nothing else on this page reads invoice data directly.
 *
 * PROTOTYPE ONLY: this runs in the browser, so it demonstrates the experience
 * but does not enforce privacy. See hub-access.js.
 */
(function () {
  'use strict';

  var Hub = window.InvoiceHub;
  var Access = Hub.Access;

  var STAGE_ALERT_DAYS = 5;   // held this long with the same owner -> flagged
  var AGED_DAYS = 30;         // total age past this -> flagged

  var currency = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });

  var $ = function (id) { return document.getElementById(id); };
  var signInView = $('signInView');
  var dashboardView = $('dashboardView');

  /* ---------- small DOM helper (user text is only ever set via textContent) ---------- */

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  /* ---------- greeting ---------- */

  // The hour (0-23) on a US Central clock, whatever timezone the visitor's device is in.
  // "America/Chicago" follows daylight saving, so it matches Central clocks all year.
  var centralClock = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Chicago', hour: 'numeric', hourCycle: 'h23' });

  function centralHour(date) {
    return Number(centralClock.format(date));
  }

  // 1:00 AM - 11:59 AM morning, 12:00 PM - 5:59 PM afternoon, 6:00 PM until 1:00 AM evening.
  // The hour after midnight (12:00-12:59 AM) is not covered by the brief; it counts as evening.
  function greetingFor(hour) {
    if (hour >= 1 && hour < 12) return 'Good morning';
    if (hour >= 12 && hour < 18) return 'Good afternoon';
    return 'Good evening';
  }

  /* ---------- aging ---------- */

  function startOfDay(d) {
    return new Date(d.getFullYear(), d.getMonth(), d.getDate());
  }

  function daysSince(date) {
    return Math.max(0, Math.round((startOfDay(new Date()) - startOfDay(date)) / 86400000));
  }

  function withAging(invoice) {
    return Object.assign({}, invoice, { ageDays: daysSince(invoice.receivedOn), stageDays: daysSince(invoice.stageSince) });
  }

  function count(rows, test) { return rows.filter(test).length; }
  function average(rows, pick) {
    return rows.length ? Math.round(rows.reduce(function (sum, r) { return sum + pick(r); }, 0) / rows.length) : 0;
  }
  /* ---------- the seam: what this user is allowed to see ---------- */

  function buildDashboard(user) {
    var all = Hub.Workflow.invoices().map(withAging);
    var isAged = function (r) { return r.ageDays > AGED_DAYS; };
    var isHeld = function (r) { return r.stageDays > STAGE_ALERT_DAYS; };
    var inApproval = function (r) { return r.stage === Hub.APPROVAL_STAGE; };
    var byAge = function (a, b) { return b.ageDays - a.ageDays; };
    var rows;

    switch (user.role) {
      case 'ap':
        rows = all.filter(function (r) { return r.type === 'AP'; }).sort(byAge);
        return {
          intro: 'Vendor bills currently moving through the payables workflow.',
          tableTitle: 'Vendor bill queue',
          columns: { party: 'Vendor', document: 'Attachment' },
          rows: rows,
          emptyMessage: 'No open vendor bills.',
          kpis: [
            { label: 'Open bills', value: rows.length, note: 'in the AP queue' },
            { label: 'Awaiting approval', value: count(rows, inApproval), note: 'with a department approver' },
            { label: 'Aged over ' + AGED_DAYS + ' days', value: count(rows, isAged), note: 'since received', tone: count(rows, isAged) ? 'alert' : '' },
            { label: 'Average age', value: average(rows, function (r) { return r.ageDays; }), note: 'days since received' }
          ]
        };

      case 'ar':
        rows = all.filter(function (r) { return r.type === 'AR'; }).sort(byAge);
        return {
          intro: 'Customer invoices currently moving through the receivables workflow.',
          tableTitle: 'Customer invoice queue',
          columns: { party: 'Customer', document: 'BOL' },
          rows: rows,
          emptyMessage: 'No open customer invoices.',
          kpis: [
            { label: 'Open invoices', value: rows.length, note: 'in the AR queue' },
            { label: 'Awaiting BOL match', value: count(rows, function (r) { return r.stage === 'BOL Match'; }), note: 'need a matching BOL' },
            { label: 'Aged over ' + AGED_DAYS + ' days', value: count(rows, isAged), note: 'since received', tone: count(rows, isAged) ? 'alert' : '' },
            { label: 'Average age', value: average(rows, function (r) { return r.ageDays; }), note: 'days since received' }
          ]
        };

      case 'approver':
        // Only this approver's own department: bills waiting on them, plus bills they have already decided.
        var decidedByMe = function (r) {
          return r.history.some(function (h) { return h.by === user.email && (h.action === 'approved' || h.action === 'rejected'); });
        };
        rows = all.filter(function (r) {
          return r.type === 'AP' && r.department === user.department && (inApproval(r) || decidedByMe(r));
        }).sort(byAge);
        var pending = rows.filter(inApproval);
        return {
          intro: 'Bills waiting for approval from ' + user.department + ', and the decisions you have made.',
          tableTitle: user.department + ' approvals',
          columns: { party: 'Vendor', document: 'Attachment' },
          rows: rows,
          emptyMessage: 'Nothing is waiting on ' + user.department + ' right now.',
          kpis: [
            { label: 'Waiting on ' + user.department, value: pending.length, note: 'bills to review' },
            { label: 'Held over ' + STAGE_ALERT_DAYS + ' days', value: count(pending, isHeld), note: 'in your department', tone: count(pending, isHeld) ? 'warn' : '' },
            { label: 'Amount waiting', value: currency.format(pending.reduce(function (s, r) { return s + r.amount; }, 0)), note: 'total value' },
            { label: 'Decided by you', value: count(rows, decidedByMe), note: 'approved or rejected' }
          ]
        };

      case 'gl':
      case 'office':
        // These roles work from their own mailbox, so their figures come from their rules, not the invoice queue.
        var mine = Hub.Inbox.lifecycle(user);
        var job = Hub.ROLES[user.role];
        var waiting = count(mine, function (b) { return b.status === job.waitsAt; });
        return {
          intro: job.summary + ' Your rules below tell the tracker how to find each stage in your own mailbox.',
          tableTitle: null,
          rows: null,
          kpis: [
            { label: 'Bills tracked', value: mine.length, note: 'found in your mailbox' },
            { label: 'Waiting on you', value: waiting, note: job.waitsText, tone: waiting ? 'warn' : '' },
            { label: 'Approved', value: count(mine, function (b) { return !!b.when.approved; }), note: 'PO and approval done' },
            { label: 'GL coded, sent to AP', value: count(mine, function (b) { return !!b.when.sent; }), note: 'sent for payment' }
          ]
        };

      default: // admin: organization-wide totals only, no individual invoice or user dashboards
        return {
          intro: 'Organization-wide totals and the access list. Individual dashboards stay private to each person.',
          tableTitle: null,
          rows: null,
          kpis: [
            { label: 'People with access', value: Access.list().length, note: 'on the access list' },
            { label: 'Open vendor bills', value: count(all, function (r) { return r.type === 'AP'; }), note: 'AP queue' },
            { label: 'Open customer invoices', value: count(all, function (r) { return r.type === 'AR'; }), note: 'AR queue' },
            { label: 'Aged over ' + AGED_DAYS + ' days', value: count(all, isAged), note: 'across both queues', tone: count(all, isAged) ? 'alert' : '' }
          ]
        };
    }
  }

  /* ---------- rendering: dashboard ---------- */

  function renderKpis(kpis) {
    var grid = $('kpiGrid');
    grid.replaceChildren();
    kpis.forEach(function (k) {
      var card = el('div', 'kpi-card' + (k.tone ? ' is-' + k.tone : ''));
      card.appendChild(el('span', 'kpi-label', k.label));
      card.appendChild(el('strong', 'kpi-value', String(k.value)));
      card.appendChild(el('span', 'kpi-note', k.note));
      grid.appendChild(card);
    });
  }

  function flag(text, kind, title) {
    var f = el('span', 'flag flag-' + kind, text);
    f.title = title;
    return f;
  }

  // A colored pill for a workflow stage. The words stay on the pill, so color is never the only signal.
  var STAGE_TONE = {
    'Received': 'neutral', 'Coding': 'info', 'Department Approval': 'warn', 'Payment Scheduled': 'ok',
    'BOL Match': 'warn', 'Billing Review': 'info', 'Sent to Customer': 'ok'
  };
  function stageBadge(stage) {
    return el('span', 'stage-badge stage-' + (STAGE_TONE[stage] || 'neutral'), stage);
  }

  // A small envelope, drawn as SVG (no text from the mailbox is ever placed in the markup).
  var SVG_NS = 'http://www.w3.org/2000/svg';
  function mailIcon() {
    var svg = document.createElementNS(SVG_NS, 'svg');
    [['viewBox', '0 0 24 24'], ['width', '16'], ['height', '16'], ['fill', 'none'], ['stroke', 'currentColor'], ['stroke-width', '2'],
     ['stroke-linecap', 'round'], ['stroke-linejoin', 'round'], ['aria-hidden', 'true'], ['focusable', 'false']].forEach(function (a) { svg.setAttribute(a[0], a[1]); });
    var body = document.createElementNS(SVG_NS, 'path');
    body.setAttribute('d', 'M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z');
    var flap = document.createElementNS(SVG_NS, 'polyline');
    flap.setAttribute('points', '22,6 12,13 2,6');
    svg.appendChild(body);
    svg.appendChild(flap);
    return svg;
  }

  /* ---------- queue: filtering, sorting, selecting and paging ---------- */

  var SORTS = {
    'age-desc':    function (a, b) { return b.ageDays - a.ageDays; },
    'age-asc':     function (a, b) { return a.ageDays - b.ageDays; },
    'stage-desc':  function (a, b) { return b.stageDays - a.stageDays || b.ageDays - a.ageDays; },
    'amount-desc': function (a, b) { return b.amount - a.amount; },
    'party-asc':   function (a, b) { return a.party.localeCompare(b.party); }
  };

  // allRows: every row this user may see. shown: the filtered, sorted rows (all pages). The detail view
  // only ever reads from these, so it cannot reach anything outside the user's scope.
  // selected: ids ticked in the table (kept while paging and filtering). lineage: PDF name -> emails it came from.
  var queue = { allRows: [], shown: [], emptyMessage: '', user: null, page: 1, pageSize: 10, selected: {}, lineage: {}, focusKey: null };

  function currentFilters() {
    return { q: $('filterSearch').value.trim().toLowerCase(), stage: $('filterStage').value, aging: $('filterAging').value, sort: $('filterSort').value };
  }

  function matches(r, f) {
    if (f.q && [r.id, r.party, r.document].join(' ').toLowerCase().indexOf(f.q) === -1) return false;
    if (f.stage && r.stage !== f.stage) return false;
    if (f.aging === 'held' && r.stageDays <= STAGE_ALERT_DAYS) return false;
    if (f.aging === 'old' && r.ageDays <= AGED_DAYS) return false;
    return true;
  }

  function docKey(name) { return String(name || '').toLowerCase().replace(/\s+/g, ' ').trim(); }

  // Every email in this person's mailbox that carries each PDF name, oldest first.
  function buildLineage(user) {
    var index = {};
    Hub.Mail.messages(user).forEach(function (msg) {
      var seen = {};
      msg.attachments.forEach(function (a) {
        var key = docKey(a.name);
        if (seen[key]) return;
        seen[key] = true;
        (index[key] = index[key] || []).push(msg);
      });
    });
    Object.keys(index).forEach(function (key) { index[key].sort(function (a, b) { return a.receivedOn - b.receivedOn; }); });
    return index;
  }

  function canSelect() { return !!queue.user && Hub.Workflow.canEverAct(queue.user.role); }
  function selectedRows() { return queue.allRows.filter(function (r) { return queue.selected[r.id]; }); }
  function currentPageRows() {
    var start = (queue.page - 1) * queue.pageSize;
    return queue.shown.slice(start, start + queue.pageSize);
  }

  // keepFilters: after an approval the table refreshes but the person's search, filters and page stay put.
  function setupQueue(dashboard, user, keepFilters) {
    var panel = $('queuePanel');
    panel.hidden = !dashboard.rows;
    if (!dashboard.rows) { queue.allRows = []; queue.shown = []; queue.user = null; queue.selected = {}; return; }

    $('queueTitle').textContent = dashboard.tableTitle;
    $('partyHeading').textContent = dashboard.columns.party;
    $('documentHeading').textContent = dashboard.columns.document;
    queue.allRows = dashboard.rows;
    queue.emptyMessage = dashboard.emptyMessage;
    queue.user = user;
    queue.lineage = buildLineage(user);
    if (!keepFilters) queue.page = 1;

    // A selected bill that has left this person's list (for example after it was approved) is no longer selected.
    var present = {};
    dashboard.rows.forEach(function (r) { present[r.id] = true; });
    Object.keys(queue.selected).forEach(function (id) { if (!present[id]) delete queue.selected[id]; });

    var kept = keepFilters ? currentFilters() : null;
    var keptRaw = keepFilters ? $('filterSearch').value : '';

    // Filter choices come from this user's own rows, in workflow order.
    var stageSelect = $('filterStage');
    stageSelect.length = 1;
    var stagesPresent = {};
    dashboard.rows.forEach(function (r) { stagesPresent[r.type + '|' + r.stage] = true; });
    var type = dashboard.rows.length ? dashboard.rows[0].type : null;
    (type ? Hub.STAGES[type] : []).forEach(function (s) {
      if (stagesPresent[type + '|' + s]) stageSelect.appendChild(new Option(s, s));
    });

    $('filterAging').options[1].textContent = 'Held over ' + STAGE_ALERT_DAYS + ' days in stage';
    $('filterAging').options[2].textContent = 'Older than ' + AGED_DAYS + ' days';

    if (kept) {
      $('filterSearch').value = keptRaw;
      $('filterAging').value = kept.aging;
      $('filterSort').value = kept.sort;
      var stageStillListed = [].some.call($('filterStage').options, function (o) { return o.value === kept.stage; });
      $('filterStage').value = stageStillListed ? kept.stage : ''; // "All stages" if that stage no longer has items
    } else {
      $('filterBar').reset();
    }
    renderQueue();
  }

  function renderQueue() {
    var f = currentFilters();
    var total = queue.allRows.length;
    queue.shown = queue.allRows.filter(function (r) { return matches(r, f); }).sort(SORTS[f.sort] || SORTS['age-desc']);

    var filtering = !!(f.q || f.stage || f.aging);
    $('clearFilters').hidden = !filtering;
    $('filterBar').hidden = total === 0;
    $('resultCount').textContent = total === 0 ? '' :
      (filtering ? queue.shown.length + ' of ' + total + ' match your filters' : total + (total === 1 ? ' item' : ' items'));

    var empty = $('queueEmpty');
    empty.hidden = queue.shown.length > 0;
    if (!empty.hidden) empty.textContent = total === 0 ? queue.emptyMessage : 'No items match your filters.';
    $('queueTable').hidden = queue.shown.length === 0;

    // Only one page of rows is drawn at a time.
    var pages = Math.max(1, Math.ceil(queue.shown.length / queue.pageSize));
    queue.page = Math.min(Math.max(1, queue.page), pages);
    var start = (queue.page - 1) * queue.pageSize;
    var pageRows = queue.shown.slice(start, start + queue.pageSize);

    var selectable = canSelect();
    $('selectHeader').hidden = !selectable;

    // If any row on this page has an envelope, every row keeps the same space, so the filenames line up.
    var pageHasEnvelopes = pageRows.some(function (r) { return (queue.lineage[docKey(r.document)] || []).length > 0; });

    var body = $('queueBody');
    body.replaceChildren();

    pageRows.forEach(function (r, i) {
      var index = start + i; // position in the whole filtered list, so Previous / Next in the detail view crosses pages
      var tr = el('tr', 'is-clickable' + (queue.selected[r.id] ? ' is-selected' : ''));
      tr.addEventListener('click', function (event) {
        if (event.target.closest('button, input, a')) return; // buttons and boxes handle their own clicks
        openDetail(index);
      });

      if (selectable) {
        var pick = el('td', 'check-col');
        var box = el('input');
        box.type = 'checkbox';
        box.checked = !!queue.selected[r.id];
        box.setAttribute('aria-label', 'Select ' + r.id + ', ' + r.party);
        box.addEventListener('change', function () {
          if (box.checked) queue.selected[r.id] = true; else delete queue.selected[r.id];
          tr.classList.toggle('is-selected', box.checked);
          updateSelectionUi();
        });
        pick.appendChild(box);
        tr.appendChild(pick);
      }

      var refCell = el('td');
      var open = el('button', 'row-link', r.id);
      open.type = 'button';
      open.setAttribute('aria-label', 'View details for ' + r.id + ', ' + r.party);
      open.addEventListener('click', function () { openDetail(index); });
      refCell.appendChild(open);
      tr.appendChild(refCell);
      tr.appendChild(el('td', '', r.party));

      // The attachment name, with an envelope when the PDF was found in one of this person's emails.
      var doc = el('td', 'doc-cell');
      var emails = queue.lineage[docKey(r.document)];
      if (emails && emails.length) {
        var mailButton = el('button', 'lineage-button');
        mailButton.type = 'button';
        mailButton.title = 'Found in ' + emails.length + (emails.length === 1 ? ' email' : ' emails') + ' in your mailbox. Select to see them.';
        mailButton.setAttribute('aria-label', 'Show the ' + (emails.length === 1 ? 'email' : emails.length + ' emails') + ' behind ' + r.document);
        mailButton.appendChild(mailIcon());
        mailButton.addEventListener('click', function () { openLineage(r, emails); });
        doc.appendChild(mailButton);
      } else if (pageHasEnvelopes) {
        doc.appendChild(el('span', 'lineage-spacer'));
      }
      doc.appendChild(el('span', 'doc-name', r.document));
      tr.appendChild(doc);

      var stageCell = el('td');
      stageCell.appendChild(stageBadge(r.stage));
      if (Hub.Workflow.availableActions(queue.user, r).length) stageCell.appendChild(flag('Your action', 'action', 'Waiting for you to act'));
      tr.appendChild(stageCell);

      // Numbers are right-aligned with tabular digits. Any warning tag goes BEFORE the digits, so the digits line up.
      var age = el('td', 'num');
      if (r.ageDays > AGED_DAYS) age.appendChild(flag(AGED_DAYS + '+', 'alert', 'Older than ' + AGED_DAYS + ' days'));
      age.appendChild(document.createTextNode(r.ageDays + ' d'));
      tr.appendChild(age);

      var inStage = el('td', 'num');
      if (r.stageDays > STAGE_ALERT_DAYS) inStage.appendChild(flag(STAGE_ALERT_DAYS + '+', 'warn', 'Held over ' + STAGE_ALERT_DAYS + ' days in this stage'));
      inStage.appendChild(document.createTextNode(r.stageDays + ' d'));
      tr.appendChild(inStage);

      tr.appendChild(el('td', 'num', currency.format(r.amount)));
      body.appendChild(tr);
    });

    renderPagination(queue.shown.length, pages, start, pageRows.length);
    updateSelectionUi();
  }

  /* ---------- paging ---------- */

  // Which page numbers to show: the first, the last, and the pages around the current one, with gaps as "…".
  function pageWindow(current, pages) {
    if (pages <= 7) return Array.from({ length: pages }, function (_, i) { return i + 1; });
    var from = Math.max(2, current - 1);
    var to = Math.min(pages - 1, current + 1);
    if (current <= 3) { from = 2; to = 4; }
    if (current >= pages - 2) { from = pages - 3; to = pages - 1; }
    var out = [1];
    if (from > 2) out.push('gap');
    for (var p = from; p <= to; p++) out.push(p);
    if (to < pages - 1) out.push('gap');
    out.push(pages);
    return out;
  }

  function renderPagination(total, pages, start, count) {
    var nav = $('pagination');
    nav.hidden = total === 0;
    if (!total) return;
    $('pageRange').textContent = 'Rows ' + (start + 1) + '–' + (start + count) + ' of ' + total;
    $('pageSize').value = String(queue.pageSize);

    var box = $('pageButtons');
    box.replaceChildren();
    function pageButton(label, page, key, disabled, current, aria) {
      var b = el('button', '', label);
      b.type = 'button';
      b.dataset.page = String(page);
      b.dataset.key = key;
      b.disabled = !!disabled;
      if (aria) b.setAttribute('aria-label', aria);
      if (current) b.setAttribute('aria-current', 'page');
      box.appendChild(b);
    }
    pageButton('‹ Previous', queue.page - 1, 'prev', queue.page === 1, false, 'Previous page');
    pageWindow(queue.page, pages).forEach(function (p) {
      if (p === 'gap') box.appendChild(el('span', 'page-gap', '…'));
      else pageButton(String(p), p, 'p' + p, false, p === queue.page, 'Page ' + p + ' of ' + pages);
    });
    pageButton('Next ›', queue.page + 1, 'next', queue.page === pages, false, 'Next page');

    // Redrawing the buttons drops keyboard focus, so put it back on the one that was used.
    if (queue.focusKey) {
      var target = box.querySelector('[data-key="' + queue.focusKey + '"]:not(:disabled)') || box.querySelector('[aria-current="page"]');
      if (target) target.focus();
      queue.focusKey = null;
    }
  }

  /* ---------- selecting and bulk actions ---------- */

  // Actions the person may take on EVERY selected bill (for example approve and reject on bills waiting on them).
  function commonActions(rows) {
    if (!rows.length) return [];
    var keys = Hub.Workflow.availableActions(queue.user, rows[0]);
    rows.slice(1).forEach(function (r) {
      var mine = Hub.Workflow.availableActions(queue.user, r);
      keys = keys.filter(function (k) { return mine.indexOf(k) !== -1; });
    });
    return keys;
  }

  // Keeps the "select all" box, the bulk bar and its buttons in step with what is ticked.
  function updateSelectionUi() {
    var selectable = canSelect();
    var rows = selectedRows();
    var pageRows = currentPageRows();
    var ticked = pageRows.filter(function (r) { return queue.selected[r.id]; }).length;

    var all = $('selectPage');
    all.checked = pageRows.length > 0 && ticked === pageRows.length;
    all.indeterminate = ticked > 0 && ticked < pageRows.length;

    var bar = $('bulkBar');
    bar.hidden = !selectable || rows.length === 0;
    if (bar.hidden) return;

    $('bulkCount').textContent = rows.length + (rows.length === 1 ? ' bill selected' : ' bills selected');
    var actions = commonActions(rows);
    var buttons = $('bulkButtons');
    buttons.replaceChildren();
    actions.forEach(function (key) {
      var b = el('button', 'button ' + (key === 'reject' ? 'button-danger' : 'button-primary'), Hub.Workflow.ACTIONS[key].label + ' (' + rows.length + ')');
      b.type = 'button';
      b.addEventListener('click', function () { openBulk(key); });
      buttons.appendChild(b);
    });
    $('bulkNote').textContent = actions.length ? '' : 'No single action applies to all of these bills. Select bills at the same stage.';

    // Offer to select everything that matches the filters when only this page is ticked.
    var more = $('bulkSelectAll');
    var everyPageTicked = pageRows.length > 0 && ticked === pageRows.length;
    more.hidden = !(everyPageTicked && queue.shown.length > pageRows.length && rows.length < queue.shown.length);
    more.textContent = 'Select all ' + queue.shown.length + ' matching bills';
  }

  $('selectPage').addEventListener('change', function () {
    var on = this.checked;
    currentPageRows().forEach(function (r) { if (on) queue.selected[r.id] = true; else delete queue.selected[r.id]; });
    renderQueue();
  });
  $('bulkSelectAll').addEventListener('click', function () {
    queue.shown.forEach(function (r) { queue.selected[r.id] = true; });
    renderQueue();
  });
  $('bulkClear').addEventListener('click', function () {
    queue.selected = {};
    renderQueue();
  });

  $('pageButtons').addEventListener('click', function (event) {
    var button = event.target.closest('button[data-page]');
    if (!button || button.disabled) return;
    queue.page = Number(button.dataset.page);
    queue.focusKey = button.dataset.key;
    renderQueue();
  });
  $('pageSize').addEventListener('change', function () {
    queue.pageSize = Number(this.value) || 10;
    queue.page = 1;
    renderQueue();
  });

  function renderDashboard(user, keepFilters) {
    var dashboard = buildDashboard(user);
    var role = Hub.ROLES[user.role];

    $('roleEyebrow').textContent = role.label + (user.department ? ' · ' + user.department : '');
    $('dashboardHeading').textContent = greetingFor(centralHour(new Date())) + ', ' + user.name.split(/\s+/)[0];
    $('dashboardIntro').textContent = dashboard.intro;
    $('signedInAs').textContent = user.name + ' (' + user.email + ')';

    renderKpis(dashboard.kpis);
    setupQueue(dashboard, user, keepFilters);

    var admin = user.role === 'admin';
    $('settingsButton').hidden = admin; // administrators have no mailbox to set up
    $('adminPanel').hidden = !admin;
    if (admin) renderPeople(user);

    // Administrators see totals only; their inbox is not part of the dashboard.
    Hub.InboxUI.render(admin ? null : user, {
      rows: queue.allRows,
      openInvoice: openDetailById,
      requireUser: requireUser,
      // Called after a rule is added or removed, so the summary figures above redraw too.
      changed: function () { var current = Access.currentUser(); if (current) renderDashboard(current, true); }
    });
  }

  /* ---------- rendering: invoice detail ---------- */

  var detail = { id: null, index: -1 }; // index is the position in the filtered list, or -1 if opened from search
  var dialog = $('detailDialog');

  function formatDate(date) {
    return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  }

  function plural(n, word) { return n + ' ' + word + (n === 1 ? '' : 's'); }

  function addFact(list, label, value, className) {
    var wrap = el('div', 'fact');
    wrap.appendChild(el('dt', '', label));
    wrap.appendChild(el('dd', className || '', value));
    list.appendChild(wrap);
  }

  function renderDetail(r) {
    $('detailType').textContent = Hub.TYPE_LABELS[r.type];
    $('detailTitle').textContent = r.id;
    $('detailSubtitle').textContent = r.party + ' · ' + currency.format(r.amount);

    // Plain-language status against the same thresholds the table flags use.
    var flagBox = $('detailFlag');
    var notes = [];
    var kind = 'ok';
    if (r.stageDays > STAGE_ALERT_DAYS) { notes.push('Held ' + plural(r.stageDays, 'day') + ' in ' + r.stage + ', over the ' + STAGE_ALERT_DAYS + '-day guideline.'); kind = 'warn'; }
    if (r.ageDays > AGED_DAYS) { notes.push('Received ' + plural(r.ageDays, 'day') + ' ago, older than ' + AGED_DAYS + ' days.'); kind = 'alert'; }
    flagBox.className = 'detail-flag is-' + kind;
    flagBox.textContent = notes.length ? notes.join(' ') : 'On track: within the aging guidelines.';

    var steps = Hub.STAGES[r.type];
    var current = steps.indexOf(r.stage);
    var track = $('detailTrack');
    track.replaceChildren();
    steps.forEach(function (name, i) {
      var state = i < current ? 'done' : i === current ? 'current' : 'todo';
      var li = el('li', 'step is-' + state);
      if (state === 'current') li.setAttribute('aria-current', 'step');
      li.appendChild(el('span', 'step-dot', state === 'done' ? '✓' : String(i + 1)));
      li.appendChild(el('span', 'step-name', name));
      if (state !== 'todo') li.appendChild(el('span', 'visually-hidden', state === 'done' ? ' (completed)' : ' (current stage)'));
      track.appendChild(li);
    });

    showActionStatus('');
    renderActions(r);

    // Newest first: the latest change is what people usually want to see.
    var history = $('detailHistory');
    history.replaceChildren();
    r.history.slice().reverse().forEach(function (h) {
      var li = el('li', 'history-item is-' + h.action);
      li.appendChild(el('span', 'history-dot'));
      var body = el('div');
      body.appendChild(el('strong', '', Hub.Workflow.describe(h)));
      var when = formatDate(h.at) + (h.by ? ' at ' + h.at.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }) : '');
      body.appendChild(el('span', 'history-meta', when + ' · ' + h.actor));
      if (h.note) body.appendChild(el('p', 'history-note', '“' + h.note + '”'));
      li.appendChild(body);
      history.appendChild(li);
    });

    var facts = $('detailFacts');
    facts.replaceChildren();
    addFact(facts, r.type === 'AP' ? 'Vendor' : 'Customer', r.party);
    addFact(facts, 'Amount', currency.format(r.amount));
    addFact(facts, 'Current stage', r.stage);
    addFact(facts, 'Department', r.department);
    addFact(facts, 'Received', formatDate(r.receivedOn));
    addFact(facts, 'In this stage since', formatDate(r.stageSince));
    addFact(facts, 'Total age', plural(r.ageDays, 'day'));
    addFact(facts, 'Days in stage', plural(r.stageDays, 'day'));

    var source = $('detailSource');
    source.replaceChildren();
    addFact(source, 'Outlook folder', Hub.FOLDERS[r.type], 'mono');
    if (r.type === 'AR') {
      addFact(source, 'Email subject', 'Invoice ' + r.id + ' | ' + r.party + ' | ' + r.document, 'mono');
      addFact(source, 'BOL', r.document, 'mono');
    } else {
      addFact(source, 'Attachment', r.document, 'mono');
    }
    $('detailSourceNote').textContent = r.type === 'AR'
      ? 'Sample record. In the live service the customer, invoice number and BOL are read from the email subject.'
      : 'Sample record. In the live service the vendor and invoice details are read from the attachment filename and email details.';

    var inList = detail.index >= 0;
    $('detailPosition').textContent = inList ? (detail.index + 1) + ' of ' + queue.shown.length : 'Not in the current filtered list';
    $('detailPrev').disabled = !inList || detail.index === 0;
    $('detailNext').disabled = !inList || detail.index >= queue.shown.length - 1;
  }

  /* ---------- invoice actions (approve, reject, send for approval) ---------- */

  function showActionStatus(message, kind) {
    var box = $('actionStatus');
    box.textContent = message;
    box.className = 'form-status' + (kind ? ' is-' + kind : '');
    box.hidden = !message;
  }

  function setActionError(message) {
    var box = $('actionError');
    box.textContent = message || '';
    box.hidden = !message;
    if (message) $('actionNote').setAttribute('aria-invalid', 'true'); else $('actionNote').removeAttribute('aria-invalid');
  }

  // Buttons appear only for actions this person may take on this invoice right now.
  function renderActions(r) {
    var keys = queue.user ? Hub.Workflow.availableActions(queue.user, r) : [];
    $('detailActions').hidden = keys.length === 0;
    $('actionNote').value = '';
    setActionError('');
    var buttons = $('actionButtons');
    buttons.replaceChildren();
    if (!keys.length) return;

    var needsReason = keys.some(function (k) { return Hub.Workflow.ACTIONS[k].noteRequired; });
    $('actionNoteLabel').textContent = needsReason ? 'Comment (required to reject)' : 'Comment (optional)';
    keys.forEach(function (key) {
      var btn = el('button', 'button ' + (key === 'reject' ? 'button-danger' : 'button-primary'), Hub.Workflow.ACTIONS[key].label);
      btn.type = 'button';
      btn.addEventListener('click', function () { performAction(key); });
      buttons.appendChild(btn);
    });
  }

  function performAction(key) {
    var user = requireUser();
    if (!user) return;
    var r = queue.allRows.filter(function (x) { return x.id === detail.id; })[0];
    if (!r) return;

    var rule = Hub.Workflow.ACTIONS[key];
    var note = $('actionNote').value;
    setActionError('');
    showActionStatus('');

    if (rule.noteRequired && note.trim().length < Hub.Workflow.REJECT_NOTE_MIN) {
      setActionError('Add a reason of at least ' + Hub.Workflow.REJECT_NOTE_MIN + ' characters so the AP team knows what to fix.');
      $('actionNote').focus();
      return;
    }
    if (!window.confirm(rule.label + ' ' + r.id + ' (' + r.party + ', ' + currency.format(r.amount) + ')?')) return;

    var result = Hub.Workflow.act(user.email, r.id, key, note, r.stage);
    if (!result.ok) {
      if (result.field === 'note') { setActionError(result.error); return; }
      if (result.stale) { renderDashboard(user, true); openDetailById(r.id); }
      showActionStatus(result.error, 'warning');
      return;
    }

    renderDashboard(user, true);   // refresh the table and totals behind the dialog
    if (openDetailById(r.id)) {    // redraw this invoice with its new stage and history
      showActionStatus(rule.done + '. ' + r.id + ' is now in ' + result.to + '.', 'success');
      $('detailClose').focus();
    } else {
      closeDetail();
    }
  }

  // Only records already in this person's own table can be opened, from the table or from search.
  function openDetailById(id) {
    var index = -1;
    queue.shown.forEach(function (r, i) { if (r.id === id) index = i; });
    var record = index !== -1 ? queue.shown[index] : queue.allRows.filter(function (r) { return r.id === id; })[0];
    if (!record) return false;
    detail.id = id;
    detail.index = index;
    renderDetail(record);
    if (!dialog.open) dialog.showModal();
    return true;
  }

  function openDetail(index) {
    var r = queue.shown[index];
    if (r) openDetailById(r.id);
  }

  function closeDetail() {
    if (dialog.open) dialog.close();
  }

  $('detailClose').addEventListener('click', closeDetail);
  $('detailPrev').addEventListener('click', function () { openDetail(detail.index - 1); });
  $('detailNext').addEventListener('click', function () { openDetail(detail.index + 1); });
  dialog.addEventListener('click', function (event) {
    if (event.target === dialog) closeDetail(); // click on the backdrop
  });

  /* ---------- filter events ---------- */

  function firstPageAgain() { queue.page = 1; renderQueue(); }
  $('filterBar').addEventListener('input', firstPageAgain);
  $('filterBar').addEventListener('change', firstPageAgain);
  $('filterBar').addEventListener('submit', function (event) { event.preventDefault(); });
  $('clearFilters').addEventListener('click', function () {
    var sort = $('filterSort').value; // clearing filters keeps the chosen sort order
    $('filterBar').reset();
    $('filterSort').value = sort;
    firstPageAgain();
    $('filterSearch').focus();
  });

  /* ---------- email lineage: the email(s) a PDF was taken from ---------- */

  var lineageDialog = $('lineageDialog');

  function openLineage(record, emails) {
    $('lineageFile').textContent = record.document;
    var list = $('lineageList');
    list.replaceChildren();
    var hasLink = false;
    emails.forEach(function (m) {
      var li = el('li', 'lineage-item');
      li.appendChild(el('strong', '', m.subject || '(no subject)'));
      li.appendChild(el('span', 'lineage-meta', 'From ' + m.from.split(' <')[0] + ' · ' + formatDate(m.receivedOn) + ' · ' + m.folder));
      if (m.to) li.appendChild(el('span', 'lineage-meta', 'To ' + m.to.split('; ').map(function (p) { return p.split(' <')[0]; }).join(', ')));
      if (m.webLink) {
        hasLink = true;
        var link = el('a', 'lineage-link', 'Open in Outlook');
        link.href = m.webLink;        // only secure links get this far (see hub-mail.js)
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        li.appendChild(link);
      }
      list.appendChild(li);
    });
    $('lineageNote').textContent = hasLink ? '' : 'With real Outlook mail, each email here will have an "Open in Outlook" link to the original message.';
    if (!lineageDialog.open) lineageDialog.showModal();
  }

  $('lineageClose').addEventListener('click', function () { lineageDialog.close(); });
  lineageDialog.addEventListener('click', function (event) { if (event.target === lineageDialog) lineageDialog.close(); });

  /* ---------- bulk actions ---------- */

  var bulkDialog = $('bulkDialog');
  var bulk = { key: null, items: [] };
  var BULK_LIST_SHOWN = 8;

  function showBulkError(message) {
    var box = $('bulkError');
    box.textContent = message || '';
    box.hidden = !message;
    if (message) $('bulkNoteInput').setAttribute('aria-invalid', 'true'); else $('bulkNoteInput').removeAttribute('aria-invalid');
  }

  function openBulk(key) {
    var user = requireUser();
    if (!user) return;
    var rows = selectedRows();
    if (!rows.length || commonActions(rows).indexOf(key) === -1) { updateSelectionUi(); return; }

    var rule = Hub.Workflow.ACTIONS[key];
    bulk.key = key;
    bulk.items = rows.map(function (r) { return { id: r.id, stage: r.stage }; }); // the stage the person saw, so a stale page is caught
    var total = rows.reduce(function (sum, r) { return sum + r.amount; }, 0);

    $('bulkTitle').textContent = rule.label + ' ' + rows.length + (rows.length === 1 ? ' bill' : ' bills');
    $('bulkSummary').textContent = 'Total ' + currency.format(total) + '. Each bill is checked again before it changes.';
    var list = $('bulkItems');
    list.replaceChildren();
    rows.slice(0, BULK_LIST_SHOWN).forEach(function (r) {
      var li = el('li');
      li.appendChild(el('span', '', r.id + ' · ' + r.party));
      li.appendChild(el('span', '', currency.format(r.amount)));
      list.appendChild(li);
    });
    if (rows.length > BULK_LIST_SHOWN) list.appendChild(el('li', '', 'and ' + (rows.length - BULK_LIST_SHOWN) + ' more'));

    $('bulkNoteLabel').textContent = rule.noteRequired ? 'Reason (required, added to every bill)' : 'Comment (optional, added to every bill)';
    $('bulkNoteInput').value = '';
    $('bulkConfirm').textContent = rule.label + ' ' + rows.length + (rows.length === 1 ? ' bill' : ' bills');
    $('bulkConfirm').className = 'button ' + (key === 'reject' ? 'button-danger' : 'button-primary');
    showBulkError('');
    $('bulkForm').hidden = false;
    $('bulkResult').hidden = true;
    if (!bulkDialog.open) bulkDialog.showModal();
  }

  $('bulkConfirm').addEventListener('click', function () {
    var user = requireUser();
    if (!user) return;
    var rule = Hub.Workflow.ACTIONS[bulk.key];
    var result = Hub.Workflow.actMany(user.email, bulk.items, bulk.key, $('bulkNoteInput').value);
    if (!result.ok) {
      showBulkError(result.error);
      if (result.field === 'note') $('bulkNoteInput').focus();
      return;
    }

    result.done.forEach(function (d) { delete queue.selected[d.id]; }); // bills that failed stay selected so they can be looked at
    renderDashboard(user, true);

    $('bulkForm').hidden = true;
    $('bulkResult').hidden = false;
    var box = $('bulkResultText');
    box.textContent = rule.done + ': ' + result.done.length + ' of ' + bulk.items.length + (bulk.items.length === 1 ? ' bill.' : ' bills.');
    box.className = 'form-status ' + (result.failed.length ? 'is-warning' : 'is-success');
    var failures = $('bulkFailures');
    failures.replaceChildren();
    result.failed.forEach(function (f) { failures.appendChild(el('li', '', f.id + ': ' + f.error)); });
    failures.hidden = !result.failed.length;
    $('bulkDone').focus();
  });

  function closeBulk() { if (bulkDialog.open) bulkDialog.close(); }
  $('bulkCancel').addEventListener('click', closeBulk);
  $('bulkClose').addEventListener('click', closeBulk);
  $('bulkDone').addEventListener('click', closeBulk);
  $('bulkNoteInput').addEventListener('input', function () { showBulkError(''); });

  /* ---------- rendering: access list (administrators) ---------- */

  function renderPeople(actor) {
    var body = $('peopleBody');
    body.replaceChildren();
    Access.list().forEach(function (person) {
      var tr = el('tr');
      tr.appendChild(el('td', '', person.name));
      tr.appendChild(el('td', 'mono', person.email));
      tr.appendChild(el('td', '', Hub.ROLES[person.role].label + (person.department ? ' · ' + person.department : '')));

      var cell = el('td', 'num');
      var isSelf = person.email === actor.email;
      var btn = el('button', 'link-button', 'Remove');
      btn.type = 'button';
      btn.setAttribute('aria-label', 'Remove ' + person.name);
      btn.disabled = isSelf;
      if (isSelf) btn.title = 'You cannot remove your own access';
      btn.addEventListener('click', function () { removePerson(person); });
      cell.appendChild(btn);
      tr.appendChild(cell);
      body.appendChild(tr);
    });
  }

  function fieldEls(key) {
    return { input: $('access' + key.charAt(0).toUpperCase() + key.slice(1)), error: $('access' + key.charAt(0).toUpperCase() + key.slice(1) + '-error') };
  }

  var ACCESS_FIELDS = ['email', 'name', 'role', 'department'];

  function setAccessError(key, message) {
    var f = fieldEls(key);
    f.error.textContent = message || '';
    f.error.hidden = !message;
    if (message) f.input.setAttribute('aria-invalid', 'true'); else f.input.removeAttribute('aria-invalid');
  }

  function showAccessStatus(message, kind) {
    var box = $('accessStatus');
    box.textContent = message;
    box.className = 'form-status' + (kind ? ' is-' + kind : '');
    box.hidden = !message;
  }

  /* ---------- views ---------- */

  // Empty everything tied to the previous person, in memory and in the page, so the
  // next person on a shared computer does not inherit it.
  function clearPersonalData() {
    queue.allRows = [];
    queue.shown = [];
    queue.user = null;
    queue.selected = {};
    queue.lineage = {};
    queue.page = 1;
    closeBulk();
    lineageDialog.close();
    $('bulkBar').hidden = true;
    $('pagination').hidden = true;
    detail.id = null;
    detail.index = -1;
    ['kpiGrid', 'queueBody', 'peopleBody', 'detailTrack', 'detailHistory', 'detailFacts', 'detailSource', 'actionButtons'].forEach(function (id) { $(id).replaceChildren(); });
    ['dashboardHeading', 'dashboardIntro', 'signedInAs', 'roleEyebrow', 'detailTitle', 'detailSubtitle', 'detailFlag', 'accessStatus', 'actionStatus', 'resetStatus'].forEach(function (id) { $(id).textContent = ''; });
    $('filterBar').reset();
    $('filterStage').length = 1;
    $('accessForm').reset();
    $('actionNote').value = '';
    $('departmentField').hidden = true;
    $('accessStatus').hidden = true;
    $('actionStatus').hidden = true;
    $('resetStatus').hidden = true;
    $('detailActions').hidden = true;
    Hub.InboxUI.clear();
    Hub.Mail.clear();               // nothing from one person's mail is kept for the next person
    Hub.SettingsUI.close();
    $('pageStatus').hidden = true;
    $('pageStatus').textContent = '';
    $('inboxPanel').hidden = true;
    $('lifecyclePanel').hidden = true;
  }

  function showSignIn(message) {
    closeDetail();
    clearPersonalData();
    dashboardView.hidden = true;
    signInView.hidden = false;
    var error = $('signInError');
    error.textContent = message || '';
    error.hidden = !message;
    if (message) $('signInEmail').focus();
  }

  function showDashboard(user) {
    signInView.hidden = true;
    dashboardView.hidden = false;
    // Start reading the person's mail first, so the page can say "loading" while it waits.
    var loading = user.role === 'admin' ? null : Hub.Mail.load(user);
    renderDashboard(user);
    $('dashboardHeading').focus();
    if (loading) {
      loading.then(function () {
        var current = Access.currentUser();
        if (current && current.email === user.email) renderDashboard(current, true); // fill in the mail figures
      });
    }
  }

  /* ---------- actions ---------- */

  // Re-check on every action so a person removed in another tab loses access at once.
  function requireUser() {
    var user = Access.currentUser();
    if (!user) showSignIn('Your session ended or your access was removed. Please sign in again.');
    return user;
  }

  function removePerson(person) {
    var actor = requireUser();
    if (!actor) return;
    if (!window.confirm('Remove access for ' + person.name + ' (' + person.email + ')?')) return;
    var result = Access.remove(actor.email, person.email);
    if (!result.ok) { showAccessStatus(result.error, 'warning'); return; }
    showAccessStatus(person.name + ' no longer has access.', 'success');
    renderDashboard(actor);
  }

  $('resetWorkflow').addEventListener('click', function () {
    var actor = requireUser();
    if (!actor) return;
    if (!window.confirm('Return every sample invoice to its starting stage and erase the approvals and rejections recorded in this browser?')) return;
    var result = Hub.Workflow.reset(actor.email);
    var box = $('resetStatus');
    box.textContent = result.ok ? 'Sample workflow reset.' : result.error;
    box.className = 'form-status ' + (result.ok ? 'is-success' : 'is-warning');
    box.hidden = false;
    if (result.ok) renderDashboard(actor, true);
  });

  $('signInForm').addEventListener('submit', function (event) {
    event.preventDefault();
    var result = Access.signIn($('signInEmail').value);
    if (!result.ok) { showSignIn(result.error); return; }
    $('signInForm').reset();
    showDashboard(result.user);
  });

  $('signOutButton').addEventListener('click', function () {
    Access.signOut();
    showSignIn('');
    $('signInEmail').focus();
  });

  $('accessRole').addEventListener('change', function () {
    $('departmentField').hidden = this.value !== 'approver';
  });

  $('accessForm').addEventListener('input', function (event) {
    var key = event.target.id.replace(/^access/, '').toLowerCase();
    if (ACCESS_FIELDS.indexOf(key) !== -1) setAccessError(key, '');
  });

  $('accessForm').addEventListener('submit', function (event) {
    event.preventDefault();
    var actor = requireUser();
    if (!actor) return;
    showAccessStatus('');
    ACCESS_FIELDS.forEach(function (k) { setAccessError(k, ''); });

    var result = Access.add(actor.email, {
      email: $('accessEmail').value,
      name: $('accessName').value,
      role: $('accessRole').value,
      department: $('accessDepartment').value
    });

    if (!result.ok) {
      if (result.errors) {
        var first;
        ACCESS_FIELDS.forEach(function (k) {
          if (result.errors[k]) { setAccessError(k, result.errors[k]); first = first || k; }
        });
        if (first) fieldEls(first).input.focus();
      } else {
        showAccessStatus(result.error, 'warning');
      }
      return;
    }

    $('accessForm').reset();
    $('departmentField').hidden = true;
    showAccessStatus(result.user.name + ' now has access as ' + Hub.ROLES[result.user.role].label + '.', 'success');
    renderDashboard(actor);
  });

  // After someone saves their settings, redraw their view (and reopen it on a new default date range).
  Hub.SettingsUI.onSaved = function (person, info) {
    var current = Access.currentUser();
    if (!current) return;
    if (info && info.rangeChanged) Hub.InboxUI.applyDefaultRange(current);
    renderDashboard(current, true);
  };

  /* ---------- initial render ---------- */

  function renderSignInHelp() {
    var help = $('demoHelp');
    if (!Hub.SHOW_DEMO_HINTS) return;
    var list = $('demoList');
    Hub.SEED_USERS.forEach(function (u) {
      var li = el('li');
      var btn = el('button', 'demo-chip', u.email);
      btn.type = 'button';
      btn.addEventListener('click', function () { $('signInEmail').value = u.email; $('signInEmail').focus(); });
      li.appendChild(btn);
      li.appendChild(el('span', '', Hub.ROLES[u.role].label + (u.department ? ' · ' + u.department : '')));
      list.appendChild(li);
    });
    help.hidden = false;
  }

  renderSignInHelp();
  var existing = Access.currentUser();
  if (existing) showDashboard(existing); else showSignIn('');
})();
