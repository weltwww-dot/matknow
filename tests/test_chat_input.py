import json
from threading import Event

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtWidgets import QApplication

from matknow.llm import DeepSeekClient
from tests.test_desktop import make_profile_window


@pytest.mark.parametrize('trigger', ['return', 'keypad_enter', 'ctrl_return', 'button'])
def test_send_captures_question_once_and_clears_input_immediately(qtbot, tmp_path, monkeypatch, trigger):
    _, credentials, window = make_profile_window(qtbot, tmp_path)
    credentials.key = 'FAKE-chat-input-key'
    entered, release = Event(), Event()
    questions = []

    def complete(self, messages, **kwargs):
        questions.append(json.loads(messages[-1]['content'])['question'])
        entered.set()
        assert release.wait(5)
        return json.dumps({'mode': 'chat', 'answer': '你好。'})

    monkeypatch.setattr(DeepSeekClient, 'complete', complete)
    window.question_edit.setPlainText('你好，保留这条原问题')
    window.question_edit.setFocus()
    try:
        if trigger == 'button':
            qtbot.mouseClick(window.ask_button, Qt.MouseButton.LeftButton)
        else:
            key = Qt.Key.Key_Enter if trigger == 'keypad_enter' else Qt.Key.Key_Return
            modifiers = Qt.KeyboardModifier.ControlModifier if trigger == 'ctrl_return' else Qt.KeyboardModifier.NoModifier
            qtbot.keyClick(window.question_edit, key, modifiers)
        qtbot.waitUntil(entered.is_set)
        assert window.busy and not window.question_edit.toPlainText()
        window.question_edit.setPlainText('下一条草稿')
        window.ask()  # A repeated submission while the request is running cannot send again.
        assert window.question_edit.toPlainText() == '下一条草稿'
        assert questions == ['你好，保留这条原问题']
    finally:
        release.set()
        qtbot.waitUntil(lambda: not window.busy)
    assert window.last_answer.question == '你好，保留这条原问题'
    assert window.last_answer.status == 'chat' and window.history_list.count() == 1
    assert window.question_edit.toPlainText() == '下一条草稿'


def test_newline_invalid_input_and_ime_composition_do_not_send(qtbot, tmp_path):
    _, _, window = make_profile_window(qtbot, tmp_path)
    editor = window.question_edit
    editor.setFocus()
    qtbot.keyClicks(editor, 'line1')
    qtbot.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    qtbot.keyClicks(editor, 'line2')
    assert editor.toPlainText() == 'line1\nline2' and not window.busy
    for invalid in ('   ', 'x' * 2001):
        editor.setPlainText(invalid)
        qtbot.keyClick(editor, Qt.Key.Key_Return)
        assert editor.toPlainText() == invalid and not window.busy
    editor.setPlainText('未完成的中文输入')
    QApplication.sendEvent(editor, QInputMethodEvent('ni', []))
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    assert not window.busy and not window.knowledge.list_history()
    commit = QInputMethodEvent()
    commit.setCommitString('你')
    QApplication.sendEvent(editor, commit)
    question = editor.toPlainText().strip()
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: not window.busy)
    assert not editor.toPlainText() and window.last_answer.question == question
