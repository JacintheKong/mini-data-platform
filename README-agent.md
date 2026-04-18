# CLI Data Agent

A terminal agent that answers ad-hoc questions about the mini-data-platform warehouse. Ask it anything — sales, product pairs, anomalies, customer metrics — and it explores the schema, writes SQL, and explains the result.

*Illustrative output (numbers depend on the data seed):*

```
$ uv run python -m cli_agent "Total revenue in Q4 2024?"
Q4 2024 completed-order revenue was $512,340 across 1,842 orders.
(Revenue = subtotal, status='completed', deduped by transaction_id.)

SQL:
  SELECT SUM(subtotal) AS revenue, COUNT(DISTINCT transaction_id) AS orders
  FROM (SELECT DISTINCT transaction_id, subtotal, transaction_date, status
        FROM marts.fct_orders)
  WHERE status='completed' AND transaction_date BETWEEN '2024-10-01' AND '2024-12-31';

Result:
  | revenue | orders |
  |---|---|
  | 512340  | 1842   |
```

## Quick start

```bash
uv sync
export ANTHROPIC_API_KEY=sk-ant-...
uv run python -m cli_agent "your question"          # single-shot
uv run python -m cli_agent                          # REPL
uv run python -m cli_agent "question" --verbose     # show tool-call trace
```

## How it works

One tool-using Claude Sonnet 4.5 agent with four tools:

| Tool | What it does |
|---|---|
| `list_tables` | Enumerate schemas + tables |
| `describe_table` | Columns, types, and dbt documentation |
| `sample_rows` | Up to 20 example rows |
| `run_sql` | Read-only SELECT against DuckDB |

At session start, the agent runs a **bootstrap query** that fetches the data's date range and mart row counts. These facts go into the system prompt so relative-date questions ("last month") resolve against `MAX(transaction_date)`, not wall-clock — critical because the synthetic data ends 2024-12-31.

**Safety:** the DuckDB connection is opened `read_only=True` at startup. `INSERT`/`UPDATE`/`DELETE`/`DROP` fail at the engine level regardless of what the LLM generates. Additional caps: 1000-row result fetch, 30s query timeout, 10-turn agent loop.

## Design decisions

**Tool-using agent, not one-shot text-to-SQL.** The brief says "keep it generic, infer from code/metadata" and asks for diverse question types. A tool-using agent can introspect schema, recover from SQL errors, and chain queries — a one-shot pipeline can't.

**Claude Sonnet 4.5 over alternatives.** Best-in-class tool use, matches the repo's MCP configuration. Model is configurable via `CLI_AGENT_MODEL`.

**Single agent, not multi-agent.** Ad-hoc questions decompose sequentially (schema → SQL → inspect). Splitting into planner+executor adds latency without capability gain. Multi-agent makes sense for parallel investigations (listed in next steps).

**Four tools, not more.** I folded dbt-docs access into `describe_table` (one tool call instead of two) and dropped an AST-based SQL guard (read-only DuckDB is already bulletproof). Smaller tool surface → less confusion in the agent loop.

## Handling the data quirks

The system prompt encodes the landmines the generator intentionally plants:

| Question type | What the agent does |
|---|---|
| Revenue totals | Filters `status='completed'`, dedupes by `transaction_id`, uses `subtotal`, states the definition |
| Order counts | `COUNT(DISTINCT transaction_id)` |
| Data anomalies | Surfaces negative totals, null payment methods, duplicate transaction IDs |
| Margin analysis | Computes `(unit_price - product_cost) * quantity` manually — ignores the misnamed `line_margin` column |
| Relative dates | Anchors on `MAX(transaction_date)` (2024-12-31), not wall-clock (2026-04-17) |
| Segment analysis | Uses LEFT-JOIN-aware filters so NULL-segment rows aren't silently dropped |

## Examples

See [`cli_agent/examples.sh`](cli_agent/examples.sh) for five canned questions across the brief's categories.

## Next steps if I had more time

- **Automated eval harness.** Pair ~20 questions with ground-truth SQL and assert the agent's answer matches within tolerance. Would turn "does it work?" into a regression-testable metric.
- **Multi-agent for scheduled investigations.** A watcher finds anomalies nightly and dispatches a diagnostic sub-agent per anomaly. Fits the task's anomaly category as a productized workflow.
- **Result caching across REPL session.** Cache tool results (especially `list_tables`, `describe_table`) within a session — saves turns on follow-ups like "now break that down by segment."
- **Chart generation.** Add a `make_chart` tool (matplotlib → PNG → terminal image via iTerm2 / Kitty protocols) for trend questions.
- **Vector-search over dbt docs.** Scales schema discovery for warehouses with thousands of tables. Overkill for 13 tables; justified at 1,000+.
- **Streaming output.** Use the Anthropic SDK's streaming API so long answers feel responsive.
- **Richer REPL.** Arrow-key history, tab-completion on table names, session transcript save (`:save session.md`).

## Known limitations

- **Prompt injection in the question text** can steer the agent toward odd SQL. Read-only enforcement means the blast radius is confusing results, not data loss — but a determined user could cost themselves API tokens.
- **Single-warehouse assumption.** Path is pinned by `CLI_AGENT_WAREHOUSE`; no multi-project mode.
- **No streaming.** Long answers print in one chunk after the whole turn completes.
- **No auth / multi-user.** Local CLI only.
- **No test coverage on the CLI and rendering layers.** Unit tests cover the tools (10 tests, strict TDD) and the agent dispatch loop (2 smoke tests). Manual smoke script exercises the full end-to-end.

## Testing

```bash
uv run pytest tests/cli_agent/ -v
```

Ten TDD tests on `tools.py` (list/describe/sample/run_sql, read-only enforcement, row cap, timeout) plus two smoke tests on the agent loop. No network required.
