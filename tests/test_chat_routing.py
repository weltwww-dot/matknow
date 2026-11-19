import json

import pytest

from matknow.agent import ResearchAgent
from matknow.knowledge import KnowledgeBase
from matknow.llm import DeepSeekClient, ModelError
from tests.test_agent import final
from tests.test_agent_policy import write_policy
from tests.test_desktop import make_profile_window
from tests.test_unified_chat import records


class Model:
    model='custom-model-id'
    def __init__(self,*responses):self.responses=iter(responses);self.messages=[]
    def complete(self,messages,json_output=False):
        assert json_output
        self.messages.append(messages)
        response=next(self.responses)
        if isinstance(response,Exception):raise response
        return json.dumps(response(json.loads(messages[-1]['content'])) if callable(response) else response)


def test_plain_chat_skips_policy_tools_and_private_data_even_with_selections(tmp_path,monkeypatch):
    kb,doc,snap=records(tmp_path)
    model=Model({'mode':'chat','answer':'当前选择的模型是 custom-model-id。'})
    def forbidden(*a,**kw):raise AssertionError('普通聊天不应加载规则或检索资料')
    monkeypatch.setattr('matknow.agent.load_policy',forbidden)
    monkeypatch.setattr(kb,'search',forbidden)
    answer=ResearchAgent(kb,model,'PRIVATE_PROFILE',policy_path=tmp_path/'missing.md').respond(
        '你是什么模型',[doc.id],sample_ids=[s.id for s in snap.draft.samples])
    assert answer.status=='chat' and not answer.sources and answer.agent_run is None
    assert len(model.messages)==1 and len(kb.list_history())==1
    prompt=json.dumps(model.messages,ensure_ascii=False)
    assert 'PRIVATE_' not in prompt and 'custom-model-id' in prompt


def test_research_route_loads_policy_after_decision_and_keeps_both_scopes(tmp_path):
    kb,doc,snap=records(tmp_path);policy=tmp_path/'agent.md';write_policy(policy,'POLICY_RESEARCH_ONLY')
    model=Model({'mode':'research'},final)
    answer=ResearchAgent(kb,model,policy_path=policy).respond('查询所选样品的干燥条件',[doc.id],sample_ids=[snap.draft.samples[0].id])
    assert answer.status=='generated' and answer.agent_run['outcome']=='completed'
    assert 'POLICY_RESEARCH_ONLY' not in model.messages[0][0]['content']
    assert 'POLICY_RESEARCH_ONLY' in model.messages[1][0]['content']
    assert any(s.id.startswith('exp:') for s in answer.sources)
    assert 'PRIVATE_' not in json.dumps(model.messages,ensure_ascii=False)
    assert len(kb.list_history())==1


@pytest.mark.parametrize('decision',[{}, {'mode':'other'}, {'mode':'chat','answer':''}, {'mode':'research','answer':'未经核对的结论'}])
def test_invalid_route_stops_without_tools_or_automatic_paid_retry(tmp_path,decision):
    kb=KnowledgeBase(tmp_path/'kb.sqlite3');model=Model(decision)
    answer=ResearchAgent(kb,model).respond('查看实验记录')
    assert answer.status=='model_error' and not answer.sources and answer.agent_run is None
    assert len(model.messages)==1 and len(kb.list_history())==1


def test_routing_network_failure_is_saved_once_without_private_evidence(tmp_path):
    kb,_,_=records(tmp_path);model=Model(ModelError('无法连接模型服务。'))
    answer=ResearchAgent(kb,model).respond('你好')
    assert answer.status=='model_error' and not answer.sources
    assert len(kb.list_history())==1 and 'PRIVATE_' not in json.dumps(model.messages)


def test_missing_research_policy_blocks_followup_but_not_routing(tmp_path):
    kb=KnowledgeBase(tmp_path/'kb.sqlite3');model=Model({'mode':'research'})
    answer=ResearchAgent(kb,model,policy_path=tmp_path/'missing.md').respond('查询本地实验记录')
    assert answer.status=='model_error' and 'agent.md' in answer.answer
    assert len(model.messages)==1 and len(kb.list_history())==1 and not answer.sources


def test_plain_chat_ui_restores_history_and_marks_model_connected(qtbot,tmp_path,monkeypatch):
    _,credentials,w=make_profile_window(qtbot,tmp_path);credentials.key='FAKE-chat-key'
    w.knowledge.import_text('旧资料','干燥 PRIVATE_OLD_SOURCE')
    w.present_answer(ResearchAgent(w.knowledge).ask('干燥'))
    monkeypatch.setattr(DeepSeekClient,'complete',lambda *a,**kw:json.dumps({'mode':'chat','answer':'当前模型是 deepseek-flash。'}))
    def forbidden(*a,**kw):raise AssertionError('聊天不应读取 agent.md')
    monkeypatch.setattr('matknow.agent.load_policy',forbidden)
    w.question_edit.setPlainText('你是什么模型');w.ask();qtbot.waitUntil(lambda:not w.busy)
    assert w.last_answer.status=='chat'
    assert '普通聊天' in w.status_label.text() and '0 个来源' not in w.status_label.text()
    assert '处理步骤' not in w.answer_view.toPlainText()
    assert w.history_list.count()==1 and not w.source_view.toPlainText()
    w.open_history(w.history_list.item(0))
    assert 'deepseek-flash' in w.answer_view.toPlainText() and not w.last_answer.agent_run
