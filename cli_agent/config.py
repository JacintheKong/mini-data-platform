"""Runtime configuration from environment variables."""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
MODEL = os.environ.get("CLI_AGENT_MODEL", "claude-sonnet-4-5")
WAREHOUSE_PATH = Path(
    os.environ.get("CLI_AGENT_WAREHOUSE", REPO_ROOT / "warehouse" / "data.duckdb")
).resolve()
DBT_MANIFEST_PATH = REPO_ROOT / "dbt_project" / "target" / "manifest.json"

MAX_ROWS = int(os.environ.get("CLI_AGENT_MAX_ROWS", "1000"))
MAX_ITERATIONS = int(os.environ.get("CLI_AGENT_MAX_ITERATIONS", "10"))
QUERY_TIMEOUT_SECONDS = int(os.environ.get("CLI_AGENT_QUERY_TIMEOUT", "30"))


def require_api_key() -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Export it before running: "
            "export ANTHROPIC_API_KEY=sk-ant-..."
        )
    return ANTHROPIC_API_KEY
