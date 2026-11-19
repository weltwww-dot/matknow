import json
import re
import socket
import ssl
import urllib.request
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from matknow.credentials import validate_key
from matknow.model_config import DEFAULT_URL, completion_url, validate_model
from matknow.model_context import ModelDirectory,valid_capacity,official_capacity,output_reserve

MAX_RESPONSE_BYTES = 1024 * 1024


class ModelError(RuntimeError):
    pass


class ContextWindowError(ModelError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ModelError('模型接口发生重定向，已拒绝转发 API Key。')


def _request_json(request,key):
    try:
        opener=urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()),_NoRedirect())
        with opener.open(request,timeout=60) as response:raw=response.read(MAX_RESPONSE_BYTES+1)
        if len(raw)>MAX_RESPONSE_BYTES:raise ModelError('模型响应超过大小限制，请缩小问题范围后重试。')
        data=json.loads(raw)
        if key in json.dumps(data,ensure_ascii=False):raise ModelError('模型响应包含敏感凭据，已拒绝显示和保存。')
        return data
    except HTTPError as exc:
        if exc.code in (400,413):
            try:
                body=json.loads(exc.read(65536));error=body.get('error',{})
                code=str(error.get('code','')).casefold();detail=str(error.get('message',''))
                if code in ('context_length_exceeded','context_window_exceeded','context_overflow') or re.search(r'maximum context length|context (?:length|window).{0,40}exceed',detail,re.I):
                    raise ContextWindowError('模型上下文容量不足，请缩小本次资料范围；会话历史已保留。')
            except (ValueError,TypeError,AttributeError,OSError):pass
            finally:exc.close()
        messages={400:'模型请求未被接受，请检查模型设置。',401:'鉴权失败（HTTP 401），请核对 API URL 与该厂商的 API Key。',
                  402:'模型服务余额或额度不足，请检查账户。',403:'该 API Key 无权访问模型，请检查账户权限。',
                  404:'接口不支持此请求，请检查 API URL。',429:'模型请求限流，请稍后重试。'}
        message=messages.get(exc.code,'模型服务暂时不可用，请稍后重试。');exc.close()
        raise ModelError(message) from None
    except (socket.timeout,TimeoutError):raise ModelError('模型请求超时，请检查网络或稍后重试。') from None
    except (URLError,OSError,HTTPException):raise ModelError('无法连接模型服务，请检查 API 地址、网络和代理设置。') from None
    except (ValueError,UnicodeError):raise ModelError('模型服务返回的响应格式无效，请稍后重试。') from None


def list_provider_models(key,base_url):
    key=validate_key(key)
    endpoint=completion_url(base_url).removesuffix('/chat/completions')+'/models'
    try:
        names={};capacities={};cursor=None;seen=set()
        for _ in range(20):
            url=endpoint+('?' + urlencode({'after':cursor}) if cursor else '')
            request=urllib.request.Request(url,headers={'Authorization':'Bearer '+key},method='GET')
            data=_request_json(request,key);items=data['data']
            if not isinstance(items,list) or not 1<=len(items)<=1000:raise ValueError
            names.update((validate_model(item['id']),None) for item in items)
            for item in items:
                candidates=[valid_capacity(item.get('context_length')),valid_capacity(item.get('context_window'))]
                top=item.get('top_provider')
                if isinstance(top,dict):candidates.append(valid_capacity(top.get('context_length')))
                values=[n for n in candidates if n is not None]
                if values:capacities[item['id']]=min(values)
            if len(names)>1000:raise ValueError
            more=data.get('has_more',False)
            if not isinstance(more,bool):raise ValueError
            if not more:return ModelDirectory(names,capacities)
            cursor=validate_model(data.get('last_id') or items[-1]['id'])
            if cursor in seen:raise ValueError
            seen.add(cursor)
        raise ValueError
    except (ValueError,KeyError,TypeError,AttributeError):
        raise ModelError('未获取到有效的完整模型列表，请检查厂商 URL 和模型列表接口。') from None


class DeepSeekClient:
    def __init__(self, key: str, model: str, base_url: str = DEFAULT_URL):
        self._key = validate_key(key)
        self.model = validate_model(model)
        self.endpoint = completion_url(base_url)
        self.context_window=official_capacity(self.endpoint,self.model)
        self.context_callback=None;self.last_usage=None

    def complete(self, messages: list[dict], json_output: bool = False, max_tokens: int = 8192) -> str:
        self.last_usage=None
        max_tokens=min(max_tokens,output_reserve(self.context_window))
        payload = {'model': self.model, 'messages': messages, 'stream': False,
                   'max_tokens': max_tokens}
        if self.endpoint in ('https://api.deepseek.com/chat/completions', 'https://api.deepseek.com/v1/chat/completions'):
            payload['thinking'] = {'type': 'disabled'}
        if json_output:
            payload['response_format'] = {'type': 'json_object'}
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self._key},
            method='POST',
        )
        try:
            data = _request_json(request,self._key)
            usage=data.get('usage')
            if isinstance(usage,dict) and all(type(usage.get(field)) is int and 0<=usage[field]<=10000000 for field in ('prompt_tokens','completion_tokens')):
                self.last_usage={field:usage[field] for field in ('prompt_tokens','completion_tokens')}
            choice = data['choices'][0]
            content = choice['message']['content']
            if choice['finish_reason'] != 'stop':
                raise ModelError('模型回答未完整生成，请缩小问题范围后重试。')
            if not isinstance(content, str) or not content.strip():
                raise ModelError('模型没有返回有效正文，请重试。')
            if self._key in content:
                raise ModelError('模型响应包含敏感凭据，已拒绝显示和保存。')
            if json_output:
                decoded = json.loads(content)
                if self._key in json.dumps(decoded, ensure_ascii=False):
                    raise ModelError('模型响应包含敏感凭据，已拒绝显示和保存。')
            return content.strip()
        except (ValueError, KeyError, TypeError, IndexError, UnicodeError):
            raise ModelError('模型服务返回的响应格式无效，请稍后重试。') from None

    def test_connection(self) -> str:
        self.complete([{'role': 'user', 'content': '请只回复 OK。'}], max_tokens=32)
        return '连接成功'
