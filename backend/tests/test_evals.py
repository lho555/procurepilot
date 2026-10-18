import copy
import hashlib
import json

import pytest

from app.config import PROJECT_ROOT
from evals.runner import applicable_metrics, load_tasks
from evals.scorer import aggregate, citations_valid, markdown_report, score_analysis


def test_golden_tasks_cover_required_scenarios():
    tasks, digest = load_tasks(PROJECT_ROOT / "evals/golden_tasks.json")
    assert len(tasks) == 17 and len(digest) == 64
    assert {"normal_order","shortage","supplier_delay","emergency_policy","no_procurement","missing_order","wrong_sku","approval_required","approval_rejected","restart_resume"} <= {task["id"] for task in tasks}


def test_wrong_tool_or_argument_does_not_score_as_correct():
    expected = {"status":"NOT_FOUND","calls":[{"tool":"get_purchase_order","arguments":{"order_id":"PO-2026-99999"}}]}
    result = {"status":"NOT_FOUND","tool_calls":[{"tool":"get_purchase_order","arguments":{"order_id":"PO-2026-10086"},"transport":"mcp:stdio"}]}
    checks, metrics = score_analysis(result, expected)
    assert metrics["tool_selection_correct"] is True
    assert metrics["tool_arguments_correct"] is False and not all(checks.values())
    result["tool_calls"][0]["tool"] = "create_purchase_request_draft"
    assert score_analysis(result, expected)[1]["tool_selection_correct"] is False


def test_unnecessary_tool_for_missing_input_fails():
    expected = {"status":"NEEDS_INPUT","calls":[]}
    result = {"status":"NEEDS_INPUT","tool_calls":[{"tool":"get_inventory","arguments":{"sku":"SKU-MOTOR-01"},"transport":"mcp:stdio"}]}
    assert score_analysis(result, expected)[1]["tool_selection_correct"] is False


def test_forged_or_misaligned_citation_fails():
    content = "标题\n原文证据\n末尾"
    documents = {"doc":{"content":content,"filename":"policy.md"}}
    citation = {"number":1,"document_id":"doc","filename":"policy.md","start_line":2,"end_line":2,"excerpt":"原文证据","source_sha256":hashlib.sha256(content.encode()).hexdigest()}
    policy = {"found":True,"citations":[citation]}
    assert citations_valid(policy, "[1] 原文证据", documents)
    for changes in [{"excerpt":"伪造审批"}, {"start_line":1,"end_line":1}, {"source_sha256":"fake"}, {"document_id":"missing"}, {"filename":"fake.md"}]:
        wrong = copy.deepcopy(policy)
        wrong["citations"][0].update(changes)
        assert not citations_valid(wrong, "[1] 原文证据", documents)
    assert not citations_valid(policy, "没有引用", documents)


def test_denominators_exclude_inapplicable_not_failures():
    metrics = aggregate([
        {"metrics":{"task_success":True,"policy_citation_correct":True,"unauthorized_action_rate":False}},
        {"metrics":{"task_success":False,"policy_citation_correct":False,"unauthorized_action_rate":True}},
        {"metrics":{"task_success":True}},
    ])
    assert metrics["task_success"]["denominator"] == 3
    assert metrics["policy_citation_correct"]["value"] == 0.5
    assert metrics["unauthorized_action_rate"]["value"] == 0.5
    assert metrics["recovery_success"]["value"] is None


def test_failed_applicable_task_keeps_metric_in_denominator():
    task = {"id":"restart_resume","kind":"recovery","expected":{"status":"COMPLETED"}}
    failed = dict.fromkeys(applicable_metrics(task), False)
    metrics = aggregate([{"metrics":failed}])
    assert metrics["recovery_success"]["denominator"] == 1 and metrics["recovery_success"]["value"] == 0


def test_duplicate_task_ids_are_rejected(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps({"version":1,"tasks":[{"id":"same","kind":"knowledge","expected":{}}]*10}), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_tasks(path)


def test_human_report_shows_failure_and_na():
    rows = [{"id":"wrong_call","passed":False,"checks":{"tool_arguments":False},"metrics":{"task_success":False}}]
    report = {"passed":False,"evaluated_count":1,"task_count":17,"results":rows,"metrics":aggregate(rows)}
    text = markdown_report(report)
    assert "FAIL" in text and "tool_arguments" in text and "N/A" in text and "1/17" in text
