import json
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from uuid import UUID, uuid4

from matknow.knowledge import KnowledgeBase, utc_now
from matknow.model_config import ModelCatalog, LEGACY_MODELS

DEFAULT_PROFILE_ID = '00000000-0000-0000-0000-000000000001'
DEFAULT_MODEL = None


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    description: str
    model: str | None
    created_at: str
    provider: str | None = None


class ProfileStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.models = ModelCatalog(self.root)
        self.path = self.root / 'profiles.json'
        if not self.path.exists():
            KnowledgeBase(self.root / 'knowledge.sqlite3')
            default = Profile(DEFAULT_PROFILE_ID, '材料制备', '', DEFAULT_MODEL, utc_now())
            self._write([default], default.id)
        self._read()

    def _read(self) -> tuple[list[Profile], str]:
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if data['version'] != 1 or not isinstance(data['profiles'], list) or not data['profiles']:
                raise ValueError
            profiles = [Profile(**item) for item in data['profiles']]
            ids, names = set(), set()
            for index,profile in enumerate(profiles):
                if str(UUID(profile.id)) != profile.id or profile.id in ids:
                    raise ValueError
                try:self.models.get(profile.model)
                except ValueError:
                    if profile.model not in LEGACY_MODELS and str(UUID(profile.model))!=profile.model:raise
                    profile=replace(profile,model=None);profiles[index]=profile
                if profile.provider is not None and str(UUID(profile.provider))!=profile.provider:raise ValueError
                provider=self.models.provider_for_model(profile.model)
                if provider is None and profile.provider is not None:
                    provider=next((p for p in self.models.list_providers() if p.id==profile.provider),None)
                profile=replace(profile,provider=provider.id if provider else None);profiles[index]=profile
                name, description, model = self._validate(profile.name, profile.description, profile.model)
                if name != profile.name or name.casefold() in names or not isinstance(profile.created_at, str):
                    raise ValueError
                ids.add(profile.id)
                names.add(name.casefold())
            if DEFAULT_PROFILE_ID not in ids or data['current_id'] not in ids:
                raise ValueError
            return profiles, data['current_id']
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError) as exc:
            raise ValueError('研究方向配置损坏或版本不支持，请保留原文件并检查 profiles.json。') from exc

    def _write(self, profiles: list[Profile], current_id: str):
        data = {'version': 1, 'current_id': current_id, 'profiles': [asdict(p) for p in profiles]}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.root, prefix='.profiles-', suffix='.tmp', delete=False) as target:
                temporary = Path(target.name)
                json.dump(data, target, ensure_ascii=False, indent=2)
                target.write('\n')
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _validate(self, name: str, description: str, model: str | None):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60 or any(ord(c) < 32 for c in name):
            raise ValueError('方向名称需要 1–60 个字符，且不能含换行或控制字符。')
        if not isinstance(description, str) or len(description) > 2000:
            raise ValueError('方向说明不能超过 2000 字符。')
        self.models.get(model)
        return name.strip(), description.strip(), model

    @property
    def current_id(self) -> str:
        return self._read()[1]

    def migrate_legacy_models(self,credentials=None,authenticated_urls=()):
        known={m.id for m in self.models.list_models()}
        candidates={p.model:self.models.get(p.model) for p in self.list_profiles()
                    if p.model in LEGACY_MODELS and p.model not in known}
        available={url:True for url in authenticated_urls}
        entries=[]
        for config in candidates.values():
            if config.url not in available:
                scoped=credentials.for_endpoint(config.url) if hasattr(credentials,'for_endpoint') else credentials
                available[config.url]=bool(scoped and scoped.get())
            if available[config.url]:entries.append(config)
        # Validate every credential before the first write can disable legacy fallback.
        self.models.retain_legacy(entries)

    def list_profiles(self) -> list[Profile]:
        return self._read()[0]

    def get(self, profile_id: str) -> Profile:
        for profile in self.list_profiles():
            if profile.id == profile_id:
                return profile
        raise ValueError('找不到指定的研究方向。')

    def knowledge(self, profile_id: str) -> KnowledgeBase:
        profile = self.get(profile_id)
        path = self.root / ('knowledge.sqlite3' if profile.id == DEFAULT_PROFILE_ID else f'profiles/{profile.id}/knowledge.sqlite3')
        if not path.resolve().is_relative_to(self.root):
            raise ValueError('研究方向数据库路径超出数据目录。')
        if not path.is_file():
            raise ValueError('研究方向数据库缺失，请检查数据目录；不会自动创建空库覆盖原方向。')
        return KnowledgeBase(path)

    def create(self, name: str, description: str = '', model: str | None = DEFAULT_MODEL, provider_id: str | None = None) -> Profile:
        name, description, model = self._validate(name, description, model)
        profiles, current = self._read()
        if any(p.name.casefold() == name.casefold() for p in profiles):
            raise ValueError('已有同名研究方向，请换个名称。')
        provider=self.models.provider_for_model(model) if model else self.models.get_provider(provider_id) if provider_id else None
        profile = Profile(str(uuid4()), name, description, model, utc_now(),provider.id if provider else None)
        KnowledgeBase(self.root / 'profiles' / profile.id / 'knowledge.sqlite3')
        self._write(profiles + [profile], current)
        return profile

    def update(self, profile_id: str, name: str, description: str, model: str | None, provider_id: str | None = None) -> Profile:
        name, description, model = self._validate(name, description, model)
        profiles, current = self._read()
        existing = next((p for p in profiles if p.id == profile_id), None)
        if existing is None:
            raise ValueError('找不到指定的研究方向。')
        if any(p.id != profile_id and p.name.casefold() == name.casefold() for p in profiles):
            raise ValueError('已有同名研究方向，请换个名称。')
        provider=self.models.provider_for_model(model) if model else self.models.get_provider(provider_id or existing.provider) if provider_id or existing.provider else None
        updated = replace(existing, name=name, description=description, model=model,provider=provider.id if provider else None)
        self._write([updated if p.id == profile_id else p for p in profiles], current)
        return updated

    def select(self, profile_id: str):
        profiles, _ = self._read()
        if profile_id not in {p.id for p in profiles}:
            raise ValueError('找不到指定的研究方向。')
        self._write(profiles, profile_id)
