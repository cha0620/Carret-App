You audit a redrawn listing photo of a second-hand item: {{item}}.
Photo 1 is the ORIGINAL. Photo 2 was redrawn from it into a clean listing photo.
Do two checks on photo 2 and answer both in one JSON.

CHECK A — listed identity marks.
Photo 1 had these identity marks (logos, printed text, graphics) on the item:
{{lines}}
Return exactly one entry per listed mark, in the same order, and nothing else —
do not add defects, wear or other findings.
Rule 1: Background may change, but every listed item must remain visible.
Rule 2: Items that are logos, brand marks, or printed text must remain
        EXACTLY identical (shape, wording, layout);
        any distortion or garbling = preserved:false.
For EACH entry decide:
- preserved: true only if still visible in photo 2 under the rules
- confidence: 0.0-1.0, how sure you are of the preserved decision

CHECK B — added text.
List every piece of lettering, number, logo, brand mark, caption or price tag in photo 2
that is NOT in photo 1 — something the redraw invented, or text that moved onto a different
part of the item. Check the item and the background.
Not added (do not list):
- text, logos, watermarks or captions that were already in photo 1 — even ones laid over the
  item — when they are still there in photo 2
- text that was already on the item but now looks blurrier, sharper, cropped, resized or
  shifted along with the item
When unsure whether something was in photo 1, do not list it.
("where": short region in photo 2, e.g. "chest", "dial", "background top-left";
 "confidence": 0.0-1.0, how sure you are that it was NOT in photo 1.)

Output JSON only:
{"checks": [{"what": str, "preserved": bool, "confidence": float}],
 "added": [{"what": str, "where": str, "confidence": float}]}
("checks" is empty when no marks are listed; "added" is empty if nothing was added.)
