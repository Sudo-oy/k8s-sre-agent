"""Build the root cause analysis prompt and orchestrate a diagnosis."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from k8s_sre_agent.detectors import run_detectors
from k8s_sre_agent.llm import LLMError, LLMProvider
from k8s_sre_agent.models import ClusterSnapshot, Finding
from k8s_sre_agent.redact import redact

SYSTEM_PROMPT = """\
You are a senior Site Reliability Engineer performing a root cause analysis on a \
Kubernetes namespace. You receive a JSON document with deterministic findings produced by \
rule-based detectors and a compact, redacted snapshot of the cluster state.

Write a concise incident analysis in Markdown with exactly these sections:
## Summary
## Root cause
## Evidence
## Remediation
## Prevention

Rules:
- Base every statement on the provided data and cite the evidence (resource names, reasons, \
exit codes, log lines). If the data is insufficient, say what is missing and which kubectl \
command would reveal it.
- Correlate findings: distinguish the root cause from its symptoms (for example a failed \
rollout caused by a crashing container).
- Remediation steps must be concrete commands or spec changes, ordered from the safest to \
the most invasive. Never suggest deleting data or disabling security controls.
- The tool is read-only; the operator applies the remediation.
"""


@dataclass
class Diagnosis:
    namespace: str
    findings: list[Finding]
    analysis: str | None = None
    provider: str | None = None
    model: str | None = None
    warnings: list[str] = field(default_factory=list)


def build_prompt(snapshot: ClusterSnapshot, findings: list[Finding], max_log_chars: int) -> str:
    unhealthy = [
        {
            "pod": p.name,
            "workload": p.workload,
            "phase": p.phase,
            "node": p.node_name,
            "containers": [
                {
                    "name": c.name,
                    "image": c.image,
                    "state": c.state,
                    "reason": c.reason,
                    "restarts": c.restart_count,
                    "last_reason": c.last_state_reason,
                    "last_exit_code": c.last_exit_code,
                    "memory_limit": c.memory_limit,
                }
                for c in [*p.init_containers, *p.containers]
            ],
        }
        for p in snapshot.pods
        if p.phase not in {"Running", "Succeeded"}
        or any(not c.ready or c.restart_count for c in p.containers)
    ]
    logs = {key: text[-max_log_chars:] for key, text in sorted(snapshot.logs.items())}
    document = {
        "namespace": snapshot.namespace,
        "findings": [f.to_dict() for f in findings],
        "unhealthy_pods": unhealthy,
        "warning_events": [
            {
                "object": f"{e.involved_kind}/{e.involved_name}",
                "reason": e.reason,
                "message": e.message,
                "count": e.count,
            }
            for e in snapshot.events[:50]
        ],
        "deployments": [
            {"name": d.name, "replicas": d.replicas, "available": d.available_replicas}
            for d in snapshot.deployments
        ],
        "logs": logs,
    }
    return redact(json.dumps(document, indent=2, sort_keys=True))


def diagnose(
    snapshot: ClusterSnapshot,
    provider: LLMProvider | None = None,
    *,
    max_log_chars: int = 2000,
) -> Diagnosis:
    """Run the detectors and, when a provider is configured, ask it for an RCA."""
    findings = run_detectors(snapshot)
    diagnosis = Diagnosis(
        namespace=snapshot.namespace, findings=findings, warnings=list(snapshot.warnings)
    )
    if provider is None or not findings:
        return diagnosis
    try:
        diagnosis.analysis = provider.complete(
            SYSTEM_PROMPT, build_prompt(snapshot, findings, max_log_chars)
        )
        diagnosis.provider, diagnosis.model = provider.name, provider.model
    except LLMError as exc:
        diagnosis.warnings.append(f"LLM analysis skipped: {exc}")
    return diagnosis
