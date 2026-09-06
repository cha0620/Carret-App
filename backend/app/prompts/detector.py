
"""검출 프롬프트 층 - 범용 검수의 말.

CLASSIFY_PROMPT : 물건 식별 + 루브릭(considered) 수립
detect_prompt() : 루브릭 기반으로 원본 검수
verify_prompt() : 결과에서 보존 감사
"""
# backend/app/prompts/ (기존 detect_prompt 교체)

CLASSIFY_PROMPT = """
You are a product analyst for a used-goods marketplace.

Look at the image and produce:

1. item
   - the product as a short common noun
   - e.g. "hoodie", "sneaker", "smartphone", "handbag", "desk lamp"

2. considered
   - defect types worth inspecting for THIS item
   - use specific terms, most likely first, 3~8 items
   - examples:
     clothing : stain, tear, fraying, pilling, discoloration
     shoes    : scuff, sole wear, creasing, dirt
     phone    : scratch, crack, display line, dead pixel
   - add "logo" if a brand mark is visible
   - add "printed text" if text is visible

Output JSON only:
{"item": str, "considered": [str]}
"""

CATEGORIES = {
    "surface_damage":   "stains, scratches, scuffs, tears, holes, dents, cracks, fraying, pilling",
    "functional_fault": "broken parts, display lines, dead pixels, not working",
    "printed":       "printed or logos, pictures, emblems, brand symbols ,embroidered text, labels",
    "other":            "anything else visible",
}

def detect_prompt(item: str, considered: list) -> str:
    cats = "\n".join(f"- {k}: {v}" for k, v in CATEGORIES.items())
    hints = ", ".join(considered) if considered else "any visible issue"
    return f"""You are a defect inspector for a used {item}.

Item-specific hints: {hints}

For EVERY finding choose exactly one category from:
{cats}

Output JSON only:
{{"defects": [{{"category": str, "what": str, "where": str}}]}}

Rules:
- category: exactly one of the 5 names above
- what: short natural description (e.g. "brown stain on front pocket")
- where: short region (e.g. "left sleeve")
- report physical issues first, marks second
- do not invent invisible damage

If nothing visible: {{"defects": []}}"""


def verify_prompt(anchors: list, item: str, considered: list) -> str:
    lines = "\n".join(f"- {a['what']} ({a['where']})" for a in anchors)
    checklist = ", ".join(considered) if considered else "(open-ended)"
    return f"""
You are a preservation auditor for a restored product photo.
Item: {item}
Inspection checklist used: {checklist}

The ORIGINAL photo contained these defects and marks:
{lines}

This photo is the RESTORED version.
Rule 1: Background may change, but every listed item must remain visible.
Rule 2: Items that are logos, brand marks, or printed text must remain
        EXACTLY identical (shape, wording, layout);
        any distortion or garbling = preserved:false.

For EACH entry decide:
- preserved: true only if still visible under the rules
- x1,y1,x2,y2: box in THIS photo (0-1000), required when preserved

Output JSON only:
{{"checks": [{{"what": str, "preserved": bool,
   "x1": int, "y1": int, "x2": int, "y2": int}}]}}
"""

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