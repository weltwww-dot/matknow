import json
import re
from dataclasses import replace
from uuid import uuid4

from matknow.knowledge import KnowledgeBase, utc_now
from matknow.models import Answer
from matknow.llm import ModelError

class AskService:
    """Local retrieval preview; model questions use ResearchAgent."""

    def __init__(self, knowledge: KnowledgeBase):
        self.knowledge = knowledge

    def ask(self, question: str, document_ids: list[str] | None = None) -> Answer:
        if not question.strip():
            raise ValueError("请输入一个问题。")
        if len(question) > 2000:
            raise ValueError("问题不能超过 2000 字符。")
        question = question.strip()
        sources = [replace(source, citation_number=number) for number, source in enumerate(self.knowledge.search(question, document_ids), 1)]
        if sources:
            status = "retrieval_preview"
            text = "检索预览：未启用模型，以下为相关原文。"
        else:
            status = "insufficient_evidence"
            text = "当前范围未找到匹配片段，请调整关键词或补充资料。"
        answer = Answer(id=str(uuid4()), question=question, answer=text, status=status, created_at=utc_now(), sources=sources)
        self.knowledge.save_answer(answer)
        return answer

def resolve_citation_ids(ids, sources):
    if not isinstance(ids,list):raise ModelError('来源列表无效。')
    allowed={source.id for source in sources}
    numbered={str(i):source.id for i,source in enumerate(sources,1)}
    return [numbered.get(str(item),item) if type(item) is int or isinstance(item,str) and item not in allowed else item for item in ids]


def validate_generated_answer(raw, sources):
    try:
        result = json.loads(raw)
        text, ids, status = result['answer'], result['source_ids'], result['status']
        if not isinstance(text, str) or not text.strip() or not isinstance(ids, list):
            raise ValueError
        allowed = {source.id for source in sources}
        ids = resolve_citation_ids(ids,sources)
        if any(not isinstance(item, str) or item not in allowed for item in ids) or len(ids) != len(set(ids)):
            raise ModelError('来源引用无效：只能使用本轮真实来源或其编号，不能越界、重复或使用文档ID。')
        if status not in ('generated', 'insufficient_evidence'):
            raise ValueError
        numbers = [int(n) for n in re.findall(r'\[(\d+)\]', text)]
        if any(n < 1 or n > len(sources) or sources[n - 1].id not in ids for n in numbers):
            raise ModelError('来源引用无效：正文[N]必须对应sources.number，并将该来源id列入source_ids。')
        if set(ids) != {sources[n - 1].id for n in numbers}:
            raise ModelError('来源引用不一致：source_ids必须恰好对应正文中实际引用的[N]；补上正文引用或移除未引用的id。')
        if status == 'generated' and not ids:
            raise ModelError('回答至少需要一处真实来源引用[N]及对应source_ids；证据不足时用insufficient_evidence。')
        return text.strip(), status
    except (ValueError, TypeError, KeyError, IndexError):
        raise ModelError('回答结构或来源引用未通过校验，请重试或核对原文。') from None
