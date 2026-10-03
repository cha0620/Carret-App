You compare two photos of the SAME second-hand item.
Photo 1 is the ORIGINAL. Photo 2 was redrawn from it into a clean listing photo.

List every piece of lettering, number, logo or brand mark that is ON THE ITEM in photo 2
but is NOT on the item in photo 1 — something the redraw invented or moved to a new place.
Text that was already on the item (even if slightly blurrier, cropped or resized) does not count.
Ignore the background, the shadow, and watermarks or captions laid over photo 1.
When unsure whether something was in photo 1, do not list it.

Output JSON only:
{"added": [{"what": str, "where": str}]}
("where": short region on the item in photo 2, e.g. "chest", "dial". Empty list if nothing was added.)
