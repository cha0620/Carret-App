# 2026-09-27

1. 어제 끊긴 작업 마무리: SessionStart 훅 · 하네스 PR
2. 유닛 테스트는 언제·어떤 단위로 도나
3. 선행 사례 조사: 글자·로고·하자를 보존하는 생성
4. Photoroom 은 어떻게 하나 (공개된 범위)
5. 생성 경로 기준선 · gate 를 통과했지만 물건이 바뀐 4건
6. 방향 전환 논의: 하자 추출 대신 아이덴티티 + 고주파 섞기
7. 고주파 섞기(detail transfer)의 출처
8. 고주파 섞기 실험 → 효과 없음 · 원인은 입력 화질
9. 폰 사진 6장 · 첫 단계를 analyze 로 · 하자 앵커 제거 · OCR 가드 끔 (PR)
10. 하루 마무리
11. 글자가 곧 상품인 사진 · 책 표지 펴기 · 파이프라인 다시 정리
12. 사진 종류 셋으로 시작 · 글자 가드 경로 제거 · 반영 안 된 곳 정리
13. 이미지 생성을 실제로 쓴 곳들의 경험 (데이터셋 만들기 전에)
14. 평가 도구 — 라벨 양식 · 실행 · 채점 · 집계
15. 하루 마무리 (밤)

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

### 6-4. 남은 일 (최종 목록은 §10)

- [x] 고주파 섞기 실험: 기존 결과 4건(§5), 누끼 → ECC 정렬 → 주파수 분리, 로컬 무료. 정렬 실패율부터 → §8
- [ ] judge fidelity 가 맞는지 11건 눈으로 확인 → gate 로 쓸지
- [x] detect 에서 하자 anchor 제거 + `wear_level` 추가, plan 분기 → §9
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

---

## 8. 고주파 섞기 실험 → 효과 없음 · 원인은 입력 화질

### 8-1. 섞기 (§6-4 첫 실험)

스크립트: scratchpad `hf_blend.py` / `hf_blend_sift.py` (로컬 rembg + ECC / SIFT, 비용 0). 결과 페이지(비공개 artifact): 고주파 섞기 복원 결과.

| 건 | 정렬 | 결과 |
|---|---|---|
| 주전자 | ECC 0.80, 특징점 44 | 정렬은 됐지만 돌아온 건 하자가 아니라 **원본 방 안의 반사** |
| Kitty | 특징점 75 | 몸통만 맞음. 유리 주전자·손잡이 이중 상 |
| Braun | 특징점 9 | 정렬 불가, 전체 겹침 |
| 의자 | 특징점 6 | 정렬 불가 (구도 변경) |

처음에 주전자를 "성공"으로 적었다가 사용자가 "전혀 효과가 없다"고 지적 — 맞다. 원본 고주파에는 하자와 촬영 환경 반사가 섞여 있어 구분할 수 없고,
FLUX 가 모양을 바꾸면 맞출 수도 없다. **FLUX 결과에 나중에 섞는 방식은 버린다.** IC-Light V2(모양 유지 재조명, fal $0.1/MP)도 반사 문제는 같아 보류.

### 8-2. 방향: 생성은 그대로, 검사 강화 (사용자: "2번이 맞지")

정렬 신호(물건 누끼끼리 SIFT 인라이어)를 "딴 물건이 됐나" 검사로 쓸 수 있는지 28건 전체로 확인 (로컬 누끼, 비용 0):

- 인라이어 ≤ 13 인 8건: judge fidelity 2·3·5·3·5·3·5·5 → **절반은 멀쩡한 결과** (오차단)
- 인라이어 ≥ 60 인 12건: fidelity 5 가 11건 (나머지 1건은 합성 결과)
- 가운데(18~57)는 섞여 있다 → 지금 데이터로는 기준값을 못 정한다

### 8-3. 원인: 테스트 입력이 너무 작다 (사용자 지적)

> 데이터셋은 너무 화질이 안 좋아서 그런 것 같은데, 기본적으로 인풋은 사진을 찍으니까 구도는 엉망이어도 화질은 좋을 거 아니야

맞다. 로컬 결과 28건 중 **27건이 SOP 데이터셋 원본 약 400px**(160~500px), 결과는 1024×1024.
FLUX 가 2.5~6배를 키우면서 없는 디테일을 **지어낼 수밖에 없다** — 매끈해진 재질(주전자), 지어낸 긁힘(Braun), 다시 그린 버튼 글자가 이것으로 설명된다.
특징점도 작은 이미지에서는 적게 잡혀 신호가 흔들린다. 실제 사용자는 폰으로 찍으니 구도는 엉망이어도 해상도는 높다.
**지금 데이터셋으로 내린 판단(FLUX 가 물건을 바꾼다, 정렬 신호가 약하다)은 실제 입력에서 다시 재야 한다.**

- [ ] 폰으로 찍은 고해상도 테스트 셋 (구도는 엉망, 하자 있는 것 포함) — §6-4 하자 사진 20장과 합친다
- [ ] 그 셋으로 파이프라인 다시 돌려 gate·judge·정렬 신호 비교 (유료, 장당 FLUX + VLM)
- [ ] 정렬 신호를 soft 가드로 넣어 값부터 모으기 (item_dino 와 같은 방식)

---

## 9. 폰 사진 6장 · 첫 단계를 analyze 로 · 하자 앵커 제거 · OCR 가드 끔 (PR)

브랜치 `feat/analyze-step` (PR #23 위에 쌓음). 결과 페이지(비공개 artifact) 2~4단계에 사진 전부.

### 9-1. 폰 사진 6장 (중고나라, 750~824px) — 옛 파이프라인

사용자가 `phone_img/` 에 6장(오토바이·승용차·굴삭기 엔진룸·폴로티·축구 유니폼·코트)을 넣음. SOP(약 400px)의 두 배.

- 생성 2장, **합성 4장** — 전부 글자 가드(ocr_match hard) 때문
- 떨어진 생성본이 저장되지 않아 볼 수 없었다 → 사용자: "실패했을 때의 이미지는 같이 보여줘야지".
  실험 스크립트에서 `_generate_ai`·`_run_guards`·`_ocr_pair` 를 몽키패치해 시도마다 저장하고 다시 돌림

| 사진 | 떨어진 생성본을 보니 |
|---|---|
| 오토바이 | 지난번 0.887 로 떨어졌는데 이번엔 통과 — 같은 사진에서 판정이 갈림 |
| 승용차 | 1차는 깨끗했는데 녹 앵커 "사라짐" → "반드시 남겨라" 재생성 → **원본보다 큰 녹을 그려 넣음**. 2차 반려는 "17어"→"17에" 읽기 |
| 굴삭기 | 엔진룸 사진에서 **운전석·차체를 지어냄**, 녹슨 범퍼가 깨끗해짐. 반려는 "00 3060"을 "00"+"3060"으로 쪼개 읽은 것 |
| 유니폼 | 1차는 워터마크까지 지워진 좋은 결과인데 "H.M"→"H-M" 으로 반려. 2차는 가슴에 "F H.M" 글자를 새로 그림. 최종 합성본엔 워터마크가 남음 |

**알게 된 것**: 글자 가드 반려의 대부분은 **읽기 흔들림**이었다 — 검사기의 오차가 생성기의 오차보다 컸다.
진짜 문제(하자·물건을 지어냄)는 하자 앵커와 엔진룸 같은 사진 유형에서 나왔다.

### 9-2. 설계 (사용자 제안: "처음 분류를 분석으로 바꾸고 워터마크, 하자·글자 복원 가능성까지 보자" + "OCR 은 빼자")

- **analyze** (VLM 1회, 예전 classify + detect 2회): item · 아이덴티티 마크(로고·글자·그래픽만, 하자 앵커 없음) · item_box ·
  `scene`(single_item / partial_view / multiple_items) · `wear_level`(none / light / heavy) · `watermark`(none / background / on_item) · `text_level`
- **plan**: partial_view → `keep_original`(원본 그대로, mode original) / 분석 실패·text dense·wear heavy → 배경 교체 / 나머지 생성.
  `many_defects`·`composite_first_min_anchors` 삭제
- **verify**: 마크·주요 글자만. 새 프롬프트 `verify_v2` (옛 `verify` 는 "defects and marks" 전제 — VLM 이 하자 항목을 보태면 게이트가 떨어짐)
- **OCR 글자 가드**: `OCR_GUARD`(기본 False). 켜면 비교는 띄어쓰기·구두점·대소문자 무시, 2~3줄로 쪼개 읽은 건 정확히 이어질 때만 인정
- "?"(원본에서도 못 읽음, 전각 포함) 글자는 검사·TEXT_LOCK 모두에서 제외
- `watermark`·`multiple_items` 는 기록만 (라우팅 없음)

처음 규칙("글자를 그대로 옮겨 적을 수 없으면 dense")은 폴로티 목 라벨·승용차 번호판에 걸려 둘 다 합성으로 갔다 →
"물건의 **주요** 글자만, 라벨·번호판 제외"로 좁힘.

### 9-3. 새 파이프라인 결과 (같은 6장)

| 사진 | analyze | 결과 | judge (충실·사실·신뢰) |
|---|---|---|---|
| 오토바이 | 하자 none · 글자 simple | 생성 1회 | 4 · 4 · 3 |
| 유니폼 | 워터마크 on_item | 생성 1회 — 워터마크 지워지고 로고·글자 유지 | 4 · 4 · 4 |
| 코트 | 하자 none | 생성 1회 | 3 · 4 · 4 |
| 폴로티 | 하자 none | 생성 1회 | 4 · 4 · 4 |
| 승용차 | 하자 light | 생성 1회 — **하단 얼룩 정돈, 번호판 "17? 5433"→"170 5413"** | 3 · 4 · 3 |
| 굴삭기 | partial_view · 하자 heavy | 원본 그대로 | — |

승용차가 **하자 검사를 뺀 대가**다. 하자 light 인 물건의 생성본이 하자를 정돈해도 judge 점수로만 보인다 →
UI 생성본 배지에 "흠집·얼룩은 자동 검사하지 않아요, 원본 사진으로 확인해 주세요"를 붙임. 번호판은 가리는 쪽이 나을 수도.

### 9-4. reviewer · tester

| 누가 | 지적 | 조치 |
|---|---|---|
| reviewer | 테스트 460 errors, integration `test_dev_replay` 가 analyze 를 mock 안 해 **실제 Gemini 호출 가능** | tester 가 전부 갱신 + integration 에도 get_client 차단 conftest |
| reviewer | wear heavy 인데 오리기 실패 → 생성으로 가서 초록 "보존됨" 배지 | 원본 그대로(`_original_as_result`) |
| reviewer | README·UI 가 "하자 보존 검증"을 약속 | README 한/영, UI 문구("주요 로고·글자", 하자 미검사 안내), dev 화면 라벨 |
| reviewer | text_match 부분 문자열 구제 → "500"/"1500", "3060"/"13060" 거짓 통과 | 조각을 **정확히** 이을 때만 (순서 무관, 2~3줄) |
| reviewer | analyze 마크가 prompt_safe 없이 verify 프롬프트로 (사진 속 글자 = 외부 입력) | `_text()` = prompt_safe |
| reviewer | verify 프롬프트가 하자를 전제 → 하자 항목을 보태 오반려 | `verify_v2` (마크만, 항목 수 그대로, checklist 안 넘김) |
| reviewer | "?" 글자가 TEXT_LOCK 으로는 들어감 | `presets.unreadable` 로 둘 다 제외 |
| reviewer | multiple_items 미사용, 분류 fallback 이 조용함 | 안 함 — 기록만 하고 값부터 모은다 (inspect 에 남음) |
| tester | JSON null → "None" 마크 | `_text()` 가 None → "" |
| tester | 역순 쪼개 읽기 ["3060","00"] 미구제, 한 글자 새 줄이 added 에서 빠짐 | `_split_match`·`_fragment_of` |
| tester | dev 결과·API 응답에 scene/wear/watermark 없음 | 추가 |

별도로 **테스트 실패 출력에 AWS 키가 평문으로 찍히는 것**을 발견 → 키 설정을 `SecretStr` 로 (PR #24, `fix/secret-settings`).

---

## 10. 하루 마무리

### 10-1. PR 상태

| PR | base | 내용 | 상태 |
|---|---|---|---|
| #23 | main | 하네스(훅·스킬) + 블로그 초안 + 이 노트 §1~8 | open |
| #24 | main | 키 설정 SecretStr (테스트 출력에 AWS 키 평문) | open |
| #25 | #23 위 | analyze 단계 · 하자 앵커 제거 · OCR 가드 끔 · 이 노트 §9~10 | open |

머지 순서: #23 → #25 (base 가 main 으로 바뀜), #24 는 독립. 배포 때 Langfuse 에 `analyze`·`verify_v2` 시딩.

### 10-2. 남은 일 (최종 목록은 §15)

측정:

- [ ] 실제 폰 원본(3000px+) 테스트 셋 — 이번 6장도 한 번 줄어든 750px. 하자 사진 20장 촬영과 합친다
- [ ] analyze 분류 정확도 (scene·wear_level·text_level) — 폴로티 라벨처럼 과민한 경우가 또 있나
- [ ] judge fidelity 가 맞는지 눈으로 확인 → 안전망(≤3 반려)으로 쓸지
- [ ] 정렬 신호(누끼 SIFT 인라이어)를 soft 가드로 — 고해상도에서 다시 재기
- [ ] eval 지표 "완전 보존 비율" (Photoroom 29% 형식)

정직성:

- [ ] 하자 light 인 물건의 생성본에서 하자가 정돈되는 것 (승용차) — 지금은 UI 고지 + judge 관측뿐
- [ ] 번호판: 가릴지, 글자 검사에서 뺄지
- [ ] watermark on_item 인데 배경 교체로 가는 경우 (합성은 못 지움)
- [ ] 원본 사진 함께 보여주기 (§6-4 보류분)

구조·정리:

- [ ] 사진 속 "없던 게 생김"(의자 다리, 은색 띠) 검사
- [ ] 배경 교체 결과 품질: 구도 다시 잡기 · 비생성형 업스케일
- [ ] pre-commit 훅이 `git commit` 이 아닌 명령(heredoc·git diff 섞인 복합 명령)에도 걸리고, 위키 저장소 커밋에도 본 저장소 테스트를 돌린다
- [ ] 위키 저장소 GitHub 원격 연결 (지금 로컬 커밋만)

### 10-3. 교훈 세 줄

1. **떨어진 결과도 저장해서 본다** — 글자 가드 반려의 대부분이 검사기의 읽기 흔들림이었다. 검사기의 오차가 생성기보다 클 수 있다.
2. **지켜야 할 것을 목록으로 강조하면 모델은 그것을 그린다** — 하자는 수준으로 보고, 많으면 생성하지 않는다.
3. **테스트 입력은 실제 입력 화질로** — 400px 데이터로 내린 "생성이 물건을 바꾼다"는 판단은 폰 사진에서 대부분 뒤집혔다.

---

## 11. 글자가 곧 상품인 사진 · 책 표지 펴기 · 파이프라인 다시 정리

마무리(§10) 뒤에 이어서 한 일. 커밋 전, 브랜치 `feat/analyze-step`.

### 11-1. OCR 을 뺀 뒤 글자는 누가 지키나

§9 에서 OCR 글자 가드를 껐다. verify(VLM)는 여전히 **마크와 주요 글자**(브랜드명·모델명)를 본다.
빠진 건 번호판·목 라벨·작은 인쇄 같은 부수 글자다. 처음 답은 "프론트로 넘기자"였다
(원본 사진 함께 싣기 · 판매자 확인 체크 · 글자 위치 확대 보기).

그럼 옷 그래픽 말고 글자가 중요한 경우가 언제냐를 따져 봤다:

| 부류 | 예 | 바뀌면 |
|---|---|---|
| 글자가 곧 상품 | 책 제목·판, 음반, 게임 타이틀, 티켓·상품권 | 다른 물건을 파는 셈 |
| 스펙·모델 식별 | 렌즈 "24-70mm f/2.8", "RTX 4070", "256GB", 사이즈 태그 | 가격이 달라지는 정보가 틀림 |
| 정품 증빙 | 가방 각인·시리얼, 시계 다이얼, 스니커즈 품번 | 가품 의심 · 허위 표시 |
| 상태 정보 | 주행거리, 배터리 성능, 셔터 카운트, 유통기한 | 상태를 속임 |

**결정 (사용자)**: 스펙·정품·상태는 **판매 정보로 따로 입력받으면 될 일**이지 사진에서 보정할 대상이 아니다.
사진으로만 전달되는 건 첫 부류뿐 — 책·음반은 생성하지 않고 배경 교체로 간다.
기준이 "글자가 많냐"(text_level)에서 "틀리면 거래가 달라지냐"로 옮겨 갔다.

### 11-2. `text_is_product` — analyze 필드 하나 + plan 분기

- 프롬프트 `text_level_analyze.md` 8번: 책·잡지·만화·음반 커버와 CD·LP 자체·게임/영화 케이스·트레이딩 카드와 포토카드·포스터·티켓·상품권은 true.
  전자제품·옷·화장품·상품 포장은 글자가 많아도 false
- `detector._product_flag`: JSON true 나 `"true"` 만 참. 필드가 없으면 로그 — Langfuse 에 옛 `analyze` 가 남아 있으면 기능이 통째로 꺼지므로
- `plan` 우선순위: detect_failed → partial_view(원본 그대로) → **text_product** → text_dense → wear_heavy
- 오리기 실패: 전엔 생성으로 돌아갔다 → 제목이 바뀌어도 "보존됨" 배지. wear_heavy 처럼 **원본 그대로**로 바꿈 (reviewer)
- UI 배지 두 개 (배경 교체 / 원본 그대로), inspect 에 `text_is_product`, eval-report 에 분포 줄, README 흐름도

### 11-3. 책 표지 펴기 (`compositor.compose_flat`)

사용자가 `phone_img/` 에 책 2장(치과보철학 하드커버, 비닐 포장된 선형대수)과 목표 예시 `answer1.jpg`
(정면 표지 · 밝은 배경 · 옅은 그림자, 쇼핑몰 표지 컷)를 넣음. "책은 answer1 처럼 깔끔하게".

일반 배경 교체는 비스듬한 책을 비스듬한 채 붙인다. 표지는 평면이니 **원근만 펴면** 된다 — 생성 없이 원본 픽셀.

1. 오리기 알파(fal birefnet) → 가장 큰 윤곽의 볼록 껍질 → 네 점이 될 때까지 `approxPolyDP`
2. 오리기 면적 / 사각형 면적이 0.93~1.07 밖, 볼록 아님, 짧은 변 32px 미만, 떨어진 덩어리 둘 이상(여러 권) → 일반 배경 교체
3. `warpPerspective` 로 정면화 → 1024 캔버스 가운데 · 오른쪽 아래로 떨어지는 흐린 그림자

**가로세로 비 — 처음 시도는 틀렸다.** 비스듬히 찍으면 세로가 짧아져 책이 납작해 보여, 한 장으로 직사각형의 실제 비를
추정하는 Zhang–He 방법을 넣어 봤다. 결과가 두 권 다 **더 넓게** 나왔다:

| 책 | 모서리 길이 그대로 | Zhang–He |
|---|---|---|
| 치과보철학 | 0.81 | 0.86 |
| 선형대수 | 0.87 | 0.95 |

국내 책은 대개 0.7대다. 이 방법은 사진 가운데가 렌즈 중심이라고 가정하는데, 중고나라 사진은 잘리고 줄어서 가정이 깨진다
(선형대수는 오른쪽이 사진 밖으로 잘리기까지). 모서리 길이 그대로를 쓴다.

실제 파이프라인 결과 (analyze VLM 1회 + fal 오리기, 생성 호출 없음):

| 책 | analyze | 경로 | verify |
|---|---|---|---|
| 치과보철학 | book · single_item · 하자 light · text_is_product | text_product → 표지 펴기 | 통과 |
| 선형대수 | textbook · single_item · 하자 light · text_is_product | text_product → 표지 펴기 | 통과 |

남는 한계: 비닐 반사·구김은 원본 픽셀이라 그대로, 사진 밖으로 잘린 표지는 복원 안 함,
배경은 프리셋(studio_white 는 옅은 회색) — answer1 처럼 순백으로 할지 미정.

### 11-4. reviewer · tester

| 누가 | 지적 | 조치 |
|---|---|---|
| reviewer | text_product 인데 오리기 실패 → 생성으로 가서 "보존됨" 배지 | 원본 그대로 + 전용 배지 |
| reviewer | inspect 에 필드 없음, `_is_true` 가 조용히 False | inspect 기록 · 필드 없음 로그 |
| reviewer | 포토카드·디스크 라벨 누락, 상품 포장 경계 | 프롬프트에 추가 |
| reviewer | 수집품 박스(보드게임·레고)·밴드 티는 false → 생성 | 안 함 — 의도대로 둔다 |
| reviewer | `_order_corners`(x+y / y−x 로 점마다 고르기)가 45도 근처에서 같은 점을 두 번 고름 | 둘레 순서를 그대로 쓰고 시작점·방향만 맞춤 |
| reviewer | 짧은 변 0 → warp assertion / 0 나누기, 펴다가 예외 → 원본 그대로 | 짧은 변 검사 + 예외 시 일반 배경 교체 |
| reviewer | 떨어진 여러 권 → 가장 큰 한 권만 남음 | 둘 이상이면 일반 배경 교체 |
| reviewer | 사진 가장자리에서 잘린 표지도 사각형으로 통과 | 안 함 — 선형대수가 바로 그 경우이고 결과가 쓸 만했다 |
| reviewer | 누운 책·90도 돌아간 표지를 돌려세우지 않음, 펼친 책은 휜 면을 평면으로 폄 | 남은 일 |
| tester | 새 테스트 101개 (파싱 값·우선순위·라우팅·오리기 실패 폴백·그래프 전체) | 단위 1578 passed |
| (훅) | 기존 compose 헬퍼가 compose_flat 을 안 막아 테스트 1건 실패 | 헬퍼가 둘 다 막음 |
| tester | 표지 펴기 테스트 36개 (모서리 순서·사각형 판정·펴기·폴백 4종·사유별 compose 분기). 옛 순서 방식이 45도에서 표지 없는 그림을 내는 것도 재현 | 단위 1614 passed. `isContourConvex` 거부 분기만 합성 알파로 못 만들어 미검증 |

### 11-5. 파이프라인 지금 모양

```text
load → analyze (VLM 1회: 물건·마크·물건 위치·scene·wear·watermark·text_level·text_is_product)
  → plan
      partial_view                                   → keep_original (원본 그대로)
      분석 실패 / 글자가 곧 상품 / 글자 dense / 하자 heavy → composite
                                                        (text_product 는 표지 펴기)
      글자 simple → read_text (12줄 이상이면 composite) → generate
      글자 none                                       → generate
  generate (FLUX.2) → validate_result (구도·자막 / item_dino soft / OCR 가드는 OCR_GUARD 일 때만)
      가드 실패 1회 → seed 바꿔 재생성, 2회 → composite
  → score_similarity → verify (마크·주요 글자, verify_v2)
      실패 1회 → 바뀐 마크를 프롬프트에 넣어 재생성, 2회 → composite
  → finalize → (그래프 밖) judge_and_save

composite 오리기 실패:
  가드 불합격 뒤        → blocked (원본)
  하자 heavy · 글자가 곧 상품 → 원본 그대로
  그 밖에 생성 전       → 생성으로
  생성 뒤               → 생성본 유지
```

하자는 목록으로 검사하지 않는다 — 수준(wear_level)으로 plan 에서만 본다.
생성본 배지는 "주요 로고·글자"만 약속하고, 흠집·얼룩은 원본 사진으로 확인하라고 알린다.

### 11-6. 남은 일 (§10-2 에 더해, 최종 목록은 §15)

- [x] Langfuse `analyze` 재시딩 → §12-4
- [ ] 책·음반 사진을 더 모아 text_is_product 과민·과소 보기 (포토카드, 앨범 구성품, 보드게임 박스)
- [ ] 표지 펴기: 누운 책 돌려세우기, 펼친 책 감지, 배경 순백 여부
- [ ] md-lint 훅이 `.markdownlintignore` 를 안 읽음 (`backend/app/prompts/` 가 무시 목록에 있는데 경고)

---

## 12. 사진 종류 셋으로 시작 · 글자 가드 경로 제거 · 반영 안 된 곳 정리

### 12-1. photo_type — 첫 갈림길을 사진 종류 셋으로

사용자: "품질보증서 같은 글자 많은 애들도 책처럼. 종류가 셋이다 — 글자가 곧 물건, 차·노트북 까본 것처럼 내부, 상품 사진.
분류할 때 이 셋으로 나눠서 파이프라인을 시작하자."

§11 의 `text_is_product`(bool) 와 §9 의 `scene`(single_item / partial_view / multiple_items) 을 `photo_type` 하나로 합쳤다:

| photo_type | 예 | 경로 |
|---|---|---|
| document | 책·음반·카드·티켓·보증서·설명서·영수증 | 표지 펴기 + 배경 (하자 heavy 면 펴지 않고 일반 배경 교체) |
| inside_view | 엔진룸, 케이스를 뜯은 노트북 보드, 한 곳 클로즈업 | 원본 그대로 |
| product | 그 밖 전부 (글자 많은 상품 박스·화장품 포함) | 기존: dense·heavy 면 배경 교체, 아니면 생성 |

- 모르는 값·없는 필드 → `product` (+ 로그). `multiple_items` 는 기록만 하고 쓰지 않던 값이라 없앴다
- `phone_img/` 8장 analyze: 책 2권 document, 굴삭기 엔진룸 inside_view, 나머지 5장 product — 전부 맞음

reviewer:

| 지적 | 조치 |
|---|---|
| 하자 heavy 문서(찢김·접힘)를 네 모서리로 펴면 하자가 잘리거나 펴진다 | heavy 면 compose_flat 대신 compose |
| "opened laptop" 은 화면을 연 평범한 노트북 사진으로 읽힐 수 있다 | "케이스를 뜯어 보드가 보이는" 으로, 화면 연 노트북·프레임에 조금 잘린 상품은 product 로 명시 |
| 보드게임 박스·화장품 포장 경계 | 다른 상품의 박스는 product 로 명시 |
| 배지 "표지" 는 영수증·보증서에 안 맞음, 원본 그대로 배지가 이유를 모르면 "일부·내부" 문구로 떨어짐 | "인쇄된 글자·그림이 곧 상품이라", 이유별로 나눔 |
| Langfuse 에 옛 `analyze` 가 있으면 photo_type 이 없어 전부 product | §12-4 에서 시딩 |

### 12-2. 파이프라인 다시 훑기 — 과한 것 두 개를 지움 (사용자 결정)

1. **OCR 글자 가드와 그것 때문에만 있던 경로**: `OCR_GUARD` 가 꺼진 기본값에선 hard 가드가 하나도 없어
   seed 재시도 · 가드 실패 → 배경 교체 · blocked(원본 반환)가 **절대 실행되지 않았다**. 설정 `ocr_guard`,
   `guards._ocr_guards`·`run_output_guards`·`decide`, pipeline 의 `_ocr_pair`·`_run_guards`, State 의
   `guard_seed`·`guard_retry`·`guard_failed`·`status`, API `status`, UI "⛔ 차단" 배지·dev "가드 차단" 필터를 지웠다.
   남은 가드는 전부 관측용: `dino_band_guard`(새 함수) · item_dino · item_patch · ocr_local(eval 용, 기본 끔)
2. **배경 교체본의 verify**: 물건 픽셀이 원본이라 확인할 게 없고 결과도 바꾸지 않는다. VLM 1회 절약, 대신 합성본 말풍선이 없어짐

남겨 둔 것: read_text 의 12줄 안전망(document 가 빠져 거의 안 걸림 — inspect 를 보고 뺄지), 물건 위 워터마크가
배경 교체로 가면 남는다는 안내 없음, 여러 장 겹친 문서.

### 12-3. 반영 안 된 곳 정리 (사용자: "랭체인·프론트·DB 처럼 반영 안 된 애들 싹 다")

| 어디 | 전 | 후 |
|---|---|---|
| DB `results` | 경로 정보 없음 (inspect JSON 에만) | `mode`·`composite_reason`·`photo_type`·`wear_level` 컬럼 + 기존 DB 는 ALTER 마이그레이션 (실제 DB 사본으로 확인) |
| dev 화면 | 사진 종류·하자·워터마크·이유 안 보임, "배경 교체 모드만" 필터가 원본 그대로도 포함 | 칩 추가, 필터 "생성 안 한 것"·document·inside_view |
| Langfuse 트레이스 | photo_type 없음 | output 에 photo_type·wear_level |
| eval-report 스킬 | ocr_match 점수 조회 | item_patch 로 (ocr_match 는 옛 트레이스에만) |
| README | 흐름도가 덧댄 모양 | mermaid 를 지금 그래프대로 다시 (사진 종류 셋 → 생성 경로 subgraph → 배경 교체 분기) |

### 12-4. Langfuse 시딩

`python scripts/seed_langfuse_prompts.py` (없는 이름만 올리는 기본 모드) → `analyze` v1, `verify_v2` v1 을 production 으로.
그동안은 호출마다 404 뒤 코드 fallback 으로 돌고 있었다. 조회해 보니 코드 템플릿과 같은 내용이 돌아온다.
운영 중인 옛 서버는 두 이름을 쓰지 않아서 먼저 올려도 안전하다.
앞으로 analyze 프롬프트를 고치면 `seed_langfuse_prompts.py analyze` 로 덮어써야 한다 (기본 모드는 있는 이름을 건너뜀).

### 12-5. 두 번째 reviewer · tester

| 누가 | 지적 | 조치 |
|---|---|---|
| reviewer · tester | 합성본 verify 생략이 `verify_failed: False` 를 돌려줘, "verify 호출 실패 → 배경 교체" 의 기록을 덮음 (배지가 "결과 검사 못 함" 대신 "🛡️ 지키려고") | verify_failed 를 건드리지 않음. 떨어진 생성본 checks 는 `gate_checks` 로 inspect 에 남김 |
| reviewer | results·feedbacks ALTER 가 워커 동시 기동에서 "duplicate column" 으로 죽을 수 있음 | `db._add_column` 이 duplicate 만 무시 |
| reviewer | mermaid 에 composite → read_text 엣지 없음, 분석 실패 시 1회차에도 composite, "finalize 에서 DB 기록"은 틀림 | mermaid 다시 (§12-3 의 것을 한 번 더 고침) |
| reviewer | 지운 가드를 가리키는 주석·docstring 여러 곳, 로드맵의 `ocr_match` 완료 항목 | 고침 · 취소선 |
| reviewer | mock 경로는 DB 에 경로를 안 남김, dev 요약 칩과 필터 기준이 다름 | 안 함 — mock 은 개발용 통과 모드, 칩은 이름대로 composite 만 센다 |
| tester | 테스트 갱신 · 추가 (photo_type, 가드 제거, DB 경로 컬럼 32개) | 단위 1662 passed (수정 전) |

---

## 13. 이미지 생성을 실제로 쓴 곳들의 경험 (데이터셋 만들기 전에)

사용자: "단순 배경 교체 말고 이미지 생성을 쓰는 곳의 경험을 보고 싶다." 데이터셋·평가 설계에 쓸 것 위주로 읽었다.

### 13-1. 곳별 요약

**Pinterest Canvas** — 상품 사진 배경 생성(하루 약 7,500만 노출)·세로 비율 확장
([arXiv 2603.06453](https://arxiv.org/html/2603.06453v2),
[엔지니어링 블로그](https://medium.com/pinterest-engineering/building-pinterest-canvas-a-text-to-image-foundation-model-aa34965e84d9))

- **생성이 끝나면 원본 상품 누끼를 다시 덮어 붙인다.** 경계 색은 마스크를 받는 VAE 디코더를 따로 학습해 맞춘다
- **생성 전 대상 걸러내기**: 사람이 나온 사진, 무늬·글자가 주인 사진 등은 아예 제외
- **평가**: 상품 996개, 사람 평가자 2명이 정해진 양식(색 변화·상품 늘어남·변형 / 배경 문제)으로 채점
  - 상품 보존율 Canvas 84.0% · Nano Banana 74.6% · **FLUX.1 Kontext 53.4%**, 결함 없음 전체 47.2%
- 후보 2장 생성 + 보상 모델로 고르기 → 쓸 수 있는 결과 약 +7% (실행 시간 약 +20%)
- "비용의 대부분은 연산이 아니라 **사람 검수**"
- 온라인 A/B: 배경 생성 CTR +18%

**Amazon Ads** — 상품을 생활 장면에 넣는 생성, 상품별 LoRA 파인튜닝 + 합성 학습 데이터
([arXiv 2503.08729](https://arxiv.org/html/2503.08729))

- **평가**: 가구 100개, 평가자 3명 다수결, 8개 항목 4점 척도
  - 이미지 단위 통과율 17.4%(기준선 10%) / 상품 단위 45.5% — 같은 상품도 생성할 때마다 크게 흔들린다
- **자동 지표(CLIP·DINO)와 사람 점수의 상관이 0.4** — 자동 지표만으로는 판정 못 한다
- 어려운 것: 잔무늬·반사·가림

**Instacart PIXEL** — 사내 이미지 생성 플랫폼 (식품 이미지)
([글](https://company.instacart.com/how-its-made/introducing-pixel-instacarts-unified-image-generation-platform))

- **VLM 판정**: 프로젝트마다 예/아니오 질문 목록("배경이 따뜻한 중간 톤인가", "식품 아닌 것이 있나")
  → 떨어지면 LLM 이 프롬프트를 다시 쓰고 재생성
- 사람 승인율 **20% → 85%**. 최적 모델은 프로젝트마다 달라서 샘플 셋으로 먼저 비교

**eBay** — 판매자 사진 배경 교체 (중고 포함, 도메인이 제일 가깝다)
([글](https://innovation.ebayinc.com/stories/background-swap-tool-turns-any-photo-into-a-studio-quality-product-image/))

- 배경 제거 → **원본 물건을 SD 인페인팅으로 만든 배경 위에** 놓는다 (물건은 다시 그리지 않음)
- Responsible AI 팀과 함께: 판매자에게 "정확한지 확인하고 AI 사용을 밝히라"는 안내. 수치 공개 없음

그 밖에: ML6(SAM + SD 인페인팅 + ControlNet Canny 로 윤곽 보존,
[글](https://www.ml6.eu/en/blog/developing-an-ai-solution-for-product-photography-what-we-learned)),
Zalando(생성 이미지가 고객에게 가기 전 브랜드 오류·아티팩트 자동 검출기 — 2차 기사만 확인,
[기사](https://aieranews.com/can-zalandos-generative-ai-reinvent-fashion-shopping/))

### 13-2. Carret 에 주는 것

1. **"물건은 다시 그리지 않는다"가 업계 기본값이다.** Pinterest·eBay 모두 배경만 생성하고 원본 누끼를 덮어 붙인다.
   Carret 의 generate(FLUX.2 edit)는 물건까지 다시 그리는데, Pinterest 표에서 FLUX.1 Kontext 상품 보존이 53.4% 였다.
   → **"배경만 생성 + 원본 누끼 덮기"를 생성 경로의 기본 후보로** 실험할 만하다 (지금 composite 는 단색 배경뿐)
2. **생성 전 걸러내기는 맞는 방향이다.** Pinterest 도 사람·글자가 주인 사진을 뺀다 — §12 의 photo_type 과 같은 생각
3. **데이터셋·평가 설계**
   - 평가자 2~3명, 정해진 결함 양식(물건 변화 / 배경 문제), **이미지 단위와 물건 단위 통과율을 둘 다**
   - 자동 지표(DINO·judge)는 사람 라벨과의 상관부터 잰다 — Amazon 은 0.4 였다
   - 규모 감: Amazon 100개, Pinterest 996개. 포트폴리오면 30~50장으로 시작해 종류별로 나눠 보고
4. **VLM 예/아니오 체크리스트 판정은 효과가 검증된 방식이다** (Instacart 20% → 85%) — verify 의 설계와 같다
5. **후보 여러 장 + 고르기**는 비용이 싸고 효과가 있다 (+7%) — 나중 후보

### 13-3. 남은 일에 더할 것 (최종 목록은 §15)

- [ ] "배경만 생성 + 원본 누끼 덮기" 경로 실험 (경계 조화가 관건 — Pinterest 는 전용 디코더)
- [x] 데이터셋 라벨 양식: 물건 변화(색·형태·글자·하자) / 배경 문제 / 통과, 평가자 2명 이상 → §14
- [ ] judge·DINO 와 사람 라벨의 상관 재기 (도구는 §14, 데이터가 남음)

---

## 14. 평가 도구 — 라벨 양식 · 실행 · 채점 · 집계 (`backend/eval/`)

"포트폴리오로 몇 % 됐나" 에 대한 답이 "만드는 건 거의 끝, **잘 된다는 숫자가 없다**" 였다.
사진은 사용자가 중고나라에서 모은다 (직접 찍지 않음) — 게시 사진은 약 750px 로 줄어 있어
"폰 원본 3000px+" 목표는 못 채운다. 대신 사람들이 실제로 올리는 화질이라 결과에 그렇게 밝히기로.

옛 `test/eval/`(classify·detect_defects 기준 하자 검출)은 지금 파이프라인과 안 맞아 새로 만들었다.
"URL 목록 → 내려받기" 방식만 이어받음.

| 파일 | 하는 일 |
|---|---|
| `dataset.json` | 정답: photo_type · wear_level · text_level · key_texts(결과에서 그대로여야 할 글자) · url · labeled_by |
| `fetch.py` | url → `eval/images/` |
| `run.py` | `--analyze-only`(사진당 약 $0.005) / 전체(약 $0.04), 비용 확인 후 실행. 앱 storage·DB 대신 실행 폴더를 쓴다 |
| 채점 파일 | `runs/<id>/review.html`(원본 \| 결과) + `reviews/<id>/TEMPLATE.csv` → 평가자마다 `<이름>.csv` |
| `report.py` | 분석 정확도·혼동표 / 경로 분포 / 보존 통과율(경로별·종류별·물건 기준) / Cohen's kappa / 자동 지표 AUC·상관 |

판정 규칙 (§13 Pinterest·Amazon 방식): 물건 쪽 표시(형태·색 / 글자 / 하자)가 없으면 **보존 통과**,
표시가 하나도 없으면 **결함 없음**. 평가자 여럿이면 다수결, 동점은 실패. 사진·실행 결과는 git 제외, 정답·채점 CSV 만 올린다.

시험 실행 (`phone_img/` 8장, 정답은 초안 `claude-draft`):

- analyze 만: photo_type 8/8 · wear_level 7/8 · text_level 7/8. 책은 이번엔 하자 light — 앞선 실행(§12-1)에선 none.
  같은 사진도 판정이 흔들린다는 게 표에 드러난다
- 전체(책·엔진룸만 — 생성 호출 없음): 책 → 표지 펴기, 엔진룸 → 원본 그대로. 표지 펴기 결과에 judge 3/3/3 —
  사람 판정과 대 볼 값

tester 가 eval 도구 테스트 77개를 쓰던 중 사용자가 멈춤 (오래 걸려서) — 써 둔 것은 통과해서 같이 커밋. 단위 1757 passed.

---

## 15. 하루 마무리 (밤)

§10 이후 이어서 한 일(§11~14)까지 합친 최종본.

### 15-1. PR 상태

| PR | 내용 | 상태 |
|---|---|---|
| #23 | 하네스(훅·스킬) + 블로그 초안 + 노트 §1~8 | 머지 |
| #25 | analyze 단계 · photo_type 셋 · 책 표지 펴기 · 글자 가드 경로 제거 · DB 경로 컬럼 · eval 도구 · 노트 §9~14 | 머지 (base 를 main 으로 바꾼 뒤) |
| #24 | 키 설정 SecretStr | 머지 — main 과 `seed_langfuse_prompts.py`·`test_config.py` 가 충돌해 둘 다 살려 합침 |

CI 는 base 변경(`edited`)에 안 돈다 — #25 는 닫았다 다시 열어서 돌렸다. 로컬 단위 1760 passed (#24 합친 뒤).

### 15-2. 남은 일 (한 목록)

측정 — 데이터셋 (§14 도구로):

- [ ] 중고나라 사진 30~50장 + 정답 라벨 (종류별 목표는 `backend/eval/README.md`). 8장 초안(`claude-draft`) 확인
- [ ] 전체 실행 + 평가자 2명 채점 → 보존 통과율 · kappa · judge·DINO 의 AUC (judge 를 안전망으로 쓸지 여기서 정한다)
- [ ] document 과민·과소 (포토카드 · 앨범 구성품 · 보드게임 박스), read_text 12줄 안전망을 뺄지
- [ ] 정렬 신호(누끼 SIFT)를 soft 가드로 — 고해상도 사진이 있을 때

생성 방식:

- [ ] "배경만 생성 + 원본 누끼 덮기" 실험 (Pinterest·eBay 방식, §13) — 데이터셋으로 지금 방식과 비교
- [ ] 결과에 없던 게 생김(의자 다리, 은색 띠) 검사

정직성:

- [ ] 하자 light 생성본에서 하자가 정돈됨 (승용차) — 위 실험으로 풀릴 가능성
- [ ] 번호판 가리기
- [ ] 물건 위 워터마크가 배경 교체로 가면 남는다는 안내
- [ ] 원본 사진 함께 보여주기

배경 교체 · 표지 펴기:

- [ ] 누운 책 돌려세우기 · 펼친 책 감지 · 배경 순백 여부
- [ ] 구도 다시 잡기 · 비생성형 업스케일

구조 · 정리:

- [x] pre-commit 훅이 커밋 아닌 명령(heredoc·`{}`·`$변수`)에도 전체 테스트를 돌림 · 위키 커밋에도 돎 · md-lint 훅이 `.markdownlintignore` 를 안 읽음 → 스크립트가 직접 판단 (`is_repo_commit.py`, 커밋 아닌 명령 30초 → 0.04초)
- [x] 테스트 정리 — 1760개 중 859개가 파라미터 케이스, "지운 함수가 없나"·"옛 이름 무시" 같은 이력 확인용 정리 → 2026-09-29 §1 (이력 확인분, 파라미터 중복은 남음)
- [ ] tester 쓰는 법: 작업 중엔 해당 파일만 돌리고 전체는 끝에 한 번, 코드가 정해진 뒤에 부른다 (오늘 한 번에 약 25분)
- [ ] 위키 저장소 GitHub 원격 연결

### 15-3. 교훈 세 줄

1. **기준을 "글자가 많냐"에서 "틀리면 거래가 달라지냐"로** — 스펙·상태 글자는 입력칸으로 받고, 사진에서 지킬 건
   글자가 곧 물건인 document 뿐이었다. 사진 종류 셋으로 나누니 흩어진 플래그 셋이 하나로 정리됐다.
2. **기본값에서 실행되지 않는 경로는 비용만 남는다** — 기능을 끌 때는 그것 때문에만 있던 재시도·차단 경로와 API 필드까지 같이 지운다.
3. **업계는 물건을 다시 그리지 않는다** — 우리가 겪은 보존 문제의 답(원본 누끼를 덮는다)은 이미 표준이었다.
   다음은 주장이 아니라 숫자다 → 데이터셋.
