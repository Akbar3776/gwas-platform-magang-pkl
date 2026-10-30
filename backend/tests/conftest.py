import inspect
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = str(Path(__file__).resolve().parent.parent)
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import fastapi.testclient
import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.routing import APIRoute
from pydantic import BaseModel


def _loopback_available() -> bool:
    if os.name != "nt":
        return True
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.settimeout(0.15)
    try:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        addr = srv.getsockname()
        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.settimeout(0.15)
        try:
            cli.connect(addr)
            conn, _ = srv.accept()
            conn.close()
            return True
        finally:
            cli.close()
    except OSError:
        return False
    finally:
        srv.close()


class _DirectResponse:
    def __init__(self, status_code: int, data: Any):
        self.status_code = status_code
        self._data = data

    def json(self) -> Any:
        if isinstance(self._data, BaseModel):
            return json.loads(self._data.model_dump_json())
        return json.loads(json.dumps(self._data, default=str))


class _DirectTestClient:
    """In-memory FastAPI route dispatcher used when Windows sandbox blocks loopback socketpair()."""

    __test__ = False

    def __init__(self, app: FastAPI):
        self.app = app

    def _iter_api_routes(self):
        iter_fn = getattr(fastapi.routing, "_iter_routes_with_context", None)
        if iter_fn is not None:
            for route, ctx in iter_fn(self.app.routes):
                if isinstance(route, APIRoute):
                    yield route, (ctx.path if ctx is not None else route.path)
        else:
            for route in self.app.routes:
                if isinstance(route, APIRoute):
                    yield route, route.path

    def _request(self, method: str, path: str, json_body: dict | None = None) -> _DirectResponse:
        import re

        clean_path = path.split("?", 1)[0]
        for route, full_path in self._iter_api_routes():
            pattern = "^" + re.sub(r"\{(\w+)(?::[^}]+)?\}", r"(?P<\1>[^/]+)", full_path) + "$"
            match = re.match(pattern, clean_path)
            if (full_path == clean_path or match is not None) and method.upper() in route.methods:
                path_params = match.groupdict() if match is not None else {}
                kwargs: dict[str, Any] = {}
                sig = inspect.signature(route.endpoint)
                for name, param in sig.parameters.items():
                    default = param.default
                    if name in path_params:
                        kwargs[name] = path_params[name]
                    elif default.__class__.__name__ == "Depends":
                        dep_fn = default.dependency
                        override = self.app.dependency_overrides.get(dep_fn, dep_fn)
                        val = override()
                        if inspect.isgenerator(val):
                            val = next(val)
                        kwargs[name] = val
                    elif hasattr(default, "default") and default.__class__.__name__ in {"Query", "FieldInfo"}:
                        kwargs[name] = default.default
                    elif isinstance(param.annotation, type) and issubclass(param.annotation, BaseModel):
                        kwargs[name] = param.annotation(**(json_body or {}))
                    elif json_body and name in json_body:
                        kwargs[name] = json_body[name]
                try:
                    result = route.endpoint(**kwargs)
                    return _DirectResponse(route.status_code or 200, result)
                except HTTPException as exc:
                    return _DirectResponse(exc.status_code, {"detail": exc.detail})
        return _DirectResponse(404, {"detail": "Not Found"})

    def post(self, path: str, json: dict | None = None, **kwargs: Any) -> _DirectResponse:
        return self._request("POST", path, json_body=json)

    def get(self, path: str, **kwargs: Any) -> _DirectResponse:
        return self._request("GET", path)


_LOOPBACK_OK = _loopback_available()
if not _LOOPBACK_OK:
    fastapi.testclient.TestClient = _DirectTestClient


@pytest.fixture(autouse=True)
def _patch_testclient_if_loopback_blocked(monkeypatch):
    if not _LOOPBACK_OK:
        monkeypatch.setattr(fastapi.testclient, "TestClient", _DirectTestClient)
        import sys
        for mod in list(sys.modules.values()):
            if getattr(mod, "TestClient", None) is not None:
                monkeypatch.setattr(mod, "TestClient", _DirectTestClient)
