"""Actual authenticated grant, native content files and recoverable IO failures."""
from pathlib import Path
import pytest
from sqlalchemy import select, func
from kaf.format import inspect_kaf, IV_LEN
from app.models.kaf import KafGrant
from tests.test_round43_kaf_recovery import real_kaf_http, account_app, private_kaf


@pytest.mark.parametrize('fault,status', [('missing',404), ('permission',503),
                                        ('io',503), ('bad_format',404), ('bad_envelope',404)])
async def test_content_failure_safe_response_no_audit_then_original_recovers(real_kaf_http, monkeypatch, fault, status):
    app, client, headers, doc, expected, service = real_kaf_http
    path = Path(doc.storage_path); original = path.read_bytes(); read = Path.read_bytes
    with monkeypatch.context() as patch:
        if fault == 'missing': path.unlink()
        elif fault == 'bad_format': path.write_bytes(b'bad-format45')
        elif fault == 'bad_envelope':
            info = inspect_kaf(original); data = bytearray(original)
            offset = info['envelope_offset'] + IV_LEN
            data[offset:offset + 512] = bytes(512)
            path.write_bytes(data)
        else:
            def failed(p):
                if p == path:
                    error = PermissionError if fault == 'permission' else OSError
                    raise error('sensitive-path-and-secret45')
                return read(p)
            patch.setattr(Path, 'read_bytes', failed)
        response = await client.post(f'/api/v1/kaf/files/{doc.file_id}/grant', headers=headers)
        assert response.status_code == status, response.text
        assert 'content_key_hex' not in response.text and 'sensitive-path' not in response.text
        assert str(path) not in response.text
        assert ('retry-after' in response.headers) == (status == 503)
        if status == 503: assert response.headers['retry-after'] == '5'
    async with app.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(KafGrant)) == 0
    assert service._grant_cache == {}
    path.write_bytes(original)
    response = await client.post(f'/api/v1/kaf/files/{doc.file_id}/grant', headers=headers)
    assert response.status_code == 200 and response.json()['content_key_hex'] == expected


async def test_unwrap_programming_error_is_not_labeled_file_outage(real_kaf_http, monkeypatch):
    _, client, headers, doc, _, service = real_kaf_http
    def wrong(data): raise TypeError('synthetic-programming-error45')
    monkeypatch.setattr(service, '_unwrap_content_key', wrong)
    response = await client.post(f'/api/v1/kaf/files/{doc.file_id}/grant', headers=headers)
    assert response.status_code == 500 and 'retry-after' not in response.headers
