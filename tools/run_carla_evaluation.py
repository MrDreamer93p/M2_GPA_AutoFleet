"""Collect an external CARLA controller trace; does not launch/download a server."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from autofleet_evaluation.carla_adapter import collect, load_controller
from autofleet_evaluation.cli import write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scenario'); parser.add_argument('--candidate-version', required=True)
    parser.add_argument('--controller', default='autofleet_evaluation.carla_adapter:route_follower')
    parser.add_argument('--host', default='127.0.0.1'); parser.add_argument('--port', default=2000, type=int)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    scenario = json.loads(Path(args.scenario).read_text(encoding='utf-8-sig'))
    result = collect(scenario, load_controller(args.controller), candidate_version=args.candidate_version,
                     host=args.host, port=args.port)
    write(args.output, result.model_dump(by_alias=True, mode='json'))


if __name__ == '__main__': main()
