import json
import os

import pytest

from matknow.knowledge import KnowledgeBase
from matknow.profiles import DEFAULT_PROFILE_ID, ProfileStore
from matknow.service import AskService


def test_legacy_library_preserved_and_profiles_isolate_same_filename(tmp_path):
    legacy = KnowledgeBase(tmp_path / 'knowledge.sqlite3')
    document = legacy.import_text('方法.txt', '干燥温度为 60 °C。')
    answer = AskService(legacy).ask('干燥温度')
    store = ProfileStore(tmp_path)
    assert store.knowledge(DEFAULT_PROFILE_ID).path == legacy.path
    assert store.knowledge(DEFAULT_PROFILE_ID).list_history()[0].id == answer.id
    other = store.create('生物实验', '仅依据本方向资料回答。')
    other_kb = store.knowledge(other.id)
    other_doc = other_kb.import_text('方法.txt', '干燥温度为 25 °C。')
    assert other_kb.get_evidence(answer.sources[0].id) is None
    assert other_kb.search('干燥', [document.id]) == []
    assert other_kb.search('干燥')[0].document_id == other_doc.id
    assert other_kb.list_history() == []
    store.select(other.id)
    reopened = ProfileStore(tmp_path)
    assert reopened.current_id == other.id
    assert len(reopened.knowledge(DEFAULT_PROFILE_ID).list_documents()) == 1
    updated = reopened.update(other.id, '生物材料', '新说明', 'deepseek-v4-pro')
    assert updated.id == other.id
    assert reopened.knowledge(other.id).list_documents()[0].id == other_doc.id


@pytest.mark.parametrize('name', ['', '  ', '字' * 61, '材料制备', '方向\n标题'])
def test_invalid_names_do_not_create_profiles(tmp_path, name):
    store = ProfileStore(tmp_path)
    with pytest.raises(ValueError):
        store.create(name)
    assert len(store.list_profiles()) == 1


def test_invalid_ids_and_duplicate_names_are_rejected(tmp_path):
    store = ProfileStore(tmp_path)
    store.create('ABC')
    with pytest.raises(ValueError):
        store.create(' abc ')
    for value in ['../outside', 'unknown', '00000000-0000-0000-0000-000000000099']:
        with pytest.raises(ValueError):
            store.knowledge(value)
    with pytest.raises(ValueError):
        store.create('长说明', '字' * 2001)
    with pytest.raises(ValueError):
        store.create('模型错误', model='unapproved-model')


def test_failed_write_preserves_registry_and_current_profile(tmp_path, monkeypatch):
    store = ProfileStore(tmp_path)
    other = store.create('另一个方向')
    original = (tmp_path / 'profiles.json').read_bytes()
    def fail(*args):
        raise OSError('disk unavailable')
    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(OSError):
        store.select(other.id)
    assert (tmp_path / 'profiles.json').read_bytes() == original
    assert store.current_id == DEFAULT_PROFILE_ID


@pytest.mark.parametrize('payload', ['{', '{"version": 9}', '{"version": 1, "current_id": "x", "profiles": []}'])
def test_corrupt_registry_is_not_overwritten(tmp_path, payload):
    path = tmp_path / 'profiles.json'
    path.write_text(payload, encoding='utf-8')
    with pytest.raises(ValueError, match='配置'):
        ProfileStore(tmp_path)
    assert path.read_text(encoding='utf-8') == payload


def test_registry_rejects_path_fields_and_duplicate_ids(tmp_path):
    ProfileStore(tmp_path)
    path = tmp_path / 'profiles.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['profiles'][0]['path'] = '../outside.sqlite3'
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError):
        ProfileStore(tmp_path)


def test_missing_registered_database_is_not_silently_recreated(tmp_path):
    store = ProfileStore(tmp_path)
    profile = store.create('丢失数据库')
    path = store.knowledge(profile.id).path
    path.unlink()
    with pytest.raises(ValueError, match='缺失'):
        store.knowledge(profile.id)
    assert not path.exists()
