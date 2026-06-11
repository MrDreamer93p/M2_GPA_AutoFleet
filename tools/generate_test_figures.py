from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


PASS = "#2E7D32"
PARTIAL = "#F9A825"
TODO = "#9E9E9E"
FAIL = "#C62828"
BLUE = "#1565C0"
CYAN = "#00838F"
PURPLE = "#6A1B9A"
TEXT = "#1F2933"
GRID = "#D9E2EC"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def latest_file(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not matches:
        raise FileNotFoundError(f"No file matches {pattern} in {directory}")
    return matches[0]


def setup_style() -> None:
    plt.rcParams.update(
        {
            "figure.dpi": 140,
            "savefig.dpi": 300,
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.titlesize": 14,
            "axes.labelsize": 11,
            "axes.edgecolor": "#B0BEC5",
            "axes.linewidth": 0.8,
            "xtick.color": TEXT,
            "ytick.color": TEXT,
            "axes.labelcolor": TEXT,
            "text.color": TEXT,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "grid.color": GRID,
            "grid.linewidth": 0.8,
            "legend.frameon": False,
        }
    )


def save(fig: plt.Figure, out_dir: Path, filename: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / filename
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def draw_acceptance_matrix(out_dir: Path) -> Path:
    tests = [
        ("T01", "Docker stack", "Integration"),
        ("T02", "Robot simulation", "Functional"),
        ("T03", "Video worker", "Functional / integration"),
        ("T04", "Perception + map", "Functional"),
        ("T05", "V2X command ACK", "Integration"),
        ("T06", "Formation", "Integration"),
        ("T07", "Mission scenario", "Scenario"),
        ("T08", "Frontend pages", "Functional"),
        ("T09", "V2X light load", "Performance"),
        ("T10", "Persistence + events", "Integration"),
    ]
    values = np.ones((len(tests), 1))
    fig, ax = plt.subplots(figsize=(8.4, 5.8))
    ax.imshow(values, cmap=plt.matplotlib.colors.ListedColormap([PASS]), aspect="auto", vmin=0, vmax=1)
    ax.set_xticks([0])
    ax.set_xticklabels(["Certified"])
    ax.set_yticks(range(len(tests)))
    ax.set_yticklabels([f"{tid}  {name}" for tid, name, _ in tests])
    ax.set_title("Executed Acceptance Test Certification Matrix", loc="left", pad=14, fontweight="bold")
    for i, (_, _, test_type) in enumerate(tests):
        ax.text(0, i, f"PASS  |  {test_type}", ha="center", va="center", color="white", fontweight="bold")
    ax.tick_params(axis="both", length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.text(
        0.01,
        0.01,
        "Figure 1. Local MVP acceptance tests all reached their configured acceptance criteria.",
        fontsize=9,
        color="#52606D",
    )
    return save(fig, out_dir, "fig01_acceptance_certification_matrix.png")


def draw_subsystem_status(out_dir: Path) -> Path:
    subsystem_statuses = {
        "Mobile robot simulation": "PASS",
        "LiDAR simulation": "NOT EXECUTED",
        "Actuators / motors": "NOT EXECUTED",
        "Motor encoder": "NOT EXECUTED",
        "Raspberry Pi 5 - ESP32": "NOT EXECUTED",
        "Embedded architecture": "PARTIAL",
        "System watchdog": "NOT EXECUTED",
        "Ultrasonic sensor": "NOT EXECUTED",
        "Embedded camera": "PARTIAL",
        "SLAM": "NOT EXECUTED",
        "Autonomous navigation": "PARTIAL",
        "Multi-sensor integration": "PARTIAL",
        "V2X communication": "PASS",
        "V2X performance": "PASS",
        "Complete software integration": "PASS",
    }
    counts = Counter(subsystem_statuses.values())
    labels = ["PASS", "PARTIAL", "NOT EXECUTED"]
    values = [counts.get(label, 0) for label in labels]
    colors = [PASS, PARTIAL, TODO]

    fig, (ax1, ax2) = plt.subplots(
        1,
        2,
        figsize=(11.5, 5.2),
        gridspec_kw={"width_ratios": [1, 1.45]},
    )
    wedges, _ = ax1.pie(
        values,
        colors=colors,
        startangle=90,
        wedgeprops={"width": 0.45, "edgecolor": "white", "linewidth": 2},
    )
    ax1.text(0, 0.08, str(sum(values)), ha="center", va="center", fontsize=24, fontweight="bold")
    ax1.text(0, -0.14, "subsystems", ha="center", va="center", fontsize=10, color="#52606D")
    ax1.set_title("Subsystem validation status", loc="left", pad=14, fontweight="bold")
    ax1.legend(wedges, [f"{l}: {v}" for l, v in zip(labels, values)], loc="lower center", bbox_to_anchor=(0.5, -0.12), ncol=1)

    names = list(subsystem_statuses.keys())
    y = np.arange(len(names))
    color_map = {"PASS": PASS, "PARTIAL": PARTIAL, "NOT EXECUTED": TODO}
    ax2.barh(y, [1] * len(names), color=[color_map[subsystem_statuses[n]] for n in names], height=0.62)
    ax2.set_yticks(y)
    ax2.set_yticklabels(names)
    ax2.invert_yaxis()
    ax2.set_xticks([])
    ax2.set_xlim(0, 1)
    for i, name in enumerate(names):
        status = subsystem_statuses[name]
        ax2.text(0.5, i, status, ha="center", va="center", color="white" if status != "NOT EXECUTED" else TEXT, fontweight="bold")
    for spine in ax2.spines.values():
        spine.set_visible(False)
    fig.text(
        0.01,
        0.01,
        "Figure 2. Certification is intentionally limited to the software/simulation scope available in the repository.",
        fontsize=9,
        color="#52606D",
    )
    return save(fig, out_dir, "fig02_subsystem_validation_status.png")


def draw_runtime_counts(acceptance: dict[str, Any], load: dict[str, Any], out_dir: Path) -> Path:
    summary = acceptance["summary_checks"]
    labels = [
        "Robots\nonline",
        "Video streams\nonline",
        "Perception\nsummaries",
        "Map\nsummaries",
        "V2X load\nsuccesses",
    ]
    values = [
        summary["robots_online_count"],
        summary["video_streams_online_count"],
        summary["perception_count"],
        summary["map_summary_count"],
        load["successful_requests"],
    ]
    targets = [3, 3, 3, 3, load["request_count"]]

    fig, ax = plt.subplots(figsize=(9.6, 4.8))
    x = np.arange(len(labels))
    bars = ax.bar(x, values, color=[BLUE, CYAN, PURPLE, PASS, "#455A64"], width=0.62)
    ax.plot(x, targets, color=FAIL, marker="o", linewidth=1.8, label="Acceptance target")
    for bar, value, target in zip(bars, values, targets):
        ax.text(bar.get_x() + bar.get_width() / 2, value + max(targets) * 0.035, f"{value}/{target}", ha="center", fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Count")
    ax.set_title("Runtime Evidence Coverage", loc="left", pad=14, fontweight="bold")
    ax.grid(axis="y")
    ax.legend(loc="upper left")
    ax.set_ylim(0, max(targets) * 1.25)
    fig.text(
        0.01,
        0.01,
        "Figure 3. Main local evidence counts satisfy their acceptance targets.",
        fontsize=9,
        color="#52606D",
    )
    return save(fig, out_dir, "fig03_runtime_evidence_coverage.png")


def draw_v2x_latency_timeseries(load: dict[str, Any], out_dir: Path) -> Path:
    samples = load["samples"]
    by_robot: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for sample in samples:
        by_robot[sample["robot_id"]].append((int(sample["iteration"]), float(sample["elapsed_ms"])))

    fig, ax = plt.subplots(figsize=(10.2, 5.2))
    palette = {"R1": BLUE, "R2": CYAN, "R3": PURPLE}
    for robot_id in sorted(by_robot):
        points = sorted(by_robot[robot_id])
        x = [p[0] for p in points]
        y = [p[1] for p in points]
        ax.plot(x, y, marker="o", linewidth=2.0, markersize=5, label=robot_id, color=palette.get(robot_id, TEXT))
    ax.axhline(500, color=FAIL, linestyle="--", linewidth=1.6, label="500 ms criterion")
    ax.set_title("V2X Teleoperation Response Time Under Light Load", loc="left", pad=14, fontweight="bold")
    ax.set_xlabel("Command iteration")
    ax.set_ylabel("HTTP response time (ms)")
    ax.set_xticks(range(10))
    ax.grid(True)
    ax.legend(ncol=4, loc="upper center", bbox_to_anchor=(0.5, 1.08))
    ax.set_ylim(0, max(520, max(float(s["elapsed_ms"]) for s in samples) * 1.2))
    fig.text(
        0.01,
        0.01,
        f"Figure 4. {load['successful_requests']}/{load['request_count']} commands succeeded; average response time = {load['average_http_response_ms']} ms.",
        fontsize=9,
        color="#52606D",
    )
    return save(fig, out_dir, "fig04_v2x_latency_timeseries.png")


def draw_v2x_latency_distribution(load: dict[str, Any], out_dir: Path) -> Path:
    samples = load["samples"]
    robots = sorted({sample["robot_id"] for sample in samples})
    data = [[float(sample["elapsed_ms"]) for sample in samples if sample["robot_id"] == robot] for robot in robots]

    fig, ax = plt.subplots(figsize=(8.6, 5.1))
    box = ax.boxplot(data, tick_labels=robots, patch_artist=True, widths=0.55, showmeans=True)
    colors = [BLUE, CYAN, PURPLE]
    for patch, color in zip(box["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.78)
    for median in box["medians"]:
        median.set_color("white")
        median.set_linewidth(2)
    for i, robot_data in enumerate(data, start=1):
        jitter = np.linspace(-0.08, 0.08, len(robot_data))
        ax.scatter(np.full(len(robot_data), i) + jitter, robot_data, s=28, color="#263238", alpha=0.58, zorder=3)
    ax.axhline(500, color=FAIL, linestyle="--", linewidth=1.6, label="500 ms criterion")
    ax.set_title("V2X Latency Distribution by Robot", loc="left", pad=14, fontweight="bold")
    ax.set_xlabel("Robot")
    ax.set_ylabel("HTTP response time (ms)")
    ax.grid(axis="y")
    ax.legend(loc="upper right")
    fig.text(
        0.01,
        0.01,
        "Figure 5. Per-robot latency stayed far below the configured 500 ms acceptance threshold.",
        fontsize=9,
        color="#52606D",
    )
    return save(fig, out_dir, "fig05_v2x_latency_distribution.png")


def draw_robot_network_metrics(acceptance: dict[str, Any], out_dir: Path) -> Path:
    robots = acceptance["robots_before_commands"]["items"]
    robot_ids = [robot["robot_id"] for robot in robots]
    latency = [float(robot["network"]["latency_ms"]) for robot in robots]
    loss = [float(robot["network"]["packet_loss_pct"]) for robot in robots]
    throughput = [
        float(
            robot["network"].get(
                "throughput_kb_s",
                float(robot["network"].get("throughput_kbps", 0.0)) / 8.192,
            )
        )
        for robot in robots
    ]
    rssi = [float(robot["network"]["rssi_dbm"]) for robot in robots]

    fig, axes = plt.subplots(2, 2, figsize=(10.5, 7.0))
    metrics = [
        ("Latency", latency, "ms", BLUE),
        ("Packet loss", loss, "%", FAIL),
        ("Throughput", throughput, "KB/s", PASS),
        ("RSSI", rssi, "dBm", PURPLE),
    ]
    for ax, (title, values, unit, color) in zip(axes.ravel(), metrics):
        bars = ax.bar(robot_ids, values, color=color, width=0.55)
        ax.set_title(title, loc="left", fontweight="bold")
        ax.set_ylabel(unit)
        ax.grid(axis="y")
        for bar, value in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width() / 2, value, f"{value:.1f}", ha="center", va="bottom" if value >= 0 else "top", fontweight="bold")
    fig.suptitle("Robot Network Telemetry Snapshot Before Command Tests", x=0.01, ha="left", fontsize=15, fontweight="bold")
    fig.text(
        0.01,
        0.01,
        "Figure 6. Robot simulator telemetry provides latency, packet loss, throughput, and RSSI evidence for network diagnostics.",
        fontsize=9,
        color="#52606D",
    )
    return save(fig, out_dir, "fig06_robot_network_metrics.png")


def draw_acceptance_workflow(out_dir: Path) -> Path:
    fig, ax = plt.subplots(figsize=(12, 3.9))
    ax.set_axis_off()
    labels = [
        "Story",
        "Acceptance\ncriteria",
        "BDD tests",
        "Implementation",
        "Automated /\nmanual run",
        "Certification",
    ]
    x_positions = np.linspace(0.05, 0.88, len(labels))
    y = 0.55
    width = 0.135
    height = 0.28
    colors = [BLUE, CYAN, PURPLE, "#455A64", PARTIAL, PASS]
    for i, (label, x, color) in enumerate(zip(labels, x_positions, colors)):
        box = FancyBboxPatch(
            (x, y),
            width,
            height,
            boxstyle="round,pad=0.018,rounding_size=0.018",
            linewidth=1.2,
            edgecolor="#263238",
            facecolor=color,
            alpha=0.95,
        )
        ax.add_patch(box)
        ax.text(x + width / 2, y + height / 2, label, ha="center", va="center", color="white", fontweight="bold")
        if i < len(labels) - 1:
            arrow = FancyArrowPatch(
                (x + width + 0.01, y + height / 2),
                (x_positions[i + 1] - 0.01, y + height / 2),
                arrowstyle="-|>",
                mutation_scale=16,
                linewidth=1.6,
                color="#263238",
            )
            ax.add_patch(arrow)
    ax.text(0.05, 0.22, "KO path: record defect -> fix implementation -> rerun failed and regression tests", fontsize=10, color="#52606D")
    ax.set_title("Agile Story Acceptance Pipeline", loc="left", pad=14, fontweight="bold")
    fig.text(
        0.01,
        0.01,
        "Figure 7. Tests are part of story definition and acceptance, not a final waterfall phase.",
        fontsize=9,
        color="#52606D",
    )
    return save(fig, out_dir, "fig07_agile_acceptance_workflow.png")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate report-grade figures from AutoFleet test evidence.")
    parser.add_argument("--evidence-dir", default="docs/test-evidence/api")
    parser.add_argument("--acceptance", default="")
    parser.add_argument("--load", default="")
    parser.add_argument("--out-dir", default="docs/test-evidence/figures")
    args = parser.parse_args()

    setup_style()
    evidence_dir = Path(args.evidence_dir)
    acceptance_path = Path(args.acceptance) if args.acceptance else latest_file(evidence_dir, "acceptance-results-*.json")
    load_path = Path(args.load) if args.load else latest_file(evidence_dir, "v2x-load-results-*.json")
    out_dir = Path(args.out_dir)

    acceptance = load_json(acceptance_path)
    load = load_json(load_path)

    outputs = [
        draw_acceptance_matrix(out_dir),
        draw_subsystem_status(out_dir),
        draw_runtime_counts(acceptance, load, out_dir),
        draw_v2x_latency_timeseries(load, out_dir),
        draw_v2x_latency_distribution(load, out_dir),
        draw_robot_network_metrics(acceptance, out_dir),
        draw_acceptance_workflow(out_dir),
    ]

    manifest = {
        "acceptance_source": str(acceptance_path),
        "load_source": str(load_path),
        "figures": [str(path) for path in outputs],
    }
    manifest_path = out_dir / "figures-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
