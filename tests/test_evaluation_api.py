import copy
import csv
import io

from fastapi.testclient import TestClient

from test_evaluation_metrics import image_input


def client_for(path):
    from autofleet_evaluation.api import create_app
    return TestClient(create_app(path))


def test_run_persistence_exports_and_version_comparison(tmp_path):
    client = client_for(tmp_path / 'eval.sqlite3')
    bad = image_input(); bad['frames'][0]['predictions'] = []
    a = client.post('/api/v1/evaluation/runs', json=bad)
    assert a.status_code == 201, a.text
    ar = a.json()
    good = image_input(); good['candidate_version'] = 'fixed'
    br = client.post('/api/v1/evaluation/runs', json=good).json()
    assert client.get('/api/v1/evaluation/runs').json()['items'][0]['run_id'] == br['run_id']
    assert client_for(tmp_path / 'eval.sqlite3').get(f"/api/v1/evaluation/runs/{ar['run_id']}").json() == ar
    assert client.get(f"/api/v1/evaluation/runs/{ar['run_id']}/input").json() == bad | {'config': {'iou_threshold': .5, 'score_threshold': 0, 'alignment_tolerance_s': .001}, 'source': bad['source'] | {'annotation_revision': 'unspecified', 'prediction_source': 'unspecified'}}
    comparison = client.get('/api/v1/evaluation/compare', params={'baseline': ar['run_id'], 'candidate': br['run_id']})
    assert comparison.status_code == 200
    assert comparison.json()['metrics']['perception.recall']['delta'] == 1
    assert comparison.json()['slices']['tag:night']['perception.recall']['delta'] == 1
    assert client.get(f"/api/v1/evaluation/runs/{ar['run_id']}/report.html").status_code == 200
    rows = list(csv.reader(io.StringIO(client.get(f"/api/v1/evaluation/runs/{ar['run_id']}/failures.csv").text)))
    assert rows[0][0] == 'scenario_id' and rows[1][2] == 'false_negative'


def test_changed_truth_or_policy_cannot_be_compared(tmp_path):
    c = client_for(tmp_path / 'db')
    data = image_input(); a = c.post('/api/v1/evaluation/runs', json=data).json()
    data['frames'][0]['ground_truth'][0]['label'] = 'truck'
    b = c.post('/api/v1/evaluation/runs', json=data).json()
    assert c.get('/api/v1/evaluation/compare', params={'baseline': a['run_id'], 'candidate': b['run_id']}).status_code == 409
    data = image_input(); data['gates'][0]['value'] = .5
    b = c.post('/api/v1/evaluation/runs', json=data).json()
    assert c.get('/api/v1/evaluation/compare', params={'baseline': a['run_id'], 'candidate': b['run_id']}).status_code == 409


def test_defects_reopen_without_rewriting_historical_reports(tmp_path):
    c = client_for(tmp_path / 'db'); data = image_input(); data['frames'][0]['predictions'] = []
    first = c.post('/api/v1/evaluation/runs', json=data).json()
    defect = c.get('/api/v1/evaluation/defects').json()['items'][0]
    response = c.patch(f"/api/v1/evaluation/defects/{defect['defect_id']}", json={'status': 'resolved', 'note': 'fix pending regression'})
    assert response.status_code == 200
    assert response.json()['status'] == 'resolved'
    data['candidate_version'] = 'still-bad'; second = c.post('/api/v1/evaluation/runs', json=data).json()
    reopened = c.get('/api/v1/evaluation/defects').json()['items'][0]
    assert reopened['status'] == 'open' and reopened['occurrences'] == 2
    assert len(reopened['history']) == 3
    assert c.get(f"/api/v1/evaluation/runs/{first['run_id']}").json() == first
    assert second['run_id'] != first['run_id']


def test_bad_requests_missing_ids_and_html_escaping(tmp_path):
    c = client_for(tmp_path / 'db')
    assert c.get('/api/v1/evaluation/runs/missing').status_code == 404
    assert c.post('/api/v1/evaluation/runs', json={}).status_code == 422
    assert c.patch('/api/v1/evaluation/defects/missing', json={'status': 'resolved', 'note': 'checked'}).status_code == 404
    data = image_input(); data['candidate_version'] = '<script>alert(1)</script>'
    data['frames'][0]['ground_truth'][0]['label'] = '=HYPERLINK("evil")'
    r = c.post('/api/v1/evaluation/runs', json=data).json()
    html = c.get(f"/api/v1/evaluation/runs/{r['run_id']}/report.html").text
    assert '<script>' not in html and '&lt;script&gt;' in html
    csv_text = c.get(f"/api/v1/evaluation/runs/{r['run_id']}/failures.csv").text
    assert "'=HYPERLINK" in csv_text


def test_false_positive_defects_are_explicit_frame_class_groups(tmp_path):
    c = client_for(tmp_path/'db'); data = image_input()
    data['frames'][0]['ground_truth'] = []
    data['frames'][0]['predictions'] = [
        {'label': 'car', 'score': .9, 'box': [0, 0, .1, .1]},
        {'label': 'car', 'score': .8, 'box': [.8, .8, .9, .9]}]
    c.post('/api/v1/evaluation/runs', json=data)
    defects = c.get('/api/v1/evaluation/defects').json()['items']
    assert len(defects) == 1 and defects[0]['failure']['group_size'] == 2
    defect_id = defects[0]['defect_id']
    c.patch(f'/api/v1/evaluation/defects/{defect_id}', json={'status': 'resolved', 'note': 'review group'})
    data['frames'][0]['predictions'].pop(0)
    c.post('/api/v1/evaluation/runs', json=data)
    defects = c.get('/api/v1/evaluation/defects').json()['items']
    assert len(defects) == 1 and defects[0]['defect_id'] == defect_id
    assert defects[0]['status'] == 'open' and defects[0]['failure']['group_size'] == 1


def test_update_old_defect_is_not_limited_to_latest_listing(tmp_path):
    from autofleet_evaluation.store import EvaluationStore
    store = EvaluationStore(tmp_path/'db')
    with store.connection() as db:
        db.executemany('INSERT INTO defects VALUES (?,?,?,?,?,?)',
                       [(str(i), 'open', 'first', 'last', 1, '{}') for i in range(1001)])
    result = store.update_defect('0', 'resolved', 'old defect reviewed')
    assert result['defect_id'] == '0' and result['status'] == 'resolved'
    assert result['history'][0]['note'] == 'old defect reviewed'


def test_parallel_run_persistence_keeps_defect_occurrences_atomic(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from autofleet_evaluation.models import EvaluationInput
    from autofleet_evaluation.store import EvaluationStore
    store = EvaluationStore(tmp_path/'db')
    data = image_input(); data['frames'][0]['predictions'] = []
    dataset = EvaluationInput.model_validate(data)
    with ThreadPoolExecutor(max_workers=4) as pool:
        reports = list(pool.map(lambda _: store.create_run(dataset), range(8)))
    assert len({r['run_id'] for r in reports}) == 8
    defect = store.defects()[0]
    assert defect['occurrences'] == 8 and len(defect['history']) == 8
    for report in reports:
        assert store.get_run(report['run_id']) == report
