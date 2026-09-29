import copy
import importlib
import json
import math
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import shapely
from fastapi.testclient import TestClient
from shapely.geometry import LineString, box

from planner.catalog import load_catalog
from planner.coverage import Piece
from planner.geo import parse_geojson
from planner.optimizer import Planner
from planner.routing import Router
from planner.sortie import TrajBuilder, _transit, pack
from test_audit_regressions import model_context, params, scenario


@pytest.mark.parametrize('field', ['gsd_cm', 'front_overlap', 'side_overlap', 'deadline_h',
    'takeoff_weight_h', 'wind_speed_ms', 'wind_dir_deg', 'cloud_base_m',
    'min_sun_elevation_deg', 'vlos_radius_km', 'reserve_pct'])
@pytest.mark.parametrize('literal', ['NaN', 'Infinity', '1e309'])
@pytest.mark.parametrize('endpoint', ['/api/plan', '/api/advise', '/api/validate'])
def test_invalid_numbers_return_serializable_422(field, literal, endpoint):
    api = importlib.import_module('api.app')
    body = json.dumps({'scenario': scenario(), 'params': params().model_dump(mode='json')})
    data = json.loads(body)
    if endpoint == '/api/validate':
        data['plan'] = {'sorties': []}
    data['params'][field] = '__INVALID_NUMBER__'
    body = json.dumps(data).replace('"__INVALID_NUMBER__"', literal)
    response = TestClient(api.app).post(endpoint, content=body, headers={'Content-Type': 'application/json'})
    assert response.status_code == 422
    assert response.json()['detail']


def test_accepted_cancel_prevents_publication_after_early_failure(monkeypatch, tmp_path):
    api = importlib.import_module('api.app')
    monkeypatch.setattr(api, 'PLANS', tmp_path)
    monkeypatch.setattr(api, 'PLAN_SCENARIOS', tmp_path / 'scenarios')
    (tmp_path / 'scenarios').mkdir()
    job = {'job_id': 'cancel-regression', 'status': 'queued', 'progress': 0., 'message': 'В очереди'}
    monkeypatch.setattr(api, 'JOBS', {'cancel-regression': job})
    class EarlyRefusal:
        search_log = []
        def __init__(self, *a): pass
        def run(self):
            api.cancel_job('cancel-regression')
            return {'id': 'must-not-save', 'status': 'infeasible'}
    monkeypatch.setattr(api, 'Planner', EarlyRefusal)
    api._run_job('cancel-regression', parse_geojson(scenario()), params(), scenario())
    assert job['status'] == 'cancelled'
    assert not list(tmp_path.rglob('*.json'))


def test_preparation_has_cancellation_checkpoint_after_dem_load():
    api = importlib.import_module('api.app')
    def cancel_after_terrain(fraction, message):
        if fraction > 0.02:
            raise api.Cancelled()
    planner = Planner(parse_geojson(scenario()), params(), cancel_after_terrain)
    with pytest.raises(api.Cancelled):
        planner.run()
    assert hasattr(planner, 'terrain_raw')
    assert not planner.search_log


def test_form_restores_all_parameters_and_numeric_boundaries():
    if not shutil.which('node'):
        pytest.skip('Node is required to exercise production JavaScript')
    cat = load_catalog()
    fixture = Path(__file__).parent / 'fixtures' / 'mission_params.json'
    parameter_sets = json.loads(fixture.read_text(encoding='utf-8'))
    normalized = [params(**item).model_dump(mode='json') for item in parameter_sets]
    for start, direction in [('2026-09-25T05:02:37.123Z', 359.9),
                             ('2026-09-25T08:13:27+05:30', 359.5),
                             ('2026-09-25T05:02:37-04:00', 0.1),
                             ('2026-09-25T05:02:37+03:00', 0.0)]:
        normalized.append(params(start_time=start, wind_dir_deg=direction,
                                 reserve_pct=60, min_sun_elevation_deg=-5, takeoff_weight_h=5).model_dump(mode='json'))
    catalog = {'drones': {k: vars(v) for k, v in cat.drones.items()}, 'survey_types': cat.survey_types}
    proc = subprocess.run(['node', 'tests/ui_params_roundtrip.cjs'],
                          input=json.dumps({'catalog': catalog, 'params': normalized}), text=True,
                          capture_output=True, check=True)
    output = json.loads(proc.stdout)
    for expected, actual in zip(normalized, output['results']):
        restored = params(**actual['params']).model_dump(mode='json')
        for field in expected:
            if field == 'start_time':
                from datetime import datetime
                assert datetime.fromisoformat(restored[field]) == datetime.fromisoformat(expected[field])
            elif field == 'fleet':
                positive = lambda fleet: {f['model']: (f['count'], f['batteries'] or cat.drones[f['model']].batteries)
                                          for f in fleet if f['count']}
                assert positive(restored[field]) == positive(expected[field])
            elif isinstance(expected[field], float):
                assert restored[field] == pytest.approx(expected[field]), field
            else:
                assert restored[field] == expected[field], field
        if expected['front_overlap'] is None and expected['survey_type'] == 'multispectral':
            assert actual['front'] == '75' and actual['side'] == '60'
        if expected['side_overlap'] is None and expected['survey_type'] == 'lidar':
            assert actual['side'] == '30'
    assert output['steppers'] == [19, 20, 19, 20]


def test_sparse_router_expands_graph_without_cutting_safety_buffer(monkeypatch):
    import planner.routing as routing
    monkeypatch.setattr(routing, 'FULL_GRAPH_MAX', 2)
    monkeypatch.setattr(routing, 'K_NEAREST', 1)
    forbidden = box(0, 0, 100, 100)
    router = Router(forbidden)
    path = router.path((-10, 50), (110, 50))
    assert not LineString(path).intersects(forbidden.buffer(-0.02))
    assert router._neighbors > 1


def test_pack_uses_reachable_site_when_nearest_site_cannot_reach_piece(monkeypatch):
    ctx = model_context('geoscan_gemini')
    ctx.sites_xy = np.array([[0., 0.], [1000., 0.]])
    leg = ctx.site_leg
    monkeypatch.setattr(ctx, 'site_leg', lambda i, x, y, outbound: (math.inf, math.inf) if i == 0 else leg(i, x, y, outbound))
    piece = Piece(model=ctx.key, comp=0, col=0, row=0, ax=200, ay=0, bx=400, by=0)
    sorties, bad = pack(ctx, [piece], 200)
    assert not bad and sorties and sorties[0].site == 1


def test_unreachable_transition_keeps_other_pieces_in_schedule(monkeypatch):
    from planner.dispatch import DroneState, dispatch
    from planner.routing import RoutingError
    from planner.sortie import Sortie, build_trajectory
    ctx = model_context('geoscan_gemini')
    pieces = [Piece(model=ctx.key, comp=i, col=0, row=0, ax=200 + i * 500, ay=0,
                    bx=400 + i * 500, by=0) for i in range(2)]
    def one_inaccessible(context, sortie, site, layer):
        if any(p.comp == 0 for p in sortie.pieces):
            raise RoutingError('inaccessible piece')
        return build_trajectory(context, sortie, site, layer)
    monkeypatch.setattr('planner.dispatch.build_trajectory', one_inaccessible)
    drone = DroneState('GEM-1', ctx.key, ctx.model.title, 200, [0.], 3600, 120, 0.)
    out, _, stats = dispatch({ctx.key: ctx}, {ctx.key: [Sortie(ctx.key, pieces, 0)]}, [drone],
                             ctx.sites_xy, load_catalog().glob, None, ctx.terrain)
    assert len(out) == 1 and out[0].sortie.pieces[0].comp == 1
    assert len(stats['unreachable']) == 1


def test_plane_transit_routes_from_actual_spiral_exit():
    ctx = model_context()
    forbidden = box(400, -150, 600, -50)
    ctx.router_transit = Router(forbidden)
    tb = TrajBuilder(0, -200, 300)
    _transit(tb, ctx, [(0, 0), (1000, 0)], 300, 'transit_out')
    assert not LineString(np.asarray(tb.P)[:, :2]).intersects(forbidden.buffer(-0.02))


def test_high_gsd_reserves_height_for_terrain_interpolation():
    p = Planner(parse_geojson(scenario()), params(gsd_cm=200))
    p.prepare()
    p.survey_geoms()
    assert p.geoms['geoscan_gemini'].altitude_agl <= load_catalog().drones['geoscan_gemini'].max_alt_agl_m - 8


def test_transit_accounts_for_obstacle_top_in_normal_corridor():
    from planner.sortie import _chunks
    ctx = model_context('geoscan_gemini')
    ctx.terrain = SimpleNamespace(enabled=True, h=lambda x, y: np.zeros_like(np.atleast_1d(x)))
    ctx.obst_tops = (shapely.STRtree([box(10, -10, 20, 10)]), np.array([110.]))
    chunks = _chunks(ctx, np.array([0., 0.]), np.array([100., 0.]), 100)
    assert all(z >= 111 for _, _, z in chunks)


def test_failure_reports_routing_reason_without_recommending_existing_801():
    p = Planner(parse_geojson(scenario()), params(survey_type='ir', fleet=[{'model': 'geoscan_801', 'count': 1}]))
    p.excluded = {'geoscan_201': ['Нет ИК камеры']}
    result = p._fail('Нет маршрута между площадкой и участком', 1)
    texts = [r['text'] for r in result['recommendations']]
    assert texts[0] == result['error']
    assert not any('добавьте его' in text for text in texts)


def test_invalid_cheapest_candidate_does_not_hide_valid_alternative(monkeypatch):
    planner = Planner(parse_geojson(scenario()), params())
    search = planner.search
    dispatch = planner._dispatch_cfg
    calls = 0
    def two_candidates():
        search()
        planner.candidates = [copy.deepcopy(planner.candidates[0]) for _ in range(2)]
    def unsafe_first(*args):
        nonlocal calls
        result = dispatch(*args)
        calls += 1
        if calls == 1:
            assigned = result[1]
            assigned[0].regression_bad = True
            assigned[0].traj.P = [(x, y, z + 600 if z > 0 else z) for x, y, z in assigned[0].traj.P]
        return result
    monkeypatch.setattr(planner, 'search', two_candidates)
    monkeypatch.setattr(planner, '_dispatch_cfg', unsafe_first)
    monkeypatch.setattr(planner, '_actual_score', lambda assigned: -1 if getattr(assigned[0], 'regression_bad', False) else 0)
    result = planner.run()
    assert calls == 2
    assert result['validation']['passed'] and result['status'] == 'ok'
    assert any('отклонен проверкой' in warning for warning in result['warnings'])


def test_vlos_recommendation_reports_partial_coverage():
    from planner.geo import LocalFrame
    from planner.sites import vlos_sites
    result = vlos_sites(box(0, 0, 10000, 10000), np.zeros((0, 2)), 300,
                        shapely.Polygon(), LocalFrame(38, 55), max_sites=2)
    assert result['coverage_pct'] < 95
    assert 'недостаточно для полного покрытия' in result['reason']
