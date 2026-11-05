"""运行配置。全部可以通过环境变量覆盖。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("XHS_DATA_DIR", Path.home() / ".xhs-studio")).expanduser()
    )
    # 发布时是否无头运行浏览器。默认显示窗口，便于观察和人工介入（验证码等）。
    headless: bool = field(default_factory=lambda: _env_bool("XHS_HEADLESS", False))
    # 同一账号两次发布之间的最小间隔（分钟）
    min_interval_minutes: int = field(default_factory=lambda: _env_int("XHS_MIN_INTERVAL_MINUTES", 30))
    # 同一账号 24 小时内最多发布篇数
    daily_limit: int = field(default_factory=lambda: _env_int("XHS_DAILY_LIMIT", 3))
    # 调度器轮询间隔（秒）
    scheduler_interval_seconds: int = field(default_factory=lambda: _env_int("XHS_SCHEDULER_INTERVAL", 30))
    scheduler_enabled: bool = field(default_factory=lambda: _env_bool("XHS_SCHEDULER_ENABLED", True))
    # 扫码登录等待时长（秒）
    login_timeout_seconds: int = field(default_factory=lambda: _env_int("XHS_LOGIN_TIMEOUT", 180))
    # 可选：指定浏览器可执行文件（例如本机 Google Chrome），留空使用 Playwright 自带 Chromium
    browser_path: str = field(default_factory=lambda: os.environ.get("XHS_BROWSER_PATH", ""))
    ai_model: str = field(default_factory=lambda: os.environ.get("XHS_AI_MODEL", "claude-opus-5-5"))

    # 小红书平台的内容限制
    title_max: int = 20
    body_max: int = 1000
    images_max: int = 18

    @property
    def db_path(self) -> Path:
        return self.data_dir / "studio.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def profiles_dir(self) -> Path:
        return self.data_dir / "profiles"

    @property
    def screenshots_dir(self) -> Path:
        return self.data_dir / "screenshots"

    @property
    def selectors_override(self) -> Path:
        return self.data_dir / "selectors.json"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.uploads_dir, self.profiles_dir, self.screenshots_dir):
            d.mkdir(parents=True, exist_ok=True)
