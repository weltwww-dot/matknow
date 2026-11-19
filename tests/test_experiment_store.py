from copy import deepcopy
from uuid import uuid4

import pytest

from matknow.knowledge import KnowledgeBase
from matknow.experiments.models import ExperimentDraft, SampleDraft, ResultDraft, Value
from matknow.experiments.store import ExperimentStore


def record(title='实验一', value='60'):
    return ExperimentDraft(title, '2026-10-13', {'温度': Value('100', '°C')},
                           [SampleDraft('A1', {'温度': Value(value, '°C')},
                                        [ResultDraft('强度', Value('0.1', 'MPa'), method='拉伸', test_conditions={'温度': '25 °C'})])])


@pytest.fixture
def store(tmp_path):
    return ExperimentStore(KnowledgeBase(tmp_path / 'knowledge.sqlite3'))


def test_same_sample_code_is_scoped_to_experiment(store):
    first, second = store.save(record()), store.save(record('实验二', '80'))
    assert first.id != second.id
    assert first.draft.samples[0].id != second.draft.samples[0].id
    assert store.samples([first.draft.samples[0].id])[0][0].id == first.id
    bad = deepcopy(first.draft)
    bad.samples[0].id = second.draft.samples[0].id
    with pytest.raises(ValueError):
        store.save(bad, experiment_id=first.id, expected_revision=1)


def test_append_and_remove_create_immutable_revisions(store):
    first = store.save(record())
    original_evidence = store.search('A1 强度')[0]
    draft = deepcopy(first.draft)
    draft.samples[0].results.append(ResultDraft('强度', Value('0.2', 'MPa')))
    second = store.save(draft, experiment_id=first.id, expected_revision=1)
    assert len(second.draft.samples[0].results) == 2
    draft = deepcopy(second.draft)
    draft.samples[0].results.pop(0)
    third = store.save(draft, experiment_id=first.id, expected_revision=2)
    assert third.revision == 3
    assert len(store.get(first.id, 1).draft.samples[0].results) == 1
    old = store.resolve_many([original_evidence.id])[original_evidence.id]
    assert old.text == original_evidence.text
    assert '已更新' in old.locator
    assert {e.id for e in store.search('A1 强度')}.isdisjoint({original_evidence.id})


def test_stale_revision_cannot_overwrite_new_save(store):
    first = store.save(record())
    store.save(record(value='80'), experiment_id=first.id, expected_revision=1)
    with pytest.raises(ValueError, match='更新'):
        store.save(first.draft, experiment_id=first.id, expected_revision=1)
    assert store.get(first.id).draft.samples[0].conditions['温度'].text == '80'


def test_migration_preserves_legacy_documents_history_and_ids(tmp_path):
    import json
    import sqlite3
    path = tmp_path / 'knowledge.sqlite3'
    document_id, source_id, answer_id = [str(uuid4()) for _ in range(3)]
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE documents (id TEXT PRIMARY KEY,filename TEXT NOT NULL,content_hash TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL);
            CREATE TABLE chunks (id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,position INTEGER NOT NULL,locator TEXT NOT NULL,text TEXT NOT NULL);
            CREATE TABLE history (id TEXT PRIMARY KEY,question TEXT NOT NULL,answer TEXT NOT NULL,status TEXT NOT NULL,source_ids TEXT NOT NULL,created_at TEXT NOT NULL);
        ''')
        db.execute('INSERT INTO documents VALUES (?,?,?,?)',(document_id,'旧记录.txt','legacy-hash','2026-10-13'))
        db.execute('INSERT INTO chunks VALUES (?,?,?,?,?)',(source_id,document_id,0,'段落 1','干燥条件 60 °C。'))
        db.execute('INSERT INTO history VALUES (?,?,?,?,?,?)',(answer_id,'干燥','旧回答 [1]','retrieval_preview',json.dumps([source_id]),'2026-10-13'))
    kb = KnowledgeBase(path)
    store = ExperimentStore(kb)
    store.save(record())
    ExperimentStore(kb)
    assert kb.list_documents()[0].id == document_id
    assert kb.list_history()[0].id == answer_id
    assert kb.get_evidence(source_id).text == '干燥条件 60 °C。'


@pytest.mark.parametrize('mutate', [
    lambda d: setattr(d, 'date', '2026-02-31'),
    lambda d: setattr(d, 'samples', []),
    lambda d: setattr(d.samples[0], 'code', ''),
    lambda d: d.samples.append(deepcopy(d.samples[0])),
    lambda d: setattr(d.samples[0].results[0].value, 'text', 'NaN'),
    lambda d: setattr(d.samples[0].results[0].value, 'confirmed', False),
])
def test_invalid_date_empty_samples_nonfinite_values_and_foreign_result_ids_rejected(store, mutate):
    draft = record()
    mutate(draft)
    with pytest.raises(ValueError):
        store.save(draft)
    assert store.list_experiments() == []


def test_foreign_result_id_and_unknown_sample_scope_rejected(store):
    first, second = store.save(record()), store.save(record('实验二'))
    draft = deepcopy(first.draft)
    draft.samples[0].results[0].id = second.draft.samples[0].results[0].id
    with pytest.raises(ValueError):
        store.save(draft, experiment_id=first.id, expected_revision=1)
    with pytest.raises(ValueError):
        store.samples([str(uuid4())])


def test_transaction_failure_rolls_back_new_revision_and_schema(store):
    first = store.save(record())
    with store.knowledge.connection() as db:
        db.execute("CREATE TRIGGER fail_revision BEFORE INSERT ON experiment_revisions BEGIN SELECT RAISE(ABORT, 'test fail'); END")
    import sqlite3
    with pytest.raises(sqlite3.Error):
        store.save(record(value='80'), experiment_id=first.id, expected_revision=1)
    assert store.get(first.id).revision == 1
    assert store.get(first.id).draft.samples[0].conditions['温度'].text == '60'
    with store.knowledge.connection() as db:
        db.execute('UPDATE experiment_meta SET version=99')
    with pytest.raises(ValueError, match='版本'):
        ExperimentStore(store.knowledge)
    with pytest.raises(ValueError, match='版本'):
        KnowledgeBase(store.knowledge.path)


def test_condition_inheritance_and_current_scope(store):
    snapshot = store.save(record())
    found = store.search('A1 温度', [snapshot.draft.samples[0].id])
    assert found and '60' in found[0].text
    assert store.search('A1', []) == []
    assert store.list_experiments('实验一')[0].sample_count == 1
