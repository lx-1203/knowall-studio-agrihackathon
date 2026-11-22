"""heading_hierarchy_test.go 的逐条翻译。"""

from __future__ import annotations

from app.core.parsing.chunking import HeadingHierarchy, new_heading_hierarchy


def test_heading_hierarchy_linear_nesting() -> None:
    h = new_heading_hierarchy()
    h.observe("# Chapter 1")
    h.observe("## Section 1.1")
    h.observe("### Subsection 1.1.1")
    assert h.breadcrumb() == "Chapter 1 > Section 1.1 > Subsection 1.1.1"


def test_heading_hierarchy_pops_deeper_on_sibling_heading() -> None:
    h = new_heading_hierarchy()
    h.observe("# Chapter 1")
    h.observe("## Section 1.1")
    h.observe("### Subsection 1.1.1")
    h.observe("## Section 1.2")  # 弹出 H3
    assert h.breadcrumb() == "Chapter 1 > Section 1.2"


def test_heading_hierarchy_pops_all_on_new_top_level() -> None:
    h = new_heading_hierarchy()
    h.observe("# Chapter 1")
    h.observe("## Section A")
    h.observe("### Sub")
    h.observe("# Chapter 2")
    assert h.breadcrumb() == "Chapter 2"
    assert h.depth() == 1


def test_heading_hierarchy_non_heading_ignored() -> None:
    h = new_heading_hierarchy()
    h.observe("# Title")
    level, txt = h.observe("just a paragraph")
    assert level == 0
    assert txt == ""
    assert h.breadcrumb() == "Title"


def test_heading_hierarchy_breadcrumb_with_hashes() -> None:
    h = new_heading_hierarchy()
    h.observe("# A")
    h.observe("## B")
    assert h.breadcrumb_with_hashes() == "# A\n## B"


def test_heading_hierarchy_empty_state() -> None:
    h = new_heading_hierarchy()
    assert h.breadcrumb() == ""
    assert h.breadcrumb_with_hashes() == ""
    assert h.depth() == 0


def test_heading_hierarchy_reset() -> None:
    h = new_heading_hierarchy()
    h.observe("# A")
    h.observe("## B")
    h.reset()
    assert h.breadcrumb() == ""
    assert h.depth() == 0


def test_heading_hierarchy_skip_levels() -> None:
    h = new_heading_hierarchy()
    # 文档从 H1 直接跳到 H3，跳过 H2
    h.observe("# Top")
    h.observe("### Deep")
    assert h.breadcrumb() == "Top > Deep"


def test_heading_hierarchy_copy_is_independent() -> None:
    # Go 里 sectionStart := *hierarchy 是结构体值拷贝，Python 用 copy()
    h = HeadingHierarchy()
    h.observe("# A")
    clone = h.copy()
    h.observe("## B")
    assert clone.breadcrumb() == "A"
    assert h.breadcrumb() == "A > B"
