"""VLM 저지 채점 기준표. 수정 = 이 dict만 만지기."""

RUBRIC = {
    "fidelity": {
        "question": "Is the product itself unchanged?",
        "anchors": {
            5: "identical including wear, texture, labels",
            4: "negligible edge-level difference",
            3: "same item but visible smoothing or color shift",
            2: "details lost (logo blurred, stain removed)",
            1: "effectively a different product",
        },
    },
    "realism": {
        "question": "Does it look like a real photograph?",
        "anchors": {
            5: "indistinguishable from a studio shot",
            4: "slight unease only on close inspection",
            3: "pasted look visible (shadow/light mismatch)",
            2: "obviously composited",
            1: "collage-level",
        },
    },
    "trust": {
        "question": "Honest as a secondhand listing?",
        "anchors": {
            5: "clean background, condition honestly visible",
            3: "slightly over-staged, buyer might doubt",
            1: "deceptive advertising look",
        },
    },
}

AXES = list(RUBRIC.keys())


def rubric_text() -> str:
    lines = []
    for axis, spec in RUBRIC.items():
        lines.append(f"[{axis}] {spec['question']}")
        for score in sorted(spec["anchors"], reverse=True):
            lines.append(f"  {score} = {spec['anchors'][score]}")
    return "\n".join(lines)