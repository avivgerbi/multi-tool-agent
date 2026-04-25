from __future__ import annotations
import aiosqlite
import json
import os

DB_PATH = os.getenv("DB_PATH", "tasks.db")


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id            TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                task          TEXT NOT NULL,
                answer        TEXT,
                trace         TEXT,
                status        TEXT DEFAULT 'completed',
                latency_ms    REAL,
                input_tokens  INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                created_at    TEXT NOT NULL,
                messages      TEXT
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversation ON tasks(conversation_id, created_at)"
        )
        await db.commit()


async def save_task(task: dict) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO tasks
              (id, conversation_id, task, answer, trace, status,
               latency_ms, input_tokens, output_tokens, created_at, messages)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task["task_id"],
                task["conversation_id"],
                task["task"],
                task.get("answer"),
                json.dumps(task.get("trace", [])),
                task.get("status", "completed"),
                task.get("latency_ms"),
                task.get("input_tokens", 0),
                task.get("output_tokens", 0),
                task["created_at"],
                json.dumps(task.get("messages", [])),
            ),
        )
        await db.commit()


async def get_task(task_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    data = dict(row)
    data["trace"] = json.loads(data["trace"] or "[]")
    data["messages"] = json.loads(data["messages"] or "[]")
    return data


async def get_conversation_messages(conversation_id: str) -> list:
    """Return the serialised message history from the most recent task in a conversation."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT messages FROM tasks WHERE conversation_id = ? ORDER BY created_at DESC LIMIT 1",
            (conversation_id,),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return []
    return json.loads(row["messages"] or "[]")
