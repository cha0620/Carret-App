Report:

1. item — the product as a short common noun (e.g. "hoodie", "motorcycle", "kettle")
   item_count — how many separate items for sale are in the photo: two CDs side by side = 2,
   a stack of 12 comics = 12, a pair of shoes = 1, one product with its box or accessories = 1.
   Things in the background that are not for sale do not count.
   objects — every separate physical object clearly visible in the photo that a seller could be
   selling, ONE entry per piece (two CDs = two entries, a pair of shoes = one entry, a product and
   its box = two entries), at most 12. For each: what (short noun), box_2d, and for_sale — true if it
   is part of what is being sold (the items you counted in item_count), false for things around it
   (a keyboard behind, a mug next to it). Do not list hands, people, furniture, the floor or walls.
   item_cut_off — true if part of an item for sale is outside the photo (cut by the frame edge) or
   hidden behind something, so its whole outline is not shown. A close-up of one spot is
   "inside_view", not cut off. false if every item for sale is fully in the photo.

2. considered — 3~8 inspection terms for THIS item, most likely first
   (e.g. clothing: stain, tear, pilling; phone: scratch, crack, display line).
   Add "logo" if a brand mark is visible, "printed text" if text is visible.

3. marks — the item's IDENTITY marks that must look exactly the same after redrawing:
   logos, brand symbols, emblems, printed or embroidered text, labels,
   decorative graphics. Only marks physically ON THE ITEM.
   Do NOT list damage, wear, stains or scratches here.
   Do NOT list watermarks, captions or UI overlays laid over the photo.
   - what: short description (e.g. "SUZUKI logo on side fairing")
   - where: short region (e.g. "left side panel")

4. photo_type — what kind of photo this is. Pick exactly one:
   - "document": an actual paper document whose written content IS the item —
     warranty cards, certificates, receipts, manuals, tickets, gift cards, letters.
     Books, magazines, comics, CDs, records, game or movie cases, trading cards,
     photo cards and posters are goods for sale, NOT documents — they are "product"
   - "inside_view": only part or the INSIDE of a larger object, with no clear
     outline of a whole item — an open engine bay, a laptop or PC with its case
     removed and the board visible, a close-up of one spot, the inside of a bag.
     A laptop with its lid open and screen showing is "product". If most of the
     item is visible (even cut off a little at the frame), it is "product"
   - "product": everything else — a whole product photo, even when the product
     carries a lot of text (electronics, clothing, cosmetics, product boxes,
     books, CDs, albums)
   When unsure, choose "product".

5. wear_level — visible wear or damage on the item:
   - "none": looks new or nearly new
   - "light": a few small marks (a stain, a scratch, some pilling)
   - "heavy": widespread wear — rust, peeling paint, many scratches or stains,
     cracks, a surface where the damage is a large part of what you see
   Judge only real damage, not intentional distressing or design.

6. watermark — text or logos laid OVER the photo (not printed on the item):
   - "none"
   - "background": only over the background
   - "on_item": crosses the item itself
