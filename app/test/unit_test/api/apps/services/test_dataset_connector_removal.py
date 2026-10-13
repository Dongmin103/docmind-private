"""External connector requests cannot mutate datasets or register ingestion APIs."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

APP = Path(__file__).resolve().parents[5]


def _load_functions(path, names, **namespace):
    """Execute service functions without importing database/model runtimes."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    exec(compile(tree, str(path), "exec"), namespace)  # noqa: S102 - trusted repository functions only
    return SimpleNamespace(**namespace)


@pytest.mark.parametrize("operation", ["create_dataset", "update_dataset"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("connectors", [[], None, [{"id": "external-source", "auto_parse": "1"}]])
def test_dataset_service_rejects_connector_input_before_storage(operation, nested, connectors):
    # Any DB access here means unsupported input reached the write path.
    class UnavailableStorage:
        def __getattr__(self, name):
            pytest.fail(f"Unsupported connector request reached storage: {name}")

    module = _load_functions(
        APP / "api/apps/services/dataset_api_service.py",
        {operation},
        KnowledgebaseService=UnavailableStorage(),
    )
    fields = {"connectors": connectors}
    request = {"name": "example", **({"ext": fields} if nested else fields)}
    args = ("tenant", request) if operation == "create_dataset" else ("tenant", "dataset", request)

    ok, error = asyncio.run(getattr(module, operation)(*args))

    assert ok is False
    assert "connector" in error.lower()


@pytest.mark.parametrize("model_name", ["CreateDatasetReq", "UpdateDatasetReq"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("connectors", [[], None, [{"id": "external-source"}]])
def test_dataset_request_models_reject_external_connectors(model_name, nested, connectors):
    from pydantic import ValidationError

    from api.utils import validation_utils

    fields = {"connectors": connectors}
    request = {"name": "example", **({"ext": fields} if nested else fields)}
    if model_name == "UpdateDatasetReq":
        request["dataset_id"] = "12345678123412341234123456789abc"

    with pytest.raises(ValidationError, match="connector"):
        getattr(validation_utils, model_name)(**request)


@pytest.mark.parametrize("model_name", ["CreateDatasetReq", "UpdateDatasetReq"])
def test_dataset_request_without_connectors_still_validates(model_name):
    from api.utils import validation_utils

    request = {"name": "example", "ext": {"pagerank": 1}}
    if model_name == "UpdateDatasetReq":
        request["dataset_id"] = "12345678123412341234123456789abc"
    parsed = getattr(validation_utils, model_name)(**request).model_dump()
    assert parsed["name"] == "example"
    assert "connectors" not in parsed


def test_api_discovery_excludes_external_connector_routes():
    module = _load_functions(APP / "api/apps/__init__.py", {"search_pages_path"})
    discovered = module.search_pages_path(APP / "api/apps")
    assert not any(path.name == "connector_api.py" for path in discovered)
    assert any(path.name == "docmind_api.py" for path in discovered)
