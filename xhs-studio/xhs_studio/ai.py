"""用 Claude 生成小红书笔记文案草稿（标题 / 正文 / 话题）。

需要设置环境变量 ANTHROPIC_API_KEY（或已执行 `ant auth login`）。
生成结果只是草稿，发布前请人工修改确认。
"""

from __future__ import annotations

import anthropic
from pydantic import BaseModel, Field

from .config import Settings

SYSTEM_PROMPT = """你是一名资深的小红书内容编辑，帮助博主为自己的账号撰写图文笔记草稿。

写作要求：
- 标题不超过 20 个字（含标点和 emoji），具体、有信息量，避免夸大和“绝对化”用语（如“最”“第一”“100%有效”）。
- 正文 300~600 字为宜，绝不超过 1000 字。口语化、真诚，分短段落，可以适度使用 emoji 和小标题分点。
- 只写真实可信的内容：不编造个人经历、数据、价格或功效，不冒充用户评价，不使用医疗/金融承诺类表述。
- 话题标签 3~8 个，与内容强相关，不带 # 号。
- 不在正文中写站外引流信息（微信号、二维码、外链等）。"""


class PostDraft(BaseModel):
    title: str = Field(description="笔记标题，不超过 20 个字")
    body: str = Field(description="笔记正文，可包含换行和 emoji")
    tags: list[str] = Field(description="话题标签，不带 # 号")


class AIUnavailableError(RuntimeError):
    pass


def generate_draft(settings: Settings, *, topic: str, style: str = "", notes: str = "") -> PostDraft:
    parts = [f"主题：{topic.strip()}"]
    if style.strip():
        parts.append(f"账号风格 / 目标读者：{style.strip()}")
    if notes.strip():
        parts.append(f"必须包含的要点或素材：\n{notes.strip()}")
    parts.append("请写一篇小红书图文笔记草稿。")

    try:
        client = anthropic.Anthropic()
        response = client.messages.parse(
            model=settings.ai_model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            output_config={"effort": "medium"},
            messages=[{"role": "user", "content": "\n\n".join(parts)}],
            output_format=PostDraft,
        )
    except anthropic.AuthenticationError as exc:
        raise AIUnavailableError("Claude API 认证失败，请检查 ANTHROPIC_API_KEY") from exc
    except anthropic.RateLimitError as exc:
        raise AIUnavailableError("Claude API 请求过于频繁，请稍后再试") from exc
    except anthropic.APIConnectionError as exc:
        raise AIUnavailableError("无法连接 Claude API，请检查网络") from exc
    except anthropic.APIStatusError as exc:
        raise AIUnavailableError(f"Claude API 返回错误：{exc.status_code}") from exc

    if response.stop_reason == "refusal":
        raise AIUnavailableError("模型拒绝生成该主题的内容，请调整主题后重试")
    if response.parsed_output is None:
        raise AIUnavailableError("模型输出无法解析，请重试")

    draft = response.parsed_output
    draft.tags = [t.strip().lstrip("#") for t in draft.tags if t.strip()][:10]
    return draft
