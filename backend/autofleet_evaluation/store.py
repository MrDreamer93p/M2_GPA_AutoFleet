from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .engine import digest, evaluate
from .models import EvaluationInput


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


class EvaluationStore:
    """Short transactions; each request has its own SQLite connection."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS runs (
                  run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, input TEXT NOT NULL, report TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS defects (
                  defect_id TEXT PRIMARY KEY, status TEXT NOT NULL, first_run TEXT NOT NULL,
                  last_run TEXT NOT NULL, occurrences INTEGER NOT NULL, failure TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS occurrences (
                  defect_id TEXT NOT NULL, run_id TEXT NOT NULL, PRIMARY KEY(defect_id, run_id));
                CREATE TABLE IF NOT EXISTS history (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, defect_id TEXT NOT NULL, at TEXT NOT NULL,
                  status TEXT NOT NULL, note TEXT NOT NULL, run_id TEXT);
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db: yield db
        finally:
            db.close()

    def create_run(self, dataset: EvaluationInput):
        report = evaluate(dataset)
        report.update(run_id=uuid.uuid4().hex, created_at=now())
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO runs VALUES (?,?,?,?)', (report['run_id'], report['created_at'],
                       encode(dataset.model_dump(by_alias=True, mode='json')), encode(report)))
            groups = {}
            for failure in report['failures']:
                # Review units are frame/reason/class groups; prediction indices are candidate-local.
                identity = {key: failure.get(key) for key in ('scenario_id', 'frame_id', 'reason', 'label')}
                defect_id = digest({'truth': report['truth_digest'], 'failure': identity})
                groups.setdefault(defect_id, []).append(failure)
            for defect_id, members in groups.items():
                failure = {key: value for key, value in members[0].items() if key not in ('object_index', 'box', 'score')}
                failure.update(group_size=len(members), examples=members[:10])
                previous = db.execute('SELECT status FROM defects WHERE defect_id=?', (defect_id,)).fetchone()
                status = 'ignored' if previous and previous['status'] == 'ignored' else 'open'
                if previous:
                    db.execute('UPDATE defects SET last_run=?,occurrences=occurrences+1,status=?,failure=? WHERE defect_id=?',
                               (report['run_id'], status, encode(failure), defect_id))
                else:
                    db.execute('INSERT INTO defects VALUES (?,?,?,?,?,?)',
                               (defect_id, status, report['run_id'], report['run_id'], 1, encode(failure)))
                db.execute('INSERT INTO occurrences VALUES (?,?)', (defect_id, report['run_id']))
                db.execute('INSERT INTO history(defect_id,at,status,note,run_id) VALUES (?,?,?,?,?)',
                           (defect_id, report['created_at'], status, 'Observed in evaluation', report['run_id']))
        return report

    def get_run(self, run_id, *, inputs=False):
        with self.connection() as db:
            row = db.execute('SELECT input,report FROM runs WHERE run_id=?', (run_id,)).fetchone()
        if row is None: raise KeyError(run_id)
        return json.loads(row['input' if inputs else 'report'])

    def list_runs(self, limit=100):
        with self.connection() as db:
            rows = db.execute('SELECT report FROM runs ORDER BY created_at DESC,run_id DESC LIMIT ?', (limit,)).fetchall()
        return [{key: report[key] for key in ('run_id', 'created_at', 'suite_version', 'candidate_version', 'source', 'gate')}
                for report in (json.loads(row['report']) for row in rows)]

    def defects(self, status=None, run_id=None):
        query = 'SELECT d.* FROM defects d WHERE 1=1'
        params = []
        if status: query += ' AND d.status=?'; params.append(status)
        if run_id:
            query += ' AND EXISTS(SELECT 1 FROM occurrences o WHERE o.defect_id=d.defect_id AND o.run_id=?)'
            params.append(run_id)
        query += ' ORDER BY d.rowid DESC LIMIT 1000'
        with self.connection() as db:
            rows = db.execute(query, params).fetchall()
            result = []
            for row in rows:
                result.append(self._defect(db, row))
        return result

    @staticmethod
    def _defect(db, row):
        item = dict(row); item['failure'] = json.loads(item['failure'])
        item['history'] = [dict(h) for h in db.execute('SELECT at,status,note,run_id FROM history WHERE defect_id=? ORDER BY id',
                                                    (item['defect_id'],))]
        return item

    def update_defect(self, defect_id, status, note):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute('UPDATE defects SET status=? WHERE defect_id=?', (status, defect_id)).rowcount
            if not changed: raise KeyError(defect_id)
            db.execute('INSERT INTO history(defect_id,at,status,note) VALUES (?,?,?,?)', (defect_id, now(), status, note))
            result = self._defect(db, db.execute('SELECT * FROM defects WHERE defect_id=?', (defect_id,)).fetchone())
        return result
