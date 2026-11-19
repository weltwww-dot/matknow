import pytest
import json

from matknow.knowledge import KnowledgeBase
from matknow.service import AskService
from matknow.agent import ResearchAgent


def test_citation_feedback_tells_model_how_to_repair_unused_ids(tmp_path):
    from matknow.service import validate_generated_answer
    from matknow.llm import ModelError
    kb=KnowledgeBase(tmp_path/'kb.sqlite3');doc=kb.import_text('方法','干燥60 °C。')
    source=kb.document_sources(doc.id)[0]
    with pytest.raises(ModelError,match='source_ids.*正文'):
        validate_generated_answer(json.dumps({'answer':'已完成。','source_ids':[source.id],'status':'generated'}),[source])
    with pytest.raises(ModelError,match='至少.*引用'):
        validate_generated_answer(json.dumps({'answer':'已完成。','source_ids':[],'status':'generated'}),[source])


def test_preview_is_grounded_and_history_resolves_live_sources(tmp_path):
    kb = KnowledgeBase(tmp_path / "kb.sqlite3")
    doc = kb.import_text("教学样例.md", "干燥温度为 60 °C，时间为 6 h。")
    answer = AskService(kb).ask("干燥温度是多少？")
    assert answer.status == "retrieval_preview"
    assert "检索预览" in answer.answer
    assert answer.sources[0].document_id == doc.id
    assert "60 °C" in answer.sources[0].text
    history = kb.list_history()
    assert history[0].question == "干燥温度是多少？"
    assert history[0].sources[0].id == answer.sources[0].id
    kb.delete_document(doc.id)
    removed = kb.list_history()[0]
    assert removed.sources == []
    assert removed.unavailable_sources == 1


def test_no_evidence_and_empty_selection(tmp_path):
    kb = KnowledgeBase(tmp_path / "kb.sqlite3")
    kb.import_text("A.txt", "干燥温度为 60 °C。")
    service = AskService(kb)
    assert service.ask("干燥", []).status == "insufficient_evidence"
    assert service.ask("XRD987").sources == []


@pytest.mark.parametrize("question", ["", " \n", "问" * 2001])
def test_invalid_question_rejected(tmp_path, question):
    with pytest.raises(ValueError):
        AskService(KnowledgeBase(tmp_path / "kb.sqlite3")).ask(question)


class ScriptedModel:
    def __init__(self, callback):
        self.callback = callback
        self.messages = None

    def complete(self, messages, json_output=False):
        assert json_output
        self.messages = messages
        context = json.loads(messages[-1]['content'])
        return self.callback(context)


def test_generated_answer_uses_real_ids_and_retains_numbering_order(tmp_path):
    kb = KnowledgeBase(tmp_path / 'generated.sqlite3')
    kb.import_text('A.txt', '干燥温度 60 °C。')
    kb.import_text('B.txt', '干燥温度 80 °C。')
    def output(context):
        first, second = context['sources']
        return json.dumps({'answer': '条件分别为 60 °C [1] 和 80 °C [2]。', 'source_ids': [second['id'], first['id']], 'status': 'generated'})
    model = ScriptedModel(output)
    answer = ResearchAgent(kb, model, '材料方法对比').ask('干燥温度')
    assert answer.status == 'generated'
    assert [p.filename for p in answer.sources] == ['A.txt', 'B.txt']
    assert kb.list_history()[0].answer == answer.answer
    context = json.loads(model.messages[-1]['content'])
    assert context['description'] == '材料方法对比'
    assert context['sources'][0]['number'] == 1


@pytest.mark.parametrize('payload', [
    {'answer': '编造的答案 [1]', 'source_ids': ['foreign-id'], 'status': 'generated'},
    {'answer': '没有引文', 'source_ids': [], 'status': 'generated'},
    {'answer': '未知编号 [9]', 'source_ids': ['USE_REAL_ID'], 'status': 'generated'},
    {'answer': '', 'source_ids': [], 'status': 'insufficient_evidence'},
    {'answer': 'x', 'source_ids': 'wrong-type', 'status': 'generated'},
    {'answer': 'x', 'source_ids': [], 'status': 'invented'},
])
def test_invalid_model_output_falls_back_to_explicit_error_preview(tmp_path, payload):
    kb = KnowledgeBase(tmp_path / 'invalid.sqlite3')
    kb.import_text('A.txt', '干燥温度 60 °C。')
    def output(context):
        payload['source_ids'] = [context['sources'][0]['id']] if payload['source_ids'] == ['USE_REAL_ID'] else payload['source_ids']
        return json.dumps(payload)
    answer = ResearchAgent(kb, ScriptedModel(output)).ask('干燥温度')
    assert answer.status == 'model_error'
    assert answer.agent_run['outcome'] == 'failed'
    assert len(kb.list_history()) == 1
    assert answer.sources[0].filename == 'A.txt'


def test_local_miss_and_model_evidence_insufficiency_keep_explicit_status(tmp_path):
    kb = KnowledgeBase(tmp_path / 'insufficient.sqlite3')
    kb.import_text('A.txt', '干燥温度 60 °C。')
    model = ScriptedModel(lambda context: json.dumps({'answer': '资料没有给出该参数。', 'source_ids': [], 'status': 'insufficient_evidence'}))
    assert AskService(kb).ask('unfindable987').status == 'insufficient_evidence'
    assert model.messages is None
    service = ResearchAgent(kb, model)
    answer = service.ask('干燥升温速率')
    assert answer.status == 'insufficient_evidence'
    assert answer.sources


def test_model_context_does_not_include_other_profile_history_or_documents(tmp_path):
    from matknow.profiles import ProfileStore
    store = ProfileStore(tmp_path)
    private = store.create('另一方向')
    foreign = store.knowledge(private.id)
    foreign.import_text('private.txt', '干燥 PRIVATE_OTHER_DIRECTION。')
    AskService(foreign).ask('干燥 PRIVATE_HISTORY')
    current = store.knowledge(store.current_id)
    current.import_text('current.txt', '干燥条件 60 °C。')
    model = ScriptedModel(lambda c: json.dumps({'answer': '60 °C [1]', 'source_ids': [c['sources'][0]['id']], 'status': 'generated'}))
    ResearchAgent(current, model).ask('干燥')
    assert 'PRIVATE_' not in json.dumps(model.messages)
