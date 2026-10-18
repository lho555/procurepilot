"""Verify policy retrieval and exact citation snapshots through a running API."""
import argparse
import hashlib
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    checks = []

    def call(path, body=None):
        request = Request(args.api.rstrip("/") + path,
                          data=json.dumps(body).encode() if body is not None else None,
                          headers={"Content-Type": "application/json"} if body is not None else {})
        with urlopen(request, timeout=15) as response:
            return json.load(response)

    try:
        for question, keyword in (
            ("紧急采购超过 20000 元需要谁审批？", "共同审批"),
            ("缺货与供应商延期应该如何核查？", "可用库存"),
            ("供应商准入需要核实哪些资质？", "营业资质"),
        ):
            answer = call("/knowledge/ask", {"question": question})
            if not answer["found"] or not any(keyword in citation["excerpt"] for citation in answer["citations"]):
                raise ValueError(f"Missing expected policy evidence: {keyword}")
            for citation in answer["citations"]:
                source = call(f"/knowledge/documents/{citation['document_id']}")
                source_text = "\n".join(source["content"].splitlines()[citation["start_line"] - 1:citation["end_line"]])
                if citation["excerpt"] not in source_text or hashlib.sha256(source["content"].encode()).hexdigest() != citation["source_sha256"]:
                    raise ValueError("Citation does not match the source snapshot")
            checks.append({"question": question, "status": "pass", "citations": len(answer["citations"])})
        unrelated = call("/knowledge/ask", {"question": "火星移民的氧气价格"})
        if unrelated["found"] or unrelated["citations"]:
            raise ValueError("Unrelated question must not fabricate citations")
        checks.append({"check": "no_evidence_abstention", "status": "pass"})
    except (HTTPError, URLError, OSError, ValueError, KeyError) as error:
        print(json.dumps({"stage": 2, "passed": False, "checks": checks, "error": str(error)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"stage": 2, "passed": True, "checks": checks}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
