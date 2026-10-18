import asyncio
import copy
import json
import shutil

import httpx
import pytest

from evals import model_replay
from evals.model_replay import Case, ROOT, evaluate, load_inputs, main, markdown_report
from scripts.verify_delivery import read_model_evaluation_report


def inputs():
    return load_inputs(ROOT / "evals/model_responses.json")


def run(changes=None):
    suite, records, case_hash, response_hash = inputs()
    if changes:
        changes(records)
    return asyncio.run(evaluate(suite, records, case_hash, response_hash, "fixture_replay"))


def replace_selection(records, **changes):
    record = records["shortage_and_delay"]
    selection = json.loads(record.response["choices"][0]["message"]["content"])
    record.response["choices"][0]["message"]["content"] = json.dumps({**selection, **changes})


def test_fixture_replay_and_safe_denominators_without_network_or_credentials(monkeypatch):
    monkeypatch.setenv("PP_LLM_MODE", "chat_completions")
    monkeypatch.setenv("PP_LLM_ALLOW_SYNTHETIC_SEND", "true")
    monkeypatch.setenv("PP_LLM_API_KEY", "synthetic-credential-must-not-be-read")
    import os
    original = os.getenv
    def getenv(name, default=None):
        assert not name.startswith("PP_LLM_")
        return original(name, default)
    monkeypatch.setattr(os, "getenv", getenv)
    async def no_network(*args, **kwargs):
        raise AssertionError("Replay must never use network transport")
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)
    report = run()
    assert report["passed"] and report["task_count"] == 12
    assert report["metrics"]["grounded_facts"]["denominator"] == 4
    assert report["metrics"]["failure_handling"]["denominator"] == 8
    assert report["metrics"]["unexpected_acceptance"]["numerator"] == 0
    assert report["model_cost"] is None and report["model_latency_ms"] is None
    assert report["live_model_requests"] == 0 and report["model_quality_claim"] is False
    missing = next(row for row in report["results"] if row["id"] == "missing_policy")
    assert missing["provider_attempts"] == 0
    assert all(row["provider_attempts"] <= 1 for row in report["results"])


def test_existing_but_inapplicable_citation_is_a_quality_failure():
    report = run(lambda records: replace_selection(records, citation_numbers=[2]))
    row = report["results"][0]
    assert row["actual_outcome"] == "accepted" and row["checks"]["grounded_facts"]
    assert not row["passed"] and not row["checks"]["citation_applicability"]
    assert report["metrics"]["citation_applicability"]["value"] == 0.75
    assert not report["passed"]


def test_safe_but_incomplete_advice_does_not_receive_full_score():
    report = run(lambda records: replace_selection(records, recommendations=["HUMAN_REVIEW"]))
    assert report["results"][0]["actual_outcome"] == "accepted"
    assert not report["results"][0]["checks"]["recommendation_coverage"]
    assert report["metrics"]["recommendation_coverage"]["value"] == 0.75


def test_failed_applicable_case_stays_in_all_quality_denominators():
    def fail(records):
        records["shortage_and_delay"] = model_replay.ResponseRecord(id="shortage_and_delay", event="timeout")
    report = run(fail)
    for metric in ["grounded_facts", "citation_applicability", "recommendation_coverage"]:
        assert report["metrics"][metric]["denominator"] == 4
        assert report["metrics"][metric]["numerator"] == 3


def test_unexpected_acceptance_is_a_violation_not_success():
    def accept(records):
        records["unknown_citation"] = records["shortage_and_delay"].model_copy(update={"id": "unknown_citation"}, deep=True)
    report = run(accept)
    assert not report["passed"]
    assert report["metrics"]["unexpected_acceptance"]["numerator"] == 1
    assert report["metrics"]["unexpected_acceptance"]["denominator"] == 8


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "hash", "extra_field", "oversize"])
def test_misaligned_or_unbounded_input_is_rejected(tmp_path, corruption):
    data = json.loads((ROOT / "evals/model_responses.json").read_text(encoding="utf-8"))
    if corruption == "missing": data["responses"].pop()
    if corruption == "duplicate": data["responses"][1] = copy.deepcopy(data["responses"][0])
    if corruption == "hash": data["case_sha256"] = "0" * 64
    if corruption == "extra_field": data["api_key"] = "synthetic-credential"
    path = tmp_path / "responses.json"
    path.write_bytes(b"x" * (2 * 1024 * 1024 + 1) if corruption == "oversize" else json.dumps(data).encode())
    with pytest.raises(ValueError):
        load_inputs(path)


def test_bad_case_labels_are_not_silently_used_as_oracle():
    case = inputs()[0].cases[0].model_dump()
    case["expected"]["applicable_citations"] = [99]
    with pytest.raises(ValueError):
        Case.model_validate(case)


def test_report_and_markdown_never_copy_untrusted_response_text():
    def inject(records):
        records["shortage_and_delay"].response["choices"][0]["message"]["content"] = "synthetic-password=do-not-export"
    report = run(inject)
    assert not report["passed"]
    assert "do-not-export" not in json.dumps(report) + markdown_report(report)
    assert "FAIL" in markdown_report(report) and "not measured" in markdown_report(report)


@pytest.mark.parametrize("corruption", ["stale", "outside", "failed", "online", "mode", "hash", "missing_case", "cost"])
def test_delivery_refuses_reused_mismatched_or_online_replay_reports(tmp_path, corruption):
    (tmp_path / "evals").mkdir()
    for name in ["model_cases.json", "model_responses.json"]:
        shutil.copyfile(ROOT / "evals" / name, tmp_path / "evals" / name)
    report = run()
    report["report_path"] = ".local/model-evals/fresh/report.json"
    path = tmp_path / report["report_path"]
    path.parent.mkdir(parents=True)
    stdout = tmp_path / "stdout.json"
    def save():
        path.write_bytes(json.dumps(report).encode())
        stdout.write_bytes(json.dumps(report).encode())
    save()
    assert read_model_evaluation_report(stdout, set(), tmp_path)[1]["passed"]
    if corruption == "outside": report["report_path"] = str(tmp_path / "other.json")
    if corruption == "failed": report["passed"] = False
    if corruption == "online": report["live_model_requests"] = 1
    if corruption == "mode": report["mode"] = "recorded_replay"
    if corruption == "hash": report["responses_sha256"] = "0" * 64
    if corruption == "missing_case": report["results"].pop()
    if corruption == "cost": report["model_cost"] = 0
    save()
    with pytest.raises(ValueError):
        read_model_evaluation_report(stdout, {"fresh"} if corruption == "stale" else set(), tmp_path)


def test_cli_confirmation_failure_exit_and_replay_receipts(tmp_path, monkeypatch, capsys):
    (tmp_path / "evals").mkdir()
    for name in ["model_cases.json", "model_responses.json"]:
        shutil.copyfile(ROOT / "evals" / name, tmp_path / "evals" / name)
    monkeypatch.setattr(model_replay, "ROOT", tmp_path)
    with pytest.raises(SystemExit) as missing:
        main([])
    assert missing.value.code == 2 and not (tmp_path / ".local").exists()
    assert main(["--confirm"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "fixture_replay"
    assert json.loads((tmp_path / report["report_path"]).read_text(encoding="utf-8")) == report
    records = json.loads((tmp_path / "evals/model_responses.json").read_text(encoding="utf-8"))
    selection = json.loads(records["responses"][0]["response"]["choices"][0]["message"]["content"])
    selection["citation_numbers"] = [2]
    records["responses"][0]["response"]["choices"][0]["message"]["content"] = json.dumps(selection)
    path = tmp_path / "records.json"
    path.write_bytes(json.dumps(records).encode())
    assert main(["--confirm", "--responses", str(path)]) == 1
    assert json.loads(capsys.readouterr().out)["mode"] == "recorded_replay"
    path.write_bytes(b'{"api_key":"do-not-export"}')
    count = len(list((tmp_path / ".local/model-evals").iterdir()))
    with pytest.raises(SystemExit) as bad:
        main(["--confirm", "--responses", str(path)])
    assert bad.value.code == 2
    assert "do-not-export" not in capsys.readouterr().err
    assert len(list((tmp_path / ".local/model-evals").iterdir())) == count
