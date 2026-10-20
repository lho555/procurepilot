import asyncio
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import shutil

import httpx
import pytest

from app.model_explanation import ChatCompletionsAdapter
from evals import bailian_live as live
from scripts.settle_unused_reservations import main


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    (tmp_path / "evals").mkdir()
    shutil.copyfile(live.ROOT / "evals/model_cases.json", tmp_path / "evals/model_cases.json")
    monkeypatch.setattr(live, "ROOT", tmp_path)
    monkeypatch.setattr(live, "fingerprint", lambda: "a" * 64)
    return tmp_path


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def stopped(count):
    path, _ = live.prepare(Decimal("12"))
    selected, _ = live.cases()
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == count:
            return httpx.Response(503, text="fixture-sensitive-provider-error")
        case = selected[len(calls) - 1]
        selection = {"finding_ids": ["stock:1", "delivery"], "citation_numbers": [1],
                     "recommendations": case.expected.required_recommendations}
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": json.dumps(selection)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30}})
    def key():
        if count == 0: raise ValueError("fixture-sensitive-credential")
        return "fixture-sensitive-key"
    factory = lambda *args, **kwargs: ChatCompletionsAdapter(*args, **kwargs, transport=httpx.MockTransport(handler))
    report = asyncio.run(live.execute(path, key, factory))
    assert report["request_attempts"] == count and report["status"] == "stopped"
    return path


@pytest.mark.parametrize("count", [0, 1, 2, 3])
def test_explicit_settlement_retains_every_attempt_upper_bound_and_original_record(workspace, count):
    path = stopped(count)
    before = files(workspace)
    assert live.claim_inventory()[0]["reserved_cny"] == "4.7805312"
    proof = live.closed_exposure(path)
    assert files(workspace) == before
    settlement = live.settle_unused(path)
    assert settlement["unused_request_slots"] == 4 - count
    assert Decimal(settlement["retained_reserved_cny"]) == live.PER_CALL * count
    row = live.claim_inventory()[0]
    assert row["original_reserved_cny"] == "4.7805312"
    assert row["reserved_cny"] == proof["retained_reserved_cny"]
    assert all((workspace / name).read_bytes() == raw for name, raw in before.items())
    assert set(files(workspace)) - set(before) == {str((path.parent / "unused-reservation.json").relative_to(workspace)).replace("\\", "/")}
    with pytest.raises(FileExistsError): live.settle_unused(path)
    with pytest.raises(FileExistsError): live.validate_plan(path)
    assert "fixture-sensitive" not in json.dumps(settlement)


@pytest.mark.parametrize("damage", ["missing_report", "missing_marker", "extra_marker", "wrong_plan_digest", "boolean_count", "not_stopped", "wrong_price"])
def test_incomplete_or_inconsistent_evidence_cannot_release_any_slot(workspace, damage):
    path = stopped(1)
    report_path = path.parent / "live-report.json"
    report = json.loads(report_path.read_text())
    if damage == "missing_report": report_path.unlink()
    elif damage == "missing_marker": (path.parent / "attempt-shortage_and_delay.json").unlink()
    elif damage == "extra_marker": (path.parent / "attempt-unknown.json").write_text("{}")
    elif damage == "wrong_price":
        plan = json.loads(path.read_text());plan["spec"]["input_cny_per_million"] = "0"
        path.write_text(json.dumps(plan))
        claim_path = path.parent / "claimed.json";claim = json.loads(claim_path.read_text())
        claim["plan_sha256"] = report["plan_sha256"] = live.digest(plan)
        claim_path.write_text(json.dumps(claim));report_path.write_text(json.dumps(report))
    else:
        if damage == "wrong_plan_digest": report["plan_sha256"] = "fixture-sensitive-digest"
        if damage == "boolean_count": report["request_attempts"] = True
        if damage == "not_stopped": report["status"] = "completed"
        report_path.write_text(json.dumps(report))
    before = files(workspace)
    with pytest.raises((ValueError, OSError)): live.settle_unused(path)
    assert files(workspace) == before and not (path.parent / "unused-reservation.json").exists()


@pytest.mark.parametrize("damage", ["report", "attempt", "claim", "retained_amount", "extra_field"])
def test_changed_settlement_evidence_blocks_new_claims_before_credentials(workspace, damage):
    path = stopped(1)
    live.settle_unused(path)
    candidate, _ = live.prepare(Decimal("12"))
    if damage in {"report", "attempt", "claim"}:
        name = {"report": "live-report.json", "attempt": "attempt-shortage_and_delay.json", "claim": "claimed.json"}[damage]
        target = path.parent / name
        target.write_bytes(target.read_bytes() + b" ")
    else:
        target = path.parent / "unused-reservation.json"
        value = json.loads(target.read_text())
        if damage == "retained_amount": value["retained_reserved_cny"] = "0"
        else: value["debug"] = "fixture-sensitive-extra"
        target.write_text(json.dumps(value))
    before = files(workspace)
    with pytest.raises(ValueError): asyncio.run(live.execute(candidate, lambda: pytest.fail("Credential access after evidence changed")))
    assert files(workspace) == before and not (candidate.parent / "claimed.json").exists()


def test_settlement_invalidates_prepared_budget_snapshot(workspace):
    path = stopped(1)
    candidate, _ = live.prepare(Decimal("12"))
    live.settle_unused(path)
    with pytest.raises(ValueError): asyncio.run(live.execute(candidate, lambda: pytest.fail("Stale plan reached credentials")))
    assert not (candidate.parent / "claimed.json").exists()
    assert live.preflight()["budget_review"]["aggregate_reserved_cny"] == str(live.PER_CALL * 5)


def test_only_one_concurrent_settlement_succeeds(workspace):
    path = stopped(1)
    def settle(_):
        try: return live.settle_unused(path)
        except FileExistsError: return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(settle, range(2)))
    assert sum(value is not None for value in results) == 1
    assert live.claim_inventory()[0]["reserved_cny"] == str(live.PER_CALL)


def test_partial_settlement_write_is_retained_and_fails_closed(workspace, monkeypatch):
    path = stopped(1)
    write = live.write_new
    def partial(target, value):
        if target.name == "unused-reservation.json":
            target.write_text("{")
            raise OSError("fixture-sensitive-disk")
        write(target, value)
    monkeypatch.setattr(live, "write_new", partial)
    with pytest.raises(OSError): live.settle_unused(path)
    assert (path.parent / "unused-reservation.json").read_text() == "{"
    with pytest.raises(ValueError): live.preflight()


def test_cli_requires_explicit_confirmation_and_never_echoes_sensitive_arguments(workspace, capsys):
    path = stopped(1)
    with pytest.raises(SystemExit) as caught: main(["--plan", str(path)])
    assert caught.value.code == 2 and not (path.parent / "unused-reservation.json").exists()
    capsys.readouterr()
    with pytest.raises(SystemExit): main(["--plan", str(path), "--unknown", "fixture-sensitive"])
    assert "fixture-sensitive" not in capsys.readouterr().err
    assert main(["--plan", str(path), "--confirm-unused"]) == 0
    assert json.loads(capsys.readouterr().out)["request_attempts"] == 1
