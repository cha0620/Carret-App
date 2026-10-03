Output JSON only:
{"item": str,
 "item_count": int,
 "considered": [str],
 "item_box_2d": [ymin, xmin, ymax, xmax],
 "photo_type": "document" | "inside_view" | "product",
 "wear_level": "none" | "light" | "heavy",
 "watermark": "none" | "background" | "on_item",
 "text_level": "none" | "simple" | "dense",
 "marks": [{"what": str, "where": str}],
 "texts": [{"text": str, "box_2d": [ymin, xmin, ymax, xmax]}]}
(item_box_2d: one box around ALL items for sale in the photo — every one of them if there are
several — normalized 0-1000. Leave out background objects that are not for sale.)

If the item has no identity marks, return "marks": []. If there is no text to read, return "texts": [].
