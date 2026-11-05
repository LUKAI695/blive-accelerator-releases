"""批量导入：解析 CSV（可用 Excel 另存为 CSV），并计算排期时间。

CSV 表头支持中文或英文（大小写不敏感）：
    标题 / title     必填
    正文 / body
    话题 / tags      多个用空格、逗号或 # 分隔
    图片 / images    图片文件名，多个用 | 或 ; 分隔，顺序即发布顺序（第一张为封面）
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

HEADER_ALIASES = {
    "title": {"title", "标题"},
    "body": {"body", "content", "正文", "内容"},
    "tags": {"tags", "topics", "话题", "标签", "话题标签"},
    "images": {"images", "image", "pictures", "图片", "图片文件"},
}
MAX_ROWS = 200


class ImportFormatError(ValueError):
    pass


@dataclass
class ImportRow:
    line: int
    title: str
    body: str
    tags: list[str]
    image_names: list[str]
    problems: list[str] = field(default_factory=list)


def decode_csv(data: bytes) -> str:
    """Excel 在中文 Windows 上默认存为 GBK，其余一般是 UTF-8（可能带 BOM）。"""
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ImportFormatError("无法识别 CSV 编码，请另存为 UTF-8 格式的 CSV")


def _map_headers(headers: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, raw in enumerate(headers):
        key = raw.strip().lower()
        for field_name, aliases in HEADER_ALIASES.items():
            if key in aliases and field_name not in mapping:
                mapping[field_name] = idx
    if "title" not in mapping:
        raise ImportFormatError("CSV 第一行必须是表头，并包含「标题」列")
    return mapping


def _split(value: str, pattern: str) -> list[str]:
    return [p.strip() for p in re.split(pattern, value or "") if p.strip()]


def parse_csv(data: bytes) -> list[ImportRow]:
    text = decode_csv(data)
    reader = csv.reader(io.StringIO(text))
    try:
        headers = next(reader)
    except StopIteration as exc:
        raise ImportFormatError("CSV 是空的") from exc
    cols = _map_headers(headers)

    def cell(row: list[str], name: str) -> str:
        i = cols.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    rows: list[ImportRow] = []
    for line_no, row in enumerate(reader, start=2):
        if not any(c.strip() for c in row):
            continue
        tags = []
        for t in _split(cell(row, "tags"), r"[,，、\s#]+"):
            if t not in tags:
                tags.append(t)
        rows.append(ImportRow(
            line=line_no,
            title=cell(row, "title"),
            body=cell(row, "body").replace("\r\n", "\n"),
            tags=tags,
            image_names=_split(cell(row, "images"), r"[|;；]+"),
        ))
        if len(rows) > MAX_ROWS:
            raise ImportFormatError(f"一次最多导入 {MAX_ROWS} 篇")
    if not rows:
        raise ImportFormatError("CSV 里没有内容行")
    return rows


def plan_slots(start: datetime, count: int, *, interval_minutes: int, daily_limit: int) -> list[datetime]:
    """从 start 开始按间隔排期，同时保证任意 24 小时内不超过 daily_limit 篇。"""
    slots: list[datetime] = []
    step = timedelta(minutes=max(interval_minutes, 0))
    for i in range(count):
        t = start if i == 0 else slots[-1] + step
        if daily_limit > 0 and i >= daily_limit:
            t = max(t, slots[i - daily_limit] + timedelta(hours=24))
        slots.append(t)
    return slots
