Rule 1: Background may change, but every listed item must remain visible.
Rule 2: Items that are logos, brand marks, or printed text must remain
        EXACTLY identical (shape, wording, layout);
        any distortion or garbling = preserved:false.

For EACH entry decide:
- preserved: true only if still visible under the rules
- box_2d: bounding box of the item in THIS photo as [ymin, xmin, ymax, xmax],
  each normalized to 0-1000 (y = vertical from top, x = horizontal from left),
  required when preserved
