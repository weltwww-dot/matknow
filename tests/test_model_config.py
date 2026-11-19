import json

import pytest

from matknow.model_config import ModelCatalog
from matknow.profiles import ProfileStore


def test_custom_models_persist_and_profile_selection_is_isolated(tmp_path):
    store=ProfileStore(tmp_path)
    first=store.models.save(None,'甲厂商','https://one.example/v1','shared-model')
    second=store.models.save(None,'乙厂商','https://two.example/v1','shared-model')
    assert first.id != second.id
    profile=store.create('自定义方向',model=second.id)
    reopened=ProfileStore(tmp_path)
    assert reopened.get(profile.id).model==second.id
    assert reopened.models.get(first.id).url=='https://one.example/v1/chat/completions'
    assert reopened.get(reopened.current_id).model is None
    assert set(json.loads((tmp_path/'models.json').read_text(encoding='utf8'))['providers'][0])=={'id','name','url','models'}


def test_failed_catalog_save_preserves_existing_models(tmp_path,monkeypatch):
    import os
    models=ModelCatalog(tmp_path)
    first=models.save(None,'厂商','https://vendor.example/v1','custom')
    original=(tmp_path/'models.json').read_bytes()
    def fail(*args):raise OSError('disk full')
    monkeypatch.setattr(os,'replace',fail)
    with pytest.raises(OSError):models.save(first.id,'厂商','https://new.example','changed')
    assert (tmp_path/'models.json').read_bytes()==original


def test_corrupt_catalog_is_not_overwritten(tmp_path):
    path=tmp_path/'models.json';path.write_text('{')
    with pytest.raises(ValueError):ModelCatalog(tmp_path)
    assert path.read_text()=='{'


def test_fresh_install_has_no_preset_models_and_allows_empty_catalog(tmp_path):
    store=ProfileStore(tmp_path)
    assert store.models.list_models()==[]
    assert store.get(store.current_id).model is None
    (tmp_path/'models.json').write_text(json.dumps({'version':2,'models':[]}),encoding='utf8')
    assert ProfileStore(tmp_path).models.list_models()==[]


def test_old_presets_are_hidden_without_discarding_custom_configuration(tmp_path):
    from matknow.model_config import DEFAULT_URL, completion_url
    entries=[dict(id=name,provider='深度求索',url=completion_url(DEFAULT_URL),model=name)
             for name in ('deepseek-flash','deepseek-v4-pro')]
    custom=dict(id='11111111-1111-4111-8111-111111111111',provider='自己的厂商',url='https://custom.example/v1/chat/completions',model='lab')
    (tmp_path/'models.json').write_text(json.dumps({'version':1,'models':entries+[custom]}),encoding='utf8')
    catalog=ModelCatalog(tmp_path)
    assert [entry.id for entry in catalog.list_models()]==[custom['id']]
    assert catalog.get('deepseek-flash').model=='deepseek-flash'
    saved=catalog.save(None,'另一个厂商','https://other.example','new')
    assert {entry.id for entry in ModelCatalog(tmp_path).list_models()}=={custom['id'],saved.id}


def test_edited_legacy_model_is_kept_as_custom_even_without_a_key(tmp_path):
    entry=dict(id='deepseek-flash',provider='我的接口',url='https://my.example/chat/completions',model='my-model')
    (tmp_path/'models.json').write_text(json.dumps({'version':1,'models':[entry]}),encoding='utf8')
    assert ModelCatalog(tmp_path).get('deepseek-flash').provider=='我的接口'
    assert len(ModelCatalog(tmp_path).list_models())==1
