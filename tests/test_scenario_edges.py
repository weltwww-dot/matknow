from copy import deepcopy

import pytest

from matknow.agent_tools import ResearchTools
from matknow.agent import ResearchAgent
from tests.test_agent import lab, Script


@pytest.mark.parametrize('variant',['missing_conditions','different_method','different_unit','different_conditions','missing_measurement'])
def test_a_partial_test_group_cannot_authorize_a_global_selected_sample_ranking(lab,variant):
    kb,store,snapshot=lab;draft=deepcopy(snapshot.draft);result=draft.samples[1].results[0]
    if variant=='missing_conditions':result.test_conditions={}
    elif variant=='different_method':result.method='压缩'
    elif variant=='different_unit':result.value.unit='kPa'
    elif variant=='different_conditions':result.test_conditions={'温度':'30 °C'}
    else:draft.samples[1].results=[]
    snapshot=store.save(draft,experiment_id=snapshot.id,expected_revision=1)
    ids=[s.id for s in snapshot.draft.samples]
    tools=ResearchTools(kb,sample_ids=ids)
    calculation=tools.call('compare_samples',{'sample_ids':ids,'metric':'强度'})
    row=calculation['groups'][0]['rankings'][0]
    with pytest.raises(ValueError,match='可比|测试组'):
        tools.validate_calculation('本次所选样品的最高强度',[row['source_id']],[{'tool':'compare_samples','status':'ok','result':calculation}])


def test_reference_measurement_is_identifiable_without_using_its_citation_id(lab):
    kb,_,snapshot=lab;ids=[s.id for s in snapshot.draft.samples]
    def compare(context):
        source=next(s for s in context['sources'] if '结果：强度 = 1.1' in s['text'])
        return {'action':{'tool':'compare_samples','arguments':{'sample_ids':ids,'metric':'强度','reference_result_id':source.get('result_id',source['id'])}}}
    def finish(context):
        if context['observation'].get('status')!='ok':
            return {'answer':'无法确定参照测量。','source_ids':[],'status':'insufficient_evidence'}
        row=context['observation']['result']['groups'][0]['rankings'][0]
        assert row['difference']=='0.2'
        source=next(s for s in context['sources'] if s['id']==row['source_id'])
        return {'answer':f'A2相差0.2 MPa [{source["number"]}]。','source_ids':[source['id']],'status':'generated'}
    answer=ResearchAgent(kb,Script(compare,finish)).ask('所选样品强度以A1测量为参照计算差值',sample_ids=ids,workspace='unified')
    assert answer.status=='generated' and '0.2' in answer.answer
