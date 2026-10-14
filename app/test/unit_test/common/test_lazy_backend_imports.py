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
    "rag.utils.infinity_conn": "InfinityConnection",
    "rag.utils.ob_conn": "OBConnection",
    "rag.utils.opensearch_conn": "OSConnection",
    "rag.utils.gaussdb_conn": "GaussDBConnection",
    "rag.utils.serenedb_conn": "SereneDBConnection",
    "memory.utils.es_conn": "ESConnection",
    "memory.utils.infinity_conn": "InfinityConnection",
    "memory.utils.ob_conn": "OBConnection",
    "memory.utils.gaussdb_conn": "GaussDBMemoryConnection",
    "rag.utils.minio_conn": "RAGFlowMinio",
    "rag.utils.azure_spn_conn": "RAGFlowAzureSpnBlob",
    "rag.utils.azure_sas_conn": "RAGFlowAzureSasBlob",
    "rag.utils.s3_conn": "RAGFlowS3",
    "rag.utils.oss_conn": "RAGFlowOSS",
    "rag.utils.opendal_conn": "OpenDALStorage",
    "rag.utils.gcs_conn": "RAGFlowGCS",
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
    importlib.import_module("memory.utils")

    stub("common.config_utils", get_base_config=lambda _name, default=None: default or {}, decrypt_database_config=lambda **_: {})
    stub("rag.utils.redis_conn", REDIS_CONN=types.SimpleNamespace())
    stub("rag.nlp", search=types.SimpleNamespace(Dealer=lambda conn: conn))
    stub("rag.graphrag", search=types.SimpleNamespace(KGSearch=lambda conn: conn))
    stub("requests")
    stub("api.db.db_models", DB=types.SimpleNamespace())
    stub("common.doc_store.gaussdb_conn_base", mask_gaussdb_text=str)
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
    for name in ("DOC_ENGINE", "STORAGE_IMPL", "DOCMIND_STORAGE_AUDIT_ROOT", "RAGFLOW_CRYPTO_ENABLED", "RAGFLOW_SECRET_KEY"):
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


@pytest.mark.parametrize(
    "engine,doc_driver,memory_driver",
    [
        ("elasticsearch", "es_conn", "es_conn"),
        ("infinity", "infinity_conn", "infinity_conn"),
        ("opensearch", "opensearch_conn", None),
        ("oceanbase", "ob_conn", "ob_conn"),
        ("seekdb", "ob_conn", "ob_conn"),
        ("gaussdb", "gaussdb_conn", "gaussdb_conn"),
        ("GaussDB", "gaussdb_conn", "gaussdb_conn"),
        ("serenedb", "serenedb_conn", None),
    ],
)
def test_initialize_only_selected_backend_drivers(backend_sandbox, monkeypatch, engine, doc_driver, memory_driver):
    settings, loader, _ = backend_sandbox
    monkeypatch.setenv("DOC_ENGINE", engine)
    settings.init_settings()
    expected = {f"rag.utils.{doc_driver}", "rag.utils.minio_conn"}
    if memory_driver:
        expected.add(f"memory.utils.{memory_driver}")
        assert settings.msgStoreConn.driver == f"memory.utils.{memory_driver}"
    else:
        assert settings.msgStoreConn is None
    assert settings.docStoreConn.driver == f"rag.utils.{doc_driver}"
    assert settings.STORAGE_IMPL.driver == "rag.utils.minio_conn"
    assert set(loader.loaded) == expected
    assert set(loader.constructed) == expected


@pytest.mark.parametrize(
    "storage,driver",
    [("MINIO", "minio_conn"), ("AZURE_SPN", "azure_spn_conn"), ("AZURE_SAS", "azure_sas_conn"), ("AWS_S3", "s3_conn"), ("OSS", "oss_conn"), ("OPENDAL", "opendal_conn"), ("GCS", "gcs_conn")],
)
def test_storage_factory_imports_only_selected_driver(backend_sandbox, storage, driver):
    settings, loader, _ = backend_sandbox
    result = settings.StorageFactory.create(settings.Storage[storage])
    assert result.driver == f"rag.utils.{driver}"
    assert loader.loaded == [f"rag.utils.{driver}"]


def test_selected_driver_failure_is_not_silently_replaced(backend_sandbox):
    settings, loader, _ = backend_sandbox
    loader.fail.add("rag.utils.minio_conn")
    with pytest.raises(RuntimeError, match="driver unavailable"):
        settings.init_settings()
    assert "rag.utils.s3_conn" not in loader.loaded


def test_health_import_and_unconfigured_probes_do_not_load_drivers(backend_sandbox, monkeypatch):
    _, loader, load = backend_sandbox
    health = load("api/utils/health_utils.py")
    monkeypatch.setenv("DOC_ENGINE", "elasticsearch")
    assert health.get_gaussdb_status()["status"] == "not_configured"
    assert health.check_gaussdb_health()["status"] == "not_configured"
    assert health.check_oceanbase_health()["status"] == "not_configured"
    with pytest.raises(Exception, match="not in use"):
        health.get_infinity_status()
    with pytest.raises(Exception, match="not in use"):
        health.get_oceanbase_status()
    assert loader.loaded == []


@pytest.mark.parametrize(
    "engine,probe,driver",
    [
        ("elasticsearch", "get_es_cluster_stats", "es_conn"),
        ("infinity", "get_infinity_status", "infinity_conn"),
        ("oceanbase", "get_oceanbase_status", "ob_conn"),
        ("gaussdb", "get_gaussdb_status", "gaussdb_conn"),
    ],
)
@pytest.mark.parametrize("fails", [False, True])
def test_health_probe_imports_selected_driver_and_preserves_failure_response(backend_sandbox, monkeypatch, engine, probe, driver, fails):
    _, loader, load = backend_sandbox
    health = load("api/utils/health_utils.py")
    monkeypatch.setenv("DOC_ENGINE", engine)
    if fails:
        loader.fail.add(f"rag.utils.{driver}")
    result = getattr(health, probe)()
    assert result["status"] == ("timeout" if fails else "alive")
    assert loader.loaded == [f"rag.utils.{driver}"]


def test_gaussdb_health_reuses_existing_connection_without_import(backend_sandbox, monkeypatch):
    settings, loader, load = backend_sandbox
    settings.docStoreConn = types.SimpleNamespace(db_type=lambda: "gaussdb")
    health = load("api/utils/health_utils.py")
    assert health._get_gaussdb_connection() is settings.docStoreConn
    assert loader.loaded == []
