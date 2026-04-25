import os
import tempfile

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch

from app.tools import calculator, unit_converter


class TestCalculator:
    def test_addition(self):
        assert "= 4" in calculator("2 + 2")

    def test_multiplication(self):
        assert "= 42" in calculator("6 * 7")

    def test_float_division(self):
        assert "2.5" in calculator("10 / 4")

    def test_precedence(self):
        assert "= 14" in calculator("2 + 3 * 4")

    def test_power(self):
        assert "= 1024" in calculator("2 ** 10")

    def test_floor_division(self):
        assert "= 3" in calculator("17 // 5")

    def test_modulo(self):
        assert "= 2" in calculator("17 % 5")

    def test_division_by_zero(self):
        assert "zero" in calculator("5 / 0").lower()

    def test_invalid_expression(self):
        result = calculator("import os")
        assert "error" in result.lower() or "unsupported" in result.lower()


class TestUnitConverter:
    def test_km_to_miles(self):
        assert "0.621" in unit_converter(1.0, "km", "miles")

    def test_celsius_to_fahrenheit(self):
        result = unit_converter(0.0, "celsius", "fahrenheit")
        assert "32.0" in result or "= 32" in result

    def test_celsius_to_kelvin(self):
        assert "273.15" in unit_converter(0.0, "celsius", "kelvin")

    def test_kg_to_pounds(self):
        assert "2.20" in unit_converter(1.0, "kg", "pounds")

    def test_meters_to_feet(self):
        assert "3.28" in unit_converter(1.0, "m", "feet")

    def test_fahrenheit_to_celsius(self):
        result = unit_converter(100.0, "fahrenheit", "celsius")
        assert "37.777" in result or "37.78" in result

    def test_unknown_unit(self):
        result = unit_converter(1.0, "parsec", "light-year")
        assert "cannot" in result.lower() or "supported" in result.lower()

    def test_same_unit(self):
        result = unit_converter(5.0, "kg", "kg")
        assert "5.0 kg = 5.0 kg" in result or "= 5" in result


MOCK_AGENT_RESULT = {
    "answer": "The answer is 42.",
    "trace": [
        {"step": 1, "type": "reasoning",   "content": "I will calculate this.",                              "timestamp": "2024-01-01T00:00:00+00:00"},
        {"step": 2, "type": "tool_call",   "content": {"tool": "calculator", "input": {"expression": "6 * 7"}}, "timestamp": "2024-01-01T00:00:01+00:00"},
        {"step": 3, "type": "tool_result", "content": {"tool": "calculator", "result": "6 * 7 = 42"},           "timestamp": "2024-01-01T00:00:02+00:00"},
    ],
    "input_tokens": 100,
    "output_tokens": 50,
    "latency_ms": 1234.5,
    "messages": [
        {"role": "user",      "content": "What is 6 * 7?"},
        {"role": "assistant", "content": [{"type": "text", "text": "The answer is 42."}]},
    ],
}


@pytest_asyncio.fixture
async def client():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_path = f.name
    try:
        import app.database as db_module
        original_path = db_module.DB_PATH
        db_module.DB_PATH = tmp_path
        try:
            with patch("app.main.run_agent", new_callable=AsyncMock, return_value=MOCK_AGENT_RESULT):
                from app.main import app
                from app.database import init_db
                await init_db()
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    yield ac
        finally:
            db_module.DB_PATH = original_path
    finally:
        os.unlink(tmp_path)


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert "model" in resp.json()


@pytest.mark.asyncio
async def test_post_task_returns_200(client):
    assert (await client.post("/task", json={"task": "What is 6 * 7?"})).status_code == 200


@pytest.mark.asyncio
async def test_post_task_has_answer(client):
    resp = await client.post("/task", json={"task": "What is 6 * 7?"})
    assert resp.json()["answer"] == "The answer is 42."


@pytest.mark.asyncio
async def test_post_task_has_trace(client):
    data = (await client.post("/task", json={"task": "What is 6 * 7?"})).json()
    assert isinstance(data["trace"], list) and len(data["trace"]) == 3
    types = [s["type"] for s in data["trace"]]
    assert "reasoning" in types
    assert "tool_call" in types
    assert "tool_result" in types


@pytest.mark.asyncio
async def test_post_task_has_metrics(client):
    data = (await client.post("/task", json={"task": "What is 6 * 7?"})).json()
    assert data["input_tokens"] == 100
    assert data["output_tokens"] == 50
    assert data["latency_ms"] > 0
    assert "task_id" in data
    assert "conversation_id" in data


@pytest.mark.asyncio
async def test_get_task_retrieval(client):
    post = await client.post("/task", json={"task": "What is 6 * 7?"})
    task_id = post.json()["task_id"]
    get = await client.get(f"/tasks/{task_id}")
    assert get.status_code == 200
    assert get.json()["task_id"] == task_id
    assert get.json()["answer"] == "The answer is 42."


@pytest.mark.asyncio
async def test_get_task_not_found(client):
    assert (await client.get("/tasks/nonexistent-id-xyz")).status_code == 404


@pytest.mark.asyncio
async def test_conversation_id_propagation(client):
    resp1 = await client.post("/task", json={"task": "Tell me about Paris."})
    conv_id = resp1.json()["conversation_id"]
    resp2 = await client.post("/task", json={"task": "What is its population?", "conversation_id": conv_id})
    assert resp2.status_code == 200
    assert resp2.json()["conversation_id"] == conv_id
