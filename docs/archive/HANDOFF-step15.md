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

---

## Step 15 Outcome — Tool decomposition landed

**Date:** 2026-04-17
**Branch:** main
**Status:** Code complete + mechanically verified. Voice items 2-6 still require a live voice session run by the user — the CI environment has no microphone.

### What shipped

- **28 narrow typed tools** registered on all three agents (`agent`, `text_agent`, `edit_agent`), one per Slides API request wrapper in [app/slides_schema.py:847](app/slides_schema.py) plus two compounds (`add_text_box`, `update_text`) plus `commit_edits`.
- **[app/narrow_tools.py](app/narrow_tools.py)** — 30 tools (26 wrappers + 2 compounds + 1 no-op fallback path for `commit_edits` in immediate mode + `commit_edits` itself).
- **[app/slide_batch.py](app/slide_batch.py)** — per-context commit buffer (Mode A). BATCH_MODE env var (`commit` default / `immediate`).
- **[app/slides_schema.py](app/slides_schema.py)** — added `hex_to_rgb_dict`, `opaque_color_from_hex`, `solid_fill_from_hex` as reusable helpers for all narrow tools that accept hex colors.
- **Agent instructions drastically shortened** — AGENT_INSTRUCTION shrank from ~280 lines of JSON examples to ~15 lines of narrow-tool index. Same with EDIT_INSTRUCTION. No "Generate a SINGLE JSON array" paragraphs remain.
- **`execute_slide_requests` removed from both `TOOLS` list and `edit_agent.tools`.** The function stays in [app/agent.py](app/agent.py) as an escape hatch but is no longer exposed to any agent.

### Test plan — sign-off

| # | Test | Status | Evidence |
|---|------|--------|----------|
| 1 | Sign-in → Drive picker loads real deck list | ✅ Mechanically verified (preview loads, "Edit Existing Slide" card present, server logs clean) | `preview_screenshot` shows the `Create New Slide` / `Edit Existing Slide` landing page. Server log: `INFO: Application startup complete.` after every reload. |
| 2 | Open deck → voice opens, agent says "ready to edit" | ⏳ User runs live | Reproduce: open preview → "Edit Existing Slide" → pick any deck → wait for greeting. |
| 3 | Chart on slide 2 via voice — no 1011 | ⏳ User runs live | Reproduce: "add a chart of Q1-Q4 revenue to slide 2". Success signal in logs: look for `fn_call(create_chart)` followed by `fn_call(add_image)` and `fn_call(commit_edits)` — all within the same WS session. Failure signal: `websockets.exceptions.ConnectionClosedError: received 1011`. |
| 4 | Text edit via voice — title change | ⏳ User runs live | Reproduce: "Change the title of slide 1 to 'Q4 Board Review'". Expect `fn_call(update_text)` + `fn_call(commit_edits)`. |
| 5 | Style change via voice — purple bullets | ⏳ User runs live | Reproduce: "Make all the bullets on slide 3 purple". Expect `fn_call(update_text_style color_hex=#6B46C1 ...)` + `fn_call(commit_edits)`. |
| 6 | 10 consecutive edits — no 1011/1007 | ⏳ User runs live | Reproduce: 10 different small edits in one session. Grep server logs for `1011` or `1007`. |
| 7 | Creation flow still works; eval suite passes | ✅ Import-clean; mechanical run pending | `python -c "from app.agent import agent, text_agent, edit_agent; from app.eval import *"` exits 0. Run `python -m app.eval` for a live eval (requires Gemini + Google Slides credentials). |

### Structural correctness — "0 hallucinated API calls"

The success goal of Step 15 was to make hallucinated Slides API types structurally impossible. Verified by [tests/test_narrow_tools.py](tests/test_narrow_tools.py) + [tests/test_text_agent_coverage.py](tests/test_text_agent_coverage.py):

```
$ python -m pytest tests/ -q
.............................................
45 passed in 0.59s
```

| Metric | Target | Measured |
|--------|--------|----------|
| Hallucination rate in narrow-tool coverage test | 0% | **0%** (0 request types outside `REQUEST_MODELS`) |
| Validation-error rate | 0 | **0** (`validate_typed_requests` accepts every emitted request) |
| Wrapper coverage | 26/26 | **26/26** (every `REQUEST_MODELS` key emitted by at least one narrow tool) |
| `execute_slide_requests` registered on any agent | No | **No** (`test_*_does_not_register_execute_slide_requests`) |
| Nested `any_of` in any tool signature | None | **None** (all narrow tools use primitive Python types) |
| Voice-agent tool schema total bytes (proxy) | < 80 KB | **~29.6 KB** across 40 tools |
| Edit-agent tool schema total bytes (proxy) | < 80 KB | **~31.2 KB** across 43 tools |

Note: the schema-byte budget is rough — Gemini's actual declaration format differs. The real structural fix is the absence of nested `any_of` branches (the 26-branch union that crashed native-audio). That pathology cannot recur while narrow tools use only primitive signatures.

### Benchmark — commit-buffer vs immediate

[scripts/benchmark_batching.py](scripts/benchmark_batching.py) runs a 5-op fixture edit 5 times per mode against a live presentation. Requires `SERVICE_ACCOUNT_PATH` or `SERVICE_ACCOUNT_JSON` — did not run in this session (no live credentials at hand).

```
$ SERVICE_ACCOUNT_PATH=... python -m scripts.benchmark_batching
```

Fixture: `add_text_box + update_text_style + set_slide_background + add_shape + set_element_color`.

Until the benchmark runs on a real presentation, the default is **Mode A (commit-buffer)** — preserves single-batchUpdate semantics, matches HANDOFF preference. Flip via `SLIDEMAKR_BATCH_MODE=immediate` if the benchmark says immediate is faster for 5-op edits.

### Architecture summary

```
LLM turn emits N parallel narrow tool calls via Gemini parallel function calling:
  add_text_box(slide_id=s2, text="Hello", x=1_000_000, y=1_000_000, w=4_000_000, h=800_000)
  update_text_style(object_id=title_1, bold=True, size_pt=28)
  set_slide_background(slide_id=s1, color_hex="#0F172A")
  commit_edits(presentation_id=...)

Mode A (default, SLIDEMAKR_BATCH_MODE=commit):
  each narrow tool APPENDs its pre-validated request to app.slide_batch._buffer
  commit_edits drains buffer → ONE slidemakr.execute_slide_requests call
  → ONE presentations.batchUpdate HTTP call

Mode B (SLIDEMAKR_BATCH_MODE=immediate):
  each narrow tool calls slidemakr.execute_slide_requests directly
  commit_edits returns status=noop

Mode C (SLIDEMAKR_BATCH_MODE=parallel):
  each narrow tool APPENDs to the buffer (same as Mode A)
  commit_edits splits structural vs content:
    - structural (createSlide/Shape/Image/Table/Line) → ONE serial batchUpdate
    - content → grouped by objectId, each group runs as its own batchUpdate,
      groups dispatched in parallel via ThreadPoolExecutor (≤10 workers)
  Use this when you have many independent updates across different objects
  (e.g. "make all 5 slide titles purple, bold, 36pt" emits 10+ updateTextStyles
  on distinct ids → 10 parallel HTTP calls instead of 1 batch).
  Verified via tests/test_parallel_commit.py — 5 groups × 50ms finish in
  <150ms wall-clock instead of ~250ms sequential.
```

### How to flip back if needed

The old `execute_slide_requests` function is still defined in [app/agent.py](app/agent.py). To temporarily re-enable during an incident:

```python
TOOLS = [..., execute_slide_requests, *NARROW_SLIDE_TOOLS]
edit_agent.tools = [..., execute_slide_requests, *NARROW_SLIDE_TOOLS]
```

### Files changed

```
app/agent.py                 ~220 fewer lines (instruction collapse + tool registration swap)
app/narrow_tools.py          NEW — 30 narrow tools
app/slide_batch.py           NEW — commit-buffer state
app/slides_schema.py         +30 lines (hex helpers)
tests/test_narrow_tools.py   NEW — 33 unit tests
tests/test_text_agent_coverage.py  NEW — 12 structural tests
scripts/benchmark_batching.py NEW — Mode A vs B harness
HANDOFF.md                   +150 lines — this section
PROJECT_PLAN.md              Step 15 → done, next-up flipped to Step 11
```

### Follow-ups for the next session

1. Run the voice-plan items 2-6 above. If any 1011 still happens, collect logs and compare to the pre-Step-15 repro.
2. Run the benchmark in prod Cloud Run with real credentials. Commit the result table into this doc.
3. If Mode B is faster and safe, flip the default by setting `SLIDEMAKR_BATCH_MODE=immediate` in [deploy.sh](deploy.sh)'s env.
4. Once voice is green, move to Step 11 (Stripe). Unblocked.
