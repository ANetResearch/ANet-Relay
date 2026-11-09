"""FastAPI application factory (03 §10)."""

from __future__ import annotations

import contextlib
import time
from collections.abc import AsyncIterator, Iterable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..app_bridge import AppBridge
from ..config import API_PREFIX, VERSION
from ..errors import RelayError, err
from ..fleet import FleetService
from . import (
    routes_agents,
    routes_app,
    routes_arena,
    routes_control,
    routes_device,
    routes_fleet,
    routes_hitl,  # 11 §7: serial ports and hardware in the loop
    routes_judge,
    routes_match,  # WP-B
    routes_procedures,
    routes_programs,
    routes_records,
    routes_twin,  # 11 §10: firmware twins
    stream,
)
from .origins import ALLOWED_ORIGINS

TRUSTED_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}


def _host_only(value: str) -> str:
    """Host header without the port (DNS-rebinding guard, H8)."""
    if value.startswith("["):
        return value.split("]", 1)[0] + "]"
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def create_app(
    svc: FleetService,
    *,
    manage_service: bool = True,
    trusted_hosts: Iterable[str] = TRUSTED_HOSTS,
) -> FastAPI:
    """Build the app. With ``manage_service`` the app starts and stops the fleet loop in its lifespan."""

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if manage_service:
            svc.start()
        try:
            yield
        finally:
            if manage_service:
                svc.shutdown()

    app = FastAPI(
        title="ANet Relay",
        version=VERSION,
        summary="Ground control for agent swarms: local fleet, control relay and agent API.",
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.service = svc
    app.state.app_bridge = AppBridge()
    app.state.trusted_hosts = set(trusted_hosts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if _host_only(request.headers.get("host") or "") not in request.app.state.trusted_hosts:
            return JSONResponse(status_code=403, content=err("untrusted_host").body())
        started = time.perf_counter()
        response = await call_next(request)
        session = getattr(request.state, "session", None)
        if session is not None:
            svc.sessions.log_call(
                session,
                time.time(),
                request.method,
                request.url.path,
                response.status_code,
                (time.perf_counter() - started) * 1000.0,
            )
        return response

    @app.exception_handler(RelayError)
    async def relay_error(_request: Request, exc: RelayError) -> JSONResponse:
        headers = {"Retry-After": "1"} if exc.status == 429 else None
        return JSONResponse(status_code=exc.status, content=exc.body(), headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = []
        for item in exc.errors():
            loc = ".".join(str(p) for p in item.get("loc", ()) if p != "body")
            problems.append(f"{loc}: {item.get('msg')}" if loc else str(item.get("msg")))
        error = err("invalid_request", reason="; ".join(problems) or "invalid body", errors=problems)
        return JSONResponse(status_code=400, content=error.body())

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            error = err("not_found")
        else:
            error = RelayError("invalid_request", status=exc.status_code, detail={"reason": str(exc.detail)})
            error.message_en = f"The request is not valid: {exc.detail}."
            error.message_zh = f"请求无效：{exc.detail}。"
        return JSONResponse(status_code=exc.status_code, content=error.body())

    for module in (routes_fleet, routes_control, routes_device, routes_agents, routes_records, routes_app):
        app.include_router(module.router, prefix=API_PREFIX)
    app.include_router(routes_procedures.router, prefix=API_PREFIX)  # bench procedures (WP-B)
    app.include_router(routes_arena.router, prefix=API_PREFIX)  # WP-D: LAN arena
    app.include_router(routes_programs.router, prefix=API_PREFIX)  # WP-1: onboard programs
    app.include_router(routes_judge.router, prefix=API_PREFIX)  # WP-2b: challenges, duels, jobs, traces
    app.include_router(routes_match.router, prefix=API_PREFIX)  # WP-B: dogfight, match, leaderboard
    app.include_router(routes_twin.router, prefix=API_PREFIX)  # 11 §10: firmware twins
    app.include_router(routes_hitl.router, prefix=API_PREFIX)  # 11 §7: serial ports, hardware in the loop
    app.include_router(stream.router, prefix=API_PREFIX)
    return app
