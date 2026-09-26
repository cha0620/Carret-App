Output JSON only:
{"text_level": "none" | "simple" | "dense",
 "item_box_2d": [ymin, xmin, ymax, xmax],
 "defects": [{"category": str, "what": str, "where": str}]}
(item_box_2d: the whole item's bounding box in the photo, normalized 0-1000)

If no defects are visible, return "defects": [].
