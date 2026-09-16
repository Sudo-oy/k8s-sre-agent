"""Deterministic detectors that turn a cluster snapshot into findings.

Detectors never call the Kubernetes API or an LLM: they only read the snapshot, so
their output is reproducible and easy to test. Findings are grouped per workload and
container, so ten crashing replicas of one Deployment produce a single finding.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from k8s_sre_agent.models import (
    ClusterSnapshot,
    ContainerSnapshot,
    EventSnapshot,
    Finding,
    PodSnapshot,
    Severity,
)

EXIT_CODE_HINTS = {
    1: "the application exited with a generic error; the logs usually show the exception",
    2: "the command was misused (bad arguments or shell syntax)",
    126: "the entrypoint is not executable (permissions or wrong binary architecture)",
    127: "the entrypoint command was not found in the image",
    128: "the process called exit() with an invalid argument",
    134: "the process aborted with SIGABRT (assertion failure or abort())",
    137: "the process was killed with SIGKILL (out of memory or a failed liveness probe)",
    139: "the process crashed with a segmentation fault",
    143: "the process received SIGTERM (liveness probe failure or eviction)",
    255: "the exit status is out of range (often a wrapper script or unsigned 8-bit overflow)",
}

IMAGE_PULL_REASONS = {"ImagePullBackOff", "ErrImagePull", "InvalidImageName", "ErrImageNeverPull"}
CONFIG_ERROR_REASONS = {"CreateContainerConfigError", "CreateContainerError", "RunContainerError"}

ROLLBACK_KINDS = {"Deployment", "StatefulSet", "DaemonSet"}

Detector = Callable[[ClusterSnapshot], list[Finding]]


@dataclass
class _Group:
    finding: Finding
    pods: set[str] = field(default_factory=set)


class _Accumulator:
    def __init__(self) -> None:
        self._groups: dict[tuple[str, ...], _Group] = {}

    def add(self, key: tuple[str, ...], pod: str, finding: Finding) -> None:
        """Record ``pod`` under ``key``; the first finding seen for a key is kept."""
        group = self._groups.get(key)
        if group is None:
            group = self._groups[key] = _Group(finding=finding)
        group.pods.add(pod)

    def findings(self) -> list[Finding]:
        out = []
        for group in self._groups.values():
            group.finding.resources = sorted(f"Pod/{p}" for p in group.pods)
            count = len(group.pods)
            group.finding.evidence.insert(0, f"{count} pod{'s' if count > 1 else ''} affected")
            out.append(group.finding)
        return out


def _all_containers(pod: PodSnapshot) -> Iterable[ContainerSnapshot]:
    yield from pod.init_containers
    yield from pod.containers


def _events_for(snapshot: ClusterSnapshot, pod_name: str) -> list[EventSnapshot]:
    return [e for e in snapshot.events if e.involved_kind == "Pod" and e.involved_name == pod_name]


def _rollback_hint(pod: PodSnapshot) -> list[str]:
    kind, _, name = pod.workload.partition("/")
    if kind not in ROLLBACK_KINDS:
        return []
    return [
        f"kubectl rollout undo -n {pod.namespace} {kind.lower()}/{name} "
        "(if a recent rollout introduced the problem)"
    ]


def _last_log_lines(snapshot: ClusterSnapshot, pod: str, container: str, n: int = 5) -> list[str]:
    text = snapshot.logs.get(f"{pod}/{container}", "")
    return [line for line in text.strip().splitlines()[-n:] if line.strip()]


def detect_oom_killed(snapshot: ClusterSnapshot) -> list[Finding]:
    acc = _Accumulator()
    for pod in snapshot.pods:
        for c in _all_containers(pod):
            if "OOMKilled" not in (c.reason, c.last_state_reason):
                continue
            acc.add(
                ("oom", pod.workload, c.name),
                pod.name,
                Finding(
                    detector="oom-killed",
                    severity=Severity.CRITICAL,
                    workload=pod.workload,
                    summary=f"Container '{c.name}' is being OOMKilled",
                    probable_cause=(
                        "The container exceeds its memory limit "
                        f"({c.memory_limit or 'no limit set: node memory pressure'}) "
                        "and is killed by the kernel."
                    ),
                    evidence=[
                        f"lastState.terminated.reason=OOMKilled, exitCode={c.last_exit_code}",
                        f"restartCount={c.restart_count}",
                        f"resources.limits.memory={c.memory_limit}",
                    ],
                    remediation=[
                        f"Check real usage: kubectl top pod -n {pod.namespace} --containers",
                        "Raise the memory limit (and request) of the container, or fix the "
                        "memory leak / unbounded cache in the application",
                        "For JVM or Node.js workloads, align heap size with the container limit",
                    ],
                ),
            )
    return acc.findings()


def detect_crash_loop(snapshot: ClusterSnapshot) -> list[Finding]:
    acc = _Accumulator()
    for pod in snapshot.pods:
        for c in _all_containers(pod):
            if c.reason != "CrashLoopBackOff" or c.last_state_reason == "OOMKilled":
                continue
            hint = EXIT_CODE_HINTS.get(c.last_exit_code or -1, "see the container logs")
            logs = _last_log_lines(snapshot, pod.name, c.name)
            acc.add(
                ("crashloop", pod.workload, c.name, str(c.last_exit_code)),
                pod.name,
                Finding(
                    detector="crash-loop",
                    severity=Severity.CRITICAL,
                    workload=pod.workload,
                    summary=f"Container '{c.name}' is in CrashLoopBackOff",
                    probable_cause=f"Exit code {c.last_exit_code}: {hint}.",
                    evidence=[
                        f"state.waiting.reason=CrashLoopBackOff, restartCount={c.restart_count}",
                        f"lastState.terminated.exitCode={c.last_exit_code} "
                        f"(reason={c.last_state_reason})",
                        *[f"log: {line}" for line in logs],
                    ],
                    remediation=[
                        f"kubectl logs -n {pod.namespace} {pod.name} -c {c.name} --previous",
                        "Fix the startup error shown in the logs (configuration, missing "
                        "dependency, wrong command) and roll out a new version",
                        *_rollback_hint(pod),
                    ],
                ),
            )
    return acc.findings()


def _classify_pull_error(messages: str) -> str:
    text = messages.lower()
    if "unauthorized" in text or "authentication required" in text or "denied" in text:
        return "The registry rejected the credentials: imagePullSecrets are missing or invalid."
    if "not found" in text or "manifest unknown" in text:
        return "The image or tag does not exist in the registry (typo or tag never pushed)."
    if "no such host" in text or "i/o timeout" in text or "connection refused" in text:
        return "The node cannot reach the registry (DNS, egress firewall or proxy issue)."
    if "toomanyrequests" in text or "rate limit" in text:
        return "The registry is rate limiting pulls."
    return "The kubelet cannot pull the image; see the event messages."


def detect_image_pull(snapshot: ClusterSnapshot) -> list[Finding]:
    acc = _Accumulator()
    for pod in snapshot.pods:
        for c in _all_containers(pod):
            if c.reason not in IMAGE_PULL_REASONS:
                continue
            messages = [
                e.message for e in _events_for(snapshot, pod.name) if e.reason in {"Failed"}
            ]
            cause = _classify_pull_error(" ".join([c.message or "", *messages]))
            acc.add(
                ("image-pull", pod.workload, c.name, c.image),
                pod.name,
                Finding(
                    detector="image-pull",
                    severity=Severity.CRITICAL,
                    workload=pod.workload,
                    summary=f"Image '{c.image}' cannot be pulled",
                    probable_cause=cause,
                    evidence=[
                        f"state.waiting.reason={c.reason}",
                        *[f"event: {m}" for m in messages[:2]],
                    ],
                    remediation=[
                        f"Verify the image exists: docker manifest inspect {c.image}",
                        "Fix the image name or tag in the workload spec",
                        "For private registries, check the imagePullSecrets of the pod or its "
                        "ServiceAccount",
                    ],
                ),
            )
    return acc.findings()


_MISSING_REF = re.compile(r'(configmap|secret) "([^"]+)" not found', re.IGNORECASE)


def detect_config_error(snapshot: ClusterSnapshot) -> list[Finding]:
    acc = _Accumulator()
    for pod in snapshot.pods:
        for c in _all_containers(pod):
            if c.reason not in CONFIG_ERROR_REASONS:
                continue
            message = c.message or ""
            match = _MISSING_REF.search(message)
            if match:
                kind, name = match.group(1).lower(), match.group(2)
                cause = f"The {kind} '{name}' referenced by the container does not exist."
                fix = (
                    f"Create the {kind} '{name}' in namespace '{pod.namespace}' "
                    "or fix the reference"
                )
            else:
                cause = "The container cannot be created from its spec; see the message."
                fix = "Fix the container spec (volume mounts, env references, security context)"
            acc.add(
                ("config", pod.workload, c.name, message),
                pod.name,
                Finding(
                    detector="config-error",
                    severity=Severity.CRITICAL,
                    workload=pod.workload,
                    summary=f"Container '{c.name}' cannot start: {c.reason}",
                    probable_cause=cause,
                    evidence=[f"state.waiting.reason={c.reason}", f"message: {message}"],
                    remediation=[
                        fix,
                        f"kubectl describe pod -n {pod.namespace} {pod.name}",
                    ],
                ),
            )
    return acc.findings()


def _classify_scheduling(message: str) -> tuple[str, str]:
    text = message.lower()
    if "insufficient memory" in text or "insufficient cpu" in text:
        return (
            "No node has enough allocatable CPU or memory for the pod's resource requests.",
            "Lower the resource requests, scale down other workloads, or add nodes "
            "(check the cluster autoscaler)",
        )
    if "untolerated taint" in text or "had taint" in text:
        return (
            "Every candidate node has a taint the pod does not tolerate.",
            "Add the matching toleration to the pod or schedule it on untainted nodes",
        )
    if "affinity" in text or "selector" in text:
        return (
            "No node matches the pod's nodeSelector or node affinity rules.",
            "Fix the nodeSelector / affinity labels or label a node accordingly",
        )
    if "persistentvolumeclaim" in text or "unbound" in text:
        return (
            "A PersistentVolumeClaim used by the pod is not bound.",
            "Check the PVC and its StorageClass: kubectl get pvc",
        )
    return (
        "The scheduler cannot place the pod on any node.",
        "Read the scheduler message and adjust requests, constraints or capacity",
    )


def detect_unschedulable(snapshot: ClusterSnapshot) -> list[Finding]:
    acc = _Accumulator()
    for pod in snapshot.pods:
        if pod.phase != "Pending":
            continue
        cond = next(
            (
                c
                for c in pod.conditions
                if c.type == "PodScheduled" and c.status == "False" and c.reason == "Unschedulable"
            ),
            None,
        )
        if cond is None:
            continue
        message = cond.message or ""
        cause, fix = _classify_scheduling(message)
        acc.add(
            ("unschedulable", pod.workload, cause),
            pod.name,
            Finding(
                detector="unschedulable",
                severity=Severity.CRITICAL,
                workload=pod.workload,
                summary="Pods are stuck in Pending: the scheduler cannot place them",
                probable_cause=cause,
                evidence=["condition PodScheduled=False (Unschedulable)", f"scheduler: {message}"],
                remediation=[fix, f"kubectl describe pod -n {pod.namespace} {pod.name}"],
            ),
        )
    return acc.findings()


def detect_probe_failures(snapshot: ClusterSnapshot) -> list[Finding]:
    acc = _Accumulator()
    pods = {p.name: p for p in snapshot.pods}
    for event in snapshot.events:
        if event.reason != "Unhealthy" or event.involved_kind != "Pod":
            continue
        pod = pods.get(event.involved_name)
        if pod is None:
            continue
        probe = "liveness" if "liveness" in event.message.lower() else "readiness"
        if probe == "readiness" and "startup" in event.message.lower():
            probe = "startup"
        acc.add(
            ("probe", pod.workload, probe),
            pod.name,
            Finding(
                detector="probe-failure",
                severity=Severity.WARNING,
                workload=pod.workload,
                summary=f"{probe.capitalize()} probe is failing",
                probable_cause=(
                    f"The {probe} probe does not succeed: wrong path/port, the application is "
                    "slow to start, or a dependency is unavailable."
                ),
                evidence=[f"event Unhealthy x{event.count}: {event.message}"],
                remediation=[
                    "Check that the probe path and port match the application",
                    "Increase initialDelaySeconds / failureThreshold or add a startupProbe for "
                    "slow-starting applications",
                ],
            ),
        )
    return acc.findings()


def detect_stalled_rollouts(snapshot: ClusterSnapshot) -> list[Finding]:
    findings = []
    for dep in snapshot.deployments:
        progressing = next((c for c in dep.conditions if c.type == "Progressing"), None)
        deadline = progressing is not None and progressing.reason == "ProgressDeadlineExceeded"
        if dep.replicas == 0 or (dep.available_replicas >= dep.replicas and not deadline):
            continue
        findings.append(
            Finding(
                detector="rollout",
                severity=Severity.CRITICAL if dep.available_replicas == 0 else Severity.WARNING,
                workload=f"Deployment/{dep.name}",
                summary=(
                    f"Deployment has {dep.available_replicas}/{dep.replicas} available replicas"
                    + (" and exceeded its progress deadline" if deadline else "")
                ),
                probable_cause=(
                    "New pods do not become available; see the pod-level findings for the "
                    "same workload."
                ),
                evidence=[
                    f"replicas={dep.replicas} ready={dep.ready_replicas} "
                    f"available={dep.available_replicas} updated={dep.updated_replicas}",
                    *([f"Progressing: {progressing.message}"] if deadline and progressing else []),
                ],
                remediation=[
                    f"kubectl rollout status -n {dep.namespace} deployment/{dep.name}",
                    f"kubectl rollout undo -n {dep.namespace} deployment/{dep.name}",
                ],
                resources=[f"Deployment/{dep.name}"],
            )
        )
    return findings


def detect_node_problems(snapshot: ClusterSnapshot) -> list[Finding]:
    findings = []
    for node in snapshot.nodes:
        problems = [
            c
            for c in node.conditions
            if (c.type == "Ready" and c.status != "True")
            or (c.type in {"MemoryPressure", "DiskPressure", "PIDPressure"} and c.status == "True")
        ]
        if not problems:
            continue
        findings.append(
            Finding(
                detector="node",
                severity=Severity.CRITICAL,
                workload=f"Node/{node.name}",
                summary="Node is "
                + ", ".join("NotReady" if c.type == "Ready" else c.type for c in problems),
                probable_cause="The node is unhealthy; pods on it may be evicted or not scheduled.",
                evidence=[f"{c.type}={c.status}: {c.message}" for c in problems],
                remediation=[
                    f"kubectl describe node {node.name}",
                    "Check kubelet and container runtime health, disk usage and memory pressure",
                ],
                resources=[f"Node/{node.name}"],
            )
        )
    return findings


DETECTORS: tuple[Detector, ...] = (
    detect_node_problems,
    detect_oom_killed,
    detect_crash_loop,
    detect_image_pull,
    detect_config_error,
    detect_unschedulable,
    detect_probe_failures,
    detect_stalled_rollouts,
)


def _correlate_rollouts(findings: list[Finding]) -> list[Finding]:
    """Fold a rollout finding into the pod-level findings that explain it.

    A Deployment without available replicas is a symptom: when a pod-level finding exists
    for the same workload, the rollout status becomes evidence of that finding instead of
    a separate entry.
    """
    explained = {f.workload for f in findings if f.detector not in {"rollout", "node"}}
    out = []
    for finding in findings:
        if finding.detector == "rollout" and finding.workload in explained:
            for cause in findings:
                if cause.workload == finding.workload and cause.detector != "rollout":
                    cause.evidence.append(f"rollout: {finding.summary}")
            continue
        out.append(finding)
    return out


def run_detectors(snapshot: ClusterSnapshot) -> list[Finding]:
    """Run every detector and return findings sorted by severity, workload and detector."""
    findings = _correlate_rollouts([f for detector in DETECTORS for f in detector(snapshot)])
    return sorted(findings, key=lambda f: (f.severity.rank, f.workload, f.detector, f.summary))
