"""SQLite 存储：账号、帖子、日志。"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'unknown',
    last_checked TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    images TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'draft',
    scheduled_at TEXT,
    published_at TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_posts_status_time ON posts(status, scheduled_at);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER,
    account_id INTEGER,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

POST_STATUSES = ("draft", "scheduled", "publishing", "published", "failed")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    """统一存储格式：UTC，秒级，Z 结尾，可直接做字符串比较。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        self._conn.close()

    # ---------- accounts ----------
    def create_account(self, name: str, note: str = "") -> dict[str, Any]:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO accounts(name, note, created_at) VALUES (?, ?, ?)",
                (name, note, to_iso(utcnow())),
            )
            account_id = cur.lastrowid
        return self.get_account(account_id)  # type: ignore[return-value]

    def get_account(self, account_id: int) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return dict(row) if row else None

    def list_accounts(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def set_account_status(self, account_id: int, status: str) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE accounts SET status = ?, last_checked = ? WHERE id = ?",
                (status, to_iso(utcnow()), account_id),
            )

    def delete_account(self, account_id: int) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM accounts WHERE id = ?", (account_id,))

    # ---------- posts ----------
    @staticmethod
    def _post_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        d = dict(row)
        d["tags"] = json.loads(d["tags"] or "[]")
        d["images"] = json.loads(d["images"] or "[]")
        return d

    def create_post(
        self,
        *,
        account_id: int | None,
        title: str,
        body: str,
        tags: list[str],
        images: list[str],
    ) -> dict[str, Any]:
        now = to_iso(utcnow())
        with self._tx() as c:
            cur = c.execute(
                """INSERT INTO posts(account_id, title, body, tags, images, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'draft', ?, ?)""",
                (account_id, title, body, json.dumps(tags, ensure_ascii=False),
                 json.dumps(images), now, now),
            )
            post_id = cur.lastrowid
        return self.get_post(post_id)  # type: ignore[return-value]

    def get_post(self, post_id: int) -> dict[str, Any] | None:
        return self._post_row(self._conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone())

    def list_posts(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM posts WHERE status = ? ORDER BY COALESCE(scheduled_at, updated_at) DESC", (status,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM posts ORDER BY updated_at DESC").fetchall()
        return [self._post_row(r) for r in rows]  # type: ignore[misc]

    def update_post(self, post_id: int, **fields: Any) -> dict[str, Any] | None:
        allowed = {"account_id", "title", "body", "tags", "images", "status",
                   "scheduled_at", "published_at", "error"}
        sets, values = [], []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"unknown field {key}")
            if key in ("tags", "images"):
                value = json.dumps(value, ensure_ascii=False)
            sets.append(f"{key} = ?")
            values.append(value)
        if not sets:
            return self.get_post(post_id)
        sets.append("updated_at = ?")
        values.append(to_iso(utcnow()))
        values.append(post_id)
        with self._tx() as c:
            c.execute(f"UPDATE posts SET {', '.join(sets)} WHERE id = ?", values)
        return self.get_post(post_id)

    def claim_post(self, post_id: int, from_statuses: tuple[str, ...]) -> bool:
        """原子地把帖子置为 publishing，防止调度器和“立即发布”重复发同一篇。"""
        marks = ",".join("?" for _ in from_statuses)
        with self._tx() as c:
            cur = c.execute(
                f"UPDATE posts SET status = 'publishing', error = NULL, updated_at = ? "
                f"WHERE id = ? AND status IN ({marks})",
                (to_iso(utcnow()), post_id, *from_statuses),
            )
            return cur.rowcount == 1

    def delete_post(self, post_id: int) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM posts WHERE id = ?", (post_id,))

    def due_posts(self, now: datetime) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM posts WHERE status = 'scheduled' AND scheduled_at <= ? ORDER BY scheduled_at",
            (to_iso(now),),
        ).fetchall()
        return [self._post_row(r) for r in rows]  # type: ignore[misc]

    def recover_interrupted(self) -> int:
        """进程重启时，处于 publishing 的帖子状态未知，标记为失败交给人工确认。"""
        with self._tx() as c:
            cur = c.execute(
                "UPDATE posts SET status = 'failed', error = ?, updated_at = ? WHERE status = 'publishing'",
                ("发布过程中程序中断，请到小红书确认是否已发出后再重试", to_iso(utcnow())),
            )
            return cur.rowcount

    # ---------- rate-limit helpers ----------
    def published_since(self, account_id: int, since: datetime) -> list[str]:
        rows = self._conn.execute(
            "SELECT published_at FROM posts WHERE account_id = ? AND status = 'published' "
            "AND published_at >= ? ORDER BY published_at DESC",
            (account_id, to_iso(since)),
        ).fetchall()
        return [r["published_at"] for r in rows]

    # ---------- logs ----------
    def log(self, message: str, *, level: str = "info", post_id: int | None = None,
            account_id: int | None = None) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO logs(post_id, account_id, level, message, created_at) VALUES (?, ?, ?, ?, ?)",
                (post_id, account_id, level, message, to_iso(utcnow())),
            )

    def list_logs(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def rate_limit_reason(db: Database, account_id: int, *, now: datetime,
                      min_interval_minutes: int, daily_limit: int) -> str | None:
    """返回不能发布的原因；可以发布时返回 None。"""
    recent = db.published_since(account_id, now - timedelta(hours=24))
    if daily_limit > 0 and len(recent) >= daily_limit:
        return f"该账号 24 小时内已发布 {len(recent)} 篇，达到上限 {daily_limit}"
    if recent and min_interval_minutes > 0:
        last = from_iso(recent[0])
        wait_until = last + timedelta(minutes=min_interval_minutes)
        if now < wait_until:
            mins = int((wait_until - now).total_seconds() // 60) + 1
            return f"距离上次发布不足 {min_interval_minutes} 分钟，约 {mins} 分钟后可发"
    return None
