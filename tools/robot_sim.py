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


def now_ts() -> int:
    return int(time.time())


class Simulator:
    def __init__(self, host: str, port: int, prefix: str, robot_ids: list[str]) -> None:
        self.host = host
        self.port = port
        self.prefix = prefix
        self.robots = {
            rid: Robot(robot_id=rid, x=random.uniform(0.0, 2.0), y=random.uniform(0.0, 2.0), yaw=0.0, video_rtsp_url=f"rtsp://{rid}.local/stream")
            for rid in robot_ids
        }
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.running = True

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
            robot.state = payload.get("args", {}).get("mode", "IDLE")
        elif cmd_type == "START_MISSION":
            robot.state = "RUNNING"
            robot.mission_id = payload.get("args", {}).get("mission_id")
        elif cmd_type == "RETURN_HOME":
            robot.state = "RETURNING"
        elif cmd_type == "STOP":
            robot.state = "SAFE"

        ack = {
            "v": 1,
            "cmd_id": cmd_id,
            "robot_id": robot_id,
            "status": "ACCEPTED",
            "ts": now_ts(),
        }
        self.client.publish(f"{self.prefix}/ack/{robot_id}", json.dumps(ack), qos=1)

    def telemetry_loop(self):
        while self.running:
            for robot in self.robots.values():
                phase = time.time() * 0.4 + hash(robot.robot_id) % 10
                robot.x += 0.05 * math.cos(phase)
                robot.y += 0.05 * math.sin(phase)
                robot.yaw = (robot.yaw + 0.05) % (2 * math.pi)
                robot.battery = max(0.1, robot.battery - 0.0005)

                payload = {
                    "v": 1,
                    "robot_id": robot.robot_id,
                    "ts": now_ts(),
                    "pose": {"x": round(robot.x, 3), "y": round(robot.y, 3), "yaw": round(robot.yaw, 3)},
                    "battery": round(robot.battery, 3),
                    "state": robot.state,
                    "mission_id": robot.mission_id,
                    "video_rtsp_url": robot.video_rtsp_url,
                }
                self.client.publish(f"{self.prefix}/telemetry/{robot.robot_id}", json.dumps(payload), qos=0)
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
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--prefix", default="fleet/v1")
    parser.add_argument("--robots", default="R1,R2,R3")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    robot_ids = [x.strip() for x in args.robots.split(",") if x.strip()]
    sim = Simulator(args.host, args.port, args.prefix, robot_ids)
    sim.run()
