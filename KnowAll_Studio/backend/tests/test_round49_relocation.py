"""Current content root and cold grant identity using real JWT/SQLite/KAF bytes."""
import hashlib
import shutil
from pathlib import Path
import pytest
from sqlalchemy import select, func
from app.core import kaf_service
from app.models.kaf import KafFile, KafGrant
from tests.test_round43_kaf_recovery import real_kaf_http, account_app, private_kaf


async def test_relocated_root_downloads_same_authorized_bytes(real_kaf_http, tmp_path, monkeypatch):
    app, client, headers, doc, expected, service = real_kaf_http
    old=Path(doc.storage_path); original=old.read_bytes()
    moved=tmp_path/'relocated'/'kaf'; shutil.copytree(kaf_service.KAF_DIR,moved)
    shutil.rmtree(kaf_service.KAF_DIR)
    monkeypatch.setattr(kaf_service,'KAF_DIR',moved)
    # Keep the stored old publisher path, as a restored database actually does.
    async with app.sessions() as db:
        assert (await db.scalar(select(KafFile))).storage_path==str(old)
    grant=await client.post(f'/api/v1/kaf/files/{doc.file_id}/grant',headers=headers)
    assert grant.status_code==200 and grant.json()['content_key_hex']==expected
    response=await client.get(f'/api/v1/kaf/files/{doc.file_id}/download',headers=headers)
    assert response.status_code==200 and response.content==original
    ranged=await client.get(f'/api/v1/kaf/files/{doc.file_id}/download',headers={**headers,'Range':'bytes=0-31'})
    assert ranged.status_code==206 and ranged.content==original[:32]


async def test_download_does_not_follow_foreign_database_path(real_kaf_http,tmp_path):
    app,client,headers,doc,_,_=real_kaf_http
    original=Path(doc.storage_path).read_bytes(); foreign=tmp_path/'another-account.kaf'
    foreign.write_bytes(b'foreign private bytes49')
    async with app.sessions() as db:
        stored=await db.scalar(select(KafFile)); stored.storage_path=str(foreign); await db.commit()
    response=await client.get(f'/api/v1/kaf/files/{doc.file_id}/download',headers=headers)
    assert response.status_code==200 and response.content==original


@pytest.mark.parametrize('fault',['foreign_identity','changed_bytes','same_id_other_package','foreign_identity_matching_digest','empty_digest'])
async def test_cold_grant_rejects_swapped_or_changed_content_then_recovers(real_kaf_http,fault):
    app,client,headers,doc,expected,service=real_kaf_http
    path=Path(doc.storage_path); original=path.read_bytes()
    if fault=='empty_digest': replacement=original
    elif fault=='changed_bytes':
        data=bytearray(original); data[-1]^=1; replacement=bytes(data)
    else:
        # Exclusive publication protects existing identity; produce then replace.
        if fault=='same_id_other_package': path.unlink()
        other=service.publish_from_markdown('# Another49\n\nDifferent private lesson','other49',
                 file_id=doc.file_id if fault=='same_id_other_package' else None)
        replacement=Path(other.storage_path).read_bytes()
    path.write_bytes(replacement)
    if fault in {'foreign_identity_matching_digest','empty_digest'}:
        async with app.sessions() as db:
            stored=await db.scalar(select(KafFile)); stored.sha256='' if fault=='empty_digest' else hashlib.sha256(replacement).hexdigest(); await db.commit()
    response=await client.post(f'/api/v1/kaf/files/{doc.file_id}/grant',headers=headers)
    assert response.status_code==404 and 'content_key_hex' not in response.text
    assert service._grant_cache=={}
    async with app.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(KafGrant))==0
    path.write_bytes(original)
    if fault in {'foreign_identity_matching_digest','empty_digest'}:
        async with app.sessions() as db:
            stored=await db.scalar(select(KafFile)); stored.sha256=hashlib.sha256(original).hexdigest(); await db.commit()
    restored=await client.post(f'/api/v1/kaf/files/{doc.file_id}/grant',headers=headers)
    assert restored.status_code==200 and restored.json()['content_key_hex']==expected


@pytest.mark.parametrize('file_id',['../private','..\\private','C:\\private','/private','.', '..'])
def test_publication_location_accepts_only_single_portable_identity(file_id):
    with pytest.raises(LookupError): kaf_service.publication_path(file_id)


async def test_denial_precedes_path_resolution(real_kaf_http,monkeypatch):
    app,client,headers,doc,_,service=real_kaf_http
    async with app.sessions() as db:
        stored=await db.scalar(select(KafFile)); stored.enabled_tiers='enterprise'; await db.commit()
    def forbidden(file_id): raise AssertionError('denied path was touched')
    monkeypatch.setattr(kaf_service,'publication_path',forbidden)
    # The real router imported the function; patch its own imported binding too.
    for route in app.app.routes:
        if getattr(route,'path','')==f'/api/v1/kaf/files/{{file_id}}/download':
            monkeypatch.setitem(route.endpoint.__globals__,'publication_path',forbidden)
    for method,route in [('get','download'),('post','grant')]:
        response=await getattr(client,method)(f'/api/v1/kaf/files/{doc.file_id}/{route}',headers=headers)
        assert response.status_code==403
