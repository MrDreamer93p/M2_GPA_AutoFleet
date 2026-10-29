# Executed algorithm evaluation — 2026-10-29

## Real annotated video

`mtid-20261029.json` records independent YOLOv4-tiny inference on the three existing
MTID video clips, 180 frames each. The 540 frames contain 4,686 vehicle annotations.
`Car/Van/Lorry/Bus` and detector vehicle labels are explicitly mapped to the vehicle
superclass. Matching uses IoU 0.5 and the same truth and gate policy for both
candidates. Model source URLs, weights hashes, input/truth/policy digests, overall
and per-view scores and comparison deltas are included.

| Detection cutoff | TP | FP | FN | Precision | Recall | AP50 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.18 | 1,539 | 692 | 3,147 | 0.6898 | 0.3284 | 0.2658 |
| 0.35 | 889 | 271 | 3,797 | 0.7664 | 0.1897 | 0.1655 |

The second configuration trades recall for precision. Version comparison detects
the recall/AP regressions, and both candidates are blocked by the illustrative
0.9 precision/recall policy. These are detector measurements on these clips, not
an improvement claim about the evaluator or a general model benchmark. The live
API also returned the stored reports, comparisons and defect groups successfully.

The detector entry point requires `requirements-perception.txt`; the tested
environment was Python 3.11, OpenCV 4.11.0 and NumPy 2.2.6.

## Live CARLA

`carla-20261029.json` records a real CARLA 0.9.16 Windows server and matching Python
client, Town10HD_Opt, fixed 50 ms steps, 20 settling ticks and disabled rendering.
Each following, cut-in, pedestrian and parking case produced 81 observed samples
for a 4-second reference horizon. Every case reached full timestamp coverage,
restored prior world settings and left zero created vehicles after cleanup.

The reference follower passed the following case's configured collision/corridor
checks; other cases produced tracking, pedestrian-proximity/comfort and parking
tolerance findings. Successful harness execution is distinct from controller
acceptance. Exact scores and failures are retained in the JSON evidence.

Live execution exposed an actor-cache initialization defect: before the first
world tick, newly spawned actors appeared at (0,0), producing false collisions and
corridor departures. The collector now brakes and advances initialization ticks
before starting the reference timeline. Reproducing regression tests cover both
snapshot priming and settling. Failed numeric gates now also create failure
records, including FDE failures without a geometry-derived failure category.

Reproduce against a dedicated server already running Town10HD_Opt:

```powershell
python -m pip install -r requirements-test.txt -r requirements-carla.txt
python tools/run_carla_evaluation.py docs/test-evidence/algorithm-evaluation/carla-scenarios/adjacent_cut_in-scenario.json --candidate-version reference-follower-v1 --output data/results/cut-in-input.json
python tools/evaluate_algorithms.py run data/results/cut-in-input.json --output data/results/cut-in-report.json --html data/results/cut-in-report.html
```

The four executed scenario definitions are stored under `carla-scenarios/`.
Map switching on this local Windows/GPU setup exited the simulator, so verification
used the server's existing map and transformed reference/actor positions onto a
valid straight driving lane. `-nullrhi` was also unsuccessful here; normal
offscreen rendering followed by `world.apply_settings(no_rendering_mode=True)`
worked. The collector never changes maps implicitly.

## Checks

The complete Python functional suite passes 41 tests, including real-run regression
coverage and atomic concurrent run/defect persistence. GitHub Actions exercises
Python 3.11 and 3.12. HIL, real-road driving and standards certification were not
part of these software integration runs.
