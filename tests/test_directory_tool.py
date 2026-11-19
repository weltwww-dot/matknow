import hashlib
import json
from pathlib import Path

import pytest

from matknow.agent import ResearchAgent
from matknow.agent_tools import ResearchTools
from matknow.knowledge import KnowledgeBase
from matknow.local_files import explicit_paths
from tests.test_agent import final
from tests.test_chat_routing import Model


@pytest.fixture
def folder(tmp_path):
    root=tmp_path/'资料 inbox';(root/'P004').mkdir(parents=True)
    (root/'P004'/'方法.txt').write_text('样品SP. 干燥温度60 °C。',encoding='utf8')
    (root/'P005').mkdir();(root/'P005'/'记录.md').write_text('样品A1，搅拌30分钟。',encoding='utf8')
    (root/'SEM.png').write_bytes(b'not interpreted')
    return KnowledgeBase(tmp_path/'app-data/knowledge.sqlite3'),root


def test_unquoted_extensionless_directory_followed_by_chinese_instructions(folder):
    _,root=folder
    assert explicit_paths(str(root)+'整理一下，分类导入知识库')==[str(root)]
    assert explicit_paths('读取“'+str(root)+'”并整理')==[str(root)]
    missing=root.parent/'不存在的目录'
    assert explicit_paths(str(missing)+'整理一下，分类导入知识库')==[str(missing)]


def test_directory_name_with_document_extension_and_spaces_is_not_truncated(tmp_path):
    root=tmp_path/'资料.pdf 文件夹';root.mkdir()
    assert explicit_paths(str(root)+'整理一下，分类导入知识库')==[str(root)]


def test_directory_imports_in_pages_keeps_groups_and_original_bytes(folder):
    kb,root=folder;before={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    tools=ResearchTools(kb,file_paths=[str(root)])
    first=tools.call('read_file',{'path':str(root),'limit':1})
    assert first['kind']=='directory' and first['has_more'] and first['skipped_count']==1
    last=tools.call('read_file',first['next_page'])
    assert not last['has_more'] and last['processed']==2 and last['next_offset'] is None
    assert {d.group for d in kb.list_documents()}=={'P004','P005'}
    assert {o['member'] for d in kb.list_documents() for o in kb.document_origins(d.id)}=={'P004/方法.txt','P005/记录.md'}
    assert before=={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    tools.ensure_current()


def test_directory_scope_allows_children_but_not_siblings_or_internal_storage(folder,tmp_path):
    kb,root=folder;outside=tmp_path/'outside.txt';outside.write_text('PRIVATE')
    tools=ResearchTools(kb,file_paths=[str(root)])
    tools.call('read_file',{'path':str(root/'P004/方法.txt')})
    for member in ('../outside.txt',str(outside),'P004/../../outside.txt'):
        with pytest.raises(ValueError):tools.call('read_file',{'path':str(root),'member':member})
    with pytest.raises(ValueError):tools.call('read_file',{'path':str(outside)})
    protected=root/'app-data';protected.mkdir();(protected/'secret.txt').write_text('PRIVATE_KEY')
    result=tools.call('read_file',{'path':str(root)})
    assert 'PRIVATE_KEY' not in json.dumps(tools.context())
    assert result['skipped_count']==2 and len(kb.list_documents())==3,result
    with pytest.raises(ValueError):tools.call('read_file',{'path':str(protected/'secret.txt')})


def test_directory_membership_changes_invalidate_current_task(folder):
    kb,root=folder;tools=ResearchTools(kb,file_paths=[str(root)])
    tools.call('read_file',{'path':str(root)})
    (root/'new.txt').write_text('new')
    with pytest.raises(ValueError,match='更新|变更'):tools.ensure_current()


@pytest.mark.parametrize('workspace',['documents','experiments'])
def test_both_agents_receive_user_directory_and_can_import(folder,workspace):
    kb,root=folder
    def classify(c):return {'category':'方法资料','summary':'实验条件。','tags':['制备'],'locators':[c['sources'][0]['locator']]}
    def respond(c):
        if c.get('task'):return classify(c)
        if 'tools' not in c:return {'mode':'research','workspace':workspace}
        if c['observation'].get('result',{}).get('kind')=='directory':
            assert all(f['status']=='已导入' for f in c['observation']['result']['files']),c['observation']
            return final(c)
        assert c['observation'].get('status')=='ok',c['observation']
        return {'action':{'tool':'read_file','arguments':{'path':str(root)}}}
    model=Model(*[respond]*8)
    answer=ResearchAgent(kb,model).respond(str(root)+'整理一下，分类导入知识库')
    assert answer.status=='generated' and len(kb.list_documents())==2
    assert json.loads(model.messages[0][-1]['content'])['file_paths']==[str(root)]
    assert sum(json.loads(m[-1]['content']).get('task')=='classify_document' for m in model.messages)==2


def test_directory_limit_fails_before_importing_any_partial_collection(tmp_path):
    root=tmp_path/'large';root.mkdir()
    for i in range(501):(root/str(i)).write_bytes(b'')
    kb=KnowledgeBase(tmp_path/'app-data/kb.sqlite3')
    with pytest.raises(ValueError,match='500'):ResearchTools(kb,file_paths=[str(root)]).call('read_file',{'path':str(root)})
    assert not kb.list_documents()


def test_directory_does_not_follow_links_to_outside_files(folder,tmp_path):
    kb,root=folder;outside=tmp_path/'outside';outside.mkdir();(outside/'private.txt').write_text('OUTSIDE_PRIVATE')
    link=root/'shortcut'
    try:link.symlink_to(outside,target_is_directory=True)
    except OSError:pytest.skip('Creating filesystem links is unavailable on this Windows host.')
    tools=ResearchTools(kb,file_paths=[str(root)])
    receipt=tools.call('read_file',{'path':str(root)})
    assert receipt['skipped_count']==2 and 'OUTSIDE_PRIVATE' not in json.dumps(tools.context())
    with pytest.raises(ValueError):tools.call('read_file',{'path':str(link/'private.txt')})
    with pytest.raises(ValueError):ResearchTools(kb,file_paths=[str(link)]).call('read_file',{'path':str(outside/'private.txt')})


def test_default_batch_leaves_room_for_agent_to_finish_and_reports_actual_progress(folder):
    kb,root=folder
    for i in range(9):(root/f'新增{i}.txt').write_text('实验记录。',encoding='utf8')
    receipt=ResearchTools(kb,file_paths=[str(root)]).call('read_file',{'path':str(root)})
    assert len(receipt['files'])==10 and receipt['next_offset']==10
    assert '10份原文已导入' in receipt['import_status'] and '1份尚未处理' in receipt['import_status']
    assert all(row['organized'] is False for row in receipt['files'])


def test_duplicate_original_success_clears_earlier_pending_metadata_status(tmp_path):
    root=tmp_path/'inbox'
    for child in ('a','b'):
        path=root/'P001'/child/'same.txt';path.parent.mkdir(parents=True);path.write_text('实验条件60 °C。',encoding='utf8')
    kb=KnowledgeBase(tmp_path/'app-data/kb.sqlite3')
    def classify(c):return {'category':'方法资料','summary':'实验条件。','tags':[],'locators':[c['sources'][0]['locator']]}
    receipt=ResearchTools(kb,file_paths=[str(root)],model_client=Model(ValueError('invalid metadata'),classify)).call('read_file',{'path':str(root)})
    assert len(kb.list_documents())==1 and kb.list_documents()[0].summary
    assert receipt['files'][1]['organized'] is True
    assert 'AI整理未完成' not in receipt['import_status']
