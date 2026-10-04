You compare two photos of the SAME second-hand item.
Photo 1 is the ORIGINAL. Photo 2 was redrawn from it into a clean listing photo.

List every piece of lettering, number, logo, brand mark, caption or price tag in photo 2
that is NOT in photo 1 — something the redraw invented, or text that moved onto a different
part of the item. Check the item and the background.

Not added (do not list):
- text, logos, watermarks or captions that were already in photo 1 — even ones laid over the
  item — when they are still there in photo 2
- text that was already on the item but now looks blurrier, sharper, cropped, resized or
  shifted along with the item
When unsure whether something was in photo 1, do not list it.

Output JSON only:
{"added": [{"what": str, "where": str}]}
("where": short region in photo 2, e.g. "chest", "dial", "background top-left". Empty list if nothing was added.)
