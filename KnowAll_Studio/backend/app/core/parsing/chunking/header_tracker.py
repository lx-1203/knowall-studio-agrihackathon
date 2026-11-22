"""上下文表头追踪：让被切到多个 chunk 的大 Markdown 表格每块都带上表头。

移植自 docreader/splitter/header_hook.py。表头不是简单的一行文本，而是一段
有生命周期的上下文：start 模式命中后表头激活，直到 end 模式命中（空行 /
非表格行）才算结束；期间被切开的每一块都要在开头补上这段表头，否则后续
chunk 里的数据行会失去列名语义。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .types import MARKDOWN_TABLE_HOOK_PRIORITY

# 围栏式 Markdown 表格：表头行 + 分隔行，例如
#   | A | B |
#   | --- | --- |
# Go 的无 (?m) 的 $ 只匹配文本末尾，Python 的 $ 还会匹配末尾换行符之前，
# 因此这里用 \Z 保持等价。
_TABLE_HEADER_START = re.compile(
    r"^\s*(?:\|[^|\n]*)+[\r\n]+\s*(?:\|\s*:?-{3,}:?\s*)+\|?[\r\n]+\Z",
    re.S | re.ASCII,
)
# 空行/纯空白行，或以非竖线非空白字符开头的行 —— 表格结束。
_TABLE_HEADER_END = re.compile(r"^\s*\Z|^\s*[^|\s].*\Z", re.S | re.ASCII)
# 单个 Markdown 表格行："| cell | cell | ... |"
TABLE_ROW_PATTERN = re.compile(r"^\s*(?:\|[^|\n]*)+\|\s*$", re.M | re.ASCII)


@dataclass
class HeaderTrackerHook:
    """一对 start/end 模式：start 命中时该段文本成为活跃表头，end 命中时结束。"""

    start_pattern: re.Pattern[str]
    end_pattern: re.Pattern[str]
    priority: int


def default_header_hooks() -> list[HeaderTrackerHook]:
    """与 docreader/splitter/header_hook.py 的 Python 默认值一致。"""
    return [
        HeaderTrackerHook(
            start_pattern=_TABLE_HEADER_START,
            end_pattern=_TABLE_HEADER_END,
            priority=MARKDOWN_TABLE_HOOK_PRIORITY,
        )
    ]


class HeaderTracker:
    """跨切分单元维护活跃表头的状态。"""

    def __init__(self) -> None:
        self.hooks: list[HeaderTrackerHook] = default_header_hooks()
        self.active_headers: dict[int, str] = {}
        self.ended_headers: dict[int, bool] = {}
        # 列名为空（例如 "||"）的表头，等待第一行数据行来补全列名。
        self.pending_extend: dict[int, bool] = {}
        # 表格行以段间空行结尾时置位：\n\n 切分吃掉了两个表格之间的空行，
        # 所以表头要活到下一个单元，才能识别出新表格开始。
        self.pending_table_break = False
        # 通知 mergeUnits：本单元开始了新表格，需要在它之前先 flush。
        self.header_ended_this_unit = False

    def update(self, split: str) -> None:
        """检查切分文本中的表头 start/end 标记并更新状态。"""
        self.header_ended_this_unit = False

        if self.pending_table_break:
            self.pending_table_break = False
            if MARKDOWN_TABLE_HOOK_PRIORITY in self.active_headers:
                if first_table_row_column_count(split) > 0:
                    self._clear_table_header()
                    self.header_ended_this_unit = True
                else:
                    self._clear_table_header()

        # 1. 在当前活跃的表头里检查结束标记
        for hook in self.hooks:
            if hook.priority in self.active_headers:
                if hook.end_pattern.search(split):
                    self.ended_headers[hook.priority] = True
                    del self.active_headers[hook.priority]
                    self.pending_extend.pop(hook.priority, None)

        # 1b. 段落切分会吃掉两个表格之间的空行。看到 "| last row |\n\n" 时
        # 先标记一个待定断点，下一单元再裁决；同时处理新表格列数与活跃表头
        # 列数不一致的情况。
        if MARKDOWN_TABLE_HOOK_PRIORITY in self.active_headers:
            if not self.pending_extend.get(MARKDOWN_TABLE_HOOK_PRIORITY):
                if split_ends_with_paragraph_break(split):
                    self.pending_table_break = True
                else:
                    self._end_table_header_on_column_mismatch(split)

        # 2. 表头行是空的（例如 "||"）时，用第一行数据行重写表头。
        #
        #    之前："||"           + "| --- | --- |\n"
        #    之后："| col1 | col2 |\n" + "| --- | --- |\n"
        for p in list(self.pending_extend):
            if p in self.active_headers and TABLE_ROW_PATTERN.search(split):
                sep = extract_separator_line(self.active_headers[p])
                self.active_headers[p] = split + sep
            del self.pending_extend[p]

        # 3. 检查新的表头起始标记（既不活跃、也没结束过的 hook 才有资格）
        for hook in self.hooks:
            if hook.priority in self.active_headers:
                continue
            if self.ended_headers.get(hook.priority):
                continue
            m = hook.start_pattern.search(split)
            if m is not None and m.group(0) != "":
                loc = m.group(0)
                self.active_headers[hook.priority] = loc
                if is_empty_table_header_row(loc):
                    self.pending_extend[hook.priority] = True

        # 4. 所有表头都结束后清空 ended 集合，让后续表格还能被追踪。
        if not self.active_headers:
            self.ended_headers.clear()

    def get_headers(self) -> str:
        """所有活跃表头按优先级降序拼接。"""
        if not self.active_headers:
            return ""
        entries = sorted(self.active_headers.items(), key=lambda kv: kv[0], reverse=True)
        return "\n".join(text for _, text in entries)

    def _clear_table_header(self) -> None:
        self.ended_headers[MARKDOWN_TABLE_HOOK_PRIORITY] = True
        self.active_headers.pop(MARKDOWN_TABLE_HOOK_PRIORITY, None)
        self.pending_extend.pop(MARKDOWN_TABLE_HOOK_PRIORITY, None)

    def _end_table_header_on_column_mismatch(self, split: str) -> None:
        header = self.active_headers.get(MARKDOWN_TABLE_HOOK_PRIORITY)
        if header is None:
            return
        row_cols = first_table_row_column_count(split)
        header_cols = header_table_column_count(header)
        if row_cols > 0 and header_cols > 0 and row_cols != header_cols:
            self._clear_table_header()
            self.header_ended_this_unit = True

    # Go 导出名别名
    Update = update
    GetHeaders = get_headers


def new_header_tracker() -> HeaderTracker:
    return HeaderTracker()


def is_empty_table_header_row(header: str) -> bool:
    """表头行（分隔行之前那行）是否只由竖线和空白组成 —— 即列名为空。

    这在 MarkItDown 之类的转换器上很常见：
        ||
        | --- | --- |
        | real column A | real column B |
    """
    idx = header.find("\n")
    if idx < 0:
        return False
    row = header[:idx].strip()
    return all(c in "| \t" for c in row)


def extract_separator_line(header: str) -> str:
    """取出表头里的分隔行（含 --- 的那行）。"""
    for line in header.split("\n"):
        if "---" in line:
            return line + "\n"
    return ""


def split_ends_with_paragraph_break(split: str) -> bool:
    trimmed = split.rstrip(" \t\r")
    return trimmed.endswith("\n\n") or trimmed.endswith("\r\n\r\n")


def table_row_column_count(line: str) -> int:
    line = line.strip()
    if not line.startswith("|"):
        return 0
    parts = line.split("|")
    if parts and parts[0].strip() == "":
        parts = parts[1:]
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    return len(parts)


def first_table_row_column_count(text: str) -> int:
    for raw in text.split("\n"):
        line = raw.strip()
        if line != "" and TABLE_ROW_PATTERN.search(line):
            return table_row_column_count(line)
    return 0


def header_table_column_count(header: str) -> int:
    for raw in header.split("\n"):
        line = raw.strip()
        if line == "" or "---" in line:
            continue
        n = table_row_column_count(line)
        if n > 0:
            return n
    return 0


def header_column_mismatch(headers: str, next_unit: str) -> bool:
    """下一个单元是否开始了列数与活跃表头不同的新表格。"""
    header_cols = header_table_column_count(headers)
    row_cols = first_table_row_column_count(next_unit)
    return header_cols > 0 and row_cols > 0 and header_cols != row_cols


# Go 导出名别名
NewHeaderTracker = new_header_tracker
isEmptyTableHeaderRow = is_empty_table_header_row
extractSeparatorLine = extract_separator_line
splitEndsWithParagraphBreak = split_ends_with_paragraph_break
tableRowColumnCount = table_row_column_count
firstTableRowColumnCount = first_table_row_column_count
headerTableColumnCount = header_table_column_count
headerColumnMismatch = header_column_mismatch
