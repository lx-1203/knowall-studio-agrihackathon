"""WeKnora docreader 移植子包（纯 Python、零新依赖）。

EPUB/MHTML/Markdown/SSRF 参考上游 WeKnora 的 docreader 模块；网页抓取器按
本项目的 http.client 链路实现，见各模块头部说明。
本包只做"解析与安全判定"，不连 DB、不写业务目录、不引入新依赖。
"""
from app.core.parsing.docreader.document import Document

__all__ = ["Document"]
