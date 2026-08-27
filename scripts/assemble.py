#!/usr/bin/env python
"""모든 절이 published일 때 장 단위 Markdown을 원자적으로 조립한다."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


class AssemblyError(RuntimeError):
    pass


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(content, encoding="utf-8")
    os.replace(temp, path)


def assemble(root: str | Path) -> dict[str, Any]:
    root = Path(root).resolve()
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    incomplete = [s["id"] for s in state.get("sections", []) if s.get("stage") != "published"]
    if incomplete:
        raise AssemblyError("모든 절이 published여야 합니다: " + ", ".join(incomplete))
    material_parts = [f"# {state.get('book', '')} {state.get('chapter', '')} 통합 교재\n"]
    question_parts = [f"# {state.get('book', '')} {state.get('chapter', '')} 예상문제\n"]
    for section in state.get("sections", []):
        sid = section["id"]
        base = root / "outputs" / "sections" / sid
        for filename in ("material.md", "questions.md"):
            if not (base / filename).exists():
                raise AssemblyError(f"published 절의 산출물이 없습니다: {base / filename}")
        material_parts.append((base / "material.md").read_text(encoding="utf-8").strip())
        question_parts.append((base / "questions.md").read_text(encoding="utf-8").strip())
    out = root / "outputs" / "chapter"
    material = out / "material.md"
    questions = out / "questions.md"
    _write_atomic(material, "\n\n---\n\n".join(material_parts) + "\n")
    _write_atomic(questions, "\n\n---\n\n".join(question_parts) + "\n")
    return {"material": str(material), "questions": str(questions), "sections": len(state.get("sections", []))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="published 절 통합")
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    try:
        result = assemble(args.root)
    except AssemblyError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
