import html
import json
import re
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QCloseEvent, QTextDocument, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFrame, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QInputDialog,
    QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from matknow.knowledge import KnowledgeBase
from matknow.models import Answer
from matknow.conversations import Conversations,scope_key,ContextMemory
from matknow.agent import ResearchAgent, execution_summary,CHAT_PROMPT
from matknow.credentials import CredentialError
from matknow.llm import DeepSeekClient, list_provider_models
from matknow.settings import ModelSettingsDialog, ProfileDialog
from matknow.tasks import Task
from matknow.model_context import estimate_tokens
from matknow.theme import STYLE


from matknow.ui_controls import label, button, QuestionEdit, TaskActivity, ChatDocument
from matknow.experiments.store import ExperimentStore
from matknow.experiments.files import ExperimentArchive
from matknow.experiments.page import ExperimentPage


class MainWindow(QMainWindow):
    taskBusyChanged = Signal(bool)
    agentProgress = Signal(str)
    contextUsage = Signal(object)

    def __init__(self, knowledge: KnowledgeBase, profiles=None, credentials=None, allow_model=True):
        super().__init__()
        self.knowledge = knowledge
        self.profiles, self.credentials = profiles, credentials
        self.allow_model = allow_model
        self.profile_id = profiles.current_id if profiles else None
        self.model_problem = ''
        self.service = ResearchAgent(knowledge)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.busy = False
        self.deletion_mode = False
        self._query_scope_before_delete: set[str] = set()
        self.last_answer: Answer | None = None
        self.conversations=Conversations(knowledge)
        self.conversation_id=None;self.conversation=[];self._turn_scopes={}
        self._pending_question=None;self._unavailable_scope=None
        self.task: Task | None = None
        self.setWindowTitle("材知 · 科研知识库助手")
        self.resize(1280, 840)
        self.setMinimumSize(1000, 680)
        self.setStyleSheet(STYLE)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 15)
        layout.setSpacing(17)
        header = QHBoxLayout()
        branding = QVBoxLayout()
        branding.setSpacing(1)
        branding.addWidget(label("材知  /  MATKNOW", "brand"))
        branding.addWidget(label("科研知识库助手", "subtitle"))
        header.addLayout(branding)
        header.addStretch()
        header.addWidget(label('●  本地知识库', 'badge'))
        layout.addLayout(header)

        direction_bar = QHBoxLayout()
        direction_bar.addWidget(label('研究方向', 'muted'))
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(210)
        self.profile_combo.setAccessibleName('当前研究方向 Profile')
        self.profile_combo.activated.connect(lambda index: self.switch_profile(self.profile_combo.itemData(index)))
        direction_bar.addWidget(self.profile_combo)
        self.new_profile_button = button('＋ 新建方向', self.new_profile)
        self.edit_profile_button = button('方向设置', self.edit_profile)
        direction_bar.addWidget(self.new_profile_button)
        direction_bar.addWidget(self.edit_profile_button)
        direction_bar.addStretch()
        self.model_settings_button = button('模型设置', self.model_settings)
        direction_bar.addWidget(self.model_settings_button)
        layout.addLayout(direction_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        self.library_panel = self._library_panel()
        self.workspaces = QTabWidget()
        self.workspaces.addTab(self.library_panel, '资料库')
        experiment_store = ExperimentStore(knowledge)
        experiment_archive = ExperimentArchive(experiment_store)
        experiment_archive.recover()
        self.experiment_page = ExperimentPage(experiment_store, experiment_archive, self)
        self.experiment_page.taskRequested.connect(self.start_task)
        self.experiment_page.selectionChanged.connect(self.update_context)
        self.experiment_page.statusChanged.connect(lambda message: self.status_label.setText(message))
        self.workspaces.addTab(self.experiment_page, '实验记录')
        splitter.addWidget(self.workspaces)
        splitter.addWidget(self._question_panel())
        self.evidence_panel = self._evidence_panel()
        splitter.addWidget(self.evidence_panel)
        splitter.setSizes([395, 530, 260])
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        footer = QHBoxLayout()
        self.status_label = label("准备就绪", "muted")
        self.agentProgress.connect(self._agent_progress)
        self.contextUsage.connect(self._context_usage)
        footer.addWidget(self.status_label, 1)
        self.model_state = label('本地检索', 'muted')
        footer.addWidget(self.model_state)
        layout.addLayout(footer)
        self.setCentralWidget(root)
        self.refresh_documents()
        self.refresh_history()
        self._refresh_profile_combo()
        self._reload_service()
        self._update_controls()

    def _refresh_profile_combo(self):
        profiles = self.profiles.list_profiles() if self.profiles else None
        was_blocked = self.profile_combo.blockSignals(True)
        try:
            self.profile_combo.clear()
            if profiles:
                for profile in profiles:
                    self.profile_combo.addItem(profile.name, profile.id)
                self.profile_combo.setCurrentIndex(self.profile_combo.findData(self.profile_id))
            else:
                self.profile_combo.addItem('材料制备')
        finally:
            self.profile_combo.blockSignals(was_blocked)

    def _service_for(self, knowledge, profile):
        client, problem = None, ''
        config = self.profiles.models.get(profile.model) if profile else None
        if self.credentials is not None and self.allow_model:
            try:
                if self.profiles:self.profiles.migrate_legacy_models(self.credentials)
                key = self.credentials_for(config.url).get() if config else None
                if key:
                    client = DeepSeekClient(key, config.model, base_url=config.url)
                    client.context_window=self.profiles.models.context_window(config.id)
                    client.context_callback=self.contextUsage.emit
            except CredentialError as exc:
                problem = str(exc)
            except OSError:
                problem = '模型配置未保存，请检查数据目录权限。'
        return ResearchAgent(knowledge, client, profile.description if profile else ''), problem

    def credentials_for(self, url):
        return self.credentials.for_endpoint(url) if hasattr(self.credentials, 'for_endpoint') else self.credentials

    def _reload_service(self):
        profile = self.profiles.get(self.profile_id) if self.profiles else None
        self.service, self.model_problem = self._service_for(self.knowledge, profile)
        self._update_model_state(profile)
        if self.model_problem:
            self.status_label.setText(self.model_problem)

    def _update_model_state(self, profile):
        self._context_preview_key=None
        entries = self.profiles.models.list_models() if self.profiles else []
        self.current_config = next((entry for entry in entries if profile and entry.id == profile.model), None)
        self.current_model = self.current_config.id if self.current_config else None
        providers=self.profiles.models.list_providers() if self.profiles else []
        current_provider=next((p for p in providers if profile and (p.id==profile.provider or any(m.id==self.current_model for m in p.models))),None)
        self.current_provider=current_provider
        for combo in (self.provider_combo,self.model_combo):combo.blockSignals(True);combo.clear()
        for provider in providers:
            title=provider.name
            if sum(p.name==provider.name for p in providers)>1:title+=' · '+provider.url.removesuffix('/chat/completions')
            self.provider_combo.addItem(title,provider.id)
            self.provider_combo.setItemData(self.provider_combo.count()-1,provider.url,Qt.ItemDataRole.ToolTipRole)
        self.provider_combo.setCurrentIndex(self.provider_combo.findData(current_provider.id if current_provider else None))
        if current_provider:
            for entry in current_provider.models:self.model_combo.addItem(entry.model,entry.id)
        self.model_combo.setCurrentIndex(self.model_combo.findData(self.current_model))
        self.model_combo.setToolTip(self.current_config.provider+' / '+self.current_config.model+'\n'+self.current_config.url if self.current_config else '先选择厂商')
        for combo in (self.provider_combo,self.model_combo):combo.blockSignals(False)
        self._update_controls()
        self.experiment_page.model_client = self.service.model_client
        self.model_state.setText('密钥读取失败' if self.model_problem else (f'{self.current_config.provider} · {self.current_config.model}' if self.service.model_client else '本地检索'))
        self._refresh_context_usage()

    @Slot(object)
    def _context_usage(self,usage):
        if self.busy:self.context_ring.set_usage(usage)

    def _refresh_context_usage(self):
        if not hasattr(self,'current_config') or not hasattr(self,'context_ring') or self.busy:return
        selected=[item.data(Qt.ItemDataRole.UserRole) for item in self.document_list.selectedItems()]
        scope=scope_key(selected,self.experiment_page.selected_sample_ids())
        cache_key=(str(self.knowledge.path),self.conversation_id,len(self.conversation),self.conversation[-1].id if self.conversation else None,scope,self.current_model)
        if getattr(self,'_context_preview_key',None)!=cache_key:
            memory=ContextMemory(self.knowledge,self._conversation_context(scope),self.service.model_client,
                conversation_id=self.conversation_id,scope=scope)
            payload={'question':'','current_model':self.current_config.model if self.current_config else '',
                     'file_paths':self._conversation_file_paths(scope),'suggest_title':self.conversation_id is None}
            self._context_preview_base=memory.preview(CHAT_PROMPT,payload)
            self._context_preview_base['capacity']=self.profiles.models.context_window(self.current_model) if self.profiles and self.current_model else None
            self._context_preview_key=cache_key
        usage=dict(self._context_preview_base)
        text=json.dumps(self.question_edit.toPlainText(),ensure_ascii=False)[1:-1]
        usage['used']+=estimate_tokens([{'role':'user','content':text}])-11
        self.context_ring.set_usage(usage)

    def change_model(self, index):
        self.use_model(self.model_combo.itemData(index))

    def change_provider(self,index):
        if self.busy or self.deletion_mode or self.profiles is None:
            self._update_model_state(self.profiles.get(self.profile_id) if self.profiles else None)
            return
        identifier=self.provider_combo.itemData(index)
        if identifier is None:return
        provider=self.profiles.models.get_provider(identifier)
        if self.current_provider and provider.id==self.current_provider.id:
            self.refresh_provider_models()
            return
        try:
            profile=self.profiles.get(self.profile_id)
            self.profiles.update(profile.id,profile.name,profile.description,provider.models[0].id if provider.models else None,provider.id)
            self._reload_service()
            self.status_label.setText('厂商已切换。' if provider.models else '该厂商尚未获取到模型，请在模型设置中重新保存。')
            self.refresh_provider_models()
        except (ValueError,OSError):
            self._update_model_state(self.profiles.get(self.profile_id))
            self.status_label.setText('厂商未切换，请检查方向配置或目录权限。')

    def refresh_provider_models(self):
        if self.busy or self.deletion_mode or not self.allow_model or self.credentials is None or self.current_provider is None:return
        provider=self.current_provider
        try:
            credentials=self.credentials_for(provider.url);key=credentials.get()
            if not key:
                self._reload_service()
                self.status_label.setText('请在模型设置填写此厂商的 API Key，以获取完整模型列表。')
                return
        except CredentialError as exc:
            self._reload_service();self.status_label.setText(str(exc));return
        def received(models):
            try:
                current=self.profiles.models.get_provider(provider.id)
                if current.url!=provider.url or credentials.get()!=key:
                    raise CredentialError('厂商配置或 API Key 已变更，请重新选择厂商。')
                updated=self.profiles.models.save_provider(provider.id,provider.name,provider.url,models)
                profile=self.profiles.get(self.profile_id)
                selected=next((m.id for m in updated.models if m.id==profile.model),updated.models[0].id)
                self.profiles.update(profile.id,profile.name,profile.description,selected,updated.id)
            finally:self._reload_service()
            self.status_label.setText(f'已更新 {len(models)} 个模型。')
        self.start_task(lambda:list_provider_models(key,provider.url),received,'正在更新厂商模型列表…')

    def use_model(self,identifier):
        if self.busy or self.deletion_mode or self.profiles is None or identifier is None:
            self._update_model_state(self.profiles.get(self.profile_id) if self.profiles else None)
            return
        try:
            profile = self.profiles.get(self.profile_id)
            profile = self.profiles.update(profile.id, profile.name, profile.description, identifier)
        except (ValueError, OSError):
            self._update_model_state(self.profiles.get(self.profile_id))
            self.status_label.setText('模型未切换，请检查方向配置或目录权限。')
            return
        self.service, self.model_problem = self._service_for(self.knowledge, profile)
        self._update_model_state(profile)
        self.status_label.setText(self.model_problem or '模型已切换。')

    def _update_controls(self):
        idle = not self.busy
        can_switch = idle and not self.deletion_mode and self.profiles is not None
        for control in (self.profile_combo, self.new_profile_button, self.edit_profile_button):
            control.setEnabled(can_switch)
        self.provider_combo.setEnabled(can_switch and self.provider_combo.count()>0)
        self.model_combo.setEnabled(can_switch and self.model_combo.count()>0)
        self.model_settings_button.setEnabled(can_switch and self.credentials is not None and self.allow_model)
        self.library_panel.setEnabled(idle)
        self.question_edit.setEnabled(not self.deletion_mode)
        self.stop_button.setVisible(self.busy and getattr(self,'_cancel_event',None) is not None)
        self.history_list.setEnabled(idle and not self.deletion_mode)
        self.ask_button.setEnabled(idle and not self.deletion_mode)
        self.new_chat_button.setEnabled(idle and not self.deletion_mode)
        self.import_button.setEnabled(idle and not self.deletion_mode)
        self.export_button.setEnabled(idle and not self.deletion_mode and self.last_answer is not None)
        if hasattr(self, 'experiment_page'):
            self.experiment_page.set_busy(not idle or self.deletion_mode)

    def switch_profile(self, profile_id):
        if self.busy or self.deletion_mode or self.profiles is None:
            return False
        try:
            profile = self.profiles.get(profile_id)
            knowledge = self.profiles.knowledge(profile_id)
            knowledge.list_documents()
            knowledge.list_history()
            service, problem = self._service_for(knowledge, profile)
            experiment_store = ExperimentStore(knowledge)
            experiment_archive = ExperimentArchive(experiment_store)
            for summary in experiment_store.list_experiments():
                experiment_store.get(summary.id)
            self.profiles.select(profile_id)
        except Exception:
            self.profile_combo.setCurrentIndex(self.profile_combo.findData(self.profile_id))
            self.status_label.setText('方向切换未完成，请检查方向配置和数据库；仍保留原方向。')
            return False
        self.profile_id, self.knowledge, self.service = profile_id, knowledge, service
        self.conversations=Conversations(knowledge)
        self.conversation_id=None;self.conversation=[];self._turn_scopes={};self._pending_question=None;self._unavailable_scope=None
        self.model_problem = problem
        self.experiment_page.bind(experiment_store, experiment_archive, service.model_client)
        self.last_answer = None
        self._query_scope_before_delete.clear()
        self.document_list.clear()
        self.question_edit.clear()
        self.answer_view.setHtml(self.welcome_html())
        self.answer_activity.clear()
        self.source_title.setText('等待选择来源')
        self.source_view.clear()
        self.tabs.setCurrentIndex(0)
        self.refresh_documents()
        self.refresh_history()
        self._refresh_profile_combo()
        self._update_model_state(profile)
        self._update_controls()
        self.status_label.setText(f'已切换至「{profile.name}」。' + problem)
        return True

    def new_profile(self):
        if self.busy or self.deletion_mode or self.profiles is None:
            return
        dialog = ProfileDialog(self.profiles, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.switch_profile(dialog.saved_profile.id)

    def edit_profile(self):
        if self.busy or self.deletion_mode or self.profiles is None:
            return
        dialog = ProfileDialog(self.profiles, self.profiles.get(self.profile_id), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._refresh_profile_combo()
            self._reload_service()
            self.status_label.setText('方向设置已保存。')

    def model_settings(self):
        if self.busy or self.deletion_mode or self.profiles is None or not self.allow_model:
            return
        dialog = ModelSettingsDialog(self.profiles, self.profile_id, self.credentials, self)
        dialog.exec()
        self._reload_service()
        dialog.deleteLater()

    def _credentials_changed(self):
        self._reload_service()

    def _settings_busy_changed(self, busy):
        self.busy = busy
        self._update_controls()

    @staticmethod
    def welcome_html():
        return "<p style='color:#748691;'>导入资料或实验记录，开始提问。</p>"

    @staticmethod
    def panel():
        frame = QFrame()
        frame.setObjectName("panel")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(17, 18, 17, 16)
        frame_layout.setSpacing(12)
        return frame, frame_layout

    def _library_panel(self):
        panel, layout = self.panel()
        panel.setMinimumWidth(220)
        layout.addWidget(label("知识库", "sectionTitle"))
        self.import_button = QPushButton("＋  导入资料")
        self.import_button.setObjectName("primary")
        import_menu = QMenu(self.import_button)
        import_menu.addAction("导入文件…", self.choose_files)
        import_menu.addAction("粘贴文本…", self.paste_text)
        import_menu.addAction("载入教学样例", self.load_examples)
        self.import_button.setMenu(import_menu)
        layout.addWidget(self.import_button)
        self.library_stats = label("0 份资料 · 0 个片段", "muted")
        layout.addWidget(self.library_stats)
        self.document_list = QListWidget()
        self.document_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.document_list.setAccessibleName("知识库资料列表")
        self.document_list.itemSelectionChanged.connect(self.update_scope)
        layout.addWidget(self.document_list, 1)
        self.scope_label = label("检索范围：全部资料", "muted", True)
        layout.addWidget(self.scope_label)
        row = QHBoxLayout()
        self.clear_selection_button = button("恢复全库", self.clear_scope, "ghost")
        row.addWidget(self.clear_selection_button)
        self.delete_button = button("删除所选", self.delete_selected, "ghost")
        row.addWidget(self.delete_button)
        layout.addLayout(row)
        return panel

    def _question_panel(self):
        panel, layout = self.panel()
        heading=QHBoxLayout();heading.addWidget(label("科研助手", "sectionTitle"));heading.addStretch()
        self.evidence_button=button('出处',self.toggle_evidence,'ghost');heading.addWidget(self.evidence_button)
        self.new_chat_button=button('新对话',self.new_conversation,'ghost');heading.addWidget(self.new_chat_button);layout.addLayout(heading)
        self.tabs = QTabWidget()
        self.answer_view = QTextBrowser()
        self.answer_view.setDocument(ChatDocument(self.answer_view))
        self.answer_view.setAccessibleName("检索结果与回答")
        self.answer_view.setOpenLinks(False)
        self.answer_view.setOpenExternalLinks(False)
        self.answer_view.anchorClicked.connect(self.open_citation)
        self.answer_view.setHtml(self.welcome_html())
        self.answer_activity=TaskActivity()
        current=QWidget();current_layout=QVBoxLayout(current);current_layout.setContentsMargins(0,0,0,0)
        current_layout.addWidget(self.answer_activity);current_layout.addWidget(self.answer_view,1)
        self.history_list = QListWidget()
        self.history_list.setAccessibleName("历史对话")
        self.history_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history_list.customContextMenuRequested.connect(self.conversation_menu)
        self.history_list.itemDoubleClicked.connect(self.open_history)
        self.tabs.addTab(current, "对话")
        self.tabs.addTab(self.history_list, "历史对话")
        layout.addWidget(self.tabs, 1)
        row = QHBoxLayout()
        questions = [("关键条件", "当前范围报告了哪些关键制备条件？"), ("样品对比", "所选样品的制备条件和实验结果有哪些异同？请引用来源。"), ("证据核对", "当前范围中哪些结论有直接证据支持？")]
        for text, question in questions:
            row.addWidget(button(text, lambda checked=False, value=question: self.question_edit.setPlainText(value), "chip"))
        row.addStretch()
        layout.addLayout(row)
        self.context_label = label('资料：全部 · 样品：全部', 'muted', True)
        layout.addWidget(self.context_label)
        self.question_edit = QuestionEdit()
        self.context_ring=self.question_edit.context_ring
        self.question_edit.textChanged.connect(self._refresh_context_usage)
        self.question_edit.sendRequested.connect(self.ask)
        self.question_edit.setAccessibleName("问题输入")
        self.question_edit.setPlaceholderText("输入问题…（Enter 发送，Shift+Enter 换行）")
        self.question_edit.setMaximumHeight(91)
        layout.addWidget(self.question_edit)
        row = QHBoxLayout()
        row.addWidget(label('厂商', 'muted'))
        self.provider_combo=QComboBox()
        self.provider_combo.setAccessibleName('问答厂商')
        self.provider_combo.setPlaceholderText('选择厂商')
        self.provider_combo.setMinimumContentsLength(8)
        self.provider_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.provider_combo.activated.connect(self.change_provider)
        row.addWidget(self.provider_combo,1)
        row.addWidget(label('模型', 'muted'))
        self.model_combo = QComboBox()
        self.model_combo.setPlaceholderText('选择模型')
        self.model_combo.setAccessibleName('问答模型')
        self.model_combo.setMinimumWidth(150)
        self.model_combo.setMinimumContentsLength(12)
        self.model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model_combo.activated.connect(self.change_model)
        row.addWidget(self.model_combo)
        self.export_button = button("导出回答", self.export_answer, "ghost")
        self.export_button.setEnabled(False)
        row.addWidget(self.export_button)
        row.addStretch()
        self.ask_button = button("提问  →", self.ask, "primary")
        self.ask_button.setToolTip('Enter 发送，Shift+Enter 换行')
        self.stop_button=button('停止',self.stop_task,'ghost');self.stop_button.hide();row.addWidget(self.stop_button)
        row.addWidget(self.ask_button)
        layout.addLayout(row)
        return panel

    def _evidence_panel(self):
        panel, layout = self.panel()
        panel.setMinimumWidth(210)
        layout.addWidget(label("证据与出处", "sectionTitle"))
        self.source_title = label("等待选择来源", "sourceTitle", True)
        layout.addWidget(self.source_title)
        self.source_view = QTextBrowser()
        self.source_view.setAccessibleName("来源原文")
        self.source_view.setOpenLinks(False)
        self.source_view.setOpenExternalLinks(False)
        self.source_view.setPlainText("选择来源以查看原文。")
        layout.addWidget(self.source_view, 1)
        return panel

    def toggle_evidence(self):
        self.evidence_panel.setVisible(not self.evidence_panel.isVisible())

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'evidence_panel'):
            compact=self.width()<1180
            if compact!=getattr(self,'_compact_evidence',None):self.evidence_panel.setVisible(not compact)
            self._compact_evidence=compact

    def refresh_documents(self):
        self._context_preview_key=None
        selected = {item.data(Qt.ItemDataRole.UserRole) for item in self.document_list.selectedItems()}
        self.document_list.clear()
        documents = sorted(self.knowledge.list_documents(),key=lambda d:(d.group,{'正文':0,'方法摘录':1,'补充材料':2}.get(d.role,0)))
        for document in documents:
            category=(document.category+' · AI 候选 · ') if document.category else ''
            title=(document.group+' · ' if document.group else '')+document.filename
            item = QListWidgetItem(f"{title}\n{document.role+' · ' if document.role else ''}{category}{document.chunk_count} 个片段")
            item.setData(Qt.ItemDataRole.UserRole, document.id)
            item.setToolTip(html.escape(document.filename)+('<br>AI 候选摘要：'+html.escape(document.summary)+'<br>标签：'+html.escape('、'.join(document.tags)) if document.summary else ''))
            self.document_list.addItem(item)
            item.setSelected(document.id in selected)
        groups={d.group for d in documents if d.group}
        self.library_stats.setText(f"{len(documents)} 份资料"+(f" · {len(groups)} 个资料组" if groups else f" · {sum(d.chunk_count for d in documents)} 个片段"))
        self.update_scope()
        self._update_controls()

    def refresh_history(self):
        self.conversations.sync_legacy()
        self.history_list.clear()
        for conversation in self.conversations.list():
            item = QListWidgetItem(conversation['title'])
            item.setData(Qt.ItemDataRole.UserRole, conversation['id'])
            item.setToolTip(conversation['title'])
            self.history_list.addItem(item)

    def conversation_menu(self,position):
        if self.busy or self.deletion_mode:return
        entry=self.history_list.itemAt(position)
        if entry is None:return
        self.history_list.setCurrentItem(entry)
        identifier=entry.data(Qt.ItemDataRole.UserRole)
        menu=QMenu(self.history_list)
        menu.addAction('重命名',lambda:self.rename_conversation(identifier))
        menu.exec(self.history_list.viewport().mapToGlobal(position))
        menu.deleteLater()

    def rename_conversation(self,identifier):
        if self.busy or self.deletion_mode:return
        try:
            current=self.conversations.title_state(identifier)['title']
            title,accepted=QInputDialog.getText(self,'重命名对话','对话标题',QLineEdit.EchoMode.Normal,current)
            if not accepted:return
            self.conversations.set_title(identifier,title,manual=True)
            self.refresh_history()
        except ValueError as exc:self.status_label.setText(str(exc))
        except (OSError,sqlite3.Error):self.status_label.setText('标题未保存，请稍后重试。')

    def new_conversation(self,checked=False):
        if self.busy or self.deletion_mode:return
        self.conversation_id=None;self.conversation=[];self._turn_scopes={};self._pending_question=None;self._unavailable_scope=None
        self.last_answer=None;self.question_edit.clear();self.answer_view.setHtml(self.welcome_html())
        self.answer_activity.clear();self.source_title.setText('等待选择来源');self.source_view.clear()
        self.tabs.setCurrentIndex(0);self._update_controls();self.question_edit.setFocus()
        self._refresh_context_usage()

    def _conversation_context(self,scope):
        turns=[]
        for answer in self.conversation:
            saved_scope=self._turn_scopes.get(answer.id)
            valid_sources=not answer.unavailable_sources and len(self.knowledge.get_evidence_many([s.id for s in answer.sources]))==len(answer.sources)
            # Keep the user's request after a partial task; failed model text is never evidence or authorization.
            text=answer.answer if valid_sources and answer.status in ('chat','generated') else ''
            if text and saved_scope!=scope and answer.status!='chat':
                text='【历史对话，读取范围已改变；仅用于理解指代，当前事实须重新检索】\n'+text
            turns.append({'question':answer.question,'answer':text, 'mode':'chat' if answer.status=='chat' else 'research'})
        return turns

    def _conversation_file_paths(self,scope):
        from matknow.local_files import explicit_paths
        paths=[]
        for answer in reversed(self.conversation):
            if self._turn_scopes.get(answer.id)!=scope:break
            paths.extend(explicit_paths(answer.question))
        return list(dict.fromkeys(paths))[:10]

    @staticmethod
    def _question_html(question):
        return "<p align='right' style='color:#203b43;font-weight:600;margin-top:20px;'>"+html.escape(question).replace('\n','<br>')+'</p>'

    def _render_conversation(self):
        bar=self.answer_view.verticalScrollBar();position=bar.value()
        follow=position>=bar.maximum()-24
        parts=[]
        for answer in self.conversation:
            parts.append(f'<a name="answer-{html.escape(answer.id,quote=True)}"></a>')
            parts.append(self._question_html(answer.question))
            sources={s.citation_number or i:s for i,s in enumerate(answer.sources,1)}
            def citation(match):
                number=int(match[1]);source=sources.get(number)
                return f'[{number}](source:{source.id})' if source else match[0]
            text=re.sub(r'\[(\d+)\]',citation,answer.answer)
            lines=[];fenced=False
            for line in text.splitlines():
                delimiter=line.lstrip().startswith(('```','~~~'))
                lines.append(line+('  ' if line and not fenced and not delimiter else ''))
                if delimiter:fenced=not fenced
            text='\n'.join(lines)
            document=ChatDocument();font=self.answer_view.font();font.setWeight(QFont.Weight.DemiBold);document.setDefaultFont(font)
            document.setMarkdown(text,QTextDocument.MarkdownFeature.MarkdownDialectGitHub|QTextDocument.MarkdownFeature.MarkdownNoHTML)
            rendered=document.toHtml();match=re.search(r'<body[^>]*>(.*)</body>',rendered,re.S)
            body=match[1] if match else html.escape(answer.answer).replace('\n','<br>')
            body=body.replace('<p style="','<p style="font-weight:600;')
            parts.append(f"<div style='color:#445967;font-weight:600;'>{body}</div>")
            if answer.agent_run:parts.append(f"<p style='color:#72818f;font-size:12px;'>{html.escape(execution_summary(answer))}</p>")
            if answer.unavailable_sources:parts.append(f'<p>{answer.unavailable_sources} 个来源已删除，请重新核对回答。</p>')
            links=[f"<a href='source:{html.escape(source.id,quote=True)}'>[{number}] {html.escape(source.filename)}</a>" for number,source in list(sources.items())[:3]]
            if links:parts.append('<p style="font-size:12px;">来源：'+' · '.join(links)+'</p>')
            for draft_id in (answer.agent_run or {}).get('draft_ids',[]):
                parts.append(f'<p><a href="draft:{html.escape(draft_id,quote=True)}">核对实验草稿并保存</a></p>')
            for index,report in enumerate((answer.agent_run or {}).get('reports',[])):
                title=html.escape(report['report']['title'])
                parts.append(f'<p><a href="report:{answer.id}:{index}">预览并导出报告：{title}</a></p>')
        if self._pending_question is not None:parts.append(self._question_html(self._pending_question))
        self.answer_view.setHtml(''.join(parts) or self.welcome_html())
        if self._pending_question is not None:bar.setValue(bar.maximum())
        elif follow and self.conversation:self.answer_view.scrollToAnchor('answer-'+self.conversation[-1].id)
        else:bar.setValue(position)

    def update_scope(self):
        count = len(self.document_list.selectedItems())
        if self.deletion_mode:
            for index in range(self.document_list.count()):
                item = self.document_list.item(index)
                # Selection owns the checkbox state so row/checkbox clicks toggle only once.
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if item.isSelected() else Qt.CheckState.Unchecked)
            self.scope_label.setText(f"已选择 {count} 份待删除资料")
            self.delete_button.setText(f"确认删除（{count}）")
            return
        self.scope_label.setText(f"检索范围：所选 {count} 份资料" if count else "检索范围：全部资料")
        self.update_context()

    def update_context(self):
        if not hasattr(self, 'context_label'):return
        documents = len(self.document_list.selectedItems())
        samples = len(self.experiment_page.selected_sample_ids())
        self.context_label.setText(f'资料：{documents or "全部"} · 样品：{samples or "全部"}')
        self._refresh_context_usage()

    def set_deletion_mode(self, enabled: bool):
        if enabled:
            self._query_scope_before_delete = {item.data(Qt.ItemDataRole.UserRole) for item in self.document_list.selectedItems()}
        self.deletion_mode = enabled
        self.document_list.blockSignals(True)
        self.document_list.clearSelection()
        for index in range(self.document_list.count()):
            item = self.document_list.item(index)
            if not enabled:
                item.setData(Qt.ItemDataRole.CheckStateRole, None)
                item.setSelected(item.data(Qt.ItemDataRole.UserRole) in self._query_scope_before_delete)
        self.document_list.blockSignals(False)
        self.clear_selection_button.setText("取消删除" if enabled else "恢复全库")
        self.delete_button.setText("删除所选")
        self.import_button.setEnabled(not enabled)
        self.ask_button.setEnabled(not enabled)
        self.update_scope()
        self._update_controls()

    def clear_scope(self):
        if self.deletion_mode:
            self.set_deletion_mode(False)
            self.status_label.setText("已取消删除。")
        else:
            self.document_list.clearSelection()

    def start_task(self, function, on_success, message: str):
        if self.busy:
            return
        self.busy = True
        from threading import Event
        client=self.service.model_client
        self._cancel_event=Event() if client is not None else None
        self._task_client=client
        if client is not None:client.cancel_event=self._cancel_event
        self.stop_button.setEnabled(True);self.stop_button.setText('停止')
        self.status_label.setText(message)
        self._update_controls()
        self.taskBusyChanged.emit(True)
        self.task = Task(function)
        self._task_callback = on_success
        self.task.signals.succeeded.connect(self._task_succeeded)
        self.task.signals.failed.connect(self._task_failed)
        self.task.signals.finished.connect(self._task_finished)
        self.pool.start(self.task)

    def stop_task(self):
        if self.busy and getattr(self,'_cancel_event',None) is not None:
            self._cancel_event.set();self.stop_button.setEnabled(False);self.stop_button.setText('正在停止')
            self.status_label.setText('正在结束当前步骤，随后停止；已完成的内容保留。')

    def _task_succeeded(self, result):
        try:
            self._task_callback(result)
        except Exception as exc:
            self._task_failed(str(exc))

    def _task_failed(self, message: str):
        self._context_preview_key=None
        self.status_label.setText("操作未完成：" + message)
        if self.answer_activity.running:
            self.answer_activity.update_stage('未完成：'+message)
            self.answer_activity.finish('未完成')
            self.answer_view.append('<p>'+html.escape(message)+'</p>')
            self._pending_question=None
        self.experiment_page.task_failed(message)

    def _task_finished(self):
        self.answer_activity.finish('已结束')
        self.busy = False
        client=getattr(self,'_task_client',None)
        if client is not None and getattr(client,'cancel_event',None) is self._cancel_event:
            client.cancel_event=None
        self._task_client=None
        self._cancel_event=None
        if getattr(self,'_context_preview_key',None) is None or self.context_ring.usage.get('phase')!='request':self._refresh_context_usage()
        self._update_controls()
        self.taskBusyChanged.emit(False)

    @Slot(str)
    def _agent_progress(self,message):
        if self.busy and self.answer_activity.running:
            self.status_label.setText(message)
            self.answer_activity.update_stage(message)

    def ask(self, checked=False, *, question=None, comparison=None):
        if self.busy or self.deletion_mode:
            return
        question = self.question_edit.toPlainText() if question is None else question
        if not question.strip() or len(question) > 2000:
            self.status_label.setText("请输入问题，长度不要超过 2000 字符。")
            return
        selected = [item.data(Qt.ItemDataRole.UserRole) for item in self.document_list.selectedItems()]
        samples = self.experiment_page.selected_sample_ids()
        scope=scope_key(selected,comparison.sample_ids if comparison is not None else samples)
        if self._unavailable_scope==scope:
            self.status_label.setText('原对话所选资料或样品已删除，请重新选择范围或新建对话。');return
        self._unavailable_scope=None
        self._reload_service()
        service = self.service
        conversation=self._conversation_context(scope)
        proposed_titles=[]
        suggest_title=self.conversation_id is None or self.conversations.title_state(self.conversation_id)['source']=='fallback'
        if comparison is not None:
            from matknow.experiments.service import ExperimentAskService
            service = ExperimentAskService(self.experiment_page.store, service.model_client, service.description)
            function = lambda: service.ask(question, comparison.sample_ids, comparison,conversation=conversation,conversation_id=identifier,conversation_scope=scope,progress=self.agentProgress.emit)
        else:
            file_paths=self._conversation_file_paths(scope)
            function = lambda: service.respond(question, selected or None, sample_ids=samples or None, progress=self.agentProgress.emit,conversation=conversation,title_callback=proposed_titles.append if suggest_title else None,user_file_paths=file_paths,conversation_id=identifier,conversation_scope=scope)
        execute=function;repository=self.conversations;identifier=self.conversation_id
        def function():
            answer=execute()
            imported=(answer.agent_run or {}).get('imported_document_ids',[])
            saved_scope=scope_key([*selected,*imported],comparison.sample_ids if comparison is not None else samples) if selected and imported else scope
            saved_id=repository.append(answer,identifier,saved_scope)
            if proposed_titles and answer.status in ('chat','generated','insufficient_evidence'):
                try:repository.set_title(saved_id,proposed_titles[-1])
                except (ValueError,OSError,sqlite3.Error):pass
            return answer
        message="正在等待模型响应…" if service.model_client else "正在检索资料和实验记录…"
        self.last_answer=None;self.source_view.clear();self.source_title.setText('等待来源')
        self._pending_question=question.strip();self._render_conversation()
        self.tabs.setCurrentIndex(0);self.answer_activity.start(message)
        self.start_task(function, self.present_answer, message)
        self.question_edit.clear()

    def present_answer(self, answer: Answer):
        resolved = self.knowledge.get_evidence_many([source.id for source in answer.sources])
        live_sources = [replace(live, citation_number=source.citation_number or number) for number, source in enumerate(answer.sources, 1) if (live := resolved.get(source.id)) is not None]
        answer = replace(answer, sources=live_sources, unavailable_sources=answer.unavailable_sources + len(answer.sources) - len(live_sources))
        if not self.answer_activity.running and (self.last_answer is None or self.last_answer.id!=answer.id):
            self.answer_activity.clear()
        self.last_answer = answer
        imported=(answer.agent_run or {}).get('imported_document_ids',[])
        if imported:
            selected={entry.data(Qt.ItemDataRole.UserRole) for entry in self.document_list.selectedItems()}
            self.refresh_documents()
            if selected:
                for index in range(self.document_list.count()):
                    entry=self.document_list.item(index)
                    entry.setSelected(entry.data(Qt.ItemDataRole.UserRole) in selected.union(imported))
        self.conversations.sync_legacy()
        self.conversation_id=self.conversations.containing(answer.id)
        if self.conversation_id:
            self.conversation,self._turn_scopes=self.conversations.load(self.conversation_id)
            self.conversation=[answer if turn.id==answer.id else turn for turn in self.conversation]
        else:self.conversation=[answer]
        self._pending_question=None;self._render_conversation()
        self.tabs.setCurrentIndex(0)
        self.export_button.setEnabled(not self.busy)
        self.refresh_history()
        if answer.sources:
            self.show_source(answer.sources[0].id, navigate=False)
        else:
            self.source_title.setText("当前没有可查看的来源")
            self.source_view.clear()
        mode = {'chat': '普通聊天', 'generated': '模型回答', 'model_error': '模型调用失败', 'insufficient_evidence': '证据不足', 'retrieval_preview': '检索预览'}.get(answer.status, answer.status)
        detail = f' · {len(answer.sources)} 个来源片段' if answer.status != 'chat' and answer.sources else ''
        self.status_label.setText(mode + detail + (' · ' + self.model_problem if self.model_problem else ''))
        outcome={'model_error':'未完成','insufficient_evidence':'待补充资料','retrieval_preview':'检索完成'}.get(answer.status,'已完成')
        if answer.agent_run and answer.agent_run['outcome']=='needs_input':outcome='待处理'
        self.answer_activity.finish(outcome)
        if not self.busy:self._refresh_context_usage()

    def open_citation(self, url):
        if url.scheme() == "source":
            self.show_source(url.toString().split(":", 1)[1])
        elif url.scheme()=='draft':self.open_experiment_draft(url.toString().split(':',1)[1])
        elif url.scheme()=='report':
            try:
                _,identifier,index=url.toString().split(':');self.open_report(identifier,int(index))
            except (ValueError,IndexError):self.status_label.setText('报告入口无效。')

    def open_report(self,answer_id,index):
        if self.busy:return
        try:
            answer=next(a for a in self.conversation if a.id==answer_id)
            reports=(answer.agent_run or {}).get('reports',[])
            if not 0<=index<len(reports):raise ValueError('报告入口无效。')
            payload=reports[index]
            self.experiment_page.store.get(payload['experiment_id'],payload['revision'])
            from .experiments.report import restore_report
            from .experiments.report_dialog import ReportDialog
            report=restore_report(payload['report'],self.knowledge.path)
            dialog=ReportDialog(report,self,model_client=self.service.model_client,archive=self.experiment_page.archive)
            dialog.exec();dialog.deleteLater()
        except (StopIteration,ValueError,KeyError):self.status_label.setText('报告对应的实验已删除或记录无效，请重新生成。')

    def open_experiment_draft(self,identifier):
        if self.busy or self.deletion_mode:return
        if identifier not in {draft_id for a in self.conversation for draft_id in (a.agent_run or {}).get('draft_ids',[])}:
            self.status_label.setText('只能打开当前对话中的实验草稿。');return
        try:
            from matknow.experiments.models import draft_from_dict
            from matknow.experiments.readers import ReadDocument,Block
            from matknow.experiments.import_dialog import RecordImportDialog
            import tempfile,hashlib
            payload=self.knowledge.experiment_draft(identifier)
            sources=self.knowledge.document_sources(payload['document_id'])
            selected_sources=payload.get('source_ids',[s.id for s in sources])
            if not set(selected_sources).issubset({s.id for s in sources}):raise ValueError('草稿来源已变化，请重新生成。')
            sources=[s for s in sources if s.id in selected_sources]
            store=self.experiment_page.store
            if payload['target_id'] is not None and store.get(payload['target_id']).revision!=payload['target_revision']:
                raise ValueError('目标实验已更新，请重新生成草稿，避免覆盖新记录。')
            candidates=[]
            for target in payload['allowed_targets']:
                try:candidates.append(store.get(target))
                except ValueError:pass
            data=payload['source_text'].encode('utf8')
            document=ReadDocument(payload['document_id'],payload['filename'],hashlib.sha256(data).hexdigest(),[Block(s.text,s.locator) for s in sources],[],['来源文件保存为文字快照，图片和未解析附件未转入实验。'])
            temporary=tempfile.TemporaryDirectory(prefix='.draft-',dir=self.knowledge.path.parent)
            try:
                path=Path(temporary.name)/payload['filename'];path.write_bytes(data)
                dialog=RecordImportDialog(document,parent=self,archive=self.experiment_page.archive,prepared=draft_from_dict(payload['draft']),candidates=candidates)
            except Exception:
                temporary.cleanup();raise
            dialog.source_path=path;dialog.draft_id=identifier
            dialog.destroyed.connect(lambda:temporary.cleanup())
            dialog.target.setCurrentIndex(max(0,dialog.target.findData(payload['target_id'])))
            self.experiment_page.submit_dialog(dialog)
        except (ValueError,OSError,sqlite3.Error) as exc:self.status_label.setText(str(exc))

    def show_source(self, source_id: str, *, navigate=True):
        if source_id not in {source.id for answer in self.conversation for source in answer.sources}:
            return
        evidence = self.knowledge.get_evidence(source_id)
        if navigate:self.evidence_panel.show()
        if evidence is None:
            self.source_title.setText("来源已删除")
            self.source_view.clear()
            return
        self.source_title.setText(f"{evidence.filename}\n{evidence.locator}")
        self.source_view.setPlainText(evidence.text)
        if source_id.startswith('exp:') and navigate:
            self.workspaces.setCurrentIndex(1)
            self.experiment_page.show_evidence(source_id)

    def open_history(self, item):
        if self.busy or self.deletion_mode:return
        try:
            turns,scopes=self.conversations.load(item.data(Qt.ItemDataRole.UserRole))
            saved=scopes.get(turns[-1].id)
            self._unavailable_scope=None
            if saved is not None:
                selection=json.loads(saved)
                if not isinstance(selection,dict) or set(selection)!={'documents','samples'} or any(value is not None and (not isinstance(value,list) or any(not isinstance(token,str) for token in value)) for value in selection.values()):raise ValueError('对话范围记录无效。')
                for index in range(self.document_list.count()):
                    entry=self.document_list.item(index);entry.setSelected(entry.data(Qt.ItemDataRole.UserRole) in (selection['documents'] or []))
                self.experiment_page.selection=set(selection['samples'] or []);self.experiment_page.refresh()
                documents=[entry.data(Qt.ItemDataRole.UserRole) for entry in self.document_list.selectedItems()]
                samples=self.experiment_page.selected_sample_ids()
                if (selection['documents'] and not documents) or (selection['samples'] and not samples):
                    self._unavailable_scope=scope_key(documents,samples)
        except ValueError as exc:self.status_label.setText(str(exc));return
        self.question_edit.clear();self.present_answer(turns[-1])

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "导入知识库资料", "", "支持的资料 (*.pdf *.docx *.xlsx *.txt *.md *.markdown *.csv *.tsv)")
        if paths:
            self.import_paths([Path(path) for path in paths])

    def import_paths(self, paths: list[Path]):
        knowledge,client = self.knowledge,self.service.model_client
        def import_all():
            from matknow.tasks import TaskCancelled,check_cancelled
            results, failures = [], []
            for path in paths:
                try:
                    check_cancelled(client)
                    document=knowledge.import_file(path);results.append(document)
                    if client is not None:
                        from matknow.library_agent import organize_document
                        from matknow.llm import ModelError
                        try:organize_document(knowledge,document.id,client)
                        except (ModelError,ValueError,OSError):failures.append(f'{path.name}：原文已导入，AI 整理未完成。')
                except TaskCancelled:
                    failures.append('已停止后续处理；已导入的原文保留，未完成整理的资料可稍后重试。')
                    break
                except (ValueError, OSError) as exc:
                    failures.append(f"{path.name}：{exc}")
            return results, failures

        def imported(result):
            documents, failures = result
            self.refresh_documents()
            message = f"已处理 {len(documents)} 份资料。"
            if failures:
                message += " 需处理：" + "；".join(failures[:2])
            self.status_label.setText(message)

        self.start_task(import_all, imported, "资料库智能体正在导入并整理…" if client else "正在解析并导入资料…")

    def delete_selected(self):
        if self.busy:
            return
        if not self.deletion_mode:
            if not self.document_list.count():
                self.status_label.setText("知识库暂无可删除的资料。")
                return
            self.set_deletion_mode(True)
            self.status_label.setText("选择要删除的资料。")
            return
        selected = self.document_list.selectedItems()
        if not selected:
            self.status_label.setText("请先勾选要删除的资料。")
            return
        ids = [item.data(Qt.ItemDataRole.UserRole) for item in selected]
        reply = QMessageBox.question(self, "删除资料", f"删除所选 {len(ids)} 份资料？相关历史引用将失效。", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        for document_id in ids:
            self.knowledge.delete_document(document_id)
        self.refresh_documents()
        self.set_deletion_mode(False)
        self.refresh_history()
        if self.last_answer:
            self.present_answer(self.last_answer)
        self.status_label.setText(f"已删除 {len(ids)} 份资料。")

    def load_examples(self):
        examples = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "examples"
        paths = sorted(examples.glob("*.md"))
        if not paths:
            self.status_label.setText("未找到教学样例，请检查程序资源。")
            return
        self.import_paths(paths)

    def paste_text(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("导入文本资料")
        dialog.resize(600, 440)
        layout = QVBoxLayout(dialog)
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("资料名称")
        text_edit = QPlainTextEdit()
        text_edit.setPlaceholderText("粘贴资料正文…")
        layout.addWidget(name_edit)
        layout.addWidget(text_edit, 1)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        controls.button(QDialogButtonBox.StandardButton.Ok).setText("导入")
        controls.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        controls.accepted.connect(dialog.accept)
        controls.rejected.connect(dialog.reject)
        layout.addWidget(controls)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, text = name_edit.text().strip(), text_edit.toPlainText()
        if not name or not text.strip():
            self.status_label.setText("资料名称和正文都不能为空。")
            return

        def imported(result):
            document,warning=result
            self.refresh_documents()
            self.status_label.setText(f"已导入：{document.filename}"+warning)

        knowledge,client = self.knowledge,self.service.model_client
        def import_text():
            document=knowledge.import_text(name,text);warning=''
            if client is not None:
                from matknow.library_agent import organize_document
                from matknow.llm import ModelError
                try:organize_document(knowledge,document.id,client)
                except (ModelError,ValueError,OSError):warning='；原文已保存，AI 整理未完成。'
            return document,warning
        self.start_task(import_text, imported, "资料库智能体正在整理文本…" if client else "正在导入文本…")

    def export_answer(self):
        if not self.last_answer:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出问答记录", "材知-检索记录.md", "Markdown (*.md)")
        if not path:
            return
        answer = self.last_answer
        profile_name = self.profiles.get(self.profile_id).name if self.profiles else '材料制备'
        try:
            from matknow.experiments.export import export_answer
            export_answer(answer, self.knowledge, path, profile_name=profile_name)
        except (OSError, ValueError):
            self.status_label.setText("导出失败，请选择数据存储目录之外的可写位置。")
            return
        self.status_label.setText("记录已导出。")

    def closeEvent(self, event: QCloseEvent):
        if self.busy:
            self.status_label.setText("请等待当前任务结束后关闭窗口。")
            event.ignore()
        else:
            event.accept()
