/* Drive the REAL request-shaping code out of the workspace pages, in node,
   with a minimal DOM.

   Why this exists: whether an API call goes out relative with a cookie or
   absolute with a Bearer token is the whole difference between the Render page
   and the HubSpot page at digital.rpmliving.com/client-portal/v2. Nothing in
   pytest executes that JS, so it could drift in either direction without a
   test going red — and a mistake in the cross-origin direction sends a session
   token somewhere it should not go.

   The code is sliced out of the page on every run, so this cannot pass against
   a stale copy.

   usage: node workspace_api_base_harness.js <mode> <page.html> <scenario-json>
          mode: workspace | report
   prints: one JSON object of observations on stdout. The pytest side asserts.
*/
const fs = require('fs');
const vm = require('vm');

const [MODE, PAGE, SCENARIO] = process.argv.slice(2);
const src = fs.readFileSync(PAGE, 'utf8');
const scenario = JSON.parse(SCENARIO || '{}');

/* ── slicing ─────────────────────────────────────────────────────────────── */
function braceSlice(text, from) {
  const open = text.indexOf('{', from);
  if (open === -1) throw new Error('no block at ' + from);
  let depth = 0;
  for (let i = open; i < text.length; i++) {
    const c = text[i];
    if (c === '{') depth++;
    else if (c === '}') { depth--; if (depth === 0) return text.slice(from, i + 1); }
  }
  throw new Error('unbalanced block at ' + from);
}

function fn(text, name) {
  const at = text.indexOf('function ' + name + '(');
  if (at === -1) throw new Error('function ' + name + ' not found — the page changed shape');
  return braceSlice(text, at);
}

function scriptBlocks(html) {
  const out = [];
  let i = 0;
  for (;;) {
    const a = html.indexOf('<script>', i);
    if (a === -1) break;
    const b = html.indexOf('</script>', a);
    out.push(html.slice(a + '<script>'.length, b));
    i = b + 1;
  }
  return out;
}

const API_BASE_BLOCK = /var API_BASE = \(function \(\) \{[\s\S]*?\n  \}\)\(\);/;

/* ── shared sandbox pieces ───────────────────────────────────────────────── */
function baseSandbox(href) {
  const requests = [];
  const store = {};
  const location = {
    href: href,
    get origin() { return new URL(href).origin; },
    get pathname() { return new URL(href).pathname; },
    get search() { return new URL(href).search; },
    hash: '',
    replace() {},
  };
  const storage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
  };
  const sandbox = {
    console: { warn: () => {}, log: () => {} },
    URL, URLSearchParams, Headers, Promise, JSON, Math, Date, Array, Object, String, Number,
    encodeURIComponent, decodeURIComponent, setTimeout, clearTimeout, setInterval, clearInterval,
    requests,
    sessionStorage: storage,
    localStorage: storage,
    history: { replaceState() {} },
    fetch: function (url, init) {
      requests.push({
        url: String(url),
        method: (init && init.method) || 'GET',
        credentials: init && init.credentials,
        headers: headersToObject(init && init.headers),
      });
      return Promise.resolve({
        ok: true, status: 200,
        text: () => Promise.resolve('{}'),
        json: () => Promise.resolve({}),
      });
    },
  };
  sandbox.window = sandbox;
  sandbox.self = sandbox;
  sandbox.location = location;
  sandbox.window.location = location;
  return sandbox;
}

function headersToObject(h) {
  if (!h) return {};
  if (typeof h.forEach === 'function' && typeof h.get === 'function') {
    const o = {};
    h.forEach((v, k) => { o[k] = v; });
    return o;
  }
  return Object.assign({}, h);
}

/* ── mode: workspace.html ────────────────────────────────────────────────── */
function runWorkspace() {
  const app = scriptBlocks(src).pop();
  const apiBaseBlock = app.match(API_BASE_BLOCK);
  if (!apiBaseBlock) throw new Error('the API_BASE block is gone from workspace.html');

  const code = [
    apiBaseBlock[0],
    fn(app, 'apiUrl'),
    fn(app, 'crossOrigin'),
    fn(app, 'appHere'),
    fn(app, 'reportUrl'),
    fn(app, 'reportHref'),
    fn(app, 'ApiError'),
    fn(app, 'api'),
  ].join('\n');

  const sandbox = baseSandbox(scenario.href || 'https://rpm-portal-server.onrender.com/workspace');
  sandbox.window.__PORTAL_API_BASE__ = scenario.api_base === undefined ? '' : scenario.api_base;
  sandbox.linkToken = scenario.link_token || null;
  sandbox.state = { companyId: scenario.company_id || '123', preview: !!scenario.preview };
  sandbox.signedIn = !!scenario.signed_in;
  sandbox.signInNotes = [];
  sandbox.showSignIn = function (note) { sandbox.signInNotes.push(note || null); };
  sandbox.clerkReady = Promise.resolve(!!scenario.signed_in);
  if (scenario.signed_in) {
    sandbox.window.Clerk = { session: { getToken: () => Promise.resolve('jwt-for-test') } };
  }

  const prelude = 'var out = {};\n';
  const epilogue = `
    out.api_base = API_BASE;
    out.cross_origin = crossOrigin();
    out.report_href = reportHref();
    out.report_url_other = reportUrl('999');
    out.api_url = apiUrl('/api/workspace/me');
    out.rejects_absolute = (function () {
      try { api('https://evil.example/api/workspace/me'); return false; } catch (e) { return true; }
    })();
    api('/api/workspace/work', { method: 'POST', body: { a: 1 } }).then(function () {
      out.requests = requests;
      out.sign_in_notes = signInNotes;
      console_out(JSON.stringify(out));
    });
  `;
  sandbox.console_out = (s) => process.stdout.write(s + '\n');
  vm.runInNewContext(prelude + code + epilogue, sandbox, { filename: 'workspace-slice.js' });
}

/* ── mode: workspace_report.html ─────────────────────────────────────────── */
function runReport() {
  const blocks = scriptBlocks(src);
  const identity = blocks.find((b) => b.indexOf('var APP_ORIGINS') !== -1);
  if (!identity) throw new Error('the identity script is gone from workspace_report.html');

  const sandbox = baseSandbox(scenario.href || 'https://rpm-portal-server.onrender.com/workspace/report?company_id=123');
  sandbox.window.__CLERK_PK__ = scenario.clerk_pk || '';
  sandbox.window.__PORTAL_API_BASE__ = scenario.api_base === undefined ? '' : scenario.api_base;

  const links = (scenario.links || ['/workspace#/dashboard', '/workspace#/properties']).map((href) => ({
    _href: href,
    getAttribute() { return this._href; },
    setAttribute(k, v) { if (k === 'href') this._href = v; },
  }));
  sandbox.document = {
    readyState: 'complete',
    addEventListener() {},
    querySelectorAll(sel) { return sel === 'a[href^="/workspace#"]' ? links.filter((l) => l._href.indexOf('/workspace#') === 0) : []; },
    getElementById() { return null; },
    createElement() { return { setAttribute() {}, appendChild() {}, style: {}, head: {} }; },
    head: { appendChild() {} },
    body: { appendChild() {} },
  };

  const out = {};
  sandbox.console_out = (s) => process.stdout.write(s + '\n');
  vm.runInNewContext(identity, sandbox, { filename: 'report-identity.js' });

  out.api_base = sandbox.window.__PORTAL_API_BASE__;
  out.app_base = sandbox.window.__PORTAL_APP_BASE__;
  out.api_url = sandbox.window.portalApiUrl('/api/workspace/report?company_id=123');
  out.app_url = sandbox.window.portalAppUrl('#/properties');
  out.links = links.map((l) => l._href);

  sandbox.window.fetch(out.api_url).then(function () {
    out.requests = sandbox.requests;
    process.stdout.write(JSON.stringify(out) + '\n');
  });
}

if (MODE === 'workspace') runWorkspace();
else if (MODE === 'report') runReport();
else { process.stderr.write('unknown mode: ' + MODE + '\n'); process.exit(2); }
