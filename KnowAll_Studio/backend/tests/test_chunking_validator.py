"""strategy_test.go 中 ValidateChunks 相关用例的逐条翻译。"""

from __future__ import annotations

from app.core.parsing.chunking import Chunk, validate_chunks


def test_validate_chunks_empty() -> None:
    assert validate_chunks(None, 1000, 500).ok is False


def test_validate_chunks_single_chunk_large_doc() -> None:
    chunks = [Chunk(content="a" * 5000)]
    assert validate_chunks(chunks, 5000, 500).ok is False


def test_validate_chunks_accepts_reasonable_output() -> None:
    chunks = [
        Chunk(content="a" * 480),
        Chunk(content="b" * 510),
        Chunk(content="c" * 460),
    ]
    assert validate_chunks(chunks, 1500, 512).ok is True


def test_validate_chunks_rejects_oversized() -> None:
    chunks = [
        Chunk(content="a" * 100),
        Chunk(content="b" * 5000),  # > 2x chunkSize
    ]
    assert validate_chunks(chunks, 5100, 1000).ok is False


def test_validate_chunks_tolerant_tiny_tail() -> None:
    chunks = [
        Chunk(content="a" * 480),
        Chunk(content="b" * 510),
        Chunk(content="tail"),
    ]
    assert validate_chunks(chunks, 994, 512).ok is True
