"""Block generators for the Book Engine.

Each generator produces content for a specific block type.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("knowall.book.blocks")


async def generate_text_block(topic: str, context: str, chapter_title: str) -> str:
    """Generate a text content block."""
    from app.core.api_scheduler import api_client

    prompt = f"""你是一位优秀的教材编写者。请为以下章节撰写详细的教材内容。

章节标题：{chapter_title}
具体主题：{topic}

参考材料：
{context[:3000]}

要求：
- 使用清晰、准确的学术语言
- 适当使用Markdown格式（标题、列表、加粗等）
- 重要概念用**加粗**标注
- 如有公式请使用LaTeX格式（$行内$ 或 $$独立$$）
- 内容详实，适合大学生学习"""

    result = await api_client.chat(prompt, task_type="book_gen", max_tokens=4096)
    if not isinstance(result, str) or not result.strip():
        raise ValueError(f"{topic}的教材正文生成为空，请重试")
    return result


async def generate_quiz_block(topic: str, context: str, chapter_title: str) -> dict:
    """Generate an embedded quiz block."""
    from app.core.api_scheduler import api_client

    prompt = f"""请为以下学习内容生成 2-3 道测试题。

章节：{chapter_title}
主题：{topic}

参考材料：
{context[:2000]}

请以JSON格式返回，包含 questions 数组，每个问题有:
- question: 题目文本
- type: "single_choice" | "fill_blank" | "short_answer"
- options: (选择题) [{{"label":"A","text":"..."}}]
- answer: 正确答案
- explanation: 解析

只返回JSON。"""

    from app.core.api_scheduler.client import GenerationConfig, AllModelsFailedError
    invalid_response = False
    def validate(content):
        nonlocal invalid_response
        valid = _parse_quiz(content) is not None
        invalid_response |= not valid
        return valid
    try:
        result = await api_client.chat(prompt, task_type="book_gen",
            config=GenerationConfig(response_validator=validate))
    except AllModelsFailedError:
        if not invalid_response:
            raise
        result = ""
    return _parse_quiz(result) or {"questions": [{"question": f"请简述{topic}的核心概念", "type": "short_answer", "answer": "（待补充）"}]}


def _parse_quiz(content: str) -> dict | None:
    """Use the display/export field contract before accepting model output."""
    try:
        import json, re
        text = content.strip()
        if text.startswith("```"):
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                text = match.group(0)
        data = json.loads(text)
        if not isinstance(data, dict) or not isinstance(data.get("questions"), list) or not data["questions"]:
            return None
        for question in data["questions"]:
            if not isinstance(question, dict) or any(
                not isinstance(question.get(field), str) or not question[field].strip()
                for field in ("question", "answer")
            ):
                return None
            kind = question.get("type", "short_answer")
            if kind not in ("single_choice", "fill_blank", "short_answer") or not isinstance(question.get("explanation", ""), str):
                return None
            options = question.get("options", [])
            if not isinstance(options, list):
                return None
            labels = set()
            for option in options:
                if not isinstance(option, dict) or any(
                    not isinstance(option.get(field), str) or not option[field].strip()
                    for field in ("label", "text")
                ) or option["label"] in labels:
                    return None
                labels.add(option["label"])
            if kind == "single_choice" and question["answer"] not in labels:
                return None
        return data
    except (ValueError, TypeError, AttributeError):
        return None


async def generate_callout_block(topic: str, context: str, callout_type: str = "key_point") -> dict:
    """Generate a callout/highlight block."""
    from app.core.api_scheduler import api_client

    type_labels = {
        "key_point": "重点",
        "warning": "注意",
        "tip": "提示",
        "example": "举例",
    }
    label = type_labels.get(callout_type, "提示")

    prompt = f"""请用一句话概括以下内容的{label}：

{context[:1000]}

只返回一句话，不要添加任何标题或前缀。"""

    result = await api_client.chat(prompt, task_type="book_gen", max_tokens=256)
    return {"type": callout_type, "label": label, "content": result or topic}


async def generate_flash_cards_block(topic: str, context: str, chapter_title: str) -> list[dict]:
    """Generate flash cards for a section."""
    from app.core.api_scheduler import api_client

    prompt = f"""请根据以下学习内容生成 3-5 张闪卡。

章节：{chapter_title}
主题：{topic}

参考材料：
{context[:2000]}

请以JSON格式返回 cards 数组，每张卡片有:
- front: 正面（问题/概念）
- back: 背面（答案/解释）
- type: "qa" | "cloze"

只返回JSON。"""

    result = await api_client.chat(prompt, task_type="book_gen")
    try:
        import json, re
        text = result.strip()
        if text.startswith("```"):
            match = re.search(r'\[.*\]', text, re.DOTALL)
            if match:
                text = match.group(0)
        data = json.loads(text)
        return data if isinstance(data, list) else data.get("cards", [])
    except Exception:
        return [{"front": topic, "back": "（待补充）", "type": "qa"}]


async def generate_retrieval_practice(topic: str, context: str) -> str:
    """Generate a retrieval practice prompt (no LLM needed)."""
    return f"请闭上眼睛，尝试回忆关于 **{topic}** 的核心知识点，然后写下你能记住的内容。之后再翻阅教材进行对照。"
