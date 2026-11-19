"""Read explicitly supplied local files and ZIP members without extracting to disk."""
import hashlib
from io import BytesIO
import os
from pathlib import Path,PurePosixPath
import re
import stat
import zlib
from zipfile import ZipFile,BadZipFile

from matknow.documents import parse_bytes,MAX_FILE_BYTES
from matknow.models import Chunk

EXTENSIONS={'.pdf','.docx','.xlsx','.txt','.md','.markdown','.csv','.tsv'}
PATH_PATTERN=re.compile(r'''(?:[A-Za-z]:[\\/]|/)[^\r\n"'`<>|]*?\.(?:pdf|docx|xlsx|txt|md|markdown|csv|tsv|zip)(?=$|[\s"'`，。！？；（）\[\]]|[\u4e00-\u9fff])''',re.I)
PATH_START=re.compile(r'[A-Za-z]:[\\/]|(?<![\w:/\\])/(?!/)')
MAX_ARCHIVE_BYTES=100*1024*1024


def explicit_paths(question):
    found=[]
    starts=list(PATH_START.finditer(question))
    for index,match in enumerate(starts):
        end=starts[index+1].start() if index+1<len(starts) else len(question)
        candidate=re.split(r'''[\r\n"'`<>|“”‘’]''',question[match.start():end],maxsplit=1)[0].strip()
        known=PATH_PATTERN.match(candidate)
        quoted=match.start()>0 and question[match.start()-1] in '\"\'`“‘'
        if not quoted:
            boundary=max(candidate.rfind('\\'),candidate.rfind('/'))+1
            cuts=[m.start() for m in re.finditer(r'\s|[，。！？；]|整理|分类|导入|总结|分析|帮我|请|并',candidate) if m.start()>boundary]
            choices=[candidate,*[candidate[:cut].rstrip() for cut in sorted(cuts,reverse=True)]]
            existing=None
            for choice in choices:
                try:
                    if Path(choice).exists():existing=choice;break
                except OSError:pass
            candidate=existing or (known.group() if known else candidate[:min(cuts)].rstrip() if cuts else candidate)
        path=Path(candidate)
        if path.is_absolute() and str(path) not in found:found.append(str(path))
    return found[:10]


class LocalFiles:
    def __init__(self,knowledge,paths):
        self.paths=list(dict.fromkeys(paths));self.digests={};self.member_digests={};self.directory_digests={}
        root=knowledge.path.resolve().parent
        self.storage=root.parent.parent if root.parent.name=='profiles' else root

    def _path(self,value,*,allow_directory=False):
        if not isinstance(value,str) or not value or '\x00' in value:raise ValueError('文件路径无效。')
        if value.startswith(('\\\\','//')) or ':' in value[2:]:raise ValueError('请使用本地磁盘上的普通文件路径。')
        path=Path(value)
        if not path.is_absolute():raise ValueError('请提供完整的本地文件路径。')
        key=os.path.normcase(os.path.abspath(path))
        allowed={os.path.normcase(os.path.abspath(p)) for p in self.paths}
        if key not in allowed:
            permitted=False
            for root in self.paths:
                try:
                    checked=self._path(root,allow_directory=True)
                    if checked.is_dir() and path.resolve().is_relative_to(checked):permitted=True;break
                except (ValueError,OSError):continue
            if not permitted:raise ValueError('只能读取用户在当前问题或当前会话中明确指定的文件或文件夹。')
        try:
            for part in (path,*path.parents):
                info=part.lstat()
                if part.is_symlink() or getattr(info,'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise ValueError('不读取符号链接或重解析路径，请直接提供原文件路径。')
            path=path.resolve()
            if path.is_relative_to(self.storage) or 'app-data' in {p.casefold() for p in path.parts}:
                raise ValueError('不能读取应用内部数据、凭据或其他方向的存储文件。')
            if not (path.is_file() or allow_directory and path.is_dir()):raise ValueError('指定路径不是普通文件或可读取的文件夹。')
        except OSError:raise ValueError('找不到指定文件，或没有读取权限。') from None
        return path

    def is_directory(self,path):
        return self._path(path,allow_directory=True).is_dir()

    def _directory_entries(self,value):
        root=self._path(value,allow_directory=True);pending=[root];entries=[];fingerprint=[];count=0;total=0
        try:
            while pending:
                current=self._path(str(pending.pop()),allow_directory=True)
                with os.scandir(current) as children:
                    for child in children:
                        count+=1
                        if count>500:raise ValueError('文件夹超过500个条目，请指定更小的子文件夹。')
                        path=Path(child.path);name=path.relative_to(root).as_posix();info=child.stat(follow_symlinks=False)
                        blocked=child.is_symlink() or getattr(info,'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT or path.is_relative_to(self.storage) or 'app-data' in {p.casefold() for p in path.parts}
                        # Folder timestamps can settle after enumeration on Windows; compare their membership by name.
                        directory=stat.S_ISDIR(info.st_mode)
                        fingerprint.append((name,0 if directory or blocked else info.st_size,0 if directory or blocked else info.st_mtime_ns,bool(blocked)))
                        if blocked:
                            entries.append({'name':name,'bytes':0,'readable':False,'reason':'应用内部存储或链接路径，已跳过'})
                        elif directory:pending.append(path)
                        else:
                            total+=info.st_size
                            if total>MAX_ARCHIVE_BYTES:raise ValueError('文件夹内容超过100 MB，请指定更小的子文件夹。')
                            entries.append({'name':name,'bytes':info.st_size,'readable':stat.S_ISREG(info.st_mode) and path.suffix.lower() in EXTENSIONS})
        except OSError:raise ValueError('无法读取指定文件夹，请检查路径和权限。') from None
        digest=hashlib.sha256(repr(sorted(fingerprint)).encode('utf8')).hexdigest();key=os.path.normcase(os.path.abspath(value))
        if key in self.directory_digests and self.directory_digests[key]!=digest:raise ValueError('文件夹内容已更新（条目变更），请重新提问；已导入原文保留。')
        self.directory_digests[key]=digest
        return sorted(entries,key=lambda e:e['name'].casefold())

    def _bytes(self,value):
        path=self._path(value)
        maximum=MAX_ARCHIVE_BYTES if path.suffix.lower()=='.zip' else MAX_FILE_BYTES
        try:
            with path.open('rb') as source:data=source.read(maximum+1)
        except OSError:raise ValueError('无法读取指定文件，请检查路径和权限。') from None
        if len(data)>maximum:raise ValueError('文件超过读取限制：普通文件20 MB，ZIP压缩包100 MB。')
        digest=hashlib.sha256(data).hexdigest()
        key=os.path.normcase(os.path.abspath(value))
        if key in self.digests and self.digests[key]!=digest:raise ValueError('文件已更新（内容变更），请重新提问；已导入的原文快照保留。')
        self.digests[key]=digest
        return path,data

    @staticmethod
    def _zip(data):
        try:
            archive=ZipFile(BytesIO(data));entries=archive.infolist()
            if len(entries)>500 or sum(e.file_size for e in entries)>MAX_ARCHIVE_BYTES:raise ValueError('ZIP内容超过500个条目或100 MB，请拆分后读取。')
            seen=set()
            for entry in entries:
                name=entry.filename.replace('\\','/');parts=PurePosixPath(name)
                if parts.is_absolute() or '..' in parts.parts or ':' in name or len(name)>512 or name in seen or stat.S_ISLNK(entry.external_attr>>16):
                    raise ValueError('ZIP包含无效、重复或不安全的文件名。')
                if entry.flag_bits&1:raise ValueError('暂不支持加密ZIP，请先提供未加密副本。')
                seen.add(name)
            return archive
        except (BadZipFile,OSError):raise ValueError('ZIP文件损坏或格式无效。') from None

    def entries(self,path):
        if self.is_directory(path):return self._directory_entries(path)
        original,data=self._bytes(path)
        with self._zip(data) as archive:
            return [{'name':e.filename,'bytes':e.file_size,'readable':Path(e.filename).suffix.lower() in EXTENSIONS}
                    for e in archive.infolist() if not e.is_dir()]

    def read(self,path,member=None):
        original=self._path(path,allow_directory=True);prefix='';filename=original.name
        if original.is_dir():
            if not isinstance(member,str) or not member:raise ValueError('请指定文件夹中的相对文件名。')
            relative=PurePosixPath(member.replace('\\','/'))
            if relative.is_absolute() or '..' in relative.parts or ':' in member:raise ValueError('文件夹成员必须是当前文件夹内的相对路径。')
            key=os.path.normcase(os.path.abspath(path))
            if key not in self.directory_digests:self._directory_entries(path)
            child,data=self._bytes(str(original.joinpath(*relative.parts)))
            filename=child.name;prefix=f'文件夹 {original.name} / {member} / '
        else:original,data=self._bytes(path)
        if original.is_file() and original.suffix.lower()=='.zip':
            if not isinstance(member,str) or not member:raise ValueError('请指定ZIP内部的文件名。')
            try:
                with self._zip(data) as archive:
                    entry=archive.getinfo(member)
                    if entry.file_size>MAX_FILE_BYTES:raise ValueError('ZIP中的单个文档超过20 MB。')
                    if Path(member).suffix.lower() not in EXTENSIONS:raise ValueError('此文件格式无法提取可读文字，未解读图片或仪器文件。')
                    with archive.open(entry) as source:data=source.read(MAX_FILE_BYTES+1)
            except (KeyError,BadZipFile,RuntimeError,NotImplementedError,EOFError,zlib.error):raise ValueError('无法读取ZIP中的指定文档，内容损坏或压缩格式不支持。') from None
            filename=PurePosixPath(member.replace('\\','/')).name
            prefix=f'ZIP {original.name} / {member} / '
        elif original.is_file() and member is not None:raise ValueError('普通文件不需要ZIP内部文件名。')
        if Path(filename).suffix.lower() not in EXTENSIONS:raise ValueError('支持PDF、DOCX、XLSX、TXT、Markdown、CSV、TSV。')
        self.member_digests[path,member]=hashlib.sha256(data).hexdigest()
        warnings=[];chunks=parse_bytes(data,filename,warnings=warnings)
        return filename,[Chunk(c.text,prefix+c.locator) for c in chunks],warnings

    def ensure_current(self):
        for path in list(self.directory_digests):
            try:self._directory_entries(path)
            except ValueError:raise ValueError('指定文件夹已更新、删除或无法读取，请重新提问；已导入的原文快照保留。') from None
        for path in list(self.digests):
            try:self._bytes(path)
            except ValueError:raise ValueError('指定文件已更新、删除或无法读取，请重新提问；已导入的原文快照保留。') from None
