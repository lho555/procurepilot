"""Run the Stage 7 evaluator with project and backend imports configured."""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "backend"))
from evals.runner import main

if __name__ == "__main__":
    raise SystemExit(main())
