import pytest
from fastapi.testclient import TestClient

from matknow.api import create_app
from matknow.knowledge import KnowledgeBase


@pytest.fixture
def api(tmp_path):
    kb = KnowledgeBase(tmp_path / "api.sqlite3")
    first = kb.import_text("A.md", "干燥温度为 60 °C。")
    second = kb.import_text("B.md", "干燥温度为 80 °C。")
    return kb, first, second, TestClient(create_app(kb, "test-token"))


def test_missing_token_prevents_app_creation(tmp_path):
    with pytest.raises(ValueError):
        create_app(KnowledgeBase(tmp_path / "kb.sqlite3"), "")


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer incorrect"}])
def test_unauthorized_ask_cannot_read_or_write(api, headers):
    kb, first, second, client = api
    result = client.post("/v1/ask", json={"question": "干燥温度"}, headers=headers)
    assert result.status_code == 401
    assert kb.list_history() == []
    assert "60" not in result.text


def test_authenticated_scope_and_source_identity(api):
    kb, first, second, client = api
    headers = {"Authorization": "Bearer test-token"}
    result = client.post("/v1/ask", json={"question": "干燥温度", "document_ids": [second.id]}, headers=headers)
    assert result.status_code == 200
    data = result.json()
    assert data["status"] == "retrieval_preview"
    assert {source["document_id"] for source in data["sources"]} == {second.id}
    assert "80" in data["sources"][0]["text"]
    assert kb.get_evidence(data["sources"][0]["id"]).text == data["sources"][0]["text"]
    empty = client.post("/v1/ask", json={"question": "干燥温度", "document_ids": []}, headers=headers)
    assert empty.json()["sources"] == []


@pytest.mark.parametrize("question", ["", " ", "字" * 2001])
def test_input_limits(api, question):
    client = api[3]
    response = client.post("/v1/ask", json={"question": question}, headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 422


def test_health_reports_preview_without_source_data(api):
    response = api[3].get("/health")
    assert response.json() == {"status": "ok", "mode": "retrieval_preview"}


def test_api_service_binds_explicit_profile_and_does_not_follow_desktop_selection(tmp_path):
    from matknow.api import build_profile_service
    from matknow.profiles import DEFAULT_PROFILE_ID, ProfileStore
    store = ProfileStore(tmp_path)
    first = store.knowledge(DEFAULT_PROFILE_ID)
    first.import_text('same.txt', '干燥温度 60 °C。')
    other = store.create('其他方向')
    other_kb = store.knowledge(other.id)
    foreign = other_kb.import_text('same.txt', '干燥温度 25 °C。')
    store.select(other.id)
    service = build_profile_service(tmp_path)
    assert store.current_id == other.id
    client = TestClient(create_app(service.knowledge, 'test-token', service=service, profile_id=DEFAULT_PROFILE_ID))
    headers = {'Authorization': 'Bearer test-token'}
    assert '60' in client.post('/v1/ask', json={'question': '干燥温度'}, headers=headers).json()['sources'][0]['text']
    assert client.post('/v1/ask', json={'question': '干燥温度', 'document_ids': [foreign.id]}, headers=headers).json()['sources'] == []
    assert client.get('/health').json()['profile_id'] == DEFAULT_PROFILE_ID
    assert other_kb.list_history() == []
    assert len(first.list_history()) == 2
    assert build_profile_service(tmp_path, other.id).knowledge.path == other_kb.path
    with pytest.raises(ValueError):
        build_profile_service(tmp_path, 'not-a-profile')


def test_api_model_answer_and_error_status_do_not_leak_key(tmp_path, monkeypatch):
    from matknow.api import build_profile_service
    from matknow.profiles import ProfileStore
    from matknow.llm import DeepSeekClient, ModelError
    import json
    key = 'FAKE-server-key-only-for-tests'
    store=ProfileStore(tmp_path)
    entry=store.models.save(None,'测试厂商','https://test.example/v1','test-model')
    profile=store.get(store.current_id)
    store.update(profile.id,profile.name,profile.description,entry.id)
    kb = KnowledgeBase(tmp_path / 'knowledge.sqlite3')
    kb.import_text('A.txt', '干燥温度 60 °C。')
    def complete(self, messages, json_output=False):
        assert key not in json.dumps(messages)
        source_id = json.loads(messages[-1]['content'])['sources'][0]['id']
        return json.dumps({'answer': '60 °C [1]', 'source_ids': [source_id], 'status': 'generated'})
    monkeypatch.setattr(DeepSeekClient, 'complete', complete)
    service = build_profile_service(tmp_path, api_key=key)
    client = TestClient(create_app(service.knowledge, 'token', service=service))
    headers = {'Authorization': 'Bearer token'}
    answer = client.post('/v1/ask', json={'question': '干燥温度'}, headers=headers)
    assert answer.json()['status'] == 'generated'
    assert key not in answer.text
    assert client.get('/health').json()['mode'] == 'model_qa'
    def fail(*args, **kwargs):
        raise ModelError('API Key 无效或已失效，请重新填写。')
    monkeypatch.setattr(DeepSeekClient, 'complete', fail)
    answer = client.post('/v1/ask', json={'question': '干燥温度'}, headers=headers)
    assert answer.json()['status'] == 'model_error'
    assert answer.json()['sources']
    assert key not in answer.text


def test_api_startup_handles_damaged_sqlite_without_traceback(tmp_path, monkeypatch):
    import sqlite3
    from matknow import api as api_module
    from matknow.profiles import ProfileStore
    ProfileStore(tmp_path)
    monkeypatch.setattr(api_module, 'default_data_dir', lambda: tmp_path)
    monkeypatch.setenv('MATKNOW_API_TOKEN', 'fake-token')
    monkeypatch.setattr(api_module, 'build_profile_service', lambda *a: (_ for _ in ()).throw(sqlite3.DatabaseError('database is malformed')))
    monkeypatch.setattr('sys.argv', ['matknow.api'])
    with pytest.raises(SystemExit) as failure:
        api_module.main()
    assert failure.value.code == 2


def test_custom_api_profile_does_not_inherit_deepseek_environment_key(tmp_path,monkeypatch):
    from matknow.api import build_profile_service
    from matknow.profiles import ProfileStore
    from matknow.credentials import CredentialStore
    store=ProfileStore(tmp_path)
    entry=store.models.save(None,'实验厂商','https://lab.example/v1','lab-model')
    profile=store.create('自定义方向',model=entry.id)
    monkeypatch.setenv('DEEPSEEK_API_KEY','FAKE-deepseek-only-key')
    monkeypatch.delenv('MATKNOW_MODEL_API_KEY',raising=False)
    monkeypatch.setattr(CredentialStore,'get',lambda self:None)
    assert build_profile_service(tmp_path,profile.id).model_client is None
    monkeypatch.setenv('MATKNOW_MODEL_API_KEY','FAKE-explicit-service-key')
    client=build_profile_service(tmp_path,profile.id).model_client
    assert client._key=='FAKE-explicit-service-key' and client.model=='lab-model'
    assert client.endpoint=='https://lab.example/v1/chat/completions'


def test_api_uses_each_provider_key_and_ignores_removed_disconnect_preferences(tmp_path):
    from matknow.api import build_profile_service
    from matknow.profiles import ProfileStore
    store=ProfileStore(tmp_path)
    one=store.models.save(None,'甲厂商','https://one.example/v1','same-model')
    two=store.models.save(None,'乙厂商','https://two.example/v1','same-model')
    first=store.create('甲方向',model=one.id);second=store.create('乙方向',model=two.id)
    (tmp_path/'model-disabled').touch()
    assert build_profile_service(tmp_path,first.id,'FAKE-one-key').model_client._key=='FAKE-one-key'
    assert build_profile_service(tmp_path,second.id,'FAKE-two-key').model_client._key=='FAKE-two-key'
