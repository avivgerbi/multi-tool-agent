# Multi-Tool Agent with Observability

A production-ready REST API that accepts natural-language tasks, solves them step-by-step using Claude and real external tools, and returns a fully structured reasoning trace alongside the final answer. All activity is persisted to SQLite for later retrieval.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    FastAPI REST API                      │
│  POST /task  ·  GET /tasks/{id}  ·  GET /health         │
└──────────────────────┬──────────────────────────────────┘
                       │
              ┌────────▼────────┐
              │   Agent Loop    │  (app/agent.py)
              │                 │
              │  1. Build msgs  │
              │  2. Call Claude │◄──── claude-opus-4-7
              │  3. Tool use?   │
              │     └─ Execute  │◄──── Tools (below)
              │     └─ Append   │
              │  4. end_turn?   │
              │     └─ Return   │
              └────────┬────────┘
                       │
       ┌───────────────▼───────────────┐
       │            Tools              │
       │  calculator   — safe AST eval │
       │  weather      — OpenWeatherMap│
       │  web_search   — DuckDuckGo    │
       │  unit_converter — pure Python │
       └───────────────────────────────┘
                       │
       ┌───────────────▼───────────────┐
       │     SQLite Persistence        │
       │  tasks.db   — task + trace log│
       └───────────────────────────────┘
```

### Agent Reasoning Loop

The agent implements Anthropic's **tool use** pattern with a manual loop for full observability:

1. **Build messages** — start from the user's task, optionally prepending prior conversation turns for multi-turn support.
2. **Call Claude** — send all tool definitions and the message history to `claude-opus-4-7`.
3. **Inspect stop reason**:
   - `tool_use` → extract each `ToolUseBlock`, execute the tool, append the result, and loop again.
   - `end_turn` → extract the final text answer and stop.
4. **Record trace** — every reasoning block, tool call, and tool result is captured with a timestamp and step number.
5. **Track metrics** — input/output token counts are accumulated across all loop iterations; wall-clock latency is measured end-to-end.
6. **Persist** — the complete result (answer, trace, token usage, full message history) is written to SQLite.

A `MAX_ITERATIONS` guard prevents infinite loops. The Anthropic client is a module-level singleton so it is not re-created on every request.

---

## Setup & Run

### Prerequisites

- Docker & Docker Compose **or** Python 3.11+
- An Anthropic API key — **required** (get one at [console.anthropic.com](https://console.anthropic.com))
- An OpenWeatherMap API key — **optional**, enables the `weather` tool (free at [openweathermap.org](https://openweathermap.org))

### 1. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in your ANTHROPIC_API_KEY
```

### 2a. Run with Docker (recommended)

```bash
mkdir -p data
docker compose up --build
```

### 2b. Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

The API and web UI are available at **http://localhost:8000**.

### 3. Run tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/health` | Health check — returns model name |
| `POST` | `/task` | Submit a task; returns answer + trace |
| `GET`  | `/tasks/{task_id}` | Retrieve a past task by ID |

### POST /task

**Request:**
```json
{
  "task": "What is 15% of 840?",
  "conversation_id": "optional-uuid-for-multi-turn"
}
```

**Response:**
```json
{
  "task_id": "uuid",
  "conversation_id": "uuid",
  "task": "What is 15% of 840?",
  "answer": "15% of 840 is 126.",
  "trace": [
    { "step": 1, "type": "reasoning",   "content": "I need to calculate 15% of 840.", "timestamp": "..." },
    { "step": 2, "type": "tool_call",   "content": { "tool": "calculator", "input": { "expression": "840 * 0.15" } }, "timestamp": "..." },
    { "step": 3, "type": "tool_result", "content": { "tool": "calculator", "result": "840 * 0.15 = 126.0" }, "timestamp": "..." }
  ],
  "latency_ms": 1823.4,
  "input_tokens": 820,
  "output_tokens": 112,
  "created_at": "2024-11-25T12:00:00Z"
}
```

---

## Available Tools

| Tool | Description |
|------|-------------|
| `calculator` | Safe AST-based math evaluator — no `eval()` |
| `weather` | Current conditions via OpenWeatherMap API |
| `web_search` | DuckDuckGo search — no API key needed |
| `unit_converter` | Length, weight, and temperature conversions |

---

## Example Tasks & Traces

### 1. Calculator

**Task:** `What is (2^10 + 15% of 840) / 3?`

| Step | Type | Content |
|------|------|---------|
| 1 | reasoning | "I need to calculate 2^10, then 15% of 840, add them, and divide by 3." |
| 2 | tool_call | `calculator("2 ** 10")` |
| 3 | tool_result | `2 ** 10 = 1024` |
| 4 | tool_call | `calculator("840 * 0.15")` |
| 5 | tool_result | `840 * 0.15 = 126.0` |
| 6 | tool_call | `calculator("(1024 + 126) / 3")` |
| 7 | tool_result | `(1024 + 126) / 3 = 383.3333333333` |
| 8 | reasoning | "The result is approximately 383.33." |

---

### 2. Unit Conversion

**Task:** `Convert 100 miles to kilometres, then tell me how many light-seconds that is.`

| Step | Type | Content |
|------|------|---------|
| 1 | reasoning | "I'll convert miles to km first, then km to light-seconds." |
| 2 | tool_call | `unit_converter(100, "miles", "km")` |
| 3 | tool_result | `100 miles = 160.9344 km` |
| 4 | tool_call | `calculator("160934.4 / 299792458")` |
| 5 | tool_result | `≈ 0.000537 light-seconds` |
| 6 | reasoning | "100 miles ≈ 160.93 km, which is about 0.000537 light-seconds." |

---

### 3. Weather Comparison

**Task:** `Is it warmer in London or Tokyo right now?`

| Step | Type | Content |
|------|------|---------|
| 1 | reasoning | "I'll fetch the weather for both cities and compare." |
| 2 | tool_call | `weather("London")` |
| 3 | tool_result | `Overcast clouds, 12°C, feels like 10°C` |
| 4 | tool_call | `weather("Tokyo")` |
| 5 | tool_result | `Clear sky, 22°C, feels like 21°C` |
| 6 | reasoning | "Tokyo is warmer at 22°C vs London's 12°C." |

---

### 4. Web Search

**Task:** `Who won the most recent FIFA World Cup and what was the final score?`

| Step | Type | Content |
|------|------|---------|
| 1 | reasoning | "I'll search for the latest World Cup result." |
| 2 | tool_call | `web_search("FIFA World Cup 2022 winner final score")` |
| 3 | tool_result | Top 5 DuckDuckGo results summarised |
| 4 | reasoning | Summary of Argentina's win over France |

---

### 5. Multi-Turn Conversation

**Turn 1 — Task:** `Tell me about the Eiffel Tower.`
**Turn 2 — Task:** `How tall is it in feet?` *(same `conversation_id`)*

The agent carries forward the message history from turn 1, so it knows "it" refers to the Eiffel Tower and calls `unit_converter(330, "m", "feet")` without needing to re-ask.

---

## Design Decisions

- **No agent framework** — the loop runs directly against the Anthropic SDK for full transparency and control over every step.
- **AST-safe calculator** — uses Python's `ast` module with an explicit operator allowlist instead of `eval()`, preventing code injection.
- **DuckDuckGo for web search** — no API key required; runs in a thread pool via `asyncio.to_thread` to keep the async event loop free.
- **Plain-dict message history** — all Anthropic SDK content blocks are converted to JSON-serialisable dicts before storage, making multi-turn reconstruction from SQLite straightforward.
- **Singleton Anthropic client** — created once at module import time, not on every request.
- **SQLite for persistence** — zero external dependencies; the `data/` volume keeps the database alive across Docker restarts.
