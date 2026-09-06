## 왜 (문제, 숫자로)
- 현재: recall 0.67 / precision 0.67 → 목표 1.0 / 1.0
- fn(miss)  : printed와 surface_damage를 구분하지 못함
- fp(extra) : 같은이유

## 무엇 (완료 정의)
- [ ] GT 감사: 기존 GT 5개의 데이터 라벨링을 잘 했나 체크
- [ ] 프롬프트를 변환함으로써 성능이 개선될수 있는지 체크
- [ ] detect_prompt: physical damage 우선 + NEW 테스트


## GT 감사


## 프롬프트 변환


## 측정
- before: R 0.67 / P 0.67
- after : R 1.0 / P 1.0,

## 순서
1. GT 감사 (자 먼저)
2. sanitize (어휘)
3. 프롬프트 (모델)

## ⚠️ 과적합 방어
- 5장 암기 금지 → 이미지 추가 또는 2장 홀드아웃