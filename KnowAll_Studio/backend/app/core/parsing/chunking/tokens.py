"""语言探测与近似 token 估算。

刻意不引入分词器依赖（如 tiktoken）：改用按语言的经验字符/token 比例，
数值来自常见 embedding 模型的词表。数值偏保守 —— 宁可略微高估 token
数，也不要让 chunk 越过模型上限。
"""

from __future__ import annotations

import string

from .types import LANG_CHINESE, LANG_ENGLISH, LANG_GERMAN, LANG_MIXED

# 近似 字符/token 比例，误差方向是"高估 token 数"。
CHARS_PER_TOKEN: dict[str, float] = {
    LANG_ENGLISH: 4.0,
    LANG_GERMAN: 4.5,
    LANG_CHINESE: 1.7,
    LANG_MIXED: 3.0,
}

# Go 的 containsLower 只做 ASCII 大小写折叠，这里保持同样的语义。
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)

# Go 的 unicode.Is(Han/Hangul/Hiragana/Katakana) 在 Python 标准库里没有
# 等价物（unicodedata 不暴露 script 属性），因此用区间近似。文档分块只
# 需要"这段文字是不是 CJK"这个粗判，落在区间外的生僻扩展区影响可忽略。
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x2E80, 0x2EFF),    # CJK 部首补充
    (0x3005, 0x3007),
    (0x3021, 0x3029),
    (0x3038, 0x303B),
    (0x3400, 0x4DBF),    # CJK 扩展 A
    (0x4E00, 0x9FFF),    # CJK 基本区
    (0xF900, 0xFAFF),    # CJK 兼容表意
    (0x1100, 0x11FF),    # 谚文字母
    (0x3130, 0x318F),    # 谚文兼容字母
    (0xA960, 0xA97F),
    (0xAC00, 0xD7FF),    # 谚文音节
    (0x3041, 0x3096),    # 平假名
    (0x309D, 0x309F),
    (0x30A1, 0x30FA),    # 片假名
    (0x30FD, 0x30FF),
    (0x31F0, 0x31FF),
    (0xFF66, 0xFF9D),
    (0x20000, 0x2EBEF),  # CJK 扩展 B~F
    (0x30000, 0x323AF),
)


def is_cjk(r: str) -> bool:
    cp = ord(r)
    for lo, hi in _CJK_RANGES:
        if lo <= cp <= hi:
            return True
    return False


def is_german_umlaut(r: str) -> bool:
    return r in "äöüÄÖÜß"


def approx_token_count(s: str, lang: str) -> int:
    """给定语言的保守 token 估算。空串或未知语言回落到 mixed。"""
    if s == "":
        return 0
    # Go: utf8.RuneCountInString(s) == Python 的 len(str)
    return approx_token_count_from_rune_len(len(s), lang)


def approx_token_count_from_rune_len(rune_len: int, lang: str) -> int:
    """调用方已经知道 rune 数时的免分配版本（热点循环里避免重复计数）。"""
    if rune_len <= 0:
        return 0
    ratio = CHARS_PER_TOKEN.get(lang, CHARS_PER_TOKEN[LANG_MIXED])
    approx = rune_len / ratio
    if approx < 1:
        return 1
    return int(approx + 0.5)


def detect_language(s: str) -> str:
    """按 CJK 与拉丁字符的比例做粗粒度语言判定。

    只服务于启发式分派，不是真正的语言识别。
    """
    if s == "":
        return LANG_MIXED
    cjk = latin = umlaut = 0
    for r in s:
        if is_cjk(r):
            cjk += 1
        elif is_german_umlaut(r):
            umlaut += 1
            latin += 1
        elif ("a" <= r <= "z") or ("A" <= r <= "Z"):
            latin += 1
    total = cjk + latin
    if total == 0:
        return LANG_MIXED
    cjk_ratio = cjk / total
    latin_ratio = latin / total
    # 两种文字都有显著占比（各 >= 15%）视为混合。
    if cjk_ratio >= 0.15 and latin_ratio >= 0.15:
        return LANG_MIXED
    if cjk_ratio > 0.3:
        return LANG_CHINESE
    if umlaut > 0 or has_german_words(s):
        return LANG_GERMAN
    return LANG_ENGLISH


def has_german_words(s: str) -> bool:
    """德语虚词的小样本检查，用来在无变音符时偏向 de。

    廉价启发式：对借用词偶有误判是可以接受的。
    """
    # Go 按字节截断（len(s) > 512），这里保持字节语义以免样本范围不同。
    if len(s.encode("utf-8")) > 512:
        s = s.encode("utf-8")[:512].decode("utf-8", errors="ignore")
    for w in (" der ", " die ", " das ", " und ", " ist ", " nicht ", " mit ", " auf "):
        if contains_lower(s, w):
            return True
    return False


def contains_lower(haystack: str, needle: str) -> bool:
    """ASCII-only 大小写不敏感子串查找（对应 Go 的逐字节比较）。"""
    return needle in haystack.translate(_ASCII_LOWER)


def chars_for_token_limit(tokens: int, lang: str) -> int:
    """把 token 上限换算成近似字符预算，用于给 chunk 定尺寸。

    乘 0.9 安全系数，宁可低于模型上限也不要超出。
    """
    if tokens <= 0:
        return 0
    ratio = CHARS_PER_TOKEN.get(lang, CHARS_PER_TOKEN[LANG_MIXED])
    return int(tokens * ratio * 0.9)


# Go 导出名别名
ApproxTokenCount = approx_token_count
ApproxTokenCountFromRuneLen = approx_token_count_from_rune_len
DetectLanguage = detect_language
CharsForTokenLimit = chars_for_token_limit
