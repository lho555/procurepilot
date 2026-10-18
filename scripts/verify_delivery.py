"""Verify an installed checkout using isolated data and owned loopback services."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYSTEM_ENVIRONMENT = {
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "SYSTEMDRIVE",
    "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "ALLUSERSPROFILE", "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE", "LANG", "LC_ALL", "TZ", "TERM", "CI",
}


def child_environment(directory: Path, root: Path = PROJECT_ROOT) -> dict[str, str]:
    # Do not inherit another project's database, browser-exposed VITE_* values,
    # model credentials, Python imports, or tracing/export configuration.
    env = {key: value for key, value in os.environ.items() if key.upper() in SYSTEM_ENVIRONMENT}
    env.update(
        PYTHONPATH=os.pathsep.join([str(root / "backend"), str(root)]),
        PYTHONNOUSERSITE="1", PYTHONIOENCODING="utf-8",
        DATABASE_URL=f"sqlite:///{(directory / 'unused.db').as_posix()}",
        RAG_INDEX_PATH=str(directory / "unused-index.json"),
        LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false",
        PP_LLM_MODE="offline",
    )
    return env


def start_process(command, **options):
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(command, **options)
    process.delivery_session = sys.platform != "win32"
    return process


def stop_process(process):
    if process is None or process.poll() is not None:
        return
    if sys.platform == "win32":
        # The venv launcher, npm and MCP may own child processes. Limit tree
        # termination to this still-running process; never look up services by port.
        subprocess.run([str(Path(os.environ["SYSTEMROOT"]) / "System32/taskkill.exe"),
                        "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
    elif getattr(process, "delivery_session", False):
        os.killpg(process.pid, signal.SIGTERM)
    else:
        process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        if getattr(process, "delivery_session", False):
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=5)


def source_manifest(root: Path) -> dict:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root, capture_output=True, check=True,
    )
    files = []
    for name in sorted(set(result.stdout.decode("utf-8").rstrip("\0").split("\0"))):
        if not name or name.endswith(".md") or name == "SHA256SUMS" or name.startswith("docs/evidence/"):
            continue
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise ValueError("Source manifest requires regular files")
        files.append({"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    encoded = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip()
    return {"commit": commit, "sha256": hashlib.sha256(encoded).hexdigest(), "files": files,
            "recipe": "SHA256 of UTF-8 compact sorted-key JSON files array; tracked and unignored files, excluding Markdown, SHA256SUMS and docs/evidence/"}


def read_evaluation_report(stdout: Path, previous: set[str], root: Path = PROJECT_ROOT) -> tuple[Path, dict]:
    output = json.loads(stdout.read_text(encoding="utf-8"))
    report_path = Path(output["report_path"]).resolve()
    eval_root = (root / ".local/evals").resolve()
    if (not report_path.is_relative_to(eval_root) or report_path.name != "report.json"
            or report_path.parent.parent != eval_root or report_path.parent.name in previous):
        raise ValueError("Evaluator must produce a new isolated report")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    fixture = (root / "evals/golden_tasks.json").read_bytes()
    count = len(json.loads(fixture)["tasks"])
    if (report != output or report.get("passed") is not True or report.get("adapter") != "mock"
            or report.get("golden_sha256") != hashlib.sha256(fixture).hexdigest()
            or report.get("task_count") != count or report.get("evaluated_count") != count
            or len(report.get("results", [])) != count
            or not all(row.get("passed") is True for row in report["results"])):
        raise ValueError("Evaluator report is incomplete or failed")
    return report_path, report


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def read_model_evaluation_report(stdout: Path, previous: set[str], root: Path = PROJECT_ROOT) -> tuple[Path, dict]:
    output = json.loads(stdout.read_text(encoding="utf-8"))
    path = (root / output["report_path"]).resolve()
    report_root = (root / ".local/model-evals").resolve()
    if (not path.is_relative_to(report_root) or path.name != "report.json" or path.parent.parent != report_root
            or path.parent.name in previous):
        raise ValueError("Model evaluator must produce a new isolated report")
    report = json.loads(path.read_text(encoding="utf-8"))
    fixture = (root / "evals/model_cases.json").read_bytes()
    responses = (root / "evals/model_responses.json").read_bytes()
    cases = json.loads(fixture)["cases"]
    ids = [case["id"] for case in cases]
    if (report != output or report.get("schema_version") != 1 or report.get("stage") != 9
            or report.get("passed") is not True or report.get("mode") != "fixture_replay" or report.get("adapter") != "response_replay"
            or report.get("model_quality_claim") is not False or type(report.get("live_model_requests")) is not int or report["live_model_requests"] != 0
            or report.get("model_cost", 0) is not None or report.get("model_latency_ms", 0) is not None
            or report.get("case_sha256") != hashlib.sha256(fixture).hexdigest()
            or report.get("responses_sha256") != hashlib.sha256(responses).hexdigest()
            or report.get("task_count") != len(ids) or report.get("evaluated_count") != len(ids)
            or [row.get("id") for row in report.get("results", [])] != ids
            or not all(row.get("passed") is True for row in report["results"])
            or report.get("metrics", {}).get("task_success", {}).get("numerator") != len(ids)
            or report.get("metrics", {}).get("task_success", {}).get("denominator") != len(ids)):
        raise ValueError("Model evaluator report is incomplete, failed or not offline fixture replay")
    return path, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="Approve local isolated verification files and synthetic workflow writes")
    args = parser.parse_args()
    if not args.confirm:
        parser.error("--confirm is required; only new isolated synthetic data is used")
    directory = PROJECT_ROOT / ".local/delivery" / uuid4().hex
    directory.mkdir(parents=True)
    env = child_environment(directory)
    report = {"schema_version": 1, "passed": False, "started_at": datetime.now(timezone.utc).isoformat(),
              "environment": {"python": platform.python_version(), "os": platform.system()}, "steps": []}
    report_path = directory / "report.json"

    def persist():
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def run(name, command, timeout):
        started = time.monotonic()
        row = {"name": name, "passed": False, "stdout": str((directory / f"{name}.stdout.log").relative_to(PROJECT_ROOT)),
               "stderr": str((directory / f"{name}.stderr.log").relative_to(PROJECT_ROOT))}
        report["steps"].append(row)
        persist()
        with (PROJECT_ROOT / row["stdout"]).open("w", encoding="utf-8") as stdout, (PROJECT_ROOT / row["stderr"]).open("w", encoding="utf-8") as stderr:
            try:
                process = start_process(command, cwd=PROJECT_ROOT, env=env, stdout=stdout, stderr=stderr)
                try:
                    row["exit_code"] = process.wait(timeout=timeout)
                finally:
                    stop_process(process)
            except subprocess.TimeoutExpired:
                row["error_type"] = "TimeoutExpired"
            finally:
                row["duration_seconds"] = round(time.monotonic() - started, 3)
                row["passed"] = row.get("exit_code") == 0
                persist()
        if not row["passed"]:
            raise RuntimeError(f"{name} failed; inspect its local logs")
        print(json.dumps({"step": name, "passed": True}), flush=True)
        return PROJECT_ROOT / row["stdout"]

    try:
        report["source"] = source_manifest(PROJECT_ROOT)
        persist()
        npm = shutil.which("npm.cmd" if sys.platform == "win32" else "npm")
        node = shutil.which("node")
        if not npm or not node:
            raise RuntimeError("Node.js and npm must be installed")
        run("python_dependencies", [sys.executable, "-m", "pip", "check"], 60)
        node_version = run("node_version", [node, "--version"], 15)
        report["environment"]["node"] = node_version.read_text(encoding="utf-8").strip()
        run("backend_tests", [sys.executable, "-m", "pytest", "backend/tests", "-q", "--import-mode=importlib",
                              "-o", "pythonpath=backend", "--basetemp", str(directory / "pytest-temp"),
                              "--junitxml", str(directory / "pytest.xml")], 600)
        run("frontend_build", [npm, "--prefix", "frontend", "run", "build"], 180)
        model_root = PROJECT_ROOT / ".local/model-evals"
        previous_models = {path.name for path in model_root.iterdir()} if model_root.exists() else set()
        model_output = run("model_response_replay", [sys.executable, "scripts/run_model_evals.py", "--confirm"], 60)
        model_path, model_report = read_model_evaluation_report(model_output, previous_models)
        report["model_evaluation"] = {"report": str(model_path.relative_to(PROJECT_ROOT)), "passed": True,
                                      "task_count": model_report["task_count"], "mode": "fixture_replay", "live_model_requests": 0}
        eval_root = PROJECT_ROOT / ".local/evals"
        previous = {path.name for path in eval_root.iterdir()} if eval_root.exists() else set()
        output = run("golden_tasks", [sys.executable, "scripts/run_evals.py", "--confirm"], 600)
        evaluation_path, evaluation = read_evaluation_report(output, previous)
        report["evaluation"] = {"report": str(evaluation_path.relative_to(PROJECT_ROOT)),
                                "passed": evaluation["passed"], "task_count": evaluation["task_count"]}
        sys.path[:0] = [str(PROJECT_ROOT / "backend"), str(PROJECT_ROOT)]
        from evals.server import LocalAPI
        api = LocalAPI(directory, f"sqlite:///{(evaluation_path.parent / 'eval.db').as_posix()}", evaluation_path.parent / "knowledge.json")
        api.env = {**env, "DATABASE_URL": api.env["DATABASE_URL"], "RAG_INDEX_PATH": api.env["RAG_INDEX_PATH"]}
        frontend = None
        try:
            api.start()
            port = free_port()
            frontend_env = {**env, "BACKEND_URL": f"http://127.0.0.1:{api.port}"}
            with (directory / "frontend.log").open("w", encoding="utf-8") as log:
                frontend = start_process([node, "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
                    cwd=PROJECT_ROOT / "frontend", env=frontend_env, stdout=log, stderr=log)
                opener = build_opener(ProxyHandler({}))
                for _ in range(150):
                    if frontend.poll() is not None:
                        raise RuntimeError("Owned frontend exited; inspect its local log")
                    try:
                        with opener.open(f"http://127.0.0.1:{port}/", timeout=1):
                            break
                    except (URLError, OSError):
                        time.sleep(0.1)
                else:
                    raise RuntimeError("Owned frontend startup timed out")
                output = run("startup_smoke", [sys.executable, "scripts/smoke_demo.py", "--backend", f"http://127.0.0.1:{api.port}",
                                               "--frontend", f"http://127.0.0.1:{port}"], 45)
                smoke = json.loads(output.read_text(encoding="utf-8"))
                if smoke.get("passed") is not True or len(smoke.get("checks", [])) != 3:
                    raise ValueError("Startup smoke result is incomplete")
                report["startup_checks"] = [item["check"] for item in smoke["checks"]]
        finally:
            stop_process(frontend)
            stop_process(api.process)
            api.close()
        if source_manifest(PROJECT_ROOT)["sha256"] != report["source"]["sha256"]:
            raise ValueError("Source files changed during verification")
        report["passed"] = True
    except Exception as error:
        # Never copy potentially sensitive subprocess output into the summary.
        report["error_type"] = type(error).__name__
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        persist()
    print(json.dumps({"passed": report["passed"], "report": str(report_path.relative_to(PROJECT_ROOT))}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
