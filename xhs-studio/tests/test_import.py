from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from xhs_studio.app import create_app
from xhs_studio.config import Settings
from xhs_studio.db import from_iso, utcnow
from xhs_studio.importer import ImportFormatError, parse_csv, plan_slots

from test_studio import PNG, FakePublisher


def test_parse_chinese_headers_and_gbk():
    data = "标题,正文,话题,图片\n杭州一日游,\"第一段\n第二段\",#旅行 杭州，旅行,a.jpg|b.png\n".encode("gb18030")
    (row,) = parse_csv(data)
    assert row.title == "杭州一日游"
    assert row.body == "第一段\n第二段"
    assert row.tags == ["旅行", "杭州"]
    assert row.image_names == ["a.jpg", "b.png"]


def test_parse_english_headers_bom_and_blank_lines():
    data = "﻿Title,Body\nhello,world\n,\n".encode("utf-8")
    rows = parse_csv(data)
    assert len(rows) == 1 and rows[0].line == 2


def test_parse_requires_title_column():
    with pytest.raises(ImportFormatError):
        parse_csv("正文\nxx\n".encode())


def test_plan_slots_respects_daily_limit():
    start = utcnow()
    slots = plan_slots(start, 5, interval_minutes=60, daily_limit=3)
    assert slots[:3] == [start, start + timedelta(hours=1), start + timedelta(hours=2)]
    assert slots[3] == start + timedelta(hours=24)
    assert slots[4] == start + timedelta(hours=25)
    for i in range(len(slots)):
        window = [s for s in slots if slots[i] <= s < slots[i] + timedelta(hours=24)]
        assert len(window) <= 3


@pytest.fixture
def client(tmp_path):
    s = Settings(data_dir=tmp_path, scheduler_enabled=False, min_interval_minutes=30, daily_limit=2)
    with TestClient(create_app(s, FakePublisher())) as c:
        yield c


def test_import_endpoint_schedules_valid_rows(client):
    acc = client.post("/api/accounts", json={"name": "主号"}).json()
    csv_bytes = ("标题,正文,话题,图片\n"
                 "第一篇,内容,旅行,A.png\n"
                 "第二篇,内容,,b.png\n"
                 "第三篇,内容,,missing.png\n"
                 "第四篇,内容,,a.png\n").encode("utf-8-sig")
    start = (utcnow() + timedelta(hours=1)).isoformat()
    r = client.post("/api/posts/import",
                    files=[("csv_file", ("p.csv", csv_bytes, "text/csv")),
                           ("images", ("a.png", PNG, "image/png")),
                           ("images", ("b.png", PNG, "image/png"))],
                    data={"account_id": str(acc["id"]), "start_at": start, "interval_minutes": "60"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["created"] == 4 and body["scheduled"] == 3
    rows = {x["title"]: x for x in body["rows"]}
    assert rows["第三篇"]["status"] == "draft" and "missing.png" in rows["第三篇"]["problems"][0]
    # 每日上限 2：第三篇可排的（第四篇）顺延到第一篇 24 小时后
    t1 = from_iso(rows["第一篇"]["scheduled_at"])
    t4 = from_iso(rows["第四篇"]["scheduled_at"])
    assert t4 - t1 >= timedelta(hours=24)


def test_import_rejects_interval_below_limit(client):
    acc = client.post("/api/accounts", json={"name": "a"}).json()
    r = client.post("/api/posts/import",
                    files=[("csv_file", ("p.csv", "标题\nx\n".encode(), "text/csv"))],
                    data={"account_id": str(acc["id"]), "start_at": (utcnow() + timedelta(hours=1)).isoformat(),
                          "interval_minutes": "5"})
    assert r.status_code == 400


def test_template_is_served(client):
    r = client.get("/static/import-template.csv")
    assert r.status_code == 200
    assert parse_csv(r.content)[0].title == "周末杭州citywalk路线"
