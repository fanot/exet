/* The first-mission guide disappears after two run attempts and stays hidden on reload. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('api/static/app.js', 'utf8');
const extract = name => {
  const start = source.indexOf(`function ${name}(`);
  assert(start >= 0, name);
  const begin = source.slice(start - 6, start) === 'async ' ? start - 6 : start;
  return source.slice(begin, source.indexOf('\n}', start) + 2);
};
const saved = new Map();
const localStorage = {
  getItem: key => saved.has(key) ? saved.get(key) : null,
  setItem: (key, value) => saved.set(key, value),
};
function page() {
  const banner = {hidden: false};
  const controls = Array.from({length: 4}, () => ({disabled: false}));
  const ctx = vm.createContext({localStorage, $: () => banner, $$: () => controls,
    S: {scenario: {features: []}, revision: 0, cancelRequested: false},
    params: () => ({fleet: []}), structuredClone, objectiveName: () => 'Время',
    runPlan: async () => { throw new Error('Расчёт не запустился'); },
    toast() {}, runPanel() {}, Cancelled: class Cancelled extends Error {},
    Number, Math,
  });
  vm.runInContext(source.slice(source.indexOf('let storageScope ='), source.indexOf('// ================================================================ тема')), ctx);
  vm.runInContext(source.slice(source.indexOf('let busy = false;'), source.indexOf('function updateOnboarding()')), ctx);
  for (const name of ['updateOnboarding', 'recordMissionRunAttempt', 'onRun']) vm.runInContext(extract(name), ctx);
  return {ctx, banner};
}

(async () => {
  const first = page();
  vm.runInContext('updateOnboarding()', first.ctx);
  assert.equal(first.banner.hidden, false);
  await vm.runInContext('onRun(["time"])', first.ctx);
  assert.equal(first.banner.hidden, false, 'One attempt keeps the guide');
  await vm.runInContext('onRun(["time"])', first.ctx);
  assert.equal(first.banner.hidden, true, 'Second attempt hides the guide even if calculation fails');
  assert.equal(saved.get('gfp.onboarding.attempts'), '2');
  const reloaded = page();
  vm.runInContext('updateOnboarding()', reloaded.ctx);
  assert.equal(reloaded.banner.hidden, true, 'The guide stays hidden after reload');
  console.log('First-mission guide attempt count and persistence passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
