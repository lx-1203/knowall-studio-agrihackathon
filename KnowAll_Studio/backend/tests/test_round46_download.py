"""Native FileResponse late-stat faults through actual authenticated routes."""
from pathlib import Path
import os
import pytest
from sqlalchemy import select, func
from app.models.kaf import KafGrant
from tests.test_round43_kaf_recovery import real_kaf_http, account_app, private_kaf


@pytest.mark.parametrize('fault,status', [('missing',404), ('permission',503), ('storage',503)])
async def test_deferred_stat_fault_is_safe_and_recovers(real_kaf_http, monkeypatch, fault, status):
    app, client, headers, document, _, _ = real_kaf_http
    original = os.stat
    target = Path(document.storage_path)
    def failed(path, *args, **kwargs):
        # Path.exists in the old route accepts Path; FileResponse's deferred
        # stat receives str. This recreates loss between the two checks.
        if isinstance(path, str) and Path(path) == target:
            error = FileNotFoundError if fault=='missing' else PermissionError if fault=='permission' else OSError
            raise error('private-round46-storage-path')
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'stat', failed)
        response = await client.get(f'/api/v1/kaf/files/{document.file_id}/download', headers=headers)
    assert response.status_code == status
    assert 'private-round46' not in response.text and str(target) not in response.text
    if status==503: assert response.headers['retry-after']=='5'
    async with app.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(KafGrant)) == 0
    recovered = await client.get(f'/api/v1/kaf/files/{document.file_id}/download', headers=headers)
    assert recovered.status_code == 200 and recovered.content == target.read_bytes()


async def test_native_ranges_remain_supported(real_kaf_http):
    _, client, headers, document, _, _ = real_kaf_http
    original = Path(document.storage_path).read_bytes()
    url = f'/api/v1/kaf/files/{document.file_id}/download'
    response = await client.get(url, headers={**headers, 'Range':'bytes=2-11'})
    assert response.status_code==206 and response.content==original[2:12]
    assert response.headers['content-range']==f'bytes 2-11/{len(original)}'
    assert response.headers['accept-ranges']=='bytes'
    assert '.kaf' in response.headers['content-disposition']
    bad = await client.get(url, headers={**headers, 'Range':f'bytes={len(original)+2}-'})
    assert bad.status_code==416 and bad.headers['content-range']==f'bytes */{len(original)}'
    multiple = await client.get(url, headers={**headers, 'Range':'bytes=0-2,10-12'})
    assert multiple.status_code==206 and 'multipart/byteranges' in multiple.headers['content-type']
    assert original[0:3] in multiple.content and original[10:13] in multiple.content
    unchanged = await client.get(url, headers={**headers, 'Range':'bytes=2-11', 'If-Range':'"different-etag"'})
    assert unchanged.status_code==200 and unchanged.content==original


async def test_native_head_and_after_start_fault_do_not_send_second_response(tmp_path, monkeypatch):
    import anyio
    from tests.test_product_auth import load_account_router
    response_type = load_account_router('kaf')._RecoverableKAFFileResponse
    file = tmp_path/'direct46.kaf'; file.write_bytes(b'original-native-head46')
    scope = {'type':'http', 'method':'HEAD', 'headers':[]}
    events = []
    async def send(message): events.append(message)
    async def receive(): return {'type':'http.request', 'body':b''}
    await response_type(str(file))(scope, receive, send)
    assert events[0]['status']==200 and events[1]['body']==b''
    events.clear(); scope['method']='GET'
    async def denied(*args, **kwargs): raise PermissionError('after-start46')
    with monkeypatch.context() as patch:
        patch.setattr(anyio, 'open_file', denied)
        with pytest.raises(PermissionError, match='after-start46'):
            await response_type(str(file))(scope, receive, send)
    assert len(events)==1 and events[0]['status']==200
