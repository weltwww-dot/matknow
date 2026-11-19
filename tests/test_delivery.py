import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_desktop_main_releases_qt_before_interpreter_shutdown(tmp_path):
    check = """
import sys
from PySide6.QtWidgets import QApplication
from matknow.__main__ import main
sys.argv = ['matknow', '--screenshot', sys.argv[1]]
assert main() == 0
assert QApplication.instance() is None, 'Qt must finish cleanup before Python exits'
"""
    target = tmp_path / 'desktop.png'
    result = subprocess.run([sys.executable, '-c', check, str(target)], capture_output=True,
                            text=True, timeout=15, env=dict(os.environ, QT_QPA_PLATFORM='offscreen'))
    assert result.returncode == 0, result.stderr
    assert target.stat().st_size > 1000


def test_collected_notices_include_open_source_qt_license_texts(tmp_path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "notices.txt"
    subprocess.run([sys.executable, str(root / "scripts/collect_licenses.py"), str(output)], check=True, cwd=root)
    notices = output.read_text(encoding="utf-8")
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in notices
    assert "GNU GENERAL PUBLIC LICENSE" in notices
    assert "PySide6" in notices
    assert "pypdf" in notices


def test_packaged_office_parser_import_and_notices(tmp_path):
    root=Path(__file__).resolve().parents[1]
    output=tmp_path/'notices.txt'
    subprocess.run([sys.executable,str(root/'scripts/collect_licenses.py'),str(output)],check=True,cwd=root)
    text=output.read_text(encoding='utf8').lower()
    for name in ['python-docx','openpyxl','lxml','et_xmlfile','defusedxml']:
        assert name in text


def test_experiment_screenshot_uses_temporary_profile_and_exits_cleanly(tmp_path):
    target=tmp_path/'experiments.png';data=tmp_path/'real-data';data.mkdir();marker=data/'KEEP';marker.write_bytes(b'FAKE_KEY_PRIVATE')
    check="""
import sys
from PySide6.QtWidgets import QApplication
from matknow.__main__ import main
sys.argv=['matknow','--screenshot',sys.argv[1],'--preview-workspace','experiments','--data-dir',sys.argv[2]]
assert main()==0
assert QApplication.instance() is None
"""
    result=subprocess.run([sys.executable,'-c',check,str(target),str(data)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert target.stat().st_size>1000
    assert list(data.iterdir())==[marker] and marker.read_bytes()==b'FAKE_KEY_PRIVATE'


def test_experiment_preview_exercises_office_curve_image_and_pdf_readers(tmp_path):
    target=tmp_path/'all-formats.png'
    check="""
import sys
from matknow.__main__ import main
from matknow.experiments import preview
original=preview.prepare_attachment
seen=[]
def record(*args,**kwargs):
    payload=original(*args,**kwargs)
    seen.append(payload[0])
    return payload
preview.prepare_attachment=record
sys.argv=['matknow','--screenshot',sys.argv[1],'--preview-workspace','experiments']
assert main()==0
assert {'table','image','text'}<=set(seen),seen
"""
    result=subprocess.run([sys.executable,'-c',check,str(target)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows package dependency check")
def test_built_qt_dlls_resolve_without_foreign_icu_collision():
    root = Path(__file__).resolve().parents[1] / "dist/MatKnow/_internal"
    if not (root / "PySide6/QtCore.pyd").is_file():
        pytest.skip("Build the Windows package to run this delivery check")
    check = """
import ctypes, os, sys
from pathlib import Path
ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
root = Path(sys.argv[1])
handles = [os.add_dll_directory(str(folder)) for folder in [root, root/'PySide6', root/'shiboken6']]
for relative in ['PySide6/Qt6Core.dll', 'shiboken6/shiboken6.abi3.dll', 'PySide6/pyside6.abi3.dll', 'PySide6/QtCore.pyd']:
    ctypes.WinDLL(str(root / relative))
"""
    result = subprocess.run([sys.executable, "-c", check, str(root)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
