"""Document parsing engine - local file parsing, no API calls"""
import asyncio
import hashlib
import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from dataclasses import dataclass, field
from time import perf_counter
from app.config import settings
from app.core.parsing import chunking
from app.core.parsing.page_map import PageMap

logger = logging.getLogger(__name__)

_ocr_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="document-ocr")
MAX_PDF_OCR_PIXELS = 6_000_000
MAX_PDF_OCR_SIDE = 4096
MAX_IMAGE_OCR_PIXELS = 16_000_000


def _pdf_ocr_scale(page_rect) -> float:
    """Keep ordinary pages at 200 DPI while bounding unusually large pages."""
    width = max(float(page_rect.width), 1.0)
    height = max(float(page_rect.height), 1.0)
    return min(200 / 72, math.sqrt(MAX_PDF_OCR_PIXELS / (width * height)),
               MAX_PDF_OCR_SIDE / max(width, height))


def _render_ocr_page(result, table) -> str:
    if table is None:
        return result.text
    _, top, _, _ = table.bounds
    segments = [(top, table.markdown)]
    for line in table.unassigned_lines:
        segments.append((line.box[1], line.text))
    segments.sort(key=lambda segment: segment[0])
    return "\n".join(text for _, text in segments if text.strip())


def _formula_candidate_records(result, image_size, page_number=None) -> list[dict]:
    from app.core.parsing.formula_local import locate_formula_candidates

    records = []
    for candidate in locate_formula_candidates(result.lines, image_size):
        record = {"ocr_text": candidate.ocr_text, "box": list(candidate.box)}
        if page_number is not None:
            record["page"] = page_number
        records.append(record)
    return records


def _diagram_records(image, result, page_number: int) -> tuple[list[dict], list[dict]]:
    from app.core.parsing.diagram_relations import extract_diagram_relations

    graph = extract_diagram_relations(image, result)
    ids = {node.id: f"p{page_number}_{node.id}" for node in graph.nodes}
    nodes = [
        {"id": ids[node.id], "text": node.text, "box": list(node.box), "page": page_number}
        for node in graph.nodes
    ]
    edges = [
        {"source": ids[edge.source], "target": ids[edge.target],
         "relation": edge.relation, "confidence": edge.confidence,
         "evidence": edge.evidence, "page": page_number}
        for edge in graph.edges
    ]
    return nodes, edges


@dataclass
class ParsedDocument:
    """Result of document parsing"""
    text: str
    page_count: int
    metadata: dict = field(default_factory=dict)
    headings: list = field(default_factory=list)   # list[HeadingNode] from docling
    images: list = field(default_factory=list)     # list[ImageItem] from docling
    tables: list = field(default_factory=list)     # list[TableItem] from docling
    # 按阅读顺序的 (条目文本, 页码)。只给 page_map 做偏移→页码对齐用，
    # 不对应任何业务字段；解析器给不出页码时留空即可。
    page_items: list = field(default_factory=list)  # list[tuple[str, int]]
    page_texts: list[str] = field(default_factory=list)  # Docling per-page Markdown, when available


@dataclass
class TextChunk:
    """A single text chunk after splitting"""
    index: int
    text: str
    token_count: int
    content_hash: str
    page_range: str = ""
    # 在源文本中的字符区间。page_map 靠它反解真实页码，也方便调用方做高亮定位。
    start: int = 0
    end: int = 0
    # 标题面包屑原文（"# 第一章\n## 第二节"）。text 里已经并进去了，
    # 这里保留一份原始值给需要单独渲染标题路径的调用方。
    context_header: str = ""


class DocumentParser:
    """Parse various document formats to plain text (100% local)."""

    async def parse(self, file_path: str, file_type: str) -> ParsedDocument:
        if file_type == "pdf":
            from app.core.parsing.scan_pdf import inspect_pdf

            scan_flags, has_digital_text = await asyncio.to_thread(inspect_pdf, file_path)
            if any(scan_flags):
                structured = None
                if has_digital_text and settings.use_docling:
                    try:
                        structured = await self._parse_with_docling(file_path, "pdf")
                    except Exception as exc:
                        logger.warning("Docling structure parse failed on mixed PDF %s: %s", file_path, exc)
                return await self._parse_pdf(file_path, structured, scan_flags)
        # Try Docling first for formats it excels at
        if settings.use_docling and file_type in ("pdf", "docx", "pptx", "html"):
            try:
                return await self._parse_with_docling(file_path, file_type)
            except Exception as e:
                logger.warning("Docling parse failed for %s (%s), falling back: %s",
                               file_type, file_path, e)

        parsers = {
            "pdf": self._parse_pdf,
            "docx": self._parse_docx,
            "pptx": self._parse_pptx,
            "md": self._parse_markdown,
            "markdown": self._parse_markdown,
            "txt": self._parse_text,
            "text": self._parse_text,
            "png": self._parse_image,
            "jpg": self._parse_image,
            "jpeg": self._parse_image,
            "gif": self._parse_image,
            "bmp": self._parse_image,
            "webp": self._parse_image,
            "xlsx": self._parse_xlsx,
            "csv": self._parse_csv,
            "xmind": self._parse_xmind,
            "epub": self._parse_epub,
            "mhtml": self._parse_mhtml,
            "mht": self._parse_mhtml,
            "url": self._parse_url,
            "html": self._parse_html,
            "py": self._parse_code,
            "js": self._parse_code,
            "ts": self._parse_code,
            "jsx": self._parse_code,
            "tsx": self._parse_code,
            "java": self._parse_code,
            "cpp": self._parse_code,
            "c": self._parse_code,
            "h": self._parse_code,
            "go": self._parse_code,
            "rs": self._parse_code,
            "sql": self._parse_code,
            "yaml": self._parse_code,
            "yml": self._parse_code,
            "json": self._parse_code,
            "xml": self._parse_code,
            "css": self._parse_code,
            "html_code": self._parse_code,
        }
        parser = parsers.get(file_type)
        if not parser:
            raise ValueError(f"Unsupported file type: {file_type}. Supported: {list(parsers.keys())}")
        return await parser(file_path)

    async def _parse_pdf(self, file_path: str, structured: ParsedDocument | None = None,
                         scan_flags: list[bool] | None = None) -> ParsedDocument:
        queued_at = perf_counter()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _ocr_executor, self._parse_pdf_sync, file_path, structured, scan_flags, queued_at
        )

    def _parse_pdf_sync(self, file_path: str, structured: ParsedDocument | None = None,
                        scan_flags: list[bool] | None = None,
                        queued_at: float | None = None) -> ParsedDocument:
        import fitz  # PyMuPDF
        import numpy as np
        from PIL import Image
        from app.core.parsing import local_ocr
        from app.core.parsing.scan_pdf import page_needs_ocr, page_has_partial_scan, partial_scan_bbox
        from app.core.parsing.table_grid import recover_table

        texts = []
        page_methods = []
        ocr_seconds = 0.0
        formula_candidates = []
        diagram_nodes = []
        diagram_edges = []
        blank_pages = []
        unrecognized_pages = []
        downsampled_pages = []
        table_retry_truncated_pages = []
        partial_scan_pages = []
        partial_scan_order_uncertain_pages = []
        queue_seconds = max(0.0, perf_counter() - queued_at) if queued_at else 0.0
        with fitz.open(file_path) as doc:
            for page_number, page in enumerate(doc, start=1):
                text = page.get_text()
                needs_ocr = (
                    scan_flags[page_number - 1]
                    if scan_flags is not None and len(scan_flags) >= page_number
                    else page_needs_ocr(page)
                )
                if not needs_ocr:
                    docling_text = (
                        structured.page_texts[page_number - 1]
                        if structured and len(structured.page_texts) >= page_number else ""
                    )
                    texts.append(docling_text or text)
                    page_methods.append("Docling" if docling_text else "PyMuPDF")
                    continue
                partial_scan = page_has_partial_scan(page)
                clip = partial_scan_bbox(page) if partial_scan else page.rect
                if partial_scan:
                    partial_scan_pages.append(page_number)
                scale = _pdf_ocr_scale(page.rect)
                if scale < 200 / 72:
                    downsampled_pages.append(page_number)
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
                origin_x, origin_y = pix.x, pix.y
                image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                del pix
                try:
                    result = local_ocr.recognize(image)
                except Exception as exc:
                    raise ValueError(f"OCR failed on PDF page {page_number}: {exc}") from exc
                ocr_seconds += result.seconds
                table = recover_table(image, result, retry_empty=True)
                if table and table.retry_truncated:
                    table_retry_truncated_pages.append(page_number)
                if table is None:
                    nodes, edges = _diagram_records(image, result, page_number)
                    for node in nodes:
                        node["box"] = [node["box"][0] + origin_x, node["box"][1] + origin_y,
                                       node["box"][2] + origin_x, node["box"][3] + origin_y]
                    diagram_nodes.extend(nodes)
                    diagram_edges.extend(edges)
                scanned_text = _render_ocr_page(result, table)
                if not scanned_text.strip():
                    grayscale = np.asarray(image.convert("L"))
                    if float((grayscale < 200).mean()) >= 0.0002:
                        unrecognized_pages.append(page_number)
                    else:
                        blank_pages.append(page_number)
                if partial_scan:
                    docling_text = (
                        structured.page_texts[page_number - 1]
                        if structured and len(structured.page_texts) >= page_number else ""
                    )
                    digital_text = docling_text or text
                    blocks = [block for block in page.get_text("blocks")
                              if len(block) > 6 and block[6] == 0 and str(block[4]).strip()]
                    first_digital_y = min((block[1] for block in blocks), default=0)
                    last_digital_y = max((block[3] for block in blocks), default=0)
                    if clip.y1 <= first_digital_y:
                        page_text = "\n\n".join(part for part in (scanned_text, digital_text) if part.strip())
                    else:
                        page_text = "\n\n".join(part for part in (digital_text, scanned_text) if part.strip())
                        if clip.y0 < last_digital_y:
                            partial_scan_order_uncertain_pages.append(page_number)
                else:
                    page_text = scanned_text
                texts.append(page_text)
                candidates = _formula_candidate_records(result, image.size, page_number)
                for candidate in candidates:
                    box = candidate["box"]
                    candidate["box"] = [box[0] + origin_x, box[1] + origin_y,
                                        box[2] + origin_x, box[3] + origin_y]
                formula_candidates.extend(candidates)
                page_methods.append(
                    ("Docling+RapidOCR" if docling_text else "PyMuPDF+RapidOCR")
                    if partial_scan else "RapidOCR"
                )
        if not any(text.strip() for text in texts):
            if unrecognized_pages:
                raise ValueError(f"OCR failed on PDF page {unrecognized_pages[0]}: no text recognized")
            raise ValueError("OCR failed: no text recognized in PDF")
        if unrecognized_pages:
            logger.warning("OCR recognized no text on PDF pages %s in %s", unrecognized_pages, file_path)
        return ParsedDocument(
            text="\n\n".join(texts),
            page_count=len(texts),
            metadata={**(structured.metadata if structured else {}),
                      "parser": ("Docling+RapidOCR" if structured else "PyMuPDF+RapidOCR")
                      if any("RapidOCR" in method for method in page_methods) else "PyMuPDF",
                      "page_methods": page_methods, "ocr_seconds": ocr_seconds,
                      "ocr_queue_seconds": queue_seconds,
                      "formula_candidates": formula_candidates,
                      "diagram_nodes": diagram_nodes, "diagram_edges": diagram_edges,
                      "blank_pages": blank_pages,
                      "unrecognized_pages": unrecognized_pages,
                      "downsampled_pages": downsampled_pages,
                      "table_retry_truncated_pages": table_retry_truncated_pages,
                      "partial_scan_pages": partial_scan_pages,
                      "partial_scan_order_uncertain_pages": partial_scan_order_uncertain_pages},
            # PyMuPDF 是按页抽的，页码天然精确：这里给的就是每页的起始文本。
            page_items=[(t, i + 1) for i, t in enumerate(texts) if t.strip()],
            headings=structured.headings if structured else [],
            images=structured.images if structured else [],
            tables=structured.tables if structured else [],
            page_texts=structured.page_texts if structured else [],
        )

    async def _parse_docx(self, file_path: str) -> ParsedDocument:
        import docx
        from docx.opc.exceptions import PackageNotFoundError
        from zipfile import BadZipFile
        try:
            doc = docx.Document(file_path)
        except (PackageNotFoundError, BadZipFile) as exc:
            raise ValueError("无法读取 Word 文件，请确认文件未损坏且为有效的 DOCX 格式。") from exc
        texts = []
        for p in doc.paragraphs:
            text = p.text.strip()
            if not text:
                continue
            # 把 Word 的标题层级转成 Markdown 井号，交给分块引擎识别标题并生成
            # 面包屑。原实现直接丢掉层级，等于把文档结构信息白扔了 —— 分块会
            # 退化成按长度硬切，检索命中后也说不清片段出自哪一节。
            level = self._docx_heading_level(p)
            texts.append(f"{'#' * level} {text}" if level else text)
        return ParsedDocument(
            text="\n\n".join(texts),
            page_count=1,
            metadata={"parser": "python-docx"},
        )

    @staticmethod
    def _docx_heading_level(paragraph) -> int:
        """Word 段落的标题层级，0 表示不是标题。

        python-docx 不暴露层级，只能从样式名里取。中文版 Word 的样式名是
        "标题 2" 而不是 "Heading 2"，所以认两种写法而不是精确匹配。
        """
        name = (getattr(getattr(paragraph, "style", None), "name", "") or "").strip()
        if name.lower() == "title" or name == "标题":
            return 1
        match = re.match(r"^(?:Heading|标题)\s*(\d+)", name, re.IGNORECASE)
        if not match:
            return 0
        return min(6, max(1, int(match.group(1))))

    async def _parse_pptx(self, file_path: str) -> ParsedDocument:
        from pptx import Presentation
        prs = Presentation(file_path)
        texts = []
        for slide in prs.slides:
            slide_texts = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        if para.text.strip():
                            slide_texts.append(para.text.strip())
            if slide_texts:
                texts.append("\n".join(slide_texts))
        return ParsedDocument(
            text="\n\n---\n\n".join(texts),
            page_count=len(texts),
            metadata={"parser": "python-pptx"},
            # PPT 没有"页"，但 slide 序号就是用户心里的页码，且天然精确。
            page_items=[(t, i + 1) for i, t in enumerate(texts) if t.strip()],
        )

    async def _parse_xlsx(self, file_path: str) -> ParsedDocument:
        import openpyxl
        # Uploads use extensionless content hashes. Passing a binary stream
        # lets openpyxl validate the archive rather than reject the filename.
        with open(file_path, "rb") as source:
            return self._read_xlsx(source, openpyxl)

    def _read_xlsx(self, source, openpyxl) -> ParsedDocument:
        wb = openpyxl.load_workbook(source, read_only=True, data_only=True)
        all_texts = []
        try:
            for name in wb.sheetnames:
                ws = wb[name]
                rows = []
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c) for c in row if c is not None]
                    if cells:
                        rows.append("\t".join(cells))
                if rows:
                    all_texts.append(f"## Sheet: {name}\n\n" + "\n".join(rows))
        finally:
            wb.close()
        if not all_texts:
            raise ValueError("Excel file contains no readable data")
        return ParsedDocument(
            text="\n\n".join(all_texts),
            page_count=len(wb.sheetnames),
            metadata={"parser": "openpyxl", "sheet_count": len(wb.sheetnames)},
            # 和 page_count 保持同一口径：Excel 用 sheet 序号当"页"，
            # all_texts 只收有内容的 sheet，所以序号要用它在原表里的位置重新数。
            page_items=[
                (t, i + 1) for i, t in enumerate(all_texts) if t.strip()
            ],
        )

    async def _parse_csv(self, file_path: str) -> ParsedDocument:
        import csv
        # Try UTF-8 first, then GBK (common for Chinese CSV files)
        encodings = ["utf-8", "gbk", "gb2312", "utf-8-sig"]
        text = ""
        parser_used = "csv-utf8"
        for enc in encodings:
            try:
                with open(file_path, "r", encoding=enc, newline="") as f:
                    reader = csv.reader(f)
                    rows = []
                    for row in reader:
                        rows.append("\t".join(row))
                text = "\n".join(rows)
                parser_used = f"csv-{enc}"
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        if not text:
            raise ValueError("CSV parsing failed: could not decode file with any encoding")
        line_count = text.count("\n") + 1
        return ParsedDocument(
            text=text,
            page_count=1,
            metadata={"parser": parser_used, "lines": line_count},
        )

    async def _parse_markdown(self, file_path: str) -> ParsedDocument:
        from app.core.parsing.docreader.markdown_parser import format_tables

        text = Path(file_path).read_text(encoding="utf-8")
        # 先规整表格再交给分块引擎：Word/MarkItDown 产出的畸形表格行（伪空首行、
        # 缺分隔行）会被启发式分块当成普通段落切碎，表结构就丢了。
        text = format_tables(text)
        return ParsedDocument(
            text=text,
            page_count=1,
            metadata={"parser": "builtin"},
        )

    async def _parse_epub(self, file_path: str) -> ParsedDocument:
        """Parse an EPUB e-book (纯标准库 zipfile + xml)。

        章节标题由解析器写成 Markdown 井号（"# 第一章"），分块引擎才能识别层级
        并生成面包屑；图片只计数不解码——项目里目前没有消费 base64 图片的环节，
        为了不白占内存把 extract_images 关掉（解析器本身支持抽取）。
        """
        from app.core.parsing.docreader.epub_parser import parse_epub

        document = parse_epub(Path(file_path).read_bytes(), extract_images=False)
        if not document.content.strip():
            raise ValueError("EPUB 中未解析出正文内容")
        return ParsedDocument(
            text=document.content,
            page_count=1,
            metadata={"parser": "docreader.epub", **document.metadata},
        )

    async def _parse_mhtml(self, file_path: str) -> ParsedDocument:
        """Parse a saved web page (.mhtml/.mht)。

        图片同样只计数不解码，原因见 _parse_epub。
        """
        from app.core.parsing.docreader.mhtml_parser import parse_mhtml

        document = parse_mhtml(Path(file_path).read_bytes(), extract_images=False)
        if not document.content.strip():
            raise ValueError("MHTML 中未找到可用的 HTML 正文")
        return ParsedDocument(
            text=document.content,
            page_count=1,
            metadata={"parser": "docreader.mhtml", **document.metadata},
        )

    async def _parse_text(self, file_path: str) -> ParsedDocument:
        text = Path(file_path).read_text(encoding="utf-8")
        return ParsedDocument(
            text=text,
            page_count=1,
            metadata={"parser": "builtin"},
        )

    async def _parse_image(self, file_path: str) -> ParsedDocument:
        """OCR one image locally and recover a ruled table when present."""
        queued_at = perf_counter()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_ocr_executor, self._parse_image_sync,
                                          file_path, queued_at)

    def _parse_image_sync(self, file_path: str, queued_at: float | None = None) -> ParsedDocument:
        from PIL import Image
        from app.core.parsing import local_ocr
        from app.core.parsing.table_grid import recover_table

        queue_seconds = max(0.0, perf_counter() - queued_at) if queued_at else 0.0
        with Image.open(file_path) as source:
            pixels = source.size[0] * source.size[1]
            if pixels > MAX_IMAGE_OCR_PIXELS:
                raise ValueError(f"image exceeds OCR pixel limit: {pixels} > {MAX_IMAGE_OCR_PIXELS}")
            image = source.convert("RGB")
        result = local_ocr.recognize(image)
        table = recover_table(image, result, retry_empty=True)
        diagram_nodes, diagram_edges = (
            _diagram_records(image, result, 1) if table is None else ([], [])
        )
        text = _render_ocr_page(result, table)
        formula_candidates = _formula_candidate_records(result, image.size)
        if not text.strip():
            raise ValueError("OCR failed: no text recognized in image")

        return ParsedDocument(
            text=text,
            page_count=1,
            metadata={"parser": "RapidOCR", "source": "ocr", "ocr_seconds": result.seconds,
                      "ocr_queue_seconds": queue_seconds,
                      "table_cells": table.cells if table else None,
                      "table_retry_attempts": table.retry_attempts if table else 0,
                      "table_retry_truncated": table.retry_truncated if table else False,
                      "diagram_nodes": diagram_nodes, "diagram_edges": diagram_edges,
                      "formula_candidates": formula_candidates},
            page_items=[(text, 1)],
        )

    async def _parse_xmind(self, file_path: str) -> ParsedDocument:
        """Parse XMind mind-map file."""
        try:
            import xmindparser
            # xmindparser supports both dict and list output
            content = xmindparser.xmind_to_dict(file_path)
            texts = []

            def extract_topics(data, depth=0):
                if isinstance(data, list):
                    for item in data:
                        extract_topics(item, depth)
                elif isinstance(data, dict):
                    title = data.get("title", "")
                    if title:
                        texts.append("#" * min(depth + 1, 6) + " " + title)
                    children = data.get("topics", [])
                    if isinstance(children, list):
                        for child in children:
                            extract_topics(child, depth + 1)

            # Handle different xmindparser output formats
            if isinstance(content, list):
                for item in content:
                    # xmindparser 的常见返回值是 sheet 列表：每个 sheet 的
                    # "topic" 才是根主题，sheet.title 只是工作表名。
                    if isinstance(item, dict) and isinstance(item.get("topic"), dict):
                        extract_topics(item["topic"])
                    else:
                        extract_topics(item)
            elif isinstance(content, dict):
                sheet = content.get("sheet") or content
                root_topic = sheet.get("rootTopic") or sheet.get("topic") or sheet
                extract_topics(root_topic)
            else:
                extract_topics(content)

            text = "\n\n".join(texts) if texts else str(content)
            return ParsedDocument(text=text, page_count=1, metadata={"parser": "xmindparser"})
        except ImportError:
            raise ValueError("XMind parsing requires: pip install xmindparser")

    async def _parse_code(self, file_path: str) -> ParsedDocument:
        """Parse source code files with language tagging."""
        ext = Path(file_path).suffix.lstrip(".")
        lang_map = {
            "py": "Python", "js": "JavaScript", "ts": "TypeScript",
            "jsx": "React JSX", "tsx": "React TSX", "java": "Java",
            "cpp": "C++", "c": "C", "h": "C/C++ Header", "go": "Go",
            "rs": "Rust", "sql": "SQL", "yaml": "YAML", "yml": "YAML",
            "json": "JSON", "xml": "XML", "css": "CSS",
        }
        lang = lang_map.get(ext, ext.upper())
        text = Path(file_path).read_text(encoding="utf-8", errors="replace")
        # Wrap code with language tag for better LLM understanding
        text = f"以下为{lang}源代码文件:\n\n```{ext}\n{text}\n```"
        return ParsedDocument(text=text, page_count=1, metadata={"parser": "code", "language": lang})

    async def _parse_url(self, file_path: str) -> ParsedDocument:
        """Fetch a page through the pinned, redirect-aware SSRF guard."""
        import asyncio
        from app.core.parsing.docreader.web_parser import fetch_web_page

        document = await asyncio.to_thread(fetch_web_page, file_path)
        return ParsedDocument(
            text=document.content,
            page_count=1,
            metadata={"parser": "docreader.web", "source": file_path[:200], **document.metadata},
        )

    async def _parse_html(self, file_path: str) -> ParsedDocument:
        """Parse a local HTML file."""
        text = Path(file_path).read_text(encoding="utf-8", errors="replace")
        import re
        text = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return ParsedDocument(text=text, page_count=1, metadata={"parser": "html"})

    async def _parse_with_docling(self, file_path: str, file_type: str) -> ParsedDocument:
        """Parse using Docling for structure-aware extraction."""
        from app.core.parsing.docling_parser import docling_parser as dl_parser
        structured = await dl_parser.parse(file_path, file_type)
        return ParsedDocument(
            text=structured.text,
            page_count=structured.page_count,
            metadata=structured.metadata,
            headings=structured.headings,
            images=structured.images,
            tables=structured.tables,
            page_items=structured.page_items,
            page_texts=structured.page_texts,
        )


class TextCleaner:
    """Clean extracted text: remove headers, footers, watermarks, ads, redundant whitespace."""

    # Common watermark patterns
    WATERMARK_PATTERNS = [
        r"版权所有.*?翻印必究",
        r"Copyright.*?All rights reserved",
        r"仅供学习交流",
        r"扫描二维码.*",
    ]

    def clean(self, text: str) -> str:
        """Apply all cleaning steps."""
        text = self._remove_watermarks(text)
        text = self._normalize_whitespace(text)
        text = self._remove_redundant_blank_lines(text)
        text = self._remove_short_lines(text, min_chars=3)
        return text.strip()

    def _remove_watermarks(self, text: str) -> str:
        for pattern in self.WATERMARK_PATTERNS:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE)
        return text

    def _normalize_whitespace(self, text: str) -> str:
        # Replace multiple spaces/newlines
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text

    def _remove_redundant_blank_lines(self, text: str) -> str:
        return re.sub(r"\n\s*\n\s*\n", "\n\n", text)

    def _remove_short_lines(self, text: str, min_chars: int = 3) -> str:
        """Remove lines that are too short (page numbers, standalone symbols).

        Preserves heading lines even if they're short (e.g. "pH", "DNA", "1. 概述").
        """
        lines = text.split("\n")
        cleaned = []
        for line in lines:
            stripped = line.strip()
            # Keep if long enough
            if len(stripped) >= min_chars:
                cleaned.append(line)
                continue
            # Keep if it looks like a heading or numbered item
            if re.match(r"^[#]", stripped):     # Markdown heading #
                cleaned.append(line)
                continue
            if re.match(r"^[\d]+[\.\)、]\s", stripped):  # Numbered item (1. 1) 1、)
                cleaned.append(line)
                continue
            if re.match(r"^[IVX]+[\.\)、]", stripped):   # Roman numeral
                cleaned.append(line)
                continue
            if re.match(r"^[一二三四五六七八九十]、", stripped):  # Chinese numbered
                cleaned.append(line)
                continue
            if re.match(r"^（[一二三四五六七八九十\d]）", stripped):  # (一) (1)
                cleaned.append(line)
                continue
            # Drop line - too short and doesn't look like a heading/list item
        return "\n".join(cleaned)


class TextSplitter:
    """把长文档切成适合 LLM 上下文的 chunk。

    真正的分块交给 app.core.parsing.chunking（WeKnora chunker 的移植版）。这一层
    只负责把项目配置翻译过去，并补上原实现缺的三件事：

    1. **真实页码**。以前 page_range 是按 chunk 序号在总页数上线性插值出来的，
       页码和内容毫无关系；现在用 chunk 的字符区间配合 PageMap 反解，判断不出来
       就留空 —— 编一个页码比不显示更糟。
    2. **标题面包屑**。每块都带上"第一章 > 第二节"的归属，并并进 chunk 文本。
       少了它，检索命中的片段就是一坨没有出处的孤立文字，embedding 也丢掉了层级。
    3. **结构保护**。预算内的表格、代码块、公式保持完整；超出预算时
       优先保证下游 embedding 能处理。

    为什么用字符而不是 token 当预算：引擎内部按字符计（WeKnora 的做法），再由
    chars_for_token_limit 把 settings 里的 token 目标换算成字符预算。这样既保住
    了"每块约 2000 token"的原配置语义，又不用引入 tokenizer 依赖。
    """

    def __init__(
        self,
        chunk_size_tokens: int | None = None,
        overlap_ratio: float | None = None,
        strategy: str | None = None,
    ):
        self.chunk_size_tokens = chunk_size_tokens or settings.chunk_size_tokens
        self.overlap_ratio = overlap_ratio or settings.chunk_overlap_ratio
        # "auto" 由文档画像选策略；显式传 "heading" / "heuristic" / "legacy"
        # 可以绕开画像，调试分块质量时用得上。
        self.strategy = strategy or chunking.STRATEGY_AUTO

    def split(
        self,
        text: str,
        total_pages: int = 1,
        page_map: PageMap | None = None,
    ) -> list[TextChunk]:
        """把 text 切成带页码和标题归属的 chunk。

        page_map 必须是用**同一份 text** 建出来的（也就是清洗之后的正文），
        否则偏移对不上，页码会整体错位。
        """
        if not text or not text.strip():
            return []

        raw = chunking.split(text, self._build_config(text))
        chunks: list[TextChunk] = []
        for i, chunk in enumerate(raw):
            body = self._merge_context_header(chunk.context_header, chunk.content)
            chunks.append(TextChunk(
                index=i,
                text=body,
                token_count=self._estimate_tokens(body),
                content_hash=self._hash_text(body),
                page_range=self._resolve_page_range(
                    page_map, chunk.start, chunk.end, total_pages
                ),
                start=chunk.start,
                end=chunk.end,
                context_header=chunk.context_header,
            ))
        return chunks

    def _build_config(self, text: str) -> chunking.SplitterConfig:
        """把项目配置翻译成引擎配置。

        语言按文档实际语种传：它同时决定 chars_for_token_limit 的换算比例
        （中文 1.7 字符/token，英文 4.0）和启发式分块用哪套句子分隔符。
        """
        lang = chunking.detect_language(text)
        budget = chunking.chars_for_token_limit(self.chunk_size_tokens, lang)
        return chunking.SplitterConfig(
            chunk_size=budget,
            chunk_overlap=max(1, int(budget * self.overlap_ratio)),
            strategy=self.strategy,
            languages=[lang],
            token_limit=self.chunk_size_tokens,
        )

    @staticmethod
    def _merge_context_header(context_header: str, content: str) -> str:
        """把标题面包屑并进 chunk 文本，正文里已有的标题行不重复加。

        去重必须按行判，不能只比最后一行：分块引擎会把标题行留在它所辖各段的
        开头，于是正文开头可能已经带着整条路径（"# 第一章" 紧跟 "## 1.1"），
        也可能只带着末级标题。逐行剔除后，真正缺的父级标题才补进去 ——
        既不会重复，也不会把父级上下文丢掉。
        """
        lines = [ln for ln in (context_header or "").splitlines() if ln.strip()]
        if not lines:
            return content

        # 正文开头的连续标题行，就是与面包屑重叠的那一批
        already: set[str] = set()
        for line in content.lstrip().splitlines():
            if not line.lstrip().startswith("#"):
                break
            already.add(line.strip())

        missing = [ln for ln in lines if ln.strip() not in already]
        if not missing:
            return content
        return "\n".join(missing) + "\n\n" + content

    @staticmethod
    def _resolve_page_range(
        page_map: PageMap | None,
        start: int,
        end: int,
        total_pages: int,
    ) -> str:
        """把字符区间反解成 "p3" / "p3-p5"。

        反解不出来就不编：单页文档退化成 "p1"（本来就只有一页，说 p1 是实话），
        多页文档留空，让前端改显示文件名。
        """
        if page_map:
            resolved = page_map.page_range(start, end)
            if resolved:
                return resolved
        return "p1" if total_pages <= 1 else ""

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """按语种估算 token 数（引擎的保守估算，不引 tokenizer 依赖）。"""
        return chunking.approx_token_count(text, chunking.detect_language(text))

    @staticmethod
    def _hash_text(text: str) -> str:
        normalized = "".join(text.split()).lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# Singleton instances
parser = DocumentParser()
cleaner = TextCleaner()
splitter = TextSplitter()
