from PySide6.QtCore import Qt, Signal, QElapsedTimer, QTimer,QRectF
from PySide6.QtGui import QTextDocument,QPainter,QPen,QColor
from PySide6.QtWidgets import QLabel, QPushButton, QPlainTextEdit, QWidget, QVBoxLayout, QHBoxLayout, QProgressBar, QToolButton


class ChatDocument(QTextDocument):
    def loadResource(self,resource_type,name):
        # Model Markdown cannot load local files or remote images.
        return None


class TaskActivity(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.running=False;self.clock=QElapsedTimer()
        self.timer=QTimer(self);self.timer.setInterval(250);self.timer.timeout.connect(self._refresh_title)
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(5)
        row=QHBoxLayout();self.title=label('','muted');row.addWidget(self.title,1)
        self.toggle=QToolButton();self.toggle.setText('处理记录');self.toggle.setCheckable(True)
        self.toggle.setAccessibleName('展开或收起处理记录');row.addWidget(self.toggle);layout.addLayout(row)
        self.stage=label('','muted',True);layout.addWidget(self.stage)
        self.bar=QProgressBar();self.bar.setRange(0,0);self.bar.setTextVisible(False);self.bar.setFixedHeight(4)
        self.bar.setAccessibleName('问题处理中');layout.addWidget(self.bar)
        self.details=QPlainTextEdit();self.details.setReadOnly(True);self.details.setMaximumBlockCount(64)
        self.details.setMaximumHeight(96);self.details.setAccessibleName('问题处理步骤')
        layout.addWidget(self.details);self.toggle.toggled.connect(self.details.setVisible)
        self.hide()

    def start(self,message):
        self.running=True;self.clock.start();self.details.clear()
        self.toggle.setChecked(True);self.details.show();self.bar.show();self.show()
        self.update_stage(message);self._refresh_title();self.timer.start()

    def update_stage(self,message):
        if not self.running:return
        if message!=self.stage.text() or not self.details.toPlainText():
            self.details.appendPlainText(message)
        self.stage.setText('已生成行动计划' if message.startswith('行动计划：') else message)

    def _refresh_title(self):
        seconds=self.clock.elapsed()//1000 if self.clock.isValid() else 0
        self.title.setText(('处理中' if self.running else '处理记录')+f' · 已用时 {seconds} 秒')

    def finish(self,message):
        if not self.running:return
        self.update_stage(message);self.running=False;self.timer.stop();self.bar.hide()
        self._refresh_title();self.toggle.setChecked(False);self.details.hide()

    def clear(self):
        self.running=False;self.timer.stop();self.details.clear();self.stage.clear();self.hide()


class ContextRing(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent);self.setFixedSize(26,26)
        self.setAccessibleName('上下文使用量');self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet('QToolTip { background:#343a40; color:white; border:none; padding:6px; }')
        self.set_usage({'used':0,'capacity':None,'estimated':True})

    def set_usage(self,usage):
        self.usage=dict(usage);used=usage['used'];capacity=usage['capacity']
        if capacity:
            ratio=used/capacity
            text=f'{ratio:.1%} · {used/1000:.1f}K / {capacity/1000:.1f}K tokens'
        else:text=f'{used/1000:.1f}K tokens · 模型上下文容量未知'
        text+='\n'+(('请求用量（估算）' if usage.get('phase') else '预计下次消息用量（估算，不含尚未检索的证据）') if usage['estimated'] else '请求用量（厂商返回，含回答）')
        text+='\n'+('正在自动压缩较早对话' if usage.get('phase')=='compacting' else '接近可用容量时自动压缩，原始历史保留')
        if not capacity:text+='\n厂商未返回容量，由模型服务判定；超限后尝试压缩历史'
        self.setToolTip(text);self.setAccessibleDescription(text);self.update()

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect=QRectF(5,5,16,16);pen=QPen(QColor('#dde4e8'),2.5);pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen);painter.drawEllipse(rect)
        capacity=self.usage['capacity']
        if capacity:
            ratio=min(1,self.usage['used']/capacity)
            pen.setColor(QColor('#bc7041' if ratio>=.8 else '#267862'));painter.setPen(pen)
            painter.drawArc(rect,90*16,-round(360*16*ratio))
        else:
            painter.setPen(QColor('#8a99a4'));painter.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,'?')


class QuestionEdit(QPlainTextEdit):
    sendRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._composing = False
        self._hint = ''
        self.textChanged.connect(self._refresh_placeholder)
        self.context_ring=ContextRing(self)
        self.setViewportMargins(0,0,36,0)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.context_ring.move(self.width()-self.context_ring.width()-10,self.height()-self.context_ring.height()-8)
        self.context_ring.raise_()

    def setPlaceholderText(self,text):
        self._hint=text;self._refresh_placeholder()

    def _refresh_placeholder(self):
        super().setPlaceholderText('' if self._composing or self.toPlainText() else self._hint)

    def inputMethodEvent(self, event):
        self._composing = bool(event.preeditString())
        super().inputMethodEvent(event)
        self._refresh_placeholder()

    def keyPressEvent(self, event):
        if not self._composing and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ControlModifier):
            event.accept()
            self.sendRequested.emit()
            return
        super().keyPressEvent(event)


def label(text: str, style: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if style:
        widget.setObjectName(style)
    widget.setWordWrap(wrap)
    return widget


def button(text: str, action, style: str = "") -> QPushButton:
    widget = QPushButton(text)
    if style:
        widget.setObjectName(style)
    widget.clicked.connect(action)
    return widget


