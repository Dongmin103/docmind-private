"""Retired owners stay undiscovered; ordinary mutations and retrieval execute."""
import ast
import asyncio
import logging
import math
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, AsyncMock

import pytest

APP = Path(__file__).resolve().parents[5]


def production(path, name, namespace):
    tree = ast.parse((APP / path).read_text(encoding="utf8"))
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), namespace)
    return namespace[name]


def test_real_discovery_has_ordinary_owners_without_retired_owners():
    discover = production("api/apps/__init__.py", "search_pages_path", {})
    paths = {p.name for p in discover(APP / "api/apps")}
    assert {"chat_api.py", "openai_api.py", "attachment_api.py", "document_api.py", "file_api.py"} <= paths
    assert not {"agent_api.py", "mcp_api.py", "search_api.py", "bot_api.py", "file2document_api.py"} & paths
    aliases = (APP / "api/apps/backward_compat.py").read_text(encoding="utf8")
    assert "deprecated_agent_completions" not in aliases
    assert "deprecated_agents_openai_chat_completions" not in aliases
    assert "deprecated_chat_completions" in aliases


def test_no_retired_imports_or_models():
    prefixes = ("agent", "rag.flow", "mcp", "common.mcp_tool_call_conn", "api.db.services.canvas_service", "api.db.services.search_service", "api.db.services.mcp_server_service", "api.apps.services.canvas_replica_service")
    for owner in ["api", "rag", "common"]:
        for path in (APP / owner).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf8"))
            for node in ast.walk(tree):
                names = [node.module or ""] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names] if isinstance(node, ast.Import) else []
                assert not any(name == prefix or name.startswith(prefix + ".") for name in names for prefix in prefixes), str(path)
    tree = ast.parse((APP / "api/db/db_models.py").read_text(encoding="utf8"))
    names = {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
    assert not names & {"UserCanvas", "CanvasTemplate", "UserCanvasVersion", "MCPServer", "Search", "PipelineOperationLog", "Connector", "Connector2Kb", "SyncLogs", "ChatChannel"}
    assert {"Document", "Knowledgebase", "Conversation", "ParserRun", "DocmindSourceVersion"} <= names
    assert "add_graph_templates" not in (APP / "api/db/init_data.py").read_text(encoding="utf8")


@pytest.mark.parametrize("protected", [False, True])
def test_document_update_executes_chunk_status_metadata_and_evidence_guard(protected):
    class Protected(PermissionError):
        code = "DOCMIND_VERSION_PROTECTED"
    doc = SimpleNamespace(id="doc", name="source.pdf", type="pdf")
    req = {"chunk_method": "table", "enabled": 0, "meta_fields": {"author": "A"}}
    events = []
    def guard(*a, **kw):
        events.append("guard")
        if protected:
            raise Protected()
    ns = dict(get_request_json=AsyncMock(return_value=req), KnowledgebaseService=SimpleNamespace(query=lambda **kw: [1], get_by_id=lambda ident: (True, object())),
              DocumentService=SimpleNamespace(query=lambda **kw: [doc], assert_docmind_evidence_mutable=guard, get_by_id=lambda ident: (True, doc)),
              UpdateDocumentReq=lambda **kw: SimpleNamespace(parser_config=None, chunk_method=kw["chunk_method"], meta_fields=kw["meta_fields"]),
              validate_document_update_fields=lambda *a: (None, None), DocmindProtectedEvidenceError=Protected,
              update_chunk_method=lambda *a: events.append("chunk"), update_document_status_only=lambda *a: events.append("status"),
              DocMetadataService=SimpleNamespace(update_document_metadata=lambda *a: events.append("metadata") or True),
              map_doc_keys=lambda d: {"id": d.id}, get_result=lambda **kw: kw, get_error_data_result=lambda **kw: kw,
              ValidationError=ValueError, OperationalError=RuntimeError)
    fn = production("api/apps/restful_apis/document_api.py", "update_document", ns)
    result = asyncio.run(fn("tenant", "kb", "doc"))
    assert events == (["guard"] if protected else ["guard", "metadata", "chunk", "status"])
    assert result == ({"message": "DOCMIND_VERSION_PROTECTED"} if protected else {"data": {"id": "doc"}})


@pytest.mark.parametrize("name", ["search", "search_datasets"])
def test_dataset_retrieval_preserves_metadata_filter_and_compiled_exclusion(monkeypatch, name):
    def stub(module, **attrs):
        value = ModuleType(module)
        value.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, module, value)
    kb = SimpleNamespace(id="kb", tenant_id="tenant", embd_id="embedding")
    filter_call = AsyncMock(return_value=["filtered-doc"])
    retrieval = AsyncMock(return_value={"chunks": [], "total": 0})
    stub("api.db.joint_services.tenant_model_service", get_tenant_default_model_by_type=lambda *a: {})
    stub("api.db.services.doc_metadata_service", DocMetadataService=SimpleNamespace(get_flatted_meta_by_kbs=lambda *a: {}))
    stub("api.db.services.llm_service", LLMBundle=lambda *a: object())
    stub("api.db.services.user_service", UserTenantService=SimpleNamespace(query=lambda **kw: [SimpleNamespace(tenant_id="tenant")]))
    stub("common.constants", LLMType=SimpleNamespace(CHAT="chat", EMBEDDING="embedding", RERANK=SimpleNamespace(value="rerank")))
    stub("common.metadata_utils", apply_meta_data_filter=filter_call)
    stub("rag.app.tag", label_question=lambda *a: {})
    stub("rag.prompts.generator", cross_languages=AsyncMock(), keyword_extraction=AsyncMock())
    ns = dict(logging=logging, math=math, KnowledgebaseService=SimpleNamespace(accessible=lambda *a: True, get_by_id=lambda *a: (True, kb), get_by_ids=lambda *a: [kb], query=lambda **kw: [kb]),
              resolve_model_config=lambda *a: {}, validate_dataset_embedding_models=lambda *a: None,
              settings=SimpleNamespace(retriever=SimpleNamespace(retrieval=retrieval, retrieval_by_children=lambda chunks, tenants: chunks)))
    fn = production("api/apps/services/dataset_api_service.py", name, ns)
    req = {"dataset_ids": ["kb"], "question": "question", "meta_data_filter": {"method": "auto"}}
    result = asyncio.run(fn("kb", "tenant", req) if name == "search" else fn("tenant", req))
    assert result[0] is True
    filter_call.assert_awaited_once()
    assert retrieval.call_args.kwargs["doc_ids"] == ["filtered-doc"]
    assert retrieval.call_args.kwargs["must_not"] == {"exists": "compile_kwd"}


def test_document_enqueue_preserves_normal_contract_and_rejects_stale_pipeline(monkeypatch):
    def stub(module, **attrs):
        value = ModuleType(module)
        value.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, module, value)
    queue = Mock()
    gate = Mock()
    mutable = Mock()
    address = Mock(return_value=("bucket", "name"))
    stub("api.db.services.file2document_service", File2DocumentService=SimpleNamespace(get_storage_address=address))
    stub("api.db.services.task_service", queue_tasks=queue)
    stub("rag.parser_platform.gates", enforce_prequeue_gate=gate)
    ns = dict(ParserType=SimpleNamespace(NAIVE="naive", TABLE="table"))
    fn = production("api/db/services/document_service.py", "run", ns)
    owner = SimpleNamespace(assert_docmind_evidence_mutable=mutable)
    with pytest.raises(ValueError):
        fn(owner, "tenant", {"id": "doc", "pipeline_id": "retired"}, {})
    gate.assert_not_called()
    mutable.assert_not_called()
    queue.assert_not_called()
    doc = {"id": "doc", "parser_id": "naive"}
    fn(owner, "tenant", doc, {})
    queue.assert_called_once_with(doc, "bucket", "name", 0)
    mutable.assert_called_once_with("doc", "RUN_DOCUMENT_PARSE", actor_id="tenant")


@pytest.mark.parametrize("key", ["pipeline_id", "parse_type"])
@pytest.mark.parametrize("nested", [False, True])
def test_request_validator_rejects_retired_pipeline_options(key, nested):
    tree = ast.parse((APP / "api/utils/validation_utils.py").read_text(encoding="utf8"))
    for owner_name in ["CreateDatasetReq", "UpdateDocumentReq"]:
        owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == owner_name)
        node = next(n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name == "reject_document_pipeline")
        node.decorator_list = []
        ns = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "validator", "exec"), ns)
        value = {"ext": {key: "retired"}} if nested else {key: "retired"}
        with pytest.raises(ValueError, match="not supported"):
            ns[node.name](object, value)
        assert ns[node.name](object, {"chunk_method": "naive"}) == {"chunk_method": "naive"}


def test_account_protected_evidence_blocks_all_cleanup():
    class Protected(PermissionError):
        code = "DOCMIND_VERSION_PROTECTED"
    kb = Mock(get_kb_ids=Mock(return_value=["kb"]))
    docs = Mock(get_all_doc_ids_by_kb_ids=Mock(return_value=[{"id": "doc"}]),
                get_all_docs_by_creator_id=Mock(return_value=[]),
                assert_documents_docmind_evidence_mutable=Mock(side_effect=Protected()))
    storage = Mock()
    user = Mock(filter_by_id=Mock(return_value=SimpleNamespace(id="user", is_active="0", is_superuser=False)))
    ns = dict(UserService=user, UserTenantService=SimpleNamespace(get_user_tenant_relation_by_user_id=lambda ident: [{"role": "owner", "tenant_id": "user"}]),
              UserTenantRole=SimpleNamespace(OWNER=SimpleNamespace(value="owner")), ActiveEnum=SimpleNamespace(ACTIVE=SimpleNamespace(value="1")),
              KnowledgebaseService=kb, DocumentService=docs, DocmindProtectedEvidenceError=Protected,
              settings=SimpleNamespace(STORAGE_IMPL=storage))
    fn = production("api/db/joint_services/user_account_service.py", "delete_user_data", ns)
    assert fn("user") == {"success": False, "message": "DOCMIND_VERSION_PROTECTED"}
    user.delete_by_id.assert_not_called()
    kb.delete_by_ids.assert_not_called()
    assert storage.mock_calls == []


def test_retained_file_read_permission():
    file = object()
    for exists, permitted, expected in [(False, True, (False, "document not found")), (True, False, (False, "no authorization")), (True, True, (True, file))]:
        permission = Mock(return_value=permitted)
        ns = dict(FileService=SimpleNamespace(get_by_id=lambda ident: (exists, file)), check_file_team_permission=permission)
        fn = production("api/apps/services/file_read_service.py", "get_file_content", ns)
        assert fn("tenant", "file") == expected
        if exists:
            permission.assert_called_once_with(file, "tenant")
        else:
            permission.assert_not_called()


def test_blank_task_reaches_standard_chunking():
    from contextlib import nullcontext
    handler_path = "rag/svr/task_executor_refactor/task_handler.py"
    guard = production(handler_path, "_validate_task_type", {})
    chunk = AsyncMock()
    init = Mock()
    context = SimpleNamespace(task_type="", id="task", doc_id="", has_canceled_func=Mock(return_value=False))
    bind = AsyncMock(return_value=(nullcontext(), 768))
    owner = SimpleNamespace(_task_context=context, _validate_task_type=guard, _bind_embedding_model=bind,
                            _init_kb=init, _run_standard_chunking=chunk)
    fn = production(handler_path, "handle", {})
    asyncio.run(fn(owner))
    bind.assert_awaited_once()
    init.assert_called_once_with(768)
    chunk.assert_awaited_once()


def test_document_list_uses_only_file_joins():
    joins = []
    class Field:
        def __eq__(self, other):
            return self
        def asc(self):
            return self
    field = Field()
    class Query:
        def join(self, model, **kw):
            joins.append(model)
            return self
        def where(self, *a): return self
        def order_by(self, *a): return self
        def count(self): return 0
        def paginate(self, *a): return self
        def dicts(self): return []
    file = SimpleNamespace(id=field)
    link = SimpleNamespace(document_id=field, file_id=field)
    model = SimpleNamespace(id=field, kb_id=field, select=lambda *a: Query(), getter_by=lambda order: field)
    owner = SimpleNamespace(get_cls_model_fields=lambda: [], model=model)
    fn = production("api/db/services/document_service.py", "get_list", dict(File=file, File2Document=link))
    assert fn(owner, "kb", 1, 10, "create_time", False, "", None, None) == ([], 0)
    assert joins == [link, file]
