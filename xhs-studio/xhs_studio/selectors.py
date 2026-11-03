"""小红书创作服务平台的页面地址与元素选择器。

网页改版后选择器可能失效。无需改代码：在数据目录下放一个 selectors.json，
写入需要覆盖的字段即可，例如 {"title_input": "input.d-text"}。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class Selectors:
    login_url: str = "https://creator.xiaohongshu.com/login"
    home_url: str = "https://creator.xiaohongshu.com/new/home"
    publish_url: str = "https://creator.xiaohongshu.com/publish/publish?source=official"
    # URL 中包含该片段即视为未登录
    login_marker: str = "/login"

    image_tab_text: str = "上传图文"
    image_input: str = 'input[type="file"]'
    title_input: str = 'input[placeholder*="标题"]'
    body_editor: str = 'div.tiptap.ProseMirror, div.ql-editor, [contenteditable="true"]'
    topic_suggestion: str = "#creator-editor-topic-container .item, .publish-topic-item"
    publish_button_text: str = "发布"
    success_text: str = "发布成功"
    success_url_fragment: str = "published=true"

    @classmethod
    def load(cls, override_path: Path | None = None) -> "Selectors":
        sel = cls()
        if override_path and override_path.exists():
            data = json.loads(override_path.read_text(encoding="utf-8"))
            known = {f.name for f in fields(cls)}
            for key, value in data.items():
                if key in known and isinstance(value, str):
                    setattr(sel, key, value)
        return sel

    def to_dict(self) -> dict[str, str]:
        return asdict(self)
