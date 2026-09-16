"""Render a diagnosis as Markdown or JSON."""

from __future__ import annotations

import json
from typing import Any

from k8s_sre_agent import __version__
from k8s_sre_agent.models import Severity
from k8s_sre_agent.rca import Diagnosis
from k8s_sre_agent.redact import redact

ICONS = {Severity.CRITICAL: "CRITICAL", Severity.WARNING: "WARNING", Severity.INFO: "INFO"}


def to_json(diagnosis: Diagnosis) -> str:
    data: dict[str, Any] = {
        "version": __version__,
        "namespace": diagnosis.namespace,
        "summary": {
            s.value: sum(1 for f in diagnosis.findings if f.severity is s) for s in Severity
        },
        "findings": [f.to_dict() for f in diagnosis.findings],
        "analysis": diagnosis.analysis,
        "llm": {"provider": diagnosis.provider, "model": diagnosis.model}
        if diagnosis.provider
        else None,
        "warnings": diagnosis.warnings,
    }
    return redact(json.dumps(data, indent=2))


def to_markdown(diagnosis: Diagnosis, *, show_resources: bool = True) -> str:
    lines = [f"# Diagnosis for namespace `{diagnosis.namespace}`", ""]
    if not diagnosis.findings:
        lines += ["No problem detected.", ""]
    else:
        counts = ", ".join(
            f"{sum(1 for f in diagnosis.findings if f.severity is s)} {s.value}"
            for s in Severity
            if any(f.severity is s for f in diagnosis.findings)
        )
        lines += [f"**{len(diagnosis.findings)} finding(s)**: {counts}", ""]
        for i, f in enumerate(diagnosis.findings, start=1):
            lines += [
                f"## {i}. [{ICONS[f.severity]}] {f.workload}: {f.summary}",
                "",
                f"- **Detector**: `{f.detector}`",
                f"- **Probable cause**: {f.probable_cause}",
            ]
            if show_resources and f.resources:
                lines.append(f"- **Resources**: {', '.join(f.resources)}")
            lines += ["", "**Evidence**", ""]
            lines += [f"- {e}" for e in f.evidence]
            lines += ["", "**Remediation**", ""]
            lines += [f"{n}. {step}" for n, step in enumerate(f.remediation, start=1)]
            lines.append("")
    if diagnosis.analysis:
        lines += [
            f"# Root cause analysis ({diagnosis.provider}: {diagnosis.model})",
            "",
            diagnosis.analysis.strip(),
            "",
        ]
    if diagnosis.warnings:
        lines += ["---", ""]
        lines += [f"> {w}" for w in diagnosis.warnings]
        lines.append("")
    return redact("\n".join(lines))
