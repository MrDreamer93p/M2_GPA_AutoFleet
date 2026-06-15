from __future__ import annotations

import argparse
import ipaddress
import json
import math
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import SplitResult, quote, urlsplit, urlunsplit

import paho.mqtt.client as mqtt


DEFAULT_MQTT_PORTS = (3889, 1883)
DEFAULT_RTSP_PORTS = "8554,554"
DEFAULT_RTSP_PATHS = "camera,stream,live,cam,video"
STATE_PATH = Path.home() / ".autofleet_agent_state.json"


def now_ts() -> int:
    return int(time.time())


def parse_csv_strings(raw: str) -> list[str]:
    values: list[str] = []
    for item in raw.split(","):
        item = item.strip()
        if item:
            values.append(item)
    return values


def parse_csv_ints(raw: str) -> list[int]:
    values: list[int] = []
    for item in raw.split(","):
        item = item.strip()
        if item:
            values.append(int(item))
    return values


def parse_video_streams(raw_streams: str, default_url: str) -> dict[str, str]:
    raw = str(raw_streams or "").strip()
    if not raw:
        return {"color": default_url} if default_url else {}
    out: dict[str, str] = {}
    for pair in raw.split(","):
        if "=" not in pair:
            continue
        stream_type, stream_url = pair.split("=", 1)
        key = stream_type.strip().lower().replace("-", "_")
        value = stream_url.strip()
        if key and value:
            out[key] = value
    if not out and default_url:
        return {"color": default_url}
    return out


def run_command(args: list[str], timeout: float = 3.0) -> str:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception:
        return ""
    return proc.stdout or ""


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(payload: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass


def discover_self_ip() -> str:
    output = run_command(["ip", "-o", "-4", "addr", "show", "scope", "global"], timeout=3.0)
    rows: list[tuple[int, str]] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        iface = parts[1]
        cidr = parts[3]
        ip = cidr.split("/", 1)[0]
        if ip.startswith(("127.", "169.254.")):
            continue
        priority = 100
        if iface.startswith("wlan"):
            priority = 0
        elif iface.startswith("eth"):
            priority = 10
        rows.append((priority, ip))
    if rows:
        rows.sort()
        return rows[0][1]
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def add_rtsp_credentials(url: str, username: str, password: str) -> str:
    if not username:
        return url
    parsed = urlsplit(url)
    if "@" in parsed.netloc:
        return url
    auth = quote(username, safe="")
    if password:
        auth = f"{auth}:{quote(password, safe='')}"
    netloc = f"{auth}@{parsed.netloc}"
    return urlunsplit(SplitResult(parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def discover_rtsp_url(self_ip: str, args: argparse.Namespace) -> tuple[str, str]:
    raw = args.rtsp_url.strip()
    if raw and raw.lower() != "auto":
        return raw, "manual"

    script = Path(__file__).with_name("discover_rtsp.py")
    cmd = [
        sys.executable,
        str(script),
        "--seed-hosts",
        ",".join([self_ip, "127.0.0.1"]),
        "--no-scan-current-subnets",
        "--ports",
        args.rtsp_ports,
        "--paths",
        args.rtsp_paths,
        "--json",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=8.0, check=False)
    except Exception:
        proc = None

    if proc and proc.returncode == 0 and proc.stdout.strip():
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload = {}
        selected_url = str(payload.get("selected_url") or "").strip()
        selected_status = str(payload.get("selected_status") or "").strip() or "unknown"
        if selected_url:
            return add_rtsp_credentials(selected_url, args.rtsp_username.strip(), args.rtsp_password), selected_status

    port = parse_csv_ints(args.rtsp_ports)[0]
    path = parse_csv_strings(args.rtsp_paths)[0]
    fallback = add_rtsp_credentials(f"rtsp://{self_ip}:{port}/{path}", args.rtsp_username.strip(), args.rtsp_password)
    return fallback, "fallback"


def mqtt_connect_probe(host: str, port: int, timeout: float) -> bool:
    client_id = f"af-probe-{int(time.time() * 1000) % 100000}"
    client_bytes = client_id.encode("utf-8")
    variable_header = b"\x00\x04MQTT\x04\x02\x00\x05"
    payload = len(client_bytes).to_bytes(2, "big") + client_bytes
    remaining = len(variable_header) + len(payload)
    packet = b"\x10" + bytes([remaining]) + variable_header + payload
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(packet)
            reply = sock.recv(4)
    except OSError:
        return False
    return len(reply) >= 4 and reply[0] == 0x20 and reply[1] == 0x02 and reply[3] == 0x00


def build_candidate_hosts(self_ip: str, seed_hosts: list[str], subnet: str) -> list[str]:
    hosts: list[str] = []
    seen: set[str] = set()

    def add(host: str) -> None:
        host = host.strip()
        if host and host not in seen:
            seen.add(host)
            hosts.append(host)

    for host in seed_hosts:
        add(host)

    network = ipaddress.IPv4Network(subnet, strict=False)
    for ip in network.hosts():
        host = str(ip)
        if host != self_ip:
            add(host)
    return hosts


def discover_mqtt_host(self_ip: str, args: argparse.Namespace) -> tuple[str, int]:
    raw_host = args.mqtt_host.strip()
    if raw_host and raw_host.lower() != "auto":
        return raw_host, args.mqtt_port

    state = load_state()
    seed_hosts = parse_csv_strings(args.mqtt_seed_hosts)
    remembered = str(state.get("last_mqtt_host") or "").strip()
    if remembered:
        seed_hosts.insert(0, remembered)

    subnet = args.mqtt_scan_subnet.strip()
    if not subnet:
        subnet = str(ipaddress.IPv4Network(f"{self_ip}/24", strict=False))

    ports = parse_csv_ints(args.mqtt_candidate_ports) or list(DEFAULT_MQTT_PORTS)
    hosts = build_candidate_hosts(self_ip, seed_hosts, subnet)
    if not hosts:
        raise RuntimeError("No candidate MQTT hosts found.")

    seed_count = len(seed_hosts)
    for host in hosts[:seed_count]:
        for port in ports:
            if mqtt_connect_probe(host, port, args.mqtt_probe_timeout):
                save_state({"last_mqtt_host": host, "last_mqtt_port": port})
                return host, port

    with ThreadPoolExecutor(max_workers=max(1, args.mqtt_discovery_workers)) as pool:
        futures = {
            pool.submit(mqtt_connect_probe, host, port, args.mqtt_probe_timeout): (host, port)
            for host in hosts[seed_count:]
            for port in ports
        }
        for future in as_completed(futures):
            host, port = futures[future]
            try:
                ok = future.result()
            except Exception:
                ok = False
            if ok:
                save_state({"last_mqtt_host": host, "last_mqtt_port": port})
                return host, port

    raise RuntimeError(f"Could not find MQTT broker on subnet {subnet}.")


class RaspberryAgent:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        self.connected = threading.Event()
        self.robot_state = args.state
        self.battery = args.battery
        self.mission_id: str | None = None
        self.linear_x = 0.0
        self.angular_z = 0.0
        self.pose_x = 0.0
        self.pose_y = 0.0
        self.pose_yaw = 0.0
        self.video_streams = parse_video_streams(args.video_streams, "")
        self.mqtt_host = ""
        self.mqtt_port = args.mqtt_port
        self.self_ip = discover_self_ip()
        self.rtsp_url, self.rtsp_status = discover_rtsp_url(self.self_ip, args)
        self.last_refresh = 0.0

    def on_connect(self, client: mqtt.Client, *_args) -> None:
        client.subscribe(f"{self.args.prefix}/cmd/{self.args.robot_id}", qos=1)
        self.connected.set()
        print(f"connected mqtt {self.mqtt_host}:{self.mqtt_port} for {self.args.robot_id}")

    def on_disconnect(self, *_args) -> None:
        self.connected.clear()

    def on_message(self, _client: mqtt.Client, _userdata, msg: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except json.JSONDecodeError:
            return
        cmd_id = str(payload.get("cmd_id") or f"cmd-{now_ts()}")
        cmd_type = str(payload.get("type") or "")
        args = payload.get("args") or {}

        if cmd_type == "SET_MODE":
            self.robot_state = str(args.get("mode") or "IDLE")
            if self.robot_state in {"AUTO", "SAFE", "IDLE"}:
                self.linear_x = 0.0
                self.angular_z = 0.0
        elif cmd_type == "START_MISSION":
            self.robot_state = "RUNNING"
            self.mission_id = str(args.get("mission_id") or "") or None
        elif cmd_type == "RETURN_HOME":
            self.robot_state = "RETURNING"
            self.linear_x = -0.2
            self.angular_z = 0.0
        elif cmd_type == "STOP":
            self.robot_state = "SAFE"
            self.linear_x = 0.0
            self.angular_z = 0.0
        elif cmd_type == "TELEOP":
            self.robot_state = "MANUAL"
            self.linear_x = float(args.get("linear_x") or 0.0)
            self.angular_z = float(args.get("angular_z") or 0.0)
        elif cmd_type == "FOLLOW_LEADER_INPUT":
            self.robot_state = "FOLLOWING"
            self.linear_x = float(args.get("linear_x") or 0.0) * 0.9
            self.angular_z = float(args.get("angular_z") or 0.0) * 0.9

        ack = {
            "v": 1,
            "cmd_id": cmd_id,
            "robot_id": self.args.robot_id,
            "status": "ACCEPTED",
            "ts": now_ts(),
        }
        self.client.publish(f"{self.args.prefix}/ack/{self.args.robot_id}", json.dumps(ack), qos=1)

    def refresh_endpoints(self, *, force: bool = False) -> None:
        interval = max(5.0, self.args.refresh_interval)
        if not force and time.time() - self.last_refresh < interval:
            return
        self.self_ip = discover_self_ip()
        self.rtsp_url, self.rtsp_status = discover_rtsp_url(self.self_ip, self.args)
        if not self.args.video_streams.strip():
            self.video_streams = parse_video_streams("", self.rtsp_url)
        self.last_refresh = time.time()

    def connect(self) -> None:
        while not self.connected.is_set():
            self.refresh_endpoints(force=True)
            try:
                self.mqtt_host, self.mqtt_port = discover_mqtt_host(self.self_ip, self.args)
                self.client.connect(self.mqtt_host, self.mqtt_port, 30)
                self.client.loop_start()
                if self.connected.wait(timeout=5.0):
                    return
            except Exception as exc:
                print(f"mqtt connect failed: {exc}")
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass
            time.sleep(max(1.0, self.args.retry_interval))

    def update_pose(self) -> None:
        self.pose_yaw = (self.pose_yaw + self.angular_z * 0.18) % (2 * math.pi)
        self.pose_x += self.linear_x * 0.12 * math.cos(self.pose_yaw)
        self.pose_y += self.linear_x * 0.12 * math.sin(self.pose_yaw)
        self.battery = max(0.1, self.battery - 0.00015)

    def publish_once(self) -> None:
        self.update_pose()
        t = time.time()
        latency_ms = 8.0 + abs(math.sin(t * 0.85)) * 4.0
        throughput_kb_s = 210.0 + math.sin(t * 0.45) * 22.0
        rssi_dbm = -44.0 - abs(math.sin(t * 0.3)) * 8.0
        telemetry = {
            "v": 1,
            "schema": "autofleet.telemetry.v1",
            "robot_id": self.args.robot_id,
            "ts": now_ts(),
            "pose": {"x": round(self.pose_x, 3), "y": round(self.pose_y, 3), "yaw": round(self.pose_yaw, 3)},
            "battery": round(self.battery, 3),
            "state": self.robot_state,
            "mission_id": self.mission_id,
            "video_rtsp_url": self.rtsp_url,
            "video_view_profile": self.args.view_profile,
            "video_streams": self.video_streams if self.video_streams else ({"color": self.rtsp_url} if self.rtsp_url else None),
            "controls": {"linear_x": round(self.linear_x, 3), "angular_z": round(self.angular_z, 3)},
            "motors": {
                "left_rpm": round(95.0 * self.linear_x + 40.0 * self.angular_z, 2),
                "right_rpm": round(95.0 * self.linear_x - 40.0 * self.angular_z, 2),
            },
            "network": {
                "latency_ms": round(latency_ms, 2),
                "packet_loss_pct": 0.0,
                "throughput_kb_s": round(max(10.0, throughput_kb_s), 1),
                "rssi_dbm": round(rssi_dbm, 1),
            },
        }
        heartbeat = {
            "v": 1,
            "schema": "autofleet.heartbeat.v1",
            "source_id": self.args.robot_id,
            "source_type": "robot",
            "robot_id": self.args.robot_id,
            "status": "OK",
            "ts": now_ts(),
            "meta": {
                "state": self.robot_state,
                "battery": round(self.battery, 3),
                "video_rtsp_url": self.rtsp_url,
                "video_view_profile": self.args.view_profile,
                "video_streams": self.video_streams if self.video_streams else ({"color": self.rtsp_url} if self.rtsp_url else None),
                "self_ip": self.self_ip,
                "mqtt_host": self.mqtt_host,
                "rtsp_discovery_status": self.rtsp_status,
            },
        }
        self.client.publish(f"{self.args.prefix}/telemetry/{self.args.robot_id}", json.dumps(telemetry), qos=0)
        self.client.publish(f"{self.args.prefix}/heartbeat/{self.args.robot_id}", json.dumps(heartbeat), qos=0)
        sensor_summary = {
            "v": 1,
            "schema": "autofleet.sensor.v1",
            "robot_id": self.args.robot_id,
            "ts": now_ts(),
            "fusion_status": "degraded",
            "depth_frame_url": None,
            "nearest_obstacle_m": None,
            "imu_yaw_rate_rad_s": None,
            "lidar_points": None,
            "note": "Camera stream is online; depth, LiDAR, and IMU drivers are reserved but not publishing real measurements.",
            "sensors": [
                {
                    "sensor_id": "rpi-camera",
                    "sensor_type": "camera",
                    "status": "online",
                    "ts": now_ts(),
                    "frame_id": "camera",
                    "rate_hz": 15.0,
                    "latency_ms": None,
                    "confidence": None,
                    "metrics": {"rtsp_url": self.rtsp_url, "view_profile": self.args.view_profile},
                    "note": "Real RTSP camera stream published by MediaMTX.",
                },
                {
                    "sensor_id": "kinect-depth",
                    "sensor_type": "kinect",
                    "status": "offline",
                    "ts": now_ts(),
                    "metrics": {},
                    "note": "Reserved for Xbox Kinect depth frames; no real driver publishing yet.",
                },
                {
                    "sensor_id": "lidar",
                    "sensor_type": "lidar",
                    "status": "offline",
                    "ts": now_ts(),
                    "metrics": {},
                    "note": "Reserved for LiDAR scans; no real scan driver publishing yet.",
                },
                {
                    "sensor_id": "imu",
                    "sensor_type": "imu",
                    "status": "offline",
                    "ts": now_ts(),
                    "metrics": {},
                    "note": "Reserved for IMU orientation/acceleration; no real IMU driver publishing yet.",
                },
            ],
        }
        self.client.publish(f"{self.args.prefix}/sensor/{self.args.robot_id}", json.dumps(sensor_summary), qos=0)

    def run(self) -> None:
        print(f"AutoFleet Raspberry agent starting for {self.args.robot_id}")
        while True:
            self.connect()
            try:
                while self.connected.is_set():
                    self.refresh_endpoints()
                    self.publish_once()
                    time.sleep(max(0.2, self.args.interval))
            finally:
                try:
                    self.client.loop_stop()
                    self.client.disconnect()
                except Exception:
                    pass
                self.connected.clear()
                time.sleep(max(1.0, self.args.retry_interval))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AutoFleet Raspberry agent with auto-discovery for MQTT host and RTSP URL.")
    parser.add_argument("--mqtt-host", default="auto", help="MQTT host or auto.")
    parser.add_argument("--mqtt-port", type=int, default=3889, help="Default MQTT port when --mqtt-host is explicit.")
    parser.add_argument("--mqtt-candidate-ports", default="3889,1883", help="Ports to probe when MQTT host is auto.")
    parser.add_argument("--mqtt-seed-hosts", default="", help="Comma-separated MQTT host hints, tried before subnet scan.")
    parser.add_argument("--mqtt-scan-subnet", default="", help="Subnet to scan for MQTT when host is auto, for example 192.168.1.0/24.")
    parser.add_argument("--mqtt-probe-timeout", type=float, default=0.35, help="Per MQTT probe timeout in seconds.")
    parser.add_argument("--mqtt-discovery-workers", type=int, default=64, help="Parallel workers for MQTT discovery.")
    parser.add_argument("--prefix", default="fleet/v1", help="MQTT topic prefix.")
    parser.add_argument("--robot-id", default="R1", help="Robot id shown in AutoFleet.")
    parser.add_argument("--rtsp-url", default="auto", help="RTSP URL or auto.")
    parser.add_argument("--rtsp-ports", default=DEFAULT_RTSP_PORTS, help="Candidate RTSP ports for local discovery.")
    parser.add_argument("--rtsp-paths", default=DEFAULT_RTSP_PATHS, help="Candidate RTSP paths for local discovery.")
    parser.add_argument("--rtsp-username", default="", help="Optional RTSP username to inject into the discovered URL.")
    parser.add_argument("--rtsp-password", default="", help="Optional RTSP password to inject into the discovered URL.")
    parser.add_argument("--view-profile", default="front_center", help="Video view profile published to the dashboard.")
    parser.add_argument(
        "--video-streams",
        default="",
        help="Comma-separated key=url map. Example: color=rtsp://.../rgb,depth=rtsp://.../depth,pose=rtsp://.../pose",
    )
    parser.add_argument("--state", default="MANUAL", help="Initial robot state.")
    parser.add_argument("--battery", type=float, default=1.0, help="Initial battery value between 0 and 1.")
    parser.add_argument("--interval", type=float, default=1.0, help="Telemetry publish interval in seconds.")
    parser.add_argument("--refresh-interval", type=float, default=20.0, help="Seconds between endpoint refreshes.")
    parser.add_argument("--retry-interval", type=float, default=3.0, help="Seconds between MQTT reconnect attempts.")
    return parser.parse_args()


def main() -> None:
    agent = RaspberryAgent(parse_args())
    agent.run()


if __name__ == "__main__":
    main()
