import pytest

from scripts.build_installer import ROOT, stage_payload, POLICY_FILES


def copy_policies(bundle):
    for name in POLICY_FILES:(bundle/name).write_bytes((ROOT/name).read_bytes())


def test_installer_excludes_personal_files_and_copies_only_runtime(tmp_path):
    bundle=tmp_path/'bundle';runtime=bundle/'_internal';runtime.mkdir(parents=True)
    (bundle/'MatKnow.exe').write_bytes(b'MZ-program')
    (bundle/'THIRD_PARTY_NOTICES.txt').write_text('Licenses')
    copy_policies(bundle)
    (runtime/'python314.dll').write_bytes(b'MZ-runtime')
    for name in ('profiles.json','models.json','knowledge.sqlite3','.env','notes.docx','api-key.txt'):
        (bundle/name).write_bytes(b'PRIVATE_SENTINEL')
    stage=tmp_path/'stage'
    stage_payload(bundle,stage,tmp_path/'packages')
    assert sorted(p.name for p in stage.iterdir())==sorted(['MatKnow.exe','THIRD_PARTY_NOTICES.txt','_internal',*POLICY_FILES])
    assert (stage/'agent.md').read_bytes()==(ROOT/'agent.md').read_bytes()
    assert all(b'PRIVATE_SENTINEL' not in p.read_bytes() for p in stage.rglob('*') if p.is_file())


@pytest.mark.parametrize('name,content',[
    ('knowledge.sqlite3',b'private database'),
    ('api-key.txt',b'private key'),
    ('unexpected.dll',b'sk-'+b'A'*32),
    ('unexpected.dll',b'-----BEGIN PRIVATE KEY-----\n'+b'A'*64),
])
def test_installer_refuses_private_or_secret_runtime_files(tmp_path,name,content):
    bundle=tmp_path/'bundle';runtime=bundle/'_internal';runtime.mkdir(parents=True)
    (bundle/'MatKnow.exe').write_bytes(b'MZ-program')
    (bundle/'THIRD_PARTY_NOTICES.txt').write_text('Licenses')
    copy_policies(bundle)
    (runtime/name).write_bytes(content)
    with pytest.raises(ValueError):
        stage_payload(bundle,tmp_path/'stage',tmp_path/'packages')


def test_installer_refuses_modified_vendor_documents(tmp_path):
    bundle=tmp_path/'bundle';templates=bundle/'_internal/docx/templates';templates.mkdir(parents=True)
    (templates/'default.docx').write_bytes(b'PRIVATE_LAB_RECORD')
    copy_policies(bundle)
    original=tmp_path/'packages/docx/templates';original.mkdir(parents=True)
    (original/'default.docx').write_bytes(b'official template')
    with pytest.raises(ValueError):
        stage_payload(bundle,tmp_path/'stage',tmp_path/'packages')


def test_public_pem_format_marker_in_runtime_is_not_a_private_key(tmp_path):
    bundle=tmp_path/'bundle';runtime=bundle/'_internal';runtime.mkdir(parents=True)
    (bundle/'MatKnow.exe').write_bytes(b'MZ-program')
    (bundle/'THIRD_PARTY_NOTICES.txt').write_text('Licenses')
    copy_policies(bundle)
    (runtime/'crypto.dll').write_bytes(b'-----BEGIN RSA PRIVATE KEY-----\x00format marker')
    stage_payload(bundle,tmp_path/'stage',tmp_path/'packages')


@pytest.mark.parametrize('policy',[None,b'PRIVATE_LAB_RULE'])
def test_installer_refuses_missing_or_personally_modified_policy(tmp_path,policy):
    bundle=tmp_path/'bundle';(bundle/'_internal').mkdir(parents=True)
    (bundle/'MatKnow.exe').write_bytes(b'MZ-program')
    (bundle/'THIRD_PARTY_NOTICES.txt').write_text('Licenses')
    if policy is not None:(bundle/'agent.md').write_bytes(policy)
    with pytest.raises(ValueError,match='policy'):
        stage_payload(bundle,tmp_path/'stage',tmp_path/'packages')
