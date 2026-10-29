# Algorithm Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add a usable perception/planning evaluation and regression workflow to AutoFleet.

**Architecture:** A shared evaluation package under backend is used by CLI and FastAPI. SQLite persists immutable inputs/reports and a separate defect lifecycle. A static workbench consumes these APIs; optional adapters import existing tracks and collect CARLA traces.

**Tech Stack:** Python 3.11+, Pydantic 2, FastAPI, SQLite, pytest, existing vanilla HTML/CSS/JS; optional CARLA Python API.

**Spec:** `docs/superpowers/specs/2026-10-29-algorithm-evaluation-design.md`

## Global Constraints

- No performance experiments or effectiveness claims.
- Preserve fleet control and the latest main branch multiview changes.
- Report unavailable labels/metrics explicitly; do not silently score them as passing.
- No HIL, real-road or safety-standard certification claims.
- Public feature branch PR, never merge into main automatically.

## Review Focus

- Input identity, timestamps and annotation leakage must prevent invalid evaluations and comparisons.
- Undefined metrics and incomplete trajectories must fail gates.
- SQLite operations must preserve immutable historical runs, defect recurrence and atomicity.
- UI and report exports must handle arbitrary text without HTML/CSV injection.
- CARLA must clean up actors/settings on errors; external validation remains explicitly unexecuted.

## Task 1: Validated datasets and evaluation metrics

Files: `backend/autofleet_evaluation/{models,perception,planning,engine}.py`, `tests/test_evaluation_metrics.py`.

Interfaces: `evaluate(EvaluationInput) -> dict`; strict validated image and trajectory models; stable failure records.

- [ ] Write tests for matching/AP, negative frames, unknown labels, finite geometry, trajectories/parking/TTC and missing-metric gates; run RED.
- [ ] Implement schemas, metric engines, ODD/scenario slices and gate evaluation; run GREEN.

## Task 2: Persistence, API and report comparison

Files: `backend/autofleet_evaluation/{store,api,reports}.py`, `backend/main.py`, `tests/test_evaluation_api.py`.

Interfaces: `create_app(db_path)` for evaluation-only usage; `create_router(store)` for fleet backend; immutable run IDs and report JSON/HTML/CSV; defect transitions and comparable truth digests.

- [ ] Write API tests for run persistence, missing IDs, comparability, defect history, HTML escaping and immutable results; run RED.
- [ ] Implement SQLite transactions, report exports, API router and backend inclusion; run GREEN.

## Task 3: Adapters, scenario library and command line

Files: `backend/autofleet_evaluation/{adapters,carla_adapter,cli}.py`, `tools/evaluate_algorithms.py`, `tools/run_carla_evaluation.py`, `scenarios/evaluation/`, `tests/test_evaluation_adapters.py`.

Interfaces: independently sourced track conversion to EvaluationInput; CARLA trace collection to the same planning schema; CLI evaluate/compare/import-tracks commands with correct exit status for blocked gates.

- [ ] Write tests for frame alignment, annotation-source rejection, provenance and CLI failure status; run RED.
- [ ] Implement adapters, parameterized scenario generation and examples; run GREEN. Do not launch CARLA or benchmark a model.

## Task 4: Workbench, CI and operational documentation

Files: `frontend/evaluation.{html,js,css}`, `frontend/index.html`, `.github/workflows/evaluation.yml`, `requirements-test.txt`, `infra/compose.yml`, `README.md`, `docs/algorithm-evaluation.md`.

- [ ] Reuse existing design tokens and implement upload/run/detail/compare/defect flows, trajectory SVG and slice tables.
- [ ] Run Python suite, CLI/API smoke checks, JS syntax, Docker Compose config and visual browser review if available.
- [ ] Request independent final review, fix significant issues with regressions, commit, push feature branch and create/verify PR.
