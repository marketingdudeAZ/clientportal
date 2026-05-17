/*
 * RPM Living Client Portal — Loop subpage (ADR 0018)
 *
 * Orchestrates the 4-stage Multifamily Loop view:
 *  - Loads Loop Status, Forecast, Timeline, Channels in parallel
 *  - Wires tab switching + deep-link via ?tab=
 *  - Handles approve / reject / counter-propose actions
 *  - Wires Execute pane buttons
 *
 * Vanilla JS; no framework. Talks to /api/loop/* on the Render Flask service.
 */

(function () {
  'use strict';

  var cfg = window.RPM_LOOP_CONFIG || {};
  if (!cfg.uuid) {
    console.error('RPM_LOOP_CONFIG.uuid missing — cannot init Loop view');
    return;
  }

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  // ── HTTP helper ─────────────────────────────────────────────────────────
  function api(path, opts) {
    opts = opts || {};
    var url = cfg.apiBase + path;
    var headers = opts.headers || {};
    if (cfg.portalEmail) headers['X-Portal-Email'] = cfg.portalEmail;
    if (opts.method && opts.method !== 'GET') headers['Content-Type'] = 'application/json';
    return fetch(url, {
      method:  opts.method || 'GET',
      headers: headers,
      body:    opts.body ? JSON.stringify(opts.body) : undefined,
      credentials: 'omit'
    }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  // ── Tabs ────────────────────────────────────────────────────────────────
  function showTab(name) {
    $$('.tab').forEach(function (t) {
      t.classList.toggle('active', t.dataset.tab === name);
    });
    $$('.pane').forEach(function (p) {
      p.classList.toggle('active', p.dataset.pane === name);
    });
    // Update URL without reload
    var qs = new URLSearchParams(window.location.search);
    qs.set('tab', name);
    history.replaceState(null, '', window.location.pathname + '?' + qs.toString());
  }

  // ── Loop Status (4-stage grid) ──────────────────────────────────────────
  function loadStatus() {
    return api('/api/loop/status?uuid=' + encodeURIComponent(cfg.uuid))
      .then(function (data) {
        Object.keys(data.stages || {}).forEach(function (stage) {
          var info = data.stages[stage] || {};
          var dot = $('[data-health-dot="' + stage + '"]');
          if (dot) {
            dot.className = 'health-dot ' + (info.health || 'no_data');
          }
          var summary = $('[data-stage-summary="' + stage + '"]');
          if (summary) {
            if (info.last_event_type) {
              summary.innerHTML =
                '<code>' + info.last_event_type + '</code>' +
                '<span class="last-time">' + fmtRelative(info.last_at) + '</span>';
            } else {
              summary.innerHTML = '<span style="color:var(--tl);font-size:12px">No activity yet</span>';
            }
          }
        });
        $('#last-updated').textContent = 'Updated ' + fmtRelative(new Date().toISOString());
      })
      .catch(function (err) {
        console.warn('Status fetch failed:', err);
      });
  }

  // ── Forecast + Channels + Recommendations (Plan tab) ────────────────────
  function loadForecast() {
    return api('/api/loop/forecast?uuid=' + encodeURIComponent(cfg.uuid))
      .then(function (data) {
        var f = data.forecast;
        if (!f) {
          $('#forecast-empty').style.display = '';
          $('#forecast-body').style.display = 'none';
          $('#allocation-empty').style.display = '';
          $('#allocation-table').style.display = 'none';
          return null;
        }
        $('#forecast-empty').style.display = 'none';
        $('#forecast-body').style.display = '';
        $('#forecast-num').textContent = (f.forecast_leases != null ? f.forecast_leases : '—');
        $('#forecast-range').textContent = 'Range ' + (f.ci_low || 0) + '–' + (f.ci_high || 0) + ' leases (' + Math.round((f.confidence_level || 0.5) * 100) + '% confidence)';
        $('#forecast-method').textContent = f.methodology || 'unknown';
        $('#forecast-conf').textContent = Math.round((f.confidence_level || 0) * 100) + '%';
        $('#forecast-months').textContent = (f.inputs_payload || f.inputs || {}).data_months || '—';
        $('#forecast-when').textContent = fmtRelative(f.run_at);

        // Channel allocation table
        var alloc = f.channel_allocation || {};
        var rows = [];
        Object.keys(alloc).forEach(function (c) {
          var x = alloc[c] || {};
          if ((x.spend || 0) <= 0) return;
          rows.push(
            '<tr>' +
              '<td class="chan-cell-name">' + prettyChannel(c) + '</td>' +
              '<td class="chan-cell-num">$' + Math.round(x.spend || 0).toLocaleString() + '</td>' +
              '<td class="chan-cell-num">$' + (x.cpl ? Math.round(x.cpl) : '—') + '</td>' +
              '<td class="chan-cell-num">' + (x.forecast_leases || 0).toFixed(1) + '</td>' +
            '</tr>'
          );
        });
        if (rows.length) {
          $('#allocation-tbody').innerHTML = rows.join('');
          $('#allocation-table').style.display = '';
          $('#allocation-empty').style.display = 'none';
        } else {
          $('#allocation-empty').style.display = '';
          $('#allocation-table').style.display = 'none';
        }
        return f;
      })
      .catch(function (err) {
        console.warn('Forecast fetch failed:', err);
        return null;
      });
  }

  function loadRecommendations() {
    return api('/api/loop/recommendations?uuid=' + encodeURIComponent(cfg.uuid))
      .then(function (data) {
        var recs = data.recommendations || [];
        $('#rec-count').textContent = recs.length ? '(' + recs.length + ')' : '';
        if (!recs.length) {
          $('#recs-empty').style.display = '';
          $('#recs-list').innerHTML = '';
          return;
        }
        $('#recs-empty').style.display = 'none';
        $('#recs-list').innerHTML = recs.map(function (r, i) {
          var action = r.action || 'recommend';
          var title  = humanizeAction(r);
          var impact = (r.forecast_impact != null) ?
            '<span class="rec-chip impact">+' + r.forecast_impact + ' leases (30d)</span>' : '';
          return (
            '<div class="rec" data-rec-idx="' + i + '">' +
              '<h4>' + title + '</h4>' +
              '<p>' + (r.reason || '') + '</p>' +
              '<div class="rec-meta">' +
                '<span class="rec-chip">' + action + '</span>' +
                impact +
              '</div>' +
              '<div class="rec-actions">' +
                '<button class="btn primary" data-rec-action="approve" data-idx="' + i + '">Approve &amp; Apply</button>' +
                '<button class="btn" data-rec-action="reject" data-idx="' + i + '">Defer</button>' +
              '</div>' +
            '</div>'
          );
        }).join('');

        // Wire up approve/reject buttons
        $$('#recs-list [data-rec-action]').forEach(function (btn) {
          btn.addEventListener('click', function () {
            var idx = parseInt(btn.dataset.idx, 10);
            var rec = recs[idx];
            var act = btn.dataset.recAction;
            decideRecommendation(act, rec, data.forecast_id, btn);
          });
        });

        // Wire up loop mode hint
        var modeKey = (cfg.loopMode || 'co-pilot').toLowerCase();
        var modeNames = {
          'auto-pilot': 'Auto-pilot mode:',
          'co-pilot':   'Co-pilot mode:',
          'custom':     'Custom mode:'
        };
        var modeDescs = {
          'auto-pilot': 'The Loop applies bounded recommendations automatically. You review the weekly digest.',
          'co-pilot':   'Recommendations require your approval before the Loop applies them.',
          'custom':     'Your AM crafts the plan; the Loop executes against it and reports against your goals.'
        };
        $('#mode-label').textContent = modeNames[modeKey] || 'Co-pilot mode:';
        $('#mode-desc').textContent = modeDescs[modeKey] || modeDescs['co-pilot'];
      })
      .catch(function (err) {
        console.warn('Recommendations fetch failed:', err);
      });
  }

  function decideRecommendation(action, rec, forecastId, btn) {
    btn.disabled = true;
    btn.textContent = (action === 'approve') ? 'Submitting…' : 'Deferring…';
    var endpoint = action === 'approve' ? '/api/loop/approve' : '/api/loop/reject';
    api(endpoint, {
      method: 'POST',
      body: {
        uuid:           cfg.uuid,
        recommendation: rec,
        forecast_id:    forecastId
      }
    }).then(function () {
      btn.textContent = (action === 'approve') ? '✓ Approved' : '✓ Deferred';
      showActionStatus('Recommendation ' + action + 'd',
                       'Loop event recorded. The next Optimize run will incorporate your decision.');
    }).catch(function () {
      btn.disabled = false;
      btn.textContent = (action === 'approve') ? 'Approve & Apply' : 'Defer';
      showActionStatus('Failed', 'Could not submit decision — please try again.', true);
    });
  }

  // ── Channels (Hyly attribution, shown in Status pane) ───────────────────
  function loadChannels() {
    if (!cfg.companyId) return Promise.resolve();
    return api('/api/loop/channels?company_id=' + encodeURIComponent(cfg.companyId) + '&days=30')
      .then(function (data) {
        $('#channels-card').style.display = '';
        var channels = data.channels || {};
        var rows = Object.keys(channels)
          .filter(function (c) { return c !== '_total' && (channels[c].visitors || 0) > 0; })
          .sort(function (a, b) { return (channels[b].visitors || 0) - (channels[a].visitors || 0); })
          .map(function (c) {
            var x = channels[c];
            var conv = x.conv_rate != null ? (x.conv_rate * 100).toFixed(1) + '%' : '—';
            return (
              '<tr>' +
                '<td class="chan-cell-name">' + c + '</td>' +
                '<td class="chan-cell-num">' + (x.visitors || 0).toLocaleString() + '</td>' +
                '<td class="chan-cell-num">' + (x.known_visitors || 0).toLocaleString() + '</td>' +
                '<td class="chan-cell-num">' + (x.contacts || 0).toLocaleString() + '</td>' +
                '<td class="chan-cell-num">' + conv + '</td>' +
              '</tr>'
            );
          });
        if (rows.length) {
          $('#channels-tbody').innerHTML = rows.join('');
          $('#channels-table').style.display = '';
          $('#channels-empty').style.display = 'none';
        } else {
          $('#channels-empty').style.display = '';
          $('#channels-table').style.display = 'none';
        }
      })
      .catch(function () {
        $('#channels-card').style.display = 'none';
      });
  }

  // ── Timeline ────────────────────────────────────────────────────────────
  function loadTimeline(stageFilter) {
    var qs = '?uuid=' + encodeURIComponent(cfg.uuid) + '&limit=100';
    if (stageFilter) qs += '&stage=' + encodeURIComponent(stageFilter);
    return api('/api/loop/events' + qs)
      .then(function (data) {
        var events = data.events || [];
        if (!events.length) {
          $('#timeline-empty').style.display = '';
          $('#timeline-body').style.display = 'none';
          return;
        }
        $('#timeline-empty').style.display = 'none';
        $('#timeline-body').style.display = '';
        $('#timeline-body').innerHTML = events.map(function (e) {
          var msg = '<code>' + (e.event_type || '?') + '</code>';
          if (e.payload && typeof e.payload === 'object') {
            var preview = summarizePayload(e.payload);
            if (preview) msg += ' — ' + preview;
          }
          if (e.status === 'failed') {
            msg += ' <span style="color:var(--r);font-weight:600">failed</span>';
            if (e.error_message) msg += ': <span style="color:var(--tm)">' + escapeHtml(e.error_message) + '</span>';
          }
          return (
            '<div class="timeline-row">' +
              '<div class="timeline-time">' + fmtRelative(e.occurred_at) + '</div>' +
              '<div><span class="timeline-stage ' + (e.stage || 'ops') + '">' + (e.stage || 'ops') + '</span></div>' +
              '<div class="timeline-msg">' + msg + '</div>' +
            '</div>'
          );
        }).join('');
      })
      .catch(function (err) {
        console.warn('Timeline fetch failed:', err);
      });
  }

  // ── Execute actions ─────────────────────────────────────────────────────
  function runAction(action, btn) {
    var orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Working…';
    var done = function (title, body, isError) {
      btn.disabled = false;
      btn.textContent = orig;
      showActionStatus(title, body, isError);
    };

    if (action === 'run-forecast') {
      api('/api/loop/forecast/run', {
        method: 'POST',
        // Note: this endpoint requires X-Internal-Key in production.
        // For client-side use we may need to add a client-allowed
        // /api/loop/forecast/run-client endpoint that wraps the same call.
        body: { company_id: cfg.companyId, seo_tier: cfg.seoTier }
      }).then(function (data) {
        done('Forecast complete', 'Refreshing the Plan tab…');
        Promise.all([loadForecast(), loadRecommendations()]);
      }).catch(function () {
        done('Forecast unavailable',
             'Run forecast requires internal credentials. Ask your AM to trigger this from the admin shell, or wait for the daily auto-run.',
             true);
      });
      return;
    }

    if (action === 'open-hyly-crm') {
      if (!cfg.hylyPropertyId) {
        done('Hyly not configured', 'This property has no Hyly Property ID yet (Hyly beta hasn\'t reached it).', true);
        return;
      }
      window.open('https://my.hy.ly/crm/' + cfg.hylyPropertyId, '_blank');
      done('Opened Hyly CRM', 'Opens in a new tab with the property\'s lead pipeline.');
      return;
    }

    if (action === 'talk-to-am') {
      window.location.href = '/portal-dashboard?uuid=' + cfg.uuid + '#ticket=new&context=loop';
      return;
    }

    if (action === 'change-mode') {
      done('Mode change',
           'Loop mode is set by your Account Manager. Open a ticket via "Talk to my AM" to request a change.');
      return;
    }

    // For other actions, route to the corresponding existing admin endpoint
    // via the talk-to-am ticket flow (we don't expose admin endpoints to the
    // client side directly).
    done(humanizeAction({ action: action }) + ' queued',
         'This action would be queued through your AM in production. Stub in place; full wiring is part of the next milestone.');
  }

  // ── Helpers ─────────────────────────────────────────────────────────────
  function fmtRelative(iso) {
    if (!iso) return '';
    var dt = new Date(iso);
    if (isNaN(dt.getTime())) return '';
    var diff = (Date.now() - dt.getTime()) / 1000;
    if (diff < 60) return 'just now';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    if (diff < 86400 * 30) return Math.floor(diff / 86400) + 'd ago';
    return dt.toLocaleDateString();
  }

  function prettyChannel(key) {
    return {
      paid_search:    'Paid Search',
      paid_social:    'Paid Social',
      seo:            'SEO',
      reputation:     'Reputation',
      creative:       'Creative'
    }[key] || key;
  }

  function humanizeAction(rec) {
    if (!rec || !rec.action) return 'Recommendation';
    switch (rec.action) {
      case 'shift_budget':
        return 'Shift $' + (rec.amount || 0) + ' from ' + prettyChannel(rec.from_channel) +
               ' → ' + prettyChannel(rec.to_channel);
      case 'regenerate-marquee':
        return 'Regenerate Marquee paid creative';
      case 'refresh-seo':
        return 'Refresh SEO content';
      case 'seo-content-batch':
        return 'Generate SEO page batch';
      case 'generate-aeo-batch':
        return 'Generate AEO Q&A batch';
      case 'refresh-community-brief':
        return 'Refresh Community Brief';
      case 'refresh-property-brief':
        return 'Refresh Property Brief';
      case 'refresh-aptiq':
        return 'Refresh AptIQ snapshot';
      case 'backfill-hyly':
        return 'Backfill Hyly history (180 days)';
      default:
        return rec.action.replace(/_/g, ' ').replace(/-/g, ' ');
    }
  }

  function summarizePayload(p) {
    if (!p) return '';
    if (typeof p === 'string') return '';
    var keys = Object.keys(p).filter(function (k) { return !k.startsWith('_'); }).slice(0, 3);
    if (!keys.length) return '';
    return keys.map(function (k) {
      var v = p[k];
      if (typeof v === 'object') v = '…';
      else if (typeof v === 'string' && v.length > 30) v = v.slice(0, 30) + '…';
      return '<span style="color:var(--tm);font-size:12px">' + k + '=' + v + '</span>';
    }).join(' ');
  }

  function escapeHtml(s) {
    if (!s) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
  }

  function showActionStatus(title, body, isError) {
    var card = $('#action-status');
    card.style.display = '';
    card.style.background = isError ? '#fef2f2' : '#f0f9ff';
    card.style.borderColor = isError ? '#fecaca' : '#bae6fd';
    $('#action-status-title').textContent = title;
    $('#action-status-body').textContent = body;
  }

  // ── Init ────────────────────────────────────────────────────────────────
  function init() {
    // Tab wiring
    $$('.tab').forEach(function (t) {
      t.addEventListener('click', function () { showTab(t.dataset.tab); });
    });
    showTab(cfg.defaultTab || 'status');

    // Stage cards click-through → timeline filter
    $$('.stage').forEach(function (s) {
      s.addEventListener('click', function () {
        var stage = s.dataset.stage;
        var sel = $('#timeline-filter');
        if (sel) sel.value = stage;
        loadTimeline(stage);
        showTab('timeline');
      });
    });

    // Timeline filter
    $('#timeline-filter').addEventListener('change', function (e) {
      loadTimeline(e.target.value || null);
    });

    // Execute pane buttons
    $$('[data-action]').forEach(function (btn) {
      btn.addEventListener('click', function () { runAction(btn.dataset.action, btn); });
    });

    // Initial loads — all in parallel
    Promise.all([loadStatus(), loadForecast(), loadRecommendations(), loadTimeline(), loadChannels()]);

    // Auto-refresh Status every 60s
    setInterval(loadStatus, 60000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
