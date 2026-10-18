import hashlib
import json
import os
import socket
import subprocess
import sys

import pytest

from scripts.verify_delivery import child_environment, read_evaluation_report, start_process, stop_process


def test_delivery_environment_does_not_inherit_business_secrets_or_imports(tmp_path, monkeypatch):
    for name in ["DATABASE_URL", "RAG_INDEX_PATH", "OPENAI_API_KEY", "LANGSMITH_API_KEY", "VITE_API_KEY", "PYTHONPATH", "HTTP_PROXY"]:
        monkeypatch.setenv(name, "fixture-not-a-real-credential")
    env = child_environment(tmp_path, tmp_path)
    assert env["DATABASE_URL"] == f"sqlite:///{(tmp_path / 'unused.db').as_posix()}"
    assert env["RAG_INDEX_PATH"] == str(tmp_path / "unused-index.json")
    assert env["PYTHONPATH"] == os.pathsep.join([str(tmp_path / "backend"), str(tmp_path)])
    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["PP_LLM_MODE"] == "offline"
    assert all(name not in env for name in ["OPENAI_API_KEY", "LANGSMITH_API_KEY", "VITE_API_KEY", "HTTP_PROXY"])


def test_delivery_rejects_stale_external_and_failed_evaluation_reports(tmp_path):
    fixture = tmp_path / "evals/golden_tasks.json"
    fixture.parent.mkdir()
    fixture.write_text('{"tasks": [{"id": "example"}]}', encoding="utf-8")
    path = tmp_path / ".local/evals/current/report.json"
    path.parent.mkdir(parents=True)
    report = {"report_path": str(path), "passed": True, "adapter": "mock",
              "golden_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
              "task_count": 1, "evaluated_count": 1, "results": [{"passed": True}]}
    stdout = tmp_path / "stdout.json"

    def save():
        text = json.dumps(report)
        path.write_text(text, encoding="utf-8")
        stdout.write_text(text, encoding="utf-8")

    save()
    assert read_evaluation_report(stdout, set(), tmp_path)[0] == path
    with pytest.raises(ValueError):
        read_evaluation_report(stdout, {"current"}, tmp_path)
    report["passed"] = False
    save()
    with pytest.raises(ValueError):
        read_evaluation_report(stdout, set(), tmp_path)
    report["report_path"] = str(tmp_path / "outside.json")
    save()
    with pytest.raises(ValueError):
        read_evaluation_report(stdout, set(), tmp_path)


def test_cleanup_stops_only_the_owned_process():
    child_code = "import socket,time; s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(); print(s.getsockname()[1],flush=True); time.sleep(30)"
    parent_code = f"import subprocess,sys; child=subprocess.Popen([sys.executable,'-c',{child_code!r}]); child.wait()"
    owned = start_process([sys.executable, "-c", parent_code], stdout=subprocess.PIPE, text=True)
    other = start_process([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        port = int(owned.stdout.readline())
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
        stop_process(owned)
        assert owned.poll() is not None
        assert other.poll() is None
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1)
        stop_process(owned)
    finally:
        stop_process(owned)
        stop_process(other)
        owned.stdout.close()
