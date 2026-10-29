import copy
import math

import pytest


def image_input():
    return {
        'schema': 'autofleet.evaluation.v1', 'suite_version': 'unit-v1',
        'candidate_version': 'model-a', 'source': {'kind': 'synthetic', 'dataset': 'unit', 'revision': '1'},
        'frames': [{'scenario_id': 'follow', 'frame_id': '0', 't_s': 0, 'tags': ['night'],
                    'ground_truth': [{'label': 'car', 'box': [0, 0, 0.5, 0.5]}],
                    'predictions': [{'label': 'car', 'box': [0, 0, 0.5, 0.5], 'score': 0.9}]}],
        'trajectories': [], 'gates': [{'metric': 'perception.recall', 'op': '>=', 'value': 0.9}]
    }


def trajectory_input():
    d = image_input(); d['frames'] = []; d['gates'] = []
    ref = [{'t_s': i, 'x_m': i, 'y_m': 0, 'yaw_rad': 0, 'speed_mps': 1} for i in range(3)]
    d['trajectories'] = [{'scenario_id': 'parking', 'kind': 'parking', 'tags': ['day'],
                          'reference': ref, 'samples': copy.deepcopy(ref), 'corridor_half_width_m': 1.5}]
    return d


def score(d):
    from autofleet_evaluation.engine import evaluate
    from autofleet_evaluation.models import EvaluationInput
    return evaluate(EvaluationInput.model_validate(d))


def test_perfect_detections_have_defined_denominators_and_ap():
    r = score(image_input())
    assert r['perception']['precision'] == r['perception']['recall'] == r['perception']['map50'] == 1
    assert r['perception']['ground_truth_count'] == 1
    assert r['slices']['tag:night']['perception']['tp'] == 1
    assert r['gate']['passed'] is True


def test_duplicate_wrong_class_and_missing_outputs_become_failures():
    d = image_input(); f = d['frames'][0]
    f['predictions'] += [copy.deepcopy(f['predictions'][0]), {'label': 'truck', 'box': [0, 0, .5, .5], 'score': .8}]
    r = score(d)
    assert (r['perception']['tp'], r['perception']['fp'], r['perception']['fn']) == (1, 2, 0)
    assert r['perception']['per_class']['truck']['ap50'] is None
    d['frames'][0]['predictions'] = []
    r = score(d)
    assert r['perception']['recall'] == 0 and r['gate']['passed'] is False
    assert r['failures'][0]['reason'] == 'false_negative'


def test_ap_uses_confidence_order_not_final_precision():
    d = image_input()
    d['frames'][0]['predictions'].append({'label': 'car', 'box': [.7, .7, .9, .9], 'score': .95})
    assert score(d)['perception']['map50'] == pytest.approx(.5)


def test_unlabelled_and_empty_gt_do_not_manufacture_accuracy():
    d = image_input(); d['frames'][0]['ground_truth'] = None
    r = score(d)
    assert r['perception']['labelled_frames'] == 0
    assert r['perception']['recall'] is None and r['gate']['passed'] is False
    d['frames'][0]['ground_truth'] = []
    r = score(d)
    assert r['perception']['fp'] == 1 and r['perception']['recall'] is None


@pytest.mark.parametrize('mutation', ['nan', 'outside_box', 'duplicate_frame', 'unknown_field', 'zero_iou'])
def test_invalid_or_ambiguous_data_rejected(mutation):
    d = image_input()
    if mutation == 'nan': d['frames'][0]['t_s'] = math.nan
    elif mutation == 'outside_box': d['frames'][0]['ground_truth'][0]['box'] = [0, 0, 2, 1]
    elif mutation == 'duplicate_frame': d['frames'].append(copy.deepcopy(d['frames'][0]))
    elif mutation == 'unknown_field': d['framez'] = []
    else: d['config'] = {'iou_threshold': 0}
    with pytest.raises(ValueError): score(d)


def test_trajectory_geometry_and_wrapped_parking_angle():
    d = trajectory_input(); t = d['trajectories'][0]
    for p in t['samples']: p['y_m'] = .5
    t['reference'][-1]['yaw_rad'] = math.pi - .1
    t['samples'][-1]['yaw_rad'] = -math.pi + .1
    r = score(d)['planning']
    assert r['ade_m'] == pytest.approx(.5)
    assert r['fde_m'] == pytest.approx(.5)
    assert r['max_cross_track_m'] == pytest.approx(.5)
    assert r['parking_heading_error_rad'] == pytest.approx(.2)
    assert r['max_abs_jerk_mps3'] == 0


def test_disc_collision_ttc_and_missing_trajectory_coverage():
    d = trajectory_input(); t = d['trajectories'][0]
    t['samples'][0]['obstacles'] = [{'actor_id': 'other', 'x_m': 4, 'y_m': 0,
                                   'vx_mps': 0, 'vy_mps': 0, 'radius_m': .5}]
    t['ego_radius_m'] = .5
    assert score(d)['planning']['min_ttc_s'] == pytest.approx(3)
    t['samples'][0]['obstacles'][0]['x_m'] = .5
    assert score(d)['planning']['collision_scenarios'] == 1
    t['samples'] = t['samples'][:2]
    d['gates'] = [{'metric': 'planning.coverage', 'op': '>=', 'value': 1}]
    r = score(d)
    assert r['planning']['coverage'] == pytest.approx(2/3)
    assert r['gate']['passed'] is False


def test_missing_or_undefined_metric_gate_is_blocked():
    d = image_input(); d['gates'] = [{'metric': 'planning.fde_m', 'op': '<=', 'value': 1}]
    r = score(d)
    assert r['gate']['checks'][0]['status'] == 'unavailable'
    assert not r['gate']['passed']


def test_no_gates_means_unconfigured_not_release_approval():
    d = image_input(); d['gates'] = []
    assert score(d)['gate']['status'] == 'unconfigured'


def test_repeated_or_unsorted_trajectory_time_is_rejected():
    d = trajectory_input(); d['trajectories'][0]['samples'][1]['t_s'] = 0
    with pytest.raises(ValueError): score(d)


def test_custom_iou_is_not_mislabelled_as_ap50():
    d = image_input(); d['config'] = {'iou_threshold': .75}
    r = score(d)
    assert r['perception']['map50'] is None
    assert r['perception']['map_at_iou'] == 1


def test_incomplete_trajectory_blocks_release_even_without_coverage_gate():
    d = trajectory_input(); d['trajectories'][0]['samples'] = d['trajectories'][0]['samples'][1:]
    d['gates'] = [{'metric': 'planning.fde_m', 'op': '<=', 'value': 1}]
    assert not score(d)['gate']['passed']
