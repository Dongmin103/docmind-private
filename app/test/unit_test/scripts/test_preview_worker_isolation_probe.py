import importlib.util
import json
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[3] / "scripts/preview_worker_isolation_probe.py"
spec = importlib.util.spec_from_file_location("isolation_probe", path)
probe = importlib.util.module_from_spec(spec)
if path.exists():
    spec.loader.exec_module(probe)


def samples():
    linux = [{"elapsed_seconds": i, "wsl_available_bytes": 2 * 1024**3, "swap_used_bytes": 16 * 1024**2,
              "allocation_errors": 0, "expected_containers": ["app"], "expected_tmpfs": ["shared"], "containers": [{"name": "app", "id": "one", "started_at": "t1",
                  "current_bytes": 64 * 1024**2, "peak_bytes": 128 * 1024**2, "limit_bytes": 256 * 1024**2,
                  "oom_kill": 0}], "tmpfs": [{"identity": "shared", "used_bytes": 4096}, {"identity": "shared", "used_bytes": 4096}]} for i in range(1201)]
    windows = [{"elapsed_seconds": i, "windows_available_bytes": 2 * 1024**3, "expected_worker_roles": ["ingest", "preview"],
                "workers": [{"role": "ingest", "pid": 1, "private_bytes": 100, "working_set_bytes": 200},
                            {"role": "preview", "pid": 2, "private_bytes": 100, "working_set_bytes": 200}]} for i in range(1201)]
    return windows, linux


def test_missing_measurement_is_failure_not_zero():
    windows, linux = samples()
    linux[1]["swap_used_bytes"] = None
    assert not probe.evaluate(windows, linux)["passed"]


def test_memory_units_and_shared_tmpfs_counted_once():
    result = probe.evaluate(*samples())
    assert result["passed"]
    assert result["tmpfs_peak_bytes"] == 4096
    assert result["windows_min_available_mib"] == 2048
    assert result["wsl_min_available_mib"] == 2048


def test_inventory_requires_core_services_and_excludes_retired_stopped_containers(monkeypatch):
    configured = [{"Name": "/docmind-dev-ragflow-cpu-1", "State": {"Running": True},
                   "Mounts": [{"Source": "/shared", "Destination": "/run/docmind-previews"}]},
                  {"Name": "/docmind-dev-retired-ocr-1", "State": {"Running": False}, "Mounts": []}]
    monkeypatch.setattr(probe, "_run", lambda command: json.dumps(configured) if "inspect" in command else "ids")
    inventory = probe.candidate_inventory()
    assert "docmind-dev-mysql-1" in inventory["expected_containers"]
    assert "docmind-dev-docmind-preview-processor-1" in inventory["expected_containers"]
    assert "docmind-dev-retired-ocr-1" not in inventory["expected_containers"]


@pytest.mark.parametrize("change", ["expected_missing", "container_absent", "tmpfs_absent", "worker_absent"])
def test_memory_gate_requires_complete_candidate_coverage(change):
    windows, linux = samples()
    for row in linux:
        if change == "expected_missing":
            row.pop("expected_containers")
        elif change == "container_absent":
            row["expected_containers"] = ["app", "preview"]
        elif change == "tmpfs_absent":
            row["tmpfs"] = []
    if change == "worker_absent":
        for row in windows:
            row["workers"] = row["workers"][:1]
    assert not probe.evaluate(windows, linux)["passed"]


@pytest.mark.parametrize("change", ["oom", "reset", "restart", "windows", "wsl", "swap", "peak", "allocation", "gap"])
def test_memory_gate_rejects_unsafe_or_reset_observations(change):
    windows, linux = samples()
    if change == "oom":
        linux[-1]["containers"][0]["oom_kill"] = 1
    elif change == "reset":
        linux[0]["containers"][0]["oom_kill"] = 1
    elif change == "restart":
        linux[-1]["containers"][0]["id"] = "two"
    elif change == "windows":
        windows[-1]["windows_available_bytes"] = 512 * 1024**2
    elif change == "wsl":
        linux[-1]["wsl_available_bytes"] = 512 * 1024**2
    elif change == "swap":
        linux[-1]["swap_used_bytes"] = 600 * 1024**2
    elif change == "peak":
        linux[-1]["containers"][0]["peak_bytes"] = 255 * 1024**2
    elif change == "gap":
        del linux[2:9]
    else:
        linux[-1]["allocation_errors"] = 1
    assert not probe.evaluate(windows, linux)["passed"]
