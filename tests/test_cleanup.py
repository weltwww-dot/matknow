from contextlib import contextmanager

from PySide6.QtWidgets import QFileDialog

from matknow.agent import ResearchAgent
from matknow.desktop import MainWindow
from matknow.experiments.service import ExperimentAskService
from matknow.experiments.store import ExperimentStore
from matknow.service import AskService
from tests.test_agent import Script, final
from tests.test_unified_chat import records


def trace_database(monkeypatch, knowledge):
    statements=[];original=knowledge.connection
    @contextmanager
    def connection():
        with original() as db:
            db.set_trace_callback(statements.append)
            yield db
    monkeypatch.setattr(knowledge,'connection',connection)
    return statements


def test_existing_evidence_reads_and_answer_render_do_not_reinitialize_schema(qtbot,tmp_path,monkeypatch):
    kb,_,_=records(tmp_path);w=MainWindow(kb);qtbot.addWidget(w)
    statements=trace_database(monkeypatch,kb)
    answer=ResearchAgent(kb,Script(final)).ask('干燥',workspace='unified')
    w.present_answer(answer)
    assert answer.status=='generated' and any(s.id.startswith('exp:') for s in answer.sources)
    assert not [sql for sql in statements if sql.startswith(('CREATE ','BEGIN IMMEDIATE'))]
    assert w.history_list.count()==1


def test_model_reload_keeps_sample_selection_without_reloading_records(qtbot,tmp_path,monkeypatch):
    kb,_,_=records(tmp_path);w=MainWindow(kb);qtbot.addWidget(w)
    w.experiment_page.record_list.setCurrentRow(0)
    selected=w.experiment_page.selected_sample_ids()
    statements=trace_database(monkeypatch,kb)
    w._reload_service()
    assert w.experiment_page.selected_sample_ids()==selected
    assert not [sql for sql in statements if sql.startswith('SELECT') and 'experiment_revisions' in sql]


def test_legacy_experiment_index_is_preserved_but_no_longer_written(tmp_path):
    kb,_,_=records(tmp_path);old=AskService(kb).ask('干燥')
    with kb.connection() as db:
        db.execute('CREATE TABLE IF NOT EXISTS experiment_questions (id TEXT PRIMARY KEY REFERENCES history(id) ON DELETE CASCADE)')
        db.execute('INSERT INTO experiment_questions VALUES (?)',(old.id,))
        db.execute("CREATE TRIGGER reject_legacy_write BEFORE INSERT ON experiment_questions BEGIN SELECT RAISE(ABORT, 'unused legacy index'); END")
    answer=ExperimentAskService(ExperimentStore(kb)).ask('干燥')
    assert len(kb.list_history())==2 and kb.list_history()[0].id==answer.id
    with kb.connection() as db:
        assert db.execute('SELECT id FROM experiment_questions').fetchall()[0][0]==old.id


def test_desktop_export_escapes_markdown_and_preserves_fixed_citation_numbers(qtbot,tmp_path,monkeypatch):
    kb,_,_=records(tmp_path);kb.import_text('补充','干燥 *原文* [链接](https://example.com)')
    w=MainWindow(kb);qtbot.addWidget(w)
    w.present_answer(ResearchAgent(kb).ask('干燥',workspace='unified'))
    target=tmp_path/'answer.md'
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a:(str(target),''))
    w.export_answer()
    text=target.read_text(encoding='utf8')
    assert r'\*原文\*' in text and r'\[链接\]' in text
    for source in w.last_answer.sources:assert f'### [{source.citation_number}]' in text
    assert str(tmp_path) not in text
