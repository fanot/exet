/* Exercise the real selection, route and animation logic without a browser. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('api/static/app.js', 'utf8');
const extract = name => {
  const start = source.indexOf(`function ${name}(`);
  assert(start >= 0, name);
  return source.slice(start, source.indexOf('\n}', start) + 2);
};
const layer = () => ({items: new Set(), hasLayer(x) { return this.items.has(x); },
  addLayer(x) { this.items.add(x); }, removeLayer(x) { this.items.delete(x); },
  addTo(map) { map.addLayer(this); return this; }});
const elements = new Map();
const $ = key => {
  if (!elements.has(key)) elements.set(key, {hidden: false, children: [], classList: {toggle() {}, remove() {}},
    style: {}, setAttribute(key, value) { this[key] = value; }});
  return elements.get(key);
};
const sorties = ['A', 'B', 'C'].map((drone_id, i) => ({id: i + 1, drone_id,
  t_takeoff: 0, t_landing: 100, waypoints: [[30, 60, 100, 100, 0, 'survey'], [31, 61, 100, 100, 100, 'survey']],
  phases: [], model_title: 'Test', takeoff_iso: '', landing_iso: '', site: 'S', landing_site: 'S'}));
const S = {plan: {sorties, drones: ['A', 'B', 'C'].map(id => ({id}))}, sel: null,
  hidden: new Set(['C']), showOtherDrones: false, showFutureRoutes: false, t: 50, t0: 0, t1: 100,
  layers: {routes: layer(), future: layer(), coverage: layer(), drones: layer()},
  routeLines: {}, futureLines: {}, markers: {}, colors: {}, progress: null, coverageShown: 0,
  timelineEvents: [], timelineGroups: [], highlightedEvent: null};
for (const s of sorties) {
  S.routeLines[s.id] = [{options: {_ph: 'survey'}, _times: [0, 100], _fullPts: [[60, 30], [61, 31]],
    setStyle(style) { this.style = style; }, setLatLngs(coords) { this.coords = coords; }, bringToFront() {}}];
  S.futureLines[s.id] = [{options: {_ph: 'survey'}, setStyle(style) { this.style = style; }}];
  S.markers[s.drone_id] = {setLatLng() {}, setTooltipContent() {}};
}
const noop = () => {};
const map = layer(); map.addLayer(S.layers.routes);
map.latLngToLayerPoint = ([lat, lon]) => ({x: lon, y: lat});
const coverageClicks = [];
const ctx = vm.createContext({S, $, $$: () => [], store: {set: noop}, renderDrones: noop, drawSelection: noop,
  drawGantt: noop, dropLayer: noop, updateProfileCursor: noop, renderOverlayList: noop,
  routeColor: () => '#2563eb', showCoverageAnalytics: (...args) => coverageClicks.push(args),
  map, canvasR: {}, coverageR: {}, L: {geoJSON: () => ({handlers: {}, on(type, handler) { this.handlers[type] = handler; return this; }}),
    DomEvent: {stopPropagation: noop}},
  hms: String, hm: String, hmDur: String, dm: String, dur: String, esc: String, fmt: String,
  plural: () => 'события',
  ico: String, icon: String, profileSVG: () => '', canExport: () => false, bindProfile: noop,
  PHASE_RU: {survey: 'Съёмка'}, PHASE_COLORS: {}, Math});
for (const name of ['isDroneVisible', 'styleFor', 'applyRouteStyles', 'setFutureRoutes', 'routeTimeAtClick',
  'updateRouteProgress', 'updateCoverage', 'prepareObstacleGeometry', 'sortieRouteBounds',
  'nearestObstacleOnSortie', 'collectTimelineEvents', 'groupTimelineEvents',
  'renderTimelineEvents', 'showTimelineGroup',
  'updateTimelineCaption', 'zoneLabel', 'hoverSortie', 'selectSortie',
  'clearSelection', 'posAt', 'setTime', 'renderInspector']) vm.runInContext(extract(name), ctx);
const run = code => vm.runInContext(code, ctx);
assert.match(run('zoneLabel({kind:"no_fly", name:"Аэродром (R=2.5 км)"}).html'), /R = 2.5 км/);
assert.match(run('zoneLabel({kind:"no_fly", alt_max_m:120}).html'), /Ограничение.*до 120 м/);
assert.equal(run('routeTimeAtClick(S.routeLines[1][0], [60.5, 30.5])'), 50, 'Click position maps to the corresponding moment on a route');
const visibleMarkers = () => Object.keys(S.markers).filter(id => S.layers.drones.hasLayer(S.markers[id]));
const visibleRoutes = () => sorties.filter(s => S.layers.routes.hasLayer(S.routeLines[s.id][0])).map(s => s.drone_id);
run('applyRouteStyles(); setTime(50)');
assert.deepEqual(visibleMarkers(), ['A', 'B']);
assert.equal(S.routeLines[1][0].coords.length, 2, 'The flown route ends at the interpolated position');
assert.equal(S.routeLines[1][0].coords[1][0], 60.5);
run('setTime(0)');
assert.equal(S.routeLines[1][0].coords.length, 0, 'No route is drawn before takeoff');
run('setTime(100)');
assert.equal(S.routeLines[1][0].coords[1][0], 61, 'The whole route is present at landing');
run('setTime(50)');
run('setFutureRoutes(true)');
assert(map.hasLayer(S.layers.future), 'Future routes can be shown on demand');
run('setFutureRoutes(false)');
assert(!map.hasLayer(S.layers.future), 'Future routes can be hidden again');
map.removeLayer(S.layers.routes);
run('setFutureRoutes(true)');
assert(map.hasLayer(S.layers.routes) && map.hasLayer(S.layers.future), 'Future path enables flown routes when needed');
run('setFutureRoutes(false)');
run('selectSortie(1, false)');
assert.equal(S.t, 50, 'Selecting an already active flight keeps the clock');
assert.deepEqual(visibleMarkers(), ['A'], 'Other markers disappear immediately at unchanged time');
assert.deepEqual(visibleRoutes(), ['A'], 'Other routes leave the hit-test layer');
run('hoverSortie(2, true)');
assert.equal(S.routeLines[2][0].style.opacity, 0, 'Hover cannot reveal a hidden route');
assert.doesNotMatch($('#inspector').innerHTML, /ins-others|Показать остальные БВС/);
run('selectSortie(2, false)');
assert.deepEqual(visibleMarkers(), ['B']);
assert.deepEqual(visibleRoutes(), ['B']);
run('setTime(75)');
assert.deepEqual(visibleMarkers(), ['B'], 'Isolation persists during animation');
run('clearSelection()');
assert.deepEqual(visibleMarkers(), ['A', 'B']);
assert.deepEqual(visibleRoutes(), ['A', 'B']);
assert(S.hidden.has('C'), 'Manual visibility choice survives selection');
S.routeFocus = 2;
run('applyRouteStyles(); setTime(50)');
assert.deepEqual(visibleRoutes(), ['B'], 'Route focus removes every other sortie from the map');
assert.deepEqual(visibleMarkers(), ['B'], 'Route focus isolates its drone marker');
S.routeFocus = null;
run('applyRouteStyles(); setTime(50)');
assert.deepEqual(visibleRoutes(), ['A', 'B'], 'Showing all routes restores visible sorties');
run('selectSortie(3, false)');
assert.deepEqual(visibleMarkers(), ['C'], 'Explicitly selected board becomes visible');
run('selectSortie(999, false)');
assert.equal(S.sel, 3, 'Stale selection is ignored');
S.progress = {steps: [{from: 0, t: 25, pct: 50, geometry: {}},
  {from: 30, t: 75, pct: 100, geometry: {}}], final_pct: 100, allowed_km2: 1};
run('setTime(0)');
assert.equal(S.coverageShown, 0);
run('setTime(25)');
assert.equal(S.coverageShown, 1);
assert.match($('#coverage-percent').textContent, /50/);
S.progress.steps[0]._layer.handlers.click({originalEvent: {}, latlng: [60.5, 30.5]});
assert.equal(coverageClicks[0][1], 0, 'Clicking the coverage footprint opens analytics for its time step');
run('setTime(50)');
assert.equal(S.coverageShown, 1);
assert.match($('#coverage-area').textContent, /км²/);
run('setTime(75)');
assert.equal(S.coverageShown, 2);
run('setTime(10)');
assert.equal(S.coverageShown, 0, 'Rewinding removes future survey footprints');
S.plan.validation = {checks: [{passed: true, name: 'Опасные сближения',
  points: [{t: 55, label: 'Наибольшее сближение: 70 м'}]},
  {passed: false, name: 'Высотные препятствия', points: [{t: 65, label: 'Вышка'}]}]};
const obstacle = {type: 'Point', coordinates: [30.5, 60.5]};
const near = run('nearestObstacleOnSortie(S.plan.sorties[0], {type: "Point", coordinates: [30.5, 60.5]})');
assert(Math.abs(near.t - 50) < 0.1, 'Obstacle approach is timed along the route, including between waypoints');
assert(near.distance_m < 1);
const crossing = run('nearestObstacleOnSortie(S.plan.sorties[0], {type: "LineString", coordinates: [[30.4, 60.6], [30.6, 60.4]]})');
assert(Math.abs(crossing.t - 50) < 0.1 && crossing.distance_m < 1, 'Line obstacle crossing is detected');
const scenario = {features: [{properties: {kind: 'no_fly', name: 'Временная зона',
  active_from: new Date(40_000).toISOString(), active_to: new Date(80_000).toISOString()}, geometry: {}},
  {properties: {kind: 'obstacle', name: 'Вышка'}, geometry: obstacle}]};
const events = vm.runInContext('collectTimelineEvents(S.plan, scenario, 0, 100)',
  vm.createContext({...ctx, scenario}));
assert(events.some(e => e.kind === 'restriction' && e.t === 40));
assert(events.some(e => e.kind === 'obstacle' && Math.abs(e.t - 50) < 0.1 && e.label.includes('Вышка')));
assert(events.some(e => e.kind === 'check' && e.t === 55 && e.label.startsWith('Контроль:')));
assert(events.some(e => e.kind === 'alert' && e.t === 65 && e.label.startsWith('Нарушение:')));
assert(events.every((e, i) => i === 0 || events[i - 1].t <= e.t));
S.timelineEvents = events;
S.timelineGroups = vm.runInContext('groupTimelineEvents(S.timelineEvents, 0, 100)', ctx);
S.scenario = scenario;
run('renderTimelineEvents(S.plan)');
assert.match($('#timeline-markers').innerHTML, /event-obstacle/);
assert.match($('#timeline-markers').innerHTML, /event-restriction/);
S.timelineDrone = 'A';
run('renderTimelineEvents(S.plan)');
assert(S.timelineGroups.every(g => g.events.every(e => e.droneId === 'A')), 'Timeline filter retains only events for the chosen drone');
S.timelineDrone = '';
run('renderTimelineEvents(S.plan)');
run(`showTimelineGroup(${S.timelineGroups.findIndex(g => g.kind === 'obstacle')})`);
assert.match($('#timeline-event-popover').innerHTML, /Вышка/);
run('setTime(39)');
assert.match($('#timeline-next').textContent, /40|00:00/);
console.log('Flight trails, future routes, coverage scrubbing and sortie isolation passed');
