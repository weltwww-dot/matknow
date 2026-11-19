from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from matknow.experiments.models import ChemistryRecord, Reagent, ExperimentDraft, SampleDraft, draft_from_dict, validate_draft
from matknow.experiments.store import ExperimentStore
from matknow.knowledge import KnowledgeBase


def record():
    return ExperimentDraft('虚构材料制备记录','2026-10-14',samples=[SampleDraft('A1'),SampleDraft('A2')],notes='09:10 记录实际操作；09:20 观察与原计划不同。',
        chemistry=ChemistryRecord(objective='比较两组条件',operator='测试人员',status='aborted',
            reagents=[Reagent('原料甲','0.50','g','已记录规格','批次1',['A1']),Reagent('PRIVATE_A2_REAGENT','0.60','g',sample_codes=['A2'])],
            setup='装置和实际温度见记录',deviations='09:25 中止，原因见观察。',discussion='尚不能判断原因',conclusion='需要补充测试',references='参考 SOP 版本 1'))


def test_legacy_payload_opens_and_structured_record_survives_revisions(tmp_path):
    kb=KnowledgeBase(tmp_path/'kb.sqlite3');store=ExperimentStore(kb)
    snap=store.save(record());first=deepcopy(snap.draft)
    first.chemistry.reagents[0].quantity='0.55'
    store.save(first,experiment_id=snap.id,expected_revision=1)
    assert store.get(snap.id,1).draft.chemistry.reagents[0].quantity=='0.50'
    assert store.get(snap.id).draft.chemistry.reagents[0].quantity=='0.55'
    raw=asdict(snap.draft);raw.pop('chemistry')
    with kb.connection() as db:
        db.execute('UPDATE experiment_revisions SET payload=? WHERE experiment_id=? AND revision=1',(json.dumps(raw),snap.id))
        db.execute('UPDATE experiment_meta SET version=1')
    reopened=ExperimentStore(KnowledgeBase(kb.path))
    assert reopened.get(snap.id,1).draft.chemistry==ChemistryRecord()
    assert reopened.get(snap.id).draft.chemistry.status=='aborted'
    with kb.connection() as db:assert db.execute('SELECT version FROM experiment_meta').fetchone()[0]==2


def test_chemical_details_are_searchable_only_in_matching_sample_evidence(tmp_path):
    store=ExperimentStore(KnowledgeBase(tmp_path/'kb.sqlite3'));snap=store.save(record())
    sources=store.search('中止 原料',[snap.draft.samples[0].id])
    assert sources and '0.50 g' in sources[0].text and '09:25' in sources[0].text
    assert 'PRIVATE_A2_REAGENT' not in '\n'.join(s.text for s in sources)
    assert 'PRIVATE_A2_REAGENT' in store.evidence_rows([snap.draft.samples[1].id])[0]['text']


def test_editor_preserves_chemistry_when_only_process_notes_change(qtbot):
    from matknow.experiments.editors import ExperimentEditor
    draft=record();editor=ExperimentEditor(draft);qtbot.addWidget(editor)
    editor.notes.setPlainText('补记实际现象，不覆盖旧版本。')
    assert editor.collect().chemistry==draft.chemistry


@pytest.mark.parametrize('change',[lambda d:setattr(d.chemistry,'status','invented'),lambda d:setattr(d.chemistry.reagents[0],'sample_codes',['UNKNOWN']),lambda d:setattr(d.chemistry.reagents[0],'quantity',4)])
def test_invalid_chemical_metadata_is_rejected_without_dropping_it(change):
    draft=record();change(draft)
    with pytest.raises(ValueError):validate_draft(draft)
    with pytest.raises(ValueError):draft_from_dict({'title':'坏结构','date':'2026-10-14','chemistry':[]})
