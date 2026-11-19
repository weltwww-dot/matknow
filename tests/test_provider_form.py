from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit
import pytest
import sys
import json

from matknow.settings import ModelSettingsDialog
from tests.test_custom_only import fresh_window


def saved_dialog(qtbot,tmp_path):
    store,credentials,w=fresh_window(qtbot,tmp_path)
    provider=store.models.save_provider(None,'DeepSeek','https://one.example',['a'])
    credentials.for_endpoint(provider.url).key='FAKE-saved-key';w.use_model(provider.models[0].id)
    dialog=ModelSettingsDialog(store,store.current_id,credentials,w);qtbot.addWidget(dialog)
    return store,credentials,w,provider,dialog


def test_saved_key_has_visible_mask_after_reopen_and_unchanged_save(qtbot,tmp_path,monkeypatch):
    store,credentials,_,provider,dialog=saved_dialog(qtbot,tmp_path)
    assert dialog.key_edit.text() and dialog.key_edit.text()!='FAKE-saved-key'
    assert dialog.key_edit.echoMode()==QLineEdit.EchoMode.Password
    assert 'FAKE-saved-key' not in dialog.key_edit.displayText()
    calls=[];monkeypatch.setattr('matknow.settings.list_provider_models',lambda key,url:calls.append(key) or ['a'])
    dialog.save();qtbot.waitUntil(lambda:not dialog.busy)
    assert dialog.key_edit.text() and credentials.for_endpoint(provider.url).key=='FAKE-saved-key'
    assert calls==['FAKE-saved-key']
    assert 'FAKE-saved-key' not in (tmp_path/'models.json').read_text(encoding='utf8')


def test_url_change_clears_saved_mask_and_never_carries_old_key(qtbot,tmp_path):
    _,credentials,w,provider,dialog=saved_dialog(qtbot,tmp_path)
    dialog.url_edit.setText('https://two.example/v1')
    assert dialog.key_edit.text()==''
    dialog.save()
    assert w.service.model_client is None
    assert credentials.for_endpoint(provider.url).key=='FAKE-saved-key'
    assert credentials.for_endpoint('https://two.example/v1').get() is None


def test_key_replacement_and_provider_switch_do_not_store_mask(qtbot,tmp_path,monkeypatch):
    store,credentials,w,provider,dialog=saved_dialog(qtbot,tmp_path)
    monkeypatch.setattr('matknow.settings.list_provider_models',lambda *args:['a'])
    dialog.key_edit.setText('FAKE-replacement');dialog.save();qtbot.waitUntil(lambda:not dialog.busy)
    assert credentials.for_endpoint(provider.url).key=='FAKE-replacement' and dialog.key_edit.text()!='FAKE-replacement'
    dialog.new_provider();assert dialog.key_edit.text()==''


@pytest.mark.parametrize('position',[0,16,32])
def test_paste_replaces_saved_mask_without_manual_clear(qtbot,tmp_path,monkeypatch,position):
    store,credentials,_,provider,dialog=saved_dialog(qtbot,tmp_path)
    calls=[]
    monkeypatch.setattr('matknow.settings.list_provider_models',lambda key,url:calls.append(key) or ['a'])
    dialog.show();dialog.key_edit.setFocus();dialog.key_edit.deselect()
    dialog.key_edit.setCursorPosition(position)
    from PySide6.QtWidgets import QApplication
    clipboard=QApplication.clipboard();clipboard.setText('FAKE-fresh-secret')
    try:
        qtbot.keyClick(dialog.key_edit,Qt.Key.Key_V,Qt.KeyboardModifier.ControlModifier)
        dialog.save();qtbot.waitUntil(lambda:not dialog.busy)
        assert calls==['FAKE-fresh-secret']
        assert credentials.for_endpoint(provider.url).get()=='FAKE-fresh-secret'
        dialog.load_entry();dialog.test_connection();qtbot.waitUntil(lambda:not dialog.busy)
        assert calls==['FAKE-fresh-secret']*2
        assert dialog.key_edit.text()==dialog.SAVED_KEY_MASK
        assert 'FAKE-fresh-secret' not in (tmp_path/'models.json').read_text(encoding='utf8')
    finally:clipboard.clear()


def test_typing_replaces_saved_mask_and_normal_edits_keep_new_key(qtbot,tmp_path):
    _,credentials,_,provider,dialog=saved_dialog(qtbot,tmp_path)
    dialog.show();dialog.key_edit.setFocus();dialog.key_edit.deselect()
    dialog.key_edit.setCursorPosition(len(dialog.key_edit.text()))
    qtbot.keyClicks(dialog.key_edit,'FAKE-new')
    assert dialog._resolve_key(provider.url)==('FAKE-new',True)
    qtbot.keyClick(dialog.key_edit,Qt.Key.Key_Backspace)
    assert dialog._resolve_key(provider.url)==('FAKE-ne',True)
    assert credentials.for_endpoint(provider.url).get()=='FAKE-saved-key'


@pytest.mark.parametrize('length',[2560,2561])
def test_replacement_key_length_is_validated_before_overwriting_saved_key(qtbot,tmp_path,monkeypatch,length):
    from PySide6.QtWidgets import QApplication
    _,credentials,_,provider,dialog=saved_dialog(qtbot,tmp_path)
    calls=[]
    monkeypatch.setattr('matknow.settings.list_provider_models',lambda key,url:calls.append(key) or ['a'])
    clipboard=QApplication.clipboard();clipboard.setText('x'*length)
    try:
        dialog.key_edit.setCursorPosition(len(dialog.key_edit.text()));dialog.key_edit.paste()
        dialog.save();qtbot.waitUntil(lambda:not dialog.busy)
        if length==2560:
            assert credentials.for_endpoint(provider.url).get()=='x'*length
            assert calls==['x'*length]
        else:
            assert credentials.for_endpoint(provider.url).get()=='FAKE-saved-key'
            assert calls==[] and '2560' in dialog.status_label.text()
    finally:clipboard.clear()


@pytest.mark.skipif(sys.platform!='win32',reason='Windows native credentials')
def test_native_saved_key_reaches_discovery_and_chat_unchanged(qtbot,tmp_path,monkeypatch):
    from matknow.credentials import CredentialStore
    from matknow.profiles import ProfileStore
    from matknow.llm import DeepSeekClient
    from tests.test_llm import install_transport,response
    store=ProfileStore(tmp_path);credentials=CredentialStore(tmp_path)
    captured=install_transport(monkeypatch,json.dumps({'data':[{'id':'deepseek-flash'}]}).encode())
    dialog=ModelSettingsDialog(store,store.current_id,credentials);qtbot.addWidget(dialog)
    try:
        dialog.url_edit.setText('https://api.deepseek.com')
        dialog.key_edit.setText('FAKE-native-original');dialog.save();qtbot.waitUntil(lambda:not dialog.busy)
        assert captured[0][0].get_header('Authorization')=='Bearer FAKE-native-original'
        dialog.key_edit.setCursorPosition(16)
        qtbot.keyClicks(dialog.key_edit,'FAKE-native-replacement')
        dialog.save();qtbot.waitUntil(lambda:not dialog.busy)
        assert captured[-1][0].full_url=='https://api.deepseek.com/models'
        assert captured[-1][0].get_header('Authorization')=='Bearer FAKE-native-replacement'
        assert credentials.get()=='FAKE-native-replacement'
        reopened=ModelSettingsDialog(store,store.current_id,CredentialStore(tmp_path));qtbot.addWidget(reopened)
        reopened.test_connection();qtbot.waitUntil(lambda:not reopened.busy)
        assert captured[-1][0].get_header('Authorization')=='Bearer FAKE-native-replacement'
        chat_requests=install_transport(monkeypatch,response('OK'))
        assert DeepSeekClient(credentials.get(),'deepseek-flash').test_connection()=='连接成功'
        assert chat_requests[0][0].full_url=='https://api.deepseek.com/chat/completions'
        assert chat_requests[0][0].get_header('Authorization')=='Bearer FAKE-native-replacement'
        assert 'FAKE-native-' not in (tmp_path/'models.json').read_text(encoding='utf8')
    finally:credentials.delete()


def test_url_suggests_provider_and_preserves_user_name(qtbot,tmp_path):
    store,credentials,w=fresh_window(qtbot,tmp_path)
    dialog=ModelSettingsDialog(store,store.current_id,credentials,w);qtbot.addWidget(dialog)
    dialog.url_edit.setText('https://api.deepseek.com');assert dialog.provider_edit.text()=='deepseek'
    dialog.url_edit.setText('https://api.openai.com/v1');assert dialog.provider_edit.text()=='openai'
    dialog.provider_edit.selectAll();qtbot.keyClicks(dialog.provider_edit,'My lab')
    dialog.url_edit.setText('https://api.other.example/v1');assert dialog.provider_edit.text()=='My lab'


def test_connection_test_uses_saved_key_without_saving_or_selecting_model(qtbot,tmp_path,monkeypatch):
    store,_,w,provider,dialog=saved_dialog(qtbot,tmp_path);calls=[]
    before=(tmp_path/'models.json').read_bytes()
    monkeypatch.setattr('matknow.settings.list_provider_models',lambda key,url:calls.append((key,url)) or ['a','b'])
    dialog.show();qtbot.mouseClick(dialog.test_button,Qt.MouseButton.LeftButton);qtbot.waitUntil(lambda:not dialog.busy)
    assert calls==[('FAKE-saved-key',provider.url)] and '连接成功' in dialog.status_label.text()
    assert (tmp_path/'models.json').read_bytes()==before
    assert not hasattr(dialog,'model_combo') and w.model_combo.count()==1


def test_unsaved_connection_test_uses_typed_key_and_recovers_after_error(qtbot,tmp_path,monkeypatch):
    from matknow.llm import ModelError
    store,credentials,w=fresh_window(qtbot,tmp_path)
    dialog=ModelSettingsDialog(store,store.current_id,credentials,w);qtbot.addWidget(dialog)
    dialog.url_edit.setText('https://api.deepseek.com');dialog.key_edit.setText('FAKE-unsaved-key')
    def fail(key,url):assert key=='FAKE-unsaved-key';raise ModelError('无法读取模型目录')
    monkeypatch.setattr('matknow.settings.list_provider_models',fail)
    dialog.test_connection();qtbot.waitUntil(lambda:not dialog.busy)
    assert '无法读取' in dialog.status_label.text() and dialog.test_button.isEnabled()
    assert store.models.list_providers()==[] and credentials.get() is None


def test_switching_saved_providers_updates_mask_for_each_endpoint(qtbot,tmp_path):
    store,_,_,_,dialog=saved_dialog(qtbot,tmp_path)
    other=store.models.save_provider(None,'Other','https://two.example',['b'])
    dialog.refresh_entries()
    item=next(dialog.provider_list.item(i) for i in range(dialog.provider_list.count())
              if dialog.provider_list.item(i).data(Qt.ItemDataRole.UserRole)==other.id)
    dialog.provider_list.setCurrentItem(item)
    assert dialog.key_edit.text()=='' and dialog.provider_edit.text()=='Other'


def test_missing_saved_key_removes_mask_and_prevents_connection_test(qtbot,tmp_path):
    _,credentials,_,provider,dialog=saved_dialog(qtbot,tmp_path)
    credentials.for_endpoint(provider.url).delete();dialog.test_connection()
    assert not dialog.busy and dialog.key_edit.text()==''
    assert 'API Key' in dialog.status_label.text()


def test_url_names_handle_ips_and_compound_domains_without_external_lookup():
    from matknow.model_config import provider_name_from_url
    assert provider_name_from_url('http://127.0.0.1:11434/v1')=='127.0.0.1'
    assert provider_name_from_url('https://api.vendor.com.cn/v1')=='vendor'
    assert provider_name_from_url('https://www.api.vendor.co.uk/v1')=='vendor'
