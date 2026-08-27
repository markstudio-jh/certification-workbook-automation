#!/usr/bin/env python
"""상태 파일을 중심으로 절 단위 Hermes 에이전트를 병렬 실행한다."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any

import quality_gate


class PreconditionError(RuntimeError):
    pass


class AgentError(RuntimeError):
    pass


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, state: dict[str, Any]) -> None:
        with self._lock:
            state["updated"] = date.today().isoformat()
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temp, self.path)

    def update_section(self, section_id: str, **changes: Any) -> None:
        with self._lock:
            state = self.load()
            section = next(s for s in state["sections"] if s["id"] == section_id)
            section.update(changes)
            self.save(state)


def ready_sections(state: dict[str, Any], selected: set[str] | None = None) -> list[dict[str, Any]]:
    terminal = {"published"}
    return [s for s in state.get("sections", []) if s.get("stage", "pending") not in terminal
            and (selected is None or s["id"] in selected)]


class HermesAgent:
    """각 호출을 별도 one-shot 세션으로 실행해 집필/검수 컨텍스트를 격리한다."""
    def __init__(self, executable: str = "hermes", timeout: int = 900, extra_args: list[str] | None = None):
        self.executable = executable
        self.timeout = timeout
        self.extra_args = extra_args or []

    def command(self, prompt: str) -> list[str]:
        return [self.executable, *self.extra_args, "-z", prompt]

    def run(self, prompt: str, cwd: Path, log_path: Path) -> str:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(self.command(prompt), cwd=cwd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=self.timeout)
        log_path.write_text(proc.stdout + ("\n[stderr]\n" + proc.stderr if proc.stderr else ""), encoding="utf-8")
        if proc.returncode != 0:
            raise AgentError(f"Hermes 실패(exit={proc.returncode}); 로그: {log_path}")
        return proc.stdout.strip()


class DryRunAgent(HermesAgent):
    def __init__(self):
        super().__init__()
        self.commands: list[str] = []

    def run(self, prompt: str, cwd: Path, log_path: Path) -> str:
        cmd = "hermes -z " + json.dumps(prompt, ensure_ascii=False)
        self.commands.append(cmd)
        return "DRY-RUN"


class Pipeline:
    def __init__(self, root: str | Path, agent: HermesAgent | None = None, max_retries: int = 2):
        self.root = Path(root).resolve()
        self.store = StateStore(self.root / "state.json")
        self.agent = agent or HermesAgent()
        self.max_retries = max_retries

    def _validate(self, state: dict[str, Any]) -> None:
        if not state.get("glossary_locked"):
            raise PreconditionError("병렬 실행 전 glossary를 확정(glossary_locked=true)해야 합니다")
        glossary = self.root / state.get("glossary", "glossary.md")
        if not glossary.exists():
            raise PreconditionError(f"glossary 파일이 없습니다: {glossary}")
        for section in state.get("sections", []):
            source = self.root / section["source"]
            if not source.exists():
                raise PreconditionError(f"절 원문이 없습니다: {source}")

    def _context(self, state: dict[str, Any], section: dict[str, Any]) -> dict[str, Path]:
        sid = section["id"]
        out = self.root / "outputs" / "sections" / sid
        out.mkdir(parents=True, exist_ok=True)
        return {
            "source": self.root / section["source"],
            "glossary": self.root / state.get("glossary", "glossary.md"),
            "profile": self.root / state.get("profile_card", "profile-card.md"),
            "template": self.root / state.get("template", "templates/six-block.md"),
            "material": out / "material.md", "questions": out / "questions.md",
            "out": out, "logs": out / "logs", "reviews": out / "reviews",
        }

    def _prompt(self, role: str, sid: str, paths: dict[str, Path], extra: str = "") -> str:
        common = (f"프로젝트 루트: {self.root}\n절 ID: {sid}\n원문: {paths['source']}\n"
                  f"프로파일 카드: {paths['profile']}\n확정 glossary: {paths['glossary']}\n"
                  "glossary 표기를 반드시 그대로 사용하고 원문에 없는 사실을 만들지 마라. ")
        prompts = {
            "writer": common + f"6블록 템플릿 {paths['template']}에 맞춰 절 교재를 집필하고 {paths['material']}에 저장하라. 원문 보존 수준 L2를 적용하라.",
            "question_writer": common + f"완성 교재 {paths['material']}에서만 정답이 도출되는 예상문제를 만들고 {paths['questions']}에 저장하라. 모든 문항에 정답, 근거, 정확한 원문 PDF 페이지, 함정은 ★로 표시하라.",
            "repair": common + f"교재 {paths['material']}와 문제 {paths['questions']}를 읽고 아래 실패 항목만 수정하라. 다른 내용은 바꾸지 마라.\n{extra}",
            "review_image": common + f"너는 독립 검수자다. 원문 이미지/표와 교재 {paths['material']}를 대조하라. 수정하지 말고 불일치만 보고하라. 없으면 정확히 '이상 없음'.",
            "review_facts": common + f"너는 독립 검수자다. 원문과 교재 {paths['material']}의 정의·수치·근거를 대조하라. 원문 인용과 초안 인용을 병기하고, 수정하지 말라. 없으면 정확히 '이상 없음'.",
            "review_answers": common + f"너는 독립 검수자다. 문제 {paths['questions']}의 각 정답이 교재 {paths['material']}에서 도출되는지, 각 근거의 PDF 페이지가 원문 {paths['source']}와 정확히 일치하는지 검증하라. 수정하지 말고 불일치만 보고하라. 없으면 정확히 '이상 없음'.",
        }
        return prompts[role]

    def _gate_with_repair(self, state: dict[str, Any], section: dict[str, Any], paths: dict[str, Path], gate: str) -> dict[str, Any]:
        target = paths["material"] if gate == "G2" else paths["questions"]
        for attempt in range(self.max_retries + 1):
            report = quality_gate.evaluate(target, gate)
            (paths["out"] / f"{gate.lower()}-attempt-{attempt}.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            if report["passed"]:
                return report
            if attempt == self.max_retries:
                return report
            failed = json.dumps(report["failures"], ensure_ascii=False, indent=2)
            self.agent.run(self._prompt("repair", section["id"], paths, f"{gate} 실패:\n{failed}"),
                           self.root, paths["logs"] / f"repair-{gate}-{attempt + 1}.log")
        raise AssertionError("unreachable")

    @staticmethod
    def _review_clean(text: str) -> bool:
        normalized = text.strip().replace(".", "")
        if normalized == "이상 없음":
            return True
        try:
            payload = json.loads(text)
            return payload.get("findings") == []
        except (json.JSONDecodeError, AttributeError):
            return False

    def _run_section(self, state: dict[str, Any], section: dict[str, Any]) -> dict[str, Any]:
        sid = section["id"]
        paths = self._context(state, section)
        self.store.update_section(sid, stage="writing")
        self.agent.run(self._prompt("writer", sid, paths), self.root, paths["logs"] / "writer.log")
        g2 = self._gate_with_repair(state, section, paths, "G2")
        if not g2["passed"]:
            self.store.update_section(sid, stage="human_review", gates={"G2": "fail"}, last_error=g2["failures"])
            return {"id": sid, "status": "human_review", "gate": "G2"}

        self.store.update_section(sid, stage="questions", gates={"G2": "pass"})
        self.agent.run(self._prompt("question_writer", sid, paths), self.root, paths["logs"] / "question-writer.log")
        g3 = self._gate_with_repair(state, section, paths, "G3")
        if not g3["passed"]:
            self.store.update_section(sid, stage="human_review", gates={"G2": "pass", "G3": "fail"}, last_error=g3["failures"])
            return {"id": sid, "status": "human_review", "gate": "G3"}

        self.store.update_section(sid, stage="review", gates={"G2": "pass", "G3": "pass"})
        paths["reviews"].mkdir(parents=True, exist_ok=True)
        roles = ("review_image", "review_facts", "review_answers")
        review_results: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(self.agent.run, self._prompt(role, sid, paths), self.root,
                                   paths["reviews"] / f"{role}.md"): role for role in roles}
            for future in as_completed(futures):
                review_results[futures[future]] = future.result()
        clean = all(self._review_clean(text) for text in review_results.values())
        if not clean:
            findings = "\n\n".join(f"## {role}\n{text}" for role, text in review_results.items())
            self.agent.run(self._prompt("repair", sid, paths, "독립 검수 지적:\n" + findings),
                           self.root, paths["logs"] / "review-repair.log")
            g2 = self._gate_with_repair(state, section, paths, "G2")
            g3 = self._gate_with_repair(state, section, paths, "G3")
            if not (g2["passed"] and g3["passed"]):
                self.store.update_section(sid, stage="human_review", last_error="검수 수정 후 게이트 실패")
                return {"id": sid, "status": "human_review", "gate": "post-review"}
        self.store.update_section(sid, stage="published", gates={"G2": "pass", "G3": "pass"})
        return {"id": sid, "status": "published", "reviews_clean": clean}

    def _dry_plan(self, state: dict[str, Any], sections: list[dict[str, Any]]) -> dict[str, Any]:
        commands = []
        for section in sections:
            paths = self._context(state, section)
            for role in ("writer", "question_writer", "review_image", "review_facts", "review_answers"):
                commands.append("hermes -z " + json.dumps(self._prompt(role, section["id"], paths), ensure_ascii=False))
        return {"scheduled": [s["id"] for s in sections], "commands": commands, "dry_run": True}

    def run(self, dry_run: bool = False, max_workers: int = 3, selected: set[str] | None = None) -> dict[str, Any]:
        state = self.store.load()
        self._validate(state)
        sections = ready_sections(state, selected)
        if dry_run:
            return self._dry_plan(state, sections)
        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(self._run_section, state, section): section["id"] for section in sections}
            for future in as_completed(futures):
                sid = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    self.store.update_section(sid, stage="human_review", last_error=str(exc))
                    results.append({"id": sid, "status": "error", "error": str(exc)})
        final = self.store.load()
        all_published = all(s.get("stage") == "published" for s in final.get("sections", []))
        return {"scheduled": [s["id"] for s in sections], "results": results,
                "chapter_ready": all_published}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Hermes 절 병렬 제작 파이프라인")
    p.add_argument("--root", default=".")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--section", action="append", help="특정 절만 실행(반복 가능)")
    p.add_argument("--max-retries", type=int, default=2)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runner = Pipeline(args.root, max_retries=args.max_retries)
    try:
        result = runner.run(dry_run=args.dry_run, max_workers=args.workers,
                            selected=set(args.section) if args.section else None)
    except PreconditionError as exc:
        print(json.dumps({"status": "precondition_failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.dry_run:
        return 0
    return 0 if all(r.get("status") == "published" for r in result["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
