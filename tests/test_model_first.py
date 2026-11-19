from copy import deepcopy
import json

import pytest

from matknow.agent import ResearchAgent
from matknow.agent_tools import ResearchTools
from matknow.experiments.drafts import organize
from matknow.experiments.models import ExperimentDraft, SampleDraft
from matknow.experiments.readers import ReadDocument, Block
from matknow.knowledge import KnowledgeBase
from matknow.llm import ModelError
from tests.test_chat_routing import Model


def source():
    return ReadDocument('record','方案.docx','hash',[
        Block('方案：目的比较温度，样品 B1、B2；原料甲各 0.50 g。B1 60 °C，B2 80 °C。预期强度提高。','段落1'),
        Block('计划进行拉伸测试，尚未实施。','段落2'),Block('PRIVATE_UNSELECTED','段落3')],[],[])


def extracted():
    return {'title':'温度对比方案','record_type':'plan','chemistry':{
        'objective':{'text':'比较温度','locator':'段落1'},'status':'planned',
        'reagents':[{'name':'原料甲','quantity':'0.50','unit':'g','sample_codes':['B1','B2'],'locator':'段落1'}]},
        'common':[], 'samples':[{'code':code,'locator':'段落1','conditions':[{'name':'温度','text':temp,'unit':'°C','locator':'段落1'}],'results':[]}
                               for code,temp in [('B1','60'),('B2','80')]],
        'review':['实验日期未提供。','尚无实测结果。']}


def test_model_discovers_samples_and_chemistry_without_prefilled_mapping():
    base=ExperimentDraft('导入','2026-10-14',samples=[SampleDraft('A1')]);before=deepcopy(base)
    model=Model(extracted())
    draft=organize(source(),['段落1','段落2'],base,model)
    assert [s.code for s in draft.samples]==['B1','B2']
    assert draft.chemistry.objective=='比较温度' and draft.chemistry.status=='planned'
    assert draft.chemistry.reagents[0].quantity=='0.50'
    assert '段落1' in draft.notes and '日期未提供' in draft.notes and 'PRIVATE_UNSELECTED' not in json.dumps(model.messages)
    assert not any(s.results for s in draft.samples) and base==before


@pytest.mark.parametrize('change',[
    lambda p:p['chemistry']['objective'].update(locator='段落3'),
    lambda p:p['samples'][0].update(id='foreign-id'),
    lambda p:p['chemistry']['reagents'][0].update(sample_codes=['OTHER']),
    lambda p:p.update(record_type='invented'),
    lambda p:p['samples'][0].update(results=[{'metric':'预期强度','text':'99','unit':'MPa','kind':'number','locator':'段落1'}]),
])
def test_model_draft_rejects_foreign_sources_and_plan_measurements(change):
    payload=extracted();change(payload)
    with pytest.raises(ModelError):organize(source(),['段落1','段落2'],ExperimentDraft('导入','2026-10-14',samples=[SampleDraft('A1')]),Model(payload,payload))


def test_sample_identity_cannot_match_only_a_longer_code():
    doc=source();doc.blocks[0].text=doc.blocks[0].text.replace('B1','B10')
    payload=extracted()
    with pytest.raises(ModelError):organize(doc,['段落1','段落2'],ExperimentDraft('导入','2026-10-14',samples=[SampleDraft('A1')]),Model(payload,payload))


def test_model_routes_library_questions_to_specialist_without_lab_context(tmp_path):
    kb=KnowledgeBase(tmp_path/'kb.sqlite3');kb.import_text('SOP','干燥 60 °C。')
    def final(context):
        assert context['workspace']=='documents'
        assert 'read_documents' in context['tools'] and 'compare_samples' not in context['tools']
        s=context['sources'][0]
        return {'answer':f'见 SOP [{s["number"]}]。','source_ids':[s['id']],'status':'generated'}
    model=Model({'mode':'research','workspace':'documents'},final)
    answer=ResearchAgent(kb,model).respond('查导入 SOP 的干燥条件')
    assert answer.status=='generated' and answer.agent_run['workspace']=='documents'
    assert '资料库智能体' in model.messages[1][0]['content']
    assert '实验智能体专用规则' not in model.messages[1][0]['content']


def test_library_tools_read_only_selected_originals(tmp_path):
    kb=KnowledgeBase(tmp_path/'kb.sqlite3');a=kb.import_text('选中资料','过程记载：慢慢冷却。');b=kb.import_text('私有资料','PRIVATE_OTHER_DOC')
    tools=ResearchTools(kb,document_ids=[a.id])
    listed=tools.call('list_documents',{})
    assert [r['document_id'] for r in listed['documents']]==[a.id]
    read=tools.call('read_documents',{'document_ids':[a.id]})
    assert read['source_ids'] and '慢慢冷却' in json.dumps(tools.context(),ensure_ascii=False)
    with pytest.raises(ValueError):tools.call('read_documents',{'document_ids':[b.id]})
    assert 'PRIVATE_OTHER_DOC' not in json.dumps(tools.context())


def test_import_ui_automatically_organizes_without_sample_or_column_input(qtbot):
    from matknow.experiments.import_dialog import RecordImportDialog
    dialog=RecordImportDialog(source(),Model(extracted()));qtbot.addWidget(dialog)
    dialog.show();qtbot.waitUntil(lambda:not dialog.busy and dialog.editor.collect().chemistry.status=='planned')
    assert [s.code for s in dialog.editor.collect().samples]==['B1','B2']
    assert not dialog.editor.isVisible() and dialog.tabs.currentIndex()==1
    assert 'B1' in dialog.summary.toPlainText() and '0.50' in dialog.summary.toPlainText()


def test_library_classification_preserves_originals_and_is_separate_metadata(tmp_path):
    from matknow.library_agent import organize_document
    kb=KnowledgeBase(tmp_path/'kb.sqlite3');doc=kb.import_text('操作方法','先记录再冷却。');original=kb.document_sources(doc.id)
    data={'category':'SOP','summary':'说明操作顺序。','tags':['冷却'],'locators':[original[0].locator]}
    organize_document(kb,doc.id,Model(data));listed=kb.list_documents()[0]
    assert listed.category=='SOP' and listed.summary==data['summary'] and listed.tags==('冷却',)
    assert kb.document_sources(doc.id)==original
    bad={**data,'locators':['不存在']}
    with pytest.raises(ModelError):organize_document(kb,doc.id,Model(bad,bad))
    assert kb.list_documents()[0]==listed
    kb.delete_document(doc.id)
    with kb.connection() as db:assert db.execute('SELECT count(*) FROM document_metadata').fetchone()[0]==0


def test_library_import_keeps_source_when_model_fails(qtbot,tmp_path):
    from tests.test_experiment_desktop import window
    w=window(qtbot,tmp_path);w.service.model_client=Model(ModelError('服务不可用。'))
    path=tmp_path/'SOP.txt';path.write_text('操作原文保持不变。',encoding='utf8')
    w.import_paths([path]);qtbot.waitUntil(lambda:not w.busy)
    docs=w.knowledge.list_documents()
    assert len(docs)==1 and w.knowledge.document_sources(docs[0].id)[0].text=='操作原文保持不变。'
    assert '原文已导入' in w.status_label.text() and '未完成' in w.status_label.text()


def test_library_import_runs_model_and_shows_candidate_category(qtbot,tmp_path):
    from tests.test_experiment_desktop import window
    w=window(qtbot,tmp_path)
    def classify(context):return {'category':'SOP','summary':'整理操作顺序。','tags':['操作'],'locators':[context['sources'][0]['locator']]}
    w.service.model_client=Model(classify)
    path=tmp_path/'SOP.txt';path.write_text('操作原文保持不变。',encoding='utf8')
    w.import_paths([path]);qtbot.waitUntil(lambda:not w.busy)
    assert 'SOP · AI 候选' in w.document_list.item(0).text()
    assert '整理操作顺序' in w.document_list.item(0).toolTip()


def test_import_failure_does_not_enable_saving_an_empty_default_record(qtbot):
    from matknow.experiments.import_dialog import RecordImportDialog
    dialog=RecordImportDialog(source(),Model(ModelError('服务不可用。')));qtbot.addWidget(dialog)
    dialog.show();qtbot.waitUntil(lambda:dialog.auto_started and not dialog.busy)
    assert not dialog.draft_ready
    dialog.confirm_check.setChecked(True);dialog.accept()
    assert dialog.draft is None and '生成草稿' in dialog.message.text()


def test_model_matches_results_to_known_batch_and_merge_preserves_old_samples(report_archive):
    from matknow.experiments.import_agent import match_record, merge_record
    from matknow.experiments.models import Value, ResultDraft
    archive,snap=report_archive;incoming=ExperimentDraft('测试数据','2026-10-14',samples=[SampleDraft('A1',results=[ResultDraft('强度',Value('63.0','MPa'))])],notes='新增一次测量。')
    before=deepcopy(snap.draft)
    assert match_record(incoming,[snap],Model({'experiment_id':snap.id,'reason':'批次和样品对应。'}))==snap.id
    merged=merge_record(snap.draft,incoming)
    assert merged.samples[0].id==snap.draft.samples[0].id and merged.samples[1]==snap.draft.samples[1]
    assert [r.value.text for r in merged.samples[0].results]==['60.0','62.0','63.0'] and snap.draft==before
    with pytest.raises(ModelError):match_record(incoming,[snap],Model({'experiment_id':'foreign-id','reason':'错误'}, {'experiment_id':'foreign-id','reason':'错误'}))


def test_batch_merge_refuses_conflicting_conditions():
    from matknow.experiments.import_agent import merge_record
    from matknow.experiments.models import Value
    old=ExperimentDraft('原实验','2026-10-14',samples=[SampleDraft('A1',{'温度':Value('60','°C')})])
    incoming=ExperimentDraft('新文件','2026-10-14',samples=[SampleDraft('A1',{'温度':Value('80','°C')})])
    with pytest.raises(ValueError,match='冲突'):merge_record(old,incoming)
    assert old.samples[0].conditions['温度'].text=='60'


def test_partial_batch_import_does_not_assign_common_values_to_other_samples():
    from matknow.experiments.import_agent import merge_record
    from matknow.experiments.models import Value
    old=ExperimentDraft('原实验','2026-10-14',samples=[SampleDraft('A1'),SampleDraft('A2')])
    incoming=ExperimentDraft('新文件','2026-10-14',common={'温度':Value('60','°C')},samples=[SampleDraft('A1')])
    merged=merge_record(old,incoming)
    assert not merged.common and merged.samples[0].conditions['温度'].text=='60'
    assert not merged.samples[1].conditions


def test_existing_shared_reagent_scope_is_preserved_during_partial_merge():
    from matknow.experiments.import_agent import merge_record
    from matknow.experiments.models import ChemistryRecord, Reagent, chemistry_text
    old=ExperimentDraft('原实验','2026-10-14',samples=[SampleDraft('A1'),SampleDraft('A2')],chemistry=ChemistryRecord(reagents=[Reagent('X','5','g')]))
    incoming=ExperimentDraft('新文件','2026-10-14',samples=[SampleDraft('A1')],chemistry=ChemistryRecord(reagents=[Reagent('X','5','g')]))
    assert 'X' in chemistry_text(merge_record(old,incoming).chemistry,'A2')


@pytest.mark.parametrize('status',['running','completed','aborted'])
def test_plan_import_cannot_downgrade_executed_record(status):
    from matknow.experiments.import_agent import merge_record
    from matknow.experiments.models import ChemistryRecord
    old=ExperimentDraft('原实验','2026-10-14',samples=[SampleDraft('A1')],chemistry=ChemistryRecord(status=status))
    incoming=ExperimentDraft('方案','2026-10-14',samples=[SampleDraft('A1')],chemistry=ChemistryRecord(status='planned'))
    with pytest.raises(ValueError,match='计划'):merge_record(old,incoming)


def test_disjoint_sheets_from_same_workbook_can_append_results(qtbot,tmp_path):
    from openpyxl import Workbook
    from matknow.experiments.readers import read_record
    from matknow.experiments.drafts import from_table, ColumnMap, confirm_draft
    from matknow.experiments.files import ExperimentArchive, Upload
    from matknow.experiments.store import ExperimentStore
    from matknow.experiments.import_dialog import RecordImportDialog
    path=tmp_path/'results.xlsx';book=Workbook();book.active.title='Run1';book.active.append(['样品','强度']);book.active.append(['A1',2])
    second=book.create_sheet('Run2');second.append(['样品','强度']);second.append(['A1',3]);book.save(path);book.close()
    archive=ExperimentArchive(ExperimentStore(KnowledgeBase(tmp_path/'kb.sqlite3')))
    def extracted(sheet,token):
        doc=read_record(path,file_id=token,sheets=[sheet]);draft=from_table(doc,ColumnMap(sheet,1,(1,1,2,2),1,{}, {2:('强度','MPa')}),title='批次1',date='2026-10-14')
        return doc,confirm_draft(draft)
    first,old=extracted('Run1','one');snap=archive.save(old,[Upload(path,'one','source')])
    second,incoming=extracted('Run2','two');dialog=RecordImportDialog(second,archive=archive);qtbot.addWidget(dialog)
    dialog.source_path=path;dialog.set_editor(incoming);dialog.target.setCurrentIndex(dialog.target.findData(snap.id));dialog.confirm_check.setChecked(True);dialog.accept()
    assert dialog.result()==dialog.DialogCode.Accepted
    saved=archive.save(dialog.draft,dialog.uploads,experiment_id=dialog.snapshot.id,expected_revision=dialog.snapshot.revision)
    assert [r.value.text for r in saved.draft.samples[0].results]==['2','3']
    repeated=RecordImportDialog(second,archive=archive);qtbot.addWidget(repeated);repeated.source_path=path
    repeated.set_editor(incoming);repeated.target.setCurrentIndex(repeated.target.findData(snap.id));repeated.confirm_check.setChecked(True);repeated.accept()
    assert repeated.result()!=repeated.DialogCode.Accepted and '重复' in repeated.message.text()


def test_report_model_analysis_is_separate_and_preserves_measurements(report_archive):
    from matknow.experiments.report import build_report, report_markdown, analyze_report
    archive,snap=report_archive;report=build_report(archive,snap.id,profile_name='材料');before=deepcopy(report)
    data={'discussion':'记录显示存在差异，原因尚不能确定。','next_steps':'补充重复测试。','uncertainties':['A2 尚无结果。'],'source_sections':['样品 A1 结果','样品 A2 结果']}
    analyzed=analyze_report(report,Model(data))
    text=report_markdown(analyzed)
    assert 'AI 分析草稿' in text and '60.0 MPa' in text and '62.0 MPa' in text
    assert report==before and archive.store.get(snap.id).draft==snap.draft
    bad={**data,'source_sections':['伪造章节']}
    with pytest.raises(ModelError):analyze_report(report,Model(bad,bad))
    recycled={**data,'source_sections':['AI 分析草稿']}
    with pytest.raises(ModelError):analyze_report(analyzed,Model(recycled,recycled))


def test_report_preview_keeps_all_multiline_table_rows_and_model_analysis(qtbot,report_archive):
    from matknow.experiments.report import build_report, analyze_report
    from matknow.experiments.report_dialog import ReportDialog
    archive,snap=report_archive
    report=analyze_report(build_report(archive,snap.id,profile_name='材料'),Model({'discussion':'不能确定原因。','next_steps':'补充 A2 测量。','uncertainties':[],'source_sections':['样品 A1 结果']}))
    dialog=ReportDialog(report);qtbot.addWidget(dialog);dialog.show()
    text=dialog.preview.toPlainText()
    assert all(value in text for value in ('60.0 MPa','62.0 MPa','AI 分析草稿','补充 A2'))


def test_plan_quantities_are_not_presented_as_actual_use(tmp_path):
    from matknow.experiments.models import chemistry_text
    from matknow.experiments.files import ExperimentArchive, Upload
    from matknow.experiments.store import ExperimentStore
    from matknow.experiments.report import build_report, report_markdown
    draft=organize(source(),['段落1','段落2'],ExperimentDraft('导入','2026-10-14',samples=[SampleDraft('A1')]),Model(extracted()))
    assert '计划用量' in chemistry_text(draft.chemistry) and '实际试剂' not in chemistry_text(draft.chemistry)
    archive=ExperimentArchive(ExperimentStore(KnowledgeBase(tmp_path/'kb.sqlite3')))
    from matknow.experiments.drafts import confirm_draft
    original=tmp_path/'plan.txt';original.write_text(source().blocks[0].text,encoding='utf8')
    snap=archive.save(confirm_draft(draft),[Upload(original,'record','source')])
    text=report_markdown(build_report(archive,snap.id,profile_name='材料'))
    assert '计划用量' in text and '实际用量' not in text


def test_oversized_workbook_has_range_fallback_with_model_connected(qtbot,tmp_path,monkeypatch):
    from tests.test_experiment_desktop import window
    w=window(qtbot,tmp_path);page=w.experiment_page;page.model_client=Model(extracted());seen=[]
    def fail(*args,**kwargs):raise ValueError('Excel 读取范围超过 50000 个单元格，请缩小范围。')
    monkeypatch.setattr('matknow.experiments.page.read_record',fail)
    monkeypatch.setattr('matknow.experiments.page.workbook_sheets',lambda path:['Sheet1'])
    monkeypatch.setattr(page,'select_sheet',lambda path,names:seen.append(names))
    page.import_record(tmp_path/'large.xlsx',choose_sheet=True)
    qtbot.waitUntil(lambda:bool(seen) and not w.busy)
    assert seen==[['Sheet1']]


@pytest.fixture
def report_archive(tmp_path):
    from tests.test_experiment_report import report_archive as fixture
    return fixture.__wrapped__(tmp_path)
