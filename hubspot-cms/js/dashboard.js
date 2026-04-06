/* ══════════════════════════════════════════
   RPM Living Portfolio Dashboard — Client JS
   HubSpot Membership auth — no custom login.
   User identity injected by HubL via request.contact.
   ══════════════════════════════════════════ */

(function () {
  'use strict';

  // ── Configuration (set by HubL in the template) ──
  var API_BASE = window.__PORTAL_API_BASE || '';
  var PORTAL_EMAIL = window.__PORTAL_EMAIL || '';
  var PORTAL_ROLE = window.__PORTAL_ROLE || 'marketing_manager';

  // ── State ──
  var portfolioData = null;
  var currentSort = { key: 'name', dir: 'asc' };

  // ══════════════════════════════════════════
  //  DASHBOARD
  // ══════════════════════════════════════════

  window.loadPortfolio = function () {
    if (!PORTAL_EMAIL) {
      show('dashboard-error');
      var msgEl = document.getElementById('dashboard-error-msg');
      if (msgEl) msgEl.textContent = 'Unable to identify your account. Please refresh the page.';
      return;
    }

    // Show user email in header
    var emailEl = document.getElementById('user-email');
    if (emailEl) emailEl.textContent = PORTAL_EMAIL;

    // Show role badge
    var roleEl = document.getElementById('user-role');
    if (roleEl) roleEl.textContent = formatRole(PORTAL_ROLE);

    // Show loading
    show('dashboard-loading');
    hide('dashboard-content');
    hide('dashboard-error');

    fetch(API_BASE + '/api/portfolio?role=' + encodeURIComponent(PORTAL_ROLE), {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
        'X-Portal-Email': PORTAL_EMAIL
      }
    })
      .then(function (r) {
        if (!r.ok) throw new Error('failed');
        return r.json();
      })
      .then(function (data) {
        portfolioData = data;
        renderDashboard(data);
        hide('dashboard-loading');
        show('dashboard-content');
      })
      .catch(function () {
        hide('dashboard-loading');
        show('dashboard-error');
        var msgEl = document.getElementById('dashboard-error-msg');
        if (msgEl) msgEl.textContent = 'Unable to load your portfolio. Please try again.';
      });
  };

  function renderDashboard(data) {
    renderKPIs(data.rollups);
    renderHealthDistribution(data.rollups);
    renderPropertyTable(data.properties);
    populateMarketFilter(data.rollups.market_breakdown);
  }

  // ── KPI Cards ──
  function renderKPIs(rollups) {
    setText('kpi-properties', rollups.total_properties.toLocaleString());
    setText('kpi-units', rollups.total_units.toLocaleString());
    setText('kpi-health', rollups.avg_health_score != null ? rollups.avg_health_score : '—');
    setText('kpi-spend', '$' + Math.round(rollups.total_monthly_spend).toLocaleString());
    setText('kpi-flags', rollups.total_flags.toLocaleString());
  }

  // ── Health Distribution ──
  function renderHealthDistribution(rollups) {
    var dist = rollups.health_distribution;
    var total = dist.healthy + dist.warning + dist.critical + dist.no_data;
    if (total === 0) {
      hide('health-distribution-card');
      return;
    }

    var bar = document.getElementById('health-bar');
    bar.innerHTML = '';

    var segments = [
      { key: 'healthy', label: 'Healthy (80+)', count: dist.healthy },
      { key: 'warning', label: 'Warning (60-79)', count: dist.warning },
      { key: 'critical', label: 'Critical (<60)', count: dist.critical },
      { key: 'no-data', label: 'No Data', count: dist.no_data }
    ];

    segments.forEach(function (seg) {
      if (seg.count === 0) return;
      var el = document.createElement('div');
      el.className = 'health-segment ' + seg.key;
      el.style.width = ((seg.count / total) * 100).toFixed(1) + '%';
      el.title = seg.label + ': ' + seg.count;
      bar.appendChild(el);
    });

    var legend = document.getElementById('health-legend');
    legend.innerHTML = '';
    segments.forEach(function (seg) {
      if (seg.count === 0) return;
      var item = document.createElement('span');
      item.className = 'legend-item';
      item.innerHTML = '<span class="legend-dot ' + seg.key + '"></span>' +
        seg.label + ' (' + seg.count + ')';
      legend.appendChild(item);
    });
  }

  // ── Market Filter ──
  function populateMarketFilter(marketBreakdown) {
    var select = document.getElementById('market-filter');
    if (!select) return;
    select.innerHTML = '<option value="">All Markets</option>';
    var markets = Object.keys(marketBreakdown).sort();
    markets.forEach(function (m) {
      var opt = document.createElement('option');
      opt.value = m;
      opt.textContent = m + ' (' + marketBreakdown[m] + ')';
      select.appendChild(opt);
    });
  }

  // ── Property Table ──
  function renderPropertyTable(properties) {
    var filtered = filterProperties(properties);
    var sorted = sortProperties(filtered);

    var tbody = document.getElementById('property-tbody');
    tbody.innerHTML = '';

    sorted.forEach(function (p) {
      var tr = document.createElement('tr');
      tr.onclick = function () { navigateToProperty(p.uuid); };

      var healthClass = getHealthClass(p.health_score);
      var healthText = p.health_score != null ? p.health_score : '—';
      var flagClass = p.flags > 0 ? 'has-flags' : 'no-flags';

      var location = [p.city, p.state].filter(Boolean).join(', ');

      tr.innerHTML =
        '<td class="property-name-cell">' + escapeHtml(p.name) +
          (location ? '<span class="property-location">' + escapeHtml(location) + '</span>' : '') +
        '</td>' +
        '<td>' + escapeHtml(p.market || '—') + '</td>' +
        '<td class="text-right">' + (p.units || '—') + '</td>' +
        '<td class="text-right"><span class="health-dot ' + healthClass + '"></span>' + healthText + '</td>' +
        '<td class="text-right">$' + Math.round(p.monthly_spend).toLocaleString() + '</td>' +
        '<td class="text-right"><span class="flag-count ' + flagClass + '">' + p.flags + '</span></td>';

      tbody.appendChild(tr);
    });

    // Update count
    var countEl = document.getElementById('table-count');
    if (countEl) {
      countEl.textContent = sorted.length + ' of ' + properties.length + ' properties';
    }

    updateSortIcons();
  }

  // ── Sorting ──
  window.applySort = function (key) {
    if (currentSort.key === key) {
      currentSort.dir = currentSort.dir === 'asc' ? 'desc' : 'asc';
    } else {
      currentSort.key = key;
      currentSort.dir = 'asc';
    }
    if (portfolioData) renderPropertyTable(portfolioData.properties);
  };

  function sortProperties(properties) {
    var key = currentSort.key;
    var dir = currentSort.dir === 'asc' ? 1 : -1;

    return properties.slice().sort(function (a, b) {
      var va = a[key];
      var vb = b[key];

      if (va == null) va = key === 'name' || key === 'market' ? '' : -1;
      if (vb == null) vb = key === 'name' || key === 'market' ? '' : -1;

      if (typeof va === 'string') {
        return dir * va.localeCompare(vb);
      }
      return dir * (va - vb);
    });
  }

  function updateSortIcons() {
    document.querySelectorAll('.sort-icon').forEach(function (icon) {
      icon.className = 'sort-icon';
    });
    var activeHeader = document.querySelector('th[data-sort="' + currentSort.key + '"] .sort-icon');
    if (activeHeader) {
      activeHeader.className = 'sort-icon ' + currentSort.dir;
    }
  }

  // ── Filtering ──
  window.applyFilters = function () {
    if (portfolioData) renderPropertyTable(portfolioData.properties);
  };

  function filterProperties(properties) {
    var search = (document.getElementById('search-input') || {}).value || '';
    search = search.toLowerCase().trim();

    var market = (document.getElementById('market-filter') || {}).value || '';
    var health = (document.getElementById('health-filter') || {}).value || '';

    return properties.filter(function (p) {
      // Search
      if (search) {
        var haystack = (p.name + ' ' + p.market + ' ' + p.city + ' ' + p.state + ' ' + p.address).toLowerCase();
        if (haystack.indexOf(search) === -1) return false;
      }
      // Market
      if (market && p.market !== market) return false;
      // Health
      if (health) {
        var hc = getHealthClass(p.health_score);
        if (hc.replace('-', '_') !== health && hc !== health) return false;
      }
      return true;
    });
  }

  // ── Navigation ──
  window.navigateToProperty = function (uuid) {
    if (!uuid) return;
    window.location.href = window.location.pathname + '?uuid=' + encodeURIComponent(uuid);
  };

  // ── Helpers ──
  function getHealthClass(score) {
    if (score == null) return 'no-data';
    if (score >= 80) return 'healthy';
    if (score >= 60) return 'warning';
    return 'critical';
  }

  function formatRole(role) {
    var labels = {
      'marketing_manager': 'Marketing Manager',
      'marketing_director': 'Marketing Director',
      'marketing_rvp': 'Marketing RVP'
    };
    return labels[role] || role;
  }

  function setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function show(id) {
    var el = document.getElementById(id);
    if (el) el.style.display = '';
  }

  function hide(id) {
    var el = document.getElementById(id);
    if (el) el.style.display = 'none';
  }

  function escapeHtml(str) {
    if (!str) return '';
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ── Init ──
  document.addEventListener('DOMContentLoaded', function () {
    if (document.getElementById('dashboard-main')) {
      window.loadPortfolio();
    }
  });

})();
