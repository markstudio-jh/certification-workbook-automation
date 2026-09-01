#!/usr/bin/env python
"""교재 산출물용 결정론적 품질 게이트(G2/G3/G4)."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

BLOCK_ALIASES = {
    "learning_objectives": ("학습목표", "학습 목표"),
    "key_concepts": ("핵심개념", "핵심 개념"),
    "body": ("본문", "핵심내용", "핵심 내용"),
    "example": ("사례", "예시", "실무사례"),
    "exam_points": ("출제포인트", "출제 포인트"),
    "confusion_warning": ("혼동주의", "혼동 주의"),
}
NUMERIC_RE = re.compile(r"(?<![\w.])\d[\d,.]*(?:\s*)(?:%|퍼센트|원|만원|억원|일|개월|년|명)(?![A-Za-z0-9])")
SOURCE_RE = re.compile(r"(?:출처|근거|법령|시행령|고시|통계|보고서|https?://|\[[^\]]+\])", re.I)
ORIGINAL_RE = re.compile(r"[가-힣][가-힣\s·-]{1,30}\s*\((?=[^)]*[A-Za-z])[A-Za-z][A-Za-z0-9 /&.,-]*(?:,\s*[A-Z]{2,})?\)")
TABLE_ORIGINAL_RE = re.compile(r"\|[^\n|]*[가-힣][^\n|]*\|[^\n|]*[A-Za-z][^\n|]*\|")
QUESTION_RE = re.compile(r"(?m)^\s*(\d+)\s*[.)]\s+")
ANSWER_RE = re.compile(r"(?m)^\s*정답\s*[:：]\s*([①②③④⑤1-5])")
EVIDENCE_RE = re.compile(r"(?m)^\s*(?:정답\s*)?근거\s*[:：]\s*\S+")
EVIDENCE_LINE_RE = re.compile(r"(?mi)^\s*(?:정답\s*)?근거\s*[:：][^\n]+$")
PAGE_REFERENCE_RE = re.compile(r"(?:PDF\s*)?페이지\s*\d+|\bp{1,2}\.\s*\d+", re.I)
QUESTION_SECTION_RE = re.compile(r"(?mi)^#{1,6}\s*제2부\s+예상문제(?:\s*및\s*해설)?\s*$")
ANSWER_MAP = {"①": "1", "②": "2", "③": "3", "④": "4", "⑤": "5"}


def failure(check_id: str, message: str, actual: Any = None, expected: Any = None) -> dict[str, Any]:
    item = {"id": check_id, "message": message}
    if actual is not None:
        item["actual"] = actual
    if expected is not None:
        item["expected"] = expected
    return item


def _body_text(text: str) -> str:
    """요약/문항 반복 수치를 피하려고 본문 계열 헤딩부터 다음 큰 블록까지만 취한다."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if re.match(r"^#{1,6}\s*(본문|핵심내용|핵심 내용)\s*$", line.strip())]
    if not starts:
        return text
    start = starts[0]
    stop_names = tuple(BLOCK_ALIASES["example"] + BLOCK_ALIASES["exam_points"] + BLOCK_ALIASES["confusion_warning"])
    end = len(lines)
    for i in range(start + 1, len(lines)):
        m = re.match(r"^#{1,6}\s*(.+?)\s*$", lines[i])
        if m and any(name in m.group(1) for name in stop_names):
            end = i
            break
    # 핵심개념의 통계 수치도 평가 대상에 포함한다.
    key_lines = []
    in_key = False
    for line in lines[:start]:
        if re.match(r"^#{1,6}\s*(핵심개념|핵심 개념)\s*$", line.strip()):
            in_key = True
            continue
        if in_key and re.match(r"^#{1,6}\s+", line):
            in_key = False
        if in_key:
            key_lines.append(line)
    return "\n".join(key_lines + lines[start + 1:end])


def _question_text(text: str) -> str:
    """통합 문제집이면 제2부 예상문제부터 평가하고, 문제 전용 문서는 그대로 평가한다."""
    match = QUESTION_SECTION_RE.search(text)
    return text[match.start():] if match else text


def gate_g2(text: str, require_original_terms: bool = True) -> dict[str, Any]:
    failures = []
    found_blocks = {
        key: any(re.search(rf"(?mi)^\s*#{{1,6}}\s*{re.escape(alias)}\s*$", text) for alias in aliases)
        for key, aliases in BLOCK_ALIASES.items()
    }
    if not all(found_blocks.values()):
        missing = [k for k, found in found_blocks.items() if not found]
        failures.append(failure("six_blocks", f"6블록 누락: {', '.join(missing)}", len(found_blocks) - len(missing), 6))

    todo_count = len(re.findall(r"(?i)\bTODO\b|미작성|작성\s*예정", text))
    if todo_count:
        failures.append(failure("todo_zero", f"TODO/미작성 표식 {todo_count}건", todo_count, 0))

    scope = _body_text(text)
    numerics = list(NUMERIC_RE.finditer(scope))
    sourced = 0
    for match in numerics:
        line_start = scope.rfind("\n", 0, match.start()) + 1
        paragraph_end = scope.find("\n\n", match.end())
        if paragraph_end < 0:
            paragraph_end = len(scope)
        if SOURCE_RE.search(scope[line_start:paragraph_end]):
            sourced += 1
    source_rate = sourced / len(numerics) if numerics else 1.0
    if source_rate < 0.60:
        failures.append(failure("numeric_sources", "통계성 수치의 출처 병기율이 60% 미만", round(source_rate, 4), 0.60))

    original_terms = len(ORIGINAL_RE.findall(text)) + len(TABLE_ORIGINAL_RE.findall(text))
    if require_original_terms and original_terms == 0:
        failures.append(failure("original_terms", "용어 원어 병기를 찾지 못함", 0, ">=1"))
    if not found_blocks["exam_points"]:
        failures.append(failure("exam_points", "출제포인트 블록 누락"))
    if not found_blocks["confusion_warning"]:
        failures.append(failure("confusion_warning", "혼동주의 블록 누락"))

    return {
        "gate": "G2", "passed": not failures, "failures": failures,
        "metrics": {"blocks": found_blocks, "todo_count": todo_count,
                    "numeric_count": len(numerics), "sourced_numeric_count": sourced,
                    "numeric_source_rate": round(source_rate, 4), "original_term_count": original_terms},
    }


def gate_g3(text: str) -> dict[str, Any]:
    text = _question_text(text)
    failures = []
    question_matches = list(QUESTION_RE.finditer(text))
    question_count = len(question_matches)
    answers = [ANSWER_MAP.get(a, a) for a in ANSWER_RE.findall(text)]
    evidence_count = len(EVIDENCE_RE.findall(text))
    question_blocks = [
        text[match.start(): question_matches[index + 1].start() if index + 1 < question_count else len(text)]
        for index, match in enumerate(question_matches)
    ]
    page_evidence_count = sum(
        any(PAGE_REFERENCE_RE.search(line) for line in EVIDENCE_LINE_RE.findall(block))
        for block in question_blocks
    )
    trap_count = text.count("★")

    answer_rate = len(answers) / question_count if question_count else 0.0
    evidence_rate = evidence_count / question_count if question_count else 0.0
    page_evidence_rate = page_evidence_count / question_count if question_count else 0.0
    counts = Counter(answers)
    max_share = max(counts.values()) / len(answers) if answers else 0.0

    if question_count == 0:
        failures.append(failure("questions_present", "문항을 찾지 못함", 0, ">=1"))
    if answer_rate < 1.0:
        failures.append(failure("answer_labels", "일부 문항에 정답 표기가 없음", round(answer_rate, 4), 1.0))
    if evidence_rate < 0.70:
        failures.append(failure("answer_evidence", "정답 근거 병기율이 70% 미만", round(evidence_rate, 4), 0.70))
    if page_evidence_rate < 1.0:
        failures.append(failure("answer_page_evidence", "모든 문항의 정답 근거에 PDF 페이지가 필요함",
                                round(page_evidence_rate, 4), 1.0))
    if trap_count == 0:
        failures.append(failure("trap_present", "함정(★) 표기가 없음", 0, ">=1"))
    if max_share > 0.50:
        failures.append(failure("answer_bias", "한 정답 위치의 비율이 50% 초과", round(max_share, 4), "<=0.50"))

    return {
        "gate": "G3", "passed": not failures, "failures": failures,
        "metrics": {"question_count": question_count, "answer_count": len(answers),
                    "answer_evidence_rate": round(evidence_rate, 4),
                    "answer_page_evidence_count": page_evidence_count,
                    "answer_page_evidence_rate": round(page_evidence_rate, 4), "trap_count": trap_count,
                    "answer_distribution": dict(sorted(counts.items())), "max_answer_share": round(max_share, 4)},
    }


def gate_g4(path: Path, soffice: str = "soffice") -> dict[str, Any]:
    failures = []
    executable = shutil.which(soffice)
    if not executable:
        failures.append(failure("soffice_available", f"LibreOffice 실행 파일을 찾지 못함: {soffice}"))
        return {"gate": "G4", "passed": False, "failures": failures,
                "metrics": {"converted": False, "pages_generated": False}}
    if not path.exists():
        failures.append(failure("docx_exists", f"DOCX 파일 없음: {path}"))
        return {"gate": "G4", "passed": False, "failures": failures,
                "metrics": {"converted": False, "pages_generated": False}}
    with tempfile.TemporaryDirectory(prefix="g4-") as outdir:
        proc = subprocess.run([executable, "--headless", "--convert-to", "pdf", "--outdir", outdir, str(path)],
                              capture_output=True, text=True, timeout=120)
        pdf = Path(outdir) / f"{path.stem}.pdf"
        converted = proc.returncode == 0 and pdf.exists() and pdf.stat().st_size > 0
        if not converted:
            failures.append(failure("soffice_conversion", "DOCX→PDF 변환 실패", proc.returncode, 0))
        pages_generated = converted
        if not pages_generated:
            failures.append(failure("pages_generated", "렌더링된 페이지가 없음"))
    return {"gate": "G4", "passed": not failures, "failures": failures,
            "metrics": {"converted": converted, "pages_generated": pages_generated}}


def evaluate(
    path: str | Path,
    gate: str,
    soffice: str = "soffice",
    require_original_terms: bool = True,
) -> dict[str, Any]:
    path = Path(path)
    gate = gate.upper()
    if gate == "G4":
        report = gate_g4(path, soffice)
    else:
        if not path.exists():
            return {"gate": gate, "passed": False,
                    "failures": [failure("input_exists", f"입력 파일 없음: {path}")], "metrics": {}}
        text = path.read_text(encoding="utf-8")
        report = gate_g2(text, require_original_terms=require_original_terms) if gate == "G2" else gate_g3(text)
    report["file"] = str(path)
    return report


def _human(report: dict[str, Any]) -> str:
    lines = [f"[{report['gate']}] {'PASS' if report['passed'] else 'FAIL'} — {report['file']}"]
    for item in report["failures"]:
        lines.append(f"  - {item['id']}: {item['message']}")
    lines.append("  metrics: " + json.dumps(report["metrics"], ensure_ascii=False))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="교재 품질 게이트")
    p.add_argument("path", nargs="?", help="G2/G3 Markdown 또는 G4 DOCX")
    p.add_argument("--docx", help="G4용 DOCX 경로(호환 옵션)")
    p.add_argument("--gate", required=True, choices=("G2", "G3", "G4"))
    p.add_argument("--json", action="store_true", help="기계 판독 JSON 출력")
    p.add_argument("--soffice", default="soffice")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    target = args.docx or args.path
    if not target:
        build_parser().error("path 또는 --docx가 필요합니다")
    report = evaluate(target, args.gate, args.soffice)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else _human(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
