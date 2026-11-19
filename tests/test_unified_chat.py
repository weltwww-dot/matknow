import json
import pytest

from PySide6.QtWidgets import QFileDialog, QPlainTextEdit

from matknow.agent import ResearchAgent
from matknow.desktop import MainWindow
from matknow.experiments.compare import compare_samples
from matknow.experiments.models import ExperimentDraft, SampleDraft, Value
from matknow.experiments.store import ExperimentStore
from matknow.knowledge import KnowledgeBase
from tests.test_agent import Script, final


def records(tmp_path):
    kb=KnowledgeBase(tmp_path/'kb.sqlite3')
    selected=kb.import_text('选中方法','干燥60 °C。')
    kb.import_text('未选资料','干燥 PRIVATE_DOCUMENT。')
    store=ExperimentStore(kb)
    snap=store.save(ExperimentDraft('批次','2026-10-14',samples=[
        SampleDraft('A1',{'干燥温度':Value('60','°C')}),
        SampleDraft('A2',{'干燥温度':Value('80','°C'),'标记':Value('PRIVATE_SAMPLE')})]))
    return kb,selected,snap


def test_unified_agent_seeds_both_scopes_and_saves_one_history(tmp_path):
    kb,doc,snap=records(tmp_path);model=Script(final)
    answer=ResearchAgent(kb,model).ask('干燥',[doc.id],sample_ids=[snap.draft.samples[0].id],workspace='unified')
    context=json.dumps(model.contexts,ensure_ascii=False)
    assert 'PRIVATE_DOCUMENT' not in context and 'PRIVATE_SAMPLE' not in context
    assert any(s.id.startswith('exp:') for s in answer.sources)
    assert any(s.document_id==doc.id for s in answer.sources)
    assert {s['tool'] for s in answer.agent_run['steps']} >= {'search_documents','search_experiments'}
    assert answer.status=='generated' and len(kb.list_history())==1


def test_unified_local_preview_merges_sources_without_duplicate_history(tmp_path):
    kb,doc,snap=records(tmp_path)
    answer=ResearchAgent(kb).ask('干燥',[doc.id],sample_ids=[snap.draft.samples[0].id],workspace='unified')
    assert answer.status=='retrieval_preview' and answer.agent_run is None
    assert len(kb.list_history())==1
    assert any(s.id.startswith('exp:') for s in answer.sources)
    assert any(s.document_id==doc.id for s in answer.sources)
    assert [s.citation_number for s in answer.sources]==list(range(1,len(answer.sources)+1))


def test_unrelated_experiment_hit_does_not_require_lab_ranking_for_document_answer(tmp_path):
    kb,doc,snap=records(tmp_path)
    answer=ResearchAgent(kb,Script(final)).ask('资料报告的最高干燥温度',[doc.id],workspace='unified')
    assert answer.status=='generated'


@pytest.mark.parametrize('question',['所选样品的干燥温度最高的是哪个？','干燥温度最高的是哪个？','干燥实验一共有几个样品？','论文与所选样品的干燥温度最高的是哪个？'])
def test_document_citation_cannot_bypass_lab_ranking_or_count_validation(tmp_path,question):
    kb,doc,snap=records(tmp_path)
    answer=ResearchAgent(kb,Script(final)).ask(question,[doc.id],sample_ids=[s.id for s in snap.draft.samples],workspace='unified')
    assert answer.status=='model_error' and answer.agent_run['outcome']=='failed'


@pytest.mark.parametrize('has_lab_records',[False,True])
def test_paper_described_samples_do_not_require_personal_experiment_calculation(tmp_path,has_lab_records):
    if has_lab_records:kb,doc,_=records(tmp_path)
    else:
        kb=KnowledgeBase(tmp_path/'kb.sqlite3');doc=kb.import_text('论文','干燥温度60 °C。')
    answer=ResearchAgent(kb,Script(final)).ask('论文中实验样品的最高干燥温度是多少？',[doc.id],workspace='unified')
    assert answer.status=='generated'


def test_single_chat_stays_visible_and_submits_both_selections(qtbot,tmp_path):
    kb,doc,snap=records(tmp_path);w=MainWindow(kb);qtbot.addWidget(w);w.show()
    w.document_list.item(next(i for i in range(w.document_list.count()) if w.document_list.item(i).data(256)==doc.id)).setSelected(True)
    w.experiment_page.selection={snap.draft.samples[0].id};w.experiment_page.refresh()
    w.question_edit.setPlainText('干燥');w.workspaces.setCurrentIndex(1)
    assert w.question_edit.isVisible() and w.model_combo.isVisible()
    assert not hasattr(w.experiment_page,'question_edit') and not hasattr(w.experiment_page,'model_combo')
    assert len([edit for edit in w.findChildren(QPlainTextEdit) if not edit.isReadOnly()])==1
    assert '资料：1' in w.context_label.text() and '样品：1' in w.context_label.text()
    w.ask();qtbot.waitUntil(lambda:not w.busy)
    assert w.history_list.count()==1 and w.answer_view.isVisible()
    assert any(s.id.startswith('exp:') for s in w.last_answer.sources)
    assert any(s.document_id==doc.id for s in w.last_answer.sources)
    assert 'PRIVATE_' not in w.answer_view.toPlainText()
    w.workspaces.setCurrentIndex(0)
    assert not w.question_edit.toPlainText() and w.answer_view.isVisible()


def test_manual_comparison_uses_shared_chat_and_preserves_confirmed_comparison(qtbot,tmp_path):
    kb,_,snap=records(tmp_path);w=MainWindow(kb);qtbot.addWidget(w);w.show()
    comparison=compare_samples(w.experiment_page.store,[s.id for s in snap.draft.samples])
    w.experiment_page.ask_comparison('对比干燥温度',comparison)
    qtbot.waitUntil(lambda:not w.busy)
    assert w.last_answer.question=='对比干燥温度'
    assert not w.question_edit.toPlainText()
    assert w.history_list.count()==1 and w.answer_view.isVisible()


@pytest.mark.parametrize('destination',['database','attachment'])
def test_shared_export_never_overwrites_managed_data(qtbot,tmp_path,monkeypatch,destination):
    kb,_,_=records(tmp_path);w=MainWindow(kb);qtbot.addWidget(w)
    w.present_answer(ResearchAgent(kb).ask('干燥',workspace='unified'))
    target=kb.path if destination=='database' else tmp_path/'experiment-files'/'original.csv'
    if destination=='attachment':
        target.parent.mkdir(exist_ok=True);target.write_bytes(b'ORIGINAL_DATA')
    before=target.read_bytes()
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a:(str(target),''))
    w.export_answer()
    assert target.read_bytes()==before and '导出失败' in w.status_label.text()
