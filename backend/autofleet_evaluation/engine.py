from __future__ import annotations

import hashlib
import json

from . import ENGINE_VERSION
from .models import EvaluationInput
from .perception import evaluate_perception
from .planning import evaluate_planning


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def metric_value(report, path):
    value = report
    for part in path.split('.'):
        if not isinstance(value, dict): return None
        value = value.get(part)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def evaluate(dataset: EvaluationInput):
    p, failures = evaluate_perception(dataset.frames, dataset.config)
    planning, planning_failures = evaluate_planning(dataset.trajectories, dataset.config)
    payload = dataset.model_dump(by_alias=True, mode='json')
    truth = {'source': {k: v for k, v in payload['source'].items() if k != 'prediction_source'},
             'suite_version': dataset.suite_version,
             'frames': [{k: v for k, v in f.items() if k != 'predictions'} for f in payload['frames']],
             'trajectories': [{**{k: v for k, v in t.items() if k != 'samples'},
                               'obstacle_truth': [{'t_s': s['t_s'], 'obstacles': s['obstacles']} for s in t['samples']]}
                              for t in payload['trajectories']]}
    report = {'engine_version': ENGINE_VERSION, 'suite_version': dataset.suite_version,
              'candidate_version': dataset.candidate_version, 'source': payload['source'],
              'input_digest': digest(payload), 'truth_digest': digest(truth),
              'policy_digest': digest({'config': payload['config'], 'gates': payload['gates'], 'engine': ENGINE_VERSION}),
              'perception': p, 'planning': planning, 'slices': {}, 'failures': failures + planning_failures,
              'limitations': ['AP is evaluated at one IoU threshold; not COCO mAP50:95.',
                              'Trajectory safety metrics use sampled discs and constant-velocity TTC.',
                              'No HIL, real-road, SOTIF or NCAP validation is implied.']}
    scenario_ids = sorted({f.scenario_id for f in dataset.frames} | {t.scenario_id for t in dataset.trajectories})
    tags = sorted({tag for f in dataset.frames for tag in f.tags} | {tag for t in dataset.trajectories for tag in t.tags})
    for key, predicate in [(f'scenario:{s}', lambda x, s=s: x.scenario_id == s) for s in scenario_ids] + [
            (f'tag:{tag}', lambda x, tag=tag: tag in x.tags) for tag in tags]:
        sp, _ = evaluate_perception([f for f in dataset.frames if predicate(f)], dataset.config)
        st, _ = evaluate_planning([t for t in dataset.trajectories if predicate(t)], dataset.config)
        st.pop('per_scenario')
        report['slices'][key] = {'perception': sp, 'planning': st}
    checks = []
    for gate in dataset.gates:
        actual = metric_value(report, gate.metric)
        passed = actual is not None and (actual >= gate.value if gate.op == '>=' else actual <= gate.value)
        checks.append({**gate.model_dump(), 'actual': actual,
                       'status': 'unavailable' if actual is None else ('passed' if passed else 'failed')})
    if dataset.trajectories and planning['coverage'] < 1:
        checks.append({'metric': 'planning.coverage', 'op': '>=', 'value': 1,
                       'actual': planning['coverage'], 'status': 'failed', 'required': True})
    report['gate'] = {'passed': bool(dataset.gates) and all(c['status'] == 'passed' for c in checks),
                      'status': ('passed' if all(c['status'] == 'passed' for c in checks) else 'blocked') if checks else 'unconfigured',
                      'checks': checks}
    return report
