"""HTML -> Markdown 转换（纯标准库）。

上游 docreader 走 bs4 + markdownify；本项目要求零新依赖，而这两个包只被 docling
间接带进来、并没有写进 requirements.txt，所以这里用 html.parser 自己实现一个够用
的子集：标题、段落、列表、引用、代码块、表格、链接、图片、行内强调。

列表符号与缩进刻意对齐 markdownify 的默认值（'*'/'+'/'-' 逐层循环、每层缩进两个
空格），这样上游测试里的断言值可以原样保留，epub 与 mhtml 也能共用同一份实现。

为什么"章节标题必须变成 Markdown 井号"：项目分块引擎靠井号识别标题、生成面包屑
（"第一章 > 1.1"），把标题只当普通文本会让章节层级整段丢掉。
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Dict, Iterator, List, Optional, Tuple, Union

_VOID_TAGS = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }
)
# head 里的东西（标题、样式、脚本）都不是正文；iframe 里的内容多半是广告。
_SKIP_TAGS = frozenset(
    {"script", "style", "noscript", "iframe", "head", "title", "template"}
)
_BULLETS = ("*", "+", "-")
_EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:")
# Rendering adds several Python frames per nested element; leave headroom below
# the interpreter's usual recursion limit so the parser raises a clear error.
_MAX_HTML_DEPTH = 200


class HTMLDepthError(ValueError):
    """The input HTML is nested beyond the supported parser depth."""


class Node:
    """轻量 DOM 节点。文本子节点直接以 str 存在 children 里。"""

    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: Optional[Dict[str, str]] = None) -> None:
        self.tag = tag
        self.attrs = attrs or {}
        self.children: List[Union["Node", str]] = []
        self.parent: Optional["Node"] = None


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self._stack: List[Node] = [self.root]

    def _append(self, node: Node) -> None:
        node.parent = self._stack[-1]
        self._stack[-1].children.append(node)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag not in _VOID_TAGS and len(self._stack) >= _MAX_HTML_DEPTH:
            raise HTMLDepthError("HTML is too deeply nested")
        node = Node(tag, {k.lower(): (v or "") for k, v in attrs})
        self._append(node)
        if tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs) -> None:
        self._append(Node(tag, {k.lower(): (v or "") for k, v in attrs}))

    def handle_endtag(self, tag: str) -> None:
        # 找最近的同名祖先；找不到（多余闭合标签）就忽略，绝不弹掉根节点。
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self._stack[-1].children.append(data)


def parse(html_text: str) -> Node:
    """把 HTML/XHTML 文本解析成节点树。"""
    builder = _TreeBuilder()
    builder.feed(html_text)
    builder.close()
    return builder.root


def iter_nodes(root: Node, tags: Union[str, Tuple[str, ...], List[str]]) -> Iterator[Node]:
    """按文档顺序（先序）产出指定标签的节点。"""
    wanted = (tags,) if isinstance(tags, str) else tuple(tags)
    for child in list(root.children):
        if isinstance(child, Node):
            if child.tag in wanted:
                yield child
            yield from iter_nodes(child, wanted)


def node_text(node: Node) -> str:
    """节点子树里的原始文本（不折叠空白，供 pre/标题使用）。"""
    parts: List[str] = []
    for child in node.children:
        if isinstance(child, str):
            parts.append(child)
        else:
            parts.append(node_text(child))
    return "".join(parts)


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _detach(node: Node) -> None:
    parent = node.parent
    if parent is None:
        return
    for index, child in enumerate(parent.children):
        if child is node:
            del parent.children[index]
            return


def _unwrap(node: Node) -> None:
    """用子节点替换自身（去掉标签保留内容）。"""
    parent = node.parent
    if parent is None:
        return
    for index, child in enumerate(parent.children):
        if child is node:
            for grand_child in node.children:
                if isinstance(grand_child, Node):
                    grand_child.parent = parent
            parent.children[index : index + 1] = node.children
            return


def unwrap_internal_links(root: Node) -> None:
    """把不指向外部资源的 <a> 拆成纯文本。

    epub/mhtml 里的站内链接（其它章节文件、#锚点、目录项）在抽取后都是死链，
    留着只会污染正文；只有 http(s)/mailto/tel 这类外部链接才保留。
    """
    for node in list(iter_nodes(root, "a")):
        href = (node.attrs.get("href") or "").strip().lower()
        if not href or not href.startswith(_EXTERNAL_PREFIXES):
            _unwrap(node)


def take_first_heading(root: Node) -> str:
    """取下并返回正文里的第一个 h1/h2，用作章节标题。"""
    for node in iter_nodes(root, ("h1", "h2")):
        title = _collapse(node_text(node)).strip()
        _detach(node)
        return title
    return ""


def _block(inner: str) -> str:
    inner = inner.strip(" \t\n")
    if not inner:
        return ""
    return "\n\n" + inner + "\n\n"


def _render_children(node: Node, depth: int) -> str:
    return "".join(_render(child, depth) for child in node.children)


def _render(node: Union[Node, str], depth: int = 0) -> str:
    if isinstance(node, str):
        # 排版用的换行缩进（"\n   " 这种纯空白文本）不是正文，留着会让下一行
        # 莫名多出一个前导空格；而单个空格（<b>a</b> <b>b</b>）必须保留。
        if not node.strip() and "\n" in node:
            return ""
        return _collapse(node)
    if node.tag in _SKIP_TAGS:
        return ""
    handler = _DISPATCH.get(node.tag)
    if handler is None:
        return _render_children(node, depth)
    return handler(node, depth)


def _heading(node: Node, depth: int) -> str:
    text = _render_children(node, depth).strip()
    if not text:
        return ""
    return _block("#" * int(node.tag[1]) + " " + text)


def _blocky(node: Node, depth: int) -> str:
    return _block(_render_children(node, depth))


def _break(node: Node, depth: int) -> str:
    # 两个尾随空格是 Markdown 的硬换行；_normalize_markdown 会保住它们。
    return "  \n"


def _pre(node: Node, depth: int) -> str:
    code = node.children[0] if node.children and isinstance(node.children[0], Node) else None
    language = ""
    if code is not None and code.tag == "code":
        match = re.search(r"(?:language|lang)-([\w+#-]+)", code.attrs.get("class", ""))
        if match:
            language = match.group(1)
    body = node_text(node).replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    if not body:
        return ""
    return _block("```" + language + "\n" + body + "\n```")


def _blockquote(node: Node, depth: int) -> str:
    inner = _render_children(node, depth).strip("\n")
    if not inner.strip():
        return ""
    lines = [("> " + line).rstrip() if line.strip() else ">" for line in inner.split("\n")]
    return _block("\n".join(lines))


def _list(node: Node, depth: int) -> str:
    ordered = node.tag == "ol"
    index = 1
    start = node.attrs.get("start", "")
    if ordered and start.isdigit():
        index = int(start)
    items: List[str] = []
    for child in node.children:
        if isinstance(child, str) or child.tag != "li":
            continue
        marker = f"{index}. " if ordered else _BULLETS[depth % 3] + " "
        index += 1
        body = _render_li(child, depth)
        if not body:
            items.append(marker.rstrip())
            continue
        # 续行（含嵌套列表）按 2 空格递增缩进，Markdown 才认得它们属于这一项。
        items.append(marker + body.replace("\n", "\n" + "  " * (depth + 1)))
    return "\n".join(items)


def _render_li(node: Node, depth: int) -> str:
    parts: List[str] = []
    for child in node.children:
        if isinstance(child, str):
            parts.append(_collapse(child))
            continue
        if child.tag in ("ul", "ol"):
            nested = _list(child, depth + 1)
            if nested:
                # 嵌套列表换行另起，缩进由外层 _list 的续行补齐。
                parts.append("\n" + nested)
            continue
        parts.append(_render(child, depth + 1))
    return "".join(parts).strip(" \t\n")


def _cell(node: Node, depth: int) -> str:
    text = re.sub(r"\s*\n\s*", " ", _render_children(node, depth))
    return _collapse(text).strip().replace("|", "\\|")


def _table(node: Node, depth: int) -> str:
    rows: List[List[str]] = []
    for row in iter_nodes(node, "tr"):
        cells = [
            child
            for child in row.children
            if isinstance(child, Node) and child.tag in ("td", "th")
        ]
        if cells:
            rows.append([_cell(cell, depth) for cell in cells])
    if not rows:
        return ""
    width = max(len(row) for row in rows)

    def line(cells: List[str]) -> str:
        return "| " + " | ".join(cells + [""] * (width - len(cells))) + " |"

    out = [line(rows[0]), "| " + " | ".join(["---"] * width) + " |"]
    out.extend(line(row) for row in rows[1:])
    return _block("\n".join(out))


def _anchor(node: Node, depth: int) -> str:
    href = (node.attrs.get("href") or "").strip()
    text = _render_children(node, depth).strip()
    if not href:
        return text
    if not text:
        return f"<{href}>"
    return f"[{text}]({href})"


def _image(node: Node, depth: int) -> str:
    src = (node.attrs.get("src") or "").strip()
    if not src:
        return ""
    alt = _collapse(node.attrs.get("alt", "")).strip()
    title = node.attrs.get("title", "")
    if title:
        return f'![{alt}]({src} "{title}")'
    return f"![{alt}]({src})"


def _wrap(node: Node, depth: int, marker: str) -> str:
    text = _render_children(node, depth)
    return f"{marker}{text}{marker}" if text.strip() else ""


def _inline_code(node: Node, depth: int) -> str:
    text = _render_children(node, depth)
    return f"`{text}`" if text.strip() else ""


def _passthrough(node: Node, depth: int) -> str:
    return _render_children(node, depth)


_BLOCKY_TAGS = (
    "p", "div", "section", "article", "header", "footer", "main", "aside",
    "figure", "figcaption", "dl", "dt", "dd", "form", "fieldset", "details",
    "summary", "center", "address",
)

_DISPATCH = {
    **{tag: _blocky for tag in _BLOCKY_TAGS},
    **{f"h{level}": _heading for level in range(1, 7)},
    "br": _break,
    "hr": lambda node, depth: _block("---"),
    "pre": _pre,
    "blockquote": _blockquote,
    "ul": _list,
    "ol": _list,
    "li": lambda node, depth: _render_li(node, depth),
    "table": _table,
    "a": _anchor,
    "img": _image,
    "strong": lambda node, depth: _wrap(node, depth, "**"),
    "b": lambda node, depth: _wrap(node, depth, "**"),
    "em": lambda node, depth: _wrap(node, depth, "*"),
    "i": lambda node, depth: _wrap(node, depth, "*"),
    "del": lambda node, depth: _wrap(node, depth, "~~"),
    "s": lambda node, depth: _wrap(node, depth, "~~"),
    "strike": lambda node, depth: _wrap(node, depth, "~~"),
    "code": _inline_code,
    "span": _passthrough,
    "u": _passthrough,
    "mark": _passthrough,
    "sup": _passthrough,
    "sub": _passthrough,
    "font": _passthrough,
}


def _opening_fence(line: str) -> Optional[Tuple[str, int]]:
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3 or not stripped:
        return None
    fence_char = stripped[0]
    if fence_char not in {"`", "~"}:
        return None
    fence_len = len(stripped) - len(stripped.lstrip(fence_char))
    if fence_len < 3:
        return None
    return fence_char, fence_len


def _is_closing_fence(line: str, fence_char: str, fence_len: int) -> bool:
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3:
        return False
    closing_len = len(stripped) - len(stripped.lstrip(fence_char))
    if closing_len < fence_len:
        return False
    return not stripped[closing_len:].strip(" \t")


def normalize_markdown(markdown_text: str) -> str:
    """折叠块之间的多余空行，但代码围栏内部原样保留。"""
    text = markdown_text.replace("\r\n", "\n").replace("\r", "\n")
    output: List[str] = []
    pending_blank = False
    fence_char: Optional[str] = None
    fence_len = 0

    for line in text.split("\n"):
        if fence_char is not None:
            output.append(line)
            if _is_closing_fence(line, fence_char, fence_len):
                fence_char = None
                fence_len = 0
            continue

        opening = _opening_fence(line)
        if opening is not None:
            if pending_blank and output:
                output.append("")
            pending_blank = False
            output.append(line)
            fence_char, fence_len = opening
            continue

        if not line.strip(" \t"):
            pending_blank = True
            continue

        if pending_blank and output:
            output.append("")
        pending_blank = False

        trailing_spaces = len(line) - len(line.rstrip(" "))
        if trailing_spaces >= 2:
            line = line.rstrip(" \t") + "  "
        else:
            line = line.rstrip(" \t")
        output.append(line)

    return "\n".join(output).strip("\n")


def to_markdown(root: Node) -> str:
    """渲染节点树，不做空行规整。"""
    return _render(root, 0)


def render(root: Node) -> str:
    """渲染并规整成最终 Markdown。"""
    return normalize_markdown(to_markdown(root))


def html_to_markdown(
    html_text: str,
    *,
    strip_internal_links: bool = True,
    fallback_to_raw_html: bool = True,
) -> str:
    """一次性完成"解析 -> 去站内链接 -> 渲染"，空结果按策略兜底。"""
    root = parse(html_text)
    if strip_internal_links:
        unwrap_internal_links(root)
    result = render(root)
    if result:
        return result
    text_fallback = _collapse(node_text(root)).strip()
    if text_fallback:
        return text_fallback
    if fallback_to_raw_html:
        return "```html\n" + html_text[:50000] + "\n```"
    return ""
