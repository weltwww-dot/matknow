from datetime import date
from pathlib import Path
import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QCheckBox, QPushButton, QSpinBox, QTabWidget, QListWidget, QListWidgetItem, QPlainTextEdit)

from .editors import FormDialog, ExperimentEditor, grid, item
from .models import ExperimentDraft, SampleDraft, RECORD_STATUSES
from .drafts import ColumnMap, from_table, organize, confirm_draft
from .files import Upload


class RecordImportDialog(FormDialog):
    def __init__(self, read_document, model_client=None, parent=None, *, archive=None,prepared=None,candidates=None):
        super().__init__(parent)
        self.document,self.model_client=read_document,model_client
        self.source_path=None;self.draft=None;self.uploads=[]
        self.draft_ready=False;self.auto_started=False
        self.archive=archive;self.snapshot=None;self.candidates=[]
        self.setWindowTitle('导入实验记录');self.resize(900,760)
        self.tabs=QTabWidget();self.layout.addWidget(self.tabs,1)
        mapping=QWidget();layout=QVBoxLayout(mapping)
        self.sample_codes=QLineEdit();self.sample_codes.setPlaceholderText('样品编号，例如 A1,A2,A3')
        layout.addWidget(self.sample_codes)
        bar=QHBoxLayout();self.table_combo=QComboBox()
        for table in read_document.tables:self.table_combo.addItem(table.name)
        self.use_table=QCheckBox('按表格列整理');self.use_table.setChecked(Path(read_document.filename).suffix.lower()=='.xlsx')
        bar.addWidget(self.use_table);bar.addWidget(self.table_combo);layout.addLayout(bar)
        ranges=QHBoxLayout();self.header_row=QSpinBox();self.row_end=QSpinBox();self.column_start=QSpinBox();self.column_end=QSpinBox()
        for name,spin,maxval in [('表头行',self.header_row,1048576),('结束行',self.row_end,1048576),('起始列',self.column_start,16384),('结束列',self.column_end,16384)]:
            spin.setRange(1,maxval);ranges.addWidget(QLabel(name));ranges.addWidget(spin)
        layout.addLayout(ranges)
        self.map_grid=grid(['列', '用途', '名称', '单位']);self.map_grid.setMaximumHeight(200);layout.addWidget(self.map_grid)
        self.duplicates=QComboBox();self.duplicates.addItem('重复编号：提示修正','reject');self.duplicates.addItem('重复编号：同一样品多次测量','repeated_measurements');layout.addWidget(self.duplicates)
        self.blocks=QListWidget();self.blocks.setMaximumHeight(180)
        for block in read_document.blocks:
            entry=QListWidgetItem(f'{block.locator} · {block.text[:75]}');entry.setData(Qt.ItemDataRole.UserRole,block.locator)
            entry.setFlags(entry.flags()|Qt.ItemFlag.ItemIsUserCheckable);entry.setCheckState(Qt.CheckState.Checked);self.blocks.addItem(entry)
        layout.addWidget(self.blocks,1)
        self.source_text=QPlainTextEdit();self.source_text.setReadOnly(True);self.source_text.setMaximumHeight(115)
        self.blocks.currentItemChanged.connect(lambda current,previous:self.source_text.setPlainText(next((b.text for b in self.document.blocks if current and b.locator==current.data(Qt.ItemDataRole.UserRole)),'')))
        layout.addWidget(self.source_text)
        self.build_button=QPushButton('生成草稿');self.build_button.clicked.connect(self.build_draft);layout.addWidget(self.build_button)
        self.tabs.addTab(mapping,'原文 / 手动整理')
        self.editor=ExperimentEditor(ExperimentDraft(Path(read_document.filename).stem,date.today().isoformat(),samples=[SampleDraft('A1')]))
        self.review=QWidget();self.review_layout=QVBoxLayout(self.review)
        self.summary=QPlainTextEdit();self.summary.setReadOnly(True);self.summary.setPlainText('正在准备模型整理…');self.review_layout.addWidget(self.summary,1)
        self.details_button=QPushButton('编辑详情');self.details_button.setCheckable(True);self.details_button.toggled.connect(self.editor.setVisible);self.review_layout.addWidget(self.details_button)
        self.review_layout.addWidget(self.editor,2);self.editor.setVisible(model_client is None)
        self.summary.setVisible(model_client is not None);self.details_button.setVisible(model_client is not None)
        self.tabs.addTab(self.review,'整理草稿')
        self.ai_button=QPushButton('重新用模型整理');self.ai_button.setEnabled(model_client is not None);self.ai_button.clicked.connect(self.organize_selected)
        self.layout.addWidget(self.ai_button)
        self.target=QComboBox();self.target.addItem('新建实验',None)
        if archive is not None:
            self.candidates=candidates if candidates is not None else [archive.store.get(row.id) for row in archive.store.list_experiments(limit=20)]
            for snapshot in self.candidates:self.target.addItem(snapshot.draft.date+' '+snapshot.draft.title,snapshot.id)
        self.target.setVisible(bool(self.candidates));self.target.setToolTip('保存位置：模型推荐后仍需核对，可选择新建实验。');self.layout.addWidget(self.target)
        self.target.currentIndexChanged.connect(lambda:self.confirm_check.setChecked(False))
        self.target.currentIndexChanged.connect(self.update_merge_preview)
        self.details_button.toggled.connect(lambda checked:self.update_merge_preview() if not checked else None)
        self.confirm_check=QCheckBox('已核对草稿、来源和公式缓存');self.layout.addWidget(self.confirm_check)
        if read_document.warnings:self.message.setText('；'.join(read_document.warnings))
        self.table_combo.currentIndexChanged.connect(self.reset_mapping)
        self.column_start.valueChanged.connect(self.fill_mapping);self.column_end.valueChanged.connect(self.fill_mapping)
        self.reset_mapping();self.finish_form('保存实验')
        if model_client is not None:self.tabs.setCurrentIndex(1)
        if prepared is not None:
            self.auto_started=True;self.setWindowTitle('核对实验草稿');self.set_editor(prepared)
            self.summary.show();self.details_button.show();self.editor.hide();self.ai_button.hide();self.tabs.setCurrentIndex(1)

    def showEvent(self,event):
        super().showEvent(event)
        if self.model_client is not None and not self.auto_started:
            self.auto_started=True;QTimer.singleShot(0,self.organize_selected)

    def selected_locators(self):
        return [self.blocks.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.blocks.count()) if self.blocks.item(i).checkState()==Qt.CheckState.Checked]

    def task_failed(self,message):
        super().task_failed(message)
        self.summary.setPlainText('整理未完成\n\n'+message+'\n\n原文已保留，可以重新整理或切换到手动整理。')

    def reset_mapping(self):
        table=next((t for t in self.document.tables if t.name==self.table_combo.currentText()),None)
        if not table:return
        self.header_row.setValue(min(c.row for c in table.cells));self.row_end.setValue(max(c.row for c in table.cells))
        self.column_start.blockSignals(True);self.column_end.blockSignals(True)
        self.column_start.setValue(min(c.column for c in table.cells));self.column_end.setValue(max(c.column for c in table.cells))
        self.column_start.blockSignals(False);self.column_end.blockSignals(False);self.fill_mapping()

    def fill_mapping(self):
        table=next((t for t in self.document.tables if t.name==self.table_combo.currentText()),None)
        self.map_grid.setRowCount(0)
        if not table:return
        start,stop=self.column_start.value(),self.column_end.value()
        if stop-start>=100:
            self.message.setText('请先将列范围缩小到 100 列以内。');return
        headers={c.column:c.text for c in table.cells if c.row==self.header_row.value()}
        for row,column in enumerate(range(start,stop+1)):
            self.map_grid.insertRow(row);cell=item(column);cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable);self.map_grid.setItem(row,0,cell)
            role=QComboBox();role.addItems(['忽略','样品编号','制备条件','数值结果']);role.setCurrentIndex(1 if row==0 else 0);self.map_grid.setCellWidget(row,1,role)
            self.map_grid.setItem(row,2,item(headers.get(column,'')));self.map_grid.setItem(row,3,item(''))

    def set_editor(self,draft):
        previous=self.editor;previous.hide();self.review_layout.removeWidget(previous);self.editor=ExperimentEditor(draft);self.review_layout.addWidget(self.editor,2)
        self.details_button.toggled.disconnect(previous.setVisible);self.details_button.toggled.connect(self.editor.setVisible)
        self.editor.setVisible(self.model_client is None or self.details_button.isChecked());previous.deleteLater()
        self.tabs.setCurrentIndex(1);self.confirm_check.setChecked(False);self.draft_ready=True
        self.update_merge_preview()
        self.message.setText('请核对整理摘要；需要修改时展开详情。')
        if draft.common or len(draft.samples)>1 or any(sample.conditions or sample.results for sample in draft.samples):self.editor.advanced_button.setChecked(True)

    def update_merge_preview(self):
        if not self.draft_ready:return
        try:draft=self.editor.collect()
        except ValueError:return
        target=next((s for s in self.candidates if s.id==self.target.currentData()),None)
        before=[]
        if target is not None:
            from .import_agent import merge_record
            before=['追加到：'+target.draft.title+' · 版本 '+str(target.revision)]
            before += [f'{s.code}：新增 {len(s.results)} 条结果' for s in draft.samples]
            try:
                merged=merge_record(target.draft,draft)
                kept=[name for name,field in [('目的','objective'),('材料','reagents')] if getattr(target.draft.chemistry,field) and not getattr(draft.chemistry,field)]
                if kept:before.append('沿用已有'+ '、'.join(kept))
            except ValueError as exc:
                self.summary.setPlainText('\n'.join(before+['','合并前需处理：'+str(exc)]));return
        else:merged=draft
        chemistry=merged.chemistry
        lines=[draft.title,'状态：'+RECORD_STATUSES[chemistry.status],'目的：'+(chemistry.objective or '未提供'),
               '样品：'+'、'.join(s.code for s in merged.samples),'材料：'+('；'.join(r.name+' '+r.quantity+' '+r.unit for r in chemistry.reagents) or '未提供'),
               '实测结果：'+str(sum(len(s.results) for s in merged.samples))+' 条']
        if merged.common:
            lines+=['','共用条件：'+'；'.join(name+' '+(v.text+' '+v.unit).strip() for name,v in merged.common.items())]
        for sample in merged.samples:
            if sample.conditions:lines.append(sample.code+'：'+'；'.join(name+' '+(v.text+' '+v.unit).strip() for name,v in sample.conditions.items()))
            for result in sample.results:
                detail=([result.method] if result.method else [])+[name+' '+value for name,value in result.test_conditions.items()]
                lines.append(sample.code+' 结果：'+result.metric+' '+(result.value.text+' '+result.value.unit).strip()+('（'+'；'.join(detail)+'）' if detail else ''))
        if draft.notes.startswith('模型整理摘要（待核对） / '):
            content=draft.notes.partition('\n')[2].split('\n\n所选原记录\n',1)[0].strip()
            if content:lines+=['','本次整理内容',content]
        if '\n\n待核对\n' in draft.notes:
            review=draft.notes.rsplit('\n\n待核对\n',1)[1].replace('experiment_id','实验').replace('document_id','资料')
            lines+=['','本次文件待核对',review]
        self.summary.setPlainText('\n'.join(before+(['','合并后记录'] if target else [])+lines))

    def build_draft(self):
        try:
            title=self.editor.title.text().strip() or Path(self.document.filename).stem;when=self.editor.date.text()
            if self.use_table.isChecked():
                sample_columns=[];conditions={};metrics={}
                for row in range(self.map_grid.rowCount()):
                    column=int(self.map_grid.item(row,0).text());role=self.map_grid.cellWidget(row,1).currentIndex();name=self.map_grid.item(row,2).text().strip();unit=self.map_grid.item(row,3).text().strip()
                    if role==1:sample_columns.append(column)
                    elif role==2:conditions[column]=name
                    elif role==3:metrics[column]=(name,unit)
                if len(sample_columns)!=1:raise ValueError('请选择一列样品编号。')
                draft=from_table(self.document,ColumnMap(self.table_combo.currentText(),self.header_row.value(),(self.header_row.value(),self.column_start.value(),self.row_end.value(),self.column_end.value()),sample_columns[0],conditions,metrics,self.duplicates.currentData()),title=title,date=when)
            else:
                codes=[s.strip() for s in re.split('[,，;；\n]',self.sample_codes.text()) if s.strip()]
                if not codes:raise ValueError('请先填写样品编号。')
                selected=set(self.selected_locators())
                draft=ExperimentDraft(title,when,samples=[SampleDraft(code) for code in codes],notes='\n'.join(f'{b.locator}\n{b.text}' for b in self.document.blocks if b.locator in selected),source_ids=[self.document.file_id])
            self.set_editor(draft);self.message.setText('请核对草稿后保存。')
        except ValueError as exc:self.message.setText(str(exc))

    def organize_selected(self):
        try:base=self.editor.collect();locators=self.selected_locators()
        except ValueError as exc:self.message.setText(str(exc));return
        def extract():
            draft=organize(self.document,locators,base,self.model_client);target=None
            if self.candidates:
                from .import_agent import match_record
                from matknow.llm import ModelError
                try:target=match_record(draft,self.candidates,self.model_client)
                except (ModelError,ValueError):draft.notes+='\n\n关联推荐未完成，默认新建实验，请核对保存位置。'
            return draft,target
        def ready(result):
            draft,target=result;self.target.setCurrentIndex(max(0,self.target.findData(target)));self.set_editor(draft)
            if target:self.message.setText('模型推荐关联已有实验，请核对保存位置、样品和结果后保存。')
        self.run(extract,ready,'实验智能体正在读取并整理…')

    def accept(self):
        if self.busy:return
        try:
            if not self.confirm_check.isChecked():raise ValueError('请先核对草稿并勾选确认。')
            if not self.draft_ready:raise ValueError('请先使用模型或手动生成草稿。')
            if self.source_path is None:raise ValueError('原始文件未关联，请重新导入。')
            self.draft=confirm_draft(self.editor.collect())
            target=self.target.currentData()
            self.snapshot=next((s for s in self.candidates if s.id==target),None)
            if self.snapshot is not None:
                from .import_agent import merge_record
                old_values=[r.value for sample in self.snapshot.draft.samples for r in sample.results]
                tokens=set(self.snapshot.draft.source_ids)|{v.source.file_id for v in old_values if v.source}
                same_files={token for token in tokens if self.archive.attachment(token).sha256==self.document.sha256}
                old_measurements={(sample.code.casefold(),result.metric,result.value.source.locator) for sample in self.snapshot.draft.samples for result in sample.results if result.value.source and result.value.source.file_id in same_files}
                if any((sample.code.casefold(),result.metric,result.value.source.locator) in old_measurements for sample in self.draft.samples for result in sample.results if result.value.source):raise ValueError('选定范围包含已导入的重复测量，请选择尚未导入的范围。')
                self.draft=merge_record(self.snapshot.draft,self.draft)
            self.uploads=[Upload(Path(self.source_path),self.document.file_id,'source',expected_sha256=self.document.sha256)]
        except ValueError as exc:self.message.setText(str(exc));return
        super().accept()
