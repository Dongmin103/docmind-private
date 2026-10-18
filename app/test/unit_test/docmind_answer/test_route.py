"""Real Quart routing with isolated auth/storage boundaries; no application startup."""

import asyncio
import importlib.util
import sys
import types
from functools import wraps
from pathlib import Path

import pytest
from quart import Blueprint, Quart, request


@pytest.fixture
def route_app(monkeypatch, service):
    app = Quart(__name__)
    blueprint = Blueprint("answer_test", __name__)

    def login_required(function):
        @wraps(function)
        async def authenticated(**kwargs):
            if not request.headers.get("X-Test-User"):
                return {"error": "unauthorized"}, 401
            return await function(**kwargs)

        return authenticated

    def add_tenant(function):
        @wraps(function)
        async def bind_tenant(**kwargs):
            return await function(**kwargs, tenant_id=request.headers["X-Test-User"])

        return bind_tenant

    services = types.ModuleType("api.apps.services")
    services.docmind_answer_service = service
    async def worker():
        pass
    services.docmind_answer_jobs = types.SimpleNamespace(
        JobError=type('JobError', (RuntimeError,), {}), run_worker=worker, initialize=lambda: None,
        submit=lambda *args: {"job_id": args[2], "status": "queued"},
        get=lambda *args: {"job_id": args[2], "status": "running"},
    )
    app.jobs = services.docmind_answer_jobs
    monkeypatch.setattr(service.AnswerConfig, 'from_env', lambda: object())
    services.docmind_api_service = types.SimpleNamespace(DocmindCatalogNotInitializedError=RuntimeError)
    for name in (
        "docmind_bootstrap_service",
        "docmind_catalog_admin_service",
        "docmind_hierarchy_service",
        "docmind_ingestion_service",
        "docmind_preview_service",
        "docmind_registration_service",
        "docmind_shared_workspace_service",
        "docmind_worker_auth",
    ):
        setattr(services, name, types.SimpleNamespace(PreviewError=RuntimeError))
    modules = {
        "api.apps": types.SimpleNamespace(current_user=types.SimpleNamespace(id="user"), login_required=login_required, login_user=None),
        "api.apps.services": services,
        "api.db.db_models": types.SimpleNamespace(DocmindIngestionJob=None, DocmindPreviewSession=None),
        "api.db.services.document_service": types.SimpleNamespace(DocmindProtectedEvidenceError=RuntimeError),
        "api.utils.api_utils": types.SimpleNamespace(
            add_tenant_id_to_kwargs=add_tenant,
            get_result=lambda *, data: {"code": 0, "data": data},
            get_error_argument_result=lambda message: {"code": 400, "message": message},
            get_error_data_result=lambda message: {"code": 500, "message": message},
        ),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    path = Path(__file__).resolve().parents[3] / "api/apps/restful_apis/docmind_api.py"
    spec = importlib.util.spec_from_file_location("answer_route_under_test", path)
    module = importlib.util.module_from_spec(spec)
    module.manager = blueprint
    module.app = app
    spec.loader.exec_module(module)
    app.register_blueprint(blueprint, url_prefix="/api/v1")
    return app


def test_route_accepts_without_waiting_for_model(route_app, service, monkeypatch):
    calls = []
    def submit(*args):
        calls.append(args)
        return {"job_id": args[2], "status": "queued"}
    monkeypatch.setattr(route_app.jobs, 'submit', submit)
    async def run():
        response = await route_app.test_client().post('/api/v1/docmind/answer/jobs',
            headers={"X-Test-User": "member"}, json={"request_id": "a"*32, "question": " question ",
            "project_id": " kb ", "scope": {"mode": "documents", "document_ids": ["doc"]}, "neighbor_window": 5})
        assert response.status_code == 202
        assert response.headers['Cache-Control'] == 'no-store'
        return await response.get_json()
    assert asyncio.run(run())["data"]["status"] == "queued"
    assert calls == [("member", "user", "a"*32, {"question": "question", "project_id": "kb", "scope": {"mode": "documents", "document_ids": ["doc"]}, "neighbor_window": 5})]


@pytest.mark.parametrize('extra', [{"chunks": []}, {"model": "bad"}, {"base_url": "http://untrusted"}, {"tenant_id": "other"}])
def test_route_rejects_client_evidence_and_configuration(route_app, extra):
    async def run():
        response = await route_app.test_client().post('/api/v1/docmind/answer/jobs', headers={"X-Test-User": "member"}, json={"question": "question", "scope": {"mode": "all"}, **extra})
        return await response.get_json()
    assert asyncio.run(run()) == {"code": 400, "message": "invalid answer request"}


@pytest.mark.parametrize('method,path', [('post', '/api/v1/docmind/answer/jobs'), ('get', '/api/v1/docmind/answer/jobs/'+'a'*32)])
def test_route_requires_authentication(route_app, method, path):
    async def run():
        return await getattr(route_app.test_client(), method)(path)
    assert asyncio.run(run()).status_code == 401


def test_route_sanitizes_unknown_exceptions(route_app, monkeypatch):
    def fail(*args):
        raise OSError('PRIVATE PROVIDER DATA')
    monkeypatch.setattr(route_app.jobs, 'submit', fail)
    async def run():
        response = await route_app.test_client().post('/api/v1/docmind/answer/jobs', headers={"X-Test-User": "member"}, json={"request_id": "a"*32, "question": "q", "scope": {"mode": "all"}})
        return await response.get_json()
    assert asyncio.run(run()) == {"code": 500, "message": "DOCMIND_ANSWER_FAILED"}


def test_poll_is_authenticated_and_not_cached(route_app):
    async def run():
        response = await route_app.test_client().get('/api/v1/docmind/answer/jobs/'+'a'*32, headers={"X-Test-User": "member"})
        assert response.headers['Cache-Control'] == 'no-store'
        return await response.get_json()
    assert asyncio.run(run())["data"]["status"] == "running"
