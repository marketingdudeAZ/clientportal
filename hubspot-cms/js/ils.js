/*
 * RPM Living Client Portal — ILS Performance page (ADR 0021)
 *
 * Renders apartments.com listing performance + lead data for one property:
 *  - Attract KPIs (impressions / views) with period-over-period delta
 *  - Engage KPIs (leads by source) with delta
 *  - A daily impressions + leads trend chart (inline SVG, no deps)
 *
 * Vanilla JS. Talks to /api/ils/* on the Render Flask service.
 */

(function () {
  'use strict';

  var cfg = window.RPM_ILS_CONFIG || {};
  if (!cfg.uuid) {
    console.error('RPM_ILS_CONFIG.uuid missing — cannot init ILS view');
    return;
  }
  var days = cfg.days || 30;

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  function api(path) {
    var headers = {};
    if (cfg.portalEmail) headers['X-Portal-Email'] = cfg.portalEmail;
    return fetch(cfg.apiBase + path, { headers: headers, credentials: 'omit' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
  }

  function fmt(n) {
    if (n === null || n === undefined) return '—';
    return Number(n).toLocaleString();
  }

  function deltaHTML(pct) {
    if (pct === null || pct === undefined) {
      return '<span class="delta flat">no prior data</span>';
    }
    var cls = pct > 0 ? 'up' : (pct < 0 ? 'down' : 'flat');
    var arrow = pct > 0 ? '▲' : (pct < 0 ? '▼' : '■');
    return '<span class="delta ' + cls + '">' + arrow + ' ' + Math.abs(pct) + '% vs prev ' + days + 'd</span>';
  }

  // Attract + Engage KPI definitions (label, metric key).
  var ATTRACT = [
    ['Total impressions', 'total_impressions'],
    ['Search results', 'search_result_impressions'],
    ['Detail page views', 'details_page_impressions'],
    ['Media views', 'total_media_views']
  ];
  var ENGAGE = [
    ['Total leads', 'total_leads'],
    ['Phone', 'phone_leads'],
    ['Email', 'email_leads'],
    ['Request to tour', 'request_to_tour_leads']
  ];

  function renderKPIs(containerId, defs, values, deltas) {
    var host = document.getElementById(containerId);
    if (!host) return;
    host.innerHTML = defs.map(function (d) {
      var label = d[0], key = d[1];
      return '<div class="kpi">' +
        '<div class="label">' + label + '</div>' +
        '<div class="num">' + fmt(values[key]) + '</div>' +
        deltaHTML(deltas ? deltas[key] : null) +
        '</div>';
    }).join('');
  }

  function renderSummary(data) {
    var pkg = $('#ils-package');
    if (pkg) pkg.textContent = (data.meta && data.meta.ad_package) || '—';
    var last = $('#ils-lastdate');
    if (last && data.meta && data.meta.last_date) {
      last.textContent = 'through ' + data.meta.last_date;
    }
    var body = $('#ils-body');
    if (body) {
      if (!data.listings) {
        body.innerHTML = '<div class="empty">No apartments.com data for this property yet. ' +
          'Once its CoStar listing is mapped and ingested, performance appears here.</div>';
      } else {
        body.innerHTML = '<div class="meta" style="color:var(--tm)">' +
          fmt(data.listings) + ' listing(s) · last ' + days + ' days</div>';
      }
    }
    renderKPIs('ils-attract', ATTRACT, data.attract || {}, data.delta_pct || {});
    renderKPIs('ils-engage', ENGAGE, data.engage || {}, data.delta_pct || {});
  }

  // ── Minimal dual-line SVG chart (impressions + leads, dual axis) ─────────
  function renderChart(series) {
    var svg = $('#ils-chart');
    if (!svg) return;
    var W = svg.clientWidth || 900, H = 220, pad = 24;
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    if (!series || !series.length) {
      svg.innerHTML = '<text x="' + (W / 2) + '" y="' + (H / 2) +
        '" text-anchor="middle" fill="#a8a29e" font-size="13">No trend data</text>';
      return;
    }
    var n = series.length;
    var maxImp = Math.max(1, Math.max.apply(null, series.map(function (d) { return d.total_impressions || 0; })));
    var maxLead = Math.max(1, Math.max.apply(null, series.map(function (d) { return d.total_leads || 0; })));

    function x(i) { return pad + (W - 2 * pad) * (n === 1 ? 0.5 : i / (n - 1)); }
    function yImp(v) { return H - pad - (H - 2 * pad) * (v / maxImp); }
    function yLead(v) { return H - pad - (H - 2 * pad) * (v / maxLead); }

    function path(accessor, y) {
      return series.map(function (d, i) {
        return (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(accessor(d)).toFixed(1);
      }).join(' ');
    }
    var impPath = path(function (d) { return d.total_impressions || 0; }, yImp);
    var leadPath = path(function (d) { return d.total_leads || 0; }, yLead);

    svg.innerHTML =
      '<path d="' + impPath + '" fill="none" stroke="#f59e0b" stroke-width="2"/>' +
      '<path d="' + leadPath + '" fill="none" stroke="#2563eb" stroke-width="2"/>';
  }

  function loadAll() {
    renderKPIs('ils-attract', ATTRACT, {}, {});
    renderKPIs('ils-engage', ENGAGE, {}, {});
    api('/api/ils/summary?uuid=' + encodeURIComponent(cfg.uuid) + '&days=' + days)
      .then(renderSummary)
      .catch(function (e) {
        var body = $('#ils-body');
        if (body) body.innerHTML = '<div class="empty">Could not load ILS data (' + e.message + ').</div>';
      });
    api('/api/ils/trend?uuid=' + encodeURIComponent(cfg.uuid) + '&days=' + Math.max(days, 90))
      .then(function (d) { renderChart(d.series || []); })
      .catch(function () { renderChart([]); });
  }

  // Range switch
  $$('#ils-range button').forEach(function (b) {
    b.classList.toggle('active', parseInt(b.dataset.days, 10) === days);
    b.addEventListener('click', function () {
      days = parseInt(b.dataset.days, 10) || 30;
      $$('#ils-range button').forEach(function (x) {
        x.classList.toggle('active', x === b);
      });
      var qs = new URLSearchParams(window.location.search);
      qs.set('days', days);
      history.replaceState(null, '', window.location.pathname + '?' + qs.toString());
      loadAll();
    });
  });

  loadAll();
})();
