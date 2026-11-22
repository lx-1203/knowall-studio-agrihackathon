"""strategy_test.go / strategy_token_test.go / strategy_diagnostics_test.go 的逐条翻译。

（strategy_test.go 里 ValidateChunks 的 5 条已放在 test_chunking_validator.py。）
"""

from __future__ import annotations

import pytest

from app.core.parsing.chunking import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    LANG_CHINESE,
    LANG_ENGLISH,
    STRATEGY_AUTO,
    STRATEGY_HEADING,
    STRATEGY_HEURISTIC,
    STRATEGY_LEGACY,
    STRATEGY_RECURSIVE,
    TIER_HEADING,
    TIER_LEGACY,
    SplitterConfig,
    chars_for_token_limit,
    default_config,
    derive_parent_child_configs,
    ensure_defaults,
    merge_breadcrumbs,
    normalize_line_endings,
    normalize_splitter_config,
    split,
    split_parent_child,
    split_parent_child_with_diagnostics,
    split_text,
    split_with_diagnostics,
    validate_chunks,
)


def test_unbroken_chinese_text_obeys_default_embedding_budget() -> None:
    text = "中" * 7000
    cfg = SplitterConfig(
        chunk_size=3060, chunk_overlap=100, token_limit=2000,
        languages=[LANG_CHINESE], strategy=STRATEGY_AUTO,
    )

    chunks, diagnostics = split_with_diagnostics(text, cfg)

    assert len(chunks) > 1
    assert all(len(chunk.content) <= 3060 for chunk in chunks)
    assert "".join(chunk.content for chunk in chunks) == text
    assert all(chunk.content == text[chunk.start:chunk.end] for chunk in chunks)
    assert validate_chunks(chunks, len(text), 3060).ok
    assert diagnostics.selected_tier


def test_split_empty_text() -> None:
    assert split("", default_config()) == []


def test_split_legacy_strategy_matches_split_text() -> None:
    text = "Hello world.\n\n" * 30
    cfg = SplitterConfig(
        chunk_size=100, chunk_overlap=20, separators=["\n\n"], strategy=STRATEGY_LEGACY
    )
    a = split(text, cfg)
    b = split_text(text, cfg)
    assert len(a) == len(b)
    for i in range(len(a)):
        assert a[i].content == b[i].content


def test_split_empty_strategy_equals_legacy() -> None:
    text = "Sentence one. Sentence two.\n" * 20
    cfg = SplitterConfig(chunk_size=80, chunk_overlap=10)
    a = split(text, cfg)
    cfg = SplitterConfig(chunk_size=80, chunk_overlap=10, strategy=STRATEGY_LEGACY)
    b = split(text, cfg)
    assert len(a) == len(b)


def test_split_auto_strategy_picks_heading_for_markdown_doc() -> None:
    doc = "# A\nbody\n## B\nbody\n## C\nbody\n## D\nbody\n"
    cfg = SplitterConfig(chunk_size=200, chunk_overlap=20, strategy=STRATEGY_AUTO)
    chunks = split(doc, cfg)
    assert len(chunks) > 0


def test_split_heading_strategy_keeps_distinct_top_level_headings() -> None:
    doc = """# Intro
short intro.

# Usage
short usage.

# FAQ
short faq."""
    cfg = SplitterConfig(chunk_size=500, chunk_overlap=0, strategy=STRATEGY_HEADING)
    chunks = split(doc, cfg)
    assert len(chunks) == 3
    for i, heading in enumerate(["# Intro", "# Usage", "# FAQ"]):
        assert heading in chunks[i].content


@pytest.mark.parametrize(
    "name,doc",
    [
        (
            "heading-tier",
            "# Top\nintro paragraph here.\n\n## Section A\nbody A here.\n\n## Section B\nbody B here.\n\n## Section C\nbody C.",
        ),
        (
            "heuristic-tier",
            "Kapitel 1: Einleitung\n"
            + "Beispieltext. " * 50
            + "\n\n"
            + "Kapitel 2: Hauptteil\n"
            + "Mehr Text. " * 50,
        ),
        ("recursive-tier", "plain prose without structure. " * 100),
    ],
    ids=["heading-tier", "heuristic-tier", "recursive-tier"],
)
def test_split_preserves_position_invariant_across_tiers(name: str, doc: str) -> None:
    cfg = SplitterConfig(
        chunk_size=300, chunk_overlap=30, separators=["\n\n", "\n", "。", ". "], strategy=STRATEGY_AUTO
    )
    runes = list(doc)
    chunks = split(doc, cfg)
    assert chunks
    for i, c in enumerate(chunks):
        assert c.end - c.start == len(c.content), f"chunk {i}: span != content runes"
        assert c.start >= 0 and c.end <= len(runes)
        assert "".join(runes[c.start : c.end]) == c.content


def test_split_parent_child_auto_strategy_enriches_child_breadcrumbs() -> None:
    body = "Lorem ipsum dolor sit amet. " * 40
    doc = (
        "# Chapter\n" + body
        + "\n\n## Section A\n" + body
        + "\n\n## Section B\n" + body
        + "\n\n## Section C\n" + body
    )
    seps = ["\n\n", "\n", ". "]
    parent_cfg = SplitterConfig(
        chunk_size=800, chunk_overlap=80, strategy=STRATEGY_AUTO, separators=seps
    )
    child_cfg = SplitterConfig(
        chunk_size=200, chunk_overlap=20, strategy=STRATEGY_AUTO, separators=seps
    )
    res = split_parent_child(doc, parent_cfg, child_cfg)
    assert res.children

    # 至少有一个子块的面包屑包含子标题（## Section …）—— 只有子块切分器在
    # 父块内容里重新发现了标题才可能。
    assert any("## Section" in c.context_header for c in res.children), (
        f"samples: {res.children[0].context_header!r} / {res.children[-1].context_header!r}"
    )

    # 面包屑里不该出现连续重复行（mergeBreadcrumbs 去掉了父子接缝处的重复）。
    for i, c in enumerate(res.children):
        lines = c.context_header.split("\n")
        for j in range(1, len(lines)):
            if lines[j].strip() != "" and lines[j] == lines[j - 1]:
                pytest.fail(f"child[{i}] has duplicate breadcrumb lines: {c.context_header!r}")


@pytest.mark.parametrize(
    "parent,child,expected",
    [
        ("", "## Sub", "## Sub"),
        ("# Top", "", "# Top"),
        ("", "", ""),
        ("# Top", "## Other", "# Top\n## Other"),
        ("# Top\n## A", "## A\n### A1", "# Top\n## A\n### A1"),
        ("# Top", "# Top", "# Top"),
        ("# Top\n## A", "  ## A  \n### A1", "# Top\n## A\n### A1"),
    ],
)
def test_merge_breadcrumbs(parent: str, child: str, expected: str) -> None:
    assert merge_breadcrumbs(parent, child) == expected


def test_split_parent_child_legacy_strategy() -> None:
    text = "This is a sentence. Another one.\n\n" * 50
    parent_cfg = SplitterConfig(chunk_size=400, chunk_overlap=40, strategy=STRATEGY_LEGACY)
    child_cfg = SplitterConfig(chunk_size=100, chunk_overlap=20, strategy=STRATEGY_LEGACY)
    res = split_parent_child(text, parent_cfg, child_cfg)
    assert res.children
    for i, c in enumerate(res.children):
        if c.parent_index >= 0:
            assert c.parent_index < len(res.parents), f"child[{i}] has invalid ParentIndex"


def test_ensure_defaults() -> None:
    cfg = ensure_defaults(SplitterConfig())
    assert cfg.chunk_size == DEFAULT_CHUNK_SIZE
    assert cfg.chunk_overlap == DEFAULT_CHUNK_OVERLAP
    assert len(cfg.separators) > 0


def test_ensure_defaults_does_not_mutate_caller_config() -> None:
    # Go 的 SplitterConfig 是值语义：传入的 struct 不会被改写。
    cfg = SplitterConfig()
    ensure_defaults(cfg)
    assert cfg.chunk_size == 0
    assert cfg.separators == []


def test_normalize_splitter_config_matches_ingestion_defaults() -> None:
    cfg = normalize_splitter_config(SplitterConfig())
    assert cfg.chunk_size == DEFAULT_CHUNK_SIZE
    assert cfg.chunk_overlap == DEFAULT_CHUNK_OVERLAP
    assert len(cfg.separators) == 3


def test_normalize_line_endings() -> None:
    assert normalize_line_endings("first\r\nsecond\rthird\nfourth") == "first\nsecond\nthird\nfourth"


def test_derive_parent_child_configs_default_sizes() -> None:
    base = SplitterConfig(
        chunk_size=1000,
        chunk_overlap=100,
        separators=["\n\n", "\n"],
        strategy=STRATEGY_HEADING,
    )
    parent, child = derive_parent_child_configs(base, 0, 0)
    assert parent.chunk_size == 4096 and child.chunk_size == 384
    assert parent.strategy == STRATEGY_HEADING and child.strategy == STRATEGY_HEADING
    assert child.chunk_overlap == 384 // 5


def test_derive_parent_child_configs_applies_embedding_token_limit_only_to_children() -> None:
    parent_size, child_size, token_limit = 4096, 1024, 200
    base = SplitterConfig(
        chunk_size=DEFAULT_CHUNK_SIZE,
        chunk_overlap=DEFAULT_CHUNK_OVERLAP,
        separators=["。"],
        strategy=STRATEGY_LEGACY,
        token_limit=token_limit,
        languages=[LANG_CHINESE],
    )

    parent, child = derive_parent_child_configs(base, parent_size, child_size)
    assert parent.token_limit == 0
    assert parent.languages == [LANG_CHINESE]
    assert child.token_limit == token_limit
    assert child.languages == [LANG_CHINESE]

    text = "这是用于验证中文分块预算的句子。" * 100
    result = split_parent_child(text, parent, child)
    assert len(result.parents) == 1
    assert len(result.parents[0].content) == len(text)
    budget = chars_for_token_limit(token_limit, LANG_CHINESE)
    for i, c in enumerate(result.children):
        assert len(c.content) <= budget, f"child[{i}] length exceeds budget"


# --- strategy_token_test.go ------------------------------------------------


def test_ensure_defaults_token_limit_clamps_chunk_size() -> None:
    cfg = SplitterConfig(chunk_size=10000, token_limit=100, languages=[LANG_ENGLISH])
    out = ensure_defaults(cfg)
    # 100 token * 4 字符/token * 0.9 ≈ 360 字符
    assert out.chunk_size < 1000
    assert out.chunk_overlap < out.chunk_size


def test_ensure_defaults_token_limit_chinese_tighter() -> None:
    cfg_en = ensure_defaults(SplitterConfig(token_limit=200, languages=[LANG_ENGLISH]))
    cfg_zh = ensure_defaults(SplitterConfig(token_limit=200, languages=[LANG_CHINESE]))
    assert cfg_zh.chunk_size < cfg_en.chunk_size


def test_ensure_defaults_no_token_limit_keeps_chunk_size() -> None:
    out = ensure_defaults(SplitterConfig(chunk_size=800))
    assert out.chunk_size == 800


# --- strategy_diagnostics_test.go ------------------------------------------


def test_split_with_diagnostics_legacy_strategy_reports_legacy_tier() -> None:
    text = "Hello world.\n\nNext paragraph here.\n\n" * 50
    cfg = SplitterConfig(
        chunk_size=200,
        chunk_overlap=20,
        separators=["\n\n", "\n"],
        strategy=STRATEGY_LEGACY,
    )
    chunks, diag = split_with_diagnostics(text, cfg)
    assert len(chunks) > 0
    assert diag.selected_tier == TIER_LEGACY
    assert len(diag.tier_chain) == 1 and diag.tier_chain[0] == TIER_LEGACY
    assert diag.rejected == []


def test_split_with_diagnostics_auto_on_heading_doc_picks_heading() -> None:
    doc = "# Top\nintro paragraph here.\n\n## Section A\nbody A here.\n\n## Section B\nbody B here.\n\n## Section C\nbody C here.\n\n"
    cfg = SplitterConfig(chunk_size=300, chunk_overlap=30, strategy=STRATEGY_AUTO)
    _, diag = split_with_diagnostics(doc, cfg)
    assert len(diag.tier_chain) > 0
    assert diag.tier_chain[0] == TIER_HEADING


def test_split_with_diagnostics_empty_text() -> None:
    chunks, diag = split_with_diagnostics("", default_config())
    assert chunks == []
    assert diag is not None


def test_split_and_diagnostics_agree_on_chunks() -> None:
    text = "para one.\n\npara two.\n\npara three."
    cfg = SplitterConfig(chunk_size=100, chunk_overlap=10)
    a = split(text, cfg)
    b, diag = split_with_diagnostics(text, cfg)
    assert len(a) == len(b)
    for i in range(len(a)):
        assert (a[i].content, a[i].start, a[i].end) == (b[i].content, b[i].start, b[i].end)
    assert diag is not None


def test_split_with_diagnostics_profile_set_for_auto() -> None:
    doc = "# Top\nintro.\n\n## A\nbody A.\n\n## B\nbody B."
    _, diag = split_with_diagnostics(doc, SplitterConfig(chunk_size=200, strategy=STRATEGY_AUTO))
    assert diag.profile is not None
    assert diag.profile.md_heading_total > 0


@pytest.mark.parametrize(
    "strategy", [STRATEGY_HEADING, STRATEGY_HEURISTIC, STRATEGY_RECURSIVE, STRATEGY_LEGACY]
)
def test_split_with_diagnostics_profile_nil_for_explicit(strategy: str) -> None:
    _, diag = split_with_diagnostics("plain text", SplitterConfig(chunk_size=200, strategy=strategy))
    assert diag.profile is None


def test_split_parent_child_with_diagnostics_matches_split_parent_child() -> None:
    text = ("## Record\n" + "A sufficiently long entry body. " * 10 + "\n\n") * 12
    parent_cfg = SplitterConfig(
        chunk_size=300, chunk_overlap=30, separators=["\n\n", "\n"], strategy=STRATEGY_HEADING
    )
    child_cfg = SplitterConfig(
        chunk_size=100, chunk_overlap=20, separators=["\n\n", "\n"], strategy=STRATEGY_HEADING
    )

    want = split_parent_child(text, parent_cfg, child_cfg)
    got, diag = split_parent_child_with_diagnostics(text, parent_cfg, child_cfg)

    assert len(got.parents) == len(want.parents)
    assert len(got.children) == len(want.children)
    for i in range(len(want.children)):
        assert got.children[i] == want.children[i], f"child {i} differs"
    assert diag is not None and diag.selected_tier != ""
