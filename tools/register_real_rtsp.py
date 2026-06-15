from __future__ import annotations

import argparse
import json
import math
import time

import paho.mqtt.client as mqtt


def now_ts() -> int:
    return int(time.time())


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
        out["color"] = default_url
    return out


def build_payload(
    robot_id: str, rtsp_url: str, view_profile: str, state: str, battery: float, video_streams: dict[str, str]
) -> dict:
    t = time.time()
    latency_ms = 7.5 + abs(math.sin(t * 0.9)) * 4.0
    throughput_kb_s = 220.0 + math.sin(t * 0.6) * 27.0
    rssi_dbm = -43.0 - abs(math.sin(t * 0.4)) * 7.0
    return {
        "v": 1,
        "schema": "autofleet.telemetry.v1",
        "robot_id": robot_id,
        "ts": now_ts(),
        "pose": {"x": 0.0, "y": 0.0, "yaw": 0.0},
        "battery": battery,
        "state": state,
        "mission_id": None,
        "video_rtsp_url": rtsp_url,
        "video_view_profile": view_profile,
        "video_streams": video_streams or None,
        "controls": {"linear_x": 0.0, "angular_z": 0.0},
        "motors": {"left_rpm": 0.0, "right_rpm": 0.0},
        "network": {
            "latency_ms": round(latency_ms, 2),
            "packet_loss_pct": 0.0,
            "throughput_kb_s": round(throughput_kb_s, 1),
            "rssi_dbm": round(rssi_dbm, 1),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Register a real Raspberry RTSP stream in AutoFleet over MQTT.")
    parser.add_argument("--host", default="127.0.0.1", help="MQTT host, usually this PC IP or 127.0.0.1.")
    parser.add_argument("--port", type=int, default=3889, help="MQTT port.")
    parser.add_argument("--prefix", default="fleet/v1", help="MQTT topic prefix.")
    parser.add_argument("--robot-id", default="R1", help="Robot id shown in the dashboard.")
    parser.add_argument("--rtsp-url", required=True, help="Raspberry stream URL, for example rtsp://192.168.110.83:8554/camera.")
    parser.add_argument("--view-profile", default="front_center", help="Optional video crop/profile.")
    parser.add_argument(
        "--video-streams",
        default="",
        help="Comma-separated key=url map. Example: color=rtsp://.../rgb,depth=rtsp://.../depth,pose=http://.../pose_stream",
    )
    parser.add_argument("--state", default="MANUAL", help="Robot state shown in the dashboard.")
    parser.add_argument("--battery", type=float, default=1.0, help="Battery value between 0 and 1.")
    parser.add_argument("--interval", type=float, default=1.0, help="Publish interval in seconds.")
    args = parser.parse_args()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(args.host, args.port, 30)
    client.loop_start()

    telemetry_topic = f"{args.prefix}/telemetry/{args.robot_id}"
    heartbeat_topic = f"{args.prefix}/heartbeat/{args.robot_id}"
    video_streams = parse_video_streams(args.video_streams, args.rtsp_url)
    print(f"Registering {args.robot_id} -> {args.rtsp_url}")
    print(f"MQTT: {args.host}:{args.port}, topic: {telemetry_topic}")
    try:
        while True:
            payload = build_payload(
                args.robot_id,
                args.rtsp_url,
                args.view_profile,
                args.state,
                args.battery,
                video_streams,
            )
            client.publish(telemetry_topic, json.dumps(payload), qos=0)
            client.publish(
                heartbeat_topic,
                json.dumps(
                    {
                        "v": 1,
                        "schema": "autofleet.heartbeat.v1",
                        "source_id": args.robot_id,
                        "source_type": "robot",
                        "robot_id": args.robot_id,
                        "status": "OK",
                        "ts": now_ts(),
                        "meta": {
                            "state": args.state,
                            "battery": args.battery,
                            "video_rtsp_url": args.rtsp_url,
                            "video_view_profile": args.view_profile,
                            "video_streams": video_streams,
                        },
                    }
                ),
                qos=0,
            )
            time.sleep(max(0.2, args.interval))
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
