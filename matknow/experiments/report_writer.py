"""Model-authored narrative with a separate evidence review and untouched raw appendix."""
from copy import deepcopy

from matknow.llm import ModelError
from matknow.model_tasks import complete_task
from .report import ReportBlock


SECTIONS=['实验目的与依据','材料与制备条件','操作过程与观察','样品与测试结果','讨论与局限','结论与下一步','缺失信息与待核对项']


def report_sources(report,archive):
    sources=[{'id':f'R{i}','locator':b.heading,'text':'\n'.join(b.paragraphs),
              'headers':b.headers,'rows':b.rows,'role':'指定版本记录'} for i,b in enumerate(report.blocks,1)]
    warnings=[]
    owner=dict(report.metadata)['档案编号']
    for token in report.attachment_ids:
        filename='关联附件'
        try:
            item=archive.attachment(token);filename=item.filename
            if item.experiment_id!=owner:raise ValueError('附件不属于当前实验。')
            path=archive.path_for(token);suffix=path.suffix.lower()
            if suffix in ('.docx','.xlsx'):
                from .readers import read_record
                document=read_record(path,file_id=token)
                parts=[b.locator+'：'+b.text for b in document.blocks]
                for table in document.tables:
                    parts.extend(_table_text(table))
                warnings.extend(filename+'：'+w for w in document.warnings)
            elif suffix=='.csv':
                from .readers import read_table
                parts=_table_text(read_table(path,file_id=token))
            elif suffix in ('.txt','.md','.pdf'):
                from matknow.documents import parse_file
                parts=[c.locator+'：'+c.text for c in parse_file(path)]
            else:
                raise ValueError('仅登记原件，未解读图片、曲线图或此格式内容。')
            text='\n'.join(parts)
            if not text.strip():raise ValueError('未提取到可读正文。')
        except (ValueError,OSError):
            # Parser messages may contain paths; only emit our filename and a generic status.
            text='未读取附件内容，原件缺失、校验失败或格式不支持；不能据此判断图像与测量结果。'
            warnings.append(filename+'：'+text)
        sources.append({'id':'F'+str(len(sources)+1),'attachment_id':token,'links':report.attachment_links.get(token,[]),
                        'filename':filename,'locator':'附件 '+token+' / 文件内定位见正文',
                        'text':text,'role':'原件背景，可能包含其他批次；不是本实验新增实测记录'})
    return sources,list(dict.fromkeys(warnings))


def _table_text(table):
    from openpyxl.utils.cell import get_column_letter
    return [f'{table.name}!{get_column_letter(c.column)}{c.row}：'+
            (f'公式 {c.formula}；缓存值 {c.cached if c.cached is not None else "缺失"}（未重算，待核对）' if c.formula else c.text)
            for c in table.cells]


def generate_report(archive,experiment_id,*,revision,profile_name,client=None,progress=None):
    from .report import build_report
    report=build_report(archive,experiment_id,profile_name=profile_name,revision=revision)
    if client is None:return report
    try:return write_report(report,client,archive=archive,progress=progress)
    except (ModelError,ValueError) as exc:
        report.original=deepcopy(report)
        report.warnings.append('AI 完整报告未完成，当前仅为原始记录整理稿。')
        report.blocks.append(ReportBlock('报告生成状态',[str(exc),'原始记录已保留，请处理上述问题后重试。']))
        return report


def write_report(report,client,*,archive,progress=None):
    from matknow.agent_policy import load_policy,policy_path
    policy=load_policy(policy_path().with_name('experiment_agent.md'))
    if any('task='+task not in policy.prompt for task in ('write_report','review_report')):
        raise ModelError('当前实验规则缺少完整报告协议，请将新版 experiment_agent.md 的 write_report 和 review_report 规则补入自定义文件后重试。')
    original=deepcopy(report.original if report.original is not None else report)
    sources,warnings=report_sources(original,archive)
    ids={s['id'] for s in sources};record_ids={s['id'] for s in sources if s['id'].startswith('R')}
    def notify(text):
        if progress:progress(text)
    def references(value):
        if not isinstance(value,list) or not value or any(not isinstance(s,str) or s not in ids for s in value):
            raise ValueError('引用必须来自本次真实来源 ID。')
        return set(value)
    def strings(value):
        if not isinstance(value,list) or len(value)>50 or any(not isinstance(s,str) or not 1<=len(s.strip())<=1000 for s in value):
            raise ValueError('待核对项结构无效。')
    def validate_draft(data):
        if not isinstance(data,dict) or set(data)!={'sections','uncertainties'}:raise ValueError('完整报告结构无效。')
        sections=data['sections'];seen=set()
        if not isinstance(sections,list) or len(sections)!=len(SECTIONS):raise ValueError('报告须覆盖全部必需章节。')
        for section,heading in zip(sections,SECTIONS):
            if not isinstance(section,dict) or set(section)!={'heading','text','source_ids'} or section['heading']!=heading:
                raise ValueError('每个章节必须仅含 heading、text、source_ids；heading 须按 required_sections 原样依序填写，不能省略 source_ids。')
            if not isinstance(section['text'],str) or not 1<=len(section['text'].strip())<=7000:raise ValueError('报告正文为空或过长。')
            seen.update(references(section['source_ids']))
        if not record_ids<=seen:raise ValueError('报告遗漏了这些原记录来源：'+', '.join(sorted(record_ids-seen))+'。请在相关章节交代其内容并加入 source_ids，包括原件索引和缺项。')
        strings(data['uncertainties'])
        return data
    context={'metadata':original.metadata,'sources':sources,'required_sections':SECTIONS,'required_source_ids':sorted(record_ids),'reading_warnings':warnings}
    notify('模型正在撰写完整实验报告')
    draft=complete_task(client,'experiment','write_report',context,validate_draft)
    def validate_review(data):
        if not isinstance(data,dict) or set(data)!={'approved','issues','checked_source_ids'} or type(data['approved']) is not bool:
            raise ValueError('报告复核结构无效。')
        strings(data['issues'])
        if references(data['checked_source_ids'])!=ids:raise ValueError('复核未覆盖全部交付来源。')
        if data['approved'] and data['issues']:raise ValueError('存在问题时不能标记复核通过。')
        if not data['approved'] and not data['issues']:raise ValueError('复核不通过时须说明原因。')
        return data
    notify('模型正在核对报告与原始记录的一致性')
    review=complete_task(client,'experiment','review_report',{**context,'draft':draft},validate_review)
    if not review['approved']:
        notify('模型正在按复核意见修订报告')
        draft=complete_task(client,'experiment','write_report',{**context,'previous_draft':draft,'revision_issues':review['issues']},validate_draft)
        notify('模型正在复核修订后的报告')
        review=complete_task(client,'experiment','review_report',{**context,'draft':draft},validate_review)
    if not review['approved']:
        raise ModelError('报告修订后仍有待核对问题：\n'+'\n'.join('• '+item for item in review['issues'][:5]))
    result=deepcopy(original);result.original=original
    labels={s['id']:s.get('filename',s['locator'])+' / '+s['locator'] if 'filename' in s else s['locator'] for s in sources}
    result.blocks=[ReportBlock(section['heading'],[section['text'],'依据：'+'；'.join(s+' '+labels[s] for s in section['source_ids'])]) for section in draft['sections']]
    if draft['uncertainties']:result.blocks[-1].paragraphs.extend(draft['uncertainties'])
    result.blocks.append(ReportBlock('原始记录对照',['以下内容逐条保留指定版本，包括每次重复测量；模型正文有疑问时以原始记录及原件为核对依据。']))
    result.blocks.extend(deepcopy(original.blocks))
    result.warnings=list(dict.fromkeys([*original.warnings,*warnings,*draft['uncertainties']]))
    if warnings:result.blocks.append(ReportBlock('附件读取限制',warnings))
    result.summary='AI 撰写的完整报告草稿，已进行模型一致性复核；不等同于科学事实证明，须人工核对。原始记录对照附后。'
    return result
