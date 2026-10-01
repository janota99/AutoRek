/*
 * Reviews & Feedback page behavior.
 *
 * Validates the feedback form, adds submitted reviews to the list, keeps the
 * review count and average rating current, and saves submissions in this
 * browser's localStorage. Nothing is sent to a server.
 *
 * This is a classic script (not an ES module) so it also works when the page
 * is opened directly from disk, where browsers block module scripts.
 */
(function () {
  'use strict';

  var STORAGE_KEY = 'invoiceTracker.reviews.v1';
  var LIMITS = { name: [2, 80], department: [0, 80], feedback: [10, 1000] };

  var form = document.getElementById('feedbackForm');
  if (!form) return;

  var reviewsPanel = document.querySelector('.reviews-panel');
  var panelHeading = reviewsPanel.querySelector('.panel-heading');
  var countLabel = document.getElementById('reviewCount');
  var statusBox = document.getElementById('formStatus');
  var clearButton = document.getElementById('clearReviews');
  var charCount = document.getElementById('feedback-count');

  var fields = {
    name: form.elements.customerName,
    department: form.elements.department,
    rating: form.elements.rating,
    recommendation: form.querySelector('input[name="recommendation"]'),
    feedback: form.elements.feedback
  };

  /* ---------- storage ---------- */

  function loadReviews() {
    try {
      var saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
      return Array.isArray(saved) ? saved.filter(isValidReview) : [];
    } catch (err) {
      return [];
    }
  }

  function saveReviews(list) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
      return true;
    } catch (err) {
      return false; // storage blocked or full; the review still shows for this visit
    }
  }

  function isValidReview(r) {
    return r && typeof r.name === 'string' && typeof r.text === 'string' &&
      r.rating >= 1 && r.rating <= 5 && (r.recommend === 'yes' || r.recommend === 'no');
  }

  /* ---------- validation ---------- */

  function readValues() {
    var picked = form.querySelector('input[name="recommendation"]:checked');
    return {
      name: fields.name.value.trim(),
      department: fields.department.value.trim(),
      rating: Number(fields.rating.value),
      recommend: picked ? picked.value : '',
      text: fields.feedback.value.trim()
    };
  }

  function validate(v) {
    var errors = {};
    if (v.name.length < LIMITS.name[0]) errors.name = 'Enter your name (at least 2 characters).';
    else if (v.name.length > LIMITS.name[1]) errors.name = 'Name must be 80 characters or fewer.';
    if (v.department.length > LIMITS.department[1]) errors.department = 'Keep this to 80 characters or fewer.';
    if (!(v.rating >= 1 && v.rating <= 5)) errors.rating = 'Select a rating.';
    if (!v.recommend) errors.recommendation = 'Choose Yes or No.';
    if (v.text.length < LIMITS.feedback[0]) errors.feedback = 'Add a little more detail (at least 10 characters).';
    else if (v.text.length > LIMITS.feedback[1]) errors.feedback = 'Feedback must be 1,000 characters or fewer.';
    return errors;
  }

  function errorBox(key) {
    return document.getElementById(
      (key === 'name' ? 'customerName' : key) + '-error'
    );
  }

  function showError(key, message) {
    var box = errorBox(key);
    box.textContent = message;
    box.hidden = false;
    if (key === 'recommendation') return; // radios are flagged through the fieldset text
    fields[key].setAttribute('aria-invalid', 'true');
  }

  function clearError(key) {
    var box = errorBox(key);
    box.textContent = '';
    box.hidden = true;
    fields[key].removeAttribute('aria-invalid');
  }

  function clearAllErrors() {
    Object.keys(fields).forEach(clearError);
  }

  /* ---------- rendering ---------- */

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text; // textContent: user text is never parsed as HTML
    return node;
  }

  function initialsFor(name) {
    // Only letters and digits count, so punctuation in a name never becomes an initial.
    var words = name.split(/\s+/).map(function (w) {
      return (w.match(/[\p{L}\p{N}]/gu) || []).join('');
    }).filter(Boolean);
    var letters = words.length > 1
      ? [words[0], words[words.length - 1]].map(function (w) { return Array.from(w)[0]; })
      : Array.from(words[0] || '?').slice(0, 2);
    return letters.join('').toUpperCase();
  }

  function formatDate(iso) {
    var d = new Date(iso);
    return isNaN(d) ? '' : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  }

  function buildReviewCard(review) {
    var card = el('article', 'review-card');
    card.dataset.rating = String(review.rating);
    card.dataset.userReview = 'true';

    var top = el('div', 'review-topline');
    var reviewer = el('div', 'reviewer');
    var dark = reviewsPanel.querySelectorAll('.review-card').length % 2 === 1;
    var initials = el('span', 'reviewer-initials' + (dark ? ' dark' : ''), initialsFor(review.name));
    initials.setAttribute('aria-hidden', 'true');
    var who = el('div');
    who.appendChild(el('strong', '', review.name));
    who.appendChild(el('span', '', review.role || 'Prototype reviewer'));
    reviewer.appendChild(initials);
    reviewer.appendChild(who);

    var rating = el('div', 'rating');
    rating.setAttribute('aria-label', review.rating + ' out of 5 stars');
    rating.appendChild(document.createTextNode('★'.repeat(review.rating)));
    if (review.rating < 5) rating.appendChild(el('span', '', '★'.repeat(5 - review.rating)));

    top.appendChild(reviewer);
    top.appendChild(rating);

    var quote = el('blockquote', '', '“' + review.text + '”');

    var rec = el('p', 'recommendation', 'Would recommend: ');
    rec.appendChild(el('strong', '', review.recommend === 'yes' ? 'Yes' : 'No'));
    var when = formatDate(review.submittedAt);
    if (when) rec.appendChild(document.createTextNode(' · Submitted ' + when));

    card.appendChild(top);
    card.appendChild(quote);
    card.appendChild(rec);
    return card;
  }

  function addCardToTop(review) {
    var card = buildReviewCard(review);
    panelHeading.after(card); // newest first, directly under the panel heading
    return card;
  }

  function updateSummary() {
    var cards = reviewsPanel.querySelectorAll('.review-card');
    var total = 0;
    cards.forEach(function (c) { total += Number(c.dataset.rating) || 0; });
    var n = cards.length;
    countLabel.textContent = n + (n === 1 ? ' review' : ' reviews') +
      (n ? ' · ' + (total / n).toFixed(1) + ' avg' : '');
    clearButton.hidden = !reviewsPanel.querySelector('[data-user-review]');
  }

  function showStatus(message, kind) {
    statusBox.textContent = message;
    statusBox.className = 'form-status' + (kind ? ' is-' + kind : '');
    statusBox.hidden = !message;
  }

  function updateCharCount() {
    charCount.textContent = fields.feedback.value.length + ' / ' + LIMITS.feedback[1];
  }

  /* ---------- events ---------- */

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    showStatus('');
    clearAllErrors();

    var values = readValues();
    var errors = validate(values);
    var keys = Object.keys(errors);

    if (keys.length) {
      keys.forEach(function (k) { showError(k, errors[k]); });
      fields[keys[0]].focus(); // first invalid field, in form order
      return;
    }

    var review = {
      name: values.name,
      role: values.department,
      rating: values.rating,
      recommend: values.recommend,
      text: values.text,
      submittedAt: new Date().toISOString()
    };

    var saved = loadReviews();
    saved.push(review);
    var persisted = saveReviews(saved);

    var card = addCardToTop(review);
    updateSummary();
    form.reset();
    updateCharCount();

    showStatus(persisted
      ? 'Thank you. Your review was added to the list.'
      : 'Your review was added, but this browser blocked saving, so it will disappear when you leave the page.',
      persisted ? 'success' : 'warning');
    card.scrollIntoView({ block: 'nearest' });
  });

  // Clear a field's error as soon as the user starts fixing it.
  form.addEventListener('input', function (event) {
    var target = event.target;
    Object.keys(fields).forEach(function (k) {
      if (k === 'recommendation' ? target.name === 'recommendation' : target === fields[k]) clearError(k);
    });
    if (target === fields.feedback) updateCharCount();
  });
  form.addEventListener('change', function (event) {
    if (event.target.name === 'recommendation') clearError('recommendation');
    if (event.target === fields.rating) clearError('rating');
  });

  clearButton.addEventListener('click', function () {
    if (!window.confirm('Remove the reviews you added in this browser? The sample reviews will stay.')) return;
    saveReviews([]);
    reviewsPanel.querySelectorAll('[data-user-review]').forEach(function (c) { c.remove(); });
    updateSummary();
    showStatus('Your added reviews were removed.', 'success');
  });

  /* ---------- initial render ---------- */

  loadReviews().forEach(addCardToTop);
  updateSummary();
  updateCharCount();
})();
