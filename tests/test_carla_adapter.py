from types import SimpleNamespace as NS

import pytest


class FakeCarla:
    class Location:
        def __init__(self, x=0, y=0, z=0): self.x, self.y, self.z = x, y, z
    class Rotation:
        def __init__(self, yaw=0): self.yaw = yaw
    class Transform:
        def __init__(self, location, rotation): self.location, self.rotation = location, rotation
    class Vector3D(Location): pass
    class VehicleControl:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class WalkerControl(VehicleControl): pass
    WeatherParameters = NS(ClearNoon=object())


class Actor:
    def __init__(self, actor_id, transform):
        self.id = actor_id; self.transform = transform; self.type_id = 'vehicle.test'
        self.bounding_box = NS(extent=NS(x=.5, y=.5)); self.destroyed = False
    def get_transform(self): return self.transform
    def get_velocity(self): return NS(x=0, y=0, z=0)
    def apply_control(self, control): pass
    def set_target_velocity(self, velocity): pass
    def destroy(self): self.destroyed = True


class World:
    def __init__(self): self.settings = NS(synchronous_mode=False, fixed_delta_seconds=None); self.actors = []; self.weather = object()
    def get_settings(self): return NS(**self.settings.__dict__)
    def apply_settings(self, value): self.settings = value
    def get_weather(self): return self.weather
    def set_weather(self, value): self.weather = value
    def get_map(self): return NS(name='Carla/Maps/Town03')
    def get_blueprint_library(self): return NS(find=lambda x: NS(id=x))
    def try_spawn_actor(self, blueprint, transform):
        actor = Actor(len(self.actors)+1, transform); self.actors.append(actor); return actor
    def tick(self): return 1


def scenario():
    return {'schema': 'autofleet.scenario.v1', 'scenario_id': 'unit', 'suite_version': '1', 'map': 'Town03',
            'seed': 17, 'weather': 'ClearNoon', 'dt_s': .05, 'ego_blueprint': 'vehicle.test', 'spawn_z_m': .5,
            'actors': [], 'gates': [{'metric': 'planning.coverage', 'op': '>=', 'value': 1}],
            'trajectory': {'scenario_id': 'unit', 'kind': 'driving', 'reference': [
                {'t_s': i*.05, 'x_m': i, 'y_m': 0, 'yaw_rad': 0, 'speed_mps': 0} for i in range(3)]}}


def test_collector_preserves_fixed_timeline_and_restores_environment():
    from autofleet_evaluation.carla_adapter import collect
    world = World(); old_weather = world.weather
    data = collect(scenario(), lambda state: {'throttle': 0, 'brake': 1, 'steer': 0},
                   client=NS(get_world=lambda: world), carla_module=FakeCarla, candidate_version='fixture')
    assert [s.t_s for s in data.trajectories[0].samples] == [0, .05, .1]
    assert not world.settings.synchronous_mode and world.weather is old_weather
    assert all(a.destroyed for a in world.actors)
    assert data.source.kind == 'carla'


def test_collector_cleans_up_when_controller_fails():
    from autofleet_evaluation.carla_adapter import collect
    world = World()
    def fail(state): raise RuntimeError('controller failed')
    with pytest.raises(RuntimeError, match='controller failed'):
        collect(scenario(), fail, client=NS(get_world=lambda: world), carla_module=FakeCarla, candidate_version='fixture')
    assert not world.settings.synchronous_mode
    assert all(a.destroyed for a in world.actors)


def test_collector_rejects_other_world_without_loading_it():
    from autofleet_evaluation.carla_adapter import collect
    world = World(); data = scenario(); data['map'] = 'OtherTown'
    with pytest.raises(ValueError, match='map'):
        collect(data, lambda s: {}, client=NS(get_world=lambda: world), carla_module=FakeCarla, candidate_version='fixture')
    assert world.actors == []


def test_repeated_collection_uses_stable_scenario_actor_identity():
    from autofleet_evaluation.carla_adapter import collect
    from autofleet_evaluation.engine import evaluate
    world = World(); data = scenario()
    data['actors'] = [{'blueprint': 'vehicle.test', 'x_m': 10, 'y_m': 0, 'yaw_rad': 0, 'vx_mps': 0, 'vy_mps': 0}]
    reports = [evaluate(collect(data, lambda state: {'brake': 1}, client=NS(get_world=lambda: world),
                                carla_module=FakeCarla, candidate_version=str(i))) for i in range(2)]
    assert reports[0]['truth_digest'] == reports[1]['truth_digest']


def test_collector_primes_new_actor_snapshot_before_first_sample():
    from autofleet_evaluation.carla_adapter import collect
    class CachedWorld(World):
        primed = False
        def try_spawn_actor(self, blueprint, transform):
            actor = super().try_spawn_actor(blueprint, transform)
            actor.get_transform = lambda: transform if self.primed else FakeCarla.Transform(FakeCarla.Location(), FakeCarla.Rotation())
            return actor
        def tick(self):
            self.primed = True
            return super().tick()
    world = CachedWorld(); data = scenario()
    for p in data['trajectory']['reference']: p['x_m'] += 10; p['y_m'] = 5
    result = collect(data, lambda state: {'brake': 1}, client=NS(get_world=lambda: world),
                     carla_module=FakeCarla, candidate_version='fixture')
    first = result.trajectories[0].samples[0]
    assert first.x_m == 10 and first.y_m == 5


def test_vehicle_settles_before_reference_timeline_begins():
    from autofleet_evaluation.carla_adapter import collect
    class CountingWorld(World):
        ticks = 0
        def tick(self):
            self.ticks += 1
            return super().tick()
    world = CountingWorld(); data = scenario(); data['settling_ticks'] = 20
    result = collect(data, lambda state: {'brake': 1}, client=NS(get_world=lambda: world),
                     carla_module=FakeCarla, candidate_version='fixture')
    assert world.ticks == 20 + len(data['trajectory']['reference']) - 1
    assert result.trajectories[0].samples[0].t_s == 0
