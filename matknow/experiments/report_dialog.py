from PySide6.QtCore import Signal
from PySide6.QtWidgets import QTextBrowser, QPushButton, QFileDialog, QDialogButtonBox
import html

from .editors import FormDialog
from .report import export_report
from .report_writer import write_report


def preview_html(report):
    # Qt Markdown loses following content after multiline table cells; render the structured report directly.
    def text(value):return html.escape(str(value)).replace('\n','<br>')
    parts=['<h1>'+text(report.title)+'</h1>','<p>'+text(report.summary)+'</p>',
           '<p>'+'<br>'.join(text(name)+'：'+text(value) for name,value in report.metadata)+'</p>']
    for block in report.blocks:
        parts.append('<h2>'+text(block.heading)+'</h2>')
        parts.extend('<p>'+text(p)+'</p>' for p in block.paragraphs)
        if block.rows:
            parts.append('<table border="1" cellspacing="0" cellpadding="5" width="100%"><tr>'+''.join('<th>'+text(h)+'</th>' for h in block.headers)+'</tr>')
            parts.extend('<tr>'+''.join('<td>'+text(cell)+'</td>' for cell in row)+'</tr>' for row in block.rows)
            parts.append('</table>')
    return ''.join(parts)


class ReportDialog(FormDialog):
    reportProgress=Signal(str)

    def __init__(self,report,parent=None,*,model_client=None,archive=None):
        super().__init__(parent);self.report=report;self.setWindowTitle('实验报告预览');self.resize(950,740)
        self.preview=QTextBrowser();self.preview.setOpenExternalLinks(False);self.preview.setOpenLinks(False)
        self.preview.setHtml(preview_html(report));self.layout.addWidget(self.preview,1)
        save=QPushButton('导出报告');save.clicked.connect(self.save_report);self.layout.addWidget(save)
        self.reportProgress.connect(self.message.setText)
        if model_client is not None and archive is not None:
            generate=QPushButton('重新生成完整报告');generate.clicked.connect(lambda:self.run(lambda:write_report(self.report,model_client,archive=archive,progress=self.reportProgress.emit),self.show_report,'正在生成完整报告'));self.layout.addWidget(generate)
        self.message.setText(f'{len(report.warnings)} 项待补充或核对' if report.warnings else '请核对原记录和结果后导出。')
        self.finish_form('关闭')
        self.controls.button(QDialogButtonBox.StandardButton.Cancel).hide()

    def show_report(self,report):
        self.report=report;self.preview.setHtml(preview_html(report));self.message.setText('完整报告已生成，请核对原始记录后导出。')

    def save_report(self):
        path,selected_filter=QFileDialog.getSaveFileName(self,'导出实验报告','实验报告.docx','Word (*.docx);;Markdown (*.md)')
        if not path:return
        from pathlib import Path
        if not Path(path).suffix:path+='.md' if selected_filter.startswith('Markdown') else '.docx'
        self.run(lambda:export_report(self.report,path),lambda _:self.message.setText('报告已导出。'),'正在导出报告…')
