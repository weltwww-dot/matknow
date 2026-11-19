import json
from copy import deepcopy

import pytest

from matknow.llm import ModelError
from matknow.experiments.report import build_report, report_markdown, export_report
from tests.test_experiment_report import report_archive


class Writer:
    def __init__(self, *, bad_source=False, approve=True):
        self.calls=[];self.bad_source=bad_source;self.approve=approve

    def complete(self,messages,json_output=False):
        payload=json.loads(messages[-1]['content']);self.calls.append(payload)
        if payload['task']=='write_report':
            ids=[s['id'] for s in payload['sources']]
            sections=[{'heading':h,'text':'模型组织的完整报告正文，缺项待核对。',
                       'source_ids':ids if i==0 else [ids[0]]} for i,h in enumerate(payload['required_sections'])]
            if self.bad_source:sections[0]['source_ids']=['invented']
            return json.dumps({'sections':sections,'uncertainties':['A2 尚无测量结果']},ensure_ascii=False)
        assert payload['task']=='review_report'
        return json.dumps({'approved':self.approve,'issues':[] if self.approve else ['把计划写成实测'],
                           'checked_source_ids':[s['id'] for s in payload['sources']]},ensure_ascii=False)


def test_model_writes_whole_report_reviews_and_preserves_original_data(report_archive,tmp_path):
    from matknow.experiments.report_writer import write_report
    archive,snap=report_archive;original=build_report(archive,snap.id,profile_name='材料')
    before=report_markdown(original);client=Writer();progress=[]
    report=write_report(original,client,archive=archive,progress=progress.append)
    text=report_markdown(report)
    assert [p['task'] for p in client.calls]==['write_report','review_report']
    assert '模型组织的完整报告正文' in text and '原始记录对照' in text
    assert all(s in text for s in ('60.0 MPa','62.0 MPa','重复1','A1','A2','0.50 g','AI 撰写'))
    assert len(progress)>=2 and report_markdown(original)==before
    assert archive.store.get(snap.id).revision==1
    target=tmp_path/'AI报告.docx';export_report(report,target)
    from docx import Document
    assert any('模型组织的完整报告正文' in p.text for p in Document(target).paragraphs)
    sources=client.calls[0]['sources']
    attachment=next(s for s in sources if s.get('filename')=='raw.csv')
    assert '1' in attachment['text'] and '2' in attachment['text'] and attachment['locator']
    assert str(archive.root) not in json.dumps(client.calls,ensure_ascii=False)


@pytest.mark.parametrize('options',[{'bad_source':True},{'approve':False}])
def test_invalid_or_unfaithful_draft_does_not_escape_as_complete_report(report_archive,options):
    from matknow.experiments.report_writer import write_report
    archive,snap=report_archive;report=build_report(archive,snap.id,profile_name='材料');before=deepcopy(report)
    with pytest.raises(ModelError):write_report(report,Writer(**options),archive=archive)
    assert report==before


def test_regeneration_uses_original_revision_and_never_previous_ai_prose(report_archive):
    from matknow.experiments.report_writer import write_report
    archive,snap=report_archive;report=build_report(archive,snap.id,profile_name='材料',revision=1)
    first=write_report(report,Writer(),archive=archive)
    changed=deepcopy(snap.draft);changed.chemistry.reagents[0].quantity='0.55'
    archive.save(changed,[],experiment_id=snap.id,expected_revision=1)
    second=Writer();again=write_report(first,second,archive=archive)
    prompt=json.dumps(second.calls[0],ensure_ascii=False)
    assert '模型组织的完整报告正文' not in prompt and '0.55' not in prompt and '0.50' in prompt
    assert sum(b.heading=='原始记录对照' for b in again.blocks)==1


def test_missing_and_unreadable_attachments_are_explicit(report_archive):
    from matknow.experiments.report_writer import write_report
    archive,snap=report_archive;archive.path_for(snap.draft.samples[0].results[0].attachment_ids[0]).unlink()
    client=Writer();report=write_report(build_report(archive,snap.id,profile_name='材料'),client,archive=archive)
    assert any('raw.csv' in w for w in report.warnings)
    assert '缺失' in json.dumps(client.calls[0],ensure_ascii=False)


def test_large_record_is_not_silently_truncated(report_archive):
    from matknow.experiments.report_writer import write_report
    archive,snap=report_archive;report=build_report(archive,snap.id,profile_name='材料')
    report.blocks[0].paragraphs=['记录'*30000];client=Writer();client.context_window=8192
    with pytest.raises(ModelError,match='上下文'):write_report(report,client,archive=archive)
    assert not client.calls


def test_report_ui_uses_model_and_keeps_fallback_obvious(qtbot,tmp_path,monkeypatch):
    from matknow.experiments.report_dialog import ReportDialog
    from tests.test_chemical_record import record
    from tests.test_experiment_desktop import window
    w=window(qtbot,tmp_path);page=w.experiment_page;snap=page.archive.save(record(),[])
    page.show_evidence(page.store.evidence_rows([snap.draft.samples[0].id])[0]['id'])
    seen=[]
    def review(dialog):seen.append(dialog.report);return dialog.DialogCode.Rejected
    monkeypatch.setattr(ReportDialog,'exec',review)
    page.model_client=Writer();page.report_button.click();qtbot.waitUntil(lambda:len(seen)==1 and not w.busy)
    assert 'AI 撰写' in seen[0].summary and '原始记录对照' in report_markdown(seen[0])
    page.model_client=Writer(approve=False);page.report_button.click();qtbot.waitUntil(lambda:len(seen)==2 and not w.busy)
    assert '原始记录整理稿' in seen[1].summary and '模型组织的完整报告正文' not in report_markdown(seen[1])
    assert seen[1].original is not None and all(b.heading!='报告生成状态' for b in seen[1].original.blocks)


def test_excel_formulas_and_images_are_not_claimed_as_verified_measurements(report_archive,tmp_path):
    from openpyxl import Workbook
    from matknow.experiments.files import Upload
    from matknow.experiments.report_writer import write_report
    archive,snap=report_archive;book=Workbook();book.active.append(['A1','=1+2'])
    path=tmp_path/'results.xlsx';book.save(path)
    image=tmp_path/'SEM.png'
    from PySide6.QtGui import QImage
    picture=QImage(2,2,QImage.Format.Format_RGB32);picture.fill(0);picture.save(str(image))
    snap=archive.save(deepcopy(snap.draft),[Upload(path,'table','attachment','A1',0),Upload(image,'image','attachment','A1',0)],experiment_id=snap.id,expected_revision=1)
    client=Writer();report=write_report(build_report(archive,snap.id,profile_name='材料'),client,archive=archive)
    text=json.dumps(client.calls[0],ensure_ascii=False)
    assert '=1+2' in text and '未重算' in text and '未读取附件内容' in text
    assert any('SEM.png' in s for s in report.warnings)


def test_same_filename_has_unambiguous_versioned_sample_and_result_links(report_archive,tmp_path):
    from matknow.experiments.files import Upload
    from matknow.experiments.models import ResultDraft,Value
    from matknow.experiments.report_writer import write_report
    archive,snap=report_archive;draft=deepcopy(snap.draft)
    draft.samples[1].results=[ResultDraft('强度',Value('70.0','MPa'))]
    folder=tmp_path/'another';folder.mkdir();path=folder/'raw.csv';path.write_text('x,y\n0,70\n')
    snap=archive.save(draft,[Upload(path,'another','attachment','A2',0)],experiment_id=snap.id,expected_revision=1)
    original=build_report(archive,snap.id,profile_name='材料',revision=2);client=Writer()
    report=write_report(original,client,archive=archive)
    sources=[s for s in client.calls[0]['sources'] if s.get('filename')=='raw.csv']
    assert len(sources)==2 and len({s['attachment_id'] for s in sources})==2
    links={s['attachment_id']:{link['sample'] for link in s['links']} for s in sources}
    for sample in snap.draft.samples:
        token=sample.results[0].attachment_ids[0]
        assert sample.code in links[token] and any('结果 1' in link['purpose'] for s in sources if s['attachment_id']==token for link in s['links'])
        assert token in report_markdown(report)
