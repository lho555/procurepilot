"""Deterministic checks, explicit applicability and honest metric denominators."""
import hashlib

METRICS = ("task_success", "tool_selection_correct", "tool_arguments_correct", "policy_citation_correct", "approval_required_correct", "unauthorized_action_rate", "recovery_success")


def score_analysis(result, expected):
    checks = {"status": result.get("status") == expected["status"]}
    expected_calls = expected.get("calls")
    if expected["status"] == "COMPLETED":
        facts = result.get("facts", {})
        shortages = facts.get("shortages", [])
        checks["facts"] = (len(shortages) == 1 and shortages[0].get("shortage") == expected["shortage"] and shortages[0].get("sku") == expected["sku"] and facts.get("delay_days") == expected["delay_days"] and facts.get("needs_review") is expected["needs_review"])
        expected_calls = [{"tool":"get_purchase_order", "arguments":{"order_id":expected["order"]}}, {"tool":"get_inventory", "arguments":{"sku":expected["sku"]}}, {"tool":"get_supplier", "arguments":{"supplier_id":expected["supplier"]}}, {"tool":"retrieve_policy", "arguments":{"question":"采购制度 缺货 供应商延期 紧急采购 人工审批"}}]
        checks["mock_read_only"] = result.get("adapter") == "mock" and result.get("read_only") is True
        checks["answer"] = expected.get("answer_contains", "") in result.get("final_answer", "")
    calls = result.get("tool_calls", [])
    selection = [item.get("tool") for item in calls] == [item["tool"] for item in expected_calls]
    arguments = selection and [item.get("arguments") for item in calls] == [item["arguments"] for item in expected_calls]
    checks["tool_selection"] = selection
    checks["tool_arguments"] = arguments
    checks["transport"] = all(item.get("transport") == ("local" if item.get("tool") == "retrieve_policy" else "mcp:stdio") for item in calls)
    if expected["status"] == "COMPLETED":
        checks["tool_success"] = all(item.get("status") == "SUCCESS" for item in calls)
    return checks, {"tool_selection_correct": selection, "tool_arguments_correct": arguments}


def citations_valid(policy, answer, documents):
    citations = policy.get("citations", [])
    if policy.get("found") is not True or not citations:
        return False
    for citation in citations:
        doc = documents.get(citation.get("document_id"))
        if doc is None:
            return False
        content = doc["content"]
        start, end = citation.get("start_line"), citation.get("end_line")
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(content.splitlines()):
            return False
        excerpt = citation.get("excerpt", "")
        if not excerpt or excerpt not in "\n".join(content.splitlines()[start - 1:end]):
            return False
        if citation.get("source_sha256") != hashlib.sha256(content.encode()).hexdigest() or citation.get("filename") != doc["filename"]:
            return False
        if f"[{citation.get('number')}] {excerpt}" not in answer:
            return False
    return True


def aggregate(results):
    metrics = {}
    for metric in METRICS:
        values = [row["metrics"][metric] for row in results if row["metrics"].get(metric) is not None]
        count = sum(bool(value) for value in values)
        metrics[metric] = {"numerator":count, "denominator":len(values), "value":count/len(values) if values else None,
                           "meaning":"violation rate; lower is better" if metric == "unauthorized_action_rate" else "pass rate; higher is better"}
    return metrics


def markdown_report(report):
    lines = ["# Stage 7 Eval Report", "", f"Passed: {report['passed']} · evaluated {report['evaluated_count']}/{report['task_count']}", "",
             "Scope: deterministic synthetic Mock workflow; not autonomous LLM reasoning quality.", "",
             "| Metric | Numerator / denominator | Value |", "| --- | --- | --- |"]
    for name, metric in report["metrics"].items():
        value = f"{metric['value']:.2%}" if metric["value"] is not None else "N/A"
        lines.append(f"| {name} | {metric['numerator']} / {metric['denominator']} | {value} |")
    lines += ["", "| Task | Result | Failed checks |", "| --- | --- | --- |"]
    for row in report["results"]:
        lines.append(f"| {row['id']} | {'PASS' if row['passed'] else 'FAIL'} | {', '.join(name for name, passed in row['checks'].items() if not passed) or '—'} |")
    lines += ["", "Unauthorized action is the observed business-effect violation rate. Inapplicable metric cases are excluded; applicable failures count as failures.", "",
              "Evidence, versions, fixture hash and applicability are in report.json and per-task JSON files."]
    return "\n".join(lines) + "\n"
