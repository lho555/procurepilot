"""Read-only review of operator-reported Token limits and full cash fallback."""
import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from evals import bailian_live as live


MAX_SNAPSHOT_AGE = timedelta(minutes=15)
TokenCount = Annotated[int, Field(ge=0, le=1_000_000_000_000)]


class QuotaSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Annotated[int, Field(ge=1, le=1)]
    provider: Literal["bailian"]
    model: Literal["qwen3.7-flash"]
    shared: bool
    observed_at: datetime | None
    expires_at: datetime | None
    remaining_tokens: TokenCount | None
    project_token_limit: TokenCount
    free_quota_only: bool | None

    @field_validator("observed_at", "expires_at")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("An explicit timezone is required")
        return value


def token_review(snapshot, required, now):
    """Report consistency checks only; a shared snapshot is not a reservation."""
    blockers = []
    if snapshot.observed_at is None:
        blockers.append("observation_time_unknown")
    elif snapshot.observed_at > now:
        blockers.append("observation_in_future")
    elif now - snapshot.observed_at > MAX_SNAPSHOT_AGE:
        blockers.append("snapshot_stale")
    if snapshot.expires_at is None:
        blockers.append("expiry_unknown")
    elif snapshot.expires_at <= now:
        blockers.append("quota_expired")
    if (snapshot.observed_at is not None and snapshot.expires_at is not None
            and snapshot.observed_at >= snapshot.expires_at):
        blockers.append("observation_not_before_expiry")
    if snapshot.remaining_tokens is None:
        blockers.append("remaining_tokens_unknown")
    elif snapshot.remaining_tokens < required:
        blockers.append("reported_balance_insufficient")
    if snapshot.project_token_limit < required:
        blockers.append("project_token_limit_exceeded")
    if snapshot.remaining_tokens is not None and snapshot.project_token_limit > snapshot.remaining_tokens:
        blockers.append("project_limit_above_reported_balance")
    return {"passed": not blockers, "blockers": blockers,
            "required_tokens": required, "reported_remaining_tokens": snapshot.remaining_tokens,
            "project_token_limit": snapshot.project_token_limit, "reported_shared": snapshot.shared,
            "observed_at": snapshot.observed_at.isoformat() if snapshot.observed_at is not None else None,
            "expires_at": snapshot.expires_at.isoformat() if snapshot.expires_at is not None else None,
            "maximum_snapshot_age_seconds": int(MAX_SNAPSHOT_AGE.total_seconds()),
            "reported_free_quota_only": snapshot.free_quota_only,
            "provider_state_verified": False, "shared_tokens_reserved": False}


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate snapshot field")
        result[key] = value
    return result


def review(path, budget=Decimal("12"), case_ids=None, *, now=None):
    path = live.ROOT / path
    # Only a bounded project-owned JSON snapshot; never read a credential file.
    if path.suffix != ".json" or not path.resolve().is_relative_to(live.ROOT.resolve()):
        raise ValueError("Snapshot must be a project-owned JSON file")
    raw = live.bounded_bytes(path, 16_000)
    json.loads(raw, object_pairs_hook=unique_fields)
    snapshot = QuotaSnapshot.model_validate_json(raw)
    now = datetime.now(timezone.utc) if now is None else now
    if now.utcoffset() is None:
        raise ValueError("Review time requires a timezone")
    specification = live.spec(budget, case_ids)
    cash = live.budget_review(specification)
    per_request = specification["input_token_cap"] + specification["output_token_reserve"]
    tokens = token_review(snapshot, per_request * specification["maximum_requests"], now)
    blockers = list(tokens["blockers"])
    if not cash["can_claim"]:
        blockers.append("cash_budget_exceeded")
    if os.path.lexists(live.ROOT / ".local/bailian-claim.lock"):
        blockers.append("claim_lock_present")
    return {"schema_version": 1, "mode": "offline_budget_review", "passed": not blockers,
            "blockers": blockers, "reviewed_at": now.isoformat(),
            "model": specification["model"], "source_sha256": specification["source_sha256"],
            "snapshot_sha256": hashlib.sha256(raw).hexdigest(),
            "selected_case_ids": [request["case_id"] for request in specification["requests"]],
            "maximum_requests": specification["maximum_requests"],
            "per_request_token_reserve": per_request, "token_review": tokens,
            "cash_fallback_review": cash, "free_token_cash_discount_cny": "0",
            "scope": "Operator-reported snapshot only, not provider verification, shared quota reservation, a bill or execution permission. Full cash fallback remains required regardless of the reported free-only setting.",
            "writes": False, "credential_reads": False, "live_model_requests": 0,
            "execution_authorized": False}


class ReviewParser(argparse.ArgumentParser):
    def error(self, message):
        # Unknown arguments may themselves contain a secret; never echo them.
        self.exit(2, "Budget review refused: invalid arguments.\n")


def main(argv=None):
    parser = ReviewParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--budget-cny", default="12")
    parser.add_argument("--case", action="append", dest="case_ids")
    args = parser.parse_args(argv)
    try:
        result = review(args.snapshot, Decimal(args.budget_cny), args.case_ids)
    except Exception:
        parser.exit(2, "Budget review refused: invalid snapshot, scope, budget, ledger or local input. No action taken.\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["passed"] else 1
