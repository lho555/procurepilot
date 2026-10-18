from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.knowledge import Document, KnowledgeStore, get_knowledge_store

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
Store = Annotated[KnowledgeStore, Depends(get_knowledge_store)]


class KnowledgeQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: Annotated[str, Field(min_length=2, max_length=1000)]
    top_k: Annotated[int, Field(strict=True, ge=1, le=5)] = 3


class Citation(BaseModel):
    number: int
    chunk_id: str
    document_id: str
    title: str
    filename: str
    section: str
    start_line: int
    end_line: int
    excerpt: str
    score: float
    source_sha256: str


class KnowledgeAnswer(BaseModel):
    question: str
    found: bool
    answer: str
    method: Literal["extractive"] = "extractive"
    index_revision: str
    citations: list[Citation]


@router.get("/status")
def status(store: Store):
    index = store.load()
    return {"ready": True, "documents": len(index.documents), "chunks": len(index.chunks), "algorithm": index.algorithm, "revision": index.revision}


@router.get("/documents")
def list_documents(store: Store):
    return [{"id": doc.id, "title": doc.title, "filename": doc.filename, "sha256": doc.sha256} for doc in store.load().documents]


@router.get("/documents/{document_id}", response_model=Document)
def document(document_id: str, store: Store):
    match = next((doc for doc in store.load().documents if doc.id == document_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="知识文档不存在")
    return match


@router.post("/ask", response_model=KnowledgeAnswer)
def ask(body: KnowledgeQuestion, store: Store) -> KnowledgeAnswer:
    index, matches = store.search(body.question, body.top_k)
    documents = {doc.id: doc for doc in index.documents}
    citations = []
    for number, (chunk, score) in enumerate(matches, 1):
        doc = documents[chunk.document_id]
        citations.append(Citation(
            number=number, chunk_id=chunk.id, document_id=doc.id, title=doc.title, filename=doc.filename,
            section=chunk.section, start_line=chunk.start_line, end_line=chunk.end_line,
            excerpt=chunk.text, score=round(score, 6), source_sha256=doc.sha256,
        ))
    answer = ("根据以下合成制度原文摘录：\n\n" + "\n\n".join(f"[{citation.number}] {citation.excerpt}" for citation in citations)
              if citations else "未找到依据，请补充问题或交由人工核实。")
    return KnowledgeAnswer(question=body.question, found=bool(citations), answer=answer, index_revision=index.revision, citations=citations)
