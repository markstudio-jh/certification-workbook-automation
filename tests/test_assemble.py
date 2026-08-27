import json
from pathlib import Path

import assemble


def test_assemble_requires_all_sections_published(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"sections": [{"id": "1-1", "stage": "review"}]}), encoding="utf-8")
    try:
        assemble.assemble(tmp_path)
    except assemble.AssemblyError as exc:
        assert "published" in str(exc)
    else:
        raise AssertionError("expected failure")


def test_assemble_combines_sections_in_state_order(tmp_path):
    sections = [{"id": "1-2", "stage": "published"}, {"id": "1-1", "stage": "published"}]
    (tmp_path / "state.json").write_text(json.dumps({"book": "1권", "chapter": "1장", "sections": sections}), encoding="utf-8")
    for sid in ("1-1", "1-2"):
        out = tmp_path / "outputs" / "sections" / sid
        out.mkdir(parents=True)
        (out / "material.md").write_text(f"# 교재 {sid}", encoding="utf-8")
        (out / "questions.md").write_text(f"# 문제 {sid}", encoding="utf-8")
    result = assemble.assemble(tmp_path)
    text = Path(result["material"]).read_text(encoding="utf-8")
    assert text.index("교재 1-2") < text.index("교재 1-1")
    assert Path(result["questions"]).exists()
