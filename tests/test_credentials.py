import sys
import shutil

import pytest

from matknow.credentials import CredentialError, CredentialStore, validate_key


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native credentials')
def test_windows_credential_lifecycle_is_local_encrypted_and_scoped(tmp_path):
    first, second = CredentialStore(tmp_path / 'first'), CredentialStore(tmp_path / 'second')
    try:
        assert first.get() is None
        first.set('test-only-FAKE-secret-one')
        assert CredentialStore(tmp_path / 'first').get() == 'test-only-FAKE-secret-one'
        assert second.get() is None
        first.set('test-only-FAKE-secret-two')
        assert first.get() == 'test-only-FAKE-secret-two'
        files=[p for p in tmp_path.rglob('*') if p.is_file()]
        assert files and all(b'test-only-FAKE-secret' not in p.read_bytes() for p in files)
        assert all(p.is_relative_to(tmp_path/'first') for p in files)
        first.delete()
        assert first.get() is None
        first.delete()
    finally:
        first.delete()
        second.delete()


@pytest.mark.parametrize('key', ['', ' ', 'x\ny', 'x\ry', 'x\x00y', '字', 'x' * 2561])
def test_invalid_key_rejected_without_echo(key):
    with pytest.raises(ValueError):
        validate_key(key)


def test_key_validation_accepts_visible_ascii_without_assuming_prefix():
    assert validate_key('  arbitrary-token.abc-123  ') == 'arbitrary-token.abc-123'


def test_unavailable_platform_cannot_fall_back_to_plaintext(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'unsupported')
    with pytest.raises(CredentialError, match='Windows'):
        CredentialStore(tmp_path).set('test-FAKE-key')
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native credentials')
def test_custom_endpoint_keys_are_isolated_and_legacy_key_is_preserved(tmp_path):
    root=CredentialStore(tmp_path)
    first=root.for_endpoint('https://one.example/v1')
    same=root.for_endpoint('https://one.example/v1/chat/completions')
    other=root.for_endpoint('https://two.example/v1')
    try:
        root.set('FAKE-legacy-deepseek-key')
        assert first.get() is None and other.get() is None
        first.set('FAKE-vendor-one-key')
        assert same.get()=='FAKE-vendor-one-key'
        assert other.get() is None and root.get()=='FAKE-legacy-deepseek-key'
        first.delete()
        assert root.get()=='FAKE-legacy-deepseek-key'
        assert all(b'FAKE-' not in p.read_bytes() for p in tmp_path.rglob('*') if p.is_file())
    finally:
        root.delete();first.delete();other.delete()


@pytest.mark.skipif(sys.platform!='win32',reason='Windows DPAPI')
def test_copied_and_moved_folder_keeps_key_but_edits_are_independent(tmp_path):
    original=tmp_path/'original';copy=tmp_path/'copy';moved=tmp_path/'moved'
    first=CredentialStore(original);first.set('FAKE-copy-source')
    shutil.copytree(original,copy)
    assert CredentialStore(copy).get()=='FAKE-copy-source'
    CredentialStore(copy).set('FAKE-copy-new')
    assert first.get()=='FAKE-copy-source'
    shutil.move(str(copy),str(moved))
    assert CredentialStore(moved).get()=='FAKE-copy-new'
    CredentialStore(moved).delete()
    assert first.get()=='FAKE-copy-source'


@pytest.mark.skipif(sys.platform!='win32',reason='Windows DPAPI')
def test_unreadable_encrypted_key_never_falls_back_or_echoes_contents(tmp_path):
    store=CredentialStore(tmp_path);store.set('FAKE-private-key')
    files=[p for p in tmp_path.rglob('*') if p.is_file()]
    assert len(files)==1
    files[0].write_bytes(b'FAKE-private-key-corrupted')
    with pytest.raises(CredentialError) as failure:store.get()
    assert 'FAKE-private-key' not in str(failure.value)
    assert files[0].read_bytes()==b'FAKE-private-key-corrupted'


@pytest.mark.skipif(sys.platform!='win32',reason='Windows DPAPI')
def test_atomic_save_failure_preserves_previous_encrypted_key(tmp_path,monkeypatch):
    store=CredentialStore(tmp_path);store.set('FAKE-before')
    def fail(*args):raise OSError('FAKE-secret-error')
    monkeypatch.setattr('matknow.credentials.os.replace',fail)
    with pytest.raises(CredentialError) as failure:store.set('FAKE-after')
    assert 'FAKE-' not in str(failure.value)
    assert store.get()=='FAKE-before'
    assert len([p for p in tmp_path.rglob('*') if p.is_file()])==1
