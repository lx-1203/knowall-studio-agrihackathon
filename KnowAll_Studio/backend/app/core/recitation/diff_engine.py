"""文本 diff 对比引擎 - 用于默写模式的逐字对比"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

try:
    from diff_match_patch import diff_match_patch
except ImportError:
    # Fallback: simple character-level diff when diff-match-patch is not installed
    diff_match_patch = None


@dataclass
class DiffSegment:
    """一个 diff 片段"""
    type: Literal["equal", "insert", "delete"]  # equal=正确, insert=多写, delete=漏写
    text: str


def _normalize_text(text: str) -> str:
    """标准化文本：去除首尾空白，统一换行"""
    text = text.strip()
    text = re.sub(r'\r\n?', '\n', text)
    # Defensive cap on input size to bound the O(n*m) diff cost.
    # Callers (API) enforce a tighter limit; this is a backstop.
    return text[:100_000]


def diff_texts(original: str, user_input: str) -> list[dict]:
    """
    对比原文和用户输入，返回逐字 diff 结果。

    Args:
        original: 原文
        user_input: 用户默写输入

    Returns:
        list of {type: "equal"|"insert"|"delete", text: "..."}
        - equal: 用户写对的部分
        - insert: 用户多写的部分（原文没有）
        - delete: 用户漏写的部分（原文有但用户没写）
    """
    original = _normalize_text(original)
    user_input = _normalize_text(user_input)

    if diff_match_patch is not None:
        return _diff_with_dmp(original, user_input)
    return _diff_simple(original, user_input)


def _diff_with_dmp(original: str, user_input: str) -> list[dict]:
    """使用 diff-match-patch 库进行精确对比"""
    dmp = diff_match_patch()
    diffs = dmp.diff_main(original, user_input)
    dmp.diff_cleanupSemantic(diffs)

    result = []
    for op, text in diffs:
        if op == 0:  # EQUAL
            result.append({"type": "equal", "text": text})
        elif op == -1:  # DELETE (原文有，用户没写)
            result.append({"type": "delete", "text": text})
        elif op == 1:  # INSERT (用户多写)
            result.append({"type": "insert", "text": text})

    return result


def _diff_simple(original: str, user_input: str) -> list[dict]:
    """简单逐字对比（fallback，当 diff-match-patch 未安装时使用）"""
    result = []
    orig_chars = list(original)
    input_chars = list(user_input)

    i, j = 0, 0
    while i < len(orig_chars) and j < len(input_chars):
        if orig_chars[i] == input_chars[j]:
            # 连续相同字符合并
            eq_text = orig_chars[i]
            i += 1
            j += 1
            while i < len(orig_chars) and j < len(input_chars) and orig_chars[i] == input_chars[j]:
                eq_text += orig_chars[i]
                i += 1
                j += 1
            result.append({"type": "equal", "text": eq_text})
        else:
            # 尝试向前看找到匹配
            found_in_orig = False
            found_in_input = False

            # 用户漏写了（原文有但用户跳过）
            for k in range(1, min(5, len(orig_chars) - i)):
                if orig_chars[i + k] == input_chars[j]:
                    result.append({"type": "delete", "text": ''.join(orig_chars[i:i + k])})
                    i += k
                    found_in_orig = True
                    break

            if found_in_orig:
                continue

            # 用户多写了（用户有但原文没有）
            for k in range(1, min(5, len(input_chars) - j)):
                if input_chars[j + k] == orig_chars[i]:
                    result.append({"type": "insert", "text": ''.join(input_chars[j:j + k])})
                    j += k
                    found_in_input = True
                    break

            if found_in_input:
                continue

            # 都找不到，标记为一删一增
            result.append({"type": "delete", "text": orig_chars[i]})
            result.append({"type": "insert", "text": input_chars[j]})
            i += 1
            j += 1

    # 处理剩余字符
    if i < len(orig_chars):
        result.append({"type": "delete", "text": ''.join(orig_chars[i:])})
    if j < len(input_chars):
        result.append({"type": "insert", "text": ''.join(input_chars[j:])})

    return result


def compute_accuracy(original: str, user_input: str, diffs: list[dict] | None = None) -> float:
    """
    计算默写准确率 (0-1)。

    算法：正确字符数 / 原文总字符数
    """
    if diffs is None:
        diffs = diff_texts(original, user_input)

    original = _normalize_text(original)
    if not original:
        return 1.0

    correct_chars = sum(len(d["text"]) for d in diffs if d["type"] == "equal")
    total_chars = len(original)

    return round(min(correct_chars / total_chars, 1.0), 4)


def generate_hide_plan(content: str, level: int = 1) -> dict:
    """
    生成遮字背诵的隐藏方案。

    Args:
        content: 原文
        level: 隐藏等级 1-5
            1 = 隐藏 20% 的字符（初级）
            2 = 隐藏 40%
            3 = 隐藏 60%
            4 = 隐藏 80%
            5 = 全部隐藏（默写模式）

    Returns:
        {segments: [{text, hidden}], hide_ratio: float}
    """
    import random

    chars = list(content)
    ratio = min(level * 0.2, 1.0)
    hide_count = max(1, int(len(chars) * ratio))

    # 按词/句分组隐藏，而非完全随机
    # 中文按字隐藏，标点和空格保留
    hide_indices = set()
    candidates = [i for i, c in enumerate(chars) if c.strip() and not _is_punctuation(c)]
    random.shuffle(candidates)
    hide_indices = set(candidates[:hide_count])

    segments = []
    current_text = ""
    current_hidden = False

    for i, ch in enumerate(chars):
        is_hidden = i in hide_indices
        if is_hidden == current_hidden:
            current_text += ch
        else:
            if current_text:
                segments.append({"text": current_text, "hidden": current_hidden})
            current_text = ch
            current_hidden = is_hidden

    if current_text:
        segments.append({"text": current_text, "hidden": current_hidden})

    return {
        "segments": segments,
        "hide_ratio": ratio,
        "total_chars": len(chars),
        "hidden_chars": len(hide_indices),
    }


def _is_punctuation(ch: str) -> bool:
    """判断是否为标点符号"""
    import unicodedata
    cat = unicodedata.category(ch)
    return cat.startswith('P') or ch in '，。、；：？！""''【】《》（）—…·\n\r\t'
