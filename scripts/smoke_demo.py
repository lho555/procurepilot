"""Check running Stage 0 services, including the frontend's API proxy."""

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="http://127.0.0.1:8000")
    parser.add_argument("--frontend", default="http://127.0.0.1:5173")
    args = parser.parse_args()
    expected = {"status": "ok", "service": "procurepilot-api", "stage": 0}
    results = []
    for name, url, is_json in (
        ("backend_health", f"{args.backend.rstrip('/')}/health", True),
        ("frontend_page", f"{args.frontend.rstrip('/')}/", False),
        ("frontend_proxy", f"{args.frontend.rstrip('/')}/api/health", True),
    ):
        try:
            with urlopen(url, timeout=10) as response:
                body = response.read().decode("utf-8")
                if response.status != 200:
                    raise ValueError(f"HTTP {response.status}")
            if is_json:
                if json.loads(body) != expected:
                    raise ValueError("Unexpected health response")
            elif '<div id="root"></div>' not in body or "ProcurePilot AI" not in body:
                raise ValueError("Frontend entry page missing")
            results.append({"check": name, "url": url, "status": "pass"})
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
            results.append({"check": name, "url": url, "status": "fail", "error": str(error)})
    passed = all(result["status"] == "pass" for result in results)
    print(json.dumps({"stage": 0, "passed": passed, "checks": results}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
