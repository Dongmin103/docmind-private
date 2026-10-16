import asyncio
from contextlib import contextmanager
import threading
from types import SimpleNamespace

import pytest
from werkzeug.datastructures import MultiDict

from api.apps.restful_apis import docmind_api


@pytest.mark.parametrize("reject", [False, True])
def test_worker_auth_keeps_loop_responsive_and_closes_worker_connection(monkeypatch, reject):
    loop_thread = threading.get_ident()
    connections = []
    release = threading.Event()

    @contextmanager
    def connection_context():
        connections.append(("open", threading.get_ident()))
        try:
            yield
        finally:
            connections.append(("close", threading.get_ident()))

    def verify(**kwargs):
        assert threading.get_ident() != loop_thread
        if not connections:
            connections.append(("open", threading.get_ident()))
        assert connections == [("open", threading.get_ident())]
        assert release.wait(1), "authentication blocked the event loop"
        assert kwargs == dict(method="POST", path_and_query="/claim?source=x", body=b"signed",
                              key_id="key", timestamp="123", nonce="nonce",
                              content_sha256="abc", signature="def")
        if reject:
            raise docmind_api.docmind_worker_auth.WorkerAuthenticationError("WORKER_AUTH_REPLAYED")

    monkeypatch.setattr(docmind_api.DocmindIngestionJob._meta, "database",
                        SimpleNamespace(connection_context=connection_context,
                                        close=lambda: connections.append(("close", threading.get_ident()))))
    monkeypatch.setattr(docmind_api.docmind_worker_auth, "verify", verify)
    monkeypatch.setattr(docmind_api, "request", SimpleNamespace(
        method="POST", path="/claim", query_string=b"source=x", headers={
            "X-DocMind-Key-Id": "key", "X-DocMind-Timestamp": "123",
            "X-DocMind-Nonce": "nonce", "X-DocMind-Content-SHA256": "ABC",
            "X-DocMind-Signature": "DEF"}))

    async def run():
        asyncio.get_running_loop().call_later(.01, release.set)
        if reject:
            with pytest.raises(docmind_api.docmind_worker_auth.WorkerAuthenticationError, match="WORKER_AUTH_REPLAYED"):
                await docmind_api._authenticate_worker(b"signed")
        else:
            assert await docmind_api._authenticate_worker(b"signed") == "key"
    asyncio.run(run())
    assert len(connections) == 2
    assert connections[0][1] == connections[1][1] != loop_thread
    assert [state for state, _ in connections] == ["open", "close"]


def test_rejected_auth_does_not_acquire_database_connection(monkeypatch):
    def reject(**_kwargs):
        raise docmind_api.docmind_worker_auth.WorkerAuthenticationError("WORKER_AUTH_HEADER_INVALID")
    monkeypatch.setattr(docmind_api.docmind_worker_auth, "verify", reject)
    closed = []
    monkeypatch.setattr(docmind_api.DocmindIngestionJob._meta, "database", SimpleNamespace(
        connection_context=lambda: pytest.fail("unauthenticated request acquired DB connection"),
        close=lambda: closed.append(True)))
    with pytest.raises(docmind_api.docmind_worker_auth.WorkerAuthenticationError, match="WORKER_AUTH_HEADER_INVALID"):
        docmind_api._verify_worker_in_thread()
    assert closed == [True]


def test_artifact_page_cap_is_opt_in_and_bounded():
    assert docmind_api._artifact_page_cap(MultiDict()) is None
    assert docmind_api._artifact_page_cap(MultiDict([("max_pdf_pages", "30")])) == 30
    for values in (
        [("max_pdf_pages", "0")],
        [("max_pdf_pages", "31")],
        [("max_pdf_pages", "01")],
        [("max_pdf_pages", "30"), ("max_pdf_pages", "1")],
        [("max_pdf_pages", "30"), ("other", "1")],
    ):
        with pytest.raises(docmind_api.docmind_ingestion_service.DocmindIngestionError, match="DOCMIND_INGESTION_REQUEST_INVALID"):
            docmind_api._artifact_page_cap(MultiDict(values))


@pytest.mark.parametrize("change", [
    {"single_claim": False},
    {"single_claim": "true"},
    {"claim_job_id": 123},
])
def test_exact_claim_route_rejects_missing_or_invalid_single_claim(monkeypatch, change):
    req = {"worker_id": "worker-1", "protocol_version": 1,
           "claim_source_id": "dept-2-e2e", "allowed_formats": ["pptx"],
           "skip_retries": True, "claim_job_id": "a" * 32, "single_claim": True}
    req.update(change)

    async def get_data():
        return b"signed-request"

    async def get_json(*_args, **_kwargs):
        return req

    async def authenticate(_body):
        return "worker-key-1"

    monkeypatch.setattr(docmind_api, "request", SimpleNamespace(get_data=get_data, get_json=get_json))
    monkeypatch.setattr(docmind_api, "_authenticate_worker", authenticate)
    monkeypatch.setattr(docmind_api, "_signed_worker_response", lambda payload, _key, status=200: (status, payload))
    monkeypatch.setattr(docmind_api, "_claim_cloud_sync_job_in_thread", lambda *_args: pytest.fail("invalid claim reached database"))

    status, payload = asyncio.run(docmind_api.claim_cloud_sync_job())
    assert (status, payload) == (409, {"error": "DOCMIND_INGESTION_REQUEST_INVALID"})


@pytest.mark.parametrize('pool_size', [None, 2])
def test_exact_claim_route_forwards_signed_job_id(monkeypatch, pool_size):
    target = "a" * 32

    async def get_data():
        return b"signed-request"

    async def get_json(*_args, **_kwargs):
        request_body = {"worker_id": "worker-1", "protocol_version": 1,
                "claim_source_id": "dept-2-e2e", "allowed_formats": ["pptx"],
                "skip_retries": True, "claim_job_id": target, "single_claim": True}
        if pool_size is not None:
            request_body['ingestion_pool_size'] = pool_size
        return request_body

    async def authenticate(_body):
        return "worker-key-1"

    seen = []
    monkeypatch.setattr(docmind_api, "request", SimpleNamespace(get_data=get_data, get_json=get_json))
    monkeypatch.setattr(docmind_api, "_authenticate_worker", authenticate)
    monkeypatch.setattr(docmind_api, "_signed_worker_response", lambda payload, _key, status=200: (status, payload))
    monkeypatch.setattr(docmind_api, "_claim_cloud_sync_job_in_thread", lambda *args: seen.append(args))

    status, payload = asyncio.run(docmind_api.claim_cloud_sync_job())
    assert (status, payload) == (200, {"job": None})
    expected = ("worker-1", 300, ["pptx"], True, "dept-2-e2e", target)
    assert seen == [expected if pool_size is None else (*expected, pool_size)]


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ({"mode": "all"}, {"mode": "all"}),
        (
            {"mode": "folders", "folder_ids": [" folder-1 "]},
            {"mode": "folders", "folder_ids": ["folder-1"]},
        ),
        (
            {"mode": "documents", "document_ids": ["document-1"]},
            {"mode": "documents", "document_ids": ["document-1"]},
        ),
    ],
)
def test_parse_search_scope_accepts_each_explicit_mode(scope, expected):
    assert docmind_api._parse_search_scope(scope) == expected


@pytest.mark.parametrize(
    "scope",
    [
        None,
        {},
        {"mode": "automatic"},
        {"mode": "all", "folder_ids": ["folder-1"]},
        {"mode": "folders", "folder_ids": []},
        {"mode": "folders", "document_ids": ["document-1"]},
        {"mode": "documents", "document_ids": [""]},
        {"mode": "documents", "folder_ids": ["folder-1"]},
    ],
)
def test_parse_search_scope_rejects_missing_empty_or_mixed_scope(scope):
    with pytest.raises(ValueError):
        docmind_api._parse_search_scope(scope)


def test_search_route_passes_authenticated_tenant_project_and_scope(monkeypatch):
    async def request_json(*_args, **_kwargs):
        return {
            "question": "  validation evidence  ",
            "project_id": " project-1 ",
            "scope": {"mode": "documents", "document_ids": ["doc-2", "doc-1"]},
        }

    calls = []

    async def search_result(*args, **kwargs):
        calls.append((args, kwargs))
        return {"chunks": [], "scope_doc_ids": ["private-doc"]}

    raw_search = docmind_api.search.__wrapped__.__wrapped__
    monkeypatch.setattr(docmind_api, "request", SimpleNamespace(get_json=request_json))
    monkeypatch.setattr(docmind_api.docmind_api_service, "search", search_result)
    monkeypatch.setattr(docmind_api, "get_result", lambda *, data: {"code": 0, "data": data})

    response = asyncio.run(raw_search(tenant_id="tenant-1"))

    assert response == {"code": 0, "data": {"chunks": []}}
    assert calls == [
        (
            ("tenant-1", "validation evidence"),
            {
                "project_id": "project-1",
                "scope": {"mode": "documents", "document_ids": ["doc-2", "doc-1"]},
            },
        )
    ]


def test_search_route_rejects_legacy_top_level_scope(monkeypatch):
    async def request_json(*_args, **_kwargs):
        return {"question": "question", "folder_ids": ["folder-1"]}

    raw_search = docmind_api.search.__wrapped__.__wrapped__
    monkeypatch.setattr(docmind_api, "request", SimpleNamespace(get_json=request_json))
    monkeypatch.setattr(
        docmind_api,
        "get_error_argument_result",
        lambda message: {"code": 101, "message": message},
    )

    response = asyncio.run(raw_search(tenant_id="tenant-1"))

    assert response == {"code": 101, "message": "request contains unsupported fields"}
