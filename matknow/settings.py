import sqlite3

from PySide6.QtCore import Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QVBoxLayout,
)

from matknow.credentials import CredentialError, validate_key
from matknow.llm import list_provider_models
from matknow.model_config import completion_url, provider_name_from_url
from matknow.tasks import Task


def plain_label(text):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class ProfileDialog(QDialog):
    def __init__(self, store, profile=None, parent=None):
        super().__init__(parent)
        self.store, self.profile = store, profile
        self.saved_profile = None
        self.setWindowTitle('方向设置' if profile else '新建研究方向')
        self.resize(480, 280)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(profile.name if profile else '')
        self.name_edit.setMaxLength(60)
        self.name_edit.setPlaceholderText('输入方向名称')
        self.description_edit = QPlainTextEdit(profile.description if profile else '')
        self.description_edit.setPlaceholderText('研究主题或回答侧重点（可选）')
        form.addRow('方向名称', self.name_edit)
        form.addRow('方向说明', self.description_edit)
        layout.addLayout(form)
        self.status_label = plain_label('')
        layout.addWidget(self.status_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        try:
            selected=self.store.get(self.profile.id if self.profile else self.store.current_id).model
            values = (self.name_edit.text(), self.description_edit.toPlainText(), selected)
            current=self.store.get(self.profile.id if self.profile else self.store.current_id)
            self.saved_profile = self.store.update(self.profile.id, *values,provider_id=current.provider) if self.profile else self.store.create(*values,provider_id=current.provider)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status_label.setText(str(exc) if isinstance(exc, ValueError) else '方向设置未保存，请检查数据目录是否可写。')
            return
        self.accept()


class ModelSettingsDialog(QDialog):
    SAVED_KEY_MASK='•'*32
    busyChanged=Signal(bool)
    credentialsChanged=Signal()

    def __init__(self,store,profile_id,credentials,parent=None):
        super().__init__(parent)
        self.store,self.profile_id,self.credentials=store,profile_id,credentials
        self.catalog=store.models;self.busy=False;self.pool=QThreadPool(self);self.pool.setMaxThreadCount(1)
        self._key_endpoint=None;self._auto_provider_name='';self._name_manual=False
        self.controller=parent if hasattr(parent,'_credentials_changed') else None
        providers=self.catalog.list_providers();selected=store.get(profile_id).provider
        self.editing_id=selected or (providers[0].id if providers else None)
        self.setWindowTitle('模型设置');self.resize(720,360)
        layout=QVBoxLayout(self);body=QHBoxLayout();left=QVBoxLayout();right=QVBoxLayout()
        left.addWidget(plain_label('厂商'))
        self.provider_list=QListWidget();self.provider_list.setAccessibleName('已配置厂商')
        self.provider_list.setFixedWidth(190);left.addWidget(self.provider_list,1)
        self.new_button=QPushButton('＋ 添加厂商');self.new_button.clicked.connect(self.new_provider)
        self.remove_button=QPushButton('删除厂商');self.remove_button.clicked.connect(self.delete_provider)
        left.addWidget(self.new_button);left.addWidget(self.remove_button)
        self.provider_name_label=plain_label('新厂商');self.provider_name_label.setStyleSheet('font-weight: 600;')
        right.addWidget(self.provider_name_label)
        form=QFormLayout()
        self.provider_edit=QLineEdit();self.provider_edit.setMaxLength(60);self.provider_edit.setPlaceholderText('厂商名称')
        self.provider_edit.setAccessibleName('厂商名称')
        self.url_edit=QLineEdit();self.url_edit.setMaxLength(2000);self.url_edit.setPlaceholderText('https://api.example.com/v1')
        self.url_edit.setAccessibleName('厂商 API URL')
        self.key_edit=QLineEdit();self.key_edit.setMaxLength(2560+len(self.SAVED_KEY_MASK)+1);self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText('输入 API Key（留空保留）');self.key_edit.setAccessibleName('厂商 API Key')
        form.addRow('名称',self.provider_edit);form.addRow('API URL',self.url_edit);form.addRow('API Key',self.key_edit)
        self.test_button=QPushButton('测试连接');self.test_button.clicked.connect(self.test_connection)
        test_row=QHBoxLayout();test_row.addWidget(self.test_button);test_row.addStretch();form.addRow('',test_row)
        right.addLayout(form);self.status_label=plain_label('');right.addWidget(self.status_label);right.addStretch()
        body.addLayout(left);body.addSpacing(16);body.addLayout(right,1);layout.addLayout(body,1)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Close)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存')
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
        self.buttons.accepted.connect(self.save);self.buttons.rejected.connect(self.reject);layout.addWidget(self.buttons)
        self.provider_list.currentRowChanged.connect(self.select_entry)
        self.url_edit.textChanged.connect(self._url_changed)
        self.provider_edit.textEdited.connect(self._name_edited)
        self.key_edit.textEdited.connect(self._key_edited)
        if self.controller:
            self.controller.taskBusyChanged.connect(self._set_busy)
            self.busyChanged.connect(self.controller._settings_busy_changed)
            self.credentialsChanged.connect(self.controller._credentials_changed)
        self.refresh_entries();self.load_entry()

    def refresh_entries(self):
        blocked=self.provider_list.blockSignals(True)
        try:
            self.provider_list.clear();providers=self.catalog.list_providers()
            for provider in providers:
                title=provider.name
                if sum(p.name==provider.name for p in providers)>1:title+='\n'+provider.url.removesuffix('/chat/completions')
                item=QListWidgetItem(title);item.setData(Qt.ItemDataRole.UserRole,provider.id);item.setToolTip(provider.url)
                self.provider_list.addItem(item)
                if provider.id==self.editing_id:self.provider_list.setCurrentItem(item)
        finally:self.provider_list.blockSignals(blocked)
        self.remove_button.setEnabled(bool(self.editing_id and not self.busy))

    def load_entry(self):
        if self.editing_id is None:self.new_provider();return
        provider=self.catalog.get_provider(self.editing_id)
        self._name_manual=True;self._auto_provider_name=''
        self.provider_name_label.setText(provider.name);self.provider_edit.setText(provider.name)
        self.url_edit.setText(provider.url.removesuffix('/chat/completions'));self.status_label.clear()
        self._show_saved_key(provider.url)

    def select_entry(self,index):
        if self.busy or index<0:return
        self.editing_id=self.provider_list.item(index).data(Qt.ItemDataRole.UserRole);self.load_entry()

    def new_provider(self):
        if self.busy:return
        self.editing_id=None;self.provider_list.setCurrentRow(-1);self.provider_name_label.setText('新厂商')
        self._key_endpoint=None;self._auto_provider_name='';self._name_manual=False
        for widget in (self.provider_edit,self.url_edit,self.key_edit):widget.clear()
        self.remove_button.setEnabled(False);self.status_label.clear();self.provider_edit.setFocus()

    def credentials_for(self,url):
        return self.credentials.for_endpoint(url) if hasattr(self.credentials,'for_endpoint') else self.credentials

    def _name_edited(self,_):
        self._name_manual=True

    def _key_edited(self,text):
        if self._key_endpoint is not None:
            # The saved marker is display text, never part of a replacement credential.
            self._key_endpoint=None
            self.key_edit.setText(text.replace(self.SAVED_KEY_MASK[0],''))

    def _url_changed(self,text):
        try:endpoint=completion_url(text)
        except ValueError:endpoint=None
        if endpoint!=self._key_endpoint:
            if self.key_edit.text()==self.SAVED_KEY_MASK:self.key_edit.clear()
            self._key_endpoint=None
        name=self.provider_edit.text().strip()
        if endpoint and (not name or (not self._name_manual and name==self._auto_provider_name)):
            self._auto_provider_name=provider_name_from_url(text)
            self.provider_edit.setText(self._auto_provider_name)

    def _show_saved_key(self,url):
        self.key_edit.clear();self._key_endpoint=None
        try:
            if self.credentials_for(url).get():
                self.key_edit.setText(self.SAVED_KEY_MASK);self._key_endpoint=url
        except CredentialError as exc:self.status_label.setText(str(exc))

    def _resolve_key(self,url):
        text=self.key_edit.text().strip()
        if text and text!=self.SAVED_KEY_MASK:return validate_key(text),True
        if text==self.SAVED_KEY_MASK and self._key_endpoint!=url:
            raise ValueError('请重新填写此接口的 API Key。')
        key=self.credentials_for(url).get()
        if not key and text==self.SAVED_KEY_MASK:self.key_edit.clear();self._key_endpoint=None
        return key,False

    def _select_provider(self,provider):
        profile=self.store.get(self.profile_id)
        selected=next((m.id for m in provider.models if m.id==profile.model),provider.models[0].id if provider.models else None)
        self.store.update(profile.id,profile.name,profile.description,selected,provider.id)
        self.editing_id=provider.id;self.refresh_entries();self.load_entry();self.credentialsChanged.emit()

    def save(self):
        if self.busy:return
        key_saved=config_saved=False
        try:
            self.store.migrate_legacy_models(self.credentials)
            name,url,_=self.catalog.validate_provider(self.provider_edit.text(),self.url_edit.text(),[])
            previous=self.catalog.validate_provider_identity(self.editing_id,name,url)
            credentials=self.credentials_for(url)
            key,changed=self._resolve_key(url)
            if changed:credentials.set(key);key_saved=True;self._show_saved_key(url)
            models=[m.model for m in previous.models] if previous and previous.url==url else []
            provider=self.catalog.save_provider(self.editing_id,name,url,models);config_saved=True
            self._select_provider(provider)
            if not key:self.status_label.setText('已保存，请填写 API Key 后重新保存以获取模型。');return
        except (ValueError,CredentialError) as exc:
            if key_saved or config_saved:self.credentialsChanged.emit()
            self.status_label.setText(str(exc));return
        except OSError:
            if key_saved or config_saved:self.credentialsChanged.emit()
            self.status_label.setText(('API Key 已保存；' if key_saved else '')+'厂商设置未完整保存，请检查数据目录权限。');return
        self._set_busy(True);self.status_label.setText('正在获取模型…')
        self.task=Task(lambda:list_provider_models(key,url))
        def received(models):
            try:
                if credentials.get()!=key:raise CredentialError('API Key 已变更，请重新保存。')
                updated=self.catalog.save_provider(provider.id,provider.name,provider.url,models)
                self._select_provider(updated);self.status_label.setText(f'已保存 · 获取 {len(models)} 个模型')
            except (ValueError,CredentialError,OSError):
                self.credentialsChanged.emit();self.status_label.setText('模型列表未保存，请检查配置后重新保存。')
        self.task.signals.succeeded.connect(received)
        self.task.signals.failed.connect(lambda message:self.status_label.setText('厂商已保存；'+message+'。重新保存可重试。'))
        self.task.signals.finished.connect(lambda:self._set_busy(False));self.pool.start(self.task)

    def test_connection(self):
        if self.busy:return
        try:
            url=completion_url(self.url_edit.text());key,_=self._resolve_key(url)
            if not key:raise ValueError('请填写 API Key。')
        except (ValueError,CredentialError) as exc:self.status_label.setText(str(exc));return
        self._set_busy(True);self.status_label.setText('正在测试…')
        self.task=Task(lambda:list_provider_models(key,url))
        self.task.signals.succeeded.connect(lambda models:self.status_label.setText(f'连接成功 · {len(models)} 个模型'))
        self.task.signals.failed.connect(self.status_label.setText)
        self.task.signals.finished.connect(lambda:self._set_busy(False));self.pool.start(self.task)

    def delete_provider(self):
        if self.busy or not self.editing_id:return
        if QMessageBox.question(self,'删除厂商','删除此厂商的配置及未共用的 Key？实验与资料仍保留。',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        key_deleted=False
        try:
            provider=self.catalog.get_provider(self.editing_id);credentials=self.credentials_for(provider.url)
            def shared(other):
                scoped=self.credentials_for(other.url)
                return scoped.target==credentials.target if hasattr(scoped,'target') and hasattr(credentials,'target') else scoped is credentials
            if not any(p.id!=provider.id and shared(p) for p in self.catalog.list_providers()):credentials.delete();key_deleted=True
            self.catalog.delete_provider(provider.id);self.editing_id=None
            self.refresh_entries();self.new_provider();self.credentialsChanged.emit();self.status_label.setText('厂商已删除')
        except (ValueError,CredentialError,OSError):
            self.credentialsChanged.emit();self.status_label.setText(('Key 已删除；' if key_deleted else '')+'厂商删除未完成，请重试。')

    def _set_busy(self,busy):
        self.busy=busy
        for widget in (self.provider_list,self.new_button,self.remove_button,self.provider_edit,self.url_edit,self.key_edit,self.test_button,self.buttons):widget.setEnabled(not busy)
        self.remove_button.setEnabled(bool(self.editing_id and not busy));self.busyChanged.emit(busy)

    def reject(self):
        if not self.busy:self.key_edit.clear();super().reject()

    def closeEvent(self,event):
        if self.busy:event.ignore()
        else:self.key_edit.clear();event.accept()


