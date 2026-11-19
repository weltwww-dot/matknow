from PySide6.QtCore import QObject, QRunnable, Signal


class TaskCancelled(Exception):
    pass


def check_cancelled(client):
    event = getattr(client, 'cancel_event', None)
    if event is not None and event.is_set():
        raise TaskCancelled('已停止后续处理，已经完成的导入和保存仍保留。')


class TaskSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()


class Task(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = TaskSignals()

    def run(self):
        try:
            self.signals.succeeded.emit(self.function())
        except Exception as exc:
            self.signals.failed.emit(str(exc) or '操作未完成，请重试。')
        finally:
            self.signals.finished.emit()
