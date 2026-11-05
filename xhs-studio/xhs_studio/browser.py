"""基于 Playwright 的小红书创作平台自动化。

每个账号使用独立的持久化浏览器目录（cookie / localStorage 都在里面），
只需扫码登录一次。程序不保存账号密码。
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import Settings
from .selectors import Selectors


class NotLoggedInError(RuntimeError):
    pass


class PublishError(RuntimeError):
    def __init__(self, message: str, screenshot: str | None = None):
        super().__init__(message)
        self.screenshot = screenshot


@dataclass
class PublishJob:
    post_id: int
    account_id: int
    title: str
    body: str
    tags: list[str]
    image_paths: list[Path]


class Publisher(Protocol):
    async def login(self, account_id: int) -> bool: ...
    async def check_login(self, account_id: int) -> bool: ...
    async def publish(self, job: PublishJob) -> None: ...


async def _pause(lo: float = 0.4, hi: float = 1.2) -> None:
    """步骤之间留一点时间给页面渲染。"""
    await asyncio.sleep(random.uniform(lo, hi))


class XHSBrowser:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._locks: dict[int, asyncio.Lock] = {}

    @property
    def selectors(self) -> Selectors:
        # 每次读取，修改 selectors.json 后无需重启
        return Selectors.load(self.settings.selectors_override)

    def _lock(self, account_id: int) -> asyncio.Lock:
        return self._locks.setdefault(account_id, asyncio.Lock())

    def profile_dir(self, account_id: int) -> Path:
        path = self.settings.profiles_dir / f"account_{account_id}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def _open(self, pw, account_id: int, headless: bool):
        return await pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir(account_id)),
            headless=headless,
            viewport={"width": 1366, "height": 900},
            locale="zh-CN",
            executable_path=self.settings.browser_path or None,
        )

    # ---------------- 登录 ----------------
    async def login(self, account_id: int) -> bool:
        """弹出浏览器窗口，用户用小红书 App 扫码。登录成功返回 True。"""
        from playwright.async_api import async_playwright

        sel = self.selectors
        async with self._lock(account_id), async_playwright() as pw:
            ctx = await self._open(pw, account_id, headless=False)
            try:
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                await page.goto(sel.login_url, wait_until="domcontentloaded")
                deadline = asyncio.get_running_loop().time() + self.settings.login_timeout_seconds
                while asyncio.get_running_loop().time() < deadline:
                    if sel.login_marker not in page.url:
                        await page.wait_for_load_state("domcontentloaded")
                        return True
                    await asyncio.sleep(1.5)
                return False
            finally:
                await ctx.close()

    async def check_login(self, account_id: int) -> bool:
        from playwright.async_api import async_playwright

        sel = self.selectors
        async with self._lock(account_id), async_playwright() as pw:
            ctx = await self._open(pw, account_id, headless=True)
            try:
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                await page.goto(sel.home_url, wait_until="domcontentloaded")
                await asyncio.sleep(3)  # 未登录时页面会跳转到登录页
                return sel.login_marker not in page.url
            finally:
                await ctx.close()

    # ---------------- 发布图文 ----------------
    async def publish(self, job: PublishJob) -> None:
        from playwright.async_api import async_playwright

        sel = self.selectors
        async with self._lock(job.account_id), async_playwright() as pw:
            ctx = await self._open(pw, job.account_id, headless=self.settings.headless)
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            try:
                await self._publish_steps(page, sel, job)
            except NotLoggedInError:
                raise
            except Exception as exc:  # noqa: BLE001 - 统一转成带截图的发布错误
                shot = await self._screenshot(page, job.post_id)
                raise PublishError(f"{type(exc).__name__}: {exc}", shot) from exc
            finally:
                await ctx.close()

    async def _publish_steps(self, page, sel: Selectors, job: PublishJob) -> None:
        await page.goto(sel.publish_url, wait_until="domcontentloaded")
        await _pause(2, 3)
        if sel.login_marker in page.url:
            raise NotLoggedInError("登录已失效，请重新扫码登录")

        # 1. 切换到“上传图文”
        await self._click_first_visible(page.get_by_text(sel.image_tab_text, exact=True))
        await _pause()

        # 2. 上传图片
        missing = [str(p) for p in job.image_paths if not p.exists()]
        if missing:
            raise FileNotFoundError(f"图片不存在: {missing}")
        await page.locator(sel.image_input).first.set_input_files([str(p) for p in job.image_paths])

        # 3. 标题（上传图片后编辑区才出现）
        title = page.locator(sel.title_input).first
        await title.wait_for(state="visible", timeout=60_000)
        await title.click()
        await title.fill(job.title)
        await _pause()

        # 4. 正文
        editor = page.locator(sel.body_editor).first
        await editor.wait_for(state="visible", timeout=30_000)
        await editor.click()
        lines = job.body.split("\n")
        for i, line in enumerate(lines):
            if line:
                await page.keyboard.insert_text(line)
            if i < len(lines) - 1:
                await page.keyboard.press("Enter")
        await _pause()

        # 5. 话题标签：输入 #xxx 后选择联想出的话题，没有联想则保留纯文本
        if job.tags:
            await page.keyboard.press("Enter")
            for tag in job.tags:
                await self._caret_to_end(editor)  # 点选联想话题后编辑器可能失去焦点
                await page.keyboard.type(f"#{tag}", delay=60)
                await asyncio.sleep(1.5)
                suggestion = page.locator(sel.topic_suggestion).first
                if await suggestion.count() and await suggestion.is_visible():
                    await suggestion.click()
                else:
                    await page.keyboard.type(" ")
                await _pause(0.3, 0.8)

        # 6. 等待图片上传完成后点击发布
        button = page.get_by_role("button", name=sel.publish_button_text, exact=True)
        await button.first.wait_for(state="visible", timeout=30_000)
        for _ in range(60):
            if await button.first.is_enabled():
                break
            await asyncio.sleep(1)
        await _pause(1, 2)
        await button.first.click()

        # 7. 确认发布成功
        for _ in range(40):
            if sel.success_url_fragment in page.url:
                return
            if await page.get_by_text(sel.success_text).count():
                return
            await asyncio.sleep(0.5)
        raise TimeoutError("点击发布后 20 秒内没有看到发布成功提示")

    @staticmethod
    async def _caret_to_end(editor) -> None:
        await editor.evaluate(
            """el => { el.focus(); const r = document.createRange(); r.selectNodeContents(el);
                       r.collapse(false); const s = window.getSelection(); s.removeAllRanges(); s.addRange(r); }"""
        )

    @staticmethod
    async def _click_first_visible(locator) -> None:
        for _ in range(20):
            for item in await locator.all():
                if await item.is_visible():
                    await item.click()
                    return
            await asyncio.sleep(0.5)
        raise TimeoutError("找不到可见的“上传图文”入口")

    async def _screenshot(self, page, post_id: int) -> str | None:
        try:
            from .db import to_iso, utcnow

            name = f"post_{post_id}_{to_iso(utcnow()).replace(':', '')}.png"
            path = self.settings.screenshots_dir / name
            await page.screenshot(path=str(path), full_page=True)
            return name
        except Exception:  # noqa: BLE001
            return None
