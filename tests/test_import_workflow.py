import json
import pytest
from zipfile import ZipFile

from matknow.agent_tools import ResearchTools
from matknow.knowledge import KnowledgeBase
from matknow.library_agent import organize_sources
from matknow.models import Chunk
from matknow.model_context import fits_context
from tests.test_chat_routing import Model


def classification(context):
    sources=context.get('sources',[])
    locators=[s['locator'] for s in sources] if sources else [loc for part in context['parts'] for loc in part['locators']]
    return {'category':'论文','summary':'材料制备与测试方法。','tags':['制备'],'locators':locators}


def test_long_document_classification_covers_all_original_chunks_without_truncation():
    sources=[Chunk(f'UNIQUE_{i:03d} '+('材料条件记录。'*100),f'段落{i}') for i in range(90)]
    model=Model(*[classification]*12);model.context_window=16384
    result=organize_sources('长论文.txt',sources,model)
    payloads=[json.loads(messages[-1]['content']) for messages in model.messages]
    read={s['locator'] for p in payloads for s in p.get('sources',[])}
    assert read=={f'段落{i}' for i in range(90)}
    assert result['summary'] and set(result['locators'])==read
    assert all(fits_context(messages,model.context_window) for messages in model.messages)


def test_bad_metadata_for_one_file_does_not_disable_next_file(tmp_path):
    kb=KnowledgeBase(tmp_path/'app-data/kb.sqlite3');archive=tmp_path/'inbox.zip'
    with ZipFile(archive,'w') as z:
        z.writestr('P004/bad.txt','不易归类的原始文档。');z.writestr('P005/good.txt','第二篇完整制备方法。')
    def classify(context):
        if context['filename']=='bad.txt':return {'category':'invalid'}
        return classification(context)
    tools=ResearchTools(kb,file_paths=[str(archive)],model_client=Model(*[classify]*5))
    result=tools.call('read_file',{'path':str(archive)})
    good=next(d for d in kb.list_documents() if d.filename=='good.txt')
    assert good.category=='论文' and good.summary
    assert len(result['files'])==2 and len(kb.list_documents())==2


def test_successful_import_is_reused_across_questions_without_reclassifying(tmp_path):
    kb=KnowledgeBase(tmp_path/'app-data/kb.sqlite3');path=tmp_path/'paper.txt';path.write_text('原文不变。',encoding='utf8')
    first=ResearchTools(kb,file_paths=[str(path)],model_client=Model(classification)).call('read_file',{'path':str(path)})
    second=ResearchTools(kb,file_paths=[str(path)],model_client=Model(AssertionError('Already organized'))).call('read_file',{'path':str(path)})
    assert second['document_id']==first['document_id'] and not second['warnings']
    assert len(kb.list_documents())==1 and kb.list_documents()[0].category=='论文'


def test_zip_origin_groups_are_available_in_the_document_catalog(tmp_path):
    kb=KnowledgeBase(tmp_path/'app-data/kb.sqlite3');archive=tmp_path/'inbox.zip'
    with ZipFile(archive,'w') as z:
        z.writestr('inbox/P004/paper.txt','第一篇论文。');z.writestr('inbox/P005/paper.txt','第二篇论文。')
    tools=ResearchTools(kb,file_paths=[str(archive)],model_client=Model(classification,classification))
    tools.call('read_file',{'path':str(archive)})
    rows=tools.call('list_documents',{})['documents']
    assert {r.get('group') for r in rows}=={'P004','P005'}
    assert {r.get('member') for r in rows}=={'inbox/P004/paper.txt','inbox/P005/paper.txt'}


def test_identical_zip_originals_reuse_one_document_and_keep_both_origins(tmp_path):
    kb=KnowledgeBase(tmp_path/'app-data/kb.sqlite3');archive=tmp_path/'inbox.zip'
    with ZipFile(archive,'w') as z:
        z.writestr('inbox/P004/supplementary/a.txt','同一份补充材料原件。')
        z.writestr('inbox/P004/extracted/a.txt','同一份补充材料原件。')
    tools=ResearchTools(kb,file_paths=[str(archive)],model_client=Model(classification,classification))
    result=tools.call('read_file',{'path':str(archive)})
    assert len(kb.list_documents())==1
    document_id=result['files'][0]['document_id']
    assert result['files'][1]['document_id']==document_id
    assert len(kb.document_origins(document_id))==2


def test_import_receipts_do_not_fill_the_model_context_with_fulltext(tmp_path):
    kb=KnowledgeBase(tmp_path/'app-data/kb.sqlite3');archive=tmp_path/'inbox.zip'
    with ZipFile(archive,'w') as z:
        for i in range(24):z.writestr(f'P{i:03d}/paper.txt',f'论文{i} '+('文字'*600))
    tools=ResearchTools(kb,file_paths=[str(archive)],model_client=Model(*[classification]*30))
    for offset in (0,8,16):tools.call('read_file',{'path':str(archive),'offset':offset,'limit':8})
    assert len(kb.list_documents())==24
    assert sum(len(s['text']) for s in tools.context())<=3000


def test_document_catalog_last_page_clearly_has_no_more_items(tmp_path):
    kb=KnowledgeBase(tmp_path/'kb.sqlite3')
    for i in range(27):kb.import_text(str(i),f'资料{i}')
    tools=ResearchTools(kb);first=tools.call('list_documents',{});last=tools.call('list_documents',{'offset':20})
    assert first.get('has_more') is True and last.get('has_more') is False
    assert last['next_offset'] is None and len(first['documents'])+len(last['documents'])==27
    assert first['next_page']=={'query':'','offset':20} and last['next_page'] is None
    tools.call('list_documents',{'query':'不存在'})
    assert tools.catalog_pages['']['page_end']==27


def test_failed_turn_keeps_user_file_authorization_but_never_assistant_paths(qtbot,tmp_path):
    from tests.test_desktop import make_profile_window
    from matknow.models import Answer
    from matknow.conversations import scope_key
    _,_,window=make_profile_window(qtbot,tmp_path/'app-data')
    original=tmp_path/'source.txt';other=tmp_path/'assistant.txt'
    answer=Answer('failed','读取 '+str(original),'请读取 '+str(other),'insufficient_evidence','now')
    window.conversation=[answer];window._turn_scopes={answer.id:scope_key()}
    context=window._conversation_context(scope_key())
    model=Model({'mode':'research','workspace':'documents'},lambda c:{'answer':'缺少原文','status':'insufficient_evidence','source_ids':[]})
    from matknow.agent import ResearchAgent
    ResearchAgent(window.knowledge,model).respond('继续',conversation=context)
    routing=json.loads(model.messages[0][-1]['content'])
    assert routing['file_paths']==[str(original)]
    assert str(other) not in json.dumps(routing,ensure_ascii=False)
    assert window._conversation_context(scope_key(['another']))[0]['question']==answer.question
    assert window._conversation_file_paths(scope_key(['another']))==[]


def test_partial_import_can_continue_through_the_same_chat(qtbot,tmp_path,monkeypatch):
    from tests.test_desktop import make_profile_window
    from matknow.llm import DeepSeekClient,ModelError
    from tests.test_agent import final
    _,credentials,window=make_profile_window(qtbot,tmp_path/'app-data');credentials.key='FAKE-only'
    source=tmp_path/'paper.txt';source.write_text('干燥60 °C。',encoding='utf8')
    first=True
    def complete(self,messages,**kwargs):
        nonlocal first
        c=json.loads(messages[-1]['content'])
        if c.get('task'):return json.dumps(classification(c))
        if 'tools' not in c:return json.dumps({'mode':'research','workspace':'documents'})
        if not c.get('observation',{}).get('result',{}).get('document_id'):
            return json.dumps({'action':{'tool':'read_file','arguments':{'path':c['file_paths'][0]}}})
        if first:first=False;raise ModelError('连接中断')
        return json.dumps(final(c))
    monkeypatch.setattr(DeepSeekClient,'complete',complete)
    window.question_edit.setPlainText('读取 '+str(source));window.ask();qtbot.waitUntil(lambda:not window.busy)
    assert window.last_answer.status=='model_error' and window.document_list.count()==1
    window.question_edit.setPlainText('继续');window.ask();qtbot.waitUntil(lambda:not window.busy)
    assert window.last_answer.status=='generated' and window.document_list.count()==1


def test_file_authorization_survives_a_long_same_scope_conversation(qtbot,tmp_path):
    from tests.test_desktop import make_profile_window
    from matknow.models import Answer
    from matknow.conversations import scope_key
    from matknow.agent import ResearchAgent
    _,_,window=make_profile_window(qtbot,tmp_path/'app-data');path=tmp_path/'paper.txt'
    window.conversation=[Answer('original','读取 '+str(path),'','model_error','now')]+[
        Answer(str(i),'追问'+str(i),'普通回答','chat','now') for i in range(9)]
    window._turn_scopes={a.id:scope_key() for a in window.conversation}
    assert window._conversation_file_paths(scope_key())==[str(path)]
    model=Model({'mode':'research','workspace':'documents'},{'answer':'文件尚不可读','source_ids':[],'status':'insufficient_evidence'})
    ResearchAgent(window.knowledge,model).respond('继续',conversation=window._conversation_context(scope_key()),user_file_paths=window._conversation_file_paths(scope_key()))
    assert json.loads(model.messages[0][-1]['content'])['file_paths']==[str(path)]
    assert window._conversation_file_paths(scope_key(['other']))==[]


def test_renaming_archive_does_not_merge_paper_groups(tmp_path):
    kb=KnowledgeBase(tmp_path/'app-data/kb.sqlite3');path=tmp_path/'renamed.zip'
    with ZipFile(path,'w') as z:
        z.writestr('inbox/P004/paper.txt','第一篇正文。');z.writestr('inbox/P005/paper.txt','第二篇正文。')
    ResearchTools(kb,file_paths=[str(path)]).call('read_file',{'path':str(path)})
    assert {d.group for d in kb.list_documents()}=={'P004','P005'}


def test_long_document_resumes_successful_parts_after_connection_failure(tmp_path):
    import pytest
    from matknow.llm import ModelError
    from matknow.library_agent import organize_document
    kb=KnowledgeBase(tmp_path/'kb.sqlite3')
    chunks=[Chunk(f'段{i} '+('材料条件记录。'*100),f'段落{i}') for i in range(90)]
    doc=kb._save_document('long.txt',chunks)
    first=Model(classification,ModelError('连接中断'))
    first.context_window=16384
    with pytest.raises(ModelError):organize_document(kb,doc.id,first)
    finished={s['locator'] for s in json.loads(first.messages[0][-1]['content'])['sources']}
    resumed=Model(*[classification]*10);resumed.context_window=16384;organize_document(kb,doc.id,resumed)
    reread={s['locator'] for m in resumed.messages for s in json.loads(m[-1]['content']).get('sources',[])}
    assert not finished.intersection(reread)
    assert kb.list_documents()[0].summary


@pytest.mark.parametrize('first,second',[('supplementary','extracted'),('main','supplements')])
def test_single_paper_archive_keeps_its_real_root_and_deduplicates_supplements(tmp_path,first,second):
    kb=KnowledgeBase(tmp_path/'app-data/kb.sqlite3');path=tmp_path/'single.zip'
    with ZipFile(path,'w') as z:
        z.writestr(f'P004/{first}/a.txt','同一份原文。');z.writestr(f'P004/{second}/a.txt','同一份原文。')
    ResearchTools(kb,file_paths=[str(path)]).call('read_file',{'path':str(path)})
    assert len(kb.list_documents())==1 and kb.list_documents()[0].group=='P004'


def test_different_original_bytes_are_not_deduplicated_only_by_extracted_text(tmp_path):
    kb=KnowledgeBase(tmp_path/'app-data/kb.sqlite3');path=tmp_path/'inbox.zip'
    with ZipFile(path,'w') as z:
        z.writestr('inbox/P004/a/paper.txt','相同正文。'.encode('utf8'))
        z.writestr('inbox/P004/b/paper.txt',b'\xef\xbb\xbf'+'相同正文。'.encode('utf8'))
    ResearchTools(kb,file_paths=[str(path)]).call('read_file',{'path':str(path)})
    assert len(kb.list_documents())==2


def test_standalone_original_identity_survives_same_name_and_same_extracted_text(tmp_path):
    kb=KnowledgeBase(tmp_path/'app-data/kb.sqlite3')
    for directory,data in [('a','同一段原文。'.encode('utf8')),('b',b'\xef\xbb\xbf'+'同一段原文。'.encode('utf8'))]:
        path=tmp_path/directory/'paper.txt';path.parent.mkdir();path.write_bytes(data)
        ResearchTools(kb,file_paths=[str(path)]).call('read_file',{'path':str(path)})
    assert len(kb.list_documents())==2


def test_catalog_search_matches_group_and_member_with_multiple_terms(tmp_path):
    kb=KnowledgeBase(tmp_path/'app-data/kb.sqlite3');archive=tmp_path/'inbox.zip'
    with ZipFile(archive,'w') as z:
        z.writestr('inbox/P004/methods_only.txt','制备方法。')
        z.writestr('inbox/P005/supplementary/paper.txt','补充材料。')
    tools=ResearchTools(kb,file_paths=[str(archive)])
    tools.call('read_file',{'path':str(archive)})
    assert tools.call('list_documents',{'query':'P004'})['total']==1
    assert tools.call('list_documents',{'query':'P004 methods_only'})['total']==1
    assert tools.call('list_documents',{'query':'P004 P005'})['total']==2
    assert tools.call('list_documents',{'query':'supplementary'})['total']==1
    kb.import_text('P009.txt','独立导入的旧资料。')
    kb.import_text('P0091.txt','另一编号。')
    assert tools.call('list_documents',{'query':'P009'})['total']==1


def test_catalog_ids_survive_later_tool_observations(tmp_path):
    from tests.test_agent import Script,final
    from matknow.agent import ResearchAgent
    kb=KnowledgeBase(tmp_path/'kb.sqlite3');doc=kb.import_text('方法','干燥60 °C。')
    def use_catalog(c):
        assert c['document_catalog'][0]['document_id']==doc.id
        return final(c)
    model=Script({'action':{'tool':'list_documents','arguments':{}}},
                 {'action':{'tool':'search_documents','arguments':{'query':'干燥60'}}},use_catalog)
    assert ResearchAgent(kb,model).ask('干燥').status=='generated'
