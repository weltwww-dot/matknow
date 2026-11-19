import json
from threading import Event
import pytest
from PySide6.QtCore import Qt
from matknow.knowledge import KnowledgeBase
from matknow.models import Answer, Evidence
from matknow.agent import ResearchAgent
from matknow.agent_tools import ResearchTools
from matknow.conversations import scope_key
from matknow.llm import ModelError
from matknow.service import validate_generated_answer
from tests.test_experiment_report import report_archive
from tests.test_report_writer import Writer
from tests.test_experiment_desktop import window
from tests.test_chat_routing import Model
from tests.test_model_first import source, extracted

def test_changing_sample_scope_retains_visible_dialogue_but_not_path_grants(qtbot,tmp_path):
    w=window(qtbot,tmp_path)
    a=Answer('history','请读取 D:\\research\\notes.txt','三项条件：温度、时间、厚度。','generated','now')
    w.conversation=[a];w._turn_scopes={a.id:scope_key()}
    turns=w._conversation_context(scope_key(None,['sample']))
    assert len(turns)==1 and '厚度' in turns[0]['answer']
    assert '历史' in turns[0]['answer']
    assert w._conversation_file_paths(scope_key(None,['sample']))==[]

def test_explicit_empty_grants_do_not_reauthorize_historical_paths(tmp_path):
    model=Model({'mode':'chat','answer':'继续'})
    ResearchAgent(KnowledgeBase(tmp_path/'kb.sqlite3'),model).respond('继续',conversation=[
        {'question':r'读取 D:\research\notes.txt','answer':'之前的内容','mode':'research'}],user_file_paths=[])
    assert json.loads(model.messages[-1][-1]['content'])['file_paths']==[]

def test_citation_numbers_are_mapped_only_to_current_evidence():
    sources=[Evidence('source-a','doc','a','p1','first'),Evidence('source-b','doc','b','p2','second')]
    assert validate_generated_answer(json.dumps({'answer':'结论 [2]','source_ids':['2'],'status':'generated'}),sources)[0]=='结论 [2]'
    for ids in [['3'],['2','source-b'],['foreign']]:
        with pytest.raises(ModelError):validate_generated_answer(json.dumps({'answer':'结论 [2]','source_ids':ids,'status':'generated'}),sources)

def test_successful_draft_finishes_without_another_generation(tmp_path):
    from matknow.models import Chunk
    kb=KnowledgeBase(tmp_path/'kb.sqlite3');doc=kb._save_document('方案.docx',[Chunk(b.text,b.locator) for b in source().blocks[:2]])
    client=Model({'action':{'tool':'prepare_experiment_draft','arguments':{'document_id':doc.id,'kind':'plan'}}},extracted(),ModelError('不能再请求'))
    answer=ResearchAgent(kb,client).ask('生成实验方案',workspace='experiments')
    assert answer.status=='generated' and answer.agent_run['draft_ids']
    assert '已生成' in answer.answer and len(client.messages)==2

def test_report_revises_once_and_returns_specific_failure(report_archive):
    from matknow.experiments.report import build_report
    from matknow.experiments.report_writer import write_report
    archive,snapshot=report_archive
    class Revising(Writer):
        def complete(self,messages,**kwargs):
            payload=json.loads(messages[-1]['content'])
            if payload['task']=='review_report':self.approve=sum(c['task']=='review_report' for c in self.calls)>0
            return super().complete(messages,**kwargs)
    client=Revising()
    result=write_report(build_report(archive,snapshot.id,profile_name='测试'),client,archive=archive)
    assert [c['task'] for c in client.calls]==['write_report','review_report','write_report','review_report']
    assert client.calls[2]['revision_issues']==['把计划写成实测'] and 'AI 撰写' in result.summary
    bad=Writer(approve=False)
    with pytest.raises(ModelError,match='把计划写成实测'):write_report(build_report(archive,snapshot.id,profile_name='测试'),bad,archive=archive)
    assert len(bad.calls)==4


def test_report_validation_tells_model_exactly_which_source_to_restore(report_archive):
    from matknow.experiments.report import build_report
    from matknow.experiments.report_writer import write_report
    archive,snapshot=report_archive
    class MissingSource(Writer):
        def complete(self,messages,**kwargs):
            data=json.loads(super().complete(messages,**kwargs))
            if len(self.calls)==1:
                self.missing=self.calls[0]['required_source_ids'][-1]
                for section in data['sections']:section['source_ids']=[s for s in section['source_ids'] if s!=self.missing]
            return json.dumps(data)
    client=MissingSource();result=write_report(build_report(archive,snapshot.id,profile_name='测试'),client,archive=archive)
    assert client.missing in client.calls[1]['validation_feedback'] and result.summary.startswith('AI')

def test_report_tool_preserves_revision_and_blocks_partial_sample_scope(report_archive):
    archive,snapshot=report_archive;kb=archive.store.knowledge
    tools=ResearchTools(kb,model_client=Writer())
    receipt=tools.call('generate_experiment_report',{'experiment_id':snapshot.id})
    assert receipt['revision']==snapshot.revision and len(tools.generated_reports)==1
    assert 'database' not in tools.generated_reports[0]['report']
    with pytest.raises(ValueError,match='全部样品'):
        ResearchTools(kb,sample_ids=[snapshot.draft.samples[0].id],model_client=Writer()).call('generate_experiment_report',{'experiment_id':snapshot.id})

def test_waiting_chat_can_draft_next_message_and_stop_before_tools(qtbot,tmp_path,monkeypatch):
    from tests.test_desktop import make_profile_window
    from matknow.llm import DeepSeekClient
    _,credentials,w=make_profile_window(qtbot,tmp_path);credentials.key='FAKE-stop-key'
    entered,release=Event(),Event();calls=[]
    def complete(self,messages,**kwargs):
        calls.append(messages);entered.set();assert release.wait(5)
        return json.dumps({'mode':'research','workspace':'documents'})
    monkeypatch.setattr(DeepSeekClient,'complete',complete)
    w.question_edit.setPlainText('查资料');w.ask()
    try:
        qtbot.waitUntil(entered.is_set)
        assert w.question_edit.isEnabled() and w.stop_button.isVisible()
        w.question_edit.setPlainText('下一条保留');w.stop_button.click()
    finally:release.set();qtbot.waitUntil(lambda:not w.busy)
    assert len(calls)==1 and w.question_edit.toPlainText()=='下一条保留'
    assert w.last_answer.agent_run['outcome']=='stopped'
    from matknow.tasks import check_cancelled
    check_cancelled(w.service.model_client)

def test_small_window_can_toggle_evidence(qtbot,tmp_path):
    w=window(qtbot,tmp_path);w.resize(1000,680);qtbot.wait(20)
    assert not w.evidence_panel.isVisible()
    w.evidence_button.click()
    assert w.evidence_panel.isVisible()


def test_stop_batch_import_keeps_completed_files_visible(qtbot,tmp_path,monkeypatch):
    from matknow.tasks import check_cancelled
    w=window(qtbot,tmp_path);client=Model();w.service.model_client=client
    paths=[tmp_path/'one.txt',tmp_path/'two.txt']
    for path in paths:path.write_text('实验资料',encoding='utf8')
    def organize(*args):client.cancel_event.set()
    monkeypatch.setattr('matknow.library_agent.organize_document',organize)
    w.import_paths(paths);qtbot.waitUntil(lambda:not w.busy)
    assert w.document_list.count()==1 and len(w.knowledge.list_documents())==1
    assert '停止' in w.status_label.text()
    check_cancelled(client)


def test_chat_report_does_not_expand_all_measurements_into_chat_sources(tmp_path):
    from matknow.experiments.models import ExperimentDraft,SampleDraft,ResultDraft,Value
    from matknow.experiments.store import ExperimentStore
    kb=KnowledgeBase(tmp_path/'kb.sqlite3')
    snapshot=ExperimentStore(kb).save(ExperimentDraft('大量测量','2026-10-17',samples=[SampleDraft('A1',results=[ResultDraft('强度',Value(str(i),'MPa')) for i in range(50)])]))
    tools=ResearchTools(kb,model_client=Writer())
    tools.call('generate_experiment_report',{'experiment_id':snapshot.id})
    assert len(tools.sources)==1 and tools.generated_reports


def test_report_round_trip_and_chat_preview_remain_on_original_revision(qtbot,tmp_path,monkeypatch):
    from copy import deepcopy
    from tests.test_chemical_record import record
    from matknow.experiments.report import build_report,report_payload,restore_report,report_markdown
    from matknow.experiments.report_dialog import ReportDialog
    w=window(qtbot,tmp_path);archive=w.experiment_page.archive
    snapshot=archive.save(record(),[]);report=build_report(archive,snapshot.id,profile_name='测试')
    payload=json.loads(json.dumps(report_payload(report)))
    assert report_markdown(restore_report(payload,w.knowledge.path))==report_markdown(report)
    answer=Answer('report-answer','生成报告','已生成','generated','now',agent_run={'plan':[],'steps':[],'outcome':'completed','reports':[{'experiment_id':snapshot.id,'revision':1,'report':payload}]})
    w.knowledge.save_answer(answer);w.present_answer(answer)
    changed=deepcopy(snapshot.draft);changed.chemistry.reagents[0].quantity='0.55'
    archive.save(changed,[],experiment_id=snapshot.id,expected_revision=1)
    seen=[]
    monkeypatch.setattr(ReportDialog,'exec',lambda dialog:seen.append(report_markdown(dialog.report)))
    w.open_report(answer.id,0)
    assert len(seen)==1 and '0.50 g' in seen[0] and '0.55 g' not in seen[0]
    w.conversation=[];w.open_report(answer.id,0)
    assert len(seen)==1


def test_merge_preview_shows_existing_materials_and_all_results(qtbot,tmp_path):
    from copy import deepcopy
    from tests.test_chemical_record import record
    from matknow.experiments.import_dialog import RecordImportDialog
    from matknow.experiments.models import ExperimentDraft,SampleDraft,ResultDraft,Value
    w=window(qtbot,tmp_path);archive=w.experiment_page.archive;snapshot=archive.save(record(),[])
    before=deepcopy(snapshot.draft)
    incoming=ExperimentDraft('新增结果','2026-10-17',samples=[SampleDraft('A1',results=[ResultDraft('强度',Value('60','MPa'))])])
    dialog=RecordImportDialog(source(),parent=w,archive=archive,prepared=incoming);qtbot.addWidget(dialog)
    dialog.target.setCurrentIndex(dialog.target.findData(snapshot.id))
    text=dialog.summary.toPlainText()
    assert '沿用已有目的、材料' in text and 'A1：新增 1 条结果' in text
    assert 'A2' in text and '合并后记录' in text
    assert archive.store.get(snapshot.id).draft==before
    dialog.target.setCurrentIndex(0)
    assert '合并后记录' not in dialog.summary.toPlainText()

