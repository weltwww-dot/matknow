import sys
from pathlib import Path

import pytest

from matknow.__main__ import default_data_dir
from matknow.credentials import CredentialStore
from matknow.profiles import ProfileStore


def installed_root(monkeypatch,exe):
    with monkeypatch.context() as context:
        context.setattr(sys,'frozen',True,raising=False)
        context.setattr(sys,'executable',str(exe))
        return default_data_dir()


def test_packaged_copies_in_different_folders_have_separate_data_and_credentials(monkeypatch,tmp_path):
    monkeypatch.delenv('MATKNOW_DATA_DIR',raising=False)
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'appdata'))
    first=installed_root(monkeypatch,tmp_path/'first'/'MatKnow.exe')
    second=installed_root(monkeypatch,tmp_path/'second'/'MatKnow.exe')
    assert first!=second and first!=tmp_path/'appdata'/'MatKnow'
    assert CredentialStore(first).target!=CredentialStore(second).target
    assert first==tmp_path/'first'/'app-data'
    assert second==tmp_path/'second'/'app-data'


def test_same_install_path_keeps_identity_across_cwd_and_exe_rename(monkeypatch,tmp_path):
    monkeypatch.delenv('MATKNOW_DATA_DIR',raising=False)
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'appdata'))
    first=installed_root(monkeypatch,tmp_path/'first'/'MatKnow.exe')
    monkeypatch.chdir(tmp_path)
    assert installed_root(monkeypatch,tmp_path/'first'/'Renamed.exe')==first
    assert not first.exists()


def test_source_project_uses_its_own_folder_and_explicit_override(monkeypatch,tmp_path):
    import matknow.__main__ as entry
    monkeypatch.setattr(sys,'frozen',False,raising=False)
    monkeypatch.delenv('MATKNOW_DATA_DIR',raising=False)
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'appdata'))
    monkeypatch.setattr(entry,'__file__',str(tmp_path/'project'/'matknow'/'__main__.py'))
    assert default_data_dir()==tmp_path/'project'/'app-data'
    monkeypatch.setenv('MATKNOW_DATA_DIR',str(tmp_path/'explicit'))
    assert default_data_dir()==tmp_path/'explicit'
    assert installed_root(monkeypatch,tmp_path/'first'/'MatKnow.exe')==tmp_path/'explicit'


@pytest.mark.skipif(sys.platform!='win32',reason='Windows native credentials')
def test_existing_project_data_and_keys_do_not_appear_in_new_install(monkeypatch,tmp_path):
    from matknow.service import AskService
    from matknow.experiments.models import ExperimentDraft,SampleDraft
    from matknow.experiments.store import ExperimentStore
    monkeypatch.delenv('MATKNOW_DATA_DIR',raising=False)
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'appdata'))
    legacy=tmp_path/'appdata'/'MatKnow'
    old=ProfileStore(legacy);old_kb=old.knowledge(old.current_id)
    old_kb.import_text('已有资料','干燥温度 60 °C。');AskService(old_kb).ask('干燥温度')
    ExperimentStore(old_kb).save(ExperimentDraft('已有实验','2026-10-14',samples=[SampleDraft('A1')]))
    old.models.save_provider(None,'已有厂商','https://api.deepseek.com',['deepseek-flash'])
    first=installed_root(monkeypatch,tmp_path/'first'/'MatKnow.exe')
    second=installed_root(monkeypatch,tmp_path/'second'/'MatKnow.exe')
    credentials=[CredentialStore(path) for path in (legacy,first,second)]
    try:
        credentials[0].set('FAKE-legacy-install-key')
        snapshots={p.name:p.read_bytes() for p in legacy.iterdir() if p.is_file()}
        for path,credential in zip((first,second),credentials[1:]):
            store=ProfileStore(path);kb=store.knowledge(store.current_id)
            assert not kb.list_documents() and not kb.list_history()
            assert not ExperimentStore(kb).list_experiments() and not store.models.list_providers()
            assert credential.get() is None
        a=ProfileStore(first);a.knowledge(a.current_id).import_text('仅A的资料','A 独立资料')
        credentials[1].set('FAKE-install-A-key')
        reopened=ProfileStore(installed_root(monkeypatch,tmp_path/'first'/'MatKnow.exe'))
        assert len(reopened.knowledge(reopened.current_id).list_documents())==1
        assert credentials[1].get()=='FAKE-install-A-key' and credentials[2].get() is None
        assert credentials[0].get()=='FAKE-legacy-install-key'
        assert all((legacy/name).read_bytes()==data for name,data in snapshots.items())
    finally:
        for credential in credentials:credential.delete()
