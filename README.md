# Mini Data Platform — CLI Data Agent

> **The original brief from Astronomer:**
>
> *You'll fork the [Mini Data Platform repo](https://github.com/astronomer/mini-data-platform) and build a CLI agent to answer ad-hoc questions about the data (sales, product pairs, anomalies, customer metrics, etc.). Keep it generic, infer answers from the code/metadata, and include a README outlining your approach and next steps if you had more time. This should take just a few hours.*

## What I built

A terminal agent that answers ad-hoc questions about the warehouse. Ask it anything — sales, product pairs, anomalies, customer metrics — and it explores the schema, writes SQL, and explains the result.

*Illustrative output (real numbers depend on the data seed):*

```
$ uv run python -m cli_agent "What was the total revenue in Q4 2024?"
In Q4 2024 there were 3,449 completed orders generating $2,476,968 in revenue
(using subtotal as the revenue definition — gross merchandise value before
tax and shipping).

SQL:
  WITH order_totals AS (
    SELECT DISTINCT transaction_id, subtotal, transaction_date, status
    FROM marts.fct_orders
    WHERE transaction_date >= '2024-10-01' AND transaction_date < '2025-01-01'
      AND status = 'completed'
  )
  SELECT ROUND(SUM(subtotal), 2) AS total_revenue,
         COUNT(DISTINCT transaction_id) AS completed_orders
  FROM order_totals;

Result:
  | total_revenue | completed_orders |
  |---|---|
  | 2476968       | 3449             |
```

## Quick start

**Prerequisite:** [install `uv`](https://docs.astral.sh/uv/getting-started/installation/). The setup script below uses `uv run`, which provisions the project venv on first call.

```bash
# 1. Build the warehouse (one-time, ~30 seconds)
./setup.sh

# 2. Export your Anthropic API key
export ANTHROPIC_API_KEY=sk-ant-...

# 3. Ask a question
uv run python -m cli_agent "your question"          # single-shot
uv run python -m cli_agent                          # REPL
uv run python -m cli_agent "question" --verbose     # show full tool-call trace
```

`./setup.sh` generates synthetic CSVs, runs the Airflow ingestion DAGs into DuckDB, and executes the dbt staging→marts transformations. After it finishes you have `warehouse/data.duckdb` ready for the agent to query.

## How it works

One tool-using Claude Sonnet 4.5 agent with four tools:

| Tool | What it does |
|---|---|
| `list_tables` | Enumerate schemas and tables |
| `describe_table` | Columns, types, and dbt documentation merged from `manifest.json` |
| `sample_rows` | Up to 20 example rows (handy for enum-like columns) |
| `run_sql` | Read-only SELECT against DuckDB |

At session start the agent runs a **bootstrap query** that fetches the data's date range and mart row counts. Those facts get embedded in the system prompt so relative-date questions ("last month") resolve against `MAX(transaction_date)`, not wall-clock — critical, because the synthetic data ends 2024-12-31 while today's date is well past that.

**Safety.** The DuckDB connection is opened `read_only=True` at startup. `INSERT`/`UPDATE`/`DELETE`/`DROP` and the like fail at the engine level regardless of what the LLM generates. Additional caps: 1000-row result fetch, 30-second query timeout, 10-turn agent loop.

## Design decisions

**Tool-using agent, not one-shot text-to-SQL.** The brief calls for diverse question types and inference from code/metadata. A tool-using agent introspects schema, recovers from SQL errors, and chains queries. A one-shot text-to-SQL pipeline can't.

**Single agent, not multi-agent.** Ad-hoc questions decompose sequentially (look at schema → write SQL → inspect result). Splitting into planner + executor adds latency without capability gain. Multi-agent makes sense for parallel investigations (see "Next steps").

**Four tools, not more.** I folded dbt-docs access into `describe_table` (one call instead of two) and dropped an AST-based SQL guard (read-only DuckDB is already bulletproof). Smaller tool surface → less confusion in the agent loop.

**Claude Sonnet 4.5.** Best-in-class tool use among current models. Configurable via `CLI_AGENT_MODEL` if you want to swap.

## Handling the data quirks

The synthetic data generator intentionally plants landmines. The system prompt teaches the agent to dodge them:

| Question type | What the agent does |
|---|---|
| Revenue totals | Filters `status='completed'`, dedupes line-items by `transaction_id`, uses `subtotal`, states the definition |
| Order counts | `COUNT(DISTINCT transaction_id)` — never raw row count |
| Data anomalies | Surfaces negative totals, null payment methods, duplicate transaction IDs |
| Margin analysis | Computes `(unit_price - product_cost) * quantity` manually — ignores the misnamed `line_margin` column |
| Relative dates | Anchors on `MAX(transaction_date)` (2024-12-31), not wall-clock |
| Segment analysis | Stays LEFT-JOIN-aware so NULL-segment rows aren't silently dropped |

## Examples and testing

Five canned questions covering the brief's categories:

```bash
./cli_agent/examples.sh 2>&1 | tee /tmp/smoke.log
```

Unit + smoke tests (no API key required):

```bash
uv run pytest tests/cli_agent/ -v
```

Ten TDD tests on `tools.py` (list/describe/sample/run_sql, read-only enforcement, row cap, timeout) plus three smoke tests on the agent loop (tool round-trip, no-tool-use early exit, max-iterations cap).

## Next steps if I had more time

- **Automated eval harness.** Pair ~20 questions with ground-truth SQL and assert the agent's answer matches within tolerance. Turns "does it work?" into a regression-testable metric.
- **Multi-agent for scheduled investigations.** A watcher finds anomalies nightly and dispatches a diagnostic sub-agent per anomaly. Productizes the anomaly category as a workflow.
- **Result caching across REPL session.** Cache `list_tables` / `describe_table` outputs within a session — saves turns on follow-ups like "now break that down by segment."
- **Chart generation.** Add a `make_chart` tool (matplotlib → PNG → terminal image via iTerm2 / Kitty protocols) for trend questions.
- **Vector-search over dbt docs.** Scales schema discovery to warehouses with thousands of tables. Overkill for 13 tables here; justified at 1,000+.
- **Streaming output.** Use the SDK's streaming API so long answers feel responsive.
- **Richer REPL.** Arrow-key history, tab-completion on table names, session transcript save (`:save session.md`).

## Known limitations

- **Prompt injection in the question text** can steer the agent toward odd SQL. Read-only enforcement means the blast radius is confusing results, not data loss — but a determined user could cost themselves API tokens.
- **Single-warehouse assumption.** Path is pinned by `CLI_AGENT_WAREHOUSE`; no multi-project mode.
- **No streaming.** Long answers print in one chunk after the whole turn completes.
- **No auth / multi-user.** Local CLI only.
- **No test coverage on the CLI and rendering layers.** Unit tests cover the tools and the agent loop. The manual smoke script exercises the full end-to-end.

---

## Platform reference

<details>
<summary>The underlying data platform — sources, ingestion, dbt models, dashboards</summary>

This repo is a synthetic data platform: mock CSV files → Airflow DAGs → DuckDB warehouse → dbt staging/marts → Evidence dashboards. The agent above queries the marts layer. Everything below is the original Astronomer-provided platform documentation, kept here as reference.

### Manual setup (instead of `./setup.sh`)

```bash
# 1. Install dependencies
uv sync

# 2. Generate synthetic data
uv run python scripts/generate_all.py

# 3. Initialize Airflow
cd airflow
# Update sql_alchemy_conn in airflow.cfg to an absolute path
export AIRFLOW_HOME=$(pwd)
uv run airflow db migrate

# 4. Run ingestion DAGs
uv run python dags/ingest_products.py
uv run python dags/ingest_users.py
uv run python dags/ingest_transactions.py
uv run python dags/ingest_campaigns.py
uv run python dags/ingest_pageviews.py

# 5. Run dbt transformations
uv run python dags/run_dbt.py
# or directly:
cd ../dbt_project && uv run dbt build --profiles-dir .
```

### Project structure

```
mini-data-platform/
├── cli_agent/           # CLI data agent (this project's contribution)
├── tests/cli_agent/     # Unit + smoke tests
├── sources/             # Raw source CSVs (postgres, salesforce, analytics)
├── airflow/dags/        # Ingestion + dbt orchestration DAGs
├── warehouse/           # DuckDB database (data.duckdb)
├── dbt_project/         # dbt staging + marts models
├── evidence/            # Evidence BI dashboards
└── scripts/             # Synthetic data generation
```

### Data layers

- **Raw** (`raw` schema): 5 tables loaded by Airflow — products, users, transactions, campaigns, pageviews. ~93K rows total.
- **Staging** (`staging` schema): 5 dbt views, lightly cleaned.
- **Marts** (`marts` schema): 3 denormalized tables — `dim_customers` (5,000 rows), `dim_products` (62 rows), `fct_orders` (35,980 rows).

### Evidence dashboards

```bash
cd evidence
npm install       # First time only
npm run sources   # Build data sources
npm run dev       # http://localhost:3000
```

Dashboards: Overview, Sales, Products, Customers. Connects to `../warehouse/data.duckdb` and queries the `marts` schema via pass-through SQL files in `evidence/sources/warehouse/`.

</details>
