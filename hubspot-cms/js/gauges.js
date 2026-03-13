/**
 * Phase 2: Health Score Gauge Animations
 * Animates SVG arc on scroll into view.
 */

(function () {
  'use strict';

  var gauges = document.querySelectorAll('.gauge-arc');
  if (!gauges.length) return;

  var animated = new Set();

  function animateGauge(el) {
    if (animated.has(el)) return;
    animated.add(el);

    // Store final dashoffset, then animate from full to target
    var finalOffset = parseFloat(el.getAttribute('stroke-dashoffset'));
    var dashArray = el.getAttribute('stroke-dasharray').split(' ');
    var arcLength = parseFloat(dashArray[0]);

    el.setAttribute('stroke-dashoffset', arcLength);
    el.getBoundingClientRect(); // force reflow

    requestAnimationFrame(function () {
      el.style.transition = 'stroke-dashoffset 1s ease-out';
      el.setAttribute('stroke-dashoffset', finalOffset);
    });
  }

  // Intersection Observer for scroll-triggered animation
  if ('IntersectionObserver' in window) {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          animateGauge(entry.target);
        }
      });
    }, { threshold: 0.3 });

    gauges.forEach(function (gauge) { observer.observe(gauge); });
  } else {
    // Fallback: animate immediately
    gauges.forEach(animateGauge);
  }
})();
