/*
 * The mail layer. Everything in the app that needs email goes through here, so the source of the
 * mail can be swapped without touching the rules, the bill tracking or the pages.
 *
 * A provider is an object with two functions, both returning a Promise:
 *
 *   provider.fetchMailboxes(user)  -> [{ email, name, folders: ['Inbox', 'Inbox / Invoices', 'Sent Items', ...] }]
 *       the mailboxes this person can read (their own, plus any shared with them) and each one's folder paths.
 *
 *   provider.fetchMessages(user)   -> [{ id, mailbox, folder, from, to, subject, receivedOn: Date,
 *                                        preview, webLink (optional), attachments: [{ name, sizeKb }] }]
 *       messages in those mailboxes. `from` and `to` are display text such as "Name <address>".
 *
 * Only names of attachments are ever needed, never their contents.
 *
 * Providers available:
 *   Hub.SampleMailProvider          the fictitious mail in hub-data.js (what the demo runs on)
 *   Hub.createGraphMailProvider()   Microsoft Graph; see hub-mail-graph.js (not switched on in the demo)
 *
 * Switch with Hub.Mail.setProvider(provider).
 */
(function (global) {
  'use strict';

  var Hub = global.InvoiceHub;

  /* ---------- the sample provider (demo data) ---------- */

  function sampleMailboxes(user) {
    var list = Hub.MAILBOXES.filter(function (m) { return m.access.indexOf(user.email) !== -1; })
      .map(function (m) { return { email: m.email, name: m.name, folders: m.folders.slice() }; });
    if (!list.some(function (m) { return m.email === user.email; })) {
      list.push({ email: user.email, name: 'My mailbox', folders: ['Inbox', 'Sent Items'] });
    }
    return list;
  }

  Hub.SampleMailProvider = {
    name: 'Sample data (demo)',
    isSample: true,
    fetchMailboxes: function (user) {
      return Promise.resolve(sampleMailboxes(user));
    },
    fetchMessages: function (user) {
      var emails = sampleMailboxes(user).map(function (m) { return m.email; });
      return Promise.resolve(Hub.SAMPLE_MESSAGES.filter(function (m) { return emails.indexOf(m.mailbox) !== -1; }));
    }
  };

  /* ---------- checking what a provider returns ---------- */

  // Keeps the rest of the app safe from a provider's quirks: every message has the same fields, and
  // anything that cannot be understood is skipped and counted, never allowed to break the page.
  function normalizeMessage(m) {
    if (!m || typeof m.id !== 'string' || typeof m.mailbox !== 'string') return null;
    var received = m.receivedOn instanceof Date ? m.receivedOn : new Date(m.receivedOn);
    if (isNaN(received)) return null;
    return {
      id: m.id,
      mailbox: m.mailbox.toLowerCase(),
      folder: String(m.folder || ''),
      from: String(m.from || ''),
      to: String(m.to || ''),
      subject: String(m.subject || ''),
      receivedOn: received,
      preview: String(m.preview || ''),
      // A link that opens the original email in Outlook. Only secure web links are accepted.
      webLink: typeof m.webLink === 'string' && /^https:\/\//i.test(m.webLink) ? m.webLink : '',
      attachments: (Array.isArray(m.attachments) ? m.attachments : [])
        .filter(function (a) { return a && typeof a.name === 'string'; })
        .map(function (a) { return { name: a.name, sizeKb: Number(a.sizeKb) || 0, text: typeof a.text === 'string' ? a.text : '' }; })
    };
  }

  /* ---------- the layer ---------- */

  var provider = Hub.SampleMailProvider;
  var state = {}; // per person: { messages, mailboxes, loading, error, loadedAt, skipped, promise }

  function entry(user) {
    return state[user.email] || (state[user.email] = { messages: [], mailboxes: null, loading: false, error: null, loadedAt: null, skipped: 0, promise: null });
  }

  Hub.Mail = {
    /** Switches the mail source. Anything already loaded is discarded. */
    setProvider: function (next) {
      provider = next;
      state = {};
    },

    provider: function () { return provider; },

    /**
     * Loads this person's mail from the provider. Resolves (never rejects) with their entry; check
     * status(user).error afterwards. Loading again while a load is running shares that load.
     * Pass { force: true } to fetch again.
     */
    load: function (user, options) {
      var s = entry(user);
      if (s.promise && !(options && options.force)) return s.promise;
      s.loading = true;
      s.error = null;
      var mine = s.promise = Promise.all([provider.fetchMailboxes(user), provider.fetchMessages(user)])
        .then(function (result) {
          if (s.promise !== mine) return s; // a newer load has replaced this one
          var messages = (result[1] || []).map(normalizeMessage);
          s.mailboxes = result[0] || [];
          s.messages = messages.filter(Boolean);
          s.skipped = messages.length - s.messages.length;
          s.loadedAt = new Date();
          s.loading = false;
          return s;
        })
        .catch(function (err) {
          if (s.promise !== mine) return s;
          s.error = err && err.message ? err.message : String(err);
          s.loading = false;
          return s;
        });
      return s.promise;
    },

    /** The messages loaded so far for this person (empty until load() finishes). */
    messages: function (user) {
      var s = state[user.email];
      return s ? s.messages : [];
    },

    /** The person's mailboxes with their folders; falls back to just their own until loaded. */
    mailboxes: function (user) {
      var s = state[user.email];
      return s && s.mailboxes ? s.mailboxes : [{ email: user.email, name: 'My mailbox', folders: ['Inbox', 'Sent Items'] }];
    },

    status: function (user) {
      var s = state[user.email] || {};
      return {
        loading: !!s.loading, error: s.error || null, loadedAt: s.loadedAt || null, skipped: s.skipped || 0,
        providerName: provider.name || 'Mail', isSample: !!provider.isSample
      };
    },

    /** Forgets everything loaded for one person, or for everyone. */
    clear: function (user) {
      if (user) delete state[user.email]; else state = {};
    },

    normalizeMessage: normalizeMessage
  };
})(window);
