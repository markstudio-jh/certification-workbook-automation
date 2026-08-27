# 프로젝트 규칙

이 저장소는 절 단위 수험교재 자동화 파이프라인이다.

- `state.json`을 먼저 읽고 `glossary_locked=true`인지 확인한다.
- 집필자는 `inputs/sections/<절>.md`, `profile-card.md`, `glossary.md`, `templates/six-block.md`만 근거로 사용한다.
- 원문에 없는 정의·수치·법령을 만들지 않는다.
- 검수자는 원문과 초안만 대조하고 파일을 수정하지 않는다.
- 수정자는 게이트/검수에서 지적된 항목만 수정한다.
- 완료 선언 전 `python scripts/quality_gate.py ...`를 실행한다.
- `state.json`은 `scripts/pipeline.py`를 통해 갱신하며 병렬 작업자가 직접 덮어쓰지 않는다.
