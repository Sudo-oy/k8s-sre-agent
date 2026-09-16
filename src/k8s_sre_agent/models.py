"""Plain data model of a cluster snapshot and of diagnosis findings.

The snapshot is deliberately decoupled from the Kubernetes client objects so that
detectors can be unit-tested and snapshots can be saved, shared and replayed offline.
"""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field
from typing import Any


class Severity(str, enum.Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"

    @property
    def rank(self) -> int:
        return {"critical": 0, "warning": 1, "info": 2}[self.value]


@dataclass
class Condition:
    type: str
    status: str
    reason: str | None = None
    message: str | None = None


@dataclass
class ContainerSnapshot:
    name: str
    image: str
    ready: bool = False
    restart_count: int = 0
    state: str = "unknown"  # waiting | running | terminated | unknown
    reason: str | None = None
    message: str | None = None
    exit_code: int | None = None
    last_state_reason: str | None = None
    last_exit_code: int | None = None
    memory_limit: str | None = None
    cpu_limit: str | None = None


@dataclass
class PodSnapshot:
    name: str
    namespace: str
    phase: str
    workload: str  # e.g. "Deployment/web", "StatefulSet/db" or "Pod/debug"
    node_name: str | None = None
    conditions: list[Condition] = field(default_factory=list)
    containers: list[ContainerSnapshot] = field(default_factory=list)
    init_containers: list[ContainerSnapshot] = field(default_factory=list)


@dataclass
class EventSnapshot:
    type: str
    reason: str
    message: str
    involved_kind: str
    involved_name: str
    count: int = 1


@dataclass
class DeploymentSnapshot:
    name: str
    namespace: str
    replicas: int = 0
    ready_replicas: int = 0
    available_replicas: int = 0
    updated_replicas: int = 0
    conditions: list[Condition] = field(default_factory=list)


@dataclass
class NodeSnapshot:
    name: str
    conditions: list[Condition] = field(default_factory=list)
    unschedulable: bool = False


@dataclass
class ClusterSnapshot:
    namespace: str
    pods: list[PodSnapshot] = field(default_factory=list)
    events: list[EventSnapshot] = field(default_factory=list)
    deployments: list[DeploymentSnapshot] = field(default_factory=list)
    nodes: list[NodeSnapshot] = field(default_factory=list)
    # "<pod>/<container>" -> last log lines (previous instance when the container restarted)
    logs: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClusterSnapshot:
        def conditions(items: list[dict[str, Any]] | None) -> list[Condition]:
            return [Condition(**c) for c in items or []]

        def containers(items: list[dict[str, Any]] | None) -> list[ContainerSnapshot]:
            return [ContainerSnapshot(**c) for c in items or []]

        return cls(
            namespace=data["namespace"],
            pods=[
                PodSnapshot(
                    **{
                        **p,
                        "conditions": conditions(p.get("conditions")),
                        "containers": containers(p.get("containers")),
                        "init_containers": containers(p.get("init_containers")),
                    }
                )
                for p in data.get("pods", [])
            ],
            events=[EventSnapshot(**e) for e in data.get("events", [])],
            deployments=[
                DeploymentSnapshot(**{**d, "conditions": conditions(d.get("conditions"))})
                for d in data.get("deployments", [])
            ],
            nodes=[
                NodeSnapshot(**{**n, "conditions": conditions(n.get("conditions"))})
                for n in data.get("nodes", [])
            ],
            logs=dict(data.get("logs", {})),
            warnings=list(data.get("warnings", [])),
        )


@dataclass
class Finding:
    detector: str
    severity: Severity
    workload: str
    summary: str
    probable_cause: str
    evidence: list[str] = field(default_factory=list)
    remediation: list[str] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["severity"] = self.severity.value
        return data
