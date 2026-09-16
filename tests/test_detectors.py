from __future__ import annotations

import pytest

from k8s_sre_agent.detectors import EXIT_CODE_HINTS, run_detectors
from k8s_sre_agent.models import (
    ClusterSnapshot,
    Condition,
    ContainerSnapshot,
    DeploymentSnapshot,
    NodeSnapshot,
    PodSnapshot,
    Severity,
)


def _by_detector(snapshot: ClusterSnapshot) -> dict[str, list]:
    out: dict[str, list] = {}
    for f in run_detectors(snapshot):
        out.setdefault(f.detector, []).append(f)
    return out


def test_healthy_namespace_has_no_findings(healthy_snapshot: ClusterSnapshot) -> None:
    assert run_detectors(healthy_snapshot) == []


def test_detects_every_incident_type(incident_snapshot: ClusterSnapshot) -> None:
    found = _by_detector(incident_snapshot)
    assert set(found) == {
        "crash-loop",
        "oom-killed",
        "image-pull",
        "config-error",
        "unschedulable",
        "probe-failure",
    }


def test_crash_loop_is_grouped_per_workload(incident_snapshot: ClusterSnapshot) -> None:
    (finding,) = _by_detector(incident_snapshot)["crash-loop"]
    assert finding.workload == "Deployment/checkout"
    assert finding.evidence[0] == "2 pods affected"
    assert len(finding.resources) == 2
    assert "Exit code 1" in finding.probable_cause
    assert any("PAYMENT_API_URL" in e for e in finding.evidence)
    assert any("rollout undo" in r for r in finding.remediation)


def test_oom_killed_reports_memory_limit(incident_snapshot: ClusterSnapshot) -> None:
    (finding,) = _by_detector(incident_snapshot)["oom-killed"]
    assert finding.workload == "StatefulSet/cache"
    assert "64Mi" in finding.probable_cause
    assert finding.severity is Severity.CRITICAL


def test_image_pull_classifies_missing_tag(incident_snapshot: ClusterSnapshot) -> None:
    (finding,) = _by_detector(incident_snapshot)["image-pull"]
    assert "does not exist" in finding.probable_cause


def test_config_error_names_missing_configmap(incident_snapshot: ClusterSnapshot) -> None:
    (finding,) = _by_detector(incident_snapshot)["config-error"]
    assert "configmap 'worker-config'" in finding.probable_cause


def test_unschedulable_detects_insufficient_resources(incident_snapshot: ClusterSnapshot) -> None:
    (finding,) = _by_detector(incident_snapshot)["unschedulable"]
    assert "enough allocatable CPU or memory" in finding.probable_cause


def test_probe_failure_is_a_warning(incident_snapshot: ClusterSnapshot) -> None:
    (finding,) = _by_detector(incident_snapshot)["probe-failure"]
    assert finding.severity is Severity.WARNING
    assert finding.summary.startswith("Readiness")


def test_rollout_is_folded_into_the_pod_level_cause(incident_snapshot: ClusterSnapshot) -> None:
    (finding,) = _by_detector(incident_snapshot)["crash-loop"]
    assert "rollout: Deployment has 0/2 available replicas" in finding.evidence[-1]


def test_unexplained_rollout_is_reported() -> None:
    snapshot = ClusterSnapshot(
        namespace="shop",
        deployments=[
            DeploymentSnapshot(
                name="web",
                namespace="shop",
                replicas=3,
                available_replicas=1,
                conditions=[
                    Condition(
                        type="Progressing",
                        status="False",
                        reason="ProgressDeadlineExceeded",
                        message="timed out",
                    )
                ],
            ),
            DeploymentSnapshot(name="ok", namespace="shop", replicas=1, available_replicas=1),
        ],
    )
    (finding,) = run_detectors(snapshot)
    assert finding.detector == "rollout"
    assert finding.workload == "Deployment/web"
    assert finding.severity is Severity.WARNING
    assert "progress deadline" in finding.summary


def test_findings_are_sorted_critical_first(incident_snapshot: ClusterSnapshot) -> None:
    ranks = [f.severity.rank for f in run_detectors(incident_snapshot)]
    assert ranks == sorted(ranks)


def test_node_not_ready_is_critical() -> None:
    snapshot = ClusterSnapshot(
        namespace="default",
        nodes=[
            NodeSnapshot(
                name="node-1",
                conditions=[
                    Condition(type="Ready", status="Unknown", message="Kubelet stopped posting"),
                    Condition(type="DiskPressure", status="True", message="disk full"),
                ],
            )
        ],
    )
    (finding,) = run_detectors(snapshot)
    assert finding.detector == "node"
    assert finding.summary == "Node is NotReady, DiskPressure"


def test_snapshot_round_trip(incident_snapshot: ClusterSnapshot) -> None:
    assert ClusterSnapshot.from_dict(incident_snapshot.to_dict()) == incident_snapshot


@pytest.mark.parametrize(
    "exit_code",
    [1, 2, 126, 127, 128, 134, 137, 139, 143, 255],
)
def test_exit_code_hint_is_used_in_crash_loop(exit_code: int) -> None:
    snapshot = ClusterSnapshot(
        namespace="shop",
        pods=[
            PodSnapshot(
                name="worker-0",
                namespace="shop",
                phase="Running",
                workload="Deployment/worker",
                containers=[
                    ContainerSnapshot(
                        name="app",
                        image="app:1",
                        reason="CrashLoopBackOff",
                        restart_count=3,
                        last_exit_code=exit_code,
                        last_state_reason="Error",
                    )
                ],
            )
        ],
    )
    findings = [f for f in run_detectors(snapshot) if f.detector == "crash-loop"]
    assert findings, f"expected crash-loop finding for exit code {exit_code}"
    hint = EXIT_CODE_HINTS[exit_code]
    assert f"Exit code {exit_code}: {hint}." == findings[0].probable_cause
