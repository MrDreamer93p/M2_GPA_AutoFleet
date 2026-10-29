from __future__ import annotations

import csv
import html
import io
import json


def numeric_metrics(report):
    result = {}
    def walk(value, prefix):
        if isinstance(value, dict):
            for key, child in value.items():
                if key not in ('per_scenario', 'per_class'):
                    walk(child, f'{prefix}.{key}' if prefix else key)
        elif value is None or isinstance(value, (int, float)) and not isinstance(value, bool):
            result[prefix] = value
    for section in ('perception', 'planning'):
        walk(report[section], section)
    return result


HIGHER_IS_BETTER = {'precision', 'recall', 'f1', 'map50', 'map_at_iou', 'coverage', 'obstacle_observation_coverage',
                    'collision_observation_coverage', 'parking_success_rate', 'min_clearance_m', 'min_ttc_s'}
LOWER_IS_BETTER = {'ade_m', 'fde_m', 'max_cross_track_m', 'collision_scenarios', 'lane_departure_samples',
                   'max_abs_jerk_mps3', 'max_abs_acceleration_mps2', 'parking_position_error_m', 'parking_heading_error_rad'}


def compare_reports(baseline, candidate):
    for key in ('truth_digest', 'policy_digest'):
        if baseline[key] != candidate[key]:
            raise ValueError(f'{key} differs: compare identical truth, suite, sampling and evaluation policy')
    regressions = []
    def compare(a, b, prefix=''):
        left, right = numeric_metrics(a), numeric_metrics(b)
        rows = {}
        for key in sorted(left.keys() | right.keys()):
            x, y = left.get(key), right.get(key)
            delta = y - x if x is not None and y is not None else None
            name = key.rsplit('.', 1)[-1]
            regressed = (delta is not None and ((name in HIGHER_IS_BETTER and delta < -1e-12)
                         or (name in LOWER_IS_BETTER and delta > 1e-12)))
            regressed |= x is not None and y is None and name in HIGHER_IS_BETTER | LOWER_IS_BETTER
            states = {}
            if key in ('planning.min_ttc_s', 'planning.min_clearance_m'):
                state_key = 'ttc_status' if name == 'min_ttc_s' else 'clearance_status'
                ax, by = a['planning'][state_key], b['planning'][state_key]
                states = {'baseline_status': ax, 'candidate_status': by}
                infinite = {'no_contact', 'no_obstacles'}
                if by == 'unavailable': regressed = ax != 'unavailable'
                elif by in infinite: regressed = False
                elif ax in infinite: regressed = True
            if regressed: regressions.append(prefix + key)
            rows[key] = {'baseline': x, 'candidate': y, 'delta': delta, 'regressed': regressed, **states}
        return rows
    metrics = compare(baseline, candidate)
    slices = {key: compare(baseline['slices'][key], candidate['slices'][key], f'{key}/')
              for key in sorted(baseline['slices'].keys() & candidate['slices'].keys())}
    return {'baseline': baseline['run_id'], 'candidate': candidate['run_id'], 'metrics': metrics,
            'slices': slices, 'regressed_metrics': regressions, 'candidate_gate': candidate['gate'],
            'regression_free': not regressions}


def html_report(report):
    esc = lambda x: html.escape(str(x), quote=True)
    rows = ''.join(f'<tr><th>{esc(k)}</th><td>{esc(v) if v is not None else "Unavailable"}</td></tr>'
                   for k, v in numeric_metrics(report).items())
    evidence = esc(json.dumps(report, ensure_ascii=False, indent=2))
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><title>AutoFleet evaluation</title>'
            '<style>body{font:16px system-ui;max-width:1100px;margin:2rem auto;padding:1rem}'
            'table{border-collapse:collapse;width:100%}th,td{border:1px solid #aaa;padding:.5rem;text-align:left}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
            f'<h1>Algorithm evaluation: {esc(report["candidate_version"])}</h1>'
            f'<p>Run: {esc(report["run_id"])} | Gate: {esc(report["gate"]["status"])}</p>'
            f'<p>Source: {esc(report["source"])}</p><table>{rows}</table>'
            f'<h2>Evidence, slices, failures and limitations</h2><pre>{evidence}</pre></html>')


def failures_csv(report):
    output = io.StringIO(newline='')
    fields = ['scenario_id', 'frame_id', 'reason', 'label', 't_s', 'object_index', 'tags']
    writer = csv.writer(output); writer.writerow(fields)
    for failure in report['failures']:
        row = []
        for key in fields:
            value = failure.get(key, '')
            if isinstance(value, list): value = '|'.join(value)
            value = str(value)
            if value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')):
                value = "'" + value
            row.append(value)
        writer.writerow(row)
    return output.getvalue()
