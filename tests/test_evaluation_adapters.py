import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_evaluation_metrics import image_input


def tracks(kind='mtid-annotation'):
    return {'source_type': kind, 'source': 'unit-source', 'width': 100, 'height': 100, 'fps': 10,
            'frames': [{'frame': 0, 't': 0, 'detections': [{'label': 'Car', 'bbox': [10, 20, 30, 40], 'confidence': .8}]}]}


def convert(gt, predictions):
    from autofleet_evaluation.adapters import import_tracks
    return import_tracks(gt, predictions, scenario_id='unit', suite_version='1', candidate_version='model',
                         dataset_id='independent-video', revision='1', label_map={'Car': 'car'})


def test_track_import_separates_annotations_and_predictions():
    result = convert(tracks(), tracks('opencv-darknet'))
    assert result.frames[0].ground_truth[0].box == (.1, .2, .4, .6)
    assert result.frames[0].predictions[0].label == 'car'
    assert result.source.kind == 'dataset'
    assert 'opencv-darknet' in result.source.prediction_source


@pytest.mark.parametrize('condition', ['annotation_leak', 'time_mismatch', 'duplicate', 'resolution', 'missing_frame', 'bad_confidence'])
def test_track_import_rejects_ambiguous_inputs(condition):
    gt, pred = tracks(), tracks('opencv-darknet')
    if condition == 'annotation_leak': pred['frames'][0]['detections'][0]['source'] = 'mtid-annotation'
    elif condition == 'time_mismatch': pred['frames'][0]['t'] = 1
    elif condition == 'duplicate': pred['frames'].append(copy.deepcopy(pred['frames'][0]))
    elif condition == 'resolution': pred['width'] = 200
    elif condition == 'missing_frame': pred['frames'] = []
    else: pred['frames'][0]['detections'][0]['confidence'] = 2
    with pytest.raises(ValueError): convert(gt, pred)


def test_cli_blocked_gate_has_nonzero_exit_and_writes_report(tmp_path):
    data = image_input(); data['frames'][0]['predictions'] = []
    source = tmp_path/'input.json'; source.write_text(json.dumps(data))
    output = tmp_path/'report.json'
    result = subprocess.run([sys.executable, 'tools/evaluate_algorithms.py', '--db', str(tmp_path/'db'),
                             'run', str(source), '--output', str(output)], capture_output=True, text=True)
    assert result.returncode == 2, result.stderr
    assert not json.loads(output.read_text())['gate']['passed']


def test_scenario_catalog_compiles_without_executing_a_simulator():
    from autofleet_evaluation.scenarios import load_catalog, materialize
    catalog = load_catalog(Path('scenarios/evaluation/catalog.json'))
    assert {s['category'] for s in catalog['scenarios']} == {'following', 'cut_in', 'pedestrian_crossing', 'parking'}
    sample = materialize(catalog, 'parking_reverse', {'target_x_m': 4})
    assert sample['trajectory']['reference'][-1]['x_m'] == 4
    assert 'samples' not in sample['trajectory']
    assert sample['status'] == 'scenario_definition_not_execution'
