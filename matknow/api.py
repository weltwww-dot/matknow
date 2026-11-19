import argparse
import json
import os
import secrets
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from matknow.__main__ import default_data_dir
from matknow.knowledge import KnowledgeBase
from matknow.models import Answer
from matknow.agent import ResearchAgent
from matknow.profiles import DEFAULT_PROFILE_ID, ProfileStore
from matknow.credentials import CredentialError, CredentialStore
from matknow.llm import DeepSeekClient


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000, description="当前绑定研究方向的知识库问题")
    document_ids: list[str] | None = Field(default=None, max_length=100, description="资料 UUID；省略为全库，空数组为不检索任何资料")


def build_profile_service(data_dir: Path, profile_id: str = DEFAULT_PROFILE_ID, api_key: str | None = None) -> ResearchAgent:
    profiles = ProfileStore(data_dir)
    profile = profiles.get(profile_id)
    config = profiles.models.get(profile.model)
    knowledge = profiles.knowledge(profile_id)
    if config is None:return ResearchAgent(knowledge, None, profile.description)
    key = api_key
    if key is None:
        key = os.environ.get('MATKNOW_MODEL_API_KEY')
        if key is None and config.url in ('https://api.deepseek.com/chat/completions', 'https://api.deepseek.com/v1/chat/completions'):
            key = os.environ.get('DEEPSEEK_API_KEY')
    if key is None and sys.platform == 'win32':
        key = CredentialStore(data_dir).for_endpoint(config.url).get()
    if key:
        profiles.migrate_legacy_models(CredentialStore(data_dir) if sys.platform=='win32' else None,(config.url,))
    client = DeepSeekClient(key, config.model, base_url=config.url) if key else None
    return ResearchAgent(knowledge, client, profile.description)


def create_app(knowledge: KnowledgeBase, token: str, service: ResearchAgent | None = None, profile_id: str | None = None) -> FastAPI:
    if not token or not token.strip():
        raise ValueError("请设置 MATKNOW_API_TOKEN 后再启动问答服务。")
    app = FastAPI(title="MatKnow Knowledge API", version="0.2.0", description="绑定单个研究方向的知识库问答；配置模型后生成有来源的回答，否则返回检索预览。")
    bearer = HTTPBearer(auto_error=False, scheme_name="MatKnowToken")
    service = service if service is not None else ResearchAgent(knowledge)
    if service.knowledge.path.resolve() != knowledge.path.resolve():
        raise ValueError('问答服务必须绑定同一个方向数据库。')

    def authorize(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if credentials is None or not secrets.compare_digest(credentials.credentials.encode("utf-8"), token.encode("utf-8")):
            raise HTTPException(status_code=401, detail="需要有效的服务访问令牌。", headers={"WWW-Authenticate": "Bearer"})

    @app.get("/health", operation_id="matknow_health")
    def health():
        result = {"status": "ok", "mode": "model_qa" if service.model_client is not None else "retrieval_preview"}
        if profile_id is not None:
            result['profile_id'] = profile_id
        return result

    @app.post("/v1/ask", response_model=Answer, dependencies=[Depends(authorize)], operation_id="matknow_ask")
    def ask(request: AskRequest):
        try:
            return service.ask(request.question, request.document_ids)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app


def main():
    parser = argparse.ArgumentParser(description="材知 API 适配服务")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument('--profile', default=DEFAULT_PROFILE_ID, help='绑定的 Profile UUID，默认材料制备方向，不跟随桌面切换')
    parser.add_argument("--export-openapi", type=Path)
    arguments = parser.parse_args()
    if arguments.export_openapi:
        with tempfile.TemporaryDirectory(prefix="matknow-schema-") as temporary:
            app = create_app(KnowledgeBase(Path(temporary) / "schema.sqlite3"), "schema-export-only")
            arguments.export_openapi.parent.mkdir(parents=True, exist_ok=True)
            arguments.export_openapi.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
        return
    token = os.environ.get("MATKNOW_API_TOKEN", "")
    if not token.strip():
        parser.error("请先设置 MATKNOW_API_TOKEN；不会启动未鉴权的问答服务。")
    try:
        service = build_profile_service(arguments.data_dir, arguments.profile)
    except (ValueError, CredentialError) as exc:
        parser.error(str(exc))
    except (OSError, sqlite3.Error):
        parser.error('无法打开研究方向配置或数据库，请检查数据目录权限。')
    app = create_app(service.knowledge, token, service=service, profile_id=arguments.profile)
    import uvicorn
    uvicorn.run(app, host=arguments.host, port=arguments.port)


if __name__ == "__main__":
    main()
