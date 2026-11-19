from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QComboBox, QPushButton,
    QListWidget, QListWidgetItem, QPlainTextEdit, QAbstractItemView, QLineEdit, QFileDialog)

from .editors import ExperimentDialog, FormDialog, grid, item, action_row
from .import_dialog import RecordImportDialog
from .result_dialog import ResultDialog, parse_conditions
from .compare import compare_samples, rank_measurements, confirm_group


class ComparisonDialog(FormDialog):
    askRequested=Signal(str,object)

    def __init__(self,store,sample_ids,parent=None):
        super().__init__(parent);self.comparison=compare_samples(store,sample_ids);self.setWindowTitle('样品对比');self.resize(1000,720)
        self.layout.addWidget(QLabel(self.comparison.scope))
        self.conditions=grid(['制备条件','状态',*[s.code+' / '+snap.draft.title for snap,s in store.samples(sample_ids)]])
        self.conditions.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.conditions.setMaximumHeight(130);self.layout.addWidget(self.conditions)
        statuses={'same':'相同','different':'不同','missing':'缺失'}
        for row,condition in enumerate(self.comparison.conditions):
            self.conditions.insertRow(row)
            values=[condition['condition_name'],statuses[condition['status']],*[f'{v.text} {v.unit}'.strip() if (v:=condition['values'][sid]) else '未记录' for sid in sample_ids]]
            for col,text in enumerate(values):self.conditions.setItem(row,col,item(text))
        self.group_combo=QComboBox();self.layout.addWidget(self.group_combo)
        self.measurements=QListWidget();self.measurements.setMinimumHeight(110);self.measurements.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection);self.layout.addWidget(self.measurements,1)
        bar=QHBoxLayout();self.reference=QComboBox();bar.addWidget(QLabel('差值参照'));bar.addWidget(self.reference,1)
        for name,descending in [('从大到小',True),('从小到大',False)]:
            button=QPushButton(name);button.clicked.connect(lambda checked=False,d=descending:self.rank(d));bar.addWidget(button)
        self.layout.addLayout(bar)
        self.calculation=QPlainTextEdit();self.calculation.setReadOnly(True);self.calculation.setMaximumHeight(140);self.layout.addWidget(self.calculation)
        action_row(self.layout,('确认等价测试组',self.confirm_mapping),('导出对比',self.export))
        self.question=QLineEdit();self.question.setPlaceholderText('向已选完整实验范围提问…');self.layout.addWidget(self.question)
        action_row(self.layout,('提问',lambda:self.askRequested.emit(self.question.text(),self.comparison)))
        self.group_combo.currentIndexChanged.connect(self.show_group);self.refresh_groups();self.message.setText('；'.join(self.comparison.warnings));self.finish_form('关闭')

    def refresh_groups(self):
        self.group_combo.blockSignals(True);self.group_combo.clear()
        for group in self.comparison.groups:
            suffix=' · 待核对' if group['status']=='needs_confirmation' else ''
            self.group_combo.addItem(f'{group["metric"]} / {group["unit"]} / {group["method"] or "方法未记录"}{suffix}',group['group_id'])
        self.group_combo.blockSignals(False);self.show_group()

    def show_group(self):
        self.measurements.clear();self.reference.clear();self.reference.addItem('不计算差值',None);self.calculation.clear()
        index=self.group_combo.currentIndex()
        if index<0:return
        group=self.comparison.groups[index]
        for row in group['measurements']:
            text=f'{row["label"]} · {row["value"].text} {row["value"].unit} · {row["measured_at"] or "时间未记录"}'
            entry=QListWidgetItem(text);entry.setData(Qt.ItemDataRole.UserRole,row['result_id']);self.measurements.addItem(entry)
            self.reference.addItem(text,row['result_id'])
        self.message.setText('测试条件：'+str(group['test_conditions'])+'；重复测量逐条保留。')

    def rank(self,descending):
        ids=[entry.data(Qt.ItemDataRole.UserRole) for entry in self.measurements.selectedItems()]
        try:
            rows=rank_measurements(self.comparison,result_ids=ids,descending=descending,reference_result_id=self.reference.currentData())
            self.comparison.calculation={'result_ids':ids,'descending':descending,'reference_result_id':self.reference.currentData()}
            self.calculation.setPlainText('\n'.join(f'{i}. {r["label"]} · {r["value"].text} {r["value"].unit}'+(f' · 差值 {r["difference"]}' if r['difference'] is not None else '') for i,r in enumerate(rows,1)))
        except ValueError as exc:self.message.setText(str(exc))

    def confirm_mapping(self):
        dialog=FormDialog(self);dialog.setWindowTitle('确认等价写法');dialog.resize(700,500)
        groups=QListWidget();groups.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for g in self.comparison.groups:
            entry=QListWidgetItem(f'{g["metric"]} / {g["unit"]} / {g["method"]} / {g["test_conditions"]}');entry.setData(Qt.ItemDataRole.UserRole,g['group_id']);groups.addItem(entry)
        dialog.layout.addWidget(groups)
        metric,unit,method=QLineEdit(),QLineEdit(),QLineEdit();conditions=QPlainTextEdit();conditions.setPlaceholderText('测试条件，每行 名称=值')
        for name,widget in [('指标',metric),('单位（须与原记录一致）',unit),('方法',method),('测试条件',conditions)]:dialog.layout.addWidget(QLabel(name));dialog.layout.addWidget(widget)
        dialog.layout.addWidget(QLabel('仅确认本次比较中的等价写法，不换算单位。'))
        dialog.finish_form('确认')
        if dialog.exec()!=dialog.DialogCode.Accepted:return
        selected={entry.data(Qt.ItemDataRole.UserRole) for entry in groups.selectedItems()}
        ids=[r['result_id'] for g in self.comparison.groups if g['group_id'] in selected for r in g['measurements']]
        try:self.comparison=confirm_group(self.comparison,ids,metric=metric.text(),unit=unit.text(),method=method.text(),test_conditions=parse_conditions(conditions.toPlainText()));self.refresh_groups()
        except ValueError as exc:self.message.setText(str(exc))

    def export(self):
        path,_=QFileDialog.getSaveFileName(self,'导出样品对比','材知-样品对比.md','Markdown (*.md)')
        if not path:return
        from .export import export_comparison
        parent=self.parent();name=parent.profiles.get(parent.profile_id).name if parent and getattr(parent,'profiles',None) else '研究方向'
        try:export_comparison(self.comparison,path,profile_name=name);self.message.setText('对比已导出。')
        except (ValueError,OSError) as exc:self.message.setText(str(exc))
