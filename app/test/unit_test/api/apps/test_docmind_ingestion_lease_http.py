"""Authenticated ingestion lease checks without loading model providers."""

import ast
import asyncio
import logging
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from quart import Quart, Response, request

SOURCE = Path(__file__).resolve().parents[4] / "api/apps/restful_apis/docmind_api.py"


class IngestionError(RuntimeError):
    def __init__(self, code):
        self.code = code


@pytest.mark.parametrize("authenticated, change, service_error, expected", [
    (False, {}, None, 401),
    (True, {}, None, 200),
    (True, {}, "DOCMIND_INGESTION_STALE_LEASE", 409),
    (True, {}, "DOCMIND_INGESTION_SOURCE_CHANGED", 409),
    (True, {}, "DOCMIND_INGESTION_VERSION_MISMATCH", 409),
    (True, {"fencing_token": True}, None, 409),
    (True, {"fencing_token": 0}, None, 409),
    (True, {"worker_id": ""}, None, 409),
    (True, {"version_id": None}, None, 409),
    (True, {"extend": 300}, None, 409),
])
async def test_ingestion_lease_route_authentication_and_signed_result(authenticated, change, service_error, expected):
    class WorkerAuthenticationError(RuntimeError):
        pass

    calls, connections = [], []
    loop_thread = threading.get_ident()

    @contextmanager
    def connection_context():
        connections.append(("open", threading.get_ident()))
        try:
            yield
        finally:
            connections.append(("close", threading.get_ident()))

    async def authenticate(body):
        assert body
        if not authenticated:
            raise WorkerAuthenticationError()
        return "worker-key"

    def validate(job_id, **kwargs):
        assert threading.get_ident() != loop_thread
        assert connections == [("open", threading.get_ident())]
        calls.append((job_id, kwargs))
        if service_error:
            raise IngestionError(service_error)
        return {"valid": True, "lease_expires_at": "2030-01-01T00:00:00+00:00"}

    app = Quart(__name__)
    namespace = {
        "manager": app,
        "asyncio": asyncio, "request": request, "Response": Response,
        "_authenticate_worker": authenticate,
        "docmind_worker_auth": SimpleNamespace(WorkerAuthenticationError=WorkerAuthenticationError),
        "docmind_ingestion_service": SimpleNamespace(DocmindIngestionError=IngestionError, validate_worker_lease=validate),
        "DocmindIngestionJob": SimpleNamespace(_meta=SimpleNamespace(database=SimpleNamespace(connection_context=connection_context))),
        "logger": logging.getLogger(__name__),
        "_signed_worker_response": lambda data, key, status=200: (data, status, {"X-Signed-Key": key}),
    }
    selected = {"validate_cloud_sync_worker_lease", "_validate_cloud_sync_worker_lease_in_thread"}
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    definitions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in selected]
    assert len(definitions) == 2, "Ingestion lease validation route missing"
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(SOURCE), "exec"), namespace)  # noqa: S102 - trusted repository functions only
    body = {"worker_id": "w", "version_id": "v", "fencing_token": 1, **change}
    result = await app.test_client().post("/cloud-sync/host-worker/jobs/job/validate", json=body)
    assert result.status_code == expected
    if authenticated:
        data = await result.get_json()
        assert result.headers["X-Signed-Key"] == "worker-key"
        if service_error:
            assert data == {"error": service_error}
        elif change:
            assert data == {"error": "DOCMIND_INGESTION_REQUEST_INVALID"}
        else:
            assert data == {"valid": True, "lease_expires_at": "2030-01-01T00:00:00+00:00"}
    if authenticated and not change:
        assert calls == [("job", {"worker_id": "w", "version_id": "v", "fencing_token": 1})]
        assert connections == [("open", connections[0][1]), ("close", connections[0][1])]
    else:
        assert calls == connections == []
