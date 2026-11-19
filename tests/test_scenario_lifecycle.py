"""Continuous user journeys over real temporary archives; no paid model calls."""
from copy import deepcopy
import hashlib
import json
from zipfile import ZipFile

from PySide6.QtCore import Qt

from matknow.agent import ResearchAgent
from matknow.experiments.compare import compare_samples
from matknow.experiments.demo import populate_demo
from matknow.experiments.drafts import correct_value
from matknow.experiments.export import export_answer, export_comparison, export_experiment
from matknow.experiments.files import ExperimentArchive
from matknow.experiments.preview import CurveSpec, prepare_attachment
from matknow.experiments.store import ExperimentStore
from matknow.llm import DeepSeekClient
from matknow.profiles import ProfileStore
from tests.test_agent import final
from tests.test_chat_routing import Model
from tests.test_desktop import make_profile_window


def test_import_mixed_results_edit_restart_export_switch_and_delete(qapp, tmp_path):
    profiles = ProfileStore(tmp_path/'data')
    original = profiles.current_id
    kb = profiles.knowledge(original)
    inputs = tmp_path/'inputs'; inputs.mkdir()
    archive = ExperimentArchive(ExperimentStore(kb))
    snapshot = populate_demo(archive, inputs)  # Real Word/Excel parsers and managed originals.
    documents = []
    for name in ('方法.txt', '补充.md'):
        path = inputs/name; path.write_text('虚构强度实验：制备温度160 °C，反应8 h。', encoding='utf8')
        documents.append(kb.import_file(path))
        assert kb.import_file(path).id == documents[-1].id
    documents.append(kb.import_file(inputs/'虚构测试报告.pdf'))
    assert len(kb.list_documents()) == 3
    first, second, third = snapshot.draft.samples
    for sample in snapshot.draft.samples:
        for result in sample.results:
            if result.kind not in ('curve', 'image', 'pdf'):
                continue
            data = result.data
            spec = CurveSpec(data['x_column'], data['y_column']) if result.kind == 'curve' else None
            payload = prepare_attachment(archive, result.attachment_ids[0], spec, bounds=data.get('bounds'))
            assert payload[0] == {'curve': 'table', 'image': 'image', 'pdf': 'text'}[result.kind]
    comparison = compare_samples(archive.store, [first.id, second.id, third.id])
    strength = [g for g in comparison.groups if g['metric'] == '强度']
    assert sorted(len(g['measurements']) for g in strength) == [1, 3]
    assert any(g['status'] == 'needs_confirmation' for g in strength)
    answer = ResearchAgent(kb).respond('制备温度和强度', [d.id for d in documents], sample_ids=[first.id, second.id])
    assert answer.status == 'retrieval_preview'
    assert any(s.id.startswith('exp:') for s in answer.sources)
    assert any(s.document_id in {d.id for d in documents} for s in answer.sources)
    original_source = next(r['id'] for r in archive.store.evidence_rows([first.id]) if r['result_id'] == first.results[0].id)
    draft = deepcopy(snapshot.draft)
    draft.samples[0].results[0].value = correct_value(draft.samples[0].results[0].value, '65')
    updated = archive.save(draft, [], experiment_id=snapshot.id, expected_revision=1)
    assert updated.revision == 2
    assert '60 MPa' in kb.get_evidence(original_source).text
    assert archive.store.get(snapshot.id).draft.samples[0].results[0].value.text == '65'
    other = profiles.create('虚构生物方向')
    profiles.select(other.id)
    assert not profiles.knowledge(other.id).list_documents()
    assert not profiles.knowledge(other.id).list_history()
    assert not ExperimentStore(profiles.knowledge(other.id)).list_experiments()
    profiles = ProfileStore(tmp_path/'data')
    assert profiles.current_id == other.id
    profiles.select(original)
    kb = profiles.knowledge(original)
    archive = ExperimentArchive(ExperimentStore(kb))
    restored = kb.list_history()[0]
    assert restored.id == answer.id and restored.unavailable_sources == 0
    assert archive.store.get(snapshot.id, 1).draft.samples[0].results[0].value.text == '60'
    target = tmp_path/'experiment.zip'
    export_experiment(archive, snapshot.id, target, profile_name='材料制备', revision=1)
    with ZipFile(target) as output:
        manifest = json.loads(output.read('manifest.json'))
        assert manifest['revision'] == 1 and len(manifest['attachments']) == 5
        for item in manifest['attachments']:
            assert hashlib.sha256(output.read(item['member'])).hexdigest() == item['sha256']
        assert str(tmp_path) not in output.read('manifest.json').decode('utf8')
    export_comparison(compare_samples(archive.store, [first.id, second.id]), tmp_path/'comparison.md', profile_name='材料制备')
    assert '65' in (tmp_path/'comparison.md').read_text(encoding='utf8')
    for document in documents:
        kb.delete_document(document.id)
    archive.delete(snapshot.id)
    restored = kb.list_history()[0]
    assert restored.id == answer.id and restored.answer == answer.answer
    assert restored.unavailable_sources == len(answer.sources) and not restored.sources
    export_answer(restored, kb, tmp_path/'answer.md', profile_name='材料制备')
    assert '历史来源已删除' in (tmp_path/'answer.md').read_text(encoding='utf8')
    assert len(archive.store.list_experiments()) == 1  # Unrelated historical batch survives.
    assert all(path.is_file() for path in inputs.iterdir())  # User originals survive deletion.


def test_single_chat_switches_modes_restores_history_and_clears_profile(qtbot, tmp_path, monkeypatch):
    profiles, credentials, window = make_profile_window(qtbot, tmp_path)
    original = profiles.current_id
    credentials.key = 'FAKE-scenario-key'
    selected = window.knowledge.import_text('选中资料', '干燥温度60 °C。')
    window.knowledge.import_text('排除资料', '干燥 PRIVATE_EXCLUDED。')
    window.refresh_documents()
    for i in range(window.document_list.count()):
        item = window.document_list.item(i)
        item.setSelected(item.data(Qt.ItemDataRole.UserRole) == selected.id)
    model = Model({'mode': 'chat', 'answer': '你好。'}, {'mode': 'research'}, final,
                  {'mode': 'chat', 'answer': '周末愉快。'})
    monkeypatch.setattr(DeepSeekClient, 'complete', lambda self, messages, **kw: model.complete(messages, **kw))
    statuses = []
    for question in ('你好', '所选资料的干燥温度是多少', '写一句周末问候'):
        window.question_edit.setPlainText(question)
        window.ask()
        qtbot.waitUntil(lambda: not window.busy)
        statuses.append(window.last_answer.status)
    assert statuses == ['chat', 'generated', 'chat']
    assert len(model.messages) == 4 and window.history_list.count() == 1
    assert 'PRIVATE_EXCLUDED' not in json.dumps(model.messages, ensure_ascii=False)
    assert not window.source_view.toPlainText() and window.last_answer.agent_run is None
    window.open_history(window.history_list.item(0))
    assert window.last_answer.status == 'chat' and len(window.conversation)==3
    assert window.conversation[1].status=='generated' and window.conversation[1].sources
    other = profiles.create('另一实验方向')
    assert window.switch_profile(other.id)
    assert window.history_list.count() == 0 and window.last_answer is None
    assert not window.question_edit.toPlainText() and not window.source_view.toPlainText()
    assert window.switch_profile(original)
    assert window.history_list.count() == 1 and window.model_combo.currentData() == profiles.get(original).model
