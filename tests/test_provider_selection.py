import pytest
from matknow.settings import ProfileDialog
from tests.test_desktop import make_profile_window, connection_dialog


def configured(qtbot,tmp_path):
    store,credentials,w=make_profile_window(qtbot,tmp_path)
    first=store.models.save(None,'厂商甲','https://one.example/v1','model-a')
    alternate=store.models.save(None,'厂商甲','https://one.example/v1','model-b')
    other=store.models.save(None,'厂商甲','https://other.example/v1','model-other')
    second=store.models.save(None,'厂商乙','https://two.example/v1','model-a')
    credentials.for_endpoint(first.url).key='FAKE-one-key'
    credentials.for_endpoint(second.url).key='FAKE-two-key'
    w.use_model(first.id)
    # Routing tests isolate selection from the separately tested directory refresh.
    w.refresh_provider_models=lambda:None
    return store,credentials,w,first,alternate,other,second


def test_direction_settings_only_edit_metadata_and_inherit_model(qtbot,tmp_path):
    store,_,w,first,*_=configured(qtbot,tmp_path)
    dialog=ProfileDialog(store,parent=w);qtbot.addWidget(dialog)
    assert not hasattr(dialog,'model_combo')
    dialog.name_edit.setText('新方向');dialog.save()
    assert dialog.saved_profile.model==first.id


def test_chat_has_provider_then_only_its_models_and_routes_keys(qtbot,tmp_path):
    store,_,w,first,alternate,other,second=configured(qtbot,tmp_path)
    assert w.provider_combo.currentData()==store.models.provider_for_model(first.id).id
    assert [w.model_combo.itemData(i) for i in range(w.model_combo.count())]==[first.id,alternate.id]
    w.change_model(w.model_combo.findData(alternate.id))
    assert w.service.model_client.model=='model-b' and w.service.model_client._key=='FAKE-one-key'
    w.change_provider(w.provider_combo.findData(store.models.provider_for_model(second.id).id))
    assert w.model_combo.count()==1 and w.model_combo.currentData()==second.id
    assert w.service.model_client._key=='FAKE-two-key'
    assert w.experiment_page.model_client is w.service.model_client
    w.change_provider(w.provider_combo.findData(store.models.provider_for_model(other.id).id))
    assert w.model_combo.currentData()==other.id and w.service.model_client is None


def test_settings_list_providers_and_refresh_their_models(qtbot,tmp_path,monkeypatch):
    store,credentials,w,first,alternate,*_=configured(qtbot,tmp_path)
    dialog=connection_dialog(qtbot,store,credentials,w)
    assert dialog.provider_list.count()==len(store.models.list_providers())
    assert not hasattr(dialog,'models_edit')
    monkeypatch.setattr('matknow.settings.list_provider_models',lambda *args:['model-a','model-b','model-c'])
    dialog.provider_edit.setText('厂商改名');dialog.save();qtbot.waitUntil(lambda:not dialog.busy)
    assert w.provider_combo.currentText()=='厂商改名' and w.model_combo.currentData()==first.id
    assert [w.model_combo.itemText(i) for i in range(w.model_combo.count())]==['model-a','model-b','model-c']
    assert store.models.get(alternate.id).provider=='厂商改名'


def test_settings_save_switches_chat_to_selected_provider(qtbot,tmp_path,monkeypatch):
    from PySide6.QtCore import Qt
    store,credentials,w,_,_,_,second=configured(qtbot,tmp_path)
    dialog=connection_dialog(qtbot,store,credentials,w)
    provider=store.models.provider_for_model(second.id)
    item=next(dialog.provider_list.item(i) for i in range(dialog.provider_list.count()) if dialog.provider_list.item(i).data(Qt.ItemDataRole.UserRole)==provider.id)
    monkeypatch.setattr('matknow.settings.list_provider_models',lambda *args:['model-a'])
    dialog.provider_list.setCurrentItem(item);dialog.save();qtbot.waitUntil(lambda:not dialog.busy)
    assert w.current_model==second.id and w.provider_combo.currentData()==provider.id
    assert w.service.model_client._key=='FAKE-two-key'


@pytest.mark.parametrize('blocked',['busy','deletion_mode'])
def test_both_selectors_are_blocked_during_tasks_or_deletion(qtbot,tmp_path,blocked):
    store,_,w,first,alternate,_,second=configured(qtbot,tmp_path)
    setattr(w,blocked,True);w._update_controls()
    assert not w.provider_combo.isEnabled() and not w.model_combo.isEnabled()
    w.change_provider(w.provider_combo.findData(store.models.provider_for_model(second.id).id))
    w.change_model(w.model_combo.findData(alternate.id))
    assert w.current_model==first.id
    setattr(w,blocked,False)


def test_same_named_providers_identify_endpoint_and_models_never_mix(qtbot,tmp_path):
    store,_,w,first,_,other,_=configured(qtbot,tmp_path)
    a=w.provider_combo.findData(store.models.provider_for_model(first.id).id)
    b=w.provider_combo.findData(store.models.provider_for_model(other.id).id)
    assert w.provider_combo.itemText(a)!=w.provider_combo.itemText(b)
    assert first.url in w.provider_combo.itemData(a,3) and other.url in w.provider_combo.itemData(b,3)


def test_removed_api_model_falls_back_and_other_direction_stays_usable(qtbot,tmp_path,monkeypatch):
    store,credentials,w,_,alternate,*_=configured(qtbot,tmp_path)
    other=store.create('其他方向',model=alternate.id)
    dialog=connection_dialog(qtbot,store,credentials,w)
    monkeypatch.setattr('matknow.settings.list_provider_models',lambda *args:['model-b'])
    dialog.save();qtbot.waitUntil(lambda:not dialog.busy)
    assert w.current_model==alternate.id and w.model_combo.count()==1
    assert store.get(other.id).model==alternate.id and w.switch_profile(other.id)


def test_delete_provider_clears_chat_and_key_without_deleting_knowledge(qtbot,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    store,credentials,w,first,*_=configured(qtbot,tmp_path)
    doc=w.knowledge.import_text('保留资料','干燥条件 60 °C。')
    provider=store.models.provider_for_model(first.id)
    dialog=connection_dialog(qtbot,store,credentials,w)
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.Yes)
    dialog.delete_provider()
    assert provider.id not in [p.id for p in store.models.list_providers()]
    assert w.current_model is None and w.service.model_client is None
    assert w.model_combo.count()==0 and not w.model_combo.isEnabled()
    assert w.knowledge.list_documents()[0].id==doc.id
    assert credentials.for_endpoint(first.url).key is None
