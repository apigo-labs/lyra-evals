from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from vohu_evals.console.billing import BillingConfig, billing_loop, reconcile, save_config
from vohu_evals.console.contracts import BENCHMARKS, PriceCap, RunInput, TargetInput
from vohu_evals.console.planner import PlanInput, build_plan, capabilities
from vohu_evals.console.preparation import Preparations
from vohu_evals.console.results import csv_export, episode_rows, result_rows
from vohu_evals.console.scheduler import Scheduler
from vohu_evals.console.store import Store
from vohu_evals.suites.registry import suite_status


def create_app(data_root: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = Store(
            data_root or Path(os.environ.get("LYRA_CONSOLE_DATA", ".local/console"))
        )
        app.state.scheduler = Scheduler(app.state.store)
        app.state.suite_root = (
            (data_root / "suites")
            if data_root
            else Path(__file__).resolve().parents[3] / ".local/suites"
        )
        app.state.preparations = Preparations()
        billing_task = asyncio.create_task(billing_loop(app.state.scheduler))
        yield
        billing_task.cancel()
        await asyncio.gather(billing_task, return_exceptions=True)
        await app.state.preparations.close()
        await app.state.scheduler.close()
        app.state.store.db.close()

    app = FastAPI(
        title="Lyra local console",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.headers.get("host", "")
        if host not in {
            "127.0.0.1:8768",
            "localhost:8768",
            "127.0.0.1:5178",
            "localhost:5178",
            "testserver",
        }:
            return JSONResponse({"detail": "仅允许本地控制台访问"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin not in {
            "http://127.0.0.1:8768",
            "http://localhost:8768",
            "http://127.0.0.1:5178",
            "http://localhost:5178",
        }:
            return JSONResponse({"detail": "拒绝跨站请求"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "拒绝跨站请求"}, status_code=403)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.headers.get("x-lyra-console") != "1":
                return JSONResponse({"detail": "缺少本地请求标识"}, status_code=403)
            if "application/json" not in request.headers.get("content-type", ""):
                return JSONResponse({"detail": "需要 JSON 请求"}, status_code=415)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Pydantic validation errors include input values: never echo API keys.
        return JSONResponse(
            {"detail": "配置格式不正确，请检查名称、Endpoint、协议、题数与预算"}, status_code=422
        )

    @app.get("/api/health")
    async def health():
        return await app.state.scheduler.health()

    @app.get("/api/benchmarks")
    async def benchmarks():
        return suite_status(app.state.suite_root, BENCHMARKS)

    @app.get("/api/billing")
    async def billing_status():
        return {
            "configured": (app.state.store.secrets / "platform-billing.json").exists(),
            "error": app.state.scheduler.billing_error,
        }

    @app.post("/api/billing/config")
    async def configure_billing(data: BillingConfig):
        save_config(app.state.store, data)
        return {"configured": True}

    @app.post("/api/billing/reconcile")
    async def reconcile_billing():
        try:
            async with app.state.scheduler.billing_lock:
                return await reconcile(app.state.scheduler)
        except (ValueError, OSError) as exc:
            raise HTTPException(409, "账单同步失败，请检查平台令牌、工作区与账单匹配情况") from exc
        except Exception as exc:
            raise HTTPException(502, "平台账单暂不可用") from exc

    @app.post("/api/targets/{identifier}/price-cap")
    async def price_cap(identifier: str, data: PriceCap):
        try:
            return app.state.store.set_price_cap(identifier, data.model_dump())
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/targets")
    async def targets():
        return app.state.store.targets()

    @app.post("/api/targets", status_code=201)
    async def add_target(data: TargetInput):
        return app.state.store.add_target(data)

    @app.delete("/api/targets/{identifier}")
    async def delete_target(identifier: str):
        if not app.state.store.delete_target(identifier):
            raise HTTPException(404, "模型配置不存在")
        return {"ok": True}

    @app.get("/api/capabilities")
    async def target_capabilities():
        return [capabilities(t) for t in app.state.store.targets()]

    @app.get("/api/plans")
    async def plans():
        return app.state.store.plans()

    @app.get("/api/plans/{identifier}")
    async def get_plan(identifier: str):
        for plan in app.state.store.plans():
            if plan["id"] == identifier:
                return plan
        raise HTTPException(404, "计划不存在")

    @app.get("/api/plans/{identifier}/environment")
    async def environment_status(identifier: str):
        return app.state.preparations.states.get(
            identifier, {"status": "unknown", "message": "SWE 需先准备所选实例环境"}
        )

    @app.post("/api/plans/{identifier}/environment")
    async def prepare_environment(identifier: str):
        plan = next((p for p in app.state.store.plans() if p["id"] == identifier), None)
        if not plan or "swebench" not in plan["manifest"]["benchmarks"]:
            raise HTTPException(404, "SWE 计划不存在")
        return app.state.preparations.start(plan)

    @app.post("/api/plans", status_code=201)
    async def add_plan(data: PlanInput):
        try:
            plan = build_plan(
                data,
                app.state.store.targets(),
                app.state.suite_root,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        app.state.store.save_plan(plan)
        return plan

    @app.get("/api/runs")
    async def runs():
        return sorted(
            app.state.scheduler.runs.values(), key=lambda r: r["created_at"], reverse=True
        )

    @app.get("/api/results")
    async def results(
        run_id: str | None = None,
        benchmark: str | None = None,
        model: str | None = None,
        include_synthetic: bool = False,
        format: str = "json",
        level: str = "jobs",
        download: bool = False,
    ):
        if format not in {"json", "csv"}:
            raise HTTPException(422, "仅支持 JSON / CSV")
        if level not in {"jobs", "episodes"}:
            raise HTTPException(422, "仅支持作业或逐题导出")
        projection = result_rows if level == "jobs" else episode_rows
        rows = [
            r
            for r in projection(list(app.state.scheduler.runs.values()))
            if (include_synthetic or not r["synthetic"])
            and (run_id is None or r["run_id"] == run_id)
            and (benchmark is None or r["benchmark"] == benchmark)
            and (model is None or r["model"] == model)
        ]
        if format == "csv":
            return Response(
                csv_export(rows),
                media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": 'attachment; filename="lyra-results.csv"'},
            )
        payload = {"schema_version": 1, "currency": "USD", "latency_unit": "ms", "rows": rows}
        if download:
            return JSONResponse(
                payload, headers={"Content-Disposition": 'attachment; filename="lyra-results.json"'}
            )
        return payload

    @app.post("/api/runs", status_code=201)
    async def add_run(data: RunInput):
        try:
            return await app.state.scheduler.create(data)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/runs/{identifier}/cancel")
    async def cancel(identifier: str):
        try:
            return await app.state.scheduler.cancel(identifier)
        except KeyError as exc:
            raise HTTPException(404, "运行不存在") from exc

    @app.get("/api/events")
    async def events(request: Request):
        async def stream():
            last = -1
            while not await request.is_disconnected():
                revision = app.state.scheduler.revision
                if revision != last:
                    yield f"data: {revision}\n\n"
                    last = revision
                else:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    dist = Path(__file__).resolve().parents[3] / "console" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="console")
    return app


app = create_app()
