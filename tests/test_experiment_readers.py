from datetime import date
from io import BytesIO
from zipfile import ZipFile

import pytest

from matknow.experiments import readers
from matknow.experiments.readers import read_record, read_table
from experiment_fixtures import make_docx, make_xlsx, rewrite_zip


def test_docx_keeps_paragraph_table_paragraph_and_nested_cell_order(tmp_path):
    path = make_docx(tmp_path / 'record.docx')
    doc = read_record(path, file_id='file')
    assert [b.text for b in doc.blocks] == ['开始', 'A1', '结束']
    assert '表格' in doc.blocks[1].locator
    make_docx(path, nested=True)
    assert [b.text for b in read_record(path, file_id='file').blocks] == ['开始', 'A1', '内层', '结束']


def test_xlsx_preserves_cell_addresses_formula_and_cache_separately(tmp_path):
    doc = read_record(make_xlsx(tmp_path / 'data.xlsx', cached=2), file_id='file')
    cell = next(c for c in doc.tables[0].cells if (c.row, c.column) == (2, 2))
    assert cell.formula == '=1+1'
    assert cell.cached == '2'
    assert any(b.locator == 'Sheet1!B2' for b in doc.blocks)
    assert doc.sha256


def test_formula_expression_and_cached_value_are_both_visible_in_import_source(tmp_path):
    doc=read_record(make_xlsx(tmp_path/'formula.xlsx',cached=2),file_id='source')
    block=next(b for b in doc.blocks if b.locator=='Sheet1!B2')
    assert '=1+1' in block.text and '2' in block.text and '未重新计算' in block.text


def test_selected_cells_budget_allows_small_range_of_larger_sheet(tmp_path,monkeypatch):
    from openpyxl import Workbook
    path=tmp_path/'larger.xlsx';workbook=Workbook()
    for i in range(10):workbook.active.append([i])
    workbook.save(path);workbook.close();monkeypatch.setattr(readers,'MAX_XLSX_CELLS',5)
    table=read_table(path,file_id='source',bounds=(1,1,3,1))
    assert len(table.cells)==3 and table.cells[-1].text=='2'


def test_formula_without_cache_is_missing_not_zero(tmp_path):
    table = read_table(make_xlsx(tmp_path / 'data.xlsx'), file_id='file')
    cell = next(c for c in table.cells if (c.row, c.column) == (2, 2))
    assert cell.formula == '=1+1'
    assert cell.cached is None


def test_numeric_sample_codes_keep_explicit_zero_padding_and_dates(tmp_path):
    from openpyxl import Workbook
    path = tmp_path / 'data.xlsx'
    wb = Workbook()
    wb.active.append([1, date(2026, 10, 13)])
    wb.active['A1'].number_format = '000'
    wb.save(path)
    wb.close()
    cells = read_table(path, file_id='file').cells
    assert cells[0].text == '001'
    assert cells[1].text.startswith('2026-10-13')


def test_merged_cells_and_duplicate_blank_headers_need_explicit_mapping(tmp_path):
    from openpyxl import Workbook
    path = tmp_path / 'data.xlsx'
    wb = Workbook()
    wb.active.append(['样品', '结果', '结果', ''])
    wb.active.append(['A1', 2, 3, 4])
    wb.active.merge_cells('A2:A3')
    wb.save(path)
    wb.close()
    cells = {(c.row, c.column): c for c in read_table(path, file_id='file').cells}
    assert cells[1, 2].text == cells[1, 3].text == '结果'
    assert cells[3, 1].text == ''


def test_sparse_fake_dimensions_do_not_allocate_huge_ranges(tmp_path):
    doc = read_record(make_xlsx(tmp_path / 'data.xlsx', fake_dimension=True), file_id='file')
    assert len(doc.tables[0].cells) == 4


@pytest.mark.parametrize('setting,value', [('MAX_ZIP_ENTRIES', 1), ('MAX_UNPACKED_BYTES', 10), ('MAX_TEXT_CHARS', 1), ('MAX_XLSX_CELLS', 3)])
def test_ooxml_zip_text_sheet_cell_and_csv_limits(tmp_path, monkeypatch, setting, value):
    path = make_xlsx(tmp_path / 'data.xlsx')
    monkeypatch.setattr(readers, setting, value)
    with pytest.raises(ValueError):
        read_record(path, file_id='file')


@pytest.mark.parametrize('setting,value', [('MAX_CSV_ROWS', 1), ('MAX_CSV_COLUMNS', 1), ('MAX_CSV_CELLS', 3)])
def test_csv_budget_limits(tmp_path, monkeypatch, setting, value):
    path = tmp_path / 'data.csv'
    path.write_text('x,y\n1,2', encoding='utf-8-sig')
    monkeypatch.setattr(readers, setting, value)
    with pytest.raises(ValueError):
        read_table(path, file_id='file')


def test_selected_sheet_and_bounds_are_enforced(tmp_path, monkeypatch):
    from openpyxl import Workbook
    path = tmp_path / 'data.xlsx'
    wb = Workbook()
    wb.active['A1'] = 'first'
    wb.create_sheet('Other')['A1'] = 'second'
    wb.save(path)
    wb.close()
    monkeypatch.setattr(readers, 'MAX_SHEETS', 1)
    with pytest.raises(ValueError):
        read_record(path, file_id='file')
    assert read_record(path, file_id='file', sheets=['Other']).tables[0].cells[0].text == 'second'
    table = read_table(path, file_id='file', sheet='Other', bounds=(1, 1, 1, 1))
    assert len(table.cells) == 1


@pytest.mark.parametrize('suffix', ['.doc', '.xls', '.xlsm', '.docm'])
def test_doc_xls_macros_encrypted_corrupt_and_external_relationships_are_not_executed(tmp_path, suffix):
    path = tmp_path / ('bad' + suffix)
    path.write_bytes(b'unsupported')
    with pytest.raises(ValueError):
        read_record(path, file_id='file')


def test_entity_and_corrupt_zip_rejected_without_external_read(tmp_path):
    path = make_docx(tmp_path / 'record.docx')
    rewrite_zip(path, lambda name, data: b'<!DOCTYPE x [<!ENTITY foo SYSTEM "file:///C:/private">]><x>&foo;</x>' if name == 'word/document.xml' else data)
    with pytest.raises(ValueError):
        read_record(path, file_id='file')
    path.write_bytes(b'not a zip')
    with pytest.raises(ValueError):
        read_record(path, file_id='file')


def test_workbooks_close_on_parse_failure(tmp_path, monkeypatch):
    path = make_xlsx(tmp_path / 'data.xlsx')
    monkeypatch.setattr(readers, 'MAX_TEXT_CHARS', 1)
    with pytest.raises(ValueError):
        read_record(path, file_id='file')
    path.unlink()
    assert not path.exists()
