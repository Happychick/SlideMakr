# SlideMakr Handoff — Step 15: Tool Decomposition for Voice Editing

**Date:** 2026-04-17
**Branch:** main
**Blocker status:** Voice editing is the gate to everything else on the roadmap (including Stripe). This handoff exists because voice editing works *until the agent tries to modify slides*, and then Gemini Live crashes. Tool decomposition is the fix.

---

## Your task

Build **tool decomposition** for SlideMakr's agents, replacing the single `execute_slide_requests(requests: List[Dict])` tool with a narrow, typed tool per Slides API operation. Keep batching (server-side, via parallel function calling), but move shape enforcement from a single blob to many small typed signatures.

This is **Step 15 in [PROJECT_PLAN.md](PROJECT_PLAN.md)** — the immediate pre-requisite for Step 11 (Stripe). Nothing in the Stripe/launch/GTM chain ships until this works.

---

## What happened in the previous session

1. Voice editing worked end-to-end through Drive picker → voice session open → agent talked back → user asked for a chart on slide 2.
2. `create_chart` tool ran successfully (see logs below).
3. Agent spent 6 reasoning events positioning/placing the chart…
4. …then Gemini Live crashed with `1011 Internal error occurred` **before** it could emit the `execute_slide_requests` call.

The session never got to execute a single slide edit. Voice editing is effectively broken at the moment of actually editing.

### The log evidence (read this literally, don't infer beyond it)

```
Event 159: create_chart: line with 6 labels, 1 datasets          ← tool succeeded
Events 160-165: agent reasoning
  "**Positioning the Chart** I've got the chart URL..."
  "**Refining the Chart Placement** I've refined..."
  "**Adjusting the Layout** I'm now focused on..."
  "**Creating a New Slide** I've decided to take a..."
  "**Finalizing the Placement** I've created the cha..."
  "**Positioning the Chart** I've got the chart URL..."
Event 166: APIError: 1011 None. Internal error occurred.
```

The agent never called `execute_slide_requests`. It crashed while planning it.

### Hypotheses (ordered by confidence, NONE proven)

1. **Tool declaration payload too large for native-audio Gemini Live** (edit_agent uses `gemini-2.5-flash-native-audio-latest`). During this session we briefly shipped a typed-wrapper schema for `execute_slide_requests` that serialised to **19 KB / 963 keys** with 26 `any_of` branches. After observing the 1011, we reverted to an untyped `List[Dict[str, Any]]` signature (schema dropped to 1.3 KB, 15×). The revert is committed but NOT yet tested end-to-end against the voice flow.
2. **Context accumulation** — 165 events of audio + reasoning may have blown native-audio's context window during chart positioning.
3. **Transient Gemini 1011** — `1011` is service-side "internal error" and is commonly transient. Cannot be ruled out without deterministic repro.

**None of the hypotheses are confirmed.** The reverted state (hypothesis 1 partially addressed) has not been retested with a voice edit + chart prompt. That's the *first* thing the new session should do — before writing any new code — to know whether the simple revert is enough or whether tool decomposition is genuinely needed.

---

## The design ask

Assume hypothesis 1 is at least a contributing cause and that the correct long-term architecture is narrow tools. **Replace `execute_slide_requests` with ~20 narrow typed tools**, plus server-side batching so a 5-op edit still hits Google as one `batchUpdate` HTTP call.

### Why this is the right design (not just a workaround)

- **Hallucination becomes structurally impossible.** The LLM can only call registered tools. `moveElement`, `resizeShape`, and the 7 other made-up types the agent invented in the drop-list simply cannot be emitted.
- **Gemini picks narrow tools faster and with fewer retries.** Less reasoning surface per tool = fewer mistakes. This is documented behaviour for both Gemini and Claude function-calling.
- **Latency does NOT increase.** Modern Gemini supports **parallel function calling** — the model emits multiple tool calls in one response turn. ADK collects them, your server batches into one `batchUpdate` HTTP call. Same round trips as today, better correctness.
- **Lower schema budget per call.** Instead of one 19 KB schema with 26 branches, each tool has a small (100-500 byte) focused schema. Native-audio will be happy.

### Architecture

```
LLM turn: emits N parallel tool calls
  ├─ add_text_box(slide_id, text, x, y, w, h, style_args...)
  ├─ insert_text(object_id, text)
  ├─ update_text_style(object_id, color, bold, ...)
  ├─ set_page_background(slide_id, color)
  └─ ...

Server:
  1. Each narrow tool APPENDS its pre-built Slides API request to a session-scoped buffer
     (e.g. a list stored on the ADK Session or a contextvar keyed by ws session_id)
  2. A FINAL `commit_edits()` tool flushes the buffer → ONE batchUpdate → returns verification
  3. LLM is instructed to always end an edit turn with commit_edits()

OR (simpler alternative — decide which):
  1. Each narrow tool is synchronous and hits Google immediately
     (loses batching but keeps code simple; each call is ~200-400ms against Slides API)
  2. Measure — if 5 calls takes <2s total, this may be fine.
```

The commit-buffer design preserves batching. The immediate-execute design is simpler. Benchmark both before committing to one.

### Narrow tool inventory (use this as the target set)

Derived from [app/slides_schema.py:847-875](app/slides_schema.py) — we already built all 26 Pydantic wrappers, they're just unused at the tool layer. Each of these becomes one narrow tool:

| Tool name | Wraps Slides API request | Common enough to prioritize? |
|-----------|--------------------------|-----------------------------|
| `add_slide(insertion_index, layout)` | createSlide | ⭐ ship first |
| `add_text_box(slide_id, text, x, y, w, h)` | createShape(TEXT_BOX) + insertText | ⭐ ship first |
| `add_image(slide_id, url, x, y, w, h)` | createImage | ⭐ ship first |
| `update_text(object_id, new_text)` | deleteText(ALL) + insertText | ⭐ ship first |
| `update_text_style(object_id, bold, italic, color, size, font, underline, text_range)` | updateTextStyle | ⭐ ship first |
| `set_slide_background(slide_id, color)` | updatePageProperties | ⭐ ship first |
| `delete_element(object_id)` | deleteObject | ⭐ ship first |
| `move_element(object_id, x, y)` | updatePageElementTransform (translate) | ⭐ ship first |
| `resize_element(object_id, w, h)` | updatePageElementTransform (scale) | ⭐ ship first |
| `set_element_color(object_id, fill_color, outline_color)` | updateShapeProperties | ⭐ ship first |
| `add_bullets(object_id, preset)` | createParagraphBullets | — |
| `set_paragraph_style(object_id, alignment, line_spacing, ...)` | updateParagraphStyle | — |
| `add_shape(slide_id, shape_type, x, y, w, h)` | createShape | — |
| `add_table(slide_id, rows, cols, x, y, w, h)` | createTable | — |
| `add_line(slide_id, x1, y1, x2, y2)` | createLine | — |
| `duplicate_element(object_id)` | duplicateObject | — |
| `replace_all_text(find, replace)` | replaceAllText | — |
| `reorder_slides(slide_ids, insertion_index)` | updateSlidesPosition | — |
| `insert_table_row(table_id, row, column, below)` | insertTableRows | — |
| `insert_table_column(table_id, row, column, right)` | insertTableColumns | — |
| `delete_table_row(table_id, row, column)` | deleteTableRow | — |
| `delete_table_column(table_id, row, column)` | deleteTableColumn | — |
| `set_cell_background(table_id, row, column, color)` | updateTableCellProperties | — |
| `merge_cells(table_id, row_start, col_start, row_end, col_end)` | mergeTableCells | — |
| `unmerge_cells(table_id, row_start, col_start, row_end, col_end)` | unmergeTableCells | — |
| `set_line_style(object_id, weight, dash_style, color)` | updateLineProperties | — |

**Start with the 10 starred as MVP.** Ship those, confirm voice editing works end-to-end. Then add the rest in a second PR.

### Use what's already there

- All 26 Pydantic wrappers exist already in [app/slides_schema.py:815-875](app/slides_schema.py). Each narrow tool constructs a wrapper instance, calls `.model_dump(exclude_none=True)`, and appends to the batch buffer. The request reaches Google pre-validated.
- `validate_typed_requests()` at [app/slides_schema.py:957](app/slides_schema.py) stays as defence-in-depth for the buffer-flush path.
- The old `execute_slide_requests` at [app/agent.py:90](app/agent.py) can stay as an escape hatch during the transition — delete it once the narrow tools cover every prompt the eval suite throws at them.

### Don't do these things

- ❌ Don't emit the 26-branch `any_of` schema the previous session tried. It crashed native-audio with 1011. The `TypedBatchTool` + `pydantic_to_gemini_schema` + `build_slide_request_item_schema` plumbing in [app/agent.py](app/agent.py) and [app/slides_schema.py](app/slides_schema.py) was reverted for a reason. Leave that code alone or delete it; don't re-enable it.
- ❌ Don't change the Drive picker UI. It works — [app/static/index.html:1076-1083](app/static/index.html) routes to `showDrivePicker()`, picker + search + Back all work in preview (verified by screenshot in previous session).
- ❌ Don't change [app/auth.py:63-84](app/auth.py) `force_reauth` logic. It's the fix for stale-refresh-token recovery and is tested.
- ❌ Don't touch Stripe, Google Slides Add-on, PowerPoint Add-in, or any Step 12+ work. Those are downstream of Step 15.

---

## Test plan (this is the acceptance criteria)

The handoff is "done" only when **all** of these pass, tested in a real browser against the live preview server:

1. **Sign-in loop recovery:** click Edit → Drive picker loads → you see your real Google Slides list.
2. **Open a deck → voice opens.** Agent says "ready to edit".
3. **Chart on slide 2 via voice.** Say "add a chart showing [any data] to slide 2". Agent calls `create_chart`, then narrow edit tools (e.g. `add_image` with the chart URL). **Session does NOT crash with 1011.** Chart appears in the iframe within 5 seconds of the agent saying "done".
4. **Text edit via voice.** "Change the title of slide 1 to 'Q4 Board Review'." Title changes. Iframe refreshes.
5. **Style change via voice.** "Make all the bullets on slide 3 purple." Bullets become purple.
6. **Long-ish session:** 10 consecutive voice edits without a 1011 or 1007. (HANDOFF.md previously reported 1007 at ~2680 events; that's a different failure mode — don't conflate.)
7. **Creation flow still works.** Start fresh, create a 5-slide deck from scratch via `text_agent`. Eval suite in [app/eval.py](app/eval.py) still passes.

**Sign off per requirement** in your handoff-back doc with log excerpts as proof.

---

## Files to touch (expected scope)

- [app/agent.py](app/agent.py) — add ~10-20 narrow tool functions, register on both agents, remove old `execute_slide_requests` once done
- [app/slides_schema.py](app/slides_schema.py) — reuse existing wrappers; possibly add small helpers to build each wrapper from narrow tool kwargs
- [app/slidemakr.py](app/slidemakr.py) — add the batch buffer / flush mechanism if you go with the commit-buffer design
- AGENT_INSTRUCTION / EDIT_INSTRUCTION — simplify drastically; remove the paragraphs explaining how to build JSON batches, replace with "call the tools for what you want"

**Do NOT touch:**
- [app/static/index.html](app/static/index.html) (frontend is fine)
- [app/auth.py](app/auth.py) (auth is fine)
- [app/db.py](app/db.py) (unless you need a new field)
- [app/server.py](app/server.py) (`/ws` handler is fine)

---

## How to start the new session

**Run the dev server via preview:**
```
preview_start name="SlideMakr ADK Server"  # .claude/launch.json config
```
Server on :8080, auto-reload.

**Preview verification gate (from [PROJECT_PLAN.md](PROJECT_PLAN.md)):** after every edit to `app/*.py` or `app/static/index.html`, reload preview, check `preview_console_logs level=error` and `preview_logs level=error` are clean. Skip only for non-browser changes.

**Before writing any code, reproduce the bug.** Open preview → sign in → pick a presentation → "add a chart of revenue growth" → capture logs. If it crashes 1011 without the typed schema, hypothesis 1 is wrong and we need to dig into context accumulation before decomposing tools. If it succeeds, we know the revert was enough and tool decomposition becomes a longer-term architectural win (still worth doing, but less urgent than it looks).

---

## How this fits into PROJECT_PLAN.md

See [PROJECT_PLAN.md Execution Priority](PROJECT_PLAN.md) — the current priority ordering needs an update. Insert Step 15 as the immediate next blocker:

```
A (HANDOFF.md fixes) → A1 ✅ A2 ✅ A3 ✅  ← all done in previous session
  ↓
Step 15 (Tool Decomposition)        ← YOU ARE HERE — gate to everything below
  ↓
Step 11 (Stripe)
  ↓
Step 9 (Wait UX)
  ↓
Step 12 (Google Slides Add-on)
  ↓
Step 13 (PowerPoint Add-in)
  ↓
Step 14 (Session History)
  ↓
Step 8 (Comment Resolution)
  ↓
Step 10 (Multi-Agent Speed)
```

After you finish Step 15, update PROJECT_PLAN.md to mark it done and flip the next-up marker to Step 11.

---

## Previous-session context (what else is fresh on disk)

- **[PROJECT_PLAN.md](PROJECT_PLAN.md)** — the master roadmap. Contains the Preview Verification Gate rule, Steps 12/13/14 as new additions, expanded Step 9, the new Stripe Checkout spec with dev/prod test matrix.
- **Step 7 HANDOFF regressions (A1, A2, A3) are fixed:**
  - A1: presentation spotlight banner + iframe refresh
  - A2: voice audio playback uses single shared output AudioContext
  - A3: schema-error crashes dropped via `validate_typed_requests` + expanded hallucination drop-list
- **Drive picker restored** — Edit CTA → list view + search, not voice-mode. Fixed the stale-refresh-token loop via `/auth/login?force_reauth=1`.
- **Typed wrappers infrastructure built** — 26 Pydantic wrappers + Gemini schema converter exist in [app/slides_schema.py](app/slides_schema.py). They're wired at the validator layer only; the Gemini-schema-layer wiring was reverted (caused the 1011). Reuse the wrappers; don't re-emit them as a schema.

---

## Things to verify before you open a PR

- `app/agent.py` has no remaining references to `execute_slide_requests_tool` or `TypedBatchTool`
- Old `execute_slide_requests(presentation_id, requests)` is deleted (not just unused) once narrow tools cover eval cases
- Agent instructions drastically shortened — no more "Generate a SINGLE JSON array" paragraphs
- Eval suite (`python -m app.eval`) still passes
- A voice-editing session survives 10+ tool calls on the same slide without a 1011 or 1007 close code

When it works: commit with `feat: tool decomposition — narrow tools + server-side batching for voice editing`. Push. Update PROJECT_PLAN.md. Tell the next session Stripe is unblocked.
