from __future__ import annotations

import argparse
import json
import math
import random
import threading
import time
from dataclasses import dataclass

import paho.mqtt.client as mqtt


@dataclass
class Robot:
    robot_id: str
    x: float
    y: float
    yaw: float
    battery: float = 0.95
    state: str = "IDLE"
    mission_id: str | None = None
    video_rtsp_url: str | None = None
    linear_x: float = 0.0
    angular_z: float = 0.0
    latency_base_ms: float = 25.0
    loss_base_pct: float = 0.5
    rssi_base_dbm: float = -55.0


def now_ts() -> int:
    return int(time.time())


class Simulator:
    def __init__(self, host: str, port: int, prefix: str, robot_ids: list[str]) -> None:
        self.host = host
        self.port = port
        self.prefix = prefix
        self.robots = {
            rid: Robot(
                robot_id=rid,
                x=random.uniform(0.0, 2.0),
                y=random.uniform(0.0, 2.0),
                yaw=0.0,
                video_rtsp_url=f"rtsp://{rid}.local/stream",
                latency_base_ms=random.uniform(18.0, 36.0),
                loss_base_pct=random.uniform(0.1, 1.2),
                rssi_base_dbm=random.uniform(-68.0, -48.0),
            )
            for rid in robot_ids
        }
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.running = True
        self.last_heartbeat_sent = {rid: 0 for rid in robot_ids}

    def on_connect(self, client: mqtt.Client, *_):
        client.subscribe(f"{self.prefix}/cmd/+")
        print("connected to mqtt, subscribed cmd topics")

    def on_message(self, _: mqtt.Client, __, msg: mqtt.MQTTMessage):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except json.JSONDecodeError:
            return

        robot_id = payload.get("robot_id")
        cmd_id = payload.get("cmd_id", f"cmd-{now_ts()}")
        robot = self.robots.get(robot_id)
        if robot is None:
            return

        cmd_type = payload.get("type", "")
        if cmd_type == "SET_MODE":
            mode = payload.get("args", {}).get("mode", "IDLE")
            robot.state = str(mode)
            if mode in {"AUTO", "SAFE", "IDLE"}:
                robot.linear_x = 0.0
                robot.angular_z = 0.0
        elif cmd_type == "START_MISSION":
            robot.state = "RUNNING"
            robot.mission_id = payload.get("args", {}).get("mission_id")
        elif cmd_type == "RETURN_HOME":
            robot.state = "RETURNING"
            robot.linear_x = -0.25
            robot.angular_z = 0.0
        elif cmd_type == "STOP":
            robot.state = "SAFE"
            robot.linear_x = 0.0
            robot.angular_z = 0.0
        elif cmd_type == "TELEOP":
            args = payload.get("args", {})
            robot.linear_x = float(args.get("linear_x", 0.0))
            robot.angular_z = float(args.get("angular_z", 0.0))
            robot.state = "MANUAL"
        elif cmd_type == "FOLLOW_LEADER_INPUT":
            args = payload.get("args", {})
            robot.linear_x = float(args.get("linear_x", 0.0)) * 0.9
            robot.angular_z = float(args.get("angular_z", 0.0)) * 0.9
            robot.state = "FOLLOWING"

        ack = {
            "v": 1,
            "cmd_id": cmd_id,
            "robot_id": robot_id,
            "status": "ACCEPTED",
            "ts": now_ts(),
        }
        time.sleep(random.uniform(0.008, 0.04))
        self.client.publish(f"{self.prefix}/ack/{robot_id}", json.dumps(ack), qos=1)

    def telemetry_loop(self):
        while self.running:
            for robot in self.robots.values():
                phase = time.time() * 0.4 + hash(robot.robot_id) % 10
                if robot.state in {"MANUAL", "FOLLOWING", "RETURNING"}:
                    robot.yaw = (robot.yaw + robot.angular_z * 0.14) % (2 * math.pi)
                    robot.x += robot.linear_x * 0.10 * math.cos(robot.yaw)
                    robot.y += robot.linear_x * 0.10 * math.sin(robot.yaw)
                else:
                    robot.x += 0.04 * math.cos(phase)
                    robot.y += 0.04 * math.sin(phase)
                    robot.yaw = (robot.yaw + 0.04) % (2 * math.pi)
                robot.battery = max(0.1, robot.battery - 0.0005)
                latency_jitter = 8.0 * abs(math.sin(phase * 0.55)) + random.uniform(0.0, 5.0)
                latency_ms = robot.latency_base_ms + latency_jitter
                packet_loss_pct = min(12.0, robot.loss_base_pct + random.uniform(0.0, 1.6))
                throughput_kbps = max(60.0, 1400.0 - latency_ms * 9.0 + random.uniform(-100.0, 120.0))
                rssi_dbm = robot.rssi_base_dbm - latency_jitter * 0.18 + random.uniform(-1.5, 1.5)

                payload = {
                    "v": 1,
                    "schema": "autofleet.telemetry.v1",
                    "robot_id": robot.robot_id,
                    "ts": now_ts(),
                    "pose": {"x": round(robot.x, 3), "y": round(robot.y, 3), "yaw": round(robot.yaw, 3)},
                    "battery": round(robot.battery, 3),
                    "state": robot.state,
                    "mission_id": robot.mission_id,
                    "video_rtsp_url": robot.video_rtsp_url,
                    "controls": {"linear_x": round(robot.linear_x, 3), "angular_z": round(robot.angular_z, 3)},
                    "motors": {
                        "left_rpm": round(90.0 * robot.linear_x + 35.0 * robot.angular_z, 2),
                        "right_rpm": round(90.0 * robot.linear_x - 35.0 * robot.angular_z, 2),
                    },
                    "network": {
                        "latency_ms": round(latency_ms, 2),
                        "packet_loss_pct": round(packet_loss_pct, 2),
                        "throughput_kbps": round(throughput_kbps, 1),
                        "rssi_dbm": round(rssi_dbm, 1),
                    },
                }
                self.client.publish(f"{self.prefix}/telemetry/{robot.robot_id}", json.dumps(payload), qos=0)
                now = now_ts()
                if now - self.last_heartbeat_sent[robot.robot_id] >= 1:
                    heartbeat = {
                        "v": 1,
                        "schema": "autofleet.heartbeat.v1",
                        "source_id": robot.robot_id,
                        "source_type": "robot",
                        "robot_id": robot.robot_id,
                        "status": "OK",
                        "ts": now,
                        "meta": {
                            "battery": round(robot.battery, 3),
                            "state": robot.state,
                            "video_rtsp_url": robot.video_rtsp_url,
                        },
                    }
                    self.client.publish(f"{self.prefix}/heartbeat/{robot.robot_id}", json.dumps(heartbeat), qos=0)
                    self.last_heartbeat_sent[robot.robot_id] = now
            time.sleep(0.2)

    def run(self):
        self.client.connect(self.host, self.port, 30)
        self.client.loop_start()
        t = threading.Thread(target=self.telemetry_loop, daemon=True)
        t.start()
        print(f"simulating robots: {', '.join(self.robots.keys())}")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.running = False
            self.client.loop_stop()
            self.client.disconnect()
            print("sim stopped")


def parse_args():
    parser = argparse.ArgumentParser(description="AutoFleet robot simulator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3889)
    parser.add_argument("--prefix", default="fleet/v1")
    parser.add_argument("--robots", default="R1,R2,R3")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    robot_ids = [x.strip() for x in args.robots.split(",") if x.strip()]
    sim = Simulator(args.host, args.port, args.prefix, robot_ids)
    sim.run()
