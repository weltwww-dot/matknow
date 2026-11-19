"""Small native editors; original Value provenance survives unchanged fields."""
from copy import deepcopy
from datetime import date

from PySide6.QtCore import Qt, Signal, QThreadPool
from PySide6.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QPlainTextEdit, QTableWidget, QTableWidgetItem, QPushButton, QTabWidget, QCheckBox,
    QDialogButtonBox, QLabel, QHeaderView, QAbstractItemView, QMessageBox, QComboBox)

from matknow.tasks import Task
from .models import ExperimentDraft, SampleDraft, Value, validate_draft, value_provenance, ChemistryRecord, Reagent, RECORD_STATUSES
from .drafts import correct_value, confirm_draft


def item(text, data=None):
    result = QTableWidgetItem(str(text))
    if data is not None: result.setData(Qt.ItemDataRole.UserRole, data)
    return result


def grid(headers):
    result = QTableWidget(0, len(headers)); result.setHorizontalHeaderLabels(headers)
    result.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    result.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    result.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    return result


def action_row(layout, *actions):
    row = QHBoxLayout()
    for name, function in actions:
        button = QPushButton(name); button.clicked.connect(function); row.addWidget(button)
    row.addStretch(); layout.addLayout(row)


class FormDialog(QDialog):
    busyChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.busy = False; self.pool = QThreadPool(self); self.pool.setMaxThreadCount(1)
        self.layout = QVBoxLayout(self); self.layout.setSpacing(10)
        self.message = QLabel(''); self.message.setTextFormat(Qt.TextFormat.PlainText); self.message.setWordWrap(True)
        if parent and hasattr(parent, '_settings_busy_changed'):
            self.busyChanged.connect(parent._settings_busy_changed)
        elif parent and hasattr(parent, 'busyChanged'):
            self.busyChanged.connect(parent.busyChanged.emit)

    def finish_form(self, verb='保存'):
        self.layout.addWidget(self.message)
        self.controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.controls.button(QDialogButtonBox.StandardButton.Ok).setText(verb)
        self.controls.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.controls.accepted.connect(self.accept); self.controls.rejected.connect(self.reject)
        self.layout.addWidget(self.controls)

    def run(self, function, callback, message):
        if self.busy: return
        self.busy = True; self.busyChanged.emit(True); self.setEnabled(False); self.message.setText(message);self._running_message=message
        self.task = Task(function); self.task.signals.succeeded.connect(callback)
        self.task.signals.failed.connect(self.task_failed); self.task.signals.finished.connect(self.finished_task)
        self.pool.start(self.task)

    def finished_task(self):
        self.busy = False; self.setEnabled(True); self.busyChanged.emit(False)
        if self.message.text()==getattr(self,'_running_message',None):self.message.setText('已完成，请核对。')

    def task_failed(self,message):
        self.message.setText(message)

    def reject(self):
        if not self.busy: super().reject()

    def closeEvent(self, event):
        if self.busy: event.ignore()
        else: super().closeEvent(event)


class ValueTable(QWidget):
    def __init__(self, values=None, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        self.grid = grid(['条件', '值', '单位', '来源'])
        layout.addWidget(self.grid)
        action_row(layout, ('＋ 条件', lambda: self.add('', Value(''))), ('移除', self.remove))
        self.set_values(values or {})

    def add(self, name, value):
        row = self.grid.rowCount(); self.grid.insertRow(row)
        source = value_provenance(value)
        for column, text in enumerate([name, value.text, value.unit, source]):
            cell = item(text, deepcopy(value) if column == 1 else None)
            if column == 3: cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.grid.setItem(row, column, cell)

    def remove(self):
        if self.grid.currentRow() >= 0: self.grid.removeRow(self.grid.currentRow())

    def set_values(self, values):
        self.grid.setRowCount(0)
        for name, value in values.items(): self.add(name, value)

    def values(self):
        result = {}
        for row in range(self.grid.rowCount()):
            name = self.grid.item(row, 0).text().strip()
            if not name or name in result: raise ValueError('条件名称不能为空或重复。')
            original = self.grid.item(row, 1).data(Qt.ItemDataRole.UserRole)
            text, unit = self.grid.item(row, 1).text(), self.grid.item(row, 2).text()
            result[name] = deepcopy(original) if (text, unit) == (original.text, original.unit) else correct_value(original, text, unit)
        return result


class ExperimentEditor(QWidget):
    def __init__(self, draft=None, parent=None):
        super().__init__(parent)
        self.original = deepcopy(draft or ExperimentDraft('', date.today().isoformat(), samples=[SampleDraft('A1')]))
        self.samples = deepcopy(self.original.samples); self.active = -1
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout(); self.title = QLineEdit(self.original.title); self.date = QLineEdit(self.original.date)
        self.objective=QLineEdit(self.original.chemistry.objective);self.objective.setPlaceholderText('这次实验想解决什么问题？')
        form.addRow('实验名称', self.title); form.addRow('实验日期', self.date);form.addRow('实验目的',self.objective); layout.addLayout(form)
        self.notes = QPlainTextEdit(self.original.notes); self.notes.setPlaceholderText('按时间记录实际操作、条件和观察；偏差与中止也应记下。');layout.addWidget(self.notes,1)
        self.advanced_button=QPushButton('实验详情（展开）');self.advanced_button.setCheckable(True);layout.addWidget(self.advanced_button)
        self.tabs = QTabWidget(); layout.addWidget(self.tabs, 1);self.tabs.hide()
        self.advanced_button.toggled.connect(self.tabs.setVisible)
        self.advanced_button.toggled.connect(lambda opened:self.advanced_button.setText('实验详情（'+('收起' if opened else '展开')+'）'))
        materials=QWidget();material_layout=QVBoxLayout(materials)
        self.reagents=grid(['试剂 / 材料','记录用量','单位','规格 / 浓度','来源 / 批号','样品（留空为全部）']);material_layout.addWidget(self.reagents)
        action_row(material_layout,('＋ 材料',lambda:self.add_reagent(Reagent(''))),('移除',self.remove_reagent));self.tabs.addTab(materials,'试剂与材料')
        for reagent in self.original.chemistry.reagents:self.add_reagent(reagent)
        sample_widget = QWidget(); sample_layout = QVBoxLayout(sample_widget)
        self.sample_grid = grid(['样品编号', '结果数']); self.sample_grid.setMaximumHeight(170)
        sample_layout.addWidget(self.sample_grid)
        action_row(sample_layout, ('＋ 样品', self.add_sample), ('移除样品', self.remove_sample), ('编辑结果', self.edit_results))
        self.conditions = ValueTable(); sample_layout.addWidget(self.conditions, 1)
        self.tabs.addTab(sample_widget, '样品条件')
        self.common = ValueTable(self.original.common); self.tabs.addTab(self.common, '共用条件')
        information=QWidget();info=QFormLayout(information);chemistry=self.original.chemistry
        self.operator=QLineEdit(chemistry.operator);self.record_status=QComboBox()
        for key,name in RECORD_STATUSES.items():self.record_status.addItem(name,key)
        self.record_status.setCurrentIndex(self.record_status.findData(chemistry.status));info.addRow('操作者',self.operator);info.addRow('状态',self.record_status)
        self.record_status.currentIndexChanged.connect(lambda:self.reagents.horizontalHeaderItem(1).setText('计划用量' if self.record_status.currentData()=='planned' else '记录用量'))
        self.reagents.horizontalHeaderItem(1).setText('计划用量' if chemistry.status=='planned' else '记录用量')
        self.record_text={}
        for key,name in [('setup','装置与条件'),('deviations','偏差 / 中止原因'),('references','方案 / 文献依据')]:
            widget=QPlainTextEdit(getattr(chemistry,key));widget.setMaximumHeight(65);self.record_text[key]=widget;info.addRow(name,widget)
        self.tabs.addTab(information,'补充信息')
        summary=QWidget();summary_layout=QFormLayout(summary)
        for key,name in [('discussion','讨论与局限'),('conclusion','结论 / 下一步')]:
            widget=QPlainTextEdit(getattr(chemistry,key));widget.setPlaceholderText('依据已有观察和结果填写；推测与事实分开。');self.record_text[key]=widget;summary_layout.addRow(name,widget)
        self.tabs.addTab(summary,'分析与总结')
        self.sample_grid.currentCellChanged.connect(self.change_sample)
        self.refresh_samples()

    def refresh_samples(self):
        self.sample_grid.blockSignals(True); self.sample_grid.setRowCount(len(self.samples))
        for row, sample in enumerate(self.samples):
            self.sample_grid.setItem(row, 0, item(sample.code))
            count = item(len(sample.results)); count.setFlags(count.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.sample_grid.setItem(row, 1, count)
        self.active = -1; self.sample_grid.blockSignals(False)
        if self.samples:
            self.sample_grid.setCurrentCell(0, 0)
            self.load_sample(0)
        else: self.conditions.set_values({})

    def flush(self):
        if self.active >= 0 and self.active < len(self.samples):
            self.samples[self.active].conditions = self.conditions.values()
        for row, sample in enumerate(self.samples): sample.code = self.sample_grid.item(row, 0).text().strip()

    def change_sample(self, row, column, previous_row, previous_column):
        try: self.flush()
        except ValueError:
            self.sample_grid.blockSignals(True); self.sample_grid.setCurrentCell(self.active, 0); self.sample_grid.blockSignals(False); return
        self.load_sample(row)

    def load_sample(self,row):
        self.active=row
        self.conditions.set_values(self.samples[row].conditions if row >= 0 else {})

    def add_sample(self):
        self.flush(); used = {s.code for s in self.samples}; index = 1
        while f'A{index}' in used: index += 1
        self.samples.append(SampleDraft(f'A{index}')); self.refresh_samples(); self.sample_grid.setCurrentCell(len(self.samples)-1, 0)

    def remove_sample(self):
        if self.active < 0: return
        if self.samples[self.active].id and QMessageBox.question(self,'移除样品','从当前版本移除？旧版本及原文件仍保留。',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        self.flush(); self.samples.pop(self.active); self.refresh_samples()

    def collect(self):
        self.flush()
        reagents=[]
        for row in range(self.reagents.rowCount()):
            values=[self.reagents.item(row,column).text() for column in range(6)]
            codes=[code.strip() for code in values[5].replace('，',',').split(',') if code.strip()]
            reagents.append(Reagent(*values[:5],sample_codes=codes))
        chemistry=ChemistryRecord(objective=self.objective.text(),operator=self.operator.text(),status=self.record_status.currentData(),reagents=reagents,
                                  **{key:widget.toPlainText() for key,widget in self.record_text.items()})
        return ExperimentDraft(self.title.text().strip(), self.date.text().strip(), self.common.values(), deepcopy(self.samples), self.notes.toPlainText(), list(self.original.source_ids),chemistry)

    def add_reagent(self,reagent):
        row=self.reagents.rowCount();self.reagents.insertRow(row)
        for column,value in enumerate([reagent.name,reagent.quantity,reagent.unit,reagent.specification,reagent.source,','.join(reagent.sample_codes)]):self.reagents.setItem(row,column,item(value))

    def remove_reagent(self):
        row=self.reagents.currentRow()
        if row>=0:self.reagents.removeRow(row)

    def edit_results(self):
        if self.active < 0: return
        from .result_dialog import ResultDialog
        from .models import ExperimentSnapshot
        draft = self.collect()
        # Existing or not-yet-saved sample IDs are stable within this editor only.
        from uuid import uuid4
        sample = draft.samples[self.active]; sample.id = sample.id or str(uuid4())
        snapshot = ExperimentSnapshot('', 0, draft, '')
        parent = self.window()
        dialog = ResultDialog(snapshot, sample.id, parent=parent)
        # Files are attached from the main result action after the experiment is saved.
        dialog.attach_button.setEnabled(False)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.samples = dialog.draft.samples; self.refresh_samples()


class ExperimentDialog(FormDialog):
    def __init__(self, snapshot=None, parent=None):
        super().__init__(parent); self.setWindowTitle('编辑记录' if snapshot else '记实验'); self.resize(700, 560)
        self.snapshot = snapshot; self.draft = None
        self.editor = ExperimentEditor(snapshot.draft if snapshot else None); self.layout.addWidget(self.editor, 1)
        self.confirm_check = QCheckBox('已核对提取值和公式缓存'); self.layout.addWidget(self.confirm_check)
        self.confirm_check.setVisible(bool(snapshot and any(not v.confirmed for s in snapshot.draft.samples for v in [*s.conditions.values(), *[r.value for r in s.results]])))
        self.finish_form()

    def accept(self):
        try:
            draft = self.editor.collect()
            if self.confirm_check.isChecked(): draft = confirm_draft(draft)
            validate_draft(draft); self.draft = draft
        except ValueError as exc: self.message.setText(str(exc)); return
        super().accept()
