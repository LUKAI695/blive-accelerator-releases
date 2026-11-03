"""发布流程与定时调度。"""

from __future__ import annotations

import asyncio
import logging

from .browser import NotLoggedInError, PublishError, PublishJob, Publisher
from .config import Settings
from .db import Database, rate_limit_reason, to_iso, utcnow

log = logging.getLogger("xhs_studio")


def validate_post(post: dict, settings: Settings) -> list[str]:
    """发布前校验，返回问题列表（空列表表示可以发布）。"""
    problems = []
    if not post.get("account_id"):
        problems.append("未选择发布账号")
    title = (post.get("title") or "").strip()
    if not title:
        problems.append("标题不能为空")
    elif len(title) > settings.title_max:
        problems.append(f"标题超过 {settings.title_max} 字")
    if len(post.get("body") or "") > settings.body_max:
        problems.append(f"正文超过 {settings.body_max} 字")
    images = post.get("images") or []
    if not images:
        problems.append("图文笔记至少需要 1 张图片")
    elif len(images) > settings.images_max:
        problems.append(f"图片最多 {settings.images_max} 张")
    return problems


class PublishService:
    def __init__(self, db: Database, publisher: Publisher, settings: Settings):
        self.db = db
        self.publisher = publisher
        self.settings = settings
        self._task: asyncio.Task | None = None

    def blocked_reason(self, post: dict) -> str | None:
        problems = validate_post(post, self.settings)
        if problems:
            return "；".join(problems)
        account = self.db.get_account(post["account_id"])
        if account is None:
            return "发布账号不存在"
        return rate_limit_reason(
            self.db, post["account_id"], now=utcnow(),
            min_interval_minutes=self.settings.min_interval_minutes,
            daily_limit=self.settings.daily_limit,
        )

    async def publish(self, post_id: int, *, from_statuses: tuple[str, ...]) -> bool:
        """执行一次发布。调用前应已通过 blocked_reason 检查。"""
        if not self.db.claim_post(post_id, from_statuses):
            return False
        return await self.run_claimed(post_id)

    async def run_claimed(self, post_id: int) -> bool:
        """发布一篇已被 claim_post 置为 publishing 的帖子。"""
        post = self.db.get_post(post_id)
        assert post is not None
        account_id = post["account_id"]
        self.db.log(f"开始发布《{post['title']}》", post_id=post_id, account_id=account_id)
        job = PublishJob(
            post_id=post_id,
            account_id=account_id,
            title=post["title"].strip(),
            body=post["body"],
            tags=post["tags"],
            image_paths=[self.settings.uploads_dir / name for name in post["images"]],
        )
        try:
            await self.publisher.publish(job)
        except NotLoggedInError as exc:
            self.db.set_account_status(account_id, "logged_out")
            self._fail(post_id, account_id, str(exc))
            return False
        except PublishError as exc:
            msg = str(exc) + (f"（截图：{exc.screenshot}）" if exc.screenshot else "")
            self._fail(post_id, account_id, msg)
            return False
        except Exception as exc:  # noqa: BLE001
            self._fail(post_id, account_id, f"{type(exc).__name__}: {exc}")
            return False
        self.db.update_post(post_id, status="published", published_at=to_iso(utcnow()), error=None)
        self.db.set_account_status(account_id, "logged_in")
        self.db.log(f"发布成功《{post['title']}》", post_id=post_id, account_id=account_id)
        return True

    def _fail(self, post_id: int, account_id: int, message: str) -> None:
        self.db.update_post(post_id, status="failed", error=message)
        self.db.log(f"发布失败：{message}", level="error", post_id=post_id, account_id=account_id)

    # ---------------- 调度 ----------------
    async def run_due_once(self) -> int:
        """处理所有到期的定时帖子；被频率限制挡住的留到下一轮。返回成功篇数。"""
        done = 0
        busy_accounts: set[int] = set()
        for post in self.db.due_posts(utcnow()):
            account_id = post["account_id"]
            if account_id in busy_accounts:
                continue
            reason = self.blocked_reason(post)
            if reason:
                if validate_post(post, self.settings) or self.db.get_account(account_id or 0) is None:
                    # 内容本身有问题，等待不会好转，直接标记失败
                    self._fail(post["id"], account_id, reason)
                continue  # 频率限制：保持 scheduled，下一轮再看
            busy_accounts.add(account_id)
            if await self.publish(post["id"], from_statuses=("scheduled",)):
                done += 1
        return done

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_due_once()
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")
            await asyncio.sleep(self.settings.scheduler_interval_seconds)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
