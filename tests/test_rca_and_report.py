from __future__ import annotations

import json

from k8s_sre_agent.llm import LLMError
from k8s_sre_agent.models import ClusterSnapshot
from k8s_sre_agent.rca import SYSTEM_PROMPT, diagnose
from k8s_sre_agent.redact import redact
from k8s_sre_agent.report import to_json, to_markdown


class RecordingProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, answer: str = "## Summary\nThe checkout service misses a variable.") -> None:
        self.answer = answer
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system: str, prompt: str) -> str:
        self.prompts.append((system, prompt))
        return self.answer


class FailingProvider(RecordingProvider):
    def complete(self, system: str, prompt: str) -> str:
        raise LLMError("boom")


def test_diagnosis_without_provider_is_deterministic(incident_snapshot: ClusterSnapshot) -> None:
    first = to_json(diagnose(incident_snapshot))
    second = to_json(diagnose(incident_snapshot))
    assert first == second
    assert json.loads(first)["analysis"] is None


def test_prompt_is_redacted_and_contains_findings(incident_snapshot: ClusterSnapshot) -> None:
    provider = RecordingProvider()
    result = diagnose(incident_snapshot, provider)
    system, prompt = provider.prompts[0]
    assert system == SYSTEM_PROMPT
    assert "hunter2" not in prompt
    assert "crash-loop" in prompt
    assert result.analysis and result.provider == "fake"


def test_provider_is_not_called_for_healthy_namespace(healthy_snapshot: ClusterSnapshot) -> None:
    provider = RecordingProvider()
    diagnose(healthy_snapshot, provider)
    assert provider.prompts == []


def test_provider_failure_degrades_to_a_warning(incident_snapshot: ClusterSnapshot) -> None:
    result = diagnose(incident_snapshot, FailingProvider())
    assert result.analysis is None
    assert result.findings
    assert "LLM analysis skipped: boom" in result.warnings


def test_markdown_report(incident_snapshot: ClusterSnapshot) -> None:
    report = to_markdown(diagnose(incident_snapshot, RecordingProvider()))
    assert report.startswith("# Diagnosis for namespace `shop`")
    assert "[CRITICAL] Deployment/checkout" in report
    assert "# Root cause analysis (fake: fake-1)" in report
    assert "hunter2" not in report


def test_json_report_summary(incident_snapshot: ClusterSnapshot) -> None:
    data = json.loads(to_json(diagnose(incident_snapshot)))
    assert data["summary"]["critical"] == 5
    assert data["summary"]["warning"] == 1


def test_redaction_patterns() -> None:
    text = (
        "password=s3cr3t token: abc123 Authorization: Bearer abcdefghijklmnop "
        "postgres://user:pa55@db:5432 AKIAIOSFODNN7EXAMPLE"
    )
    out = redact(text)
    for secret in ("s3cr3t", "abc123", "abcdefghijklmnop", "pa55", "AKIAIOSFODNN7EXAMPLE"):
        assert secret not in out
    assert "postgres://user:[REDACTED]@db:5432" in out
