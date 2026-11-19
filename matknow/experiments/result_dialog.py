from copy import deepcopy
from dataclasses import asdict
import hashlib
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QFormLayout, QPlainTextEdit, QLineEdit, QComboBox,
    QPushButton, QFileDialog, QLabel, QSpinBox, QCheckBox, QTabWidget, QWidget, QMessageBox)

from .editors import FormDialog, grid, item
from .models import ResultDraft, Value, SourceRef, validate_draft, value_provenance
from .drafts import correct_value, confirm_draft
from .files import Upload, read_bytes
from .preview import AttachmentPreview, CurveSpec, prepare_attachment
from .readers import read_table

KINDS = {'number': '数值', 'text': '文字', 'table': '表格', 'curve': '曲线', 'image': '图片', 'pdf': 'PDF', 'raw': '原始文件'}


def parse_conditions(text):
    result = {}
    for line in text.splitlines():
        if not line.strip(): continue
        if '=' not in line: raise ValueError('测试条件请每行填写：名称=值。')
        name, value = line.split('=', 1)
        if not name.strip() or name.strip() in result: raise ValueError('测试条件名称无效或重复。')
        result[name.strip()] = value.strip()
    return result


class LocalArchive:
    def __init__(self, uploads): self.uploads = uploads
    def attachment(self, file_id):
        u = next(u for u in self.uploads if u.token == file_id)
        return SimpleNamespace(filename=u.path.name)
    def path_for(self, file_id):
        u = next(u for u in self.uploads if u.token == file_id)
        if hashlib.sha256(read_bytes(u.path)).hexdigest() != u.expected_sha256:
            raise ValueError('文件已变化，请重新添加。')
        return u.path


class ResultDialog(FormDialog):
    def __init__(self, snapshot, sample_id, parent=None, archive=None):
        super().__init__(parent)
        self.snapshot, self.sample_id, self.archive = snapshot, sample_id, archive
        self.draft, self.uploads, self.active = None, [], -1
        self.original = deepcopy(snapshot.draft)
        self.sample = next(s for s in self.original.samples if s.id == sample_id)
        self.setWindowTitle(f'实验结果 · {self.sample.code}'); self.resize(820, 520)
        self.grid = grid(['指标', '值 / 观察', '单位', '测试方法', '测试时间', '类型'])
        self.grid.setMinimumHeight(105);self.grid.setMaximumHeight(230); self.layout.addWidget(self.grid)
        bar = QHBoxLayout()
        for text, function in [('＋ 结果', self.add_result), ('移除结果', self.remove_result)]:
            b=QPushButton(text); b.clicked.connect(function); bar.addWidget(b)
        self.attach_button = QPushButton('添加附件'); self.attach_button.clicked.connect(self.choose_attachment); bar.addWidget(self.attach_button); bar.addStretch(); self.layout.addLayout(bar)
        self.attachment_controls=QWidget();attachment_bar=QHBoxLayout(self.attachment_controls);self.attachment_combo=QComboBox();attachment_bar.addWidget(self.attachment_combo,1)
        show=QPushButton('查看文件');show.clicked.connect(self.show_attachment);attachment_bar.addWidget(show);self.layout.addWidget(self.attachment_controls);self.attachment_controls.hide()
        self.advanced_button=QPushButton('测试信息与文件设置（展开）');self.advanced_button.setCheckable(True);self.layout.addWidget(self.advanced_button)
        self.tabs=tabs=QTabWidget(); self.layout.addWidget(tabs, 1);tabs.hide()
        for column in (3,4):self.grid.setColumnHidden(column,True)
        self.advanced_button.toggled.connect(self.toggle_details)
        detail = QWidget(); form = QFormLayout(detail)
        self.test_conditions = QPlainTextEdit(); self.test_conditions.setPlaceholderText('温度=25 °C\n加载速率=1 mm/min'); self.test_conditions.setMaximumHeight(95)
        self.repeat_group = QLineEdit(); self.notes = QPlainTextEdit(); self.notes.setPlaceholderText('观察或备注…')
        self.source_label = QLabel(''); self.source_label.setTextFormat(Qt.TextFormat.PlainText); self.source_label.setWordWrap(True)
        form.addRow('测试条件', self.test_conditions); form.addRow('重复测量组', self.repeat_group); form.addRow('备注', self.notes); form.addRow('来源', self.source_label)
        tabs.addTab(detail, '测试信息')
        data = QWidget(); data_form = QFormLayout(data)
        self.sheet = QLineEdit(); self.sheet.setPlaceholderText('留空使用首个工作表')
        self.bounds = QLineEdit(); self.bounds.setPlaceholderText('起始行,起始列,结束行,结束列；留空读取全部')
        self.x_column = QSpinBox(); self.x_column.setRange(1, 16384)
        self.y_column = QSpinBox(); self.y_column.setRange(1, 16384); self.y_column.setValue(2)
        self.x_unit = QLineEdit(); self.y_unit = QLineEdit(); self.sort_x = QCheckBox('按 X 排序（各连续段内）')
        xy = QHBoxLayout(); xy.addWidget(QLabel('X')); xy.addWidget(self.x_column); xy.addWidget(self.x_unit); xy.addWidget(QLabel('Y')); xy.addWidget(self.y_column); xy.addWidget(self.y_unit)
        data_form.addRow('工作表', self.sheet); data_form.addRow('数据范围', self.bounds); data_form.addRow('列 / 单位', xy); data_form.addRow('', self.sort_x)
        actions = QHBoxLayout(); draw = QPushButton('预览曲线'); draw.clicked.connect(self.preview_curve); actions.addWidget(draw)
        self.cell = QLineEdit(); self.cell.setPlaceholderText('B2'); actions.addWidget(self.cell)
        take = QPushButton('取单元格为数值'); take.clicked.connect(self.take_cell); actions.addWidget(take)
        data_form.addRow(actions); tabs.addTab(data, '表格 / 曲线')
        self.preview = AttachmentPreview(); tabs.addTab(self.preview, '附件预览')
        self.confirm_check = QCheckBox('已核对提取值和公式缓存'); self.layout.addWidget(self.confirm_check)
        values=[*self.original.common.values(),*[v for s in self.original.samples for v in [*s.conditions.values(),*[r.value for r in s.results]]]]
        self.confirm_check.setVisible(any(not value.confirmed for value in values))
        self.grid.currentCellChanged.connect(self.change_result)
        for result in self.sample.results: self.append_row(result)
        if self.grid.rowCount(): self.grid.setCurrentCell(0, 0);self.load_result(0)
        self.finish_form()

    def toggle_details(self,opened):
        self.tabs.setVisible(opened)
        for column in (3,4):self.grid.setColumnHidden(column,not opened)
        self.advanced_button.setText('测试信息与文件设置（'+('收起' if opened else '展开')+'）')

    def append_row(self, result):
        row = self.grid.rowCount(); self.grid.insertRow(row);self.grid.setRowHeight(row,44)
        for col, text in enumerate([result.metric, result.value.text, result.value.unit, result.method, result.measured_at]):
            self.grid.setItem(row, col, item(text, deepcopy(result) if col == 0 else None))
        kind = QComboBox()
        for key, label in KINDS.items(): kind.addItem(label, key)
        kind.setCurrentIndex(kind.findData(result.kind)); self.grid.setCellWidget(row, 5, kind)

    def add_result(self):
        self.append_row(ResultDraft('', Value(''))); self.grid.setCurrentCell(self.grid.rowCount()-1, 0)
        if self.active!=self.grid.rowCount()-1:self.load_result(self.grid.rowCount()-1)

    def flush(self):
        if 0 <= self.active < self.grid.rowCount():
            result = self.grid.item(self.active, 0).data(Qt.ItemDataRole.UserRole)
            result.test_conditions = parse_conditions(self.test_conditions.toPlainText())
            result.repeat_group, result.notes = self.repeat_group.text(), self.notes.toPlainText()
            self.grid.item(self.active, 0).setData(Qt.ItemDataRole.UserRole, result)

    def change_result(self, row, column, previous_row, previous_column):
        try: self.flush()
        except ValueError as exc:
            self.message.setText(str(exc)); self.grid.blockSignals(True); self.grid.setCurrentCell(self.active, 0); self.grid.blockSignals(False); return
        self.load_result(row)

    def load_result(self,row):
        self.active = row
        if row < 0: return
        result = self.grid.item(row, 0).data(Qt.ItemDataRole.UserRole)
        self.test_conditions.setPlainText('\n'.join(f'{k}={v}' for k,v in result.test_conditions.items()))
        self.repeat_group.setText(result.repeat_group); self.notes.setPlainText(result.notes)
        self.source_label.setText(value_provenance(result.value))
        spec = result.data
        self.sheet.setText(spec.get('sheet', '')); self.bounds.setText(','.join(map(str,spec.get('bounds',[]))))
        self.x_column.setValue(spec.get('x_column',1)); self.y_column.setValue(spec.get('y_column',2))
        self.x_unit.setText(spec.get('x_unit','')); self.y_unit.setText(spec.get('y_unit','')); self.sort_x.setChecked(spec.get('sort_x',False)); self.cell.setText(spec.get('cell',''))
        self.preview.clear()
        self.refresh_attachments(result)

    def refresh_attachments(self,result):
        self.attachment_combo.clear()
        for token in result.attachment_ids:
            upload=next((u for u in self.uploads if u.token==token),None)
            try:name=upload.path.name if upload else self.archive.attachment(token).filename if self.archive else '已有附件'
            except ValueError:name='附件缺失'
            self.attachment_combo.addItem(name,token)
        index=self.attachment_combo.findData(result.data.get('attachment_id'))
        if index>=0:self.attachment_combo.setCurrentIndex(index)
        self.attachment_controls.setVisible(self.attachment_combo.count()>0)

    def show_attachment(self):
        try:result,token,archive=self.selected_attachment()
        except (ValueError,OSError) as exc:self.message.setText(str(exc));return
        self.advanced_button.setChecked(True);self.tabs.setCurrentIndex(2)
        self.run(lambda:prepare_attachment(archive,token),lambda payload:self.preview.apply_prepared(archive,token,payload),'正在读取附件…')

    def remove_result(self):
        row = self.grid.currentRow()
        if row < 0: return
        if self.grid.item(row,0).data(Qt.ItemDataRole.UserRole).id and QMessageBox.question(self,'移除结果','从当前版本移除？旧版本及原文件仍保留。',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        self.grid.blockSignals(True); self.grid.removeRow(row); self.active=-1; self.grid.blockSignals(False)
        if self.grid.rowCount():
            selected=min(row,self.grid.rowCount()-1);self.grid.setCurrentCell(selected,0);self.load_result(selected)
        else:self.test_conditions.clear();self.notes.clear();self.repeat_group.clear();self.source_label.clear();self.attachment_combo.clear();self.attachment_controls.hide();self.preview.clear()

    def choose_attachment(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '添加实验结果', '', '所有文件 (*)')
        for path in paths:
            try: self.attach_path(Path(path))
            except (ValueError, OSError) as exc: self.message.setText(str(exc))

    def attach_path(self, path):
        digest = hashlib.sha256(read_bytes(path)).hexdigest()
        if self.active < 0: self.add_result()
        row = self.active; result = self.grid.item(row,0).data(Qt.ItemDataRole.UserRole)
        token = str(uuid4()); self.uploads.append(Upload(Path(path),token,'attachment',self.sample.code,row,digest))
        result.attachment_ids.append(token); result.data['attachment_id']=token
        self.grid.item(row,0).setData(Qt.ItemDataRole.UserRole,result)
        self.refresh_attachments(result)
        if not self.grid.item(row,0).text(): self.grid.item(row,0).setText(path.stem)
        if not self.grid.item(row,1).text(): self.grid.item(row,1).setText('原始附件')
        kind = {'.csv':'table','.xlsx':'table','.png':'image','.jpg':'image','.jpeg':'image','.pdf':'pdf'}.get(path.suffix.lower(),'raw')
        self.grid.cellWidget(row,5).setCurrentIndex(self.grid.cellWidget(row,5).findData(kind))
        archive = LocalArchive(self.uploads)
        self.run(lambda: prepare_attachment(archive,token),lambda payload:self.preview.apply_prepared(archive,token,payload),'正在读取附件…')

    def selected_attachment(self):
        if self.active < 0: raise ValueError('请先选择结果。')
        result = self.grid.item(self.active,0).data(Qt.ItemDataRole.UserRole)
        token = self.attachment_combo.currentData() or result.data.get('attachment_id') or next(iter(result.attachment_ids),None)
        if not token: raise ValueError('请先添加数据附件。')
        archive = LocalArchive(self.uploads) if any(u.token==token for u in self.uploads) else self.archive
        if archive is None: raise ValueError('请从实验记录页打开此结果附件。')
        return result, token, archive

    def selection(self):
        bounds = tuple(int(v.strip()) for v in self.bounds.text().split(',')) if self.bounds.text().strip() else None
        if bounds is not None and len(bounds)!=4: raise ValueError('数据范围需要四个整数。')
        return self.sheet.text().strip() or None,bounds

    def preview_curve(self):
        try:
            result, token, archive = self.selected_attachment(); sheet,bounds=self.selection()
            spec = CurveSpec(self.x_column.value(),self.y_column.value(),self.x_unit.text(),self.y_unit.text(),self.sort_x.isChecked())
            row=self.active
        except (ValueError, OSError) as exc: self.message.setText(str(exc)); return
        def ready(payload):
            result.data={**asdict(spec),'attachment_id':token}
            if sheet: result.data['sheet']=sheet
            if bounds: result.data['bounds']=list(bounds)
            self.grid.item(row,0).setData(Qt.ItemDataRole.UserRole,result)
            self.grid.cellWidget(row,5).setCurrentIndex(self.grid.cellWidget(row,5).findData('curve'))
            self.preview.apply_prepared(archive,token,payload); self.message.setText('曲线仅用于预览，原始数据完整保留。')
            self.advanced_button.setChecked(True);self.tabs.setCurrentWidget(self.preview);self.preview.content.setCurrentIndex(1)
        self.run(lambda:prepare_attachment(archive,token,spec,sheet=sheet,bounds=bounds),ready,'正在绘制曲线…')

    def take_cell(self):
        try:
            from openpyxl.utils.cell import coordinate_to_tuple
            result,token,archive=self.selected_attachment(); sheet,bounds=self.selection()
            coordinate=self.cell.text().strip().upper(); row,column=coordinate_to_tuple(coordinate)
            active=self.active
        except (ValueError, OSError) as exc:self.message.setText(str(exc));return
        def ready(table):
            selected=next((c for c in table.cells if c.row==row and c.column==column),None)
            if selected is None: self.message.setText('单元格不在选定范围。');return
            result.value=Value(selected.cached or '' if selected.formula else selected.text,self.grid.item(active,2).text(),'imported',SourceRef(token,f'{table.name}!{coordinate}'),not bool(selected.formula))
            result.data={'attachment_id':token,'cell':coordinate,'sheet':table.name}
            if bounds:result.data['bounds']=list(bounds)
            self.grid.item(active,0).setData(Qt.ItemDataRole.UserRole,result);self.grid.item(active,1).setText(result.value.text)
            self.grid.cellWidget(active,5).setCurrentIndex(self.grid.cellWidget(active,5).findData('number'))
            self.source_label.setText(f'{table.name}!{coordinate}'+(f' · {selected.formula} → {selected.cached or "无缓存"} · 待确认' if selected.formula else ''))
            if selected.formula:self.confirm_check.show()
        self.run(lambda:read_table(archive.path_for(token),file_id=token,sheet=sheet,bounds=bounds),ready,'正在读取单元格…')

    def accept(self):
        if self.busy:return
        try:
            self.flush();draft=deepcopy(self.original);sample=next(s for s in draft.samples if s.id==self.sample_id);sample.results=[]
            for row in range(self.grid.rowCount()):
                result=deepcopy(self.grid.item(row,0).data(Qt.ItemDataRole.UserRole))
                result.metric=self.grid.item(row,0).text();text,unit=self.grid.item(row,1).text(),self.grid.item(row,2).text()
                if (text,unit)!=(result.value.text,result.value.unit):result.value=correct_value(result.value,text,unit)
                result.method=self.grid.item(row,3).text();result.measured_at=self.grid.item(row,4).text();result.kind=self.grid.cellWidget(row,5).currentData()
                sample.results.append(result)
            uploads=[]
            for upload in self.uploads:
                matches=[i for i,r in enumerate(sample.results) if upload.token in r.attachment_ids]
                if matches:upload.result_index=matches[0];upload.sample_code=sample.code;uploads.append(upload)
            if self.confirm_check.isChecked():draft=confirm_draft(draft)
            validate_draft(draft);self.draft,self.uploads=draft,uploads
        except ValueError as exc:self.message.setText(str(exc));return
        super().accept()
