from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from matknow.experiments.export import export_comparison, export_experiment, export_answer
from matknow.experiments.files import ExperimentArchive, Upload
from matknow.experiments.models import ExperimentDraft, SampleDraft, ResultDraft, Value, SourceRef
from matknow.experiments.store import ExperimentStore
from matknow.experiments.compare import compare_samples
from matknow.experiments.service import ExperimentAskService
from matknow.knowledge import KnowledgeBase


@pytest.fixture
def archive(tmp_path):
    archive=ExperimentArchive(ExperimentStore(KnowledgeBase(tmp_path/'kb.sqlite3')))
    p=tmp_path/'record.txt';p.write_text('source')
    draft=ExperimentDraft('实验 <unsafe>', '2026-10-13', samples=[
        SampleDraft('A1', results=[ResultDraft('强度',Value('60','MPa','imported',SourceRef('record','Sheet1!B2')),method='拉伸',test_conditions={'温度':'25'})]),
        SampleDraft('A2', results=[ResultDraft('强度',Value('80','MPa'),method='拉伸',test_conditions={'温度':'25'})])],source_ids=['record'])
    return archive,archive.save(draft,[Upload(p,'record','source')])


def test_markdown_has_profile_scope_versions_units_and_source_locators(archive,tmp_path):
    a,snap=archive;ids=[s.id for s in snap.draft.samples]
    target=tmp_path/'answer.md';answer=ExperimentAskService(a.store).ask('强度',ids)
    export_answer(answer,a.store.knowledge,target,profile_name='材料')
    text=target.read_text(encoding='utf8')
    assert 'Sheet1!B2' in text and '版本 1' in text and 'MPa' in text and '材料' in text
    assert '<unsafe>' not in text and str(tmp_path) not in text
    export_comparison(compare_samples(a.store,ids),target,profile_name='材料')
    text=target.read_text(encoding='utf8');assert '完整选择' in text and '60' in text and '80' in text


def test_zip_contains_selected_revision_json_and_managed_originals_only(archive,tmp_path):
    a,snap=archive;new=tmp_path/'new.bin';new.write_bytes(b'NEW')
    a.save(snap.draft,[Upload(new,'new','attachment','A1',0)],experiment_id=snap.id,expected_revision=1)
    target=tmp_path/'record.zip';export_experiment(a,snap.id,target,profile_name='材料',revision=1)
    with ZipFile(target) as z:
        manifest=json.loads(z.read('manifest.json'))
        assert manifest['revision']==1
        assert len(manifest['attachments'])==1
        assert len(z.namelist())==2
        assert all(not name.startswith('/') and '..' not in name for name in z.namelist())
        assert 'new.bin' not in z.read('manifest.json').decode()


def test_export_failure_keeps_previous_target_and_no_unrelated_files(archive,tmp_path,monkeypatch):
    a,snap=archive;target=tmp_path/'record.zip';target.write_bytes(b'OLD')
    def fail(_):raise OSError('locked')
    monkeypatch.setattr(a,'path_for',fail)
    with pytest.raises(OSError):export_experiment(a,snap.id,target,profile_name='材料')
    assert target.read_bytes()==b'OLD'
    assert list(tmp_path.glob('.*.tmp'))==[]


def test_missing_or_deleted_source_warns_without_fabricated_value(archive,tmp_path):
    a,snap=archive;answer=ExperimentAskService(a.store).ask('强度')
    a.delete(snap.id);target=tmp_path/'answer.md'
    export_answer(answer,a.store.knowledge,target,profile_name='材料')
    text=target.read_text(encoding='utf8')
    assert '已删除' in text and 'Sheet1!B2' not in text and '60 MPa' not in text


def test_comparison_exports_cannot_replace_database_sidecars_or_managed_files(archive):
    a,snap=archive;c=compare_samples(a.store,[s.id for s in snap.draft.samples])
    for target in [a.store.knowledge.path,Path(str(a.store.knowledge.path)+'-wal'),a.path_for(snap.draft.source_ids[0])]:
        old=target.read_bytes() if target.exists() else None
        with pytest.raises(ValueError):export_comparison(c,target,profile_name='材料')
        assert (target.read_bytes() if target.exists() else None)==old


def test_attachment_save_cannot_replace_database_or_managed_files(archive,qtbot):
    from matknow.experiments.preview import AttachmentPreview
    a,snap=archive;preview=AttachmentPreview();qtbot.addWidget(preview);preview.archive=a;preview.attachment_id=snap.draft.source_ids[0]
    for target in [a.store.knowledge.path,a.path_for(preview.attachment_id)]:
        old=target.read_bytes()
        with pytest.raises(ValueError):preview.save_to(target)
        assert target.read_bytes()==old


def test_condition_only_export_identifies_all_samples_and_revisions(tmp_path):
    a=ExperimentArchive(ExperimentStore(KnowledgeBase(tmp_path/'kb.sqlite3')))
    snap=a.save(ExperimentDraft('无结果批次','2026-10-13',common={'温度':Value('60','°C')},samples=[SampleDraft('A1'),SampleDraft('A2')]),[])
    target=tmp_path/'conditions.md';export_comparison(compare_samples(a.store,[s.id for s in snap.draft.samples]),target,profile_name='材料')
    text=target.read_text(encoding='utf8');assert all(word in text for word in ['A1','A2','无结果批次','版本 1'])


def test_corrected_values_are_identified_in_editor_evidence_and_export(archive,tmp_path,qtbot):
    from matknow.experiments.drafts import correct_value
    from matknow.experiments.editors import ValueTable
    a,snap=archive;draft=deepcopy(snap.draft);draft.samples[0].results[0].value=correct_value(draft.samples[0].results[0].value,'600')
    a.save(draft,[],experiment_id=snap.id,expected_revision=1)
    answer=ExperimentAskService(a.store).ask('强度',[snap.draft.samples[0].id])
    assert '人工修正' in answer.sources[0].text and '原文件定位' in answer.sources[0].text
    editor=ValueTable({'强度':draft.samples[0].results[0].value});qtbot.addWidget(editor)
    assert '人工修正' in editor.grid.item(0,3).text()
    target=tmp_path/'answer.md';export_answer(answer,a.store.knowledge,target,profile_name='材料')
    assert '人工修正' in target.read_text(encoding='utf8')
