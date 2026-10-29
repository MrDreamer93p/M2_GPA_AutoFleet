from __future__ import annotations

from .models import Config, Frame


def iou(a, b):
    overlap = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - overlap
    return overlap / union


def average_precision(ranked: list[bool], positives: int):
    """All-points interpolated AP, at a single configured IoU threshold."""
    if positives == 0:
        return None
    tp = 0
    recalls, precisions = [0.0], [0.0]
    for n, matched in enumerate(ranked, 1):
        tp += matched
        recalls.append(tp / positives)
        precisions.append(tp / n)
    recalls.append(1.0)
    precisions.append(0.0)
    for i in range(len(precisions) - 2, -1, -1):
        precisions[i] = max(precisions[i], precisions[i + 1])
    return sum((recalls[i] - recalls[i - 1]) * precisions[i] for i in range(1, len(recalls)))


def evaluate_perception(frames: list[Frame], config: Config):
    labelled = [f for f in frames if f.ground_truth is not None]
    labels = sorted({o.label for f in labelled for o in [*(f.ground_truth or []), *f.predictions]})
    per_class, failures = {}, []
    for label in labels:
        truths = {i: [o for o in f.ground_truth if o.label == label] for i, f in enumerate(labelled)}
        used = {i: set() for i in range(len(labelled))}
        predictions = [(p.score, i, j, p) for i, f in enumerate(labelled)
                       for j, p in enumerate(f.predictions)
                       if p.label == label and p.score >= config.score_threshold]
        predictions.sort(key=lambda x: (-x[0], x[1], x[2]))
        matched = []
        for _, i, j, prediction in predictions:
            choices = [(iou(prediction.box, gt.box), k) for k, gt in enumerate(truths[i]) if k not in used[i]]
            overlap, index = max(choices, default=(0, -1))
            hit = overlap >= config.iou_threshold
            matched.append(hit)
            if hit:
                used[i].add(index)
            else:
                f = labelled[i]
                failures.append({'scenario_id': f.scenario_id, 'frame_id': f.frame_id,
                                 't_s': f.t_s, 'reason': 'false_positive', 'label': label,
                                 'object_index': j, 'score': prediction.score, 'box': list(prediction.box),
                                 'tags': f.tags})
        positives = sum(map(len, truths.values()))
        tp = sum(matched); fp = len(matched) - tp; fn = positives - tp
        for i, gt_list in truths.items():
            for j, gt in enumerate(gt_list):
                if j not in used[i]:
                    f = labelled[i]
                    failures.append({'scenario_id': f.scenario_id, 'frame_id': f.frame_id,
                                     't_s': f.t_s, 'reason': 'false_negative', 'label': label,
                                     'object_index': j, 'box': list(gt.box), 'tags': f.tags})
        per_class[label] = {'tp': tp, 'fp': fp, 'fn': fn, 'ground_truth_count': positives,
                            'precision': tp / (tp + fp) if tp + fp else None,
                            'recall': tp / positives if positives else None,
                            'ap_at_iou': average_precision(matched, positives),
                            'ap50': average_precision(matched, positives) if config.iou_threshold == .5 else None}
    tp = sum(c['tp'] for c in per_class.values())
    fp = sum(c['fp'] for c in per_class.values())
    fn = sum(c['fn'] for c in per_class.values())
    aps = [c['ap_at_iou'] for c in per_class.values() if c['ap_at_iou'] is not None]
    return {'frames': len(frames), 'labelled_frames': len(labelled), 'unlabelled_frames': len(frames) - len(labelled),
            'ground_truth_count': tp + fn, 'tp': tp, 'fp': fp, 'fn': fn,
            'precision': tp / (tp + fp) if tp + fp else None,
            'recall': tp / (tp + fn) if tp + fn else None,
            'f1': 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
            'map_at_iou': sum(aps) / len(aps) if aps else None,
            'map50': sum(aps) / len(aps) if aps and config.iou_threshold == .5 else None,
            'iou_threshold': config.iou_threshold, 'score_threshold': config.score_threshold,
            'per_class': per_class}, failures
