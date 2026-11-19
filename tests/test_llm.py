import io
import json
import socket
from urllib.error import HTTPError, URLError

import pytest

from matknow.llm import DeepSeekClient, ModelError, _NoRedirect

FAKE_KEY = 'FAKE-only-test-secret-abcdef'


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def response(content='answer', finish='stop'):
    return json.dumps({'choices': [{'message': {'content': content}, 'finish_reason': finish}]}).encode()


def install_transport(monkeypatch, outcome):
    import urllib.request
    captured = []
    class Opener:
        def open(self, request, timeout):
            captured.append((request, timeout))
            if isinstance(outcome, Exception):
                raise outcome
            return Reply(outcome)
    def build(*handlers):
        assert any(isinstance(h, _NoRedirect) for h in handlers)
        assert any(isinstance(h, urllib.request.HTTPSHandler) and h._context.check_hostname for h in handlers)
        return Opener()
    monkeypatch.setattr(urllib.request, 'build_opener', build)
    return captured


def test_request_uses_official_tls_origin_auth_json_and_bounded_timeout(monkeypatch):
    captured = install_transport(monkeypatch, response('{"answer":"ok"}'))
    client = DeepSeekClient(FAKE_KEY, 'deepseek-flash')
    assert client.complete([{'role': 'user', 'content': 'test'}], json_output=True) == '{"answer":"ok"}'
    request, timeout = captured[0]
    assert request.full_url == 'https://api.deepseek.com/chat/completions'
    assert request.get_header('Authorization') == 'Bearer ' + FAKE_KEY
    assert timeout == 60
    data = json.loads(request.data)
    assert data['response_format'] == {'type': 'json_object'}
    assert data['model'] == 'deepseek-flash'
    assert data['stream'] is False
    assert FAKE_KEY not in request.data.decode()
    assert FAKE_KEY not in repr(client)


def test_canonical_context_overflow_is_distinct_from_other_http_400(monkeypatch):
    from matknow.llm import ContextWindowError
    error=HTTPError('https://vendor.example',400,'bad request',{},io.BytesIO(json.dumps({'error':{'code':'context_length_exceeded','message':'too long'}}).encode()))
    install_transport(monkeypatch,error)
    with pytest.raises(ContextWindowError):DeepSeekClient(FAKE_KEY,'model').complete([{'role':'user','content':'hi'}])


@pytest.mark.parametrize('url', ['https://vendor.example/v1', 'https://vendor.example/v1/chat/completions'])
def test_custom_endpoint_and_model_are_used_without_deepseek_only_parameters(monkeypatch,url):
    captured=install_transport(monkeypatch,response())
    client=DeepSeekClient(FAKE_KEY,'lab-model-v2',base_url=url)
    assert client.complete([{'role':'user','content':'test'}])=='answer'
    request,_=captured[0]
    assert request.full_url=='https://vendor.example/v1/chat/completions'
    payload=json.loads(request.data)
    assert payload['model']=='lab-model-v2' and 'thinking' not in payload
    assert request.get_header('Authorization')=='Bearer '+FAKE_KEY


@pytest.mark.parametrize('url', ['http://vendor.example/v1','https://user:secret@vendor.example','https://vendor.example?key=secret','file:///private','https://vendor.example/#fragment','https://vendor.example/\nprivate'])
def test_unsafe_custom_endpoints_are_rejected_before_request(url):
    with pytest.raises(ValueError):
        DeepSeekClient(FAKE_KEY,'lab-model',base_url=url)


def test_loopback_endpoint_supports_local_models(monkeypatch):
    captured=install_transport(monkeypatch,response())
    DeepSeekClient(FAKE_KEY,'local-model',base_url='http://127.0.0.1:11434/v1').test_connection()
    assert captured[0][0].full_url=='http://127.0.0.1:11434/v1/chat/completions'


@pytest.mark.parametrize('status, hint', [(401, 'API Key'), (402, '余额'), (429, '限流'), (500, '服务')])
def test_http_errors_do_not_echo_secrets_or_response_bodies(monkeypatch, status, hint):
    error = HTTPError('https://api.deepseek.com/chat/completions', status, FAKE_KEY, {}, io.BytesIO(FAKE_KEY.encode()))
    captured = install_transport(monkeypatch, error)
    with pytest.raises(ModelError, match=hint) as failure:
        DeepSeekClient(FAKE_KEY, 'deepseek-flash').test_connection()
    assert FAKE_KEY not in str(failure.value)
    assert len(captured) == 1


def test_unauthorized_reports_http_status_without_claiming_key_expired(monkeypatch):
    error=HTTPError('https://api.deepseek.com/models',401,FAKE_KEY,{},io.BytesIO(FAKE_KEY.encode()))
    install_transport(monkeypatch,error)
    from matknow.llm import list_provider_models
    with pytest.raises(ModelError) as failure:
        list_provider_models(FAKE_KEY,'https://api.deepseek.com')
    message=str(failure.value)
    assert '401' in message and 'API URL' in message and 'API Key' in message
    assert '已失效' not in message and FAKE_KEY not in message


@pytest.mark.parametrize('outcome', [URLError(FAKE_KEY), socket.timeout(FAKE_KEY), b'{', b'x' * (1024 * 1024 + 1), response(''), response('partial', 'length'), response(FAKE_KEY)], ids=['network', 'timeout', 'invalid-json', 'too-large', 'empty', 'truncated', 'secret-echo'])
def test_invalid_or_failed_reply_is_safe(monkeypatch, outcome):
    install_transport(monkeypatch, outcome)
    with pytest.raises(ModelError) as failure:
        DeepSeekClient(FAKE_KEY, 'deepseek-flash').complete([])
    assert FAKE_KEY not in str(failure.value)


def test_redirect_is_refused_before_credentials_can_be_forwarded():
    with pytest.raises(ModelError, match='重定向'):
        _NoRedirect().redirect_request(None, None, 307, 'redirect', {}, 'https://elsewhere.example')


def test_json_escaped_key_is_rejected_after_decoding(monkeypatch):
    escaped = ''.join('\\u%04x' % ord(char) for char in FAKE_KEY)
    content = json.dumps({'answer': FAKE_KEY, 'source_ids': ['source'], 'status': 'generated'}).replace(FAKE_KEY, escaped)
    install_transport(monkeypatch, response(content))
    with pytest.raises(ModelError, match='敏感凭据'):
        DeepSeekClient(FAKE_KEY, 'deepseek-flash').complete([], json_output=True)


def test_escaped_key_never_reaches_service_history(monkeypatch, tmp_path):
    import urllib.request
    from matknow.knowledge import KnowledgeBase
    from matknow.agent import ResearchAgent
    escaped = ''.join('\\u%04x' % ord(char) for char in FAKE_KEY)
    class Opener:
        def open(self, request, timeout):
            context = json.loads(request.data)['messages'][-1]['content']
            source_id = json.loads(context)['sources'][0]['id']
            inner = json.dumps({'answer': FAKE_KEY + ' [1]', 'source_ids': [source_id], 'status': 'generated'}).replace(FAKE_KEY, escaped)
            return Reply(response(inner))
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *handlers: Opener())
    kb = KnowledgeBase(tmp_path / 'no-leak.sqlite3')
    kb.import_text('A.txt', '干燥温度 60 °C。')
    answer = ResearchAgent(kb, DeepSeekClient(FAKE_KEY, 'deepseek-flash')).ask('干燥温度')
    assert answer.status == 'model_error'
    assert FAKE_KEY not in answer.answer
    assert FAKE_KEY not in kb.list_history()[0].answer
