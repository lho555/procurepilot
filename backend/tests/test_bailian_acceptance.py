import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import shutil

import httpx
import pytest

from app.model_explanation import ChatCompletionsAdapter, ConfiguredAdapter, ModelFailure, ModelFailureKind
from evals import bailian_live as live


@pytest.fixture
def acceptance(tmp_path, monkeypatch):
    (tmp_path / "evals").mkdir()
    shutil.copyfile(live.ROOT / "evals/model_cases.json", tmp_path / "evals/model_cases.json")
    monkeypatch.setattr(live, "ROOT", tmp_path)
    monkeypatch.setattr(live, "fingerprint", lambda: "a" * 64)
    return tmp_path


def factory(handler):
    return lambda *args, **kwargs: ChatCompletionsAdapter(*args, **kwargs, transport=httpx.MockTransport(handler))


def response(case, **extra):
    selection = {"finding_ids": ["stock:1", "delivery"], "citation_numbers": [1], "recommendations": case.expected.required_recommendations}
    return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(selection)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30, "private_debug": "fixture-secret"}, **extra}


def test_bailian_body_and_safe_per_call_observation(acceptance):
    case = live.cases()[0][0]
    def handler(request):
        body = json.loads(request.content)
        assert body["enable_thinking"] is False and body["temperature"] == 0
        assert body["max_completion_tokens"] == 1024
        assert "store" not in body and "extra_body" not in body and "tools" not in body
        return httpx.Response(200, json=response(case))
    adapter = factory(handler)(live.ENDPOINT, live.MODEL, "fixture-key", provider="bailian")
    result = asyncio.run(adapter.explain(*case.inputs()))
    assert result["usage"] == {"prompt_tokens": 100, "completion_tokens": 30}
    assert "fixture-secret" not in json.dumps(result) and "fixture-key" not in json.dumps(result)
    assert result["request_elapsed_ms"] >= 0


@pytest.mark.parametrize("usage", [{"prompt_tokens": True, "completion_tokens": 30}, {"prompt_tokens": 100, "completion_tokens": 1035}, None])
def test_unreliable_usage_is_not_treated_as_free_or_valid_cost(acceptance, usage):
    case = live.cases()[0][0]
    adapter = factory(lambda _: httpx.Response(200, json=response(case, usage=usage)))(live.ENDPOINT, live.MODEL, "fixture-key", provider="bailian")
    result = asyncio.run(adapter.explain(*case.inputs()))
    assert result["usage"] is None and result["selection"]["citation_numbers"] == [1]


def test_concurrent_observations_are_per_call_not_shared_state(acceptance):
    selected = live.cases()[0][:2]
    async def handler(request):
        context = json.loads(json.loads(request.content)["messages"][1]["content"])
        index = 0 if "晚 2 天" in context["findings"]["delivery"] else 1
        await asyncio.sleep(0.01 if index == 0 else 0)
        return httpx.Response(200, json=response(selected[index], usage={"prompt_tokens": 100 + index, "completion_tokens": 30}))
    adapter = factory(handler)(live.ENDPOINT, live.MODEL, "fixture-key", provider="bailian")
    async def together():
        return await asyncio.gather(*(adapter.explain(*case.inputs()) for case in selected))
    results = asyncio.run(together())
    assert [result["usage"]["prompt_tokens"] for result in results] == [100, 101]


@pytest.mark.parametrize("endpoint,model,provider", [
    ("https://other.example/v1/chat/completions", live.MODEL, "bailian"),
    (live.ENDPOINT, "other-model", "bailian"), (live.ENDPOINT, live.MODEL, "unknown"),
])
def test_unreviewed_profile_rejected(endpoint, model, provider):
    with pytest.raises(ModelFailure):
        ChatCompletionsAdapter(endpoint, model, "fixture-key", provider=provider)


def test_configured_bailian_is_shared_without_provider_field_leaking(monkeypatch, acceptance):
    from app import model_explanation
    for name, value in {"PP_LLM_MODE": "chat_completions", "PP_LLM_ALLOW_SYNTHETIC_SEND": "true", "PP_LLM_PROVIDER": "bailian",
                        "PP_LLM_ENDPOINT": live.ENDPOINT, "PP_LLM_MODEL": live.MODEL, "PP_LLM_API_KEY": "fixture-key"}.items():
        monkeypatch.setenv(name, value)
    case = live.cases()[0][0]
    original = ChatCompletionsAdapter
    calls = []
    def build(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response(case))))
    monkeypatch.setattr(model_explanation, "ChatCompletionsAdapter", build)
    answer = asyncio.run(ConfiguredAdapter().summarize(*case.inputs()))
    assert "参考缺口 78" in answer and calls == [{"provider": "bailian"}]


@pytest.mark.parametrize("budget", ["0", "1", "4.7", "12.01", "NaN", "Infinity"])
def test_budget_refused_before_plan_or_key_access(acceptance, budget):
    with pytest.raises(ValueError):
        live.prepare(Decimal(budget))
    assert not (acceptance / ".local").exists()


def test_whole_reservation_precedes_key_and_one_shot_is_enforced(acceptance):
    plan, data = live.prepare(Decimal("12"))
    assert data["spec"]["reserved_cny"] == "4.7805312"
    calls, key_reads = [], []
    cases = live.cases()[0]
    def key():
        claim = json.loads((plan.parent / "claimed.json").read_text(encoding="utf-8"))
        assert claim["reserved_cny"] == "4.7805312"
        key_reads.append(True)
        return "fixture-credential"
    def handler(request):
        case = cases[len(calls)]
        assert (plan.parent / f"attempt-{case.id}.json").exists()
        calls.append(request)
        return httpx.Response(200, json=response(case))
    report = asyncio.run(live.execute(plan, key, factory(handler)))
    assert report["passed"] and report["request_attempts"] == 4 and report["validated_responses"] == 4
    assert report["failure"] is None
    assert Decimal(report["cost_estimate_upper_cny"]) <= live.RESERVATION
    assert report["reservation_refunded"] is False
    persisted = "".join(p.read_text(encoding="utf-8") for p in plan.parent.glob("*.json"))
    assert "fixture-credential" not in persisted and "fixture-secret" not in persisted
    with pytest.raises(FileExistsError):
        asyncio.run(live.execute(plan, key, factory(handler)))
    assert len(calls) == 4 and len(key_reads) == 1


@pytest.mark.parametrize("failure", ["key", "timeout", "http_error"])
def test_uncertain_failure_stops_without_retry_or_refund(acceptance, failure):
    plan, _ = live.prepare(Decimal("12"))
    calls = []
    def key():
        if failure == "key": raise ValueError("fixture-secret-path-or-token")
        return "fixture-key"
    def handler(request):
        calls.append(request)
        if failure == "timeout": raise httpx.ReadTimeout("fixture-secret-path-or-token", request=request)
        return httpx.Response(503, text="fixture-secret-path-or-token")
    report = asyncio.run(live.execute(plan, key, factory(handler)))
    assert not report["passed"] and report["status"] == "stopped"
    assert report["request_attempts"] == (0 if failure == "key" else 1)
    assert len(calls) == report["request_attempts"]
    assert report["reserved_cny"] == "4.7805312" and report["cost_estimate_upper_cny"] is None
    assert "fixture-secret" not in json.dumps(report)
    with pytest.raises(FileExistsError):
        asyncio.run(live.execute(plan, key, factory(handler)))


@pytest.mark.parametrize("mutation", ["source", "expired", "budget", "request"])
def test_changed_or_expired_plan_refused_before_claim_and_credentials(acceptance, monkeypatch, mutation):
    path, plan = live.prepare(Decimal("12"))
    if mutation == "source": monkeypatch.setattr(live, "fingerprint", lambda: "b" * 64)
    if mutation == "expired":
        now = datetime.now(timezone.utc)
        plan.update(created_at=(now-timedelta(days=2)).isoformat(), expires_at=(now-timedelta(days=1)).isoformat())
    if mutation == "budget": plan["spec"]["approved_budget_cny"] = "13"
    if mutation == "request": plan["spec"]["requests"][0]["body_sha256"] = "0" * 64
    path.write_bytes(json.dumps(plan).encode())
    def never(): raise AssertionError("Unexpected credential read")
    with pytest.raises(ValueError):
        asyncio.run(live.execute(path, never))
    assert not (path.parent / "claimed.json").exists()


def test_concurrent_execution_only_one_can_read_key_or_call_provider(acceptance):
    path, _ = live.prepare(Decimal("12"))
    selected = live.cases()[0]
    calls, keys = [], []
    async def handler(request):
        case = selected[len(calls)]
        calls.append(request)
        await asyncio.sleep(0)
        return httpx.Response(200, json=response(case))
    def key():
        keys.append(True)
        return "fixture-key"
    async def concurrent():
        return await asyncio.gather(live.execute(path, key, factory(handler)), live.execute(path, key, factory(handler)), return_exceptions=True)
    outcomes = asyncio.run(concurrent())
    assert sum(isinstance(item, FileExistsError) for item in outcomes) == 1
    assert len(calls) == 4 and len(keys) == 1


def test_quality_failure_is_reported_without_tuning_or_more_requests(acceptance):
    plan, _ = live.prepare(Decimal("12"))
    selected, calls = live.cases()[0], []
    def handler(request):
        case = selected[len(calls)]
        data = response(case)
        if not calls:
            selection = json.loads(data["choices"][0]["message"]["content"])
            selection["citation_numbers"] = [2]
            data["choices"][0]["message"]["content"] = json.dumps(selection)
        calls.append(request)
        return httpx.Response(200, json=data)
    report = asyncio.run(live.execute(plan, lambda: "fixture-key", factory(handler)))
    assert not report["passed"] and report["status"] == "completed_with_quality_or_usage_gaps"
    assert report["failure"] is None
    assert len(calls) == 4 and not report["observations"][0]["label_checks"]["checks"]["citation_applicability"]


def test_named_credential_is_not_expanded_or_copied_to_another_file(tmp_path):
    path = tmp_path / ".env"
    path.write_bytes(b'OTHER=not-used\nREFERENCE=${KEY}\nKEY="fixture-backend-credential"\n')
    assert live.read_credential(path, "KEY") == "fixture-backend-credential"
    with pytest.raises(ValueError): live.read_credential(path, "REFERENCE")
    assert len(list(tmp_path.iterdir())) == 1


def test_cli_plan_is_offline_and_execute_requires_explicit_flags(acceptance, monkeypatch, capsys):
    def never(*args): raise AssertionError("Offline preparation accessed credentials")
    monkeypatch.setattr(live, "read_credential", never)
    with pytest.raises(SystemExit) as missing:
        live.main(["--prepare"])
    assert missing.value.code == 2 and not (acceptance / ".local").exists()
    assert live.main(["--prepare", "--confirm-local", "--budget-cny", "12"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["spec"]["maximum_requests"] == 4
    with pytest.raises(SystemExit) as no_live:
        live.main(["--execute", str(acceptance / output["plan"])])
    assert no_live.value.code == 2


@pytest.mark.parametrize("event,category,status", [
    ("connect", "transport", None), ("timeout", "timeout", None),
    ("401", "provider_http", 401), ("429", "provider_http", 429),
    ("503", "provider_http", 503), ("302", "provider_http", 302),
    ("oversize", "response_limit", None), ("invalid_json", "output_contract", None),
    ("invalid_facts", "output_contract", None), ("truncated", "output_contract", None),
])
def test_failure_report_classifies_real_adapter_boundary_without_secret_or_retry(acceptance, event, category, status):
    path, _ = live.prepare(Decimal("12"))
    calls = []
    def handler(request):
        calls.append(request)
        if event == "connect": raise httpx.ConnectError("fixture-sensitive-error", request=request)
        if event == "timeout": raise httpx.ReadTimeout("fixture-sensitive-error", request=request)
        if event.isdecimal(): return httpx.Response(int(event), text="fixture-sensitive-body", headers={"Location": "https://fixture-sensitive-host.example"})
        if event == "oversize": return httpx.Response(200, text="fixture-sensitive-body" * 4000)
        if event == "invalid_json": return httpx.Response(200, text="fixture-sensitive-body")
        data = response(live.cases()[0][0])
        if event == "invalid_facts":
            data["choices"][0]["message"]["content"] = json.dumps({"finding_ids": ["fixture-sensitive-fact"], "citation_numbers": [1], "recommendations": ["HUMAN_REVIEW"]})
        if event == "truncated": data["choices"][0]["finish_reason"] = "length"
        return httpx.Response(200, json=data)
    report = asyncio.run(live.execute(path, lambda: "fixture-sensitive-key", factory(handler)))
    assert report["failure"] == {"category": category, "phase": "model_request", "case_id": "shortage_and_delay", "http_status": status}
    assert report["status"] == "stopped" and not report["passed"]
    assert len(calls) == report["request_attempts"] == 1 and report["validated_responses"] == 0
    assert report["cost_estimate_upper_cny"] is None and report["reserved_cny"] == "4.7805312"
    persisted = "".join(p.read_text(encoding="utf-8") for p in path.parent.glob("*.json"))
    assert "fixture-sensitive" not in persisted and "ConnectError" not in persisted and "ReadTimeout" not in persisted
    with pytest.raises(FileExistsError):
        asyncio.run(live.execute(path, lambda: pytest.fail("Second credential read"), factory(handler)))
    assert len(calls) == 1


def test_second_case_failure_preserves_first_observation_and_stops_remaining_cases(acceptance):
    path, _ = live.prepare(Decimal("12"))
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response(live.cases()[0][0])) if len(calls) == 1 else httpx.Response(429, text="fixture-sensitive-body")
    report = asyncio.run(live.execute(path, lambda: "fixture-key", factory(handler)))
    assert report["failure"] == {"category": "provider_http", "phase": "model_request", "case_id": "shortage_only", "http_status": 429}
    assert len(calls) == report["request_attempts"] == 2 and report["validated_responses"] == 1
    assert report["observations"][0]["label_checks"]["passed"]
    assert report["cost_estimate_upper_cny"] is None and not report["reservation_refunded"]
    assert not (path.parent / "attempt-delay_only.json").exists()


@pytest.mark.parametrize("phase,category,attempts", [
    ("credential", "credential", 0), ("configure_adapter", "configuration", 0),
    ("prepare_request", "local_processing", 0), ("reserve_request", "local_processing", 0),
    ("validate_result", "local_processing", 1), ("persist_result", "local_processing", 1),
    ("model_request", "local_processing", 1),
])
def test_local_failures_keep_phase_and_never_claim_provider_failure(acceptance, monkeypatch, phase, category, attempts):
    path, _ = live.prepare(Decimal("12"))
    calls = []
    def key():
        if phase == "credential": raise TimeoutError("fixture-sensitive-file")
        return "fixture-key"
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response(live.cases()[0][0]))
    def fail(*args, **kwargs): raise RuntimeError("fixture-sensitive-runtime")
    def build(*args, **kwargs):
        if phase == "configure_adapter": return ChatCompletionsAdapter(live.ENDPOINT, "invalid", "fixture-key", provider="bailian")
        adapter = factory(handler)(*args, **kwargs)
        if phase == "prepare_request": adapter.request_body = fail
        if phase == "model_request":
            async def unknown(*args): raise RuntimeError("fixture-sensitive-runtime")
            adapter.explain = unknown
        return adapter
    write = live.write_new
    def persist(target, value):
        if (phase == "reserve_request" and target.name.startswith("attempt-")) or (phase == "persist_result" and target.name.startswith("result-")):
            raise OSError("fixture-sensitive-file")
        write(target, value)
    monkeypatch.setattr(live, "write_new", persist)
    if phase == "validate_result":
        async def invalid_local_result(*args):
            raise ModelFailure("fixture-sensitive-local", kind=ModelFailureKind.PROVIDER_HTTP, http_status=503)
        monkeypatch.setattr(live, "evaluate_case", invalid_local_result)
    report = asyncio.run(live.execute(path, key, build))
    assert report["failure"] == {"category": category, "phase": phase, "case_id": None if attempts == 0 and phase in {"credential", "configure_adapter"} else "shortage_and_delay", "http_status": None}
    assert report["request_attempts"] == attempts and report["validated_responses"] == 0
    assert len(calls) == (0 if phase == "model_request" else attempts)
    assert report["status"] == "stopped" and report["reserved_cny"] == "4.7805312"
    persisted = "".join(p.read_text(encoding="utf-8") for p in path.parent.glob("*.json"))
    assert "fixture-sensitive" not in persisted


@pytest.mark.parametrize("status", [True, 700, "fixture-sensitive-status"])
def test_failure_metadata_is_allowlisted_even_when_exception_attributes_are_invalid(status):
    error = ModelFailure("fixture-sensitive-error", kind=ModelFailureKind.PROVIDER_HTTP, http_status=status)
    result = live.safe_failure(error, "model_request", live.CASE_IDS[0])
    assert result["category"] == "provider_http" and result["http_status"] is None
    error.kind = "fixture-sensitive-category"
    assert live.safe_failure(error, "model_request", live.CASE_IDS[0])["category"] == "local_processing"
    assert "fixture-sensitive" not in json.dumps(result)


def test_cli_reports_only_safe_failure_fields(acceptance, monkeypatch, capsys):
    path, _ = live.prepare(Decimal("12"))
    execute = live.execute
    monkeypatch.setattr(live, "read_credential", lambda *args: "fixture-sensitive-key")
    def handler(request): raise httpx.ConnectError("fixture-sensitive-host", request=request)
    monkeypatch.setattr(live, "execute", lambda plan, loader: execute(plan, loader, factory(handler)))
    assert live.main(["--execute", str(path), "--confirm-live", "--credential-file", "unused-fixture", "--credential-name", "KEY"]) == 1
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["failure"]["category"] == "transport"
    assert "fixture-sensitive" not in output.out + output.err and not output.err
