from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
import json
from uuid import UUID


@dataclass
class SourceRef:
    file_id: str
    locator: str


@dataclass
class Value:
    text: str
    unit: str = ''
    origin: str = 'manual'
    source: SourceRef | None = None
    confirmed: bool = True


@dataclass
class ResultDraft:
    metric: str
    value: Value
    kind: str = 'number'
    method: str = ''
    test_conditions: dict[str, str] = field(default_factory=dict)
    measured_at: str = ''
    repeat_group: str = ''
    notes: str = ''
    attachment_ids: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)
    id: str | None = None


@dataclass
class SampleDraft:
    code: str
    conditions: dict[str, Value] = field(default_factory=dict)
    results: list[ResultDraft] = field(default_factory=list)
    id: str | None = None


@dataclass
class Reagent:
    name: str
    quantity: str = ''
    unit: str = ''
    specification: str = ''
    source: str = ''
    sample_codes: list[str] = field(default_factory=list)


RECORD_STATUSES = {'unknown':'未标注','planned':'计划中','running':'进行中','completed':'已结束','aborted':'已中止'}


@dataclass
class ChemistryRecord:
    objective: str = ''
    operator: str = ''
    status: str = 'unknown'
    reagents: list[Reagent] = field(default_factory=list)
    setup: str = ''
    deviations: str = ''
    discussion: str = ''
    conclusion: str = ''
    references: str = ''


@dataclass
class ExperimentDraft:
    title: str
    date: str
    common: dict[str, Value] = field(default_factory=dict)
    samples: list[SampleDraft] = field(default_factory=list)
    notes: str = ''
    source_ids: list[str] = field(default_factory=list)
    chemistry: ChemistryRecord = field(default_factory=ChemistryRecord)


@dataclass
class ExperimentSnapshot:
    id: str
    revision: int
    draft: ExperimentDraft
    created_at: str


@dataclass
class ExperimentSummary:
    id: str
    title: str
    date: str
    revision: int
    sample_count: int


@dataclass
class Attachment:
    id: str
    experiment_id: str
    filename: str
    relative_path: str
    sha256: str
    size: int
    role: str
    sample_id: str | None = None
    result_id: str | None = None


def chemistry_text(record, sample_code=None):
    fields=[('实验目的（目标）',record.objective),('操作者（填写）',record.operator),('装置与条件',record.setup),
            ('偏差与中止记录',record.deviations),('讨论（用户解释，非实测事实）',record.discussion),
            ('结论（用户填写，需核对证据）',record.conclusion),('方法依据（参考，非执行记录）',record.references)]
    lines=[f'{name}：{value}' for name,value in fields if value.strip()]
    if record.status!='unknown':lines.insert(0,'实验状态：'+RECORD_STATUSES[record.status])
    for reagent in record.reagents:
        if sample_code is not None and reagent.sample_codes and sample_code.casefold() not in {c.casefold() for c in reagent.sample_codes}:continue
        quantity_label='计划用量' if record.status=='planned' else '记录用量'
        lines.append(f'试剂/材料：{reagent.name}；{quantity_label}：{reagent.quantity or "未记录"} {reagent.unit}；规格/浓度：{reagent.specification or "未记录"}；来源/批号：{reagent.source or "未记录"}；样品：{",".join(reagent.sample_codes) or "全部"}')
    return '\n'.join(lines)


def value_provenance(value: Value) -> str:
    label = {'manual': '人工修正' if value.source else '人工填写',
             'imported': '直接导入', 'model_confirmed': '模型整理后确认'}[value.origin]
    if value.source:
        label += f' · 原文件定位 {value.source.locator}'
        if value.origin == 'manual': label += '（仅供核对，当前值已人工修改）'
    if not value.confirmed: label += ' · 待确认'
    return label


def valid_id(value: str) -> str:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError('记录编号无效。') from None
    return value


def number(text: str) -> Decimal:
    try:
        if not isinstance(text, str) or not text.strip():
            raise ValueError
        value = Decimal(text.strip())
        if not value.is_finite():
            raise ValueError
        return value
    except (InvalidOperation, ValueError):
        raise ValueError('请输入有限数值，缺测请移除数值项或改为文字记录。') from None


def draft_from_dict(data: dict) -> ExperimentDraft:
    def value(raw):
        raw = dict(raw)
        if raw.get('source') is not None:
            raw['source'] = SourceRef(**raw['source'])
        return Value(**raw)

    try:
        raw = dict(data)
        raw['common'] = {k: value(v) for k, v in raw.get('common', {}).items()}
        samples = []
        for sample in raw.get('samples', []):
            sample = dict(sample)
            sample['conditions'] = {k: value(v) for k, v in sample.get('conditions', {}).items()}
            sample['results'] = [ResultDraft(**{**r, 'value': value(r['value'])}) for r in sample.get('results', [])]
            samples.append(SampleDraft(**sample))
        raw['samples'] = samples
        chemistry=raw.get('chemistry',{})
        if not isinstance(chemistry,dict) or not isinstance(chemistry.get('reagents',[]),list):raise ValueError
        raw['chemistry']=ChemistryRecord(**{**chemistry,'reagents':[Reagent(**r) for r in chemistry.get('reagents',[])]})
        return ExperimentDraft(**raw)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError('实验记录结构无效。') from None


def validate_draft(draft: ExperimentDraft, *, confirmed=True):
    def text(value, required=False):
        if not isinstance(value, str) or '\x00' in value or (required and not value.strip()):
            raise ValueError('请填写有效的名称、编号和文字。')

    def fields(values):
        if not isinstance(values, dict):
            raise ValueError('条件字段无效。')
        for key, value in values.items():
            text(key, True)
            check_value(value)

    def check_value(value):
        if not isinstance(value, Value):
            raise ValueError('结果值无效。')
        text(value.text)
        text(value.unit)
        if value.origin not in ('manual', 'imported', 'model_confirmed') or not isinstance(value.confirmed, bool):
            raise ValueError('来源标记无效。')
        if confirmed and not value.confirmed:
            raise ValueError('请先确认提取值和公式缓存。')
        if value.source:
            text(value.source.file_id, True)
            text(value.source.locator, True)

    if not isinstance(draft, ExperimentDraft):
        raise ValueError('实验记录结构无效。')
    text(draft.title, True)
    text(draft.notes)
    try:
        if date.fromisoformat(draft.date).isoformat() != draft.date:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError('实验日期需要有效的 YYYY-MM-DD 日期。') from None
    fields(draft.common)
    if not isinstance(draft.samples, list) or not draft.samples:
        raise ValueError('请至少添加一个样品。')
    codes, ids = set(), set()
    for sample in draft.samples:
        text(sample.code, True)
        code = sample.code.strip().casefold()
        if code in codes:
            raise ValueError('同一实验内样品编号不能重复。')
        codes.add(code)
        fields(sample.conditions)
        for entity in [sample, *sample.results]:
            if entity.id is not None:
                valid_id(entity.id)
                if entity.id in ids:
                    raise ValueError('记录编号不能重复。')
                ids.add(entity.id)
        for result in sample.results:
            text(result.metric, True)
            check_value(result.value)
            if result.kind not in ('number', 'text', 'table', 'curve', 'image', 'pdf', 'raw'):
                raise ValueError('结果类型无效。')
            if result.kind == 'number' and (confirmed or result.value.text.strip()):
                number(result.value.text)
            for item in (result.method, result.measured_at, result.repeat_group, result.notes):
                text(item)
            if not isinstance(result.test_conditions, dict):
                raise ValueError('测试条件无效。')
            for key, item in result.test_conditions.items():
                text(key, True)
                text(item)
            if not isinstance(result.data, dict) or set(result.data) - {'attachment_id', 'sheet', 'bounds', 'cell', 'x_column', 'y_column', 'x_unit', 'y_unit', 'sort_x'}:
                raise ValueError('数据选择无效。')
            for file_id in result.attachment_ids:
                text(file_id, True)
    for file_id in draft.source_ids:
        text(file_id, True)
    chemistry=draft.chemistry
    if not isinstance(chemistry,ChemistryRecord) or not isinstance(chemistry.status,str) or chemistry.status not in RECORD_STATUSES or not isinstance(chemistry.reagents,list):
        raise ValueError('实验详情结构或状态无效。')
    for value in (chemistry.objective,chemistry.operator,chemistry.setup,chemistry.deviations,chemistry.discussion,chemistry.conclusion,chemistry.references):text(value)
    for reagent in chemistry.reagents:
        if not isinstance(reagent,Reagent):raise ValueError('试剂记录无效。')
        text(reagent.name,True)
        for value in (reagent.quantity,reagent.unit,reagent.specification,reagent.source):text(value)
        if not isinstance(reagent.sample_codes,list):raise ValueError('请用当前实验的样品编号关联试剂。')
        seen=set()
        for code in reagent.sample_codes:
            text(code,True)
            if code.casefold() not in codes or code.casefold() in seen:raise ValueError('试剂关联的样品不存在或重复，请核对样品编号。')
            seen.add(code.casefold())
    try:
        encoded = json.dumps(asdict(draft), ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError):
        raise ValueError('记录含无效数据。') from None
    if len(encoded) > 1_000_000:
        raise ValueError('记录文本超过 100 万字符，请拆分实验。')
