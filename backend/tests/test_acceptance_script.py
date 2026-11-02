"""The acceptance checker must fail closed on missing Docker evidence."""

import importlib
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("responses", "expected"),
    [
        (["a" * 12, "{}"], True),
        (["a" * 12, "null"], True),
        (["a" * 12, '{"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "55433"}]}'], False),
        ([""], False),
        (["a" * 12, "invalid-json"], False),
    ],
)
def test_database_port_check_requires_evidence(monkeypatch, responses, expected):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    module = importlib.import_module("scripts.verify_phase01")
    monkeypatch.setattr(module.shutil, "which", lambda _: "C:/docker.exe")
    outputs = iter(responses)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=next(outputs))

    monkeypatch.setattr(module.subprocess, "run", run)
    passed, detail = module.check_database_port("isolated-test-project")
    assert passed is expected
    assert detail
    assert "label=com.docker.compose.project=isolated-test-project" in calls[0]
    assert "label=com.docker.compose.service=postgres" in calls[0]


def test_database_port_check_docker_unavailable(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    module = importlib.import_module("scripts.verify_phase01")
    monkeypatch.setattr(module.shutil, "which", lambda _: "C:/docker.exe")

    def run(*args, **kwargs):
        raise FileNotFoundError("Docker is not installed")

    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.check_database_port("isolated-test-project")[0] is False
