"""Complete selected-sample comparisons. Calculations never use retrieved excerpts."""
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import DecimalException, localcontext
import json
from uuid import uuid4

from .models import number


@dataclass
class Comparison:
    sample_ids: list[str]
    conditions: list[dict]
    groups: list[dict]
    warnings: list[str]
    scope: str
    labels: dict[str,str] = field(default_factory=dict)
    calculation: dict | None = None


def compare_samples(store, sample_ids):
    if not 2 <= len(sample_ids) <= 20 or len(set(sample_ids)) != len(sample_ids):
        raise ValueError('请选择 2–20 个不同样品。')
    samples = store.samples(sample_ids)
    evidence = {r['result_id']: r['id'] for r in store.evidence_rows(sample_ids) if r['result_id']}
    values, groups = {}, {}
    for snapshot, sample in samples:
        values[sample.id] = {**snapshot.draft.common, **sample.conditions}
        for result in sample.results:
            key = (result.metric, result.value.unit, result.method,
                   json.dumps(result.test_conditions, sort_keys=True, ensure_ascii=False), result.kind)
            group = groups.setdefault(key, {
                'group_id': str(uuid4()), 'metric': result.metric, 'unit': result.value.unit,
                'method': result.method, 'test_conditions': deepcopy(result.test_conditions),
                'status': 'comparable' if result.method.strip() and result.test_conditions and all(v.strip() for v in result.test_conditions.values()) else 'needs_confirmation',
                'measurements': []})
            group['measurements'].append({
                'result_id': result.id, 'sample_id': sample.id, 'value': deepcopy(result.value),
                'source_id': evidence[result.id], 'kind': result.kind, 'measured_at': result.measured_at,
                'label': f'{snapshot.draft.date} / {snapshot.draft.title} / {sample.code}',
                'experiment_id': snapshot.id, 'revision': snapshot.revision,
                'notes': result.notes, 'repeat_group': result.repeat_group})
    conditions = []
    for name in sorted(set().union(*(v.keys() for v in values.values()))):
        row = {sid: values[sid].get(name) for sid in sample_ids}
        present = {(v.text.strip(), v.unit.strip()) for v in row.values() if v and v.text.strip()}
        missing = any(v is None or not v.text.strip() for v in row.values())
        conditions.append({'condition_name': name, 'status': 'missing' if missing else 'same' if len(present) == 1 else 'different', 'values': row})
    warnings = ['条件差异仅供对照，不能据此确定因果。']
    if any(g['status'] == 'needs_confirmation' for g in groups.values()):
        warnings.append('部分测试条件未记录，请核对后再比较。')
    result=Comparison(list(sample_ids), conditions, list(groups.values()), warnings, f'完整选择：{len(sample_ids)} 个样品（含全部已记录测量）',
                      {s.id:f'{snap.draft.date} / {snap.draft.title} / {s.code} · 版本 {snap.revision}' for snap,s in samples})
    # Local output guard context is deliberately excluded from dataclass serialization/prompts.
    result._database_path=store.knowledge.path
    return result


def confirm_group(comparison, result_ids, *, metric, unit, method, test_conditions):
    if not metric.strip() or not method.strip() or not test_conditions or not all(v.strip() for v in test_conditions.values()):
        raise ValueError('请确认指标、方法和测试条件。')
    result = deepcopy(comparison)
    result.calculation=None
    selected = set(result_ids)
    rows = [r for g in result.groups for r in g['measurements'] if r['result_id'] in selected]
    if not selected or len(selected) != len(result_ids) or len(rows) != len(selected):
        raise ValueError('结果选择无效。')
    if any(r['value'].unit != unit for r in rows) or len({r['kind'] for r in rows}) != 1:
        raise ValueError('不能自动换算单位或合并不同结果类型。')
    for group in result.groups:
        group['measurements'] = [r for r in group['measurements'] if r['result_id'] not in selected]
    result.groups = [g for g in result.groups if g['measurements']]
    result.groups.append({'group_id': str(uuid4()), 'metric': metric, 'unit': unit, 'method': method,
                          'test_conditions': dict(test_conditions), 'status': 'comparable', 'measurements': rows})
    result.warnings.append('本次比较含人工确认的等价映射，原始记录未修改。')
    return result


def rank_measurements(comparison, *, result_ids, descending, reference_result_id=None):
    selected = set(result_ids)
    if not selected or len(selected) != len(result_ids):
        raise ValueError('请选择明确的测量记录。')
    matching = [g for g in comparison.groups if selected.intersection(r['result_id'] for r in g['measurements'])]
    if len(matching) != 1 or matching[0]['status'] != 'comparable':
        raise ValueError('排序需要同一已确认测试组。')
    rows = [deepcopy(r) for r in matching[0]['measurements'] if r['result_id'] in selected]
    if len(rows) != len(selected) or any(r['kind'] != 'number' for r in rows):
        raise ValueError('请选择该组内的数值结果。')
    numbers = {r['result_id']: number(r['value'].text) for r in rows}
    if reference_result_id is not None and reference_result_id not in numbers:
        raise ValueError('参照记录必须包含在当前选择中。')
    try:
        # Precision covers decimal places in every operand; bound pathological exponents.
        exponent_span = max(v.adjusted() for v in numbers.values()) - min(v.as_tuple().exponent for v in numbers.values())
        if exponent_span > 10000:
            raise ValueError('数值精度范围过大，无法安全计算差值。')
        with localcontext() as context:
            context.prec = max(28, exponent_span + 3)
            for row in rows:
                row['difference'] = None if reference_result_id is None else numbers[row['result_id']] - numbers[reference_result_id]
    except DecimalException:
        raise ValueError('数值范围超出可计算范围。') from None
    return sorted(rows, key=lambda r: numbers[r['result_id']], reverse=descending)
