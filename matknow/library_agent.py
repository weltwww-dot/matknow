"""Model-authored metadata stays separate from source documents."""
from matknow.model_tasks import complete_task
from matknow.agent_policy import load_policy,policy_path
import json
import hashlib
from matknow.model_context import valid_capacity,input_budget,estimate_tokens


def organize_document(knowledge,document_id,client):
    sources=knowledge.document_sources(document_id)
    result=organize_sources(sources[0].filename,sources,client,knowledge=knowledge,document_id=document_id)
    knowledge.save_document_metadata(document_id,result)
    return result


def organize_sources(filename,sources,client,*,knowledge=None,document_id=None,progress=None):
    allowed={s.locator for s in sources}
    def validate(data,permitted=None):
        if not isinstance(data,dict) or set(data)!={'category','summary','tags','locators'}:raise ValueError('分类结构无效。')
        if data['category'] not in ('论文','综述','SOP','方法资料','其他'):raise ValueError('分类无效。')
        if not isinstance(data['summary'],str) or not 1<=len(data['summary'].strip())<=600:raise ValueError('摘要需要1–600字符。')
        tags=data['tags'];locators=data['locators']
        if not isinstance(tags,list) or len(tags)>8 or any(not isinstance(t,str) or not 1<=len(t.strip())<=30 for t in tags):raise ValueError('标签无效。')
        if not isinstance(locators,list) or not locators or any(not isinstance(loc,str) or loc not in (allowed if permitted is None else permitted) for loc in locators):raise ValueError('摘要来源定位无效。')
        return data
    policy=load_policy(policy_path().with_name('library_agent.md'))
    capacity=valid_capacity(getattr(client,'context_window',None))
    header=[{'role':'system','content':policy.prompt},{'role':'user','content':json.dumps({'task':'classify_document','filename':filename,'sources':[],'part':1,'total_parts':len(sources)},ensure_ascii=False)}]
    budget=input_budget(capacity)-estimate_tokens(header) if capacity else None
    groups=[];group=[];size=0
    for source in sources:
        item={'locator':source.locator,'text':source.text}
        length=estimate_tokens([{'content':json.dumps(item,ensure_ascii=False)}])
        if group and budget is not None and size+length>budget:groups.append(group);group=[];size=0
        group.append(item);size+=length
    if group:groups.append(group)
    parts=[]
    for i,part in enumerate(groups):
        context={'filename':filename,'sources':part,'part':i+1,'total_parts':len(groups)}
        key=hashlib.sha256((policy.sha256+json.dumps(context,ensure_ascii=False)).encode('utf8')).hexdigest()
        permitted={s['locator'] for s in part}
        candidate=knowledge.document_part(document_id,key) if knowledge is not None and len(groups)>1 else None
        try:candidate=validate(candidate,permitted)
        except (ValueError,TypeError,KeyError):candidate=None
        if candidate is None:
            if progress:progress(f'整理 {filename}：第{i+1}/{len(groups)}段')
            candidate=complete_task(client,'library','classify_document',context,lambda data:validate(data,permitted))
            if knowledge is not None and len(groups)>1:knowledge.document_part(document_id,key,candidate)
        parts.append(candidate)
    if len(parts)==1:return parts[0]
    return complete_task(client,'library','merge_document_metadata',{'filename':filename,'parts':parts,'coverage':'全部原文分段已读取，合并摘要并保留对应定位。'},validate)
