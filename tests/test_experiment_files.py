from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from matknow.knowledge import KnowledgeBase
from matknow.experiments.models import ExperimentDraft, SampleDraft, ResultDraft, Value, SourceRef
from matknow.experiments.store import ExperimentStore
from matknow.experiments.files import ExperimentArchive, Upload


@pytest.fixture
def archive(tmp_path):
    return ExperimentArchive(ExperimentStore(KnowledgeBase(tmp_path / 'data/knowledge.sqlite3')))


def draft():
    return ExperimentDraft('实验', '2026-10-13', samples=[SampleDraft('A1', results=[ResultDraft('观察', Value('均匀'), kind='text')])])


def test_originals_survive_source_move_and_same_filenames_do_not_collide(archive, tmp_path):
    source = tmp_path / 'same.txt'
    source.write_bytes(b'original one')
    first = archive.save(draft(), [Upload(source, 'first', role='source')])
    source.write_bytes(b'original two')
    second = archive.save(draft(), [Upload(source, 'second', role='source')])
    source.unlink()
    first_path = archive.path_for(first.draft.source_ids[0])
    second_path = archive.path_for(second.draft.source_ids[0])
    assert first_path != second_path
    assert first_path.read_bytes() == b'original one'
    assert second_path.read_bytes() == b'original two'


def test_upload_tokens_map_to_explicit_sample_and_result(archive, tmp_path):
    source = tmp_path / 'data.csv'
    source.write_text('x,y\n1,2', encoding='utf-8')
    record = draft()
    record.samples[0].results[0].value.source = SourceRef('file', 'Sheet1!B2')
    saved = archive.save(record, [Upload(source, 'file', sample_code='A1', result_index=0)])
    result = saved.draft.samples[0].results[0]
    attachment = archive.attachment(result.attachment_ids[0])
    assert attachment.sample_id == saved.draft.samples[0].id
    assert attachment.result_id == result.id
    assert result.value.source.file_id == attachment.id
    assert attachment.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()


def test_file_changed_after_preview_rejected(archive, tmp_path):
    source = tmp_path / 'data.txt'
    source.write_bytes(b'old')
    expected = hashlib.sha256(b'old').hexdigest()
    source.write_bytes(b'changed')
    with pytest.raises(ValueError, match='变化|修改'):
        archive.save(draft(), [Upload(source, 'file', expected_sha256=expected)])
    assert archive.store.list_experiments() == []
    assert list(archive.root.glob('*/original*')) == []


def test_bad_scope_traversal_or_symlink_escape_rejected(archive, tmp_path):
    source = tmp_path / 'data.txt'
    source.write_bytes(b'value')
    with pytest.raises(ValueError):
        archive.save(draft(), [Upload(source, 'file', sample_code='B1', result_index=0)])
    saved = archive.save(draft(), [Upload(source, 'file', role='source')])
    attachment_id = saved.draft.source_ids[0]
    import json
    with archive.store.knowledge.connection() as db:
        raw = json.loads(db.execute('SELECT payload FROM experiment_attachments WHERE id=?', (attachment_id,)).fetchone()[0])
        raw['relative_path'] = '../../outside.txt'
        db.execute('UPDATE experiment_attachments SET payload=? WHERE id=?', (json.dumps(raw), attachment_id))
    with pytest.raises(ValueError):
        archive.path_for(attachment_id)


def test_copy_or_sql_failure_removes_only_new_staging_files(archive, tmp_path, monkeypatch):
    source = tmp_path / 'data.txt'
    source.write_bytes(b'old')
    saved = archive.save(draft(), [Upload(source, 'file', role='source')])
    old_path = archive.path_for(saved.draft.source_ids[0])
    original = archive.store.save
    monkeypatch.setattr(archive.store, 'save', lambda *a, **k: (_ for _ in ()).throw(OSError('sql failed')))
    with pytest.raises(OSError):
        archive.save(draft(), [Upload(source, 'new')])
    assert old_path.read_bytes() == b'old'
    assert list(archive.root.glob('*/original*')) == [old_path]
    monkeypatch.setattr(archive.store, 'save', original)


def test_delete_failure_remains_visible_and_retries_on_reopen(archive, tmp_path, monkeypatch):
    source = tmp_path / 'data.txt'
    source.write_bytes(b'value')
    saved = archive.save(draft(), [Upload(source, 'file', role='source')])
    managed = archive.path_for(saved.draft.source_ids[0])
    unlink = Path.unlink
    def locked(path, *args, **kwargs):
        if path == managed:
            raise PermissionError('locked')
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', locked)
    with pytest.raises(OSError, match='文件'):
        archive.delete(saved.id)
    assert managed.exists()
    assert archive.store.list_experiments() == []
    with archive.store.knowledge.connection() as db:
        assert db.execute('SELECT count(*) FROM experiment_file_deletions').fetchone()[0] == 1
    monkeypatch.setattr(Path, 'unlink', unlink)
    assert archive.recover() == []
    assert not managed.exists()


def test_unconfirmed_cancel_leaves_no_records_or_files(archive, tmp_path):
    source = tmp_path / 'data.txt'
    source.write_bytes(b'value')
    record = draft()
    record.samples[0].results[0].value.confirmed = False
    with pytest.raises(ValueError):
        archive.save(record, [Upload(source, 'file')])
    assert archive.store.list_experiments() == []
    assert list(archive.root.glob('*/original*')) == []


def test_replaced_archive_root_cannot_write_outside_profile(archive, tmp_path, monkeypatch):
    external = tmp_path / 'external'
    external.mkdir()
    resolve = Path.resolve
    def relocated(path, *args, **kwargs):
        if path.is_relative_to(archive.root):
            return external / path.relative_to(archive.root)
        return resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'resolve', relocated)
    source = tmp_path / 'source.txt'
    source.write_bytes(b'content')
    with pytest.raises(ValueError):
        archive.save(draft(), [Upload(source, 'file')])
    assert list(external.iterdir()) == []
