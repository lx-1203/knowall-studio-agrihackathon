"""字符偏移 → 真实页码的映射，供引用溯源使用。

为什么需要：分块后的 chunk 只有一段文本，前端要"点引用跳到原文第几页"就必须知道
这段文本来自源文件的哪一页。原先的 page_range 是按 chunk 序号插值出来的假页码，
检索命中的定位指向完全无关的一页。

这里的思路和 WeKnora 的 internal/sourceloc 一致：解析器抽正文时顺手记下每个条目
(element) 的页码，分块后再把 chunk 的字符区间映射回页码。不做 OCR、不做版面分析，
只做一次单调对齐 —— 把 (条目文本, 页码) 序列在源文本里从前往后依次定位，得到稀疏
锚点，再补成连续区间。

两个关键的容错设计：

1. 单调游标。定位失败只会让后面的区间继续继承上一页，绝不会把页码映射回更早的
   位置。所以最坏情况是"页码偏粗"，而不是"指到错误的页"。
2. 比对在"空白归一"的副本上做。条目文本与正文的空白几乎不可能完全一致（Markdown
   导出、清洗、折行都会改），归一后再比对，命中位置再换回原偏移。
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from typing import Iterable, Sequence

# 页码未知时的占位值。用 0 而不是 None，是为了和数据库里 page_range 的空串对齐：
# 任何一步判断不出页码，最终都退化成空串，而不是编一个数字出来。
PAGE_UNKNOWN = 0

# 匹配键的"特异性"下限。不能按字符数判断，因为同样的长度在中英文里含义完全不同：
# 中文 7 个字（约 12 token）已经足够唯一，英文 7 个字符（"the best"）在正文里到处都是。
# 所以按"拉丁字符 1 分、中日韩字符 2 分"计权，12 分约等于 6 个汉字或 12 个拉丁字符。
# 达标不了就放弃这个锚点 —— 定位到错误位置比定位不到更糟。
MIN_ANCHOR_WEIGHT = 12

_CJK = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]"
)


def _specificity(key: str) -> int:
    """匹配键的特异性权重：中日韩字符记 2 分，其余记 1 分。"""
    return len(key) + len(_CJK.findall(key))

# 参与比对的条目前缀上限。条目越长越容易被后续清洗改动打断，取前缀反而更稳；
# 砍到 200 字符也足够在正文里唯一定位。
MAX_KEY_LEN = 200

_WS_RUN = re.compile(r"\s+")
# 条目开头的 Markdown 装饰：标题井号、列表符号、引用、表格竖线、代码围栏。
# 清洗器不一定保留这些符号，留着会让匹配键在正文里找不到。
_LEADING_DECOR = re.compile(r"^[\s#>\-+|*`~\u3000]+")
# docling 用 HTML 注释占位图片，它本身不是正文，不参与定位。
_PLACEHOLDER = re.compile(r"^<!--.*-->$")


@dataclass(frozen=True)
class PageSpan:
    """源文本中的一段连续区间及其所在页码。"""

    start: int
    end: int
    page: int


class PageMap:
    """不可变的偏移 → 页码映射。

    锚点是稀疏的（只有条目起点有页码），page_at 用二分找出该偏移落在哪个区间里。
    """

    __slots__ = ("_starts", "_spans")

    def __init__(self, spans: Sequence[PageSpan]) -> None:
        self._spans = list(spans)
        self._starts = [s.start for s in self._spans]

    def __bool__(self) -> bool:
        return bool(self._spans)

    def __len__(self) -> int:
        return len(self._spans)

    def page_at(self, offset: int) -> int:
        """返回该字符偏移所在的页码；在第一个锚点之前返回 PAGE_UNKNOWN。"""
        if not self._spans:
            return PAGE_UNKNOWN
        i = bisect.bisect_right(self._starts, offset) - 1
        if i < 0:
            return PAGE_UNKNOWN
        return self._spans[i].page

    def page_range(self, start: int, end: int) -> str:
        """把字符区间格式化成 "p3" / "p3-p5"；无从判断时返回空串。

        返回空串是刻意的：调用方拿到空串就知道"这一条没有可信页码"，
        可以退化成显示文件名，而不是展示一个编出来的页码。
        """
        if end <= start:
            end = start + 1
        first = self.page_at(start)
        last = self.page_at(end - 1)
        if first == PAGE_UNKNOWN:
            first = last
        if last == PAGE_UNKNOWN:
            last = first
        if first == PAGE_UNKNOWN:
            return ""
        if last <= first:
            # last < first 在单调锚点下不该出现；真出现了说明输入不是阅读顺序，
            # 这时报单页比报一个倒序区间诚实。
            return f"p{first}"
        return f"p{first}-p{last}"


class _NormalizedText:
    """空白归一的文本副本，保留映射回原偏移的能力。

    归一规则只做一件事：把所有空白串压成一个空格。不删标点、不改大小写 ——
    改动越多，映射回原偏移时越容易和真实位置错位。
    """

    __slots__ = ("text", "_orig_index")

    def __init__(self, source: str) -> None:
        buf: list[str] = []
        orig: list[int] = []
        pending_space = False
        for i, ch in enumerate(source):
            if ch.isspace():
                pending_space = True
                continue
            if pending_space and buf:
                buf.append(" ")
                # 空格记成"下一个非空白字符"的原偏移：归一后的位置本来就落在
                # 这个字符之前，映射过去才是原位。
                orig.append(i)
            pending_space = False
            buf.append(ch)
            orig.append(i)
        self.text = "".join(buf)
        self._orig_index = orig

    def original_offset(self, norm_offset: int) -> int:
        if not self._orig_index:
            return 0
        if norm_offset < len(self._orig_index):
            return self._orig_index[norm_offset]
        return self._orig_index[-1] + 1


def _candidate_keys(item_text: str) -> list[str]:
    """把一条解析条目压成若干候选匹配键，长的在前。

    为什么要给多个候选而不是一个：条目文本的首行往往是页眉/页脚/页码，
    正好是清洗器删掉的东西。只用整条前缀去匹配时，首行被删则所有前缀长度都
    匹配不上（问题出在开头，截短救不了）。所以额外给出去掉首行的候选。

    拆行必须在压缩空白**之前**做：先按换行拆，再把每行内部的空白压成一个空格。
    反过来做会把换行也压成空格，行结构就丢了，"去掉首行"这个候选键也就无从谈起。
    """
    raw_lines = [ln.strip() for ln in item_text.splitlines()]
    lines = [_WS_RUN.sub(" ", ln) for ln in raw_lines if ln]

    keys: list[str] = []
    if lines:
        keys.append(" ".join(lines))
        if len(lines) > 1:
            keys.append(" ".join(lines[1:]))
            keys.append(lines[0])

    out: list[str] = []
    seen: set[str] = set()
    for key in keys:
        key = _LEADING_DECOR.sub("", key).strip()[:MAX_KEY_LEN].strip()
        if key and not _PLACEHOLDER.match(key) and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _locate(haystack: str, key: str, start: int, min_weight: int) -> int:
    """从 start 起定位 key；整键找不到就折半截短再试。

    截短是必要的：条目后半段可能被清洗改掉（合并断行、去冗余空格），
    但只要前缀仍足够特异，锚点位置依然正确。
    """
    length = len(key)
    while length > 0:
        candidate = key[:length]
        if _specificity(candidate) < min_weight:
            # 从长到短遍历，一旦不够特异，更短的只会更差
            break
        pos = haystack.find(candidate, start)
        if pos >= 0:
            return pos
        length //= 2
    return -1


def build_page_map(
    text: str,
    items: Iterable[tuple[str, int]],
    *,
    min_weight: int = MIN_ANCHOR_WEIGHT,
) -> PageMap:
    """按阅读顺序把 (条目文本, 页码) 对齐到 text，生成页码映射。

    items 必须是解析器给出的阅读顺序序列。乱序输入会让单调游标失效，结果是
    页码整体错位 —— 这是本函数唯一的强前置条件。

    text 必须是**清洗之后**的正文：chunk 的字符区间是相对清洗后文本算的，
    拿清洗前的文本建映射会整体错位。清洗前的条目文本仍可用作匹配键，因为
    定位是在清洗后文本上做的模糊查找。
    """
    if not text:
        return PageMap(())

    norm = _NormalizedText(text)
    anchors: list[tuple[int, int]] = []  # (归一偏移, 页码)
    cursor = 0

    for item_text, page in items:
        if page <= PAGE_UNKNOWN or not item_text:
            continue
        pos = -1
        for key in _candidate_keys(item_text):
            pos = _locate(norm.text, key, cursor, min_weight)
            if pos >= 0:
                break
        if pos < 0:
            continue
        cursor = pos + 1
        # 同一页的连续条目只记第一个锚点：区间本来就会覆盖到下一个锚点，
        # 密集重复记录只会让锚点表白白变长。
        if anchors and anchors[-1][1] == page:
            continue
        anchors.append((pos, page))

    if not anchors:
        return PageMap(())

    # 把锚点偏移换回原文本偏移。find 的返回值单调递增，所以换回来也是递增的；
    # 仍然显式去重，避免归一化在极端空白下把两个锚点折到同一个原偏移。
    starts: list[int] = []
    pages: list[int] = []
    for pos, page in anchors:
        start = norm.original_offset(pos)
        if starts and start <= starts[-1]:
            continue
        starts.append(start)
        pages.append(page)

    if not starts:
        return PageMap(())

    spans = [
        PageSpan(
            start=0 if i == 0 else starts[i],
            end=starts[i + 1] if i + 1 < len(starts) else len(text),
            page=pages[i],
        )
        for i in range(len(starts))
    ]
    # 第一个锚点之前的正文归到首个锚点所在页。理由：正文从哪一页开始，就说明
    # 前面几页没有可抽取的文字（扫描封面、纯图页），此时不存在"更早的页"可归属。
    return PageMap(spans)


def build_page_map_from_parts(
    parts: Sequence[tuple[str, int]],
    joiner: str = "\n\n",
) -> PageMap:
    """已知每段文本及其页码时的精确映射，不经过对齐。

    只适用于"正文就是这些段落按 joiner 拼出来"且**清洗没有改动过正文**的场景。
    一旦正文被清洗器改过（长度变了），这里的偏移就全错了 —— 所以除了调试和
    单测，正常入库流程请一律走 build_page_map 的对齐路径。
    """
    spans: list[PageSpan] = []
    cursor = 0
    for i, (part, page) in enumerate(parts):
        if i:
            cursor += len(joiner)
        start = cursor
        cursor += len(part)
        if page > PAGE_UNKNOWN and part.strip():
            spans.append(PageSpan(start, cursor, page))
    return PageMap(spans)
