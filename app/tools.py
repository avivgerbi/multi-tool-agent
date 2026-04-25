import ast
import asyncio
import operator as op
import os

import httpx
from duckduckgo_search import DDGS

_OPERATORS: dict = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.Pow: op.pow,
    ast.Mod: op.mod,
    ast.FloorDiv: op.floordiv,
    ast.USub: op.neg,
    ast.UAdd: op.pos,
}


def _eval_node(node: ast.expr) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp):
        fn = _OPERATORS.get(type(node.op))
        if fn is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return fn(_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        fn = _OPERATORS.get(type(node.op))
        if fn is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return fn(_eval_node(node.operand))
    raise ValueError(f"Unsupported expression type: {type(node).__name__}")


def calculator(expression: str) -> str:
    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _eval_node(tree.body)
        display = int(result) if result == int(result) else round(result, 10)
        return f"{expression} = {display}"
    except ZeroDivisionError:
        return "Error: division by zero"
    except Exception as exc:
        return f"Error evaluating '{expression}': {exc}"


async def weather(city: str) -> str:
    api_key = os.getenv("OPENWEATHERMAP_API_KEY", "")
    if not api_key:
        return f"Weather tool unavailable: OPENWEATHERMAP_API_KEY not set. Cannot retrieve weather for '{city}'."
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {"q": city, "appid": api_key, "units": "metric"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url, params=params)
            if resp.status_code == 404:
                return f"City '{city}' not found."
            resp.raise_for_status()
            d = resp.json()
            desc = d["weather"][0]["description"].capitalize()
            temp = d["main"]["temp"]
            feels = d["main"]["feels_like"]
            humidity = d["main"]["humidity"]
            wind = d["wind"]["speed"]
            return (
                f"Weather in {city}: {desc}. "
                f"Temperature {temp}°C (feels like {feels}°C), "
                f"humidity {humidity}%, wind {wind} m/s."
            )
        except httpx.HTTPError as exc:
            return f"HTTP error fetching weather: {exc}"


def _search_sync(query: str) -> str:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        if not results:
            return f"No results found for: {query}"
        lines = [f"Web search results for '{query}':"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            body = r.get("body", "")[:250]
            href = r.get("href", "")
            lines.append(f"\n{i}. {title}\n   {body}\n   Source: {href}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Search error: {exc}"


async def web_search(query: str) -> str:
    return await asyncio.to_thread(_search_sync, query)


_LENGTH_M: dict[str, float] = {
    "mm": 1e-3, "millimeter": 1e-3, "millimeters": 1e-3,
    "cm": 1e-2, "centimeter": 1e-2, "centimeters": 1e-2,
    "m": 1.0,   "meter": 1.0,       "meters": 1.0,
    "km": 1e3,  "kilometer": 1e3,   "kilometers": 1e3,
    "in": 0.0254,   "inch": 0.0254,   "inches": 0.0254,
    "ft": 0.3048,   "foot": 0.3048,   "feet": 0.3048,
    "yd": 0.9144,   "yard": 0.9144,   "yards": 0.9144,
    "mi": 1609.344, "mile": 1609.344, "miles": 1609.344,
}

_WEIGHT_KG: dict[str, float] = {
    "mg": 1e-6, "milligram": 1e-6, "milligrams": 1e-6,
    "g": 1e-3,  "gram": 1e-3,      "grams": 1e-3,
    "kg": 1.0,  "kilogram": 1.0,   "kilograms": 1.0,
    "t": 1e3,   "ton": 1e3,        "tons": 1e3, "tonne": 1e3, "tonnes": 1e3,
    "oz": 0.0283495, "ounce": 0.0283495, "ounces": 0.0283495,
    "lb": 0.453592,  "pound": 0.453592,  "pounds": 0.453592, "lbs": 0.453592,
}

_TEMP_ALIASES = {"c": "celsius", "f": "fahrenheit", "k": "kelvin"}


def unit_converter(value: float, from_unit: str, to_unit: str) -> str:
    fu_raw = from_unit.lower().strip()
    tu_raw = to_unit.lower().strip()
    fu = _TEMP_ALIASES.get(fu_raw, fu_raw)
    tu = _TEMP_ALIASES.get(tu_raw, tu_raw)

    temp_units = {"celsius", "fahrenheit", "kelvin"}
    if fu in temp_units or tu in temp_units:
        if fu == "celsius":
            c = value
        elif fu == "fahrenheit":
            c = (value - 32) * 5 / 9
        elif fu == "kelvin":
            c = value - 273.15
        else:
            return f"Unknown temperature unit: '{from_unit}'"

        if tu == "celsius":
            result = c
        elif tu == "fahrenheit":
            result = c * 9 / 5 + 32
        elif tu == "kelvin":
            result = c + 273.15
        else:
            return f"Unknown temperature unit: '{to_unit}'"
        return f"{value} {from_unit} = {round(result, 4)} {to_unit}"

    if fu in _LENGTH_M and tu in _LENGTH_M:
        return f"{value} {from_unit} = {round(value * _LENGTH_M[fu] / _LENGTH_M[tu], 6)} {to_unit}"

    if fu in _WEIGHT_KG and tu in _WEIGHT_KG:
        return f"{value} {from_unit} = {round(value * _WEIGHT_KG[fu] / _WEIGHT_KG[tu], 6)} {to_unit}"

    return (
        f"Cannot convert '{from_unit}' → '{to_unit}'. "
        "Supported: length (mm/cm/m/km/inch/foot/yard/mile), "
        "weight (mg/g/kg/ton/oz/lb), "
        "temperature (celsius/fahrenheit/kelvin)."
    )
