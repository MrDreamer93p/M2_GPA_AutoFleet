from __future__ import annotations

import math

from .engine import digest
from .models import EvaluationInput


def import_tracks(truth, predictions, *, scenario_id, suite_version, candidate_version,
                  dataset_id, revision, label_map=None):
    """Import existing pixel-xywh tracks, with independent annotations and detector outputs."""
    label_map = label_map or {}
    for tracks in (truth, predictions):
        for key in ('width', 'height', 'fps'):
            if not isinstance(tracks.get(key), (int, float)) or not math.isfinite(tracks[key]) or tracks[key] <= 0:
                raise ValueError(f'{key} must be positive and finite')
    if any(truth[key] != predictions[key] for key in ('width', 'height', 'fps')):
        raise ValueError('tracks must use identical resolution and frame rate')
    source_type = predictions.get('source_type', '')
    if not source_type or any(token in source_type.lower() for token in ('annotation', 'ground_truth', 'ground-truth')):
        raise ValueError('predictions require a declared detector source_type; annotations cannot be predictions')
    def indexed(tracks):
        rows = tracks.get('frames', [])
        keys = [row['frame'] for row in rows]
        if len(keys) != len(set(keys)):
            raise ValueError('duplicate track frame')
        return dict(zip(keys, rows))
    gt, pred = indexed(truth), indexed(predictions)
    if not gt or gt.keys() != pred.keys():
        raise ValueError('both tracks must explicitly contain the same nonempty frame set; empty detections are allowed')
    width, height = truth['width'], truth['height']
    def box(obj, prediction=False):
        if prediction and any(token in str(obj.get('source', '')).lower() for token in ('annotation', 'ground_truth', 'ground-truth')):
            raise ValueError('annotation-sourced detection cannot be scored as a prediction')
        x, y, w, h = obj['bbox']
        result = {'label': label_map.get(obj['label'], obj['label']),
                  'box': [x / width, y / height, (x + w) / width, (y + h) / height]}
        if prediction: result['score'] = obj['confidence']
        return result
    frames = []
    for key in sorted(gt):
        a, b = gt[key], pred[key]
        if not math.isclose(a['t'], b['t'], abs_tol=.001, rel_tol=0):
            raise ValueError(f'timestamp mismatch at frame {key}')
        frames.append({'scenario_id': scenario_id, 'frame_id': str(key), 't_s': a['t'],
                       'ground_truth': [box(x) for x in a['detections']],
                       'predictions': [box(x, True) for x in b['detections']]})
    return EvaluationInput.model_validate({
        'schema': 'autofleet.evaluation.v1', 'suite_version': suite_version, 'candidate_version': candidate_version,
        'source': {'kind': 'dataset', 'dataset': dataset_id, 'revision': revision,
                   'annotation_revision': digest({'truth': truth, 'label_map': label_map}),
                   'prediction_source': f'{source_type}:{predictions.get("model_weights_sha256", "unversioned-weights")}:{predictions.get("source", "unspecified")}'},
        'frames': frames, 'gates': [{'metric': 'perception.recall', 'op': '>=', 'value': .9},
                                  {'metric': 'perception.precision', 'op': '>=', 'value': .9}]
    })
