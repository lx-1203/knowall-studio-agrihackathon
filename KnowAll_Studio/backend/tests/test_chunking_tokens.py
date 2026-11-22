"""tokens_test.go 的逐条翻译。"""

from __future__ import annotations

from app.core.parsing.chunking import (
    LANG_CHINESE,
    LANG_ENGLISH,
    LANG_GERMAN,
    LANG_MIXED,
    approx_token_count,
    chars_for_token_limit,
    detect_language,
)


def test_approx_token_count_english() -> None:
    got = approx_token_count("The quick brown fox jumps over the lazy dog.", LANG_ENGLISH)
    # 44 字符 / 4 ≈ 11 token
    assert 9 <= got <= 13


def test_approx_token_count_chinese() -> None:
    got = approx_token_count("这是一段中文测试内容用于检验分词估算", LANG_CHINESE)
    # 18 rune / 1.7 ≈ 10
    assert 9 <= got <= 12


def test_approx_token_count_empty() -> None:
    assert approx_token_count("", LANG_ENGLISH) == 0


def test_approx_token_count_unknown_lang() -> None:
    got = approx_token_count("Hello world hello world", "xx")
    assert got > 0


def test_detect_language_english() -> None:
    assert detect_language("The quick brown fox jumps over the lazy dog.") == LANG_ENGLISH


def test_detect_language_german() -> None:
    assert detect_language("Der schnelle braune Fuchs springt über den faulen Hund.") == LANG_GERMAN


def test_detect_language_german_by_stopwords() -> None:
    # 没有变音符，但德语虚词充足。
    assert detect_language("Das ist ein Test und nicht mit Umlauten.") == LANG_GERMAN


def test_detect_language_chinese() -> None:
    assert detect_language("这是一段中文测试内容") == LANG_CHINESE


def test_detect_language_mixed() -> None:
    assert detect_language("This 这是 mixed 测试 content with 多语言 inside") == LANG_MIXED


def test_chars_for_token_limit_applies_safety_margin() -> None:
    got = chars_for_token_limit(1000, LANG_ENGLISH)
    # 1000 * 4 * 0.9 = 3600
    assert 3500 <= got <= 3700


def test_chars_for_token_limit_zero_tokens() -> None:
    assert chars_for_token_limit(0, LANG_ENGLISH) == 0
