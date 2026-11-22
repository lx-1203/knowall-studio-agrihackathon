r"""多语言正则与边界优先级常量 —— 全包唯一的模式来源。

按用途分组（章节标记 / 编号 / 分隔线），并给每个模式标一个优先级，
供启发式分块器对候选边界排序。所有模式等价于 Go 的 regexp（RE2）：
- Go 的 \w \d \s 都是 ASCII 语义，Python 默认是 Unicode 语义，
  所以统一加 re.ASCII，否则中文文档里的 \d 会匹配全角数字。
- Go 里没有 (?m) 时 $ 只匹配文本末尾，而 Python 的 $ 还会匹配末尾换行符
  之前的位置，因此非多行模式的 $ 一律写成 \Z。
"""

from __future__ import annotations

import re

from .types import (  # noqa: F401  (re-export，供上层按 Go 文件名对照)
    PRIO_ALL_CAPS_HEADING,
    PRIO_BLANK_BLOCK,
    PRIO_CHAPTER_MARKER,
    PRIO_FORM_FEED,
    PRIO_NUMBERED_HEAD,
    PRIO_PAGE_FOOTER,
    PRIO_VISUAL_SEP,
    LANG_CHINESE,
    LANG_ENGLISH,
    LANG_GERMAN,
)

_FLAGS_M = re.M | re.ASCII
_FLAGS_MI = re.M | re.I | re.ASCII
_FLAGS_S = re.S | re.ASCII

# markdown ATX 标题，捕获组：(1) 井号，(2) 标题文本。
# 要求整行匹配，行内缩进的 "#" 不算标题。
MarkdownHeadingPattern = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", _FLAGS_M)

# PDF 转换器常用作分页标记的换页符。
FormFeedPattern = re.compile(r"\f")

# 数字/罗马数字编号的章节行，例如 "1. Intro" / "2.3 Methods" / "IV. Results"。
# 多级编号的结尾点可选，因为很多技术文档写 "1.1 Foo" 而不带收尾的点。
NumberedSectionPattern = re.compile(
    r"^[ \t]*(?:\d+(?:\.\d+){1,3}\.?|(?:\d+|[IVX]{1,5})\.)[ \t]+\S.{0,200}$", _FLAGS_M
)

# 短的全大写行（未使用 Markdown 标题的章节名）。要求至少 4 个字母、
# 至多约 10 个单词，容忍行尾冒号。
AllCapsHeadingPattern = re.compile(r"^[ \t]*([A-ZÄÖÜ][A-ZÄÖÜ \-]{3,80}):?\s*$", _FLAGS_M)

# 纯文本/非 Markdown 文档里的水平分隔线。
VisualSeparatorPattern = re.compile(r"^[ \t]*(?:-{3,}|={3,}|\*{3,}|_{3,})[ \t]*$", _FLAGS_M)

# 连续 3 个以上换行，通常意味着硬分节。
ExcessiveBlanksPattern = re.compile(r"\n{3,}")

# 典型的 "Seite X von Y" / "Page X of Y" 页脚。
PageFooterPattern = re.compile(
    r"^[ \t]*(?:Seite|Page|页码?)\s+\d+(?:\s*(?:von|of|/)\s*\d+)?[ \t]*$", _FLAGS_MI
)

# 德/英/中三种语言的章节标记。
GermanChapterPattern = re.compile(
    r"^[ \t]*(?:Kapitel|Abschnitt|Teil)\s+(?:[0-9]+|[IVX]{1,5})[\.: ].{0,200}$", _FLAGS_M
)
EnglishChapterPattern = re.compile(
    r"^[ \t]*(?:Chapter|Section|Part)\s+(?:[0-9]+|[IVX]{1,5})[\.: ].{0,200}$", _FLAGS_M
)
ChineseChapterPattern = re.compile(
    r"^[ \t]*第[ \t]*[一二三四五六七八九十百千零〇0-9]+[ \t]*(?:章|节|節|部分|篇)[ \t]?.{0,200}$",
    _FLAGS_M,
)


def sentence_separators(lang: str) -> list[str]:
    """按语言返回句级分隔符，用于小节仍然过大时的细粒度切分。"""
    if lang == LANG_CHINESE:
        return ["。", "！", "？", "；", "\n"]
    if lang in (LANG_GERMAN, LANG_ENGLISH):
        return [". ", "! ", "? ", "; ", "\n"]
    return ["。", "！", "？", "；", ". ", "! ", "? ", "; ", "\n"]


def chapter_patterns_for_langs(langs: list[str] | None) -> list[re.Pattern[str]]:
    """返回给定语言提示下适用的章节标记正则。

    空/未知列表返回全部模式，这样自动探测的文档依然能命中。
    """
    if not langs:
        return [GermanChapterPattern, EnglishChapterPattern, ChineseChapterPattern]
    out: list[re.Pattern[str]] = []
    for lang in langs:
        if lang == LANG_GERMAN:
            out.append(GermanChapterPattern)
        elif lang == LANG_ENGLISH:
            out.append(EnglishChapterPattern)
        elif lang == LANG_CHINESE:
            out.append(ChineseChapterPattern)
    if not out:
        out = [GermanChapterPattern, EnglishChapterPattern, ChineseChapterPattern]
    return out


# Go 导出名别名
SentenceSeparators = sentence_separators
ChapterPatternsForLangs = chapter_patterns_for_langs
