STYLE = """
QMainWindow, QDialog { background: #f3f6f8; }
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #203344; }
QFrame#panel { background: white; border: 1px solid #dfe6eb; border-radius: 14px; }
QLabel#brand { font-size: 29px; font-weight: 700; color: #183d43; }
QLabel#subtitle { color: #71808f; font-size: 12px; }
QLabel#sectionTitle { font-size: 17px; font-weight: 600; }
QLabel#muted { color: #72818f; font-size: 12px; }
QLabel#badge { background: #e0f1eb; color: #26725d; padding: 8px 15px; border-radius: 15px; }
QLabel#sourceTitle { font-size: 14px; font-weight: 600; color: #26725d; }
QPushButton { background: #eef3f5; border: 1px solid #e0e7eb; padding: 9px 13px; border-radius: 7px; }
QPushButton:hover { background: #e2eeeb; border-color: #b8d7cc; }
QPushButton:pressed { background: #d1e6df; }
QPushButton:disabled { color: #99a6af; background: #f4f6f7; border-color: #e9edef; }
QPushButton#primary { background: #267862; color: white; border: none; font-weight: 600; }
QPushButton#primary:hover { background: #1e6653; }
QPushButton#primary:disabled { background: #a6c5bb; }
QPushButton#ghost { background: transparent; color: #617788; padding: 5px 7px; border: none; }
QPushButton#chip { background: #f4f8f7; color: #3a7466; font-size: 12px; padding: 7px 9px; }
QMenu { background: white; border: 1px solid #dfe6eb; padding: 5px; }
QMenu::item { padding: 9px 20px; }
QMenu::item:selected { background: #e5f1ec; color: #215f50; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { padding: 10px 5px; border-bottom: 1px solid #f0f3f5; }
QListWidget::item:selected { background: #e5f1ec; color: #215f50; border-radius: 6px; }
QListWidget::item:hover { background: #f0f6f3; }
QTextBrowser { background: transparent; border: none; padding: 7px; font-size: 14px; }
QPlainTextEdit, QLineEdit, QComboBox { background: #f9fbfc; border: 1px solid #dce5e9; border-radius: 8px; padding: 10px; selection-background-color: #c7e2d7; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: white; selection-background-color: #e5f1ec; selection-color: #215f50; }
QPlainTextEdit:focus, QLineEdit:focus { border-color: #64a18a; }
QProgressBar { border: none; background: #e5f1ec; }
QProgressBar::chunk { background: #267862; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: transparent; color: #81909b; padding: 10px 15px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #267862; border-bottom: 2px solid #267862; }
QSplitter::handle { background: transparent; width: 10px; }
QTableWidget { background: white; gridline-color: #eef2f4; border: 1px solid #dfe6eb; selection-background-color: #e5f1ec; selection-color: #215f50; }
QTableWidget::item { padding: 4px; }
QHeaderView::section { background: #f0f5f4; color: #45685f; border: none; padding: 7px; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 2px; }
QScrollBar::handle:vertical { background: #cdd9dd; border-radius: 3px; min-height: 25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""
