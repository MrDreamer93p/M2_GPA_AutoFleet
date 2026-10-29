from __future__ import annotations

import json
import math
from pathlib import Path


def load_catalog(path: Path):
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if data.get('schema') != 'autofleet.scenarios.v1':
        raise ValueError('unsupported scenario catalog')
    ids = [s['scenario_id'] for s in data['scenarios']]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate scenario ID')
    return data


def materialize(catalog, scenario_id, overrides=None):
    case = next((s for s in catalog['scenarios'] if s['scenario_id'] == scenario_id), None)
    if case is None: raise ValueError('unknown scenario ID')
    params = dict(case['parameters'])
    overrides = overrides or {}
    if overrides.keys() - params.keys(): raise ValueError('unknown scenario parameter')
    params.update(overrides)
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in params.values()):
        raise ValueError('scenario parameters must be finite numbers')
    dt, duration = params['dt_s'], params['duration_s']
    if not (.01 <= dt <= .1 and 2 <= duration / dt <= 2000 and math.isclose(duration / dt, round(duration / dt))):
        raise ValueError('use fixed 10-100 ms steps, 2-2000 intervals and an integral interval count')
    count = round(duration / dt)
    ref = []
    for i in range(count + 1):
        f = i / count
        ref.append({'t_s': round(i * dt, 8), 'x_m': params['start_x_m'] + f * (params['target_x_m'] - params['start_x_m']),
                    'y_m': params['start_y_m'] + f * (params['target_y_m'] - params['start_y_m']),
                    'yaw_rad': params['yaw_rad'], 'speed_mps': math.hypot(params['target_x_m'] - params['start_x_m'],
                                                                         params['target_y_m'] - params['start_y_m']) / duration})
    if case['kind'] == 'parking': ref[-1]['speed_mps'] = 0
    return {'schema': 'autofleet.scenario.v1', 'status': 'scenario_definition_not_execution',
            'scenario_id': scenario_id, 'suite_version': catalog['version'], 'map': case['map'],
            'seed': case['seed'], 'weather': case['weather'], 'dt_s': dt, 'settling_ticks': 20,
            'ego_blueprint': 'vehicle.tesla.model3', 'spawn_z_m': params['spawn_z_m'],
            'actors': case['actors'],
            'trajectory': {'scenario_id': scenario_id, 'kind': case['kind'], 'tags': case['tags'], 'reference': ref,
                           'corridor_half_width_m': params['corridor_half_width_m'],
                           'parking_position_tolerance_m': .3, 'parking_heading_tolerance_rad': .1},
            'gates': case['gates'],
            'note': 'Coordinates must be placed on the selected CARLA map or replaced with coordinates from your site.'}
