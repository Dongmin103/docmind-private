"""Run launch scripts using synthetic executables, never production services."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[3]
BASH = shutil.which("bash") if os.name != "nt" else None


def shell_path(path):
    value = path.as_posix()
    return "/" + value[0].lower() + value[2:] if os.name == "nt" else value


def launch(tmp_path, filename, selection=None, args=()):
    app = tmp_path / "app"
    (app / "conf").mkdir(parents=True)
    (app / "VERSION").write_text("synthetic")
    (app / "conf/service_conf.yaml.template").write_text("synthetic: true\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    trace = tmp_path / "trace"
    for name in ["python3", "nginx", "cp", "sleep"]:
        file = bindir / name
        file.write_text('#!/bin/sh\nprintf "%s %s\\n" "' + name + '" "$*" >> "$TRACE"\nexit ' + ("1" if name == "sleep" else "0") + '\n')
        file.chmod(0o755)
    (app / "tools/scripts").mkdir(parents=True)
    migration = app / "tools/scripts/run_migrations.sh"
    migration.write_text('#!/bin/sh\nprintf "migration\\n" >> "$TRACE"\n')
    migration.chmod(0o755)
    source = (APP / "docker" / filename).read_text().replace("/ragflow/", shell_path(app) + "/").replace("/usr/sbin/nginx", shell_path(bindir / "nginx"))
    script = tmp_path / filename
    script.write_text(source, newline="\n")
    env = {**os.environ, "TRACE": shell_path(trace), "USE_DOCLING": "false"}
    for name in ["API_PROXY_SCHEME", "DB_TYPE", "DOC_ENGINE", "STORAGE_IMPL"]:
        env.pop(name, None)
    if selection:
        env["API_PROXY_SCHEME"] = selection
    command = 'export PATH="' + shell_path(bindir) + ':$PATH"; exec bash "' + shell_path(script) + '" ' + " ".join(args)
    result = subprocess.run([BASH, "-c", command], cwd=app, env=env, capture_output=True, text=True, timeout=8)
    return result, trace.read_text().splitlines() if trace.exists() else []


@pytest.mark.skipif(not BASH, reason="Bash is required")
@pytest.mark.parametrize("filename", ["entrypoint.sh", "launch_backend_service.sh"])
def test_unset_selection_starts_python(tmp_path, filename):
    result, trace = launch(tmp_path, filename)
    assert any("api/ragflow_server.py" in line for line in trace), (result.stderr, trace)
    assert not any("bin/ragflow_server" in line for line in trace)


@pytest.mark.skipif(not BASH, reason="Bash is required")
@pytest.mark.parametrize("filename", ["entrypoint.sh", "launch_backend_service.sh"])
@pytest.mark.parametrize("selection", ["go", "hybrid", "unknown"])
def test_retired_selection_fails_before_python_or_config_work(tmp_path, filename, selection):
    result, trace = launch(tmp_path, filename, selection)
    assert result.returncode == 1
    assert "Unsupported API_PROXY_SCHEME" in result.stderr
    assert trace == []


@pytest.mark.skipif(not BASH, reason="Bash is required")
def test_superuser_argument_reaches_python(tmp_path):
    result, trace = launch(tmp_path, "entrypoint.sh", "python", ["--init-superuser"])
    assert any("api/ragflow_server.py --init-superuser" in line for line in trace), (result.stderr, trace)


@pytest.mark.skipif(not BASH, reason="Bash is required")
def test_disabled_webserver_starts_no_process(tmp_path):
    result, trace = launch(tmp_path, "entrypoint.sh", args=["--disable-webserver"])
    assert result.returncode == 0
    assert not any(line.startswith("python3 ") or line.startswith("nginx ") for line in trace)
