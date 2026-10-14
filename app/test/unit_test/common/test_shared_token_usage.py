"""Import the real token module offline; only tokenizer/cache dependencies are replaced."""
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

APP = Path(__file__).resolve().parents[3]


@pytest.fixture
def token_module(monkeypatch, tmp_path):
    tokenizer = ModuleType("tiktoken")
    tokenizer.get_encoding = lambda name: SimpleNamespace(encode=lambda text: list(text), decode=lambda values: "".join(values))
    files = ModuleType("common.file_utils")
    files.get_project_base_directory = lambda *parts: str(tmp_path.joinpath(*parts))
    monkeypatch.setitem(sys.modules, "tiktoken", tokenizer)
    monkeypatch.setitem(sys.modules, "common.file_utils", files)
    spec = importlib.util.spec_from_file_location("common.token_utils", APP / "common/token_utils.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "common.token_utils", module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("response, expected", [
    ({"usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 9}}, (3, 4, 9)),
    (SimpleNamespace(usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4, total_tokens=9)), (3, 4, 9)),
    ({"usage": {"input_tokens": "2", "output_tokens": "5"}}, (2, 5, 7)),
    (SimpleNamespace(usage=SimpleNamespace(input_tokens=2, output_tokens=5)), (2, 5, 7)),
    ({"usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 0}}, (3, 4, 7)),
    ({"usage": {"total_tokens": 8}}, (0, 0, 8)),
    ({"usage": {}}, (0, 0, 0)),
    ({}, (0, 0, 0)),
    (None, (0, 0, 0)),
])
def test_shared_response_usage_import_and_behavior(token_module, response, expected):
    from common.token_utils import usage_from_response
    assert usage_from_response is token_module.usage_from_response
    assert usage_from_response(response) == dict(zip(("prompt_tokens", "completion_tokens", "total_tokens"), expected))
    assert not hasattr(token_module, "token_usage_sink")
    assert not hasattr(token_module, "langfuse_run_attrs")


def test_shared_helper_is_exported_for_every_chat_model_import(token_module):
    import ast
    source = ast.parse((APP / "rag/llm/chat_model.py").read_text(encoding="utf8"))
    names = [alias.name for node in source.body if isinstance(node, ast.ImportFrom) and node.module == "common.token_utils" for alias in node.names]
    assert "usage_from_response" in names
    namespace = {}
    exec("from common.token_utils import " + ", ".join(names), namespace)
    assert all(callable(namespace[name]) for name in names)
