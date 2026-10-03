8. texts — read the text physically ON the item, so the studio can tell the generator what to keep
   (only when text_level is "simple"; for "none" or "dense" return "texts": []):
   - Only text that is part of the item itself (its surface, a label or tag sewn on it, a dial, a cover).
     Ignore text on the background, other objects, the person's clothing or hands, price stickers,
     watermarks and overlays.
   - Transcribe EXACTLY what is rendered, glyph by glyph. Do NOT fix spelling or complete words from
     brand knowledge (do not "correct" a garbled logo into the real brand name).
     Write an unreadable character as "?".
   - One entry per visual line of text, with its box. Skip text too small to read at all.
