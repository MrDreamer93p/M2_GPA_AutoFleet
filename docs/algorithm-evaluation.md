# Algorithm evaluation

## What is implemented

The evaluation package is independent of MQTT and video polling. Both the fleet
backend and the evaluation-only API call the same metric functions and SQLite store.
The workbench imports JSON, executes a run, displays gates, scenario/ODD slices,
trajectory overlays and failure samples, exports reports and records defect reviews.

| Capability | Implementation | Validation boundary |
| --- | --- | --- |
| Test cases and input contracts | Versioned scenario catalog, typed image/trajectory input, explicit gates | Example scenarios are definitions, not executed results |
| Perception evaluation | Class-aware confidence-ranked matching, AP at a configured IoU, precision/recall/F1, slices and FP/FN samples | Requires independent annotations; no detector is trained here |
| Planning and parking evaluation | ADE/FDE, corridor deviation, acceleration/jerk, sampled-disc collision and constant-velocity TTC, final pose errors | Not a vehicle dynamics or safety certification model |
| Replay integration | Existing pixel-xywh tracks converted to normalized evaluation input | MTID annotations are truth; independently generated detector outputs are required |
| Software-in-loop input | Optional fixed-step synchronous CARLA collector and replaceable controller | External CARLA execution was not performed for this change |
| Reports and defects | Immutable inputs/reports, hashes, HTML/CSV exports, recurrence and review history | Marking a defect resolved never changes the original score |
| Continuous checks | pytest, Python compilation, JS syntax and Compose configuration on GitHub Actions | No model benchmark, load experiment or hardware test |

No ROS2 implementation, HIL bench, real-road result, NCAP or SOTIF conformance is
implied by the data schema or adapters. A hardware recording can be normalized into
the input contract, but the `hardware_recording` source tag alone is not HIL evidence.

## Input contract

`GET /api/v1/evaluation/example` returns an explicitly synthetic, intentionally
imperfect fixture. `GET /openapi.json` documents the complete typed schema.

Required top-level fields: `schema: autofleet.evaluation.v1`, `suite_version`,
`candidate_version`, `source` with `kind/dataset/revision`, and at least one image
frame or trajectory. Record annotation revision and prediction source when known.
Unknown fields, invalid coordinates, non-finite numbers, repeated frame identities
and non-increasing trajectory timestamps are rejected rather than silently skipped.

Image frames contain `scenario_id`, `frame_id`, `t_s`, optional ODD `tags`,
`ground_truth` and `predictions`. Boxes are **normalized xyxy**, not pixel xywh.
Predictions require confidence `score` in [0,1]. `ground_truth: []` means a labelled
negative frame, so predictions are false positives. `ground_truth: null` means
unlabelled, excluded from accuracy denominators and explicitly counted. An empty
prediction list on a positive frame is a miss. Matching is per frame, class-aware,
one-to-one and ordered by confidence, with deterministic ties.

`config.iou_threshold` defaults to 0.5 and `score_threshold` to 0. At IoU 0.5 the
report includes `map50`/`ap50`; for another IoU these names are unavailable and the
values are exposed as `map_at_iou`/`ap_at_iou`. AP uses all-points precision-envelope
integration; classes with no positive truth have undefined AP and are excluded
from the mean, while their false positives remain in aggregate precision. This is
not COCO mAP50:95. Scores reflect the explicitly applied confidence cutoff.

Trajectory fields: `scenario_id`, `kind` (`driving` or `parking`), `reference` and
`samples`, each with `t_s/x_m/y_m/yaw_rad/speed_mps`. Units are seconds, metres,
radians and signed longitudinal metres/second. Observed samples may include
`obstacles` with IDs, positions, velocities and radii, and externally recorded
`collision` flags. Reference/sample alignment is one-to-one within
`alignment_tolerance_s` (default 1 ms); no missing prediction is interpolated away.
Incomplete coverage is an automatic blocking check. FDE is unavailable without
an observed final reference timestamp. ADE/FDE are averaged per scenario; coverage
is weighted by reference sample count. Jerk uses finite differences at interval
midpoints; fewer than three samples cannot define jerk. Corridor distance is the
minimum distance to the reference polyline, independent of timing error. Parking
success requires full coverage, final position/heading tolerances and no collision.

Collision checks use circumscribed planar discs at recorded samples. TTC solves
the first contact time for constant-velocity discs; it is unavailable when there
is no projected future contact or no obstacle data. Neither metric detects every
between-frame collision or models braking capability. `min_ttc_s: null` is not a
numeric infinity. Gates requiring unavailable values are blocked.

## Gates and comparisons

`gates` is a list of `{metric, op, value}` with `op` equal to `>=` or `<=`.
Paths include `perception.recall`, `planning.fde_m`, or
`slices.tag:night.perception.recall`. The example 0.9 precision/recall values and
scenario tolerances are configurable demonstration policies, not industry limits.
An absent metric or undefined denominator blocks a gate. With no gates, status is
`unconfigured`, not a release approval.

Every run stores input, truth and policy SHA-256 digests and engine version.
Version comparison requires identical truth/suite/source, sampling contract and
evaluation policy; model predictions and `candidate_version` may change. It reports
absolute deltas, per-slice regressions and candidate gate status. Regression-free
does not automatically mean accepted: the candidate must also pass its gates.
Review the metric directions and safety model before interpreting these reports
as release decisions. Tags are selection predicates, not proof of scenario coverage.

## Existing video and MTID integration

The existing `tools/generate_highway_vehicle_tracks.py` is an OpenCV Darknet
inference entry point. Its new outputs declare detector source and weights SHA-256.
Run it on the **same video** used for the annotations, with your locally available
model config, weights and names. Nothing downloads or executes a model implicitly.

```powershell
python tools/generate_highway_vehicle_tracks.py --video frontend/assets/mtid/mtid-r1-infrastructure-0.mp4 --output data/results/r1-predictions.json
python tools/evaluate_algorithms.py import-tracks --truth frontend/assets/mtid/mtid-r1-infrastructure-0-tracks.json --predictions data/results/r1-predictions.json --scenario-id mtid_r1 --suite-version mtid-v1 --candidate-version yolov4-tiny-local --dataset-id mtid-infra-0 --revision local-clip-v1 --output data/results/mtid-input.json
python tools/evaluate_algorithms.py run data/results/mtid-input.json --output data/results/mtid-report.json --html data/results/mtid-report.html --failures-csv data/results/mtid-failures.csv
```

The importer rejects annotation-sourced predictions (including per-detection
`source: mtid-annotation`), missing/duplicate frames, mismatched timestamps,
dimensions or frame rate. It does not silently equate `Car`, `car`, `Van` and
`truck`: pass `--label-map classes.json` with an explicitly reviewed mapping for
both annotation and prediction labels. Ground-truth provenance includes the
mapping digest. Review that frames actually describe the same source, camera,
crop and temporal segment; matching IDs alone cannot prove this.

The old MTID display tracks are **annotations**, not detector accuracy evidence.
Do not copy them into the prediction field to generate a high score.

## Scenario catalog and CARLA collector

`scenarios/evaluation/catalog.json` contains following, cut-in, pedestrian-crossing
and reverse-parking definitions. Each defines ODD tags, actors, a fixed timestep,
reference path and acceptance checks. Coordinates are templates: override them
with valid spawn/path positions on your selected map. `scenario` only materializes
a definition; it never invents an observed trajectory or an evaluation result.

```powershell
python tools/evaluate_algorithms.py scenario adjacent_cut_in --parameters site-parameters.json --output data/results/cut-in-scenario.json
# On a machine with an already-running dedicated CARLA Town03 server and its matching Python client:
python tools/run_carla_evaluation.py data/results/cut-in-scenario.json --candidate-version controller-v2 --controller my_controller:step --output data/results/cut-in-observed.json
python tools/evaluate_algorithms.py run data/results/cut-in-observed.json --output data/results/cut-in-report.json
```

A controller callable receives `{t_s, ego, target, reference, dt_s}` and returns
finite bounded `{throttle, brake, steer, reverse}`. The default route follower is a
small replaceable reference controller, not an obstacle-avoidance or production
driving algorithm. Ego controls influence subsequent CARLA states; actor truth
comes from world transforms and velocities. The adapter samples actor geometry,
does not synthesize detection predictions and does not replace the existing video
worker. External perception evaluations enter through the independent label path.

The adapter refuses a mismatched map rather than silently loading a different
world; uses one synchronous fixed-step client; restores weather/world settings
and removes only the actors it created, including on controller failure. Run with
exclusive ownership of a dedicated server; start/reset the environment for each
candidate. Scenario seed is recorded; no Traffic Manager or random actor process
is started here. Different server/client/physics settings may change results.
The CARLA unit tests use a controllable fake world to check lifecycle and schema;
they are not proof of real CARLA execution or deterministic physics.

Official references: [synchronous mode and timestep](https://carla.readthedocs.io/en/latest/adv_synchrony_timestep/),
[CARLA Python API](https://carla.readthedocs.io/en/latest/python_api/),
[ScenarioRunner metric separation](https://scenario-runner.readthedocs.io/en/latest/metrics_module/).

## API, reports and defects

| Method / route under `/api/v1/evaluation` | Purpose |
| --- | --- |
| `GET /health`, `GET /example` | Readiness and a synthetic schema fixture |
| `POST /runs` | Validate, evaluate and atomically persist an immutable run |
| `GET /runs`, `GET /runs/{id}` | Recent runs and full report |
| `GET /runs/{id}/input` | Exact validated input used to calculate the result |
| `GET /runs/{id}/report.html`, `/failures.csv` | Escaped standalone report and spreadsheet-safe failure export |
| `GET /compare?baseline={id}&candidate={id}` | Comparable-input version deltas; 409 on incompatible truth/policy |
| `GET /defects?status=open&run_id={id}` | Defects and occurrence/review history |
| `PATCH /defects/{id}` | `{status: open/resolved/ignored, note: ...}` |

Repeated failures with the same truth and frame/reason/class/object identity append
an occurrence and reopen resolved defects. Ignored defects remain ignored but
still accumulate occurrences. A successful run does not automatically resolve
historical defects; a person records the review and fix reference. HTML escapes
input values; CSV protects formula-leading text. SQLite WAL and short transactions
keep runs and failure occurrences atomic. Runtime files live under ignored
`data/results/`; keep them out of source control. `AUTOFLEET_EVALUATION_DB` selects
the database (bind-mounted to the existing data directory in Docker).

CLI run/compare exit codes: `0` accepted and, for comparison, regression-free;
`2` blocked/unconfigured/regressed; `1` invalid input or operational error.

## Development checks

```powershell
python -m pip install -r requirements-test.txt
python -m pytest -q
python -m compileall -q backend tools/evaluate_algorithms.py tools/run_carla_evaluation.py
node --check frontend/evaluation.js
docker compose -f infra/compose.yml --profile evaluation config --quiet
```

These are correctness and integration checks against explicit synthetic fixtures,
not effectiveness experiments. No real CARLA, detector benchmarking, HIL or
performance experiment is required by CI or claimed by the implementation.
