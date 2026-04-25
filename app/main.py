import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

# load_dotenv must run before importing app modules that call os.getenv at import time
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .agent import MODEL, run_agent
from .database import get_conversation_messages, get_task, init_db, save_task
from .models import TaskRequest, TaskResponse, TraceStep


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Multi-Tool Agent API",
    description="An AI agent that reasons step-by-step and calls tools to solve tasks.",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/health", summary="Health check")
async def health():
    return {"status": "ok", "model": MODEL}


@app.post("/task", response_model=TaskResponse, summary="Submit a task")
async def create_task(request: TaskRequest):
    task_id = str(uuid.uuid4())
    conversation_id = request.conversation_id or str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()

    prior_messages: list[dict] = []
    if request.conversation_id:
        prior_messages = await get_conversation_messages(request.conversation_id)

    try:
        result = await run_agent(
            task=request.task,
            conversation_messages=prior_messages or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    await save_task({
        "task_id": task_id,
        "conversation_id": conversation_id,
        "task": request.task,
        "answer": result["answer"],
        "trace": result["trace"],
        "status": "completed",
        "latency_ms": result["latency_ms"],
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "created_at": created_at,
        "messages": result["messages"],
    })

    return TaskResponse(
        task_id=task_id,
        conversation_id=conversation_id,
        task=request.task,
        answer=result["answer"],
        trace=[TraceStep(**s) for s in result["trace"]],
        latency_ms=result["latency_ms"],
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        created_at=created_at,
    )


@app.get("/tasks/{task_id}", response_model=TaskResponse, summary="Retrieve a past task")
async def get_task_by_id(task_id: str):
    task = await get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    return TaskResponse(
        task_id=task["id"],
        conversation_id=task["conversation_id"],
        task=task["task"],
        answer=task["answer"] or "",
        trace=[TraceStep(**s) for s in task["trace"]],
        latency_ms=task["latency_ms"] or 0.0,
        input_tokens=task["input_tokens"] or 0,
        output_tokens=task["output_tokens"] or 0,
        created_at=task["created_at"],
    )


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse("static/index.html")
