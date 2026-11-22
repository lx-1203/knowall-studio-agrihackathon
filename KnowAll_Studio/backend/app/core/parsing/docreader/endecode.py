"""编解码工具（移植自 docreader/utils/endecode.py）。

PIL 是 requirements.txt 里已有的依赖，numpy 只是在用到 ndarray 时才延迟导入，
所以导入本模块本身永远不会因为缺包失败。
"""
from __future__ import annotations

import base64
import io
import logging
from typing import List, Union

logger = logging.getLogger(__name__)


def decode_image(image) -> str:
    """把图片转成 base64 字符串，支持文件路径 / bytes / PIL Image / ndarray。"""
    if isinstance(image, str):
        with open(image, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode()

    if isinstance(image, bytes):
        return base64.b64encode(image).decode()

    try:
        from PIL import Image
    except ImportError:
        Image = None  # type: ignore[assignment]

    if Image is not None and isinstance(image, Image.Image):
        buffer = io.BytesIO()
        # 没有原始格式信息时统一落 PNG，避免 save() 对未知扩展名报错。
        image.save(buffer, format=image.format if image.format else "PNG")
        return base64.b64encode(buffer.getvalue()).decode()

    if type(image).__name__ == "ndarray" and hasattr(image, "shape"):
        import numpy as np
        from PIL import Image as PILImage

        buffer = io.BytesIO()
        PILImage.fromarray(np.asarray(image)).save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode()

    raise ValueError(f"Unsupported image type: {type(image)}")


def encode_image(image: str, errors: str = "strict") -> bytes:
    """把 base64 字符串解回 bytes。

    errors="ignore" 时解不出来返回 b""：非 ASCII 的 str 会在解码器启动前就抛
    普通 ValueError（不是 binascii.Error），只捕 binascii.Error 会让"不要抛"的
    调用方照样收到异常。
    """
    try:
        return base64.b64decode(image)
    except ValueError as e:
        if errors == "ignore":
            return b""
        raise e


def encode_bytes(content: str) -> bytes:
    return content.encode()


def decode_bytes(
    content: bytes,
    encodings: List[str] = [
        "utf-8",
        "gb18030",
        "gb2312",
        "gbk",
        "big5",
        "ascii",
        "latin-1",
    ],
) -> str:
    """按候选编码依次尝试，全失败则用 latin-1 兜底（可能出替换字符）。"""
    for encoding in encodings:
        try:
            text = content.decode(encoding)
            logger.debug(f"Decode content with {encoding}: {len(text)} characters")
            return text
        except UnicodeDecodeError:
            continue

    text = content.decode(encoding="latin-1", errors="replace")
    logger.warning(
        "Unable to determine correct encoding, using latin-1 as fallback. "
        "This may cause character issues."
    )
    return text
