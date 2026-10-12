# 정직함 지표 공부 (2026-10-11 ~)

목표: "통과 배지를 단 사진 중 원본과 다른 점이 있는 것은 ε 이하 (신뢰 95%)" 라고 **보증**할 수 있는 지표.
배경 · 결정은 `study/2026-10-11-choice-and-inpaint.md` §4 · §5 · §9.

## 자료별 정리

| 파일 | 자료 | 한 줄 |
|---|---|---|
| [01-amazon-product-fidelity.md](01-amazon-product-fidelity.md) | Amazon, *Preserving Product Fidelity* | 물건 유사도 순위 — 사람과 Pearson 0.4 |
| [02-product-consistency.md](02-product-consistency.md) | *ProductConsistency* | SFT + RL, 유사도 보상은 베끼기로 해킹됨 |
| [03-dreambench-pp.md](03-dreambench-pp.md) | *DreamBench++* | DINO 는 모양만 본다 |
| [04-viescore.md](04-viescore.md) | *VIEScore* | VLM 점수 — 편집에선 약함 |
| [05-imgedit-gedit.md](05-imgedit-gedit.md) | *ImgEdit* · *GEditBench v2* · *GIE-Bench* | 학습한 심사 모델 · 보존 점수 |
| [06-tifa-dsg.md](06-tifa-dsg.md) | *TIFA* · *DSG* | 질문으로 쪼개 묻기 — 사람과 더 맞음 |
| [07-spotedit.md](07-spotedit.md) | *SpotEdit* | 할루시네이션 전용 분할 |
| [08-refadgen.md](08-refadgen.md) | *RefAdGen* | 마스크 + 원본 참조로 생성 |
| [09-ebay-background-swap.md](09-ebay-background-swap.md) | eBay 배경 교체 | 물건은 다시 그리지 않음 |
| [10-conformal-guarantee.md](10-conformal-guarantee.md) | Conformal risk control · Conf-Gen · CRC-Derive | 보증을 만드는 통계 + 필요한 표본 수 |
| [11-carret-design.md](11-carret-design.md) | (우리 설계안) | 위를 Carret 에 맞춘 것 |
| [12-small-verifier-plan.md](12-small-verifier-plan.md) | (계획, 10-13 시작) | 원본 · 결과 쌍이 다른가를 맞히는 작은 모델 — DINO 위 작은 머리 |

## 결론 세 줄

1. 임베딩 유사도 · VLM 점수는 **순위 · 거르기 도구**이지 보증이 아니다 (01 · 02 · 03 · 04)
2. 검사는 **원본에서 만든 질문으로 쪼개** 묻는 쪽이 해석되고 사람과 더 맞는다 (06 · 07)
3. 보증은 **사람 정답 세트에서 잰 틀린 통과율의 신뢰 상한**에서만 나온다 (10)

## 확인 정도

- 숫자는 논문 본문 · 검색 요약에서 읽은 것. 원문 전체를 다 읽은 건 01 · 02 뿐 — 나머지는 요약 수준, 쓰기 전 원문 확인
- 10 의 표본 수 표는 직접 계산 (Clopper–Pearson, scipy)
