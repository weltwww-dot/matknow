from copy import deepcopy
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from matknow.desktop import MainWindow
from matknow.experiments.dialogs import RecordImportDialog, ExperimentDialog, ResultDialog, ComparisonDialog
from matknow.experiments.models import ExperimentDraft, SampleDraft, Value
from matknow.experiments.readers import read_record
from matknow.profiles import ProfileStore
from tests.experiment_fixtures import make_xlsx, make_docx


def window(qtbot, tmp_path):
    profiles = ProfileStore(tmp_path)
    w = MainWindow(profiles.knowledge(profiles.current_id), profiles=profiles, allow_model=False)
    qtbot.addWidget(w); w.show(); w.workspaces.setCurrentIndex(1)
    return w


def save_samples(w, title='批次'):
    return w.experiment_page.archive.save(ExperimentDraft(title, '2026-10-13', samples=[SampleDraft('A1'), SampleDraft('A2')]), [])


def test_import_confirm_multisample_append_and_compare_without_key(qtbot, tmp_path):
    w = window(qtbot, tmp_path); page = w.experiment_page
    p = tmp_path / 'record.docx'; make_docx(p)
    dialog = RecordImportDialog(read_record(p, file_id='record'), parent=w); qtbot.addWidget(dialog)
    dialog.source_path = p
    dialog.sample_codes.setText('A1,A2'); dialog.build_draft()
    dialog.confirm_check.setChecked(True); dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    snap = page.archive.save(dialog.draft, dialog.uploads)
    result = ResultDialog(snap, snap.draft.samples[0].id, parent=w); qtbot.addWidget(result)
    result.add_result(); result.grid.item(0,0).setText('强度'); result.grid.item(0,1).setText('1.2')
    result.grid.item(0,2).setText('MPa'); result.accept()
    second = page.archive.save(result.draft, result.uploads, experiment_id=snap.id, expected_revision=1)
    assert second.draft.samples[0].results[0].value.text == '1.2'
    compare = ComparisonDialog(page.store, [s.id for s in second.draft.samples]); qtbot.addWidget(compare)
    assert len(compare.comparison.sample_ids) == 2


def test_cancel_or_unresolved_formula_never_saves(qtbot, tmp_path):
    w = window(qtbot, tmp_path)
    p = tmp_path / 'record.xlsx'; make_xlsx(p)
    dialog = RecordImportDialog(read_record(p, file_id='record'), parent=w); qtbot.addWidget(dialog)
    dialog.reject()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert w.experiment_page.store.list_experiments() == []


def test_row_clicks_multiselect_across_batches_and_show_complete_scope(qtbot, tmp_path):
    w = window(qtbot, tmp_path); page = w.experiment_page
    save_samples(w); save_samples(w, '批次二'); page.refresh()
    for row in [0, 1]:
        qtbot.mouseClick(page.record_list.viewport(), Qt.MouseButton.LeftButton, pos=page.record_list.visualItemRect(page.record_list.item(row)).center())
    assert len(page.selected_sample_ids()) == 4
    assert '4' in page.scope_label.text()


def test_profile_switch_clears_draft_selection_preview_and_answer(qtbot, tmp_path):
    w = window(qtbot, tmp_path); save_samples(w); page = w.experiment_page; page.refresh()
    page.record_list.setCurrentRow(0); w.question_edit.setPlainText('旧问题'); page.source_view.setPlainText('PRIVATE_OLD')
    other = w.profiles.create('另一个方向')
    assert w.switch_profile(other.id)
    assert page.selected_sample_ids() == [] and not w.question_edit.toPlainText()
    assert 'PRIVATE' not in page.source_view.toPlainText()


def test_edit_conflict_keeps_newer_data_and_editable_form(qtbot, tmp_path):
    w = window(qtbot, tmp_path); snap = save_samples(w)
    dialog = ExperimentDialog(snap); qtbot.addWidget(dialog); dialog.show()
    draft = deepcopy(snap.draft); draft.title = '较新记录'
    w.experiment_page.archive.save(draft, [], experiment_id=snap.id, expected_revision=1)
    dialog.accept()
    with pytest.raises(ValueError, match='更新'):
        w.experiment_page.archive.save(dialog.draft, [], experiment_id=snap.id, expected_revision=1)
    assert w.experiment_page.store.get(snap.id).draft.title == '较新记录'


def test_result_and_import_busy_lock_chat_management_and_profile(qtbot, tmp_path):
    w = window(qtbot, tmp_path)
    entry=w.profiles.models.save(None,'厂商','https://one.example/v1','model-a');w.use_model(entry.id)
    w._settings_busy_changed(True)
    assert not w.profile_combo.isEnabled() and not w.model_combo.isEnabled()
    assert not w.experiment_page.isEnabled()
    w._settings_busy_changed(False)
    assert w.experiment_page.isEnabled() and w.model_combo.isEnabled()


def test_saved_model_syncs_between_workspaces_and_settings(qtbot, tmp_path):
    w = window(qtbot, tmp_path)
    first=w.profiles.models.save(None,'甲厂商','https://one.example/v1','model-a')
    second=w.profiles.models.save(None,'乙厂商','https://two.example/v1','model-b')
    w.use_model(first.id)
    combo = w.provider_combo
    qtbot.keyClick(combo, Qt.Key.Key_End)
    assert w.profiles.get(w.profile_id).model == second.id
    assert w.question_edit.isVisible() and not hasattr(w.experiment_page,'model_combo')


def test_history_citation_opens_exact_revision_and_managed_attachment(qtbot, tmp_path):
    w = window(qtbot, tmp_path); snap = save_samples(w); page = w.experiment_page
    old = page.store.search('A1')[0]
    draft = deepcopy(snap.draft); draft.notes = '新值'
    page.archive.save(draft, [], experiment_id=snap.id, expected_revision=1)
    page.show_evidence(old.id)
    assert '版本 1' in page.source_title.text()
    assert '新值' not in page.source_view.toPlainText()


def test_import_preview_failure_unlocks_ui_and_preserves_previous_record(qtbot, tmp_path):
    w = window(qtbot, tmp_path); save_samples(w)
    p = tmp_path / 'broken.docx'; p.write_bytes(b'broken')
    w.experiment_page.import_record(p)
    qtbot.waitUntil(lambda: not w.busy)
    assert len(w.experiment_page.store.list_experiments()) == 1
    assert w.profile_combo.isEnabled()


def test_excel_formula_requires_confirmation_and_keeps_cell_provenance(qtbot,tmp_path):
    w=window(qtbot,tmp_path);p=make_xlsx(tmp_path/'formula.xlsx',cached=2)
    dialog=RecordImportDialog(read_record(p,file_id='record'),parent=w);qtbot.addWidget(dialog);dialog.source_path=p
    dialog.map_grid.cellWidget(1,1).setCurrentIndex(3);dialog.map_grid.item(1,3).setText('MPa');dialog.build_draft()
    value=dialog.editor.samples[0].results[0].value
    assert value.text=='2' and not value.confirmed and value.source.locator=='Sheet1!B2'
    dialog.accept();assert dialog.result()!=QDialog.DialogCode.Accepted
    dialog.confirm_check.setChecked(True);dialog.accept()
    snap=w.experiment_page.archive.save(dialog.draft,dialog.uploads)
    assert snap.draft.samples[0].results[0].value.confirmed
    assert w.experiment_page.archive.path_for(snap.draft.source_ids[0]).read_bytes()==p.read_bytes()


def test_table_attachment_curve_and_numeric_cell_survive_reopen(qtbot,tmp_path):
    w=window(qtbot,tmp_path);page=w.experiment_page;snap=save_samples(w)
    p=tmp_path/'curve.csv';p.write_text('x,y\n0,1\n1,3\n2,2\n',encoding='utf8')
    dialog=ResultDialog(snap,snap.draft.samples[0].id,parent=w);qtbot.addWidget(dialog)
    dialog.attach_path(p);qtbot.waitUntil(lambda:not dialog.busy)
    dialog.bounds.setText('2,1,4,2');dialog.x_unit.setText('s');dialog.y_unit.setText('mA')
    dialog.preview_curve();qtbot.waitUntil(lambda:not dialog.busy)
    dialog.accept();saved=page.archive.save(dialog.draft,dialog.uploads,experiment_id=snap.id,expected_revision=1)
    recorded=saved.draft.samples[0].results[0]
    assert recorded.kind=='curve' and recorded.data['x_unit']=='s'
    assert recorded.data['attachment_id'] in recorded.attachment_ids
    reopened=ResultDialog(saved,saved.draft.samples[0].id,parent=w,archive=page.archive);qtbot.addWidget(reopened)
    reopened.show_attachment();qtbot.waitUntil(lambda:not reopened.busy)
    assert reopened.preview.table.cells[-1].text=='2'


@pytest.mark.parametrize('index',[0,1])
def test_remove_sample_loads_remaining_row_and_keeps_immediate_edits(qtbot,tmp_path,monkeypatch,index):
    from PySide6.QtWidgets import QMessageBox
    from matknow.experiments.editors import ExperimentEditor
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.Yes)
    editor=ExperimentEditor(ExperimentDraft('测试','2026-10-13',samples=[SampleDraft('A1',{'温度':Value('1')}),SampleDraft('A2',{'温度':Value('2')})]));qtbot.addWidget(editor)
    editor.sample_grid.setCurrentCell(index,0);editor.remove_sample()
    assert editor.active==0
    editor.conditions.grid.item(0,1).setText('NEW')
    assert editor.collect().samples[0].conditions['温度'].text=='NEW'


@pytest.mark.parametrize('index',[0,1])
def test_remove_result_loads_remaining_row_and_keeps_immediate_edits(qtbot,tmp_path,monkeypatch,index):
    from PySide6.QtWidgets import QMessageBox
    from matknow.experiments.models import ResultDraft
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.Yes)
    w=window(qtbot,tmp_path);snap=save_samples(w);snap.draft.samples[0].results=[ResultDraft('一',Value('1'),test_conditions={'温度':'1'}),ResultDraft('二',Value('2'),test_conditions={'温度':'2'})]
    dialog=ResultDialog(snap,snap.draft.samples[0].id,parent=w);qtbot.addWidget(dialog)
    dialog.grid.setCurrentCell(index,0);dialog.remove_result()
    assert dialog.active==0
    dialog.test_conditions.setPlainText('温度=NEW');dialog.accept()
    assert dialog.draft.samples[0].results[0].test_conditions=={'温度':'NEW'}


def test_deleted_and_no_evidence_questions_remain_in_experiment_history(qtbot,tmp_path):
    from matknow.experiments.service import ExperimentAskService
    w=window(qtbot,tmp_path);page=w.experiment_page;snap=save_samples(w)
    service=ExperimentAskService(page.store);answer=service.ask('A1');empty=service.ask('UNFINDABLE_987')
    page.archive.delete(snap.id);w.refresh_history()
    assert w.history_list.count()==2
    row=next(w.history_list.item(i) for i in range(2) if w.history_list.item(i).data(Qt.ItemDataRole.UserRole)==answer.id)
    w.open_history(row)
    assert w.last_answer.unavailable_sources>0
    assert '已删除' in w.answer_view.toPlainText()


def test_locked_file_deletion_refreshes_records_and_invalidates_sources(qtbot,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from matknow.experiments.service import ExperimentAskService
    w=window(qtbot,tmp_path);page=w.experiment_page;snapshot=save_samples(w);page.refresh();page.record_list.setCurrentRow(0)
    w.present_answer(ExperimentAskService(page.store).ask('A1'))
    def locked(experiment_id):
        page.store.delete_rows(experiment_id)
        raise OSError('文件被占用，仍待清理。')
    monkeypatch.setattr(page.archive,'delete',locked);monkeypatch.setattr(QMessageBox,'exec',lambda *a,**k:QMessageBox.StandardButton.Yes)
    page.delete_selected();qtbot.waitUntil(lambda:not w.busy)
    assert page.record_list.count()==0
    assert w.last_answer.unavailable_sources>0
    assert '仍待清理' in w.status_label.text()
