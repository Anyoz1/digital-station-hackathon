from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import StreamingResponse

from .auth import COOKIE_NAME, PASSWORDS, SESSION_SECONDS, create_session, find_session, public_user
from .bootstrap import bootstrap
from .contracts import (
    APIError,
    AuthResponse,
    CommandEnvelope,
    CommandReceiptResponse,
    Config,
    ConfigPatchInput,
    HistoryPage,
    IncidentBatchInput,
    LoginInput,
    NewRunInput,
    RenderTelemetry,
    ReplanDetail,
    ReplanInput,
    SimulationCommand,
    State,
)
from .db import AppUser, ConfigRevision, OptimizationRun, PlanRecord, RunState, Scenario, database
from .history import HistoryError
from .history import page as history_page
from .history import snapshot as history_snapshot
from .http_contracts import (
    ClockProbe,
    ExplanationAvailable,
    ExplanationUnavailable,
    HealthLive,
    HealthReady,
    MetricsResponse,
    ScenariosResponse,
)
from .presentation import explain_plans
from .reporting import export_csv
from .runtime import MIGRATION, ActorError, StationActor
from .scenario import utc_now
from .settings import Settings

STATIC = Path(__file__).parent / "static"
DUMMY_HASH = PASSWORDS.hash("unused-password-for-login-timing")


def fail(status: int, code: str, message: str, details: dict | None = None) -> NoReturn:
    raise HTTPException(status, detail={"code": code, "message": message, "details": details or {}})


def error_body(request: Request, code: str, message: str, details=None):
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": getattr(request.state, "command_id", None),
        }
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine, sessions = database(settings.database_url.get_secret_value())

    @asynccontextmanager
    async def lifespan(app):
        app.state.run_id = await bootstrap(sessions, settings)
        app.state.actor = StationActor(engine, sessions, app.state.run_id)
        try:
            await app.state.actor.start()
            yield
        finally:
            await app.state.actor.stop()
            await engine.dispose()

    app = FastAPI(
        title="Цифровая станция — H18",
        version="1.0",
        lifespan=lifespan,
        description="Physical digital twin, independent validator, isolated deterministic heuristic planner and SSE.",
        responses={status: {"model": APIError} for status in (401, 403, 404, 409, 422, 429, 503)},
    )
    app.state.engine, app.state.sessions, app.state.settings = engine, sessions, settings

    @app.middleware("http")
    async def origin_guard(request: Request, call_next):
        request.state.trace_id = str(uuid4())
        request.state.received_ms = datetime.now(UTC).timestamp() * 1000
        origin = request.headers.get("origin")
        own_origin = str(request.base_url).rstrip("/")
        if request.method in {"POST", "PATCH", "PUT", "DELETE"} and origin:
            if origin not in settings.origins and origin != own_origin:
                return JSONResponse(
                    status_code=403,
                    content=error_body(
                        request, "FORBIDDEN", "Источник запроса не разрешён", {"reason": "origin"}
                    ),
                )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.trace_id
        if "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        if getattr(request.state, "command_id", None) is None:
            try:
                body = await request.json()
                if isinstance(body, dict) and isinstance(body.get("request_id"), str):
                    request.state.command_id = body["request_id"]
            except (ValueError, RuntimeError):
                pass
        detail = (
            exc.detail
            if isinstance(exc.detail, dict)
            else {
                "code": "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR",
                "message": str(exc.detail),
                "details": {},
            }
        )
        return JSONResponse(status_code=exc.status_code, content=error_body(request, **detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        if request.url.path == "/api/v1/telemetry/ui-render":
            try:
                body = await request.json()
                client_id = UUID(body.get("client_id", ""))
                app.state.actor.measurements.schema_invalid(client_id)
            except (ValueError, AttributeError, TypeError):
                pass
        fields = [
            {"path": ".".join(map(str, error["loc"][1:])), "message": error["msg"]} for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_body(request, "VALIDATION_ERROR", "Ошибка формата запроса", {"fields": fields}),
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        # Do not expose connection strings, parameters, SQL or credentials.
        app.state.actor.abort()
        return JSONResponse(
            status_code=503, content=error_body(request, "SERVICE_NOT_READY", "База данных недоступна")
        )

    @app.exception_handler(ActorError)
    async def actor_error(request, exc):
        return JSONResponse(
            status_code=exc.status,
            content=error_body(request, exc.code, exc.message, exc.details),
            headers={"Retry-After": "1"} if exc.status == 429 else None,
        )

    @app.exception_handler(HistoryError)
    async def history_error(request, exc):
        return JSONResponse(status_code=exc.status, content=error_body(request, exc.code, exc.message))

    async def db_session():
        async with sessions() as session:
            yield session

    async def current_user(request: Request) -> AppUser:
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            fail(401, "UNAUTHENTICATED", "Требуется вход")
        async with sessions() as session:
            auth = await find_session(session, token)
            if auth is None or auth.revoked_at is not None:
                fail(401, "UNAUTHENTICATED", "Сессия недействительна")
            if auth.expires_at <= datetime.now(UTC):
                fail(401, "SESSION_EXPIRED", "Сессия истекла")
            user = await session.get(AppUser, auth.user_id)
            if user is None or not user.active:
                fail(401, "UNAUTHENTICATED", "Пользователь недоступен")
            return user  # No request-long DB connection retained by SSE.

    async def dispatcher_or_admin(user=Depends(current_user)):
        if user.role not in {"dispatcher", "admin"}:
            fail(403, "FORBIDDEN", "Действие доступно диспетчеру или администратору")
        return user

    async def admin_only(user=Depends(current_user)):
        if user.role != "admin":
            fail(403, "FORBIDDEN", "Требуется администратор")
        return user

    async def operator_or_admin(user=Depends(current_user)):
        if user.role not in {"operator", "admin"}:
            fail(403, "FORBIDDEN", "Требуется исполнитель или администратор")
        return user

    @app.get("/health/live", response_model=HealthLive)
    async def live():
        return {"status": "alive", "stage": "H18"}

    @app.get("/health/ready", response_model=HealthReady)
    async def ready(session=Depends(db_session)):
        try:
            await session.execute(text("SELECT 1"))
            revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
            row = await session.get(RunState, app.state.actor.run_id)
            if (
                revision != MIGRATION
                or row is None
                or not app.state.actor.ready
                or not app.state.actor.coordinator.worker.ready
            ):
                fail(503, "SERVICE_NOT_READY", "Миграции или начальное состояние не готовы")
        except SQLAlchemyError:
            app.state.actor.abort()
            fail(503, "SERVICE_NOT_READY", "База данных недоступна")
        return {
            "status": "ready",
            "stage": "H18",
            "checks": {
                "database": "ok",
                "schema": "ok",
                "initial_state": "ok",
                "station_actor": "ok",
                "scenario_topology": "ok",
                "planner_worker": "ok"
                if app.state.actor.coordinator and app.state.actor.coordinator.worker.ready
                else "unavailable",
            },
            "capabilities": {
                "auth": True,
                "snapshot": True,
                "simulation": True,
                "sse": True,
                "optimization": True,
                "actual_efficiency": True,
                "config_patch": True,
                "history": True,
                "csv": True,
                "manual_confirmation": True,
                "new_run": True,
                "render_telemetry": True,
                "noisy_progress_normalization": True,
            },
        }

    @app.post("/api/v1/auth/login", response_model=AuthResponse)
    async def login(body: LoginInput, response: Response, request: Request, session=Depends(db_session)):
        user = await session.scalar(select(AppUser).where(AppUser.username == body.username))
        valid = await run_in_threadpool(
            PASSWORDS.verify, body.password, user.password_hash if user else DUMMY_HASH
        )
        if not valid or user is None or not user.active:
            fail(401, "UNAUTHENTICATED", "Неверное имя пользователя или пароль")
        old_token = request.cookies.get(COOKIE_NAME)
        if old_token:
            old_session = await find_session(session, old_token)
            if old_session is not None:
                old_session.revoked_at = datetime.now(UTC)
        token = await create_session(session, user)
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=SESSION_SECONDS,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
            path="/",
        )
        return AuthResponse(user=public_user(user))

    @app.get("/api/v1/auth/me", response_model=AuthResponse)
    async def me(user=Depends(current_user)):
        return AuthResponse(user=public_user(user))

    @app.post("/api/v1/auth/logout", status_code=204)
    async def logout(request: Request, session=Depends(db_session)):
        token = request.cookies.get(COOKIE_NAME)
        if token:
            auth = await find_session(session, token)
            if auth:
                auth.revoked_at = datetime.now(UTC)
                await session.commit()
        response = Response(status_code=204)
        response.delete_cookie(
            COOKIE_NAME, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax"
        )
        return response

    @app.get("/api/v1/snapshot", response_model=State)
    async def snapshot(user=Depends(current_user), session=Depends(db_session)):
        if not app.state.actor.ready:
            fail(503, "SERVICE_NOT_READY", "Симулятор остановлен")
        row = await session.get(RunState, app.state.actor.run_id)
        if row is None:
            fail(503, "SERVICE_NOT_READY", "Начальное состояние отсутствует")
        state = State.model_validate(row.payload)
        state.server_time = utc_now()  # Transport wall-time; not a simulation state transition.
        return state

    @app.get(
        "/api/v1/stream",
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}}},
    )
    async def stream(
        request: Request, after: str | None = None, client_id: UUID | None = None, user=Depends(current_user)
    ):
        import time

        subscribed_at = time.monotonic()
        queue = await app.state.actor.subscribe(request.headers.get("last-event-id") or after)
        token = request.cookies.get(COOKIE_NAME)
        assert token is not None  # current_user dependency already requires an authenticated cookie.

        async def generate():
            try:
                while True:
                    item = await queue.get()
                    if item is None or await request.is_disconnected():
                        return
                    async with sessions() as session:
                        auth = await find_session(session, token)
                        if (
                            auth is None
                            or auth.revoked_at is not None
                            or auth.expires_at <= datetime.now(UTC)
                        ):
                            return
                    app.state.actor.measurements.delivered(client_id, item, subscribed_at)
                    yield item.wire
            except SQLAlchemyError:
                app.state.actor.abort()
            finally:
                app.state.actor.unsubscribe(queue)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/config", response_model=Config)
    async def config(user=Depends(current_user), session=Depends(db_session)):
        current = await session.get(RunState, app.state.actor.run_id)
        row = await session.get(ConfigRevision, current.config_version)
        if row is None:
            fail(503, "SERVICE_NOT_READY", "Начальная конфигурация отсутствует")
        return row.payload

    @app.patch("/api/v1/config", response_model=CommandReceiptResponse)
    async def update_config(body: ConfigPatchInput, request: Request, user=Depends(admin_only)):
        request.state.command_id = body.request_id
        return await app.state.actor.call("config", (user.id, body, utc_now()))

    @app.post("/api/v1/operations/{operation_id}/complete", response_model=CommandReceiptResponse)
    async def complete_operation(
        operation_id: str, body: CommandEnvelope, request: Request, user=Depends(operator_or_admin)
    ):
        request.state.command_id = body.request_id
        return await app.state.actor.call("complete", (user.id, body, operation_id, utc_now()))

    @app.get("/api/v1/history", response_model=HistoryPage)
    async def history(
        run_id: str,
        from_seq: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
        from_wall_time: datetime | None = None,
        to_wall_time: datetime | None = None,
        user=Depends(current_user),
        session=Depends(db_session),
    ):
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        return await history_page(session, run_id, from_seq, limit, from_wall_time, to_wall_time)

    @app.get("/api/v1/history/snapshot", response_model=State)
    async def replay_snapshot(
        run_id: str, seq: int = Query(..., ge=1), user=Depends(current_user), session=Depends(db_session)
    ):
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        return await history_snapshot(session, run_id, seq)

    @app.get("/api/v1/reports.csv", response_class=Response, responses={200: {"content": {"text/csv": {}}}})
    async def reports(
        run_id: str,
        from_wall_time: datetime | None = None,
        to_wall_time: datetime | None = None,
        user=Depends(current_user),
        session=Depends(db_session),
    ):
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        content = await export_csv(session, run_id, from_wall_time, to_wall_time)
        return Response(
            content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="station-report.csv"'},
        )

    @app.post("/api/v1/replans", response_model=CommandReceiptResponse, status_code=202)
    async def replan(body: ReplanInput, request: Request, user=Depends(dispatcher_or_admin)):
        request.state.command_id = body.request_id
        return await app.state.actor.call("replan", (user.id, body, utc_now()))

    @app.post("/api/v1/incidents", response_model=CommandReceiptResponse, status_code=201)
    async def create_incidents(body: IncidentBatchInput, request: Request, user=Depends(dispatcher_or_admin)):
        request.state.command_id = body.request_id
        return await app.state.actor.call("incidents", (user.id, body, utc_now()))

    @app.post("/api/v1/incidents/{incident_id}/resolve", response_model=CommandReceiptResponse)
    async def resolve_incident(
        incident_id: str, body: CommandEnvelope, request: Request, user=Depends(dispatcher_or_admin)
    ):
        request.state.command_id = body.request_id
        return await app.state.actor.call("resolve", (user.id, body, incident_id, utc_now()))

    @app.get("/api/v1/replans/{job_id}", response_model=ReplanDetail)
    async def replan_detail(job_id: str, user=Depends(current_user), session=Depends(db_session)):
        job = await session.get(OptimizationRun, job_id)
        if job is None or job.run_id != app.state.actor.run_id:
            fail(404, "NOT_FOUND", "Расчёт не найден")
        records = list(
            await session.scalars(select(PlanRecord).where(PlanRecord.optimization_run_id == job_id))
        )
        plans = [record.payload["detail"].copy() for record in records]
        current = {p.id: p for p in app.state.actor.state.plans}
        for plan in plans:
            if plan["id"] in current:
                plan.update(current[plan["id"]].model_dump(mode="json"))
            else:
                plan["can_apply"] = False
        plans.sort(key=lambda plan: (plan["objective_value"], plan["id"]))
        return {"job": job.payload["job"], "plans": plans}

    @app.get(
        "/api/v1/replans/{job_id}/explanation",
        summary="Read-only railway display diff; no state mutation",
        response_model=ExplanationAvailable | ExplanationUnavailable,
    )
    async def replan_explanation(job_id: str, user=Depends(current_user), session=Depends(db_session)):
        job = await session.get(OptimizationRun, job_id)
        if job is None or job.run_id != app.state.actor.run_id:
            fail(404, "NOT_FOUND", "Расчёт не найден")
        detail = await replan_detail(job_id, user, session)
        return explain_plans(job.payload, detail["plans"])

    @app.post("/api/v1/plans/{plan_id}/apply", response_model=CommandReceiptResponse)
    async def apply_plan(
        plan_id: str, body: CommandEnvelope, request: Request, user=Depends(dispatcher_or_admin)
    ):
        request.state.command_id = body.request_id
        return await app.state.actor.call("apply", (user.id, body, plan_id, utc_now()))

    @app.get("/api/v1/time", response_model=ClockProbe)
    async def clock_probe(request: Request, user=Depends(current_user)):
        return {
            "server_received_ms": request.state.received_ms,
            "server_sent_ms": datetime.now(UTC).timestamp() * 1000,
        }

    @app.post("/api/v1/telemetry/ui-render", status_code=204)
    async def ui_render(body: RenderTelemetry, user=Depends(current_user)):
        app.state.actor.measurements.report(body)
        return Response(status_code=204)

    @app.get("/api/v1/metrics", response_model=MetricsResponse)
    async def metrics(user=Depends(current_user)):
        return await app.state.actor.measurements.metrics(app.state.actor)

    @app.post("/api/v1/runs", status_code=201, response_model=CommandReceiptResponse)
    async def create_run(body: NewRunInput, request: Request, user=Depends(admin_only)):
        request.state.command_id = body.request_id
        receipt = await app.state.actor.call("new_run", (user.id, body, utc_now()))
        app.state.run_id = app.state.actor.run_id
        return receipt

    @app.get("/api/v1/scenarios", response_model=ScenariosResponse)
    async def scenarios(user=Depends(current_user), session=Depends(db_session)):
        rows = await session.scalars(select(Scenario).where(Scenario.version == 1).order_by(Scenario.id))
        return {
            "items": [
                {
                    "id": row.id,
                    "name": "Контроль ручного осмотра"
                    if row.id == "manual-control-v1"
                    else "Основное смешанное демо",
                    "description": "12 путей; 6 грузовых и 1 пассажирский поезд; учебный сценарий",
                    "train_count": len(row.payload["initial_state"]["trains"]),
                    "horizon_sim_s": row.payload["horizon_sim_s"],
                    "enabled_service_profiles": row.payload["enabled_service_profiles"],
                }
                for row in rows
            ]
        }

    @app.post(
        "/api/v1/simulation/control",
        response_model=CommandReceiptResponse,
        summary="Play/pause/step/speed via station actor; validated deterministic plans",
    )
    async def simulation_command(
        body: SimulationCommand, request: Request, user=Depends(dispatcher_or_admin)
    ):
        request.state.command_id = body.request_id
        return await app.state.actor.call("control", (user.id, body, utc_now()))

    @app.get("/", include_in_schema=False)
    @app.get("/tech", include_in_schema=False)
    async def tech_redirect():
        return RedirectResponse("/tech/station")

    @app.get("/tech/station", include_in_schema=False)
    async def station_view():
        return FileResponse(STATIC / "station.html")

    @app.get("/tech/station.js", include_in_schema=False)
    async def station_js():
        return FileResponse(STATIC / "station.js", media_type="application/javascript")

    @app.get("/tech/railway.js", include_in_schema=False)
    async def railway_js():
        return FileResponse(STATIC / "railway.js", media_type="application/javascript")

    @app.get("/tech/render-telemetry.js", include_in_schema=False)
    async def render_telemetry_js():
        return FileResponse(STATIC / "render-telemetry.js", media_type="application/javascript")

    @app.get("/tech/station.css", include_in_schema=False)
    async def station_css():
        return FileResponse(STATIC / "station.css", media_type="text/css")

    @app.get("/tech/debug", include_in_schema=False)
    async def debug_redirect():
        return RedirectResponse("/tech/smoke")

    @app.get("/tech/smoke", include_in_schema=False)
    async def smoke():
        return FileResponse(STATIC / "smoke.html")

    @app.get("/tech/smoke.js", include_in_schema=False)
    async def smoke_js():
        return FileResponse(STATIC / "smoke.js", media_type="application/javascript")

    @app.get("/tech/smoke.css", include_in_schema=False)
    async def smoke_css():
        return FileResponse(STATIC / "smoke.css", media_type="text/css")

    return app
