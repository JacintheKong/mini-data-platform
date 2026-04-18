#!/usr/bin/env bash
# Manual smoke test — five canned questions across the brief's categories.
# Requires ANTHROPIC_API_KEY to be exported.
set -u

cd "$(dirname "$0")/.."

run() {
    echo "============================================================"
    echo "Q: $1"
    echo "------------------------------------------------------------"
    uv run python -m cli_agent "$1"
    echo
}

run "What was the total revenue in Q4 2024, and how many completed orders?"
run "Which two product categories are most often purchased together?"
run "Are there any obvious data anomalies I should know about?"
run "What's the average order value by customer segment?"
run "Show the monthly revenue trend for 2024."
