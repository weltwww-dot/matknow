from copy import deepcopy
import json

import pytest

from matknow.experiments.drafts import ColumnMap, from_table, organize, correct_value, confirm_draft
from matknow.experiments.models import ExperimentDraft, SampleDraft, Value, validate_draft
from matknow.experiments.readers import Block, Cell, Table, ReadDocument
from matknow.llm import ModelError


def document(repeated=False):
    cells = [Cell(1, 1, '样品'), Cell(1, 2, '强度'), Cell(2, 1, 'A1'), Cell(2, 2, '2', '=1+1', '2')]
    if repeated:
        cells += [Cell(3, 1, 'A1'), Cell(3, 2, '3')]
    return ReadDocument('file', 'data.xlsx', 'hash', [Block('A1 强度 2 MPa', 'Sheet1!B2'), Block('PRIVATE_OTHER_BLOCK', 'Sheet1!B9')], [Table('Sheet1', cells)], [])


def mapping(repeated=False, policy='reject'):
    return ColumnMap('Sheet1', 1, (1, 1, 3 if repeated else 2, 2), 1, {}, {2: ('强度', 'MPa')}, policy)


def test_column_mapping_retains_sources_and_requires_formula_confirmation():
    draft = from_table(document(), mapping(), title='实验', date='2026-10-13')
    candidate = draft.samples[0].results[0]
    assert candidate.value.confirmed is False
    assert candidate.value.text == '2'
    assert candidate.value.source.locator == 'Sheet1!B2'
    with pytest.raises(ValueError, match='确认'):
        validate_draft(draft)
    confirmed = confirm_draft(draft)
    validate_draft(confirmed)
    assert draft.samples[0].results[0].value.confirmed is False


def test_duplicate_codes_require_explicit_repeat_mapping_or_rename():
    with pytest.raises(ValueError, match='重复'):
        from_table(document(True), mapping(True), title='实验', date='2026-10-13')
    draft = from_table(document(True), mapping(True, 'repeated_measurements'), title='实验', date='2026-10-13')
    assert len(draft.samples) == 1
    assert len(draft.samples[0].results) == 2


class Model:
    def __init__(self, payload):
        self.payload, self.messages = payload, None
    def complete(self, messages, json_output=False):
        assert json_output
        self.messages = messages
        if isinstance(self.payload, Exception):
            raise self.payload
        return json.dumps(self.payload)


def payload():
    return {'record_type':'results','samples': [{'code': 'A1', 'locator':'Sheet1!B2','conditions': [], 'results': [{'metric': '强度', 'text': '2', 'unit': 'MPa', 'kind': 'number', 'locator': 'Sheet1!B2'}]}]}


def base():
    return ExperimentDraft('实验', '2026-10-13', samples=[SampleDraft('A1')])


def test_organization_uses_only_selected_blocks_and_never_writes_store(tmp_path):
    model = Model(payload())
    draft = organize(document(), ['Sheet1!B2'], base(), model)
    assert 'PRIVATE_OTHER_BLOCK' not in json.dumps(model.messages)
    assert draft.samples[0].results[0].value.origin == 'model_confirmed'
    assert draft.samples[0].results[0].value.confirmed is False
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('change', [
    lambda p: p['samples'][0].update(code='B1'),
    lambda p: p['samples'][0].update(path='C:/private'),
    lambda p: p['samples'][0]['results'][0].update(text='NaN'),
    lambda p: p['samples'][0]['results'][0].update(origin='manual'),
    lambda p: p['samples'][0]['results'][0].update(locator='Sheet1!B9'),
])
def test_model_foreign_sample_path_nonfinite_or_unsupported_origin_rejected(change):
    data = payload()
    change(data)
    with pytest.raises(ModelError):
        organize(document(), ['Sheet1!B2'], base(), Model(data))


def test_model_error_keeps_editable_manual_draft():
    draft = base()
    original = deepcopy(draft)
    with pytest.raises(ModelError):
        organize(document(), ['Sheet1!B2'], draft, Model(ModelError('连接失败')))
    assert draft == original


def test_manual_correction_keeps_original_source_and_marks_manual():
    draft = from_table(document(), mapping(), title='实验', date='2026-10-13')
    candidate = draft.samples[0].results[0].value
    corrected = correct_value(candidate, '2.1')
    assert corrected.origin == 'manual'
    assert corrected.confirmed is True
    assert corrected.source == candidate.source
    assert candidate.text == '2'


def test_missing_formula_cache_and_context_budget_require_user_action():
    doc = document()
    doc.tables[0].cells[-1].cached = None
    candidate = from_table(doc, mapping(), title='实验', date='2026-10-13')
    with pytest.raises(ValueError):
        confirm_draft(candidate)
    doc.blocks[0].text = 'x' * 48001
    model=Model(payload());model.context_window=8192
    with pytest.raises((ValueError,ModelError), match='范围|内容|上下文'):
        organize(doc, ['Sheet1!B2'], base(), model)
