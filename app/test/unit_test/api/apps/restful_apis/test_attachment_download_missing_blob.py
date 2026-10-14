"""Offline route registration with real login/tenant decorators and file headers."""
import ast
import asyncio
import importlib.util
import inspect
import logging
import sys
from functools import wraps
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Awaitable, Callable, ParamSpec, TypeVar

import pytest

APP = Path(__file__).resolve().parents[5]


def definition(path, names, namespace):
    tree = ast.parse((APP / path).read_text(encoding="utf8"))
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, "exec"), namespace)


class Headers(dict):
    def set(self, key, value):
        self[key] = value


class Unauthorized(Exception):
    pass


def load_routes(monkeypatch, blob, *, authorized=True, auth_type="JWT", args=None):
    request = SimpleNamespace(args=args or {"ext": "pdf", "filename": "report.pdf"}, view_args={"attachment_id": "blob"})
    user = SimpleNamespace(id="tenant-A")
    auth_calls = []
    storage_calls = []
    routes = {}

    def load_user(auth_types):
        auth_calls.append(auth_types)
        allowed = {"JWT", "API"} if auth_types is None else set(auth_types)
        return user if authorized and auth_type in allowed else None

    namespace = dict(Callable=Callable, Awaitable=Awaitable, P=ParamSpec("P"), T=TypeVar("T"), wraps=wraps,
                     os=__import__("os"), time=__import__("time"), logging=logging, request=request,
                     _load_user=load_user, _normalize_auth_types=lambda value: set(value or ["JWT", "API"]),
                     AUTH_BETA="BETA", QuartAuthUnauthorized=Unauthorized, g=SimpleNamespace(),
                     current_app=SimpleNamespace(ensure_async=lambda fn: fn))
    definition("api/apps/__init__.py", ["login_required"], namespace)
    api_apps = ModuleType("api.apps")
    api_apps.current_user = user
    api_apps.AUTH_JWT, api_apps.AUTH_API, api_apps.AUTH_BETA = "JWT", "API", "BETA"
    api_apps.login_required = namespace["login_required"]
    monkeypatch.setitem(sys.modules, "api.apps", api_apps)
    tenant_ns = dict(wraps=wraps, inspect=inspect)
    definition("api/utils/api_utils.py", ["add_tenant_id_to_kwargs"], tenant_ns)
    api_utils = ModuleType("api.utils.api_utils")
    api_utils.add_tenant_id_to_kwargs = tenant_ns["add_tenant_id_to_kwargs"]
    api_utils.get_data_error_result = lambda **kw: {"error": "data", **kw}
    api_utils.server_error_response = lambda error: {"error": "server", "message": str(error)}
    monkeypatch.setitem(sys.modules, "api.utils.api_utils", api_utils)

    def storage_get(tenant, ident):
        storage_calls.append((tenant, ident))
        return blob

    async def thread_exec(fn, *a, **kw):
        return fn(*a, **kw)

    async def make_response(payload):
        return SimpleNamespace(payload=payload, headers=Headers())

    common = ModuleType("common")
    common.settings = SimpleNamespace(STORAGE_IMPL=SimpleNamespace(get=storage_get))
    monkeypatch.setitem(sys.modules, "common", common)
    misc = ModuleType("common.misc_utils")
    misc.thread_pool_exec = thread_exec
    monkeypatch.setitem(sys.modules, "common.misc_utils", misc)
    quart = ModuleType("quart")
    quart.request, quart.make_response = request, make_response
    monkeypatch.setitem(sys.modules, "quart", quart)
    spec = importlib.util.spec_from_file_location("attachment_test_owner", APP / "api/apps/restful_apis/attachment_api.py")
    module = importlib.util.module_from_spec(spec)

    def route(url, **kwargs):
        assert kwargs["methods"] == ["GET"]
        def register(fn):
            routes[url] = fn
            return fn
        return register

    module.manager = SimpleNamespace(route=route)
    spec.loader.exec_module(module)
    return routes, auth_calls, storage_calls


@pytest.mark.parametrize("endpoint", ["preview", "download"])
@pytest.mark.parametrize("blob", [None, b"", b"PDFDATA"])
def test_routed_attachment_tenant_and_missing_blob(monkeypatch, endpoint, blob):
    routes, auth_calls, calls = load_routes(monkeypatch, blob)
    handler = routes[f"/agents/attachments/<attachment_id>/{endpoint}"]
    result = asyncio.run(handler(attachment_id="blob", tenant_id="attacker"))
    assert calls == [("tenant-A", "blob")]
    assert len(auth_calls) == 1
    if not blob:
        assert result == {"error": "data", "message": "document not found"}
    else:
        assert result.payload == blob
        assert result.headers["Content-Type"] == "application/pdf"
        disposition = "inline" if endpoint == "preview" else "attachment"
        assert result.headers["Content-Disposition"] == f'{disposition}; filename="report.pdf"'


@pytest.mark.parametrize("endpoint", ["preview", "download"])
def test_unauthenticated_routes_do_not_read_storage(monkeypatch, endpoint):
    routes, _, calls = load_routes(monkeypatch, b"data", authorized=False)
    with pytest.raises(Unauthorized):
        asyncio.run(routes[f"/agents/attachments/<attachment_id>/{endpoint}"](attachment_id="blob"))
    assert calls == []


@pytest.mark.parametrize("endpoint, allowed", [("preview", False), ("download", True)])
def test_beta_auth_policy_is_preserved(monkeypatch, endpoint, allowed):
    routes, _, calls = load_routes(monkeypatch, b"data", auth_type="BETA")
    handler = routes[f"/agents/attachments/<attachment_id>/{endpoint}"]
    if allowed:
        asyncio.run(handler(attachment_id="blob"))
        assert calls == [("tenant-A", "blob")]
    else:
        with pytest.raises(Unauthorized):
            asyncio.run(handler(attachment_id="blob"))
        assert calls == []


@pytest.mark.parametrize("endpoint", ["preview", "download"])
def test_active_content_is_forced_to_attachment(monkeypatch, endpoint):
    routes, _, _ = load_routes(monkeypatch, b"svg", args={"ext": "svg", "disposition": "inline"})
    result = asyncio.run(routes[f"/agents/attachments/<attachment_id>/{endpoint}"](attachment_id="blob"))
    assert result.headers["Content-Disposition"] == "attachment"
    assert result.headers["X-Content-Type-Options"] == "nosniff"
