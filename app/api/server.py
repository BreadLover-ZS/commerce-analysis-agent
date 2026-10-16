"""Authenticated API. Execution belongs to the independent durable worker."""

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import delete, text

from app.commerce.datasets import (
    MAX_FILE_BYTES,
    SCENARIOS,
    commit_dataset,
    csv_bytes,
    dataset_info,
    list_datasets,
    owned_dataset,
    parse_csv,
    scenario_payload,
    stage_dataset,
)
from app.commerce.review import calculate_review, dataset_review
from app.commerce.service import add_document
from app.core.auth import COOKIE, authenticate, check_origin, csrf_token, login, token_hash
from app.core.config import Settings, get_settings
from app.core.files import parse_upload, safe_path
from app.research.schemas import TaskRequest
from app.storage.models import Artifact, AuthSession, User
from app.storage.repository import TERMINAL, Conflict, NotFound, Repository

CurrentUser = Annotated[User, Depends(authenticate)]


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: StrictBool


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: str = Field(max_length=40)
    seed: int = Field(default=42, ge=0, le=1000000, strict=True)


def create_app(settings: Settings | None = None, repository: Repository | None = None):
    settings = settings or get_settings()
    repo = repository or Repository(settings)

    @asynccontextmanager
    async def lifespan(_):
        yield
        if repository is None:
            repo.engine.dispose()

    app = FastAPI(
        title="电商运营分析助手 / Commerce Analysis Agent", version="0.2.0", lifespan=lifespan
    )
    app.state.settings, app.state.repo = settings, repo
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key", "Last-Event-ID"],
    )

    @app.exception_handler(NotFound)
    async def not_found(_, __):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=404, content={"detail": "资源不存在或不可访问"})

    @app.exception_handler(Conflict)
    async def conflict(_, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.middleware("http")
    async def response_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/api/commerce/scenarios")
    def scenarios(user: CurrentUser):
        return {"scenarios": [{"id": k, "name": v} for k, v in SCENARIOS.items()]}

    @app.get("/api/commerce/template")
    def template(user: CurrentUser):
        return Response(
            csv_bytes(scenario_payload("normal")),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="commerce-sample-template.csv"'},
        )

    def preview_result(info, user):
        with repo.session() as db:
            item = owned_dataset(db, info["id"], user.id, user.tenant_id, committed=False)
            result = calculate_review(
                item.payload,
                date.fromisoformat(info["suggested_start"]),
                date.fromisoformat(info["period_end"]),
                dataset_id=item.id,
                content_hash=item.content_hash,
                sample=item.sample,
            )
        return {
            **info,
            "quality": {
                "coverage": result["coverage"],
                "previous_period": result["previous_period"],
                "limitations": result["limitations"],
                "metrics": result["metrics"],
            },
        }

    @app.post("/api/commerce/scenarios")
    def generate_scenario(body: ScenarioRequest, user: CurrentUser):
        try:
            payload = scenario_payload(body.scenario, body.seed)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        raw = csv_bytes(payload)
        info = stage_dataset(
            repo,
            user,
            payload,
            f"模拟：{SCENARIOS[body.scenario]} / seed={body.seed}",
            True,
            hashlib.sha256(raw).hexdigest(),
        )
        return preview_result(info, user)

    @app.post("/api/commerce/datasets/preview")
    def preview_dataset(user: CurrentUser, file: UploadFile = File(), sample: bool = Form(True)):
        try:
            content = file.file.read(MAX_FILE_BYTES + 1)
            payload = parse_csv(content)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        finally:
            file.file.close()
        info = stage_dataset(
            repo,
            user,
            payload,
            file.filename or "经营数据.csv",
            sample,
            hashlib.sha256(content).hexdigest(),
        )
        return preview_result(info, user)

    @app.post("/api/commerce/datasets/{dataset_id}/commit")
    def confirm_dataset(dataset_id: str, user: CurrentUser):
        return commit_dataset(repo, user, dataset_id)

    @app.get("/api/commerce/datasets")
    def datasets(user: CurrentUser):
        return {"datasets": list_datasets(repo, user)}

    @app.get("/api/commerce/datasets/{dataset_id}")
    def dataset_detail(dataset_id: str, user: CurrentUser):
        with repo.session() as db:
            info = dataset_info(owned_dataset(db, dataset_id, user.id, user.tenant_id))
        return preview_result(info, user)

    @app.get("/api/commerce/datasets/{dataset_id}/quality")
    def quality(dataset_id: str, user: CurrentUser, start: str, end: str):
        try:
            result = dataset_review(
                repo,
                user.id,
                user.tenant_id,
                dataset_id,
                date.fromisoformat(start),
                date.fromisoformat(end),
            )
        except ValueError:
            raise HTTPException(422, "需要有效日期，周期为1–90天") from None
        return {
            k: result[k]
            for k in (
                "dataset_id",
                "dataset_hash",
                "sample",
                "period",
                "coverage",
                "previous_period",
                "limitations",
                "metrics",
                "dimensions",
                "findings",
                "definitions",
            )
        }

    @app.get("/health/ready")
    def ready():
        try:
            with repo.session() as db:
                db.execute(text("SELECT 1 FROM users LIMIT 1"))
                db.execute(text("SELECT 1 FROM commerce_datasets LIMIT 1"))
        except Exception:
            raise HTTPException(503, "数据库或迁移未就绪") from None
        return {"status": "ready", "mode": settings.agent_mode}

    @app.get("/api/config")
    def config():
        return {
            "mode": settings.agent_mode,
            "demo_accounts": settings.seed_demo,
            "knowledge_provider": settings.knowledge_provider,
            "sample_period": ["2026-09-01", "2026-09-30"],
        }

    @app.post("/api/auth/login")
    def sign_in(body: LoginRequest, request: Request, response: Response):
        check_origin(request)
        user, token = login(
            repo,
            settings,
            body.email,
            body.password,
            request.client.host if request.client else "unknown",
        )
        response.set_cookie(
            COOKIE,
            token,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
            max_age=settings.session_hours * 3600,
            path="/",
        )
        return {
            "email": user.email,
            "csrf_token": csrf_token(token, settings.session_secret.get_secret_value()),
        }

    @app.get("/api/auth/me")
    def me(request: Request, user: CurrentUser):
        return {
            "email": user.email,
            "tenant_id": user.tenant_id,
            "csrf_token": csrf_token(
                request.cookies[COOKIE], settings.session_secret.get_secret_value()
            ),
        }

    @app.post("/api/auth/logout")
    def sign_out(request: Request, response: Response, user: CurrentUser):
        with repo.session() as db:
            db.execute(
                delete(AuthSession).where(
                    AuthSession.token_hash == token_hash(request.cookies[COOKIE])
                )
            )
            db.commit()
        response.delete_cookie(COOKIE, path="/")
        return {"status": "logged_out"}

    @app.post("/api/conversations", status_code=201)
    def new_conversation(user: CurrentUser):
        return {"id": repo.create_conversation(user.id)}

    @app.get("/api/tasks")
    def tasks(user: CurrentUser):
        return {"tasks": repo.list_tasks(user.id)}

    @app.post("/api/tasks", status_code=202)
    def start(body: TaskRequest, user: CurrentUser, idempotency_key: Annotated[UUID, Header()]):
        return repo.create_task(user, body, str(idempotency_key))

    @app.get("/api/tasks/{task_id}")
    def task_detail(task_id: str, user: CurrentUser):
        result = repo.get_task(task_id, user.id)
        result["artifacts"] = repo.artifacts(task_id)
        metrics = result["metrics"]
        if (
            settings.input_price_per_million is not None
            and settings.output_price_per_million is not None
            and metrics["usage_complete"]
        ):
            metrics["estimated_cost"] = (
                metrics["input_tokens"] * settings.input_price_per_million
                + metrics["output_tokens"] * settings.output_price_per_million
            ) / 1_000_000
        else:
            metrics["estimated_cost"] = None
        return result

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel(task_id: str, user: CurrentUser):
        return repo.cancel(task_id, user.id)

    @app.post("/api/tasks/{task_id}/review")
    def review(task_id: str, body: ReviewRequest, user: CurrentUser):
        return repo.review(task_id, user.id, body.approved)

    @app.get("/api/tasks/{task_id}/evidence")
    def evidence(task_id: str, user: CurrentUser):
        repo.get_task(task_id, user.id)
        return {"evidence": repo.evidence(task_id)}

    @app.get("/api/tasks/{task_id}/events")
    def events(task_id: str, user: CurrentUser, after: int = Query(0, ge=0)):
        repo.get_task(task_id, user.id)
        items = repo.events(task_id, after)
        return {"events": items, "cursor": items[-1]["id"] if items else after}

    @app.get("/api/tasks/{task_id}/stream")
    async def stream(
        task_id: str, request: Request, user: CurrentUser, after: int = Query(0, ge=0)
    ):
        repo.get_task(task_id, user.id)
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise HTTPException(400, "事件游标无效") from None

        async def generate():
            nonlocal cursor
            while not await request.is_disconnected():
                # Recheck session validity so logout/expiry also closes existing streams.
                try:
                    await asyncio.to_thread(authenticate, request)
                except HTTPException:
                    return
                items = await asyncio.to_thread(repo.events, task_id, cursor)
                for item in items:
                    cursor = item["id"]
                    yield f"id: {cursor}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
                status = (await asyncio.to_thread(repo.get_task, task_id, user.id))["status"]
                if not items and (status in TERMINAL or status == "waiting_review"):
                    return
                yield ": keepalive\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            generate(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    @app.post("/api/documents", status_code=201)
    def upload(
        user: CurrentUser,
        conversation_id: Annotated[str, Form()],
        files: Annotated[list[UploadFile], File()],
    ):
        repo.conversation(conversation_id, user.id)
        if not 1 <= len(files) <= 5:
            raise HTTPException(413, "每次允许上传1–5个文件")
        parsed, total = [], 0
        try:
            for file in files:
                content = file.file.read(settings.max_upload_bytes + 1)
                total += len(content)
                if len(content) > settings.max_upload_bytes or total > 20 * 1024 * 1024:
                    raise HTTPException(413, "附件大小超过限制")
                parsed.append((file.filename or "", parse_upload(file.filename or "", content)))
            ids = [
                add_document(repo, user.tenant_id, name, fragments, user.id, conversation_id)
                for name, fragments in parsed
            ]
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(422, "附件无法解析、类型不支持或内容超过限制") from None
        return {"documents": [{"id": doc_id, "name": parsed[i][0]} for i, doc_id in enumerate(ids)]}

    @app.get("/api/artifacts/{artifact_id}")
    def download(artifact_id: str, user: CurrentUser):
        with repo.session() as db:
            artifact = db.get(Artifact, artifact_id)
            if not artifact:
                raise NotFound()
            path = safe_path(settings.data_dir / "artifacts", artifact.storage_key)
            if not path.is_file():
                raise NotFound()
            task_id = artifact.task_id
        repo.get_task(task_id, user.id)
        return FileResponse(path, filename=artifact.name)

    return app


app = create_app()
