"""Local lexical vector retrieval with immutable source snapshots and citations."""
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

from pydantic import BaseModel, ConfigDict, Field

from app.config import PROJECT_ROOT

ALGORITHM = "tfidf-char-ngram-v1"
MAX_CHARS = 600
MAX_DOCUMENT_BYTES = 100_000
MAX_DOCUMENTS = 50
MIN_SCORE = 0.06
STOP_WORDS = {"什么", "如何", "是否", "怎么", "哪些", "多少", "可以", "需要", "必须", "不能", "合成", "演示"}


class KnowledgeError(Exception):
    pass


class Document(BaseModel):
    id: str
    title: str
    filename: str
    sha256: str
    content: str


class Chunk(BaseModel):
    id: str
    document_id: str
    section: str
    start_line: int
    end_line: int
    text: str
    vector: dict[str, float] = Field(default_factory=dict)


class Index(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    algorithm: str = ALGORITHM
    revision: str
    imported_at: str
    documents: list[Document]
    chunks: list[Chunk]
    idf: dict[str, float]


def tokens(text: str) -> Counter[str]:
    result: Counter[str] = Counter()
    for part in re.findall(r"[\u4e00-\u9fff]+|[a-z0-9]+", text.lower()):
        if re.fullmatch(r"[a-z0-9]+", part):
            result[part] += 1
        elif len(part) == 1:
            result[part] += 1
        else:
            for size in (2, 3):
                for start in range(len(part) - size + 1):
                    term = part[start:start + size]
                    if term not in STOP_WORDS:
                        result[term] += 1
    return result


def vectorize(text: str, idf: dict[str, float], include_unknown: bool = False) -> dict[str, float]:
    unknown_weight = max(idf.values(), default=1)
    vector = {term: (1 + math.log(count)) * idf.get(term, unknown_weight)
              for term, count in tokens(text).items() if include_unknown or term in idf}
    norm = math.sqrt(sum(value * value for value in vector.values()))
    return {term: value / norm for term, value in vector.items()} if norm else {}


def split_document(document: Document) -> list[Chunk]:
    chunks: list[Chunk] = []
    section = document.title
    pending: list[tuple[int, str]] = []

    def flush() -> None:
        if not pending:
            return
        text = "\n".join(line for _, line in pending).strip()
        if text:
            start, end = pending[0][0], pending[-1][0]
            fingerprint = f"{document.id}:{start}:{len(chunks)}:{text}"
            chunks.append(Chunk(
                id=hashlib.sha256(fingerprint.encode()).hexdigest()[:24],
                document_id=document.id, section=section, start_line=start, end_line=end, text=text,
            ))
        pending.clear()

    for number, line in enumerate(document.content.splitlines(), 1):
        if re.match(r"^#{1,6}\s", line):
            flush()
            section = line.lstrip("#").strip()
            continue
        if len(line) > MAX_CHARS:
            flush()
            for offset in range(0, len(line), MAX_CHARS):
                pending.append((number, line[offset:offset + MAX_CHARS]))
                flush()
            continue
        if pending and sum(len(value) + 1 for _, value in pending) + len(line) > MAX_CHARS:
            flush()
        if line.strip() or pending:
            pending.append((number, line))
    flush()
    return chunks


def build_index(source: Path) -> Index:
    source = source.resolve()
    files = sorted(source.glob("*.md"))
    if not files or len(files) > MAX_DOCUMENTS:
        raise KnowledgeError("文档目录需要包含 1～50 个 Markdown 文件")
    documents = []
    chunks = []
    for file in files:
        if file.is_symlink() or not file.is_file() or file.resolve().parent != source:
            raise KnowledgeError("文档必须是目录内的普通文件，不允许符号链接")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", file.stem):
            raise KnowledgeError("文档文件名只能使用小写字母、数字、下划线和连字符")
        if file.stat().st_size > MAX_DOCUMENT_BYTES:
            raise KnowledgeError("单份文档不能超过 100 KB")
        content = file.read_text(encoding="utf-8-sig")
        if not content.strip():
            raise KnowledgeError("不允许导入空文档")
        title = next((line[2:].strip() for line in content.splitlines() if line.startswith("# ")), file.stem)
        document = Document(
            id=file.stem, filename=file.name, title=title, content=content,
            sha256=hashlib.sha256(content.encode()).hexdigest(),
        )
        document_chunks = split_document(document)
        if not document_chunks:
            raise KnowledgeError("文档必须包含正文")
        documents.append(document)
        chunks.extend(document_chunks)
    frequencies = Counter(term for chunk in chunks for term in tokens(chunk.section + "\n" + chunk.text).keys())
    idf = {term: math.log((1 + len(chunks)) / (1 + count)) + 1 for term, count in frequencies.items()}
    for chunk in chunks:
        chunk.vector = vectorize(chunk.section + "\n" + chunk.text, idf)
    revision = hashlib.sha256("\n".join(f"{doc.id}:{doc.sha256}" for doc in documents).encode()).hexdigest()
    return Index(
        revision=revision, imported_at=datetime.now(timezone.utc).isoformat(),
        documents=documents, chunks=chunks, idf=idf,
    )


def save_index(index: Index, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(index.model_dump_json(indent=2))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


class KnowledgeStore:
    def __init__(self, path: Path):
        self.path = path
        self._stamp = None
        self._index: Index | None = None

    def load(self) -> Index:
        try:
            stat = self.path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
            if stamp != self._stamp:
                candidate = Index.model_validate_json(self.path.read_text(encoding="utf-8"))
                if candidate.version != 1 or candidate.algorithm != ALGORITHM:
                    raise ValueError("unsupported index")
                documents = {doc.id: doc for doc in candidate.documents}
                if not documents or len(documents) != len(candidate.documents):
                    raise ValueError("invalid documents")
                for doc in documents.values():
                    if hashlib.sha256(doc.content.encode()).hexdigest() != doc.sha256:
                        raise ValueError("source checksum mismatch")
                expected_revision = hashlib.sha256("\n".join(f"{doc.id}:{doc.sha256}" for doc in candidate.documents).encode()).hexdigest()
                if candidate.revision != expected_revision:
                    raise ValueError("index revision mismatch")
                for chunk in candidate.chunks:
                    doc = documents.get(chunk.document_id)
                    if doc is None or not 1 <= chunk.start_line <= chunk.end_line <= len(doc.content.splitlines()):
                        raise ValueError("invalid citation lines")
                    source = "\n".join(doc.content.splitlines()[chunk.start_line - 1:chunk.end_line])
                    if not chunk.text or chunk.text not in source or len(chunk.text) > MAX_CHARS:
                        raise ValueError("citation text mismatch")
                self._index, self._stamp = candidate, stamp
            if self._index is None:
                raise ValueError("index unavailable")
            return self._index
        except (OSError, ValueError) as error:
            raise KnowledgeError("知识索引不可用，请确认文档导入已成功完成") from error

    def search(self, question: str, top_k: int) -> tuple[Index, list[tuple[Chunk, float]]]:
        index = self.load()
        # Keep unknown terms in the norm: one shared word must not make an unrelated question look relevant.
        query = vectorize(question, index.idf, include_unknown=True)
        ranked = []
        for chunk in index.chunks:
            score = sum(value * chunk.vector.get(term, 0) for term, value in query.items())
            if score >= MIN_SCORE:
                ranked.append((chunk, score))
        ranked.sort(key=lambda match: (-match[1], match[0].id))
        return index, ranked[:top_k]


def index_path() -> Path:
    path = Path(os.getenv("RAG_INDEX_PATH", ".local/rag/index.json"))
    path = (PROJECT_ROOT / path).resolve()
    if not path.is_relative_to(PROJECT_ROOT):
        raise KnowledgeError("知识索引路径必须位于项目工作区内")
    return path


def get_knowledge_store() -> KnowledgeStore:
    # Cache is keyed by file stat in the store; a confirmed reimport is visible without restart.
    return _store_for_path(index_path())


@lru_cache(maxsize=4)
def _store_for_path(path: Path) -> KnowledgeStore:
    return KnowledgeStore(path)
