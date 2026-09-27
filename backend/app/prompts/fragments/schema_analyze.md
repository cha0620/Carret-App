Output JSON only:
{"item": str,
 "considered": [str],
 "item_box_2d": [ymin, xmin, ymax, xmax],
 "photo_type": "document" | "inside_view" | "product",
 "wear_level": "none" | "light" | "heavy",
 "watermark": "none" | "background" | "on_item",
 "text_level": "none" | "simple" | "dense",
 "marks": [{"what": str, "where": str}]}
(item_box_2d: the whole item's bounding box in the photo, normalized 0-1000)

If the item has no identity marks, return "marks": [].
