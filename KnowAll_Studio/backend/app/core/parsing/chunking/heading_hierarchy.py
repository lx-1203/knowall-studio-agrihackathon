"""Markdown 标题层级栈 → 面包屑。

概念上与 header_tracker（用 start/end 钩子追踪表头）相似，但 Markdown
标题没有显式结束符 —— 它被下一个同级或更浅的标题终结。header_tracker 的
钩子抽象套不上这个模型，所以这里显式建模成 level 栈。
"""

from __future__ import annotations

from .patterns import MarkdownHeadingPattern


class HeadingHierarchy:
    """维护当前活跃的 Markdown 标题栈（层级 1..6）。

    压入 N 级标题会弹出所有 >= N 级的旧条目，因为之前的兄弟/子节点
    已经不在作用域内。
    """

    def __init__(self) -> None:
        # stack[i] 保存 i+1 级标题文本（stack[0] 即 H1）；超过当前最深
        # 活跃层级的槽位是空串。
        self.stack: list[str] = [""] * 6
        self._depth = 0

    def copy(self) -> "HeadingHierarchy":
        """对应 Go 的结构体值拷贝 sectionStart := *hierarchy。"""
        clone = HeadingHierarchy()
        clone.stack = list(self.stack)
        clone._depth = self._depth
        return clone

    def observe(self, line: str) -> tuple[int, str]:
        """line 是 Markdown 标题时更新层级栈。

        返回 (level, 标题文本)，不是标题时返回 (0, "")。围栏代码块里的
        标题不会在这里被识别 —— 调用方必须避免把代码块内容喂进来
        （heading_splitter 就是这么做的）。
        """
        m = MarkdownHeadingPattern.search(line)
        if m is None:
            return 0, ""
        level = len(m.group(1))
        if level < 1 or level > 6:
            return 0, ""
        heading = m.group(2).strip()
        # 覆盖本级并清空更深的层级：上一级标题的兄弟/子节点已出作用域。
        self.stack[level - 1] = heading
        for i in range(level, 6):
            self.stack[i] = ""
        if level > self._depth:
            self._depth = level
        else:
            # 推入更浅的标题时深度可能收缩，需要重算。
            self._depth = 0
            for i in range(6):
                if self.stack[i] != "":
                    self._depth = i + 1
        return level, heading

    def breadcrumb(self) -> str:
        """当前标题路径，用 " > " 连接；没有活跃标题时返回空串。"""
        if self._depth == 0:
            return ""
        parts: list[str] = []
        for i in range(self._depth):
            if self.stack[i] != "":
                parts.append(self.stack[i])
        return " > ".join(parts)

    def breadcrumb_with_hashes(self) -> str:
        """带原始 # 前缀的路径，用于回填进 chunk 作为上下文头。

        例：# Chapter 1 / ## Section 2 / ### Subsection a（各占一行）
        """
        if self._depth == 0:
            return ""
        out: list[str] = []
        for i in range(self._depth):
            if self.stack[i] == "":
                continue
            out.append("#" * (i + 1) + " " + self.stack[i])
        return "\n".join(out)

    def depth(self) -> int:
        return self._depth

    def reset(self) -> None:
        self.stack = [""] * 6
        self._depth = 0

    # Go 导出名别名
    Observe = observe
    Breadcrumb = breadcrumb
    BreadcrumbWithHashes = breadcrumb_with_hashes
    Depth = depth
    Reset = reset


def new_heading_hierarchy() -> HeadingHierarchy:
    return HeadingHierarchy()


# Go 导出名别名
NewHeadingHierarchy = new_heading_hierarchy
