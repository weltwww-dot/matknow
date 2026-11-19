from copy import deepcopy
from decimal import Decimal
from uuid import uuid4

import pytest

from matknow.experiments.compare import compare_samples, confirm_group, rank_measurements
from matknow.experiments.models import ExperimentDraft, SampleDraft, ResultDraft, Value
from matknow.experiments.store import ExperimentStore
from matknow.knowledge import KnowledgeBase


@pytest.fixture
def stored(tmp_path):
    store = ExperimentStore(KnowledgeBase(tmp_path / 'kb.sqlite3'))
    draft = ExperimentDraft('制备', '2026-10-13', {'温度': Value('60', '°C')}, [
        SampleDraft('A1', {}, [ResultDraft('强度', Value('0.1', 'MPa'), method='拉伸', test_conditions={'温度': '25'})]),
        SampleDraft('A2', {'温度': Value('80', '°C'), '时间': Value('2', 'h')}, [
            ResultDraft('强度', Value('2e-1', 'MPa'), method='拉伸', test_conditions={'温度': '25'}),
            ResultDraft('强度', Value('0.2', 'MPa'), method='拉伸', test_conditions={'温度': '25'})])])
    snap = store.save(draft)
    return store, snap, [s.id for s in snap.draft.samples]


def test_two_to_twenty_samples_and_no_foreign_scope(stored):
    store, snap, ids = stored
    for scope in (ids[:1], ids + [str(uuid4())], ids * 11):
        with pytest.raises(ValueError): compare_samples(store, scope)
    many = store.save(ExperimentDraft('批次', '2026-10-13', samples=[SampleDraft(f'S{i}') for i in range(20)]))
    assert len(compare_samples(store, [s.id for s in many.draft.samples]).sample_ids) == 20


def test_common_conditions_overrides_and_unknown_are_distinct(stored):
    store, _, ids = stored
    rows = {r['condition_name']: r for r in compare_samples(store, ids).conditions}
    assert rows['温度']['status'] == 'different'
    assert rows['温度']['values'][ids[0]].text == '60'
    assert rows['时间']['status'] == 'missing'


def test_different_units_methods_and_missing_test_conditions_do_not_auto_group(stored):
    store, snap, ids = stored
    draft = deepcopy(snap.draft)
    draft.samples[1].results[0].value.unit = 'kPa'
    draft.samples[1].results[1].test_conditions = {}
    store.save(draft, experiment_id=snap.id, expected_revision=1)
    c = compare_samples(store, ids)
    assert len(c.groups) == 3
    assert sum(g['status'] == 'needs_confirmation' for g in c.groups) == 1


def test_repeats_are_all_visible_and_never_implicitly_averaged(stored):
    store, snap, ids = stored
    rows = compare_samples(store, ids).groups[0]['measurements']
    assert {r['result_id'] for r in rows} == {r.id for s in snap.draft.samples for r in s.results}
    assert all(r['source_id'].startswith('exp:') for r in rows)


def test_manual_group_confirmation_does_not_rewrite_original_values(stored):
    store, snap, ids = stored
    draft = deepcopy(snap.draft)
    draft.samples[1].results[0].method = '拉伸测试'
    saved = store.save(draft, experiment_id=snap.id, expected_revision=1)
    c = compare_samples(store, ids)
    selected = [r.id for s in saved.draft.samples for r in s.results]
    merged = confirm_group(c, selected, metric='强度', unit='MPa', method='拉伸', test_conditions={'温度': '25'})
    assert len(merged.groups) == 1
    assert store.get(snap.id).draft == saved.draft
    assert len(c.groups) == 2
    with pytest.raises(ValueError): confirm_group(c, selected, metric='强度', unit='Pa', method='拉伸', test_conditions={'温度': '25'})


def test_decimal_sort_difference_and_ties_use_all_selected_measurements(stored):
    store, _, ids = stored
    c = compare_samples(store, ids)
    results = [r['result_id'] for r in c.groups[0]['measurements']]
    rows = rank_measurements(c, result_ids=results, descending=True, reference_result_id=results[0])
    assert len(rows) == 3
    assert rows[0]['difference'] == Decimal('0.1')
    assert rows[-1]['difference'] == 0
    for invalid in ['NaN', 'Infinity', '']:
        bad = deepcopy(c); bad.groups[0]['measurements'][0]['value'].text = invalid
        with pytest.raises(ValueError): rank_measurements(bad, result_ids=results, descending=True)


def test_multiple_changed_conditions_do_not_support_unique_cause(stored):
    store, _, ids = stored
    c = compare_samples(store, ids)
    assert any('因果' in warning for warning in c.warnings)
    assert '2' in c.scope
