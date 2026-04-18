# CLI Data Agent — Design Spec

**Context:** Brief was "build a CLI agent to answer ad-hoc questions about the data (sales, product pairs, anomalies, customer metrics, etc.). Keep it generic, infer answers from the code/metadata, and include a README outlining your approach and next steps."

## 1. Goal & Scope

Build a terminal-based agent that answers natural-language questions about the mini-data-platform warehouse by dynamically introspecting the DuckDB schema and dbt metadata, then generating and executing read-only SQL.

**In scope:** single-shot and REPL CLI modes; read-only querying of `marts`, `staging`, `raw` schemas; generated SQL and results available via `--verbose`; session-scoped conversation for follow-ups; usage documented in the root README.

**Out of scope for v1:** write access, chart generation, streaming output, multi-warehouse support, authentication/multi-user, scheduled runs, a web UI, automated evaluation harness.

## 2. Non-Goals

- Not a replacement for dbt or Evidence. The agent queries the existing warehouse; it does not mutate models or materialize findings.
- Not a general SQL sandbox. Safety layers actively reject non-SELECT statements.
- Not a production service. Design targets a single local user running against a local DuckDB file.

## 3. Architecture

### 3.1 High-level loop

```
User question
    ↓
Claude Sonnet 4.5  ←───────────┐
    ↓ (tool_use blocks)        │ (tool_result blocks)
Tool dispatcher                │
    ↓                          │
[list_tables | describe_table ─┤
 sample_rows  | run_sql ]      │
    ↓ (final text)             │
Formatted answer + SQL + table ┘
```

Single tool-using agent. One Anthropic `messages.create` call per turn, iterated until `stop_reason == "end_turn"` or the 10-turn cap fires.

**Rationale for single agent (not multi-agent):** ad-hoc data questions decompose sequentially (schema → SQL → inspect → maybe refine). One agent's internal reasoning handles this without the latency/handoff cost of a planner/executor split. Multi-agent patterns (parallel anomaly investigation, scheduled watchers) are listed as future work.

**Rationale for tool-using agent (not text-to-SQL pipeline or semantic layer):** the brief says "infer answers from code/metadata" and lists diverse question types. A tool-using agent can self-correct on SQL errors, chain queries, and explore schema it doesn't know. A one-shot text-to-SQL pipeline can't recover from errors; a semantic layer breaks on questions outside its catalog.

### 3.2 Project layout

```
cli_agent/
  __init__.py
  __main__.py          # entry: `uv run python -m cli_agent [question]`
  cli.py               # arg parsing, REPL loop, single-shot dispatch
  agent.py             # core Claude loop (messages API, tool-use handling)
  tools.py             # the 4 tools + DuckDB/dbt-manifest access (read-only)
  rendering.py         # prose answer by default; --verbose adds SQL, table, and full trace
  config.py            # env vars: ANTHROPIC_API_KEY, model, warehouse path
  prompts.py           # system prompt (role, warehouse overview, quirks, tools)
tests/
  cli_agent/
    test_tools.py      # unit tests: each tool against a fixture DuckDB
    test_agent.py      # mocked Anthropic client; assert tool-call flow
    test_cli.py        # argparse + REPL basics
```

`cli_agent/` sits peer to existing `dbt_project/`, `evidence/`, `airflow/` — mirrors the repo's "each concern is a top-level directory" convention. Agent usage is documented directly in the root README so a reviewer only has one entry point to read.

## 4. Agent Loop

```python
def answer(question: str, history: list[Message]) -> AgentResult:
    messages = history + [{"role": "user", "content": question}]
    trace = []
    for _ in range(MAX_ITERATIONS):  # 10
        resp = anthropic.messages.create(
            model=MODEL, max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )
        trace.append(resp)
        if resp.stop_reason == "end_turn":
            return AgentResult(text=final_text(resp), trace=trace, messages=messages)
        tool_results = [dispatch(b) for b in resp.content if b.type == "tool_use"]
        messages.append({"role": "assistant", "content": resp.content})
        messages.append({"role": "user", "content": tool_results})
    # Exhausted iterations: surface partial trace with a warning
    return AgentResult(text="(agent did not converge in 10 turns)", trace=trace, messages=messages)
```

`messages` is returned so REPL mode can feed it back as history on the next question.

## 5. System Prompt

Located in `cli_agent/prompts.py`. Contents:

1. **Role:** "You are a data analyst with read-only access to a DuckDB warehouse."
2. **Warehouse overview:** three schemas (`raw`, `staging`, `marts`); prefer `marts`; brief description of `fct_orders`, `dim_customers`, `dim_products`.
3. **Data quirks:** lifted verbatim from `CLAUDE.md` (which was corrected as part of this design process). Covers:
   - Order-level columns (`subtotal`, `tax`, `shipping`, `discount`, `total`) repeat on every line item — dedupe by `transaction_id` before aggregating.
   - Order-status column is `status`; default revenue queries to `status = 'completed'`.
   - ~5% null `payment_method`, ~2% negative `total` (errors), ~3% duplicate `transaction_id`.
   - `line_margin` is miscomputed — compute margin manually.
   - Revenue is ambiguous between `subtotal`, `subtotal - discount`, and `total` — pick one and state it.
   - `fct_orders` uses LEFT JOINs — dim fields may be NULL.
4. **Tool guidance:** prefer `marts` over `raw`; call `describe_table` before querying unknown tables; use `sample_rows` to check enum-like values.
5. **Live warehouse facts (bootstrap):** injected at session start (see §7).
6. **Output contract:** "Respond with a concise prose summary of the answer. State which revenue definition you used if applicable. The renderer surfaces SQL and result tables separately — do not repeat them."

## 6. Tools

All tools implemented in `cli_agent/tools.py`. The DuckDB connection is opened `read_only=True` once at startup and shared.

| Tool | Signature | Behavior |
|---|---|---|
| `list_tables` | `() → str` | Query `information_schema.tables`; return markdown grouped by schema (excl. system schemas). |
| `describe_table` | `(name: str) → str` | Columns + types from `information_schema.columns`. If name matches a dbt model, append description and column docs from cached `manifest.json`. |
| `sample_rows` | `(table: str, n: int = 5) → str` | `SELECT * FROM table LIMIT n`, rendered as markdown. Hard cap `n ≤ 20`. |
| `run_sql` | `(query: str) → str` | Execute query with 30s wall-clock timeout, fetch up to 1000 rows, render as markdown + row count + `truncated` note if capped. Read-only enforcement comes from the DuckDB connection itself, not a SQL parser. |

Row cap and timeout are tunable via `MAX_ROWS` and `QUERY_TIMEOUT_SECONDS` env vars.

*Trimmed from original spec:* a separate `read_dbt_docs` tool (folded into `describe_table` — one tool call now returns columns, types, and dbt documentation together) and a sqlglot AST guard on `run_sql` (a `read_only=True` DuckDB connection already makes writes impossible, so the AST layer was redundant).

## 7. Safety Layers

Three independent layers:

1. **OS:** DuckDB connection opened `read_only=True`. `INSERT`/`UPDATE`/`DELETE`/`DROP`/`ATTACH` fail at the engine level — this alone is sufficient to block destructive writes.
2. **Resource:** 1000-row fetch cap, 30s wall-clock timeout (threading.Timer + `conn.interrupt()`), 10-turn agent-loop cap.
3. **Path:** warehouse path pinned in `config.py`; tools don't accept arbitrary paths.

**Known residual risk:** prompt injection inside a user question could steer the agent toward nonsense SQL, but read-only enforcement means the blast radius is "confusing query results," not data loss. Flagged in the root README as a v1 limitation.

## 8. Bootstrap Step

Runs once per agent session, before the first question:

```python
def bootstrap() -> str:
    min_d, max_d = CONN.execute(
        "SELECT MIN(transaction_date), MAX(transaction_date) FROM marts.fct_orders"
    ).fetchone()
    counts = CONN.execute(
        "SELECT 'fct_orders', COUNT(*) FROM marts.fct_orders UNION ALL "
        "SELECT 'dim_customers', COUNT(*) FROM marts.dim_customers UNION ALL "
        "SELECT 'dim_products',  COUNT(*) FROM marts.dim_products"
    ).fetchall()
    today = date.today().isoformat()
    return (
        f"Live warehouse facts: Data spans {min_d} to {max_d}. "
        f"Mart sizes: {dict(counts)}. "
        f"Today's wall-clock date is {today} — prefer the data's MAX(transaction_date) as 'now' for relative-date queries."
    )
```

Appended to `SYSTEM_PROMPT` at session start. Saves the agent from wasting tool calls on the two most common first questions ("how big?", "what date range?"), and — crucially — anchors relative-date queries to the data's actual end (2024-12-31), not wall-clock (2026-04-17).

## 9. CLI & REPL

Invocation via `python -m cli_agent`:

```bash
# Single-shot
uv run python -m cli_agent "How much revenue in Q4 2024?"

# REPL
uv run python -m cli_agent
> How much revenue in Q4 2024?
> break it down by customer segment     # reuses prior messages
> :exit
```

Flags:
- `--verbose` — show full tool-call trace (answer-mode C)
- `--model MODEL` — override default `claude-sonnet-4-5`
- `--max-rows N` — override 1000-row cap

REPL: `readline` for line history, `:exit` / `:quit` / `Ctrl-D` to leave, `:clear` to reset conversation. No autocomplete.

## 10. Output Rendering

Default mode: prose summary only. Keeps the common case readable for non-technical users; the prose always states the revenue definition, status filter, and any dedup so the answer is interpretable without reading SQL.

```
Q4 2024 completed-order revenue was $512,340 across 1,842 orders.
(Revenue defined as subtotal, status='completed', deduped by transaction_id.)
```

Verbose mode (`--verbose`): prose + the generated SQL, the result table, and a numbered trace of every tool call (tool name, arguments, result snippet). For reviewers and debugging.

Terminal formatting is plain ASCII (dropped the `rich` dependency — not worth the install footprint for a few markdown tables).

## 11. Configuration

Environment variables (all optional except the API key), read in `cli_agent/config.py`:

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(required)* | Anthropic API credential |
| `CLI_AGENT_MODEL` | `claude-sonnet-4-5` | Model name |
| `CLI_AGENT_WAREHOUSE` | `warehouse/data.duckdb` | DuckDB path, resolved relative to repo root |
| `CLI_AGENT_MAX_ROWS` | `1000` | run_sql row cap |
| `CLI_AGENT_MAX_ITERATIONS` | `10` | Agent-loop turn cap |
| `CLI_AGENT_QUERY_TIMEOUT` | `30` | run_sql wall-clock timeout in seconds |

## 12. Testing

Located in `tests/cli_agent/`.

- **`test_tools.py`** — fixture DuckDB built in `setUp` with ~3 tables and ~20 rows. Each tool tested against it: `list_tables` returns expected schemas; `run_sql` rejects `DROP TABLE` via the read-only connection; `run_sql` truncates at `MAX_ROWS`; `run_sql` honors the query timeout; `sample_rows` caps at 20 and rejects non-identifier names; `describe_table` merges `information_schema` + dbt manifest. No network.
- **`test_agent.py`** — Anthropic client mocked. Scripted responses: (1) agent emits a `tool_use` for `list_tables`, (2) receives tool_result, (3) emits `end_turn`. Assert the dispatcher wired tool_use → tool_result correctly and appended messages in order. Assert `MAX_ITERATIONS` cap fires with a clear result.
- **`test_cli.py`** — argparse edge cases (single-shot vs REPL, unknown flags), `--verbose` flag routing, `:clear` resets message history, `Ctrl-D` exits REPL cleanly.
- **No live-LLM integration tests in CI** — flaky and costly. A manual smoke script `cli_agent/examples.sh` runs five canned questions against the real API; the interviewer can run it.

## 13. README

Deliverable outline for the root README (content written during implementation — agent-specific sections added to the existing platform README rather than shipping a separate file):

1. **What it is** — one-paragraph pitch + screenshot of a real Q&A.
2. **Quick start** — `export ANTHROPIC_API_KEY=…`; `uv sync`; `uv run python -m cli_agent "your question"`.
3. **How it works** — tool-use loop, the 4 tools, safety layers, bootstrap facts. One architecture diagram.
4. **Design decisions** — why tool-using agent (not text-to-SQL), why Sonnet 4.5, why single agent.
5. **Handling the data quirks** — pointer to CLAUDE.md + table of "question type → how the agent handles it."
6. **Example transcripts** — 4–5 real question/answer pairs covering sales, product pairs, anomalies, customer metrics, trends.
7. **Next steps (if I had more time):**
   - Multi-agent for scheduled anomaly monitoring (watcher + diagnostic sub-agents).
   - Vector search over dbt docs for larger warehouses.
   - Eval harness: ground-truth SQL for ~20 questions → automated pass/fail.
   - Query-result caching across a REPL session.
   - Chart generation (e.g., via matplotlib or an MCP server).
   - Streaming output for long answers.
8. **Known limitations** — no streaming, prompt injection can steer SQL (read-only prevents damage but not confusion), single-warehouse assumption, no auth.

## 14. Success Criteria

- Five canned interview questions return correct, auditable answers on `warehouse/data.duckdb`.
- Read-only enforcement verified: `run_sql("DROP TABLE marts.fct_orders")` fails without affecting the DB.
- REPL follow-up ("break that down by X") reuses prior context without re-running the base query.
- All unit tests pass in CI without network access.
- The root README lets a reviewer go from `git clone` to working Q&A in under 5 minutes.
