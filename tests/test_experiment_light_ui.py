from copy import deepcopy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog

from matknow.experiments.editors import ExperimentDialog
from matknow.experiments.result_dialog import ResultDialog
from matknow.experiments.models import Value, ResultDraft
from tests.test_experiment_desktop import window, save_samples


def test_home_lists_experiments_once_and_opens_a_readable_record(qtbot, tmp_path):
    w = window(qtbot, tmp_path); page = w.experiment_page
    snapshot = save_samples(w, '水热制备')
    draft = deepcopy(snapshot.draft); draft.notes = '溶液搅拌后转入反应釜。'
    draft.samples[0].results = [ResultDraft('强度', Value('60', 'MPa'))]
    page.archive.save(draft, [], experiment_id=snapshot.id, expected_revision=1)
    save_samples(w, '第二次实验'); page.refresh()
    assert page.record_list.count() == 2
    assert not hasattr(page, 'sample_table')
    first = next(page.record_list.item(i) for i in range(page.record_list.count()) if page.record_list.item(i).data(Qt.ItemDataRole.UserRole) == snapshot.id)
    assert '水热制备' in first.text() and '2 个样品' in first.text()
    page.record_list.setCurrentItem(first); first.setSelected(True)
    assert len(page.selected_sample_ids()) == 2
    assert page.detail_dialog.isHidden()
    page.open_record()
    assert page.detail_dialog.isVisible()
    assert '溶液搅拌后转入反应釜。' in page.source_view.toPlainText()
    assert '备注：' not in page.source_view.toPlainText()
    assert '版本' not in page.source_title.text()
    assert page.detail_tabs.tabText(1) == '结果（1）'
    assert page.previous.isHidden() and page.next.isHidden()


def test_quick_note_starts_simple_and_keeps_existing_structured_data(qtbot, tmp_path):
    w = window(qtbot, tmp_path); snapshot = save_samples(w)
    draft = deepcopy(snapshot.draft); draft.common = {'时间': Value('8', 'h')}
    draft.samples[0].conditions = {'温度': Value('160', '°C')}
    draft.samples[0].results = [ResultDraft('强度', Value('60', 'MPa'))]
    snapshot = w.experiment_page.archive.save(draft, [], experiment_id=snapshot.id, expected_revision=1)
    dialog = ExperimentDialog(snapshot); qtbot.addWidget(dialog); dialog.show()
    assert dialog.editor.notes.isVisible()
    assert not dialog.editor.sample_grid.isVisible()
    dialog.editor.notes.setPlainText('补记：冷却后取出。')
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.draft.common == draft.common
    assert dialog.draft.samples == snapshot.draft.samples
    assert dialog.draft.notes == '补记：冷却后取出。'


def test_individual_sample_scope_is_available_without_changing_record_data(qtbot, tmp_path):
    w = window(qtbot, tmp_path); page = w.experiment_page
    snapshot = save_samples(w); page.refresh()
    page.record_list.item(0).setSelected(True)
    def choose():
        dialog = QApplication.activeModalWidget()
        listing = dialog.findChild(type(page.record_list))
        listing.clearSelection(); listing.item(1).setSelected(True)
        dialog.accept()
    QTimer.singleShot(0, choose)
    page.choose_samples()
    assert page.selected_sample_ids() == [snapshot.draft.samples[1].id]
    assert len(page.store.get(snapshot.id).draft.samples) == 2
    assert '1 个样品' in page.scope_label.text()
    second = save_samples(w, '第二批'); page.refresh()
    other = next(page.record_list.item(i) for i in range(page.record_list.count()) if page.record_list.item(i).data(Qt.ItemDataRole.UserRole) == second.id)
    other.setSelected(True)
    assert set(page.selected_sample_ids()) == {snapshot.draft.samples[1].id, *[s.id for s in second.draft.samples]}


def test_result_form_keeps_test_settings_out_of_the_basic_entry(qtbot, tmp_path):
    w = window(qtbot, tmp_path); snapshot = save_samples(w)
    dialog = ResultDialog(snapshot, snapshot.draft.samples[0].id, parent=w)
    qtbot.addWidget(dialog); dialog.show()
    assert dialog.grid.isColumnHidden(3) and dialog.grid.isColumnHidden(4)
    assert not dialog.test_conditions.isVisible()
    dialog.add_result(); dialog.grid.item(0, 0).setText('强度'); dialog.grid.item(0, 1).setText('60'); dialog.grid.item(0, 2).setText('MPa')
    qtbot.mouseClick(dialog.advanced_button, Qt.MouseButton.LeftButton)
    assert dialog.test_conditions.isVisible() and not dialog.grid.isColumnHidden(3)
    dialog.grid.item(0, 3).setText('拉伸'); dialog.test_conditions.setPlainText('温度=25 °C')
    dialog.advanced_button.setChecked(False); dialog.accept()
    result = dialog.draft.samples[0].results[0]
    assert result.method == '拉伸' and result.test_conditions == {'温度': '25 °C'}


def test_opening_another_record_targets_its_own_samples(qtbot, tmp_path):
    w = window(qtbot, tmp_path); page = w.experiment_page
    one = save_samples(w, '第一条'); two = save_samples(w, '第二条'); page.refresh()
    for snapshot in (one, two):
        entry = next(page.record_list.item(i) for i in range(page.record_list.count()) if page.record_list.item(i).data(Qt.ItemDataRole.UserRole) == snapshot.id)
        page.record_list.setCurrentItem(entry); page.open_record()
        assert page.evidence_snapshot.id == snapshot.id
        page.sample_combo.setCurrentIndex(1)
        assert page.selected_snapshot(detail=True)[1].id == snapshot.draft.samples[1].id
    assert page.source_title.text().startswith('第二条')


def test_record_pagination_preserves_sample_scope(qtbot, tmp_path):
    w = window(qtbot, tmp_path); page = w.experiment_page
    for i in range(51):save_samples(w, f'实验{i:02}')
    page.refresh(); page.record_list.setCurrentRow(0)
    selected = set(page.selected_sample_ids())
    assert page.record_list.count() == 50 and page.next.isVisible()
    page.show_page(50)
    assert page.record_list.count() == 1 and set(page.selected_sample_ids()) == selected
    page.record_list.item(0).setSelected(True)
    assert len(page.selected_sample_ids()) == 4
    page.show_page(0)
    assert len(page.selected_sample_ids()) == 4 and page.record_list.item(0).isSelected()
