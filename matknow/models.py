from dataclasses import dataclass, field


@dataclass(frozen=True)
class Chunk:
    text: str
    locator: str


@dataclass(frozen=True)
class Document:
    id: str
    filename: str
    chunk_count: int
    created_at: str
    category: str = ''
    summary: str = ''
    tags: tuple[str, ...] = ()
    group: str = ''
    member: str = ''
    role: str = ''


@dataclass(frozen=True)
class Evidence:
    id: str
    document_id: str
    filename: str
    locator: str
    text: str
    score: float = 0.0
    citation_number: int = 0


@dataclass
class Answer:
    id: str
    question: str
    answer: str
    status: str
    created_at: str
    sources: list[Evidence] = field(default_factory=list)
    unavailable_sources: int = 0
    agent_run: dict | None = None
