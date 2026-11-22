"""解析结果载体（移植自 docreader/models/document.py）。

上游的 Document 是 Pydantic 模型，这里只要字段语义一致：正文 Markdown、
图片（路径 -> base64）、元数据。各 parser 返回它，再由 DocumentParser 转成
项目自己的 ParsedDocument。
"""
from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class Document:
    content: str = ""
    images: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
