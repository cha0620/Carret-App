# text_check — 생성 후 텍스트/로고 깨짐 확인용

1. `input/` 에 원본 사진을 넣는다 (jpg/png/webp, 글자·로고가 보이는 물건).
2. (선택) `labels.csv` 에 사진에 실제로 적힌 글자를 적는다 — 있으면 절대 정확도까지 잰다.
   ```
   filename,expected_text
   nike_shoe.jpg,NIKE AIR
   ramen_box.png,신라면 SHIN RAMYUN
   ```
   여러 줄 글자는 공백으로 이어서 한 칸에. 모르면 비워둬도 된다.
3. 실행 결과(생성 이미지, 원본/결과 텍스트 비교표)는 `output/` 에 쌓인다.
