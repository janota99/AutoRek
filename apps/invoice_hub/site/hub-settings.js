/*
 * Per-person settings: how this person's mailbox identifies each stage of a vendor bill, how
 * their PDF filenames are written, and how their dashboard opens.
 *
 * Everyone starts from the organization defaults (Hub.DEFAULT_SETTINGS) and can change their own.
 * The settings become rules automatically (Settings.derivedRules), so a change takes effect at
 * once. Several senders, recipients or subject words can be listed, separated by commas; a
 * message matches if it fits any of them.
 *
 * Filename patterns use three placeholders: {vendor}, {invoice} and {bol}. Everything else is
 * literal text, so "{vendor} {invoice} - BOL {bol}" reads "Shoppa's WW12345- BOL 12346.pdf".
 *
 * PROTOTYPE ONLY: settings are stored in this browser. The real service would keep them on the
 * person's account.
 */
(function (global) {
  'use strict';

  var Hub = global.InvoiceHub;
  var Store = Hub.Store;
  var KEY = 'invoiceHub.settings.v1';

  var STAGES = ['received', 'approved', 'sent'];
  var FIELDS = ['sender', 'recipient', 'subject', 'folder'];
  var LIST_MAX = 5;            // options per field
  var ITEM_MAX = 60;           // characters per option
  var RULES_PER_STAGE = 12;    // after combining the lists
  var PATTERN_MAX = 80;
  var RANGE_CHOICES = [0, 30, 60, 90, 180, 365];

  /* ---------- small helpers ---------- */

  function clone(o) { return JSON.parse(JSON.stringify(o)); }

  function folderKey(value) {
    return String(value || '').split('/').map(function (p) { return p.trim().toLowerCase(); }).filter(Boolean).join(' / ');
  }

  // "Teresia Weaver, tweaver@x.com" -> ["Teresia Weaver", "tweaver@x.com"] (no blanks, no repeats)
  function splitList(value) {
    var seen = {};
    var out = [];
    String(value || '').split(/[,;\n]/).forEach(function (part) {
      var t = part.trim();
      if (t && !seen[t.toLowerCase()]) { seen[t.toLowerCase()] = true; out.push(t); }
    });
    return out;
  }

  /* ---------- filename patterns ---------- */

  var TOKEN_SOURCE = {
    vendor:  '(.+?)',
    invoice: '((?=[A-Za-z0-9-]*\\d)[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)', // letters, digits and inner hyphens, at least one digit
    bol:     '([A-Za-z0-9-]+)'
  };

  // Text between placeholders: spaces are flexible (zero or more), other characters must match exactly.
  function escapeLiteral(text) {
    return text.split(/(\s+)/).map(function (piece) {
      return /^\s+$/.test(piece) ? '\\s*' : piece.replace(/[.*+?^${}()|[\]\\\/-]/g, '\\$&');
    }).join('');
  }

  var compiledCache = {};

  /** Turns a pattern such as "{vendor} {invoice} - BOL {bol}" into a matcher, or explains what is wrong. */
  function compilePattern(pattern) {
    var text = String(pattern || '').trim();
    if (compiledCache[text]) return compiledCache[text];
    var result = buildPattern(text);
    compiledCache[text] = result;
    return result;
  }

  function buildPattern(text) {
    if (!text) return { ok: false, error: 'Enter a filename pattern.' };
    if (text.length > PATTERN_MAX) return { ok: false, error: 'Keep the pattern to ' + PATTERN_MAX + ' characters or fewer.' };

    var order = [];
    var seen = {};
    var source = '^';
    var parts = text.split(/(\{[^}]*\})/).filter(function (p) { return p !== ''; });
    for (var i = 0; i < parts.length; i++) {
      var part = parts[i];
      var token = /^\{([^}]*)\}$/.exec(part);
      if (token) {
        var name = token[1].trim().toLowerCase();
        if (!TOKEN_SOURCE[name]) return { ok: false, error: 'Unknown placeholder {' + token[1] + '}. Use {vendor}, {invoice} or {bol}.' };
        if (seen[name]) return { ok: false, error: '{' + name + '} can only appear once.' };
        seen[name] = true;
        order.push(name);
        source += TOKEN_SOURCE[name];
      } else {
        if (/[{}]/.test(part)) return { ok: false, error: 'Check the curly braces: placeholders look like {vendor}.' };
        source += /^\s+$/.test(part) ? '\\s+' : escapeLiteral(part); // a bare gap between two placeholders needs a real space
      }
    }
    if (!seen.vendor || !seen.invoice) return { ok: false, error: 'The pattern needs {vendor} and {invoice}. {bol} is optional.' };
    try {
      return { ok: true, regex: new RegExp(source + '$', 'i'), order: order };
    } catch (err) {
      return { ok: false, error: 'That pattern could not be used.' };
    }
  }

  /** Reads a PDF filename with a pattern. Names that do not fit are reported, never dropped. */
  function parseFileName(fileName, pattern) {
    var base = String(fileName || '').replace(/\.pdf$/i, '').trim().replace(/\s+/g, ' ');
    var compiled = compilePattern(pattern || Hub.DEFAULT_SETTINGS.filenamePattern);
    var m = compiled.ok ? compiled.regex.exec(base) : null;
    if (!m) return { recognized: false, vendor: base, invoice: '', bol: '' };
    var out = { recognized: true, vendor: '', invoice: '', bol: '' };
    compiled.order.forEach(function (name, i) { out[name] = m[i + 1].trim(); });
    return out;
  }

  /* ---------- stored settings ---------- */

  function loadAll() {
    try {
      var saved = JSON.parse(Store.read('localStorage', KEY));
      return saved && typeof saved === 'object' && !Array.isArray(saved) ? saved : {};
    } catch (err) {
      return {};
    }
  }

  function saveAll(all) {
    Store.write('localStorage', KEY, JSON.stringify(all));
  }

  // Fills in anything missing from the base settings. A field the person deliberately left blank stays blank.
  function normalize(raw, base) {
    var out = { filenamePattern: base.filenamePattern, defaultRangeDays: base.defaultRangeDays };
    STAGES.forEach(function (stage) {
      out[stage] = {};
      FIELDS.forEach(function (field) {
        var given = raw && raw[stage] && typeof raw[stage][field] === 'string' ? raw[stage][field] : null;
        out[stage][field] = (given !== null ? given : (base[stage] && base[stage][field]) || '').trim();
      });
    });
    if (raw && typeof raw.filenamePattern === 'string') out.filenamePattern = raw.filenamePattern.trim();
    if (raw && RANGE_CHOICES.indexOf(Number(raw.defaultRangeDays)) !== -1) out.defaultRangeDays = Number(raw.defaultRangeDays);
    return out;
  }

  function defaults() { return normalize(Hub.DEFAULT_SETTINGS, { filenamePattern: '', defaultRangeDays: 0 }); }

  /** This person's settings: the organization defaults, changed by whatever they have saved. */
  function get(user) {
    return normalize(loadAll()[user.email], defaults());
  }

  /** Problems with a settings object, keyed like "approved.sender", "filenamePattern". Empty means fine. */
  function validate(settings, options) {
    var errors = {};
    STAGES.forEach(function (stage) {
      var combinations = 1;
      ['sender', 'recipient', 'subject'].forEach(function (field) {
        var items = splitList(settings[stage][field]);
        if (settings[stage][field].length > 300) errors[stage + '.' + field] = 'That is too long.';
        else if (items.length > LIST_MAX) errors[stage + '.' + field] = 'Use up to ' + LIST_MAX + ' options.';
        else if (items.some(function (i) { return i.length > ITEM_MAX; })) errors[stage + '.' + field] = 'Each option can be up to ' + ITEM_MAX + ' characters.';
        combinations *= Math.max(1, items.length);
      });
      if (combinations > RULES_PER_STAGE && !errors[stage + '.sender']) errors[stage + '.sender'] = 'Too many combinations of senders, recipients and subjects. Use fewer options.';
      var folder = settings[stage].folder;
      if (folder && options && options.folders && !options.folders.some(function (f) { return folderKey(f) === folderKey(folder); })) {
        errors[stage + '.folder'] = 'That folder was not found in your mailbox. Leave it blank to search every folder.';
      }
    });
    var pattern = compilePattern(settings.filenamePattern);
    if (!pattern.ok) errors.filenamePattern = pattern.error;
    return errors;
  }

  /**
   * Saves this person's settings if they are valid. `data` is their complete settings: anything left out goes back to
   * the organization default. options.folders: the person's folder paths, to check folder names.
   */
  function save(user, data, options) {
    var settings = normalize(data, defaults());
    var errors = validate(settings, options);
    if (Object.keys(errors).length) return { ok: false, errors: errors };
    var all = loadAll();
    if (JSON.stringify(settings) === JSON.stringify(defaults())) delete all[user.email]; // same as the defaults: nothing to keep
    else all[user.email] = settings;
    saveAll(all);
    return { ok: true, settings: settings };
  }

  function reset(user) {
    var all = loadAll();
    delete all[user.email];
    saveAll(all);
    return { ok: true, settings: defaults() };
  }

  function isCustomized(user) {
    return !!loadAll()[user.email];
  }

  /** The settings as text, to copy to a colleague. */
  function exportJson(user) {
    return JSON.stringify(get(user), null, 2);
  }

  /** Loads settings copied from someone else. */
  function importJson(user, text, options) {
    var parsed;
    try { parsed = JSON.parse(text); } catch (err) { return { ok: false, errors: { share: 'That is not valid settings text.' } }; }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return { ok: false, errors: { share: 'That is not valid settings text.' } };
    return save(user, parsed, options);
  }

  /**
   * The rules these settings describe, one per combination of sender, recipient and subject, for this
   * person's own mailbox. A stage with every field blank is switched off.
   */
  function derivedRules(user) {
    var settings = get(user);
    var rules = [];
    STAGES.forEach(function (stage) {
      var s = settings[stage];
      var senders = splitList(s.sender), recipients = splitList(s.recipient), subjects = splitList(s.subject);
      if (!senders.length && !recipients.length && !subjects.length && !s.folder) return;
      var n = 0;
      (senders.length ? senders : ['']).forEach(function (sender) {
        (recipients.length ? recipients : ['']).forEach(function (recipient) {
          (subjects.length ? subjects : ['']).forEach(function (subject) {
            var rule = { id: 'setting-' + stage + '-' + n++, mailbox: user.email, folder: s.folder, queue: 'AP', stage: stage, derived: true };
            if (sender) rule.sender = sender;
            if (recipient) rule.recipient = recipient;
            if (subject) rule.subject = subject;
            rules.push(rule);
          });
        });
      });
    });
    return rules;
  }

  Hub.Settings = {
    STAGES: STAGES,
    FIELDS: FIELDS,
    RANGE_CHOICES: RANGE_CHOICES,
    splitList: splitList,
    compilePattern: compilePattern,
    parseFileName: parseFileName,
    defaults: defaults,
    get: get,
    validate: validate,
    save: save,
    reset: reset,
    isCustomized: isCustomized,
    exportJson: exportJson,
    importJson: importJson,
    derivedRules: derivedRules
  };
})(window);
