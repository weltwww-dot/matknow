import pytest

from matknow.knowledge import KnowledgeBase


@pytest.fixture
def kb(tmp_path):
    return KnowledgeBase(tmp_path / "knowledge.sqlite3")


def test_import_persists_source_and_deduplicates(kb):
    first = kb.import_text("方法A.md", "样品 DEMO-A 的干燥条件为 60 °C，6 h。")
    second = kb.import_text("方法A.md", "样品 DEMO-A 的干燥条件为 60 °C，6 h。")
    reopened = KnowledgeBase(kb.path)
    assert first.id == second.id
    assert len(reopened.list_documents()) == 1
    evidence = reopened.search("干燥条件")
    assert evidence[0].document_id == first.id
    assert evidence[0].filename == "方法A.md"
    assert evidence[0].locator == "段落 1"
    assert "60 °C" in evidence[0].text


def test_search_chinese_english_and_chemical_formula(kb):
    doc = kb.import_text("methods.txt", "Fe3O4 sample drying temperature: 60 °C.\n\n干燥时间为 6 h。")
    for question in ["Fe3O4", "drying temperature", "干燥时间"]:
        assert kb.search(question)[0].document_id == doc.id
    assert kb.search("完全不存在的术语XYZ987") == []


def test_selected_scope_and_empty_scope_never_leak(kb):
    first = kb.import_text("A.txt", "样品 A 的干燥温度为 60 °C。")
    second = kb.import_text("B.txt", "样品 B 的干燥温度为 80 °C。")
    assert {hit.document_id for hit in kb.search("干燥", [second.id])} == {second.id}
    assert kb.search("干燥", []) == []
    assert kb.search("干燥", ["missing"]) == []
    assert {hit.document_id for hit in kb.search("干燥")} == {first.id, second.id}


def test_delete_removes_sources_but_not_other_documents(kb):
    first = kb.import_text("A.txt", "干燥温度为 60 °C。")
    second = kb.import_text("B.txt", "干燥温度为 80 °C。")
    source_id = kb.search("干燥", [first.id])[0].id
    kb.delete_document(first.id)
    assert kb.get_evidence(source_id) is None
    assert {hit.document_id for hit in kb.search("干燥")} == {second.id}
    assert len(kb.list_documents()) == 1


def test_failed_import_does_not_leave_document(kb):
    with pytest.raises(ValueError):
        kb.import_text("empty.txt", " \n\t")
    assert kb.list_documents() == []
