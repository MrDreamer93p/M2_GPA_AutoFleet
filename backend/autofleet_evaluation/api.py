from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import Field

from .models import EvaluationInput, Model
from .examples import example_input
from .reports import compare_reports, failures_csv, html_report
from .store import EvaluationStore


class DefectUpdate(Model):
    status: Literal['open', 'resolved', 'ignored']
    note: str = Field(min_length=1, max_length=2000)


def default_db_path():
    return os.environ.get('AUTOFLEET_EVALUATION_DB', str(Path(__file__).resolve().parents[2] / 'data/results/evaluation.sqlite3'))


def create_router(store: EvaluationStore):
    router = APIRouter(prefix='/api/v1/evaluation', tags=['algorithm-evaluation'])

    def read_run(run_id, **kwargs):
        try: return store.get_run(run_id, **kwargs)
        except KeyError: raise HTTPException(404, 'Evaluation run not found')

    @router.get('/health')
    def health():
        return {'status': 'ok', 'mode': 'evaluation', 'schema': 'autofleet.evaluation.v1'}

    @router.get('/example')
    def example():
        return example_input()

    @router.post('/runs', status_code=201)
    def run(dataset: EvaluationInput):
        return store.create_run(dataset)

    @router.get('/runs')
    def runs(limit: int = Query(100, ge=1, le=500)):
        return {'items': store.list_runs(limit)}

    @router.get('/runs/{run_id}')
    def detail(run_id: str): return read_run(run_id)

    @router.get('/runs/{run_id}/input')
    def inputs(run_id: str): return read_run(run_id, inputs=True)

    @router.get('/runs/{run_id}/report.html', response_class=HTMLResponse)
    def report(run_id: str): return html_report(read_run(run_id))

    @router.get('/runs/{run_id}/failures.csv')
    def failures(run_id: str):
        return Response(failures_csv(read_run(run_id)), media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': 'attachment; filename="failures.csv"'})

    @router.get('/compare')
    def compare(baseline: str, candidate: str):
        a, b = read_run(baseline), read_run(candidate)
        try: return compare_reports(a, b)
        except ValueError as e: raise HTTPException(409, str(e))

    @router.get('/defects')
    def defects(status: Literal['open', 'resolved', 'ignored'] | None = None, run_id: str | None = None):
        return {'items': store.defects(status, run_id)}

    @router.patch('/defects/{defect_id}')
    def update_defect(defect_id: str, update: DefectUpdate):
        try: return store.update_defect(defect_id, update.status, update.note)
        except KeyError: raise HTTPException(404, 'Defect not found')

    return router


def create_app(db_path=None):
    app = FastAPI(title='AutoFleet Algorithm Evaluation', version='1.0.0')
    app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['GET', 'POST', 'PATCH'], allow_headers=['Content-Type'])
    app.include_router(create_router(EvaluationStore(db_path or default_db_path())))
    return app


app = create_app()
