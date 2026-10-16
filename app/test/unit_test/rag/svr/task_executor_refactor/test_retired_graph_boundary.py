"""Offline graph retirement boundaries execute production definitions."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import pytest

APP = Path(__file__).resolve().parents[5]
RETIRED = "dataflow dataflow_rerun dataflow_debug evaluation reembedding clone graphrag graph raptor mindmap wiki artifact skill structure_graph structure_mindmap timeline session_graph session_essence structure structuregraph structuremindmap sessiongraph sessionessence".split()

def load_guard():
    tree = ast.parse((APP / "rag/svr/task_executor_refactor/task_handler.py").read_text(encoding="utf8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TaskHandler")
    node = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_validate_task_type")
    node.decorator_list = []
    ns = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "guard", "exec"), ns)
    return ns["_validate_task_type"]

@pytest.mark.parametrize("name", RETIRED)
def test_reject_retired_task_spellings(name):
    with pytest.raises(ValueError, match="Unsupported task type"):
        load_guard()("  " + name.upper() + "  ")

def test_blank_standard_task_allowed():
    assert load_guard()("  ") == ""

def test_shared_chat_route_dispatches_ordinary_chat():
    tree = ast.parse((APP / "api/apps/restful_apis/chat_api.py").read_text(encoding="utf8"))
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "session_completion")
    calls = [n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "async_chat" in calls
    assert "rag_agent" not in calls

def test_no_retired_package_imports_in_retained_runtime():
    for path in (APP / "api").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                assert not (n.module or "").startswith(("rag.graphrag", "rag.advanced_rag")), str(path)

from unittest.mock import Mock, AsyncMock
import json
import logging
import math
import time
from copy import deepcopy

def production_defs(path, names, namespace):
    tree = ast.parse((APP / path).read_text(encoding="utf8"))
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    for n in nodes:
        n.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, "exec"), namespace)
    return namespace

@pytest.mark.parametrize("name", RETIRED)
@pytest.mark.parametrize("entrypoint", ["handle", "handle_task"])
@pytest.mark.parametrize("canceled", [False, True])
def test_entrypoints_reject_before_cleanup_or_model_binding(name, entrypoint, canceled):
    tree = ast.parse((APP / "rag/svr/task_executor_refactor/task_handler.py").read_text(encoding="utf8"))
    owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TaskHandler")
    owner.body = [n for n in owner.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in {"__init__", "_validate_task_type", "_is_standard_chunking_task", "handle", "handle_task"}]
    for n in owner.body:
        n.decorator_list = [d for d in n.decorator_list if isinstance(d, ast.Name) and d.id == "staticmethod"]
    abort = Mock()
    ns = dict(Optional=__import__("typing").Optional, BillingHook=object, TaskContext=object, abort_doc_chunking_counter=abort)
    exec(compile(ast.Module(body=[owner], type_ignores=[]), "handler", "exec"), ns)
    ctx = SimpleNamespace(task_type=" " + name.upper() + " ", has_canceled_func=Mock(return_value=canceled))
    instance = ns["TaskHandler"](ctx)
    instance._bind_embedding_model = AsyncMock()
    with pytest.raises(ValueError):
        asyncio.run(getattr(instance, entrypoint)())
    ctx.has_canceled_func.assert_not_called()
    instance._bind_embedding_model.assert_not_called()
    abort.assert_not_called()

@pytest.mark.parametrize("remaining, aborted, cancellations, result, calls", [
    (1, False, [False], True, ["credit"]),
    (-1, False, [False], True, ["credit"]),
    (-1, True, [False], True, ["credit"]),
    (0, False, [False, False], True, ["credit", "clear"]),
    (0, False, [True], False, ["abort"]),
    (0, False, [False, True], False, ["credit", "clear", "abort"]),
])
def test_gate_with_stored_graph_flags(remaining, aborted, cancellations, result, calls):
    events = []
    ns = dict(LLMBundle=object, logging=logging, timer=lambda: 0,
              abort_doc_chunking_counter=lambda *a, **k: events.append("abort"),
              is_doc_chunking_aborted=lambda *a, **k: aborted,
              credit_doc_chunking_task=lambda *a, **k: (events.append("credit"), remaining)[1],
              clear_doc_chunking_counter=lambda *a, **k: events.append("clear"))
    production_defs("rag/svr/task_executor_refactor/chunk_post_processor.py", ["run_document_post_chunking_if_last"], ns)
    ctx = SimpleNamespace(id="task", doc_id="doc", parse_run_id="run", name="doc", from_page=0, to_page=1,
                          write_interceptor=None, has_canceled_func=Mock(side_effect=cancellations), progress_cb=Mock(),
                          parser_config={"raptor": {"use_raptor": True, "do_raptor": True}, "graphrag": {"use_graphrag": True}, "compilation_template_group_id": ["old"]})
    handler = SimpleNamespace(_task_context=ctx)
    assert asyncio.run(ns["run_document_post_chunking_if_last"](handler, object(), 768, 0, 1, 1)) is result
    assert events == calls

@pytest.mark.parametrize("streaming", [False, True])
def test_ordinary_completion_persists_history_and_citations(streaming):
    req = {"chat_id": "chat", "session_id": "session", "stream": streaming, "pass_all_history_messages": True}
    question = {"role": "user", "content": "question", "id": "message"}
    refs = {"chunks": [{"id": "chunk", "doc_id": "doc", "content_with_weight": "source"}], "doc_aggs": [{"doc_id": "doc"}]}
    conv = SimpleNamespace(id="session", dialog_id="chat", message=[], reference=[])
    conv.to_dict = lambda: {"message": deepcopy(conv.message), "reference": deepcopy(conv.reference)}
    dia = SimpleNamespace(llm_id="model", tenant_id="user")
    writes = []
    async def thread_exec(fn, *args, **kwargs): return fn(*args, **kwargs)
    async def chat(dialog, messages, stream, **kwargs):
        assert dialog is dia and messages == [question] and stream == streaming
        if stream:
            yield {"answer": "Answer ##0$$", "reference": {}, "final": False}
            yield {"answer": "", "reference": deepcopy(refs), "final": True}
        else:
            yield {"answer": "Answer ##0$$", "reference": deepcopy(refs), "final": True}
    class Response:
        def __init__(self, iterator, **kwargs): self.iterator, self.headers = iterator, SimpleNamespace(add_header=lambda *a: None)
    ns = dict(async_chat=chat, deepcopy=deepcopy, json=json, math=math, logging=logging,
              get_request_json=AsyncMock(return_value=req),
              _normalize_completion_messages=lambda r: (([question], [question]), None),
              _ensure_owned_chat=AsyncMock(return_value=True),
              pop_generation_config=lambda r: {},
              thread_pool_exec=thread_exec, current_user=SimpleNamespace(id="user"),
              DialogService=SimpleNamespace(get_by_id=lambda ident: (True, dia)),
              ConversationService=SimpleNamespace(get_by_id=lambda ident: (True, conv), update_by_id=lambda *a: writes.append(a)),
              get_json_result=lambda **kwargs: kwargs, get_data_error_result=lambda **kwargs: kwargs,
              server_error_response=lambda e: (_ for _ in ()).throw(e), Response=Response)
    ns.update(time=time, chunks_format=lambda reference: reference.get("chunks", []))
    production_defs("api/db/services/conversation_service.py", ["structure_answer"], ns)
    production_defs("api/apps/restful_apis/chat_api.py", ["session_completion", "_get_bool_request_flag", "_sanitize_json_floats"], ns)
    async def run():
        response = await ns["session_completion"]()
        if streaming:
            payloads = [json.loads(item.removeprefix("data:")) async for item in response.iterator]
            assert payloads[-2]["data"]["reference"]["chunks"][0]["id"] == "chunk"
        else:
            assert response["data"]["answer"] == "Answer ##0$$"
            assert response["data"]["reference"]["chunks"][0]["id"] == "chunk"
    asyncio.run(run())
    assert len(writes) == 1
    assert conv.message[-1]["content"] == "Answer ##0$$"
    assert conv.reference[-1]["chunks"][0]["id"] == "chunk"

class MemoryRedis:
    def __init__(self): self.data, self.writes, self.decrements = {}, [], 0
    def get(self, key): return self.data.get(key)
    def set(self, key, value, exp=None):
        self.data[key] = value
        self.writes.append((key, value, exp))
        return True
    def delete(self, key): self.data.pop(key, None)
    def set_if_absent(self, key, value, exp):
        if key in self.data: return False
        return self.set(key, value, exp)
    def decrby(self, key, count):
        self.decrements += 1
        self.data[key] = int(self.data[key]) - count
        return self.data[key]

def test_gate_credits_once_waits_for_last_and_clears_real_counter():
    redis = MemoryRedis()
    ns = dict(REDIS_CONN=redis, DOC_CHUNKING_COUNTER_TTL_SECONDS=7 * 24 * 3600, logging=logging, timer=lambda: 0, LLMBundle=object)
    production_defs("api/db/services/task_service.py", ["_doc_chunking_pending_key", "_doc_chunking_aborted_key", "_doc_chunking_done_key", "seed_doc_chunking_counter", "clear_doc_chunking_counter", "abort_doc_chunking_counter", "is_doc_chunking_aborted", "credit_doc_chunking_task"], ns)
    production_defs("rag/svr/task_executor_refactor/chunk_post_processor.py", ["run_document_post_chunking_if_last"], ns)
    assert ns["seed_doc_chunking_counter"]("doc", 2, parse_run_id="run")
    ctx = SimpleNamespace(id="first", doc_id="doc", parse_run_id="run", write_interceptor=None,
                          has_canceled_func=lambda task: False, progress_cb=Mock(), name="doc", from_page=0, to_page=1)
    handler = SimpleNamespace(_task_context=ctx)
    for task, pending in [("first", 1), ("first", 1), ("last", None), ("last", None)]:
        ctx.id = task
        assert asyncio.run(ns["run_document_post_chunking_if_last"](handler, None, 768, 0, 1, 1))
        assert redis.get("doc:chunking_pending:doc:run") == pending
    assert redis.decrements == 2


def test_neutral_cache_encoding_keys_ttls_and_bytes():
    class Hash:
        def __init__(self): self.payload = b""
        def update(self, value): self.payload += value
        def hexdigest(self): return self.payload.hex()
    redis = MemoryRedis()
    ns = dict(xxhash=SimpleNamespace(xxh64=Hash), REDIS_CONN=redis, json=json)
    production_defs("rag/utils/llm_cache.py", ["get_llm_cache", "set_llm_cache", "get_tags_from_cache", "set_tags_to_cache"], ns)
    history, conf = [{"role": "user", "content": "한글"}], {"temperature": 0}
    ns["set_llm_cache"]("model", "질문", "답변", history, conf)
    expected = ("model" + "질문" + str(history) + str(conf)).encode("utf8").hex()
    assert redis.writes[-1] == (expected, "답변".encode("utf8"), 86400)
    assert ns["get_llm_cache"]("model", "질문", history, conf) == "답변".encode("utf8")
    tags = {"label": 2}
    ns["set_tags_to_cache"](["kb"], tags)
    assert redis.writes[-1] == (str(["kb"]).encode("utf8").hex(), json.dumps(tags).encode("utf8"), 600)
    assert ns["get_tags_from_cache"](["kb"]) == json.dumps(tags).encode("utf8")
    assert ns["get_llm_cache"]("missing", "", [], {}) is None


def test_route_discovery_keeps_chat_search_chunk_and_file_history():
    routes = {}
    for path in (APP / "api/apps/restful_apis").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)): continue
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "route" and decorator.args and isinstance(decorator.args[0], ast.Constant):
                    routes[decorator.args[0].value] = node.name
    assert routes["/chat/completions"] == "session_completion"
    assert "/datasets/search" in routes
    assert "/datasets/<dataset_id>/documents/<document_id>/chunks" in routes
    assert "/workspace-files/<file_id>/versions" in routes
    assert not [r for r in routes if any(part in r for part in ["/mindmap", "/artifacts", "/skills", "/navigation", "/compilation", "/structure_graph", "/knowledge_graph", "/index"])]
    assert not (APP / "api/apps/restful_apis/compilation_template_api.py").exists()


def test_orm_does_not_discover_compilation_tables_or_add_graph_columns():
    source = (APP / "api/db/db_models.py").read_text(encoding="utf8")
    tree = ast.parse(source)
    assert not [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name in {"CompilationTemplate", "CompilationTemplateGroup"}]
    assert "graphrag_task_id" not in source
    assert "compilation_template" not in source


def test_docmind_task_context_shares_neutral_limiter_singleton(monkeypatch):
    import importlib.util
    import sys
    import types
    from common.asyncio_utils import LoopLocalSemaphore
    from rag.svr.task_executor_refactor.task_context import TaskContext
    monkeypatch.setenv("MAX_CONCURRENT_CHATS", "7")
    monkeypatch.setitem(sys.modules, "xxhash", types.ModuleType("xxhash"))
    redis = types.ModuleType("rag.utils.redis_conn")
    redis.REDIS_CONN = MemoryRedis()
    monkeypatch.setitem(sys.modules, redis.__name__, redis)
    spec = importlib.util.spec_from_file_location("rag.utils.llm_cache", APP / "rag/utils/llm_cache.py")
    cache = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, cache)
    spec.loader.exec_module(cache)
    canceled = types.ModuleType("api.db.services.task_service")
    canceled.has_canceled = lambda *a: False
    monkeypatch.setitem(sys.modules, canceled.__name__, canceled)
    limit_spec = importlib.util.spec_from_file_location("api.apps.services.docmind_ingestion_limits", APP / "api/apps/services/docmind_ingestion_limits.py")
    limits = importlib.util.module_from_spec(limit_spec)
    monkeypatch.setitem(sys.modules, limit_spec.name, limits)
    limit_spec.loader.exec_module(limits)
    tree = ast.parse((APP / "api/apps/services/docmind_ingestion_runtime.py").read_text(encoding="utf8"))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_task_context")
    node.decorator_list = []
    ns = dict(TemporaryParserWorkspace=object, ParserPlatformConfig=object, _safe_progress=lambda *a, **k: None)
    exec(compile(ast.Module(body=[node], type_ignores=[]), "context", "exec"), ns)
    payload = {"id": "task", "tenant_id": "user", "parse_run_id": "run", "chunk_set_id": "set"}
    first = ns["_task_context"](payload, object(), object())
    second = ns["_task_context"](payload, object(), object())
    assert isinstance(first, TaskContext)
    assert first.chat_limiter is second.chat_limiter is cache.chat_limiter
    assert isinstance(cache.chat_limiter, LoopLocalSemaphore)
    assert cache.chat_limiter._value == 7
    async def run():
        async with cache.chat_limiter:
            assert cache.chat_limiter._get()._value == 6
    asyncio.run(run())
    asyncio.run(run())


def test_real_xxhash_cache_keys_match_original_recipe():
    xxhash = pytest.importorskip("xxhash")
    redis = MemoryRedis()
    ns = dict(xxhash=xxhash, REDIS_CONN=redis, json=json)
    production_defs("rag/utils/llm_cache.py", ["get_llm_cache", "set_llm_cache", "get_tags_from_cache", "set_tags_to_cache"], ns)
    ns["set_llm_cache"]("model", "question", "answer", [], {})
    assert redis.writes[-1] == (xxhash.xxh64(b"modelquestion[]{}").hexdigest(), b"answer", 86400)
    assert ns["get_llm_cache"]("model", "question", [], {}) == b"answer"
    ns["set_tags_to_cache"](["kb"], {"tag": 1})
    assert redis.writes[-1] == (xxhash.xxh64(b"['kb']").hexdigest(), b'{"tag": 1}', 600)
