"""Atomic exports of selected revisions and live historical citations."""
from contextlib import contextmanager
from dataclasses import asdict
import html
import json
import os
from pathlib import Path
import re
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED
from matknow.agent import execution_lines


def escape(text):
    return re.sub(r'([\\`*_{}\[\]#|])', r'\\\1', html.escape(str(text), quote=False))


@contextmanager
def atomic_target(target):
    target=Path(target)
    temporary=target.with_name('.'+target.name+'.'+str(uuid4())+'.tmp')
    try:
        # Never silently create destination directories or replace another temporary file.
        with temporary.open('xb'):pass
        yield temporary
        os.replace(temporary,target)
    finally:temporary.unlink(missing_ok=True)


def protect_archive(archive,target):
    protect_storage(archive.store.knowledge.path,target)


def protect_storage(database,target):
    target=Path(target).resolve();database=Path(database).resolve()
    if target.is_relative_to((database.parent/'experiment-files').resolve()) or str(target) in {str(database),str(database)+'-wal',str(database)+'-shm'}:
        raise ValueError('请选择实验存储目录之外的导出位置。')


def export_comparison(comparison,target,*,profile_name):
    database=getattr(comparison,'_database_path',None)
    if database is None:raise ValueError('请重新生成样品对比后导出。')
    protect_storage(database,target)
    lines=['# 材知样品对比','',f'研究方向：{escape(profile_name)}',escape(comparison.scope),'',
           '## 制备条件','']
    labels=comparison.labels
    for condition in comparison.conditions:
        lines.append(f'- {escape(condition["condition_name"])}（{escape(condition["status"])}）')
        for sample_id in comparison.sample_ids:
            value=condition['values'][sample_id]
            lines.append(f'  - {escape(labels.get(sample_id,sample_id))}：'+(f'{escape(value.text)} {escape(value.unit)}' if value else '未记录'))
            if value:
                from .models import value_provenance
                lines.append('    '+escape(value_provenance(value)))
    lines.extend(['','## 全部已选测量',''])
    for group in comparison.groups:
        lines.extend([f'### {escape(group["metric"])} / {escape(group["unit"])}',
                      f'方法：{escape(group["method"] or "未记录")}；测试条件：{escape(json.dumps(group["test_conditions"],ensure_ascii=False))}',
                      '状态：'+('待核对' if group['status']=='needs_confirmation' else '可比较'),''])
        for row in group['measurements']:
            value=row['value']
            lines.append(f'- {escape(row["label"])} · 版本 {row["revision"]} · {escape(value.text)} {escape(value.unit)} · {escape(row["measured_at"] or "时间未记录")}')
            lines.append(f'  来源：{escape(row["source_id"])}'+(f' · {escape(value.source.locator)}' if value.source else ' · 手动录入'))
            lines.append(f'  结果编号：{row["result_id"]}；备注：{escape(row["notes"])}')
    lines.extend(['','## 核对事项','',*[escape(w) for w in comparison.warnings],
                  '重复测量逐条保留；未进行单位换算、自动选取代表值或因果推断。'])
    if comparison.calculation:
        from .compare import rank_measurements
        rows=rank_measurements(comparison,**comparison.calculation)
        lines.extend(['','## 本次明确选择的计算','',f'排序：'+('降序' if comparison.calculation['descending'] else '升序'),
                      '参照结果编号：'+str(comparison.calculation.get('reference_result_id') or '无')])
        for n,row in enumerate(rows,1):
            lines.append(f'{n}. {escape(row["label"])} · {escape(row["value"].text)} {escape(row["value"].unit)}'+(f' · 差值 {row["difference"]}' if row['difference'] is not None else '')+f' · 来源 {escape(row["source_id"])}')
    with atomic_target(target) as temporary:temporary.write_text('\n'.join(lines),encoding='utf8')


def export_experiment(archive,experiment_id,target,*,profile_name,revision=None):
    protect_archive(archive,target)
    snapshot=archive.store.get(experiment_id,revision)
    draft=snapshot.draft;ids=set(draft.source_ids)
    values=list(draft.common.values())
    for sample in draft.samples:
        values.extend(sample.conditions.values())
        for result in sample.results:
            values.append(result.value);ids.update(result.attachment_ids)
            if result.data.get('attachment_id'):ids.add(result.data['attachment_id'])
    ids.update(v.source.file_id for v in values if v.source)
    manifest={'format':'matknow-experiment-v1','profile':profile_name,'experiment_id':snapshot.id,
              'revision':snapshot.revision,'created_at':snapshot.created_at,'draft':asdict(draft),'attachments':[]}
    with atomic_target(target) as temporary:
        with ZipFile(temporary,'w',ZIP_DEFLATED) as output:
            for file_id in sorted(ids):
                attachment=archive.attachment(file_id)
                if attachment.experiment_id!=snapshot.id:raise ValueError('附件不属于当前实验。')
                path=archive.path_for(file_id)
                name=f'files/{file_id}/original{path.suffix}'
                output.write(path,name)
                manifest['attachments'].append({'id':file_id,'filename':attachment.filename,'sha256':attachment.sha256,
                                                'size':attachment.size,'role':attachment.role,'member':name})
            output.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2,allow_nan=False))


def export_answer(answer,knowledge,target,*,profile_name):
    protect_storage(knowledge.path,target)
    live=knowledge.get_evidence_many([s.id for s in answer.sources])
    missing=answer.unavailable_sources+sum(s.id not in live for s in answer.sources)
    lines=['# 材知问答记录','',f'研究方向：{escape(profile_name)}',f'状态：{answer.status}',
           f'时间：{answer.created_at}','',f'问题：{escape(answer.question)}','',escape(answer.answer),'',
           '范围：本次引用的资料与实验记录；完整计算以所选样品及对应工具记录为准。','',
           *[line[:2]+escape(line[2:]) if line.startswith('- ') else line for line in execution_lines(answer)], '## 来源片段','']
    if missing:lines.extend([f'{missing} 个历史来源已删除，回答需要重新核对。',''])
    for number,source in enumerate(answer.sources,1):
        evidence=live.get(source.id)
        if evidence:
            lines.extend([f'### [{source.citation_number or number}] {escape(evidence.filename)} · {escape(evidence.locator)}','',escape(evidence.text),''])
        else:lines.extend([f'[{source.citation_number or number}] 来源已删除。',''])
    if any(s.id.startswith('exp:') for s in answer.sources):
        lines.append('图片及未解析附件仅作为原始文件保存；本问答不代表对其内容的自动解读。')
    with atomic_target(target) as temporary:temporary.write_text('\n'.join(lines),encoding='utf8')
