"""Bounded, one-page original previews; no generated learning content or disk cache."""
import os
import hashlib
import threading
from contextlib import contextmanager
from io import BytesIO
import fitz
from PIL import Image, ImageOps

IMAGE_TYPES = {'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'tif', 'tiff'}
# PyMuPDF does not support concurrent threads. Serialize all work in this module.
_render_lock = threading.Lock()


class PreviewError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message
        # Preserve constructor arguments when crossing the renderer process boundary.
        super().__init__(status, message)


def _validate(path, file_type):
    kind = (file_type or '').lower()
    if kind != 'pdf' and kind not in IMAGE_TYPES:
        raise PreviewError(415, '此格式暂不支持原件预览，请阅读文字')
    if not path or not os.path.isfile(path):
        raise PreviewError(404, '原文件尚未保存或已不可用')
    if os.path.getsize(path) > 100 * 1024 * 1024:
        raise PreviewError(413, '原文件超过100MB，请使用电脑阅读')
    return kind


@contextmanager
def _source(path, file_type):
    with _render_lock:
        kind = _validate(path, file_type)
        try:
            if kind == 'pdf':
                with fitz.open(path) as source:
                    if not source.is_pdf:
                        raise PreviewError(422, '原文件不是有效的PDF')
                    if source.needs_pass:
                        raise PreviewError(409, 'PDF有密码，请先上传解密后的文件')
                    if source.page_count < 1:
                        raise PreviewError(422, '原文件没有可阅读的页面')
                    yield kind, source
            else:
                with Image.open(path) as source:
                    if source.width * source.height > 40_000_000:
                        raise PreviewError(413, '图片尺寸过大，请使用电脑阅读')
                    yield kind, source
        except PreviewError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise PreviewError(413, '图片尺寸过大，请使用电脑阅读') from exc
        except Exception as exc:
            raise PreviewError(422, '原文件损坏或无法预览，请重新上传') from exc


@contextmanager
def _pdf_snapshot(path, file_type):
    with _render_lock:
        if _validate(path, file_type) != 'pdf': raise PreviewError(415, '只有PDF支持原件进度同步')
        try:
            with open(path, 'rb') as file: content = file.read(100 * 1024 * 1024 + 1)
            if len(content) > 100 * 1024 * 1024: raise PreviewError(413, '原文件超过100MB，请使用电脑阅读')
            with fitz.open(stream=content, filetype='pdf') as source:
                if not source.is_pdf or source.page_count < 1: raise PreviewError(422, '原文件不是有效的PDF')
                if source.needs_pass: raise PreviewError(409, 'PDF有密码，请先上传解密后的文件')
                yield source, hashlib.sha256(content).hexdigest()
        except PreviewError: raise
        except Exception as exc: raise PreviewError(422, '原文件损坏或无法预览，请重新上传') from exc

@contextmanager
def _image_snapshot(path, file_type):
    with _render_lock:
        if _validate(path, file_type) not in IMAGE_TYPES: raise PreviewError(415, '只有图片支持图片阅读状态')
        try:
            with open(path, 'rb') as file: content = file.read(100 * 1024 * 1024 + 1)
            if len(content) > 100 * 1024 * 1024: raise PreviewError(413, '原文件超过100MB，请使用电脑阅读')
            with Image.open(BytesIO(content)) as source:
                if source.width * source.height > 40_000_000: raise PreviewError(413, '图片尺寸过大，请使用电脑阅读')
                yield source, hashlib.sha256(content).hexdigest()
        except PreviewError: raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise PreviewError(413, '图片尺寸过大，请使用电脑阅读') from exc
        except Exception as exc: raise PreviewError(422, '原文件损坏或无法预览，请重新上传') from exc

def preview_metadata(path, file_type):
    if (file_type or '').lower() == 'pdf':
        with _pdf_snapshot(path, file_type) as (source, version):
            return {'page_count': source.page_count, 'file_type': 'pdf', 'source_version': version}
    with _image_snapshot(path, file_type) as (_, version):
        return {'page_count': 1, 'file_type': (file_type or '').lower(), 'source_version': version}


def render_preview(path, file_type, page_number, size=1400, source_version=None):
    if not 400 <= size <= 2000:
        raise PreviewError(422, '预览尺寸超出范围')
    if (file_type or '').lower() != 'pdf':
        with _image_snapshot(path, file_type) as (source, version):
            if source_version is not None and source_version != version: raise PreviewError(412, '图片原件已变化，请重新打开资料')
            if page_number != 1: raise PreviewError(404, '图片只支持首帧第1页')
            return _render_image(source, size)
    if source_version is not None:
        with _pdf_snapshot(path, file_type) as (source, version):
            if source_version != version: raise PreviewError(412, 'PDF原件已变化，请重新打开资料')
            return _render_pdf(source, page_number, size)
    with _source(path, file_type) as (kind, source):
        count = source.page_count if kind == 'pdf' else 1
        if not 1 <= page_number <= count:
            raise PreviewError(404, '未找到这一页')
        if kind == 'pdf':
            return _render_pdf(source, page_number, size)
        return _render_image(source, size)

def _render_image(source, size):
    # First frame for animated sources; rotate camera images according to EXIF.
    image = ImageOps.exif_transpose(source)
    image.thumbnail((size, size))
    paper = Image.new('RGB', image.size, 'white')
    if image.mode in ('RGBA', 'LA') or 'transparency' in image.info:
        image = image.convert('RGBA')
        paper.paste(image, mask=image.getchannel('A'))
    else:
        paper.paste(image.convert('RGB'))
    output = BytesIO()
    paper.save(output, format='PNG')
    return output.getvalue()

def _render_pdf(source, page_number, size):
    if not 1 <= page_number <= source.page_count: raise PreviewError(404, '未找到这一页')
    page = source.load_page(page_number - 1)
    longest = max(page.rect.width, page.rect.height)
    if not longest > 0: raise PreviewError(422, '页面尺寸无效')
    pixmap = page.get_pixmap(matrix=fitz.Matrix((size - 1) / longest, (size - 1) / longest), colorspace=fitz.csRGB, alpha=False)
    return pixmap.tobytes('png')
