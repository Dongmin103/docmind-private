"""Exercise shell startup with fake processes; never start a real service."""

import os
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[3]


@unittest.skipUnless(os.name == "posix", "Shell process harness runs on Linux")
class CloudOnlyEntrypointTest(unittest.TestCase):
    def run_entrypoint(self, args):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "ragflow"
            (app / "conf").mkdir(parents=True)
            (app / "VERSION").write_text("test")
            (app / "conf/service_conf.yaml.template").write_text("test: true\n")
            binpath = root / "bin"
            binpath.mkdir()
            trace = root / "trace"
            for name in ["python3", "nginx", "cp", "pkg-config"]:
                path = binpath / name
                path.write_text('#!/bin/sh\nprintf "%s %s\\n" "' + name + '" "$*" >> "$TRACE"\nexit 0\n')
                path.chmod(0o755)
            source = (APP / "docker/entrypoint.sh").read_text()
            source = source.replace("/ragflow/", str(app) + "/").replace("/usr/sbin/nginx", str(binpath / "nginx"))
            script = root / "entrypoint.sh"
            script.write_text(source)
            env = {**os.environ, "PATH": str(binpath) + ":" + os.environ["PATH"], "TRACE": str(trace), "API_PROXY_SCHEME": "python", "USE_DOCLING": "false"}
            p = subprocess.Popen(["bash", str(script), *args], cwd=app, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                try:
                    p.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
                lines = trace.read_text().splitlines() if trace.exists() else []
                return p.poll(), lines
            finally:
                if p.poll() is None:
                    os.killpg(p.pid, signal.SIGTERM)
                    p.wait(timeout=3)

    def test_default_starts_only_api_and_admin(self):
        _, lines = self.run_entrypoint(["--enable-adminserver"])
        self.assertTrue(any("api/ragflow_server.py" in l for l in lines), lines)
        self.assertTrue(any("admin/server/admin_server.py" in l for l in lines), lines)
        self.assertFalse(any("task_executor.py" in l or "sync_data_source.py" in l for l in lines), lines)

    def test_removed_worker_options_cannot_start_a_worker(self):
        for option in ["--workers=2", "--consumer-no-end=2", "--host-id=test"]:
            with self.subTest(option=option):
                code, lines = self.run_entrypoint([option])
                self.assertEqual(code, 1, lines)
                self.assertFalse(any(l.startswith("python3 ") for l in lines), lines)


if __name__ == "__main__":
    unittest.main()
