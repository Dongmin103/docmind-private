"""Offline API startup boundaries: no plugin/channel work, retained progress work.

Run directly with Python's unittest runner; no DB, SDKs, network or threads start.
The real entrypoint runs against doubles only at its external service boundaries.
"""

import ast
import builtins
import contextlib
import io
import runpy
import sys
import time
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

APP = Path(__file__).resolve().parents[3]
SERVER = APP / "api/ragflow_server.py"


def _module(name, **attributes):
    module = ModuleType(name)
    module.__dict__.update(attributes)
    return module


def _run_server(*, main=True, debug=False, reloader=False):
    events = []
    imported = []

    def record(name):
        return lambda *args, **kwargs: events.append((name, args, kwargs))

    class RuntimeConfig:
        DEBUG = False
        init_env = staticmethod(record("runtime_env"))
        init_config = staticmethod(record("runtime_config"))

    class Thread:
        def __init__(self, target, args=(), **kwargs):
            self.target = target
            self.args = args
            self.name = kwargs.get("name", target.__name__)

        def start(self):
            events.append(("thread", (self.name,), {}))

        def is_alive(self):
            return False

    class Timer:
        def __init__(self, interval, target):
            self.target = target
            events.append(("timer", (interval,), {}))

        def start(self):
            self.target()

    settings = _module("common.settings", HOST_IP="127.0.0.1", HOST_PORT=9380, init_settings=record("settings"), print_rag_settings=record("print_settings"))
    modules = {
        "api.apps": _module("api.apps", app=SimpleNamespace(run=record("http"))),
        "api.db.runtime_config": _module("api.db.runtime_config", RuntimeConfig=RuntimeConfig),
        "api.db.services.document_service": _module("api.db.services.document_service", DocumentService=SimpleNamespace(update_progress=record("progress"))),
        "common": _module("common", settings=settings),
        "common.settings": settings,
        "common.file_utils": _module("common.file_utils", get_project_base_directory=lambda: str(APP)),
        "api.db.db_models": _module("api.db.db_models", init_database_tables=record("db_init")),
        "api.db.init_data": _module("api.db.init_data", init_web_data=record("data_init"), init_superuser=record("superuser")),
        "common.versions": _module("common.versions", get_ragflow_version=lambda: "test"),
        "common.config_utils": _module("common.config_utils", show_configs=record("configs")),
        "common.mcp_tool_call_conn": _module("common.mcp_tool_call_conn", shutdown_all_mcp_sessions=record("mcp_shutdown")),
        "common.log_utils": _module("common.log_utils", init_root_logger=record("logger")),
        "rag.utils.redis_conn": _module("rag.utils.redis_conn", RedisDistributedLock=None),
        "agent.plugin": _module("agent.plugin", GlobalPluginManager=SimpleNamespace(load_plugins=record("plugins"))),
        "api.channels.bootstrap": _module("api.channels.bootstrap", start_channel_server=record("channels")),
    }
    real_import = builtins.__import__

    def track_import(name, *args, **kwargs):
        imported.append(name)
        return real_import(name, *args, **kwargs)

    argv = [str(SERVER)] + (["--debug"] if debug else [])
    env = {"RAGFLOW_DEBUGPY_LISTEN": "0", "WERKZEUG_RUN_MAIN": "true" if reloader else "false"}
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.dict(sys.modules, modules))
        stack.enter_context(patch("builtins.__import__", track_import))
        stack.enter_context(patch.object(sys, "argv", argv))
        stack.enter_context(patch.dict("os.environ", env))
        stack.enter_context(patch("threading.Thread", Thread))
        stack.enter_context(patch("threading.Timer", Timer))
        stack.enter_context(patch("signal.signal", record("signal")))
        stack.enter_context(patch("faulthandler.enable"))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        namespace = runpy.run_path(str(SERVER), run_name="__main__" if main else "offline_startup_test")
    return namespace, events, imported


class IntegrationRemovalTests(unittest.TestCase):
    def test_discovery_keeps_docmind_and_settings_without_integration_routes(self):
        # Execute the production discovery function over the real route tree.
        path = APP / "api/apps/__init__.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "search_pages_path")
        namespace = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)  # noqa: S102 - trusted repository source
        discovered = namespace["search_pages_path"](APP / "api/apps")
        names = {file.name for file in discovered}
        self.assertIn("docmind_api.py", names)
        self.assertIn("user_api.py", names)
        self.assertNotIn("plugin_api.py", names)
        self.assertNotIn("chat_channel_api.py", names)

    def test_cold_import_does_not_load_integration_packages(self):
        _, events, imported = _run_server(main=False)
        self.assertFalse(any(name == "agent.plugin" or name.startswith("api.channels") for name in imported), imported)
        self.assertEqual(events, [])

    def test_startup_runs_http_and_progress_without_plugins_or_channels(self):
        for debug, reloader, expected_threads in [(False, False, ["update_progress"]), (True, False, []), (True, True, ["update_progress"])]:
            with self.subTest(debug=debug, reloader=reloader):
                namespace, events, imported = _run_server(debug=debug, reloader=reloader)
                self.assertFalse(any(name == "agent.plugin" or name.startswith("api.channels") for name in imported), imported)
                self.assertEqual([args[0] for name, args, _ in events if name == "thread"], expected_threads)
                self.assertEqual(
                    [name for name, _, _ in events if name in {"db_init", "data_init", "runtime_env", "runtime_config", "http"}], ["db_init", "data_init", "runtime_env", "runtime_config", "http"]
                )
                self.assertEqual(next(kwargs for name, _, kwargs in events if name == "http"), {"host": "127.0.0.1", "port": 9380, "use_reloader": debug, "debug": False})
                self.assertTrue(namespace["stop_event"].is_set())

    def test_progress_worker_releases_lock_and_shutdown_stops_work(self):
        namespace, _, _ = _run_server(main=False)
        calls = []

        class Lock:
            def __init__(self, key, *, lock_value, timeout):
                calls.append((key, timeout))

            def acquire(self):
                return True

            def release(self):
                calls.append("release")

        def update():
            calls.append("update")
            namespace["stop_background_services"]()

        globals_ = namespace["update_progress"].__globals__
        globals_["RedisDistributedLock"] = Lock
        globals_["DocumentService"] = SimpleNamespace(update_progress=update)
        namespace["update_progress"]()
        namespace["update_progress"]()
        self.assertEqual(calls, [("update_progress", 60), "update", "release", ("update_progress", 60)])

    def test_answer_updates_preserve_earlier_conversation_history(self):
        # Load the real answer/history update and chunk formatter without DB imports.
        namespace = {"time": time}
        for relative, names in [
            ("rag/prompts/generator.py", {"get_value", "chunks_format"}),
            ("api/db/services/conversation_service.py", {"structure_answer"}),
        ]:
            path = APP / relative
            tree = ast.parse(path.read_text(encoding="utf-8"))
            functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
            exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)  # noqa: S102 - trusted repository source
        old = {"role": "assistant", "content": "Earlier answer", "id": "old"}
        question = {"role": "user", "content": "Next question", "id": "question"}
        conversation = SimpleNamespace(message=[old.copy(), question.copy()], reference=[{"chunks": []}])
        namespace["structure_answer"](conversation, {"answer": "Partial", "reference": {}, "final": False}, "answer", "session")
        answer = namespace["structure_answer"](
            conversation,
            {"answer": "Grounded answer ##0$$", "reference": {"chunks": [{"chunk_id": "chunk-1", "doc_id": "doc-1", "content_with_weight": "Evidence"}]}, "final": True},
            "answer",
            "session",
        )
        self.assertEqual(conversation.message[:2], [old, question])
        self.assertEqual(len(conversation.message), 3)
        self.assertEqual(conversation.message[-1]["content"], "Grounded answer ##0$$")
        self.assertEqual(answer["session_id"], "session")
        self.assertEqual(conversation.reference[-1]["chunks"][0]["id"], "chunk-1")
        self.assertEqual(conversation.reference[-1]["chunks"][0]["document_id"], "doc-1")


if __name__ == "__main__":
    unittest.main()
