import ipaddress
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL
from matknow.model_context import official_capacity,valid_capacity


DEFAULT_URL = 'https://api.deepseek.com'
# Only for reading configurations from releases that shipped preset models.
LEGACY_MODELS = ('deepseek-flash', 'deepseek-v4-pro')


def validate_model(model):
    if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,199}', model.strip()):
        raise ValueError('请填写有效的模型 ID（如 qwen-plus），不能含空格或控制字符。')
    return model.strip()


def completion_url(value):
    if not isinstance(value, str) or len(value) > 2000 or any(ord(c) < 33 for c in value.strip()):
        raise ValueError('请填写有效的 API 地址。')
    try:
        parts = urlsplit(value.strip())
        host = parts.hostname
        port = parts.port
        local = host == 'localhost'
        if host:
            try:local = local or ipaddress.ip_address(host).is_loopback
            except ValueError:pass
        if not host or parts.username is not None or parts.password is not None or parts.query or parts.fragment:
            raise ValueError
        if parts.scheme != 'https' and not (parts.scheme == 'http' and local):
            raise ValueError
        authority = f'[{host}]' if ':' in host else host
        if port is not None:authority += f':{port}'
        path = parts.path.rstrip('/')
        if not path.endswith('/chat/completions'):path += '/chat/completions'
        return urlunsplit((parts.scheme, authority, path, '', ''))
    except (ValueError, UnicodeError):
        raise ValueError('API 地址需使用 HTTPS（本机地址可用 HTTP），且不能包含账号、密钥参数或片段。') from None


def provider_name_from_url(value):
    host=urlsplit(completion_url(value)).hostname
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:pass
    labels=host.split('.')
    while len(labels)>1 and labels[0] in ('api','www'):labels.pop(0)
    return labels[0][:60]


@dataclass(frozen=True)
class ModelConfig:
    id: str
    provider: str
    url: str
    model: str

    @property
    def label(self):
        return self.model if self.provider == '深度求索' else f'{self.provider} / {self.model}'


@dataclass(frozen=True)
class ProviderConfig:
    id: str
    name: str
    url: str
    models: tuple[ModelConfig, ...]


class ModelCatalog:
    def __init__(self, root):
        self.path = Path(root)/'models.json'
        self.list_models()

    def list_providers(self):
        if not self.path.exists():return []
        try:
            data = json.loads(self.path.read_text(encoding='utf8'))
            if data['version'] in (1,2):
                if not isinstance(data['models'],list):raise ValueError
                entries = [ModelConfig(**entry) for entry in data['models']]
                self._validate_entries(entries)
                if data['version']==1:entries=[entry for entry in entries if entry!=self.legacy_entry(entry.id)]
                return self._group(entries)
            if data['version']!=3 or not isinstance(data['providers'],list):raise ValueError
            providers=[];ids=set();identities=set()
            for item in data['providers']:
                if set(item)!={'id','name','url','models'}:raise ValueError
                identifier,name,url=item['id'],item['name'],item['url']
                if str(UUID(identifier))!=identifier or identifier in ids or (name,url) in identities:raise ValueError
                entries=tuple(ModelConfig(m['id'],name,url,m['model']) for m in item['models'] if set(m)=={'id','model'})
                if len(entries)!=len(item['models']):raise ValueError
                if self.validate_provider(name,url,[m.model for m in entries])!=(name,url,tuple(m.model for m in entries)):raise ValueError
                providers.append(ProviderConfig(identifier,name,url,entries));ids.add(identifier);identities.add((name,url))
            self._validate_entries([entry for provider in providers for entry in provider.models])
            return providers
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
            raise ValueError('模型配置损坏，请保留 models.json 并检查配置。') from None

    def list_models(self):
        return [entry for provider in self.list_providers() for entry in provider.models]

    @classmethod
    def _validate_entries(cls,entries):
        ids=set()
        for entry in entries:
            if entry.id in ids or (entry.id not in LEGACY_MODELS and str(UUID(entry.id))!=entry.id):raise ValueError
            if cls.validate(entry.provider,entry.url,entry.model)!=(entry.provider,entry.url,entry.model):raise ValueError
            ids.add(entry.id)

    @staticmethod
    def _group(entries,previous=()):
        groups={}
        for entry in entries:groups.setdefault((entry.provider,entry.url),[]).append(entry)
        existing={(p.name,p.url):p.id for p in previous}
        return [ProviderConfig(existing.get((name,url),str(uuid5(NAMESPACE_URL,name+'\0'+url))),name,url,tuple(models))
                for (name,url),models in groups.items()]

    @staticmethod
    def validate(provider, url, model):
        if not isinstance(provider, str) or not 1 <= len(provider.strip()) <= 60 or any(ord(c) < 32 for c in provider):
            raise ValueError('请填写厂商名称（1–60 个字符）。')
        return provider.strip(), completion_url(url), validate_model(model)

    def get(self, identifier):
        if identifier is None:return None
        for entry in self.list_models():
            if entry.id == identifier:return entry
        legacy = self.legacy_entry(identifier)
        if legacy and (not self.path.exists() or json.loads(self.path.read_text(encoding='utf8'))['version']<3):return legacy
        raise ValueError('找不到该模型配置，请在模型设置中重新选择。')

    def get_provider(self,identifier):
        for provider in self.list_providers():
            if provider.id==identifier:return provider
        raise ValueError('找不到该厂商配置。')

    def context_window(self,identifier):
        config=self.get(identifier)
        if config is None:return None
        data=json.loads(self.path.read_text(encoding='utf8')) if self.path.exists() else {}
        capacities=data.get('context_windows',{})
        if isinstance(capacities,dict):
            limits=capacities.get(config.url,{})
            if isinstance(limits,dict) and valid_capacity(limits.get(config.model)) is not None:return limits[config.model]
        return official_capacity(config.url,config.model)

    def provider_for_model(self,identifier):
        return next((p for p in self.list_providers() if any(m.id==identifier for m in p.models)),None)

    @classmethod
    def validate_provider(cls,name,url,models):
        if not isinstance(models,(list,tuple)) or len(models)>1000:
            raise ValueError('模型列表最多支持 1000 个模型。')
        names=tuple(validate_model(model) for model in models)
        if len(set(names))!=len(names):raise ValueError('模型 ID 不能重复。')
        name,url,_=cls.validate(name,url,names[0] if names else 'validation')
        return name,url,names

    def save_provider(self,identifier,name,url,models):
        name,url,names=self.validate_provider(name,url,models)
        providers=self.list_providers()
        existing=self.validate_provider_identity(identifier,name,url)
        previous={m.model:m.id for m in existing.models} if existing else {}
        provider=ProviderConfig(identifier or str(uuid4()),name,url,
                                tuple(ModelConfig(previous.get(model,str(uuid4())),name,url,model) for model in names))
        providers=[provider if p.id==identifier else p for p in providers]
        if existing is None:providers.append(provider)
        self._write(providers,{url:getattr(models,'context_windows',{})} if hasattr(models,'context_windows') else None)
        return provider

    def validate_provider_identity(self,identifier,name,url):
        existing=self.get_provider(identifier) if identifier else None
        if any(p.id!=identifier and (p.name,p.url)==(name,url) for p in self.list_providers()):
            raise ValueError('此厂商和接口已存在，请编辑已有厂商。')
        return existing

    def delete_provider(self,identifier):
        self.get_provider(identifier)
        self._write([p for p in self.list_providers() if p.id!=identifier])

    def retain_legacy(self,entries):
        providers=self.list_providers()
        existing=[m for p in providers for m in p.models]
        known={m.id for m in existing}
        additions=[m for m in entries if m.id not in known]
        if additions:self._write(self._group(existing+additions,providers))

    @staticmethod
    def legacy_entry(identifier):
        if identifier in LEGACY_MODELS:
            return ModelConfig(identifier, '深度求索', completion_url(DEFAULT_URL), identifier)
        return None

    def save(self, identifier, provider, url, model):
        provider, url, model = self.validate(provider, url, model)
        providers = self.list_providers()
        entries = [m for p in providers for m in p.models]
        exists = any(entry.id == identifier for entry in entries)
        if identifier is not None and not exists and identifier not in LEGACY_MODELS:
            raise ValueError('模型配置不存在，请重新选择。')
        duplicate = next((entry for entry in entries if (entry.provider, entry.url, entry.model) == (provider, url, model)), None)
        if duplicate:return duplicate
        entry = ModelConfig(identifier or str(uuid4()), provider, url, model)
        entries = [entry if item.id == entry.id else item for item in entries]
        if not exists:entries.append(entry)
        self._write(self._group(entries,providers))
        return entry

    def _write(self,providers,updated_capacities=None):
        for provider in providers:self.validate_provider(provider.name,provider.url,[m.model for m in provider.models])
        self._validate_entries([m for p in providers for m in p.models])
        data={'version':3,'providers':[{'id':p.id,'name':p.name,'url':p.url,
              'models':[{'id':m.id,'model':m.model} for m in p.models]} for p in providers]}
        old=json.loads(self.path.read_text(encoding='utf8')).get('context_windows',{}) if self.path.exists() else {}
        capacities=dict(old) if isinstance(old,dict) else {}
        capacities.update(updated_capacities or {})
        valid={}
        for provider in providers:
            limits=capacities.get(provider.url,{})
            if isinstance(limits,dict):
                for model in provider.models:
                    if valid_capacity(limits.get(model.model)) is not None:valid.setdefault(provider.url,{})[model.model]=limits[model.model]
        if valid:data['context_windows']=valid
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf8', dir=self.path.parent, prefix='.models-', suffix='.tmp', delete=False) as target:
                temporary = Path(target.name)
                json.dump(data, target, ensure_ascii=False, indent=2)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)
