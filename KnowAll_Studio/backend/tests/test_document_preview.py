"""Original-file previews use real bytes and the existing document ownership boundary."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from io import BytesIO
import os
import fitz
from PIL import Image, ImageChops
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def original_pdf(tmp_path):
    path = tmp_path / '原件.pdf'
    with fitz.open() as pdf:
        page = pdf.new_page(width=595, height=842)
        page.insert_text((40, 60), '细胞与能量', fontname='china-s', fontsize=24)
        page.insert_text((40, 120), 'E = mc', fontsize=24)
        page.insert_text((118, 108), '2', fontsize=14)
        page.draw_circle((170, 260), 65, color=(0, .5, .4), fill=(.8, 1, .9))
        pdf.new_page(width=10000, height=20000).insert_text((50, 60), '第二页', fontname='china-s')
        pdf.save(path)
    return path


@pytest.fixture
async def preview_client(original_pdf):
    from app.api import document_preview as api
    from app.database import get_db
    from app.core.auth import get_optional_user
    doc = SimpleNamespace(id='private', file_type='pdf', local_path=str(original_pdf), metadata_={'knowledge_owner_id': 'alice'})
    db = SimpleNamespace(get=AsyncMock(return_value=doc))
    account = {'id': 'alice'}
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_optional_user] = lambda: SimpleNamespace(id=account['id']) if account['id'] else None
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        yield SimpleNamespace(client=client, doc=doc, account=account)


async def test_pdf_metadata_and_real_chinese_formula_image(preview_client):
    meta = await preview_client.client.get('/api/v1/documents/private/preview')
    import hashlib
    with open(preview_client.doc.local_path,'rb') as file: version = hashlib.sha256(file.read()).hexdigest()
    assert meta.json() == {'page_count': 2, 'file_type': 'pdf', 'source_version': version}
    response = await preview_client.client.get('/api/v1/documents/private/preview/1/image', params={'size': 1400})
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.headers['content-type'] == 'image/png'
    image = Image.open(BytesIO(response.content))
    assert max(image.size) <= 1400
    assert image.getbbox() is not None
    # Chinese title, equation and illustration each occupy a nonwhite source region.
    ratio = image.width / 595
    for box in [(35, 30, 300, 70), (35, 85, 150, 130), (95, 180, 250, 340)]:
        crop = image.crop(tuple(round(x * ratio) for x in box)).convert('RGB')
        assert ImageChops.difference(crop, Image.new('RGB', crop.size, 'white')).getbbox() is not None


@pytest.mark.parametrize('account', [None, 'bob'])
@pytest.mark.parametrize('suffix', ['', '/1/image'])
async def test_private_preview_denies_foreign_and_anonymous(preview_client, account, suffix):
    preview_client.account['id'] = account
    response = await preview_client.client.get('/api/v1/documents/private/preview' + suffix)
    assert response.status_code == 404


async def test_page_bounds_and_render_size(preview_client):
    for page in [0, 3]:
        response = await preview_client.client.get(f'/api/v1/documents/private/preview/{page}/image')
        assert response.status_code == 404
    for size in [0, 2001]:
        assert (await preview_client.client.get('/api/v1/documents/private/preview/1/image', params={'size': size})).status_code == 422
    image = await preview_client.client.get('/api/v1/documents/private/preview/2/image', params={'size': 400})
    assert max(Image.open(BytesIO(image.content)).size) <= 400


async def test_missing_unsupported_and_broken_original(preview_client, tmp_path):
    doc = preview_client.doc
    doc.local_path = str(tmp_path / 'missing.pdf')
    assert (await preview_client.client.get('/api/v1/documents/private/preview')).status_code == 404
    doc.local_path = str(tmp_path / 'broken.pdf')
    (tmp_path / 'broken.pdf').write_text('not a pdf')
    assert (await preview_client.client.get('/api/v1/documents/private/preview')).status_code == 422
    doc.file_type = 'md'
    assert (await preview_client.client.get('/api/v1/documents/private/preview')).status_code == 415


async def test_password_pdf_does_not_prompt_or_leak(preview_client, original_pdf, tmp_path):
    with fitz.open(original_pdf) as source:
        target = tmp_path / 'locked.pdf'
        source.save(target, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='secret')
    preview_client.doc.local_path = str(target)
    assert (await preview_client.client.get('/api/v1/documents/private/preview')).status_code == 409


async def test_source_image_has_one_page_and_bounded_preview(preview_client, tmp_path):
    source = tmp_path / '细胞.png'
    Image.new('RGB', (2400, 1600), color=(0, 160, 120)).save(source)
    preview_client.doc.local_path = str(source)
    preview_client.doc.file_type = 'png'
    assert (await preview_client.client.get('/api/v1/documents/private/preview')).json()['page_count'] == 1
    response = await preview_client.client.get('/api/v1/documents/private/preview/1/image', params={'size': 1000})
    assert Image.open(BytesIO(response.content)).size == (1000, 667)


def test_source_size_and_image_pixel_limits(original_pdf, monkeypatch, tmp_path):
    from app.core.parsing.document_preview import PreviewError, preview_metadata
    monkeypatch.setattr('app.core.parsing.document_preview.os.path.getsize', lambda _: 101 * 1024 * 1024)
    with pytest.raises(PreviewError) as exc:
        preview_metadata(str(original_pdf), 'pdf')
    assert exc.value.status == 413


    monkeypatch.undo()
    path = tmp_path / 'large.png'
    Image.new('1', (9000, 5000)).save(path)
    with pytest.raises(PreviewError) as exc:
        preview_metadata(str(path), 'png')
    assert exc.value.status == 413


async def test_preview_rendering_is_isolated_from_other_pdf_parsing_threads():
    from app.api.document_preview import _run
    assert await _run(os.getpid) != os.getpid()


async def test_failed_renderer_process_is_replaced_on_retry():
    from app.api.document_preview import _run
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await _run(os._exit, 1)  # Only the disposable rendering worker exits.
    assert exc.value.status_code == 503
    assert await _run(os.getpid) != os.getpid()
