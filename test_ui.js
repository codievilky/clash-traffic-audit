// Regression: a slow response for an old filter must not overwrite the latest view.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function element() {
  return { value: '', children: [], handlers: {}, textContent: '',
    addEventListener(name, fn) { this.handlers[name] = fn; },
    append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; },
    setAttribute() {} };
}
const elements = new Map();
const get = id => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); };
get('period').value = 'today'; get('kind').value = 'domain'; get('route').value = 'proxy';
const pending = [];
vm.runInNewContext(fs.readFileSync(__dirname + '/app.js', 'utf8'), {
  document: { getElementById: get, createElement: element },
  URLSearchParams, Date, console, setInterval() {},
  fetch(url) { return new Promise(resolve => pending.push({ url, resolve })); },
});
function data(kind, label) {
  return { from: '2026-10-10', to: '2026-10-10', kind,
    totals: { total: {tx: 1, rx: 2}, observed: {tx: 1, rx: 2} },
    matched_totals: {tx: 1, rx: 2}, health: {}, routes: [], days: [],
    rows: [{ label, tx: 1, rx: 2 }] };
}
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  get('kind').value = 'node';
  get('kind').handlers.change();
  assert.equal(pending.length, 2);
  pending[1].resolve({ ok: true, json: async () => data('node', 'ExampleProxy') });
  await tick();
  pending[0].resolve({ ok: true, json: async () => data('domain', 'old.example.com') });
  await tick();
  assert.equal(get('rows').children[0].children[0].textContent, 'ExampleProxy');
  assert.match(get('export').href, /kind=node/);
  console.log('UI request-order regression passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
