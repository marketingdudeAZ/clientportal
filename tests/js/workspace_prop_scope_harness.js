/* Drive the REAL Properties-scope code out of workspace.html, in node.

   Why this exists: which scope the Properties screen asks for decides whether
   a director sees one test property or the whole portfolio, and whether a
   CLIENT is ever offered a control that would show them properties that are
   not theirs. Neither is visible to pytest, which only ever sees the page as a
   string — the switch could be rendered for clients and nothing would go red.

   The functions are sliced out of the page on every run, so this cannot pass
   against a stale copy.

   usage: node workspace_prop_scope_harness.js <page.html> <scenario-json>
   prints: one JSON object of observations on stdout. The pytest side asserts.
*/
const fs = require('fs');
const vm = require('vm');

const [PAGE, SCENARIO] = process.argv.slice(2);
const src = fs.readFileSync(PAGE, 'utf8');
const scenario = JSON.parse(SCENARIO || '{}');

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

/* The state line the screen depends on, taken from the page rather than
   re-declared here, so a change to its defaults shows up in these results. */
const STATE_LINE = /state\.props = \{[^}]*\};/;
const stateInit = src.match(STATE_LINE);
if (!stateInit) throw new Error('state.props initialiser not found — the page changed shape');

const sandbox = {
  state: { me: { role: scenario.role || 'client', companies: scenario.companies || [] } },
  realInternal: function () { return sandbox.state.me.role === 'internal'; },
  esc: function (s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  },
  out: {},
};
vm.createContext(sandbox);
vm.runInContext(stateInit[0], sandbox);
vm.runInContext(fn(src, 'propScope'), sandbox);
vm.runInContext(fn(src, 'scopeSwitch'), sandbox);

/* An explicit preference, when the scenario sets one, the way a click does. */
if (scenario.chosen) {
  vm.runInContext('state.props.scope = ' + JSON.stringify(scenario.chosen) + ';', sandbox);
}

const result = vm.runInContext(`(function () {
  var scope = propScope();
  var mineHtml = scopeSwitch('mine', ${JSON.stringify(scenario.note || '')});
  var allHtml = scopeSwitch('all');
  return {
    scope: scope,
    default_scope_field: state.props.scope,
    page: state.props.page,
    switch_rendered: allHtml.length > 0,
    all_pressed_on_all_view: /data-prop-scope="all" aria-pressed="true"/.test(allHtml),
    mine_pressed_on_mine_view: /data-prop-scope="mine" aria-pressed="true"/.test(mineHtml),
    only_one_pressed: (allHtml.match(/aria-pressed="true"/g) || []).length,
    note_rendered: /class="support"/.test(mineHtml),
    html_sample: allHtml,
  };
})()`, sandbox);

process.stdout.write(JSON.stringify(result) + '\n');
