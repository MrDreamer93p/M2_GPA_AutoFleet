"""Optional live CARLA input adapter. Importing the evaluation engine never imports CARLA.

The caller provides a dedicated, already-loaded CARLA world. No map is loaded and no
Traffic Manager is enabled implicitly. Only this client's newly created actors are removed.
"""
from __future__ import annotations

import importlib
import logging
import math
import sys

from pydantic import Field

from .engine import digest
from .models import EvaluationInput, Model, Number, Trajectory

log = logging.getLogger(__name__)


class Control(Model):
    throttle: Number = Field(default=0, ge=0, le=1)
    brake: Number = Field(default=0, ge=0, le=1)
    steer: Number = Field(default=0, ge=-1, le=1)
    reverse: bool = False


def load_controller(spec):
    module, separator, name = spec.partition(':')
    if not separator: raise ValueError('controller must be module:callable')
    controller = getattr(importlib.import_module(module), name)
    if not callable(controller): raise ValueError('controller must be callable')
    return controller


def route_follower(state):
    """Small replaceable reference controller, not an autonomous-driving safety algorithm."""
    ego, target = state['ego'], state['target']
    dx, dy = target['x_m'] - ego['x_m'], target['y_m'] - ego['y_m']
    reverse = dx * math.cos(ego['yaw_rad']) + dy * math.sin(ego['yaw_rad']) < 0
    desired_yaw = math.atan2(dy, dx) + (math.pi if reverse else 0)
    error = math.atan2(math.sin(desired_yaw - ego['yaw_rad']), math.cos(desired_yaw - ego['yaw_rad']))
    steer = max(-1, min(1, error * ( -1 if reverse else 1)))
    desired_speed = abs(target['speed_mps']) if math.hypot(dx, dy) > .2 else 0
    speed_error = desired_speed - abs(ego['speed_mps'])
    return {'throttle': min(.65, max(0, speed_error * .3)),
            'brake': min(1, max(0, -speed_error * .5)), 'steer': steer, 'reverse': reverse}


def pose(actor, t_s):
    transform, velocity = actor.get_transform(), actor.get_velocity()
    yaw = math.radians(transform.rotation.yaw)
    # Signed longitudinal speed preserves reverse parking motion.
    speed = velocity.x * math.cos(yaw) + velocity.y * math.sin(yaw)
    return {'t_s': t_s, 'x_m': transform.location.x, 'y_m': transform.location.y,
            'yaw_rad': yaw, 'speed_mps': speed}


def collect(scenario, controller, *, candidate_version, client=None, carla_module=None,
            host='127.0.0.1', port=2000):
    if carla_module is None:
        try: import carla as carla_module
        except ImportError as exc: raise RuntimeError('Install a CARLA Python client matching your running server') from exc
    carla = carla_module
    if scenario.get('schema') != 'autofleet.scenario.v1': raise ValueError('unsupported scenario schema')
    dt = scenario['dt_s']
    if not isinstance(dt, (int, float)) or not math.isfinite(dt) or not .01 <= dt <= .1:
        raise ValueError('CARLA fixed timestep must be 0.01-0.1 seconds')
    reference = scenario['trajectory']['reference']
    # Validate geometry, lengths and ordering before touching the server.
    Trajectory.model_validate({**scenario['trajectory'], 'samples': reference})
    if not math.isclose(reference[0]['t_s'], 0, abs_tol=1e-8) or any(
            not math.isclose(b['t_s'] - a['t_s'], dt, abs_tol=1e-8) for a, b in zip(reference, reference[1:])):
        raise ValueError('reference timeline must start at zero and use the fixed timestep')
    if client is None:
        client = carla.Client(host, port); client.set_timeout(15)
    world = client.get_world()
    if world.get_map().name.split('/')[-1] != scenario['map']:
        raise ValueError('CARLA map differs; load the scenario map in a dedicated server before collecting')
    old_settings, old_weather = world.get_settings(), world.get_weather()
    settings = world.get_settings(); settings.synchronous_mode = True; settings.fixed_delta_seconds = dt
    actors, npcs = [], []
    samples = []
    def spawn(blueprint, x, y, yaw):
        bp = world.get_blueprint_library().find(blueprint)
        transform = carla.Transform(carla.Location(x=x, y=y, z=scenario['spawn_z_m']),
                                    carla.Rotation(yaw=math.degrees(yaw)))
        actor = world.try_spawn_actor(bp, transform)
        if actor is None: raise RuntimeError(f'Cannot spawn {blueprint}; choose valid unoccupied map coordinates')
        actors.append(actor); return actor
    try:
        world.apply_settings(settings)
        world.set_weather(getattr(carla.WeatherParameters, scenario['weather']))
        start = reference[0]
        ego = spawn(scenario['ego_blueprint'], start['x_m'], start['y_m'], start['yaw_rad'])
        for spec in scenario['actors']:
            npcs.append((spawn(spec['blueprint'], spec['x_m'], spec['y_m'], spec['yaw_rad']), spec))
        for index, ref in enumerate(reference):
            current = pose(ego, ref['t_s']); current['obstacles'] = []
            for npc_index, (actor, _) in enumerate(npcs):
                transform, velocity, ext = actor.get_transform(), actor.get_velocity(), actor.bounding_box.extent
                current['obstacles'].append({'actor_id': f'scenario-actor-{npc_index}', 'x_m': transform.location.x, 'y_m': transform.location.y,
                                             'vx_mps': velocity.x, 'vy_mps': velocity.y,
                                             'radius_m': math.hypot(ext.x, ext.y)})
            samples.append(current)
            if index == len(reference) - 1: break
            target = reference[min(index + 10, len(reference) - 1)]
            control = Control.model_validate(controller({'t_s': ref['t_s'], 'ego': current, 'target': target,
                                                         'reference': reference, 'dt_s': dt}))
            ego.apply_control(carla.VehicleControl(**control.model_dump()))
            for actor, spec in npcs:
                vx, vy = spec['vx_mps'], spec['vy_mps']
                if actor.type_id.startswith('walker.'):
                    speed = math.hypot(vx, vy)
                    direction = carla.Vector3D(x=vx / speed if speed else 0, y=vy / speed if speed else 0)
                    actor.apply_control(carla.WalkerControl(direction=direction, speed=speed))
                else:
                    actor.set_target_velocity(carla.Vector3D(x=vx, y=vy, z=0))
            world.tick()
        ext = ego.bounding_box.extent
        trajectory = {**scenario['trajectory'], 'samples': samples, 'ego_radius_m': math.hypot(ext.x, ext.y)}
        return EvaluationInput.model_validate({'schema': 'autofleet.evaluation.v1', 'suite_version': scenario['suite_version'],
                                               'candidate_version': candidate_version,
                                               'source': {'kind': 'carla', 'dataset': scenario['scenario_id'], 'revision': digest(scenario),
                                                          'prediction_source': getattr(controller, '__qualname__', 'controller')},
                                               'trajectories': [trajectory], 'gates': scenario['gates']})
    finally:
        active_exception = sys.exc_info()[0] is not None
        cleanup_errors = []
        for actor in reversed(actors):
            try: actor.destroy()
            except Exception as exc: cleanup_errors.append(exc)
        for restore, value in ((world.set_weather, old_weather), (world.apply_settings, old_settings)):
            try: restore(value)
            except Exception as exc: cleanup_errors.append(exc)
        if cleanup_errors:
            if active_exception: log.error('CARLA cleanup failed: %s', cleanup_errors)
            else: raise RuntimeError(f'CARLA cleanup failed: {cleanup_errors}')
