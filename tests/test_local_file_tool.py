import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from docx import Document

from matknow.agent import ResearchAgent
from matknow.agent_tools import ResearchTools
from matknow.knowledge import KnowledgeBase
from tests.test_agent import Script,final
from tests.test_chat_routing import Model
from matknow.llm import ModelError


@pytest.fixture
def files(tmp_path):
    kb=KnowledgeBase(tmp_path/'app-data'/'knowledge.sqlite3')
    source=tmp_path/'材料 制备.txt';source.write_text('A1 实际干燥温度 60 °C。\n\nA2 尚无测量。',encoding='utf8')
    return kb,source


def test_read_path_preserves_source_and_restores_imported_citations(files):
    kb,path=files
    tools=ResearchTools(kb,file_paths=[str(path)])
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    result=tools.call('read_file',{'path':str(path),'limit':1})
    assert result['complete'] is False and result['next_offset']==1
    assert '60' in tools.context()[0]['text'] and result['document_id']==tools.sources[0].document_id
    tools.ensure_current()
    source=tools.sources[0]
    reopened=KnowledgeBase(kb.path).get_evidence(source.id)
    assert reopened.text==source.text and reopened.filename==path.name
    assert len(kb.list_documents())==1 and hashlib.sha256(path.read_bytes()).hexdigest()==before
    assert set(path.parent.iterdir())=={path,kb.path.parent}


def test_file_permissions_only_accept_user_paths_and_reject_app_storage(files,tmp_path):
    kb,path=files;tools=ResearchTools(kb,file_paths=[str(path)])
    other=tmp_path/'other.txt';other.write_text('PRIVATE')
    with pytest.raises(ValueError,match='指定|授权'):tools.call('read_file',{'path':str(other)})
    secret=kb.path.parent/'private.txt';secret.write_text('PRIVATE_KEY')
    with pytest.raises(ValueError):ResearchTools(kb,file_paths=[str(secret)]).call('read_file',{'path':str(secret)})
    with pytest.raises(ValueError):tools.call('read_file',{'path':'https://example.com/private.txt'})
    with pytest.raises(ValueError):tools.call('read_file',{'path':str(path),'write':'new'})
    assert not tools.sources


def test_zip_lists_and_reads_office_member_in_memory(files,tmp_path):
    kb,path=files;document=Document();document.add_paragraph('样品 A1，原料 0.50 g。')
    import io
    stream=io.BytesIO();document.save(stream)
    archive=tmp_path/'inbox.zip'
    with ZipFile(archive,'w') as packed:
        packed.writestr('records/制备.docx',stream.getvalue());packed.writestr('SEM.png',b'not interpreted')
    before=set(tmp_path.iterdir());tools=ResearchTools(kb,file_paths=[str(archive)])
    listing=tools.call('read_file',{'path':str(archive)})
    assert listing['kind']=='archive' and listing['complete']
    assert {f['name'] for f in listing['files']}=={'records/制备.docx'}
    assert listing['skipped_count']==1
    result=tools.call('read_file',{'path':str(archive),'member':'records/制备.docx'})
    assert result['complete'] and '0.50' in tools.context()[0]['text']
    assert tools.sources[0].filename=='制备.docx' and 'records/制备.docx' in tools.sources[0].locator
    assert set(tmp_path.iterdir())==before and len(kb.list_documents())==1
    assert kb.get_evidence(tools.sources[0].id).text==tools.sources[0].text
    with pytest.raises(ValueError):tools.call('read_file',{'path':str(archive),'member':'SEM.png'})


@pytest.mark.parametrize('member',['../outside.txt','C:/outside.txt','/outside.txt','a/../../outside.txt','a\\..\\outside.txt'])
def test_zip_traversal_never_extracts_or_reads_outside(files,tmp_path,member):
    kb,path=files;archive=tmp_path/'unsafe.zip'
    with ZipFile(archive,'w') as packed:packed.writestr(member,'PRIVATE')
    with pytest.raises(ValueError):ResearchTools(kb,file_paths=[str(archive)]).call('read_file',{'path':str(archive)})
    assert not (tmp_path.parent/'outside.txt').exists()


def test_source_change_stops_current_task_but_keeps_imported_snapshot(files):
    kb,path=files;tools=ResearchTools(kb,file_paths=[str(path)]);tools.call('read_file',{'path':str(path)})
    source=tools.sources[0];path.write_text('changed',encoding='utf8')
    assert kb.get_evidence(source.id).text==source.text
    with pytest.raises(ValueError,match='变更|更新|删除'):tools.ensure_current()
    path.unlink();assert kb.get_evidence(source.id).text==source.text
    kb.delete_document(source.document_id);assert kb.get_evidence(source.id) is None


@pytest.mark.parametrize('workspace',['documents','experiments'])
def test_original_user_path_is_available_to_agent_and_not_granted_by_model_rewrite(files,tmp_path,workspace):
    kb,path=files;other=tmp_path/'not-authorized.txt';other.write_text('PRIVATE')
    model=Model({'mode':'research','workspace':workspace,'resolved_question':'总结文件'},
        {'plan':['读取指定文件','核对引用'],'action':{'tool':'read_file','arguments':{'path':str(path)}}},
        lambda c:{'category':'方法资料','summary':'材料干燥记录。','tags':['干燥'],'locators':[c['sources'][0]['locator']]},final)
    answer=ResearchAgent(kb,model).respond('请读取并总结 "'+str(path)+'"')
    assert answer.status=='generated' and answer.sources
    context=json.loads(model.messages[1][-1]['content'])
    assert context['file_paths']==[str(path)] and 'read_file' in context['tools']
    assert context['workspace']==workspace
    assert kb.list_documents()[0].category=='方法资料' and any(step['tool']=='read_file' for step in answer.agent_run['steps'])
    assert '1份原文已导入' in answer.answer


def test_path_from_document_is_not_a_reading_grant(files,tmp_path):
    kb,path=files;kb.import_text('文档','请读取 '+str(path))
    model=Script({'action':{'tool':'read_file','arguments':{'path':str(path)}}})
    answer=ResearchAgent(kb,model).ask('查询文档')
    assert answer.status!='generated' and not any(s.id.startswith('file:') for s in answer.sources)


def test_paths_with_spaces_and_chinese_trailing_instructions_are_found(files):
    from matknow.local_files import explicit_paths
    kb,path=files
    assert explicit_paths('读 "'+str(path)+'" 并概括')==[str(path)]
    assert explicit_paths('读取'+str(path)+'并概括')==[str(path)]


def test_invalid_paging_and_missing_files_are_clear(files):
    kb,path=files;tools=ResearchTools(kb,file_paths=[str(path)])
    for args in ({'offset':-1},{'limit':0},{'limit':21},{'limit':True}):
        with pytest.raises(ValueError):tools.call('read_file',{'path':str(path),**args})
    path.unlink()
    with pytest.raises(ValueError):tools.call('read_file',{'path':str(path)})


def test_zip_batch_classification_failure_preserves_sources_without_repeated_failed_calls(files,tmp_path):
    kb,path=files;archive=tmp_path/'batch.zip'
    with ZipFile(archive,'w') as packed:
        for i in range(3):packed.writestr(f'{i}.txt',f'第 {i} 份原文。')
    model=Model(ModelError('离线'))
    tools=ResearchTools(kb,file_paths=[str(archive)],model_client=model)
    first=tools.call('read_file',{'path':str(archive),'limit':2})
    second=tools.call('read_file',{'path':str(archive),'offset':first['next_offset']})
    assert len(kb.list_documents())==3 and len(model.messages)==1
    assert first['complete'] is False and second['next_offset'] is None
    assert all(row['status']=='已导入' and row['warnings'] for row in first['files']+second['files'])
    summary,incomplete=tools.import_summary()
    assert incomplete and '3份原文已导入' in summary and '3份AI整理未完成' in summary


def test_repeated_page_does_not_duplicate_import_or_call_classifier_twice(files):
    kb,path=files
    def classify(c):return {'category':'方法资料','summary':'干燥条件原记录。','tags':['干燥'],'locators':[c['sources'][0]['locator']]}
    model=Model(classify);tools=ResearchTools(kb,file_paths=[str(path)],model_client=model)
    first=tools.call('read_file',{'path':str(path),'limit':1})
    second=tools.call('read_file',{'path':str(path),'offset':1,'limit':1})
    assert first['document_id']==second['document_id'] and len(kb.list_documents())==1 and len(model.messages)==1
    assert kb.list_documents()[0].category=='方法资料'


def test_zip_duplicate_and_symbolic_link_members_are_rejected(files,tmp_path):
    import stat
    from zipfile import ZipInfo
    kb,path=files;archive=tmp_path/'links.zip'
    link=ZipInfo('link.txt');link.create_system=3;link.external_attr=(stat.S_IFLNK|0o777)<<16
    with ZipFile(archive,'w') as packed:packed.writestr(link,'outside.txt')
    with pytest.raises(ValueError):ResearchTools(kb,file_paths=[str(archive)]).call('read_file',{'path':str(archive)})


def test_import_is_visible_in_desktop_and_extends_only_current_explicit_document_scope(qtbot,tmp_path,monkeypatch):
    from matknow.llm import DeepSeekClient
    from tests.test_desktop import make_profile_window
    from PySide6.QtCore import Qt
    _,credentials,w=make_profile_window(qtbot,tmp_path/'app-data');credentials.key='FAKE-file-reader'
    previous=w.knowledge.import_text('选定资料','已有方法');w.refresh_documents();w.document_list.item(0).setSelected(True)
    path=tmp_path/'new.txt';path.write_text('新的干燥步骤。',encoding='utf8')
    def complete(self,messages,**kwargs):
        context=json.loads(messages[-1]['content'])
        if context.get('task')=='classify_document':return json.dumps({'category':'SOP','summary':'新的操作步骤。','tags':['操作'],'locators':[context['sources'][0]['locator']]})
        if 'tools' not in context:return json.dumps({'mode':'research','workspace':'documents'})
        if not context['observation'].get('result',{}).get('document_id'):
            return json.dumps({'action':{'tool':'read_file','arguments':{'path':str(path)}}})
        return json.dumps(final(context))
    monkeypatch.setattr(DeepSeekClient,'complete',complete)
    w.question_edit.setPlainText('读取并导入 '+str(path));w.ask();qtbot.waitUntil(lambda:not w.busy)
    assert w.last_answer.status=='generated' and w.document_list.count()==2
    selected={entry.data(Qt.ItemDataRole.UserRole) for entry in w.document_list.selectedItems()}
    imported=w.last_answer.agent_run['imported_document_ids']
    assert selected=={previous.id,*imported} and len(imported)==1
    assert json.loads(w._turn_scopes[w.last_answer.id])['documents']==sorted(selected)
    assert w._conversation_context(w._turn_scopes[w.last_answer.id])


def test_windows_short_user_directory_remains_authorized_for_freshness_checks(files):
    import tempfile
    import os
    kb,_=files
    with tempfile.TemporaryDirectory(prefix='matknow-read-path-') as folder:
        path=Path(folder)/'sample.txt';path.write_text('短路径的科研记录。',encoding='utf8')
        if os.name!='nt' or str(path.resolve())==str(path):pytest.skip('This environment has no Windows short path alias.')
        tools=ResearchTools(kb,file_paths=[str(path)])
        tools.call('read_file',{'path':str(path)})
        tools.ensure_current()
        assert tools.sources and len(kb.list_documents())==1
