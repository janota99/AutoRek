/*
 * The Settings dialog (gear icon): each person's own senders, recipients, subject words and folder for
 * the three stages of a vendor bill, their PDF filename pattern, and how their view opens.
 * All the rules live in hub-settings.js; this file draws the dialog and passes what was typed to it.
 *
 * dashboard.js sets Hub.SettingsUI.onSaved so the page redraws after settings change.
 */
(function () {
  'use strict';

  var Hub = window.InvoiceHub;
  var Settings = Hub.Settings;

  var $ = function (id) { return document.getElementById(id); };
  var dialog = $('settingsDialog');
  var user = null; // whose settings are open

  var STAGE_TITLES = {
    received: '1. Bill received from the vendor',
    approved: '2. Bill approved, ready for GL coding',
    sent: '3. Bill GL coded and sent to AP'
  };
  var STAGE_SHORT = { received: 'Received', approved: 'Approved', sent: 'GL coded' };
  var FIELD_LABELS = { sender: 'Sender contains', recipient: 'Recipient contains', subject: 'Subject contains', folder: 'Folder (optional)' };
  var EXAMPLES = {
    received: { sender: 'e.g., ACCTINV', recipient: '', subject: '', folder: 'Any folder' },
    approved: { sender: 'e.g., Teresia Weaver, tweaver@…', recipient: '', subject: 'e.g., Vendors', folder: 'Any folder' },
    sent:     { sender: '', recipient: 'e.g., Accounts Payable', subject: 'e.g., GL Coded', folder: 'Any folder' }
  };

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function cap(text) { return text.charAt(0).toUpperCase() + text.slice(1); }
  function fieldId(stage, field) { return 'set' + cap(stage) + cap(field); }

  /* ---------- building the form ---------- */

  function buildStages() {
    var host = $('settingsStages');
    var folders = el('datalist');
    folders.id = 'setFolderOptions';
    host.appendChild(folders);

    Settings.STAGES.forEach(function (stage) {
      var set = el('fieldset', 'stage-fieldset');
      set.appendChild(el('legend', '', STAGE_TITLES[stage]));
      var grid = el('div', 'stage-grid');
      Settings.FIELDS.forEach(function (field) {
        var wrap = el('div', 'stage-field');
        var label = el('label', '', FIELD_LABELS[field]);
        label.setAttribute('for', fieldId(stage, field));
        var input = el('input');
        input.id = fieldId(stage, field);
        input.type = 'text';
        input.maxLength = field === 'folder' ? 160 : 300;
        input.autocomplete = 'off';
        input.spellcheck = false;
        input.placeholder = EXAMPLES[stage][field];
        if (field === 'folder') input.setAttribute('list', 'setFolderOptions');
        wrap.appendChild(label);
        wrap.appendChild(input);
        grid.appendChild(wrap);
      });
      set.appendChild(grid);
      host.appendChild(set);
    });
  }

  function myFolders() {
    var list = [];
    Hub.Mail.mailboxes(user).forEach(function (box) { list = list.concat(box.folders); });
    return list;
  }

  function fill(settings) {
    Settings.STAGES.forEach(function (stage) {
      Settings.FIELDS.forEach(function (field) { $(fieldId(stage, field)).value = settings[stage][field]; });
    });
    $('setPattern').value = settings.filenamePattern;
    $('setRange').value = String(settings.defaultRangeDays);
  }

  function readForm() {
    var data = { filenamePattern: $('setPattern').value, defaultRangeDays: Number($('setRange').value) };
    Settings.STAGES.forEach(function (stage) {
      data[stage] = {};
      Settings.FIELDS.forEach(function (field) { data[stage][field] = $(fieldId(stage, field)).value; });
    });
    return data;
  }

  /* ---------- messages ---------- */

  function showStatus(message, kind) {
    var box = $('settingsStatus');
    box.textContent = message;
    box.className = 'form-status' + (kind ? ' is-' + kind : '');
    box.hidden = !message;
  }

  function friendly(key) {
    if (key === 'filenamePattern') return 'Filename pattern';
    if (key === 'share') return 'Copied settings';
    var parts = key.split('.');
    return STAGE_SHORT[parts[0]] + ', ' + FIELD_LABELS[parts[1]].toLowerCase().replace(' (optional)', '');
  }

  function inputFor(key) {
    if (key === 'filenamePattern') return $('setPattern');
    if (key === 'share') return $('setShare');
    var parts = key.split('.');
    return $(fieldId(parts[0], parts[1]));
  }

  function clearErrors() {
    var box = $('settingsErrors');
    box.replaceChildren();
    box.hidden = true;
    dialog.querySelectorAll('[aria-invalid]').forEach(function (i) { i.removeAttribute('aria-invalid'); });
  }

  function showErrors(errors) {
    clearErrors();
    var box = $('settingsErrors');
    var first = null;
    Object.keys(errors).forEach(function (key) {
      box.appendChild(el('li', '', friendly(key) + ': ' + errors[key]));
      var input = inputFor(key);
      if (input) { input.setAttribute('aria-invalid', 'true'); first = first || input; }
    });
    box.hidden = false;
    if (first) first.focus();
  }

  function tellPage(message) {
    var box = $('pageStatus');
    box.textContent = message;
    box.hidden = !message;
    if (message) setTimeout(function () { if (box.textContent === message) box.hidden = true; }, 5000);
  }

  /* ---------- the filename checker ---------- */

  // Runs the pattern in the box (saved or not) over the filenames pasted below it.
  function runCheck() {
    var results = $('setResults');
    var summary = $('setCheckSummary');
    results.replaceChildren();
    var pattern = $('setPattern').value;
    var compiled = Settings.compilePattern(pattern);
    if (!compiled.ok) { summary.textContent = 'Pattern problem: ' + compiled.error; return; }

    var names = $('setSamples').value.split('\n').map(function (n) { return n.trim(); }).filter(Boolean).slice(0, 200);
    if (!names.length) { summary.textContent = ''; return; }

    var good = 0;
    names.forEach(function (name) {
      var parsed = Settings.parseFileName(name, pattern);
      if (parsed.recognized) good += 1;
      var li = el('li', parsed.recognized ? 'is-ok' : 'is-bad');
      li.appendChild(el('span', 'check-mark', parsed.recognized ? '✓' : '✗'));
      var body = el('div');
      body.appendChild(el('span', 'mono', name));
      body.appendChild(el('span', 'check-detail', parsed.recognized
        ? 'Vendor: ' + parsed.vendor + ' · Invoice: ' + parsed.invoice + (parsed.bol ? ' · BOL: ' + parsed.bol : '')
        : 'Not recognized with this pattern'));
      li.appendChild(body);
      results.appendChild(li);
    });
    summary.textContent = good + ' of ' + names.length + ' recognized';
  }

  // Fills the box with the distinct PDF names found in this person's own mail.
  function useMailNames() {
    var seen = {};
    var names = [];
    Hub.Mail.messages(user).forEach(function (msg) {
      msg.attachments.forEach(function (a) {
        var key = a.name.toLowerCase();
        if (/\.pdf$/i.test(a.name) && !seen[key]) { seen[key] = true; names.push(a.name); }
      });
    });
    if (!names.length) { $('setCheckSummary').textContent = 'No PDF attachments were found in your mail yet.'; return; }
    $('setSamples').value = names.slice(0, 200).join('\n');
    runCheck();
  }

  /* ---------- opening, saving, closing ---------- */

  function open(person) {
    user = person;
    var folderList = $('setFolderOptions');
    folderList.replaceChildren();
    myFolders().forEach(function (f) { var o = el('option'); o.value = f; folderList.appendChild(o); });

    fill(Settings.get(user));
    $('setSamples').value = '';
    $('setShare').value = '';
    clearErrors();
    showStatus('');
    $('setReset').disabled = !Settings.isCustomized(user);
    runCheck();
    if (!dialog.open) dialog.showModal();
  }

  function close() {
    if (dialog.open) dialog.close();
    user = null;
  }

  // rangeChanged: the person's default date range was changed, so their view should reopen on it.
  function saved(message, rangeChanged) {
    if (Hub.SettingsUI.onSaved) Hub.SettingsUI.onSaved(user, { rangeChanged: !!rangeChanged });
    $('setReset').disabled = !Settings.isCustomized(user);
    tellPage(message);
  }

  $('settingsButton').addEventListener('click', function () {
    var person = Hub.Access.currentUser();
    if (person) open(person);
  });
  $('settingsClose').addEventListener('click', close);
  $('setCancel').addEventListener('click', close);

  $('settingsForm').addEventListener('submit', function (event) {
    event.preventDefault();
    var before = Settings.get(user).defaultRangeDays;
    var result = Settings.save(user, readForm(), { folders: myFolders() });
    if (!result.ok) { showErrors(result.errors); return; }
    saved('Your settings were saved.', before !== result.settings.defaultRangeDays);
    close();
  });

  $('setReset').addEventListener('click', function () {
    if (!window.confirm('Go back to the organization defaults? Your own settings will be removed.')) return;
    var before = Settings.get(user).defaultRangeDays;
    Settings.reset(user);
    fill(Settings.get(user));
    clearErrors();
    runCheck();
    showStatus('Back to the organization defaults.', 'success');
    saved('Your settings were reset to the organization defaults.', before !== Settings.get(user).defaultRangeDays);
  });

  $('settingsForm').addEventListener('input', function (event) {
    if (event.target.hasAttribute('aria-invalid')) event.target.removeAttribute('aria-invalid');
    if (event.target.id === 'setPattern' || event.target.id === 'setSamples') runCheck();
  });

  $('setUseMail').addEventListener('click', useMailNames);

  $('setShareShow').addEventListener('click', function () {
    $('setShare').value = Settings.exportJson(user);
  });

  $('setShareLoad').addEventListener('click', function () {
    clearErrors();
    var before = Settings.get(user).defaultRangeDays;
    var result = Settings.importJson(user, $('setShare').value, { folders: myFolders() });
    if (!result.ok) { showErrors(result.errors); return; }
    fill(Settings.get(user));
    runCheck();
    showStatus('Settings loaded and saved.', 'success');
    saved('Settings were loaded from the text you pasted.', before !== result.settings.defaultRangeDays);
  });

  buildStages();

  Hub.SettingsUI = { open: open, close: close, onSaved: null };
})();
