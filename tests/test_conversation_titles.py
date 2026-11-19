import json
import sqlite3

import pytest
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import QInputDialog,QApplication

from matknow.agent import ResearchAgent
from matknow.conversations import Conversations,scope_key
from matknow.knowledge import KnowledgeBase,utc_now
from matknow.llm import DeepSeekClient
from matknow.models import Answer
from tests.test_agent import final
from tests.test_chat_routing import Model
from tests.test_desktop import make_profile_window


def test_title_is_generated_with_same_request_and_displayed_without_turn_count(qtbot,tmp_path,monkeypatch):
    _,credentials,w=make_profile_window(qtbot,tmp_path);credentials.key='FAKE-title-key';calls=[]
    def complete(self,messages,**kwargs):
        calls.append(json.loads(messages[-1]['content']))
        return json.dumps({'mode':'chat','answer':'你好，小林。','conversation_title':'与小林的日常交流'})
    monkeypatch.setattr(DeepSeekClient,'complete',complete)
    for question in ('我叫小林','继续聊'):
        w.question_edit.setPlainText(question);w.ask();qtbot.waitUntil(lambda:not w.busy)
    assert len(calls)==2 and calls[0]['suggest_title'] is True and calls[1]['suggest_title'] is False
    assert w.history_list.count()==1 and w.history_list.item(0).text()=='与小林的日常交流'
    assert w.last_answer.status=='chat' and len(w.conversation)==2


def test_manual_title_persists_and_never_gets_overwritten_by_model(qtbot,tmp_path,monkeypatch):
    _,credentials,w=make_profile_window(qtbot,tmp_path);credentials.key='FAKE-title-key'
    monkeypatch.setattr(DeepSeekClient,'complete',lambda *a,**kw:json.dumps({'mode':'chat','answer':'答复','conversation_title':'模型标题'}))
    w.question_edit.setPlainText('你好');w.ask();qtbot.waitUntil(lambda:not w.busy)
    identifier=w.conversation_id
    monkeypatch.setattr(QInputDialog,'getText',lambda *a,**kw:('我的实验讨论',True))
    w.rename_conversation(identifier)
    assert w.history_list.item(0).text()=='我的实验讨论'
    w.question_edit.setPlainText('另一条消息');w.ask();qtbot.waitUntil(lambda:not w.busy)
    repo=Conversations(KnowledgeBase(w.knowledge.path))
    assert repo.title_state(identifier)=={'title':'我的实验讨论','source':'user'}
    assert repo.set_title(identifier,'迟到的自动标题') is False
    assert w.history_list.item(0).text()=='我的实验讨论'


def test_right_click_renames_clicked_conversation_not_other_selected_item(qtbot,tmp_path,monkeypatch):
    _,_,w=make_profile_window(qtbot,tmp_path)
    for identifier in ('one','two'):
        answer=Answer(identifier,'问题 '+identifier,'答复','chat',utc_now());w.knowledge.save_answer(answer)
    w.refresh_history();w.tabs.setCurrentIndex(1)
    clicked=w.history_list.item(1);w.history_list.setCurrentRow(0);seen=[]
    monkeypatch.setattr(QInputDialog,'getText',lambda *a,**kw:('右键改名',True))
    def choose():
        menu=QApplication.activePopupWidget()
        seen.append([action.text() for action in menu.actions()]);menu.actions()[0].trigger();menu.close()
    QTimer.singleShot(0,choose)
    w.history_list.customContextMenuRequested.emit(w.history_list.visualItemRect(clicked).center())
    assert seen==[['重命名']]
    assert w.conversations.title_state('one')['title']=='右键改名'
    assert w.conversations.title_state('two')['title']=='问题 two'


@pytest.mark.parametrize('title',['','  ','多行\n标题','x'*51,123,None])
def test_bad_model_title_never_discards_valid_answer(tmp_path,title):
    kb=KnowledgeBase(tmp_path/'kb.sqlite3');titles=[]
    model=Model({'mode':'chat','answer':'有效回答','conversation_title':title})
    answer=ResearchAgent(kb,model).respond('你好',title_callback=titles.append)
    assert answer.status=='chat' and answer.answer=='有效回答' and not titles
    assert len(model.messages)==1 and len(kb.list_history())==1


def test_research_route_can_propose_title_without_affecting_evidence_validation(tmp_path):
    kb=KnowledgeBase(tmp_path/'kb.sqlite3');kb.import_text('方法','乙醇置换24 h。');titles=[]
    model=Model({'mode':'research','workspace':'documents','conversation_title':'乙醇置换步骤'},final)
    answer=ResearchAgent(kb,model).respond('查导入的乙醇置换方法',title_callback=titles.append)
    assert answer.status=='generated' and answer.sources and titles==['乙醇置换步骤']
    assert len(model.messages)==2


def test_rename_cancel_and_invalid_input_leave_previous_title_unchanged(qtbot,tmp_path,monkeypatch):
    _,_,w=make_profile_window(qtbot,tmp_path)
    answer=Answer('one','旧标题','答复','chat',utc_now());w.knowledge.save_answer(answer);w.refresh_history()
    for text,accepted in (('新标题',False),('  ',True),('x'*51,True)):
        monkeypatch.setattr(QInputDialog,'getText',lambda *a,**kw:(text,accepted))
        w.rename_conversation('one')
        assert w.conversations.title_state('one')['title']=='旧标题'


def test_cannot_rename_other_direction_or_move_a_manually_named_legacy_answer(tmp_path):
    kb=KnowledgeBase(tmp_path/'a.sqlite3');repo=Conversations(kb)
    first=Answer('first','第一条','答复','chat',utc_now());second=Answer('second','第二条','答复','chat',utc_now())
    for answer in (first,second):kb.save_answer(answer)
    repo.sync_legacy();repo.set_title('second','手动保留',manual=True)
    with pytest.raises(ValueError):Conversations(KnowledgeBase(tmp_path/'b.sqlite3')).set_title('second','不能跨方向',manual=True)
    with pytest.raises(ValueError):repo.append(second,'first',scope_key())
    assert repo.title_state('second')['title']=='手动保留'


def test_title_save_failure_does_not_hide_answer_and_manual_failure_preserves_title(qtbot,tmp_path,monkeypatch):
    _,credentials,w=make_profile_window(qtbot,tmp_path);credentials.key='FAKE-title-key'
    monkeypatch.setattr(DeepSeekClient,'complete',lambda *a,**kw:json.dumps({'mode':'chat','answer':'正常回答','conversation_title':'模型标题'}))
    def fail(*a,**kw):raise sqlite3.OperationalError('database locked')
    monkeypatch.setattr(w.conversations,'set_title',fail)
    w.question_edit.setPlainText('首条问题');w.ask();qtbot.waitUntil(lambda:not w.busy)
    assert w.last_answer.status=='chat' and '正常回答' in w.answer_view.toPlainText()
    assert w.history_list.item(0).text()=='首条问题'
    monkeypatch.setattr(QInputDialog,'getText',lambda *a,**kw:('用户标题',True))
    w.rename_conversation(w.conversation_id)
    assert '未保存' in w.status_label.text() and w.history_list.item(0).text()=='首条问题'
