"""公开 API 契约测试。

集成方按下面这些名字调用分块引擎；本文件锁住名字与字段，避免重构时破坏调用方，
顺便冒烟验证 README/交付说明里给出的用法示例。
"""

from __future__ import annotations

from app.core.parsing.chunking import (
    STRATEGY_AUTO,
    STRATEGY_HEADING,
    STRATEGY_HEURISTIC,
    STRATEGY_LEGACY,
    STRATEGY_RECURSIVE,
    ChildChunk,
    Chunk,
    Diagnostics,
    ParentChildResult,
    SplitterConfig,
    approx_token_count,
    detect_language,
    normalize_line_endings,
    split,
    split_parent_child,
    split_with_diagnostics,
)


def test_integrator_snippet_works() -> None:
    """按交付说明里的调用方式跑一遍（集成方就是这样调用的）。"""
    cfg = SplitterConfig(
        chunk_size=64,
        chunk_overlap=8,
        separators=["\n\n", "\n", "。"],
        strategy=STRATEGY_AUTO,
        languages=["zh"],
        token_limit=0,
    )
    text = "".join(f"第{i}段：这是一段用于验证公开 API 的中文内容。\n\n" for i in range(40))

    chunks = split(text, cfg)
    assert len(chunks) > 1
    for c in chunks:
        assert isinstance(c, Chunk)
        # Chunk 字段：seq / content / start / end / context_header
        assert c.seq >= 0
        assert c.end - c.start == len(c.content)
        assert isinstance(c.context_header, str)
    # seq 应当是稠密的 0..N-1
    assert [c.seq for c in chunks] == list(range(len(chunks)))


def test_strategy_constants_have_expected_values() -> None:
    assert (STRATEGY_AUTO, STRATEGY_HEADING, STRATEGY_HEURISTIC, STRATEGY_RECURSIVE, STRATEGY_LEGACY) == (
        "auto",
        "heading",
        "heuristic",
        "recursive",
        "legacy",
    )


def test_split_with_diagnostics_returns_diagnostics_object() -> None:
    chunks, diag = split_with_diagnostics("# A\nbody\n## B\nbody\n## C\nbody", SplitterConfig(chunk_size=100, strategy=STRATEGY_AUTO))
    assert chunks
    assert isinstance(diag, Diagnostics)
    assert diag.selected_tier != ""


def test_split_parent_child_returns_parent_child_result() -> None:
    text = "# Chapter\n" + "正文句子。" * 60
    res = split_parent_child(
        text,
        SplitterConfig(chunk_size=200, chunk_overlap=20, strategy=STRATEGY_HEADING),
        SplitterConfig(chunk_size=60, chunk_overlap=5, strategy=STRATEGY_HEADING),
    )
    assert isinstance(res, ParentChildResult)
    assert res.children
    for c in res.children:
        assert isinstance(c, ChildChunk)
        assert c.parent_index == -1 or 0 <= c.parent_index < len(res.parents)


def test_pure_helpers_exposed() -> None:
    assert normalize_line_endings("a\r\nb") == "a\nb"
    assert detect_language("This is English text") == "en"
    assert approx_token_count("hello world", "en") > 0


def test_go_style_aliases_exist() -> None:
    import app.core.parsing.chunking as cc

    # Go 导出名别名，便于与上游逐行对照
    assert cc.Split is cc.split
    assert cc.SplitText is cc.split_text
    assert cc.StrategyAuto == STRATEGY_AUTO
    assert cc.LangChinese == "zh"
    assert cc.PrioFormFeed == 100
    assert cc.DefaultChunkSize == 512
    assert cc.DefaultChunkOverlap == 80

    cfg = SplitterConfig(chunk_size=10)
    assert cfg.ChunkSize == 10
    assert cfg.ChunkOverlap == 0
    assert cfg.Separators == []

    c = Chunk(content="x", context_header="## H", seq=3, start=5, end=6)
    assert c.Content == "x"
    assert c.ContextHeader == "## H"
    assert c.Seq == 3 and c.Start == 5 and c.End == 6
    assert c.EmbeddingContent() == "## H\n\nx"


def test_default_config_helper_matches_go_defaults() -> None:
    from app.core.parsing.chunking import default_config

    cfg = default_config()
    assert (cfg.chunk_size, cfg.chunk_overlap, cfg.separators) == (512, 80, ["\n\n", "\n", "。"])
