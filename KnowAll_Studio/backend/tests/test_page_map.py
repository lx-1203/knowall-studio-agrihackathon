"""page_map 单测：引用溯源的页码映射。

重点覆盖的是"容错"而不是"顺利路径"：清洗改过正文、页眉被删、同一页多条目、
条目定位失败 —— 这些才是真实文档里天天发生的情况。
"""
from __future__ import annotations

from app.core.parsing.page_map import (
    PAGE_UNKNOWN,
    PageMap,
    PageSpan,
    build_page_map,
    build_page_map_from_parts,
)


def test_empty_text_yields_empty_map():
    assert not build_page_map("", [("任意条目文本内容", 1)])
    assert len(build_page_map("", [])) == 0


def test_no_usable_items_yields_empty_map():
    # 页码未知的条目不产生锚点
    assert not build_page_map("正文内容足够长可以匹配", [("正文内容足够长可以匹配", 0)])
    # 条目太短，宁可不锚
    assert not build_page_map("正文内容足够长可以匹配", [("正文", 1)])


def test_page_at_and_range_formatting():
    spans = [PageSpan(0, 100, 3), PageSpan(100, 200, 5)]
    pm = PageMap(spans)
    assert pm.page_at(0) == 3
    assert pm.page_at(99) == 3
    assert pm.page_at(100) == 5
    assert pm.page_range(0, 50) == "p3"
    assert pm.page_range(0, 150) == "p3-p5"
    # 空区间按单字符处理，不应该炸
    assert pm.page_range(10, 10) == "p3"


def test_page_range_skips_unknown_endpoint():
    pm = PageMap([PageSpan(50, 100, 7)])
    # 起点在第一个锚点之前 → 退回用终点页码，而不是返回空
    assert pm.page_range(0, 60) == "p7"


def test_unknown_before_first_anchor_on_raw_map_is_unknown():
    # 直接构造、不经过 build_page_map 时，首锚点之前就是未知
    pm = PageMap([PageSpan(50, 100, 4)])
    assert pm.page_at(0) == PAGE_UNKNOWN
    assert pm.page_range(0, 10) == ""


def test_basic_alignment_across_pages():
    text = "第一章 绪论 这是第一页的正文内容\n\n第二章 方法 这是第二页的正文内容"
    items = [
        ("第一章 绪论 这是第一页的正文内容", 1),
        ("第二章 方法 这是第二页的正文内容", 2),
    ]
    pm = build_page_map(text, items)
    assert pm.page_at(0) == 1
    second = text.index("第二章")
    assert pm.page_at(second) == 2
    assert pm.page_range(0, second) == "p1"
    assert pm.page_range(0, len(text)) == "p1-p2"


def test_whitespace_differences_are_tolerated():
    # 条目文本与正文的空白不一致是常态（导出、清洗、折行都会改）
    text = "标题   内容紧跟着\n\n  多   空格   也被压缩"
    items = [("标题 内容紧跟着", 1), ("多 空格 也被压缩", 2)]
    pm = build_page_map(text, items)
    assert pm.page_at(0) == 1
    assert pm.page_at(text.index("多")) == 2


def test_leading_markdown_decoration_is_ignored():
    text = "正文里的标题不带井号\n\n下一段文字内容"
    items = [("## 正文里的标题不带井号", 1), ("- 下一段文字内容", 2)]
    pm = build_page_map(text, items)
    assert pm.page_at(0) == 1
    assert pm.page_at(text.index("下一段")) == 2


def test_stripped_first_line_still_anchors_on_second_line():
    # 条目首行是页眉/页码，正好会被清洗器删掉。只用整条前缀匹配会全军覆没，
    # 去掉首行的候选键必须把它救回来。
    text = "这是第二页正文的开头 后面还有内容\n\n第三页的内容在这里"
    items = [
        ("第 12 页\n这是第二页正文的开头 后面还有内容", 2),
        ("第 13 页\n第三页的内容在这里", 3),
    ]
    pm = build_page_map(text, items)
    assert pm.page_at(0) == 2
    assert pm.page_at(text.index("第三页")) == 3


def test_unlocatable_item_does_not_shift_later_anchors():
    # 中间有一条第正文里根本没有的条目（例如被清洗整段删掉的广告），
    # 后面的锚点必须继续落在正确位置 —— 这是单调游标的核心价值。
    text = "第一段正文内容在这里\n\n第二段正文内容在这里"
    items = [
        ("第一段正文内容在这里", 1),
        ("这条内容在清洗后被整段删掉了不存在于正文", 5),
        ("第二段正文内容在这里", 2),
    ]
    pm = build_page_map(text, items)
    assert pm.page_at(0) == 1
    assert pm.page_at(text.index("第二段")) == 2


def test_same_page_items_collapse_to_one_anchor():
    text = "同页第一段文字\n\n同页第二段文字\n\n下一页的文字内容"
    items = [
        ("同页第一段文字", 1),
        ("同页第二段文字", 1),
        ("下一页的文字内容", 2),
    ]
    pm = build_page_map(text, items)
    # 同页只留一个锚点，区间数 = 页数
    assert len(pm) == 2
    assert pm.page_at(text.index("同页第二段")) == 1
    assert pm.page_at(text.index("下一页")) == 2


def test_long_item_truncated_but_still_anchors():
    body = "很长的段落内容" * 60
    text = body + "\n\n结尾"
    pm = build_page_map(text, [(body, 9)])
    assert pm.page_at(0) == 9


def test_from_parts_offsets_are_exact():
    parts = [("第一页内容", 1), ("第二页内容", 2), ("第三页内容", 3)]
    pm = build_page_map_from_parts(parts, joiner="\n\n")
    joined = "\n\n".join(p for p, _ in parts)
    assert joined.index("第二页") == len("第一页内容") + 2
    assert pm.page_at(0) == 1
    assert pm.page_at(joined.index("第二页")) == 2
    assert pm.page_at(joined.index("第三页")) == 3
    assert pm.page_range(0, len(joined)) == "p1-p3"


def test_from_parts_skips_blank_parts():
    parts = [("有内容的一页", 1), ("   ", 2), ("第三页内容", 3)]
    pm = build_page_map_from_parts(parts)
    joined = "\n\n".join(p for p, _ in parts)
    # 空白页不产生锚点，第三页的偏移仍然精确
    assert pm.page_at(joined.index("第三页")) == 3
    assert pm.page_range(joined.index("第三页"), len(joined)) == "p3"
