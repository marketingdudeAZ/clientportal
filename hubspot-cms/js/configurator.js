/**
 * Phase 5: Budget Configurator JavaScript
 * Tier selection, running total calculation, and submit to webhook.
 */

(function () {
  'use strict';

  var selections = {};

  // ── Tier Card Selection ──
  document.querySelectorAll('.tier-card').forEach(function (card) {
    card.addEventListener('click', function () {
      var row = this.closest('.channel-row');
      var channel = row.getAttribute('data-channel');

      // Toggle: deselect if already selected
      if (this.classList.contains('selected')) {
        this.classList.remove('selected');
        delete selections[channel];
      } else {
        // Deselect siblings
        row.querySelectorAll('.tier-card').forEach(function (c) { c.classList.remove('selected'); });
        this.classList.add('selected');

        selections[channel] = {
          tier: this.getAttribute('data-tier'),
          monthly: parseInt(this.getAttribute('data-price'), 10),
          setup: parseInt(this.getAttribute('data-setup') || '0', 10),
        };
      }

      updateTotals();
    });
  });

  // ── Calculate & Render Totals ──
  function updateTotals() {
    var currentTotal = calculateCurrentTotal();
    var newTotal = 0;
    var setupTotal = 0;

    // Sum selections
    Object.keys(selections).forEach(function (ch) {
      newTotal += selections[ch].monthly;
      setupTotal += selections[ch].setup;
    });

    // For channels without a selection, keep current value
    document.querySelectorAll('.channel-row').forEach(function (row) {
      var ch = row.getAttribute('data-channel');
      if (!selections[ch]) {
        newTotal += getCurrentChannelValue(row);
      }
    });

    var delta = newTotal - currentTotal;

    var elCurrent = document.getElementById('total-current');
    var elNew = document.getElementById('total-new');
    var elDelta = document.getElementById('total-delta');
    var elSetup = document.getElementById('total-setup');
    var elSubmit = document.getElementById('configurator-submit');

    if (elCurrent) elCurrent.textContent = formatCurrency(currentTotal);
    if (elNew) elNew.textContent = formatCurrency(newTotal);

    if (elDelta) {
      var sign = delta > 0 ? '+' : '';
      elDelta.textContent = sign + formatCurrency(delta);
      elDelta.className = 'total-value ' + (delta > 0 ? 'positive' : delta < 0 ? 'negative' : 'zero');
    }

    if (elSetup) elSetup.textContent = formatCurrency(setupTotal);

    // Enable submit only if there are selections
    if (elSubmit) elSubmit.disabled = Object.keys(selections).length === 0;
  }

  function calculateCurrentTotal() {
    var total = 0;
    document.querySelectorAll('.channel-row').forEach(function (row) {
      total += getCurrentChannelValue(row);
    });
    return total;
  }

  function getCurrentChannelValue(row) {
    var currentCard = row.querySelector('.tier-current');
    if (currentCard) {
      return parseInt(currentCard.getAttribute('data-price'), 10) || 0;
    }
    // For paid media, current is stored in data-current
    var currentVal = row.getAttribute('data-current');
    return parseInt(currentVal, 10) || 0;
  }

  // Initialize totals on load
  updateTotals();

  // ── Submit Configurator ──
  window.submitConfigurator = function () {
    var body = document.querySelector('.configurator-body');
    var companyId = body.getAttribute('data-company-id');
    var uuid = body.getAttribute('data-uuid');

    var currentTotal = calculateCurrentTotal();
    var newTotal = 0;
    var setupTotal = 0;

    Object.keys(selections).forEach(function (ch) {
      newTotal += selections[ch].monthly;
      setupTotal += selections[ch].setup;
    });

    document.querySelectorAll('.channel-row').forEach(function (row) {
      var ch = row.getAttribute('data-channel');
      if (!selections[ch]) {
        newTotal += getCurrentChannelValue(row);
      }
    });

    var payload = {
      uuid: uuid,
      hubspot_company_id: companyId,
      selections: selections,
      totals: {
        monthly: newTotal,
        setup: setupTotal,
        delta: newTotal - currentTotal,
      },
    };

    var submitBtn = document.getElementById('configurator-submit');
    var statusEl = document.getElementById('submit-status');

    submitBtn.disabled = true;
    submitBtn.textContent = 'Submitting...';
    statusEl.style.display = 'none';

    fetch('/api/configurator-submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
      .then(function (res) {
        if (!res.ok) throw new Error('Submission failed');
        return res.json();
      })
      .then(function (data) {
        statusEl.textContent = 'Selections submitted. Quote sent to your email.';
        statusEl.className = 'submit-status success';
        statusEl.style.display = '';
        submitBtn.textContent = 'Submitted';
      })
      .catch(function (err) {
        statusEl.textContent = 'Submission failed. Please try again.';
        statusEl.className = 'submit-status error';
        statusEl.style.display = '';
        submitBtn.disabled = false;
        submitBtn.textContent = 'Confirm My Selections';
      });
  };
})();
