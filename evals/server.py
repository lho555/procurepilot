"""Owned loopback API lifecycle for isolated evaluations; never stop another service."""
import json
import os
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from app.config import PROJECT_ROOT


class LocalAPI:
    def __init__(self, directory, database_url, knowledge_path):
        self.directory = directory
        self.env = {**os.environ, "DATABASE_URL": database_url, "RAG_INDEX_PATH": str(knowledge_path)}
        self.opener = build_opener(ProxyHandler({}))
        self.process = None
        self.log = (directory / "api.log").open("a", encoding="utf-8")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.process_ids = []

    def call(self, path, body=None):
        request = Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=45) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def start(self):
        instance = uuid4().hex
        self.process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", str(self.port), "--header", f"x-eval-instance:{instance}"], cwd=PROJECT_ROOT, env=self.env, stdout=self.log, stderr=self.log,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        self.process_ids.append(self.process.pid)
        for _ in range(150):
            if self.process.poll() is not None:
                raise RuntimeError("Owned eval API exited; inspect isolated api.log")
            try:
                with self.opener.open(f"http://127.0.0.1:{self.port}/health", timeout=1) as response:
                    if response.headers.get("x-eval-instance") == instance:
                        return
            except (URLError, OSError):
                pass
            time.sleep(0.1)
        raise RuntimeError("Owned eval API startup timed out")

    def stop(self):
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait(timeout=5)
            self.process = None

    def restart(self):
        self.stop(); self.start()

    def close(self):
        self.stop(); self.log.close()
