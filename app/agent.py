import os
import time
from datetime import datetime, timezone
from typing import Any

import anthropic

from .models import TraceStep
from .tools import calculator, unit_converter, weather, web_search

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-7")
MAX_ITERATIONS = 20

SYSTEM_PROMPT = (
    "You are a helpful AI assistant with access to tools. "
    "Think through the user's request carefully, use the appropriate tools to "
    "gather information or perform calculations, and then provide a clear, "
    "well-structured final answer based on the results.\n\n"
    "Always reason before acting, and synthesise tool outputs into a coherent response. "
    "Do not expose raw tool output as the final answer without interpretation.\n\n"
    "Write your answers in plain text only. Do not use markdown formatting such as "
    "bold (**), italics (*), bullet points (-), or headers (#)."
)

TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "calculator",
        "description": (
            "Safely evaluate a mathematical expression. "
            "Supports +, -, *, /, ** (power), % (modulo), // (floor division). "
            "Example: '(3 + 5) * 2 / 4'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "The mathematical expression to evaluate.",
                }
            },
            "required": ["expression"],
        },
    },
    {
        "name": "weather",
        "description": "Get current weather conditions for a given city.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City name, e.g. 'London', 'New York', 'Tokyo'.",
                }
            },
            "required": ["city"],
        },
    },
    {
        "name": "web_search",
        "description": "Search the web and return a summary of the top results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "unit_converter",
        "description": (
            "Convert a numeric value between units of length, weight, or temperature. "
            "Supported length: mm, cm, m, km, inch, foot, yard, mile. "
            "Supported weight: mg, g, kg, ton, oz, lb. "
            "Supported temperature: celsius, fahrenheit, kelvin."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "value": {"type": "number", "description": "The numeric value to convert."},
                "from_unit": {"type": "string", "description": "Source unit, e.g. 'km'."},
                "to_unit": {"type": "string", "description": "Target unit, e.g. 'miles'."},
            },
            "required": ["value", "from_unit", "to_unit"],
        },
    },
]

_client = anthropic.AsyncAnthropic()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _block_to_dict(block: Any) -> dict:
    if isinstance(block, dict):
        return block
    block_type = getattr(block, "type", None)
    if block_type == "text":
        return {"type": "text", "text": block.text}
    if block_type == "tool_use":
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": dict(block.input) if block.input else {},
        }
    if hasattr(block, "model_dump"):
        return block.model_dump()
    return {"type": "unknown", "raw": str(block)}


def _blocks_to_dicts(content: Any) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [_block_to_dict(b) for b in content]


async def _execute_tool(name: str, inputs: dict) -> str:
    match name:
        case "calculator":
            return calculator(**inputs)
        case "weather":
            return await weather(**inputs)
        case "web_search":
            return await web_search(**inputs)
        case "unit_converter":
            return unit_converter(**inputs)
        case _:
            return f"Unknown tool: '{name}'"


def _build_result(
    answer: str,
    trace: list[TraceStep],
    input_tokens: int,
    output_tokens: int,
    t0: float,
    messages: list[dict],
) -> dict:
    return {
        "answer": answer,
        "trace": [s.model_dump() for s in trace],
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": (time.perf_counter() - t0) * 1000,
        "messages": messages,
    }


async def run_agent(
    task: str,
    conversation_messages: list[dict] | None = None,
) -> dict:
    t0 = time.perf_counter()
    trace: list[TraceStep] = []
    step = 0
    total_input_tokens = 0
    total_output_tokens = 0

    messages: list[dict] = list(conversation_messages or [])
    messages.append({"role": "user", "content": task})

    for _ in range(MAX_ITERATIONS):
        response = await _client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,  # type: ignore[arg-type]
            messages=messages,       # type: ignore[arg-type]
        )
        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens
        ts = _utc_now()

        for block in response.content:
            if getattr(block, "type", None) == "text" and block.text:
                step += 1
                trace.append(TraceStep(step=step, type="reasoning", content=block.text, timestamp=ts))

        if response.stop_reason == "end_turn":
            answer = next(
                (b.text for b in response.content if getattr(b, "type", None) == "text"),
                "Task completed.",
            )
            messages.append({"role": "assistant", "content": _blocks_to_dicts(response.content)})
            return _build_result(answer, trace, total_input_tokens, total_output_tokens, t0, messages)

        if response.stop_reason == "tool_use":
            tool_blocks = [b for b in response.content if getattr(b, "type", None) == "tool_use"]

            for tb in tool_blocks:
                step += 1
                trace.append(TraceStep(
                    step=step,
                    type="tool_call",
                    content={"tool": tb.name, "input": dict(tb.input)},
                    timestamp=_utc_now(),
                ))

            messages.append({"role": "assistant", "content": _blocks_to_dicts(response.content)})

            tool_results: list[dict] = []
            for tb in tool_blocks:
                result_text = await _execute_tool(tb.name, dict(tb.input))
                step += 1
                trace.append(TraceStep(
                    step=step,
                    type="tool_result",
                    content={"tool": tb.name, "result": result_text},
                    timestamp=_utc_now(),
                ))
                tool_results.append({"type": "tool_result", "tool_use_id": tb.id, "content": result_text})

            messages.append({"role": "user", "content": tool_results})
            continue

        messages.append({"role": "assistant", "content": _blocks_to_dicts(response.content)})
        return _build_result(
            f"Agent stopped unexpectedly (stop_reason={response.stop_reason})",
            trace, total_input_tokens, total_output_tokens, t0, messages,
        )

    return _build_result(
        "Agent reached the maximum number of iterations without completing the task.",
        trace, total_input_tokens, total_output_tokens, t0, messages,
    )
