"""Offline retained-backend behavior with network/ORM boundaries replaced."""

import ast
import asyncio
import json
import logging
import ssl
from functools import wraps
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

APP = Path(__file__).resolve().parents[3]


def production(path, names, namespace):
    tree = ast.parse((APP / path).read_text(encoding="utf8"))
    nodes = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, "exec"), namespace)
    return namespace


def test_mysql_pool_preserves_config_and_retry_options():
    pool = Mock()
    ns = dict(settings=SimpleNamespace(DATABASE={"name": "db", "max_connections": 100, "stale_timeout": 300, "password": "synthetic"}, DATABASE_TYPE="mysql"),
              PooledDatabase={"MYSQL": SimpleNamespace(value=pool)}, logging=logging)
    production("api/db/db_models.py", {"BaseDataBase"}, ns)["BaseDataBase"]()
    pool.assert_called_once_with("db", max_connections=100, stale_timeout=300, password="synthetic", max_retries=5, retry_delay=1)
    assert ns["settings"].DATABASE["name"] == "db"


def test_mysql_lost_connection_retries_and_sql_error_does_not():
    class OperationalError(Exception):
        pass

    class InterfaceError(Exception):
        pass

    class Pool:
        def __init__(self, *args, **kwargs):
            self.execute = Mock(side_effect=[OperationalError(2013, "lost"), "cursor"])
            self.close = Mock()
            self.connect = Mock()

        def execute_sql(self, *args):
            return self.execute(*args)

    delay = Mock()
    ns = dict(PooledMySQLDatabase=Pool, OperationalError=OperationalError, InterfaceError=InterfaceError, logging=logging, time=SimpleNamespace(sleep=delay))
    cls = production("api/db/db_models.py", {"RetryingPooledMySQLDatabase"}, ns)["RetryingPooledMySQLDatabase"]
    pool = cls("db", max_retries=1, retry_delay=2)
    assert pool.execute_sql("SELECT 1") == "cursor"
    pool.close.assert_called_once()
    pool.connect.assert_called_once()
    delay.assert_called_once_with(2)
    pool.execute.side_effect = OperationalError(1064, "syntax")
    with pytest.raises(OperationalError):
        pool.execute_sql("bad SQL")
    assert pool.execute.call_count == 3


def test_mysql_lock_retains_parameters_and_finite_negative_timeout():
    cursor = SimpleNamespace(fetchone=lambda: (1,))
    db = SimpleNamespace(execute_sql=Mock(return_value=cursor))
    ns = dict(logging=logging, DB=db, PooledMySQLDatabase=object, wraps=wraps, with_retry=lambda **_: lambda fn: fn)
    lock = production("api/db/db_models.py", {"MysqlDatabaseLock"}, ns)["MysqlDatabaseLock"]("tenant:scope", -1, db=db)
    assert lock.timeout == 60
    assert lock.lock() is True
    assert lock.unlock() is True
    assert db.execute_sql.call_args_list[0].args == ("SELECT GET_LOCK(%s, %s)", ("tenant:scope", 60))
    assert db.execute_sql.call_args_list[1].args == ("SELECT RELEASE_LOCK(%s)", ("tenant:scope",))


def test_mysql_longtext_json_list_and_migration_selection():
    class TextField:
        def __init__(self, **kwargs):
            self.options = kwargs

    ns = dict(TextField=TextField, json_dumps=json.dumps, json_loads=json.loads)
    production("api/db/db_models.py", {"LongTextField", "JSONField", "ListField"}, ns)
    assert ns["LongTextField"].field_type == "LONGTEXT"
    field = ns["JSONField"](null=False, default={})
    assert field.options == {"null": False, "default": {}}
    assert field.python_value(field.db_value({"name": "한글"})) == {"name": "한글"}
    assert field.db_value(None) == "{}"
    assert ns["ListField"]().python_value(None) == []
    mysql = Mock()
    ns = {"_update_tenant_llm_to_id_primary_key_mysql": mysql}
    production("api/db/db_models.py", {"update_tenant_llm_to_id_primary_key"}, ns)["update_tenant_llm_to_id_primary_key"]()
    mysql.assert_called_once_with()


@pytest.mark.parametrize("secure,verify", [(False, True), (True, True), (True, False)])
def test_minio_initialization_preserves_tls_and_credentials(secure, verify):
    constructor = Mock()
    pool_manager = Mock()
    cfg = {"host": "minio.test:9000", "user": "synthetic", "password": "synthetic", "secure": secure, "verify": verify}
    ns = dict(settings=SimpleNamespace(MINIO=cfg), Minio=constructor, urllib3=SimpleNamespace(PoolManager=pool_manager), ssl=ssl, logging=logging)
    production("rag/utils/minio_conn.py", {"_build_minio_http_client", "RAGFlowMinio"}, ns)["RAGFlowMinio"]()
    assert constructor.call_args.args == ("minio.test:9000",)
    assert constructor.call_args.kwargs["secure"] is secure
    assert constructor.call_args.kwargs["access_key"] == "synthetic"
    if not verify:
        pool_manager.assert_called_once_with(cert_reqs=ssl.CERT_NONE)


@pytest.mark.parametrize("field_map", [{}, {"amount": "integer"}])
def test_field_map_dataset_uses_normal_scoped_retrieval(field_map):
    class ReachedPrompt(Exception):
        pass

    def prompt(reference, max_tokens):
        assert reference["chunks"] == [{"doc_id": "allowed"}]
        raise ReachedPrompt

    retrieval = AsyncMock(return_value={"chunks": [{"doc_id": "allowed"}], "doc_aggs": [], "total": 1})
    retriever = SimpleNamespace(retrieval=retrieval, retrieval_by_children=lambda chunks, tenants: chunks)
    kb = SimpleNamespace(id="kb", tenant_id="owner", parser_config={"field_map": field_map})
    dialog = SimpleNamespace(kb_ids=["kb"], llm_id="chat", tenant_llm_id=None, tenant_id="viewer", meta_data_filter=None,
                             prompt_config={"parameters": [{"key": "knowledge", "optional": False}], "system": "{knowledge}"},
                             top_n=5, similarity_threshold=0.2, vector_similarity_weight=0.5, top_k=128)
    ns = dict(logging=logging, timer=lambda: 1, settings=SimpleNamespace(retriever=retriever),
              _should_use_web_search=lambda *args: False, has_web_search_provider=lambda *args: False,
              resolve_model_type=lambda *args: ["chat"], resolve_model_config=lambda *args: {"model_type": "chat", "max_tokens": 8192},
              LLMType=SimpleNamespace(CHAT="chat", VISION="vision"),
              TenantLangfuseService=SimpleNamespace(filter_by_tenant=lambda **kwargs: None),
              get_models=lambda *args, **kwargs: ([kb], object(), None, object(), None),
              get_files_content=lambda *args: ("", [], []), _resolve_reference_metadata=lambda *args, **kwargs: (False, []),
              KnowledgebaseService=SimpleNamespace(get_field_map=lambda *args: field_map),
              label_question=lambda *args: "", kb_prompt=prompt)
    from datetime import datetime, timezone
    ns.update(datetime=datetime, timezone=timezone)
    chat = production("api/db/services/dialog_service.py", {"async_chat"}, ns)["async_chat"]

    async def run():
        with pytest.raises(ReachedPrompt):
            await anext(chat(dialog, [{"role": "user", "content": "question", "doc_ids": ["allowed"]}]))
    asyncio.run(run())
    assert retrieval.await_args.args[2:4] == (["owner"], ["kb"])
    assert retrieval.await_args.kwargs["doc_ids"] == ["allowed"]
    assert retrieval.await_args.kwargs["knn_top_k"] == 128


@pytest.mark.parametrize("total,expected", [(1, ["document"]), (3, None)])
def test_es_metadata_pushdown_preserves_namespace_shape_and_overflow(monkeypatch, total, expected):
    monkeypatch.syspath_prepend(str(APP))
    response = {"hits": {"hits": [{"_source": {"id": "document"}}], "total": {"value": total, "relation": "eq"}}}
    client = SimpleNamespace(search=Mock(return_value=response))
    ns = {"settings": SimpleNamespace(docStoreConn=SimpleNamespace(es=client)), "logging": logging, "Any": object}
    production("api/db/services/doc_metadata_service.py", {"_es_response_total"}, ns)
    tree = ast.parse((APP / "api/db/services/doc_metadata_service.py").read_text(encoding="utf8"))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_filter_doc_ids_by_metadata_es")
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), "metadata", "exec"), ns)
    assert ns[node.name](object(), "meta-owner", ["kb"], [{"key": "amount", "op": ">", "value": 3}], "and", 2) == expected
    assert client.search.call_args.kwargs["index"] == "meta-owner"
    body = client.search.call_args.kwargs["body"]
    assert body["_source"] == ["id"]
    assert body["size"] == 2
    assert body["track_total_hits"] is True
    assert "kb" in json.dumps(body["query"])
    assert "amount" in json.dumps(body["query"])
