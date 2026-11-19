import hashlib
import json
import math
import re
import sqlite3
import unicodedata
from collections import Counter
from dataclasses import replace
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from matknow.documents import parse_file, parse_text
from matknow.models import Answer, Chunk, Document, Evidence


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tokenize(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).lower()
    normalized = re.sub(r"是什么|是多少|怎么样|请问|请告诉我|如何|是否|什么", " ", normalized)
    tokens = []
    for word in re.findall(r"[a-z0-9]+(?:[.+/-][a-z0-9]+)*|[\u4e00-\u9fff]+", normalized):
        if re.fullmatch(r"[\u4e00-\u9fff]+", word):
            if len(word) == 1:
                tokens.append(word)
            for width in (2, 3):
                tokens.extend(word[start:start + width] for start in range(len(word) - width + 1))
        elif word not in {"the", "is", "of", "a", "an", "what", "how", "in", "for", "and"}:
            tokens.append(word)
    return tokens


class KnowledgeBase:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL,
                    content_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL, locator TEXT NOT NULL, text TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chunks_document ON chunks(document_id);
                CREATE TABLE IF NOT EXISTS document_metadata (
                    document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS document_origins (
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    source_key TEXT NOT NULL, source_hash TEXT NOT NULL, member TEXT NOT NULL,
                    group_name TEXT NOT NULL, role TEXT NOT NULL, text_hash TEXT NOT NULL,
                    PRIMARY KEY(source_key,source_hash,member)
                );
                CREATE TABLE IF NOT EXISTS document_parts (
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    part_key TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(document_id,part_key)
                );
                CREATE TABLE IF NOT EXISTS experiment_drafts (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    payload TEXT NOT NULL, saved INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS history (
                    id TEXT PRIMARY KEY, question TEXT NOT NULL, answer TEXT NOT NULL,
                    status TEXT NOT NULL, source_ids TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_runs (
                    answer_id TEXT PRIMARY KEY REFERENCES history(id) ON DELETE CASCADE,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    answer_id TEXT PRIMARY KEY REFERENCES history(id) ON DELETE CASCADE,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL, scope TEXT,
                    UNIQUE(conversation_id,position)
                );
                CREATE TABLE IF NOT EXISTS conversation_titles (
                    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
                    source TEXT NOT NULL CHECK(source IN ('model','user'))
                );
                CREATE TABLE IF NOT EXISTS context_summaries (
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    scope TEXT NOT NULL, prefix_hash TEXT NOT NULL, turn_count INTEGER NOT NULL,
                    summary TEXT NOT NULL, PRIMARY KEY(conversation_id,scope)
                );
            """)
        from matknow.experiments.store import ExperimentStore
        ExperimentStore.initialize(self)

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def import_file(self, path: Path) -> Document:
        path = Path(path)
        return self._save_document(path.name, parse_file(path))

    def import_text(self, filename: str, text: str) -> Document:
        return self._save_document(filename, parse_text(text))

    def _save_document(self, filename: str, chunks: list[Chunk], *, raw_hash=None) -> Document:
        filename = Path(filename.strip()).name
        if not filename:
            raise ValueError("请给资料填写名称。")
        digest = hashlib.sha256((filename + "\0" + json.dumps([(item.locator, item.text) for item in chunks], ensure_ascii=False) + ("\0" + raw_hash if raw_hash else "")).encode("utf-8")).hexdigest()
        created_at = utc_now()
        document_id = str(uuid4())
        with self.connection() as connection:
            existing = connection.execute("SELECT id, filename, created_at FROM documents WHERE content_hash=?", (digest,)).fetchone()
            if existing:
                return Document(existing["id"], existing["filename"], len(chunks), existing["created_at"])
            connection.execute("INSERT INTO documents VALUES (?, ?, ?, ?)", (document_id, filename, digest, created_at))
            connection.executemany("INSERT INTO chunks VALUES (?, ?, ?, ?, ?)", [(str(uuid4()), document_id, position, chunk.locator, chunk.text) for position, chunk in enumerate(chunks)])
        return Document(document_id, filename, len(chunks), created_at)

    def list_documents(self) -> list[Document]:
        with self.connection() as connection:
            rows = connection.execute("SELECT d.id, d.filename, d.created_at, count(c.id) AS chunk_count,m.payload FROM documents d JOIN chunks c ON d.id=c.document_id LEFT JOIN document_metadata m ON m.document_id=d.id GROUP BY d.id ORDER BY d.created_at, d.rowid").fetchall()
            origins={}
            for origin in connection.execute('SELECT document_id,group_name,member,role FROM document_origins ORDER BY rowid'):
                origins.setdefault(origin['document_id'],dict(origin))
        documents=[]
        for row in rows:
            meta=json.loads(row['payload']) if row['payload'] else {}
            origin=origins.get(row['id'],{})
            documents.append(Document(row['id'],row['filename'],row['chunk_count'],row['created_at'],meta.get('category',''),meta.get('summary',''),tuple(meta.get('tags',[])),origin.get('group_name',''),origin.get('member',''),origin.get('role','')))
        return documents

    def document_origins(self,document_id):
        with self.connection() as db:
            return [dict(row) for row in db.execute('SELECT member,group_name,role FROM document_origins WHERE document_id=? ORDER BY rowid',(document_id,))]

    def document_part(self,document_id,part_key,payload=None):
        with self.connection() as db:
            if payload is not None:
                db.execute('INSERT OR REPLACE INTO document_parts VALUES (?,?,?)',(document_id,part_key,json.dumps(payload,ensure_ascii=False)))
                return payload
            row=db.execute('SELECT payload FROM document_parts WHERE document_id=? AND part_key=?',(document_id,part_key)).fetchone()
            return json.loads(row['payload']) if row else None

    def import_origin(self,filename,chunks,*,source_key,source_hash,member,group,role,content_hash=None):
        text_hash=content_hash or hashlib.sha256(json.dumps([c.text for c in chunks],ensure_ascii=False).encode('utf8')).hexdigest()
        with self.connection() as db:
            row=db.execute('SELECT d.id FROM documents d JOIN document_origins o ON o.document_id=d.id WHERE d.filename=? AND o.group_name=? AND o.text_hash=? LIMIT 1',(filename,group,text_hash)).fetchone()
        document_id=row['id'] if row else self._save_document(filename,chunks,raw_hash=content_hash).id
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO document_origins VALUES (?,?,?,?,?,?,?)',(document_id,source_key,source_hash,member,group,role,text_hash))
        return next(d for d in self.list_documents() if d.id==document_id)

    def save_experiment_draft(self,document_id,payload):
        identifier=str(uuid4())
        with self.connection() as db:
            db.execute('INSERT INTO experiment_drafts(id,document_id,payload) VALUES (?,?,?)',(identifier,document_id,json.dumps(payload,ensure_ascii=False,allow_nan=False)))
        return identifier

    def experiment_draft(self,identifier):
        from matknow.experiments.models import valid_id
        valid_id(identifier)
        with self.connection() as db:
            row=db.execute('SELECT document_id,payload,saved FROM experiment_drafts WHERE id=?',(identifier,)).fetchone()
        if row is None:raise ValueError('实验草稿或来源已删除，请重新生成。')
        if row['saved']:raise ValueError('此实验草稿已保存，请在实验记录中查看。')
        return {'document_id':row['document_id'],**json.loads(row['payload'])}

    def document_sources(self,document_id):
        if not isinstance(document_id,str):raise ValueError('资料编号无效。')
        with self.connection() as db:
            rows=db.execute('SELECT c.id,c.document_id,d.filename,c.locator,c.text FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.id=? ORDER BY c.position',(document_id,)).fetchall()
        if not rows:raise ValueError('资料已删除或不属于当前方向。')
        return [Evidence(row['id'],row['document_id'],row['filename'],row['locator'],row['text']) for row in rows]

    def save_document_metadata(self,document_id,metadata):
        with self.connection() as db:
            if not db.execute('SELECT 1 FROM documents WHERE id=?',(document_id,)).fetchone():raise ValueError('资料已删除。')
            db.execute('INSERT INTO document_metadata VALUES (?,?) ON CONFLICT(document_id) DO UPDATE SET payload=excluded.payload',(document_id,json.dumps(metadata,ensure_ascii=False,allow_nan=False)))

    def delete_document(self, document_id: str):
        with self.connection() as connection:
            connection.execute("DELETE FROM documents WHERE id=?", (document_id,))

    def get_evidence(self, source_id: str) -> Evidence | None:
        return self.get_evidence_many([source_id]).get(source_id)

    def get_evidence_many(self, source_ids: list[str]) -> dict[str, Evidence]:
        result = {}
        document_ids = list(dict.fromkeys(i for i in source_ids if not i.startswith('exp:')))
        with self.connection() as connection:
            for start in range(0, len(document_ids), 500):
                ids = document_ids[start:start + 500]
                rows = connection.execute("SELECT c.id, c.document_id, d.filename, c.locator, c.text FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.id IN (" + ','.join('?' for _ in ids) + ')', ids).fetchall()
                result.update({r['id']: Evidence(**dict(r)) for r in rows})
        experiment_ids = [i for i in source_ids if i.startswith('exp:')]
        if experiment_ids:
            from matknow.experiments.store import ExperimentStore
            result.update(ExperimentStore(self).resolve_many(experiment_ids))
        return result

    def search(self, question: str, document_ids: list[str] | None = None, limit: int = 6) -> list[Evidence]:
        if document_ids == []:
            return []
        query_tokens = set(tokenize(question))
        if not query_tokens:
            return []
        sql = "SELECT c.id, c.document_id, d.filename, c.locator, c.text FROM chunks c JOIN documents d ON d.id=c.document_id"
        parameters = []
        if document_ids is not None:
            sql += " WHERE c.document_id IN (" + ",".join("?" for _ in document_ids) + ")"
            parameters = document_ids
        sql += " ORDER BY d.rowid, c.position"
        with self.connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        if not rows:
            return []
        # shortcut: small-library BM25 scans all selected chunks; add a persisted index when real corpus latency warrants it.
        frequencies = [Counter(tokenize(row["text"])) for row in rows]
        document_frequency = Counter(token for frequency in frequencies for token in frequency)
        average_length = sum(sum(frequency.values()) for frequency in frequencies) / len(rows) or 1
        hits = []
        for row, frequency in zip(rows, frequencies):
            score = 0.0
            length = sum(frequency.values())
            for token in query_tokens.intersection(frequency):
                inverse_frequency = math.log(1 + (len(rows) - document_frequency[token] + 0.5) / (document_frequency[token] + 0.5))
                count = frequency[token]
                score += inverse_frequency * count * 2.2 / (count + 1.2 * (0.25 + 0.75 * length / average_length))
            if score > 0:
                hits.append(Evidence(**dict(row), score=round(score, 6)))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:limit]

    def save_answer(self, answer: Answer, *, connection=None):
        # Store IDs, not copied evidence: deleting a document also invalidates its historical source text.
        values=(answer.id, answer.question, answer.answer, answer.status, json.dumps([source.id for source in answer.sources]), answer.created_at)
        if connection is not None:
            connection.execute('INSERT INTO history VALUES (?, ?, ?, ?, ?, ?)',values)
            if answer.agent_run is not None:
                connection.execute('INSERT INTO agent_runs VALUES (?, ?)', (answer.id, json.dumps(answer.agent_run, ensure_ascii=False, allow_nan=False)))
        else:
            with self.connection() as db:self.save_answer(answer, connection=db)

    def list_history(self, limit: int = 50, *, answer_ids=None) -> list[Answer]:
        if answer_ids == []:return []
        with self.connection() as connection:
            sql='SELECT h.*,a.payload AS agent_payload FROM history h LEFT JOIN agent_runs a ON a.answer_id=h.id';params=[]
            if answer_ids is not None:
                sql+=' WHERE h.id IN ('+','.join('?' for _ in answer_ids)+')';params=list(answer_ids)
            rows = connection.execute(sql+' ORDER BY h.rowid DESC LIMIT ?', [*params,limit]).fetchall()
        history = []
        resolved = self.get_evidence_many([i for row in rows for i in json.loads(row['source_ids'])])
        for row in rows:
            source_ids = json.loads(row["source_ids"])
            sources = [replace(evidence, citation_number=number) for number, source_id in enumerate(source_ids, 1) if (evidence := resolved.get(source_id)) is not None]
            history.append(Answer(id=row["id"], question=row["question"], answer=row["answer"], status=row["status"], created_at=row["created_at"], sources=sources, unavailable_sources=len(source_ids) - len(sources), agent_run=json.loads(row['agent_payload']) if row['agent_payload'] else None))
        return history
