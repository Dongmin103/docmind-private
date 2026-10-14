"""Production retrieval and maintained CI contracts for Python-only runtime."""

import ast
import asyncio
import logging
import math
import re
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

APP = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("mode", ["dense", "hybrid"])
def test_dealer_search_preserves_dense_lane_and_hybrid_control(monkeypatch, mode):
    namespace = {"VEC": list, "DEFAULT_MATCH_VECTOR_TOPN": 10, "dataclass": dataclass,
                 "asyncio": asyncio, "logging": logging, "math": math, "PAGERANK_FLD": "pagerank_fea", "TAG_FLD": "tag_fea"}
    expressions = ast.parse((APP / "common/doc_store/doc_store_base.py").read_text(encoding="utf8"))
    nodes = [n for n in expressions.body if isinstance(n, ast.ClassDef) and n.name in {"MatchTextExpr", "MatchDenseExpr", "FusionExpr", "OrderByExpr"}]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "expressions", "exec"), namespace)
    lexical = namespace["MatchTextExpr"](["content_ltks"], "lexical tokens", 128)
    dense = namespace["MatchDenseExpr"]("q_768_vec", [0.2] * 768, "float", "cosine", 128, {"similarity": 0.1})
    scope = Mock(side_effect=lambda condition, **kwargs: {**condition, "active_chunk_set_filter": "active-only"})
    for name in ["api", "api.db", "api.db.services", "api.db.services.active_chunk_scope_service"]:
        module = types.ModuleType(name)
        if name != "api.db.services.active_chunk_scope_service":
            module.__path__ = []
        else:
            module.ActiveChunkScopeService = SimpleNamespace(apply_to_condition=scope)
        monkeypatch.setitem(sys.modules, name, module)

    async def thread_pool_exec(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    namespace.update(thread_pool_exec=thread_pool_exec, rag_tokenizer=SimpleNamespace(fine_grained_tokenize=lambda text: text))
    tree = ast.parse((APP / "rag/nlp/search.py").read_text(encoding="utf8"))
    dealer = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Dealer")
    dealer.body = [n for n in dealer.body if getattr(n, "name", None) in {"search", "get_filters", "SearchResult"}]
    exec(compile(ast.Module(body=[dealer], type_ignores=[]), "production Dealer", "exec"), namespace)
    store = SimpleNamespace(search=Mock(return_value=object()), get_total=lambda response: 1,
                            get_doc_ids=lambda response: ["chunk"], get_highlight=lambda *args: {},
                            get_aggregation=lambda *args: [], get_fields=lambda *args: {"chunk": {"doc_id": "allowed"}})
    instance = namespace["Dealer"]()
    instance.dataStore = store
    instance.qryr = SimpleNamespace(question=lambda *args, **kwargs: (lexical, ["lexical"]))
    instance.get_vector = AsyncMock(return_value=dense)
    request = {"question": "nonempty question", "candidate_mode": mode, "candidate_vector_similarity_weight": 0.9,
               "knn_top_k": 128, "kb_ids": ["kb"], "doc_ids": ["allowed"], "available_int": 1,
               "must_not": {"removed_kwd": "source-deleted"}, "fields": ["source_version_id", "chunk_set_id"]}
    rank = {"pagerank_fea": 2}
    result = asyncio.run(instance.search(request, ["owner-index"], ["kb"], emb_mdl=object(), rank_feature=rank))
    args = store.search.call_args.args
    actual = args[3]
    if mode == "dense":
        assert actual == [dense]
    else:
        assert actual[:2] == [lexical, dense]
        assert isinstance(actual[2], namespace["FusionExpr"])
        assert actual[2].fusion_params == {"weights": "0.1,0.9"}
    assert args[2] == {"kb_id": ["kb"], "doc_id": ["allowed"], "available_int": 1,
                       "must_not": {"removed_kwd": "source-deleted"}, "active_chunk_set_filter": "active-only"}
    assert args[0] == ["source_version_id", "chunk_set_id"]
    assert args[7:9] == (["owner-index"], ["kb"])
    assert store.search.call_args.kwargs["rank_feature"] is rank
    scope.assert_called_once_with(instance.get_filters(request), kb_ids=["kb"], requested_doc_ids=["allowed"])
    assert result.ids == ["chunk"]
    assert result.query_vector == dense.embedding_data


@pytest.mark.parametrize("lanes", [["python"], ["web"], ["python", "web"]])
def test_ci_lanes_never_become_runtime_choices(lanes):
    source = (APP / ".github/workflows/sep-tests.yml").read_text(encoding="utf8")
    assert "test_lane: ${{ fromJSON(needs.ragflow_preflight.outputs.test_lanes) }}" in source
    assert "CI_TEST_LANE: ${{ matrix.test_lane }}" in source
    selector = re.search(r"^      API_PROXY_SCHEME: (.+)$", source, re.MULTILINE).group(1)
    for lane in lanes:
        assert lane in {"python", "web"}
        assert selector == "python"
    assert 'echo "API_PROXY_SCHEME=${API_PROXY_SCHEME}"' in source
    assert "if: env.CI_TEST_LANE == 'python'" in source
    assert "env.API_PROXY_SCHEME ==" not in source and "env.API_PROXY_SCHEME !=" not in source
    assert "api_proxy_schemes" not in source and "matrix.api_proxy_scheme" not in source
    compose = (APP / "docker/docker-compose.yml").read_text(encoding="utf8")
    assert "env_file:" in compose and ".env" in compose and "entrypoint.sh" in compose
    launcher = (APP / "docker/entrypoint.sh").read_text(encoding="utf8")
    assert 'case "${API_PROXY_SCHEME-python}" in' in launcher
    assert "    python) ;;" in launcher


@pytest.mark.parametrize("workflow", ["tests.yml", "sep-tests.yml"])
def test_maintained_workflows_retire_admin_cli_and_keep_sdk_rest(workflow):
    source = (APP / ".github/workflows" / workflow).read_text(encoding="utf8")
    for owner in ("admin/client", "ragflow_cli.py", "es_cli_test.log", "run_cli(", "RAGFlow CLI retrieval"):
        assert owner not in source
    assert "test/testcases/test_sdk_api" in source
    assert "test/testcases/restful_api" in source


def test_retired_engine_and_skill_mappings_are_absent():
    for name in ("infinity_mapping.json", "doc_meta_infinity_mapping.json", "message_infinity_mapping.json",
                 "skill_infinity_mapping.json", "os_mapping.json", "skill_es_mapping.json"):
        assert not (APP / "conf" / name).exists()
    assert (APP / "conf/mapping.json").exists()
    assert (APP / "conf/doc_meta_es_mapping.json").exists()


def test_retired_oceanbase_migration_tool_and_dependency_are_absent():
    assert not (APP / "tools/es-to-oceanbase-migration/pyproject.toml").exists()
    assert '"pyobvector==0.2.22"' not in (APP / "pyproject.toml").read_text(encoding="utf8")
    assert (APP / "tools/scripts/mysql_migration.py").exists()
    assert (APP / "tools/scripts/run_migrations.sh").exists()


@pytest.mark.parametrize("workflow", ["tests.yml", "sep-tests.yml"])
def test_every_maintained_ci_runtime_selector_is_written_as_python(workflow):
    source = (APP / ".github/workflows" / workflow).read_text(encoding="utf8")
    assert "      API_PROXY_SCHEME: python" in source
    assert 'echo "API_PROXY_SCHEME=${API_PROXY_SCHEME}"' in source
    assert "/^API_PROXY_SCHEME=/d" in source
