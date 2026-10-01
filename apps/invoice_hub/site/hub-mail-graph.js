/*
 * Microsoft Graph mail provider, for when the service reads real Outlook mail.
 * NOT loaded by the demo. To use it, add this file to the page after hub-mail.js, sign the person in
 * with Microsoft (MSAL), and call:
 *
 *   Hub.Mail.setProvider(Hub.createGraphMailProvider(function () { return getAccessTokenSomehow(); }));
 *
 * It needs the person's own permission to read their mail ("Mail.Read", delegated). Only the names of
 * attachments are requested, never their contents.
 *
 * STATUS: written from Microsoft's documented API shape and tested only against MOCKED responses.
 * Before relying on it, try each request in Microsoft Graph Explorer and check what real mail returns.
 * Things to confirm there: the message fields below, the attachment fields, whether listing
 * /me/messages includes Sent Items, and how many attachments requests are needed per message.
 */
(function (global) {
  'use strict';

  var Hub = global.InvoiceHub;
  var GRAPH = 'https://graph.microsoft.com/v1.0';
  var MESSAGE_FIELDS = 'id,subject,from,toRecipients,ccRecipients,receivedDateTime,sentDateTime,bodyPreview,hasAttachments,parentFolderId,webLink';
  var PARALLEL = 4; // attachment requests at once

  function person(recipient) {
    var a = (recipient && recipient.emailAddress) || {};
    return (a.name || a.address || '') + ' <' + (a.address || '') + '>';
  }

  /** One Graph message plus its attachments, in the shape the rest of the app expects. */
  function fromGraphMessage(msg, attachments, folderPathById, mailboxEmail) {
    return {
      id: msg.id,
      mailbox: mailboxEmail,
      folder: folderPathById[msg.parentFolderId] || 'Unknown folder',
      from: msg.from ? person(msg.from) : '',
      to: (msg.toRecipients || []).concat(msg.ccRecipients || []).map(person).join('; '),
      subject: msg.subject || '',
      receivedOn: new Date(msg.receivedDateTime || msg.sentDateTime),
      preview: msg.bodyPreview || '',
      webLink: msg.webLink || '',
      attachments: (attachments || [])
        .filter(function (a) { return !a.isInline; })
        .map(function (a) { return { name: a.name, sizeKb: Math.max(1, Math.round((a.size || 0) / 1024)) }; })
    };
  }

  /**
   * options: { getAccessToken(): Promise<string>, fetch (defaults to window.fetch), baseUrl, since: Date }
   * `since` limits the messages asked for to those received on or after that date.
   */
  function createGraphMailProvider(getAccessToken, options) {
    var settings = options || {};
    var base = settings.baseUrl || GRAPH;
    var doFetch = settings.fetch || function () { return global.fetch.apply(global, arguments); };
    var folderPaths = null; // folder id -> "Inbox / Sub folder", filled by fetchMailboxes

    function get(url) {
      return Promise.resolve(getAccessToken()).then(function (token) {
        return doFetch(url, { headers: { Authorization: 'Bearer ' + token } });
      }).then(function (res) {
        if (!res.ok) throw new Error('Microsoft Graph answered ' + res.status + '. Check that you are signed in and have allowed access to your mail.');
        return res.json();
      });
    }

    // Follows @odata.nextLink until every page has been read.
    function getAll(url) {
      var items = [];
      function next(u) {
        return get(u).then(function (page) {
          items = items.concat(page.value || []);
          return page['@odata.nextLink'] ? next(page['@odata.nextLink']) : items;
        });
      }
      return next(url);
    }

    function folderTree(parentUrl, parentPath, out) {
      return getAll(parentUrl).then(function (folders) {
        return folders.reduce(function (chain, folder) {
          return chain.then(function () {
            var path = parentPath ? parentPath + ' / ' + folder.displayName : folder.displayName;
            out[folder.id] = path;
            return folder.childFolderCount > 0
              ? folderTree(base + '/me/mailFolders/' + encodeURIComponent(folder.id) + '/childFolders?$top=100', path, out)
              : null;
          });
        }, Promise.resolve());
      });
    }

    function loadFolders() {
      if (folderPaths) return Promise.resolve(folderPaths);
      var paths = {};
      return folderTree(base + '/me/mailFolders?$top=100', '', paths).then(function () { folderPaths = paths; return paths; });
    }

    // Runs `work` over `items` a few at a time.
    function inBatches(items, work) {
      var results = new Array(items.length);
      var index = 0;
      function worker() {
        if (index >= items.length) return Promise.resolve();
        var mine = index++;
        return work(items[mine]).then(function (r) { results[mine] = r; return worker(); });
      }
      var workers = [];
      for (var i = 0; i < Math.min(PARALLEL, items.length); i++) workers.push(worker());
      return Promise.all(workers).then(function () { return results; });
    }

    return {
      name: 'Microsoft Outlook (Graph)',
      isSample: false,

      fetchMailboxes: function (user) {
        return loadFolders().then(function (paths) {
          return [{ email: user.email, name: user.name, folders: Object.keys(paths).map(function (id) { return paths[id]; }) }];
        });
      },

      fetchMessages: function (user) {
        return loadFolders().then(function (paths) {
          var filter = 'hasAttachments eq true' + (settings.since ? ' and receivedDateTime ge ' + settings.since.toISOString() : '');
          var url = base + '/me/messages?$select=' + MESSAGE_FIELDS + '&$filter=' + encodeURIComponent(filter) + '&$top=50';
          return getAll(url).then(function (messages) {
            return inBatches(messages, function (msg) {
              var attachmentsUrl = base + '/me/messages/' + encodeURIComponent(msg.id) + '/attachments?$select=name,size,contentType,isInline';
              return getAll(attachmentsUrl).then(function (attachments) { return fromGraphMessage(msg, attachments, paths, user.email); });
            });
          });
        });
      }
    };
  }

  Hub.createGraphMailProvider = function (getAccessToken, options) {
    return createGraphMailProvider(getAccessToken, options);
  };
  Hub.GraphMail = { fromGraphMessage: fromGraphMessage };
})(window);
