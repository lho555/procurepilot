from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import shutil
import subprocess
import sys

import httpx
import pytest

from evals import bailian_budget as budget
from evals import bailian_live as live


NOW = datetime.now(timezone.utc)
PER_REQUEST = live.INPUT_CAP + live.OUTPUT_CAP


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    (tmp_path / "evals").mkdir()
    shutil.copyfile(live.ROOT / "evals/model_cases.json", tmp_path / "evals/model_cases.json")
    monkeypatch.setattr(live, "ROOT", tmp_path)
    monkeypatch.setattr(live, "fingerprint", lambda: "a" * 64)
    return tmp_path


def snapshot(root, **changes):
    data = {"schema_version": 1, "provider": "bailian", "model": live.MODEL,
            "shared": True, "observed_at": NOW.isoformat(), "expires_at": (NOW + timedelta(days=1)).isoformat(),
            "remaining_tokens": 4_000_000, "project_token_limit": 4_000_000, "free_quota_only": True, **changes}
    path = root / ".local/quota.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("count", [1, 2, 4])
def test_token_and_cash_limits_are_independent_and_review_has_no_side_effects(workspace, monkeypatch, count):
    required = PER_REQUEST * count
    path = snapshot(workspace, remaining_tokens=required, project_token_limit=required)
    def never(*args, **kwargs): pytest.fail("Review attempted credentials, a live call or a claim")
    for name in ["read_credential", "prepare", "reserve_plan", "execute"]:
        monkeypatch.setattr(live, name, never)
    monkeypatch.setattr(httpx, "AsyncClient", never)
    before = files(workspace)
    report = budget.review(path, case_ids=list(live.CASE_IDS[:count]), now=NOW)
    assert report["passed"] and report["blockers"] == []
    assert report["token_review"]["required_tokens"] == required
    assert report["cash_fallback_review"]["proposed_reserved_cny"] == str(live.PER_CALL * count)
    assert report["free_token_cash_discount_cny"] == "0"
    assert not report["execution_authorized"] and not report["writes"] and not report["credential_reads"]
    assert report["live_model_requests"] == 0
    assert not report["token_review"]["provider_state_verified"] and not report["token_review"]["shared_tokens_reserved"]
    assert str(workspace) not in json.dumps(report)
    assert files(workspace) == before


@pytest.mark.parametrize("changes,blocker", [
    ({"observed_at": None}, "observation_time_unknown"),
    ({"observed_at": (NOW + timedelta(seconds=1)).isoformat()}, "observation_in_future"),
    ({"observed_at": (NOW - timedelta(minutes=15, seconds=1)).isoformat()}, "snapshot_stale"),
    ({"expires_at": None}, "expiry_unknown"),
    ({"expires_at": NOW.isoformat()}, "quota_expired"),
    ({"observed_at": (NOW + timedelta(days=2)).isoformat()}, "observation_not_before_expiry"),
    ({"remaining_tokens": None}, "remaining_tokens_unknown"),
    ({"remaining_tokens": PER_REQUEST - 1}, "reported_balance_insufficient"),
    ({"project_token_limit": PER_REQUEST - 1}, "project_token_limit_exceeded"),
    ({"remaining_tokens": PER_REQUEST, "project_token_limit": PER_REQUEST + 1}, "project_limit_above_reported_balance"),
])
def test_unconfirmed_stale_expired_or_insufficient_quota_fails_read_only(workspace, changes, blocker):
    path = snapshot(workspace, **changes)
    before = files(workspace)
    report = budget.review(path, case_ids=["healthy_order"], now=NOW)
    assert not report["passed"] and blocker in report["token_review"]["blockers"]
    assert blocker in report["blockers"] and report["cash_fallback_review"]["can_claim"]
    assert files(workspace) == before


@pytest.mark.parametrize("changes", [
    {"schema_version": True}, {"model": "fixture-sensitive-model"}, {"shared": 1},
    {"remaining_tokens": -1}, {"remaining_tokens": 1.5}, {"project_token_limit": True},
    {"free_quota_only": "true"}, {"observed_at": "2026-10-20T00:00:00"},
    {"api_key": "fixture-sensitive-secret"},
])
def test_invalid_snapshot_cli_errors_never_echo_input(workspace, capsys, changes):
    path = snapshot(workspace, **changes)
    before = files(workspace)
    with pytest.raises(SystemExit) as caught:
        budget.main(["--snapshot", str(path), "--case", "healthy_order"])
    output = capsys.readouterr()
    assert caught.value.code == 2 and not output.out
    assert "fixture-sensitive" not in output.err and str(path) not in output.err
    assert files(workspace) == before


@pytest.mark.parametrize("raw", [b"fixture-sensitive-invalid-json", b"x" * 16_001,
    b'{"remaining_tokens":1000000,"remaining_tokens":0,"debug":"fixture-sensitive"}'])
def test_malformed_oversized_or_ambiguous_snapshot_is_not_echoed_or_changed(workspace, capsys, raw):
    path = snapshot(workspace)
    path.write_bytes(raw)
    before = files(workspace)
    with pytest.raises(SystemExit) as caught:
        budget.main(["--snapshot", str(path), "--case", "healthy_order"])
    output = capsys.readouterr()
    assert caught.value.code == 2 and not output.out and "fixture-sensitive" not in output.err
    assert files(workspace) == before


def test_snapshot_at_fifteen_minute_boundary_is_still_current(workspace):
    path = snapshot(workspace, observed_at=(NOW - timedelta(minutes=15)).isoformat())
    assert budget.review(path, case_ids=["healthy_order"], now=NOW)["passed"]


@pytest.mark.parametrize("setting", [True, False, None])
def test_reported_free_only_setting_never_discounts_cash_or_proves_provider_state(workspace, setting):
    path = snapshot(workspace, free_quota_only=setting)
    report = budget.review(path, case_ids=["healthy_order"], now=NOW)
    assert report["passed"]
    assert report["token_review"]["reported_free_quota_only"] is setting
    assert report["cash_fallback_review"]["aggregate_reserved_cny"] == str(live.PER_CALL)
    assert report["free_token_cash_discount_cny"] == "0" and not report["execution_authorized"]


def test_three_existing_claims_still_block_even_with_reported_free_quota(workspace):
    for selected in [None, None, ["shortage_only", "healthy_order"]]:
        plan, _ = live.prepare(Decimal("12"), selected)
        live.reserve_plan(plan)
    path = snapshot(workspace)
    before = files(workspace)
    report = budget.review(path, case_ids=["healthy_order"], now=NOW)
    assert report["token_review"]["passed"] and not report["passed"]
    assert report["blockers"] == ["cash_budget_exceeded"]
    assert report["cash_fallback_review"]["prior_reserved_cny"] == "11.9513280"
    assert report["cash_fallback_review"]["unreserved_before_claim_cny"] == "0.0486720"
    assert files(workspace) == before


def test_claim_guard_and_corrupt_history_are_preserved(workspace, capsys):
    path = snapshot(workspace)
    guard = workspace / ".local/bailian-claim.lock"
    guard.write_text('{"owner":"another-reviewer"}')
    before = files(workspace)
    report = budget.review(path, case_ids=["healthy_order"], now=NOW)
    assert not report["passed"] and report["blockers"] == ["claim_lock_present"]
    assert files(workspace) == before
    # A damaged foreign claim is not treated as zero spending.
    directory = workspace / ".local/bailian-acceptance" / ("a" * 32)
    directory.mkdir(parents=True)
    (directory / "claimed.json").write_text("fixture-sensitive-corrupt-claim")
    before = files(workspace)
    with pytest.raises(SystemExit) as caught:
        budget.main(["--snapshot", str(path), "--case", "healthy_order"])
    assert caught.value.code == 2 and "fixture-sensitive" not in capsys.readouterr().err
    assert files(workspace) == before


def test_paths_are_project_relative_and_non_json_or_external_files_are_never_opened(workspace, monkeypatch):
    path = snapshot(workspace)
    monkeypatch.chdir(workspace.parent)
    assert budget.review(path, now=NOW) == budget.review(path.relative_to(workspace), now=NOW)
    def never(*args, **kwargs): pytest.fail("Invalid path was opened")
    monkeypatch.setattr(live, "bounded_bytes", never)
    for invalid in [Path(".env"), workspace.parent / "outside.json"]:
        with pytest.raises(ValueError): budget.review(invalid, now=NOW)


def test_cli_exit_codes_and_no_execution_flags(workspace, capsys):
    path = snapshot(workspace)
    args = ["--snapshot", str(path), "--case", "healthy_order"]
    assert budget.main(args) == 0
    assert json.loads(capsys.readouterr().out)["passed"]
    snapshot(workspace, project_token_limit=0)
    assert budget.main(args) == 1
    assert "project_token_limit_exceeded" in json.loads(capsys.readouterr().out)["blockers"]
    for extra in [["--execute", "fixture-sensitive-argument"], ["--budget-cny", "12.01"], ["--case", "fixture-sensitive-case"]]:
        with pytest.raises(SystemExit) as caught: budget.main(args + extra)
        output = capsys.readouterr()
        assert caught.value.code == 2 and "fixture-sensitive" not in output.out + output.err


def test_cli_wrapper_safe_template_and_help_have_no_network_or_writes():
    root = Path(__file__).resolve().parents[2]
    command = [sys.executable, str(root / "scripts/review_bailian_budget.py")]
    result = subprocess.run(command + ["--snapshot", "profiles/bailian-quota.example.json", "--case", "healthy_order"],
                            cwd=root.parent, capture_output=True, text=True, timeout=20)
    assert result.returncode == 1 and not result.stderr
    report = json.loads(result.stdout)
    assert not report["passed"] and "remaining_tokens_unknown" in report["blockers"]
    assert not report["writes"] and not report["credential_reads"] and report["live_model_requests"] == 0
    help_result = subprocess.run(command + ["--help"], cwd=root, capture_output=True, text=True, timeout=20)
    assert help_result.returncode == 0 and "--snapshot" in help_result.stdout and "--execute" not in help_result.stdout
