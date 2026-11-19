import json

import pytest

from tests.test_llm import FAKE_KEY, install_transport


@pytest.mark.parametrize('url,expected',[
    ('https://api.deepseek.com','https://api.deepseek.com/models'),
    ('https://vendor.example/v1','https://vendor.example/v1/models'),
    ('https://vendor.example/v1/chat/completions','https://vendor.example/v1/models'),
])
def test_discovery_uses_only_selected_provider_url_and_key(monkeypatch,url,expected):
    from matknow.llm import list_provider_models
    captured=install_transport(monkeypatch,json.dumps({'data':[{'id':'model-a'},{'id':'model-b'},{'id':'model-a'}]}).encode())
    assert list_provider_models(FAKE_KEY,url)==['model-a','model-b']
    request,timeout=captured[0]
    assert request.full_url==expected and request.get_method()=='GET'
    assert request.get_header('Authorization')=='Bearer '+FAKE_KEY and request.data is None
    assert timeout==60


@pytest.mark.parametrize('outcome',[{'data':[{'id':FAKE_KEY}]},{'data':[]},{'data':[{'id':'bad model'}]},{'oops':[]}])
def test_discovery_rejects_invalid_or_incomplete_responses_without_secrets(monkeypatch,outcome):
    from matknow.llm import list_provider_models, ModelError
    install_transport(monkeypatch,json.dumps(outcome).encode())
    with pytest.raises(ModelError) as error:list_provider_models(FAKE_KEY,'https://vendor.example')
    assert FAKE_KEY not in str(error.value)


def test_paginated_models_are_collected_without_truncation_or_changing_origin(monkeypatch):
    import matknow.llm as llm
    requests=[]
    pages=iter([{'data':[{'id':'model-a'},{'id':'model-b'}],'has_more':True,'last_id':'cursor-b'},
                {'data':[{'id':'model-b'},{'id':'model-c'}],'has_more':False}])
    def request(req,key):requests.append(req.full_url);return next(pages)
    monkeypatch.setattr(llm,'_request_json',request)
    assert llm.list_provider_models(FAKE_KEY,'https://vendor.example/v1')==['model-a','model-b','model-c']
    assert requests==['https://vendor.example/v1/models','https://vendor.example/v1/models?after=cursor-b']


def test_repeating_pagination_cursor_fails_instead_of_saving_partial_list(monkeypatch):
    import matknow.llm as llm
    monkeypatch.setattr(llm,'_request_json',lambda *args:{'data':[{'id':'a'}],'has_more':True,'last_id':'a'})
    with pytest.raises(llm.ModelError):llm.list_provider_models(FAKE_KEY,'https://vendor.example')
