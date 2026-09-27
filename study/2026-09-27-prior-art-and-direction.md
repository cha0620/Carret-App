# 2026-09-27

1. 어제 끊긴 작업 마무리: SessionStart 훅 · 하네스 PR
2. 유닛 테스트는 언제·어떤 단위로 도나
3. 선행 사례 조사: 글자·로고·하자를 보존하는 생성
4. Photoroom 은 어떻게 하나 (공개된 범위)
5. 생성 경로 기준선 · gate 를 통과했지만 물건이 바뀐 4건
6. 방향 전환 논의: 하자 추출 대신 아이덴티티 + 고주파 섞기
7. 고주파 섞기(detail transfer)의 출처

---

## 1. 어제 끊긴 작업 마무리: SessionStart 훅 · 하네스 PR

어제 세션이 한도로 끊긴 지점은 **SessionStart 훅 등록**이었다. 스크립트(`.claude/hooks/session-brief.sh`)는 써 둔 상태였고
테스트 실행과 `settings.json` 등록이 남아 있었다.

- 스크립트 직접 실행 → 최신 study 노트 "하루 마무리"의 남은 일 14개 + 브랜치·마지막 커밋이 나옴
- `settings.json` 등록은 자동 모드에서 **자기 설정 수정**으로 차단 → 사용자 허락 후 등록 (`startup|resume`)
- 그동안 git 에 안 올라가 있던 하네스 파일을 PR 로

| PR | 브랜치 | 내용 | 상태 |
|---|---|---|---|
| #23 | `chore/claude-harness` | 훅 3개(커밋 전 유닛 테스트, md 린트, 세션 브리핑) + 스킬 3개(wrap-up, study-note, eval-report) + 블로그 초안 | open |

커밋: `4e1606d`(하네스), `e32b55b`(블로그 초안에 §3 인용 추가). 앱 코드 변경 없음.

---

## 2. 유닛 테스트는 언제·어떤 단위로 도나

**단위**: 모듈 하나 = 테스트 파일 하나 (`backend/test/software/unit/{service,web,data}`), 함수 하나 = 동작 하나.

**기준**: 돈 0 · 네트워크 0 · 무거운 모델 0. conftest 가 강제한다.

- `unit/conftest.py` `no_real_vlm` — 실제 Gemini 클라이언트 생성 시 예외
- `software/conftest.py` — DINO·EasyOCR 로드 금지, `test/conftest.py` — 누끼 모델 금지, 저장소·DB 임시 폴더
- 백그라운드 스레드가 테스트 뒤에 남으면 실패

**무엇을**: 모델이 잘 그렸나(eval 의 몫)가 아니라, 모델이 이런 답을 줬을 때 코드가 맞게 판단하나 (게이트·재시도·폴백·임계값·프롬프트 조립).

**언제 (혼자 작업할 때)**:

| 시점 | 무엇 | 강제 |
|---|---|---|
| 코드 수정 직후 | tester 서브에이전트 + 관련 테스트 파일 | 메모리 규칙 (Claude 판단) |
| 리뷰 반영 뒤 | 유닛 전체 (~1239개, 25초) | wrap-up 절차 |
| 커밋 직전 | `test/software/unit` 전체, 실패 시 커밋 차단 | **PreToolUse 훅 (강제)** |
| PR | CI (torch 없음) | 자동 |
| eval (`-m eval`) | 실제 VLM·fal | 매번 사용자 확인 |

파일 수정마다 자동으로 도는 장치는 없다 (25초라 강제하면 느려짐). 필요하면 `.py` 수정 시 해당 테스트 파일만 도는 훅을 추가할 수 있다.

---

## 3. 선행 사례 조사: 글자·로고·하자를 보존하는 생성

계기: "하자나 글자를 보존하면서 생성하는 건 내가 거의 처음 하는 것 같다" → 정말 그런지 웹 조사 (검색 6회).

### 3-1. 글자·로고 보존은 모두가 부딪히는 벽

| 출처 | 핵심 |
|---|---|
| [Photoroom Product Fidelity Benchmark][pr-bench] | 상품 850개·3,400장. Nano Banana 2/Pro, GPT Image 2, **FLUX.2 Klein** 중 최고도 상품 완전 유지 **29%**. 실패 1위 로고·글자 왜곡 **20.1%** |
| [Lamina 정리][lamina] | 바코드는 스캔 불가, 성분표는 전부 지어냄. 권장: 배경·조명·구도만 생성 → 진짜 라벨 합성 → 원본과 비교 검증 |
| [ProductConsistency (arXiv 2606.19103)][pc] | SFT(87k)+RL(869). 원본 설명 ↔ 결과 캡션 일치를 보상으로. CER 5배 감소 |
| [Preserving Product Fidelity (arXiv 2503.08729)][ppf] | DreamBooth·InstructPix2Pix 를 상품에 쓰면 로고 등 세부가 왜곡 |
| [Edit Fidelity Field (arXiv 2604.17500)][eff] | 글자 편집 시 **바꾸지 않을 글자 영역의 94%**까지 같이 변형 (edit spillover) — TEXT_LOCK 이 막으려는 현상 |
| [UnicEdit-10M (arXiv 2512.02790)][unic] 등 | VLM 으로 "바뀌면 안 되는 속성 유지" 채점 — verify 와 같은 발상 |

### 3-2. 중고 하자 보존은 사례를 못 찾음

- 규정·가이드는 있다: [Vinted][vinted] (실물과 다르게 보이면 정지), [중고 촬영 가이드][snappy] (얼룩·구멍·보풀은 지우지 말 것),
  [부동산 AI 사진 논란][re-news]
- 하지만 **사람에게 하는 말**이다. 생성 모델이 하자를 못 지우게 막고 확인하는 시스템은 못 찾음
- "defect + diffusion" 논문은 공장 불량 검출용으로 하자를 **만들어 넣는** 반대 방향 ([ICCV 2025 예][iccv])

### 3-3. 판단

- "처음"은 **하자 쪽만** 맞다. 글자 쪽은 업계 공통 난제이고, 우리가 도달한 "부탁 → 확인 → 포기(합성)"는 업계 권장 흐름과 같다
- 29% 는 "상용화 안 됨"이 아니다. **오려 붙이기 + 배경 생성**은 상용화돼 있고(Photoroom, Depop), 29% 는
  **편집 모델이 물건까지 다시 그리는 방식**을 검사 없이 내보낼 수 있는 비율이다

---

## 4. Photoroom 은 어떻게 하나 (공개된 범위)

내부 구현(모델 구조, 비교 방법, 재시도 규칙)은 비공개. 공식 블로그([fidelity gap][pr-gap], [nobody returns a photo][pr-inside])에 나온 틀:

| Photoroom | Carret |
|---|---|
| Fidelity Layer: 결과를 원본과 비교 → 문제 추론 → **위치 특정** → 그 분석으로 재생성 (29.0% → 38.2%) | 가드·verify(`box_2d`) → 반려 사유 재생성 |
| Visual QA: 입력 분석 → 업종별 fidelity 모델 채점 → 수정 프롬프트 재시도 → 통과만 게시 | `plan` → 가드·verify → 재시도 → 배경 교체 |
| Brush Fixer (사용자가 칠한 영역만 재생성) | 없음 |
| 자체 모델 PRX Pixel (압축 없이 픽셀 생성) | 외부 FLUX |

원인 진단도 같다: 모델이 압축했다가 다시 그려서 "복원이 아니라 **재구성**".

**채점 방법 (빌려 올 것)**: 평가자 10명, 한 장당 최소 3명, **한 명이라도 지적하면 실패**, 원본·결과 나란히 + 확대 + 최소 30초.
실패 유형 5개: 로고·글자 20.1 / 요소 누락·변경 12.5 / 무늬 11.4 / 색 8.1 / 가상 모델 6.7 (%).

---

## 5. 생성 경로 기준선 · gate 를 통과했지만 물건이 바뀐 4건

`eval-report` 스킬, 로컬 `backend/storage/quality` (운영은 S3 라 미포함). 현재 파이프라인 버전(09-26) 11건만. **표본 부족 — 방향만.**

| 지표 | 값 |
|---|---|
| 생성 / 합성 | 10 / 1 |
| anchor 있는 8건 중 첫 시도 gate 통과 | 4/8 |
| 재시도 후 통과 | 3/8 (합성 1) |
| gate 통과 + judge fidelity 5 ("완전 보존") | anchor 있는 생성 7건 중 **3건** |

gate 는 통과했는데 judge fidelity 가 낮은 4건을 원본과 나란히 봤다.
비교 이미지: `backend/storage/result/_review/` (git 제외), 뷰어 `make docs` → `/backend/storage/result/_review/index.md`

| 건 | fidelity | 눈으로 본 것 | 유형 |
|---|---|---|---|
| 58a22b52 의자 | 2 | 원본은 위에서 찍어 다리가 안 보이는데 **나무 다리 추가**, 구도·색 바뀜, 팔걸이에 없던 부품, 사용감 매끈 | 추가·형태 |
| 570193c6 Braun | 3 | 왼쪽 몸통에 **없던 긁힘 두 군데**, 흐릿하던 버튼 글자를 선명하게 다시 그림 + 없던 "start/stop" | 하자 지어냄 |
| 6e733abc 주전자 | 3 | 거울 크롬 + 찌그러짐 → **매끈한 헤어라인 스테인리스** | 재질·상태 소실 |
| e666df1f Kitty | 4 | 전원 코드·계량 스푼 사라짐, 주전자에 없던 은색 띠, 스위치에 "ON" | 누락·추가 |

**알게 된 것**

1. verify 는 "있어야 할 게 **있나**"만 본다 → **없던 게 생김**, **목록에 없던 상태가 사라짐**, **구성품 누락**을 못 잡는다
2. SECONDHAND_LOCK("흠집을 지켜라")이 역효과 — Braun 은 흠집을 **만들었고**, 그러면 verify 도 "보존됨"으로 통과
3. 원본에서 안 보이는 부분(의자 아래)은 모델이 상상해서 그린다
4. judge 는 네 건을 다 잡았지만 **백그라운드 채점**이라 결과를 막지 않는다

---

## 6. 방향 전환 논의: 하자 추출 대신 아이덴티티 + 고주파 섞기

### 6-1. 문제 제기 (사용자)

> anchor 를 하나하나 다 검사할 수도 없는 게, 전반적으로 상태가 이상한 걸 하나하나 다 잡을 순 없잖아
>
> 하자 추출은 없애는 방향으로 … 마크나 그래픽, 텍스트 같은 그 상품의 아이덴티티만 가져오고, 손상된 건 아예 똑같이는 못 하잖아

맞다. anchor 는 **이름 붙일 수 있는 것**만 지킨다. detect 혼동(R/P 0.67), 흠집을 지어냄, 목록에 없는 사용감(주전자) — 셋 다 목록 방식의 한계다.
반대로 마크·그래픽·글자는 "원본과 같은가"의 정답이 분명해서 검사가 잘 된다.

### 6-2. 우려 (Claude)

하자 **검사**를 빼면 결과가 흠집을 지워도 아무도 모른 채 나간다 = 허위 매물. §3 에서 찾은 차별점도 하자 쪽이었다.
그래서 검사를 빼더라도 정직함을 지킬 장치가 하나는 있어야 한다: ① 구조로 보존(고주파 섞기) ② 원본을 함께 보여주기 ③ judge 안전망.

### 6-3. 정한 방향 (사용자: "1로 해보자" + 초기 분류)

1. **detect**: anchor 는 아이덴티티(마크·그래픽·글자)만 + `text_level` + **`wear_level`**(none / light / heavy) — 호출 수 그대로
2. **plan**: `dense` 또는 `heavy` → 생성하지 않고 배경 교체 (하자가 너무 많으면 처음부터 배경만)
3. **생성 뒤**: 원본의 **고주파를 섞어** 상태 보존 — 무엇이 하자인지 몰라도 표면의 잔 무늬를 통째로
4. **검사**: 아이덴티티 anchor + judge fidelity 안전망 (≤3 반려는 judge 를 눈으로 검증한 뒤)

원칙 문장이 "배경은 바꾸고 상태는 바꾸지 않는다" → "배경은 바꾸고, 정체는 지키고, 상태는 원본대로"로 바뀔 수 있다 — README·블로그 반영은 실험 뒤에.

### 6-4. 남은 일

- [ ] 고주파 섞기 실험: 기존 결과 4건(§5), 누끼 → ECC 정렬 → 주파수 분리, 로컬 무료. 정렬 실패율부터
- [ ] judge fidelity 가 맞는지 11건 눈으로 확인 → gate 로 쓸지
- [ ] detect 에서 하자 anchor 제거 + `wear_level` 추가, plan 분기
- [ ] "없던 게 생김"(형태 추가, 의자 다리) 검사 — 섞기로는 못 막는다
- [ ] SECONDHAND_LOCK 문구: 목록에 없는 흠집을 그리지 말 것
- [ ] eval 지표 "완전 보존 비율" (Photoroom 29% 와 같은 형식), 채점은 "하나라도 이상하면 실패 + 나란히 확대"
- [ ] 원본 사진 함께 보여주기(표시)는 보류 — 실험 결과 보고 결정

---

## 7. 고주파 섞기(detail transfer)의 출처

"저주파(조명·색)는 생성본, 고주파(질감·긁힘)는 원본"은 새 발명이 아니다.

| 출처 | 내용 |
|---|---|
| [ComfyUI-IC-Light-Native][icl] | IC-Light 구현에 **DetailTransfer 노드** 기본 포함 — 입력 전경의 고주파 보존 |
| [RunComfy 가이드][icl-guide] | 원본 고주파를 재조명 결과로 옮겨 특징 손실 방지 |
| [Civitai: Product Photography Relight v3][civitai] | 상품 사진 재조명 + frequency separation 으로 글자 등 세부 유지 — **가장 가까운 사례** |
| [OpenArt][openart] / [RunComfy 영상][runcomfy-video] | IC-Light 는 낮은 denoise 에서 디테일을 잃어 마지막에 frequency separation 으로 복원 |
| 일반 지식 | 사진 리터칭의 frequency separation, Laplacian pyramid blending (Burt & Adelson, 1983) |

- 기법은 기존 것, 목적은 모두 **글자·로고 보존**. **하자·사용감 보존**에 쓴 사례는 못 찾음
- 차이: IC-Light 는 구도를 거의 안 바꿔 픽셀이 맞는다. FLUX edit 은 물건을 옮기므로 **정렬(ECC)이 먼저** — §6-4 첫 실험의 핵심

위키: `concepts/frequency-separation-detail-transfer`, `sources/frequency-separation-sources-2026-09-27`,
`sources/prior-art-search-2026-09-27`, `syntheses/carret-prior-art-2026-09-27`, `entities/photoroom` (위키 커밋 `16204ee` `9f12d43` `b4580d5`)

[pr-bench]: https://www.photoroom.com/blog/top-editing-image-models-maintain-product-details-only-28-of-the-time
[pr-gap]: https://www.photoroom.com/blog/fidelity-gap-ai-product-photography
[pr-inside]: https://www.photoroom.com/inside-photoroom/nobody-returns-a-photo
[lamina]: https://uselamina.ai/blog/benchmark-can-ai-product-photography-generate-ecommerce-ready-images-without-changing-logos-labe
[pc]: https://arxiv.org/abs/2606.19103
[ppf]: https://arxiv.org/abs/2503.08729
[eff]: https://arxiv.org/pdf/2604.17500
[unic]: https://arxiv.org/pdf/2512.02790
[vinted]: https://vintefy.com/en/news/2026/08/vinted-ai-photos
[snappy]: https://snappyit.ai/blog/how-to-photograph-thrifted-clothes-for-resale
[re-news]: https://www.realestatenews.com/2026/09/08/ai-modified-listing-photos-blur-line-between-enhancement-deception
[iccv]: https://openaccess.thecvf.com/content/ICCV2025/papers/Xu_Training-Free_Industrial_Defect_Generation_with_Diffusion_Models_ICCV_2025_paper.pdf
[icl]: https://github.com/huchenlei/ComfyUI-IC-Light-Native
[icl-guide]: https://www.runcomfy.com/comfyui-nodes/ComfyUI-IC-Light-Native
[civitai]: https://civitai.com/articles/5393/product-photography-relight-v3-with-internal-frequency-separation-for-preserving-details
[openart]: https://openart.ai/workflows/risunobushi/video-relighting-detail-and-color-transfer-non-animatediff/YxuxT1PkjeWCmnpSpwPs
[runcomfy-video]: https://www.runcomfy.com/comfyui-workflows/comfyui-product-relighting-video-workflow
