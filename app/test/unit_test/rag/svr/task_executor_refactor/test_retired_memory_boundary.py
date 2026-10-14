import ast
import asyncio
import pathlib
import sys
import types
import unittest
import unittest.mock
from contextlib import nullcontext

ROOT = pathlib.Path(__file__).resolve().parents[5]
SOURCE = ROOT / "rag/svr/task_executor_refactor/task_handler.py"


class RetiredMemoryBoundary(unittest.TestCase):
    def load_handler(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TaskHandler")
        owner.body = [
            n for n in owner.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in {"__init__", "_is_standard_chunking_task", "_validate_task_type", "handle_task", "handle"}
        ]
        for n in owner.body:
            n.decorator_list = [d for d in n.decorator_list if isinstance(d, ast.Name) and d.id == "staticmethod"]
        abort = unittest.mock.Mock()
        namespace = dict(
            RecordingContext=type("RecordingContext", (), {}),
            CANVAS_DEBUG_DOC_ID="debug",
            GRAPH_RAPTOR_FAKE_DOC_ID="fake",
            Optional=__import__("typing").Optional,
            BillingHook=object,
            TaskContext=object,
            abort_doc_chunking_counter=abort,
        )
        exec(compile(ast.Module(body=[owner], type_ignores=[]), str(SOURCE), "exec"), namespace)
        stub = types.ModuleType("rag.svr.task_executor_refactor.dataset_structure_merger")
        stub.STRUCTURE_MERGE_TASK_TYPES = {"structure"}
        stub.is_structure_merge_task = lambda task_type: task_type in stub.STRUCTURE_MERGE_TASK_TYPES
        self.addCleanup(lambda: sys.modules.pop(stub.__name__, None))
        sys.modules[stub.__name__] = stub
        return namespace["TaskHandler"], abort

    def test_retired_memory_rejected_before_any_context_work(self):
        for kind in ("memory", "MEMORY", " Memory "):
            for canceled in (False, True):
                for entrypoint in ("handle", "handle_task"):
                    handler, abort = self.load_handler()
                    ctx = types.SimpleNamespace(
                        task_type=kind, id="t", tenant_id="u", kb_id="k", doc_id="d", parse_run_id="p", has_canceled_func=unittest.mock.Mock(return_value=canceled), write_interceptor=None, raw_task={}
                    )
                    with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "Unsupported task type"):
                        asyncio.run(getattr(handler(ctx), entrypoint)())
                    abort.assert_not_called()
                    ctx.has_canceled_func.assert_not_called()

    def test_blank_task_routes_to_standard_ingestion(self):
        handler, abort = self.load_handler()
        ctx = types.SimpleNamespace(task_type="", id="t", tenant_id="u", kb_id="k", doc_id="", parse_run_id="p", has_canceled_func=unittest.mock.Mock(return_value=False))
        instance = handler(ctx)
        instance._bind_embedding_model = unittest.mock.AsyncMock(return_value=(nullcontext(), 768))
        instance._init_kb = unittest.mock.Mock()
        instance._run_standard_chunking = unittest.mock.AsyncMock()
        asyncio.run(instance.handle_task())
        instance._init_kb.assert_called_once_with(768)
        instance._run_standard_chunking.assert_awaited_once()
        abort.assert_not_called()

    def test_cold_web_initialization_has_only_retained_actions(self):
        tree = ast.parse((ROOT / "api/db/init_data.py").read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "init_web_data")
        retained = ["init_table", "init_docmind_shared_user", "update_document_number_in_init", "add_graph_templates", "add_compilation_templates"]
        calls = unittest.mock.Mock()
        namespace = {name: getattr(calls, name) for name in retained}
        namespace.update(time=types.SimpleNamespace(time=lambda: 0), logging=unittest.mock.Mock())
        # Removed repair/store functions are deliberately unavailable.
        exec(compile(ast.Module(body=[function], type_ignores=[]), "<cold-init>", "exec"), namespace)
        namespace["init_web_data"]()
        self.assertEqual(calls.mock_calls, [getattr(unittest.mock.call, name)() for name in retained])

    def test_blank_standard_classification(self):
        handler, _ = self.load_handler()
        self.assertTrue(handler._is_standard_chunking_task(""))
        self.assertFalse(handler._is_standard_chunking_task("MEMORY"))


if __name__ == "__main__":
    import unittest.mock

    unittest.main()
