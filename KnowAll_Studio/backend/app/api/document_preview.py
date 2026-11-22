"""Authenticated original PDF/image reader, independent from parsed text availability."""
import asyncio
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import Document
from app.core.auth import get_optional_user
from app.api.documents import _document_access
from app.core.parsing.document_preview import PreviewError, preview_metadata, render_preview

router = APIRouter(prefix='/api/v1/documents', tags=['documents'])
# Limit queued renderer work as well as native library concurrency per worker.
_preview_slots = asyncio.Semaphore(2)
_renderer = None


async def _preview_document(doc_id, db, user):
    doc = await db.get(Document, doc_id)
    _document_access(doc, user)
    return doc


async def _run(function, *args):
    global _renderer
    try:
        async with _preview_slots:
            if _renderer is None:
                # PyMuPDF cannot share threads with concurrent document parsing.
                # Spawn avoids inheriting the API worker's threads/native state.
                _renderer = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context('spawn'))
            renderer = _renderer
            try:
                return await asyncio.get_running_loop().run_in_executor(renderer, function, *args)
            except BrokenProcessPool as exc:
                if _renderer is renderer:
                    _renderer = None
                    renderer.shutdown(wait=False, cancel_futures=True)
                raise HTTPException(503, '预览服务暂不可用，请重试') from exc
    except PreviewError as exc:
        raise HTTPException(exc.status, exc.message) from exc


@router.get('/{doc_id}/preview')
async def get_original_preview(doc_id: str, db: AsyncSession = Depends(get_db), user=Depends(get_optional_user)):
    doc = await _preview_document(doc_id, db, user)
    return await _run(preview_metadata, doc.local_path, doc.file_type)


@router.get('/{doc_id}/preview/{page_number}/image')
async def get_original_page(doc_id: str, page_number: int, size: int = Query(1400, ge=400, le=2000),
                            source_version: str | None = Query(None, pattern=r'^[a-f0-9]{64}$'),
                            db: AsyncSession = Depends(get_db), user=Depends(get_optional_user)):
    doc = await _preview_document(doc_id, db, user)
    content = await _run(render_preview, doc.local_path, doc.file_type, page_number, size, source_version)
    return Response(content, media_type='image/png', headers={'Cache-Control': 'private, no-store'})
