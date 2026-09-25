"""nbp-mcp — MCP server for the Polish National Bank (NBP) public API.

Wraps https://api.nbp.pl/ — the NBP's public REST API for FX rates and gold
fixing. No authentication, no registration, no rate limit. Data flows only
between your machine and NBP's servers.

Tools: get_rate, list_rates, get_rate_history, get_gold, get_gold_history,
convert. Built primarily for Polish JDG owners who need NBP mid-rate for
converting foreign-currency invoices to PLN in bookkeeping.

Author: Bartosz Kuć <firma@bartosza.pl>
Repo:   https://github.com/bartosz-kuc/honest-nbp-mcp
License: MIT
"""

import asyncio
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

NBP_BASE = "https://api.nbp.pl/api"
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
VALID_TABLES = {"A", "B", "C"}
# How far back `before_date` looks for the last published table. Table A is
# daily and Table B weekly (Wednesdays), so 14 days covers weekends, holiday
# runs and a skipped Table B week.
BEFORE_DATE_WINDOW_DAYS = 14


def _validate_currency(code: str) -> str:
    code = code.strip().upper()
    if not CURRENCY_RE.match(code):
        raise ValueError(f"Currency code must be 3 uppercase letters (ISO 4217), got {code!r}")
    return code


def _validate_date(d: str | None) -> str | None:
    if d is None:
        return None
    d = d.strip()
    if not DATE_RE.match(d):
        raise ValueError(f"Date must be YYYY-MM-DD, got {d!r}")
    return d


def _validate_table(t: str) -> str:
    t = t.strip().upper()
    if t not in VALID_TABLES:
        raise ValueError(f"Table must be one of {sorted(VALID_TABLES)}, got {t!r}")
    return t


def _get(path: str) -> dict | list:
    url = f"{NBP_BASE}/{path}"
    resp = requests.get(url, params={"format": "json"}, timeout=30)
    if resp.status_code == 404:
        return {"error": "No data for the requested date/currency (NBP does not publish rates on weekends and holidays)", "status": 404, "url": resp.url}
    resp.raise_for_status()
    return resp.json()


def _rate_for(currency: str, table: str, on_date: str | None) -> dict:
    if currency == "PLN":
        return {"currency": "PLN", "code": "PLN", "mid": 1.0, "note": "PLN is the base currency of NBP tables — rate to itself is 1"}
    path = f"exchangerates/rates/{table}/{currency}"
    if on_date:
        path += f"/{on_date}"
    return _get(path)


def _last_rate_before(currency: str, table: str, before: str) -> dict:
    """Rate from the last NBP table published strictly before `before` (YYYY-MM-DD).

    Queries the date-range endpoint over the BEFORE_DATE_WINDOW_DAYS days preceding
    `before` and keeps only the latest entry, so the result has the same shape as a
    single-date response. Error payloads (404/400) are returned as dicts.
    """
    day = date.fromisoformat(before)
    start = (day - timedelta(days=BEFORE_DATE_WINDOW_DAYS)).isoformat()
    end = (day - timedelta(days=1)).isoformat()
    try:
        data = _get(f"exchangerates/rates/{table}/{currency}/{start}/{end}")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 400:
            return {
                "error": f"NBP rejected the date range {start}..{end} (before_date is probably too far in the future)",
                "status": 400,
                "url": e.response.url,
            }
        raise
    if isinstance(data, dict) and data.get("rates"):
        return {**data, "rates": [max(data["rates"], key=lambda r: r["effectiveDate"])]}
    return data


server = Server("nbp")


async def _list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_rate",
            description=(
                "Get the NBP exchange rate for a single currency vs PLN, on a specific date or the latest available. "
                "Table A = daily rates for major currencies (USD, EUR, GBP, CHF, JPY, etc.). "
                "Table B = weekly (Wednesday) rates for less common currencies. "
                "Table C = daily buy/sell rates (bid/ask) for major currencies — use for actual FX transactions, not accounting. "
                "For invoice-to-PLN conversion in Polish bookkeeping, use Table A. Note: NBP does not publish on weekends/holidays."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "currency": {"type": "string", "description": "3-letter ISO 4217 code (e.g., USD, EUR, GBP)"},
                    "table": {"type": "string", "enum": ["A", "B", "C"], "default": "A", "description": "NBP table — A (default) for accounting mid-rate"},
                    "date": {"type": "string", "description": "Optional YYYY-MM-DD. Defaults to the latest published rate."},
                },
                "required": ["currency"],
            },
        ),
        Tool(
            name="list_rates",
            description=(
                "List all currency rates from an NBP table on a given date (or latest). "
                "Useful for seeing what's available and comparing multiple currencies at once."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "table": {"type": "string", "enum": ["A", "B", "C"], "default": "A"},
                    "date": {"type": "string", "description": "Optional YYYY-MM-DD. Defaults to the latest published table."},
                },
            },
        ),
        Tool(
            name="get_rate_history",
            description=(
                "Get the NBP exchange rate history for a currency over a date range. "
                "Range must be at most 367 days per NBP API limits."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "currency": {"type": "string", "description": "3-letter ISO 4217 code"},
                    "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "table": {"type": "string", "enum": ["A", "B", "C"], "default": "A"},
                },
                "required": ["currency", "start_date", "end_date"],
            },
        ),
        Tool(
            name="get_gold",
            description=(
                "Get the NBP gold fixing (cena złota) in PLN per gram of 1000-fineness gold, on a specific date or the latest. "
                "Published daily on business days."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Optional YYYY-MM-DD. Defaults to the latest published fixing."},
                },
            },
        ),
        Tool(
            name="get_gold_history",
            description=(
                "Get NBP gold fixing history over a date range. Range must be at most 367 days per NBP API limits."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                },
                "required": ["start_date", "end_date"],
            },
        ),
        Tool(
            name="convert",
            description=(
                "Convert an amount from one currency to another using NBP mid-rate (Table A, or B if not in A). "
                "Primary use case: converting a foreign-currency invoice to PLN for Polish bookkeeping. "
                "Polish tax law requires using the NBP mid-rate from the last business day BEFORE the invoice date — "
                "pass the invoice date as `before_date` and the server uses the last table NBP published strictly before it. "
                "`rate_date` instead uses the table published on exactly that date (no data on weekends/holidays). "
                "Returns the amount, the rate used, and the effective date."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "amount": {"type": "number", "description": "Amount to convert"},
                    "from_currency": {"type": "string", "description": "Source currency, 3-letter ISO 4217"},
                    "to_currency": {"type": "string", "default": "PLN", "description": "Target currency (default PLN)"},
                    "rate_date": {"type": "string", "description": "Optional YYYY-MM-DD. Uses the table published on exactly this date (no data on weekends/holidays). Defaults to the latest published rate."},
                    "before_date": {"type": "string", "description": "Optional YYYY-MM-DD, e.g. the invoice date. Uses the last table published strictly before this date. Cannot be combined with rate_date."},
                },
                "required": ["amount", "from_currency"],
            },
        ),
    ]


async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "get_rate":
        currency = _validate_currency(arguments["currency"])
        table = _validate_table(arguments.get("table", "A"))
        on_date = _validate_date(arguments.get("date"))
        result = _rate_for(currency, table, on_date)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    if name == "list_rates":
        table = _validate_table(arguments.get("table", "A"))
        on_date = _validate_date(arguments.get("date"))
        path = f"exchangerates/tables/{table}"
        if on_date:
            path += f"/{on_date}"
        result = _get(path)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    if name == "get_rate_history":
        currency = _validate_currency(arguments["currency"])
        table = _validate_table(arguments.get("table", "A"))
        start = _validate_date(arguments["start_date"])
        end = _validate_date(arguments["end_date"])
        path = f"exchangerates/rates/{table}/{currency}/{start}/{end}"
        result = _get(path)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    if name == "get_gold":
        on_date = _validate_date(arguments.get("date"))
        path = "cenyzlota"
        if on_date:
            path += f"/{on_date}"
        result = _get(path)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    if name == "get_gold_history":
        start = _validate_date(arguments["start_date"])
        end = _validate_date(arguments["end_date"])
        path = f"cenyzlota/{start}/{end}"
        result = _get(path)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    if name == "convert":
        amount = float(arguments["amount"])
        src = _validate_currency(arguments["from_currency"])
        dst = _validate_currency(arguments.get("to_currency", "PLN"))
        rate_date = _validate_date(arguments.get("rate_date"))
        before_date = _validate_date(arguments.get("before_date"))
        if rate_date and before_date:
            raise ValueError("Pass either rate_date or before_date, not both")
        if before_date:
            try:
                date.fromisoformat(before_date)
            except ValueError:
                raise ValueError(f"before_date is not a valid calendar date: {before_date!r}") from None

        if src == dst:
            payload = {"amount_in": amount, "from": src, "to": dst, "amount_out": amount, "rate": 1.0, "note": "Same currency"}
            return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]

        # NBP publishes PLN cross-rates. Fetch each side vs PLN.
        # Try Table A first for both, fall back to B individually.
        def _fetch(code: str) -> dict:
            if code == "PLN":
                return {"currency": "PLN", "code": "PLN", "mid": 1.0, "table": None, "effectiveDate": None}
            if before_date:
                a = _last_rate_before(code, "A", before_date)
                if isinstance(a, dict) and a.get("status") == 404:
                    return _last_rate_before(code, "B", before_date)
                return a
            a = _rate_for(code, "A", rate_date)
            if isinstance(a, dict) and a.get("status") == 404:
                # Not in A — try B (less common currencies)
                b = _rate_for(code, "B", rate_date)
                return b
            return a

        src_data = _fetch(src)
        dst_data = _fetch(dst)

        for label, data in (("from_currency", src_data), ("to_currency", dst_data)):
            if isinstance(data, dict) and data.get("status") in (400, 404):
                return [TextContent(type="text", text=json.dumps({"error": f"No rate available for {label}", "detail": data}, ensure_ascii=False, indent=2))]

        # Rate structures from NBP: {"table": "A", "currency": "...", "code": "USD", "rates": [{"no": "...", "effectiveDate": "YYYY-MM-DD", "mid": 4.05}]}
        def _mid(data: dict) -> tuple[float, str | None, str | None]:
            if data.get("code") == "PLN":
                return 1.0, None, None
            r = data["rates"][0]
            return float(r["mid"]), data.get("table"), r.get("effectiveDate")

        src_mid, src_table, src_date = _mid(src_data)
        dst_mid, dst_table, dst_date = _mid(dst_data)

        # rate = amount_out / amount_in. NBP mid is PLN per 1 unit of foreign.
        # amount_pln = amount_in * src_mid;  amount_out = amount_pln / dst_mid
        rate = src_mid / dst_mid
        amount_out = amount * rate

        payload = {
            "amount_in": amount,
            "from": src,
            "to": dst,
            "amount_out": round(amount_out, 4),
            "rate": round(rate, 6),
            "from_mid_pln": src_mid,
            "to_mid_pln": dst_mid,
            "from_table": src_table,
            "to_table": dst_table,
            "from_effective_date": src_date,
            "to_effective_date": dst_date,
            "note": "Rate = from_mid_pln / to_mid_pln. NBP mid-rate is Polish accounting standard for FX bookkeeping.",
        }
        if before_date:
            payload["before_date"] = before_date
            # UTC date is never ahead of the Warsaw date, so this errs on the side of warning.
            if date.fromisoformat(before_date) > datetime.now(timezone.utc).date():
                payload["warning"] = (
                    "before_date is after today: if NBP has not yet published the table for the preceding "
                    "business day, an older table was used. Check the *_effective_date fields."
                )
        return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]

    raise ValueError(f"Unknown tool: {name}")


async def on_list_tools(ctx, params) -> ListToolsResult:
    return ListToolsResult(tools=await _list_tools())


async def on_call_tool(ctx, params) -> CallToolResult:
    return CallToolResult(content=await _call_tool(params.name, params.arguments or {}))


server.add_request_handler("tools/list", types.PaginatedRequestParams, on_list_tools)
server.add_request_handler("tools/call", types.CallToolRequestParams, on_call_tool)


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def sync_main():
    """Sync entry point for console script."""
    asyncio.run(main())


if __name__ == "__main__":
    sync_main()
