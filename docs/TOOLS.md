# Tool catalog

Every tool the SlideMakr agents can call. **How to use them to build slides** is in
[app/skills/google_slides.md](../app/skills/google_slides.md); this file is the
reference index.

**Agents:** `text_agent` (text creation) + `agent` (voice creation) share the same
40 tools; `edit_agent` has 43 (adds review + Drive tools, minus `create_new_presentation`).
Column: **C** = on the creation agents, **E** = on the edit agent.

## Lifecycle & state

| Tool | C | E | Purpose |
|------|---|---|---------|
| `create_new_presentation(title, use_template)` | ✓ | | Create a blank or template-based deck |
| `get_presentation_state(presentation_id)` | ✓ | ✓ | Read all slides/elements/text/colors/positions (read before editing) |
| `get_template_layouts(...)` | ✓ | ✓ | List the template's available slide layouts |
| `open_presentation(presentation_id)` | | ✓ | Load a Drive deck for editing |
| `search_drive_presentations(query)` | | ✓ | Find the user's decks in Drive |
| `duplicate_presentation(presentation_id, new_title)` | | ✓ | Copy a deck |
| `share_presentation_with_user(presentation_id, email)` | ✓ | ✓ | Share via Drive API |

## Content & assets

| Tool | C | E | Purpose |
|------|---|---|---------|
| `search_web(query)` | ✓ | ✓ | Look up real data / facts (and API request shapes) |
| `search_web_image(query)` | ✓ | ✓ | Find a photo (Unsplash) → use URL with `add_image` |
| `create_chart(type, labels_json, datasets_json, title)` | ✓ | ✓ | Build a chart image (QuickChart) → use URL with `add_image` |
| `create_flowchart(slide_id, nodes_json, edges_json, layout)` | ✓ | ✓ | Draw a full flowchart (nodes, edges, auto-layout) |
| `search_company_branding(company)` | ✓ | ✓ | Web-search a brand's colors/fonts/logo |
| `apply_brand_theme(...)` | ✓ | ✓ | Apply colors/fonts/logo across the deck in one shot |
| `review_slide_layout(presentation_id, slide_id)` | | ✓ | Vision review of a rendered slide (contrast/balance/overlap) — editing/eval only |

## Narrow editing tools (build validated Slides API requests, buffered → `commit_edits`)

All on **both** creation and edit agents.

**Slide-level:** `add_slide` · `reorder_slides` · `update_slide_flags` · `set_slide_background`

**Elements:** `add_shape` · `add_text_box` · `add_image` · `add_table` · `add_line` ·
`move_element` · `resize_element` · `delete_element` · `duplicate_element`

**Text:** `insert_text` · `delete_text` · `update_text` · `replace_all_text` ·
`update_text_style` · `set_paragraph_style` · `add_bullets`

**Styling:** `set_element_color` · `set_line_style`

**Tables:** `insert_table_row` · `insert_table_column` · `delete_table_row` ·
`delete_table_column` · `set_cell_background` · `merge_cells` · `unmerge_cells`

**Flush:** `commit_edits(presentation_id)` — END every editing turn with this; flushes
all buffered edits in one `batchUpdate`.

> Object-ID safety: targeted tools validate the `object_id` against the live deck and
> reject invented/stale IDs (returning the valid ids). Never guess IDs — read state first.
