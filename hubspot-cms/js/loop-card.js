/*
 * Loop Forecast Card — fetches /api/loop/forecast for the current
 * property and renders the small card. Designed to be safe in the
 * existing dashboard: hides itself entirely when there's no forecast,
 * never throws.
 *
 * Looks for these globals (any one path works):
 *   window.PROPERTY_UUID         — set by client-portal.html template
 *   window.RPM_PROPERTY_UUID     — alt name some pages use
 *   data-uuid attribute on body  — fallback
 *
 * Same for the API base:
 *   window.RPM_API_BASE  (default: production)
 *
 * Optional: window.PORTAL_EMAIL for X-Portal-Email; otherwise
 * the card hits the auth-required endpoint via the user's session.
 */

(function () {
  'use strict';

  function $(s) { return document.querySelector(s); }

  function resolveUuid() {
    return (window.PROPERTY_UUID
         || window.RPM_PROPERTY_UUID
         || (document.body && document.body.getAttribute('data-uuid'))
         || (new URLSearchParams(window.location.search)).get('uuid')
         || '').toString().trim();
  }

  function api(path) {
    var base = window.RPM_API_BASE || 'https://rpm-portal-server.onrender.com';
    var headers = {};
    if (window.PORTAL_EMAIL) headers['X-Portal-Email'] = window.PORTAL_EMAIL;
    return fetch(base + path, { method: 'GET', headers: headers })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      });
  }

  function prettyChan(k) {
    return ({
      paid_search: 'Paid Search', paid_social: 'Paid Social',
      seo: 'SEO', reputation: 'Reputation', creative: 'Creative'
    }[k] || k);
  }

  function fmtRelative(iso) {
    if (!iso) return '';
    var dt = new Date(iso);
    if (isNaN(dt.getTime())) return '';
    var diff = (Date.now() - dt.getTime()) / 1000;
    if (diff < 60)      return 'just now';
    if (diff < 3600)    return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400)   return Math.floor(diff / 3600) + 'h ago';
    if (diff < 86400*7) return Math.floor(diff / 86400) + 'd ago';
    return dt.toLocaleDateString();
  }

  function render(data) {
    var f = data && data.forecast;
    if (!f || f.forecast_leases === null || f.forecast_leases === undefined) return;

    var card = $('#loop-forecast-card');
    if (!card) return;
    card.style.display = '';

    // Headline number + range
    $('#lfc-num').textContent = (typeof f.forecast_leases === 'number'
      ? Number(f.forecast_leases.toFixed(1))
      : f.forecast_leases);

    var lo = f.ci_low, hi = f.ci_high;
    var conf = f.confidence_level || 0.5;
    $('#lfc-sub').innerHTML =
      'projected leases · 30d &nbsp;·&nbsp; ' +
      '<span style="color:#9ca3af">range ' + lo + '–' + hi + ', ' +
      Math.round(conf * 100) + '% confidence</span>';

    // Channel allocation — only show non-zero spend
    var alloc = f.channel_allocation || {};
    var channels = Object.keys(alloc).filter(function (c) {
      return (alloc[c] || {}).spend > 0;
    });
    var html = channels.map(function (c) {
      var x = alloc[c];
      return (
        '<div class="lfc-chan">' +
          '<div class="lfc-chan-label">' + prettyChan(c) + '</div>' +
          '<div class="lfc-chan-val">$' + Math.round(x.spend).toLocaleString() +
            ' <span style="color:#a8a29e;font-weight:500;font-size:11px">→ ' +
            (x.forecast_leases || 0).toFixed(1) + ' lease' +
            (x.forecast_leases === 1 ? '' : 's') + '</span>' +
          '</div>' +
        '</div>'
      );
    }).join('');
    $('#lfc-channels').innerHTML = html;

    // First actionable recommendation
    var recs = (f.recommendations || []).filter(function (r) {
      return ['hold','collect_more_data','expand_inputs'].indexOf(r.action) < 0;
    });
    if (recs.length) {
      var r = recs[0];
      var impact = r.forecast_impact ? ' (+' + r.forecast_impact + ' leases)' : '';
      $('#lfc-rec').innerHTML =
        '<b>💡 ' + (r.action || '').replace(/[-_]/g, ' ') +
        '</b> — ' + (r.reason || '') + impact;
      $('#lfc-rec').style.display = '';
    }

    // Meta line
    $('#lfc-meta').textContent =
      'Methodology: ' + (f.methodology || 'unknown') +
      ' · Run ' + fmtRelative(f.run_at);

    // Wire the "See full Loop" link to the subpage URL convention
    var uuid = resolveUuid();
    var link = $('#lfc-open-loop');
    if (link && uuid) {
      // Option B URL pattern (?view=loop) — works once template wiring lands
      link.href = '/portal-dashboard?uuid=' + encodeURIComponent(uuid) + '&view=loop';
    }
  }

  function init() {
    var uuid = resolveUuid();
    if (!uuid) return;   // no property context — silently skip
    api('/api/loop/forecast?uuid=' + encodeURIComponent(uuid))
      .then(render)
      .catch(function () { /* silent: no card if API fails */ });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
