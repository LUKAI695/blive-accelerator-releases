import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from xhs_studio.app import create_app
from xhs_studio.browser import NotLoggedInError, PublishError
from xhs_studio.config import Settings
from xhs_studio.db import Database, rate_limit_reason, to_iso, utcnow
from xhs_studio.service import PublishService

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8ffff3f0005fe02fea7d6a4b30000000049454e44ae426082"
)


class FakePublisher:
    def __init__(self):
        self.published = []
        self.fail_with = None

    async def login(self, account_id):
        return True

    async def check_login(self, account_id):
        return True

    async def publish(self, job):
        if self.fail_with:
            raise self.fail_with
        self.published.append(job)


@pytest.fixture
def settings(tmp_path):
    s = Settings(data_dir=tmp_path, scheduler_enabled=False, min_interval_minutes=30, daily_limit=2)
    s.ensure_dirs()
    return s


@pytest.fixture
def client(settings):
    fake = FakePublisher()
    app = create_app(settings, fake)
    with TestClient(app) as c:
        c.fake = fake
        yield c


def _make_post(client, **over):
    acc = client.post("/api/accounts", json={"name": "主号"}).json()
    img = client.post("/api/uploads", files=[("files", ("a.png", PNG, "image/png"))]).json()["files"]
    body = {"account_id": acc["id"], "title": "杭州一日游", "body": "第一段\n第二段",
            "tags": ["#旅行", "杭州", "旅行"], "images": img}
    body.update(over)
    return client.post("/api/posts", json=body).json()


def test_create_post_cleans_tags(client):
    post = _make_post(client)
    assert post["status"] == "draft"
    assert post["tags"] == ["旅行", "杭州"]


def test_upload_rejects_non_image(client):
    r = client.post("/api/uploads", files=[("files", ("x.exe", b"MZ", "application/octet-stream"))])
    assert r.status_code == 400


def test_post_rejects_unknown_image(client):
    acc = client.post("/api/accounts", json={"name": "a"}).json()
    r = client.post("/api/posts", json={"account_id": acc["id"], "images": ["../../etc/passwd"]})
    assert r.status_code == 400


def test_validation_blocks_schedule(client):
    post = _make_post(client, title="这是一个明显超过二十个字的标题，用来测试长度校验是否生效")
    r = client.post(f"/api/posts/{post['id']}/schedule", json={"scheduled_at": to_iso(utcnow())})
    assert r.status_code == 400
    assert "20" in r.json()["detail"]


def test_publish_now_success(client):
    post = _make_post(client)
    r = client.post(f"/api/posts/{post['id']}/publish")
    assert r.status_code == 202
    # 后台任务在同一事件循环中运行，轮询直到完成
    for _ in range(50):
        if client.get(f"/api/posts/{post['id']}").json()["status"] == "published":
            break
    got = client.get(f"/api/posts/{post['id']}").json()
    assert got["status"] == "published"
    assert client.fake.published[0].tags == ["旅行", "杭州"]


def test_rate_limit_and_interval(settings):
    db = Database(settings.db_path)
    acc = db.create_account("a")
    now = utcnow()
    for minutes_ago in (10,):
        p = db.create_post(account_id=acc["id"], title="t", body="", tags=[], images=["x.png"])
        db.update_post(p["id"], status="published", published_at=to_iso(now - timedelta(minutes=minutes_ago)))
    reason = rate_limit_reason(db, acc["id"], now=now, min_interval_minutes=30, daily_limit=2)
    assert reason and "30" in reason
    assert rate_limit_reason(db, acc["id"], now=now + timedelta(minutes=25), min_interval_minutes=30, daily_limit=2) is None
    p = db.create_post(account_id=acc["id"], title="t", body="", tags=[], images=["x.png"])
    db.update_post(p["id"], status="published", published_at=to_iso(now - timedelta(hours=2)))
    reason = rate_limit_reason(db, acc["id"], now=now + timedelta(hours=1), min_interval_minutes=30, daily_limit=2)
    assert reason and "上限" in reason


def _scheduled(db, settings, account_id, minutes_ago=1):
    (settings.uploads_dir / "x.png").write_bytes(PNG)
    p = db.create_post(account_id=account_id, title="标题", body="正文", tags=[], images=["x.png"])
    return db.update_post(p["id"], status="scheduled", scheduled_at=to_iso(utcnow() - timedelta(minutes=minutes_ago)))


def test_scheduler_publishes_one_per_account_per_tick(settings):
    db = Database(settings.db_path)
    fake = FakePublisher()
    svc = PublishService(db, fake, settings)
    acc = db.create_account("a")
    p1 = _scheduled(db, settings, acc["id"], 5)
    p2 = _scheduled(db, settings, acc["id"], 1)
    assert asyncio.run(svc.run_due_once()) == 1
    assert db.get_post(p1["id"])["status"] == "published"
    # 第二篇受最小间隔限制，保持定时状态而不是失败
    assert asyncio.run(svc.run_due_once()) == 0
    assert db.get_post(p2["id"])["status"] == "scheduled"


def test_scheduler_marks_invalid_post_failed(settings):
    db = Database(settings.db_path)
    svc = PublishService(db, FakePublisher(), settings)
    acc = db.create_account("a")
    p = db.create_post(account_id=acc["id"], title="", body="", tags=[], images=[])
    db.update_post(p["id"], status="scheduled", scheduled_at=to_iso(utcnow()))
    asyncio.run(svc.run_due_once())
    assert db.get_post(p["id"])["status"] == "failed"


def test_not_logged_in_marks_account(settings):
    db = Database(settings.db_path)
    fake = FakePublisher()
    fake.fail_with = NotLoggedInError("登录已失效")
    svc = PublishService(db, fake, settings)
    acc = db.create_account("a")
    p = _scheduled(db, settings, acc["id"])
    asyncio.run(svc.run_due_once())
    assert db.get_post(p["id"])["status"] == "failed"
    assert db.get_account(acc["id"])["status"] == "logged_out"


def test_publish_error_keeps_screenshot_name(settings):
    db = Database(settings.db_path)
    fake = FakePublisher()
    fake.fail_with = PublishError("找不到按钮", "shot.png")
    svc = PublishService(db, fake, settings)
    acc = db.create_account("a")
    p = _scheduled(db, settings, acc["id"])
    asyncio.run(svc.run_due_once())
    assert "shot.png" in db.get_post(p["id"])["error"]


def test_claim_prevents_double_publish(settings):
    db = Database(settings.db_path)
    acc = db.create_account("a")
    p = _scheduled(db, settings, acc["id"])
    assert db.claim_post(p["id"], ("scheduled",)) is True
    assert db.claim_post(p["id"], ("scheduled",)) is False


def test_recover_interrupted(settings):
    db = Database(settings.db_path)
    acc = db.create_account("a")
    p = _scheduled(db, settings, acc["id"])
    db.claim_post(p["id"], ("scheduled",))
    assert db.recover_interrupted() == 1
    assert db.get_post(p["id"])["status"] == "failed"
