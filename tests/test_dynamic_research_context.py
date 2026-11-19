import json
import pytest

from matknow.agent import ResearchAgent
from matknow.conversations import ContextMemory
from matknow.knowledge import KnowledgeBase
from matknow.models import Chunk,Evidence
from matknow.model_context import input_budget
from matknow.model_tasks import complete_task,TaskContextError
from tests.test_agent import Script,final
from tests.test_chat_routing import Model
from tests.test_experiment_report import report_archive


def test_input_budget_uses_the_window_remaining_after_output_reserve():
    assert input_budget(1000000)==991808
    assert input_budget(32768)==24576


@pytest.mark.parametrize('capacity,allowed',[(128000,True),(None,True),(8192,False)])
def test_research_request_has_no_fixed_character_ceiling(tmp_path,capacity,allowed):
    kb=KnowledgeBase(tmp_path/'kb.sqlite3')
    kb._save_document('长方法.txt',[Chunk('干燥条件。'*11000,'全文')])
    client=Script(final);client.context_window=capacity
    answer=ResearchAgent(kb,client).ask('干燥条件')
    assert (answer.status=='generated')==allowed
    assert bool(client.contexts)==allowed
    if allowed:assert len(client.contexts[0]['sources'][0]['text'])>48000


@pytest.mark.parametrize('capacity', [128000,None])
def test_specialist_accepts_full_long_source_without_a_character_limit(capacity):
    client=Model({'ok':True});client.context_window=capacity
    text='原文。'*20000
    assert complete_task(client,'experiment','import_record',{'text':text},lambda data:data)=={'ok':True}
    assert json.loads(client.messages[0][-1]['content'])['text']==text


def test_small_model_is_protected_by_tokens_even_below_old_character_limit():
    client=Model({'ok':True});client.context_window=8192
    with pytest.raises(TaskContextError):
        complete_task(client,'experiment','import_record',{'text':'实验内容。'*2500},lambda data:data)
    assert not client.messages


def test_old_policy_character_parameter_is_compatible_but_no_longer_limits_requests(tmp_path):
    from tests.test_agent_policy import write_policy
    path=tmp_path/'agent.md';write_policy(path,max_context_chars=2048)
    kb=KnowledgeBase(tmp_path/'kb.sqlite3');kb._save_document('方法.txt',[Chunk('干燥条件。'*11000,'全文')])
    client=Script(final);client.context_window=128000
    assert ResearchAgent(kb,client,policy_path=path).ask('干燥条件').status=='generated'


def test_unknown_capacity_sends_full_history_until_service_reports_overflow(tmp_path):
    turns=[{'question':str(i),'answer':'历史内容。'*4000,'mode':'chat'} for i in range(4)]
    client=Model(*[lambda context:{'summary':'较早的讨论。'} if context.get('task')=='summarize_conversation' else {'ok':True}]*20)
    memory=ContextMemory(KnowledgeBase(tmp_path/'kb.sqlite3'),turns,client)
    memory.complete('回答 JSON',{'question':'继续'})
    assert len(client.messages)==1 and len(client.messages[0][1:-1])==8
    assert not memory.summary


def test_unknown_capacity_overflow_summarizes_short_history_in_one_call(tmp_path):
    from matknow.llm import ContextWindowError
    turns=[{'question':'Q','answer':'a'*1000,'mode':'chat'}]
    requests=[];summaries=[]
    def respond(context):
        if context.get('task')=='summarize_conversation':
            summaries.append(context);return {'summary':'S'}
        requests.append(context)
        if len(requests)==1:raise ContextWindowError('容量不足')
        return {'ok':True}
    client=Model(*[respond]*5)
    ContextMemory(KnowledgeBase(tmp_path/'kb.sqlite3'),turns,client).complete('JSON',{'question':'继续'})
    assert len(summaries)==1 and len(requests)==2
    assert summaries[0]['messages'][-1]['content']=='a'*1000


def test_large_checkpoint_is_rebuilt_from_raw_history_for_smaller_model(tmp_path):
    from tests.test_context_management import saved_chat
    turns=[{'question':'Q','answer':'原始条件。'*3000,'mode':'chat'}]
    kb=KnowledgeBase(tmp_path/'kb.sqlite3');identifier=saved_chat(kb,turns)
    old=ContextMemory(kb,turns,Model(),conversation_id=identifier)
    with kb.connection() as db:
        db.execute('INSERT INTO context_summaries VALUES (?,?,?,?,?)',(identifier,'',old._hash(1),1,'旧'*8000))
    turns=turns+[{'question':'继续','answer':'新条件。'*375,'mode':'chat'}]
    def respond(context):
        return {'summary':'原始条件与新条件待核对。'} if context.get('task')=='summarize_conversation' else {'ok':True}
    client=Model(*[respond]*20);client.context_window=8192
    memory=ContextMemory(kb,turns,client,conversation_id=identifier)
    memory.complete('JSON',{'question':'继续'})
    assert memory.summary and len(client.messages)>1
    summaries=[json.loads(m[-1]['content']) for m in client.messages[:-1]]
    assert summaries[0]['previous_summary']==''
    assert '原始条件。' in summaries[0]['messages'][-1]['content']


@pytest.mark.parametrize('capacity',[128000,None])
def test_library_classification_uses_model_capacity_instead_of_fixed_parts(capacity):
    from matknow.library_agent import organize_sources
    def classify(context):
        return {'category':'方法资料','summary':'完整方法摘要','tags':[],
                'locators':[s['locator'] for s in context['sources']] if 'sources' in context else [loc for part in context['parts'] for loc in part['locators']]}
    client=Model(*[classify]*5);client.context_window=capacity
    sources=[Evidence('one','doc','方法.txt','第一节','方法。'*9000),Evidence('two','doc','方法.txt','第二节','条件。'*9000)]
    result=organize_sources('方法.txt',sources,client)
    assert len(client.messages)==1 and result['locators']==['第一节','第二节']


@pytest.mark.parametrize('capacity',[128000,None])
def test_report_uses_the_entire_source_when_the_service_can_accept_it(report_archive,capacity):
    from matknow.experiments.report import build_report
    from matknow.experiments.report_writer import write_report
    from tests.test_report_writer import Writer
    archive,snapshot=report_archive
    report=build_report(archive,snapshot.id,profile_name='材料')
    report.blocks[0].paragraphs=['实验记录。'*11000]
    client=Writer();client.context_window=capacity
    result=write_report(report,client,archive=archive)
    assert result.summary.startswith('AI') and len(client.calls)==2
    assert '实验记录。'*11000 in json.dumps(client.calls[0],ensure_ascii=False)
