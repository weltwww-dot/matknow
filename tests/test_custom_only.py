import json

from PySide6.QtCore import Qt

from matknow.desktop import MainWindow
from matknow.profiles import ProfileStore
from matknow.settings import ModelSettingsDialog, ProfileDialog
from tests.test_desktop import MemoryCredentials


def fresh_window(qtbot,tmp_path):
    store=ProfileStore(tmp_path);credentials=MemoryCredentials()
    window=MainWindow(store.knowledge(store.current_id),store,credentials)
    qtbot.addWidget(window);window.show()
    return store,credentials,window


def test_empty_selectors_allow_settings_and_save_first_custom_provider(qtbot,tmp_path,monkeypatch):
    store,credentials,w=fresh_window(qtbot,tmp_path)
    assert w.current_config is None and w.service.model_client is None
    assert w.provider_combo.count()==0 and w.model_combo.count()==0
    assert not w.provider_combo.isEnabled() and not w.model_combo.isEnabled()
    assert w.model_settings_button.isEnabled()
    dialog=ModelSettingsDialog(store,store.current_id,credentials,w);qtbot.addWidget(dialog)
    assert dialog.editing_id is None and not hasattr(dialog,'model_combo')
    monkeypatch.setattr('matknow.settings.list_provider_models',lambda *args:['my-model'])
    dialog.provider_edit.setText('自己的厂商');dialog.url_edit.setText('https://lab.example/v1')
    dialog.key_edit.setText('FAKE-custom-key');dialog.save();qtbot.waitUntil(lambda:not dialog.busy)
    assert w.current_config.provider=='自己的厂商'
    assert w.service.model_client.endpoint=='https://lab.example/v1/chat/completions'
    assert [entry.model for entry in store.models.list_models()]==['my-model']
    assert 'FAKE-custom-key' not in (tmp_path/'models.json').read_text(encoding='utf8')


def test_no_model_does_not_block_directions(qtbot,tmp_path):
    store,_,w=fresh_window(qtbot,tmp_path)
    dialog=ProfileDialog(store,parent=w);qtbot.addWidget(dialog)
    dialog.name_edit.setText('新研究方向');dialog.save()
    assert dialog.saved_profile.model is None
    assert w.switch_profile(dialog.saved_profile.id)
    assert w.current_config is None
    assert w.service.model_client is None


def legacy_profile(tmp_path):
    store=ProfileStore(tmp_path)
    path=tmp_path/'profiles.json';data=json.loads(path.read_text(encoding='utf8'))
    data['profiles'][0]['model']='deepseek-flash'
    path.write_text(json.dumps(data),encoding='utf8')
    return ProfileStore(tmp_path)


def test_old_direction_without_key_starts_without_preset_options(qtbot,tmp_path):
    store=legacy_profile(tmp_path);credentials=MemoryCredentials()
    w=MainWindow(store.knowledge(store.current_id),store,credentials);qtbot.addWidget(w)
    assert w.current_config is None and store.models.list_models()==[]
    dialog=ModelSettingsDialog(store,store.current_id,credentials,w);qtbot.addWidget(dialog)
    assert dialog.editing_id is None and dialog.provider_edit.text()==''


def test_old_saved_key_keeps_selected_model_without_old_connection_controls(qtbot,tmp_path):
    store=legacy_profile(tmp_path);credentials=MemoryCredentials();credentials.key='FAKE-legacy-key'
    (tmp_path/'model-disabled').touch()
    w=MainWindow(store.knowledge(store.current_id),store,credentials);qtbot.addWidget(w)
    assert [entry.model for entry in store.models.list_models()]==['deepseek-flash']
    assert w.current_config.model=='deepseek-flash' and w.service.model_client is not None
    assert credentials.key=='FAKE-legacy-key'
    assert ProfileStore(tmp_path).get(store.current_id).model=='deepseek-flash'


def test_fresh_api_does_not_infer_vendor_from_environment_key(tmp_path,monkeypatch):
    from matknow.api import build_profile_service
    monkeypatch.setenv('DEEPSEEK_API_KEY','FAKE-unused-key')
    monkeypatch.setenv('MATKNOW_MODEL_API_KEY','FAKE-unused-key')
    assert build_profile_service(tmp_path).model_client is None




def test_legacy_api_retains_only_its_configured_model(tmp_path):
    from matknow.api import build_profile_service
    store=legacy_profile(tmp_path)
    service=build_profile_service(tmp_path,api_key='FAKE-legacy-api-key')
    assert service.model_client.model=='deepseek-flash'
    assert [entry.model for entry in store.models.list_models()]==['deepseek-flash']
    assert 'FAKE-legacy-api-key' not in (tmp_path/'models.json').read_text(encoding='utf8')


def test_legacy_retention_write_failure_preserves_registry_and_key(qtbot,tmp_path,monkeypatch):
    store=legacy_profile(tmp_path);credentials=MemoryCredentials();credentials.key='FAKE-kept-key'
    original=(tmp_path/'profiles.json').read_bytes()
    def fail(*args):raise OSError('disk full')
    monkeypatch.setattr(store.models,'retain_legacy',fail)
    w=MainWindow(store.knowledge(store.current_id),store,credentials);qtbot.addWidget(w)
    assert w.service.model_client is None and '权限' in w.model_problem
    assert (tmp_path/'profiles.json').read_bytes()==original
    assert credentials.key=='FAKE-kept-key' and not (tmp_path/'models.json').exists()


def test_all_configured_legacy_directions_migrate_together(qtbot,tmp_path):
    store=legacy_profile(tmp_path)
    other=store.create('另一旧方向',model='deepseek-v4-pro')
    credentials=MemoryCredentials();credentials.key='FAKE-shared-legacy-key'
    w=MainWindow(store.knowledge(store.current_id),store,credentials);qtbot.addWidget(w)
    assert store.get(other.id).model=='deepseek-v4-pro'
    assert {m.model for m in store.models.list_models()}=={'deepseek-flash','deepseek-v4-pro'}
    assert w.switch_profile(other.id) and w.service.model_client.model=='deepseek-v4-pro'


def test_legacy_credential_read_failure_cannot_discard_old_selections(qtbot,tmp_path):
    from matknow.credentials import CredentialError
    store=legacy_profile(tmp_path);other=store.create('另一旧方向',model='deepseek-v4-pro')
    class BrokenCredentials(MemoryCredentials):
        def get(self):raise CredentialError('凭据暂不可读')
    w=MainWindow(store.knowledge(store.current_id),store,BrokenCredentials());qtbot.addWidget(w)
    assert not (tmp_path/'models.json').exists() and store.get(other.id).model=='deepseek-v4-pro'
    dialog=ModelSettingsDialog(store,store.current_id,BrokenCredentials(),w);qtbot.addWidget(dialog)
    dialog.provider_edit.setText('新厂商');dialog.url_edit.setText('https://new.example')
    dialog.save()
    assert not (tmp_path/'models.json').exists() and '不可读' in dialog.status_label.text()
