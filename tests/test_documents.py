import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from matknow.documents import parse_file, parse_text


def make_pdf(path, text=None, encrypted=False):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    if text:
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("test-password")
    with path.open("wb") as output:
        writer.write(output)


def test_text_paragraphs_and_long_chunks_keep_locators():
    chunks = parse_text("第一段干燥。\n\n第二段热处理。")
    assert [chunk.locator for chunk in chunks] == ["段落 1", "段落 2"]
    long_chunks = parse_text("材料" * 1500)
    assert len(long_chunks) > 1
    assert all(len(chunk.text) <= 1000 for chunk in long_chunks)
    assert all(chunk.locator.startswith("段落 1") for chunk in long_chunks)


def test_unicode_filename_and_utf8_bom(tmp_path):
    path = tmp_path / "材料方法.MD"
    path.write_text("干燥时间为 6 h。", encoding="utf-8-sig")
    assert parse_file(path)[0].text == "干燥时间为 6 h。"


def test_pdf_text_keeps_physical_page(tmp_path):
    path = tmp_path / "paper.pdf"
    make_pdf(path, "Drying temperature: 60 C.")
    chunks = parse_file(path)
    assert chunks[0].locator.startswith("第 1 页")
    assert "60 C" in chunks[0].text


@pytest.mark.parametrize("case", ["blank", "encrypted", "broken", "unsupported", "invalid_utf8"])
def test_invalid_documents_report_clear_errors(tmp_path, case):
    path = tmp_path / ("file.exe" if case == "unsupported" else "file.txt" if case == "invalid_utf8" else "file.pdf")
    if case in {"blank", "encrypted"}:
        make_pdf(path, encrypted=case == "encrypted")
    else:
        path.write_bytes(b"not-a-pdf" if case != "invalid_utf8" else b"\xff\xfe")
    with pytest.raises(ValueError):
        parse_file(path)


def test_size_limit(tmp_path):
    path = tmp_path / "large.txt"
    with path.open("wb") as output:
        output.truncate(20 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="20 MB"):
        parse_file(path)


@pytest.mark.parametrize('suffix',['.docx','.xlsx'])
def test_library_office_import_reuses_safe_reader_and_keeps_locations(tmp_path,suffix):
    path=tmp_path/('方法资料'+suffix)
    if suffix=='.docx':
        from docx import Document
        document=Document();document.add_paragraph('记录操作和观察。');document.save(path)
    else:
        from openpyxl import Workbook
        document=Workbook();document.active.title='条件';document.active.append(['温度','60 °C']);document.save(path);document.close()
    chunks=parse_file(path)
    assert chunks and all(c.locator for c in chunks)
    assert ('记录操作' in chunks[0].text) if suffix=='.docx' else any('60 °C' in c.text and '条件!' in c.locator for c in chunks)
