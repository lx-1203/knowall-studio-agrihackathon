"""MHTML 网页存档解析（移植自 docreader/parser/mhtml_parser.py）。

学生保存网页资料时最常见的就是 .mhtml/.mht（浏览器"网页，单个文件"）。上游这份
实现的关键判断都保留了：用最大的非广告 HTML 分片当正文、8bit UTF-8 头部还原、
按 Content-Location/Content-ID/X-Attachment-Id 三种引用方式重写图片地址。

和上游的差别在 HTML->Markdown 那一步：上游用 bs4 + markdownify，这里换成同目录
纯标准库的 html_markdown，因此在乱码头部或畸形正文上不会再抛异常。
"""
from __future__ import annotations

import base64
import email
import html
import logging
import os
import re
from email.header import decode_header
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urljoin, urlparse
import uuid

from app.core.parsing.docreader import html_markdown
from app.core.parsing.docreader.document import Document

logger = logging.getLogger(__name__)

_AD_DOMAINS = (
    "googleads",
    "doubleclick",
    "googlesyndication",
    "facebook.com/tr",
    "analytics",
    "pixel",
)

_UNKNOWN_8BIT = frozenset({"unknown-8bit", "unknown"})

_IMAGE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/x-icon": ".ico",
}


def _header_str(value) -> str:
    """把 MIME 头部归一化成 str，并还原 8bit 的 UTF-8 原始字节。

    email.message_from_bytes 默认走 compat32，浏览器保存的 .mhtml 经常在
    Content-Location 这类头部里直接塞 UTF-8 字节。这些值会以 charset 为
    unknown-8bit 的 email.header.Header 形式返回：strip()/lower() 会抛
    AttributeError，str(Header) 又把字节换成 U+FFFD，于是图片别名再也匹配不上
    HTML 正文里的引用。
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            return value.encode("ascii", "surrogateescape").decode("utf-8")
        except UnicodeError:
            return value
    try:
        chunks = decode_header(value)
    except (TypeError, ValueError, LookupError, UnicodeError):
        return str(value)

    parts: List[str] = []
    for chunk, charset in chunks:
        if isinstance(chunk, bytes):
            encoding = (charset or "utf-8").lower()
            if encoding in _UNKNOWN_8BIT:
                encoding = "utf-8"
            try:
                parts.append(chunk.decode(encoding, errors="replace"))
            except LookupError:
                parts.append(chunk.decode("utf-8", errors="replace"))
        elif chunk:
            try:
                parts.append(chunk.encode("ascii", "surrogateescape").decode("utf-8"))
            except UnicodeError:
                parts.append(chunk)
    return "".join(parts)


def _image_extension(content_type: str) -> str:
    return _IMAGE_EXTENSIONS.get(content_type, ".png")


def _filename_from_content_location(location: str) -> str:
    decoded = unquote(html.unescape(_header_str(location).strip()))
    if not decoded or decoded.lower().startswith("cid:"):
        return ""
    path = urlparse(decoded).path or decoded
    filename = os.path.basename(path)
    if not filename or filename in {".", ".."}:
        return ""
    if "/" in filename or "\\" in filename:
        return ""
    return filename


def _image_path_for_part(part, content_type: str, images: Dict[str, str]) -> str:
    """Content-Location 能给出文件名时用它，否则退回随机名。

    文件名稳定，契约测试里那份 fixture 就靠它把 images/第 1 页 (测试).gif 钉死；
    重名时递增后缀而不是覆盖。
    """
    extension = _image_extension(content_type)
    location = _header_str(part.get("Content-Location", "")).strip()
    filename = _filename_from_content_location(location)
    if not filename:
        return f"images/{uuid.uuid4().hex}{extension}"

    if not os.path.splitext(filename)[1]:
        filename = f"{filename}{extension}"
    image_path = f"images/{filename}"
    if image_path not in images:
        return image_path

    stem, location_extension = os.path.splitext(filename)
    suffix = 2
    while True:
        candidate = f"images/{stem}_{suffix}{location_extension}"
        if candidate not in images:
            return candidate
        suffix += 1


def _add_image_aliases(image_aliases: Dict[str, str], part, image_path: str) -> None:
    """登记正文里可能用来引用这张图的全部写法。"""
    for raw in (
        part.get("Content-Location", ""),
        part.get("Content-ID", ""),
        part.get("X-Attachment-Id", ""),
    ):
        raw = _header_str(raw).strip()
        if not raw:
            continue
        values = {raw, html.unescape(raw), unquote(html.unescape(raw))}
        cid = raw.strip("<>")
        if cid:
            values.add(f"cid:{cid}")
            values.add(f"cid:{unquote(cid)}")
        for value in values:
            if value:
                image_aliases[value] = image_path


def _select_main_html(html_parts: List[Dict[str, Any]], start_id: str = "") -> Dict[str, Any]:
    """Use the declared MIME root, then the largest non-ad HTML part."""
    if not html_parts:
        return {}
    if start_id:
        for part in html_parts:
            if part["content_id"] == start_id:
                return part

    def is_ad(location: str) -> bool:
        loc = _header_str(location)
        if not loc:
            return False
        loc = loc.lower()
        return any(ad in loc for ad in _AD_DOMAINS)

    non_ad = sorted(
        (part for part in html_parts if not is_ad(part.get("location", ""))),
        key=lambda part: part["size"],
        reverse=True,
    )
    if non_ad:
        return non_ad[0]

    largest = max(html_parts, key=lambda part: part["size"])
    logger.warning("Only ad content found, using largest: %d bytes", largest["size"])
    return largest


def _rewrite_image_sources(
    root: html_markdown.Node, aliases: Dict[str, str], base_location: str = ""
) -> None:
    for img in html_markdown.iter_nodes(root, "img"):
        src = (img.attrs.get("src") or "").strip()
        if not src:
            continue
        candidates = [src, html.unescape(src), unquote(html.unescape(src))]
        if base_location:
            candidates.append(urljoin(base_location, src))
        base_name = os.path.basename(unquote(html.unescape(src)))
        if base_name:
            candidates.append(base_name)
        for candidate in candidates:
            if candidate in aliases:
                img.attrs["src"] = aliases[candidate]
                break


def html_to_markdown(
    html_content: str,
    image_aliases: Optional[Dict[str, str]] = None,
    base_location: str = "",
    *,
    strip_internal_links: bool = True,
    fallback_to_raw_html: bool = True,
) -> str:
    """HTML -> Markdown，并显式给出死链与兜底策略。"""

    def raw_html_fallback() -> str:
        if not fallback_to_raw_html:
            return ""
        return "```html\n" + html_content[:50000] + "\n```"

    root = html_markdown.parse(html_content)
    if strip_internal_links:
        html_markdown.unwrap_internal_links(root)
    if image_aliases:
        _rewrite_image_sources(root, image_aliases, base_location)
    try:
        result = html_markdown.render(root)
    except Exception as exc:  # noqa: BLE001 - 转换失败不能拖垮整个导入
        logger.error("HTML to Markdown conversion failed: %s", exc)
        return raw_html_fallback()
    if not result:
        text_fallback = re.sub(r"\s+", " ", html_markdown.node_text(root)).strip()
        if text_fallback:
            logger.warning("Markdown empty, falling back to text extraction")
            return text_fallback
        return raw_html_fallback()
    return result


def _decode_payload(part) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="ignore")
    except LookupError:
        return payload.decode("utf-8", errors="ignore")


def parse_mhtml(content: bytes, extract_images: bool = True) -> Document:
    """把 MHTML 字节解析成 Markdown 正文 + 提取出的图片。"""
    msg = email.message_from_bytes(content)
    start_id = _header_str(msg.get_param("start") or "").strip().strip("<>")

    html_parts: List[Dict[str, Any]] = []
    images: Dict[str, str] = {}
    image_aliases: Dict[str, str] = {}
    image_count = 0

    for part in msg.walk():
        content_type = part.get_content_type()
        location = _header_str(part.get("Content-Location", ""))

        if content_type == "text/html":
            html_text = _decode_payload(part)
            if not html_text:
                continue
            html_parts.append(
                {
                    "content": html_text,
                    "location": location,
                    "content_id": _header_str(part.get("Content-ID", "")).strip().strip("<>"),
                    "size": len(html_text),
                }
            )
        elif content_type.startswith("image/"):
            # 不抽取时也照样计数：元数据里的 image_count 不该因为开关而撒谎。
            image_count += 1
            if not extract_images:
                continue
            image_data = part.get_payload(decode=True)
            if not image_data:
                continue
            image_path = _image_path_for_part(part, content_type, images)
            images[image_path] = base64.b64encode(image_data).decode("utf-8")
            _add_image_aliases(image_aliases, part, image_path)

    main_html = _select_main_html(html_parts, start_id)
    metadata: Dict[str, Any] = {
        "source_format": "mhtml",
        "file_size": len(content),
        "image_count": image_count,
    }
    if not main_html:
        logger.warning("No HTML content found in MHTML file")
        return Document(content="", images=images, metadata=metadata)

    markdown_text = html_to_markdown(
        main_html["content"],
        image_aliases=image_aliases,
        base_location=main_html.get("location", ""),
    )
    return Document(content=markdown_text, images=images, metadata=metadata)
