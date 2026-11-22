"""Real encryption consumers must not rekey when old secret files are lost."""
import base64
from pathlib import Path

import pytest
from app.core import crypto, kaf_service
from tests.test_product_auth import account_app, register, bearer
from tests.test_round39_mcp_persistence import private_app, SERVER


@pytest.mark.parametrize('legacy', [False, True])
def test_lost_key_decrypt_does_not_create_replacement_and_old_key_recovers(tmp_path,monkeypatch,legacy):
    key=tmp_path/'key'; monkeypatch.setattr(crypto,'_KEY_FILE',key)
    token=crypto.encrypt_api_key('synthetic-read-recovery')
    if legacy: token=base64.b64encode(token.encode()).decode()
    original=key.read_bytes(); key.unlink()
    remaining={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(ValueError): crypto.decrypt_api_key(token)
    assert not key.exists(), 'Reading old ciphertext must not publish a new key'
    # Unix native locks retain their sidecar; a read must not add or alter files.
    assert {p.name:p.read_bytes() for p in tmp_path.iterdir()}==remaining
    key.write_bytes(original)
    assert crypto.decrypt_api_key(token)=='synthetic-read-recovery'
    assert key.read_bytes()==original


async def test_real_mcp_get_lost_key_safe503_does_not_rotate_and_restoration_works(private_app):
    alice=await register(private_app); headers=bearer(alice['access_token'])
    assert (await private_app.client.post('/mcp/servers',headers=headers,json=SERVER)).status_code==200
    key=crypto._KEY_FILE; original=key.read_bytes(); key.unlink()
    failed=await private_app.client.get('/api/v1/mcp/servers',headers=headers)
    assert failed.status_code==503 and 'synthetic-secret' not in failed.text
    assert not key.exists()
    key.write_bytes(original)
    recovered=await private_app.client.get('/mcp/servers',headers=headers)
    assert recovered.status_code==200 and recovered.json()['servers'][0]['env']['API_KEY']=='***'


@pytest.fixture
def private_kaf(tmp_path,monkeypatch):
    directory=tmp_path/'kaf'; directory.mkdir()
    monkeypatch.setattr(crypto,'_KEY_FILE',tmp_path/'fernet')
    monkeypatch.setattr(kaf_service,'_KEY_DIR',directory)
    monkeypatch.setattr(kaf_service,'_PRIV_KEY_FILE',directory/'private.enc')
    monkeypatch.setattr(kaf_service,'_PUB_KEY_FILE',directory/'public.pem')
    return directory


def test_real_kaf_old_private_read_missing_fernet_preserves_files_and_recovers(private_kaf):
    first=kaf_service.KAFService(); private=first.private_key.private_numbers()
    before={p:p.read_bytes() for p in private_kaf.iterdir()}
    original=crypto._KEY_FILE.read_bytes(); crypto._KEY_FILE.unlink()
    with pytest.raises(RuntimeError,match='密钥'): kaf_service.KAFService().private_key
    assert not crypto._KEY_FILE.exists()
    assert all(p.read_bytes()==value for p,value in before.items())
    crypto._KEY_FILE.write_bytes(original)
    assert kaf_service.KAFService().private_key.private_numbers()==private


@pytest.mark.parametrize('remaining', ['private', 'public'])
def test_orphaned_kaf_keypair_file_is_refused_without_overwrite_or_rekey(private_kaf,remaining):
    path=kaf_service._PRIV_KEY_FILE if remaining=='private' else kaf_service._PUB_KEY_FILE
    path.write_bytes(b'synthetic-old-orphan')
    with pytest.raises(RuntimeError): kaf_service.KAFService().private_key
    assert path.read_bytes()==b'synthetic-old-orphan'
    # The native pair lock may retain its sidecar on Unix; original payload
    # files must remain untouched and no Fernet replacement may be published.
    assert [p for p in private_kaf.iterdir() if not p.name.endswith('.lock')]==[path]
    assert not crypto._KEY_FILE.exists()
