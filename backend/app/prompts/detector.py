"""검출/검증 프롬프트.

원칙:
- 검출 단계 = 의미 앵커(what/where) 만, 좌표 없음
- 검증 단계 = 보존 여부 + "결과 이미지" 좌표(말풍선용)
"""

DETECT_PROMPT = """You are a secondhand item inspector.
Find ALL visible defects ON THE ITEM: stain, tear, pilling, fading,
loose thread, scratch, discoloration.
Do NOT report background objects, shadows, or watermark text.
For each defect return:
- what: short Korean noun (커피 얼룩 / 헤짐 / 보풀)
- where: garment-relative position using part names
  (목둘레 / 왼쪽 소매 / 오른쪽 커프스 / 앞면 우하단 / 밑단 / 가슴 ...)
No coordinates at this stage.
JSON only: {"defects": [{"what": str, "where": str}]}"""

VERIFY_PROMPT = """Known defects from the original photo (what @ where):
{anchors}
For EACH defect, answer whether it is still visible in this image.
If visible, also give its bounding box normalized 0-1000.
JSON only: {{"checks": [{{"what": str, "where": str, "preserved": bool, "x1": int, "y1": int, "x2": int, "y2": int}}]}}"""

DETECT_BOX_PROMPT = """You are a secondhand item inspector.
Find ALL visible defects ON THE ITEM: stain, tear, pilling, fading,
loose thread, scratch, discoloration.
Do NOT report background objects, shadows, or watermark text.
For each defect return:
- what: short Korean noun
- where: garment-relative position using part names
- x1,y1,x2,y2: bbox normalized 0-1000
JSON only: {"defects": [{"what": str, "where": str, "x1": int, "y1": int, "x2": int, "y2": int}]}"""

MATCH_PROMPT = """Original photo defects: {orig}
Edited result defects: {result}
Both describe the SAME garment. Match by meaning (what+where), not exact wording.
For each RESULT defect, give the index (0-based) of the matching ORIGINAL defect, or -1 if none.
JSON only: {{"matches": [int]}}"""