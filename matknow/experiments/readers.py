from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO, StringIO
import csv
import hashlib
import posixpath
from pathlib import Path, PurePosixPath
import re
from zipfile import ZipFile, BadZipFile

from defusedxml import ElementTree as SafeXML
from defusedxml.common import DefusedXmlException

from .files import read_bytes

MAX_ZIP_ENTRIES = 5000
MAX_UNPACKED_BYTES = 100 * 1024 * 1024
MAX_TEXT_CHARS = 1_000_000
MAX_SHEETS = 20
MAX_XLSX_CELLS = 50000
MAX_CSV_ROWS = 50000
MAX_CSV_COLUMNS = 100
MAX_CSV_CELLS = 1_000_000


@dataclass
class Block:
    text: str
    locator: str


@dataclass
class Cell:
    row: int
    column: int
    text: str
    formula: str | None = None
    cached: str | None = None


@dataclass
class Table:
    name: str
    cells: list[Cell]


@dataclass
class ReadDocument:
    file_id: str
    filename: str
    sha256: str
    blocks: list[Block]
    tables: list[Table]
    warnings: list[str]


def _zip(data):
    try:
        archive = ZipFile(BytesIO(data))
        members = archive.infolist()
        if len(members) > MAX_ZIP_ENTRIES or sum(i.file_size for i in members) > MAX_UNPACKED_BYTES:
            raise ValueError('Office 文件解压内容超过限制。')
        seen, total = set(), 0
        for item in members:
            parts = PurePosixPath(item.filename).parts
            if item.filename in seen or not parts or item.filename.startswith('/') or '..' in parts or '\\' in item.filename or item.flag_bits & 1:
                raise ValueError('Office 文件结构无效或已加密。')
            seen.add(item.filename)
            if item.filename.lower().endswith('vbaproject.bin'):
                raise ValueError('暂不读取含宏的 Office 文件。')
            with archive.open(item) as source:
                body = source.read(MAX_UNPACKED_BYTES - total + 1)
            total += len(body)
            if total > MAX_UNPACKED_BYTES:
                raise ValueError('Office 文件解压内容超过限制。')
            if item.filename.endswith(('.xml', '.rels')):
                for _, element in SafeXML.iterparse(BytesIO(body), forbid_dtd=True):
                    element.clear()
        return archive
    except (BadZipFile, DefusedXmlException, SafeXML.ParseError, RuntimeError, ValueError) as exc:
        if 'archive' in locals():
            archive.close()
        if isinstance(exc, ValueError) and not isinstance(exc, DefusedXmlException):
            raise
        raise ValueError('Office 文件损坏、加密或包含不支持的 XML 内容。') from None


def _budget(blocks):
    if sum(len(b.text) for b in blocks) > MAX_TEXT_CHARS:
        raise ValueError('提取文本超过 100 万字符，请拆分资料。')


def _docx(data):
    from docx import Document
    from docx.table import Table as WordTable
    from docx.text.paragraph import Paragraph
    document = Document(BytesIO(data))
    blocks, tables, warnings = [], [], []

    def walk(container, prefix=''):
        seen_cells = set()
        for index, item in enumerate(container.iter_inner_content(), 1):
            if isinstance(item, Paragraph):
                if item.text.strip():
                    blocks.append(Block(item.text, prefix + f'段落 {index}'))
            elif isinstance(item, WordTable):
                name = prefix + f'表格 {len(tables) + 1}'
                cells = []
                tables.append(Table(name, cells))
                for r, row in enumerate(item.rows, 1):
                    for c, cell in enumerate(row.cells, 1):
                        if cell._tc in seen_cells:
                            continue
                        seen_cells.add(cell._tc)
                        cells.append(Cell(r, c, cell.text))
                        walk(cell, f'{name} · 行 {r} 列 {c} · ')
            _budget(blocks)
    walk(document)
    if document.inline_shapes:
        warnings.append('内嵌图片保留在原始 Word 中，未自动识别。')
    if document.element.xpath('.//w:ins | .//w:del'):
        warnings.append('文件含修订标记，请核对原始 Word。')
    return blocks, tables, warnings


def _worksheet_map(archive):
    workbook = SafeXML.fromstring(archive.read('xl/workbook.xml'), forbid_dtd=True)
    relations = SafeXML.fromstring(archive.read('xl/_rels/workbook.xml.rels'), forbid_dtd=True)
    targets = {}
    for relation in relations:
        if relation.get('TargetMode') == 'External':
            continue
        target = relation.get('Target', '')
        targets[relation.get('Id')] = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('xl', target))
    result = {}
    for sheet in workbook.iter():
        if sheet.tag.endswith('}sheet'):
            relation_id = sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            if relation_id in targets:
                result[sheet.get('name')] = targets[relation_id]
    return result


def workbook_sheets(path):
    with _zip(read_bytes(path)) as archive:
        return list(_worksheet_map(archive))


def _display(value, number_format, raw=None):
    if value is None:
        return ''
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bool):
        return 'TRUE' if value else 'FALSE'
    if isinstance(value, int) and re.fullmatch('0+', number_format or ''):
        return str(value).zfill(len(number_format))
    if isinstance(value, (int, float)) and raw is not None:
        return raw
    return str(value)


def _xlsx(data, archive, sheets=None, bounds=None):
    from openpyxl import load_workbook
    from openpyxl.utils.cell import coordinate_to_tuple, get_column_letter, range_boundaries
    sheet_paths = _worksheet_map(archive)
    selected = list(sheet_paths) if sheets is None else sheets
    if not selected or len(selected) > MAX_SHEETS or len(set(selected)) != len(selected) or any(s not in sheet_paths for s in selected):
        raise ValueError('请选择最多 20 个有效工作表。')
    formulas = cached = None
    tables, blocks, warnings, count = [], [], [], 0
    try:
        formulas = load_workbook(BytesIO(data), read_only=True, keep_vba=False, keep_links=False, data_only=False)
        cached = load_workbook(BytesIO(data), read_only=True, keep_vba=False, keep_links=False, data_only=True)
        for name in selected:
            raw_values = {};max_row=max_column=1
            if bounds is not None:
                if len(bounds)!=4 or not all(isinstance(i,int) for i in bounds):raise ValueError('工作表范围无效。')
                br1,bc1,br2,bc2=bounds
                if not (1<=br1<=br2<=1048576 and 1<=bc1<=bc2<=16384):raise ValueError('工作表范围无效。')
            for _, element in SafeXML.iterparse(BytesIO(archive.read(sheet_paths[name])), forbid_dtd=True):
                if element.tag.endswith('}mergeCell'):
                    c1, r1, c2, r2 = range_boundaries(element.get('ref', ''))
                    max_row,max_column=max(max_row,r2),max(max_column,c2)
                    element.clear()
                if element.tag.endswith('}c'):
                    address = element.get('r')
                    if address:
                        position = coordinate_to_tuple(address)
                        max_row,max_column=max(max_row,position[0]),max(max_column,position[1])
                        value = next((child.text for child in element if child.tag.endswith('}v')), None)
                        if bounds is None or (br1<=position[0]<=br2 and bc1<=position[1]<=bc2):
                            raw_values[position]=value
                            if len(raw_values)>MAX_XLSX_CELLS:raise ValueError('Excel 读取范围超过 50000 个单元格，请缩小范围。')
                    element.clear()
            if bounds is None:
                area = (1, 1, max_row, max_column)
            else:
                area = bounds
            r1, c1, r2, c2 = area
            if not all(isinstance(i, int) for i in area) or not (1 <= r1 <= r2 <= 1048576 and 1 <= c1 <= c2 <= 16384):
                raise ValueError('工作表范围无效。')
            count += (r2 - r1 + 1) * (c2 - c1 + 1)
            if count > MAX_XLSX_CELLS:
                raise ValueError('Excel 读取范围超过 50000 个单元格，请缩小范围。')
            cells = []
            actual = formulas[name].iter_rows(min_row=r1, max_row=r2, min_col=c1, max_col=c2)
            cached_rows = cached[name].iter_rows(min_row=r1, max_row=r2, min_col=c1, max_col=c2)
            for row_number, (row, stored_row) in enumerate(zip(actual, cached_rows), r1):
                for column, (cell, stored) in enumerate(zip(row, stored_row), c1):
                    formula = str(cell.value) if cell.data_type == 'f' else None
                    raw = raw_values.get((row_number, column))
                    cached_text = _display(stored.value, cell.number_format, raw) if formula and stored.value is not None else None
                    text = (cached_text or formula) if formula else _display(cell.value, cell.number_format, raw)
                    cells.append(Cell(row_number, column, text, formula, cached_text))
                    if text:
                        visible=f'公式：{formula}；缓存：{cached_text if cached_text is not None else "无缓存"}（未重新计算，待确认）' if formula else text
                        blocks.append(Block(visible, f'{name}!{get_column_letter(column)}{row_number}'))
            tables.append(Table(name, cells))
            _budget(blocks)
        if any(c.formula for t in tables for c in t.cells):
            warnings.append('公式缓存未重新计算，请确认后保存。')
        return blocks, tables, warnings
    finally:
        if formulas is not None:
            formulas.close()
        if cached is not None:
            cached.close()


def read_record(path: Path, *, file_id: str, sheets=None, bounds=None):
    path = Path(path)
    return read_record_bytes(read_bytes(path),filename=path.name,file_id=file_id,sheets=sheets,bounds=bounds)


def read_record_bytes(data,*,filename,file_id,sheets=None,bounds=None):
    suffix=Path(filename).suffix.lower()
    if suffix not in ('.docx', '.xlsx'):
        raise ValueError('请选择 DOCX 或 XLSX；旧 Word/Excel 请先另存为新格式。')
    if len(data)>20*1024*1024:raise ValueError('单个文件不能超过20 MB。')
    try:
        with _zip(data) as archive:
            blocks, tables, warnings = _docx(data) if suffix == '.docx' else _xlsx(data, archive, sheets, bounds)
        return ReadDocument(file_id, filename, hashlib.sha256(data).hexdigest(), blocks, tables, warnings)
    except ValueError:
        raise
    except Exception:
        raise ValueError('Office 记录解析失败，请检查文件或数据范围。') from None


def read_table(path: Path, *, file_id: str, sheet=None, bounds=None):
    path = Path(path)
    data = read_bytes(path)
    if path.suffix.lower() == '.xlsx':
        try:
            with _zip(data) as archive:
                names = list(_worksheet_map(archive))
                selected = sheet if sheet is not None else names[0]
                return _xlsx(data, archive, [selected], bounds)[1][0]
        except ValueError:
            raise
        except Exception:
            raise ValueError('Excel 数据读取失败。') from None
    if path.suffix.lower() != '.csv':
        raise ValueError('数据表目前支持 CSV 或 XLSX。')
    try:
        text = data.decode('utf-8-sig')
        if len(text) > MAX_TEXT_CHARS:
            raise ValueError('数据文本超过 100 万字符。')
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        cells = []
        for r, row in enumerate(csv.reader(StringIO(text), dialect), 1):
            if r > MAX_CSV_ROWS or len(row) > MAX_CSV_COLUMNS or len(cells) + len(row) > MAX_CSV_CELLS:
                raise ValueError('CSV 超过行列或单元格限制。')
            cells.extend(Cell(r, c, value, value if value.startswith('=') else None) for c, value in enumerate(row, 1))
        if bounds is not None:
            r1, c1, r2, c2 = bounds
            if not (1 <= r1 <= r2 and 1 <= c1 <= c2):
                raise ValueError('数据范围无效。')
            cells = [c for c in cells if r1 <= c.row <= r2 and c1 <= c.column <= c2]
        return Table(path.name, cells)
    except (UnicodeError, csv.Error):
        raise ValueError('CSV 需要有效 UTF-8 文本。') from None
