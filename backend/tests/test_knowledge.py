import hashlib
import json

from fastapi.testclient import TestClient
import pytest

from app.config import PROJECT_ROOT
from app.knowledge import ALGORITHM, MAX_CHARS, Document, KnowledgeError, KnowledgeStore, build_index, get_knowledge_store, save_index, split_document
from app.main import app


@pytest.fixture
def knowledge(tmp_path):
    index = build_index(PROJECT_ROOT / "sample_data/policies")
    path = tmp_path / "index.json"
    save_index(index, path)
    return KnowledgeStore(path)


@pytest.fixture
def knowledge_client(knowledge):
    app.dependency_overrides[get_knowledge_store] = lambda: knowledge
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_knowledge_store, None)


@pytest.mark.parametrize("question,expected_document,expected_text", [
    ("紧急采购超过 20000 元需要谁审批？", "procurement_policy", "共同审批"),
    ("紧急采购金额小于等于 20000 元", "procurement_policy", "采购经理审批"),
    ("缺货与供应商延期应该如何核查？", "emergency_sop", "可用库存"),
    ("供应商准入需要核实哪些资质？", "supplier_rules", "营业资质"),
    ("审批拒绝后还能采购吗？", "emergency_sop", "停止采购动作"),
])
def test_retrieval_has_exact_grounded_citations(knowledge_client, question, expected_document, expected_text):
    response = knowledge_client.post("/knowledge/ask", json={"question": question})
    assert response.status_code == 200
    body = response.json()
    assert body["found"] is True
    assert any(c["document_id"] == expected_document and expected_text in c["excerpt"] for c in body["citations"])
    for citation in body["citations"]:
        source = knowledge_client.get(f"/knowledge/documents/{citation['document_id']}").json()
        assert hashlib.sha256(source["content"].encode()).hexdigest() == citation["source_sha256"]
        text = "\n".join(source["content"].splitlines()[citation["start_line"] - 1:citation["end_line"]])
        assert citation["excerpt"] in text
        assert f"[{citation['number']}] {citation['excerpt']}" in body["answer"]


def test_unrelated_question_abstains(knowledge_client):
    body = knowledge_client.post("/knowledge/ask", json={"question": "火星移民的氧气价格"}).json()
    assert body["found"] is False
    assert body["citations"] == []
    assert "未找到依据" in body["answer"]


@pytest.mark.parametrize("body", [
    {"question": " "}, {"question": "a"}, {"question": "x" * 1001},
    {"question": "采购审批", "top_k": 0}, {"question": "采购审批", "top_k": 6},
    {"question": "采购审批", "top_k": True}, {"question": "采购审批", "top_k": "3"},
    {"question": "采购审批", "source_path": "../../private"},
])
def test_invalid_knowledge_request(knowledge_client, body):
    assert knowledge_client.post("/knowledge/ask", json=body).status_code == 422


def test_document_lookup_is_not_file_access(knowledge_client):
    assert knowledge_client.get("/knowledge/documents/not-found").status_code == 404
    body = knowledge_client.get("/knowledge/documents").json()
    assert len(body) == 3
    status = knowledge_client.get("/knowledge/status").json()
    assert status["ready"] is True and status["algorithm"] == ALGORITHM


def test_missing_and_corrupt_index_return_503(knowledge_client, knowledge):
    knowledge.path.unlink()
    assert knowledge_client.post("/knowledge/ask", json={"question": "采购审批"}).status_code == 503
    assert knowledge_client.get("/health").status_code == 200
    knowledge.path.write_text("invalid-json", encoding="utf-8")
    response = knowledge_client.get("/knowledge/status")
    assert response.status_code == 503
    assert str(knowledge.path) not in response.text


def test_repeat_import_preserves_revision_and_rankings(knowledge):
    first, first_matches = knowledge.search("紧急采购审批金额", 3)
    second = build_index(PROJECT_ROOT / "sample_data/policies")
    save_index(second, knowledge.path)
    after, second_matches = knowledge.search("紧急采购审批金额", 3)
    assert first.revision == after.revision
    assert [(chunk.id, score) for chunk, score in first_matches] == [(chunk.id, score) for chunk, score in second_matches]


def test_source_snapshot_is_preserved_until_confirmed_reimport(tmp_path):
    source = tmp_path / "docs"
    source.mkdir()
    document = source / "policy.md"
    document.write_text("# 制度\n\n## 采购审批\n采购经理审批。\n", encoding="utf-8")
    path = tmp_path / "index.json"
    save_index(build_index(source), path)
    store = KnowledgeStore(path)
    original = store.load()
    document.write_text("# 制度\n\n## 采购审批\n财务负责人审批。\n", encoding="utf-8")
    assert "采购经理" in store.load().documents[0].content
    save_index(build_index(source), path)
    assert store.load().revision != original.revision
    assert "财务负责人" in store.load().documents[0].content


def test_atomic_write_failure_keeps_previous_index(knowledge, monkeypatch):
    before = knowledge.path.read_bytes()
    def failed_replace(*_args):
        raise OSError("synthetic write failure")
    monkeypatch.setattr("app.knowledge.os.replace", failed_replace)
    with pytest.raises(OSError):
        save_index(knowledge.load(), knowledge.path)
    assert knowledge.path.read_bytes() == before
    assert list(knowledge.path.parent.iterdir()) == [knowledge.path]


def test_empty_document_is_rejected(tmp_path):
    (tmp_path / "empty.md").write_text(" ", encoding="utf-8")
    with pytest.raises(KnowledgeError):
        build_index(tmp_path)


def test_valid_json_with_tampered_source_is_rejected(knowledge_client, knowledge):
    body = json.loads(knowledge.path.read_text(encoding="utf-8"))
    body["documents"][0]["content"] += "\n未经确认的新规则。"
    knowledge.path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    assert knowledge_client.get("/knowledge/status").status_code == 503


def test_chunks_respect_limits_and_source_lines():
    content = "# 制度\n\n## 长段落\n" + "采购审批" * 400 + "\n\n## 下一节\n新的审批记录。\n"
    doc = Document(id="test", title="制度", filename="test.md", sha256="test", content=content)
    chunks = split_document(doc)
    assert len(chunks) >= 4
    assert len({chunk.id for chunk in chunks}) == len(chunks)
    for chunk in chunks:
        assert len(chunk.text) <= MAX_CHARS
        assert chunk.text in "\n".join(content.splitlines()[chunk.start_line - 1:chunk.end_line])
