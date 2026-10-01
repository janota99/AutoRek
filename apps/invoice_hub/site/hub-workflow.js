/*
 * Invoice workflow: stage changes (approve, reject, send for approval) and the
 * stage history they produce.
 *
 * The sample invoices in hub-data.js never change. Each action is saved as an
 * event, and Workflow.invoices() replays the events on top of the samples to
 * give the current stage and history. Events live in localStorage, so in this
 * prototype they are shared by everyone using the same browser; in the real
 * service they would be stored on the server.
 *
 * Every action is re-checked here (who, which invoice, which stage) instead of
 * trusting the buttons on the page. That check must also run on the server later.
 */
(function (global) {
  'use strict';

  var Hub = global.InvoiceHub;
  var Store = Hub.Store;
  var EVENTS_KEY = 'invoiceHub.events.v1';
  var NOTE_MAX = 300;
  var REJECT_NOTE_MIN = 5;
  var BULK_MAX = 200;   // most bills one bulk action may touch

  // What each action needs and where it sends the invoice.
  var ACTIONS = {
    approve: { label: 'Approve',           done: 'Approved',          role: 'approver', type: 'AP', from: Hub.APPROVAL_STAGE, to: 'Payment Scheduled', past: 'approved',  sameDepartment: true },
    reject:  { label: 'Reject',            done: 'Rejected',          role: 'approver', type: 'AP', from: Hub.APPROVAL_STAGE, to: 'Coding',            past: 'rejected',  sameDepartment: true, noteRequired: true },
    submit:  { label: 'Send for approval', done: 'Sent for approval', role: 'ap',       type: 'AP', from: 'Coding',           to: Hub.APPROVAL_STAGE,  past: 'submitted' }
  };

  function isValidEvent(e) {
    return e && typeof e.invoiceId === 'string' && ACTIONS[e.key] && typeof e.at === 'string' &&
      typeof e.actorEmail === 'string' && typeof e.actorName === 'string' &&
      typeof e.from === 'string' && typeof e.to === 'string';
  }

  function loadEvents() {
    try {
      var saved = JSON.parse(Store.read('localStorage', EVENTS_KEY));
      return Array.isArray(saved) ? saved.filter(isValidEvent) : [];
    } catch (err) {
      return [];
    }
  }

  function saveEvents(events) {
    Store.write('localStorage', EVENTS_KEY, JSON.stringify(events));
  }

  /** Sample invoices with recorded actions applied: current stage, stage-since date and history. */
  function invoices() {
    var events = loadEvents().sort(function (a, b) { return a.at < b.at ? -1 : a.at > b.at ? 1 : 0; });
    return Hub.SAMPLE_INVOICES.map(function (sample) {
      var rec = Object.assign({}, sample, { history: sample.history.slice() });
      events.forEach(function (e) {
        if (e.invoiceId !== sample.id) return;
        var when = new Date(e.at);
        rec.stage = e.to;
        rec.stageSince = when;
        rec.history.push({ at: when, action: ACTIONS[e.key].past, from: e.from, to: e.to, actor: e.actorName, by: e.actorEmail, note: e.note || '' });
      });
      return rec;
    });
  }

  function isAllowed(user, rule, invoice) {
    return !!user && user.role === rule.role &&
      invoice.type === rule.type &&
      invoice.stage === rule.from &&
      (!rule.sameDepartment || invoice.department === user.department);
  }

  /** Which actions this person may take on this invoice right now. */
  function availableActions(user, invoice) {
    return Object.keys(ACTIONS).filter(function (key) { return isAllowed(user, ACTIONS[key], invoice); });
  }

  function findUser(email) {
    var wanted = String(email || '').trim().toLowerCase();
    return Hub.Access.list().filter(function (u) { return u.email === wanted; })[0] || null;
  }

  /**
   * Records an action. `expectedStage` is the stage the person was looking at, so a
   * stale page (someone else already acted) is refused instead of applied twice.
   */
  function act(actorEmail, invoiceId, key, note, expectedStage) {
    var actor = findUser(actorEmail);
    if (!actor) return { ok: false, error: 'You no longer have access.' };
    var rule = ACTIONS[key];
    if (!rule) return { ok: false, error: 'Unknown action.' };

    var invoice = invoices().filter(function (r) { return r.id === invoiceId; })[0];
    if (!invoice) return { ok: false, error: 'That invoice was not found.' };
    if (expectedStage && invoice.stage !== expectedStage) {
      return { ok: false, stale: true, error: 'This invoice was updated by someone else and is now in ' + invoice.stage + '. Review it again before acting.' };
    }
    if (!isAllowed(actor, rule, invoice)) return { ok: false, error: 'You cannot ' + rule.label.toLowerCase() + ' this invoice.' };

    var text = String(note || '').trim();
    if (rule.noteRequired && text.length < REJECT_NOTE_MIN) return { ok: false, error: 'Add a reason of at least ' + REJECT_NOTE_MIN + ' characters.', field: 'note' };
    if (text.length > NOTE_MAX) return { ok: false, error: 'Keep the comment to ' + NOTE_MAX + ' characters or fewer.', field: 'note' };

    var events = loadEvents();
    events.push({ invoiceId: invoiceId, key: key, at: new Date().toISOString(), actorEmail: actor.email, actorName: actor.name,
                  from: invoice.stage, to: rule.to, note: text });
    saveEvents(events);
    return { ok: true, to: rule.to };
  }

  /**
   * Applies one action to several invoices at once (for example approving ten bills). Every invoice
   * goes through act() on its own, so each one is checked separately (who, which invoice, which stage)
   * and one refusal never blocks the others. items: [{ id, stage }], the stage the person saw for each.
   * Returns { ok, done: [{ id, to }], failed: [{ id, error }] }.
   */
  function actMany(actorEmail, items, key, note) {
    var rule = ACTIONS[key];
    if (!rule) return { ok: false, error: 'Unknown action.' };
    if (!items || !items.length) return { ok: false, error: 'Nothing is selected.' };
    if (items.length > BULK_MAX) return { ok: false, error: 'Select up to ' + BULK_MAX + ' bills at a time.' };
    var text = String(note || '').trim();
    if (rule.noteRequired && text.length < REJECT_NOTE_MIN) return { ok: false, error: 'Add a reason of at least ' + REJECT_NOTE_MIN + ' characters.', field: 'note' };
    if (text.length > NOTE_MAX) return { ok: false, error: 'Keep the comment to ' + NOTE_MAX + ' characters or fewer.', field: 'note' };

    var done = [];
    var failed = [];
    items.forEach(function (item) {
      var result = act(actorEmail, item.id, key, text, item.stage);
      if (result.ok) done.push({ id: item.id, to: result.to }); else failed.push({ id: item.id, error: result.error });
    });
    return { ok: true, done: done, failed: failed };
  }

  /** Does this role have any action at all? (Roles without one do not need selection boxes.) */
  function canEverAct(role) {
    return Object.keys(ACTIONS).some(function (key) { return ACTIONS[key].role === role; });
  }

  /** Administrator only: return every sample invoice to its starting stage. */
  function reset(actorEmail) {
    var actor = findUser(actorEmail);
    if (!actor || actor.role !== 'admin') return { ok: false, error: 'Only an administrator can reset the sample workflow.' };
    Store.remove('localStorage', EVENTS_KEY);
    return { ok: true };
  }

  /** Text for one history entry. */
  function describe(entry) {
    switch (entry.action) {
      case 'received':  return 'Received through Outlook intake';
      case 'approved':  return 'Approved and moved to ' + entry.to;
      case 'rejected':  return 'Rejected and returned to ' + entry.to;
      case 'submitted': return 'Sent for approval';
      default:          return 'Moved to ' + entry.to;
    }
  }

  Hub.Workflow = {
    ACTIONS: ACTIONS,
    NOTE_MAX: NOTE_MAX,
    REJECT_NOTE_MIN: REJECT_NOTE_MIN,
    invoices: invoices,
    availableActions: availableActions,
    act: act,
    actMany: actMany,
    canEverAct: canEverAct,
    BULK_MAX: BULK_MAX,
    reset: reset,
    describe: describe
  };
})(window);
