"""Bounded previews of originals. No fitting, image inference or system file opening."""
from dataclasses import dataclass
import math
from pathlib import Path
import shutil
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QImageReader, QPainter, QPen
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                              QPlainTextEdit, QTableWidget, QTableWidgetItem, QFileDialog, QTabWidget, QDialog)

from matknow.documents import parse_file
from .readers import read_table

MAX_IMAGE_PIXELS = 16_000_000


@dataclass
class CurveSpec:
    x_column: int
    y_column: int
    x_unit: str = ''
    y_unit: str = ''
    sort_x: bool = False


def curve_segments(table, spec):
    if spec.x_column < 1 or spec.y_column < 1 or spec.x_column == spec.y_column:
        raise ValueError('请选择不同的 X、Y 列。')
    rows = {}
    for cell in table.cells:
        if cell.column in (spec.x_column, spec.y_column):
            rows.setdefault(cell.row, {})[cell.column] = cell
    segments, segment, previous = [], [], None
    for index, cells in sorted(rows.items()):
        try:
            x, y = cells[spec.x_column], cells[spec.y_column]
            if x.formula or y.formula:
                raise ValueError  # Cached formulas require a separate confirmed numeric result.
            point = float(x.text), float(y.text)
            if not all(math.isfinite(v) for v in point):
                raise ValueError
        except (KeyError, ValueError, OverflowError):
            if segment: segments.append(segment)
            segment, previous = [], None
            continue
        if previous is not None and index != previous + 1:
            if segment: segments.append(segment)
            segment = []
        segment.append(point)
        previous = index
    if segment: segments.append(segment)
    if spec.sort_x:
        segments = [sorted(part) for part in segments]
    if not segments:
        raise ValueError('所选列没有可绘制的有限数值。')
    return segments


def reduce_curve(segments, max_points=2000):
    if max_points < 1:
        raise ValueError('绘图点数无效。')
    parts = [part for part in segments if part]
    budgets = [min(len(part), 4) for part in parts]
    if sum(budgets) > max_points:
        raise ValueError('缺测区段过多，请缩小绘图范围。')
    remaining = max_points - sum(budgets)
    needs = sum(len(part) - budget for part, budget in zip(parts, budgets))
    if needs:
        extra = [min(len(part) - budget, int(remaining * (len(part) - budget) / needs)) for part, budget in zip(parts, budgets)]
        budgets = [a+b for a, b in zip(budgets, extra)]
    result = []
    for part, budget in zip(parts, budgets):
        if len(part) <= budget:
            result.append(list(part)); continue
        indices = {0, len(part)-1}
        buckets = max(1, (budget-2)//2)
        for i in range(buckets):
            start = 1 + (len(part)-2)*i//buckets
            stop = 1 + (len(part)-2)*(i+1)//buckets
            if start < stop:
                indices.add(min(range(start, stop), key=lambda j: part[j][1]))
                indices.add(max(range(start, stop), key=lambda j: part[j][1]))
        result.append([part[i] for i in sorted(indices)])
    return result


def load_image(path):
    reader = QImageReader(str(path))
    if bytes(reader.format()).lower() not in (b'png', b'jpeg', b'jpg'):
        raise ValueError('图片需要有效的 PNG 或 JPEG 文件。')
    size = reader.size()
    if size.width() <= 0 or size.height() <= 0 or size.width()*size.height() > MAX_IMAGE_PIXELS:
        raise ValueError('图片尺寸无效或超过 1600 万像素。')
    reader.setAllocationLimit(80)
    if max(size.width(), size.height()) > 1600:
        reader.setScaledSize(size.scaled(1600, 1600, Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        raise ValueError('图片解码失败，原始附件仍保留。')
    return image


def prepare_attachment(archive, attachment_id, curve=None, *, sheet=None, bounds=None):
    """Run in the root worker; returns QImage, never creates QPixmap off the GUI thread."""
    path = archive.path_for(attachment_id)
    suffix = path.suffix.lower()
    if suffix in ('.png', '.jpg', '.jpeg'):
        return ('image', load_image(path))
    if suffix in ('.csv', '.xlsx'):
        table = read_table(path, file_id=attachment_id, sheet=sheet, bounds=bounds)
        segments=curve_segments(table,curve) if curve else None
        reduced=reduce_curve(segments) if segments else None
        counts={'original':sum(map(len,segments)),'displayed':sum(map(len,reduced))} if segments else None
        return ('table', table, reduced, curve,counts)
    if suffix == '.pdf':
        try:
            return ('text', '\n\n'.join(f'{c.locator}\n{c.text}' for c in parse_file(path)))
        except ValueError as exc:
            return ('raw', f'{exc}\n原始附件已保存。')
    return ('raw', '原始附件已保存，可另存为。')


class CurveCanvas(QWidget):
    def __init__(self, segments, spec, parent=None):
        super().__init__(parent)
        self.segments, self.spec = segments, spec

    def paintEvent(self, event):
        painter=QPainter(self)
        try:self.draw_curve(painter)
        finally:painter.end()

    def draw_curve(self,painter):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.height()<100 or self.width()<160:
            painter.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,'区域较小，请放大预览')
            return
        area = QRectF(65, 20, max(10, self.width()-90), max(10, self.height()-65))
        points = [p for segment in self.segments for p in segment]
        xmin, xmax = min(p[0] for p in points), max(p[0] for p in points)
        ymin, ymax = min(p[1] for p in points), max(p[1] for p in points)
        # Normalise before subtraction, so finite extreme floats cannot overflow an axis span.
        xscale, yscale = max(abs(xmin), abs(xmax), 1), max(abs(ymin), abs(ymax), 1)
        def axis(low,high,scale):
            low,high=low/scale,high/scale
            if low==high:
                margin=0.05 if abs(low)>=0.5 else 0.5
                low=max(-sys.float_info.max/scale,low-margin)
                high=min(sys.float_info.max/scale,high+margin)
            return low,high
        xlow,xhigh=axis(xmin,xmax,xscale);ylow,yhigh=axis(ymin,ymax,yscale)
        def project(p):
            x = (p[0]/xscale - xlow)/(xhigh-xlow)
            y = (p[1]/yscale - ylow)/(yhigh-ylow)
            return QPointF(area.left()+x*area.width(), area.bottom()-y*area.height())
        painter.setPen(QPen(Qt.GlobalColor.darkGray, 1))
        painter.drawLine(area.bottomLeft(), area.bottomRight()); painter.drawLine(area.bottomLeft(), area.topLeft())
        painter.drawText(QRectF(0, 0, self.width(), 20), Qt.AlignmentFlag.AlignCenter, f'Y ({self.spec.y_unit or "未设单位"})')
        painter.drawText(QRectF(0, self.height()-23, self.width(), 20), Qt.AlignmentFlag.AlignCenter, f'X ({self.spec.x_unit or "未设单位"})')
        painter.drawText(5, 33, f'{yhigh*yscale:.3g}'); painter.drawText(5, int(area.bottom()), f'{ylow*yscale:.3g}')
        painter.drawText(int(area.left()), int(area.bottom()+18), f'{xlow*xscale:.3g}')
        painter.drawText(int(area.right()-55), int(area.bottom()+18), f'{xhigh*xscale:.3g}')
        painter.setPen(QPen(Qt.GlobalColor.darkCyan, 2))
        for segment in self.segments:
            if len(segment) == 1:
                painter.drawEllipse(project(segment[0]), 2, 2)
            for a, b in zip(segment, segment[1:]): painter.drawLine(project(a), project(b))


class ImageCanvas(QWidget):
    def __init__(self,image,parent=None):
        super().__init__(parent);self.image=image;self.setMinimumSize(100,100)

    def paintEvent(self,event):
        size=self.image.size().scaled(self.size(),Qt.AspectRatioMode.KeepAspectRatio)
        area=QRectF((self.width()-size.width())/2,(self.height()-size.height())/2,size.width(),size.height())
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform);painter.drawImage(area,self.image);painter.end()


class AttachmentPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.archive, self.attachment_id, self.table, self.page = None, None, None, 0
        self.prepared=None
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout(); self.message = QLabel(''); self.message.setWordWrap(True)
        bar.addWidget(self.message, 1)
        self.save_button = QPushButton('另存为'); self.save_button.clicked.connect(self.save_original); self.save_button.setEnabled(False)
        bar.addWidget(self.save_button); layout.addLayout(bar)
        self.content = QTabWidget(); layout.addWidget(self.content)
        self.zoom_button=QPushButton('放大预览');self.zoom_button.clicked.connect(self.zoom_preview)
        self.content.setCornerWidget(self.zoom_button);self.zoom_button.hide()
        pages = QHBoxLayout()
        self.previous = QPushButton('上一页'); self.next = QPushButton('下一页')
        self.previous.clicked.connect(lambda: self.show_page(self.page-1)); self.next.clicked.connect(lambda: self.show_page(self.page+1))
        pages.addWidget(self.previous); pages.addWidget(self.next); pages.addStretch(); layout.addLayout(pages)
        self.previous.hide(); self.next.hide()
        self.content.currentChanged.connect(self.update_paging)

    def clear(self):
        while self.content.count():
            widget = self.content.widget(0); self.content.removeTab(0); widget.deleteLater()
        self.archive, self.attachment_id, self.table, self.page = None, None, None, 0
        self.prepared=None;self.zoom_button.hide()
        self.message.clear(); self.save_button.setEnabled(False); self.previous.hide(); self.next.hide()

    def show_attachment(self, archive, attachment_id, curve=None):
        self.clear(); self.archive, self.attachment_id = archive, attachment_id
        try:
            self.apply_prepared(archive, attachment_id, prepare_attachment(archive, attachment_id, curve))
        except (ValueError, OSError) as exc:
            self.message.setText(str(exc)); self.save_button.setEnabled(True)
        finally:
            self.set_busy(False)

    def apply_prepared(self, archive, attachment_id, payload):
        self.clear(); self.archive, self.attachment_id = archive, attachment_id
        self.prepared=payload;self.zoom_button.setVisible(payload[0] in ('image','table','text'))
        self.save_button.setEnabled(True)
        self.message.setText(archive.attachment(attachment_id).filename if hasattr(archive, 'attachment') else '附件预览')
        if payload[0] == 'image':
            self.content.addTab(ImageCanvas(payload[1]), '图片')
        elif payload[0] == 'table':
            self.table = payload[1]
            self.grid = QTableWidget(); self.grid.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            self.content.addTab(self.grid, self.table.name); self.show_page(0)
            if payload[2]: self.content.addTab(CurveCanvas(payload[2], payload[3]), '曲线')
            if len(payload)>4 and payload[4]:
                counts=payload[4]
                if counts['displayed']<counts['original']:
                    self.message.setText(self.message.text()+f' · 已简化预览：{counts["displayed"]}/{counts["original"]} 点，原始数据完整保留')
        elif payload[0] == 'text':
            text = QPlainTextEdit(); text.setReadOnly(True); text.setPlainText(payload[1]); self.content.addTab(text, '文字')
        else:
            self.message.setText(payload[1])

    def show_page(self, page):
        rows = sorted({c.row for c in self.table.cells})
        self.page = max(0, min(page, max(0, (len(rows)-1)//100)))
        selected = rows[self.page*100:(self.page+1)*100]
        columns = sorted({c.column for c in self.table.cells})
        self.grid.clear(); self.grid.setRowCount(len(selected)); self.grid.setColumnCount(len(columns))
        self.grid.setVerticalHeaderLabels([str(i) for i in selected]); self.grid.setHorizontalHeaderLabels([str(i) for i in columns])
        rowmap, colmap = {r:i for i,r in enumerate(selected)}, {c:i for i,c in enumerate(columns)}
        for cell in self.table.cells:
            if cell.row in rowmap:
                text = cell.text if not cell.formula else f'{cell.formula} → {cell.cached or "无缓存"}（待确认）'
                self.grid.setItem(rowmap[cell.row], colmap[cell.column], QTableWidgetItem(text))
        self.previous.setVisible(True); self.next.setVisible(True)
        self.previous.setEnabled(self.page > 0); self.next.setEnabled((self.page+1)*100 < len(rows))
        self.update_paging()

    def update_paging(self,*args):
        visible=self.table is not None and self.content.currentWidget() is getattr(self,'grid',None)
        self.previous.setVisible(visible);self.next.setVisible(visible)

    def zoom_preview(self):
        if self.prepared is None:return
        dialog=QDialog(self);dialog.setWindowTitle('附件预览');dialog.resize(940,650)
        layout=QVBoxLayout(dialog);preview=AttachmentPreview(dialog);layout.addWidget(preview)
        preview.apply_prepared(self.archive,self.attachment_id,self.prepared)
        preview.content.setCurrentIndex(self.content.currentIndex());preview.zoom_button.hide()
        dialog.exec()
        dialog.deleteLater()

    def set_busy(self, busy):
        self.setEnabled(not busy)

    def save_original(self):
        if not self.archive or not self.attachment_id: return
        path, _ = QFileDialog.getSaveFileName(self, '另存为附件', self.archive.attachment(self.attachment_id).filename)
        if not path: return
        try:
            self.save_to(Path(path))
            self.message.setText('附件已另存。')
        except (ValueError, OSError) as exc: self.message.setText(str(exc))

    def save_to(self,target):
        from .export import atomic_target,protect_archive,protect_storage
        if hasattr(self.archive,'store'):protect_archive(self.archive,target)
        else:
            # Unsaved result attachments still use the current desktop's storage boundary.
            parent=self.parent()
            while parent is not None:
                if hasattr(parent,'knowledge'):
                    protect_storage(parent.knowledge.path,target);break
                parent=parent.parent()
            else:raise ValueError('请从已打开的实验方向另存附件。')
        with atomic_target(target) as temporary:shutil.copyfile(self.archive.path_for(self.attachment_id),temporary)
