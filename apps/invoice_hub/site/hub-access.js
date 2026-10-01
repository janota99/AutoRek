/*
 * Access list and sign-in for the Invoice Tracker prototype.
 *
 * The access list is kept in localStorage and the signed-in email in
 * sessionStorage. This SIMULATES access control for demonstration only: it
 * runs in the visitor's browser, so it cannot keep anyone out. The real service
 * must authenticate on a server (for example Microsoft Entra ID sign-in) and
 * authorize every data request there. This module's public surface
 * (list/add/remove/signIn/currentUser/signOut) is what that server would replace.
 */
(function (global) {
  'use strict';

  var Hub = global.InvoiceHub;
  var USERS_KEY = 'invoiceHub.users.v1';
  var SESSION_KEY = 'invoiceHub.session.v1';
  var EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  /* ---------- storage (falls back to memory if the browser blocks it) ---------- */

  var memory = {};

  function read(area, key) {
    try { return global[area].getItem(key); } catch (err) { return memory[area + key] || null; }
  }
  function write(area, key, value) {
    try { global[area].setItem(key, value); } catch (err) { memory[area + key] = value; }
  }
  function remove(area, key) {
    try { global[area].removeItem(key); } catch (err) { delete memory[area + key]; }
  }

  // Shared with the workflow and inbox modules so they use the same fallback behavior.
  Hub.Store = { read: read, write: write, remove: remove };

  /* ---------- helpers ---------- */

  function normalizeEmail(value) {
    return String(value || '').trim().toLowerCase();
  }

  function isValidUser(u) {
    return u && typeof u.email === 'string' && typeof u.name === 'string' && Hub.ROLES[u.role] &&
      (u.role !== 'approver' || Hub.DEPARTMENTS.indexOf(u.department) !== -1);
  }

  function cleanUser(u) {
    var out = { email: normalizeEmail(u.email), name: u.name.trim(), role: u.role };
    if (u.role === 'approver') out.department = u.department;
    return out;
  }

  function loadUsers() {
    try {
      var saved = JSON.parse(read('localStorage', USERS_KEY));
      if (Array.isArray(saved) && saved.length && saved.every(isValidUser)) return saved.map(cleanUser);
    } catch (err) { /* fall through to seed data */ }
    var seeded = Hub.SEED_USERS.map(cleanUser);
    saveUsers(seeded);
    return seeded;
  }

  function saveUsers(users) {
    write('localStorage', USERS_KEY, JSON.stringify(users));
  }

  function findIn(users, email) {
    var wanted = normalizeEmail(email);
    return users.filter(function (u) { return u.email === wanted; })[0] || null;
  }

  function isAdmin(user) {
    return !!user && user.role === 'admin';
  }

  /* ---------- public API ---------- */

  var Access = {
    list: function () {
      return loadUsers();
    },

    /** Adds a person to the access list. Only an administrator may do this. */
    add: function (actingEmail, data) {
      var users = loadUsers();
      if (!isAdmin(findIn(users, actingEmail))) return { ok: false, error: 'Only an administrator can add people.' };

      var email = normalizeEmail(data.email);
      var name = String(data.name || '').trim();
      var errors = {};
      if (!EMAIL_PATTERN.test(email) || email.length > 120) errors.email = 'Enter a valid email address.';
      else if (findIn(users, email)) errors.email = 'That email already has access.';
      if (name.length < 2 || name.length > 80) errors.name = 'Enter a name (2–80 characters).';
      if (!Hub.ROLES[data.role]) errors.role = 'Choose a role.';
      if (data.role === 'approver' && Hub.DEPARTMENTS.indexOf(data.department) === -1) errors.department = 'Choose a department for approvers.';
      if (Object.keys(errors).length) return { ok: false, errors: errors };

      var user = cleanUser({ email: email, name: name, role: data.role, department: data.department });
      users.push(user);
      saveUsers(users);
      return { ok: true, user: user };
    },

    /** Removes a person. Only an administrator may do this, and never themselves. */
    remove: function (actingEmail, targetEmail) {
      var users = loadUsers();
      if (!isAdmin(findIn(users, actingEmail))) return { ok: false, error: 'Only an administrator can remove people.' };
      var target = findIn(users, targetEmail);
      if (!target) return { ok: false, error: 'That person is not on the access list.' };
      if (target.email === normalizeEmail(actingEmail)) return { ok: false, error: 'You cannot remove your own access.' };
      saveUsers(users.filter(function (u) { return u !== target; }));
      return { ok: true };
    },

    /** Starts a session for an email that is on the access list. */
    signIn: function (email) {
      var user = findIn(loadUsers(), email);
      if (!user) return { ok: false, error: 'That email is not on the access list. Ask an administrator to add it.' };
      write('sessionStorage', SESSION_KEY, user.email);
      return { ok: true, user: user };
    },

    /** The signed-in user, re-checked against the access list so removal takes effect. */
    currentUser: function () {
      var email = read('sessionStorage', SESSION_KEY);
      var user = email ? findIn(loadUsers(), email) : null;
      if (email && !user) remove('sessionStorage', SESSION_KEY);
      return user;
    },

    signOut: function () {
      remove('sessionStorage', SESSION_KEY);
    }
  };

  Hub.Access = Access;
})(window);
