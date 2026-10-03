# Carret

대충 찍은 중고 사진을 정직한 상품 사진으로. 원칙: **배경은 바꾸고, 물건의 정체는 지키고, 상태는 원본으로 보여준다.**
구조·단계 설명은 `Readme.ko.md` 가 기준이다 — 여기엔 작업 규칙만 둔다.

## 명령

```bash
cd backend && python -m pytest test/software/unit -o addopts="" -q   # 유닛 (돈·네트워크·무거운 모델 0)
make test                                                           # eval·e2e 제외 전체
python backend/scripts/seed_langfuse_prompts.py                     # 프롬프트를 바꿨으면 Langfuse 에 시딩
make docs                                                           # md 를 브라우저로 (mermaid)
```

- 작업 중엔 **바꾼 모듈의 테스트 파일만** 돌린다. 유닛 전체는 끝에 한 번 (커밋 때 PreToolUse 훅이 다시 돌린다)
- 파이프라인 진입점은 `backend/app/services/pipeline.py`, 외부 모델 호출은 `services/ai/` 에만 둔다

## 돈이 드는 것 — 반드시 먼저 묻는다

- 실제 VLM·fal 호출: `make eval`, `backend/eval/run.py`, `backend/scripts/run_*.py`, 실험 스크립트
- 물을 때 사진 수 × 대략 단가를 같이 말한다 (analyze 약 $0.005, 전체 약 $0.04 / 장, 재시도 별도)
- 유닛 테스트는 conftest 가 실제 클라이언트·DINO·OCR·누끼 모델 로드를 막는다. 이 차단을 풀지 않는다

## 지켜야 할 불변식

- 생성 결과가 "보존됨"으로 나가려면 verify 를 통과해야 한다. 오리기·검사 실패 때 생성본에 초록 배지가 붙는 경로를 만들지 않는다
- `SECONDHAND_LOCK` 은 코드에서 마지막에 붙인다 (`with_secondhand_lock`) — Langfuse 프롬프트로 지워지거나 뒤집히면 안 된다
- 사진에서 읽은 글자는 프롬프트에 넣기 전에 `prompt_safe()` 를 거친다 (외부 입력)
- UI 문구는 실제로 한 일만 말한다 (생성 / 배경 교체 / 원본 그대로). 하자는 자동 검사하지 않는다고 적는다
- 키는 `SecretStr` — 로그·테스트 출력에 평문으로 찍히지 않게

## 이미 정한 것 (근거는 study 노트 — 뒤집으려면 먼저 말한다)

| 결정 | 근거 |
|---|---|
| 하자를 목록(anchor)으로 뽑지 않는다. `wear_level` 로 생성 여부만 정한다 | 09-27 §5·§9 — "지켜라"고 하면 모델이 하자를 지우거나 지어냄 |
| OCR 글자 가드는 기본 꺼짐 (`OCR_GUARD`) | 09-27 §9 — 반려 대부분이 읽기 흔들림 |
| 글자가 곧 상품(책·음반·카드)은 생성하지 않고 표지 펴기 | 09-27 §11 |
| 고주파 섞기는 버림 | 09-27 §8 — 원본 반사와 하자를 구분 못 함 |
| 400px 데이터로 성능을 판단하지 않는다 | 09-27 §8·§10 — 폰 사진에서 결론이 뒤집힘 |

기능을 끌 때는 그것 때문에만 있던 재시도·폴백·API 필드까지 같이 지운다.

## 서브에이전트

- `tester`, `reviewer` 는 **코드가 정해진 뒤 한 번** 부른다 (작업 중에 부르면 한 번에 25분 걸린 적 있음). 마무리 절차는 `wrap-up` 스킬
- tester 가 테스트를 고치는 동안 production 코드를 바꾸지 않는다
- 떨어진 결과(가드·verify 실패본)도 저장해서 사람이 볼 수 있게 한다 — 실험 스크립트 포함

## 문서 · 커밋

- README 는 **한/영 둘 다** 고친다 (`Readme.ko.md`, `Readme.md`)
- 하루 작업은 `study/YYYY-MM-DD-*.md` 에 `study-note` 스킬 규칙대로. "남은 일"은 마지막 "하루 마무리" 섹션 한 곳에만
- 커밋 메시지는 한국어 conventional (`feat:` `fix:` `docs(study):` `chore(hooks):`), 무엇을·왜
- main 에 직접 커밋하지 않는다. PR 은 만들되 머지는 사용자가 한다
- 사진은 git 에 올리지 않는다 — `backend/eval/images/`, `backend/eval/runs/`, `backend/storage/` 의 사진 폴더 (남의 사진 · 번호판 · 얼굴).
  새 사진 폴더를 만들면 `.gitignore` 에 같이 넣는다
