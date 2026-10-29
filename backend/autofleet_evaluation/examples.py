"""Deliberately imperfect synthetic contract fixture; never a model result."""


def example_input():
    return {'schema': 'autofleet.evaluation.v1', 'suite_version': 'synthetic-contract-v1',
            'candidate_version': 'fixture-with-one-miss',
            'source': {'kind': 'synthetic', 'dataset': 'contract-fixture', 'revision': '1',
                       'annotation_revision': 'handwritten-unit-fixture', 'prediction_source': 'handwritten-unit-fixture'},
            'frames': [
                {'scenario_id': 'fixture_following', 'frame_id': '0', 't_s': 0, 'tags': ['day', 'following'],
                 'ground_truth': [{'label': 'car', 'box': [.1, .2, .4, .6]}],
                 'predictions': [{'label': 'car', 'box': [.1, .2, .4, .6], 'score': .9}]},
                {'scenario_id': 'fixture_following', 'frame_id': '1', 't_s': .05, 'tags': ['day', 'following'],
                 'ground_truth': [{'label': 'car', 'box': [.2, .2, .5, .6]}], 'predictions': []}],
            'trajectories': [], 'gates': [{'metric': 'perception.recall', 'op': '>=', 'value': .9}]}
