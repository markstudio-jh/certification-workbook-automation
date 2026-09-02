# Hermes 자율 교재 제작 자동화

사람의 절별 승인 대기를 제거하고, 예외만 사람에게 넘기는 실행 가능한 참조 프로젝트다.

## 공개 결과물

- [자동화 대시보드](dashboard.html) — 파이프라인 상태·품질 게이트·공개 결과물을 한 화면에서 확인
- [1권 1장 공공조달 맞춤 문제집](deliverables/volume-1-chapter-1/README.md) — 대화형 HTML·Markdown·DOCX·PDF
- [1권 2장 공공조달 맞춤 문제집](deliverables/volume-1-chapter-2/README.md) — 대화형 HTML·Markdown, 3개 절 68문항
- [1권 3장 공개용 가상 샘플 문제집](deliverables/volume-1-chapter-3/README.md) — 대화형 HTML·Markdown, 4개 절 48문항
- [1권 4장 공공조달 맞춤 문제집](deliverables/volume-1-chapter-4/README.md) — 대화형 HTML·Markdown, 5개 절 108문항

## 구현 범위

- G2: 6블록, TODO 0건, 본문 통계성 수치 출처율 60% 이상, 원문에 원어가 있을 때 병기, 출제포인트/혼동주의
- G3: 문항별 정답, 근거율 70% 이상, ★ 함정, 최대 정답 위치 비율 50% 이하
- G4: LibreOffice `soffice` DOCX→PDF 변환과 페이지 파일 생성
- 절별 독립 Hermes one-shot 세션과 최대 2회 자동 재작업
- 이미지/표, 수치/근거, 문항 정답의 3중 독립 검수
- 절 병렬 처리(`ThreadPoolExecutor`)와 검수 병렬 처리
- 원자적 `state.json` 갱신, 중단 재개, 실패 절 선택 재시도
- 모든 절 `published`일 때만 장 단위 Markdown 조립

## 요구 사항

- Python 3.11 이상
- Hermes Agent CLI와 인증된 모델
- G4 사용 시 LibreOffice(`soffice`가 PATH에 있어야 함)
- 테스트에는 pytest와 대시보드 JavaScript 동작 검증용 Node.js 필요: `uv run --with pytest ...`

Windows에서도 Hermes 터미널이 Git Bash이므로 아래 명령을 그대로 사용할 수 있다. 이 PC에서는 `python3` 대신 `python`을 사용한다.

## 1. 입력 준비

1. `inputs/sections/<절 ID>.md`에 절 원문을 둔다.
2. `profile-card.md`를 수정한다.
3. `glossary.md`를 확정한다.
4. `state.json`에 절을 추가하고 `glossary_locked`를 `true`로 둔다.

절 상태:

`pending → writing → questions → review → published`

게이트가 2회 재작업 후에도 실패하거나 에이전트 오류가 나면 `human_review`로 전환된다.

## 2. 실행 전 계획 확인

```bash
cd /path/to/MARK
python scripts/pipeline.py --dry-run
```

특정 절만 확인:

```bash
python scripts/pipeline.py --dry-run --section 3-1
```

## 3. 실제 자율 실행

저장소에 포함된 공개용 가상 샘플 `3-1~3-4`는 새 체크아웃에서 바로 실행할 수 있도록 모두 `pending` 상태로 제공한다. 각 파일은 자동화 입력 형식을 검증하기 위해 새로 작성한 예시이며, 파이프라인 실행 결과는 `outputs/`에 생성되고 저장소에는 포함되지 않는다.

```bash
python scripts/pipeline.py --workers 3
```

실패 절만 재시도하려면 원인을 고친 뒤 해당 절의 `stage`를 `pending`으로 바꾸고 실행한다.

```bash
python scripts/pipeline.py --section 3-2 --workers 1
```

에이전트별 결과와 게이트 리포트는 `outputs/sections/<절 ID>/`에 남는다.

## 4. 품질 게이트 단독 실행

```bash
python scripts/quality_gate.py 교재.md --gate G2
python scripts/quality_gate.py 문제.md --gate G3 --json
python scripts/quality_gate.py --docx 산출물.docx --gate G4
```

종료 코드: `0=PASS`, `1=FAIL`. 입력 전제 오류 등 파이프라인 전제 실패는 `2`다.

## 5. 장 조립

모든 절이 `published`일 때만 실행된다.

```bash
python scripts/assemble.py --root .
```

결과:

- `outputs/chapter/material.md`
- `outputs/chapter/questions.md`

DOCX 조판은 조직별 템플릿이 다르므로 이 프로젝트가 임의로 스타일을 만들지 않는다. 기존 DOCX 생성 단계 뒤에 G4를 연결한다.

## 6. 테스트

```bash
uv run --with pytest python -m pytest tests -q
```

## 7. Hermes Cron 연결

먼저 수동 실행으로 입력·모델·실행 시간을 검증한다. Hermes cron 실행에는 제한 시간이 있으므로 한 번에 처리할 절 수를 실제 소요 시간에 맞춘다.

```bash
hermes cron create "0 1 * * *" \
  "state.json을 읽고 미완료 절 하나만 골라 python scripts/pipeline.py --section <절ID> --workers 1을 실행하라. 결과 JSON과 human_review 예외만 보고하라." \
  --name "교재-야간-절제작" \
  --workdir "C:/path/to/MARK" \
  --deliver local
```

현재 CLI 세션의 `local` 결과는 실시간 메시지로 배달되지 않고 cron 목록에서 확인한다. 알림이 필요하면 연결된 게이트웨이에 맞게 `--deliver telegram` 또는 구체적인 플랫폼 대상을 사용한다.

## 라이선스와 공개 범위

- 자동화 소프트웨어와 저장소 문서는 `LICENSE`의 MIT License로 공개한다.
- `LICENSE`의 “Software”에는 `deliverables/`의 문제집·교재 콘텐츠가 포함되지 않는다. 해당 학습 콘텐츠는 별도 허가 없이 복제·재배포할 수 없다.
- 공개 문제집은 시험 대비용 2차 학습자료이며 원본 교재 PDF와 원문 추출본을 포함하지 않는다.
- `inputs/sections/3-1.md`부터 `3-4.md`까지는 실행 형식을 보여주기 위해 새로 작성한 공개용 가상 샘플이며 실제 교재에서 추출한 문장이 아니다.
- 실제 원본 교재·PDF·원문 추출 Markdown은 Private 백업에서만 관리하며, 그 권리는 각 권리자에게 있다.

등록 전 확인:

```bash
hermes cron list
```

## 실패 처리 원칙

- G2/G3 실패: 실패 항목만 수정하는 별도 Hermes 세션을 최대 2회 실행
- 2회 초과: `state.json`의 해당 절을 `human_review`로 전환
- 검수자는 수정 권한이 없으며 보고서만 반환
- 검수 세션이 `API call failed`를 반환하면 의미 지적으로 채택하지 않고 에이전트 오류로 격리
- 검수 지적이 있으면 수정 에이전트가 지적 항목만 수정하고 G2/G3를 다시 실행
- 장 조립은 모든 절이 `published`일 때만 허용

## 주의 사항

이 게이트는 의미적 사실성 전체를 판정하지 않는다. 기계 판정 항목은 게이트가 맡고, 원본 이미지·수치·정답의 의미 검증은 컨텍스트가 격리된 검수 에이전트가 맡는다. 실제 산출물에 적용해 정규식 오탐을 지속적으로 보정해야 한다.

## 포함된 샘플의 검증 이력

- 공개용 가상 샘플 4개 절을 writer → G2 → question writer → G3 → 3중 검수까지 종단간 실행하고 48문항을 장 단위로 조립
- 수치·근거 검수자가 원문의 “참여 기회를 부여”가 초안의 “실제로 참여”로 바뀐 의미 오류 1건을 발견
- 수정 에이전트가 해당 문장만 복원한 뒤 G2·G3와 장 조립을 통과
- 48문항 모두 정답·근거·PDF 페이지·함정을 갖추고 정답 위치는 ①~④ 각 12개로 균등 분포
- 대화형 HTML은 콘텐츠 384개 필드 전수 대조, Chrome 데스크톱·390px 모바일 렌더링, JavaScript 런타임과 독립 검수를 통과
- 공개 저장소의 초기 `state.json`은 생성 산출물을 포함하지 않으므로 재실행 가능한 `pending` 상태를 유지
- LibreOffice가 없는 환경에서 G4 전제 실패를 정상 보고하는 분기를 단위 테스트로 검증
