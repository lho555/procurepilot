"""Explicitly settle only unattempted slots in a stopped acceptance plan."""
import argparse
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / "backend"), str(root)]
from evals import bailian_live as live


class SettlementParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Settlement refused: invalid arguments.\n")


def main(argv=None):
    parser = SettlementParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--confirm-unused", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_unused:
        parser.exit(2, "Explicit --confirm-unused is required. No settlement written.\n")
    try:
        value = live.settle_unused(args.plan)
    except Exception:
        parser.exit(2, "Settlement refused: invalid, changed, incomplete, used or busy evidence. No automatic retry.\n")
    print(json.dumps(value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
