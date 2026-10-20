"""Review local Token and cash budgets without preparing or executing a plan."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / "backend"), str(root)]
from evals.bailian_budget import main

if __name__ == "__main__":
    raise SystemExit(main())
