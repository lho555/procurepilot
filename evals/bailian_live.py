"""Prepare and execute one bounded, explicitly approved Bailian acceptance plan."""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from dotenv import dotenv_values

from app.model_explanation import ChatCompletionsAdapter, ModelFailure, ModelFailureKind
from evals.model_replay import ResponseRecord, Suite, evaluate_case
from scripts.verify_delivery import source_manifest

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
MODEL = "qwen3.7-flash"
CASE_IDS = ("shortage_and_delay", "shortage_only", "delay_only", "healthy_order")
INPUT_CAP, OUTPUT_CAP = 991808, 1034  # documented output cap may overshoot 1024 by 10
INPUT_RATE, OUTPUT_RATE = Decimal("1.2"), Decimal("4.8")
PER_CALL = (INPUT_CAP * INPUT_RATE + OUTPUT_CAP * OUTPUT_RATE) / 1_000_000
RESERVATION = PER_CALL * len(CASE_IDS)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_new(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as target:
        json.dump(value, target, ensure_ascii=False, indent=2)
        target.write("\n")
        target.flush()
        os.fsync(target.fileno())


def cases():
    raw = (ROOT / "evals/model_cases.json").read_bytes()
    suite = Suite.model_validate_json(raw)
    selected = [case for case in suite.cases if case.id in CASE_IDS]
    if tuple(case.id for case in selected) != CASE_IDS or any(case.expected.outcome != "accepted" for case in selected):
        raise ValueError("Live case contract changed")
    return selected, hashlib.sha256(raw).hexdigest()


def fingerprint():
    return source_manifest(ROOT)["sha256"]


def spec(budget):
    if not budget.is_finite() or not RESERVATION <= budget <= Decimal("12"):
        raise ValueError("Budget must cover the fixed reservation and cannot exceed 12 CNY")
    selected, case_hash = cases()
    adapter = ChatCompletionsAdapter(ENDPOINT, MODEL, "offline-placeholder", provider="bailian")
    return {"provider": "bailian", "endpoint": ENDPOINT, "model": MODEL, "enable_thinking": False,
            "approved_budget_cny": str(budget), "reserved_cny": str(RESERVATION), "per_request_reserved_cny": str(PER_CALL),
            "maximum_requests": len(CASE_IDS), "input_token_cap": INPUT_CAP, "output_token_reserve": OUTPUT_CAP,
            "input_cny_per_million": str(INPUT_RATE), "output_cny_per_million": str(OUTPUT_RATE),
            "case_sha256": case_hash, "source_sha256": fingerprint(),
            "requests": [{"case_id": case.id, "body_sha256": digest(adapter.request_body(*case.inputs()))} for case in selected],
            "references": ["https://help.aliyun.com/zh/model-studio/qwen3-7-flash",
                           "https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions"]}


def prepare(budget):
    specification = spec(budget)
    now = datetime.now(timezone.utc)
    plan = {"schema_version": 1, "created_at": now.isoformat(), "expires_at": (now + timedelta(hours=24)).isoformat(), "spec": specification}
    directory = ROOT / ".local/bailian-acceptance" / uuid4().hex
    directory.mkdir(parents=True)
    write_new(directory / "plan.json", plan)
    return directory / "plan.json", plan


def validate_plan(path):
    path = path.resolve()
    base = (ROOT / ".local/bailian-acceptance").resolve()
    if not path.is_relative_to(base) or path.name != "plan.json" or path.parent.parent != base:
        raise ValueError("Plan must be in a project-owned acceptance directory")
    if path.stat().st_size > 100_000:
        raise ValueError("Plan exceeds limit")
    plan = json.loads(path.read_text(encoding="utf-8"))
    if set(plan) != {"schema_version", "created_at", "expires_at", "spec"} or plan["schema_version"] != 1:
        raise ValueError("Invalid plan")
    created, expires = (datetime.fromisoformat(plan[key]) for key in ("created_at", "expires_at"))
    if not created <= datetime.now(timezone.utc) <= expires or expires - created != timedelta(hours=24):
        raise ValueError("Plan expired or invalid")
    if plan["spec"] != spec(Decimal(plan["spec"]["approved_budget_cny"])):
        raise ValueError("Plan, source or request fingerprint changed")
    return path, plan


def read_credential(path, name):
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", name):
        raise ValueError("Invalid credential name")
    with path.open("rb") as source:
        raw = source.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("Credential file exceeds limit")
    # Parse only the named assignment, without expanding other credentials.
    lines = [line for line in raw.decode("utf-8-sig").splitlines() if re.match(r"\s*(?:export\s+)?" + re.escape(name) + r"\s*=", line)]
    if len(lines) != 1:
        raise ValueError("Credential assignment missing or ambiguous")
    key = dotenv_values(stream=StringIO(lines[0]), interpolate=False).get(name)
    if not key or "${" in key or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ValueError("Credential must be a literal backend key")
    return key


def safe_failure(error, phase, case_id):
    """Closed fields only: never persist exception text, class names or bodies."""
    category, status = "local_processing", None
    if phase == "credential":
        category = "credential"
    elif phase in {"configure_adapter", "prepare_request", "model_request"}:
        if phase == "model_request" and isinstance(error, TimeoutError):
            category = "timeout"
        elif isinstance(error, ModelFailure) and type(error.kind) is ModelFailureKind:
            category = error.kind.value
            if (phase == "model_request" and error.kind == ModelFailureKind.PROVIDER_HTTP
                    and type(error.http_status) is int and 100 <= error.http_status <= 599):
                status = error.http_status
    return {"category": category, "phase": phase, "case_id": case_id, "http_status": status}


async def execute(path, key_loader, adapter_factory=ChatCompletionsAdapter):
    path, plan = validate_plan(path)
    directory, specification = path.parent, plan["spec"]
    # Exclusive, persistent whole-run reservation precedes credential access.
    # Never delete/reset this claim or infer that a timed-out request was free.
    write_new(directory / "claimed.json", {"plan_sha256": digest(plan), "reserved_cny": specification["reserved_cny"],
                                           "maximum_requests": 4, "claimed_at": datetime.now(timezone.utc).isoformat()})
    observations, estimates, attempts, failure = [], [], 0, None
    phase, case_id = "credential", None
    try:
        key = key_loader()
        phase = "configure_adapter"
        adapter = adapter_factory(ENDPOINT, MODEL, key, provider="bailian")
        phase = "prepare_request"
        for case, request in zip(cases()[0], specification["requests"], strict=True):
            phase, case_id = "prepare_request", case.id
            if digest(adapter.request_body(*case.inputs())) != request["body_sha256"]:
                raise ValueError("Request changed")
            phase = "reserve_request"
            write_new(directory / f"attempt-{case.id}.json", {"case_id": case.id, "body_sha256": request["body_sha256"],
                       "reserved_cny": str(PER_CALL), "state": "reserved_before_request"})
            attempts += 1
            phase = "model_request"
            result = await adapter.explain(*case.inputs())
            phase = "validate_result"
            # Reconstruct only verified selection fields. No raw response/error,
            # request headers, credential path or key enters persisted evidence.
            response = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(result["selection"])}}]}
            row = await evaluate_case(case, ResponseRecord(id=case.id, event="completion", response=response))
            usage = result["usage"]
            estimate = ((usage["prompt_tokens"] * INPUT_RATE + usage["completion_tokens"] * OUTPUT_RATE) / 1_000_000) if usage else None
            observation = {"case_id": case.id, "http_success": True, "request_elapsed_ms": result["request_elapsed_ms"],
                           "usage": usage, "cost_estimate_upper_cny": str(estimate) if estimate is not None else None,
                           "selection": result["selection"], "label_checks": row}
            phase = "persist_result"
            write_new(directory / f"result-{case.id}.json", observation)
            observations.append(observation)
            estimates.append(estimate)
    except Exception as error:
        # Missing credentials, provider failures and uncertain outcomes retain
        # the entire reservation. Never log the original exception or retry.
        failure = safe_failure(error, phase, case_id)
    complete = len(observations) == 4
    passed = complete and all(item["label_checks"]["passed"] and item["usage"] is not None for item in observations)
    report = {"schema_version": 1, "kind": "bailian_live_acceptance", "passed": passed,
              "status": "completed" if passed else "stopped" if failure else "completed_with_quality_or_usage_gaps",
              "failure": failure,
              "model": MODEL, "endpoint": ENDPOINT, "enable_thinking": False,
              "approved_budget_cny": specification["approved_budget_cny"], "reserved_cny": specification["reserved_cny"],
              "reservation_refunded": False, "planned_requests": 4, "request_attempts": attempts,
              "validated_responses": len(observations), "cost_estimate_upper_cny": str(sum(estimates, Decimal("0"))) if complete and all(v is not None for v in estimates) else None,
              "cost_scope": "Usage at highest documented token rates; not a provider invoice. Uncertain outcomes keep the full reservation.",
              "quality_scope": "Four fixed synthetic positive cases only; failure scenarios remain offline; not human-reviewed correctness or production acceptance.",
              "case_sha256": specification["case_sha256"], "source_sha256": specification["source_sha256"],
              "plan_sha256": digest(plan), "observations": observations, "finished_at": datetime.now(timezone.utc).isoformat()}
    write_new(directory / "live-report.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--execute", type=Path)
    parser.add_argument("--confirm-local", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--budget-cny", default="12")
    parser.add_argument("--credential-file", type=Path)
    parser.add_argument("--credential-name")
    args = parser.parse_args(argv)
    if args.prepare and not args.confirm_local:
        parser.error("--confirm-local is required to write a new offline plan")
    if args.execute and (not args.confirm_live or args.credential_file is None or not args.credential_name):
        parser.error("Live execution requires --confirm-live and an explicitly named credential file/assignment")
    try:
        if args.prepare:
            path, plan = prepare(Decimal(args.budget_cny))
            print(json.dumps({"plan": path.relative_to(ROOT).as_posix(), "spec": plan["spec"]}, ensure_ascii=False))
            return 0
        report = asyncio.run(execute(args.execute, lambda: read_credential(args.credential_file, args.credential_name)))
        print(json.dumps({key: report[key] for key in ("passed", "status", "failure", "request_attempts", "validated_responses", "reserved_cny", "cost_estimate_upper_cny")}))
        return 0 if report["passed"] else 1
    except Exception:
        parser.exit(2, "Acceptance refused: invalid/expired/changed/used plan, budget or local input. No automatic retry.\n")
