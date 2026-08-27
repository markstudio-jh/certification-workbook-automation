import json
from pathlib import Path

import pipeline


def make_project(tmp_path: Path):
    (tmp_path / "inputs" / "sections").mkdir(parents=True)
    (tmp_path / "inputs" / "sections" / "3-1.md").write_text("원문 A", encoding="utf-8")
    (tmp_path / "inputs" / "sections" / "3-2.md").write_text("원문 B", encoding="utf-8")
    (tmp_path / "glossary.md").write_text("수의계약 | Direct Contract", encoding="utf-8")
    state = {
        "profile": "공공조달관리사", "book": "1권", "chapter": "3장",
        "glossary_locked": True,
        "sections": [
            {"id": "3-1", "source": "inputs/sections/3-1.md", "stage": "pending", "gates": {}, "attempts": {}},
            {"id": "3-2", "source": "inputs/sections/3-2.md", "stage": "published", "gates": {"G2": "pass", "G3": "pass"}, "attempts": {}}
        ]
    }
    (tmp_path / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def test_ready_sections_skip_published(tmp_path):
    make_project(tmp_path)
    state = pipeline.StateStore(tmp_path / "state.json").load()
    assert [s["id"] for s in pipeline.ready_sections(state)] == ["3-1"]


def test_glossary_must_be_locked_before_parallel_run(tmp_path):
    make_project(tmp_path)
    store = pipeline.StateStore(tmp_path / "state.json")
    state = store.load()
    state["glossary_locked"] = False
    store.save(state)
    runner = pipeline.Pipeline(tmp_path, agent=pipeline.DryRunAgent())
    try:
        runner.run()
    except pipeline.PreconditionError as exc:
        assert "glossary" in str(exc).lower()
    else:
        raise AssertionError("expected precondition failure")


def test_dry_run_produces_plan_without_mutating_state(tmp_path):
    make_project(tmp_path)
    before = (tmp_path / "state.json").read_text(encoding="utf-8")
    runner = pipeline.Pipeline(tmp_path, agent=pipeline.DryRunAgent())
    result = runner.run(dry_run=True, max_workers=2)
    after = (tmp_path / "state.json").read_text(encoding="utf-8")
    assert before == after
    assert result["scheduled"] == ["3-1"]
    assert result["commands"]


def test_atomic_state_save_leaves_valid_json(tmp_path):
    make_project(tmp_path)
    store = pipeline.StateStore(tmp_path / "state.json")
    state = store.load()
    state["sections"][0]["stage"] = "writing"
    store.save(state)
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["sections"][0]["stage"] == "writing"


def test_question_writer_prompt_requires_pdf_page_evidence(tmp_path):
    make_project(tmp_path)
    runner = pipeline.Pipeline(tmp_path, agent=pipeline.DryRunAgent())
    state = runner.store.load()
    section = state["sections"][0]
    prompt = runner._prompt("question_writer", section["id"], runner._context(state, section))
    assert "PDF 페이지" in prompt
    assert "모든 문항" in prompt


def test_answer_review_prompt_verifies_pdf_page_evidence(tmp_path):
    make_project(tmp_path)
    runner = pipeline.Pipeline(tmp_path, agent=pipeline.DryRunAgent())
    state = runner.store.load()
    section = state["sections"][0]
    prompt = runner._prompt("review_answers", section["id"], runner._context(state, section))
    assert "PDF 페이지" in prompt
    assert "원문" in prompt
