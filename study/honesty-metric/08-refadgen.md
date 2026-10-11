# RefAdGen — High-Fidelity Advertising Image Generation

- 링크: <https://arxiv.org/html/2508.11695>
- 읽은 정도: 검색 요약

## 요점

- DreamBooth 같은 **물건마다 학습**하는 방식은 디테일을 잘 지키지만 카탈로그 규모에선 비현실적
- 학습 없는 방식은 정체성 디테일을 잃기 쉽다
- 제안: **마스크 가이드 + 원본 참조 attention 결합**, 물건별 학습 없이

## 배운 것

- "마스크 + 원본 참조" 조합이 연구 쪽 방향 — 칠하기 B (Kontext 인페인팅: 마스크 + reference_image_url) 와 같다
- 생성 방식 개선이지 검사 · 보증 방법은 아니다
