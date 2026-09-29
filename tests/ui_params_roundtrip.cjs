// Exercise the production form functions with DOM value/range semantics.
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync('api/static/app.js', 'utf8');
const html = fs.readFileSync('api/static/index.html', 'utf8');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const elements = new Map();
for (const tag of html.matchAll(/<input\b[^>]*>/g)) {
  const attrs = Object.fromEntries([...tag[0].matchAll(/([\w-]+)="([^"]*)"/g)].map(m => [m[1], m[2]]));
  if (!attrs.id) continue;
  let val = attrs.value || '';
  const element = {...attrs, checked: /\bchecked\b/.test(tag[0]), setAttribute() {}};
  Object.defineProperty(element, 'value', {
    get: () => val,
    set: value => { val = attrs.type === 'range'
      ? String(Math.max(Number(element.min), Math.min(Number(element.max), Number(value))))
      : String(value); }
  });
  elements.set('#' + attrs.id, element);
}
function element(selector) {
  if (!elements.has(selector)) elements.set(selector, {setAttribute() {}});
  return elements.get(selector);
}
const ctx = vm.createContext({document: {querySelector: element}, localStorage: {},
  summarize() {}, hints() {}, setChip() {}, invalidatePlan() {}, console});
vm.runInContext(source.slice(0, source.indexOf('// ================================================================ тема')), ctx);
function productionFunction(name) {
  const start = source.indexOf('function ' + name + '(');
  if (start < 0) throw Error('Missing production function: ' + name);
  return source.slice(start, source.indexOf('\n}', start) + 2);
}
vm.runInContext(source.split('\n').find(line => line.startsWith('const RUMB =')), ctx);
for (const name of ['stepperHTML', 'renderFleet', 'setWindDir', 'overlapControls', 'params', 'restoreParams']) {
  vm.runInContext(productionFunction(name), ctx);
}
ctx.catalog = input.catalog;
vm.runInContext('S.catalog = catalog; S.fleet = Object.fromEntries(Object.keys(catalog.drones).map(k => [k, 0]));', ctx);
const results = [];
for (const p of input.params) {
  ctx.original = p;
  vm.runInContext('restoreParams(original)', ctx);
  results.push({params: vm.runInContext('params()', ctx), front: element('#front').value, side: element('#side').value});
}
const callbackStart = source.indexOf('$("#fleet").addEventListener("click",');
const callbackSource = source.slice(callbackStart, source.indexOf('\n});', callbackStart) + 4);
const steppers = [];
for (const kind of ['cnt', 'bat']) for (const delta of [-1, 1]) {
  const rendered = element('#fleet').innerHTML;
  const key = kind + ':geoscan_gemini';
  const attrs = new RegExp('data-k="' + key + '" data-min="(\\d+)" data-max="(\\d+)"').exec(rendered);
  if (!attrs) throw Error('Missing rendered stepper');
  let callback;
  element('#fleet').addEventListener = (_, fn) => callback = fn;
  vm.runInContext(callbackSource, ctx);
  vm.runInContext('S.fleet.geoscan_gemini = 20; S.bats.geoscan_gemini = 20; renderFleet = () => {}; store.set = () => {};', ctx);
  const stepper = {dataset: {k: key, min: attrs[1], max: attrs[2]}};
  const button = {dataset: {d: String(delta)}, closest: () => stepper};
  callback({target: {closest: () => button}});
  steppers.push(vm.runInContext(kind === 'cnt' ? 'S.fleet.geoscan_gemini' : 'S.bats.geoscan_gemini', ctx));
}
process.stdout.write(JSON.stringify({results, steppers}));
