#!/usr/bin/env bash
set -euo pipefail

ROBOT_DIR="${ROBOT_DIR:-/home/agent/robot-agent}"
ROBOT_USER="${ROBOT_USER:-agent}"
MQTT_HOST="${MQTT_HOST:-127.0.0.1}"
MQTT_PORT="${MQTT_PORT:-3890}"
ROBOT_ID="${ROBOT_ID:-R1}"
RTSP_URL="${RTSP_URL:-auto}"

if [[ ! -d "$ROBOT_DIR" ]]; then
  echo "Robot directory not found: $ROBOT_DIR" >&2
  exit 1
fi

sudo tee /etc/default/autofleet-agent >/dev/null <<EOF
ROBOT_ID=${ROBOT_ID}
MQTT_HOST=${MQTT_HOST}
MQTT_PORT=${MQTT_PORT}
RTSP_URL=${RTSP_URL}
EOF

sudo tee /etc/systemd/system/autofleet-mediamtx.service >/dev/null <<'EOF'
[Unit]
Description=AutoFleet MediaMTX RTSP server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/mediamtx /etc/mediamtx.yml
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/autofleet-stream.service >/dev/null <<EOF
[Unit]
Description=AutoFleet Raspberry Pi camera RTSP publisher
After=network-online.target autofleet-mediamtx.service
Wants=network-online.target
Requires=autofleet-mediamtx.service

[Service]
Type=simple
User=${ROBOT_USER}
WorkingDirectory=${ROBOT_DIR}
ExecStart=/usr/bin/python3 -u ${ROBOT_DIR}/stream_rtsp.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/autofleet-agent.service >/dev/null <<EOF
[Unit]
Description=AutoFleet Raspberry Pi telemetry and sensor agent
After=network-online.target autofleet-stream.service
Wants=network-online.target
Requires=autofleet-stream.service

[Service]
Type=simple
User=${ROBOT_USER}
WorkingDirectory=${ROBOT_DIR}
EnvironmentFile=/etc/default/autofleet-agent
ExecStart=/usr/bin/python3 -u ${ROBOT_DIR}/raspi_autofleet_agent.py --robot-id \${ROBOT_ID} --mqtt-host \${MQTT_HOST} --mqtt-port \${MQTT_PORT} --rtsp-url \${RTSP_URL} --interval 0.5
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable autofleet-mediamtx.service autofleet-stream.service autofleet-agent.service
sudo systemctl restart autofleet-mediamtx.service autofleet-stream.service autofleet-agent.service

echo "AutoFleet Raspberry Pi services installed and started."
systemctl --no-pager --full status autofleet-mediamtx.service autofleet-stream.service autofleet-agent.service || true
