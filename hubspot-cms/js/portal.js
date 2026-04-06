/**
 * RPM Client Portal — Core JavaScript
 * Handles smooth scrolling, number formatting, digest loading,
 * and recommendation approve/dismiss actions.
 */

(function () {
  'use strict';

  // ── Config (injected by HubL in client-portal.html) ──
  var API_BASE      = window.__PORTAL_API_BASE     || '';
  var PORTAL_UUID   = window.__PORTAL_UUID         || '';
  var COMPANY_ID    = window.__PORTAL_COMPANY_ID   || '';
  var PROPERTY_NAME = window.__PORTAL_PROPERTY_NAME|| '';
  var MARKET        = window.__PORTAL_MARKET       || '';
  var PORTAL_EMAIL  = window.__PORTAL_EMAIL        || '';

  // ══════════════════════════════════════════
  //  AI DIGEST (Phase 6)
  // ══════════════════════════════════════════

  function loadDigest() {
    if (!document.getElementById('digest-section')) return;
    if (!API_BASE || !PORTAL_UUID || !COMPANY_ID) {
      showDigestError();
      return;
    }

    var url = API_BASE + '/api/digest'
      + '?uuid='       + encodeURIComponent(PORTAL_UUID)
      + '&company_id=' + encodeURIComponent(COMPANY_ID)
      + '&name='       + encodeURIComponent(PROPERTY_NAME)
      + '&market='     + encodeURIComponent(MARKET);

    fetch(url, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
        'X-Portal-Email': PORTAL_EMAIL,
      },
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var text = data.digest || '';
        if (text) {
          document.getElementById('digest-loading').style.display = 'none';
          var el = document.getElementById('digest-text');
          el.textContent = text;
          el.style.display = '';
        } else {
          showDigestError();
        }
      })
      .catch(function () { showDigestError(); });
  }

  function showDigestError() {
    var loading = document.getElementById('digest-loading');
    var error   = document.getElementById('digest-error');
    if (loading) loading.style.display = 'none';
    if (error)   error.style.display = '';
  }

  // ══════════════════════════════════════════
  //  RECOMMENDATIONS FEED (Phase 7)
  // ══════════════════════════════════════════

  window.approveRec = function (btn) {
    var recId    = btn.getAttribute('data-rec-id');
    var recType  = btn.getAttribute('data-rec-type');
    var recTitle = btn.getAttribute('data-rec-title');
    var recBody  = btn.getAttribute('data-rec-body');

    if (!recId || !API_BASE) return;

    btn.disabled = true;
    var dismissBtn = btn.parentElement.querySelector('.btn-dismiss');
    if (dismissBtn) dismissBtn.disabled = true;
    btn.textContent = 'Submitting...';

    fetch(API_BASE + '/api/approve', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Portal-Email': PORTAL_EMAIL,
      },
      body: JSON.stringify({
        rec_id:        recId,
        rec_type:      recType,
        property_uuid: PORTAL_UUID,
        company_id:    COMPANY_ID,
        property_name: PROPERTY_NAME,
        rec_title:     recTitle,
        rec_body:      recBody,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var card    = btn.closest('.rec-card');
        var confirm = document.getElementById('rec-confirm-' + recId);
        if (data.status === 'ok' || data.status === 'partial') {
          showRecConfirm(confirm, 'success',
            'Your approval has been received. Your team has been notified and a task has been created.');
          if (card) card.classList.add('actioned');
        } else {
          showRecConfirm(confirm, 'error',
            'We received your request. Something went wrong — your AM has been notified.');
          btn.disabled = false;
          if (dismissBtn) dismissBtn.disabled = false;
          btn.textContent = 'Approve';
        }
      })
      .catch(function () {
        var confirm = document.getElementById('rec-confirm-' + recId);
        showRecConfirm(confirm, 'error',
          'We received your request. Something went wrong — your AM has been notified.');
        btn.textContent = 'Approve';
        btn.disabled = false;
      });
  };

  window.dismissRec = function (btn) {
    var recId = btn.getAttribute('data-rec-id');
    if (!recId || !API_BASE) return;

    btn.disabled = true;
    var approveBtn = btn.parentElement.querySelector('.btn-approve');
    if (approveBtn) approveBtn.disabled = true;

    fetch(API_BASE + '/api/dismiss', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Portal-Email': PORTAL_EMAIL,
      },
      body: JSON.stringify({
        rec_id:        recId,
        company_id:    COMPANY_ID,
        property_uuid: PORTAL_UUID,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var card    = btn.closest('.rec-card');
        var confirm = document.getElementById('rec-confirm-' + recId);
        showRecConfirm(confirm, 'success', 'Recommendation dismissed.');
        if (card) card.classList.add('actioned');
      })
      .catch(function () {
        btn.disabled = false;
        if (approveBtn) approveBtn.disabled = false;
      });
  };

  function showRecConfirm(el, type, message) {
    if (!el) return;
    el.className = 'rec-confirm ' + type;
    el.textContent = message;
    el.style.display = '';
  }

  // ── Init ──
  document.addEventListener('DOMContentLoaded', function () {
    loadDigest();
  });

  // ── Number formatting ──
  window.formatCurrency = function (num) {
    return '$' + Number(num).toLocaleString('en-US', { maximumFractionDigits: 0 });
  };

  window.formatNumber = function (num) {
    return Number(num).toLocaleString('en-US');
  };

  // ── Smooth scroll to anchor ──
  document.addEventListener('click', function (e) {
    var link = e.target.closest('a[href^="#"]');
    if (!link) return;
    var target = document.querySelector(link.getAttribute('href'));
    if (target) {
      e.preventDefault();
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  });

  // ── Escape key closes modals ──
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      var lightbox = document.getElementById('lightbox');
      if (lightbox && lightbox.classList.contains('active')) {
        closeLightbox();
      }
      var uploadModal = document.getElementById('upload-modal');
      if (uploadModal && uploadModal.classList.contains('active')) {
        uploadModal.classList.remove('active');
      }
    }
  });
})();
