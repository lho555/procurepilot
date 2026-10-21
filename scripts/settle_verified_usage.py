"""Explicitly reconcile matched provider usage at the highest verified rates."""
import argparse
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / "backend"), str(root)]
from evals import bailian_live as live


class UsageParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Usage settlement refused: invalid arguments.\n")


def main(argv=None):
    parser = UsageParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--confirm-known-usage", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_known_usage:
        parser.exit(2, "Explicit --confirm-known-usage is required. No settlement written.\n")
    try:
        value = live.settle_usage(args.plan)
    except Exception:
        parser.exit(2, "Usage settlement refused: invalid, changed, incomplete, used or busy evidence. No automatic retry.\n")
    print(json.dumps(value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
