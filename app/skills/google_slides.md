---
name: google-slides
description: How SlideMakr builds and edits Google Slides via the narrow typed tools — the single source of slide-making know-how shared by the creation and edit agents. Covers the read-before-edit loop, EMU coordinates, the tool catalog, positioning recipes, branding, and quality rules.
---

# Making slides with the Google Slides API

Every slide action is a Google Slides API request. **We deliberately do NOT store
the API request catalog here** — it drifts and bloats. The official docs are the
authoritative, always-current reference; look them up at runtime rather than
guessing or memorizing shapes:
- REST reference: https://developers.google.com/slides/api/reference/rest
- batchUpdate request types: https://developers.google.com/slides/api/reference/rest/v1/presentations/request

For anything a narrow tool below doesn't cover, use `search_web` to find the exact
request shape from those docs, then build it — don't invent it.

You never call the API directly — you call the **narrow tools** below, which build
validated requests, buffer them, and flush them in ONE `batchUpdate` via
`commit_edits`. Never invent request types or tool names; only the registered
tools exist.

## Read before you edit

1. Call `get_presentation_state(presentation_id)` to see every slide + element,
   its objectId, text, font, colors, and position. **Use the ACTUAL objectIds you
   find — never guess or reuse remembered IDs.** State changes after each commit.
2. If a targeted tool returns `status: "error"` with `valid_object_ids`, you used
   an id that isn't in the deck — retry with one of the valid ids it lists.
3. Note positions (`translateX/Y` + `width/height`) so new elements don't overlap.

## EMU coordinate system

Google Slides positions everything in EMU (English Metric Units):
**1 inch = 914,400 EMU. Slide = 9,144,000 × 5,143,500 EMU (10" × 5.625").**
Title area is roughly the top ~900,000 EMU.

## Narrow tools (use these and ONLY these)

**Slide-level**
- `add_slide(insertion_index, layout, title_id, body_id)` — new slide; pass
  `title_id`/`body_id` to pre-name the layout placeholders so you can `insert_text`
  into them the same turn (no second `get_presentation_state`).
- `reorder_slides(slide_ids, insertion_index)` — reorder
- `set_slide_background(slide_id, color_hex)` — solid background color
- `update_slide_flags(slide_id, is_skipped)` — presentation flags

**Elements** (position in EMU)
- `add_text_box(slide_id, text, x, y, w, h)` — box + text in one call
- `add_shape(slide_id, shape_type, x, y, w, h)` — RECTANGLE / ELLIPSE / DIAMOND / …
- `add_image(slide_id, url, x, y, w, h)` — image from a URL
- `add_table(slide_id, rows, cols, x, y, w, h)` — table
- `add_line(slide_id, x, y, w, h)` — line
- `move_element(object_id, x, y)` — move to absolute position
- `resize_element(object_id, scale_x, scale_y, x, y)` — scale + preserve x,y
- `duplicate_element(object_id)` / `delete_element(object_id)` — clone / remove

**Text**
- `insert_text(object_id, text, insertion_index, cell_row, cell_col)`
- `update_text(object_id, new_text)` — full replace
- `delete_text(object_id, range_type, start, end)`
- `replace_all_text(find, replace, match_case, slide_ids)`
- `update_text_style(object_id, bold, italic, color_hex, size_pt, font, ...)`
- `set_paragraph_style(object_id, alignment, line_spacing, ...)`
- `add_bullets(object_id, preset)`

**Shape / line styling**
- `set_element_color(object_id, fill_color_hex, outline_color_hex, outline_weight_pt)`
- `set_line_style(object_id, weight_pt, dash_style, color_hex)`

**Tables**
- `insert_table_row(table_id, row, column, below, count)`
- `insert_table_column(table_id, row, column, right, count)`
- `delete_table_row(table_id, row, column)` / `delete_table_column(table_id, row, column)`
- `set_cell_background(table_id, row_start, col_start, row_span, col_span, color_hex)`
- `merge_cells(...)` / `unmerge_cells(...)`

**Flush** — `commit_edits(presentation_id)`: END every editing turn with this. It
flushes all buffered edits in one `batchUpdate` and returns a verification. If you
forget it, nothing ships to Google. (status=`noop` just means immediate mode.)

**Enums**
- Shape types: TEXT_BOX, RECTANGLE, ROUND_RECTANGLE, ELLIPSE, DIAMOND, TRIANGLE, STAR_5, HEXAGON.
- Bullet presets: BULLET_DISC_CIRCLE_SQUARE, BULLET_ARROW_DIAMOND_DISC, BULLET_STAR_CIRCLE_SQUARE, NUMBERED_DIGIT_ALPHA_ROMAN.
- Colors: all tools take `color_hex="#RRGGBB"` strings — never RGB floats.

## Images, charts, flowcharts (fetch first, then place)

- Photos: `search_web_image(query)` → then `add_image(slide_id, url, ...)`.
- Data charts: `create_chart(type, labels_json, datasets_json, title)` → then
  `add_image(slide_id, chart_url, ...)`.
- Flowcharts/diagrams: `create_flowchart(slide_id, nodes_json, edges_json, layout, title)`
  draws the whole diagram itself (nodes, edges, layout) — no narrow tools needed.
  **Always pass a short, descriptive `title`** — a titled, centered flowchart scores
  far better than an untitled one.

## Templates behave differently

With `use_template=True`, the first slide already exists — use its placeholders,
don't create a "slide 0". **Trust the template: don't reposition or resize its
placeholders.** Don't call `review_slide_layout` during creation — the template
handles layout.

## Positioning recipes (EMU)

- **Full-width content** (text/table under a title):
  x=457200, y=1000000, w=8229600, h=3800000
- **Visual LEFT + text RIGHT** (chart/image + bullets — preferred for mixed content):
  visual x=300000, y=1000000, w=5000000, h=3500000 · text x=5600000, y=1000000, w=3200000, h=3500000
- **Text LEFT + visual RIGHT**:
  text x=300000, y=1000000, w=3200000, h=3500000 · visual x=3800000, y=1000000, w=5000000, h=3500000
- Adding content where a BODY placeholder already has text → INSERT into it (use its
  objectId), don't float a new text box.
- Adding bullets next to a flowchart/diagram → place a TEXT_BOX beside it (resize the
  diagram to the left half, bullets on the right), never below it.

## Branding

If the user names a company:
1. `search_company_branding(company)` → brand colors, fonts, logo URL.
2. Create the content (slides first).
3. `apply_brand_theme(...)` with the extracted `primary_color_hex`,
   `secondary_color_hex`, `heading_font`/`body_font`, `logo_url`, `dark_background`
   — applies backgrounds, text colors, fonts, and logo in one shot.

## Anti-patterns (never do these)

- Never leave new elements at default (0,0) — always specify coordinates.
- Never create tiny text boxes (< 2,000,000 EMU wide) — text gets cramped.
- Never overlap elements — check positions from `get_presentation_state` first.
- Never place content beyond slide bounds (x > 9,144,000 or y > 5,143,500).
- Never float a text box below a shape/chart with no visual connection.

## Quality bar (fast AND accurate)

After complex edits (2+ elements), `get_presentation_state` and verify:
1. No elements overlap; all content within bounds.
2. Text and visuals arranged side-by-side, not stacked awkwardly; content is
   centered/balanced, not bunched on one side with dead space.
3. Fonts match the deck; titles large (28–36pt), body readable (16–18pt).
4. Key metrics bold and/or colored; colors are on-brand.
5. Every added component (flowchart/chart) has a title/label.

## Never lie about results

After `commit_edits`, READ the response:
- `error_count > 0` → tell the user what failed, fix it, retry.
- `success` with a sane `verification` → then confirm the edit.
- Haven't committed yet → nothing shipped; your tool calls are only queued.
The user can SEE the deck — if you say "done" but nothing changed, you lose trust.
