"""EPUB 电子书解析（移植自 docreader/parser/epub_parser.py，改用标准库实现）。

上游靠 ebooklib 读 EPUB；本项目要求零新依赖，而读一本书需要的只是固定那条链路：
META-INF/container.xml -> OPF(metadata/manifest/spine) -> TOC(nav 或 NCX) -> XHTML，
zipfile + xml.etree 足够，而且省掉了"先落盘再让 ebooklib 读"的临时文件。

结构信息是这次移植的重点：每个章节标题都会写成 Markdown 一级标题（"# 章节名"），
章节内部由 HTML 转换器产出的 h2/h3 自然挂在它下面。项目分块引擎靠井号识别标题层级
并生成面包屑（"第一章 > 1.1"），如果标题只当普通文本，整本书的章节结构就白丢了。
"""
from __future__ import annotations

import base64
import posixpath
import re
import uuid
import zipfile
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote
from xml.etree import ElementTree

from app.core.parsing.docreader import html_markdown
from app.core.parsing.docreader.document import Document
from app.core.parsing.docreader.endecode import decode_bytes

_CONTAINER_PATH = "META-INF/container.xml"
_HTML_EXTENSIONS = (".html", ".xhtml", ".htm")
_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".tiff")
_IMAGE_MEDIA_PREFIX = "image/"
_NCX_MEDIA_TYPE = "application/x-dtbncx+xml"
_XML_ENCODING = re.compile(rb"encoding=[\"']([\w.+-]+)[\"']", re.IGNORECASE)
_MAX_MEMBER_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
_MAX_ARCHIVE_UNCOMPRESSED_BYTES = 512 * 1024 * 1024


@dataclass
class _ManifestItem:
    id: str
    href: str        # OPF 里原样的 href
    path: str        # 解析到 zip 内路径后的结果
    media_type: str
    properties: str


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _iter_local(root: ElementTree.Element, name: str) -> Iterable[ElementTree.Element]:
    for element in root.iter():
        if _local_name(element.tag) == name:
            yield element


def _first_local(root: ElementTree.Element, name: str) -> Optional[ElementTree.Element]:
    for element in _iter_local(root, name):
        return element
    return None


class _UnsafeXmlError(ValueError):
    """An EPUB XML member contains a DTD that must not be expanded."""


class _NoDoctypeTreeBuilder(ElementTree.TreeBuilder):
    def doctype(self, name: str, pubid: str, system: str) -> None:
        raise _UnsafeXmlError("EPUB XML DTD/entity declarations are not allowed")


def _safe_xml_parse(data: bytes) -> ElementTree.Element:
    """Parse XML while rejecting DTDs regardless of the document encoding."""
    parser = ElementTree.XMLParser(target=_NoDoctypeTreeBuilder())
    return ElementTree.fromstring(data, parser=parser)


def _normalize_epub_path(path: str) -> str:
    path = unquote(path.split("#", 1)[0].split("?", 1)[0]).replace("\\", "/")
    normalized = posixpath.normpath(path)
    return "" if normalized == "." else normalized.lstrip("/")


def _resolve_href(base_dir: str, href: str) -> str:
    """把 OPF 里相对本文件的 href 解析成 zip 内路径。"""
    target = unquote(href.split("#", 1)[0].split("?", 1)[0]).replace("\\", "/")
    if not target:
        return ""
    joined = posixpath.join(base_dir, target) if base_dir else target
    return posixpath.normpath(joined).lstrip("/")


def _decode_xhtml(data: bytes) -> str:
    declared = _XML_ENCODING.search(data[:200])
    if declared:
        try:
            return data.decode(declared.group(1).decode("ascii"), errors="replace")
        except LookupError:
            pass
    return decode_bytes(data)


def _member_index(names: List[str]) -> Dict[str, str]:
    """zip 成员名 -> 实际成员名，同时登记小写与 basename 以便容错查找。"""
    index: Dict[str, str] = {}
    for name in names:
        index.setdefault(name, name)
        index.setdefault(name.lower(), name)
        index.setdefault(posixpath.basename(name).lower(), name)
    return index


def _read_member(archive: zipfile.ZipFile, index: Dict[str, str], path: str) -> Optional[bytes]:
    actual = index.get(path) or index.get(path.lower()) or index.get(path.lstrip("/"))
    if actual is None:
        return None
    try:
        return archive.read(actual)
    except (KeyError, zipfile.BadZipFile):
        return None


def _parse_opf(opf_bytes: bytes, opf_dir: str) -> Tuple[Dict[str, Any], List[_ManifestItem], List[str], str]:
    root = _safe_xml_parse(opf_bytes)

    metadata: Dict[str, Any] = {}
    mapping = {
        "title": "title",
        "creator": "author",
        "publisher": "publisher",
        "language": "language",
        "description": "description",
        "date": "date",
        "identifier": "isbn",
    }
    meta_root = _first_local(root, "metadata")
    if meta_root is not None:
        for dc_key, out_key in mapping.items():
            values = [
                (element.text or "").strip()
                for element in _iter_local(meta_root, dc_key)
                if (element.text or "").strip()
            ]
            if values:
                metadata[out_key] = ", ".join(values) if out_key == "author" else values[0]

    items: List[_ManifestItem] = []
    for element in _iter_local(root, "item"):
        href = element.get("href") or ""
        items.append(
            _ManifestItem(
                id=element.get("id") or "",
                href=href,
                path=_resolve_href(opf_dir, href),
                media_type=(element.get("media-type") or "").strip().lower(),
                properties=(element.get("properties") or "").strip().lower(),
            )
        )

    by_id = {item.id: item for item in items}
    spine_paths: List[str] = []
    spine = _first_local(root, "spine")
    toc_id = (spine.get("toc") or "") if spine is not None else ""
    if spine is not None:
        for itemref in _iter_local(spine, "itemref"):
            item = by_id.get(itemref.get("idref") or "")
            if item is not None and item.path:
                spine_paths.append(item.path)
    return metadata, items, spine_paths, toc_id


def _nav_entries(archive: zipfile.ZipFile, index: Dict[str, str], nav: _ManifestItem) -> List[Tuple[str, str]]:
    raw = _read_member(archive, index, nav.path)
    if raw is None:
        return []
    root = html_markdown.parse(_decode_xhtml(raw))
    nav_roots = list(html_markdown.iter_nodes(root, "nav"))
    scopes = nav_roots if nav_roots else [root]
    entries: List[Tuple[str, str]] = []
    base_dir = posixpath.dirname(nav.path)
    for scope in scopes:
        for anchor in html_markdown.iter_nodes(scope, "a"):
            href = (anchor.attrs.get("href") or "").strip()
            if not href:
                continue
            label = re.sub(r"\s+", " ", html_markdown.node_text(anchor)).strip()
            entries.append((_resolve_href(base_dir, href), label))
    return entries


def _ncx_entries(
    archive: zipfile.ZipFile, index: Dict[str, str], ncx: _ManifestItem
) -> List[Tuple[str, str]]:
    raw = _read_member(archive, index, ncx.path)
    if raw is None:
        return []
    try:
        root = _safe_xml_parse(raw)
    except _UnsafeXmlError:
        raise
    except (ElementTree.ParseError, ValueError):
        return []
    base_dir = posixpath.dirname(ncx.path)
    entries: List[Tuple[str, str]] = []
    for nav_point in _iter_local(root, "navPoint"):
        content = _first_local(nav_point, "content")
        if content is None:
            continue
        label_root = _first_local(nav_point, "navLabel")
        text_element = _first_local(label_root, "text") if label_root is not None else None
        label = (text_element.text or "").strip() if text_element is not None else ""
        entries.append((_resolve_href(base_dir, content.get("src") or ""), label))
    return entries


def _toc_entries(
    archive: zipfile.ZipFile,
    index: Dict[str, str],
    items: List[_ManifestItem],
    toc_id: str,
) -> List[Tuple[str, str]]:
    for item in items:
        if "nav" in item.properties.split():
            entries = _nav_entries(archive, index, item)
            if entries:
                return entries
    if toc_id:
        for item in items:
            if item.id == toc_id:
                return _ncx_entries(archive, index, item)
    for item in items:
        if item.media_type == _NCX_MEDIA_TYPE or item.path.lower().endswith(".ncx"):
            return _ncx_entries(archive, index, item)
    return []


def _add_image_aliases(aliases: Dict[str, str], original_path: str, image_path: str) -> None:
    normalized = _normalize_epub_path(original_path)
    for alias in {
        original_path,
        normalized,
        unquote(original_path),
        unquote(normalized),
        posixpath.basename(normalized),
    }:
        if alias:
            aliases[alias] = image_path


def _rewrite_image_sources(root: html_markdown.Node, aliases: Dict[str, str], base_path: str) -> None:
    for img in html_markdown.iter_nodes(root, "img"):
        src = (img.attrs.get("src") or "").strip()
        if not src:
            continue
        normalized_src = _normalize_epub_path(src)
        candidates: List[str] = []
        if base_path:
            joined = _normalize_epub_path(posixpath.join(base_path, src))
            candidates.extend([joined, unquote(joined)])
        candidates.extend([
            src,
            normalized_src,
            unquote(src),
            unquote(normalized_src),
            posixpath.basename(normalized_src),
        ])
        for candidate in candidates:
            if candidate in aliases:
                img.attrs["src"] = aliases[candidate]
                break


def _image_members(archive: zipfile.ZipFile, items: List[_ManifestItem]) -> List[Tuple[str, str]]:
    """列出所有图片成员 (zip 内路径, OPF 里的原始 href)，不读内容。"""
    members: List[Tuple[str, str]] = []
    seen: set = set()
    for item in items:
        if item.media_type.startswith(_IMAGE_MEDIA_PREFIX) and item.path and item.path not in seen:
            seen.add(item.path)
            members.append((item.path, item.href))
    # 有些书把图片放进 zip 却不登记进 manifest，按扩展名再扫一遍补齐。
    for name in archive.namelist():
        if name.endswith("/") or name in seen:
            continue
        if name.lower().endswith(_IMAGE_EXTENSIONS):
            seen.add(name)
            members.append((name, name))
    return members


def _collect_images(
    archive: zipfile.ZipFile,
    items: List[_ManifestItem],
    extract_images: bool,
) -> Tuple[Dict[str, str], Dict[str, str], int]:
    """按需抽取图片字节；不抽取时也照样给出图片数量，元数据才不会撒谎。"""
    images: Dict[str, str] = {}
    aliases: Dict[str, str] = {}
    members = _image_members(archive, items)
    if not extract_images:
        return images, aliases, len(members)

    index = _member_index(archive.namelist())
    for path, original in members:
        raw = _read_member(archive, index, path)
        if not raw:
            continue
        extension = posixpath.splitext(path)[1] or ".bin"
        image_path = f"images/{uuid.uuid4().hex}{extension}"
        images[image_path] = base64.b64encode(raw).decode("utf-8")
        _add_image_aliases(aliases, original, image_path)
        _add_image_aliases(aliases, path, image_path)

    return images, aliases, len(images)


def _filename_title(path: str) -> str:
    title = posixpath.basename(path)
    for extension in _HTML_EXTENSIONS:
        if title.lower().endswith(extension):
            title = title[: -len(extension)]
            break
    title = re.sub(r"chapter[_-]?", "Chapter ", title, flags=re.IGNORECASE)
    return title.replace("_", " ").replace("-", " ").title().strip()


def _render_chapter(
    html_text: str,
    title_hint: str,
    aliases: Dict[str, str],
    base_path: str,
) -> str:
    root = html_markdown.parse(html_text)
    html_markdown.unwrap_internal_links(root)
    title = html_markdown.take_first_heading(root)
    _rewrite_image_sources(root, aliases, base_path)
    body = html_markdown.render(root)
    heading = title or title_hint
    if not heading:
        return f"# {_filename_title(base_path)}\n\n{body}".strip()
    return f"# {heading}\n\n{body}".strip()


def _fallback_parse(content: bytes, extract_images: bool) -> Document:
    """container.xml 缺失或 OPF 损坏时，退化成"按文件名排 XHTML"的直读方式。"""
    metadata: Dict[str, Any] = {"source_format": "epub", "file_size": len(content)}
    images: Dict[str, str] = {}
    parts: List[str] = []

    with zipfile.ZipFile(BytesIO(content), "r") as archive:
        index = _member_index(archive.namelist())
        names = [
            name
            for name in archive.namelist()
            if name.lower().endswith(_HTML_EXTENSIONS) and not name.endswith("/")
        ]

        def chapter_number(name: str) -> Tuple[int, str]:
            match = re.search(r"chapter(\d+)", name, re.IGNORECASE)
            return (int(match.group(1)) if match else 999999, name)

        names.sort(key=chapter_number)
        members = _image_members(archive, [])
        image_count = len(members)
        if extract_images:
            for path, _original in members:
                raw = _read_member(archive, index, path)
                if not raw:
                    continue
                extension = posixpath.splitext(path)[1]
                image_path = f"images/{uuid.uuid4().hex}{extension}"
                images[image_path] = base64.b64encode(raw).decode("utf-8")
        for name in names:
            raw = _read_member(archive, index, name)
            if raw is None:
                continue
            chapter = _render_chapter(_decode_xhtml(raw), "", {}, posixpath.dirname(name))
            if chapter.strip():
                parts.append(chapter)

    metadata["chapter_count"] = len(parts)
    metadata["image_count"] = image_count
    return Document(content="\n\n".join(parts), images=images, metadata=metadata)


def parse_epub(content: bytes, extract_images: bool = True) -> Document:
    """把 EPUB 字节解析成 Markdown 正文 + 提取出的图片。"""
    # Check the ZIP directory before any member is expanded. Keep this outside
    # the fallback catch: a size violation must never retry the same archive.
    with zipfile.ZipFile(BytesIO(content), "r") as archive:
        expanded_bytes = 0
        for member in archive.infolist():
            if member.is_dir():
                continue
            if member.file_size > _MAX_MEMBER_UNCOMPRESSED_BYTES:
                raise ValueError("EPUB member exceeds the uncompressed size limit")
            expanded_bytes += member.file_size
            if expanded_bytes > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise ValueError("EPUB exceeds the total uncompressed size limit")
    try:
        return _parse_epub(content, extract_images)
    except (_UnsafeXmlError, html_markdown.HTMLDepthError):
        raise
    except (zipfile.BadZipFile, ElementTree.ParseError, ValueError, KeyError, OSError):
        # OPF/container 不合规的电子书很常见，退化路径比直接报错有用得多。
        return _fallback_parse(content, extract_images)


def _parse_epub(content: bytes, extract_images: bool) -> Document:
    with zipfile.ZipFile(BytesIO(content), "r") as archive:
        index = _member_index(archive.namelist())
        container = _read_member(archive, index, _CONTAINER_PATH)
        opf_path = ""
        if container is not None:
            container_root = _safe_xml_parse(container)
            rootfile = _first_local(container_root, "rootfile")
            if rootfile is not None:
                opf_path = _normalize_epub_path(rootfile.get("full-path") or "")
        if not opf_path:
            opf_path = next(
                (name for name in archive.namelist() if name.lower().endswith(".opf")), ""
            )
        if not opf_path:
            raise ValueError("EPUB has no OPF package document")

        opf_bytes = _read_member(archive, index, opf_path)
        if opf_bytes is None:
            raise ValueError(f"EPUB package document {opf_path} is missing")

        metadata, items, spine_paths, toc_id = _parse_opf(opf_bytes, posixpath.dirname(opf_path))
        images, aliases, image_count = _collect_images(archive, items, extract_images)

        by_path = {item.path: item for item in items}
        nav_paths = {item.path for item in items if "nav" in item.properties.split()}
        toc = _toc_entries(archive, index, items, toc_id)

        ordered: List[Tuple[str, str]] = []
        seen: set = set()
        toc_labels = {path: label for path, label in toc if path and label}
        # OPF spine defines reading order. TOC may omit sections or use a
        # different display order; use it only to supply chapter labels.
        for path in spine_paths:
            if path in seen or path in nav_paths:
                continue
            item = by_path.get(path)
            media_type = item.media_type if item is not None else ""
            if not path.lower().endswith(_HTML_EXTENSIONS) and media_type not in (
                "application/xhtml+xml",
                "text/html",
            ):
                continue
            seen.add(path)
            ordered.append((path, toc_labels.get(path, "")))
        # Keep TOC-only chapters for malformed books with an incomplete spine.
        for path, label in toc:
            if not path or path in seen or path in nav_paths:
                continue
            if not path.lower().endswith(_HTML_EXTENSIONS):
                continue
            seen.add(path)
            ordered.append((path, label))

        parts: List[str] = []
        for path, label in ordered:
            raw = _read_member(archive, index, path)
            if raw is None:
                continue
            chapter = _render_chapter(
                _decode_xhtml(raw), label, aliases, posixpath.dirname(path)
            )
            if chapter.strip():
                parts.append(chapter)

    metadata["source_format"] = "epub"
    metadata["file_size"] = len(content)
    metadata["chapter_count"] = len(parts)
    metadata["image_count"] = image_count
    return Document(content="\n\n".join(parts), images=images, metadata=metadata)
