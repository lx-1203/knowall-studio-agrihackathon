"""内容包构建器:Markdown → 结构化的内容包 JSON → .kaf 文件。

内容包明文结构(解密后):

{
  "type": "course_material",
  "schema_version": 1,
  "chapters": [
    {"id": "ch-001", "title": "...", "nodes": [
        {"id": "node-0001", "kind": "markdown", "title": "...", "content_md": "..."}
    ]},
  ],
  "glossary_refs": ["术语1", "术语2"],
  "meta": {"subject": "...", "source_doc": "..."}
}

要点:
  - 生产端(MarkItDown/知识总纲管道)仍产出 MD —— MD 是内部源格式,
    .kaf 只是分发格式;本模块把 MD 装进「章节/节点」结构,供客户端流式渲染。
  - 转换过程零 AI:纯拆解 + 组装,内容一个字不改。
"""
import json
import re
import uuid


def split_markdown_by_headings(md_text: str) -> tuple:
    """把 MD 按标题切分为章节列表。

    「标题」识别三类(实际资料库中并存):
      1. `# ` / `## ` 标准 Markdown 标题
      2. 整行加粗 `**标题**`(问答/讲义类笔记的常见写法)
      3. 上述两类后紧跟的空白行,均归属当前章节 body
    返回 (chapters, intro);intro 为标题之前无归属的导言。
    """
    chapters = []
    current = None
    intro = []

    def is_heading(line: str):
        s = line.strip()
        if s.count("#") and s.lstrip("#").startswith(" "):
            level = len(s) - len(s.lstrip("#"))
            return level, s.lstrip("#").strip()
        m = re.match(r"^\*\*(.+?)\*\*\s*$", s)  # 整行加粗视为标题
        if m:
            return 1, m.group(1).strip()
        return None

    for line in md_text.splitlines():
        heading = is_heading(line)
        if heading is not None:
            level, title = heading
            current = {"id": f"ch-{len(chapters) + 1:03d}", "title": title, "body": [], "level": level}
            chapters.append(current)
        elif current is not None:
            current["body"].append(line)
        else:
            intro.append(line)

    for ch in chapters:
        ch["body"] = "\n".join(ch["body"]).strip()
        ch["title"] = ch["title"] or "未命名章节"
    return chapters, "\n".join(intro).strip()


def build_content_package(md_text: str, subject: str = "", title: str = "",
                          source_doc: str = "", glossary_refs: list = None) -> dict:
    """Markdown → 内容包明文 dict。"""
    chapters, intro = split_markdown_by_headings(md_text)

    package_chapters = []
    for ch in chapters:
        package_chapters.append({
            "id": ch["id"],
            "title": ch["title"],
            "body": ch["body"],
            "nodes": [{
                "id": f"node-{uuid.uuid4().hex[:12]}",
                "kind": "markdown",
                "title": ch["title"],
                "content_md": ch["body"],
            }],
        })

    package = {
        "type": "course_material",
        "schema_version": 1,
        "title": title,
        "subject": subject,
        "chapters": package_chapters,
        "glossary_refs": glossary_refs or [],
        "meta": {
            "source_doc": source_doc,
            "schema_version": 1,
        },
    }
    # 标题之前的导言作为第 0 章
    if intro:
        package_chapters.insert(0, {
            "id": "ch-000-intro",
            "title": "导言",
            "body": intro,
            "nodes": [{"id": "node-0000", "kind": "markdown", "title": "导言", "content_md": intro}],
        })
    return package


class KAFBuilder:
    """把内容包封装为 .kaf 文件。"""

    def __init__(self, public_key):
        self.public_key = public_key

    def build(self, md_text: str, subject: str = "", title: str = "",
              source_doc: str = "", glossary_refs: list = None, file_id: str = None) -> bytes:
        """Markdown → .kaf 文件字节。"""
        from .keys import generate_content_key, wrap_content_key
        from .format import pack_kaf
        import datetime

        package = build_content_package(
            md_text, subject=subject, title=title,
            source_doc=source_doc, glossary_refs=glossary_refs,
        )
        payload = json.dumps(package, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        content_key = generate_content_key()
        wrapped = wrap_content_key(content_key, self.public_key)
        meta = {
            "file_id": file_id or uuid.uuid4().hex,
            "title": title or "未命名资料",
            "subject": subject,
            "node_count": len(package["chapters"]),
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        return pack_kaf(payload, content_key, wrapped, meta)

    def build_from_package(self, package: dict, meta: dict) -> bytes:
        """已构造好的内容包 dict → .kaf。"""
        from .keys import generate_content_key, wrap_content_key
        from .format import pack_kaf

        payload = json.dumps(package, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        content_key = generate_content_key()
        wrapped = wrap_content_key(content_key, self.public_key)
        return pack_kaf(payload, content_key, wrapped, meta)


def build_from_markdown(md_text: str, public_key, **kwargs) -> bytes:
    """便捷函数:Markdown → .kaf 字节。"""
    return KAFBuilder(public_key).build(md_text, **kwargs)
