import json

from PySide6.QtCore import Qt,QUrl
from PySide6.QtGui import QInputMethodEvent,QFont
from PySide6.QtWidgets import QApplication

from matknow.agent import ResearchAgent
from matknow.llm import DeepSeekClient
from tests.test_chat_routing import Model
from tests.test_desktop import make_profile_window
from tests.test_agent import final


def capture(messages):
    return {**json.loads(messages[-1]['content']), 'history':messages[1:-1]}


def test_followup_model_receives_previous_turns_and_ui_keeps_both(qtbot,tmp_path,monkeypatch):
    _,credentials,w=make_profile_window(qtbot,tmp_path);credentials.key='FAKE-conversation'
    calls=[]
    def complete(self,messages,**kwargs):
        data=capture(messages);calls.append(data)
        return json.dumps({'mode':'chat','answer':'你刚才说你叫小林。' if data['question']=='我叫什么' else '记住了，你叫小林。'})
    monkeypatch.setattr(DeepSeekClient,'complete',complete)
    for question in ('我叫小林','我叫什么'):
        w.question_edit.setPlainText(question);w.ask();qtbot.waitUntil(lambda:not w.busy)
    assert calls[1]['history']==[{'role':'user','content':'我叫小林'},{'role':'assistant','content':'记住了，你叫小林。'}]
    assert all(text in w.answer_view.toPlainText() for text in ('我叫小林','记住了','我叫什么','你刚才说'))
    document=w.answer_view.document();cursor=document.find('我叫什么')
    assert cursor.blockFormat().alignment() & Qt.AlignmentFlag.AlignRight
    cursor=document.find('你刚才说你叫小林。')
    assert cursor.charFormat().fontWeight()>=QFont.Weight.DemiBold
    w.new_chat_button.click()
    assert not w.conversation and w.last_answer is None and w.history_list.count()==1
    w.question_edit.setPlainText('重新开始');w.ask();qtbot.waitUntil(lambda:not w.busy)
    assert calls[-1]['history']==[] and '我叫什么' not in w.answer_view.toPlainText()


def test_placeholder_hides_for_text_and_chinese_preedit(qtbot,tmp_path):
    _,_,w=make_profile_window(qtbot,tmp_path);editor=w.question_edit;hint=editor.placeholderText()
    assert hint
    editor.setPlainText('正在输入');assert editor.placeholderText()==''
    editor.clear();assert editor.placeholderText()==hint
    QApplication.sendEvent(editor,QInputMethodEvent('ni',[]))
    assert editor.placeholderText()==''
    QApplication.sendEvent(editor,QInputMethodEvent('',[]))
    assert editor.placeholderText()==hint


def test_scope_keeps_dialogue_while_direction_still_resets_it(qtbot,tmp_path,monkeypatch):
    store,credentials,w=make_profile_window(qtbot,tmp_path);credentials.key='FAKE-conversation';calls=[]
    def complete(self,messages,**kwargs):
        calls.append(capture(messages));return json.dumps({'mode':'chat','answer':'答复'})
    monkeypatch.setattr(DeepSeekClient,'complete',complete)
    w.question_edit.setPlainText('旧范围');w.ask();qtbot.waitUntil(lambda:not w.busy)
    w.knowledge.import_text('新资料','测试资料');w.refresh_documents();w.document_list.item(0).setSelected(True)
    w.question_edit.setPlainText('新范围');w.ask();qtbot.waitUntil(lambda:not w.busy)
    assert calls[-1]['history']==[{'role':'user','content':'旧范围'},{'role':'assistant','content':'答复'}]
    other=store.create('其他研究');assert w.switch_profile(other.id)
    assert not w.conversation and '旧范围' not in w.answer_view.toPlainText()


def test_research_followup_resolves_query_but_keeps_original_history_question(tmp_path):
    from matknow.knowledge import KnowledgeBase
    kb=KnowledgeBase(tmp_path/'kb.sqlite3');kb.import_text('方法','乙醇置换24 h。')
    history=[{'question':'查导入的乙醇置换方法','answer':'置换24 h [1]。','mode':'research'}]
    model=Model({'mode':'research','workspace':'documents','resolved_question':'导入资料中乙醇置换多久'},final)
    answer=ResearchAgent(kb,model).respond('那要多久',conversation=history)
    assert answer.question=='那要多久' and kb.list_history()[0].question=='那要多久'
    assert answer.status=='generated' and answer.sources
    routed=json.loads(model.messages[0][-1]['content']);research=json.loads(model.messages[1][-1]['content'])
    native=[{'role':'user','content':history[0]['question']},{'role':'assistant','content':history[0]['answer']}]
    assert model.messages[0][1:-1]==native and model.messages[1][1:-1]==native
    assert 'conversation' not in routed and 'conversation' not in research
    assert research['question']=='导入资料中乙醇置换多久'


def test_context_has_bounded_complete_turns_and_does_not_mutate_input(tmp_path):
    from matknow.knowledge import KnowledgeBase
    history=[{'question':str(i),'answer':'回复'*1500,'mode':'chat'} for i in range(20)]
    def respond(context):
        return {'summary':'此前讨论了实验条件，继续讨论。'} if context.get('task')=='summarize_conversation' else {'mode':'chat','answer':'答复'}
    model=Model(*[respond]*20);model.context_window=16384
    ResearchAgent(KnowledgeBase(tmp_path/'kb.sqlite3'),model).respond('接着说',conversation=history)
    context=model.messages[-1][1:-1]
    assert context[0]['content'].startswith('<conversation-summary>')
    assert context[-2:]==[{'role':'user','content':history[-1]['question']},{'role':'assistant','content':history[-1]['answer']}]
    from matknow.model_context import fits_context
    assert fits_context(model.messages[-1],model.context_window) and len(history)==20


def test_earlier_turn_source_remains_clickable_after_followup(qtbot,tmp_path,monkeypatch):
    _,_,w=make_profile_window(qtbot,tmp_path)
    w.knowledge.import_text('方法','干燥 60 °C。')
    for question in ('干燥','不存在的词'):
        w.question_edit.setPlainText(question);w.ask();qtbot.waitUntil(lambda:not w.busy)
    first=w.conversation[0];assert first.sources
    w.open_citation(QUrl('source:'+first.sources[0].id))
    assert '60' in w.source_view.toPlainText()


def test_history_groups_sessions_and_reopens_whole_conversation_after_restart(qtbot,tmp_path,monkeypatch):
    _,credentials,w=make_profile_window(qtbot,tmp_path);credentials.key='FAKE-conversation'
    calls=[]
    def complete(self,messages,**kwargs):
        calls.append(capture(messages));return json.dumps({'mode':'chat','answer':'回复 '+calls[-1]['question']})
    monkeypatch.setattr(DeepSeekClient,'complete',complete)
    for question in ('同一对话第一条','同一对话第二条'):
        w.question_edit.setPlainText(question);w.ask();qtbot.waitUntil(lambda:not w.busy)
    assert w.history_list.count()==1
    first_session=w.history_list.item(0).data(Qt.ItemDataRole.UserRole)
    w.new_chat_button.click();w.question_edit.setPlainText('另一对话');w.ask();qtbot.waitUntil(lambda:not w.busy)
    assert w.history_list.count()==2 and calls[-1]['history']==[]
    from matknow.desktop import MainWindow
    reopened=MainWindow(w.knowledge,w.profiles,credentials);qtbot.addWidget(reopened);reopened.show()
    assert reopened.history_list.count()==2
    item=next(reopened.history_list.item(i) for i in range(2) if reopened.history_list.item(i).data(Qt.ItemDataRole.UserRole)==first_session)
    reopened.open_history(item)
    assert len(reopened.conversation)==2 and all(q in reopened.answer_view.toPlainText() for q in ('同一对话第一条','同一对话第二条'))
    reopened.question_edit.setPlainText('继续旧对话');reopened.ask();qtbot.waitUntil(lambda:not reopened.busy)
    assert len(calls[-1]['history'])==4 and len(reopened.conversation)==3 and reopened.history_list.count()==2


def test_reopening_scoped_conversation_restores_selected_sources(qtbot,tmp_path,monkeypatch):
    _,credentials,w=make_profile_window(qtbot,tmp_path);credentials.key='FAKE-conversation'
    doc=w.knowledge.import_text('所选资料','测试正文');w.refresh_documents();w.document_list.item(0).setSelected(True)
    calls=[]
    def complete(self,messages,**kwargs):
        calls.append(capture(messages));return json.dumps({'mode':'chat','answer':'记住这次对话'})
    monkeypatch.setattr(DeepSeekClient,'complete',complete)
    w.question_edit.setPlainText('第一条');w.ask();qtbot.waitUntil(lambda:not w.busy)
    w.new_chat_button.click();w.document_list.clearSelection();w.open_history(w.history_list.item(0))
    assert [i.data(Qt.ItemDataRole.UserRole) for i in w.document_list.selectedItems()]==[doc.id]
    w.question_edit.setPlainText('继续');w.ask();qtbot.waitUntil(lambda:not w.busy)
    assert len(calls[-1]['history'])==2


def test_resolved_question_cannot_remove_original_calculation_requirements(tmp_path):
    from tests.test_unified_chat import records
    kb,doc,snap=records(tmp_path)
    def unsupported(context):
        source=next(s for s in context['sources'] if s['id'].startswith('exp:'))
        return {'answer':'A1 最高 [1]。','source_ids':[source['id']],'status':'generated'}
    model=Model({'mode':'research','workspace':'experiments','resolved_question':'所选样品的干燥温度'},unsupported,unsupported)
    answer=ResearchAgent(kb,model).respond('所选样品干燥温度最高的是哪个',sample_ids=[s.id for s in snap.draft.samples])
    assert answer.status=='model_error'


def test_deleted_history_scope_never_silently_expands_to_whole_library(qtbot,tmp_path,monkeypatch):
    _,credentials,w=make_profile_window(qtbot,tmp_path);credentials.key='FAKE-conversation'
    doc=w.knowledge.import_text('后来删除的资料','测试正文');w.refresh_documents();w.document_list.item(0).setSelected(True)
    calls=[]
    def complete(self,messages,**kwargs):
        calls.append(messages);return json.dumps({'mode':'chat','answer':'答复'})
    monkeypatch.setattr(DeepSeekClient,'complete',complete)
    w.question_edit.setPlainText('记住这次对话');w.ask();qtbot.waitUntil(lambda:not w.busy)
    w.knowledge.delete_document(doc.id);w.knowledge.import_text('其他资料','排除的资料');w.refresh_documents()
    w.open_history(w.history_list.item(0));w.question_edit.setPlainText('刚才的条件是什么');w.ask()
    assert len(calls)==1 and not w.busy and '已删除' in w.status_label.text()
    w.new_chat_button.click();w.question_edit.setPlainText('新的问题');w.ask();qtbot.waitUntil(lambda:not w.busy)
    assert len(calls)==2


def test_legacy_plain_chat_can_be_reopened_and_continued_with_context(qtbot,tmp_path,monkeypatch):
    from matknow.models import Answer
    from matknow.knowledge import utc_now
    _,credentials,w=make_profile_window(qtbot,tmp_path);credentials.key='FAKE-conversation'
    old=Answer('legacy-chat','我叫小林','记住了，你叫小林。','chat',utc_now());w.knowledge.save_answer(old)
    w.refresh_history();w.open_history(w.history_list.item(0));calls=[]
    def complete(self,messages,**kwargs):
        calls.append(capture(messages));return json.dumps({'mode':'chat','answer':'小林'})
    monkeypatch.setattr(DeepSeekClient,'complete',complete)
    w.question_edit.setPlainText('我叫什么');w.ask();qtbot.waitUntil(lambda:not w.busy)
    assert calls[0]['history']==[{'role':'user','content':old.question},{'role':'assistant','content':old.answer}]
    assert w.history_list.count()==1 and len(w.conversation)==2


def test_deleting_cited_document_keeps_unrelated_chat_and_original_user_requests(qtbot,tmp_path):
    from matknow.models import Answer
    from matknow.knowledge import utc_now
    from matknow.conversations import scope_key
    _,_,w=make_profile_window(qtbot,tmp_path)
    document=w.knowledge.import_text('临时方法','干燥60度。')
    sources=w.knowledge.search('干燥')
    first=Answer('first','聊天时称呼我小禾','好的，小禾。','chat',utc_now())
    research=Answer('research',r'D:\synthetic\notes.txt 查资料里的干燥温度','干燥60度 [1]。','generated',utc_now(),sources)
    w.conversation=[first,research];w._turn_scopes={a.id:scope_key() for a in w.conversation}
    w.knowledge.delete_document(document.id)
    assert w._conversation_context(scope_key())==[
        {'question':first.question,'answer':first.answer,'mode':'chat'},
        {'question':research.question,'answer':'','mode':'research'}]
    assert w._conversation_file_paths(scope_key())==[r'D:\synthetic\notes.txt']
