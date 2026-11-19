"""Load the editable Markdown policy once per research task."""
import hashlib
import json
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path


class AgentPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class AgentPolicy:
    prompt: str
    max_rounds: int
    max_validation_errors: int
    sha256: str


def policy_path():
    root = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
    return root/'agent.md'


def load_policy(path=None, *, role=None):
    try:
        with Path(path or policy_path()).open('rb') as source:
            raw = source.read(65537)
        if len(raw) > 65536:raise ValueError
        prompt = raw.decode('utf-8-sig').strip()
        match = re.search(r'^## 运行参数\s*\n```json\s*\n(.*?)\n```', prompt, re.M | re.S)
        instructions = re.search(r'^## 系统指令[ \t]*\r?\n(.*?)(?=^## |\Z)', prompt, re.M | re.S)
        if match is None or instructions is None:raise ValueError
        instruction_text = instructions[1].strip()
        if not instruction_text or re.fullmatch(r'```(?:text)?\s*```', instruction_text):raise ValueError
        values = json.loads(match[1])
        bounds = {'max_rounds': (1, 12), 'max_validation_errors': (1, 3)}
        if not isinstance(values, dict) or set(values)-{'max_context_chars'} != {'version', *bounds} or type(values['version']) is not int or values['version'] != 1:
            raise ValueError
        for key, (minimum, maximum) in bounds.items():
            if type(values[key]) is not int or not minimum <= values[key] <= maximum:raise ValueError
        # Accept old shipped/custom rules; the former character ceiling has no runtime effect.
        if 'max_context_chars' in values and (type(values['max_context_chars']) is not int or values['max_context_chars']<=0):raise ValueError
        policy=AgentPolicy(prompt, values['max_rounds'], values['max_validation_errors'], hashlib.sha256(raw).hexdigest())
        if path is None and role is not None:
            names={'documents':['library'],'experiments':['experiment'],'unified':['library','experiment']}
            if role not in names:raise ValueError
            for name in names[role]:
                specialist=load_policy(policy_path().with_name(name+'_agent.md'))
                policy=replace(policy,prompt=policy.prompt+'\n\n'+specialist.prompt,sha256=hashlib.sha256((policy.sha256+specialist.sha256).encode()).hexdigest())
        return policy
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        name=Path(path).name if path is not None and Path(path).name in ('library_agent.md','experiment_agent.md') else 'agent.md'
        raise AgentPolicyError(name+' 缺失、格式无效或运行参数超限，请检查规则文件后重试。') from None
