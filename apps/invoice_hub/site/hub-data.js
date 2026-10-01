/*
 * Shared prototype data for the Invoice Tracker: roles, departments, the seed
 * access list, and fictitious sample invoices. Loaded before hub-access.js and
 * dashboard.js (classic scripts, so the site also works when opened from disk).
 *
 * PROTOTYPE ONLY. Everything here ships to the browser, so it is not private.
 * When the real Outlook connection exists, the sample invoices and the access
 * list move to a server that authenticates the user and returns only their data.
 */
(function (global) {
  'use strict';

  var Hub = (global.InvoiceHub = global.InvoiceHub || {});

  // Show the demo sign-in emails on the sign-in page. Set to false once real users exist.
  Hub.SHOW_DEMO_HINTS = true;

  // waitsAt / waitsText: the stage of a bill's journey that is this role's job, used for the
  // "bills waiting on me" filter on the lifecycle view.
  Hub.ROLES = {
    admin:    { label: 'Administrator',       summary: 'Manages who has access and sees organization-wide totals.' },
    gl:       { label: 'GL Accountant',       summary: 'Applies GL codes to approved bills and sends them to Accounts Payable.',
                waitsAt: 'approved', waitsText: 'approved and ready for GL coding' },
    office:   { label: 'Office Admin',        summary: 'Creates a PO for each bill and gets it approved.',
                waitsAt: 'received', waitsText: 'received and waiting for a PO and approval' },
    ap:       { label: 'Accounts Payable',    summary: 'Works the vendor bill queue and pays the bills the GL accountant sends over.',
                waitsAt: 'sent', waitsText: 'GL coded and ready for payment' },
    ar:       { label: 'AR Clerk',            summary: 'Works the customer invoice queue.' },
    approver: { label: 'Department Approver', summary: 'Reviews bills that are waiting on their department.' }
  };

  Hub.DEPARTMENTS = ['Operations', 'Maintenance', 'Logistics', 'Finance'];

  // Fictitious people who mirror the roles in the real process.
  Hub.SEED_USERS = [
    { email: 'admin@example.com',         name: 'Alex Rivera',  role: 'admin' },
    { email: 'gina.lopez@example.com',    name: 'Gina Lopez',   role: 'gl' },
    { email: 'olivia.hart@example.com',   name: 'Olivia Hart',  role: 'office' },
    { email: 'marcus.bell@example.com',   name: 'Marcus Bell',  role: 'ap' },
    { email: 'jane.doe@example.com',      name: 'Jane Doe',     role: 'ap' },
    { email: 'casey.morgan@example.com',  name: 'Casey Morgan', role: 'approver', department: 'Finance' }
  ];

  // Sample records use "days ago" so ages stay realistic whenever the page is opened.
  function ago(days) {
    var d = new Date();
    d.setHours(0, 0, 0, 0);
    d.setDate(d.getDate() - days);
    return d;
  }

  // Sample stage history: "Received" on the received date, the current stage on the
  // stage-since date, and any stages between spread evenly. Real history will come from Outlook and approvals.
  function buildHistory(rec) {
    var steps = Hub.STAGES[rec.type];
    var current = steps.indexOf(rec.stage);
    var start = rec.receivedOn.getTime();
    var end = rec.stageSince.getTime();
    var history = [{ at: rec.receivedOn, action: 'received', to: steps[0], actor: 'Outlook intake', by: '' }];
    for (var i = 1; i <= current; i++) {
      var when = new Date(i === current ? end : start + ((end - start) * i) / current);
      when.setHours(0, 0, 0, 0);
      history.push({ at: when, action: 'moved', from: steps[i - 1], to: steps[i], actor: 'Workflow (sample)', by: '' });
    }
    return history;
  }

  function record(type, id, party, document, stage, department, ageDays, stageDays, amount) {
    var rec = { id: id, type: type, party: party, document: document, stage: stage, department: department,
                receivedOn: ago(ageDays), stageSince: ago(stageDays), amount: amount };
    rec.history = buildHistory(rec);
    return rec;
  }
  function bill(id, party, file, stage, department, ageDays, stageDays, amount) {
    return record('AP', id, party, file, stage, department, ageDays, stageDays, amount);
  }
  function invoice(id, party, bol, stage, ageDays, stageDays, amount) {
    return record('AR', id, party, bol, stage, 'Finance', ageDays, stageDays, amount);
  }

  Hub.APPROVAL_STAGE = 'Department Approval';

  // Workflow order for each queue, used for the progress tracker and the stage filter.
  Hub.STAGES = {
    AP: ['Received', 'Coding', 'Department Approval', 'Payment Scheduled'],
    AR: ['Received', 'BOL Match', 'Billing Review', 'Sent to Customer']
  };

  Hub.TYPE_LABELS = { AP: 'Vendor bill', AR: 'Customer invoice' };

  // Outlook folders each queue is planned to read from (matches the service page).
  Hub.FOLDERS = {
    AP: 'Outlook / Panhandle Pure / Payables / Bills',
    AR: 'Outlook / Panhandle Pure / ACCTINV'
  };

  Hub.SAMPLE_INVOICES = [
    bill('BILL-2041', 'Northwind Supply Co.',   'Northwind Supply NW4471- BOL 88120.pdf', 'Department Approval', 'Operations',  12, 6,  4820.50),
    bill('BILL-2042', 'Lakeside Chemical',      'Lakeside Chemical LC9930- BOL 88134.pdf',    'Department Approval', 'Operations',   5, 2, 12310.00),
    bill('BILL-2043', 'Summit Fleet Services',  'Summit Fleet Services SF0788- BOL 88098.pdf',     'Department Approval', 'Maintenance', 21, 9,  2145.75),
    bill('BILL-2044', 'Prairie Electric',       'Prairie Electric PE2210- BOL 88141.pdf',    'Coding',              'Maintenance',  3, 3,   980.00),
    bill('BILL-2045', 'Canyon Freight Lines',   'Canyon Freight Lines CF5512- BOL 88055.pdf',   'Department Approval', 'Logistics',   34, 14, 7650.25),
    bill('BILL-2046', 'Red River Packaging',    'Red River Packaging RR0311- BOL 88150.pdf',         'Received',            'Logistics',    1, 1,  3320.00),
    bill('BILL-2047', 'Blue Mesa Analytics',    'Blue Mesa Analytics BM0042- BOL 88012.pdf',        'Payment Scheduled',   'Finance',     41, 4,  1500.00),
    bill('BILL-2048', 'Cactus Office Supply',   'Cactus Office Supply CO88231- BOL 88119.pdf',         'Department Approval', 'Finance',      9, 4,   412.18),
    invoice('INV-3101', 'Harvest Beverage Co.',   'BOL-771204', 'Billing Review',    8,  2, 18450.00),
    invoice('INV-3102', 'Desert Springs Market',  'BOL-771318', 'BOL Match',         4,  4,  6210.40),
    invoice('INV-3103', 'Ridgeline Grocers',      'BOL-770982', 'Sent to Customer', 36, 31, 22980.00),
    invoice('INV-3104', 'Copper Canyon Hotels',   'BOL-771455', 'Received',          2,  2,  3875.90),
    invoice('INV-3105', 'Blue Sky Distributors',  'BOL-770655', 'Sent to Customer', 52, 45,  9120.00),
    invoice('INV-3106', 'Mesa Verde Foods',       'BOL-771401', 'BOL Match',        11,  8,  5340.25)
  ];

  /* ---------- Sample Outlook mail (fictitious) ---------- */

  // The organization's defaults: how a bill's stage is recognized in anyone's mailbox, whatever folders
  // they use, and how PDF filenames are written. Each person can change their own in Settings.
  // In the real service these hold your organization's actual senders and subject words.
  // Several options can be listed, separated by commas.
  Hub.DEFAULT_SETTINGS = {
    received: { sender: 'ACCTINV',     recipient: '', subject: '',         folder: '' },
    approved: { sender: 'Olivia Hart', recipient: '', subject: 'Vendors',  folder: '' },
    sent:     { sender: '',            recipient: '', subject: 'GL Coded', folder: '' },
    filenamePattern: '{vendor} {invoice} - BOL {bol}',
    defaultRangeDays: 0 // 0 = no default date range; otherwise the view opens on the last N days
  };

  // Everyone has their own mailbox, and people file mail differently, so folders differ.
  var FILING = {
    'gina.lopez@example.com':   { received: 'Inbox / ACCTINV',  approved: 'Inbox / Approved Vendors', sent: 'Inbox' },
    'olivia.hart@example.com':  { received: 'Inbox / Invoices', approved: 'Inbox',                    sent: 'Inbox' },
    'marcus.bell@example.com':  { received: 'Inbox',            approved: 'Inbox',                    sent: 'Inbox / Coded Bills' },
    'jane.doe@example.com':     { received: 'Inbox / AP',       approved: 'Inbox / AP',               sent: 'Inbox / AP' },
    'casey.morgan@example.com': { received: 'Inbox',            approved: 'Inbox',                    sent: 'Inbox' }
  };

  function personFor(email) {
    return { email: email, name: Hub.SEED_USERS.filter(function (u) { return u.email === email; })[0].name };
  }

  Hub.MAILBOXES = Object.keys(FILING).map(function (email) {
    var filing = FILING[email];
    var folders = ['Inbox', 'Sent Items'];
    [filing.received, filing.approved, filing.sent].forEach(function (f) { if (folders.indexOf(f) === -1) folders.push(f); });
    return { email: email, name: personFor(email).name, access: [email], folders: folders };
  });

  var ACCTINV = 'ACCTINV <acctinv@example.com>';
  var GL = personFor('gina.lopez@example.com');
  var OFFICE = personFor('olivia.hart@example.com');
  var APM = personFor('marcus.bell@example.com');
  var APC = personFor('jane.doe@example.com');
  var CTL = personFor('casey.morgan@example.com');

  function address(p) { return p.name + ' <' + p.email + '>'; }
  function pad(n) { return (n < 10 ? '00' : n < 100 ? '0' : '') + n; }

  // Each bill is one PDF named "Vendor INVOICE- BOL NUMBER.pdf". received/approved/coded are days ago.
  var BILLS = [
    { vendor: 'Northwind Supply',        inv: 'NW4471',  bol: '88120', received: 12 },
    { vendor: 'Lakeside Chemical',       inv: 'LC9930',  bol: '88134', received: 5 },
    { vendor: 'Summit Fleet Services',   inv: 'SF0788',  bol: '88098', received: 21 },
    { vendor: 'Prairie Electric',        inv: 'PE2210',  bol: '88141', received: 3 },
    { vendor: 'Canyon Freight Lines',    inv: 'CF5512',  bol: '88055', received: 34 },
    { vendor: 'Red River Packaging',     inv: 'RR0311',  bol: '88150', received: 1 },
    { vendor: 'Cactus Office Supply',    inv: 'CO88231', bol: '88119', received: 9 },
    { vendor: 'Blue Mesa Analytics',     inv: 'BM0042',  bol: '88012', received: 41, approved: 36, coded: 34 },
    { vendor: 'Plains Welding Supply',   inv: 'PW7731',  bol: '88070', received: 30, approved: 24, coded: 22 },
    { vendor: 'Quail Creek Landscaping', inv: 'QC2208',  bol: '88081', received: 27, approved: 24, coded: 22 },
    { vendor: 'Sandhill Tire & Auto',    inv: 'ST5590',  bol: '88109', received: 18, approved: 12 },
    { vendor: 'Ochoa Concrete',          inv: 'OC0118',  bol: '88117', received: 15, approved: 9 },
    { file: 'scan0042.pdf', label: 'scanned invoice', received: 6 } // a filename the parser will not recognize
  ];

  function fileName(b) { return b.file || (b.vendor + ' ' + b.inv + '- BOL ' + b.bol + '.pdf'); }
  function attach(b, i) { return { name: fileName(b), sizeKb: 70 + ((i * 37) % 60), text: '' }; }

  var messages = [];

  // One email, delivered the way Outlook would: a copy in every recipient's mailbox (filed where
  // that person files this kind of mail) and, if a person sent it, a copy in their Sent Items.
  function deliver(kind, from, sender, recipients, subject, daysAgo, preview, files) {
    var to = recipients.map(address).join('; ');
    function put(mailbox, folder) {
      messages.push({ id: 'MSG-' + pad(messages.length + 1), mailbox: mailbox, folder: folder, from: from, to: to,
                      subject: subject, receivedOn: ago(daysAgo), preview: preview, attachments: files });
    }
    recipients.forEach(function (p) { put(p.email, FILING[p.email][kind]); });
    if (sender) put(sender.email, 'Sent Items');
  }

  // 1. Received: ACCTINV sends each vendor bill to everyone involved.
  BILLS.forEach(function (b, i) {
    deliver('received', ACCTINV, null, [GL, OFFICE, APM, CTL, APC], 'New invoice: ' + (b.label || b.vendor + ' ' + b.inv),
      b.received, 'Vendor invoice received. All related parties are copied.', [attach(b, i)]);
  });

  // Bills approved (or coded) on the same day travel in one email with several PDFs.
  function groupByDay(key) {
    var groups = {};
    BILLS.forEach(function (b, i) { if (b[key] !== undefined) (groups[b[key]] = groups[b[key]] || []).push(attach(b, i)); });
    return groups;
  }
  function countOf(files, one, many) { return files.length + (files.length === 1 ? one : many); }

  // 2. Approved: the office admin creates the POs, gets approval, and sends the bills to the GL accountant.
  var approvedByDay = groupByDay('approved');
  Object.keys(approvedByDay).forEach(function (day) {
    var files = approvedByDay[day];
    deliver('approved', address(OFFICE), OFFICE, [GL, APM, CTL, APC], 'Vendors - PO and approval complete (' + countOf(files, ' invoice)', ' invoices)'),
      Number(day), 'POs created and approved. Ready for GL coding.', files);
  });

  // 3. Coded and sent to AP: the GL accountant emails the coded bills to everyone, including Accounts Payable.
  var codedByDay = groupByDay('coded');
  Object.keys(codedByDay).forEach(function (day) {
    var files = codedByDay[day];
    deliver('sent', address(GL), GL, [APM, APC, OFFICE, CTL], 'GL Coded - sent to AP (' + countOf(files, ' invoice)', ' invoices)'),
      Number(day), 'GL codes applied. Sent to Accounts Payable for payment.', files);
  });

  Hub.SAMPLE_MESSAGES = messages;
})(window);
