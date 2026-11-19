import json
from copy import deepcopy

import pytest

from matknow.agent import ResearchAgent
from matknow.agent_tools import ResearchTools
from matknow.experiments.models import ExperimentDraft, SampleDraft, ResultDraft, Value
from matknow.experiments.store import ExperimentStore
from matknow.knowledge import KnowledgeBase


@pytest.fixture
def lab(tmp_path):
    kb=KnowledgeBase(tmp_path/'lab.sqlite3');store=ExperimentStore(kb)
    draft=ExperimentDraft('制备', '2026-10-14', samples=[SampleDraft(code, {'干燥温度':Value(temperature,'°C')}, [
        ResultDraft('强度',Value(value,'MPa'),method='拉伸',test_conditions={'温度':'25 °C'})])
        for code,temperature,value in [('A1','60','1.1'),('A2','80','1.3')]])
    return kb,store,store.save(draft)


class Script:
    def __init__(self,*responses):self.responses=list(responses);self.contexts=[]
    def complete(self,messages,json_output=False):
        assert json_output
        context=json.loads(messages[-1]['content']);self.contexts.append(context)
        response=self.responses[min(len(self.contexts)-1,len(self.responses)-1)]
        return json.dumps(response(context) if callable(response) else response,ensure_ascii=False)


def final(context):
    source=context['sources'][0]
    return {'answer':f'记录中的条件见原文 [{source["number"]}]。','source_ids':[source['id']],'status':'generated'}


def test_plans_retrieves_with_rewritten_query_validates_and_saves_once(lab):
    kb,_,_=lab;kb.import_text('方法','乙醇浸泡 24 h。')
    model=Script({'plan':['检索置换步骤','核对原文并回答'],'action':{'tool':'search_documents','arguments':{'query':'乙醇浸泡'}}},final)
    answer=ResearchAgent(kb,model).ask('溶剂置换怎么操作')
    assert answer.status=='generated' and len(model.contexts)==2
    assert answer.agent_run['plan']==['检索置换步骤','核对原文并回答']
    assert any(step['tool']=='search_documents' and step['status']=='ok' for step in answer.agent_run['steps'])
    assert answer.agent_run['outcome']=='completed'
    history=kb.list_history();assert len(history)==1 and history[0].agent_run==answer.agent_run


def test_selected_sample_ids_are_available_for_comparison_without_guessing_citation_ids(lab):
    kb,_,snapshot=lab;ids=[s.id for s in snapshot.draft.samples]
    def compare(context):
        return {'action':{'tool':'compare_samples','arguments':{'sample_ids':context.get('selected_sample_ids',[]),'metric':'强度'}}}
    def ranked(context):
        if context['observation'].get('status')!='ok':
            return {'answer':'缺少有效样品编号。','source_ids':[],'status':'insufficient_evidence'}
        row=context['observation']['result']['groups'][0]['rankings'][0]
        source=next(s for s in context['sources'] if s['id']==row['source_id'])
        return {'answer':f'A2最高，1.3 MPa [{source["number"]}]。','source_ids':[source['id']],'status':'generated'}
    model=Script(compare,ranked)
    answer=ResearchAgent(kb,model).ask('所选样品的强度最高值',sample_ids=ids,workspace='unified')
    assert answer.status=='generated' and model.contexts[0]['selected_sample_ids']==ids
    assert any(s['tool']=='compare_samples' and s['status']=='ok' for s in answer.agent_run['steps'])


def test_tools_enforce_selection_and_refuse_unknown_or_external_access(lab):
    kb,store,snapshot=lab;one,two=snapshot.draft.samples
    selected=kb.import_text('选中','干燥 60 °C');kb.import_text('未选中','干燥 PRIVATE_DOCUMENT')
    tools=ResearchTools(kb,document_ids=[selected.id],sample_ids=[one.id])
    tools.call('search_documents',{'query':'干燥'})
    assert 'PRIVATE_DOCUMENT' not in json.dumps(tools.context(),ensure_ascii=False)
    with pytest.raises(ValueError):tools.call('read_samples',{'sample_ids':[two.id]})
    with pytest.raises(ValueError):tools.call('search_documents',{'query':'干燥','document_ids':None})
    with pytest.raises(ValueError):tools.call('read_file',{'path':'C:/private/key'})
    assert tools.call('read_samples',{'sample_ids':[one.id]})['sample_count']==1


def test_comparison_uses_all_confirmed_measurements_and_exact_decimal_differences(lab):
    kb,_,snapshot=lab;ids=[s.id for s in snapshot.draft.samples]
    reference=snapshot.draft.samples[0].results[0].id
    tools=ResearchTools(kb,sample_ids=ids)
    result=tools.call('compare_samples',{'sample_ids':ids,'metric':'强度','reference_result_id':reference})
    assert result['sample_count']==2 and result['measurement_count']==2
    rows=result['groups'][0]['rankings']
    assert rows[0]['value']=='1.3' and rows[0]['difference']=='0.2'
    assert len(tools.sources)==4 and all(s.id.startswith('exp:') for s in tools.sources)


def test_bad_citations_are_repaired_before_history_is_written(lab):
    kb,_,_=lab;kb.import_text('方法','干燥 60 °C。')
    model=Script({'answer':'伪造 [1]','source_ids':['foreign-id'],'status':'generated'},final)
    answer=ResearchAgent(kb,model).ask('干燥')
    assert answer.status=='generated' and len(model.contexts)==2
    assert model.contexts[1]['observation']['status']=='validation_error'
    assert len(kb.list_history())==1


def test_unknown_and_repeated_tools_stop_with_bounded_requests(lab):
    kb,_,_=lab
    model=Script({'action':{'tool':'read_credentials','arguments':{}}})
    answer=ResearchAgent(kb,model).ask('读取密钥')
    assert answer.status!='generated' and len(model.contexts)<=6
    assert answer.agent_run['outcome'] in ('stopped','needs_input','failed')
    assert len(kb.list_history())==1


def test_explicit_empty_scope_cannot_be_expanded_by_model(lab):
    kb,_,_=lab;kb.import_text('私有资料','干燥 PRIVATE')
    model=Script({'action':{'tool':'search_documents','arguments':{'query':'干燥'}}},
                 {'answer':'资料不足，请选择资料。','source_ids':[],'status':'insufficient_evidence'})
    answer=ResearchAgent(kb,model).ask('干燥',document_ids=[],sample_ids=[])
    assert not answer.sources and answer.status=='insufficient_evidence'
    assert 'PRIVATE' not in json.dumps(model.contexts,ensure_ascii=False)


def test_experiment_revision_change_blocks_current_conclusion(lab):
    kb,store,snapshot=lab
    def edit_and_finish(context):
        draft=deepcopy(snapshot.draft);draft.samples[0].conditions['干燥温度'].text='90'
        store.save(draft,experiment_id=snapshot.id,expected_revision=1)
        return final(context)
    answer=ResearchAgent(kb,Script(edit_and_finish)).ask('干燥',sample_ids=[snapshot.draft.samples[0].id],workspace='experiments')
    assert answer.status=='insufficient_evidence' and '更新' in answer.answer


def test_no_model_keeps_local_preview_without_planning_calls(lab):
    kb,_,_=lab;kb.import_text('方法','干燥 60 °C。')
    answer=ResearchAgent(kb).ask('干燥')
    assert answer.status=='retrieval_preview'


def test_multistep_observations_survive_intermediate_search_and_require_calculation(lab):
    kb,_,_=lab;kb.import_text('方法','拉伸方法 ASTM。')
    def compare(context):
        previous=next(o for o in context['observations'] if o.get('tool')=='list_samples')
        ids=[s['sample_id'] for s in previous['result']['samples']]
        return {'action':{'tool':'compare_samples','arguments':{'sample_ids':ids,'metric':'强度'}}}
    def ranked(context):
        group=context['observation']['result']['groups'][0]
        assert group['rankings'][0]['value']=='1.3'
        source=next(s for s in context['sources'] if s['id']==group['rankings'][0]['source_id'])
        return {'answer':f'本次两个样品的全部拉伸记录中，最大强度为1.3 MPa [{source["number"]}]。',
                'source_ids':[source['id']],'status':'generated'}
    model=Script({'action':{'tool':'list_samples','arguments':{}}},
                 {'action':{'tool':'search_documents','arguments':{'query':'ASTM'}}},compare,ranked)
    answer=ResearchAgent(kb,model).ask('这两个样品强度最大值是多少',workspace='experiments')
    assert answer.status=='generated' and len(model.contexts)==4


def test_ranking_cannot_be_claimed_without_program_comparison(lab):
    kb,_,_=lab
    model=Script(final)
    answer=ResearchAgent(kb,model).ask('强度最高的是哪个样品',workspace='experiments')
    assert answer.status=='model_error'
    assert model.contexts[1]['observation']['status']=='validation_error'


def test_agent_record_and_answer_are_saved_atomically(lab):
    from matknow.models import Answer
    kb,_,_=lab
    invalid=Answer('invalid','问题','回答','insufficient_evidence','now',agent_run={'bad':float('nan')})
    with pytest.raises(ValueError):kb.save_answer(invalid)
    assert kb.list_history()==[]


def test_rejected_answer_cannot_be_returned_if_round_budget_runs_out(lab):
    kb,_,_=lab
    responses=[final,*[{'action':{'tool':'search_experiments','arguments':{'query':f'A{i}'}}} for i in range(5)]]
    answer=ResearchAgent(kb,Script(*responses)).ask('强度最高的样品',workspace='experiments')
    assert answer.status!='generated' and answer.agent_run['outcome']=='stopped'


def test_unconfirmed_test_group_cannot_justify_generated_ranking(lab):
    kb,store,snapshot=lab
    draft=deepcopy(snapshot.draft)
    for sample in draft.samples:sample.results[0].test_conditions={}
    snapshot=store.save(draft,experiment_id=snapshot.id,expected_revision=1)
    ids=[s.id for s in snapshot.draft.samples]
    model=Script({'action':{'tool':'compare_samples','arguments':{'sample_ids':ids}}},final)
    answer=ResearchAgent(kb,model).ask('强度最高的样品',sample_ids=ids,workspace='experiments')
    assert answer.status!='generated'


def test_partial_sample_read_cannot_justify_selected_scope_count(lab):
    kb,_,snapshot=lab;ids=[s.id for s in snapshot.draft.samples]
    model=Script({'action':{'tool':'read_samples','arguments':{'sample_ids':ids[:1]}}},final)
    answer=ResearchAgent(kb,model).ask('所选样品一共有几个',sample_ids=ids,workspace='experiments')
    assert answer.status!='generated'


def test_metric_filter_keeps_requested_repeats_without_unrelated_evidence(lab):
    kb,store,snapshot=lab;draft=deepcopy(snapshot.draft)
    for sample in draft.samples:
        sample.results.extend(ResultDraft(f'其他指标{i}',Value(str(i),'MPa'),method='拉伸',test_conditions={'温度':'25 °C'}) for i in range(23))
    snapshot=store.save(draft,experiment_id=snapshot.id,expected_revision=1)
    ids=[s.id for s in snapshot.draft.samples]
    tools=ResearchTools(kb,sample_ids=ids)
    result=tools.call('compare_samples',{'sample_ids':ids,'metric':'强度'})
    assert result['measurement_count']==2 and len(tools.sources)==4
    assert '其他指标' not in json.dumps(tools.context(),ensure_ascii=False)


def test_filtered_metric_cannot_justify_total_measurement_count(lab):
    kb,store,snapshot=lab;draft=deepcopy(snapshot.draft)
    for sample in draft.samples:sample.results.append(ResultDraft('延伸率',Value('3','%'),method='拉伸',test_conditions={'温度':'25 °C'}))
    snapshot=store.save(draft,experiment_id=snapshot.id,expected_revision=1)
    ids=[s.id for s in snapshot.draft.samples]
    model=Script({'action':{'tool':'compare_samples','arguments':{'sample_ids':ids,'metric':'强度'}}},final)
    answer=ResearchAgent(kb,model).ask('所选样品总共几次测量',sample_ids=ids,workspace='experiments')
    assert answer.status!='generated'


def test_malformed_tool_type_closes_safely_with_progress_enabled(lab):
    kb,_,_=lab;progress=[]
    model=Script({'action':{'tool':[],'arguments':{}}})
    answer=ResearchAgent(kb,model).ask('干燥',progress=progress.append)
    assert answer.status=='model_error' and progress
    assert len(kb.list_history())==1
