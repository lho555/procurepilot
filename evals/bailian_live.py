"""Prepare and execute one bounded, explicitly approved Bailian acceptance plan."""
import argparse
import asyncio
from contextlib import contextmanager
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

from app.model_explanation import ChatCompletionsAdapter, ModelFailure, ModelFailureKind, OutputContractCode
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


def cases(case_ids=None):
    if case_ids is not None and type(case_ids) not in (list, tuple):
        raise ValueError("Select cases with an explicit list")
    requested = CASE_IDS if case_ids is None else tuple(case_ids)
    if (not requested or any(type(case_id) is not str or case_id not in CASE_IDS for case_id in requested)
            or len(requested) != len(set(requested))):
        raise ValueError("Select unique fixed positive case IDs")
    raw = (ROOT / "evals/model_cases.json").read_bytes()
    suite = Suite.model_validate_json(raw)
    selected = [case for case in suite.cases if case.id in CASE_IDS]
    if tuple(case.id for case in selected) != CASE_IDS or any(case.expected.outcome != "accepted" for case in selected):
        raise ValueError("Live case contract changed")
    return [case for case in selected if case.id in requested], hashlib.sha256(raw).hexdigest()


def fingerprint():
    return source_manifest(ROOT)["sha256"]


def acceptance_root():
    base = (ROOT / ".local/bailian-acceptance").resolve()
    if not base.is_relative_to(ROOT.resolve()):
        raise ValueError("Acceptance directory must remain in the project")
    return base


def bounded_bytes(path, limit):
    if path.is_symlink():
        raise ValueError("Linked acceptance records are not supported")
    with path.open("rb") as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Acceptance record exceeds limit")
    return raw


def claim_inventory(exclude=None):
    """Root-relative and absolute paths have identical, bounded ledger reads."""
    base = acceptance_root()
    excluded = (ROOT / exclude).resolve() if exclude is not None else None
    if excluded is not None and excluded.parent != base:
        raise ValueError("Excluded plan must belong to this project")
    rows = []
    for path in sorted(base.glob("*/claimed.json")):
        if path.parent.resolve() != path.parent or path.parent.is_symlink():
            raise ValueError("Linked claim directories are not supported")
        if path.parent.resolve() == excluded:
            continue
        if not re.fullmatch(r"[0-9a-f]{32}", path.parent.name):
            raise ValueError("Invalid claim directory")
        raw = bounded_bytes(path, 10_000)
        claim = json.loads(raw)
        if not isinstance(claim, dict) or set(claim) != {"plan_sha256", "reserved_cny", "maximum_requests", "claimed_at"}:
            raise ValueError("Invalid historical claim")
        if type(claim["reserved_cny"]) is not str:
            raise ValueError("Invalid historical reservation")
        amount = Decimal(claim["reserved_cny"])
        if not amount.is_finite() or not Decimal("0") < amount <= Decimal("12"):
            raise ValueError("Invalid historical reservation")
        historical = json.loads(bounded_bytes(path.parent / "plan.json", 100_000))
        if (digest(historical) != claim["plan_sha256"]
                or type(claim["maximum_requests"]) is not int or not 1 <= claim["maximum_requests"] <= len(CASE_IDS)
                or historical["spec"]["maximum_requests"] != claim["maximum_requests"]
                or len(historical["spec"]["requests"]) != claim["maximum_requests"]
                or historical["spec"]["reserved_cny"] != claim["reserved_cny"]):
            raise ValueError("Historical claim does not match its plan")
        row = {"plan_id": path.parent.name, "claim_sha256": hashlib.sha256(raw).hexdigest(),
               "reserved_cny": claim["reserved_cny"]}
        settlement_path = path.parent / "unused-reservation.json"
        if os.path.lexists(settlement_path):
            settlement_raw = bounded_bytes(settlement_path, 20_000)
            settlement = json.loads(settlement_raw)
            proof = closed_exposure(path.parent / "plan.json")
            if (set(settlement) != set(proof) | {"schema_version", "policy", "settled_at"}
                    or type(settlement["schema_version"]) is not int or settlement["schema_version"] != 1
                    or settlement["policy"] != "closed_unused_slots_v1"
                    or digest({key: settlement[key] for key in proof}) != digest(proof)):
                raise ValueError("Unused reservation settlement no longer matches evidence")
            settled = datetime.fromisoformat(settlement["settled_at"])
            if settled.utcoffset() is None or settled > datetime.now(timezone.utc):
                raise ValueError("Invalid settlement timestamp")
            row.update(original_reserved_cny=claim["reserved_cny"], reserved_cny=proof["retained_reserved_cny"],
                       settlement_sha256=hashlib.sha256(settlement_raw).hexdigest())
        usage_path = path.parent / "usage-reservation.json"
        if os.path.lexists(usage_path):
            usage_raw = bounded_bytes(usage_path, 30_000)
            settlement = json.loads(usage_raw)
            proof = usage_exposure(path.parent / "plan.json", row)
            if (set(settlement) != set(proof) | {"schema_version", "policy", "settled_at"}
                    or type(settlement["schema_version"]) is not int or settlement["schema_version"] != 1
                    or settlement["policy"] != "known_usage_upper_bound_v1"
                    or digest({key: settlement[key] for key in proof}) != digest(proof)):
                raise ValueError("Usage settlement no longer matches evidence")
            settled = datetime.fromisoformat(settlement["settled_at"])
            if settled.utcoffset() is None or settled > datetime.now(timezone.utc):
                raise ValueError("Invalid usage settlement timestamp")
            row.update(original_reserved_cny=claim["reserved_cny"], reserved_cny=proof["retained_reserved_cny"],
                       usage_settlement_sha256=hashlib.sha256(usage_raw).hexdigest())
        rows.append(row)
    return rows


def closed_exposure(path):
    """Bound every attempted request; unused slots require terminal evidence."""
    path = (ROOT / path).resolve()
    if (path.name != "plan.json" or path.parent.parent != acceptance_root()
            or not re.fullmatch(r"[0-9a-f]{32}", path.parent.name)):
        raise ValueError("Invalid closed-plan path")
    plan = json.loads(bounded_bytes(path, 100_000))
    claim_raw = bounded_bytes(path.parent / "claimed.json", 10_000)
    report_raw = bounded_bytes(path.parent / "live-report.json", 150_000)
    claim, report = json.loads(claim_raw), json.loads(report_raw)
    specification, requests = plan["spec"], plan["spec"]["requests"]
    maximum, count = claim["maximum_requests"], report["request_attempts"]
    if (type(maximum) is not int or not 1 <= maximum <= len(CASE_IDS)
            or type(count) is not int or not 0 <= count < maximum
            or len(requests) != maximum or specification["maximum_requests"] != maximum
            or claim["plan_sha256"] != digest(plan) or report["plan_sha256"] != digest(plan)
            or report["kind"] != "bailian_live_acceptance" or report["status"] != "stopped"
            or report["passed"] is not False or report["planned_requests"] != maximum
            or report["reserved_cny"] != claim["reserved_cny"]
            or specification["reserved_cny"] != str(PER_CALL * maximum)
            or claim["reserved_cny"] != specification["reserved_cny"]
            or specification["per_request_reserved_cny"] != str(PER_CALL)
            or specification["model"] != MODEL or specification["endpoint"] != ENDPOINT
            or specification["input_token_cap"] != INPUT_CAP or specification["output_token_reserve"] != OUTPUT_CAP
            or specification["input_cny_per_million"] != str(INPUT_RATE)
            or specification["output_cny_per_million"] != str(OUTPUT_RATE)
            or report["source_sha256"] != specification["source_sha256"]
            or report["case_sha256"] != specification["case_sha256"]):
        raise ValueError("Closed-plan evidence is inconsistent")
    ids = [request["case_id"] for request in requests]
    if len(set(ids)) != maximum or any(case_id not in CASE_IDS for case_id in ids):
        raise ValueError("Invalid closed-plan case IDs")
    finished = datetime.fromisoformat(report["finished_at"])
    if finished.utcoffset() is None or finished > datetime.now(timezone.utc):
        raise ValueError("Invalid terminal report timestamp")
    attempts = sorted(path.parent.glob("attempt-*.json"))
    if {p.name for p in attempts} != {f"attempt-{case_id}.json" for case_id in ids[:count]}:
        raise ValueError("Attempt markers do not match terminal report")
    proof_attempts = []
    for request in requests[:count]:
        raw = bounded_bytes(path.parent / f"attempt-{request['case_id']}.json", 10_000)
        if json.loads(raw) != {"case_id": request["case_id"], "body_sha256": request["body_sha256"],
                               "reserved_cny": str(PER_CALL), "state": "reserved_before_request"}:
            raise ValueError("Attempt marker does not match the original request")
        proof_attempts.append({"case_id": request["case_id"], "sha256": hashlib.sha256(raw).hexdigest()})
    return {"plan_sha256": digest(plan), "claim_sha256": hashlib.sha256(claim_raw).hexdigest(),
            "report_sha256": hashlib.sha256(report_raw).hexdigest(), "attempts": proof_attempts,
            "maximum_requests": maximum, "request_attempts": count, "unused_request_slots": maximum - count,
            "original_reserved_cny": claim["reserved_cny"], "retained_reserved_cny": format(PER_CALL * count, "f")}


def settle_unused(path):
    """Explicit, append-only settlement; claims remain permanently consumed."""
    with claim_guard():
        claim_inventory()  # Refuse corrupt history before adding any record.
        if os.path.lexists((ROOT / path).resolve().parent / "usage-reservation.json"):
            raise ValueError("Unused slots must be settled before usage")
        proof = closed_exposure(path)
        record = (ROOT / path).resolve().parent / "unused-reservation.json"
        if os.path.lexists(record):
            raise FileExistsError("Unused slots were already settled")
        value = {"schema_version": 1, "policy": "closed_unused_slots_v1",
                 "settled_at": datetime.now(timezone.utc).isoformat(), **proof}
        write_new(record, value)
    return value


def usage_exposure(path, prior):
    """Reconcile only matched provider usage; all other slots keep full bounds."""
    path = (ROOT / path).resolve()
    if (path.name != "plan.json" or path.parent.parent != acceptance_root()
            or not re.fullmatch(r"[0-9a-f]{32}", path.parent.name) or prior["plan_id"] != path.parent.name):
        raise ValueError("Invalid usage-settlement path")
    plan_raw = bounded_bytes(path, 100_000)
    claim_raw = bounded_bytes(path.parent / "claimed.json", 10_000)
    report_raw = bounded_bytes(path.parent / "live-report.json", 150_000)
    plan, claim, report = map(json.loads, (plan_raw, claim_raw, report_raw))
    specification, requests = plan["spec"], plan["spec"]["requests"]
    maximum, count = claim["maximum_requests"], report["request_attempts"]
    if (type(maximum) is not int or not 1 <= maximum <= len(CASE_IDS)
            or type(count) is not int or not 0 <= count <= maximum
            or len(requests) != maximum or specification["maximum_requests"] != maximum
            or claim["plan_sha256"] != digest(plan) or report["plan_sha256"] != digest(plan)
            or prior["claim_sha256"] != hashlib.sha256(claim_raw).hexdigest()
            or report["kind"] != "bailian_live_acceptance" or report["planned_requests"] != maximum
            or report["reserved_cny"] != claim["reserved_cny"]
            or claim["reserved_cny"] != specification["reserved_cny"]
            or specification["reserved_cny"] != str(PER_CALL * maximum)
            or specification["per_request_reserved_cny"] != str(PER_CALL)
            or report["model"] != specification["model"] or specification["model"] != MODEL
            or report["endpoint"] != specification["endpoint"] or specification["endpoint"] != ENDPOINT
            or report["enable_thinking"] is not False or specification["enable_thinking"] is not False
            or specification["input_token_cap"] != INPUT_CAP or specification["output_token_reserve"] != OUTPUT_CAP
            or specification["input_cny_per_million"] != str(INPUT_RATE)
            or specification["output_cny_per_million"] != str(OUTPUT_RATE)
            or report["source_sha256"] != specification["source_sha256"]
            or report["case_sha256"] != specification["case_sha256"]):
        raise ValueError("Usage evidence does not match the original plan")
    ids = [request["case_id"] for request in requests]
    observations = report["observations"]
    if (len(set(ids)) != maximum or any(case_id not in CASE_IDS for case_id in ids)
            or not isinstance(observations, list) or len(observations) > count
            or type(report["validated_responses"]) is not int or report["validated_responses"] != len(observations)
            or [item["case_id"] for item in observations] != ids[:len(observations)]
            or report.get("selected_case_ids", ids if plan["schema_version"] == 1 else None) != ids):
        raise ValueError("Invalid usage observation sequence")
    status = report["status"]
    if (status not in {"stopped", "completed", "completed_with_quality_or_usage_gaps"}
            or type(report["passed"]) is not bool or report["passed"] != (status == "completed")
            or (status != "stopped" and (count != maximum or len(observations) != maximum or report.get("failure")))):
        raise ValueError("Usage settlement requires terminal evidence")
    finished = datetime.fromisoformat(report["finished_at"])
    if finished.utcoffset() is None or finished > datetime.now(timezone.utc):
        raise ValueError("Invalid usage report timestamp")
    if ({p.name for p in path.parent.glob("attempt-*.json")} != {f"attempt-{case_id}.json" for case_id in ids[:count]}
            or {p.name for p in path.parent.glob("result-*.json")} != {f"result-{item['case_id']}.json" for item in observations}):
        raise ValueError("Usage marker/result inventory is inconsistent")
    attempts, results, verified, reduction = [], [], [], Decimal("0")
    for request in requests[:count]:
        raw = bounded_bytes(path.parent / f"attempt-{request['case_id']}.json", 10_000)
        if json.loads(raw) != {"case_id": request["case_id"], "body_sha256": request["body_sha256"],
                               "reserved_cny": str(PER_CALL), "state": "reserved_before_request"}:
            raise ValueError("Usage attempt does not match the original request")
        attempts.append({"case_id": request["case_id"], "sha256": hashlib.sha256(raw).hexdigest()})
    for observation in observations:
        raw = bounded_bytes(path.parent / f"result-{observation['case_id']}.json", 50_000)
        if digest(json.loads(raw)) != digest(observation) or observation["http_success"] is not True:
            raise ValueError("Usage result does not match the terminal report")
        results.append({"case_id": observation["case_id"], "sha256": hashlib.sha256(raw).hexdigest()})
        usage = observation["usage"]
        if (not isinstance(usage, dict) or set(usage) != {"prompt_tokens", "completion_tokens"}
                or type(usage["prompt_tokens"]) is not int or not 0 < usage["prompt_tokens"] <= INPUT_CAP
                or type(usage["completion_tokens"]) is not int or not 0 < usage["completion_tokens"] <= OUTPUT_CAP):
            continue  # Unknown/invalid/zero usage never creates a discount.
        bound = (usage["prompt_tokens"] * INPUT_RATE + usage["completion_tokens"] * OUTPUT_RATE) / 1_000_000
        if observation["cost_estimate_upper_cny"] != str(bound):
            raise ValueError("Usage cost does not match the highest-rate bound")
        verified.append({"case_id": observation["case_id"], "usage": usage, "retained_upper_cny": str(bound)})
        reduction += PER_CALL - bound
    if not verified:
        raise ValueError("No verified positive usage to reconcile")
    retained = Decimal(prior["reserved_cny"]) - reduction
    if not Decimal("0") < retained <= Decimal(prior["reserved_cny"]):
        raise ValueError("Invalid usage reduction")
    return {"plan_sha256": hashlib.sha256(plan_raw).hexdigest(), "claim_sha256": hashlib.sha256(claim_raw).hexdigest(),
            "report_sha256": hashlib.sha256(report_raw).hexdigest(), "attempts": attempts, "results": results,
            "verified_usage": verified, "maximum_requests": maximum, "request_attempts": count,
            "unknown_usage_attempts": count - len(verified), "prior_reserved_cny": prior["reserved_cny"],
            "unused_settlement_sha256": prior.get("settlement_sha256"), "retained_reserved_cny": str(retained)}


def settle_usage(path):
    """Explicit append-only reconciliation; no claims or original results change."""
    path = (ROOT / path).resolve()
    with claim_guard():
        inventory = claim_inventory()
        record = path.parent / "usage-reservation.json"
        if os.path.lexists(record):
            raise FileExistsError("Usage was already reconciled")
        prior = next((row for row in inventory if row["plan_id"] == path.parent.name), None)
        if prior is None:
            raise ValueError("Usage settlement requires an existing claim")
        proof = usage_exposure(path, prior)
        value = {"schema_version": 1, "policy": "known_usage_upper_bound_v1",
                 "settled_at": datetime.now(timezone.utc).isoformat(), **proof}
        write_new(record, value)
    return value


def budget_review(specification):
    claims = claim_inventory()
    prior = sum((Decimal(row["reserved_cny"]) for row in claims), Decimal("0"))
    cap, proposed = (Decimal(specification[key]) for key in ("approved_budget_cny", "reserved_cny"))
    return {"prior_claims": claims, "prior_reserved_cny": str(prior), "proposed_reserved_cny": str(proposed),
            "aggregate_reserved_cny": str(prior + proposed), "total_cap_cny": str(cap),
            "unreserved_before_claim_cny": str(cap - prior), "can_claim": prior + proposed <= cap}


def spec(budget, case_ids=None):
    selected, case_hash = cases(case_ids)
    reservation = PER_CALL * len(selected)
    if not budget.is_finite() or not reservation <= budget <= Decimal("12"):
        raise ValueError("Budget must cover selected cases and cannot exceed 12 CNY")
    adapter = ChatCompletionsAdapter(ENDPOINT, MODEL, "offline-placeholder", provider="bailian")
    return {"provider": "bailian", "endpoint": ENDPOINT, "model": MODEL, "enable_thinking": False,
            "approved_budget_cny": str(budget), "reserved_cny": str(reservation), "per_request_reserved_cny": str(PER_CALL),
            "maximum_requests": len(selected), "input_token_cap": INPUT_CAP, "output_token_reserve": OUTPUT_CAP,
            "input_cny_per_million": str(INPUT_RATE), "output_cny_per_million": str(OUTPUT_RATE),
            "case_sha256": case_hash, "source_sha256": fingerprint(),
            "requests": [{"case_id": case.id, "body_sha256": digest(adapter.request_body(*case.inputs()))} for case in selected],
            "references": ["https://help.aliyun.com/zh/model-studio/qwen3-7-flash",
                           "https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions"]}


def prepare(budget, case_ids=None):
    specification = spec(budget, case_ids)
    review = budget_review(specification)
    if not review["can_claim"] or os.path.lexists(ROOT / ".local/bailian-claim.lock"):
        raise ValueError("Budget or claim guard prevents a new plan")
    now = datetime.now(timezone.utc)
    plan = {"schema_version": 2, "created_at": now.isoformat(), "expires_at": (now + timedelta(hours=24)).isoformat(),
            "spec": specification, "budget_review": review}
    directory = ROOT / ".local/bailian-acceptance" / uuid4().hex
    directory.mkdir(parents=True)
    write_new(directory / "plan.json", plan)
    return directory / "plan.json", plan


def validate_plan(path):
    path = (ROOT / path).resolve()
    base = acceptance_root()
    if (not path.is_relative_to(base) or path.name != "plan.json" or path.parent.parent != base
            or not re.fullmatch(r"[0-9a-f]{32}", path.parent.name)):
        raise ValueError("Plan must be in a project-owned acceptance directory")
    if (path.parent / "claimed.json").exists():
        raise FileExistsError("Plan is already claimed")
    plan = json.loads(bounded_bytes(path, 100_000))
    if (set(plan) != {"schema_version", "created_at", "expires_at", "spec", "budget_review"}
            or type(plan["schema_version"]) is not int or plan["schema_version"] != 2):
        raise ValueError("Invalid plan")
    created, expires = (datetime.fromisoformat(plan[key]) for key in ("created_at", "expires_at"))
    if not created <= datetime.now(timezone.utc) <= expires or expires - created != timedelta(hours=24):
        raise ValueError("Plan expired or invalid")
    selected = [request["case_id"] for request in plan["spec"]["requests"]]
    if digest(plan["spec"]) != digest(spec(Decimal(plan["spec"]["approved_budget_cny"]), selected)):
        raise ValueError("Plan, source or request fingerprint changed")
    review = budget_review(plan["spec"])
    if not review["can_claim"] or digest(plan["budget_review"]) != digest(review):
        raise ValueError("Historical claims or cumulative budget changed")
    return path, plan


def preflight(path=None, budget=Decimal("12"), case_ids=None):
    """Read-only diagnostic; success is neither authorization nor a claim."""
    if path is None:
        specification = spec(budget, case_ids)
        review = budget_review(specification)
    else:
        if case_ids is not None:
            raise ValueError("A frozen plan cannot change cases")
        _, plan = validate_plan(path)
        specification, review = plan["spec"], plan["budget_review"]
    locked = os.path.lexists(ROOT / ".local/bailian-claim.lock")
    return {"mode": "offline_preflight", "passed": review["can_claim"] and not locked,
            "reason": "claim_lock_present" if locked else None if review["can_claim"] else "budget_exceeded",
            "source_sha256": specification["source_sha256"], "budget_review": review,
            "selected_case_ids": [request["case_id"] for request in specification["requests"]],
            "maximum_requests": specification["maximum_requests"],
            "writes": False, "credential_reads": False, "live_model_requests": 0}


@contextmanager
def claim_guard():
    lock = ROOT / ".local/bailian-claim.lock"
    owner = {"owner": uuid4().hex}
    write_new(lock, owner)
    try:
        yield
    finally:
        # Never remove an unknown/stale owner's guard or any claim record.
        if lock.is_symlink() or json.loads(bounded_bytes(lock, 1000)) != owner:
            raise ValueError("Claim guard ownership changed")
        lock.unlink()


def reserve_plan(path):
    validate_plan(path)  # Fail without even creating a transient guard.
    with claim_guard():
        path, plan = validate_plan(path)  # Recheck under the project-wide guard.
        write_new(path.parent / "claimed.json", {"plan_sha256": digest(plan), "reserved_cny": plan["spec"]["reserved_cny"],
                                                "maximum_requests": plan["spec"]["maximum_requests"], "claimed_at": datetime.now(timezone.utc).isoformat()})
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
    category, status, contract_code = "local_processing", None, None
    if phase == "credential":
        category = "credential"
    elif phase in {"configure_adapter", "prepare_request", "model_request"}:
        if phase == "model_request" and isinstance(error, TimeoutError):
            category = "timeout"
        elif isinstance(error, ModelFailure) and type(error.kind) is ModelFailureKind:
            category = error.kind.value
            if (phase == "model_request" and error.kind is ModelFailureKind.OUTPUT_CONTRACT
                    and type(error.contract_code) is OutputContractCode):
                contract_code = error.contract_code.value
            if (phase == "model_request" and error.kind == ModelFailureKind.PROVIDER_HTTP
                    and type(error.http_status) is int and 100 <= error.http_status <= 599):
                status = error.http_status
    return {"category": category, "phase": phase, "case_id": case_id, "http_status": status, "contract_code": contract_code}


async def execute(path, key_loader, adapter_factory=ChatCompletionsAdapter):
    path, plan = reserve_plan(path)
    directory, specification = path.parent, plan["spec"]
    # Exclusive, persistent whole-run reservation precedes credential access.
    # Never delete/reset this claim or infer that a timed-out request was free.
    observations, estimates, attempts, failure = [], [], 0, None
    phase, case_id = "credential", None
    try:
        key = key_loader()
        phase = "configure_adapter"
        adapter = adapter_factory(ENDPOINT, MODEL, key, provider="bailian")
        phase = "prepare_request"
        selected = [request["case_id"] for request in specification["requests"]]
        for case, request in zip(cases(selected)[0], specification["requests"], strict=True):
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
    complete = len(observations) == specification["maximum_requests"]
    passed = complete and all(item["label_checks"]["passed"] and item["usage"] is not None for item in observations)
    report = {"schema_version": 1, "kind": "bailian_live_acceptance", "passed": passed,
              "status": "completed" if passed else "stopped" if failure else "completed_with_quality_or_usage_gaps",
              "failure": failure,
              "budget_review": plan["budget_review"],
              "model": MODEL, "endpoint": ENDPOINT, "enable_thinking": False,
              "approved_budget_cny": specification["approved_budget_cny"], "reserved_cny": specification["reserved_cny"],
              "reservation_refunded": False, "planned_requests": specification["maximum_requests"], "request_attempts": attempts,
              "selected_case_ids": [request["case_id"] for request in specification["requests"]],
              "validated_responses": len(observations), "cost_estimate_upper_cny": str(sum(estimates, Decimal("0"))) if complete and all(v is not None for v in estimates) else None,
              "cost_scope": "Usage at highest documented token rates; not a provider invoice. Uncertain outcomes keep the full reservation.",
              "quality_scope": "Explicitly selected fixed synthetic positive cases only; failure scenarios remain offline; not full-suite, human-reviewed correctness or production acceptance.",
              "case_sha256": specification["case_sha256"], "source_sha256": specification["source_sha256"],
              "plan_sha256": digest(plan), "observations": observations, "finished_at": datetime.now(timezone.utc).isoformat()}
    write_new(directory / "live-report.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--preflight", nargs="?", const="", metavar="PLAN", help="Read-only budget check, optionally for an unused plan")
    mode.add_argument("--execute", type=Path)
    parser.add_argument("--confirm-local", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--budget-cny", default="12")
    parser.add_argument("--case", action="append", dest="case_ids", help="Select a fixed positive case; repeat to select more. Default: all four")
    parser.add_argument("--credential-file", type=Path)
    parser.add_argument("--credential-name")
    args = parser.parse_args(argv)
    if args.case_ids is not None and (args.execute is not None or args.preflight):
        parser.error("--case is only allowed with --prepare or --preflight without a frozen plan")
    if args.prepare and not args.confirm_local:
        parser.error("--confirm-local is required to write a new offline plan")
    if args.execute and (not args.confirm_live or args.credential_file is None or not args.credential_name):
        parser.error("Live execution requires --confirm-live and an explicitly named credential file/assignment")
    try:
        if args.preflight is not None:
            report = preflight(Path(args.preflight) if args.preflight else None, Decimal(args.budget_cny), args.case_ids)
            print(json.dumps(report, ensure_ascii=False))
            return 0 if report["passed"] else 1
        if args.prepare:
            path, plan = prepare(Decimal(args.budget_cny), args.case_ids)
            print(json.dumps({"plan": path.relative_to(ROOT).as_posix(), "spec": plan["spec"], "budget_review": plan["budget_review"]}, ensure_ascii=False))
            return 0
        report = asyncio.run(execute(args.execute, lambda: read_credential(args.credential_file, args.credential_name)))
        print(json.dumps({key: report[key] for key in ("passed", "status", "failure", "selected_case_ids", "planned_requests", "request_attempts", "validated_responses", "reserved_cny", "cost_estimate_upper_cny")}))
        return 0 if report["passed"] else 1
    except Exception:
        parser.exit(2, "Acceptance refused: invalid/expired/changed/used plan, cumulative budget, busy guard or local input. No automatic retry.\n")
