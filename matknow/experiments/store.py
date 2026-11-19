from copy import deepcopy
from dataclasses import asdict
import json
from uuid import uuid4

from matknow.knowledge import utc_now, tokenize
from matknow.models import Evidence
from .models import (Attachment, ExperimentDraft, ExperimentSnapshot, ExperimentSummary,
                     draft_from_dict, valid_id, validate_draft, value_provenance, chemistry_text)


def dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class ExperimentStore:
    def __init__(self, knowledge):
        self.knowledge = knowledge
        with knowledge.connection() as db:
            row = db.execute('SELECT version FROM experiment_meta').fetchone()
        if row is None or row[0] != 2:
            raise ValueError('实验档案数据库版本不支持，请保留原库。')

    @staticmethod
    def initialize(knowledge):
        with knowledge.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS experiment_meta (version INTEGER NOT NULL)')
            row = db.execute('SELECT version FROM experiment_meta').fetchone()
            if row and row[0] not in (1,2):
                raise ValueError('实验档案数据库版本不支持，请保留原库。')
            statements = [
                'CREATE TABLE IF NOT EXISTS experiments (id TEXT PRIMARY KEY, title TEXT NOT NULL, date TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS samples (id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE, code TEXT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS results (id TEXT PRIMARY KEY, sample_id TEXT NOT NULL REFERENCES samples(id) ON DELETE CASCADE, experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE)',
                'CREATE TABLE IF NOT EXISTS experiment_revisions (experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE, revision INTEGER NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(experiment_id, revision))',
                'CREATE TABLE IF NOT EXISTS experiment_attachments (id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE, payload TEXT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS experiment_evidence (id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE, revision INTEGER NOT NULL, sample_id TEXT NOT NULL, result_id TEXT, filename TEXT NOT NULL, locator TEXT NOT NULL, text TEXT NOT NULL)',
                'CREATE INDEX IF NOT EXISTS experiment_evidence_scope ON experiment_evidence(experiment_id,revision,sample_id)',
                'CREATE TABLE IF NOT EXISTS experiment_file_deletions (id TEXT PRIMARY KEY, relative_path TEXT NOT NULL)',
            ]
            for sql in statements:
                db.execute(sql)
            if row is None:
                db.execute('INSERT INTO experiment_meta VALUES (2)')
            elif row[0]==1:
                db.execute('UPDATE experiment_meta SET version=2')

    def save(self, draft: ExperimentDraft, *, experiment_id=None, expected_revision=None, attachments=None,draft_id=None):
        validate_draft(draft)
        draft = deepcopy(draft)
        experiment_id = valid_id(experiment_id) if experiment_id else str(uuid4())
        for sample in draft.samples:
            sample.code = sample.code.strip()
            sample.id = sample.id or str(uuid4())
            for result in sample.results:
                result.id = result.id or str(uuid4())
        with self.knowledge.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if draft_id is not None:
                row=db.execute('SELECT saved FROM experiment_drafts WHERE id=?',(valid_id(draft_id),)).fetchone()
                if row is None or row['saved']:raise ValueError('草稿已保存或来源已删除，请重新核对。')
                db.execute('UPDATE experiment_drafts SET saved=1 WHERE id=?',(draft_id,))
            previous = db.execute('SELECT revision FROM experiments WHERE id=?', (experiment_id,)).fetchone()
            if previous and expected_revision != previous[0]:
                raise ValueError('实验记录已更新，请重新载入后保存。')
            if previous is None and expected_revision is not None:
                raise ValueError('实验不存在或已删除。')
            for table, entities in [('samples', draft.samples), ('results', [r for s in draft.samples for r in s.results])]:
                for entity in entities:
                    owner = db.execute(f'SELECT experiment_id FROM {table} WHERE id=?', (entity.id,)).fetchone()
                    if owner and owner[0] != experiment_id:
                        raise ValueError('样品或结果不属于当前实验。')
                    # Removed IDs remain owned by their original revision, even after current-row removal.
                    old_owner = db.execute('SELECT experiment_id FROM experiment_evidence WHERE sample_id=? OR result_id=? LIMIT 1', (entity.id, entity.id)).fetchone()
                    if old_owner and old_owner[0] != experiment_id:
                        raise ValueError('历史记录编号属于其他实验。')
            available = {row['id']: Attachment(**json.loads(row['payload'])) for row in db.execute('SELECT * FROM experiment_attachments WHERE experiment_id=?', (experiment_id,))}
            sample_ids = {s.id for s in draft.samples}
            result_ids = {r.id for s in draft.samples for r in s.results}
            for item in attachments or []:
                valid_id(item.id)
                if item.experiment_id != experiment_id or (item.sample_id and item.sample_id not in sample_ids) or (item.result_id and item.result_id not in result_ids):
                    raise ValueError('附件关联不属于当前实验。')
                if db.execute('SELECT 1 FROM experiment_attachments WHERE id=?', (item.id,)).fetchone():
                    raise ValueError('附件编号已存在。')
                available[item.id] = item
            refs = list(draft.source_ids)
            for value in draft.common.values():
                if value.source:
                    refs.append(value.source.file_id)
            for sample in draft.samples:
                for value in [*sample.conditions.values(), *[r.value for r in sample.results]]:
                    if value.source:
                        refs.append(value.source.file_id)
                for result in sample.results:
                    refs.extend(result.attachment_ids)
                    if result.data.get('attachment_id'):
                        refs.append(result.data['attachment_id'])
            if any(ref not in available for ref in refs):
                raise ValueError('附件或原始文件不属于当前实验。')
            revision, created = (previous[0] + 1 if previous else 1), utc_now()
            db.execute('INSERT INTO experiments VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,date=excluded.date,revision=excluded.revision', (experiment_id, draft.title.strip(), draft.date, revision, created))
            for item in attachments or []:
                db.execute('INSERT INTO experiment_attachments VALUES (?,?,?)', (item.id, experiment_id, dump(asdict(item))))
            db.execute('DELETE FROM results WHERE experiment_id=?', (experiment_id,))
            db.execute('DELETE FROM samples WHERE experiment_id=?', (experiment_id,))
            for sample in draft.samples:
                db.execute('INSERT INTO samples VALUES (?,?,?)', (sample.id, experiment_id, sample.code))
                for result in sample.results:
                    db.execute('INSERT INTO results VALUES (?,?,?)', (result.id, sample.id, experiment_id))
            db.execute('INSERT INTO experiment_revisions VALUES (?,?,?,?)', (experiment_id, revision, dump(asdict(draft)), created))
            for sample in draft.samples:
                conditions = {**draft.common, **sample.conditions}
                base = f'实验：{draft.title}；日期：{draft.date}；样品：{sample.code}\n'
                base += '\n'.join(f'{k}：{v.text} {v.unit}'.rstrip() + f'（{value_provenance(v)}）' for k, v in conditions.items())
                base += '\n备注：' + draft.notes
                detail=chemistry_text(draft.chemistry,sample.code)
                if detail:base+='\n'+detail
                for result in [None, *sample.results]:
                    text = base
                    if result:
                        text += f'\n结果：{result.metric} = {result.value.text} {result.value.unit}\n测试方法：{result.method or "未记录"}；测试时间：{result.measured_at or "未记录"}\n测试条件：{dump(result.test_conditions)}\n观察/备注：{result.notes}'
                        text += '\n数值/观察来源：' + value_provenance(result.value)
                        text += '\n附件：' + '、'.join(available[i].filename for i in result.attachment_ids)
                    db.execute('INSERT INTO experiment_evidence VALUES (?,?,?,?,?,?,?,?)', ('exp:' + str(uuid4()), experiment_id, revision, sample.id, result.id if result else None, f'{draft.date} {draft.title} / {sample.code}', f'版本 {revision}' + (f' · {result.metric}' if result else ' · 实验条件'), text))
        return ExperimentSnapshot(experiment_id, revision, draft, created)

    def get(self, experiment_id, revision=None):
        valid_id(experiment_id)
        with self.knowledge.connection() as db:
            if revision is None:
                row = db.execute('SELECT revision FROM experiments WHERE id=?', (experiment_id,)).fetchone()
                if row is None:
                    raise ValueError('实验不存在或已删除。')
                revision = row[0]
            row = db.execute('SELECT * FROM experiment_revisions WHERE experiment_id=? AND revision=?', (experiment_id, revision)).fetchone()
        if row is None:
            raise ValueError('找不到指定实验版本。')
        return ExperimentSnapshot(experiment_id, revision, draft_from_dict(json.loads(row['payload'])), row['created_at'])

    def list_experiments(self, query='', *, offset=0, limit=50):
        if offset < 0 or not 1 <= limit <= 200:
            raise ValueError('分页范围无效。')
        with self.knowledge.connection() as db:
            rows = db.execute('SELECT e.*,count(s.id) AS sample_count FROM experiments e LEFT JOIN samples s ON s.experiment_id=e.id WHERE e.title LIKE ? OR e.date LIKE ? GROUP BY e.id ORDER BY e.date DESC,e.rowid DESC LIMIT ? OFFSET ?', ('%' + query + '%', '%' + query + '%', limit, offset)).fetchall()
        return [ExperimentSummary(r['id'], r['title'], r['date'], r['revision'], r['sample_count']) for r in rows]

    def samples(self, sample_ids):
        if len(set(sample_ids)) != len(sample_ids):
            raise ValueError('样品选择不能重复。')
        snapshots, found = {}, {}
        with self.knowledge.connection() as db:
            for sample_id in sample_ids:
                valid_id(sample_id)
                row = db.execute('SELECT experiment_id FROM samples WHERE id=?', (sample_id,)).fetchone()
                if row is None:
                    raise ValueError('所选样品不存在或不属于当前方向。')
                found[sample_id] = row[0]
        for experiment_id in set(found.values()):
            snapshots[experiment_id] = self.get(experiment_id)
        return [(snapshots[found[i]], next(s for s in snapshots[found[i]].draft.samples if s.id == i)) for i in sample_ids]

    def resolve_many(self, source_ids):
        if not source_ids:
            return {}
        result = {}
        with self.knowledge.connection() as db:
            for start in range(0, len(source_ids), 500):
                ids = source_ids[start:start + 500]
                rows = db.execute('SELECT x.*,e.revision AS current_revision FROM experiment_evidence x JOIN experiments e ON e.id=x.experiment_id WHERE x.id IN (' + ','.join('?' for _ in ids) + ')', ids).fetchall()
                for row in rows:
                    locator = row['locator'] + (' · 记录已更新' if row['revision'] != row['current_revision'] else '')
                    result[row['id']] = Evidence(row['id'], row['experiment_id'], row['filename'], locator, row['text'])
        return result

    def evidence_rows(self, sample_ids=None):
        if sample_ids == []:
            return []
        if sample_ids is not None:
            self.samples(sample_ids)
        sql = 'SELECT x.* FROM experiment_evidence x JOIN experiments e ON e.id=x.experiment_id AND e.revision=x.revision'
        params = []
        if sample_ids is not None:
            sql += ' WHERE x.sample_id IN (' + ','.join('?' for _ in sample_ids) + ')'
            params = sample_ids
        with self.knowledge.connection() as db:
            return [dict(r) for r in db.execute(sql + ' ORDER BY e.date DESC,x.rowid', params)]

    def search(self, question, sample_ids=None, limit=6):
        tokens = set(tokenize(question))
        scored = []
        for row in self.evidence_rows(sample_ids):
            score = len(tokens.intersection(tokenize(row['text'])))
            if score:
                scored.append(Evidence(row['id'], row['experiment_id'], row['filename'], row['locator'], row['text'], score))
        return sorted(scored, key=lambda e: e.score, reverse=True)[:limit]

    def delete_rows(self, experiment_id):
        self.get(experiment_id)
        with self.knowledge.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            attachments = [Attachment(**json.loads(r[0])) for r in db.execute('SELECT payload FROM experiment_attachments WHERE experiment_id=?', (experiment_id,))]
            db.executemany('INSERT OR REPLACE INTO experiment_file_deletions VALUES (?,?)', [(a.id, a.relative_path) for a in attachments])
            db.execute('DELETE FROM experiments WHERE id=?', (experiment_id,))
        return attachments

