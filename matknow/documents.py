import re
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from matknow.models import Chunk

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_TEXT_CHARS = 1_000_000


def parse_text(text: str, page: int | None = None) -> list[Chunk]:
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("提取文本超过 100 万字符，请拆分资料后导入。")
    chunks = []
    paragraphs = re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip())
    for index, paragraph in enumerate(paragraphs, 1):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        base = f"第 {page} 页 · 段落 {index}" if page else f"段落 {index}"
        start = 0
        part = 1
        while start < len(paragraph):
            end = min(start + 1000, len(paragraph))
            locator = f"{base} · 片段 {part}" if len(paragraph) > 1000 else base
            chunks.append(Chunk(paragraph[start:end], locator))
            if end == len(paragraph):
                break
            start = end - 120
            part += 1
    if not chunks:
        raise ValueError("资料没有可检索的文字。")
    return chunks


def parse_file(path: Path) -> list[Chunk]:
    path = Path(path)
    if not path.is_file():
        raise ValueError("找不到要导入的文件。")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("单个文件不能超过 20 MB。")
    with path.open('rb') as source:data=source.read(MAX_FILE_BYTES+1)
    return parse_bytes(data,path.name)


def parse_bytes(data:bytes,filename:str,*,warnings=None) -> list[Chunk]:
    if len(data)>MAX_FILE_BYTES:raise ValueError('单个文件不能超过 20 MB。')
    suffix = Path(filename).suffix.lower()
    if suffix in {'.docx','.xlsx'}:
        from matknow.experiments.readers import read_record_bytes
        document=read_record_bytes(data,filename=filename,file_id='library-import')
        if warnings is not None:warnings.extend(document.warnings)
        chunks=[]
        for block in document.blocks:
            if not block.text.strip():continue
            parts=parse_text(block.text)
            chunks.extend(Chunk(part.text,block.locator+(' · '+part.locator if len(parts)>1 else '')) for part in parts)
        if not chunks:raise ValueError('资料没有可检索的文字。')
        return chunks
    if suffix not in {".pdf", ".txt", ".md", ".markdown",'.csv','.tsv'}:
        raise ValueError("支持文本型 PDF、DOCX、XLSX、UTF-8 TXT 和 Markdown。")
    if suffix != ".pdf":
        try:
            return parse_text(data.decode("utf-8-sig"))
        except UnicodeError as exc:
            raise ValueError("文本编码不是 UTF-8，请转换编码后再导入。") from exc
    try:
        with BytesIO(data) as source:
            reader = PdfReader(source)
            if reader.is_encrypted:
                raise ValueError("暂不支持加密 PDF，请提供未加密版本。")
            if len(reader.pages) > MAX_PDF_PAGES:
                raise ValueError("单个 PDF 不能超过 100 页，请拆分后导入。")
            chunks = []
            total = 0
            for page_number, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                total += len(text)
                if total > MAX_TEXT_CHARS:
                    raise ValueError("PDF 提取文本超过 100 万字符，请拆分后导入。")
                if text.strip():
                    chunks.extend(parse_text(text, page=page_number))
            if not chunks:
                raise ValueError("PDF 没有可提取文字，扫描件需要先进行 OCR。")
            return chunks
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("PDF 解析失败，请检查文件是否完整。") from exc
