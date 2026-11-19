import json
from copy import deepcopy

import pytest

from matknow.experiments.compare import compare_samples
from matknow.experiments.models import ExperimentDraft, SampleDraft, ResultDraft, Value, SourceRef
from matknow.experiments.store import ExperimentStore
from matknow.experiments.service import ExperimentAskService
from matknow.knowledge import KnowledgeBase
from matknow.llm import ModelError
from matknow.service import AskService


class Model:
    def __init__(self, output=None): self.messages = None; self.output = output
    def complete(self, messages, json_output=False):
        self.messages = messages
        if self.output == 'error': raise ModelError('测试失败')
        c = json.loads(messages[-1]['content'])
        return self.output or json.dumps({'answer': '结果见记录 [1]', 'source_ids': [c['sources'][0]['id']], 'status': 'generated'})


@pytest.fixture
def saved(tmp_path):
    kb = KnowledgeBase(tmp_path / 'kb.sqlite3'); store = ExperimentStore(kb)
    draft = ExperimentDraft('实验', '2026-10-13', samples=[SampleDraft(f'A{i}', {}, [
        ResultDraft('强度', Value(str(i), 'MPa'), method='拉伸', test_conditions={'温度': '25'})]) for i in range(20)])
    return kb, store, store.save(draft)


def test_exp_answer_has_real_revision_sample_result_and_original_locators(saved):
    kb, store, snap = saved
    answer = ExperimentAskService(store).ask('强度', [snap.draft.samples[0].id])
    assert all(s.id.startswith('exp:') for s in answer.sources)
    assert 'A0' in answer.sources[0].text and '版本 1' in answer.sources[0].locator
    assert kb.list_history()[0].sources[0].id == answer.sources[0].id


def test_only_current_profile_selected_records_reach_model(saved, tmp_path):
    _, store, snap = saved
    other = ExperimentStore(KnowledgeBase(tmp_path / 'other.sqlite3'))
    other.save(ExperimentDraft('PRIVATE_OTHER_PROFILE', '2026-10-13', samples=[SampleDraft('PRIVATE')]))
    model = Model()
    ExperimentAskService(store, model).ask('强度', [snap.draft.samples[0].id])
    prompt = model.messages[-1]['content']
    assert 'PRIVATE' not in prompt and 'A19' not in prompt


@pytest.mark.parametrize('output', ['error', '{}', '{"answer":"错误 [1]","source_ids":["foreign"],"status":"generated"}'])
def test_missing_or_foreign_citations_and_model_error_return_explicit_preview(saved, output):
    _, store, _ = saved
    answer = ExperimentAskService(store, Model(output)).ask('强度')
    assert answer.status == 'model_error' and answer.sources


def test_global_count_or_best_question_without_complete_comparison_requests_scope(saved):
    _, store, _ = saved
    model = Model(); service = ExperimentAskService(store, model)
    for q in ['哪个最好', '一共多少次强度测试', '强度排名', '强度最大值']:
        assert service.ask(q).status == 'insufficient_evidence'
    assert model.messages is None


def test_comparison_answer_uses_full_program_calculation(saved):
    _, store, snap = saved
    ids = [s.id for s in snap.draft.samples]
    model = Model(); answer = ExperimentAskService(store, model).ask('强度排名', ids, compare_samples(store, ids))
    c = json.loads(model.messages[-1]['content'])
    assert c['comparison']['rankings'][0][0]['value']['text'] == '19'
    assert len(c['comparison']['rankings'][0]) == 20
    assert len(answer.sources) == 40


def test_revision_update_preserves_historical_value_and_delete_invalidates_it(saved):
    kb, store, snap = saved
    answer = ExperimentAskService(store).ask('强度', [snap.draft.samples[0].id])
    old = answer.sources[0]
    draft = deepcopy(snap.draft); draft.samples[0].results[0].value.text = '60'
    store.save(draft, experiment_id=snap.id, expected_revision=1)
    assert kb.get_evidence(old.id).text == old.text
    assert '已更新' in kb.get_evidence(old.id).locator
    store.delete_rows(snap.id)
    assert kb.get_evidence(old.id) is None


def test_no_model_binary_image_or_secret_enters_prompt_or_history(saved):
    kb, store, snap = saved
    draft = deepcopy(snap.draft)
    draft.samples[0].results.append(ResultDraft('显微图', Value('待观察'), kind='image'))
    store.save(draft, experiment_id=snap.id, expected_revision=1)
    answer = ExperimentAskService(store).ask('显微图')
    assert answer.status == 'retrieval_preview'
    assert not any('base64' in s.text for s in answer.sources)


def test_history_resolves_document_and_experiment_sources_in_batches(saved, monkeypatch):
    kb, store, _ = saved
    kb.import_text('旧资料', '强度 文献')
    AskService(kb).ask('强度')
    ExperimentAskService(store).ask('强度')
    calls = []; original = kb.get_evidence_many
    def batch(ids): calls.append(ids); return original(ids)
    monkeypatch.setattr(kb, 'get_evidence_many', batch)
    assert len(kb.list_history()) == 2 and len(calls) == 1


def test_explicit_selected_calculation_and_difference_are_passed_to_model(saved):
    _,store,snap=saved;ids=[s.id for s in snap.draft.samples];comparison=compare_samples(store,ids)
    rows=comparison.groups[0]['measurements'];selected=[rows[0]['result_id'],rows[2]['result_id']]
    comparison.calculation={'result_ids':selected,'descending':False,'reference_result_id':selected[0]}
    model=Model();ExperimentAskService(store,model).ask('解释刚才的差值',ids,comparison)
    calculation=json.loads(model.messages[-1]['content'])['comparison']['selected_calculation']
    assert calculation['result_ids']==selected and not calculation['descending']
    assert calculation['rows'][-1]['difference']=='2'


def test_comparison_followup_receives_native_conversation_history(saved):
    _,store,snap=saved;ids=[s.id for s in snap.draft.samples]
    history=[{'question':'请比较这批强度','answer':'先核对测试条件，再解释数值。','mode':'research'}]
    model=Model()
    ExperimentAskService(store,model).ask('解释刚才的差值',ids,compare_samples(store,ids),conversation=history)
    assert model.messages[1:-1]==[{'role':'user','content':history[0]['question']},{'role':'assistant','content':history[0]['answer']}]
    assert 'comparison' in json.loads(model.messages[-1]['content'])
