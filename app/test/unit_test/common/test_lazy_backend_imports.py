"""Backend selection must not require or initialize unrelated driver packages."""

import importlib.abc
import importlib.util
import sys
import types
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[3]
DRIVERS = {
    "rag.utils.es_conn": "ESConnection",
    "rag.utils.minio_conn": "RAGFlowMinio",
}


class DriverLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Track real imports while replacing network-bearing driver constructors."""

    def __init__(self):
        self.loaded = []
        self.constructed = []
        self.fail = set()

    def find_spec(self, fullname, path=None, target=None):
        if fullname in DRIVERS:
            return importlib.util.spec_from_loader(fullname, self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        name = module.__name__
        self.loaded.append(name)

        def construct():
            self.constructed.append(name)
            if name in self.fail:
                raise RuntimeError("driver unavailable")
            return types.SimpleNamespace(
                driver=name,
                health=lambda: {"status": "healthy"},
                get_cluster_stats=lambda: {"nodes": 1},
                get_performance_metrics=lambda: {"connection": "connected", "latency_ms": 1},
            )

        setattr(module, DRIVERS[name], construct)


@pytest.fixture
def backend_sandbox(monkeypatch):
    monkeypatch.syspath_prepend(str(APP_ROOT))

    def stub(name, **attrs):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
        parent, _, child = name.rpartition(".")
        if parent in sys.modules:
            monkeypatch.setattr(sys.modules[parent], child, module, raising=False)
        return module

    # These boundaries normally read deployment configuration, connect to Redis,
    # or import the tokenizer. They are unrelated to driver selection.
    import common

    importlib.import_module("rag.utils")

    stub("common.config_utils", get_base_config=lambda _name, default=None: default or {}, decrypt_database_config=lambda **_: {})
    stub("rag.utils.redis_conn", REDIS_CONN=types.SimpleNamespace())
    stub("rag.nlp", search=types.SimpleNamespace(Dealer=lambda conn: conn))
    stub("requests")
    stub("api.db.db_models", DB=types.SimpleNamespace())
    for name in DRIVERS:
        monkeypatch.delitem(sys.modules, name, raising=False)
        parent, _, child = name.rpartition(".")
        if parent in sys.modules:
            package = sys.modules[parent]
            # Record even an originally absent attribute: importing a child
            # assigns it on the package independently of sys.modules.
            monkeypatch.setattr(package, child, getattr(package, child, None), raising=False)
            monkeypatch.delattr(package, child)
    loader = DriverLoader()
    monkeypatch.setattr(sys, "meta_path", [loader, *sys.meta_path])
    for name in ("DB_TYPE", "DOC_ENGINE", "STORAGE_IMPL", "DOCMIND_STORAGE_AUDIT_ROOT", "RAGFLOW_CRYPTO_ENABLED", "RAGFLOW_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)

    def load(relative_path):
        spec = importlib.util.spec_from_file_location("_lazy_backend_under_test", APP_ROOT / relative_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    settings = load("common/settings.py")
    monkeypatch.setitem(sys.modules, "common.settings", settings)
    monkeypatch.setattr(common, "settings", settings, raising=False)
    yield settings, loader, load
    # Imports performed by the finder must not leak fake modules into other tests.
    for name in loader.loaded:
        sys.modules.pop(name, None)


def test_import_settings_does_not_import_backend_drivers(backend_sandbox):
    _, loader, _ = backend_sandbox
    assert loader.loaded == []
    assert loader.constructed == []


def test_initialize_only_selected_backend_drivers(backend_sandbox):
    settings, loader, _ = backend_sandbox
    settings.init_settings()
    expected = {"rag.utils.es_conn", "rag.utils.minio_conn"}
    assert not hasattr(settings, "msgStoreConn")
    assert settings.docStoreConn.driver == "rag.utils.es_conn"
    assert settings.STORAGE_IMPL.driver == "rag.utils.minio_conn"
    assert set(loader.loaded) == expected
    assert set(loader.constructed) == expected


def test_storage_factory_imports_only_selected_driver(backend_sandbox):
    settings, loader, _ = backend_sandbox
    result = settings.StorageFactory.create(settings.Storage.MINIO)
    assert result.driver == "rag.utils.minio_conn"
    assert loader.loaded == ["rag.utils.minio_conn"]


def test_selected_driver_failure_is_not_silently_replaced(backend_sandbox):
    settings, loader, _ = backend_sandbox
    loader.fail.add("rag.utils.minio_conn")
    with pytest.raises(RuntimeError, match="driver unavailable"):
        settings.init_settings()
    assert "rag.utils.s3_conn" not in loader.loaded


@pytest.mark.parametrize("fails", [False, True])
def test_es_health_probe_preserves_failure_response(backend_sandbox, fails):
    _, loader, load = backend_sandbox
    health = load("api/utils/health_utils.py")
    if fails:
        loader.fail.add("rag.utils.es_conn")
    result = health.get_es_cluster_stats()
    assert result["status"] == ("timeout" if fails else "alive")
    assert loader.loaded == ["rag.utils.es_conn"]


@pytest.mark.parametrize("secure,verify,expected", [(False, True, ("http", True)), ("true", "false", ("https", False)), (True, "yes", ("https", True))])
def test_minio_health_tls_options(backend_sandbox, secure, verify, expected):
    settings, _, load = backend_sandbox
    settings.MINIO = {"secure": secure, "verify": verify}
    health = load("api/utils/health_utils.py")
    assert health._minio_scheme_and_verify() == expected


@pytest.mark.parametrize("key,value", [("DB_TYPE", "postgres"), ("DB_TYPE", "gaussdb"), ("DB_TYPE", "oceanbase"), ("DOC_ENGINE", "infinity"), ("DOC_ENGINE", "opensearch"), ("DOC_ENGINE", "seekdb"), ("STORAGE_IMPL", "AWS_S3"), ("STORAGE_IMPL", "GCS")])
def test_retired_selection_fails_before_driver_construction(backend_sandbox, monkeypatch, key, value):
    settings, loader, _ = backend_sandbox
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=key):
        settings.init_settings()
    assert loader.loaded == []
    assert loader.constructed == []


@pytest.mark.parametrize("key,value", [("DB_TYPE", "postgres"), ("DOC_ENGINE", "infinity"), ("STORAGE_IMPL", "AWS_S3")])
def test_invalid_import_fails_before_loading_runtime_drivers(backend_sandbox, monkeypatch, key, value):
    _, loader, load = backend_sandbox
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=key):
        load("common/settings.py")
    assert loader.loaded == []
