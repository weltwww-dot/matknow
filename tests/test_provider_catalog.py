import json

import pytest

from matknow.model_config import ModelCatalog
from matknow.profiles import ProfileStore


def test_provider_owns_endpoint_and_models_and_updates_together(tmp_path):
    catalog=ModelCatalog(tmp_path)
    provider=catalog.save_provider(None,'甲厂商','https://one.example/v1',['model-a','model-b'])
    assert len(catalog.list_providers())==1
    assert [m.model for m in provider.models]==['model-a','model-b']
    updated=catalog.save_provider(provider.id,'改名厂商','https://changed.example/v1',['model-a','model-b','model-c'])
    assert updated.id==provider.id
    assert [m.id for m in updated.models[:2]]==[m.id for m in provider.models]
    assert {m.url for m in catalog.list_models()}=={'https://changed.example/v1/chat/completions'}
    saved=json.loads((tmp_path/'models.json').read_text(encoding='utf8'))
    assert saved['version']==3 and len(saved['providers'])==1 and 'models' not in saved
    assert set(saved['providers'][0])=={'id','name','url','models'}


def test_old_flat_models_group_by_provider_and_url_without_changing_selection(tmp_path):
    store=ProfileStore(tmp_path)
    entries=[dict(id=f'11111111-1111-4111-8111-11111111111{i}',provider='厂商',url=url,model=model)
             for i,url,model in [(1,'https://one.example/chat/completions','a'),(2,'https://one.example/chat/completions','b'),(3,'https://two.example/chat/completions','a')]]
    (tmp_path/'models.json').write_text(json.dumps({'version':2,'models':entries}),encoding='utf8')
    p=store.get(store.current_id);store.update(p.id,p.name,p.description,entries[1]['id'])
    providers=store.models.list_providers()
    assert len(providers)==2 and [m.model for m in providers[0].models]==['a','b']
    assert providers[0].id==ModelCatalog(tmp_path).list_providers()[0].id
    store.models.save_provider(providers[0].id,'厂商','https://one.example',['a','b','c'])
    assert ProfileStore(tmp_path).get(p.id).model==entries[1]['id']


def test_removing_models_or_provider_does_not_break_directions(tmp_path):
    store=ProfileStore(tmp_path)
    provider=store.models.save_provider(None,'厂商','https://one.example',['a','b'])
    p=store.get(store.current_id);store.update(p.id,p.name,p.description,provider.models[1].id)
    other=store.create('另一个方向',model=provider.models[0].id)
    store.models.save_provider(provider.id,provider.name,provider.url,['a'])
    assert ProfileStore(tmp_path).get(p.id).model is None
    assert store.get(other.id).model==provider.models[0].id
    store.models.delete_provider(provider.id)
    assert ProfileStore(tmp_path).get(other.id).model is None
    assert store.knowledge(other.id).path.is_file()


@pytest.mark.parametrize('models',[['a','a'],['bad model']])
def test_invalid_provider_models_do_not_change_saved_configuration(tmp_path,models):
    catalog=ModelCatalog(tmp_path);provider=catalog.save_provider(None,'厂商','https://one.example',['a'])
    before=(tmp_path/'models.json').read_bytes()
    with pytest.raises(ValueError):catalog.save_provider(provider.id,'厂商',provider.url,models)
    assert (tmp_path/'models.json').read_bytes()==before


def test_failed_provider_save_preserves_all_models(tmp_path,monkeypatch):
    import os
    catalog=ModelCatalog(tmp_path);provider=catalog.save_provider(None,'厂商','https://one.example',['a','b'])
    before=(tmp_path/'models.json').read_bytes()
    def fail(*args):raise OSError('disk full')
    monkeypatch.setattr(os,'replace',fail)
    with pytest.raises(OSError):catalog.save_provider(provider.id,'改名',provider.url,['a','b','c'])
    assert (tmp_path/'models.json').read_bytes()==before
