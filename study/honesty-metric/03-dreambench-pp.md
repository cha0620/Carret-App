# DreamBench++ — A Human-Aligned Benchmark for Personalized Image Generation

- 링크: <https://arxiv.org/html/2406.16855v2>
- 읽은 정도: 검색 요약

## 요점

- 개인화 생성(내 물건 · 인물을 새 장면에) 벤치마크. 편집은 아님
- **DINO 는 전체 모양이 같으면 높은 점수** — 색 · 질감 · 얼굴 특징은 덜 본다
- 사람에 맞춘 심사(GPT 기반)가 DINO · CLIP 보다 사람과 더 맞는다

## 배운 것

- 우리 `item_dino` 가 "모양은 같은데 색 · 글자가 바뀐" 결과를 통과시킬 수 있는 이유
- 색은 따로 재야 한다 (물건 영역 평균 색 차이 ΔE 같은 것)
