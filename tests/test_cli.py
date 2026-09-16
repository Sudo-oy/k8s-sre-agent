from __future__ import annotations

import json
from pathlib import Path

import pytest

from k8s_sre_agent import __version__
from k8s_sre_agent.cli import EXIT_ERROR, EXIT_FINDINGS, EXIT_OK, main

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _no_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("SRE_AGENT_LLM_PROVIDER", "SRE_AGENT_LLM_MODEL", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == EXIT_OK
    assert capsys.readouterr().out.strip() == __version__


def test_diagnose_from_snapshot_json(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        ["diagnose", "--from-snapshot", str(FIXTURES / "incident-snapshot.json"), "-o", "json"]
    )
    assert code == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["namespace"] == "shop"
    assert data["llm"] is None


def test_fail_on_critical_returns_exit_code_2(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "diagnose",
            "--from-snapshot",
            str(FIXTURES / "incident-snapshot.json"),
            "--fail-on",
            "critical",
        ]
    )
    assert code == EXIT_FINDINGS
    assert "# Diagnosis" in capsys.readouterr().out


def test_healthy_snapshot_passes_fail_on_warning() -> None:
    args = ["diagnose", "--from-snapshot", str(FIXTURES / "healthy-snapshot.json")]
    assert main([*args, "--fail-on", "warning"]) == EXIT_OK


def test_misconfigured_provider_is_an_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SRE_AGENT_LLM_PROVIDER", "openai")
    code = main(["diagnose", "--from-snapshot", str(FIXTURES / "healthy-snapshot.json")])
    assert code == EXIT_ERROR
    assert "SRE_AGENT_LLM_MODEL" in capsys.readouterr().err


def test_missing_snapshot_file_is_an_error(tmp_path: Path) -> None:
    assert main(["diagnose", "--from-snapshot", str(tmp_path / "nope.json")]) == EXIT_ERROR
