"""Actual key failures through authenticated KAF HTTP routes, then restoration."""
from pathlib import Path

import httpx
import pytest
from filelock import FileLock
from sqlalchemy import func, select

from app.core import crypto, kaf_service
from app.models.kaf import KafFile, KafGrant
from app.models.subscription import UserTier
from app.models.user import User
from tests.test_product_auth import account_app, bearer, load_account_router, register
from tests.test_round41_key_recovery import private_kaf


@pytest.fixture
async def real_kaf_http(account_app, private_kaf, tmp_path, monkeypatch):
    monkeypatch.setattr(kaf_service, 'KAF_DIR', tmp_path / 'documents')
    owner = await register(account_app)
    first = kaf_service.KAFService()
    document = first.publish_from_markdown('# Recovery43\n\nOriginal lesson', 'recovery43')
    expected = first._unwrap_content_key(Path(document.storage_path).read_bytes()).hex()
    async with account_app.sessions() as db:
        async with db.bind.begin() as conn:
            for table in (KafFile.__table__, KafGrant.__table__, UserTier.__table__):
                await conn.run_sync(table.create)
        user = await db.get(User, owner['user']['id'])
        user.is_admin = True
        db.add(document)
        await db.commit()
    fresh = kaf_service.KAFService()
    api = load_account_router('kaf')
    monkeypatch.setattr(api, 'get_kaf_service', lambda: fresh)
    account_app.app.include_router(api.router)
    # Observe actual HTTP500 rather than httpx rethrowing a server exception.
    transport = httpx.ASGITransport(app=account_app.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url='http://localhost') as client:
        yield account_app, client, bearer(owner['access_token']), document, expected, fresh


@pytest.mark.parametrize('route', ['grant', 'publish'])
@pytest.mark.parametrize('fault', ['missing_fernet', 'missing_private', 'bad_private',
                                 'bad_public', 'permission', 'lock_timeout',
                                 'mismatched_public', 'replaced_fernet'])
async def test_key_failure_safe503_no_writes_and_same_identity_recovers(real_kaf_http, monkeypatch, route, fault):
    app, client, headers, document, expected, fresh = real_kaf_http
    originals = {p: p.read_bytes() for p in (crypto._KEY_FILE, kaf_service._PRIV_KEY_FILE,
                                            kaf_service._PUB_KEY_FILE)}
    original_read = Path.read_bytes
    async def request():
        if route == 'grant':
            return await client.post(f'/api/v1/kaf/files/{document.file_id}/grant', headers=headers)
        return await client.post('/api/v1/kaf/admin/publish', headers=headers,
                                 json={'markdown': '# Restored43\n\nNew lesson', 'title': 'Restored43'})
    with monkeypatch.context() as patch:
        if fault.startswith('missing_'):
            (crypto._KEY_FILE if fault == 'missing_fernet' else kaf_service._PRIV_KEY_FILE).unlink()
        elif fault in ('bad_private', 'bad_public'):
            (kaf_service._PRIV_KEY_FILE if fault == 'bad_private' else kaf_service._PUB_KEY_FILE).write_bytes(b'broken43')
        elif fault == 'mismatched_public':
            from kaf.keys import generate_keypair
            from cryptography.hazmat.primitives import serialization
            _, other = generate_keypair(4096)
            kaf_service._PUB_KEY_FILE.write_bytes(other.public_bytes(serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo))
        elif fault == 'replaced_fernet':
            from cryptography.fernet import Fernet
            crypto._KEY_FILE.write_bytes(Fernet.generate_key())
        elif fault == 'permission':
            read = Path.read_bytes
            def denied(path):
                if path == kaf_service._PRIV_KEY_FILE:
                    raise PermissionError('sensitive43-path-and-secret')
                return read(path)
            patch.setattr(Path, 'read_bytes', denied)
        lock = FileLock(str(kaf_service._KEY_DIR / 'account_keypair.lock'))
        if fault == 'lock_timeout':
            import filelock
            patch.setattr(filelock, 'FileLock', lambda path, timeout: FileLock(path, timeout=.03))
            lock.acquire()
        try:
            modified = {p: original_read(p) if p.exists() else None for p in originals}
            response = await request()
            assert response.status_code == 503, response.text
            assert response.headers['retry-after'] == '5'
            assert response.json() == {'detail': '资料密钥服务暂不可用，请稍后重试或联系管理员恢复原密钥'}
            assert 'sensitive43' not in response.text and str(kaf_service._KEY_DIR) not in response.text
            assert fresh._private_key is None and fresh._public_key is None
            # A failed read must not replace or recreate even the missing bytes.
            for path, original in originals.items():
                if fault == 'missing_fernet' and path == crypto._KEY_FILE:
                    assert not path.exists()
                elif fault == 'missing_private' and path == kaf_service._PRIV_KEY_FILE:
                    assert not path.exists()
                else:
                    assert original_read(path) == modified[path]
        finally:
            if fault == 'lock_timeout':
                lock.release()
    async with app.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(KafGrant)) == 0
        assert await db.scalar(select(func.count()).select_from(KafFile)) == 1
    for path, original in originals.items():
        path.write_bytes(original)
    recovered = await request()
    assert recovered.status_code == (200 if route == 'grant' else 201), recovered.text
    old = await client.post(f'/api/v1/kaf/files/{document.file_id}/grant', headers=headers)
    assert old.status_code == 200 and old.json()['content_key_hex'] == expected
    assert all(path.read_bytes() == original for path, original in originals.items())


@pytest.mark.parametrize('route', ['grant', 'publish'])
async def test_programming_error_is_not_disguised_as_key_outage(real_kaf_http, monkeypatch, route):
    _, client, headers, document, _, _ = real_kaf_http
    def programmer_error(**kwargs):
        raise TypeError('synthetic43-programming-error')
    monkeypatch.setattr(kaf_service, 'get_fernet', programmer_error)
    response = await client.post('/api/v1/kaf/admin/publish' if route == 'publish'
                                else f'/api/v1/kaf/files/{document.file_id}/grant',
                                headers=headers, **({'json': {'markdown': '# New'}} if route == 'publish' else {}))
    assert response.status_code == 500 and 'retry-after' not in response.headers


async def test_actual_permission_and_not_found_remain_distinct_from_outage(real_kaf_http):
    app, client, headers, document, _, _ = real_kaf_http
    bob = await register(app, username='bob43', email='bob43@example.test')
    bob_headers = bearer(bob['access_token'])
    original = kaf_service._PUB_KEY_FILE.read_bytes()
    kaf_service._PUB_KEY_FILE.write_bytes(b'broken43')
    anonymous = await client.post('/api/v1/kaf/admin/publish', json={'markdown': '# Denied'})
    regular = await client.post('/api/v1/kaf/admin/publish', headers=bob_headers, json={'markdown': '# Denied'})
    assert anonymous.status_code == 401 and regular.status_code == 403
    kaf_service._PUB_KEY_FILE.write_bytes(original)
    async with app.sessions() as db:
        saved = await db.scalar(select(KafFile).where(KafFile.file_id == document.file_id))
        saved.enabled_tiers = 'pro'
        await db.commit()
    grant_url = f'/api/v1/kaf/files/{document.file_id}/grant'
    download_url = f'/api/v1/kaf/files/{document.file_id}/download'
    assert (await client.post(grant_url, headers=bob_headers)).status_code == 403
    assert (await client.get(download_url, headers=bob_headers)).status_code == 403
    async with app.sessions() as db:
        saved = await db.scalar(select(KafFile).where(KafFile.file_id == document.file_id))
        saved.is_published = False
        await db.commit()
    assert (await client.post(grant_url, headers=headers)).status_code == 404
    assert (await client.get(download_url, headers=headers)).status_code == 404
    assert (await client.post('/api/v1/kaf/files/missing43/grant', headers=headers)).status_code == 404
