/* Drive the portal's Ask JS against a REAL API payload, in node, with a
   minimal DOM. Proves the client path end to end before anything is deployed
   into a template whose CDN cache holds for ~10 hours. */
const fs = require('fs');
// argv: <extracted-ask-js> <answer-fixture> <manifest-fixture>
const [ASK_JS, ANSWER_JSON, MANIFEST_JSON] = process.argv.slice(2);

const answer   = JSON.parse(fs.readFileSync(ANSWER_JSON, 'utf8'));
const manifest = JSON.parse(fs.readFileSync(MANIFEST_JSON, 'utf8'));

// ── minimal DOM ──────────────────────────────────────────────────────────
const nodes = {};
function mk(id) {
  return nodes[id] = {
    id, innerHTML: '', textContent: '', style: {}, className: '',
    _attrs: {},
    setAttribute(k, v) { this._attrs[k] = v; },
    removeAttribute(k) { delete this._attrs[k]; },
    getAttribute(k) { return this._attrs[k]; },
  };
}
['ask-chips','ask-idle','ask-pending','ask-error','ask-answer','ask-answer-q',
 'ask-headline','ask-summary','ask-findings','ask-nextstep','ask-gaps',
 'ask-notevidenced','ask-provenance','ask-pending-title','ask-refresh'].forEach(mk);

global.document = {
  getElementById: (id) => nodes[id] || null,
  querySelectorAll: () => [],
};
global.window = {
  __PORTAL_COMPANY_ID__: '30912193455',
  __PORTAL_EMAIL__: 'kyle.shipp@rpmliving.com',
  __PORTAL_API_BASE: 'https://example.invalid',
};
global.setTimeout = (fn) => 0;
global.clearTimeout = () => {};

// ── stub fetch: manifest, then the answer ────────────────────────────────
global.fetch = (url, opts) => {
  if (url.indexOf('/api/ask/questions') !== -1) {
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(manifest) });
  }
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(answer) });
};

// ── load the extracted portal code ───────────────────────────────────────
eval(fs.readFileSync(ASK_JS, 'utf8'));

// ── exercise it ──────────────────────────────────────────────────────────
const fail = [];
function check(name, cond, extra) {
  if (cond) { console.log('  PASS  ' + name); }
  else      { console.log('  FAIL  ' + name + (extra ? '  <- ' + extra : '')); fail.push(name); }
}

window.loadAsk();

setImmediate(() => {
  const chips = nodes['ask-chips'].innerHTML;
  console.log('\n--- chips ---');
  check('renders one chip per registry question',
        (chips.match(/ask-chip/g) || []).length === manifest.questions.length,
        (chips.match(/ask-chip/g) || []).length + ' vs ' + manifest.questions.length);
  check('chip carries the question label', chips.indexOf(manifest.questions[0].label.replace(/'/g, '&#39;')) !== -1);
  check('chip has the blurb as a tooltip', chips.indexOf('title="') !== -1);
  check('no unescaped apostrophe breaks the markup', chips.indexOf("What's") === -1);

  window.askRun('whats_not_working');

  setImmediate(() => {
    console.log('\n--- answer ---');
    check('headline rendered', nodes['ask-headline'].textContent === answer.headline);
    check('summary rendered',  nodes['ask-summary'].textContent === answer.summary);

    const f = nodes['ask-findings'].innerHTML;
    check('every finding rendered',
          (f.match(/ask-finding"/g) || []).length === answer.findings.length,
          (f.match(/ask-finding"/g) || []).length + ' vs ' + answer.findings.length);

    const evCount = answer.findings.reduce((n, x) => n + ((x.evidence || []).length), 0);
    check('every evidence line rendered (' + evCount + ')',
          (f.match(/ask-evidence-item/g) || []).length === evCount,
          (f.match(/ask-evidence-item/g) || []).length + ' vs ' + evCount);

    const firstEv = (answer.findings[0].evidence || [])[0] || '';
    const num = firstEv.match(/[\d,]+/);
    check('a real number survives into the DOM' + (num ? ' (' + num[0] + ')' : ''),
          !num || f.indexOf(num[0]) !== -1);

    const gaps = nodes['ask-gaps'].innerHTML;
    check('missing inputs are shown, not swallowed (' + answer.missing_inputs.length + ')',
          (gaps.match(/ask-gap"/g) || []).length >= answer.missing_inputs.length,
          (gaps.match(/ask-gap"/g) || []).length);
    check('a dark source names its reason',
          gaps.indexOf('SOCi') !== -1 || gaps.indexOf('Google Ads') !== -1 || gaps.indexOf('Hyly') !== -1);

    // The fixture is a real captured response, so whether it carries a
    // next_step or a model narrative depends on what production returned that
    // day. Assert what the payload actually contains rather than assuming the
    // richer shape — a harness that only passes on a good day is not a test.
    if (answer.next_step) {
      check('next_step rendered', nodes['ask-nextstep'].innerHTML.indexOf('What to do next') !== -1);
    } else {
      check('no next_step in fixture -> nothing rendered',
            nodes['ask-nextstep'].innerHTML === '');
    }
    check('provenance mentions generation time',
          nodes['ask-provenance'].innerHTML.length > 0);
    const saysFallback = nodes['ask-provenance'].innerHTML
                           .indexOf('narrative model was unavailable') !== -1;
    if (answer.narrator === 'rules') {
      check('a rules-narrated answer SAYS so rather than passing as analysis',
            saysFallback);
    } else {
      check('a model-narrated answer does not claim the fallback narrator',
            !saysFallback);
    }

    console.log('\n--- panel state ---');
    check('answer panel visible',  nodes['ask-answer'].style.display === '');
    check('pending panel hidden',  nodes['ask-pending'].style.display === 'none');
    check('idle panel hidden',     nodes['ask-idle'].style.display === 'none');
    check('error panel hidden',    nodes['ask-error'].style.display === 'none');

    console.log('\n--- XSS ---');
    const nasty = JSON.parse(JSON.stringify(answer));
    nasty.headline = 'safe';
    nasty.findings = [{ title: '<img src=x onerror=alert(1)>', detail: 'x', evidence: ['<script>bad()</script>'] }];
    global.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(nasty) });
    window.askRun('whats_working');
    setImmediate(() => {
      const h = nodes['ask-findings'].innerHTML;
      check('model output is escaped, not injected',
            h.indexOf('<img src=x') === -1 && h.indexOf('<script>bad') === -1);
      check('escaped form is present', h.indexOf('&lt;img') !== -1);

      console.log('\n' + (fail.length ? 'FAILURES: ' + fail.length + ' — ' + fail.join('; ')
                                      : 'ALL CHECKS PASSED'));
      process.exit(fail.length ? 1 : 0);
    });
  });
});
