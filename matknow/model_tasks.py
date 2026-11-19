"""Shared bounded model extraction; callers validate before changing local data."""
import json

from matknow.agent_policy import load_policy, policy_path
from matknow.llm import ModelError
from matknow.tasks import check_cancelled
from matknow.model_context import fits_context


class TaskContextError(ModelError):
    pass


class TaskValidationError(ModelError):
    pass


def complete_task(client, specialist, task, context, validate):
    if client is None:raise ModelError('请先连接模型，或使用手动整理。')
    if specialist not in ('library','experiment'):raise ValueError('智能体类型无效。')
    policy=load_policy(policy_path().with_name(specialist+'_agent.md'))
    payload={'task':task,**context}
    for attempt in range(policy.max_validation_errors):
        check_cancelled(client)
        content=json.dumps(payload,ensure_ascii=False,allow_nan=False)
        messages=[{'role':'system','content':policy.prompt},{'role':'user','content':content}]
        if not fits_context(messages,getattr(client,'context_window',None)):
            raise TaskContextError('本次原文超过模型上下文限制，请缩小读取范围或拆分文件；未省略内容生成草稿。')
        raw=client.complete(messages,json_output=True)
        try:return validate(json.loads(raw))
        except (ValueError,TypeError,KeyError,AttributeError) as exc:
            payload['validation_feedback']=str(exc) or '返回结构或来源定位无效，请按协议重新整理。'
    detail='具体问题：'+payload['validation_feedback'] if task in ('write_report','review_report') else ''
    raise TaskValidationError('模型整理未通过结构或来源校验，原记录未修改，请重试或手动核对。'+detail)
