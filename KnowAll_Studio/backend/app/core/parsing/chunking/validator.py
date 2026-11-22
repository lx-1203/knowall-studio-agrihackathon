"""分块结果校验：判断某个 Tier 的输出能不能用，不能就降级到下一档。

校验器刻意宽松：只拒绝明显坏掉的输出，外观上的差异一律放过，否则
会在 Tier 之间来回震荡。
"""

from __future__ import annotations

from .types import Chunk, ValidationResult

# 除最后一块外，短于该 rune 长度的块视为过碎。
TINY_CHUNK_RUNES = 50


def validate_chunks(chunks: list[Chunk] | None, total_chars: int, chunk_size: int) -> ValidationResult:
    """校验 chunks 是否是一份可用于 total_chars 字符、目标 chunk_size 的结果。"""
    if not chunks:
        return ValidationResult(reason="no chunks produced")

    # 文档远大于 chunk_size 却只产出一个块，说明策略根本没切 —— 失败，
    # 让下一档 Tier 接手。
    if len(chunks) == 1 and total_chars > 2 * chunk_size:
        return ValidationResult(reason="single chunk for large document")

    lengths = [len(c.content) for c in chunks]  # rune 长度
    max_len = max(lengths)
    min_len = min(lengths)
    sum_sq = float(sum(l * l for l in lengths))
    avg = float(sum(lengths)) / float(len(chunks))

    # 除最后一块外每块都应有实质内容；尾块允许很小，因为尾部残料是常态。
    tiny_count = 0
    for i, c in enumerate(chunks):
        if i == len(chunks) - 1:
            continue
        if len(c.content) < TINY_CHUNK_RUNES:
            tiny_count += 1
    if tiny_count > len(chunks) // 4 and tiny_count > 2:
        return ValidationResult(reason="too many tiny chunks")

    # 没有任何块达到目标的 25%，说明切得过碎，没有使用价值。
    if max_len < chunk_size // 4 and total_chars > chunk_size:
        return ValidationResult(reason="all chunks far below target size")

    # 绝对上界兜底：超过 2x chunkSize 说明分块器无视了尺寸预算。
    if max_len > 2 * chunk_size and chunk_size > 0:
        return ValidationResult(reason="chunk exceeds 2x target size")

    _ = (avg, sum_sq, min_len)
    return ValidationResult(ok=True)


# Go 导出名别名
ValidateChunks = validate_chunks
