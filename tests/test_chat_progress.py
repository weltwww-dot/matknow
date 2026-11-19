import json
from threading import Event

from PySide6.QtCore import Qt

from matknow.agent import ResearchAgent
from matknow.knowledge import KnowledgeBase
from matknow.llm import DeepSeekClient
from matknow.llm import ModelError
from tests.test_agent import Script, final
from tests.test_desktop import make_profile_window


def test_waiting_chat_shows_question_live_elapsed_and_collapsible_steps(qtbot,tmp_path,monkeypatch):
    _,credentials,w=make_profile_window(qtbot,tmp_path);credentials.key='FAKE-progress-key'
    entered,release=Event(),Event()
    def complete(*args,**kwargs):
        entered.set();assert release.wait(5)
        return json.dumps({'mode':'chat','answer':'你好。'})
    monkeypatch.setattr(DeepSeekClient,'complete',complete)
    w.question_edit.setPlainText('你好 <b>问题</b>');w.ask()
    try:
        qtbot.waitUntil(entered.is_set)
        activity=w.answer_activity
        assert activity.isVisible() and activity.running and activity.timer.isActive()
        assert '你好 <b>问题</b>' in w.answer_view.toPlainText()
        assert '正在等待模型响应' in activity.stage.text()
        assert activity.bar.isVisible() and activity.bar.maximum()==0
        qtbot.waitUntil(lambda:'1 秒' in activity.title.text(),timeout=2500)
        assert w.busy and w.last_answer is None
    finally:
        release.set();qtbot.waitUntil(lambda:not w.busy)
    assert w.last_answer.status=='chat' and '你好。' in w.answer_view.toPlainText()
    assert not activity.running and not activity.timer.isActive() and not activity.bar.isVisible()
    assert not activity.details.isVisible()
    qtbot.mouseClick(activity.toggle,Qt.MouseButton.LeftButton)
    assert activity.details.isVisible() and '正在等待模型响应' in activity.details.toPlainText()


def test_progress_contains_actual_plan_rounds_tools_and_validation(tmp_path):
    kb=KnowledgeBase(tmp_path/'lab.sqlite3');kb.import_text('方法','乙醇浸泡 24 h。')
    model=Script({'plan':['检索置换步骤','核对原文并回答'],
                  'action':{'tool':'search_documents','arguments':{'query':'乙醇浸泡'}}},final)
    progress=[]
    answer=ResearchAgent(kb,model).ask('溶剂置换怎么操作',progress=progress.append)
    assert answer.status=='generated' and len(model.contexts)==2
    assert any('行动计划：检索置换步骤 → 核对原文并回答' in p for p in progress)
    assert any('第 1 轮' in p for p in progress) and any('第 2 轮' in p for p in progress)
    assert any('校验回答与出处' in p for p in progress)
    assert progress[-1]=='正在保存回答…'


def test_failed_worker_stops_animation_and_allows_next_question(qtbot,tmp_path,monkeypatch):
    _,credentials,w=make_profile_window(qtbot,tmp_path);credentials.key='FAKE-progress-key'
    def fail(*args,**kwargs):raise RuntimeError('测试失败')
    monkeypatch.setattr(DeepSeekClient,'complete',fail)
    w.question_edit.setPlainText('第一条');w.ask();qtbot.waitUntil(lambda:not w.busy)
    activity=w.answer_activity
    assert not activity.running and not activity.timer.isActive()
    assert '未完成' in activity.stage.text() and '测试失败' in w.answer_view.toPlainText()
    monkeypatch.setattr(DeepSeekClient,'complete',lambda *a,**kw:json.dumps({'mode':'chat','answer':'第二条回答'}))
    w.question_edit.setPlainText('第二条');w.ask();qtbot.waitUntil(lambda:not w.busy)
    assert '测试失败' not in activity.details.toPlainText()
    assert '第二条回答' in w.answer_view.toPlainText() and not activity.running


def test_offline_question_finishes_and_invalid_input_does_not_show_progress(qtbot,tmp_path):
    _,_,w=make_profile_window(qtbot,tmp_path)
    w.question_edit.setPlainText('  ');w.ask()
    assert not w.answer_activity.isVisible()
    w.knowledge.import_text('方法','干燥温度 60 °C。')
    w.question_edit.setPlainText('干燥温度');w.ask();qtbot.waitUntil(lambda:not w.busy)
    assert w.last_answer.status=='retrieval_preview' and not w.answer_activity.running
    assert not w.answer_activity.timer.isActive()


def test_unrelated_tasks_do_not_create_chat_progress(qtbot,tmp_path):
    _,_,w=make_profile_window(qtbot,tmp_path)
    w.start_task(lambda:None,lambda result:None,'正在导入…')
    qtbot.waitUntil(lambda:not w.busy)
    assert not w.answer_activity.isVisible() and not w.answer_activity.timer.isActive()


def test_handled_model_error_stops_progress_and_keeps_failure_status(qtbot,tmp_path,monkeypatch):
    _,credentials,w=make_profile_window(qtbot,tmp_path);credentials.key='FAKE-progress-key'
    def fail(*args,**kwargs):raise ModelError('请求超时')
    monkeypatch.setattr(DeepSeekClient,'complete',fail)
    w.question_edit.setPlainText('你好');w.ask();qtbot.waitUntil(lambda:not w.busy)
    assert w.last_answer.status=='model_error'
    assert w.answer_activity.stage.text()=='未完成' and not w.answer_activity.timer.isActive()


def test_answer_presentation_failure_does_not_claim_completed(qtbot,tmp_path,monkeypatch):
    _,credentials,w=make_profile_window(qtbot,tmp_path);credentials.key='FAKE-progress-key'
    monkeypatch.setattr(DeepSeekClient,'complete',lambda *a,**kw:json.dumps({'mode':'chat','answer':'答复'}))
    def fail():raise OSError('测试刷新失败')
    monkeypatch.setattr(w,'refresh_history',fail)
    w.question_edit.setPlainText('你好');w.ask();qtbot.waitUntil(lambda:not w.busy)
    assert w.answer_activity.stage.text()=='未完成'
    assert '测试刷新失败' in w.answer_view.toPlainText()
    assert not w.answer_activity.timer.isActive()


def test_other_history_and_profile_do_not_show_previous_activity(qtbot,tmp_path,monkeypatch):
    store,credentials,w=make_profile_window(qtbot,tmp_path);credentials.key='FAKE-progress-key'
    monkeypatch.setattr(DeepSeekClient,'complete',lambda *a,**kw:json.dumps({'mode':'chat','answer':'答复'}))
    for question in ('第一条','第二条'):
        w.question_edit.setPlainText(question);w.ask();qtbot.waitUntil(lambda:not w.busy)
    assert w.answer_activity.isVisible()
    first=next(a for a in w.knowledge.list_history() if a.question=='第一条')
    w.present_answer(first)
    assert not w.answer_activity.isVisible() and not w.answer_activity.timer.isActive()
    w.question_edit.setPlainText('第三条');w.ask();qtbot.waitUntil(lambda:not w.busy)
    assert w.answer_activity.isVisible()
    other=store.create('另一方向')
    assert w.switch_profile(other.id)
    assert not w.answer_activity.isVisible() and not w.answer_activity.timer.isActive()


def test_long_plan_stays_in_scrollable_details_instead_of_expanding_status(qtbot):
    from matknow.ui_controls import TaskActivity
    activity=TaskActivity();qtbot.addWidget(activity)
    activity.start('正在处理…')
    plan='行动计划：'+' → '.join(['核对实验记录中的条件和结果'*8]*6)
    activity.update_stage(plan)
    assert activity.stage.text()=='已生成行动计划'
    assert plan in activity.details.toPlainText() and activity.details.maximumHeight()<=100
    activity.clear();assert not activity.timer.isActive()
