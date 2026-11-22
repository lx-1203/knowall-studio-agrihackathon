"""Markdown 表格规整（移植自 docreader/parser/markdown_parser.py）。

比项目现有 md 解析强的地方就在这一块：现有 _parse_markdown 直接把原文交给
分块引擎，而 Word/MarkItDown 转换出来的表格常带"空首行 + 分隔行"这种畸形前缀，
分块引擎会把它们当成普通段落切碎。format_tables() 把它们修成合法 GFM 表格
（补分隔行、去掉伪前缀行、统一管道两侧空格），标题/表格结构才留得住。

实现刻意不用正则做整行匹配：https://github.com/Tencent/WeKnora/issues/2768
里 row 级正则遇到不闭合的表格行会灾难性回溯。

上游同文件的 MarkdownImageUtil（base64 图片抽取）没有移植：本项目图片走
docling 链路，md 里的 data:image base64 目前没有消费方，搬过来只是死代码。
"""
from __future__ import annotations

import re
from typing import List

_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")
# 旧 align_pattern 认定的对齐行单元格：任意非空的 : 和 - 组合，比 _SEPARATOR_CELL 宽。
_ALIGNMENT_CELL = re.compile(r"^[:-]+$")


class MarkdownTableUtil:
    """把 Markdown 表格规整成统一格式。

    >>> MarkdownTableUtil().format_table("|姓名|年龄|\n|:---|---:|\n|张三|25|")
    '| 姓名 | 年龄 |\\n| :--- | ---: |\\n| 张三 | 25 |'
    """

    @staticmethod
    def _split_row_cells(row_line: str) -> List[str]:
        """拆出单元格，保留空单元格。"""
        inner = row_line.strip()
        if not inner.startswith("|"):
            return []
        parts = inner.split("|")
        if parts and parts[0].strip() == "":
            parts = parts[1:]
        if parts and parts[-1].strip() == "":
            parts = parts[:-1]
        return [part.strip() for part in parts]

    @staticmethod
    def _is_table_row(line: str) -> bool:
        stripped = line.strip()
        return stripped.startswith("|") and "|" in stripped[1:]

    @classmethod
    def _is_separator_row(cls, line: str) -> bool:
        cells = cls._split_row_cells(line)
        return bool(cells) and all(_SEPARATOR_CELL.match(cell) for cell in cells)

    @classmethod
    def _is_alignment_row(cls, line: str) -> bool:
        cells = cls._split_row_cells(line)
        return bool(cells) and all(_ALIGNMENT_CELL.match(cell) for cell in cells)

    @classmethod
    def _is_empty_row(cls, line: str) -> bool:
        cells = cls._split_row_cells(line)
        return bool(cells) and all(cell == "" for cell in cells)

    @classmethod
    def _separator_row_for(cls, header_line: str) -> str:
        cells = cls._split_row_cells(header_line)
        return "| " + " | ".join("---" for _ in cells) + " |"

    @classmethod
    def _normalize_table_block(cls, block: List[str]) -> List[str]:
        """修 MarkItDown 风格的表格：去掉伪前缀行，必要时补 GFM 分隔行。"""
        while block and cls._is_empty_row(block[0]):
            block.pop(0)
        if block and cls._is_separator_row(block[0]):
            block.pop(0)
        # GFM/marked 要求首行之后紧跟 "| --- |"。无表头的 Word 表格在剥掉伪前缀后
        # 只剩数据行，需要自己补一行分隔符，否则渲染不出表格。
        if len(block) >= 2 and not cls._is_separator_row(block[1]):
            sep = cls._separator_row_for(block[0])
            block = [block[0], sep] + block[1:]
        return block

    def normalize_spurious_table_prefixes(self, content: str) -> str:
        """删掉 MarkItDown 表格输出里伪造的空行/分隔行前缀。"""
        lines = content.split("\n")
        out: List[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if not self._is_table_row(line):
                out.append(line)
                i += 1
                continue
            block: List[str] = []
            while i < len(lines) and self._is_table_row(lines[i]):
                block.append(lines[i])
                i += 1
            out.extend(self._normalize_table_block(block))
        return "\n".join(out)

    def format_table(self, content: str) -> str:
        """规整 content 里的所有 Markdown 表格。"""
        return self._format(content)

    def _format(self, content: str) -> str:
        def format_alignment_row(prefix: str, columns: List[str]) -> str:
            processed = []
            for col in columns:
                left_colon = ":" if col.startswith(":") else ""
                right_colon = ":" if col.endswith(":") else ""
                processed.append(left_colon + "---" + right_colon)
            return prefix + "| " + " | ".join(processed) + " |"

        def format_data_row(prefix: str, columns: List[str]) -> str:
            return prefix + "| " + " | ".join(columns) + " |"

        # 先统一换行：行尾残留的 \r 会被当成"以 | 结尾"，也会输出混合行尾。
        unified = content.replace("\r\n", "\n").replace("\r", "\n")
        out: List[str] = []
        for line in unified.split("\n"):
            stripped = line.lstrip(" \t")
            # 只有首尾都是管道符的行才算表格行，其余原样放过；这也让不闭合的行
            # 保持廉价处理。rstrip 只去空格和制表符（对应旧的 \|[\t ]*$）。
            if stripped.startswith("|") and line.rstrip(" \t").endswith("|"):
                prefix = line[: len(line) - len(stripped)]
                columns = self._split_row_cells(line)
                # 单个 "|" 不是表格行：旧 line_pattern 要求首尾都有管道符。
                # 把它格式化成 "|  |" 反而会被 normalize_spurious_table_prefixes 删掉。
                if not columns:
                    out.append(line)
                elif self._is_alignment_row(line):
                    out.append(format_alignment_row(prefix, columns))
                else:
                    out.append(format_data_row(prefix, columns))
            else:
                out.append(line)
        return self.normalize_spurious_table_prefixes("\n".join(out))


_TABLE_UTIL = MarkdownTableUtil()


def format_tables(content: str) -> str:
    """模块级入口：规整 Markdown 里的表格，无表格时原样返回。"""
    if "|" not in content:
        return content
    return _TABLE_UTIL.format_table(content)
