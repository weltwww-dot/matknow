from copy import deepcopy

import pytest
from docx import Document

from matknow.experiments.files import ExperimentArchive, Upload
from matknow.experiments.models import ResultDraft, Value, SourceRef
from matknow.experiments.report import build_report, export_report, report_markdown
from matknow.experiments.store import ExperimentStore
from matknow.knowledge import KnowledgeBase
from tests.test_chemical_record import record


@pytest.fixture
def report_archive(tmp_path):
    archive=ExperimentArchive(ExperimentStore(KnowledgeBase(tmp_path/'data'/'kb.sqlite3')))
    draft=record();draft.chemistry.status='completed'
    draft.samples[0].results=[ResultDraft('强度',Value('60.0','MPa'),method='拉伸',test_conditions={'温度':'25 °C'},measured_at='2026-10-14',repeat_group='重复1'),
                              ResultDraft('强度',Value('62.0','MPa'),method='拉伸',test_conditions={'温度':'25 °C'},repeat_group='重复1')]
    source=tmp_path/'raw.csv';source.write_text('x,y\n0,1\n1,2\n',encoding='utf8')
    snap=archive.save(draft,[Upload(source,'raw','attachment','A1',0)])
    return archive,snap


def test_report_uses_exact_revision_keeps_repeats_and_never_fills_unknowns(report_archive):
    archive,snap=report_archive;draft=deepcopy(snap.draft);draft.chemistry.reagents[0].quantity='0.55'
    archive.save(draft,[],experiment_id=snap.id,expected_revision=1)
    report=build_report(archive,snap.id,profile_name='材料制备',revision=1);text=report_markdown(report)
    assert '0.50 g' in text and '0.55 g' not in text and '版本 1' in text
    assert all(value in text for value in ('60.0 MPa','62.0 MPa','A1','A2','raw.csv','重复1','SOP 版本 1'))
    assert '产率为' not in text and '实验成功' not in text and '未记录' in text
    assert any('A2' in warning for warning in report.warnings)
    assert str(archive.root) not in text


@pytest.mark.parametrize('suffix',['.md','.docx'])
def test_report_exports_readable_content_without_database_or_credentials(report_archive,tmp_path,suffix):
    archive,snap=report_archive;report=build_report(archive,snap.id,profile_name='材料制备')
    target=tmp_path/('实验报告'+suffix);export_report(report,target)
    if suffix=='.md':text=target.read_text(encoding='utf8')
    else:
        document=Document(target)
        text='\n'.join(p.text for p in document.paragraphs)+'\n'+'\n'.join(cell.text for t in document.tables for row in t.rows for cell in row.cells)
    assert '比较两组条件' in text and '0.50 g' in text and '60.0 MPa' in text and '测试人员' in text
    assert str(archive.root) not in text and 'models.json' not in text and 'API Key' not in text


def test_missing_original_is_reported_without_faking_a_verified_file(report_archive):
    archive,snap=report_archive;token=snap.draft.samples[0].results[0].attachment_ids[0]
    archive.path_for(token).unlink()
    report=build_report(archive,snap.id,profile_name='材料')
    assert any('raw.csv' in warning for warning in report.warnings)
    assert '缺失或校验失败' in report_markdown(report)


def test_unmeasured_plan_is_not_presented_as_a_completed_experiment(tmp_path):
    archive=ExperimentArchive(ExperimentStore(KnowledgeBase(tmp_path/'kb.sqlite3')))
    draft=record();draft.chemistry.status='planned';draft.chemistry.discussion='';draft.chemistry.conclusion=''
    snap=archive.save(draft,[]);report=build_report(archive,snap.id,profile_name='材料')
    text=report_markdown(report)
    assert '计划中' in text and '不代表已经执行' in text and '结论' in text and '未记录' in text
    assert '已完成实验' not in text


def test_report_cannot_replace_managed_files_or_unsupported_formats(report_archive,tmp_path):
    archive,snap=report_archive;report=build_report(archive,snap.id,profile_name='材料')
    targets=[archive.root/'keep.md',archive.store.knowledge.path,tmp_path/'models.json']
    for target in targets:
        target.parent.mkdir(exist_ok=True,parents=True)
        if not target.exists():target.write_bytes(b'KEEP')
        before=target.read_bytes()
        with pytest.raises(ValueError):export_report(report,target)
        assert target.read_bytes()==before


def test_report_button_uses_displayed_historical_revision(qtbot,tmp_path,monkeypatch):
    from matknow.experiments.report_dialog import ReportDialog
    from tests.test_experiment_desktop import window
    w=window(qtbot,tmp_path);page=w.experiment_page
    first=page.archive.save(record(),[]);source=page.store.evidence_rows([first.draft.samples[0].id])[0]['id']
    changed=deepcopy(first.draft);changed.chemistry.reagents[0].quantity='0.55'
    page.archive.save(changed,[],experiment_id=first.id,expected_revision=1)
    page.show_evidence(source);seen=[]
    def review(dialog):seen.append(report_markdown(dialog.report));return dialog.DialogCode.Rejected
    monkeypatch.setattr(ReportDialog,'exec',review)
    page.report_button.click();qtbot.waitUntil(lambda:bool(seen) and not w.busy)
    assert '0.50 g' in seen[0] and '0.55 g' not in seen[0] and '版本 1' in seen[0]


def test_report_export_failure_keeps_previous_file(report_archive,tmp_path,monkeypatch):
    archive,snap=report_archive;report=build_report(archive,snap.id,profile_name='材料')
    target=tmp_path/'report.docx';target.write_bytes(b'PREVIOUS')
    def fail(*args):raise OSError('写入失败')
    monkeypatch.setattr('matknow.experiments.report._write_docx',fail)
    with pytest.raises(OSError):export_report(report,target)
    assert target.read_bytes()==b'PREVIOUS' and not list(tmp_path.glob('.*.tmp'))


def test_report_preserves_corrected_condition_provenance(report_archive):
    archive,snap=report_archive;draft=deepcopy(snap.draft)
    token=draft.samples[0].results[0].attachment_ids[0]
    draft.common['温度']=Value('30','°C','manual',SourceRef(token,'Sheet1!B2'))
    draft.samples[0].conditions['时间']=Value('20','min','imported',SourceRef(token,'Sheet1!C2'))
    snap=archive.save(draft,[],experiment_id=snap.id,expected_revision=1)
    text=report_markdown(build_report(archive,snap.id,profile_name='材料'))
    assert '人工修正' in text and '当前值已人工修改' in text and '直接导入' in text
    assert '共用条件 温度 / Sheet1!B2' in text and '条件 时间 / Sheet1!C2' in text


def test_report_identifies_selected_curve_file_sheet_and_range(report_archive):
    archive,snap=report_archive;draft=deepcopy(snap.draft)
    token=draft.samples[0].results[0].attachment_ids[0]
    result=draft.samples[0].results[0];result.kind='curve'
    result.data={'attachment_id':token,'sheet':'Run2','x_column':1,'y_column':2,'x_unit':'s','y_unit':'MPa','bounds':[2,1,30,2],'sort_x':False}
    snap=archive.save(draft,[],experiment_id=snap.id,expected_revision=1)
    text=report_markdown(build_report(archive,snap.id,profile_name='材料'))
    assert '工作表：Run2' in text and '所选数据文件：raw.csv' in text
    assert '数据范围：2,1,30,2' in text and '保留原顺序' in text
