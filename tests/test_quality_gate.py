import json
from pathlib import Path

import quality_gate


GOOD_G2 = """# 절 교재
## 학습목표
목표를 이해한다.
## 핵심개념
위험기반자본 (Risk-Based Capital, RBC)은 자본 규제다. 비율은 60%이다 [출처: 법령 제3조].
## 본문
신고 기한은 30일이다 [출처: 시행령 제4조].
## 사례
사례를 분석한다.
## 출제포인트
RBC의 의미를 묻는다.
## 혼동주의
지급여력비율과 혼동하지 않는다.
"""

GOOD_G3 = """# 예상문제
1. 옳은 것은? ★ 함정: 기한 혼동
① A ② B ③ C ④ D
정답: ①
근거: 본문 제1절 [PDF 페이지 1]

2. 옳은 것은?
① A ② B ③ C ④ D
정답: ②
근거: 본문 제2절 [PDF 페이지 2]

3. 옳은 것은?
① A ② B ③ C ④ D
정답: ③
근거: 본문 제3절 [PDF 페이지 3]

4. 옳은 것은?
① A ② B ③ C ④ D
정답: ④
근거: 본문 제4절 [PDF 페이지 4]
"""


def test_g2_passes_complete_document(tmp_path):
    p = tmp_path / "section.md"
    p.write_text(GOOD_G2, encoding="utf-8")
    report = quality_gate.evaluate(p, "G2")
    assert report["passed"] is True
    assert report["metrics"]["numeric_source_rate"] == 1.0


def test_g2_reports_only_machine_checkable_failures(tmp_path):
    p = tmp_path / "bad.md"
    p.write_text("# 교재\n## 학습목표\nTODO 60%", encoding="utf-8")
    report = quality_gate.evaluate(p, "G2")
    ids = {x["id"] for x in report["failures"]}
    assert {"six_blocks", "todo_zero", "numeric_sources", "original_terms", "exam_points", "confusion_warning"} <= ids


def test_g2_counts_korean_particles_after_numeric_units(tmp_path):
    p = tmp_path / "particles.md"
    p.write_text(GOOD_G2.replace("30일이다", "30일이며").replace("60%이다", "60%이고"), encoding="utf-8")
    report = quality_gate.evaluate(p, "G2")
    assert report["metrics"]["numeric_count"] == 2
    assert report["metrics"]["numeric_source_rate"] == 1.0


def test_g3_detects_answer_bias_and_missing_evidence(tmp_path):
    text = "# 문제\n" + "\n\n".join(
        f"{i}. 질문?\n① A ② B ③ C ④ D\n정답: ③" for i in range(1, 5)
    ) + "\n★ 함정"
    p = tmp_path / "questions.md"
    p.write_text(text, encoding="utf-8")
    report = quality_gate.evaluate(p, "G3")
    ids = {x["id"] for x in report["failures"]}
    assert "answer_evidence" in ids
    assert "answer_bias" in ids
    assert report["metrics"]["max_answer_share"] == 1.0


def test_g3_requires_pdf_page_reference_for_every_answer(tmp_path):
    p = tmp_path / "questions.md"
    p.write_text(GOOD_G3.replace(" [PDF 페이지 1]", "")
                            .replace(" [PDF 페이지 2]", "")
                            .replace(" [PDF 페이지 3]", "")
                            .replace(" [PDF 페이지 4]", ""), encoding="utf-8")
    report = quality_gate.evaluate(p, "G3")
    ids = {x["id"] for x in report["failures"]}
    assert "answer_page_evidence" in ids
    assert report["metrics"]["answer_page_evidence_rate"] == 0.0


def test_g3_does_not_let_duplicate_page_evidence_hide_missing_question(tmp_path):
    p = tmp_path / "questions.md"
    text = GOOD_G3.replace(
        "근거: 본문 제1절 [PDF 페이지 1]",
        "근거: 본문 제1절 [PDF 페이지 1]\n근거: 추가 설명 [PDF 페이지 1]",
    ).replace("근거: 본문 제4절 [PDF 페이지 4]", "근거: 본문 제4절")
    p.write_text(text, encoding="utf-8")
    report = quality_gate.evaluate(p, "G3")
    ids = {x["id"] for x in report["failures"]}
    assert "answer_page_evidence" in ids
    assert report["metrics"]["answer_page_evidence_rate"] == 0.75


def test_g3_scopes_combined_workbook_to_question_section():
    text = """# 제1부 핵심 교재

1. 본문의 번호 목록
2. 본문의 다른 번호 목록

# 제2부 예상문제 및 해설

""" + GOOD_G3

    report = quality_gate.gate_g3(text)

    assert report["passed"] is True
    assert report["metrics"]["question_count"] == 4
    assert report["metrics"]["answer_page_evidence_count"] == 4


def test_cli_json_and_exit_codes(tmp_path, capsys):
    p = tmp_path / "questions.md"
    p.write_text(GOOD_G3, encoding="utf-8")
    rc = quality_gate.main([str(p), "--gate", "G3", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["passed"] is True


def test_g4_fails_cleanly_when_converter_missing(tmp_path):
    p = tmp_path / "x.docx"
    p.write_bytes(b"fake")
    report = quality_gate.evaluate(p, "G4", soffice="definitely-not-installed")
    assert report["passed"] is False
    assert report["failures"][0]["id"] == "soffice_available"
