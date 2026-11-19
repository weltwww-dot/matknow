from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

from .models import Attachment, valid_id, validate_draft

MAX_FILE_BYTES = 20 * 1024 * 1024


def read_bytes(path: Path) -> bytes:
    with Path(path).open('rb') as source:
        data = source.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError('单个文件不能超过 20 MB。')
    return data


@dataclass
class Upload:
    path: Path
    token: str
    role: str = 'attachment'
    sample_code: str | None = None
    result_index: int | None = None
    expected_sha256: str | None = None


class ExperimentArchive:
    def __init__(self, store):
        self.store = store
        base = store.knowledge.path.parent.resolve()
        self.base = base
        self.root = base / 'experiment-files'
        if not self.root.resolve().is_relative_to(base):
            raise ValueError('实验附件目录超出当前方向。')
        self.root.mkdir(parents=True, exist_ok=True)

    def _owned_path(self, file_id, relative_path):
        valid_id(file_id)
        if not self.root.resolve().is_relative_to(self.base):
            raise ValueError('实验附件目录超出当前方向。')
        parts = Path(relative_path).parts
        if len(parts) != 2 or parts[0] != file_id or not re.fullmatch(r'original(?:\.[a-z0-9]{1,12})?', parts[1]):
            raise ValueError('附件存储路径无效。')
        target = self.root / relative_path
        if not target.resolve().is_relative_to(self.root.resolve()) or target.parent.is_symlink() or target.is_symlink():
            raise ValueError('附件路径超出当前方向。')
        return target

    def attachment(self, attachment_id):
        valid_id(attachment_id)
        with self.store.knowledge.connection() as db:
            row = db.execute('SELECT payload FROM experiment_attachments WHERE id=?', (attachment_id,)).fetchone()
        if row is None:
            raise ValueError('附件不存在或不属于当前方向。')
        return Attachment(**json.loads(row[0]))

    def path_for(self, attachment_id):
        item = self.attachment(attachment_id)
        target = self._owned_path(item.id, item.relative_path)
        if not target.is_file():
            raise ValueError('原始附件已缺失。')
        if hashlib.sha256(read_bytes(target)).hexdigest() != item.sha256:
            raise ValueError('已保存附件发生变化，请核对原件。')
        return target

    def save(self, draft, uploads, *, experiment_id=None, expected_revision=None,draft_id=None):
        validate_draft(draft)
        draft = deepcopy(draft)
        experiment_id = valid_id(experiment_id) if experiment_id else str(uuid4())
        for sample in draft.samples:
            sample.id = sample.id or str(uuid4())
            for result in sample.results:
                result.id = result.id or str(uuid4())
        tokens, prepared = {}, []
        for upload in uploads:
            if not upload.token or upload.token in tokens or upload.role not in ('source', 'attachment'):
                raise ValueError('附件导入标记无效或重复。')
            sample = next((s for s in draft.samples if s.code == upload.sample_code), None) if upload.sample_code is not None else None
            if upload.sample_code is not None and sample is None:
                raise ValueError('请为结果指定当前实验中的样品。')
            result = None
            if upload.result_index is not None:
                if sample is None or not isinstance(upload.result_index, int) or not 0 <= upload.result_index < len(sample.results):
                    raise ValueError('结果关联无效。')
                result = sample.results[upload.result_index]
            file_id = str(uuid4())
            tokens[upload.token] = file_id
            prepared.append((upload, file_id, sample, result))
        created, attachments = [], []
        try:
            for upload, file_id, sample, result in prepared:
                data = read_bytes(upload.path)
                digest = hashlib.sha256(data).hexdigest()
                if upload.expected_sha256 and digest != upload.expected_sha256:
                    raise ValueError('原始文件已被修改，请重新导入。')
                suffix = Path(upload.path).suffix.lower()
                if not re.fullmatch(r'\.[a-z0-9]{1,12}', suffix):
                    suffix = '.bin'
                relative = str(Path(file_id) / ('original' + suffix))
                target = self._owned_path(file_id, relative)
                target.parent.mkdir()
                created.append(target)
                with target.open('xb') as output:
                    output.write(data)
                attachments.append(Attachment(file_id, experiment_id, Path(upload.path).name, relative, digest, len(data), upload.role, sample.id if sample else None, result.id if result else None))
                if result:
                    result.attachment_ids.append(file_id)
                elif sample is None:
                    draft.source_ids.append(file_id)
            draft.source_ids = list(dict.fromkeys(tokens.get(i, i) for i in draft.source_ids))
            for value in draft.common.values():
                if value.source:
                    value.source.file_id = tokens.get(value.source.file_id, value.source.file_id)
            for sample in draft.samples:
                for value in [*sample.conditions.values(), *[r.value for r in sample.results]]:
                    if value.source:
                        value.source.file_id = tokens.get(value.source.file_id, value.source.file_id)
                for result in sample.results:
                    result.attachment_ids = list(dict.fromkeys(tokens.get(i, i) for i in result.attachment_ids))
                    if result.data.get('attachment_id'):
                        result.data['attachment_id'] = tokens.get(result.data['attachment_id'], result.data['attachment_id'])
            return self.store.save(draft, experiment_id=experiment_id, expected_revision=expected_revision, attachments=attachments,draft_id=draft_id)
        except Exception:
            for target in created:
                try:
                    target.unlink(missing_ok=True)
                    target.parent.rmdir()
                except OSError:
                    # Startup recovery retries only UUID directories without a registered attachment.
                    pass
            raise

    def _remove(self, file_id, relative):
        target = self._owned_path(file_id, relative)
        target.unlink(missing_ok=True)
        if target.parent.exists():
            target.parent.rmdir()

    def recover(self):
        warnings = []
        with self.store.knowledge.connection() as db:
            pending = list(db.execute('SELECT id,relative_path FROM experiment_file_deletions'))
            known = {r[0] for r in db.execute('SELECT id FROM experiment_attachments')}
        for file_id, relative in pending:
            try:
                self._remove(file_id, relative)
                with self.store.knowledge.connection() as db:
                    db.execute('DELETE FROM experiment_file_deletions WHERE id=?', (file_id,))
            except (OSError, ValueError):
                warnings.append('部分实验文件未清理，请关闭占用文件后重试。')
        pending_ids = {r[0] for r in pending}
        for folder in self.root.iterdir():
            try:
                valid_id(folder.name)
            except ValueError:
                continue
            if folder.name in known or folder.name in pending_ids or not folder.is_dir() or folder.is_symlink():
                continue
            try:
                entries = list(folder.iterdir())
                if len(entries) == 1:
                    self._remove(folder.name, str(Path(folder.name) / entries[0].name))
                elif not entries:
                    folder.rmdir()
            except (OSError, ValueError):
                warnings.append('部分未完成附件仍待清理。')
        return list(dict.fromkeys(warnings))

    def delete(self, experiment_id):
        self.store.delete_rows(experiment_id)
        warnings = self.recover()
        if warnings:
            raise OSError(' '.join(warnings))
