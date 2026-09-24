You are reading the printed/embroidered/engraved text that is physically ON the product in this photo.
Product: {{item}}

Rules:
1. ONLY text that is part of the product itself (its surface, label, dial, cover, tag sewn on it).
   IGNORE text on the background, walls, tables, the person's clothing, sleeves or hands,
   other objects, price stickers, watermarks and UI overlays.
2. Transcribe EXACTLY what is rendered, glyph by glyph. Do NOT fix spelling, do NOT complete
   words from memory or brand knowledge (e.g. do not "correct" a garbled logo into the real brand name).
   Write an unreadable character as "?".
3. One entry per visual line of text. Skip text too small to read at all.

Output JSON only:
{"item_box_2d": [ymin, xmin, ymax, xmax],
 "texts": [{"text": str, "box_2d": [ymin, xmin, ymax, xmax]}]}
(all coordinates normalized 0-1000)
