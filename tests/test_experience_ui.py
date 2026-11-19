import json

from PySide6.QtCore import Qt
from matknow.models import Answer
from matknow.experiments.models import ExperimentDraft,SampleDraft
from tests.test_chat_routing import Model
from tests.test_model_first import source,extracted


def test_chat_keeps_original_text_in_the_source_panel(qtbot,tmp_path):
    from tests.test_experiment_desktop import window
    w=window(qtbot,tmp_path);doc=w.knowledge.import_text('参考论文','PRIVATE_FULL_SOURCE 原始说明。')
    evidence=w.knowledge.document_sources(doc.id)[0]
    w.present_answer(Answer('answer','问题','**摘要结论** [1]','generated','now',[evidence]))
    assert 'PRIVATE_FULL_SOURCE' not in w.answer_view.toPlainText()
    assert '**摘要结论**' not in w.answer_view.toPlainText()
    w.show_source(evidence.id)
    assert 'PRIVATE_FULL_SOURCE' in w.source_view.toPlainText()


def test_zip_paper_identity_is_visible_while_rows_remain_selectable(qtbot,tmp_path):
    from tests.test_experiment_desktop import window
    from matknow.agent_tools import ResearchTools
    from zipfile import ZipFile
    w=window(qtbot,tmp_path/'app-data');path=tmp_path/'inbox.zip'
    with ZipFile(path,'w') as z:
        z.writestr('inbox/P004/paper.txt','第一篇方法。');z.writestr('inbox/P005/paper.txt','第二篇方法。')
    ResearchTools(w.knowledge,file_paths=[str(path)]).call('read_file',{'path':str(path)});w.refresh_documents()
    assert w.document_list.count()==2
    assert 'P004' in w.document_list.item(0).text() and 'P005' in w.document_list.item(1).text()
    for i in range(2):w.document_list.item(i).setSelected(True)
    assert len(w.document_list.selectedItems())==2


def test_finished_form_does_not_keep_the_running_message(qtbot):
    from matknow.experiments.editors import FormDialog
    d=FormDialog();qtbot.addWidget(d);d.show()
    d.run(lambda:'payload',lambda value:None,'正在读取附件…')
    qtbot.waitUntil(lambda:not d.busy)
    assert '正在读取' not in d.message.text()


def test_failed_import_replaces_the_preparing_summary(qtbot):
    from matknow.experiments.import_dialog import RecordImportDialog
    from matknow.llm import ModelError
    d=RecordImportDialog(source(),Model(ModelError('连接失败')));qtbot.addWidget(d);d.show()
    qtbot.waitUntil(lambda:d.auto_started and not d.busy and bool(d.message.text()))
    assert '正在准备' not in d.summary.toPlainText()
    assert not d.draft_ready and '未完成' in d.summary.toPlainText()


def test_model_summary_can_reference_multiple_selected_paragraphs():
    from matknow.experiments.import_agent import organize_record
    p=extracted();p['notes']={'text':'记录了制备计划和待执行测试。','locators':['段落1','段落2']}
    draft=organize_record(source(),['段落1','段落2'],ExperimentDraft('导入','2026-10-16',samples=[SampleDraft('A1')]),Model(p))
    assert '段落1；段落2' in draft.notes and '待执行测试' in draft.notes


def test_preview_curve_opens_the_plot_it_created(qtbot,tmp_path):
    from matknow.experiments.result_dialog import ResultDialog
    from matknow.experiments.models import ExperimentSnapshot
    draft=ExperimentDraft('批次','2026-10-16',samples=[SampleDraft('A1',id='00000000-0000-0000-0000-000000000001')])
    d=ResultDialog(ExperimentSnapshot('',0,draft,''),draft.samples[0].id);qtbot.addWidget(d);d.show()
    path=tmp_path/'curve.csv';path.write_text('x,y\n0,1\n1,2\n',encoding='utf8')
    d.attach_path(path);qtbot.waitUntil(lambda:not d.busy);d.preview_curve();qtbot.waitUntil(lambda:not d.busy)
    assert d.tabs.currentWidget()==d.preview
    assert d.preview.content.tabText(d.preview.content.currentIndex())=='曲线'
