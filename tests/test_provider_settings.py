from PySide6.QtWidgets import QMessageBox

from matknow.settings import ModelSettingsDialog
from tests.test_custom_only import fresh_window


def save_provider(qtbot,dialog,name,url,key='FAKE-provider-key'):
    dialog.provider_edit.setText(name);dialog.url_edit.setText(url);dialog.key_edit.setText(key)
    dialog.save();qtbot.waitUntil(lambda:not dialog.busy)


def test_settings_only_manage_providers_and_discover_models(qtbot,tmp_path,monkeypatch):
    store,credentials,w=fresh_window(qtbot,tmp_path);calls=[]
    monkeypatch.setattr('matknow.settings.list_provider_models',lambda key,url:calls.append((key,url)) or ['model-a','model-b'])
    dialog=ModelSettingsDialog(store,store.current_id,credentials,w);qtbot.addWidget(dialog)
    assert not any(hasattr(dialog,name) for name in ('model_combo','models_edit','connection_label','connection_button'))
    save_provider(qtbot,dialog,'DeepSeek','https://one.example/v1')
    assert w.provider_combo.currentText()=='DeepSeek' and w.model_combo.count()==2
    assert calls==[('FAKE-provider-key','https://one.example/v1/chat/completions')]
    assert w.service.model_client.model=='model-a'
    assert credentials.for_endpoint('https://one.example/v1').key=='FAKE-provider-key'
    assert 'FAKE-provider-key' not in (tmp_path/'models.json').read_text(encoding='utf8')


def test_new_provider_discovery_failure_keeps_config_for_retry(qtbot,tmp_path,monkeypatch):
    from matknow.llm import ModelError
    store,credentials,w=fresh_window(qtbot,tmp_path)
    def fail(*args):raise ModelError('无法获取模型')
    monkeypatch.setattr('matknow.settings.list_provider_models',fail)
    dialog=ModelSettingsDialog(store,store.current_id,credentials,w);qtbot.addWidget(dialog)
    save_provider(qtbot,dialog,'厂商','https://one.example')
    assert len(store.models.list_providers())==1 and w.current_provider.name=='厂商'
    assert w.current_config is None and not w.model_combo.isEnabled()
    assert '无法获取模型' in dialog.status_label.text()
    monkeypatch.setattr('matknow.settings.list_provider_models',lambda *args:['a','b'])
    dialog.save();qtbot.waitUntil(lambda:not dialog.busy)
    assert w.model_combo.count()==2 and w.current_config.model=='a'


def test_editing_provider_preserves_chat_model_and_updates_all_models(qtbot,tmp_path,monkeypatch):
    store,credentials,w=fresh_window(qtbot,tmp_path)
    monkeypatch.setattr('matknow.settings.list_provider_models',lambda *args:['a','b'])
    dialog=ModelSettingsDialog(store,store.current_id,credentials,w);qtbot.addWidget(dialog)
    save_provider(qtbot,dialog,'旧名称','https://one.example')
    w.change_model(1);identifier=w.current_model
    save_provider(qtbot,dialog,'新名称','https://one.example',key='')
    assert w.current_model==identifier and w.provider_combo.currentText()=='新名称'
    assert all(m.provider=='新名称' for m in store.models.list_models())
    assert dialog.key_edit.text() and dialog.key_edit.text()!='FAKE-provider-key'


def test_delete_provider_removes_unused_key_but_keeps_documents(qtbot,tmp_path,monkeypatch):
    store,credentials,w=fresh_window(qtbot,tmp_path)
    monkeypatch.setattr('matknow.settings.list_provider_models',lambda *args:['a'])
    dialog=ModelSettingsDialog(store,store.current_id,credentials,w);qtbot.addWidget(dialog)
    save_provider(qtbot,dialog,'厂商','https://one.example')
    doc=w.knowledge.import_text('保留资料','干燥温度 60 °C。')
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.Yes)
    dialog.delete_provider()
    assert store.models.list_providers()==[] and w.current_provider is None
    assert credentials.for_endpoint('https://one.example').get() is None
    assert w.knowledge.list_documents()[0].id==doc.id


def test_deleting_shared_endpoint_provider_keeps_other_provider_key(qtbot,tmp_path,monkeypatch):
    store,credentials,w=fresh_window(qtbot,tmp_path)
    one=store.models.save_provider(None,'厂商甲','https://one.example',['a'])
    two=store.models.save_provider(None,'厂商乙','https://one.example',['b'])
    credentials.for_endpoint(one.url).key='FAKE-shared-key';w.use_model(one.models[0].id)
    dialog=ModelSettingsDialog(store,store.current_id,credentials,w);qtbot.addWidget(dialog)
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.Yes)
    monkeypatch.setattr('matknow.desktop.list_provider_models',lambda *args:['b'])
    dialog.delete_provider();w.change_provider(w.provider_combo.findData(two.id));qtbot.waitUntil(lambda:not w.busy)
    assert w.service.model_client._key=='FAKE-shared-key'


def test_fetch_failure_preserves_cached_models_for_same_endpoint(qtbot,tmp_path,monkeypatch):
    from matknow.llm import ModelError
    store,credentials,w=fresh_window(qtbot,tmp_path)
    provider=store.models.save_provider(None,'厂商','https://one.example',['a','b'])
    credentials.for_endpoint(provider.url).key='FAKE-key';w.use_model(provider.models[1].id)
    def fail(*args):raise ModelError('模型列表暂不可用')
    monkeypatch.setattr('matknow.settings.list_provider_models',fail)
    dialog=ModelSettingsDialog(store,store.current_id,credentials,w);qtbot.addWidget(dialog)
    dialog.save();qtbot.waitUntil(lambda:not dialog.busy)
    assert w.current_model==provider.models[1].id and w.model_combo.count()==2
    assert '暂不可用' in dialog.status_label.text()


def test_fetch_busy_blocks_chat_changes_and_dialog_close(qtbot,tmp_path,monkeypatch):
    import threading
    gate=threading.Event();started=threading.Event()
    store,credentials,w=fresh_window(qtbot,tmp_path)
    def fetch(*args):started.set();gate.wait(3);return ['a']
    monkeypatch.setattr('matknow.settings.list_provider_models',fetch)
    dialog=ModelSettingsDialog(store,store.current_id,credentials,w);qtbot.addWidget(dialog);dialog.show()
    dialog.provider_edit.setText('厂商');dialog.url_edit.setText('https://one.example');dialog.key_edit.setText('FAKE-key')
    try:
        dialog.save();qtbot.waitUntil(started.is_set)
        assert dialog.busy and not w.provider_combo.isEnabled() and not w.model_combo.isEnabled()
        assert not w.profile_combo.isEnabled() and not dialog.new_button.isEnabled()
        dialog.close();assert dialog.isVisible()
    finally:gate.set()
    qtbot.waitUntil(lambda:not dialog.busy)
    assert w.provider_combo.isEnabled() and w.model_combo.isEnabled()


def test_provider_without_models_survives_restart_and_direction_inheritance(qtbot,tmp_path):
    from matknow.profiles import ProfileStore
    from matknow.settings import ProfileDialog
    store,_,w=fresh_window(qtbot,tmp_path)
    provider=store.models.save_provider(None,'厂商','https://one.example',[])
    w._reload_service();w.change_provider(w.provider_combo.findData(provider.id))
    assert ProfileStore(tmp_path).get(store.current_id).provider==provider.id
    dialog=ProfileDialog(store,parent=w);qtbot.addWidget(dialog);dialog.name_edit.setText('新方向');dialog.save()
    assert dialog.saved_profile.provider==provider.id and dialog.saved_profile.model is None


def test_duplicate_provider_validation_does_not_overwrite_existing_key(qtbot,tmp_path):
    store,credentials,w=fresh_window(qtbot,tmp_path)
    provider=store.models.save_provider(None,'厂商','https://one.example',['a'])
    credentials.for_endpoint(provider.url).key='FAKE-original-key';w.use_model(provider.models[0].id)
    dialog=ModelSettingsDialog(store,store.current_id,credentials,w);qtbot.addWidget(dialog);dialog.new_provider()
    dialog.provider_edit.setText(provider.name);dialog.url_edit.setText(provider.url)
    dialog.key_edit.setText('FAKE-unintended-key');dialog.save()
    assert '已存在' in dialog.status_label.text()
    assert credentials.for_endpoint(provider.url).key=='FAKE-original-key'
    assert w.service.model_client._key=='FAKE-original-key'
