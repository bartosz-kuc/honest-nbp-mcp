# honest-nbp-mcp

Local MCP server for the Polish National Bank (NBP) public API — exchange rates and gold fixing, no authentication required.

Part of the [honest-mcp family](https://github.com/bartosz-kuc?tab=repositories) of small, auditable, local-first MCP servers.

## Why

Every Polish JDG owner receiving a foreign-currency invoice has to convert it to PLN using the NBP mid-rate from **the last business day before the invoice date** — that's what the tax code requires. Today that's a manual look-up in a browser tab, once per invoice, every month.

This server puts the NBP API a single tool call away. Ask your AI "convert 500 EUR from an invoice dated 2026-07-15 to PLN" and it can call `convert` with `before_date="2026-07-15"`: the server takes the mid-rate from the last table NBP published before that date, computes, returns.

Same trust model as the rest of the family: data flows only between your machine and NBP.

## Features

Six tools:

- `get_rate` — NBP exchange rate for one currency on a date (or latest). Tables A / B / C.
- `list_rates` — the whole table on a date (or latest).
- `get_rate_history` — rate history for a currency over a date range (≤367 days).
- `get_gold` — gold fixing (PLN per gram of 1000-fineness) on a date (or latest).
- `get_gold_history` — gold history over a date range.
- `convert` — convert an amount between two currencies using NBP mid-rate, either on an exact date (`rate_date`) or from the last table published before a date (`before_date`, e.g. the invoice date). Handles PLN as either side, cross-rates via PLN, Table A/B fallback for exotic currencies.

## Data source

- Endpoint: [api.nbp.pl](https://api.nbp.pl/) — the National Bank of Poland's public REST API
- No API key, no registration, no rate limit
- Table A: daily mid-rate for major currencies (USD, EUR, GBP, CHF, JPY, …) — this is what accounting uses
- Table B: weekly (Wednesday) rates for less common currencies
- Table C: daily bid/ask spread — use for actual FX transactions, not bookkeeping

## Requirements

- Python 3.10+

## Setup

```bash
git clone https://github.com/bartosz-kuc/honest-nbp-mcp.git
cd honest-nbp-mcp
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

On Windows, use `venv\Scripts\pip` and `venv\Scripts\python` instead of `venv/bin/pip` and `venv/bin/python` (here and in the configs below).

Register with Claude Code:

```bash
claude mcp add nbp /absolute/path/to/venv/bin/python /absolute/path/to/server.py
```

Claude Desktop `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "nbp": {
      "command": "/absolute/path/to/venv/bin/python",
      "args": ["/absolute/path/to/server.py"]
    }
  }
}
```

## Example usage

> "What was the NBP mid-rate for USD on 2026-03-14?"

`get_rate(currency="USD", date="2026-03-14")` → mid rate + effective date. If NBP didn't publish on that day (weekend/holiday) the server returns a clear 404 payload so the caller knows to try the previous business day.

> "Convert a 1240.50 EUR invoice dated 2026-07-15 to PLN."

`convert(amount=1240.50, from_currency="EUR", before_date="2026-07-15")` → PLN amount, the rate used, and the effective date of the NBP table it came from — the last table published strictly before 2026-07-15 (looked up over the preceding 14 days).

`convert(..., rate_date="2026-07-15")` instead uses the table published on exactly that date; on weekends/holidays it returns an error payload (NBP has no table for that day). `rate_date` and `before_date` cannot be combined.

> "How has EUR/PLN moved this quarter?"

`get_rate_history(currency="EUR", start_date="2026-04-01", end_date="2026-06-30")` → full daily series.

## Data flow

```
Your AI client
     ↕  MCP stdio
This server (Python, on your machine)
     ↕  HTTPS
api.nbp.pl (National Bank of Poland)
```

No cloud middle. Nothing to log in to. No telemetry.

## For Polish accountants and JDG owners

The important bit for księgowość: **the NBP mid-rate you use to book a foreign-currency invoice in PLN is the rate published on the last business day before the invoice date** (art. 11a ust. 1–2 ustawy o PIT; the CIT act has the same rule). Not the invoice date itself. This is a common source of manual mistakes. `convert(..., before_date="YYYY-MM-DD")` with the invoice date does the look-back for you and returns the effective date of the table it used, so you can record it. `convert(..., rate_date="YYYY-MM-DD")` asks NBP for that exact date only — if NBP didn't publish that day, the response says so.

## Author

**Bartosz Kuć** — Warsaw-based developer, JDG owner running [skanfirmy.pl](https://skanfirmy.pl).

- GitHub: https://github.com/bartosz-kuc

- Email: firma@bartosza.pl

## Consulting

Available for consulting on Polish tax and business integrations (KSeF, GUS/NFZ/GIOŚ APIs, mBank data), MCP server design, and AI-assisted tooling for JDGs and small teams. See **[skanfirmy.pl/uslugi](https://skanfirmy.pl/uslugi)** for productized packages (audit 3k PLN, setup 8-15k PLN, retainer 2-4k PLN/mo), or reach out via email.

## License

MIT — see [LICENSE](LICENSE).

## Related

- [honest-gmail-mcp](https://github.com/bartosz-kuc/honest-gmail-mcp) — local Gmail MCP
- [honest-calendar-mcp](https://github.com/bartosz-kuc/honest-calendar-mcp) — local Google Calendar MCP
- [honest-drive-mcp](https://github.com/bartosz-kuc/honest-drive-mcp) — local Google Drive MCP with permission management
- [honest-nip-krs-mcp](https://github.com/bartosz-kuc/honest-nip-krs-mcp) — Polish company registry MCP (biała lista + KRS)
