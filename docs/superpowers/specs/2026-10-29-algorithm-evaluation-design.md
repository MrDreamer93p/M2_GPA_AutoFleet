# Algorithm evaluation workbench

The user requested a direct implementation and GitHub PR against the autonomous-driving algorithm test-development JD, without performance experiments. Implementation decisions below are agent decisions; no measured algorithm improvement is claimed.

## Approach

Extend the existing Python/FastAPI and static frontend rather than replace the fleet-control system. Implement an offline evaluation core first, then expose it through CLI, API, persisted reports, defect workflow and a separate workbench page. Add a real CARLA synchronous collector as an optional input adapter. The alternative, making CARLA mandatory for every evaluation, would prevent reviewing imported real annotations and detector outputs without a simulator installation. Implemented adapters do not establish HIL, road-test, SOTIF or NCAP validation.

## Contracts

- Explicit `autofleet.evaluation.v1` dataset, dataset/suite/candidate versions and source provenance. Labelled image frames have independent ground truth and predictions in normalized xyxy boxes, plus scenario/frame identity and ODD tags. Empty predictions count as misses; absent labels do not produce accuracy.
- Detection: confidence-ranked one-to-one class-aware IoU matching, precision/recall/F1, per-class AP50 and mAP50 over classes with ground truth; per-scenario and ODD slices; FP/FN failure cases. AP50 is not COCO mAP50:95.
- Planning: time-aligned trajectory/reference samples, ADE/FDE, polyline cross-track error, acceleration/jerk, sampled-disc overlap and constant-velocity disc TTC; final parking position and wrapped heading error. Missing reference coverage cannot pass. Collision is sampled geometry, not vehicle safety certification.
- Versioned gate policy: explicit metric path/operator/threshold. Missing or undefined gate metric blocks acceptance. Overall and slice regression comparisons require the same evaluation truth, source, config and gates, while predictions/model version may differ.
- Immutable run inputs and reports in SQLite, content digests, defect identity/history and recurrence on later runs. User acknowledgement or resolution never changes historical metrics. HTML/CSV exports escape user data.
- MTID annotations in existing assets are ground truth, not detector predictions. Track importer rejects annotation-sourced predictions, mismatched frames/timestamps or mixed resolutions, and requires declared provenance.
- Scenario catalog includes following, cut-in, pedestrian crossing and parking parameter sets, tags, reference trajectories and gate policies. No fabricated benchmark results. Generated examples and unit fixtures are labelled synthetic.
- Optional CARLA collector uses fixed-step synchronous world ticks and restores settings/actors; controller plugin returns real controls. Recorded trajectory and actor geometry enter the same evaluator. No CARLA server is available for external execution in this task.
- API and frontend must be usable without MQTT for evaluation-only work; production backend includes the same router. Existing Docker mount already carries backend code/data; add explicit evaluation persistence configuration.
- CI runs Python functional tests, compilation and JavaScript syntax checks; no model downloads, load experiments or simulator required.

## Verification

Write independent arithmetic/geometry and API persistence tests before implementation. Exercise wrong classes, duplicates, empty predictions, unlabelled frames, non-finite coordinates, gaps in trajectories, unsafe comparison and repeated defects. Run CLI/API smoke checks on explicit fixtures. Do not run benchmarks or claim model effectiveness. Review entire branch before pushing a feature branch and opening a PR; do not merge.
