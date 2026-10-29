from __future__ import annotations

import math
from bisect import bisect_left

from .models import Config, Trajectory


def angle_error(a, b):
    return abs(math.atan2(math.sin(a - b), math.cos(a - b)))


def distance_to_path(p, path):
    best = math.inf
    for a, b in zip(path, path[1:]):
        dx, dy = b.x_m - a.x_m, b.y_m - a.y_m
        norm = dx * dx + dy * dy
        weight = min(1, max(0, ((p.x_m - a.x_m) * dx + (p.y_m - a.y_m) * dy) / norm)) if norm else 0
        best = min(best, math.hypot(p.x_m - a.x_m - weight * dx, p.y_m - a.y_m - weight * dy))
    return best


def disc_ttc(sample, obstacle, ego_radius):
    """Earliest contact of constant-velocity discs; None means no future contact."""
    rx, ry = obstacle.x_m - sample.x_m, obstacle.y_m - sample.y_m
    vx = obstacle.vx_mps - sample.speed_mps * math.cos(sample.yaw_rad)
    vy = obstacle.vy_mps - sample.speed_mps * math.sin(sample.yaw_rad)
    c = rx * rx + ry * ry - (ego_radius + obstacle.radius_m) ** 2
    if c <= 0:
        return 0.0
    a = vx * vx + vy * vy
    b = 2 * (rx * vx + ry * vy)
    discriminant = b * b - 4 * a * c
    if a == 0 or b >= 0 or discriminant < 0:
        return None
    return (-b - math.sqrt(discriminant)) / (2 * a)


def one_trajectory(t: Trajectory, config: Config):
    timestamps = [p.t_s for p in t.samples]
    pairs = []
    used = set()
    for ref in t.reference:
        index = bisect_left(timestamps, ref.t_s)
        choices = [j for j in (index - 1, index) if 0 <= j < len(t.samples) and j not in used]
        j = min(choices, key=lambda k: abs(t.samples[k].t_s - ref.t_s), default=None)
        if j is not None and abs(t.samples[j].t_s - ref.t_s) <= config.alignment_tolerance_s:
            used.add(j); pairs.append((ref, t.samples[j]))
    errors = [math.hypot(ref.x_m - sample.x_m, ref.y_m - sample.y_m) for ref, sample in pairs]
    final = pairs[-1][1] if pairs and pairs[-1][0] is t.reference[-1] else None
    fde = math.hypot(final.x_m - t.reference[-1].x_m, final.y_m - t.reference[-1].y_m) if final else None
    cross_track = [distance_to_path(p, t.reference) for p in t.samples]
    accelerations = [(b.speed_mps - a.speed_mps) / (b.t_s - a.t_s) for a, b in zip(t.samples, t.samples[1:])]
    # Acceleration lives at interval midpoints; jerk uses midpoint spacing.
    mids = [(a.t_s + b.t_s) / 2 for a, b in zip(t.samples, t.samples[1:])]
    jerks = [(b - a) / (mids[i + 1] - mids[i]) for i, (a, b) in enumerate(zip(accelerations, accelerations[1:]))]
    ttcs, clearances = [], []
    collision = any(p.collision for p in t.samples)
    for sample in t.samples:
        for actor in sample.obstacles:
            clearance = math.hypot(actor.x_m - sample.x_m, actor.y_m - sample.y_m) - t.ego_radius_m - actor.radius_m
            clearances.append(clearance)
            collision |= clearance <= 0
            ttc = disc_ttc(sample, actor, t.ego_radius_m)
            if ttc is not None:
                ttcs.append(ttc)
    heading = angle_error(final.yaw_rad, t.reference[-1].yaw_rad) if final else None
    parking_success = None
    if t.kind == 'parking':
        parking_success = (len(pairs) == len(t.reference) and fde is not None
                           and fde <= t.parking_position_tolerance_m
                           and heading <= t.parking_heading_tolerance_rad and not collision)
    result = {'scenario_id': t.scenario_id, 'kind': t.kind, 'reference_samples': len(t.reference),
              'matched_samples': len(pairs), 'coverage': len(pairs) / len(t.reference),
              'ade_m': sum(errors) / len(errors) if errors else None, 'fde_m': fde,
              'max_cross_track_m': max(cross_track),
              'lane_departure_samples': sum(x > t.corridor_half_width_m for x in cross_track),
              'collision': collision, 'min_clearance_m': min(clearances) if clearances else None,
              'min_ttc_s': min(ttcs) if ttcs else None,
              'max_abs_acceleration_mps2': max(map(abs, accelerations)),
              'max_abs_jerk_mps3': max(map(abs, jerks)) if jerks else None,
              'parking_position_error_m': fde if t.kind == 'parking' else None,
              'parking_heading_error_rad': heading if t.kind == 'parking' else None,
              'parking_success': parking_success,
              'trace': [{'t_s': p.t_s, 'x_m': p.x_m, 'y_m': p.y_m} for p in t.samples],
              'reference_trace': [{'t_s': p.t_s, 'x_m': p.x_m, 'y_m': p.y_m} for p in t.reference]}
    reasons = []
    if result['coverage'] < 1: reasons.append('missing_trajectory_output')
    if collision: reasons.append('collision')
    if result['lane_departure_samples']: reasons.append('corridor_departure')
    if ttcs and min(ttcs) < t.minimum_ttc_s: reasons.append('low_ttc')
    if parking_success is False: reasons.append('parking_tolerance')
    failures = [{'scenario_id': t.scenario_id, 'frame_id': 'trajectory', 'reason': reason,
                 'label': 'ego', 'object_index': 0, 'tags': t.tags} for reason in reasons]
    return result, failures


def evaluate_planning(trajectories: list[Trajectory], config: Config):
    rows, failures = [], []
    for t in trajectories:
        row, errors = one_trajectory(t, config); rows.append(row); failures.extend(errors)
    def values(key): return [r[key] for r in rows if r[key] is not None]
    def mean(key):
        v = values(key); return sum(v) / len(v) if v else None
    def maximum(key):
        v = values(key); return max(v) if v else None
    def minimum(key):
        v = values(key); return min(v) if v else None
    parking = values('parking_success')
    total_ref = sum(r['reference_samples'] for r in rows)
    return {'scenarios': len(rows), 'coverage': sum(r['matched_samples'] for r in rows) / total_ref if total_ref else None,
            'ade_m': mean('ade_m'), 'fde_m': mean('fde_m'), 'max_cross_track_m': maximum('max_cross_track_m'),
            'collision_scenarios': sum(r['collision'] for r in rows) if rows else None,
            'lane_departure_samples': sum(r['lane_departure_samples'] for r in rows) if rows else None,
            'min_ttc_s': minimum('min_ttc_s'), 'min_clearance_m': minimum('min_clearance_m'),
            'max_abs_jerk_mps3': maximum('max_abs_jerk_mps3'),
            'max_abs_acceleration_mps2': maximum('max_abs_acceleration_mps2'),
            'parking_position_error_m': maximum('parking_position_error_m'),
            'parking_heading_error_rad': maximum('parking_heading_error_rad'),
            'parking_success_rate': sum(parking) / len(parking) if parking else None,
            'per_scenario': rows}, failures
