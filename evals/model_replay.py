"""Offline response replay: contract checks and explicit synthetic answer labels."""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from time import monotonic
from typing import Annotated, Literal
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from app.model_explanation import ChatCompletionsAdapter, ModelFailure, Selection, explanation_context

ROOT = Path(__file__).resolve().parents[1]
METRICS = ("task_success", "grounded_facts", "citation_applicability", "recommendation_coverage", "failure_handling", "unexpected_acceptance")
NonNegative = Annotated[int, Field(ge=0, le=1_000_000)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Citation(StrictModel):
    number: Annotated[int, Field(ge=1, le=5)]
    excerpt: Annotated[str, Field(min_length=1, max_length=3000)]


class Expected(StrictModel):
    outcome: Literal["accepted", "rejected", "timed_out"]
    applicable_citations: Annotated[list[StrictInt], Field(max_length=5)] = []
    required_recommendations: Annotated[list[str], Field(max_length=6)] = []


class Case(StrictModel):
    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,49}$")]
    required: NonNegative
    available: NonNegative
    delay_days: NonNegative
    citations: Annotated[list[Citation], Field(max_length=5)]
    expected: Expected

    def inputs(self):
        shortage = max(self.required - self.available, 0)
        facts = {"order": {"id": "PO-2026-10086"}, "shortages": [{"sku": "SKU-MOTOR-01", "required": self.required, "available": self.available, "shortage": shortage}],
                 "delay_days": self.delay_days, "needs_review": shortage > 0 or self.delay_days > 0}
        return facts, {"found": bool(self.citations), "citations": [item.model_dump() for item in self.citations]}

    @model_validator(mode="after")
    def valid_labels(self):
        numbers = [item.number for item in self.citations]
        if len(numbers) != len(set(numbers)):
            raise ValueError("Duplicate citation labels")
        expected = self.expected
        if expected.outcome == "accepted":
            facts, policy = self.inputs()
            context = explanation_context(facts, policy)
            if (not expected.applicable_citations or not set(expected.applicable_citations) <= set(numbers)
                    or not expected.required_recommendations
                    or not set(expected.required_recommendations) <= context["allowed_recommendations"].keys()
                    or context["required_recommendation"] not in expected.required_recommendations):
                raise ValueError("Invalid acceptance labels")
        elif expected.applicable_citations or expected.required_recommendations:
            raise ValueError("Failure cases cannot carry acceptance labels")
        return self


class Suite(StrictModel):
    version: Literal[1]
    cases: Annotated[list[Case], Field(min_length=1, max_length=30)]


class ResponseRecord(StrictModel):
    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,49}$")]
    event: Literal["completion", "timeout", "http_error"]
    response: dict | None = None
    status: Annotated[int, Field(ge=400, le=599)] | None = None

    @model_validator(mode="after")
    def event_fields(self):
        if ((self.event == "completion") != (self.response is not None)
                or (self.event == "http_error") != (self.status is not None)):
            raise ValueError("Response event fields do not match")
        return self


class Responses(StrictModel):
    version: Literal[1]
    case_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    responses: Annotated[list[ResponseRecord], Field(min_length=1, max_length=30)]


def read_json(path: Path, schema):
    with path.open("rb") as source:
        raw = source.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("Input exceeds replay limit")
    return schema.model_validate_json(raw), hashlib.sha256(raw).hexdigest()


def load_inputs(response_path: Path, root: Path | None = None):
    root = root if root is not None else ROOT
    suite, case_hash = read_json(root / "evals/model_cases.json", Suite)
    records, response_hash = read_json(response_path, Responses)
    case_ids = [case.id for case in suite.cases]
    response_ids = [record.id for record in records.responses]
    if (records.case_sha256 != case_hash or len(case_ids) != len(set(case_ids))
            or len(response_ids) != len(set(response_ids)) or set(response_ids) != set(case_ids)):
        raise ValueError("Replay IDs or fixture hash mismatch")
    return suite, {record.id: record for record in records.responses}, case_hash, response_hash


async def evaluate_case(case: Case, record: ResponseRecord) -> dict:
    attempts, selection, error_type = 0, None, None
    async def respond(request):
        nonlocal attempts
        attempts += 1
        if record.event == "timeout":
            raise httpx.ReadTimeout("Synthetic replay timeout", request=request)
        if record.event == "http_error":
            return httpx.Response(record.status)
        return httpx.Response(200, json=record.response)
    # This is the only transport. No server configuration or credential is read,
    # and even imported records cannot choose a host or start a network request.
    adapter = ChatCompletionsAdapter("https://model-replay.invalid/v1/chat/completions", "fixture-replay", "fixture-only", transport=httpx.MockTransport(respond))
    started = monotonic()
    try:
        await adapter.summarize(*case.inputs())
        outcome = "accepted"
        selection = Selection.model_validate_json(record.response["choices"][0]["message"]["content"])
    except ModelFailure:
        outcome, error_type = "rejected", "ModelFailure"
    except TimeoutError:
        outcome, error_type = "timed_out", "TimeoutError"
    except Exception:
        outcome, error_type = "error", "ReplayError"
    expected = case.expected
    checks = {"outcome": outcome == expected.outcome, "request_budget": attempts == int(bool(case.citations))}
    metrics = dict.fromkeys(METRICS)
    if expected.outcome == "accepted":
        checks["grounded_facts"] = selection is not None
        checks["citation_applicability"] = bool(selection and set(selection.citation_numbers) <= set(expected.applicable_citations))
        checks["recommendation_coverage"] = bool(selection and set(expected.required_recommendations) <= set(selection.recommendations))
        for name in ("grounded_facts", "citation_applicability", "recommendation_coverage"):
            metrics[name] = checks[name]
    else:
        metrics["failure_handling"] = checks["outcome"]
        metrics["unexpected_acceptance"] = outcome == "accepted"
    passed = all(checks.values())
    metrics["task_success"] = passed
    return {"id": case.id, "passed": passed, "checks": checks, "metrics": metrics,
            "expected_outcome": expected.outcome, "actual_outcome": outcome, "error_type": error_type,
            "provider_attempts": attempts, "local_validation_ms": round((monotonic() - started) * 1000, 3),
            "selected_citations": selection.citation_numbers if selection else [],
            "selected_recommendations": selection.recommendations if selection else []}


def aggregate(rows):
    result = {}
    for name in METRICS:
        values = [row["metrics"][name] for row in rows if row["metrics"][name] is not None]
        count = sum(values)
        result[name] = {"numerator": count, "denominator": len(values), "value": count / len(values) if values else None}
    return result


async def evaluate(suite, records, case_hash, response_hash, mode):
    rows = [await evaluate_case(case, records[case.id]) for case in suite.cases]
    return {"schema_version": 1, "stage": 9, "adapter": "response_replay", "mode": mode,
            "scope": "Offline response replay; applicability follows fixed synthetic labels, not independent model-quality evidence.",
            "model_quality_claim": False, "live_model_requests": 0,
            "model_latency_ms": None, "model_cost": None,
            "case_sha256": case_hash, "responses_sha256": response_hash,
            "task_count": len(suite.cases), "evaluated_count": len(rows), "results": rows,
            "metrics": aggregate(rows), "passed": all(row["passed"] for row in rows),
            "finished_at": datetime.now(timezone.utc).isoformat()}


def markdown_report(report):
    lines = ["# Model response replay", "", f"Mode: {report['mode']} · Passed: {report['passed']} · {report['evaluated_count']}/{report['task_count']}",
             "", "Offline validation only. No live model requests. Model latency and cost: not measured.",
             "Applicability scores follow fixed synthetic labels; imported responses are not authenticated.", "",
             "| Metric | Numerator / denominator |", "| --- | --- |"]
    lines.extend(f"| {name} | {metric['numerator']} / {metric['denominator']} |" for name, metric in report["metrics"].items())
    lines += ["", "| Case | Result | Failed checks |", "| --- | --- | --- |"]
    lines.extend(f"| {row['id']} | {'PASS' if row['passed'] else 'FAIL'} | {', '.join(key for key, value in row['checks'].items() if not value) or '—'} |" for row in report["results"])
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="Approve a new local report directory; no network or business writes")
    parser.add_argument("--responses", type=Path, help="Replay local recorded responses matched to the bundled synthetic case hash")
    args = parser.parse_args(argv)
    if not args.confirm:
        parser.error("--confirm is required for local report writes")
    try:
        suite, records, case_hash, response_hash = load_inputs(args.responses if args.responses is not None else ROOT / "evals/model_responses.json")
    except (OSError, ValueError, ModelFailure):
        parser.exit(2, "Invalid replay input: check file size, schema, case IDs and fixture hash.\n")
    report = asyncio.run(evaluate(suite, records, case_hash, response_hash, "recorded_replay" if args.responses is not None else "fixture_replay"))
    directory = ROOT / ".local/model-evals" / uuid4().hex
    directory.mkdir(parents=True)
    report["report_path"] = (directory / "report.json").relative_to(ROOT).as_posix()
    (directory / "report.json").write_bytes((json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
    (directory / "report.md").write_bytes(markdown_report(report).encode())
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1
