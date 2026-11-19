from copy import deepcopy
from pathlib import Path

import pytest
from PySide6.QtGui import QImage
from pypdf import PdfWriter

from matknow.experiments.preview import CurveSpec, curve_segments, reduce_curve, load_image, AttachmentPreview
from matknow.experiments.readers import Cell, Table, read_table


def test_curve_gaps_order_units_and_constant_axes_are_preserved():
    table = Table('Data', [Cell(i+1, c+1, str(v)) for i, row in enumerate([(3, 2), (1, 2), ('', ''), (2, 2)]) for c, v in enumerate(row)])
    spec = CurveSpec(1, 2, 's', 'mA')
    assert curve_segments(table, spec) == [[(3., 2.), (1., 2.)], [(2., 2.)]]
    spec.sort_x = True
    assert curve_segments(table, spec)[0] == [(1., 2.), (3., 2.)]


def test_minmax_preview_budget_retains_single_peak_and_original_table():
    table = Table('Data', [Cell(i+1, c, str(i if c == 1 else 1000 if i == 5000 else 0)) for i in range(10000) for c in [1, 2]])
    before = deepcopy(table.cells)
    segments = curve_segments(table, CurveSpec(1, 2))
    reduced = reduce_curve(segments)
    assert sum(map(len, reduced)) <= 2000
    assert (5000., 1000.) in reduced[0]
    assert table.cells == before
    with pytest.raises(ValueError): reduce_curve([[(1., 1.)]] * 2001)


def test_csv_xlsx_cell_selection_retains_measurement_source(tmp_path):
    p = tmp_path / 'data.csv'; p.write_text('x,y\n1,2\n2,4\n', encoding='utf8')
    table = read_table(p, file_id='temp', bounds=(2, 1, 3, 2))
    assert [(c.row, c.column) for c in table.cells] == [(2, 1), (2, 2), (3, 1), (3, 2)]
    assert curve_segments(table, CurveSpec(1, 2)) == [[(1., 2.), (2., 4.)]]


def test_image_signature_dimensions_and_decode_failure_are_checked(tmp_path, monkeypatch):
    p = tmp_path / 'photo.png'; image = QImage(30, 20, QImage.Format.Format_RGB32); image.fill(0xff228866); image.save(str(p))
    assert load_image(p).width() == 30
    p.write_text('not a png')
    with pytest.raises(ValueError): load_image(p)
    image.save(str(p))
    import matknow.experiments.preview as module
    monkeypatch.setattr(module, 'MAX_IMAGE_PIXELS', 599)
    with pytest.raises(ValueError): load_image(p)


class Archive:
    def __init__(self, path): self.path = path
    def path_for(self, _): return self.path


def test_pdf_page_text_and_scanned_attachment_status(qtbot, tmp_path):
    p = tmp_path / 'scan.pdf'; writer = PdfWriter(); writer.add_blank_page(width=100, height=100)
    with p.open('wb') as f: writer.write(f)
    preview = AttachmentPreview(); qtbot.addWidget(preview)
    preview.show_attachment(Archive(p), 'file')
    assert '原始附件' in preview.message.text()


def test_unknown_format_has_only_save_action_and_no_auto_open(qtbot, tmp_path):
    p = tmp_path / 'instrument.xyz'; p.write_bytes(b'raw instrument bytes')
    preview = AttachmentPreview(); qtbot.addWidget(preview)
    preview.show_attachment(Archive(p), 'file')
    assert preview.save_button.isEnabled()
    assert '原始附件' in preview.message.text()


def test_preview_failure_recovers_controls(qtbot, tmp_path):
    p = tmp_path / 'broken.png'; p.write_bytes(b'bad')
    preview = AttachmentPreview(); qtbot.addWidget(preview)
    preview.show_attachment(Archive(p), 'file')
    assert preview.isEnabled() and preview.save_button.isEnabled()
    assert '图片' in preview.message.text()


def test_curve_axes_fit_available_small_preview_area(qtbot):
    from matknow.experiments.preview import CurveCanvas
    canvas=CurveCanvas([[(0.,1.),(1.,2.)]],CurveSpec(1,2,'s','mA'));qtbot.addWidget(canvas)
    canvas.resize(500,140)
    assert canvas.height()==140


@pytest.mark.parametrize('value',[1e20,1.79e308])
def test_constant_large_axes_render_without_dividing_by_zero(qtbot,value):
    from matknow.experiments.preview import CurveCanvas
    canvas=CurveCanvas([[(value,value),(value,value)]],CurveSpec(1,2));qtbot.addWidget(canvas)
    canvas.resize(500,140)
    assert not canvas.grab().isNull()


def test_reduced_curve_is_labeled_and_table_paging_hides_on_curve_tab(qtbot,tmp_path):
    from matknow.experiments.preview import prepare_attachment
    p=tmp_path/'curve.csv';p.write_text('\n'.join(f'{i},{i%5}' for i in range(3000)),encoding='utf8')
    archive=Archive(p);payload=prepare_attachment(archive,'file',CurveSpec(1,2,'s','mA'))
    preview=AttachmentPreview();qtbot.addWidget(preview);preview.apply_prepared(archive,'file',payload);preview.content.setCurrentIndex(1)
    assert '3000' in preview.message.text() and '简化' in preview.message.text()
    assert preview.next.isHidden()


def test_small_preview_can_open_large_curve_dialog(qtbot,tmp_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from matknow.experiments.preview import CurveCanvas,prepare_attachment
    p=tmp_path/'curve.csv';p.write_text('0,1\n1,2\n',encoding='utf8')
    preview=AttachmentPreview();qtbot.addWidget(preview);preview.apply_prepared(Archive(p),'file',prepare_attachment(Archive(p),'file',CurveSpec(1,2,'s','mA')));preview.content.setCurrentIndex(1)
    seen=[]
    def inspect():
        dialog=QApplication.activeModalWidget()
        if dialog:
            canvas=dialog.findChild(CurveCanvas);seen.append(canvas.height()>200);dialog.reject()
    QTimer.singleShot(30,inspect)
    preview.zoom_preview()
    assert seen==[True]
