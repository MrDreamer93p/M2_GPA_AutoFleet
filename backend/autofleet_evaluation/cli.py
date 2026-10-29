from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .adapters import import_tracks
from .models import EvaluationInput
from .reports import compare_reports, failures_csv, html_report
from .scenarios import load_catalog, materialize
from .store import EvaluationStore


def load(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description='AutoFleet algorithm evaluation. No model benchmark is run implicitly.')
    parser.add_argument('--db', default='data/results/evaluation.sqlite3')
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run'); run.add_argument('input'); run.add_argument('--output', required=True)
    run.add_argument('--html'); run.add_argument('--failures-csv')
    compare = commands.add_parser('compare'); compare.add_argument('baseline'); compare.add_argument('candidate'); compare.add_argument('--output', required=True)
    tracks = commands.add_parser('import-tracks'); tracks.add_argument('--truth', required=True); tracks.add_argument('--predictions', required=True)
    for option in ('scenario-id', 'suite-version', 'candidate-version', 'dataset-id', 'revision', 'output'):
        tracks.add_argument('--' + option, required=True)
    tracks.add_argument('--label-map', help='JSON object with an explicit common class mapping')
    scenario = commands.add_parser('scenario'); scenario.add_argument('scenario_id')
    scenario.add_argument('--catalog', default='scenarios/evaluation/catalog.json'); scenario.add_argument('--parameters')
    scenario.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'run':
            report = EvaluationStore(args.db).create_run(EvaluationInput.model_validate(load(args.input)))
            write(args.output, report)
            if args.html: write(args.html, html_report(report))
            if args.failures_csv: write(args.failures_csv, failures_csv(report))
            print(json.dumps({'run_id': report['run_id'], 'gate': report['gate']['status']}))
            return 0 if report['gate']['passed'] else 2
        if args.command == 'compare':
            store = EvaluationStore(args.db)
            report = compare_reports(store.get_run(args.baseline), store.get_run(args.candidate))
            write(args.output, report)
            return 0 if report['regression_free'] and report['candidate_gate']['passed'] else 2
        if args.command == 'import-tracks':
            dataset = import_tracks(load(args.truth), load(args.predictions), scenario_id=args.scenario_id,
                                    suite_version=args.suite_version, candidate_version=args.candidate_version,
                                    dataset_id=args.dataset_id, revision=args.revision,
                                    label_map=load(args.label_map) if args.label_map else None)
            write(args.output, dataset.model_dump(by_alias=True, mode='json')); return 0
        if args.command == 'scenario':
            write(args.output, materialize(load_catalog(Path(args.catalog)), args.scenario_id,
                                          load(args.parameters) if args.parameters else None)); return 0
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print(f'Evaluation error: {exc}', file=sys.stderr); return 1


if __name__ == '__main__': raise SystemExit(main())
