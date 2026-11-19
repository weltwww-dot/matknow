from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
import re


def make_docx(path, nested=False):
    from docx import Document
    doc = Document()
    doc.add_paragraph('开始')
    cell = doc.add_table(rows=1, cols=1).cell(0, 0)
    cell.text = 'A1'
    if nested:
        cell.add_table(rows=1, cols=1).cell(0, 0).text = '内层'
    doc.add_paragraph('结束')
    doc.save(path)
    return path


def rewrite_zip(path, transform):
    output = BytesIO()
    with ZipFile(path) as source, ZipFile(output, 'w', ZIP_DEFLATED) as target:
        for item in source.infolist():
            target.writestr(item.filename, transform(item.filename, source.read(item)))
    path.write_bytes(output.getvalue())


def make_xlsx(path, cached=None, fake_dimension=False):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = 'Sheet1'
    ws.append(['样品', '强度'])
    ws.append(['A1', '=1+1'])
    wb.save(path)
    wb.close()
    def transform(name, data):
        if name == 'xl/worksheets/sheet1.xml':
            if cached is not None:
                data = data.replace(b'<f>1+1</f><v></v>', b'<f>1+1</f><v>' + str(cached).encode() + b'</v>')
            if fake_dimension:
                data = re.sub(rb'<dimension ref="[^"]+"', b'<dimension ref="A1:XFD1048576"', data)
        return data
    rewrite_zip(path, transform)
    return path
