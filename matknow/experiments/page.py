from pathlib import Path
import sqlite3
from uuid import uuid4

from PySide6.QtCore import Qt, Signal, QTimer, QItemSelectionModel
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QComboBox, QPlainTextEdit, QListWidget, QListWidgetItem, QFrame,
    QAbstractItemView, QFileDialog, QDialog, QMessageBox, QMenu, QTabWidget)

from .editors import FormDialog
from .dialogs import ExperimentDialog, ResultDialog, RecordImportDialog, ComparisonDialog
from .readers import read_record, workbook_sheets
from .preview import AttachmentPreview, CurveSpec, prepare_attachment
from .models import chemistry_text


class ExperimentPage(QWidget):
    taskRequested=Signal(object,object,str)
    selectionChanged=Signal()
    statusChanged=Signal(str)

    def __init__(self,store,archive,parent=None):
        super().__init__(parent)
        self.store,self.archive=store,archive;self.model_client=None;self.busy=False
        self.selection=set();self.batch_offset=0;self.rows=[];self.pending_retry=None
        self.evidence_snapshot=None;self.current_result=None
        layout=QVBoxLayout(self);layout.setContentsMargins(0,8,0,0);layout.setSpacing(12)
        bar=QHBoxLayout()
        for name,function in [('＋ 记实验',self.new),('导入',self.choose_record)]:
            button=QPushButton(name);button.clicked.connect(function);bar.addWidget(button)
        bar.addStretch()
        more=QPushButton('更多');more.setAccessibleName('实验记录更多操作');more.setToolTip('更多操作')
        menu=QMenu(more);menu.addAction('选择用于问答和对比的样品',self.choose_samples);menu.addAction('对比所选样品的条件和结果',self.compare);menu.addSeparator()
        self.target_action=menu.addAction('当前实验：未选择');self.target_action.setEnabled(False)
        menu.aboutToShow.connect(self.update_action_target)
        menu.addAction('生成当前实验的完整报告',self.generate_report);menu.addAction('导出当前实验记录和附件',self.export_record);menu.addAction('删除所选实验及其样品和附件',self.delete_selected);more.setMenu(menu);bar.addWidget(more);layout.addLayout(bar)
        searchbar=QHBoxLayout();self.search=QLineEdit();self.search.setPlaceholderText('搜索实验名称 / 日期');self.search.returnPressed.connect(self.search_records)
        self.search.setClearButtonEnabled(True);self.search.textChanged.connect(lambda text:self.search_records() if not text else None)
        find=QPushButton('搜索');find.clicked.connect(self.search_records);searchbar.addWidget(self.search,1);searchbar.addWidget(find);layout.addLayout(searchbar)
        panel=QFrame();panel.setObjectName('panel');listing=QVBoxLayout(panel)
        self.empty_label=QLabel('还没有实验记录\n\n点击“记实验”写下过程，或导入 Word / Excel。');self.empty_label.setWordWrap(True);listing.addWidget(self.empty_label)
        self.record_list=QListWidget();self.record_list.setAccessibleName('实验记录列表');self.record_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.record_list.setWordWrap(True);self.record_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.record_list.itemSelectionChanged.connect(self.selection_changed);self.record_list.itemDoubleClicked.connect(lambda _:self.open_record())
        self.record_list.currentItemChanged.connect(lambda current,previous:self.open_button.setEnabled(current is not None))
        listing.addWidget(self.record_list,1);layout.addWidget(panel,1)
        scope=QHBoxLayout();self.scope_label=QLabel('');self.scope_label.setObjectName('muted');scope.addWidget(self.scope_label,1)
        self.delete_button=QPushButton('删除所选');self.delete_button.setAccessibleName('删除所选实验记录');self.delete_button.clicked.connect(self.delete_selected);scope.addWidget(self.delete_button);layout.addLayout(scope)
        footer=QHBoxLayout();self.previous=QPushButton('上一页');self.next=QPushButton('下一页');self.clear_button=QPushButton('取消选择')
        self.previous.clicked.connect(lambda:self.show_page(self.batch_offset-50));self.next.clicked.connect(lambda:self.show_page(self.batch_offset+50));self.clear_button.clicked.connect(self.clear_selection)
        footer.addWidget(self.previous);footer.addWidget(self.next);footer.addWidget(self.clear_button);footer.addStretch()
        self.open_button=QPushButton('打开记录');self.open_button.clicked.connect(self.open_record);footer.addWidget(self.open_button);layout.addLayout(footer)
        self.detail_dialog=QDialog(self.window());self.detail_dialog.setWindowTitle('实验记录');self.detail_dialog.resize(850,650)
        detail_layout=QVBoxLayout(self.detail_dialog)
        self.source_title=QLabel('');self.source_title.setObjectName('sectionTitle');self.source_title.setTextFormat(Qt.TextFormat.PlainText);self.source_title.setWordWrap(True);detail_layout.addWidget(self.source_title)
        actions=QHBoxLayout();self.sample_combo=QComboBox();self.sample_combo.setAccessibleName('查看样品');self.sample_combo.currentIndexChanged.connect(self.change_detail_sample);actions.addWidget(self.sample_combo,1)
        self.edit_button=QPushButton('编辑记录');self.edit_button.clicked.connect(self.edit);actions.addWidget(self.edit_button)
        self.result_button=QPushButton('添加结果');self.result_button.clicked.connect(self.add_results);actions.addWidget(self.result_button);detail_layout.addLayout(actions)
        self.report_button=QPushButton('生成报告');self.report_button.clicked.connect(lambda:self.generate_report(displayed=True));actions.addWidget(self.report_button)
        self.detail_tabs=QTabWidget();detail_layout.addWidget(self.detail_tabs,1)
        self.source_view=QPlainTextEdit();self.source_view.setReadOnly(True);self.detail_tabs.addTab(self.source_view,'记录')
        self.result_list=QListWidget();self.result_list.itemClicked.connect(lambda entry:self.show_evidence(entry.data(Qt.ItemDataRole.UserRole)));self.detail_tabs.addTab(self.result_list,'结果')
        attachment_widget=QWidget();attachment_layout=QVBoxLayout(attachment_widget);attachment_layout.setContentsMargins(0,0,0,0)
        self.attachments=QListWidget();self.attachments.setMaximumHeight(85);self.attachments.itemClicked.connect(self.open_attachment);attachment_layout.addWidget(self.attachments)
        self.preview=AttachmentPreview();attachment_layout.addWidget(self.preview,1);self.preview.hide();self.detail_tabs.addTab(attachment_widget,'附件')
        self.refresh()

    def bind(self,store,archive,model_client):
        changed=self.store.knowledge.path!=store.knowledge.path
        self.store,self.archive,self.model_client=store,archive,model_client
        if changed:
            self.detail_dialog.hide();self.selection.clear();self.rows=[];self.batch_offset=0;self.pending_retry=None
            self.evidence_snapshot=None;self.current_result=None;self.source_view.clear();self.source_title.clear();self.attachments.clear();self.result_list.clear();self.preview.clear();self.preview.hide();self.search.clear();self.detail_tabs.setCurrentIndex(0)
            self.refresh()

    def set_busy(self,busy):
        self.busy=busy;self.setEnabled(not busy);self.detail_dialog.setEnabled(not busy)

    def refresh(self):
        current=self.record_list.currentItem();focused=current.data(Qt.ItemDataRole.UserRole) if current else None
        summaries=self.store.list_experiments(self.search.text(),offset=self.batch_offset,limit=51)
        if not summaries and self.batch_offset:
            self.batch_offset=max(0,self.batch_offset-50);self.refresh();return
        self.rows=[self.store.get(summary.id) for summary in summaries[:50]]
        if self.selection:
            found=set();ids=list(self.selection)
            with self.store.knowledge.connection() as db:
                for start in range(0,len(ids),500):
                    chunk=ids[start:start+500]
                    found.update(r[0] for r in db.execute('SELECT id FROM samples WHERE id IN ('+','.join('?' for _ in chunk)+')',chunk))
            self.selection.intersection_update(found)
        self.has_more=len(summaries)>50
        self.record_list.blockSignals(True);self.record_list.clear()
        for snapshot in self.rows:
            samples=snapshot.draft.samples;count=sum(len(sample.results) for sample in samples)
            preview=' '.join(snapshot.draft.notes.split())[:65]
            entry=QListWidgetItem(f'{snapshot.draft.title}\n{snapshot.draft.date} · {len(samples)} 个样品 · {count} 项结果'+(f'\n{preview}' if preview else ''))
            entry.setData(Qt.ItemDataRole.UserRole,snapshot.id);entry.setToolTip('双击打开记录；单击可多选用于问答');self.record_list.addItem(entry)
            entry.setSelected(any(sample.id in self.selection for sample in samples))
            if snapshot.id==focused:self.record_list.setCurrentItem(entry,QItemSelectionModel.SelectionFlag.NoUpdate)
        self.record_list.blockSignals(False)
        self._selected_records={entry.data(Qt.ItemDataRole.UserRole) for entry in self.record_list.selectedItems()}
        self.empty_label.setVisible(not self.rows);self.empty_label.setText('没有找到记录' if self.search.text() else '还没有实验记录\n\n点击“记实验”写下过程，或导入 Word / Excel。')
        for button in (self.previous,self.next):button.setVisible(self.batch_offset>0 or self.has_more)
        self.previous.setEnabled(self.batch_offset>0);self.next.setEnabled(self.has_more);self.update_scope()

    def search_records(self):
        self.batch_offset=0;self.selection.clear();self.refresh()

    def show_page(self,offset):
        if offset<0 or (offset>self.batch_offset and not self.has_more):return
        self.batch_offset=offset;self.refresh()

    def selection_changed(self):
        selected={entry.data(Qt.ItemDataRole.UserRole) for entry in self.record_list.selectedItems()}
        previous=self._selected_records
        self.selection.difference_update(sample.id for snapshot in self.rows if snapshot.id in previous-selected for sample in snapshot.draft.samples)
        self.selection.update(sample.id for snapshot in self.rows if snapshot.id in selected-previous for sample in snapshot.draft.samples)
        self._selected_records=selected
        self.update_scope()

    def update_action_target(self):
        current=self.record_list.currentItem()
        self.target_action.setText('当前实验：'+(current.text().split('\n')[0] if current else '未选择'))

    def update_scope(self):
        self.scope_label.setText(f'问答范围：已选 {len(self.selection)} 个样品' if self.selection else '单击选择 · 双击打开记录')
        self.clear_button.setVisible(bool(self.selection));self.open_button.setEnabled(bool(self.record_list.currentItem()))
        current=self.record_list.currentItem()
        title=current.text().split('\n')[0] if current else '未选择'
        self.open_button.setToolTip('当前记录：'+title)
        self.report_button.setToolTip('报告目标：'+(self.evidence_snapshot.draft.title if self.evidence_snapshot else title))
        self.delete_button.setVisible(bool(self.selection))
        self.selectionChanged.emit()
    def selected_sample_ids(self):return sorted(self.selection)
    def clear_selection(self):self.selection.clear();self.record_list.clearSelection();self.update_scope()

    def selected_snapshot(self, *, detail=False):
        if detail and self.evidence_snapshot:
            snapshot=self.store.get(self.evidence_snapshot.id)
            sample=next((s for s in snapshot.draft.samples if s.id==self.sample_combo.currentData()),snapshot.draft.samples[0])
            return snapshot,sample
        entry=self.record_list.currentItem()
        if entry is None:raise ValueError('请先打开一条实验记录。')
        snapshot=self.store.get(entry.data(Qt.ItemDataRole.UserRole));return snapshot,snapshot.draft.samples[0]

    def open_record(self):
        try:snapshot,sample=self.selected_snapshot()
        except ValueError as exc:self.statusChanged.emit(str(exc));return
        evidence=self.store.evidence_rows([sample.id])
        if evidence:self.show_evidence(evidence[0]['id'])

    def change_detail_sample(self):
        if not self.evidence_snapshot or self.sample_combo.currentIndex()<0:return
        with self.store.knowledge.connection() as db:
            row=db.execute('SELECT id FROM experiment_evidence WHERE experiment_id=? AND revision=? AND sample_id=? AND result_id IS NULL',(self.evidence_snapshot.id,self.evidence_snapshot.revision,self.sample_combo.currentData())).fetchone()
        if row:self.show_evidence(row['id'])

    def choose_samples(self):
        dialog=FormDialog(self.window());dialog.setWindowTitle('选择用于问答和对比的样品');dialog.resize(560,430)
        listing=QListWidget();listing.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection);dialog.layout.addWidget(listing)
        for snapshot in self.rows:
            for sample in snapshot.draft.samples:
                entry=QListWidgetItem(f'{snapshot.draft.title} / {sample.code} · {snapshot.draft.date}');entry.setData(Qt.ItemDataRole.UserRole,sample.id);listing.addItem(entry);entry.setSelected(sample.id in self.selection)
        dialog.finish_form('使用所选样品')
        if dialog.exec()==QDialog.DialogCode.Accepted:
            visible={listing.item(i).data(Qt.ItemDataRole.UserRole) for i in range(listing.count())}
            self.selection.difference_update(visible);self.selection.update(entry.data(Qt.ItemDataRole.UserRole) for entry in listing.selectedItems());self.refresh()
        dialog.deleteLater()

    def new(self):self.submit_dialog(ExperimentDialog(parent=self.window()))

    def edit(self):
        try:snapshot,_=self.selected_snapshot(detail=True)
        except ValueError as exc:self.statusChanged.emit(str(exc));return
        self.submit_dialog(ExperimentDialog(snapshot,parent=self.window()),snapshot)

    def add_results(self):
        try:snapshot,sample=self.selected_snapshot(detail=True)
        except ValueError as exc:self.statusChanged.emit(str(exc));return
        self.submit_dialog(ResultDialog(snapshot,sample.id,parent=self.window(),archive=self.archive),snapshot)

    def submit_dialog(self,dialog,snapshot=None):
        if self.busy:return
        if dialog.exec()!=QDialog.DialogCode.Accepted:
            dialog.deleteLater();return
        if isinstance(dialog,RecordImportDialog):snapshot=dialog.snapshot
        archive=self.archive;draft=dialog.draft;uploads=getattr(dialog,'uploads',[])
        self.pending_retry=(dialog,snapshot)
        def saved(result):
            self.pending_retry=None;self.refresh();self.statusChanged.emit('实验记录已保存。');dialog.deleteLater()
            evidence=self.store.evidence_rows([getattr(dialog,'sample_id',result.draft.samples[0].id)])
            if evidence:self.show_evidence(evidence[0]['id'])
        self.taskRequested.emit(lambda:archive.save(draft,uploads,experiment_id=snapshot.id if snapshot else None,expected_revision=snapshot.revision if snapshot else None,draft_id=getattr(dialog,'draft_id',None)),saved,'正在保存实验…')

    def task_failed(self,message):
        if self.pending_retry:
            dialog,snapshot=self.pending_retry;self.pending_retry=None;dialog.message.setText(message)
            # The worker releases the shared lock before reopening the intact editable form.
            QTimer.singleShot(0,lambda:self.submit_dialog(dialog,snapshot))

    def choose_record(self):
        path,_=QFileDialog.getOpenFileName(self,'导入实验记录','','Word / Excel (*.docx *.xlsx)')
        if path:self.import_record(Path(path),choose_sheet=True)

    def import_record(self,path,*,choose_sheet=False):
        if self.busy:return
        token=str(uuid4());sheets=None
        if choose_sheet and path.suffix.lower()=='.xlsx' and self.model_client is None:
            # Workbook metadata is read in the same worker as parsing; sheet chooser follows it.
            self.taskRequested.emit(lambda:workbook_sheets(path),lambda names:QTimer.singleShot(0,lambda:self.select_sheet(path,names)),'正在读取工作表…');return
        client=self.model_client
        def read():
            try:return read_record(path,file_id=token,sheets=sheets)
            except ValueError as exc:
                if choose_sheet and client is not None and path.suffix.lower()=='.xlsx' and ('50000' in str(exc) or '最多 20' in str(exc)):return None
                raise
        def ready(document):
            def open_dialog():
                if document is None:
                    self.statusChanged.emit('工作簿超出读取范围，请选择工作表或较小范围后继续 AI 整理。')
                    self.taskRequested.emit(lambda:workbook_sheets(path),lambda names:QTimer.singleShot(0,lambda:self.select_sheet(path,names)),'正在读取工作表…');return
                dialog=RecordImportDialog(document,client,parent=self.window(),archive=self.archive);dialog.source_path=path;self.submit_dialog(dialog)
            QTimer.singleShot(0,open_dialog)
        self.taskRequested.emit(read,ready,'正在读取实验记录…')

    def select_sheet(self,path,names):
        from .editors import FormDialog
        chooser=FormDialog(self.window());chooser.setWindowTitle('选择工作表 / 范围');chooser.resize(550,220)
        sheet=QComboBox();sheet.addItems(names);bounds_edit=QLineEdit();bounds_edit.setPlaceholderText('起始行,起始列,结束行,结束列；留空读取全部')
        chooser.layout.addWidget(sheet);chooser.layout.addWidget(bounds_edit);chooser.finish_form('读取')
        if chooser.exec()!=QDialog.DialogCode.Accepted:return
        name=sheet.currentText();bounds=None
        try:
            if bounds_edit.text().strip():
                bounds=tuple(int(v.strip()) for v in bounds_edit.text().split(','))
                if len(bounds)!=4:raise ValueError('范围需要四个整数。')
        except ValueError:self.statusChanged.emit('数据范围无效，请重新导入。');return
        token=str(uuid4());client=self.model_client
        def ready(document):
            def open_dialog():
                dialog=RecordImportDialog(document,client,parent=self.window(),archive=self.archive);dialog.source_path=path;self.submit_dialog(dialog)
            QTimer.singleShot(0,open_dialog)
        self.taskRequested.emit(lambda:read_record(path,file_id=token,sheets=[name],bounds=bounds),ready,'正在读取实验记录…')

    def compare(self):
        try:dialog=ComparisonDialog(self.store,self.selected_sample_ids(),parent=self.window())
        except ValueError as exc:self.statusChanged.emit(str(exc));return
        def ask(question,comparison):
            if not question.strip():dialog.message.setText('请输入问题。');return
            dialog.accept();self.ask_comparison(question,comparison)
        dialog.askRequested.connect(ask);dialog.exec();dialog.deleteLater()

    def ask_comparison(self,question,comparison):self.window().ask(question=question,comparison=comparison)

    def show_evidence(self,source_id,*,open_dialog=True):
        if not source_id.startswith('exp:'):
            evidence=self.store.knowledge.get_evidence(source_id)
            self.preview.clear();self.preview.hide();self.attachments.clear();self.result_list.clear();self.current_result=None;self.evidence_snapshot=None
            self.source_title.setText(f'{evidence.filename} · {evidence.locator}' if evidence else '来源已删除')
            self.source_view.setPlainText(evidence.text if evidence else '')
            return
        evidence=self.store.resolve_many([source_id]).get(source_id)
        self.preview.clear();self.preview.hide();self.attachments.clear();self.current_result=None
        if evidence is None:
            self.source_title.setText('来源已删除');self.source_view.clear();self.result_list.clear();self.evidence_snapshot=None;self.detail_dialog.hide();return
        with self.store.knowledge.connection() as db:
            row=db.execute('SELECT experiment_id,revision,sample_id,result_id FROM experiment_evidence WHERE id=?',(source_id,)).fetchone()
            results=db.execute('SELECT id,result_id FROM experiment_evidence WHERE experiment_id=? AND revision=? AND sample_id=? AND result_id IS NOT NULL',(row['experiment_id'],row['revision'],row['sample_id'])).fetchall()
        snapshot=self.store.get(row['experiment_id'],row['revision']);self.evidence_snapshot=snapshot
        sample=next(s for s in snapshot.draft.samples if s.id==row['sample_id'])
        self.sample_combo.blockSignals(True);self.sample_combo.clear()
        for recorded in snapshot.draft.samples:self.sample_combo.addItem('样品 '+recorded.code,recorded.id)
        self.sample_combo.setCurrentIndex(self.sample_combo.findData(sample.id));self.sample_combo.blockSignals(False)
        result=next((r for r in sample.results if r.id==row['result_id']),None);self.current_result=result
        self.result_list.clear()
        source_ids={r['result_id']:r['id'] for r in results}
        for recorded in sample.results:
            entry=QListWidgetItem(f'{recorded.metric}：{recorded.value.text} {recorded.value.unit}'.rstrip())
            entry.setData(Qt.ItemDataRole.UserRole,source_ids[recorded.id]);self.result_list.addItem(entry)
        historical=self.store.get(snapshot.id).revision!=snapshot.revision
        self.source_title.setText(f'{snapshot.draft.title}\n{snapshot.draft.date}'+(f' · 历史版本 {snapshot.revision}' if historical else ''))
        self.source_title.setToolTip(f'版本 {snapshot.revision}');self.edit_button.setEnabled(not historical);self.result_button.setEnabled(not historical)
        lines=[snapshot.draft.notes.strip() or '还没有填写实验过程。']
        detail=chemistry_text(snapshot.draft.chemistry,sample.code)
        if detail:lines+=['',detail]
        conditions={**snapshot.draft.common,**sample.conditions}
        if conditions:lines+=['','制备条件',*[f'{name}：{value.text} {value.unit}'.rstrip() for name,value in conditions.items()]]
        if result:
            lines+=['',f'{result.metric}：{result.value.text} {result.value.unit}'.rstrip()]
            if result.method:lines.append('测试方法：'+result.method)
            if result.test_conditions:lines.extend(f'{name}：{value}' for name,value in result.test_conditions.items())
            if result.measured_at:lines.append('测试时间：'+result.measured_at)
            if result.repeat_group:lines.append('重复测量组：'+result.repeat_group)
            if result.notes:lines+=['',result.notes]
        self.source_view.setPlainText('\n'.join(lines));self.detail_tabs.setTabText(1,f'结果（{len(sample.results)}）')
        values=[*snapshot.draft.common.values(),*sample.conditions.values()]
        if result:values.append(result.value)
        files=result.attachment_ids if result else [file_id for recorded in sample.results for file_id in recorded.attachment_ids]
        ids=list(dict.fromkeys([*snapshot.draft.source_ids,*[v.source.file_id for v in values if v.source],*files]))
        for file_id in ids:
            try:attachment=self.archive.attachment(file_id)
            except ValueError:continue
            entry=QListWidgetItem(attachment.filename);entry.setData(Qt.ItemDataRole.UserRole,file_id);self.attachments.addItem(entry)
        self.detail_tabs.setTabText(2,f'文件（{self.attachments.count()}）');self.detail_tabs.setCurrentIndex(0)
        if open_dialog:self.detail_dialog.show();self.detail_dialog.raise_()

    def open_attachment(self,entry):
        file_id=entry.data(Qt.ItemDataRole.UserRole);archive=self.archive
        recorded=next((r for s in self.evidence_snapshot.draft.samples for r in s.results if file_id in r.attachment_ids),None) if self.evidence_snapshot else None
        data=recorded.data if recorded else {}
        curve=CurveSpec(**{k:data[k] for k in ('x_column','y_column','x_unit','y_unit','sort_x') if k in data}) if data.get('attachment_id')==file_id and 'x_column'in data and 'y_column'in data else None
        def ready(payload):
            self.preview.show();self.preview.apply_prepared(archive,file_id,payload);self.detail_tabs.setCurrentIndex(2)
            if curve:self.preview.content.setCurrentIndex(1)
        self.taskRequested.emit(lambda:prepare_attachment(archive,file_id,curve,sheet=data.get('sheet') if data.get('attachment_id')==file_id else None,bounds=data.get('bounds') if data.get('attachment_id')==file_id else None),ready,'正在读取附件…')

    def profile_name(self):
        root=self.window();return root.profiles.get(root.profile_id).name if getattr(root,'profiles',None) else '研究方向'

    def generate_report(self,checked=False,*,displayed=False):
        if self.busy:return
        try:snapshot=self.evidence_snapshot if displayed and self.evidence_snapshot else self.selected_snapshot()[0]
        except ValueError as exc:self.statusChanged.emit(str(exc));return
        from .report_dialog import ReportDialog
        archive,name,client=self.archive,self.profile_name(),self.model_client
        def build():
            from .report_writer import generate_report
            return generate_report(archive,snapshot.id,revision=snapshot.revision,profile_name=name,client=client,progress=self.statusChanged.emit)
        def ready(report):
            def show():
                dialog=ReportDialog(report,self.window(),model_client=client,archive=archive);dialog.exec();dialog.deleteLater()
            QTimer.singleShot(0,show)
        self.taskRequested.emit(build,ready,'正在读取记录并生成完整报告' if client else '正在整理原始记录')

    def export_record(self):
        try:snapshot,_=self.selected_snapshot()
        except ValueError as exc:self.statusChanged.emit(str(exc));return
        path,_=QFileDialog.getSaveFileName(self,'导出实验档案','材知-实验档案.zip','ZIP (*.zip)')
        if not path:return
        from .export import export_experiment
        archive,name=self.archive,self.profile_name()
        self.taskRequested.emit(lambda:export_experiment(archive,snapshot.id,path,profile_name=name),lambda _:self.statusChanged.emit('实验档案已导出。'),'正在导出实验档案…')

    def delete_selected(self):
        if self.busy:return
        if not self.selection:self.statusChanged.emit('请先选择要删除的实验记录。');return
        try:
            snapshots={snapshot.id:snapshot for snapshot,_ in self.store.samples(self.selected_sample_ids())}
        except (ValueError,OSError,sqlite3.Error) as exc:self.statusChanged.emit('无法读取所选实验：'+str(exc));return
        records=list(snapshots.values())
        names=[f'{snapshot.draft.title} · {snapshot.draft.date}（{len(snapshot.draft.samples)} 个样品）' for snapshot in records]
        confirmation=QMessageBox(self);confirmation.setWindowTitle('删除所选实验');confirmation.setIcon(QMessageBox.Icon.Warning);confirmation.setTextFormat(Qt.TextFormat.PlainText)
        confirmation.setText(f'永久删除所选 {len(records)} 条实验？\n包含全部样品、历史版本和附件副本，历史引用将失效。\n\n'+'\n'.join(names[:8])+('\n其余记录见详细信息。' if len(names)>8 else ''))
        if len(names)>8:confirmation.setDetailedText('\n'.join(names))
        confirmation.setStandardButtons(QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No);confirmation.setDefaultButton(QMessageBox.StandardButton.No);confirmation.setEscapeButton(QMessageBox.StandardButton.No)
        confirmation.button(QMessageBox.StandardButton.Yes).setText('删除');confirmation.button(QMessageBox.StandardButton.No).setText('取消')
        reply=confirmation.exec();confirmation.deleteLater()
        if reply!=QMessageBox.StandardButton.Yes:return
        archive=self.archive
        def remove():
            removed,failures,warnings,unknown=set(),[],[],{}
            for snapshot in records:
                try:archive.delete(snapshot.id)
                except Exception as exc:
                    # File cleanup can fail after metadata commits; verify before counting it.
                    try:
                        with archive.store.knowledge.connection() as db:
                            exists=db.execute('SELECT 1 FROM experiments WHERE id=?',(snapshot.id,)).fetchone()
                    except Exception as verification_error:
                        unknown[snapshot.id]=f'{snapshot.draft.title}：{verification_error}';continue
                    if exists:failures.append(f'{snapshot.draft.title}：{exc}');continue
                    warnings.append(str(exc))
                removed.add(snapshot.id)
            return removed,failures,warnings,unknown
        def deleted(result):
            removed,failures,warnings,unknown=result
            self.selection.difference_update(sample.id for snapshot in records if snapshot.id in removed for sample in snapshot.draft.samples)
            if self.evidence_snapshot and (self.evidence_snapshot.id in removed or self.evidence_snapshot.id in unknown):
                self.detail_dialog.hide();self.evidence_snapshot=None;self.current_result=None
                self.source_view.clear();self.source_title.clear();self.attachments.clear();self.result_list.clear();self.preview.clear();self.preview.hide()
            message=f'已删除 {len(removed)} 条实验。'
            if failures:message+=f'{len(failures)} 条未删除：'+'；'.join(failures)
            if unknown:message+=f'{len(unknown)} 条删除状态未确认：'+'；'.join(unknown.values())
            if warnings:message+='；'+'；'.join(dict.fromkeys(warnings))
            try:self.refresh()
            except Exception:
                self.record_list.blockSignals(True);self.record_list.clear();self.record_list.blockSignals(False)
                self.rows=[];self._selected_records=set();self.empty_label.setText('记录列表待刷新，请重新搜索。');self.empty_label.show();self.update_scope()
                message+='；列表待刷新，请稍后重新搜索。'
            root=self.window()
            try:
                root.refresh_history()
                if root.last_answer:root.present_answer(root.last_answer)
            except Exception:
                root.last_answer=None;root.answer_view.clear();root.source_view.clear();root.history_list.clear()
                root.source_title.setText('历史引用状态待刷新')
                message+='；历史引用状态待刷新，请稍后重新打开应用。'
            self.statusChanged.emit(message)
        self.taskRequested.emit(remove,deleted,'正在删除实验和附件…')
