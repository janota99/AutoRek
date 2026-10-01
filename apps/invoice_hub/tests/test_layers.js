// Offline tests for settings, the mail layer, the Graph provider (mocked) and the whole chain.
const root = __dirname + '/../site/'; // the site folder
// Run with:  node tests/test_layers.js
global.window = global;
['hub-data.js', 'hub-access.js', 'hub-workflow.js', 'hub-settings.js', 'hub-mail.js', 'hub-mail-graph.js', 'hub-inbox.js'].forEach(f => require(root + f));
const H = InvoiceHub, S = H.Settings, I = H.Inbox, M = H.Mail;

let failures = 0;
const ok = (cond, label, extra) => { if (!cond) failures++; console.log((cond ? 'PASS ' : 'FAIL ') + label + (extra !== undefined ? '  -> ' + JSON.stringify(extra) : '')); };
const gina = H.SEED_USERS.find(u => u.role === 'gl');
const olivia = H.SEED_USERS.find(u => u.role === 'office');

(async () => {
  console.log('\n=== filename patterns ===');
  const p = (name, pattern) => S.parseFileName(name, pattern);
  ok(p("Shoppa's WW12345- BOL 12346.pdf").recognized && p("Shoppa's WW12345- BOL 12346.pdf").invoice === 'WW12345' && p("Shoppa's WW12345- BOL 12346.pdf").bol === '12346' && p("Shoppa's WW12345- BOL 12346.pdf").vendor === "Shoppa's", 'default pattern reads your real example');
  ok(p("Shoppa's WW12345 - BOL 12346.pdf").recognized, 'a space before the hyphen is tolerated');
  ok(!p('scan0042.pdf').recognized, 'unrelated filename is not recognized');
  ok(p('WW12345_Shoppas_12346.pdf', '{invoice}_{vendor}_{bol}').invoice === 'WW12345' && p('WW12345_Shoppas_12346.pdf', '{invoice}_{vendor}_{bol}').vendor === 'Shoppas', 'custom pattern with underscores');
  ok(p('Acme 4471.pdf', '{vendor} {invoice}').recognized && p('Acme 4471.pdf', '{vendor} {invoice}').bol === '', 'pattern without {bol}');
  ok(p('Acme 4471.pdf', '{vendor} {invoice} - BOL {bol}').recognized === false, 'name without a BOL fails a pattern that needs one');
  ['{vendor}', '{invoice} {bol}', '{vendor} {vendor} {invoice}', '{vendor} {price} {invoice}', '', '{vendor {invoice}', 'x'.repeat(81)].forEach(bad =>
    ok(!S.compilePattern(bad).ok, 'rejects pattern ' + JSON.stringify(bad.length > 20 ? bad.slice(0, 12) + '...' : bad), S.compilePattern(bad).error));
  ok(S.compilePattern('{vendor} ({invoice}) [BOL {bol}]').ok && p('Acme (4471) [BOL 9].pdf', '{vendor} ({invoice}) [BOL {bol}]').invoice === '4471', 'brackets and parentheses in a pattern are literal text');

  console.log('\n=== settings ===');
  ok(JSON.stringify(S.get(gina)) === JSON.stringify(S.defaults()), 'a new person gets the organization defaults');
  ok(!S.isCustomized(gina), 'defaults are not counted as customized');
  let r = S.save(gina, { approved: { sender: 'Teresia Weaver, tweaver@example.com', subject: 'Vendors, Vendor' } });
  ok(r.ok, 'saves several senders and subject words');
  ok(S.get(gina).approved.sender === 'Teresia Weaver, tweaver@example.com' && S.get(gina).received.sender === 'ACCTINV', 'saved values kept, other stages still default');
  const rules = S.derivedRules(gina).filter(x => x.stage === 'approved');
  ok(rules.length === 4, '2 senders x 2 subject words = 4 rules', rules.map(x => x.sender + '|' + x.subject));
  ok(S.derivedRules(olivia).filter(x => x.stage === 'approved').length === 1, 'another person still has the default single rule');
  r = S.save(gina, { sent: { sender: '', recipient: '', subject: '', folder: '' } });
  ok(r.ok && S.derivedRules(gina).every(x => x.stage !== 'sent'), 'a stage with every field blank is switched off');
  ok(!S.save(gina, { filenamePattern: '{vendor}' }).ok && S.get(gina).filenamePattern === S.defaults().filenamePattern, 'an invalid pattern is refused and nothing is saved');
  r = S.save(gina, { received: { sender: 'a,b,c,d,e,f' } });
  ok(!r.ok && r.errors['received.sender'], 'more than 5 options refused', r.errors);
  r = S.save(gina, { approved: { sender: 'a,b,c', recipient: 'x,y,z', subject: '1,2' } });
  ok(!r.ok, '3 x 3 x 2 = 18 combinations refused', r.errors);
  r = S.save(gina, { approved: { folder: 'Inbox / Nope' } }, { folders: ['Inbox', 'Sent Items'] });
  ok(!r.ok && r.errors['approved.folder'], 'unknown folder refused when the folder list is known');
  r = S.save(gina, { approved: { folder: 'inbox/approved vendors' } }, { folders: ['Inbox', 'Inbox / Approved Vendors'] });
  ok(r.ok, 'folder typed loosely is accepted');
  ok(S.save(gina, { defaultRangeDays: 45 }).ok && S.get(gina).defaultRangeDays === 0, 'an unsupported default range is ignored, not saved');
  S.save(gina, { defaultRangeDays: 30 });
  ok(S.get(gina).defaultRangeDays === 30, 'default date range saved');
  S.save(gina, { approved: { sender: 'Zed Approver', subject: 'Vendors' }, defaultRangeDays: 30, filenamePattern: '{invoice} {vendor}' });
  const text = S.exportJson(gina);
  ok(S.importJson(olivia, text).ok && S.get(olivia).approved.sender === 'Zed Approver' && S.get(olivia).defaultRangeDays === 30 && S.get(olivia).filenamePattern === '{invoice} {vendor}', 'settings can be exported by one person and imported by another', S.get(olivia).approved);
  ok(!S.importJson(olivia, 'not json').ok && !S.importJson(olivia, '[1,2]').ok, 'bad import text refused');
  S.reset(gina); S.reset(olivia);
  ok(JSON.stringify(S.get(gina)) === JSON.stringify(S.defaults()) && !S.isCustomized(gina), 'reset returns to the organization defaults');

  console.log('\n=== mail layer ===');
  ok(M.provider().isSample && M.messages(gina).length === 0, 'starts with the sample provider and nothing loaded');
  const first = await M.load(gina);
  ok(M.messages(gina).length === 19 && M.status(gina).providerName.startsWith('Sample') && M.status(gina).loadedAt instanceof Date, 'sample provider loads 19 messages for one person', M.messages(gina).length);
  ok(M.messages(olivia).length === 0, "another person's mail is not loaded until they load it");
  ok(M.mailboxes(gina).length === 1 && M.mailboxes(gina)[0].folders.indexOf('Sent Items') !== -1, 'mailbox and folders come from the provider');

  let calls = 0;
  const slow = { name: 'Slow test', fetchMailboxes: () => new Promise(res => setTimeout(() => res([{ email: gina.email, name: 'G', folders: ['Inbox'] }]), 40)),
    fetchMessages: () => { calls++; return new Promise(res => setTimeout(() => res([{ id: 'x1', mailbox: gina.email, folder: 'Inbox', from: 'A', to: 'B', subject: 'Hi', receivedOn: new Date(), attachments: [{ name: 'a.pdf' }] }]), 40)); } };
  M.setProvider(slow);
  ok(M.messages(gina).length === 0, 'switching providers discards what was loaded');
  const pa = M.load(gina), pb = M.load(gina);
  ok(M.status(gina).loading === true && pa === pb, 'while loading, status says so and a second load shares the first');
  await pa;
  ok(calls === 1 && !M.status(gina).loading && M.messages(gina).length === 1 && M.status(gina).providerName === 'Slow test', 'slow provider finishes once');
  await M.load(gina, { force: true });
  ok(calls === 2, 'force reloads');

  const broken = { name: 'Broken test', fetchMailboxes: () => Promise.resolve([]), fetchMessages: () => Promise.reject(new Error('Microsoft Graph answered 403.')) };
  M.setProvider(broken);
  const failed = await M.load(gina);
  ok(M.status(gina).error === 'Microsoft Graph answered 403.' && !M.status(gina).loading && failed && failed.error === 'Microsoft Graph answered 403.', 'a failing provider becomes an error status, not a crash', M.status(gina).error);

  const messy = { name: 'Messy test', fetchMailboxes: () => Promise.resolve([{ email: gina.email, name: 'G', folders: ['Inbox'] }]),
    fetchMessages: () => Promise.resolve([
      { id: 'ok1', mailbox: gina.email, folder: 'Inbox', from: 'A', to: 'B', subject: 'fine', receivedOn: '2026-09-01T10:00:00Z', attachments: [{ name: 'x.pdf', sizeKb: 5 }, { nope: 1 }] },
      { id: 'bad-date', mailbox: gina.email, receivedOn: 'not a date' }, null, { subject: 'no id' }, { id: 7, mailbox: 'x' }]) };
  M.setProvider(messy);
  await M.load(gina);
  ok(M.messages(gina).length === 1 && M.status(gina).skipped === 4 && M.messages(gina)[0].attachments.length === 1 && M.messages(gina)[0].receivedOn instanceof Date, 'malformed messages are skipped and counted, good ones tidied', { kept: M.messages(gina).length, skipped: M.status(gina).skipped });
  M.clear(gina);
  ok(M.messages(gina).length === 0, 'clear forgets a person\'s mail');
  M.setProvider(H.SampleMailProvider);

  console.log('\n=== Microsoft Graph provider, mocked responses ===');
  const folderPages = {
    '/me/mailFolders?$top=100': { value: [{ id: 'F-inbox', displayName: 'Inbox', childFolderCount: 2 }, { id: 'F-sent', displayName: 'Sent Items', childFolderCount: 0 }] },
    '/me/mailFolders/F-inbox/childFolders?$top=100': { value: [{ id: 'F-acct', displayName: 'ACCTINV', childFolderCount: 1 }, { id: 'F-appr', displayName: 'Approved Vendors', childFolderCount: 0 }] },
    '/me/mailFolders/F-acct/childFolders?$top=100': { value: [{ id: 'F-old', displayName: 'Old', childFolderCount: 0 }] }
  };
  const msgPage1 = { value: [
      { id: 'M1', subject: 'New invoice: Shoppas', from: { emailAddress: { name: 'ACCTINV', address: 'acctinv@x.com' } }, toRecipients: [{ emailAddress: { name: 'Gina', address: 'g@x.com' } }], ccRecipients: [{ emailAddress: { address: 'o@x.com' } }],
        receivedDateTime: '2026-09-01T15:00:00Z', bodyPreview: 'Please review', hasAttachments: true, parentFolderId: 'F-acct' } ],
    '@odata.nextLink': 'https://graph.test/v1.0/me/messages?page=2' };
  const msgPage2 = { value: [
      { id: 'M2', subject: 'GL Coded - sent to AP', from: { emailAddress: { name: 'Gina', address: 'g@x.com' } }, toRecipients: [], receivedDateTime: '2026-09-05T15:00:00Z', hasAttachments: true, parentFolderId: 'F-sent' } ] };
  const attachmentsByMessage = {
    M1: { value: [{ name: "Shoppa's WW12345- BOL 12346.pdf", size: 204800, contentType: 'application/pdf', isInline: false }, { name: 'logo.png', size: 2048, isInline: true }] },
    M2: { value: [{ name: 'Acme AC77- BOL 5.pdf', size: 1000, isInline: false }] }
  };
  const seen = [];
  const mockFetch = (url, opts) => {
    seen.push({ url, auth: opts.headers.Authorization });
    const path = url.replace('https://graph.test/v1.0', '');
    let body = folderPages[path];
    if (!body && path.startsWith('/me/messages?$select')) body = msgPage1;
    if (!body && path === '/me/messages?page=2') body = msgPage2;
    const m = /^\/me\/messages\/(M\d)\/attachments/.exec(path);
    if (!body && m) body = attachmentsByMessage[m[1]];
    return Promise.resolve(body ? { ok: true, status: 200, json: () => Promise.resolve(body) } : { ok: false, status: 404, json: () => Promise.resolve({}) });
  };
  const graph = H.createGraphMailProvider(() => Promise.resolve('TOKEN123'), { fetch: mockFetch, baseUrl: 'https://graph.test/v1.0' });
  const boxes = await graph.fetchMailboxes(gina);
  ok(boxes.length === 1 && boxes[0].email === gina.email, 'one mailbox, the signed-in person\'s own');
  ok(['Inbox', 'Inbox / ACCTINV', 'Inbox / ACCTINV / Old', 'Inbox / Approved Vendors', 'Sent Items'].every(f => boxes[0].folders.indexOf(f) !== -1), 'nested folders become "Inbox / ACCTINV / Old" paths', boxes[0].folders);
  const gmsgs = await graph.fetchMessages(gina);
  ok(gmsgs.length === 2, 'follows the next-page link', gmsgs.map(m => m.id));
  const g1 = gmsgs.find(m => m.id === 'M1');
  ok(g1.folder === 'Inbox / ACCTINV' && g1.from === 'ACCTINV <acctinv@x.com>' && g1.to.indexOf('Gina <g@x.com>') !== -1 && g1.to.indexOf('o@x.com') !== -1, 'folder, sender and recipients (to + cc) translated', { folder: g1.folder, from: g1.from, to: g1.to });
  ok(g1.attachments.length === 1 && g1.attachments[0].name === "Shoppa's WW12345- BOL 12346.pdf" && g1.attachments[0].sizeKb === 200, 'attachment names kept, inline images left out, size converted');
  ok(g1.receivedOn instanceof Date && g1.receivedOn.toISOString().startsWith('2026-09-01'), 'received date becomes a Date');
  ok(seen.every(s => s.auth === 'Bearer TOKEN123'), 'every request carries the access token');
  ok(seen.every(s => !/contentBytes|\$value/i.test(s.url)), 'no request asks for file contents');
  ok(seen.some(s => /\/attachments\?\$select=name,size,contentType,isInline$/.test(s.url)), 'attachments are requested by name and size only');
  const denied = H.createGraphMailProvider(() => Promise.resolve('T'), { fetch: () => Promise.resolve({ ok: false, status: 403, json: () => Promise.resolve({}) }), baseUrl: 'https://graph.test/v1.0' });
  let denial = null; try { await denied.fetchMessages(gina); } catch (e) { denial = e.message; }
  ok(denial && denial.indexOf('403') !== -1, 'a refused request gives a readable error', denial);
  const noToken = H.createGraphMailProvider(() => Promise.reject(new Error('Sign in needed')), { fetch: mockFetch, baseUrl: 'https://graph.test/v1.0' });
  let tokenErr = null; try { await noToken.fetchMailboxes(gina); } catch (e) { tokenErr = e.message; }
  ok(tokenErr === 'Sign in needed', 'a token problem reaches the caller', tokenErr);

  console.log('\n=== the whole chain: provider -> layer -> settings rules -> bills ===');
  M.setProvider(graph);
  await M.load(gina);
  ok(M.status(gina).providerName === 'Microsoft Outlook (Graph)' && !M.status(gina).isSample, 'the layer runs on the Graph provider');
  S.save(gina, { received: { sender: 'ACCTINV' }, approved: { sender: 'x' }, sent: { subject: 'GL Coded' } });
  const gb = I.lifecycle(gina);
  ok(gb.length === 2 && gb.find(b => b.vendor === "Shoppa's").invoice === 'WW12345' && gb.find(b => b.vendor === "Shoppa's").status === 'received', 'bills built from "Graph" messages with the default rules', gb.map(b => b.vendor + ' ' + b.invoice + ' ' + b.status));
  ok(gb.find(b => b.vendor === 'Acme').status === 'sent', 'the GL Coded email in Sent Items is stage 3');
  S.save(gina, { filenamePattern: '{vendor} {invoice}' });
  ok(I.lifecycle(gina).every(b => !b.recognized || b.bol === ''), 'changing the filename pattern changes how the same mail is read');
  S.reset(gina);

  M.setProvider(H.SampleMailProvider);
  await M.load(gina); await M.load(olivia);
  const funnel = u => { const bills = I.lifecycle(u); const c = { received: 0, approved: 0, sent: 0 }; bills.forEach(b => Object.keys(b.when).forEach(s => c[s]++)); return JSON.stringify(c) + ' unrecognized ' + bills.filter(b => !b.recognized).length; };
  ok(funnel(gina) === '{"received":13,"approved":5,"sent":3} unrecognized 1', 'sample provider + default settings: 13 -> 5 -> 3 with no rules added by hand', funnel(gina));
  ok(funnel(olivia) === funnel(gina), 'a different person, different folders, the same bills');
  S.save(gina, { approved: { sender: 'Nobody Here' } });
  ok(I.lifecycle(gina).filter(b => b.when.approved).length === 0 && I.lifecycle(olivia).filter(b => b.when.approved).length === 5, "one person's settings do not affect another's dashboard");
  S.save(gina, { approved: { sender: 'Nobody Here, Olivia Hart' } });
  ok(I.lifecycle(gina).filter(b => b.when.approved).length === 5, 'listing two senders finds the approvals from either');
  ok(!I.remove(gina, 'setting-approved-0').ok, 'a rule that comes from settings cannot be removed one by one');
  S.reset(gina);

  console.log('\n=== email link-back (webLink) ===');
  const norm = w => M.normalizeMessage({ id: 'a', mailbox: 'x@y.com', receivedOn: new Date(), webLink: w, attachments: [] }).webLink;
  ok(norm('https://outlook.office365.com/owa/?ItemID=abc') === 'https://outlook.office365.com/owa/?ItemID=abc', 'a secure web link is kept');
  ok(norm('http://example.com/x') === '' && norm('javascript:alert(1)') === '' && norm('data:text/html,hi') === '' && norm(undefined) === '' && norm(42) === '', 'insecure, script and non-text links are dropped');
  const g = H.GraphMail.fromGraphMessage({ id: 'M9', subject: 's', from: { emailAddress: { name: 'A', address: 'a@x.com' } }, toRecipients: [], receivedDateTime: '2026-09-01T00:00:00Z', parentFolderId: 'F', webLink: 'https://outlook.office365.com/owa/?ItemID=M9' }, [], { F: 'Inbox' }, 'me@x.com');
  ok(g.webLink === 'https://outlook.office365.com/owa/?ItemID=M9', 'Graph messages carry their Outlook link');

  console.log('\n=== bulk actions ===');
  const W = H.Workflow;
  const casey = H.SEED_USERS.find(u => u.email === 'casey.morgan@example.com');   // Finance approver
  const jane = H.SEED_USERS.find(u => u.email === 'jane.doe@example.com');       // AP
  const admin = H.SEED_USERS.find(u => u.role === 'admin');
  W.reset(admin.email);
  const seenAt = id => W.invoices().find(r => r.id === id).stage;
  ok(W.canEverAct('approver') && W.canEverAct('ap') && !W.canEverAct('ar') && !W.canEverAct('gl') && !W.canEverAct('admin'), 'only roles that can act get selection boxes');

  let bulkResult = W.actMany(casey.email, [{ id: 'BILL-2048', stage: 'Department Approval' }, { id: 'BILL-2041', stage: 'Department Approval' }, { id: 'BILL-9999', stage: 'x' }, { id: 'BILL-2047', stage: 'Department Approval' }], 'approve', 'Looks fine');
  ok(bulkResult.ok && bulkResult.done.length === 1 && bulkResult.done[0].id === 'BILL-2048' && bulkResult.failed.length === 3, 'each bill is checked on its own: 1 done, 3 refused', bulkResult.failed.map(f => f.id + ': ' + f.error));
  ok(seenAt('BILL-2048') === 'Payment Scheduled' && seenAt('BILL-2041') === 'Department Approval', 'only the allowed bill changed');
  ok(bulkResult.failed.find(f => f.id === 'BILL-2041').error.includes('cannot'), "another department's bill is refused");
  ok(bulkResult.failed.find(f => f.id === 'BILL-2047').error.includes('updated by someone else') || bulkResult.failed.find(f => f.id === 'BILL-2047').error.includes('cannot'), 'a bill that already moved on is refused');

  bulkResult = W.actMany(casey.email, [{ id: 'BILL-2048', stage: 'Payment Scheduled' }], 'approve', '');
  ok(bulkResult.ok && bulkResult.done.length === 0 && bulkResult.failed.length === 1, 'approving the same bill twice is caught (stale or not allowed)', bulkResult.failed);

  W.reset(admin.email);
  bulkResult = W.actMany(casey.email, [{ id: 'BILL-2048', stage: 'Department Approval' }], 'reject', '');
  ok(!bulkResult.ok && bulkResult.field === 'note' && seenAt('BILL-2048') === 'Department Approval', 'bulk reject without a reason is refused before anything changes');
  bulkResult = W.actMany(casey.email, [{ id: 'BILL-2048', stage: 'Department Approval' }], 'reject', 'Wrong PO number');
  ok(bulkResult.ok && bulkResult.done.length === 1 && seenAt('BILL-2048') === 'Coding', 'bulk reject with a reason works and returns the bill to Coding');
  bulkResult = W.actMany(jane.email, [{ id: 'BILL-2048', stage: 'Coding' }, { id: 'BILL-2044', stage: 'Coding' }, { id: 'BILL-2041', stage: 'Department Approval' }], 'submit', 'Fixed and ready');
  ok(bulkResult.ok && bulkResult.done.length === 2 && bulkResult.failed.length === 1 && seenAt('BILL-2044') === 'Department Approval', 'bulk routing: AP sends two Coding bills for approval; the one already waiting is refused', bulkResult.failed);
  ok(W.invoices().find(r => r.id === 'BILL-2044').history.slice(-1)[0].note === 'Fixed and ready', 'the comment is recorded on every bill');
  ok(!W.actMany(jane.email, [], 'submit', '').ok && !W.actMany(jane.email, [{ id: 'BILL-2044', stage: 'x' }], 'delete', '').ok, 'nothing selected and unknown actions are refused');
  const tooMany = Array.from({ length: W.BULK_MAX + 1 }, (_, i) => ({ id: 'BILL-' + i, stage: 'x' }));
  ok(!W.actMany(jane.email, tooMany, 'submit', '').ok, 'more than ' + W.BULK_MAX + ' at once is refused');
  const stranger = W.actMany('stranger@example.com', [{ id: 'BILL-2044', stage: 'Department Approval' }], 'approve', '');
  ok(stranger.ok && stranger.done.length === 0 && stranger.failed.length === 1 && stranger.failed[0].error === 'You no longer have access.', 'a stranger cannot use a bulk action: nothing is done and the bill is refused', stranger.failed);
  W.reset(admin.email);

  console.log('\n' + (failures ? failures + ' TEST(S) FAILED' : 'ALL TESTS PASSED'));
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error('CRASH', e); process.exit(2); });


