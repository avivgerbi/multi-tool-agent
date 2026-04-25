from typing import Any, Literal

from pydantic import BaseModel, Field


class TaskRequest(BaseModel):
    task: str = Field(..., description="Natural language task for the agent to solve")
    conversation_id: str | None = Field(
        None,
        description="Existing conversation ID for multi-turn follow-up questions",
    )


class TraceStep(BaseModel):
    step: int
    type: Literal["reasoning", "tool_call", "tool_result"]
    content: Any
    timestamp: str


class TaskResponse(BaseModel):
    task_id: str
    conversation_id: str
    task: str
    answer: str
    trace: list[TraceStep]
    latency_ms: float
    input_tokens: int
    output_tokens: int
    created_at: str
