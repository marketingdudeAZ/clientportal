/**
 * RPM Client Portal — Core JavaScript
 * Handles smooth scrolling, number formatting, and shared utilities.
 */

(function () {
  'use strict';

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
