"""Check the actual setuptools source selection without building/installing."""

import contextlib
import tomllib
import unittest
from pathlib import Path

from setuptools import Distribution
from setuptools.command.build_py import build_py

APP = Path(__file__).resolve().parents[3]


class PackageBoundaryTests(unittest.TestCase):
    def test_package_sources_keep_api_without_external_wechat_adapter(self):
        manifest = tomllib.loads((APP / "pyproject.toml").read_text(encoding="utf-8"))
        config = manifest["tool"]["setuptools"]
        distribution = Distribution({"packages": config["packages"], "package_dir": config.get("package-dir", {}), "script_name": "setup.py"})
        with contextlib.chdir(APP):
            builder = build_py(distribution)
            builder.ensure_finalized()
            sources = {Path(path).as_posix() for path in builder.get_source_files()}
        self.assertIn("api/ragflow_server.py", sources)
        self.assertFalse(any("chatgpt-on-wechat" in path or "chatgpt_on_wechat" in path for path in sources), sorted(sources))


if __name__ == "__main__":
    unittest.main()
