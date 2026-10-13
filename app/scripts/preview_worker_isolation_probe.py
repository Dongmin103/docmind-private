"""Read-only memory sampling and deployment gates; never changes live services.

Values are bytes on disk and MiB in summaries. Missing data, counter resets and
container restarts invalidate a run instead of becoming a new baseline.
"""
import argparse
import hashlib
import json
import math
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

MIB = 1024**2
GIB = 1024**3


def evaluate(windows, linux, *, minimum_seconds=1200):
    failures = []
    required_linux = ("wsl_available_bytes", "swap_used_bytes", "allocation_errors")
    if not windows or not linux:
        failures.append("MISSING_SAMPLES")
    for row in windows:
        if row.get("windows_available_bytes") is None:
            failures.append("WINDOWS_MEASUREMENT_MISSING")
    for row in linux:
        if any(row.get(key) is None for key in required_linux) or not row.get("containers"):
            failures.append("WSL_MEASUREMENT_MISSING")
    for rows in (windows, linux):
        if rows and rows[-1].get("elapsed_seconds", 0) - rows[0].get("elapsed_seconds", 0) < minimum_seconds:
            failures.append("MEASUREMENT_TOO_SHORT")
        for before, after in zip(rows, rows[1:]):
            gap = after.get("elapsed_seconds", 0) - before.get("elapsed_seconds", 0)
            if gap <= 0 or gap > max(3, after.get("interval_seconds", 1) * 3):
                failures.append("SAMPLE_GAP_OR_CLOCK_RESET")
    win = [r["windows_available_bytes"] for r in windows if r.get("windows_available_bytes") is not None]
    available = [r["wsl_available_bytes"] for r in linux if r.get("wsl_available_bytes") is not None]
    swap = [r["swap_used_bytes"] for r in linux if r.get("swap_used_bytes") is not None]
    if win and min(win) < GIB:
        failures.append("WINDOWS_AVAILABLE_BELOW_1GIB")
    if available and min(available) < GIB:
        failures.append("WSL_AVAILABLE_BELOW_1GIB")
    if swap and (max(swap) - swap[0] > 128 * MIB or swap[-1] >= 512 * MIB):
        failures.append("SWAP_BUDGET_EXCEEDED")
    previous = {}
    expected = None
    tmpfs_peak = 0
    peak_ratios = {}
    for row in linux:
        names = {c["name"] for c in row.get("containers", [])}
        if expected is not None and names != expected:
            failures.append("CONTAINER_SET_CHANGED")
        expected = names if expected is None else expected
        unique = {}
        for mount in row.get("tmpfs", []):
            if mount.get("used_bytes") is None:
                failures.append("TMPFS_MEASUREMENT_MISSING")
            else:
                unique[mount["identity"]] = max(unique.get(mount["identity"], 0), mount["used_bytes"])
        tmpfs_peak = max(tmpfs_peak, sum(unique.values()))
        for container in row.get("containers", []):
            name = container["name"]
            if any(container.get(k) is None for k in ("id", "started_at", "current_bytes", "peak_bytes", "limit_bytes", "oom_kill")):
                failures.append("CGROUP_MEASUREMENT_MISSING")
                continue
            old = previous.get(name)
            if old:
                if (container["id"], container["started_at"]) != (old["id"], old["started_at"]):
                    failures.append("CONTAINER_RESTARTED")
                if container["oom_kill"] < old["oom_kill"] or container["peak_bytes"] < old["peak_bytes"]:
                    failures.append("CGROUP_COUNTER_RESET")
                if container["oom_kill"] > old["oom_kill"]:
                    failures.append("NEW_OOM_KILL")
            previous[name] = container
            if not container["limit_bytes"]:
                failures.append("CGROUP_LIMIT_UNBOUNDED")
            else:
                ratio = container["peak_bytes"] / container["limit_bytes"]
                peak_ratios[name] = max(peak_ratios.get(name, 0), ratio)
                if ratio > .9:
                    failures.append("CGROUP_PEAK_ABOVE_90_PERCENT")
    errors = [r["allocation_errors"] for r in linux if r.get("allocation_errors") is not None]
    if errors and (max(errors) > errors[0] or any(b < a for a, b in zip(errors, errors[1:]))):
        failures.append("WSL_ALLOCATION_ERROR_OR_LOG_RESET")
    spans = [rows[-1].get("elapsed_seconds", 0) - rows[0].get("elapsed_seconds", 0) for rows in (windows, linux) if rows]
    return {"passed": not failures, "failures": sorted(set(failures)),
            "observed_seconds": min(spans) if spans else 0, "minimum_required_seconds": minimum_seconds,
            "windows_min_available_mib": min(win) / MIB if win else None,
            "wsl_min_available_mib": min(available) / MIB if available else None,
            "swap_peak_mib": max(swap) / MIB if swap else None,
            "swap_end_mib": swap[-1] / MIB if swap else None,
            "tmpfs_peak_bytes": tmpfs_peak, "cgroup_peak_ratios": peak_ratios}


def _run(command):
    return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL, timeout=10)


def _number(path):
    try:
        value = path.read_text().strip()
        return int(value) if value != "max" else 0
    except (OSError, ValueError):
        return None


def sample_wsl():
    row = {"utc": datetime.now(timezone.utc).isoformat(), "wsl_available_bytes": None,
           "swap_used_bytes": None, "allocation_errors": None, "containers": [], "tmpfs": []}
    try:
        memory = dict(re.findall(r"^(\w+):\s+(\d+) kB", Path("/proc/meminfo").read_text(), re.M))
        row["wsl_available_bytes"] = int(memory["MemAvailable"]) * 1024
        row["swap_used_bytes"] = (int(memory["SwapTotal"]) - int(memory["SwapFree"])) * 1024
        row["psi"] = Path("/proc/pressure/memory").read_text().strip()
    except (OSError, KeyError, ValueError):
        pass
    try:
        kernel = _run(["dmesg"])
        row["allocation_errors"] = len(re.findall(r"vmbus_alloc_ring|UtilAcceptVsock.*(?:error|fail)|page allocation failure", kernel, re.I))
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        ids = _run(["docker", "ps", "--filter", "name=docmind", "-q"]).split()
        containers = json.loads(_run(["docker", "inspect", *ids])) if ids else []
        for item in containers:
            identity = item["Id"]
            cgroup = Path("/sys/fs/cgroup/system.slice") / ("docker-" + identity + ".scope")
            events = {}
            try:
                events = dict(line.split() for line in (cgroup / "memory.events").read_text().splitlines())
            except OSError:
                pass
            row["containers"].append({"name": item["Name"].lstrip("/"), "id": identity,
                "started_at": item["State"]["StartedAt"], "current_bytes": _number(cgroup / "memory.current"),
                "peak_bytes": _number(cgroup / "memory.peak"), "limit_bytes": _number(cgroup / "memory.max"),
                "oom_kill": int(events["oom_kill"]) if "oom_kill" in events else None,
                "memory_events": events})
            for mount in item["Mounts"]:
                if mount["Destination"] == "/run/docmind-previews":
                    source = mount["Source"]
                    try:
                        # df runs in the existing container, and emits only numbers.
                        used = int(_run(["docker", "exec", identity, "df", "-B1", "--output=used", "/run/docmind-previews"]).splitlines()[-1].strip())
                    except (OSError, ValueError, subprocess.SubprocessError):
                        used = None
                    row["tmpfs"].append({"identity": hashlib.sha256(source.encode()).hexdigest(), "used_bytes": used})
    except (OSError, ValueError, subprocess.SubprocessError):
        row["collection_error"] = "DOCKER_SAMPLE_FAILED"
    return row


def collect_wsl(output, duration, interval):
    start = time.monotonic()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        while True:
            row = sample_wsl()
            row["elapsed_seconds"] = time.monotonic() - start
            row["interval_seconds"] = interval
            stream.write(json.dumps(row) + "\n")
            stream.flush()
            if row["elapsed_seconds"] >= duration:
                break
            time.sleep(max(0, interval - (time.monotonic() - start - row["elapsed_seconds"])))


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("sample-wsl", "evaluate", "browser", "serve-synthetic"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=int, default=900)
    parser.add_argument("--interval", type=float, default=1)
    parser.add_argument("--windows", type=Path)
    parser.add_argument("--linux", type=Path)
    parser.add_argument("--minimum-seconds", type=int, default=1200)
    parser.add_argument("--url")
    parser.add_argument("--storage-state", type=Path)
    parser.add_argument("--trials", type=int, default=20)
    parser.add_argument("--delivery-delay", type=int, default=540)
    args = parser.parse_args()
    if args.action == "serve-synthetic":
        serve_synthetic(args.output, args.delivery_delay)
        return 0
    if args.action == "sample-wsl":
        if args.duration < 1 or args.interval <= 0:
            parser.error("Positive duration and interval required")
        collect_wsl(args.output, args.duration, args.interval)
        return 0
    if args.action == "evaluate":
        result = evaluate(read_jsonl(args.windows), read_jsonl(args.linux), minimum_seconds=args.minimum_seconds)
    else:
        result = measure_browser(args.url, args.storage_state, args.trials)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    return 0 if result["passed"] else 1


def measure_browser(url, storage_state, trials):
    """Opt-in against a prepared synthetic fixture page; no live mutations.

    The fixture page must expose one preview button and the production PDF
    viewer. The signed synthetic ingest responder is held by the Windows
    overlap harness. Browser timing is separate from gate/process timings.
    """
    from playwright.sync_api import sync_playwright

    durations = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(storage_state=str(storage_state) if storage_state else None)
        page = context.new_page()
        page.goto(url)
        for _ in range(trials):
            page.evaluate("performance.clearMeasures('docmind-preview-click-to-first-page')")
            page.get_by_role("button", name="원문 보기", exact=True).click()
            page.wait_for_function("performance.getEntriesByName('docmind-preview-click-to-first-page').length > 0", timeout=120000)
            durations.append(page.evaluate("performance.getEntriesByName('docmind-preview-click-to-first-page')[0].duration"))
            page.get_by_role("button", name="닫기", exact=True).click()
        context.close()
        browser.close()
    p95 = sorted(durations)[math.ceil(.95 * len(durations)) - 1]
    return {"passed": len(durations) >= 20 and p95 <= 5000, "trials": len(durations), "first_page_ms": durations, "p95_ms": p95}


def serve_synthetic(ready_file, delay):
    """Loopback-only delayed ingest responder for the isolated Windows harness."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path != "/synthetic-ingest":
                self.send_error(404)
                return
            time.sleep(delay)
            body = b'{"accepted":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        ready_file.write_text(json.dumps({"port": server.server_port}), encoding="utf-8")
        server.serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())
