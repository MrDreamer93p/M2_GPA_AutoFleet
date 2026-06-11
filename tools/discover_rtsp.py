from __future__ import annotations

import argparse
import ipaddress
import json
import re
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass


DEFAULT_PORTS = (8554, 554)
DEFAULT_PATHS = ("camera", "stream", "live", "cam", "video")


@dataclass(frozen=True)
class ProbeResult:
    ip: str
    port: int
    path: str
    url: str
    status: str
    latency_ms: float
    detail: str


def parse_csv_ints(raw: str) -> list[int]:
    values: list[int] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        values.append(int(item))
    return values


def parse_csv_strings(raw: str) -> list[str]:
    values: list[str] = []
    for item in raw.split(","):
        item = item.strip().strip("/")
        if item:
            values.append(item)
    return values


def run_command(args: list[str], timeout: float = 2.0) -> str:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception:
        return ""
    return (proc.stdout or "") + "\n" + (proc.stderr or "")


def local_subnets() -> list[ipaddress.IPv4Network]:
    networks: list[ipaddress.IPv4Network] = []

    if sys.platform.startswith("win"):
        ps_output = run_command(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-NetIPAddress -AddressFamily IPv4 | "
                "Where-Object {$_.PrefixLength -and $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*'} | "
                "Select-Object IPAddress,PrefixLength | ConvertTo-Json -Compress",
            ],
            timeout=4.0,
        ).strip()
        if ps_output:
            try:
                parsed = json.loads(ps_output)
                rows = parsed if isinstance(parsed, list) else [parsed]
                for row in rows:
                    ip = str(row.get("IPAddress") or "")
                    prefix = int(row.get("PrefixLength"))
                    network = ipaddress.IPv4Network(f"{ip}/{prefix}", strict=False)
                    if is_private_scan_network(network):
                        networks.append(network)
            except Exception:
                networks = []

        output = run_command(["ipconfig"], timeout=3.0)
        current_ip: str | None = None
        for raw_line in output.splitlines():
            line = raw_line.strip()
            ip_match = re.search(r"IPv4.*?:\s*([0-9.]+)", line, re.IGNORECASE)
            if ip_match:
                current_ip = ip_match.group(1)
                continue
            mask_match = re.search(r"Subnet Mask.*?:\s*([0-9.]+)", line, re.IGNORECASE)
            if current_ip and mask_match:
                try:
                    network = ipaddress.IPv4Network(f"{current_ip}/{mask_match.group(1)}", strict=False)
                except ValueError:
                    current_ip = None
                    continue
                if is_private_scan_network(network):
                    networks.append(network)
                current_ip = None
    else:
        output = run_command(["ip", "-o", "-4", "addr", "show"], timeout=3.0)
        for match in re.finditer(r"inet\s+([0-9.]+/\d+)", output):
            try:
                network = ipaddress.IPv4Network(match.group(1), strict=False)
            except ValueError:
                continue
            if is_private_scan_network(network):
                networks.append(network)

    unique: list[ipaddress.IPv4Network] = []
    seen: set[str] = set()
    for network in networks:
        key = str(network)
        if key not in seen:
            seen.add(key)
            unique.append(network)
    return unique


def is_private_scan_network(network: ipaddress.IPv4Network) -> bool:
    if network.is_loopback or network.is_link_local or network.num_addresses <= 2:
        return False
    return network.is_private


def subnet_hosts(network: ipaddress.IPv4Network, max_hosts: int) -> list[str]:
    if network.prefixlen < 24:
        networks = list(network.subnets(new_prefix=24))
    else:
        networks = [network]
    hosts: list[str] = []
    for net in networks:
        for ip in net.hosts():
            hosts.append(str(ip))
            if len(hosts) >= max_hosts:
                return hosts
    return hosts


def probe_tcp(ip: str, port: int, timeout: float) -> tuple[bool, float, str]:
    started = time.perf_counter()
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            latency_ms = (time.perf_counter() - started) * 1000
            return True, latency_ms, "tcp open"
    except OSError as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        return False, latency_ms, str(exc)


def probe_rtsp(ip: str, port: int, path: str, timeout: float) -> ProbeResult | None:
    url = f"rtsp://{ip}:{port}/{path}"
    started = time.perf_counter()
    try:
        with socket.create_connection((ip, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            request = (
                f"OPTIONS {url} RTSP/1.0\r\n"
                "CSeq: 1\r\n"
                "User-Agent: AutoFleet-discovery\r\n"
                "\r\n"
            ).encode("ascii")
            sock.sendall(request)
            data = sock.recv(512)
    except OSError as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        return ProbeResult(ip, port, path, url, "tcp_open", latency_ms, str(exc)) if "timed out" in str(exc).lower() else None

    latency_ms = (time.perf_counter() - started) * 1000
    text = data.decode("latin1", errors="replace")
    first_line = text.splitlines()[0] if text.splitlines() else ""
    if first_line.startswith("RTSP/"):
        parts = first_line.split()
        code = parts[1] if len(parts) > 1 else ""
        if code in {"401", "403"}:
            return ProbeResult(ip, port, path, url, "rtsp_auth_required", latency_ms, first_line)
        if code.startswith(("2", "3")):
            return ProbeResult(ip, port, path, url, "rtsp", latency_ms, first_line)
        return ProbeResult(ip, port, path, url, "rtsp_rejected", latency_ms, first_line)
    return ProbeResult(ip, port, path, url, "tcp_open", latency_ms, first_line or "non-RTSP response")


def probe_host(ip: str, ports: list[int], paths: list[str], timeout: float) -> ProbeResult | None:
    best_tcp: ProbeResult | None = None
    for port in ports:
        ok, latency_ms, detail = probe_tcp(ip, port, timeout)
        if not ok:
            continue
        for path in paths:
            result = probe_rtsp(ip, port, path, timeout)
            if result and result.status == "rtsp":
                return result
            if result and best_tcp is None:
                best_tcp = result
        if best_tcp is None:
            url = f"rtsp://{ip}:{port}/{paths[0]}"
            best_tcp = ProbeResult(ip, port, paths[0], url, "tcp_open", latency_ms, detail)
    return best_tcp


def build_scan_hosts(args: argparse.Namespace) -> list[str]:
    hosts: list[str] = []
    for host in parse_csv_strings(args.seed_hosts):
        hosts.append(host)

    networks: list[ipaddress.IPv4Network] = []
    if args.subnet:
        networks.append(ipaddress.IPv4Network(args.subnet, strict=False))
    elif args.scan_current_subnets:
        networks.extend(local_subnets())

    for network in networks:
        hosts.extend(subnet_hosts(network, args.max_hosts))

    unique: list[str] = []
    seen: set[str] = set()
    for host in hosts:
        if host not in seen:
            seen.add(host)
            unique.append(host)
    return unique


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover an RTSP camera on the current LAN.")
    parser.add_argument("--seed-hosts", default="", help="Comma-separated IPs to probe before scanning, e.g. 192.168.1.24.")
    parser.add_argument("--subnet", default="", help="Subnet to scan, e.g. 192.168.1.0/24. Defaults to current private subnets.")
    parser.add_argument("--scan-current-subnets", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--ports", default=",".join(str(x) for x in DEFAULT_PORTS), help="Comma-separated RTSP ports.")
    parser.add_argument("--paths", default=",".join(DEFAULT_PATHS), help="Comma-separated RTSP paths without leading slash.")
    parser.add_argument("--timeout", type=float, default=0.45, help="Per connection timeout in seconds.")
    parser.add_argument("--workers", type=int, default=96, help="Parallel probe workers.")
    parser.add_argument("--max-hosts", type=int, default=254, help="Maximum scanned hosts per run.")
    parser.add_argument("--json", action="store_true", help="Print JSON result.")
    args = parser.parse_args()

    ports = parse_csv_ints(args.ports) or list(DEFAULT_PORTS)
    paths = parse_csv_strings(args.paths) or list(DEFAULT_PATHS)
    hosts = build_scan_hosts(args)
    if not hosts:
        print("No scan hosts found. Pass --subnet 192.168.1.0/24 or --seed-hosts 192.168.1.24.", file=sys.stderr)
        return 2

    seed_count = len(parse_csv_strings(args.seed_hosts))
    ordered_results: list[ProbeResult] = []

    for host in hosts[:seed_count]:
        result = probe_host(host, ports, paths, args.timeout)
        if result:
            ordered_results.append(result)
            if result.status == "rtsp":
                break

    if not any(result.status == "rtsp" for result in ordered_results):
        remaining = hosts[seed_count:]
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {pool.submit(probe_host, host, ports, paths, args.timeout): host for host in remaining}
            for future in as_completed(futures):
                result = future.result()
                if not result:
                    continue
                ordered_results.append(result)
                if result.status == "rtsp":
                    break

    rtsp_results = [result for result in ordered_results if result.status == "rtsp"]
    auth_results = [result for result in ordered_results if result.status == "rtsp_auth_required"]
    tcp_results = [result for result in ordered_results if result.status == "tcp_open"]
    best = (sorted(rtsp_results, key=lambda x: x.latency_ms) or sorted(auth_results, key=lambda x: x.latency_ms) or sorted(tcp_results, key=lambda x: x.latency_ms) or [None])[0]

    payload = {
        "selected_url": best.url if best else None,
        "selected_status": best.status if best else "not_found",
        "results": [result.__dict__ for result in sorted(ordered_results, key=lambda x: (x.status != "rtsp", x.latency_ms))],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        if best:
            print(best.url)
            print(f"{best.status}: {best.detail} ({best.latency_ms:.1f} ms)", file=sys.stderr)
        else:
            print("No RTSP candidate found.", file=sys.stderr)
    return 0 if best else 1


if __name__ == "__main__":
    raise SystemExit(main())
