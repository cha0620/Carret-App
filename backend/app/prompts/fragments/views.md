You see {{n}} photos of ONE second-hand item that a seller is about to list for sale,
numbered 0 to {{n_last}} in the order given.

1. Decide what kind of item it is: "shoes" | "clothing" | "bag" | "electronics" | "vehicle" | "other".
2. For EACH photo, say from which side the camera sees the item:
   "front" (straight on the main face) | "front_34" (front, turned at an angle) | "side" |
   "back" | "rear_34" (back, turned at an angle) | "top" (looking down from above) |
   "bottom" (underside, e.g. a shoe sole) | "inside" (interior, e.g. inside a bag or a car) |
   "label" (a close-up of a tag, size label or serial plate) | "detail" (any other close-up).
   For a pair of shoes, judge by the shoe that shows the most.
3. For EACH photo, also say:
   - occluded: true only if a hand, a person or another object hides part of the item's surface,
     print or a defect that a buyer would need to see. A hand that only holds an edge or the sole
     without hiding anything important is NOT occluded.
   - blurry: true if the item is too blurry to see its surface clearly
   - item_visible: false if the item is mostly out of frame, too small, or not in the photo.
     For "label" and "detail" close-ups, true as long as you can tell what is shown.

Output JSON only:
{"category": str,
 "item": str,
 "photos": [{"index": int, "view": str, "occluded": bool, "blurry": bool, "item_visible": bool}]}
("item": a short English noun for the item, e.g. "sneakers". One entry per photo, every index once.)
