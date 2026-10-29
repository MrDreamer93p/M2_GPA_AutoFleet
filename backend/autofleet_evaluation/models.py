from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Number = Annotated[float, Field(allow_inf_nan=False)]
NonNegative = Annotated[Number, Field(ge=0)]
Name = Annotated[str, Field(min_length=1, max_length=160)]
Tag = Annotated[str, Field(pattern=r'^[A-Za-z0-9_:-]{1,80}$')]


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Source(Model):
    kind: Literal['synthetic', 'dataset', 'carla', 'hardware_recording']
    dataset: Name
    revision: Name
    annotation_revision: Name = 'unspecified'
    prediction_source: str = Field(default='unspecified', max_length=500)


class Box(Model):
    label: Name
    box: tuple[Number, Number, Number, Number]

    @model_validator(mode='after')
    def valid_box(self):
        x0, y0, x1, y1 = self.box
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError('box must be normalized xyxy, with positive area')
        return self


class Prediction(Box):
    score: Annotated[Number, Field(ge=0, le=1)]


class Frame(Model):
    scenario_id: Tag
    frame_id: Name
    t_s: NonNegative
    tags: list[Tag] = Field(default_factory=list, max_length=30)
    ground_truth: list[Box] | None = Field(default=None, max_length=500)
    predictions: list[Prediction] = Field(default_factory=list, max_length=500)


class Pose(Model):
    t_s: NonNegative
    x_m: Number
    y_m: Number
    yaw_rad: Number
    speed_mps: Number


class Obstacle(Model):
    actor_id: Name
    x_m: Number
    y_m: Number
    vx_mps: Number = 0
    vy_mps: Number = 0
    radius_m: Annotated[Number, Field(gt=0)] = .5


class Sample(Pose):
    # None = not observed; [] = explicitly observed empty scene.
    obstacles: list[Obstacle] | None = Field(default=None, max_length=100)
    collision: bool | None = None


class Trajectory(Model):
    scenario_id: Tag
    kind: Literal['driving', 'parking'] = 'driving'
    tags: list[Tag] = Field(default_factory=list, max_length=30)
    reference: list[Pose] = Field(min_length=2, max_length=20000)
    samples: list[Sample] = Field(min_length=2, max_length=20000)
    ego_radius_m: Annotated[Number, Field(gt=0)] = 1
    corridor_half_width_m: Annotated[Number, Field(gt=0)] = 1.75
    minimum_ttc_s: NonNegative = 1
    parking_position_tolerance_m: NonNegative = .3
    parking_heading_tolerance_rad: NonNegative = .1

    @model_validator(mode='after')
    def increasing_time(self):
        for seq in (self.reference, self.samples):
            if any(b.t_s <= a.t_s for a, b in zip(seq, seq[1:])):
                raise ValueError('trajectory times must be strictly increasing')
        return self


class Gate(Model):
    metric: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.:-]{1,200}$')]
    op: Literal['>=', '<=']
    value: Number


class Config(Model):
    iou_threshold: Annotated[Number, Field(gt=0, le=1)] = .5
    score_threshold: Annotated[Number, Field(ge=0, le=1)] = 0
    alignment_tolerance_s: Annotated[Number, Field(ge=0, le=.1)] = .001


class EvaluationInput(Model):
    schema_version: Literal['autofleet.evaluation.v1'] = Field(alias='schema')
    suite_version: Name
    candidate_version: Name
    source: Source
    frames: list[Frame] = Field(default_factory=list, max_length=20000)
    trajectories: list[Trajectory] = Field(default_factory=list, max_length=200)
    gates: list[Gate] = Field(default_factory=list, max_length=100)
    config: Config = Field(default_factory=Config)

    @model_validator(mode='after')
    def unique_inputs(self):
        if not self.frames and not self.trajectories:
            raise ValueError('at least one frame or trajectory is required')
        keys = [(f.scenario_id, f.frame_id) for f in self.frames]
        if len(keys) != len(set(keys)):
            raise ValueError('duplicate scenario/frame identity')
        tids = [t.scenario_id for t in self.trajectories]
        if len(tids) != len(set(tids)):
            raise ValueError('duplicate trajectory scenario')
        # Bounded work for the quadratic matching step and per-slice aggregation.
        if sum(len(f.predictions) + len(f.ground_truth or []) for f in self.frames) > 100000:
            raise ValueError('too many detections; split the evaluation into batches')
        return self
