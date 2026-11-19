from copy import deepcopy
from dataclasses import dataclass, replace

from openpyxl.utils.cell import get_column_letter

from .models import ExperimentDraft, SampleDraft, ResultDraft, SourceRef, Value, validate_draft
from .import_agent import organize_record as organize

@dataclass
class ColumnMap:
    sheet: str
    header_row: int
    bounds: tuple[int, int, int, int]
    sample_column: int
    conditions: dict[int, str]
    metrics: dict[int, tuple[str, str]]
    duplicate_policy: str = 'reject'


def correct_value(value, text, unit=None):
    return replace(deepcopy(value), text=text, unit=value.unit if unit is None else unit, origin='manual', confirmed=True)


def confirm_draft(draft):
    confirmed = deepcopy(draft)
    for value in confirmed.common.values():
        value.confirmed = True
    for sample in confirmed.samples:
        for value in [*sample.conditions.values(), *[r.value for r in sample.results]]:
            value.confirmed = True
    validate_draft(confirmed)
    return confirmed


def from_table(document, mapping, *, title, date):
    table = next((t for t in document.tables if t.name == mapping.sheet), None)
    if table is None or mapping.duplicate_policy not in ('reject', 'repeated_measurements'):
        raise ValueError('工作表或重复样品处理方式无效。')
    r1, c1, r2, c2 = mapping.bounds
    columns = [mapping.sample_column, *mapping.conditions, *mapping.metrics]
    if not (1 <= r1 <= mapping.header_row < r2 and 1 <= c1 <= c2) or any(not isinstance(c, int) or not c1 <= c <= c2 for c in columns) or len(columns) != len(set(columns)):
        raise ValueError('请为样品、条件和结果选择不同的有效列。')
    cells = {(cell.row, cell.column): cell for cell in table.cells}
    samples = {}
    def value(cell, unit=''):
        return Value(cell.cached if cell.formula and cell.cached is not None else ('' if cell.formula else cell.text), unit, 'imported', SourceRef(document.file_id, f'{table.name}!{get_column_letter(cell.column)}{cell.row}'), not bool(cell.formula))
    for row in range(mapping.header_row + 1, r2 + 1):
        if any((row, c) not in cells for c in columns):
            raise ValueError(f'第 {row} 行不在已读取的完整范围中，请重新选择范围。')
        if not any(cells[row, c].text.strip() for c in columns):
            continue
        code_cell = cells[row, mapping.sample_column]
        if code_cell.formula:
            raise ValueError(f'第 {row} 行样品编号是公式，请先改为明确编号。')
        code = code_cell.text.strip()
        if not code:
            raise ValueError(f'第 {row} 行缺少样品编号。')
        key = code.casefold()
        if key in samples and mapping.duplicate_policy == 'reject':
            raise ValueError(f'样品 {code} 重复，请确认是否为同一样品的多次测量。')
        sample = samples.setdefault(key, SampleDraft(code))
        for column, name in mapping.conditions.items():
            cell = cells[row, column]
            if not cell.text.strip():
                continue
            candidate = value(cell)
            if name in sample.conditions and sample.conditions[name].text != candidate.text:
                raise ValueError(f'重复样品 {code} 的条件 {name} 不一致，请修正或拆分样品。')
            sample.conditions[name] = candidate
        for column, (metric, unit) in mapping.metrics.items():
            cell = cells[row, column]
            if not cell.text.strip() and not cell.formula:
                continue
            sample.results.append(ResultDraft(metric, value(cell, unit)))
    draft = ExperimentDraft(title, date, samples=list(samples.values()), source_ids=[document.file_id])
    validate_draft(draft, confirmed=False)
    return draft


