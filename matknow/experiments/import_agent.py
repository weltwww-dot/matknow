"""Extract a full candidate record with model-discovered samples and source checks."""
from copy import deepcopy
import re

from matknow.model_tasks import complete_task
from .models import ChemistryRecord, Reagent, ResultDraft, SampleDraft, SourceRef, Value, validate_draft


def organize_record(document,locators,base,client,*,intent=None):
    validate_draft(base,confirmed=False)
    blocks={b.locator:b.text for b in document.blocks}
    if not locators or any(loc not in blocks for loc in locators):raise ValueError('请选择有效原文范围。')
    selected=list(dict.fromkeys(locators));allowed=set(selected)
    provenance=[]
    def check_source(locator):
        if not isinstance(locator,str) or locator not in allowed:raise ValueError('字段来源不在所选原文内。')
        return locator
    def text_sources(item):
        if not isinstance(item,dict) or set(item) not in ({'text','locator'},{'text','locators'}):raise ValueError('详情字段须带原文定位。')
        locations=item.get('locators')
        if locations is None:
            locator=item.get('locator')
            locations=[locator] if isinstance(locator,str) and locator in allowed else re.split('[；;]',locator) if isinstance(locator,str) else locator
        if not isinstance(locations,list) or not 1<=len(locations)<=20 or len(set(locations))!=len(locations):raise ValueError('文本来源需要1–20个不同的原文定位。')
        return '；'.join(check_source(loc.strip() if isinstance(loc,str) else loc) for loc in locations)
    def fields(items):
        if not isinstance(items,list):raise ValueError('条件列表无效。')
        values={}
        for item in items:
            if not isinstance(item,dict) or set(item)-{'name','text','unit','locator'}:raise ValueError('条件字段无效。')
            if item['name'] in values:raise ValueError('条件名称重复。')
            values[item['name']]=Value(item['text'],item.get('unit',''),'model_confirmed',SourceRef(document.file_id,check_source(item['locator'])),False)
        return values
    def validate(data):
        provenance.clear()
        if not isinstance(data,dict) or set(data)-{'title','date','record_type','chemistry','notes','common','samples','review'}:raise ValueError('草稿字段无效。')
        kind=data['record_type']
        if kind not in ('plan','record','results'):raise ValueError('实验文档类型无效。')
        if intent=='plan' and kind!='plan':raise ValueError('用户请求生成方案，须输出plan，不能把文献或预期结果当本次实测。')
        draft=deepcopy(base);draft.title=data.get('title') or base.title;draft.date=data.get('date') or base.date
        chemistry=data.get('chemistry',{})
        names={'objective','operator','setup','deviations','discussion','conclusion','references'}
        if not isinstance(chemistry,dict) or set(chemistry)-names-{'status','reagents'}:raise ValueError('化学详情字段无效。')
        record=ChemistryRecord(status=chemistry.get('status','planned' if kind=='plan' else 'unknown'))
        if kind=='plan' and record.status!='planned':raise ValueError('实验方案必须标为计划中。')
        for name in names:
            if name not in chemistry:continue
            item=chemistry[name]
            locations=text_sources(item)
            setattr(record,name,item['text']);provenance.append(name+' / '+locations)
        reagents=chemistry.get('reagents',[])
        if not isinstance(reagents,list):raise ValueError('试剂列表无效。')
        for reagent in reagents:
            if not isinstance(reagent,dict) or set(reagent)-{'name','quantity','unit','specification','source','sample_codes','locator'}:raise ValueError('试剂字段无效。')
            locator=check_source(reagent['locator']);values={key:value for key,value in reagent.items() if key!='locator'}
            record.reagents.append(Reagent(**values));provenance.append('试剂 '+str(reagent['name'])+' / '+locator)
        draft.chemistry=record;draft.common=fields(data.get('common',[]));draft.samples=[]
        samples=data['samples']
        if not isinstance(samples,list) or len(samples)>100:raise ValueError('样品列表无效或超过100个。')
        for sample in samples:
            if not isinstance(sample,dict) or set(sample)-{'code','locator','conditions','results'}:raise ValueError('样品字段无效。')
            code=sample['code'];locator=check_source(sample['locator'])
            if not isinstance(code,str) or not code.strip() or not re.search(r'(?<![A-Za-z0-9_.-])'+re.escape(code)+r'(?![A-Za-z0-9_-]|\.[A-Za-z0-9_])',blocks[locator],re.I):raise ValueError('样品编号需要能在对应原文中找到，不能截取另一个编号。')
            candidate=SampleDraft(code,fields(sample.get('conditions',[])))
            results=sample.get('results',[])
            if not isinstance(results,list):raise ValueError('结果列表无效。')
            if (kind=='plan' or record.status=='planned') and results:raise ValueError('计划和预期结果不能作为本次实测结果。')
            for result in results:
                keys={'metric','text','unit','kind','method','test_conditions','measured_at','repeat_group','notes','locator'}
                if not isinstance(result,dict) or set(result)-keys or result.get('kind','number') not in ('number','text'):raise ValueError('测量字段无效。')
                value=Value(result['text'],result.get('unit',''),'model_confirmed',SourceRef(document.file_id,check_source(result['locator'])),False)
                candidate.results.append(ResultDraft(result['metric'],value,result.get('kind','number'),result.get('method',''),result.get('test_conditions',{}),result.get('measured_at',''),result.get('repeat_group',''),result.get('notes','')))
            draft.samples.append(candidate);provenance.append('样品 '+code+' / '+locator)
        review=data.get('review',[])
        if not isinstance(review,list) or len(review)>30 or any(not isinstance(x,str) or len(x)>500 for x in review):raise ValueError('待核对事项无效。')
        review=list(review)
        if not data.get('date'):review.append('原文未提供实验日期；当前日期为录入日期，请核对。')
        if not draft.samples:
            draft.samples=[SampleDraft('待确认样品')];review.append('原文未识别到明确样品编号，请核对临时样品。')
        notes=data.get('notes');summary=''
        if notes is not None:
            locations=text_sources(notes)
            if not isinstance(notes['text'],str):raise ValueError('操作摘要须带原文定位。')
            summary='模型整理摘要（待核对） / '+locations+'\n'+notes['text']+'\n\n'
        draft.notes=summary+'所选原记录\n'+'\n\n'.join(loc+'\n'+blocks[loc] for loc in selected)
        draft.notes+='\n\n模型整理字段出处（待核对）\n'+'\n'.join(provenance)
        if review:draft.notes+='\n\n待核对\n'+'\n'.join(review)
        draft.source_ids=list(dict.fromkeys([*base.source_ids,document.file_id]))
        validate_draft(draft,confirmed=False)
        return draft
    return complete_task(client,'experiment','import_record',{'filename':document.filename,'sources':[{'locator':loc,'text':blocks[loc]} for loc in selected],
        'reading_warnings':document.warnings,'requested_type':intent},validate)


def match_record(incoming,candidates,client):
    allowed={s.id for s in candidates}
    def validate(data):
        if not isinstance(data,dict) or set(data)!={'experiment_id','reason'} or not isinstance(data['reason'],str) or len(data['reason'])>500:raise ValueError('实验关联结构无效。')
        if data['experiment_id'] is not None and (not isinstance(data['experiment_id'],str) or data['experiment_id'] not in allowed):raise ValueError('只能关联提供的当前方向实验。')
        return data['experiment_id']
    return complete_task(client,'experiment','match_record',{'incoming':{'title':incoming.title,'date':incoming.date,'notes':incoming.notes,'samples':[s.code for s in incoming.samples]},
        'candidates':[{'experiment_id':s.id,'title':s.draft.title,'date':s.draft.date,'objective':s.draft.chemistry.objective,'samples':[x.code for x in s.draft.samples]} for s in candidates]},validate)


def merge_record(previous,incoming):
    """Append confirmed import data without dropping IDs, repeats or old sample rows."""
    merged=deepcopy(previous)
    def conditions(target,added):
        for name,value in added.items():
            if name in target and (target[name].text,target[name].unit)!=(value.text,value.unit):raise ValueError(f'条件 {name} 冲突，请核对或选择新建实验。')
            if name not in target:target[name]=deepcopy(value)
    if incoming.chemistry.status=='planned' and (previous.chemistry.status in ('running','completed','aborted') or any(s.results for s in previous.samples)):raise ValueError('已有执行记录的实验不能合并为计划，请选择新建实验。')
    known={s.code.casefold():s for s in merged.samples}
    for sample in incoming.samples:
        if sample.code=='待确认样品':raise ValueError('关联已有实验前，请核对样品编号。')
        target=known.get(sample.code.casefold())
        if target is None:
            added=deepcopy(sample);conditions(added.conditions,incoming.common)
            conditions(deepcopy(previous.common),added.conditions);merged.samples.append(added);continue
        # Effective conditions must stay compatible, including inherited common fields.
        old_conditions={**previous.common,**target.conditions};new_conditions={**incoming.common,**sample.conditions}
        conditions(deepcopy(old_conditions),new_conditions)
        conditions(target.conditions,new_conditions);target.results.extend(deepcopy(sample.results))
    for name in ('objective','operator','setup','deviations','discussion','conclusion','references'):
        old,new=getattr(merged.chemistry,name),getattr(incoming.chemistry,name)
        if not old and new:setattr(merged.chemistry,name,new)
    if incoming.chemistry.reagents:
        if merged.chemistry.reagents and merged.chemistry.reagents!=incoming.chemistry.reagents:raise ValueError('试剂记录冲突，请核对或选择新建实验。')
        if not merged.chemistry.reagents:
            merged.chemistry.reagents=deepcopy(incoming.chemistry.reagents)
            if {s.code.casefold() for s in incoming.samples}!={s.code.casefold() for s in merged.samples}:
                for reagent in merged.chemistry.reagents:
                    if not reagent.sample_codes:reagent.sample_codes=[s.code for s in incoming.samples]
    if incoming.chemistry.status!='unknown':merged.chemistry.status=incoming.chemistry.status
    elif merged.chemistry.status=='planned' and any(s.results for s in incoming.samples):merged.chemistry.status='running'
    from .models import RECORD_STATUSES
    merged.notes='此前记录（'+RECORD_STATUSES[previous.chemistry.status]+'，保留原文）\n'+merged.notes
    merged.notes+='\n\n追加导入记录：'+incoming.title+'（'+RECORD_STATUSES[incoming.chemistry.status]+'）\n'+incoming.notes
    merged.source_ids=list(dict.fromkeys([*merged.source_ids,*incoming.source_ids]))
    validate_draft(merged,confirmed=False)
    return merged
