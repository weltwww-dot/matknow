import json
import pytest
from PySide6.QtCore import QTimer
from matknow.knowledge import KnowledgeBase
from matknow.models import Chunk,Answer
from matknow.agent_tools import ResearchTools
from matknow.experiments.store import ExperimentStore
from tests.test_chat_routing import Model
from tests.test_model_first import source,extracted


@pytest.mark.parametrize('text,valid',[
    ('The first solution was called SP.',True),
    ('SP. The second solution was called SPKS.',True),
    ('The solution was called SP.2.',False),
    ('The solution was called SP-2.',False),
    ('The solution was called SPKS.',False),
])
def test_sample_code_sentence_period_is_not_part_of_the_identifier(text,valid):
    from matknow.experiments.readers import Block
    from matknow.experiments.import_agent import organize_record
    from matknow.experiments.models import ExperimentDraft,SampleDraft
    from matknow.model_tasks import TaskValidationError
    doc=source();doc.blocks=[Block(text,'段落1')]
    payload={'record_type':'plan','samples':[{'code':'SP','locator':'段落1','conditions':[],'results':[]}]}
    call=lambda:organize_record(doc,['段落1'],ExperimentDraft('方案','2026-10-16',samples=[SampleDraft('待确认样品')]),Model(payload,payload),intent='plan')
    if valid:assert call().samples[0].code=='SP'
    else:
        with pytest.raises(TaskValidationError):call()


def prepare(kb):
    doc=kb._save_document('方案.docx',[Chunk(b.text,b.locator) for b in source().blocks[:2]])
    tools=ResearchTools(kb,model_client=Model(extracted()))
    result=tools.call('prepare_experiment_draft',{'document_id':doc.id,'kind':'plan'})
    return doc,result


def test_prepare_only_creates_a_candidate_with_an_original_snapshot(tmp_path):
    kb=KnowledgeBase(tmp_path/'kb.sqlite3');doc,result=prepare(kb)
    assert ExperimentStore(kb).list_experiments()==[]
    candidate=kb.experiment_draft(result['draft_id'])
    assert candidate['document_id']==doc.id and candidate['draft']['chemistry']['status']=='planned'
    assert [s['code'] for s in candidate['draft']['samples']]==['B1','B2']
    assert '原料甲各 0.50 g' in candidate['source_text'] and result['needs_confirmation']


def test_prepare_cannot_read_unselected_documents(tmp_path):
    kb=KnowledgeBase(tmp_path/'kb.sqlite3');a=kb.import_text('允许','已选内容');other=kb.import_text('其他','未选择内容')
    tools=ResearchTools(kb,document_ids=[a.id],model_client=Model(extracted()))
    with pytest.raises(ValueError,match='范围'):tools.call('prepare_experiment_draft',{'document_id':other.id})
    assert not ExperimentStore(kb).list_experiments()


def test_deleted_source_invalidates_the_pending_draft(tmp_path):
    kb=KnowledgeBase(tmp_path/'kb.sqlite3');doc,result=prepare(kb);kb.delete_document(doc.id)
    with pytest.raises(ValueError):kb.experiment_draft(result['draft_id'])


def test_only_a_draft_in_the_visible_conversation_can_be_opened(qtbot,tmp_path):
    from tests.test_experiment_desktop import window
    w=window(qtbot,tmp_path);_,result=prepare(w.knowledge)
    w.open_experiment_draft(result['draft_id'])
    assert '当前对话' in w.status_label.text()
    assert not w.experiment_page.store.list_experiments()


def test_prepared_draft_hides_retired_editors_before_modal_event_loop(qtbot):
    from matknow.experiments.import_dialog import RecordImportDialog
    from matknow.experiments.editors import ExperimentEditor
    from matknow.experiments.models import ExperimentDraft,SampleDraft
    draft=ExperimentDraft('整理后的方案','2026-10-19',samples=[SampleDraft('B1')])
    dialog=RecordImportDialog(source(),prepared=draft);qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.summary.isVisible() and dialog.editor.isHidden()
    # Deferred deletion is delayed inside the nested draft-review modal loop.
    retired=[editor for editor in dialog.findChildren(ExperimentEditor) if editor is not dialog.editor]
    assert retired and all(editor.isHidden() for editor in retired)
    dialog.details_button.click()
    assert dialog.editor.isVisible() and all(not editor.isVisible() for editor in retired)


def test_collapsing_replaced_draft_editor_refreshes_review_summary(qtbot):
    from matknow.experiments.import_dialog import RecordImportDialog
    from matknow.experiments.models import ExperimentDraft,SampleDraft
    draft=ExperimentDraft('原始标题','2026-10-19',samples=[SampleDraft('B1')])
    dialog=RecordImportDialog(source(),prepared=draft);qtbot.addWidget(dialog);dialog.show()
    dialog.details_button.click();dialog.editor.title.setText('修改后的方案')
    dialog.details_button.click()
    assert dialog.editor.isHidden() and '修改后的方案' in dialog.summary.toPlainText()


def test_draft_review_shows_extracted_conditions_and_model_summary(qtbot):
    from matknow.experiments.import_dialog import RecordImportDialog
    from matknow.experiments.models import ExperimentDraft,SampleDraft,Value
    draft=ExperimentDraft('教学记录','2026-10-19',common={'干燥时间':Value('6','h')},
        samples=[SampleDraft('DEMO-A',{'干燥温度':Value('60','°C')})],
        notes='模型整理摘要（待核对） / 段落1\n仅记录干燥条件，具体操作未提供。\n\n所选原记录\n段落1\n原文')
    dialog=RecordImportDialog(source(),prepared=draft);qtbot.addWidget(dialog)
    text=dialog.summary.toPlainText()
    assert '干燥时间 6 h' in text and 'DEMO-A：干燥温度 60 °C' in text
    assert '仅记录干燥条件，具体操作未提供。' in text


def test_draft_review_shows_each_repeated_measurement(qtbot):
    from matknow.experiments.import_dialog import RecordImportDialog
    from matknow.experiments.models import ExperimentDraft,SampleDraft,ResultDraft,Value
    draft=ExperimentDraft('测试结果','2026-10-19',samples=[SampleDraft('A1',results=[
        ResultDraft('拉伸强度',Value('60.0','MPa')),ResultDraft('拉伸强度',Value('62.0','MPa'))])])
    dialog=RecordImportDialog(source(),prepared=draft);qtbot.addWidget(dialog)
    text=dialog.summary.toPlainText()
    assert '拉伸强度 60.0 MPa' in text and '拉伸强度 62.0 MPa' in text


def test_review_and_confirm_saves_once_with_a_traceable_source_snapshot(qtbot,tmp_path):
    from tests.test_experiment_desktop import window
    from PySide6.QtWidgets import QApplication
    from matknow.experiments.import_dialog import RecordImportDialog
    w=window(qtbot,tmp_path);doc,result=prepare(w.knowledge)
    answer=Answer('draft-answer','生成实验方案','草稿待核对 [1]','generated','now',w.knowledge.document_sources(doc.id),agent_run={'plan':[],'steps':[],'outcome':'needs_input','workspace':'experiments','draft_ids':[result['draft_id']]})
    w.knowledge.save_answer(answer);w.present_answer(answer)
    checked=[]
    def accept():
        dialog=QApplication.activeModalWidget();assert isinstance(dialog,RecordImportDialog)
        checked.append(dialog.draft_ready and 'B1' in dialog.summary.toPlainText())
        dialog.confirm_check.setChecked(True);dialog.accept()
    QTimer.singleShot(50,accept);w.open_experiment_draft(result['draft_id']);qtbot.waitUntil(lambda:not w.busy)
    assert checked==[True]
    store=w.experiment_page.store;rows=store.list_experiments();assert len(rows)==1
    snapshot=store.get(rows[0].id);assert [s.code for s in snapshot.draft.samples]==['B1','B2']
    assert snapshot.draft.chemistry.status=='planned' and not any(s.results for s in snapshot.draft.samples)
    file_id=snapshot.draft.source_ids[0];assert '原料甲各 0.50 g' in w.experiment_page.archive.path_for(file_id).read_text(encoding='utf8')
    w.open_experiment_draft(result['draft_id'])
    assert len(store.list_experiments())==1 and '已保存' in w.status_label.text()


def test_long_source_draft_uses_only_explicit_valid_document_source_ids(tmp_path):
    kb=KnowledgeBase(tmp_path/'kb.sqlite3')
    doc=kb._save_document('长论文.txt',[*[Chunk(b.text,b.locator) for b in source().blocks[:2]],*[Chunk('PRIVATE_NOT_SELECTED '+('背景文字'*500),f'其他{i}') for i in range(40)]])
    sources=kb.document_sources(doc.id);model=Model(extracted())
    result=ResearchTools(kb,model_client=model).call('prepare_experiment_draft',{'document_id':doc.id,'source_ids':[s.id for s in sources[:2]]})
    candidate=kb.experiment_draft(result['draft_id'])
    assert 'PRIVATE_NOT_SELECTED' not in candidate['source_text']
    assert 'PRIVATE_NOT_SELECTED' not in json.dumps(model.messages,ensure_ascii=False)
    other=kb.import_text('其他资料','其他内容')
    with pytest.raises(ValueError,match='来源'):ResearchTools(kb,model_client=Model()).call('prepare_experiment_draft',{'document_id':doc.id,'source_ids':[kb.document_sources(other.id)[0].id]})


def test_explicit_older_target_is_allowed_when_no_sample_scope_is_selected(tmp_path):
    from matknow.experiments.models import ExperimentDraft,SampleDraft
    kb=KnowledgeBase(tmp_path/'kb.sqlite3');store=ExperimentStore(kb)
    oldest=store.save(ExperimentDraft('旧实验','2026-10-16',samples=[SampleDraft('A1')]))
    for i in range(21):store.save(ExperimentDraft(f'更新{i}','2026-10-16',samples=[SampleDraft('A1')]))
    doc=kb._save_document('方案.docx',[Chunk(b.text,b.locator) for b in source().blocks[:2]])
    result=ResearchTools(kb,model_client=Model(extracted())).call('prepare_experiment_draft',{'document_id':doc.id,'experiment_id':oldest.id})
    assert kb.experiment_draft(result['draft_id'])['target_id']==oldest.id


def test_consuming_candidate_is_atomic_with_the_experiment_save(tmp_path):
    from matknow.experiments.models import ExperimentDraft,SampleDraft
    kb=KnowledgeBase(tmp_path/'kb.sqlite3');_,result=prepare(kb);store=ExperimentStore(kb)
    draft=ExperimentDraft('已核对','2026-10-16',samples=[SampleDraft('A1')])
    with pytest.raises(ValueError):store.save(draft,draft_id=result['draft_id'],expected_revision=1)
    assert kb.experiment_draft(result['draft_id']) and not store.list_experiments()
    store.save(draft,draft_id=result['draft_id'])
    with pytest.raises(ValueError):store.save(draft,draft_id=result['draft_id'])
    assert len(store.list_experiments())==1


def test_review_of_a_selected_source_draft_cannot_reintroduce_excluded_blocks(qtbot,tmp_path):
    from tests.test_experiment_desktop import window
    from PySide6.QtWidgets import QApplication
    w=window(qtbot,tmp_path)
    doc=w.knowledge._save_document('原文.docx',[Chunk(b.text,b.locator) for b in source().blocks])
    sources=w.knowledge.document_sources(doc.id)
    result=ResearchTools(w.knowledge,model_client=Model(extracted())).call('prepare_experiment_draft',{'document_id':doc.id,'source_ids':[s.id for s in sources[:2]]})
    a=Answer('review','生成草稿','待核对 [1]','generated','now',sources[:1],agent_run={'plan':[],'steps':[],'outcome':'needs_input','draft_ids':[result['draft_id']]})
    w.knowledge.save_answer(a);w.present_answer(a);seen=[]
    def inspect():
        d=QApplication.activeModalWidget();d.sample_codes.setText('B1,B2');d.build_draft()
        seen.append('PRIVATE_UNSELECTED' not in d.editor.collect().notes and len(d.document.blocks)==2);d.reject()
    QTimer.singleShot(50,inspect);w.open_experiment_draft(result['draft_id'])
    assert seen==[True]
