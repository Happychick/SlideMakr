# SlideMakr

AI agent that generates Google Slides presentations from natural language instructions,
using LangGraph for orchestration, GPT-4, and the Google Slides/Drive APIs.

## Commands

```bash
# Start LangGraph dev server (port 2024)
cd studio && langgraph dev
# or from project root:
slidemakr-venv/bin/langgraph dev --config studio/langgraph.json

# Install dependencies
pip install -r requirements.txt
```

## Architecture

```
SlideMakr/
  studio/
    langgraph.json          # Registers both graphs for LangGraph server
    slide_makr_agent.py     # v1: simpler agent (tools_condition routing)
    slide_makr_agent2.py    # v2: refined agent with explicit routing functions
    .env                    # API keys (gitignored)
  requirements.txt          # Root deps (includes langgraph-cli[inmem])
  slidemakr-venv/           # Primary virtualenv
```

Both agents expose a `graph` variable compiled from a `StateGraph(SlideMakrState)`.
`langgraph.json` registers them as `slide_makr_agent` and `slide_makr_agent2`.

## Agent Flow

1. `generate_code_tool` — LLM generates Google Slides API JSON requests
2. `create_presentation_tool` — creates a new presentation, returns ID
3. `run_generated_code_tool` — calls `batchUpdate` per-request; retries on error
4. `get_email` node (interrupt) — waits for user email input
5. `share_presentation_node` — shares via Drive API

## Environment (studio/.env)

```
OPENAI_API_KEY=...
LANGCHAIN_API_KEY=...          # LangSmith tracing
LANGCHAIN_TRACING_V2=true
LANGCHAIN_PROJECT=slide_makr_agent
SERVICE_ACCOUNT_PATH=...       # Absolute path to Google service account JSON
```

## Gotchas

- `slide_makr_agent.py` (v1) has a bug: references `AGENT_SYSTEM_PROMPT` and `response`
  before they are defined — use `slide_makr_agent2.py` as the working version.
- `langgraph` CLI is NOT on system PATH — always use `slidemakr-venv/bin/langgraph`.
- The Google service account JSON must be shared with the target Google account to
  allow `share_presentation` to succeed.
- `run_generated_code_tool` calls `batchUpdate` one request at a time (not batched),
  so errors are isolated per slide element.
- `python-dotenv` is not in `requirements.txt` but is used in both agents via `load_dotenv()`.
