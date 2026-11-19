from matknow.llm import ModelError
from tests.test_custom_only import fresh_window


def single_cached_model(qtbot,tmp_path):
    store,credentials,w=fresh_window(qtbot,tmp_path)
    provider=store.models.save_provider(None,'DeepSeek','https://one.example',['flash'])
    credentials.for_endpoint(provider.url).key='FAKE-refresh-key';w.use_model(provider.models[0].id)
    return store,credentials,w,provider


def test_refresh_replaces_single_model_cache_with_complete_provider_list(qtbot,tmp_path,monkeypatch):
    store,_,w,provider=single_cached_model(qtbot,tmp_path)
    monkeypatch.setattr('matknow.desktop.list_provider_models',lambda *args:['flash','pro'])
    w.refresh_provider_models();qtbot.waitUntil(lambda:not w.busy)
    assert [w.model_combo.itemText(i) for i in range(w.model_combo.count())]==['flash','pro']
    assert w.current_model==provider.models[0].id
    assert [m.model for m in store.models.get_provider(provider.id).models]==['flash','pro']


def test_reselecting_same_provider_refreshes_instead_of_using_stale_cache(qtbot,tmp_path,monkeypatch):
    _,_,w,provider=single_cached_model(qtbot,tmp_path)
    monkeypatch.setattr('matknow.desktop.list_provider_models',lambda *args:['flash','pro'])
    w.change_provider(w.provider_combo.findData(provider.id));qtbot.waitUntil(lambda:not w.busy)
    assert w.model_combo.count()==2


def test_other_provider_refresh_uses_its_key_and_models(qtbot,tmp_path,monkeypatch):
    store,credentials,w,_=single_cached_model(qtbot,tmp_path)
    other=store.models.save_provider(None,'GPT 接口','https://two.example',['gpt-a'])
    credentials.for_endpoint(other.url).key='FAKE-other-key';calls=[];w._reload_service()
    monkeypatch.setattr('matknow.desktop.list_provider_models',lambda key,url:calls.append((key,url)) or ['gpt-a','gpt-b','gpt-c'])
    w.change_provider(w.provider_combo.findData(other.id));qtbot.waitUntil(lambda:not w.busy)
    assert calls==[('FAKE-other-key',other.url)]
    assert [w.model_combo.itemText(i) for i in range(w.model_combo.count())]==['gpt-a','gpt-b','gpt-c']
    assert w.service.model_client._key=='FAKE-other-key'


def test_missing_key_does_not_fetch_and_explains_stale_cache(qtbot,tmp_path,monkeypatch):
    _,credentials,w,provider=single_cached_model(qtbot,tmp_path)
    credentials.for_endpoint(provider.url).delete()
    def unexpected(*args):raise AssertionError('must not request without a key')
    monkeypatch.setattr('matknow.desktop.list_provider_models',unexpected)
    w.refresh_provider_models()
    assert w.model_combo.count()==1 and not w.busy
    assert 'API Key' in w.status_label.text()
    assert w.service.model_client is None and w.experiment_page.model_client is None


def test_failed_refresh_keeps_cached_models_and_unlocks_ui(qtbot,tmp_path,monkeypatch):
    _,_,w,_=single_cached_model(qtbot,tmp_path)
    def fail(*args):raise ModelError('模型列表获取失败')
    monkeypatch.setattr('matknow.desktop.list_provider_models',fail)
    w.refresh_provider_models();qtbot.waitUntil(lambda:not w.busy)
    assert w.model_combo.count()==1 and w.model_combo.isEnabled()
    assert '获取失败' in w.status_label.text()


def test_profile_write_failure_does_not_leave_removed_model_active(qtbot,tmp_path,monkeypatch):
    store,_,w,_=single_cached_model(qtbot,tmp_path)
    monkeypatch.setattr('matknow.desktop.list_provider_models',lambda *args:['pro'])
    def fail(*args,**kwargs):raise OSError('disk full')
    monkeypatch.setattr(store,'update',fail)
    w.refresh_provider_models();qtbot.waitUntil(lambda:not w.busy)
    assert [w.model_combo.itemText(i) for i in range(w.model_combo.count())]==['pro']
    assert w.service.model_client is None and w.experiment_page.model_client is None


def test_key_changed_during_refresh_updates_clients_without_accepting_old_response(qtbot,tmp_path,monkeypatch):
    _,credentials,w,provider=single_cached_model(qtbot,tmp_path)
    def changed(*args):credentials.for_endpoint(provider.url).key='FAKE-new-key';return ['flash','pro']
    monkeypatch.setattr('matknow.desktop.list_provider_models',changed)
    w.refresh_provider_models();qtbot.waitUntil(lambda:not w.busy)
    assert w.model_combo.count()==1 and w.service.model_client._key=='FAKE-new-key'


def test_migrated_deepseek_flash_id_survives_automatic_directory_completion(qtbot,tmp_path,monkeypatch):
    store,credentials,w=fresh_window(qtbot,tmp_path)
    model=store.models.save('deepseek-flash','deepseek','https://api.deepseek.com','deepseek-flash')
    credentials.key='FAKE-deepseek-key';w.use_model(model.id)
    monkeypatch.setattr('matknow.desktop.list_provider_models',lambda *args:['deepseek-flash','deepseek-v4-pro'])
    w.refresh_provider_models();qtbot.waitUntil(lambda:not w.busy)
    assert w.current_model=='deepseek-flash'
    assert [w.model_combo.itemText(i) for i in range(w.model_combo.count())]==['deepseek-flash','deepseek-v4-pro']
