"""Run the offline model-response evaluator using the project's imports."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / "backend"), str(root)]
from evals.model_replay import main

if __name__ == "__main__":
    raise SystemExit(main())
