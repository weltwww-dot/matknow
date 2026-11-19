import argparse
import os
import sys
import tempfile
from pathlib import Path


def default_data_dir() -> Path:
    configured = os.environ.get("MATKNOW_DATA_DIR")
    if configured:
        return Path(configured)
    application = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
    return application / 'app-data'


def configure_fonts(application):
    from PySide6.QtGui import QFont, QFontDatabase

    # Windows' offscreen Qt platform has no system font database; use an installed font for preview rendering.
    if not QFontDatabase.families() and os.environ.get("WINDIR"):
        font_path = Path(os.environ["WINDIR"]) / "Fonts" / "msyh.ttc"
        if font_path.is_file():
            QFontDatabase.addApplicationFont(str(font_path))
    application.setFont(QFont("Microsoft YaHei UI", 10))


def lock_data_dir(data_dir):
    from PySide6.QtCore import QLockFile

    data_dir = Path(data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(data_dir / 'desktop.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        raise RuntimeError('此数据目录的应用已打开，或目录不可写。请使用已有窗口或另选数据目录。')
    return lock


def main():
    parser = argparse.ArgumentParser(description="材知科研知识库助手")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--screenshot", type=Path, help="在临时知识库中载入教学样例，保存界面截图后退出")
    parser.add_argument('--preview-workspace', choices=('documents', 'experiments'), default='documents')
    arguments = parser.parse_args()
    if arguments.screenshot:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    from matknow.desktop import MainWindow
    from matknow.profiles import ProfileStore
    from matknow.credentials import CredentialStore

    temporary = tempfile.TemporaryDirectory(prefix="matknow-preview-") if arguments.screenshot else None
    data_dir = Path(temporary.name) if temporary else arguments.data_dir or default_data_dir()
    application = QApplication(sys.argv[:1])
    application.setApplicationName("MatKnow")
    configure_fonts(application)
    examples = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "examples"
    try:
        instance_lock = lock_data_dir(data_dir)
        profiles = ProfileStore(data_dir)
        window = MainWindow(profiles.knowledge(profiles.current_id), profiles, CredentialStore(data_dir), allow_model=not bool(arguments.screenshot))
    except Exception as exc:
        message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else '无法打开数据目录或知识库，请检查目录权限和数据库文件。'
        if arguments.screenshot:
            print(message, file=sys.stderr)
        else:
            QMessageBox.critical(None, '材知无法启动', message)
        application.shutdown()
        return 1
    try:
        window.show()
        if not arguments.screenshot:
            QTimer.singleShot(0,window.refresh_provider_models)
            model_refresh_timer=QTimer(window)
            model_refresh_timer.setInterval(30*60*1000)
            model_refresh_timer.timeout.connect(window.refresh_provider_models)
            model_refresh_timer.start()
        if arguments.screenshot:
            if arguments.preview_workspace == 'experiments':
                from matknow.experiments.demo import populate_demo, exercise_demo_previews
                snapshot = populate_demo(window.experiment_page.archive, data_dir)
                window.experiment_page.refresh()
                window.workspaces.setCurrentIndex(1)
                window.experiment_page.record_list.setCurrentRow(0)
                exercise_demo_previews(window.experiment_page, snapshot)
            else:
                for path in sorted(examples.glob("*.md")):
                    window.knowledge.import_file(path)
                window.refresh_documents()
                window.question_edit.setPlainText("两份资料中的干燥温度和时间分别是多少？")
                window.present_answer(window.service.ask(window.question_edit.toPlainText()))

            def capture():
                arguments.screenshot.parent.mkdir(parents=True, exist_ok=True)
                saved = window.grab().save(str(arguments.screenshot))
                application.exit(0 if saved else 1)

            QTimer.singleShot(300, capture)
        result = application.exec()
    finally:
        # Finish Qt cleanup before Python tears down callbacks and thread storage.
        application.shutdown()
        instance_lock.unlock()
        if temporary:
            temporary.cleanup()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
