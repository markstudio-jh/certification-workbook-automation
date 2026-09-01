import json
from pathlib import Path
import re

import pipeline


ROOT = Path(__file__).resolve().parents[1]


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


def test_hermes_agent_treats_nested_api_failure_as_agent_error(tmp_path, monkeypatch):
    completed = pipeline.subprocess.CompletedProcess(
        args=["hermes"],
        returncode=0,
        stdout="API call failed after 3 retries: HTTP 429: The usage limit has been reached\n",
        stderr="",
    )
    monkeypatch.setattr(pipeline.subprocess, "run", lambda *args, **kwargs: completed)
    log_path = tmp_path / "agent.log"

    try:
        pipeline.HermesAgent().run("검수", tmp_path, log_path)
    except pipeline.AgentError as exc:
        assert "API call failed" in str(exc)
        assert str(log_path) in str(exc)
    else:
        raise AssertionError("nested API failure must not be accepted as review output")

    assert "HTTP 429" in log_path.read_text(encoding="utf-8")


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


def test_g2_requires_original_term_only_when_the_source_supports_one(tmp_path, monkeypatch):
    make_project(tmp_path)
    runner = pipeline.Pipeline(tmp_path, agent=pipeline.DryRunAgent())
    state = runner.store.load()
    section = state["sections"][0]
    paths = runner._context(state, section)
    observed = []

    def fake_evaluate(path, gate, **options):
        observed.append(options["require_original_terms"])
        return {"passed": True, "failures": []}

    monkeypatch.setattr(pipeline.quality_gate, "evaluate", fake_evaluate)

    runner._gate_with_repair(state, section, paths, "G2")
    paths["source"].write_text(
        "위험기반자본 (Risk-Based Capital, RBC)을 설명한다.",
        encoding="utf-8",
    )
    runner._gate_with_repair(state, section, paths, "G2")

    assert observed == [False, True]


def test_review_findings_are_repaired_and_independently_rechecked_before_publish(tmp_path, monkeypatch):
    make_project(tmp_path)

    class FindingThenCleanAgent:
        def __init__(self):
            self.calls = []
            self.review_round = 0

        def run(self, prompt, cwd, log_path):
            self.calls.append(prompt)
            if prompt == "review_image":
                self.review_round += 1
            if prompt.startswith("review_"):
                return "의미 오류 있음" if self.review_round == 1 else "이상 없음"
            return "완료"

    agent = FindingThenCleanAgent()
    runner = pipeline.Pipeline(tmp_path, agent=agent)
    state = runner.store.load()
    section = state["sections"][0]
    monkeypatch.setattr(runner, "_prompt", lambda role, sid, paths, extra="": role)
    monkeypatch.setattr(
        runner,
        "_gate_with_repair",
        lambda state, section, paths, gate: {"passed": True, "failures": []},
    )

    result = runner._run_section(state, section)

    review_calls = [call for call in agent.calls if call.startswith("review_")]
    assert len(review_calls) == 6
    assert all(review_calls.count(role) == 2 for role in (
        "review_image", "review_facts", "review_answers",
    ))
    assert result == {"id": "3-1", "status": "published", "reviews_clean": True}


def test_persistent_review_findings_fail_closed_to_human_review(tmp_path, monkeypatch):
    make_project(tmp_path)

    class AlwaysFindingAgent:
        def run(self, prompt, cwd, log_path):
            if prompt.startswith("review_"):
                return "의미 오류 있음"
            return "완료"

    runner = pipeline.Pipeline(tmp_path, agent=AlwaysFindingAgent())
    state = runner.store.load()
    section = state["sections"][0]
    monkeypatch.setattr(runner, "_prompt", lambda role, sid, paths, extra="": role)
    monkeypatch.setattr(
        runner,
        "_gate_with_repair",
        lambda state, section, paths, gate: {"passed": True, "failures": []},
    )

    result = runner._run_section(state, section)
    saved = runner.store.load()["sections"][0]

    assert result == {"id": "3-1", "status": "human_review", "gate": "semantic-review"}
    assert saved["stage"] == "human_review"
    assert "재검수" in saved["last_error"]


def test_public_section_samples_are_complete_synthetic_and_registered():
    expected_ids = ["3-1", "3-2", "3-3", "3-4"]
    state = json.loads((ROOT / "state.json").read_text(encoding="utf-8"))

    assert [section["id"] for section in state["sections"]] == expected_ids
    assert [section["source"] for section in state["sections"]] == [
        f"inputs/sections/{section_id}.md" for section_id in expected_ids
    ]
    assert all(section["stage"] == "pending" for section in state["sections"])

    for index, section_id in enumerate(expected_ids):
        sample = ROOT / "inputs" / "sections" / f"{section_id}.md"
        assert sample.is_file()
        text = sample.read_text(encoding="utf-8")
        assert "가상 예시" in text
        assert "실제 교재·PDF에서 추출한 문장이 아니" in text
        assert "> 원본:" not in text
        markers = [
            int(page)
            for page in re.findall(r"^\[PDF 페이지 (\d+)\]$", text, re.MULTILINE)
        ]
        start = index * 2 + 1
        assert markers == [start, start + 1]

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    dashboard = (ROOT / "dashboard.html").read_text(encoding="utf-8")
    assert "3-1~3-4" in readme
    assert "공개용 가상 샘플" in readme
    assert "원문에 원어가 있을 때 병기" in readme
    assert all(f"inputs/sections/{section_id}.md" in dashboard for section_id in expected_ids)
