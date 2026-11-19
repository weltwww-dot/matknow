"""Fictional, isolated preview data; also exercises bundled Office parsers."""
import math
from pathlib import Path
from uuid import uuid4

from docx import Document
from openpyxl import Workbook
from PySide6.QtGui import QImage, QPainter, QColor

from .models import Value, ResultDraft, ExperimentDraft, SampleDraft
from .drafts import ColumnMap, from_table, confirm_draft
from .readers import read_record
from .files import Upload


def populate_demo(archive,folder):
    folder=Path(folder)
    word=folder/'虚构制备记录.docx';document=Document();document.add_paragraph('虚构教学实验：水热制备。样品 A1、A2 分别采用 160 °C 和 180 °C，反应时间均为 8 h。');document.save(word)
    word_token=str(uuid4());word_record=read_record(word,file_id=word_token)
    excel=folder/'虚构测试结果.xlsx';workbook=Workbook();sheet=workbook.active;sheet.title='测试结果'
    sheet.append(['样品','制备温度','强度']);sheet.append(['A1',160,60]);sheet.append(['A2',180,80]);sheet.append(['A3',180,75000]);workbook.save(excel);workbook.close()
    token=str(uuid4());parsed=read_record(excel,file_id=token)
    draft=from_table(parsed,ColumnMap('测试结果',1,(1,1,4,3),1,{2:'制备温度'},{3:('强度','MPa')}),title='虚构教学实验 · 水热制备',date='2026-10-13')
    draft.common={'反应时间':Value('8','h')};draft.source_ids.append(word_token)
    draft.notes='\n'.join(f'{b.locator}：{b.text}' for b in word_record.blocks)
    for sample in draft.samples:
        sample.conditions['制备温度'].unit='°C'
        sample.results[0].method='拉伸';sample.results[0].test_conditions={'测试温度':'25 °C','加载速率':'1 mm/min'}
        sample.results[0].measured_at='2026-10-13'
    draft.samples[2].results[0].value.unit='kPa'
    draft.samples[2].results[0].test_conditions={}
    draft.samples[0].results.append(ResultDraft('强度',Value('62','MPa'),method='拉伸',test_conditions={'测试温度':'25 °C','加载速率':'1 mm/min'},repeat_group='重复测量',measured_at='2026-10-13'))
    curve=folder/'虚构曲线.csv'
    curve.write_text('时间,电流\n'+'\n'.join(f'{i/100},{math.sin(i/450)+2}' if not 2000<i<2100 else ',' for i in range(5000)),encoding='utf8')
    curve_token=str(uuid4());draft.samples[0].results.append(ResultDraft('电流曲线',Value('原始曲线'),kind='curve',attachment_ids=[curve_token],data={'attachment_id':curve_token,'bounds':[2,1,5001,2],'x_column':1,'y_column':2,'x_unit':'s','y_unit':'mA','sort_x':False}))
    image=folder/'虚构图片.png';canvas=QImage(640,400,QImage.Format.Format_RGB32);canvas.fill(QColor('#e7efeb'))
    painter=QPainter(canvas);painter.setPen(QColor('#267862'));painter.drawText(35,45,'虚构教学图片 · 不用于科研结论')
    for x,y,r in [(110,160,45),(245,220,60),(390,150,55),(500,280,35)]:painter.drawEllipse(x,y,r,r)
    painter.end();canvas.save(str(image))
    image_token=str(uuid4());draft.samples[0].results.append(ResultDraft('表面图',Value('待人工观察'),kind='image',attachment_ids=[image_token]))
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
    report=folder/'虚构测试报告.pdf';writer=PdfWriter();page=writer.add_blank_page(width=400,height=300)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 30 250 Td (Fictional teaching report - A2: 80 MPa) Tj ET');page[NameObject('/Contents')]=writer._add_object(stream)
    with report.open('wb') as output:writer.write(output)
    report_token=str(uuid4());draft.samples[1].results.append(ResultDraft('测试报告',Value('虚构教学报告'),kind='pdf',attachment_ids=[report_token]))
    uploads=[Upload(word,word_token,'source',expected_sha256=word_record.sha256),Upload(excel,token,'source',expected_sha256=parsed.sha256),
             Upload(curve,curve_token,'attachment','A1',2),Upload(image,image_token,'attachment','A1',3),Upload(report,report_token,'attachment','A2',1)]
    snapshot=archive.save(confirm_draft(draft),uploads)
    previous=ExperimentDraft('虚构教学实验 · 历史批次','2026-10-12',samples=[SampleDraft('A1',results=[ResultDraft('强度',Value('58','MPa'),method='拉伸')])])
    archive.save(previous,[Upload(word,str(uuid4()),'source',expected_sha256=word_record.sha256)])
    return snapshot


def exercise_demo_previews(page,snapshot):
    """Screenshot-only smoke: use the same managed-file readers as the actual UI."""
    from . import preview
    selected=None
    for sample in snapshot.draft.samples:
        for result in sample.results:
            if result.kind not in ('curve','image','pdf'):continue
            data=result.data
            spec=preview.CurveSpec(data['x_column'],data['y_column'],data['x_unit'],data['y_unit']) if result.kind=='curve' else None
            token=result.attachment_ids[0]
            payload=preview.prepare_attachment(page.archive,token,spec,bounds=data.get('bounds'))
            if result.kind=='curve':selected=(result.id,token,payload)
    if selected:
        result_id,token,payload=selected
        evidence=next(row for row in page.store.evidence_rows([snapshot.draft.samples[0].id]) if row['result_id']==result_id)
        page.show_evidence(evidence['id'],open_dialog=False);page.preview.apply_prepared(page.archive,token,payload)
        page.preview.show();page.detail_tabs.setCurrentIndex(2);page.preview.content.setCurrentIndex(1)
