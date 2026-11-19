import json
import sys

import pytest

from matknow.agent import ResearchAgent
from matknow.agent_policy import load_policy, policy_path
from matknow.knowledge import KnowledgeBase
from tests.test_agent import Script, final


def write_policy(path,text='回答用简洁中文。',**parameters):
    config={'version':1,'max_rounds':6,'max_context_chars':24000,'max_validation_errors':2}|parameters
    path.write_text('# 材知智能体\n\n## 运行参数\n\n```json\n'+json.dumps(config)+'\n```\n\n## 系统指令\n\n'+text,encoding='utf8')


def test_agent_reads_entire_markdown_and_reloads_on_next_question(tmp_path):
    path=tmp_path/'agent.md';write_policy(path,'POLICY_FIRST：简洁回答。')
    kb=KnowledgeBase(tmp_path/'kb.sqlite3');kb.import_text('资料','干燥60 °C。')
    class Model(Script):
        def complete(self,messages,json_output=False):
            self.prompt=messages[0]['content']
            return super().complete(messages,json_output)
    model=Model(final);agent=ResearchAgent(kb,model,policy_path=path)
    first=agent.ask('干燥');assert 'POLICY_FIRST' in model.prompt
    write_policy(path,'POLICY_SECOND：逐项核对出处。')
    second=agent.ask('干燥');assert 'POLICY_SECOND' in model.prompt and 'POLICY_FIRST' not in model.prompt
    assert first.agent_run['policy_sha256']!=second.agent_run['policy_sha256']


def test_policy_controls_round_budget(tmp_path):
    path=tmp_path/'agent.md';write_policy(path,max_rounds=1)
    kb=KnowledgeBase(tmp_path/'kb.sqlite3')
    model=Script({'action':{'tool':'list_samples','arguments':{}}})
    answer=ResearchAgent(kb,model,policy_path=path).ask('查找样品')
    assert len(model.contexts)==1 and answer.agent_run['outcome']=='stopped'


@pytest.mark.parametrize('config',[{'max_rounds':0},{'max_rounds':999},{'max_rounds':True},{'max_context_chars':-1},{'max_validation_errors':0},{'version':9},{'execute_code':True}])
def test_unsafe_or_unknown_policy_parameters_are_rejected(tmp_path,config):
    path=tmp_path/'agent.md';write_policy(path,**config)
    with pytest.raises(ValueError):load_policy(path)


@pytest.mark.parametrize('content',[None,'','not a policy','# 规则\n```json\n{\n```','字'*70000],ids=['missing','empty','plain','broken-json','oversized'])
def test_missing_or_invalid_policy_does_not_call_model_or_fallback(tmp_path,content):
    path=tmp_path/'agent.md'
    if content is not None:path.write_text(content,encoding='utf8')
    model=Script(final);kb=KnowledgeBase(tmp_path/'kb.sqlite3');kb.import_text('资料','干燥60 °C。')
    answer=ResearchAgent(kb,model,policy_path=path).ask('干燥')
    assert answer.status=='model_error' and 'agent.md' in answer.answer
    assert not model.contexts and len(kb.list_history())==1
    assert str(tmp_path) not in answer.answer


@pytest.mark.parametrize('text',['','```text\n```'])
def test_empty_system_instructions_stop_before_model_request(tmp_path,text):
    path=tmp_path/'agent.md';write_policy(path,text)
    model=Script(final);kb=KnowledgeBase(tmp_path/'kb.sqlite3')
    answer=ResearchAgent(kb,model,policy_path=path).ask('干燥')
    assert answer.status=='model_error' and not model.contexts


def test_installed_policy_is_next_to_exe_not_current_working_directory(tmp_path,monkeypatch):
    install=tmp_path/'installed';install.mkdir()
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'executable',str(install/'MatKnow.exe'))
    monkeypatch.chdir(tmp_path)
    assert policy_path()==install/'agent.md'
