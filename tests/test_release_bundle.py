from pathlib import Path
from zipfile import ZipFile
import pytest

from scripts.build_installer import ROOT,POLICY_FILES


def test_publish_preserves_local_data_and_policies_but_zip_is_blank(tmp_path):
    from scripts.release_bundle import publish_bundle
    source=tmp_path/'clean';(source/'_internal').mkdir(parents=True)
    (source/'MatKnow.exe').write_bytes(b'MZ-new-program')
    (source/'_internal'/'runtime.dll').write_bytes(b'MZ-runtime')
    (source/'THIRD_PARTY_NOTICES.txt').write_bytes(b'notices')
    for name in POLICY_FILES:(source/name).write_bytes((ROOT/name).read_bytes())
    (source/'app-data').mkdir();(source/'app-data'/'private.txt').write_bytes(b'PRIVATE_SOURCE_DATA')
    destination=tmp_path/'MatKnow';(destination/'app-data'/'credentials').mkdir(parents=True)
    private=destination/'app-data'/'credentials'/'key.dpapi';private.write_bytes(b'PRIVATE_LOCAL_CIPHER')
    (destination/'agent.md').write_bytes(b'MY_LOCAL_RULES')
    archive=tmp_path/'release.zip'
    publish_bundle(source,destination,archive,tmp_path/'packages')
    assert private.read_bytes()==b'PRIVATE_LOCAL_CIPHER'
    assert (destination/'agent.md').read_bytes()==b'MY_LOCAL_RULES'
    assert (destination/'MatKnow.exe').read_bytes()==b'MZ-new-program'
    assert not (destination/'app-data'/'private.txt').exists()
    with ZipFile(archive) as zip:
        assert zip.read('MatKnow/agent.md')==(ROOT/'agent.md').read_bytes()
        assert all('app-data' not in Path(name).parts for name in zip.namelist())
        assert all(b'PRIVATE_' not in zip.read(name) for name in zip.namelist())


def test_rejected_runtime_does_not_touch_local_app_or_existing_archive(tmp_path):
    import pytest
    from scripts.release_bundle import publish_bundle
    source=tmp_path/'invalid';(source/'_internal').mkdir(parents=True)
    for name in POLICY_FILES:(source/name).write_bytes((ROOT/name).read_bytes())
    (source/'MatKnow.exe').write_bytes(b'MZ-new')
    (source/'THIRD_PARTY_NOTICES.txt').write_bytes(b'notices')
    (source/'_internal'/'private.sqlite3').write_bytes(b'PRIVATE')
    destination=tmp_path/'existing';destination.mkdir();(destination/'MatKnow.exe').write_bytes(b'MZ-old')
    archive=tmp_path/'release.zip';archive.write_bytes(b'KEEP_OLD_RELEASE')
    with pytest.raises(ValueError):publish_bundle(source,destination,archive,tmp_path/'packages')
    assert (destination/'MatKnow.exe').read_bytes()==b'MZ-old'
    assert archive.read_bytes()==b'KEEP_OLD_RELEASE'


@pytest.mark.parametrize('policy',POLICY_FILES)
def test_publish_upgrades_only_known_stock_policy(tmp_path,monkeypatch,policy):
    import hashlib
    from scripts import release_bundle
    source=tmp_path/'clean';(source/'_internal').mkdir(parents=True)
    (source/'MatKnow.exe').write_bytes(b'MZ-new')
    (source/'THIRD_PARTY_NOTICES.txt').write_bytes(b'notices')
    for name in POLICY_FILES:(source/name).write_bytes((ROOT/name).read_bytes())
    destination=tmp_path/'app';destination.mkdir()
    stock=b'previous-shipped-experiment-policy'
    monkeypatch.setattr(release_bundle,'UPGRADABLE_POLICY_HASHES',{policy:{hashlib.sha256(stock).hexdigest()}})
    (destination/policy).write_bytes(stock)
    release_bundle.publish_bundle(source,destination,tmp_path/'release.zip',tmp_path/'packages')
    assert (destination/policy).read_bytes()==(ROOT/policy).read_bytes()
    custom=stock+b'\nmy custom changes';(destination/policy).write_bytes(custom)
    release_bundle.publish_bundle(source,destination,tmp_path/'release.zip',tmp_path/'packages')
    assert (destination/policy).read_bytes()==custom
