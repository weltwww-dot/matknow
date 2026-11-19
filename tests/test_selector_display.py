"""Use native Windows fonts; Qt's offscreen plugin has no bundled fonts."""
import os
import subprocess
import sys
import pytest

SCENARIO='''
import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication,QStyle,QStyleOptionComboBox,QSplitter
from matknow.desktop import MainWindow
from matknow.profiles import ProfileStore
app=QApplication([])
profiles=ProfileStore(Path(sys.argv[1]))
window=MainWindow(profiles.knowledge(profiles.current_id),profiles,allow_model=False)
window.resize(int(sys.argv[2]),840);window.show();app.processEvents()
provider=profiles.models.save_provider(None,'DeepSeek','https://api.deepseek.com',['deepseek-flash'])
profile=profiles.get(profiles.current_id)
profiles.update(profile.id,profile.name,profile.description,provider.models[0].id,provider.id)
window._reload_service();app.processEvents()
# Dragging the pane divider can shrink chat to its supported minimum width.
window.findChild(QSplitter).setSizes([10000,1,250]);app.processEvents()
for combo in (window.provider_combo,window.model_combo):
    option=QStyleOptionComboBox();combo.initStyleOption(option)
    rect=combo.style().subControlRect(QStyle.ComplexControl.CC_ComboBox,option,QStyle.SubControl.SC_ComboBoxEditField,combo)
    assert rect.width()>=combo.fontMetrics().horizontalAdvance(combo.currentText()),(combo.currentText(),rect.width(),combo.width())
window.close();app.processEvents()
'''


@pytest.mark.skipif(sys.platform!='win32',reason='Windows native font and scaling regression')
@pytest.mark.parametrize('width',[1000,1280])
@pytest.mark.parametrize('scale',[1,1.5,2])
def test_chat_selectors_leave_room_for_deepseek_names(tmp_path,width,scale):
    result=subprocess.run([sys.executable,'-c',SCENARIO,str(tmp_path),str(width)],
        capture_output=True,timeout=30,env=dict(os.environ,QT_QPA_PLATFORM='windows',QT_SCALE_FACTOR=str(scale)))
    assert result.returncode==0,result.stderr.decode('utf8',errors='replace')
