import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import shutil

import httpx
import pytest

from app.model_explanation import ChatCompletionsAdapter
from evals import bailian_live as live
from scripts.settle_verified_usage import main


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    (tmp_path / "evals").mkdir()
    shutil.copyfile(live.ROOT / "evals/model_cases.json", tmp_path / "evals/model_cases.json")
    monkeypatch.setattr(live, "ROOT", tmp_path)
    monkeypatch.setattr(live, "fingerprint", lambda: "a" * 64)
    return tmp_path


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def execute_case_plan(count=4, fail_at=None, quality_gap=False):
    ids = live.CASE_IDS[:count]
    path, _ = live.prepare(Decimal("12"), ids)
    selected = live.cases(ids)[0]
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == fail_at:
            return httpx.Response(503, text="fixture-sensitive-provider")
        case = selected[len(calls) - 1]
        selection = {"finding_ids": ["stock:1", "delivery"], "citation_numbers": [1],
                     "recommendations": case.expected.required_recommendations}
        if quality_gap and case.id == "shortage_only":
            selection["citation_numbers"] = [1, 2]
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": json.dumps(selection)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30}})
    factory = lambda *args, **kwargs: ChatCompletionsAdapter(*args, **kwargs, transport=httpx.MockTransport(handler))
    report = asyncio.run(live.execute(path, lambda: "fixture-sensitive-key", factory))
    assert report["request_attempts"] == len(calls)
    return path, report


def edit_result_and_report(path, change):
    report_path = path.parent / "live-report.json"
    report = json.loads(report_path.read_text())
    observation = report["observations"][0]
    change(observation)
    (path.parent / f"result-{observation['case_id']}.json").write_text(json.dumps(observation))
    report_path.write_text(json.dumps(report))


@pytest.mark.parametrize("count,fail_at,unused", [(4, None, False), (4, 2, False), (4, 2, True), (2, None, False)])
def test_usage_reduces_only_verified_attempts_and_preserves_all_original_bytes(workspace, count, fail_at, unused):
    path, report = execute_case_plan(count, fail_at)
    if unused:
        live.settle_unused(path)
    prior = live.claim_inventory()[0]
    before = files(workspace)
    proof = live.usage_exposure(path, prior)
    assert files(workspace) == before
    settlement = live.settle_usage(path)
    cost = Decimal("0.000264")
    known = len(report["observations"])
    expected = Decimal(prior["reserved_cny"]) - known * (live.PER_CALL - cost)
    assert Decimal(settlement["retained_reserved_cny"]) == expected
    assert settlement["unknown_usage_attempts"] == report["request_attempts"] - known
    row = live.claim_inventory()[0]
    assert Decimal(row["reserved_cny"]) == expected and row["usage_settlement_sha256"]
    assert row["original_reserved_cny"] == str(live.PER_CALL * count)
    assert all((workspace / n).read_bytes() == raw for n, raw in before.items())
    assert set(files(workspace)) - set(before) == {f"{path.parent.relative_to(workspace).as_posix()}/usage-reservation.json"}
    with pytest.raises(FileExistsError): live.settle_usage(path)
    with pytest.raises(FileExistsError): live.validate_plan(path)
    assert "fixture-sensitive" not in json.dumps(proof)


def test_usage_is_independent_of_quality_score_and_legacy_selected_case_field(workspace):
    path, report = execute_case_plan(quality_gap=True)
    assert report["status"] == "completed_with_quality_or_usage_gaps"
    live.settle_usage(path)
    assert Decimal(live.claim_inventory()[0]["reserved_cny"]) == Decimal("0.001056")


def test_legacy_version_one_evidence_can_reconcile_without_rewriting_it(workspace):
    path, report = execute_case_plan(fail_at=2)
    plan = json.loads(path.read_text());plan["schema_version"] = 1
    path.write_text(json.dumps(plan))
    claim_path = path.parent / "claimed.json";claim = json.loads(claim_path.read_text())
    claim["plan_sha256"] = report["plan_sha256"] = live.digest(plan)
    report.pop("selected_case_ids")
    claim_path.write_text(json.dumps(claim));(path.parent / "live-report.json").write_text(json.dumps(report))
    before = files(workspace)
    live.settle_usage(path)
    assert all((workspace / n).read_bytes() == raw for n, raw in before.items())


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": True, "completion_tokens": 30},
    {"prompt_tokens": 100, "completion_tokens": 0}, {"prompt_tokens": 0, "completion_tokens": 30},
    {"prompt_tokens": 100, "completion_tokens": -1}, {"prompt_tokens": 100.0, "completion_tokens": 30},
    {"prompt_tokens": live.INPUT_CAP + 1, "completion_tokens": 30},
    {"prompt_tokens": 100, "completion_tokens": live.OUTPUT_CAP + 1},
    {"prompt_tokens": 100, "completion_tokens": 30, "private": "fixture-sensitive"}])
def test_invalid_or_zero_usage_keeps_full_bound(workspace, usage):
    path, _ = execute_case_plan(2)
    edit_result_and_report(path, lambda row: row.update(usage=usage))
    value = live.settle_usage(path)
    assert len(value["verified_usage"]) == 1 and value["unknown_usage_attempts"] == 1
    assert Decimal(value["retained_reserved_cny"]) == live.PER_CALL + Decimal("0.000264")


def test_no_verified_usage_cannot_create_settlement(workspace):
    path, _ = execute_case_plan(1, fail_at=1)
    before = files(workspace)
    with pytest.raises(ValueError): live.settle_usage(path)
    assert files(workspace) == before


@pytest.mark.parametrize("damage", ["missing_result", "extra_result", "wrong_result", "missing_marker", "extra_marker",
    "wrong_estimate", "report_digest", "source", "status", "count", "future", "price"])
def test_inconsistent_terminal_evidence_is_refused_before_any_record(workspace, damage):
    path, report = execute_case_plan(2)
    result = path.parent / "result-shortage_and_delay.json"
    if damage == "missing_result": result.unlink()
    elif damage == "extra_result": (path.parent / "result-unknown.json").write_text("{}")
    elif damage == "wrong_result": result.write_text("{}")
    elif damage == "missing_marker": (path.parent / "attempt-shortage_and_delay.json").unlink()
    elif damage == "extra_marker": (path.parent / "attempt-unknown.json").write_text("{}")
    elif damage == "wrong_estimate": edit_result_and_report(path, lambda row: row.update(cost_estimate_upper_cny="0"))
    else:
        if damage == "report_digest": report["plan_sha256"] = "fixture-sensitive"
        if damage == "source": report["source_sha256"] = "fixture-sensitive"
        if damage == "status": report["status"] = "running"
        if damage == "count": report["request_attempts"] = True
        if damage == "future": report["finished_at"] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        if damage == "price":
            plan = json.loads(path.read_text());plan["spec"]["input_cny_per_million"] = "0"
            path.write_text(json.dumps(plan));claim_path = path.parent / "claimed.json";claim = json.loads(claim_path.read_text())
            claim["plan_sha256"] = report["plan_sha256"] = live.digest(plan);claim_path.write_text(json.dumps(claim))
        (path.parent / "live-report.json").write_text(json.dumps(report))
    before = files(workspace)
    with pytest.raises((ValueError, OSError)): live.settle_usage(path)
    assert files(workspace) == before


@pytest.mark.parametrize("name", ["plan.json", "claimed.json", "live-report.json", "attempt-shortage_and_delay.json",
                                  "result-shortage_and_delay.json", "unused-reservation.json", "usage-reservation.json"])
def test_changed_proof_blocks_new_claim_before_credentials(workspace, name):
    path, _ = execute_case_plan(fail_at=2)
    live.settle_unused(path);live.settle_usage(path)
    candidate, _ = live.prepare(Decimal("12"), [live.CASE_IDS[0]])
    target = path.parent / name
    if name == "usage-reservation.json":
        value = json.loads(target.read_text());value["retained_reserved_cny"] = "0";target.write_text(json.dumps(value))
    else:
        target.write_bytes(target.read_bytes() + b" ")
    before = files(workspace)
    with pytest.raises(ValueError): asyncio.run(live.execute(candidate, lambda: pytest.fail("Credentials after proof changed")))
    assert files(workspace) == before and not (candidate.parent / "claimed.json").exists()


def test_usage_invalidates_old_prepared_budget_and_unused_cannot_follow_usage(workspace):
    path, _ = execute_case_plan(fail_at=2)
    candidate, _ = live.prepare(Decimal("12"), [live.CASE_IDS[0]])
    live.settle_usage(path)
    with pytest.raises(ValueError): live.validate_plan(candidate)
    before = files(workspace)
    with pytest.raises(ValueError): live.settle_unused(path)
    assert files(workspace) == before


def test_concurrent_usage_reconciliation_has_one_winner(workspace):
    path, _ = execute_case_plan(1)
    def settle(_):
        try: return live.settle_usage(path)
        except FileExistsError: return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        values = list(executor.map(settle, range(2)))
    assert sum(value is not None for value in values) == 1
    assert Decimal(live.claim_inventory()[0]["reserved_cny"]) == Decimal("0.000264")


def test_partial_usage_write_stays_and_fails_closed(workspace, monkeypatch):
    path, _ = execute_case_plan(1)
    write = live.write_new
    def partial(target, value):
        if target.name == "usage-reservation.json":
            target.write_text("{");raise OSError("fixture-sensitive-disk")
        write(target, value)
    monkeypatch.setattr(live, "write_new", partial)
    with pytest.raises(OSError): live.settle_usage(path)
    assert (path.parent / "usage-reservation.json").read_text() == "{"
    with pytest.raises(ValueError): live.preflight()


def test_cli_requires_confirmation_and_does_not_echo_sensitive_errors(workspace, capsys):
    path, _ = execute_case_plan(1)
    before = files(workspace)
    with pytest.raises(SystemExit) as caught: main(["--plan", str(path)])
    assert caught.value.code == 2 and files(workspace) == before
    capsys.readouterr()
    with pytest.raises(SystemExit): main(["--plan", "fixture-sensitive-path", "--confirm-known-usage"])
    assert "fixture-sensitive" not in capsys.readouterr().err
    with pytest.raises(SystemExit): main(["--plan", str(path), "--unknown", "fixture-sensitive-value"])
    assert "fixture-sensitive" not in capsys.readouterr().err
    assert main(["--plan", str(path), "--confirm-known-usage"]) == 0
    assert json.loads(capsys.readouterr().out)["request_attempts"] == 1
