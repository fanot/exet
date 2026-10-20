/* Check the history selection and comparison flow without a browser. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('api/static/app.js', 'utf8');
const html = fs.readFileSync('api/static/index.html', 'utf8');
const extract = name => {
  const start = source.indexOf(`function ${name}(`);
  assert(start >= 0, name);
  const begin = source.slice(start - 6, start) === 'async ' ? start - 6 : start;
  return source.slice(begin, source.indexOf('\n}', start) + 2);
};
assert(html.indexOf('id="history-actions"') < html.indexOf('id="cmp"'));
assert(html.indexOf('id="cmp"') < html.indexOf('id="history"'), 'Comparison follows the action, before the plan list');

let checkboxes = [];
const elements = new Map();
const $ = selector => {
  if (!elements.has(selector)) {
    const element = {events: {}, textContent: '', disabled: false,
      addEventListener(type, handler) { this.events[type] = handler; }};
    Object.defineProperty(element, 'innerHTML', {
      get() { return this.html || ''; },
      set(value) {
        this.html = value;
        if (selector === '#history') {
          checkboxes = [...value.matchAll(/<input type="checkbox" class="hc" value="([^"]+)"( checked)?/g)]
            .map(([, id, checked]) => ({value: id, checked: !!checked, events: {},
              addEventListener(type, handler) { this.events[type] = handler; }}));
        }
      },
    });
    elements.set(selector, element);
  }
  return elements.get(selector);
};
const $$ = selector => selector === '#history .hc' ? checkboxes : [];
const summary = {makespan_h: 5, flight_hours: 8, sorties: 6, total_km: 42,
  coverage_allowed_pct: 99.9, drones_used: 2};
const list = [
  {id: 'one', objective: 'flight', status: 'ok', validation_passed: true, summary},
  {id: 'two', objective: 'time', status: 'ok', validation_passed: true, summary},
];
const plans = {
  one: {id: 'one', params: {objective: 'flight'}, summary: {...summary, allocation_shares: {geoscan_201: 1}}, validation: {passed: true}},
  two: {id: 'two', params: {objective: 'time'}, summary: {...summary, makespan_h: 4}, validation: {passed: true}},
};
let fail = false;
const errors = [];
const ctx = vm.createContext({$, $$, S: {plan: null},
  api: async url => {
    if (url === '/api/plans') return list;
    if (fail) throw new Error('Нет связи');
    return plans[url.split('/').at(-1)];
  },
  toast: message => errors.push(message), ico: () => '', icon: () => '', esc: String,
  fmt: value => value == null || Number.isNaN(value) ? '—' : String(value),
  objectiveName: value => ({flight: 'Минимум налёта', time: 'Минимум времени'})[value] || value,
  SHORT: {geoscan_201: '201'}, Number, Math, Set,
});
for (const name of ['refreshHistory', 'renderCompare']) vm.runInContext(extract(name), ctx);

(async () => {
  await vm.runInContext('refreshHistory()', ctx);
  assert.equal($('#do-cmp').disabled, true);
  checkboxes[0].checked = true; checkboxes[0].events.change();
  checkboxes[1].checked = true; checkboxes[1].events.change();
  assert.equal($('#do-cmp').disabled, false);
  assert.equal($('#cmp-selection').textContent, 'Выбрано: 2');
  await $('#do-cmp').events.click();
  assert.match($('#cmp').innerHTML, /Сравнение/);
  assert.match($('#cmp').innerHTML, /201 100%/);
  assert.match($('#cmp').innerHTML, /Минимум времени/);
  checkboxes[1].checked = false; checkboxes[1].events.change();
  assert.equal($('#cmp').innerHTML, '', 'Changing the selection removes stale comparison results');
  assert.equal($('#do-cmp').disabled, true);
  checkboxes[1].checked = true; checkboxes[1].events.change();
  await vm.runInContext('refreshHistory()', ctx);
  assert.equal(checkboxes.filter(c => c.checked).length, 2, 'Selection survives refreshing history');
  fail = true;
  await $('#do-cmp').events.click();
  assert.match($('#cmp').innerHTML, /Не удалось сравнить планы/);
  assert.match(errors.at(-1), /Нет связи/);
  assert.equal($('#do-cmp').disabled, false);
  console.log('History comparison selection, rendering and errors passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
