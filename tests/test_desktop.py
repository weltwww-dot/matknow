import pytest
from PySide6.QtCore import Qt

from matknow.desktop import MainWindow
from matknow.knowledge import KnowledgeBase


def connection_dialog(qtbot,store,credentials,window):
    from matknow.settings import ModelSettingsDialog
    dialog=ModelSettingsDialog(store,store.current_id,credentials,window)
    qtbot.addWidget(dialog);dialog.show()
    return dialog






class MemoryCredentials:
    def __init__(self):
        self.key = None
        self.endpoints = {}
    def get(self):
        return self.key
    def set(self, key):
        self.key = key
    def delete(self):
        self.key = None
    def for_endpoint(self,url):
        from matknow.model_config import completion_url
        endpoint=completion_url(url)
        if endpoint in ('https://api.deepseek.com/chat/completions','https://api.deepseek.com/v1/chat/completions'):return self
        return self.endpoints.setdefault(endpoint,MemoryCredentials())




def test_editing_vendor_url_does_not_reuse_existing_key(qtbot,tmp_path):
    store,credentials,window=make_profile_window(qtbot,tmp_path)
    entry=store.models.save(None,'实验厂商','https://lab.example/v1','lab-model')
    credentials.for_endpoint(entry.url).key='FAKE-lab-key'
    profile=store.get(store.current_id);store.update(profile.id,profile.name,profile.description,entry.id)
    window._reload_service()
    dialog=connection_dialog(qtbot,store,credentials,window)
    dialog.url_edit.setText('https://other.example/v1');dialog.save()
    assert window.service.model_client is None
    assert credentials.for_endpoint('https://lab.example/v1').get()=='FAKE-lab-key'




def test_key_save_followed_by_catalog_write_failure_refreshes_active_client(qtbot,tmp_path,monkeypatch):
    store,credentials,window=make_profile_window(qtbot,tmp_path)
    credentials.key='FAKE-old-key';window._reload_service()
    dialog=connection_dialog(qtbot,store,credentials,window)
    dialog.key_edit.setText('FAKE-new-key')
    def fail(*args):raise OSError('disk full')
    monkeypatch.setattr(store.models,'save_provider',fail)
    dialog.save()
    assert credentials.key=='FAKE-new-key'
    assert window.service.model_client._key=='FAKE-new-key'
    assert '未完整保存' in dialog.status_label.text()




def test_chat_runs_agent_tools_shows_summary_and_restores_it_from_history(qtbot,tmp_path,monkeypatch):
    import json
    from matknow.llm import DeepSeekClient
    _,credentials,window=make_profile_window(qtbot,tmp_path)
    credentials.key='FAKE-agent-ui-key'
    window.knowledge.import_text('制备方法','乙醇浸泡24 h。')
    calls=[]
    def complete(client,messages,json_output=False):
        context=json.loads(messages[-1]['content'])
        if 'tools' not in context:return json.dumps({'mode':'research'})
        calls.append(context)
        if len(calls)==1:return json.dumps({'plan':['查找置换步骤','核对原文'],'action':{'tool':'search_documents','arguments':{'query':'乙醇浸泡'}}})
        source=context['sources'][0]
        return json.dumps({'answer':'乙醇浸泡24 h [1]。','source_ids':[source['id']],'status':'generated'})
    monkeypatch.setattr(DeepSeekClient,'complete',complete)
    window.question_edit.setPlainText('溶剂置换流程');window.ask();qtbot.waitUntil(lambda:not window.busy)
    assert len(calls)==2 and window.last_answer.status=='generated'
    assert '处理步骤' in window.answer_view.toPlainText() and '已完成' in window.answer_view.toPlainText()
    run=window.last_answer.agent_run
    window.open_history(window.history_list.item(0))
    assert window.last_answer.agent_run==run and '处理步骤' in window.answer_view.toPlainText()


def test_document_agent_with_experiment_citations_keeps_originating_workspace(qtbot,tmp_path):
    from matknow.models import Answer
    from matknow.experiments.models import ExperimentDraft,SampleDraft
    _,_,window=make_profile_window(qtbot,tmp_path)
    store=window.experiment_page.store;store.save(ExperimentDraft('批次','2026-10-14',samples=[SampleDraft('A1')]))
    source=store.search('A1')[0]
    answer=Answer('mixed','问题','回答 [1]','generated','now',[source],agent_run={'plan':[],'steps':[],'workspace':'documents','outcome':'completed'})
    window.present_answer(answer)
    # Opening an actual experiment citation intentionally switches workspace; presentation must not.
    assert not hasattr(window.experiment_page,'answer_view')
    assert window.answer_view.isVisible()
    assert window.workspaces.currentIndex()==0
    window.show_source(source.id)
    assert window.workspaces.currentIndex()==1
    answer.agent_run['workspace']='experiments'
    window.present_answer(answer)
    assert window.answer_view.isVisible() and window.question_edit.isVisible()
    assert '处理步骤' in window.answer_view.toPlainText()


















def make_profile_window(qtbot, tmp_path):
    from matknow.profiles import ProfileStore
    store = ProfileStore(tmp_path)
    # These tests exercise models explicitly configured by the user.
    for model in ('deepseek-flash','deepseek-v4-pro'):
        store.models.save(model,'深度求索','https://api.deepseek.com',model)
    profile=store.get(store.current_id)
    store.update(profile.id,profile.name,profile.description,'deepseek-flash')
    credentials = MemoryCredentials()
    window = MainWindow(store.knowledge(store.current_id), profiles=store, credentials=credentials)
    qtbot.addWidget(window)
    window.show()
    return store, credentials, window


def test_question_model_selection_persists_per_profile_and_drives_answers(qtbot, tmp_path, monkeypatch):
    import json
    from matknow.llm import DeepSeekClient
    from matknow.profiles import ProfileStore

    store, credentials, window = make_profile_window(qtbot, tmp_path)
    original = store.current_id
    other = store.create('另一方向',model=store.get(original).model)
    credentials.key = 'FAKE-model-selection-key'
    window.knowledge.import_text('方法.txt', '干燥温度 60 °C。')
    window.refresh_documents()

    def complete(client, messages, json_output=False):
        context = json.loads(messages[-1]['content'])
        if 'tools' not in context:return json.dumps({'mode':'research'})
        source = context['sources'][0]
        return json.dumps({'answer': client.model + ' [1]', 'source_ids': [source['id']], 'status': 'generated'})

    monkeypatch.setattr(DeepSeekClient, 'complete', complete)
    qtbot.keyClick(window.model_combo, Qt.Key.Key_End)
    assert store.get(original).model == 'deepseek-v4-pro'
    assert ProfileStore(tmp_path).get(original).model == 'deepseek-v4-pro'
    window.question_edit.setPlainText('干燥温度')
    qtbot.mouseClick(window.ask_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not window.busy and window.last_answer is not None)
    assert window.last_answer.status == 'generated'
    assert 'deepseek-v4-pro' in window.last_answer.answer
    assert window.switch_profile(other.id)
    assert window.model_combo.currentText() == 'deepseek-flash'
    assert window.switch_profile(original)
    assert window.model_combo.currentText() == 'deepseek-v4-pro'


def test_failed_question_model_save_restores_previous_selection(qtbot, tmp_path, monkeypatch):
    store, credentials, window = make_profile_window(qtbot, tmp_path)

    def fail(*args):
        raise OSError('disk unavailable')

    monkeypatch.setattr(store, 'update', fail)
    qtbot.keyClick(window.model_combo, Qt.Key.Key_End)
    assert window.model_combo.currentText() == 'deepseek-flash'
    assert store.get(store.current_id).model == 'deepseek-flash'
    assert '未切换' in window.status_label.text()


def test_query_scope_accepts_multiple_row_clicks_without_ctrl(qtbot, tmp_path):
    kb, window = make_window(qtbot, tmp_path)
    documents = [kb.import_text(name, f'干燥条件 {temperature} °C。')
                 for name, temperature in [('A.txt', 60), ('B.txt', 80), ('C.txt', 100)]]
    window.refresh_documents()
    for index in [0, 1]:
        item = window.document_list.item(index)
        qtbot.mouseClick(window.document_list.viewport(), Qt.MouseButton.LeftButton,
                         pos=window.document_list.visualItemRect(item).center())
    assert len(window.document_list.selectedItems()) == 2
    window.question_edit.setPlainText('干燥条件')
    qtbot.mouseClick(window.ask_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not window.busy and window.last_answer is not None)
    assert {source.document_id for source in window.last_answer.sources} == {documents[0].id, documents[1].id}
    first = window.document_list.item(0)
    qtbot.mouseClick(window.document_list.viewport(), Qt.MouseButton.LeftButton,
                     pos=window.document_list.visualItemRect(first).center())
    assert window.document_list.selectedItems() == [window.document_list.item(1)]
    window.clear_scope()
    assert not window.document_list.selectedItems()
    assert '全部资料' in window.scope_label.text()


def test_profile_switch_clears_old_ui_and_loads_own_history(qtbot, tmp_path):
    from matknow.service import AskService
    store, credentials, window = make_profile_window(qtbot, tmp_path)
    original_id = store.current_id
    window.knowledge.import_text('方法.txt', '干燥 PRIVATE_A 60 °C。')
    window.refresh_documents()
    window.question_edit.setPlainText('干燥')
    window.present_answer(window.service.ask('干燥'))
    other = store.create('生物实验')
    other_kb = store.knowledge(other.id)
    other_kb.import_text('方法.txt', '干燥 PRIVATE_B 25 °C。')
    AskService(other_kb).ask('干燥 B')
    assert window.switch_profile(other.id)
    assert window.question_edit.toPlainText() == ''
    assert window.last_answer is None
    assert 'PRIVATE_A' not in window.answer_view.toPlainText()
    assert 'PRIVATE_A' not in window.source_view.toPlainText()
    assert window.history_list.count() == 1
    assert window.history_list.item(0).text() == '干燥 B'
    assert not window.export_button.isEnabled()
    assert store.current_id == other.id
    window.open_history(window.history_list.item(0))
    assert 'PRIVATE_B' in window.source_view.toPlainText()
    assert window.switch_profile(original_id)
    assert window.history_list.item(0).text() == '干燥'


def test_busy_and_delete_mode_block_profile_switch(qtbot, tmp_path):
    store, credentials, window = make_profile_window(qtbot, tmp_path)
    current = store.current_id
    other = store.create('另一个方向')
    window.knowledge.import_text('方法.txt', '干燥条件')
    window.refresh_documents()
    window.set_deletion_mode(True)
    assert not window.profile_combo.isEnabled()
    assert not window.model_combo.isEnabled()
    assert not window.switch_profile(other.id)
    window.set_deletion_mode(False)
    import threading
    release = threading.Event()
    window.start_task(lambda: release.wait(5), lambda result: None, '测试后台任务')
    assert not window.profile_combo.isEnabled()
    assert not window.model_settings_button.isEnabled()
    assert not window.model_combo.isEnabled()
    assert not window.switch_profile(other.id)
    assert store.current_id == current
    release.set()
    qtbot.waitUntil(lambda: not window.busy)
    assert window.profile_combo.isEnabled()
    assert window.model_combo.isEnabled()


def test_failed_profile_switch_keeps_old_workspace(qtbot, tmp_path, monkeypatch):
    store, credentials, window = make_profile_window(qtbot, tmp_path)
    current = window.knowledge
    window.question_edit.setPlainText('保留问题')
    other = store.create('损坏方向')
    def fail(profile_id):
        raise OSError('unavailable')
    monkeypatch.setattr(store, 'knowledge', fail)
    assert not window.switch_profile(other.id)
    assert window.knowledge is current
    assert window.question_edit.toPlainText() == '保留问题'
    assert store.current_id != other.id


def test_corrupt_registry_during_switch_keeps_selector_usable(qtbot, tmp_path):
    store, credentials, window = make_profile_window(qtbot, tmp_path)
    original = store.current_id
    other = store.create('测试方向')
    window._refresh_profile_combo()
    store.path.write_text('{', encoding='utf-8')
    assert not window.switch_profile(other.id)
    assert window.profile_id == original
    assert window.profile_combo.currentData() == original
    assert window.profile_combo.count() == 2
    assert not window.profile_combo.signalsBlocked()
    assert '切换未完成' in window.status_label.text()




def test_profile_dialog_persists_description_and_inherits_chat_model(qtbot, tmp_path):
    from matknow.settings import ProfileDialog
    store, credentials, window = make_profile_window(qtbot, tmp_path)
    window.use_model('deepseek-v4-pro')
    dialog = ProfileDialog(store, parent=window)
    qtbot.addWidget(dialog)
    dialog.name_edit.setText('电化学')
    dialog.description_edit.setPlainText('分别核对电极样品与测试条件。')
    dialog.save()
    profile = store.get(dialog.saved_profile.id)
    assert profile.description == '分别核对电极样品与测试条件。'
    assert profile.model == 'deepseek-v4-pro'


def test_model_error_and_numbered_multiline_answer_are_visible(qtbot, tmp_path):
    from matknow.models import Answer
    kb, window = make_window(qtbot, tmp_path)
    kb.import_text('A.txt', '干燥温度 60 °C。')
    sources = kb.search('干燥')
    answer = Answer('test-answer', '干燥', '第一行 [1]\n第二行 <b>纯文本</b>', 'generated', '', sources)
    kb.save_answer(answer)
    window.present_answer(answer)
    assert '\n第二行 <b>纯文本</b>' in window.answer_view.toPlainText()
    assert '模型回答' in window.status_label.text()
    answer.status = 'model_error'
    window.present_answer(answer)
    assert '失败' in window.status_label.text()


def test_deleted_first_source_does_not_renumber_remaining_citations(qtbot, tmp_path):
    from matknow.models import Answer
    kb, window = make_window(qtbot, tmp_path)
    first = kb.import_text('A.txt', '干燥条件 60 °C。')
    kb.import_text('B.txt', '干燥条件 80 °C。')
    answer = Answer('fixed-numbers', '干燥条件', '第一份 [1]，第二份 [2]。', 'generated', '', kb.search('干燥条件'))
    kb.save_answer(answer)
    kb.delete_document(first.id)
    window.present_answer(kb.list_history()[0])
    assert '[2] B.txt' in window.answer_view.toPlainText()
    assert '[1] B.txt' not in window.answer_view.toPlainText()


def test_credential_failure_is_explicit_and_never_shown_as_unconfigured(qtbot, tmp_path):
    from matknow.credentials import CredentialError
    from matknow.profiles import ProfileStore
    store = ProfileStore(tmp_path)
    entry=store.models.save(None,'厂商','https://vendor.example/v1','test-model')
    profile=store.get(store.current_id);store.update(profile.id,profile.name,profile.description,entry.id)
    class BrokenCredentials:
        def get(self):
            raise CredentialError('无法读取 Windows 凭据。')
    window = MainWindow(store.knowledge(store.current_id), store, BrokenCredentials())
    qtbot.addWidget(window)
    assert '失败' in window.model_state.text()
    assert '无法读取' in window.status_label.text()


def test_instance_lock_prevents_two_desktop_writers(tmp_path):
    from matknow.__main__ import lock_data_dir
    import pytest
    first = lock_data_dir(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            lock_data_dir(tmp_path)
    finally:
        first.unlock()
    reopened = lock_data_dir(tmp_path)
    reopened.unlock()


@pytest.mark.parametrize('delete_before_present', [True, False])
def test_export_warns_when_generated_source_was_deleted(qtbot, tmp_path, monkeypatch, delete_before_present):
    from PySide6.QtWidgets import QFileDialog
    from matknow.models import Answer
    kb, window = make_window(qtbot, tmp_path)
    doc = kb.import_text('A.txt', '干燥条件 60 °C。')
    answer = Answer('export-warning', '干燥', '答案仍有派生文字 [1]', 'generated', '', kb.search('干燥'))
    kb.save_answer(answer)
    if delete_before_present:
        kb.delete_document(doc.id)
    window.present_answer(kb.list_history()[0] if delete_before_present else answer)
    if not delete_before_present:
        kb.delete_document(doc.id)
    target = tmp_path / 'record.md'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: (str(target), ''))
    window.export_answer()
    report = target.read_text(encoding='utf-8')
    assert '来源已删除' in report
    assert '重新核对' in report


def test_new_profile_database_error_is_visible(qtbot, tmp_path, monkeypatch):
    import sqlite3
    from matknow.profiles import ProfileStore
    from matknow.settings import ProfileDialog
    store = ProfileStore(tmp_path)
    dialog = ProfileDialog(store)
    qtbot.addWidget(dialog)
    dialog.name_edit.setText('失败方向')
    def fail(*args,**kwargs):
        raise sqlite3.OperationalError('disk full')
    monkeypatch.setattr(store, 'create', fail)
    dialog.save()
    assert '未保存' in dialog.status_label.text()
    assert dialog.saved_profile is None


def make_window(qtbot, tmp_path):
    kb = KnowledgeBase(tmp_path / "kb.sqlite3")
    window = MainWindow(kb)
    qtbot.addWidget(window)
    window.show()
    return kb, window


def test_ask_uses_real_sources_and_keeps_html_as_text(qtbot, tmp_path):
    kb, window = make_window(qtbot, tmp_path)
    doc = kb.import_text("教学.txt", "干燥温度为 60 °C。<b>原文不是界面标签</b>")
    window.refresh_documents()
    window.question_edit.setPlainText("干燥温度是多少？")
    qtbot.mouseClick(window.ask_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not window.busy and window.last_answer is not None)
    assert window.last_answer.sources[0].document_id == doc.id
    assert "检索预览" in window.answer_view.toPlainText()
    assert "<b>原文不是界面标签</b>" in window.source_view.toPlainText()
    window.show_source(window.last_answer.sources[0].id)
    assert "60 °C" in window.source_view.toPlainText()
    assert "<b>原文不是界面标签</b>" in window.source_view.toPlainText()
    assert window.history_list.count() == 1


def test_failed_background_task_recovers_ui(qtbot, tmp_path):
    kb, window = make_window(qtbot, tmp_path)

    def fail():
        kb.import_text("空文件.txt", " ")

    window.start_task(fail, lambda result: None, "正在导入")
    qtbot.waitUntil(lambda: not window.busy)
    assert window.ask_button.isEnabled()
    assert "文字" in window.status_label.text()
    assert kb.list_documents() == []


def test_empty_question_shows_inline_feedback(qtbot, tmp_path):
    kb, window = make_window(qtbot, tmp_path)
    qtbot.mouseClick(window.ask_button, Qt.MouseButton.LeftButton)
    assert not window.busy
    assert "请输入" in window.status_label.text()
    assert kb.list_history() == []


def test_desktop_initialization_provides_readable_chinese_font(qapp):
    from PySide6.QtGui import QFontMetrics
    from matknow.__main__ import configure_fonts

    configure_fonts(qapp)
    assert QFontMetrics(qapp.font()).inFontUcs4(ord("材"))


def test_delete_refreshes_answer_outside_recent_history(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    kb, window = make_window(qtbot, tmp_path)
    kb.import_text("A.txt", "干燥条件：原文标记 SHOULD_DISAPPEAR。")
    window.refresh_documents()
    window.present_answer(window.service.ask("干燥条件"))
    for _ in range(50):
        window.service.ask("干燥条件")
    # Only replace the modal user confirmation; deletion and both rendered panes remain real.
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    window.delete_selected()
    window.document_list.item(0).setSelected(True)
    window.delete_selected()
    assert kb.list_documents() == []
    assert "SHOULD_DISAPPEAR" not in window.answer_view.toPlainText()
    assert "SHOULD_DISAPPEAR" not in window.source_view.toPlainText()
    assert window.last_answer.unavailable_sources == 1


def test_delete_enters_clickable_multiselect_and_cancel_restores_query_scope(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QAbstractItemView, QMessageBox

    kb, window = make_window(qtbot, tmp_path)
    for name in ["A.txt", "B.txt", "C.txt"]:
        kb.import_text(name, name + " 干燥条件。")
    window.refresh_documents()
    window.document_list.item(2).setSelected(True)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)
    qtbot.mouseClick(window.delete_button, Qt.MouseButton.LeftButton)
    assert len(kb.list_documents()) == 3
    assert window.document_list.selectionMode() == QAbstractItemView.SelectionMode.MultiSelection
    assert not window.document_list.selectedItems()
    for index in [0, 1]:
        item = window.document_list.item(index)
        qtbot.mouseClick(window.document_list.viewport(), Qt.MouseButton.LeftButton, pos=window.document_list.visualItemRect(item).center())
    assert len(window.document_list.selectedItems()) == 2
    assert window.document_list.item(0).checkState() == Qt.CheckState.Checked
    assert window.document_list.item(1).checkState() == Qt.CheckState.Checked
    assert "2" in window.delete_button.text()
    qtbot.mouseClick(window.clear_selection_button, Qt.MouseButton.LeftButton)
    assert window.document_list.selectedItems() == [window.document_list.item(2)]
    assert window.document_list.selectionMode() == QAbstractItemView.SelectionMode.MultiSelection
    assert window.ask_button.isEnabled()
    assert window.document_list.item(0).data(Qt.ItemDataRole.CheckStateRole) is None


def test_confirm_delete_removes_multiple_rows_without_ctrl(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    kb, window = make_window(qtbot, tmp_path)
    docs = [kb.import_text(name, name + " 干燥条件。") for name in ["A.txt", "B.txt", "C.txt"]]
    window.refresh_documents()
    qtbot.mouseClick(window.delete_button, Qt.MouseButton.LeftButton)
    for index in [0, 1]:
        item = window.document_list.item(index)
        qtbot.mouseClick(window.document_list.viewport(), Qt.MouseButton.LeftButton, pos=window.document_list.visualItemRect(item).center())
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    qtbot.mouseClick(window.delete_button, Qt.MouseButton.LeftButton)
    assert [doc.id for doc in kb.list_documents()] == [docs[2].id]
    assert window.delete_button.text() == "删除所选"
    assert window.ask_button.isEnabled()


def test_unified_import_entry_keeps_file_text_and_examples(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QFileDialog, QLineEdit, QPlainTextEdit

    kb, window = make_window(qtbot, tmp_path)
    actions = window.import_button.menu().actions()
    file = tmp_path / "P001.txt"
    file.write_text("文件导入记录：干燥时间为 6 h。", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args, **kwargs: ([str(file)], ""))
    actions[0].trigger()
    qtbot.waitUntil(lambda: not window.busy)
    assert kb.list_documents()[0].filename == "P001.txt"

    def enter_text():
        dialog = QApplication.activeModalWidget()
        dialog.findChild(QLineEdit).setText("P002-文本摘录.txt")
        dialog.findChild(QPlainTextEdit).setPlainText("粘贴导入记录：样品标记 PASTED_SOURCE。")
        dialog.accept()

    QTimer.singleShot(0, enter_text)
    actions[1].trigger()
    qtbot.waitUntil(lambda: not window.busy)
    assert kb.search("PASTED_SOURCE")[0].filename == "P002-文本摘录.txt"

    actions[2].trigger()
    qtbot.waitUntil(lambda: not window.busy)
    assert len(kb.list_documents()) == 4
    assert any(doc.filename.startswith("DEMO-A") for doc in kb.list_documents())
