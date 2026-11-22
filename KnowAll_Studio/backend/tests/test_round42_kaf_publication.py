"""Real RSA identities, disk failure boundaries and actual KAF consumers."""
import concurrent.futures
import hashlib
import os
import threading
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from app.core import crypto, kaf_service, secret_file
from tests.test_round41_key_recovery import private_kaf


def fingerprint(key):
    return hashlib.sha256(key.public_bytes(serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()


@pytest.mark.parametrize('destination', ['private','public'])
def test_disk_failure_has_no_cached_identity_and_retry_uses_durable_private(private_kaf,monkeypatch,destination):
    target=kaf_service._PRIV_KEY_FILE if destination=='private' else kaf_service._PUB_KEY_FILE
    write=Path.write_bytes; replace=os.replace
    def fail_write(path,data):
        if path==target: raise OSError('synthetic42-disk-failure')
        return write(path,data)
    def fail_replace(source,dest):
        if Path(dest)==target: raise OSError('synthetic42-disk-failure')
        return replace(source,dest)
    service=kaf_service.KAFService()
    with monkeypatch.context() as patch:
        patch.setattr(Path,'write_bytes',fail_write)
        patch.setattr(secret_file.os,'replace',fail_replace)
        with pytest.raises(kaf_service.KAFKeyUnavailableError) as failure: service.private_key
        assert isinstance(failure.value.__cause__, OSError)
        assert 'synthetic42' in str(failure.value.__cause__)
    assert service._private_key is None and service._public_key is None
    prior=kaf_service._PRIV_KEY_FILE.read_bytes() if kaf_service._PRIV_KEY_FILE.exists() else None
    assert not list(private_kaf.glob('*.tmp'))
    identity=fingerprint(service.public_key)
    assert service.private_key.key_size==4096
    if prior is not None: assert kaf_service._PRIV_KEY_FILE.read_bytes()==prior
    assert fingerprint(kaf_service.KAFService().public_key)==identity


@pytest.mark.parametrize('invalid', ['mismatch','corrupt'])
def test_invalid_existing_public_key_never_caches_partial_pair(private_kaf,invalid):
    from kaf.keys import generate_keypair
    service=kaf_service.KAFService(); original=service.private_key.private_numbers()
    _, other=generate_keypair(4096)
    bad=(other.public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
        if invalid=='mismatch' else b'invalid-public-pem')
    valid=kaf_service._PUB_KEY_FILE.read_bytes(); kaf_service._PUB_KEY_FILE.write_bytes(bad)
    before=kaf_service._PRIV_KEY_FILE.read_bytes(); fresh=kaf_service.KAFService()
    with pytest.raises((RuntimeError,ValueError)): fresh.public_key
    assert fresh._private_key is None and fresh._public_key is None
    assert kaf_service._PUB_KEY_FILE.read_bytes()==bad
    assert kaf_service._PRIV_KEY_FILE.read_bytes()==before
    kaf_service._PUB_KEY_FILE.write_bytes(valid)
    assert fresh.private_key.private_numbers()==original


def test_valid_private_recovers_missing_public_without_changing_identity(private_kaf):
    service=kaf_service.KAFService(); expected=fingerprint(service.public_key)
    private=kaf_service._PRIV_KEY_FILE.read_bytes(); secret=crypto._KEY_FILE.read_bytes()
    kaf_service._PUB_KEY_FILE.unlink()
    assert fingerprint(kaf_service.KAFService().public_key)==expected
    assert kaf_service._PRIV_KEY_FILE.read_bytes()==private and crypto._KEY_FILE.read_bytes()==secret


def test_concurrent_cold_services_share_real4096_identity(private_kaf):
    start=threading.Barrier(4)
    def load(_):
        start.wait(timeout=10)
        service=kaf_service.KAFService()
        return fingerprint(service.public_key),fingerprint(service.private_key.public_key())
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        identities=list(pool.map(load,range(4)))
    assert len(set(identities))==1
    assert identities[0][0]==identities[0][1]==fingerprint(kaf_service.KAFService().public_key)


def test_real_kaf_published_before_restart_and_missing_public_is_readable(private_kaf,tmp_path,monkeypatch):
    monkeypatch.setattr(kaf_service,'KAF_DIR',tmp_path/'documents')
    service=kaf_service.KAFService()
    file=service.publish_from_markdown('# Real42\n\nold content survives identity recovery','test42')
    data=Path(file.storage_path).read_bytes(); original=service._unwrap_content_key(data)
    kaf_service._PUB_KEY_FILE.unlink()
    fresh=kaf_service.KAFService()
    assert fresh._unwrap_content_key(data)==original
    assert fingerprint(fresh.public_key)==fingerprint(service.public_key)
