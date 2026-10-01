/*
 * Inbox rules and message search.
 *
 * A "rule" (called a mapping) says how to recognize one stage of a vendor bill's journey in
 * a person's own mailbox: which folder to look in (or any folder), who sent it, who it was
 * sent to, and what its subject says. Folders differ from person to person, so the rules lean
 * on sender, recipient and subject, which are the same for everyone.
 *
 * A bill is one PDF attachment. Its filename ("Vendor INVOICE- BOL NUMBER.pdf") identifies
 * it, so the same bill is recognized in the received, approved and sent emails. File contents
 * are never read, only the names.
 *
 * PROTOTYPE ONLY: it searches the fictitious messages in hub-data.js. The live service will
 * read Outlook through Microsoft Graph on a server or in the browser, where the signed-in
 * person's own permissions decide which mailbox they can read.
 */
(function (global) {
  'use strict';

  var Hub = global.InvoiceHub;
  var Store = Hub.Store;
  var MAPPINGS_KEY = 'invoiceHub.inboxMappings.v1';
  var MAX_MAPPINGS = 12;
  var TEXT_MAX = 60;
  var EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  var SNIPPET_RADIUS = 60;
  var SKIPPED_FOLDERS = /deleted items|junk/i; // "any folder" rules leave these out

  // A vendor bill's journey: received from the vendor -> approved (PO and approval done, ready
  // for GL coding) -> GL coded and sent to Accounts Payable.
  var STAGES = ['received', 'approved', 'sent'];
  var STAGE_SHORT = { received: 'Received', approved: 'Approved', sent: 'GL coded, sent to AP' };
  var STAGE_LONG = { received: 'Bills received from the vendor', approved: 'Bills approved, ready for GL coding', sent: 'Bills GL coded and sent to AP' };

  /* ---------- helpers ---------- */

  function normEmail(value) { return String(value || '').trim().toLowerCase(); }
  function lower(value) { return String(value || '').trim().toLowerCase(); }

  // "inbox/payables/ bills" -> "inbox / payables / bills" (used only for comparing)
  function folderKey(value) {
    return String(value || '').split('/').map(function (p) { return p.trim().toLowerCase(); }).filter(Boolean).join(' / ');
  }

  function isPdf(attachment) { return /\.pdf$/i.test(attachment.name); }

  /* ---------- who can map what ---------- */

  /** The mailboxes this person can read (from the mail layer), each with its folder paths. */
  function accessibleMailboxes(user) {
    return Hub.Mail.mailboxes(user);
  }

  function findMailbox(user, email) {
    var wanted = normEmail(email);
    return accessibleMailboxes(user).filter(function (m) { return m.email.toLowerCase() === wanted; })[0] || null;
  }

  /* ---------- stored rules (one list per person) ---------- */

  function loadAll() {
    try {
      var saved = JSON.parse(Store.read('localStorage', MAPPINGS_KEY));
      return saved && typeof saved === 'object' && !Array.isArray(saved) ? saved : {};
    } catch (err) {
      return {};
    }
  }

  function saveAll(all) {
    Store.write('localStorage', MAPPINGS_KEY, JSON.stringify(all));
  }

  function optionalText(v) { return v === undefined || typeof v === 'string'; }

  function isValidMapping(m) {
    return m && typeof m.id === 'string' && typeof m.mailbox === 'string' && typeof m.folder === 'string' &&
      (m.queue === 'AP' || m.queue === 'AR') && (m.stage === undefined || STAGES.indexOf(m.stage) !== -1) &&
      optionalText(m.sender) && optionalText(m.recipient) && optionalText(m.subject);
  }

  // A vendor-bill rule without a saved stage counts as "received".
  function stageOf(m) {
    return m.queue === 'AP' ? (STAGES.indexOf(m.stage) !== -1 ? m.stage : 'received') : null;
  }
  function shortLabel(m) { return m.queue === 'AR' ? 'AR' : STAGE_SHORT[stageOf(m)]; }
  function longLabel(m) { return m.queue === 'AR' ? 'Customer invoices' : STAGE_LONG[stageOf(m)]; }

  // One line describing a rule, for lists.
  function describe(m) {
    var parts = [m.folder ? m.folder : 'Any folder'];
    if (m.sender) parts.push('from “' + m.sender + '”');
    if (m.recipient) parts.push('to “' + m.recipient + '”');
    if (m.subject) parts.push('subject has “' + m.subject + '”');
    return parts.join(' · ');
  }

  function contains(text, part) {
    return !part || String(text || '').toLowerCase().indexOf(part.toLowerCase()) !== -1;
  }

  // Does this message match this rule? Same mailbox, the right folder (or any folder), and the
  // sender, recipient and subject each contain the rule's text (when the rule has any).
  function mappingFor(maps, msg) {
    return maps.filter(function (m) {
      var folderOk = m.folder ? folderKey(m.folder) === folderKey(msg.folder) : !SKIPPED_FOLDERS.test(msg.folder);
      return m.mailbox === msg.mailbox && folderOk &&
        contains(msg.from, m.sender) && contains(msg.to, m.recipient) && contains(msg.subject, m.subject);
    })[0] || null;
  }

  // Is this rule the same as another (mailbox, folder, holds, sender, recipient, subject)?
  function sameRule(m, r) {
    return m.mailbox === r.mailbox && folderKey(m.folder) === folderKey(r.folder) && m.queue === r.queue &&
      (m.stage || null) === (r.stage || null) &&
      lower(m.sender) === lower(r.sender) && lower(m.recipient) === lower(r.recipient) && lower(m.subject) === lower(r.subject);
  }

  function reachable(user, m) {
    var box = findMailbox(user, m.mailbox);
    return !!box && (!m.folder || box.folders.some(function (f) { return folderKey(f) === folderKey(m.folder); }));
  }

  /**
   * This person's rules: first the ones their settings describe (marked derived), then any extra
   * rules they added by hand. Rules whose mailbox or folder they can no longer reach are left out.
   */
  function mappings(user) {
    var mine = loadAll()[user.email];
    var extra = Array.isArray(mine) ? mine.filter(isValidMapping) : [];
    return Hub.Settings.derivedRules(user).concat(extra).filter(function (m) { return reachable(user, m); });
  }

  function add(user, data) {
    var errors = {};
    var email = normEmail(data.mailbox);
    var box = null;
    var folder = '';

    if (!EMAIL_PATTERN.test(email)) errors.mailbox = 'Enter the mailbox email address.';
    else {
      box = findMailbox(user, email);
      // Same message whether the mailbox is missing or just not shared with you.
      if (!box) errors.mailbox = 'No mailbox with that address is shared with you.';
    }

    // The folder is optional: blank means "any folder in this mailbox".
    var folderText = String(data.folder || '').trim();
    if (folderText && box) {
      folder = box.folders.filter(function (f) { return folderKey(f) === folderKey(folderText); })[0] || '';
      if (!folder) errors.folder = 'That folder was not found in ' + box.email + '. Leave it blank to search every folder.';
    }

    // data.kind is "AR" (customer invoices) or "AP:received" / "AP:approved" / "AP:sent" (vendor bills).
    var kind = String(data.kind || '');
    var queue = null;
    var stage = null;
    if (kind === 'AR') queue = 'AR';
    else if (/^AP:(received|approved|sent)$/.test(kind)) { queue = 'AP'; stage = kind.slice(3); }
    else errors.queue = 'Choose what these emails are.';

    var sender = String(data.sender || '').trim();
    var recipient = String(data.recipient || '').trim();
    var subject = String(data.subject || '').trim();
    [['sender', sender], ['recipient', recipient], ['subject', subject]].forEach(function (pair) {
      if (pair[1].length > TEXT_MAX) errors[pair[0]] = 'Keep this to ' + TEXT_MAX + ' characters or fewer.';
    });
    if (!folderText && !sender && !recipient && !subject && !errors.sender) {
      errors.sender = 'Add at least one rule: a folder, sender, recipient or subject text.';
    }

    var all = loadAll();
    var mine = Array.isArray(all[user.email]) ? all[user.email].filter(isValidMapping) : [];
    if (!Object.keys(errors).length) {
      var candidate = { mailbox: box.email, folder: folder, queue: queue, stage: stage, sender: sender, recipient: recipient, subject: subject };
      if (mine.some(function (m) { return sameRule(m, candidate); })) errors.queue = 'You already have this rule.';
      else if (mine.length >= MAX_MAPPINGS) errors.queue = 'You can have up to ' + MAX_MAPPINGS + ' rules.';
    }
    if (Object.keys(errors).length) return { ok: false, errors: errors };

    var mapping = { id: 'map-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6), mailbox: box.email, folder: folder, queue: queue };
    if (stage) mapping.stage = stage;
    if (sender) mapping.sender = sender;
    if (recipient) mapping.recipient = recipient;
    if (subject) mapping.subject = subject;
    mine.push(mapping);
    all[user.email] = mine;
    saveAll(all);
    return { ok: true, mapping: mapping };
  }

  function remove(user, id) {
    if (String(id).indexOf('setting-') === 0) return { ok: false, error: 'That rule comes from your settings. Change it in Settings.' };
    var all = loadAll();
    var mine = Array.isArray(all[user.email]) ? all[user.email] : [];
    var kept = mine.filter(function (m) { return m.id !== id; });
    if (kept.length === mine.length) return { ok: false, error: 'That rule was not found.' };
    all[user.email] = kept;
    saveAll(all);
    return { ok: true };
  }

  /* ---------- search ---------- */

  function escapeRegExp(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

  function termsOf(query) {
    return String(query || '').toLowerCase().split(/\s+/).filter(Boolean).slice(0, 8);
  }

  // A search word matches where a word starts, so "po" finds "PO 8840" but not "hypochlorite",
  // while a partial invoice number such as "INV-99" still finds "INV-9930".
  var WORD_START = '(?<![\\p{L}\\p{N}])';
  function termRegex(term, flags) { return new RegExp(WORD_START + escapeRegExp(term), flags || 'iu'); }

  // Short excerpt around the first search term found in a PDF's text (sample data may include text).
  function snippet(text, terms) {
    var at = -1;
    terms.forEach(function (t) { var m = termRegex(t).exec(text); if (m && (at === -1 || m.index < at)) at = m.index; });
    if (at === -1) return '';
    var from = Math.max(0, at - SNIPPET_RADIUS);
    var to = Math.min(text.length, at + SNIPPET_RADIUS);
    return (from > 0 ? '…' : '') + text.slice(from, to).trim() + (to < text.length ? '…' : '');
  }

  /**
   * Messages matching this person's rules that also match every search word in the subject,
   * sender, preview or attachment names. Newest first.
   * options: { query, mappingId (optional), pdfOnly }
   */
  function search(user, options) {
    var terms = termsOf(options.query);
    var maps = mappings(user).filter(function (m) { return !options.mappingId || m.id === options.mappingId; });
    var results = [];

    Hub.Mail.messages(user).forEach(function (msg) {
      var mapping = mappingFor(maps, msg);
      if (!mapping) return;

      var pdfs = msg.attachments.filter(isPdf);
      if (options.pdfOnly && !pdfs.length) return;

      var haystack = [msg.subject, msg.from, msg.to, msg.preview].concat(msg.attachments.map(function (a) { return a.name + ' ' + (a.text || ''); })).join(' ');
      if (!terms.every(function (t) { return termRegex(t).test(haystack); })) return;

      var pdfMatches = pdfs.map(function (a) { return { attachment: a, snippet: terms.length && a.text ? snippet(a.text, terms) : '' }; })
        .filter(function (m) { return m.snippet; });

      results.push({ message: msg, mapping: mapping, pdfMatches: pdfMatches });
    });

    return results.sort(function (a, b) { return b.message.receivedOn - a.message.receivedOn; });
  }

  /** Splits text into plain and matching pieces so the page can highlight without parsing HTML. */
  function highlightParts(text, terms) {
    if (!terms.length) return [{ text: text, match: false }];
    var longestFirst = terms.slice().sort(function (a, b) { return b.length - a.length; });
    var pattern = new RegExp(WORD_START + '(' + longestFirst.map(escapeRegExp).join('|') + ')', 'igu');
    // split() with one capture group alternates plain, match, plain, match, ...
    return text.split(pattern)
      .map(function (part, i) { return { text: part, match: i % 2 === 1 }; })
      .filter(function (part) { return part.text !== ''; });
  }

  /* ---------- vendor-bill lifecycle ---------- */

  // Reads a PDF filename with a pattern such as "{vendor} {invoice} - BOL {bol}" (see hub-settings.js).
  // With no pattern given, the organization's default is used. Names that do not fit come back as
  // not recognized, so they are never silently dropped.
  function parseFileName(fileName, pattern) {
    return Hub.Settings.parseFileName(fileName, pattern);
  }

  /**
   * Follows each vendor bill through this person's vendor-bill rules. A bill is one PDF filename;
   * the stage of each message comes from the rule it matches. Returns one record per bill:
   *   { vendor, invoice, bol, recognized, fileName, when: { received, approved, sent } (dates),
   *     status, messages }
   * where status is the furthest stage reached.
   */
  function lifecycle(user) {
    var maps = mappings(user).filter(function (m) { return m.queue === 'AP'; });
    var pattern = Hub.Settings.get(user).filenamePattern;
    var bills = {};

    Hub.Mail.messages(user).forEach(function (msg) {
      var mapping = mappingFor(maps, msg);
      if (!mapping) return;
      var stage = stageOf(mapping);
      msg.attachments.filter(isPdf).forEach(function (a) {
        var key = a.name.toLowerCase().replace(/\s+/g, ' ');
        var bill = bills[key] || (bills[key] = Object.assign({ fileName: a.name, when: {}, messages: [] }, parseFileName(a.name, pattern)));
        bill.messages.push({ stage: stage, message: msg, mapping: mapping });
        // If a stage has several messages (for example a reminder), keep the earliest date.
        if (!bill.when[stage] || msg.receivedOn < bill.when[stage]) bill.when[stage] = msg.receivedOn;
      });
    });

    return Object.keys(bills).map(function (key) {
      var bill = bills[key];
      bill.status = bill.when.sent ? 'sent' : bill.when.approved ? 'approved' : 'received';
      bill.messages.sort(function (a, b) { return a.message.receivedOn - b.message.receivedOn; });
      return bill;
    });
  }

  Hub.Inbox = {
    STAGES: STAGES,
    STAGE_SHORT: STAGE_SHORT,
    isPdf: isPdf,
    termsOf: termsOf,
    accessibleMailboxes: accessibleMailboxes,
    mappings: mappings,
    shortLabel: shortLabel,
    longLabel: longLabel,
    describe: describe,
    parseFileName: parseFileName,
    add: add,
    remove: remove,
    search: search,
    lifecycle: lifecycle,
    highlightParts: highlightParts
  };
})(window);
