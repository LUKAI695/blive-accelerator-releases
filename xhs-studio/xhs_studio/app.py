"""FastAPI 后端 + 静态前端。"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .browser import Publisher, XHSBrowser
from .config import Settings
from .db import POST_STATUSES, Database, to_iso, utcnow
from .service import PublishService, validate_post

STATIC_DIR = Path(__file__).parent / "static"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    note: str = ""


class PostIn(BaseModel):
    account_id: int | None = None
    title: str = ""
    body: str = ""
    tags: list[str] = []
    images: list[str] = []


class ScheduleIn(BaseModel):
    scheduled_at: datetime


class DraftIn(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    style: str = ""
    notes: str = ""


def _clean_tags(tags: list[str]) -> list[str]:
    seen, out = set(), []
    for t in tags:
        t = t.strip().lstrip("#").strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def create_app(settings: Settings | None = None, publisher: Publisher | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.ensure_dirs()
    db = Database(settings.db_path)
    publisher = publisher or XHSBrowser(settings)
    service = PublishService(db, publisher, settings)
    background: set[asyncio.Task] = set()

    def spawn(coro) -> None:
        task = asyncio.create_task(coro)
        background.add(task)
        task.add_done_callback(background.discard)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        n = db.recover_interrupted()
        if n:
            db.log(f"{n} 篇帖子在上次运行中被中断，已标记为失败", level="warn")
        if settings.scheduler_enabled:
            service.start()
        yield
        await service.stop()
        for t in list(background):
            t.cancel()
        db.close()

    app = FastAPI(title="XHS Studio", lifespan=lifespan)
    app.state.db = db
    app.state.service = service
    app.state.settings = settings

    def get_post_or_404(post_id: int) -> dict:
        post = db.get_post(post_id)
        if post is None:
            raise HTTPException(404, "帖子不存在")
        return post

    def get_account_or_404(account_id: int) -> dict:
        account = db.get_account(account_id)
        if account is None:
            raise HTTPException(404, "账号不存在")
        return account

    # ---------------- 页面 ----------------
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.mount("/uploads", StaticFiles(directory=settings.uploads_dir), name="uploads")
    app.mount("/screenshots", StaticFiles(directory=settings.screenshots_dir), name="screenshots")

    @app.get("/api/settings")
    def get_settings():
        return {
            "title_max": settings.title_max,
            "body_max": settings.body_max,
            "images_max": settings.images_max,
            "min_interval_minutes": settings.min_interval_minutes,
            "daily_limit": settings.daily_limit,
            "headless": settings.headless,
            "data_dir": str(settings.data_dir),
        }

    # ---------------- 账号 ----------------
    @app.get("/api/accounts")
    def list_accounts():
        return db.list_accounts()

    @app.post("/api/accounts", status_code=201)
    def create_account(body: AccountIn):
        account = db.create_account(body.name.strip(), body.note.strip())
        db.log(f"添加账号 {account['name']}", account_id=account["id"])
        return account

    @app.delete("/api/accounts/{account_id}", status_code=204)
    def delete_account(account_id: int):
        get_account_or_404(account_id)
        db.delete_account(account_id)

    @app.post("/api/accounts/{account_id}/login", status_code=202)
    async def login_account(account_id: int):
        account = get_account_or_404(account_id)
        if account["status"] == "logging_in":
            raise HTTPException(409, "正在登录中，请在弹出的浏览器窗口扫码")
        db.set_account_status(account_id, "logging_in")

        async def run():
            try:
                ok = await publisher.login(account_id)
            except Exception as exc:  # noqa: BLE001
                db.set_account_status(account_id, "logged_out")
                db.log(f"登录出错：{exc}", level="error", account_id=account_id)
                return
            db.set_account_status(account_id, "logged_in" if ok else "logged_out")
            db.log("扫码登录成功" if ok else "扫码登录超时", level="info" if ok else "warn",
                   account_id=account_id)

        spawn(run())
        return {"status": "logging_in"}

    @app.post("/api/accounts/{account_id}/check")
    async def check_account(account_id: int):
        get_account_or_404(account_id)
        try:
            ok = await publisher.check_login(account_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"检查失败：{exc}") from exc
        db.set_account_status(account_id, "logged_in" if ok else "logged_out")
        return get_account_or_404(account_id)

    # ---------------- 图片 ----------------
    @app.post("/api/uploads")
    async def upload_images(files: list[UploadFile] = File(...)):
        names = []
        for f in files:
            ext = Path(f.filename or "").suffix.lower()
            if ext not in IMAGE_EXTS:
                raise HTTPException(400, f"不支持的图片格式：{f.filename}")
            data = await f.read()
            if len(data) > MAX_IMAGE_BYTES:
                raise HTTPException(400, f"图片过大（>20MB）：{f.filename}")
            name = f"{uuid.uuid4().hex}{ext}"
            (settings.uploads_dir / name).write_bytes(data)
            names.append(name)
        return {"files": names}

    # ---------------- 帖子 ----------------
    @app.get("/api/posts")
    def list_posts(status: str | None = None):
        if status and status not in POST_STATUSES:
            raise HTTPException(400, "未知状态")
        return db.list_posts(status)

    def check_refs(body: PostIn) -> None:
        if body.account_id is not None:
            get_account_or_404(body.account_id)
        for name in body.images:
            if "/" in name or "\\" in name or not (settings.uploads_dir / name).is_file():
                raise HTTPException(400, f"图片不存在：{name}")

    @app.post("/api/posts", status_code=201)
    def create_post(body: PostIn):
        check_refs(body)
        return db.create_post(account_id=body.account_id, title=body.title, body=body.body,
                              tags=_clean_tags(body.tags), images=body.images)

    @app.get("/api/posts/{post_id}")
    def get_post(post_id: int):
        post = get_post_or_404(post_id)
        post["problems"] = validate_post(post, settings)
        return post

    @app.put("/api/posts/{post_id}")
    def update_post(post_id: int, body: PostIn):
        post = get_post_or_404(post_id)
        if post["status"] in ("publishing", "published"):
            raise HTTPException(409, "已发布或发布中的帖子不能修改")
        check_refs(body)
        return db.update_post(post_id, account_id=body.account_id, title=body.title, body=body.body,
                              tags=_clean_tags(body.tags), images=body.images)

    @app.delete("/api/posts/{post_id}", status_code=204)
    def delete_post(post_id: int):
        post = get_post_or_404(post_id)
        if post["status"] == "publishing":
            raise HTTPException(409, "发布中的帖子不能删除")
        db.delete_post(post_id)

    @app.post("/api/posts/import", status_code=201)
    async def import_posts(
        csv_file: UploadFile = File(...),
        images: list[UploadFile] = File(default=[]),
        account_id: int | None = Form(default=None),
        start_at: datetime | None = Form(default=None),
        interval_minutes: int | None = Form(default=None),
    ):
        from .importer import ImportFormatError, parse_csv, plan_slots

        if account_id is not None:
            get_account_or_404(account_id)
        try:
            rows = parse_csv(await csv_file.read())
        except ImportFormatError as exc:
            raise HTTPException(400, str(exc)) from exc

        interval = settings.min_interval_minutes if interval_minutes is None else interval_minutes
        if interval < settings.min_interval_minutes:
            raise HTTPException(400, f"间隔不能小于 {settings.min_interval_minutes} 分钟（账号节奏限制）")
        if start_at is not None and to_iso(start_at) < to_iso(utcnow()):
            raise HTTPException(400, "开始时间需晚于现在")

        # 保存随 CSV 一起上传的图片，按原文件名（不区分大小写）匹配
        stored: dict[str, str] = {}
        for f in images:
            original = Path(f.filename or "").name
            ext = Path(original).suffix.lower()
            if ext not in IMAGE_EXTS:
                raise HTTPException(400, f"不支持的图片格式：{original}")
            data = await f.read()
            if len(data) > MAX_IMAGE_BYTES:
                raise HTTPException(400, f"图片过大（>20MB）：{original}")
            name = f"{uuid.uuid4().hex}{ext}"
            (settings.uploads_dir / name).write_bytes(data)
            stored[original.lower()] = name

        results, ready = [], []
        for row in rows:
            image_names = []
            for ref in row.image_names:
                hit = stored.get(Path(ref).name.lower())
                if hit:
                    image_names.append(hit)
                else:
                    row.problems.append(f"没有上传图片 {ref}")
            post = db.create_post(account_id=account_id, title=row.title, body=row.body,
                                  tags=_clean_tags(row.tags), images=image_names)
            problems = row.problems + validate_post(post, settings)
            entry = {"line": row.line, "post_id": post["id"], "title": row.title,
                     "status": "draft", "scheduled_at": None, "problems": problems}
            results.append(entry)
            if not problems:
                ready.append(entry)

        if start_at is not None and ready:
            slots = plan_slots(start_at, len(ready), interval_minutes=interval,
                               daily_limit=settings.daily_limit)
            for entry, when in zip(ready, slots):
                iso = to_iso(when)
                db.update_post(entry["post_id"], status="scheduled", scheduled_at=iso)
                entry.update(status="scheduled", scheduled_at=iso)

        scheduled = sum(1 for r in results if r["status"] == "scheduled")
        db.log(f"批量导入 {len(results)} 篇，其中 {scheduled} 篇已排期", account_id=account_id)
        return {"created": len(results), "scheduled": scheduled, "rows": results}

    @app.post("/api/posts/{post_id}/schedule")
    def schedule_post(post_id: int, body: ScheduleIn):
        post = get_post_or_404(post_id)
        if post["status"] not in ("draft", "failed", "scheduled"):
            raise HTTPException(409, f"当前状态（{post['status']}）不能定时")
        problems = validate_post(post, settings)
        if problems:
            raise HTTPException(400, "；".join(problems))
        when = to_iso(body.scheduled_at)
        updated = db.update_post(post_id, status="scheduled", scheduled_at=when, error=None)
        db.log(f"已安排定时发布《{post['title']}》于 {when}", post_id=post_id, account_id=post["account_id"])
        return updated

    @app.post("/api/posts/{post_id}/cancel")
    def cancel_post(post_id: int):
        post = get_post_or_404(post_id)
        if post["status"] not in ("scheduled", "failed"):
            raise HTTPException(409, "只有定时中或失败的帖子可以撤回为草稿")
        return db.update_post(post_id, status="draft", scheduled_at=None, error=None)

    @app.post("/api/posts/{post_id}/publish", status_code=202)
    async def publish_now(post_id: int):
        post = get_post_or_404(post_id)
        if post["status"] not in ("draft", "failed", "scheduled"):
            raise HTTPException(409, f"当前状态（{post['status']}）不能发布")
        problems = validate_post(post, settings)
        if problems:
            raise HTTPException(400, "；".join(problems))
        reason = service.blocked_reason(post)
        if reason:
            raise HTTPException(429, reason)
        if not db.claim_post(post_id, ("draft", "failed", "scheduled")):
            raise HTTPException(409, "帖子正在发布")
        spawn(service.run_claimed(post_id))
        return {"status": "publishing"}

    # ---------------- AI ----------------
    @app.post("/api/ai/draft")
    async def ai_draft(body: DraftIn):
        from .ai import AIUnavailableError, generate_draft

        try:
            draft = await asyncio.to_thread(generate_draft, settings, topic=body.topic,
                                            style=body.style, notes=body.notes)
        except AIUnavailableError as exc:
            raise HTTPException(503, str(exc)) from exc
        return draft.model_dump()

    # ---------------- 日志 ----------------
    @app.get("/api/logs")
    def list_logs(limit: int = 200):
        return db.list_logs(min(max(limit, 1), 1000))

    @app.get("/api/stats")
    def stats():
        posts = db.list_posts()
        counts = {s: 0 for s in POST_STATUSES}
        for p in posts:
            counts[p["status"]] = counts.get(p["status"], 0) + 1
        return {"posts": counts, "accounts": len(db.list_accounts()), "now": to_iso(utcnow())}

    return app
