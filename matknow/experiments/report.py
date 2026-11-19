"""Reports organise a specific saved revision; they never infer experimental facts."""
from dataclasses import dataclass, field, asdict
from copy import deepcopy
from pathlib import Path

from .export import atomic_target, escape, protect_storage
from .models import RECORD_STATUSES, validate_draft, value_provenance


@dataclass
class ReportBlock:
    heading: str
    paragraphs: list[str] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class ExperimentalReport:
    title: str
    metadata: list[tuple[str,str]]
    blocks: list[ReportBlock]
    warnings: list[str]
    database: Path = field(repr=False)
    attachment_ids: list[str] = field(default_factory=list,repr=False)
    attachment_links: dict[str,list[dict]] = field(default_factory=dict,repr=False)
    original: 'ExperimentalReport | None' = field(default=None,repr=False)
    summary: str = '原始记录整理稿，尚未经模型撰写。依据指定版本保留记录，缺项待核对。'


def report_payload(report):
    data=asdict(report)
    data.pop('database')
    data['original']=report_payload(report.original) if report.original is not None else None
    return data


def restore_report(data,database):
    values=dict(data)
    values['blocks']=[ReportBlock(**block) for block in values['blocks']]
    values['original']=restore_report(values['original'],database) if values.get('original') else None
    return ExperimentalReport(database=database,**values)


def build_report(archive,experiment_id,*,profile_name,revision=None):
    snapshot=archive.store.get(experiment_id,revision)
    draft=snapshot.draft;validate_draft(draft);chemistry=draft.chemistry
    warnings=[];blocks=[];files={};associations=[]
    def section(heading,text):blocks.append(ReportBlock(heading,[text.strip() or '未记录']))
    def attach(token,sample,purpose):
        if token not in files:
            try:item=archive.attachment(token)
            except ValueError:
                files[token]=('附件记录缺失','缺失或校验失败');warnings.append('有附件记录缺失，请核对原件。')
            else:
                if item.experiment_id!=snapshot.id:raise ValueError('报告文件不属于当前实验。')
                try:archive.path_for(token);status='已核验原件'
                except (ValueError,OSError):status='缺失或校验失败';warnings.append(f'原始文件 {item.filename} 缺失或校验失败。')
                files[token]=(item.filename,status)
        row=(token,sample,purpose)
        if row not in associations:associations.append(row)
        return files[token][0]
    def conditions(heading,values,sample,prefix):
        if not values:return
        rows=[]
        for name,value in values.items():
            source=value_provenance(value)
            if value.source:source=attach(value.source.file_id,sample,f'{prefix} {name} / {value.source.locator}')+' / '+source
            rows.append([name,(value.text+' '+value.unit).strip(),source])
        blocks.append(ReportBlock(heading,headers=['条件','记录值','来源'],rows=rows))
    if not chemistry.objective.strip():warnings.append('实验目的未填。')
    if not chemistry.operator.strip():warnings.append('操作者未填。')
    if chemistry.status=='unknown':warnings.append('实验状态未标注，不能据此判定已完成。')
    if chemistry.status=='aborted' and not chemistry.deviations.strip():warnings.append('中止原因未单独记录，请核对原记录。')
    if not draft.notes.strip():warnings.append('操作、时间顺序和观察原记录为空。')
    if not chemistry.reagents:warnings.append('未填写结构化试剂/材料表，请核对原记录中的用量。')
    if not any(s.results for s in draft.samples):warnings.append('尚无结果条目，不能自动形成实测结论。')
    section('实验目的',chemistry.objective)
    if chemistry.status=='planned':blocks[-1].paragraphs.append('当前记录为计划中，计划内容不代表已经执行。')
    section('方法依据',chemistry.references)
    reagent_rows=[]
    for reagent in chemistry.reagents:
        reagent_rows.append([reagent.name,(reagent.quantity+' '+reagent.unit).strip() or '未记录',reagent.specification or '未记录',reagent.source or '未记录',','.join(reagent.sample_codes) or '全部样品'])
        if not reagent.quantity.strip() or not reagent.unit.strip():warnings.append(f'{reagent.name} 的用量或单位未填写完整。')
    quantity_label='计划用量' if chemistry.status=='planned' else '记录用量'
    blocks.append(ReportBlock('试剂与材料',[] if reagent_rows else ['未记录'],['名称',quantity_label,'规格或浓度','来源或批号','对应样品'],reagent_rows))
    section('装置与条件',chemistry.setup)
    conditions('共用制备条件',draft.common,'整个实验','共用条件')
    section('操作与观察原记录',draft.notes)
    section('偏差与中止记录',chemistry.deviations)
    for token in draft.source_ids:attach(token,'整个实验','导入原记录')
    for sample in draft.samples:
        conditions(f'样品 {sample.code} 制备条件',sample.conditions,sample.code,'条件')
        rows=[];missing_method=[];missing_conditions=[];missing_unit=[]
        for index,result in enumerate(sample.results,1):
            detail=['方法：'+(result.method or '未记录'),'时间：'+(result.measured_at or '未记录')]
            detail.extend(f'{name}：{value}' for name,value in result.test_conditions.items())
            if not result.test_conditions:detail.append('测试条件：未记录')
            if result.repeat_group:detail.append('重复测量组：'+result.repeat_group)
            if result.notes:detail.append('备注：'+result.notes)
            data=result.data
            if data.get('attachment_id'):
                selection='；'.join(f'{key}={data[key]}' for key in ('sheet','cell','bounds') if data.get(key))
                detail.append('所选数据文件：'+attach(data['attachment_id'],sample.code,f'结果 {index} / 所选数据文件'+(' / '+selection if selection else '')))
            if data.get('sheet'):detail.append('工作表：'+str(data['sheet']))
            if data.get('cell'):detail.append('单元格：'+str(data['cell']))
            if 'x_column' in data:detail.append(f'曲线列：X={data["x_column"]} {data.get("x_unit","")}；Y={data.get("y_column","未记录")} {data.get("y_unit","")}；'+('连续段内按 X 排序' if data.get('sort_x') else '保留原顺序'))
            if data.get('bounds'):detail.append('数据范围：'+','.join(map(str,data['bounds'])))
            source=value_provenance(result.value)
            if result.value.source:source=attach(result.value.source.file_id,sample.code,f'结果 {index} / {result.value.source.locator}')+' / '+source
            attached=[attach(token,sample.code,f'结果 {index} / {result.metric}') for token in result.attachment_ids]
            if attached:source+='\n文件：'+'、'.join(attached)
            rows.append([str(index),result.metric,(result.value.text+' '+result.value.unit).strip(),'\n'.join(detail),source])
            if not result.method.strip():missing_method.append(str(index))
            if not result.test_conditions:missing_conditions.append(str(index))
            if result.kind=='number' and not result.value.unit.strip():missing_unit.append(str(index))
        blocks.append(ReportBlock(f'样品 {sample.code} 结果',[] if rows else ['未记录'],['序号','指标或观察','记录值','方法与测试条件','来源'],rows))
        if not rows:warnings.append(f'样品 {sample.code} 尚无结果条目。')
        for missing,label in [(missing_method,'测试方法'),(missing_conditions,'测试条件'),(missing_unit,'单位或无量纲说明')]:
            if missing:warnings.append(f'样品 {sample.code} 的结果 {"、".join(missing)} 未填写{label}。')
    section('讨论与局限 用户记录',chemistry.discussion)
    section('结论与下一步 用户记录',chemistry.conclusion)
    blocks.append(ReportBlock('原始文件与样品关联',[] if associations else ['未关联原始文件'],['文件','附件标识','样品或范围','用途或定位','原件状态'],
                              [[files[token][0],token,sample,purpose,files[token][1]] for token,sample,purpose in associations]))
    warnings=list(dict.fromkeys(warnings))
    blocks.append(ReportBlock('待补充与核对',warnings or ['本次结构检查未发现上述缺项；仍需人工核对事实、计算依据和结论。']))
    metadata=[('研究方向',profile_name),('实验日期',draft.date),('操作者',chemistry.operator or '未记录'),('状态',RECORD_STATUSES[chemistry.status]),
              ('记录版本',f'版本 {snapshot.revision}'),('版本保存时间',snapshot.created_at),('档案编号',snapshot.id)]
    links={token:[{'sample':sample,'purpose':purpose} for ref,sample,purpose in associations if ref==token] for token in files}
    return ExperimentalReport(draft.title+' 实验报告草稿',metadata,blocks,warnings,archive.store.knowledge.path,sorted(files),links)


def report_markdown(report):
    lines=['# '+escape(report.title),'',escape(report.summary),'']
    lines.extend(f'- {name}：{escape(value)}' for name,value in report.metadata)
    for block in report.blocks:
        lines+=['','## '+escape(block.heading),'']
        lines.extend(escape(p) for p in block.paragraphs)
        if block.rows:
            lines+=['|'+'|'.join(escape(h) for h in block.headers)+'|','|'+'|'.join('---' for _ in block.headers)+'|']
            lines.extend('|'+'|'.join(escape(cell).replace('\n','<br>') for cell in row)+'|' for row in block.rows)
    return '\n'.join(lines)+'\n'


def analyze_report(report,client):
    from matknow.model_tasks import complete_task
    original=deepcopy(report)
    original.blocks=[block for block in original.blocks if block.heading not in ('AI 分析草稿','AI 分析状态')]
    headings={block.heading for block in original.blocks}
    def validate(data):
        if not isinstance(data,dict) or set(data)!={'discussion','next_steps','uncertainties','source_sections'}:raise ValueError('报告分析结构无效。')
        for name in ('discussion','next_steps'):
            if not isinstance(data[name],str) or not 1<=len(data[name].strip())<=5000:raise ValueError('分析正文无效。')
        if not isinstance(data['uncertainties'],list) or len(data['uncertainties'])>30 or any(not isinstance(x,str) or len(x)>500 for x in data['uncertainties']):raise ValueError('待核对项无效。')
        if not isinstance(data['source_sections'],list) or not data['source_sections'] or any(not isinstance(x,str) or x not in headings for x in data['source_sections']):raise ValueError('分析依据的章节无效。')
        return data
    result=complete_task(client,'experiment','report_analysis',{'report':report_markdown(original)},validate)
    original.blocks.append(ReportBlock('AI 分析草稿',['模型解释与建议，待核对；不替代原始记录。','依据章节：'+'、'.join(result['source_sections']),
        '分析与局限：\n'+result['discussion'],'后续建议：\n'+result['next_steps'],*['待核对：'+item for item in result['uncertainties']]]))
    return original


def export_report(report,target):
    target=Path(target)
    if target.suffix.lower() not in ('.md','.docx'):raise ValueError('报告请选择 Word (.docx) 或 Markdown (.md)。')
    protect_storage(report.database,target)
    with atomic_target(target) as temporary:
        if target.suffix.lower()=='.md':temporary.write_text(report_markdown(report),encoding='utf8')
        else:_write_docx(report,temporary)


def _write_docx(report,target):
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor
    document=Document();section=document.sections[0]
    section.page_width=Inches(8.5);section.page_height=Inches(11)
    section.top_margin=section.bottom_margin=Inches(.75);section.left_margin=section.right_margin=Inches(.8)
    for name,size in [('Normal',11),('Title',18),('Heading 1',13)]:
        style=document.styles[name];style.font.name='宋体';style.font.size=Pt(size);style.font.color.rgb=RGBColor(0,0,0)
        style.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'宋体')
        style.paragraph_format.space_after=Pt(7);style.paragraph_format.line_spacing=1.15
    document.add_paragraph(report.title,'Title')
    document.add_paragraph(report.summary)
    document.add_paragraph('\n'.join(name+'：'+value for name,value in report.metadata))
    weights={'序号':.6,'指标或观察':1.6,'记录值':1.5,'方法与测试条件':3.2,'来源':2.5,'名称':2,'计划用量':1.3,'记录用量':1.3,'规格或浓度':1.8,'来源或批号':2,'对应样品':1.2,
             '文件':2.4,'样品或范围':1.2,'用途或定位':2.4,'原件状态':1.5,'条件':1.6}
    for block in report.blocks:
        document.add_paragraph(block.heading,'Heading 1')
        for paragraph in block.paragraphs:document.add_paragraph(paragraph)
        if not block.rows:continue
        table=document.add_table(rows=1,cols=len(block.headers));table.alignment=WD_TABLE_ALIGNMENT.CENTER;table.autofit=False
        table.style='Table Grid'
        total=sum(weights.get(h,2.5) for h in block.headers)
        widths=[Inches(6.9*weights.get(h,2.5)/total) for h in block.headers]
        for column,width in zip(table.columns,widths):column.width=width
        repeat=OxmlElement('w:tblHeader');table.rows[0]._tr.get_or_add_trPr().append(repeat)
        for index,values in enumerate([block.headers,*block.rows]):
            row=table.rows[0] if index==0 else table.add_row()
            for column,text in enumerate(values):
                cell=row.cells[column];cell.width=widths[column];cell.text=text;cell.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
                borders=OxmlElement('w:tcBorders')
                for edge in ('top','left','bottom','right'):
                    border=OxmlElement('w:'+edge);border.set(qn('w:val'),'single');border.set(qn('w:sz'),'4');border.set(qn('w:color'),'D9D9D9');borders.append(border)
                cell._tc.get_or_add_tcPr().append(borders)
                if index==0:
                    fill=OxmlElement('w:shd');fill.set(qn('w:fill'),'E8EFF4');cell._tc.get_or_add_tcPr().append(fill)
                for paragraph in cell.paragraphs:
                    paragraph.paragraph_format.space_after=Pt(4);paragraph.paragraph_format.space_before=Pt(4)
                    paragraph.paragraph_format.keep_together=False
                    if block.headers[column] in ('序号','计划用量','记录用量','记录值','对应样品','原件状态'):paragraph.alignment=WD_ALIGN_PARAGRAPH.CENTER
                    for run in paragraph.runs:run.font.size=Pt(10);run.bold=index==0
        document.add_paragraph()
    footer=section.footer.paragraphs[0];footer.alignment=WD_ALIGN_PARAGRAPH.RIGHT
    footer.add_run('第 ');page=OxmlElement('w:fldSimple');page.set(qn('w:instr'),'PAGE');footer._p.append(page);footer.add_run(' 页')
    document.core_properties.author=dict(report.metadata).get('操作者','')
    document.core_properties.last_modified_by='';document.save(target)
