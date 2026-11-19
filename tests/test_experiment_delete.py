import sqlite3
from contextlib import contextmanager

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QMessageBox

from matknow.experiments.files import Upload
from matknow.experiments.service import ExperimentAskService
from tests.test_experiment_desktop import window, save_samples


def confirm(monkeypatch, answer=QMessageBox.StandardButton.Yes):
    messages=[]
    def reply(dialog):
        assert dialog.defaultButton() is dialog.button(QMessageBox.StandardButton.No)
        assert dialog.textFormat()==Qt.TextFormat.PlainText
        messages.append(dialog.text()+'\n'+dialog.detailedText())
        return answer
    monkeypatch.setattr(QMessageBox,'exec',reply)
    return messages


def test_bulk_delete_uses_selected_records_across_pages_not_current_focus(qtbot,tmp_path,monkeypatch):
    w=window(qtbot,tmp_path);page=w.experiment_page
    first=save_samples(w,'最早选中实验')
    source=tmp_path/'original.txt';source.write_text('用户原件',encoding='utf8')
    first=page.archive.save(first.draft,[Upload(source,'record','source')],experiment_id=first.id,expected_revision=1)
    for i in range(49):save_samples(w,f'保留实验{i}')
    last=save_samples(w,'最新选中实验');page.refresh();page.record_list.setCurrentRow(0)
    page.show_page(50);page.record_list.item(0).setSelected(True)
    page.show_page(0)
    page.record_list.setCurrentItem(page.record_list.item(1),QItemSelectionModel.SelectionFlag.NoUpdate)
    answer=ExperimentAskService(page.store).ask('A1',[first.draft.samples[0].id,last.draft.samples[0].id]);w.present_answer(answer)
    messages=confirm(monkeypatch)
    page.delete_selected();qtbot.waitUntil(lambda:not w.busy)
    remaining={s.id for s in page.store.list_experiments(limit=100)}
    assert len(remaining)==49 and first.id not in remaining and last.id not in remaining
    assert '最早选中实验' in messages[0] and '最新选中实验' in messages[0]
    assert '保留实验' not in messages[0] and '全部样品' in messages[0]
    assert source.is_file() and not page.archive.root.joinpath(first.draft.source_ids[0]).exists()
    assert w.last_answer.unavailable_sources>0 and not page.selected_sample_ids()
    assert '已删除 2 条实验' in w.status_label.text()


def test_cancel_bulk_deletion_preserves_records_and_selection(qtbot,tmp_path,monkeypatch):
    w=window(qtbot,tmp_path);page=w.experiment_page
    save_samples(w);save_samples(w,'另一条');page.refresh();page.record_list.selectAll()
    selected=page.selected_sample_ids();messages=confirm(monkeypatch,QMessageBox.StandardButton.No)
    assert page.delete_button.isVisible()
    qtbot.mouseClick(page.delete_button,Qt.MouseButton.LeftButton)
    assert messages and not w.busy and len(page.store.list_experiments())==2
    assert page.selected_sample_ids()==selected


@pytest.mark.parametrize('busy',[False,True])
def test_empty_or_busy_deletion_never_falls_back_to_focused_record(qtbot,tmp_path,monkeypatch,busy):
    w=window(qtbot,tmp_path);page=w.experiment_page
    save_samples(w);page.refresh()
    page.record_list.setCurrentItem(page.record_list.item(0),QItemSelectionModel.SelectionFlag.NoUpdate)
    if busy:page.record_list.selectAll();page.set_busy(True)
    messages=confirm(monkeypatch)
    page.delete_selected()
    assert not messages and len(page.store.list_experiments())==1 and not w.busy
    page.set_busy(False)


def test_partial_failure_refreshes_history_and_keeps_failed_records_selected(qtbot,tmp_path,monkeypatch):
    w=window(qtbot,tmp_path);page=w.experiment_page
    locked=save_samples(w,'附件占用');failed=save_samples(w,'暂时失败');ok=save_samples(w,'正常删除')
    page.refresh();page.record_list.selectAll();original=page.archive.delete
    def remove(record_id):
        if record_id==failed.id:raise sqlite3.OperationalError('数据库繁忙')
        original(record_id)
        if record_id==locked.id:raise OSError('部分附件仍待清理。')
    monkeypatch.setattr(page.archive,'delete',remove);confirm(monkeypatch)
    page.delete_selected();qtbot.waitUntil(lambda:not w.busy)
    assert {s.id for s in page.store.list_experiments()}=={failed.id}
    assert set(page.selected_sample_ids())=={s.id for s in failed.draft.samples}
    assert '已删除 2 条实验' in w.status_label.text() and '1 条未删除' in w.status_label.text()
    assert '仍待清理' in w.status_label.text() and '暂时失败' in w.status_label.text()


def test_partial_sample_selection_confirms_whole_records_and_lists_all_targets(qtbot,tmp_path,monkeypatch):
    w=window(qtbot,tmp_path);page=w.experiment_page
    snapshots=[save_samples(w,f'实验{i}') for i in range(9)]
    page.selection={snapshot.draft.samples[0].id for snapshot in snapshots};page.refresh()
    messages=confirm(monkeypatch,QMessageBox.StandardButton.No)
    page.delete_selected()
    assert '全部样品' in messages[0] and '9 条实验' in messages[0]
    assert all(f'实验{i}' in messages[0] for i in range(9))
    assert '2 个样品' in messages[0] and len(page.store.list_experiments())==9


def test_failed_status_recheck_preserves_known_deletions(qtbot,tmp_path,monkeypatch):
    w=window(qtbot,tmp_path);page=w.experiment_page
    for i in range(3):save_samples(w,f'实验{i}')
    page.refresh();page.record_list.selectAll();original_delete=page.archive.delete
    original_connection=page.store.knowledge.connection;state={'calls':0,'failures':0}
    @contextmanager
    def connection():
        if state['failures']:
            state['failures']-=1
            raise sqlite3.OperationalError('暂时无法连接数据库')
        with original_connection() as db:yield db
    def remove(record_id):
        state['calls']+=1
        if state['calls']==2:
            state['failures']=2
            with page.store.knowledge.connection():pass
        original_delete(record_id)
    monkeypatch.setattr(page.store.knowledge,'connection',connection)
    monkeypatch.setattr(page.archive,'delete',remove);confirm(monkeypatch)
    page.delete_selected();qtbot.waitUntil(lambda:not w.busy)
    assert len(page.store.list_experiments())==1 and page.record_list.count()==1
    assert '已删除 2 条实验' in w.status_label.text()
    assert '1 条删除状态未确认' in w.status_label.text()


def test_refresh_failure_does_not_hide_committed_delete_count(qtbot,tmp_path,monkeypatch):
    w=window(qtbot,tmp_path);page=w.experiment_page
    save_samples(w);page.refresh();page.record_list.selectAll();confirm(monkeypatch)
    w.present_answer(ExperimentAskService(page.store).ask('A1'))
    def unavailable():raise sqlite3.OperationalError('暂时无法刷新')
    monkeypatch.setattr(page,'refresh',unavailable)
    monkeypatch.setattr(w,'refresh_history',unavailable)
    page.delete_selected();qtbot.waitUntil(lambda:not w.busy)
    assert not page.store.list_experiments() and not page.selected_sample_ids()
    assert '已删除 1 条实验' in w.status_label.text() and '待刷新' in w.status_label.text()
    assert w.last_answer is None and not w.answer_view.toPlainText() and not w.source_view.toPlainText()
    assert len(w.knowledge.list_history())==1  # Only the unavailable view was cleared.
