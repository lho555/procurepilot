"""Explicitly prepare or run the one-use Bailian acceptance procedure."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / "backend"), str(root)]
from evals.bailian_live import main

if __name__ == "__main__":
    raise SystemExit(main())
