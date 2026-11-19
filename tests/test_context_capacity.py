import json

from matknow.conversations import ContextMemory
from matknow.knowledge import KnowledgeBase
from matknow.llm import DeepSeekClient,list_provider_models
from matknow.model_config import ModelCatalog
from tests.test_chat_routing import Model
from tests.test_llm import FAKE_KEY,install_transport,response
from tests.test_desktop import make_profile_window


def test_discovered_capacity_survives_restart_and_stays_endpoint_specific(tmp_path,monkeypatch):
    install_transport(monkeypatch,json.dumps({'data':[{'id':'custom','context_length':32768,'top_provider':{'context_length':16384}},
        {'id':'bad','context_length':True}]}).encode())
    models=list_provider_models(FAKE_KEY,'https://one.example/v1')
    catalog=ModelCatalog(tmp_path);p=catalog.save_provider(None,'One','https://one.example/v1',models)
    assert ModelCatalog(tmp_path).context_window(p.models[0].id)==16384
    assert catalog.context_window(p.models[1].id) is None
    other=catalog.save_provider(None,'Other','https://two.example/v1',['custom'])
    assert catalog.context_window(other.models[0].id) is None


def test_official_capacity_is_not_guessed_from_model_name_on_other_hosts(tmp_path):
    catalog=ModelCatalog(tmp_path)
    official=catalog.save_provider(None,'DeepSeek','https://api.deepseek.com',['deepseek-flash'])
    routed=catalog.save_provider(None,'Proxy','https://proxy.example',['deepseek-flash'])
    assert catalog.context_window(official.models[0].id)==1000000
    assert catalog.context_window(routed.models[0].id) is None


def test_model_capacity_replaces_fixed_twelve_thousand_character_history_limit(tmp_path):
    turns=[{'question':f'第{i}次','answer':'历史内容。'*500,'mode':'chat'} for i in range(7)]
    model=Model({'mode':'chat','answer':'收到'});model.context_window=128000
    memory=ContextMemory(KnowledgeBase(tmp_path/'kb.sqlite3'),turns,model)
    memory.complete('回答JSON',{'question':'继续'})
    assert len(model.messages)==1 and len(model.messages[0][1:-1])==14
    assert not memory.summary


def test_small_model_automatically_compacts_before_capacity_and_reports_usage(tmp_path):
    turns=[{'question':f'第{i}次项目QX','answer':'条件记录。'*250,'mode':'chat'} for i in range(8)]
    def complete(payload):
        return {'summary':'项目QX，继续核对条件。'} if payload.get('task')=='summarize_conversation' else {'mode':'chat','answer':'收到'}
    model=Model(*[complete]*20);model.context_window=8192;events=[];model.context_callback=events.append
    memory=ContextMemory(KnowledgeBase(tmp_path/'kb.sqlite3'),turns,model)
    memory.complete('回答JSON',{'question':'继续'})
    assert memory.summary and memory.covered>0 and events
    assert events[-1]['capacity']==8192 and events[-1]['used']<8192
    assert any(event.get('compacted') for event in events)


def test_client_exposes_valid_provider_usage_and_does_not_reuse_old_usage(monkeypatch):
    body=json.loads(response('你好'));body['usage']={'prompt_tokens':4321,'completion_tokens':12,'total_tokens':4333}
    install_transport(monkeypatch,json.dumps(body).encode());client=DeepSeekClient(FAKE_KEY,'model','https://one.example')
    client.complete([])
    assert client.last_usage=={'prompt_tokens':4321,'completion_tokens':12}
    install_transport(monkeypatch,response('无usage'));client.complete([])
    assert client.last_usage is None


def test_input_context_ring_follows_typing_model_switch_and_new_chat(qtbot,tmp_path):
    store,_,w=make_profile_window(qtbot,tmp_path)
    one=store.models.save_provider(None,'官方','https://api.deepseek.com',['deepseek-flash'])
    two=store.models.save_provider(None,'未知','https://two.example',['custom'])
    w.use_model(one.models[0].id)
    ring=w.context_ring;before=ring.usage['used']
    w.question_edit.setPlainText('这段输入会增加上下文占用。'*40)
    assert ring.usage['capacity']==1000000 and ring.usage['used']>before
    assert '估算' in ring.toolTip() and '自动压缩' in ring.toolTip()
    assert ring.parent() is w.question_edit
    w.use_model(two.models[0].id)
    assert ring.usage['capacity'] is None and '未知' in ring.toolTip()
    w.new_conversation();assert ring.usage['used']<before+100


def test_ring_updates_on_history_scope_and_metadata_refresh_without_typing(qtbot,tmp_path,monkeypatch):
    from matknow.models import Answer
    from matknow.knowledge import utc_now
    from matknow.conversations import scope_key
    from matknow.model_context import ModelDirectory
    store,credentials,w=make_profile_window(qtbot,tmp_path);credentials.key=FAKE_KEY
    provider=store.models.save_provider(None,'One','https://one.example',['custom'])
    credentials.for_endpoint(provider.url).key=FAKE_KEY;w.use_model(provider.models[0].id)
    initial=w.context_ring.usage['used']
    a=Answer('synthetic-chat','称呼我小禾','继续聊天。'*500,'chat',utc_now())
    w.knowledge.save_answer(a);w.conversations.append(a,None,scope_key());w.refresh_history();w.open_history(w.history_list.item(0))
    restored=w.context_ring.usage['used'];assert restored>initial+1000
    w.knowledge.import_text('方案','测试正文');w.refresh_documents();w.document_list.item(0).setSelected(True)
    # Changing the retrieval scope keeps this conversation's ordinary chat context.
    assert w.context_ring.usage['used']==restored
    monkeypatch.setattr('matknow.desktop.list_provider_models',lambda *a:ModelDirectory(['custom'],{'custom':16384}))
    w.refresh_provider_models();qtbot.waitUntil(lambda:not w.busy)
    assert w.context_ring.usage['capacity']==16384


def test_typing_does_not_rescan_history_or_query_evidence(qtbot,tmp_path,monkeypatch):
    _,_,w=make_profile_window(qtbot,tmp_path);w._refresh_context_usage()
    def unexpected(*a,**kw):raise AssertionError('Typing must reuse the conversation preview')
    monkeypatch.setattr(w,'_conversation_context',unexpected)
    for text in ('我','我想','我想继续讨论'):w.question_edit.setPlainText(text)
    assert w.context_ring.usage['used']>0


def test_preview_reuses_checkpoint_like_actual_request_even_with_larger_model(tmp_path):
    from tests.test_context_management import saved_chat,long_history
    kb=KnowledgeBase(tmp_path/'kb.sqlite3');turns=long_history();identifier=saved_chat(kb,turns)
    memory=ContextMemory(kb,turns,Model(*[{'summary':'项目QX，继续实验。'}]*10),conversation_id=identifier);memory.prepare(12000)
    model=Model();model.context_window=128000
    reopened=ContextMemory(kb,turns,model,conversation_id=identifier)
    preview=reopened.preview('JSON',{'question':'继续'})
    messages=reopened.messages('JSON',{'question':'继续'})
    assert preview['compacted'] and reopened.summary
    from matknow.model_context import estimate_tokens
    assert abs(preview['used']-estimate_tokens(messages))<100
