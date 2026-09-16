"""Collect a read-only snapshot of a namespace from the Kubernetes API.

Only `get`, `list` and `pods/log` verbs are used. See `deploy/rbac.yaml` for the
minimal ClusterRole required when running inside a cluster.
"""

from __future__ import annotations

import logging
from typing import Any

from k8s_sre_agent.models import (
    ClusterSnapshot,
    Condition,
    ContainerSnapshot,
    DeploymentSnapshot,
    EventSnapshot,
    NodeSnapshot,
    PodSnapshot,
)

log = logging.getLogger(__name__)

HTTP_FORBIDDEN = 403


def _conditions(items: list[Any] | None) -> list[Condition]:
    return [
        Condition(type=c.type, status=c.status, reason=c.reason, message=c.message)
        for c in items or []
    ]


def workload_of(pod: Any) -> str:
    """Return the top-level workload owning a pod, e.g. ``Deployment/web``."""
    owners = pod.metadata.owner_references or []
    controller = next((o for o in owners if o.controller), owners[0] if owners else None)
    if controller is None:
        return f"Pod/{pod.metadata.name}"
    if controller.kind == "ReplicaSet":
        pod_hash = (pod.metadata.labels or {}).get("pod-template-hash")
        if pod_hash and controller.name.endswith(f"-{pod_hash}"):
            return f"Deployment/{controller.name[: -len(pod_hash) - 1]}"
    if controller.kind == "Job" and (pod.metadata.labels or {}).get("batch.kubernetes.io/cronjob"):
        return f"CronJob/{pod.metadata.labels['batch.kubernetes.io/cronjob']}"
    return f"{controller.kind}/{controller.name}"


def _container(spec: Any, status: Any | None) -> ContainerSnapshot:
    limits = (spec.resources.limits or {}) if spec.resources else {}
    snap = ContainerSnapshot(
        name=spec.name,
        image=spec.image,
        memory_limit=limits.get("memory"),
        cpu_limit=limits.get("cpu"),
    )
    if status is None:
        return snap
    snap.ready = bool(status.ready)
    snap.restart_count = status.restart_count or 0
    state = status.state
    if state is not None:
        if state.waiting is not None:
            snap.state, snap.reason, snap.message = (
                "waiting",
                state.waiting.reason,
                state.waiting.message,
            )
        elif state.terminated is not None:
            snap.state = "terminated"
            snap.reason = state.terminated.reason
            snap.message = state.terminated.message
            snap.exit_code = state.terminated.exit_code
        elif state.running is not None:
            snap.state = "running"
    last = status.last_state
    if last is not None and last.terminated is not None:
        snap.last_state_reason = last.terminated.reason
        snap.last_exit_code = last.terminated.exit_code
    return snap


def pod_to_snapshot(pod: Any) -> PodSnapshot:
    status = pod.status
    statuses = {s.name: s for s in (status.container_statuses or [])}
    init_statuses = {s.name: s for s in (status.init_container_statuses or [])}
    return PodSnapshot(
        name=pod.metadata.name,
        namespace=pod.metadata.namespace,
        phase=status.phase or "Unknown",
        workload=workload_of(pod),
        node_name=pod.spec.node_name,
        conditions=_conditions(status.conditions),
        containers=[_container(c, statuses.get(c.name)) for c in pod.spec.containers],
        init_containers=[
            _container(c, init_statuses.get(c.name)) for c in pod.spec.init_containers or []
        ],
    )


def event_to_snapshot(event: Any) -> EventSnapshot:
    return EventSnapshot(
        type=event.type or "Normal",
        reason=event.reason or "",
        message=(event.message or "").strip(),
        involved_kind=event.involved_object.kind or "",
        involved_name=event.involved_object.name or "",
        count=event.count or 1,
    )


def deployment_to_snapshot(dep: Any) -> DeploymentSnapshot:
    st = dep.status
    return DeploymentSnapshot(
        name=dep.metadata.name,
        namespace=dep.metadata.namespace,
        replicas=dep.spec.replicas if dep.spec.replicas is not None else 1,
        ready_replicas=st.ready_replicas or 0,
        available_replicas=st.available_replicas or 0,
        updated_replicas=st.updated_replicas or 0,
        conditions=_conditions(st.conditions),
    )


def node_to_snapshot(node: Any) -> NodeSnapshot:
    return NodeSnapshot(
        name=node.metadata.name,
        conditions=_conditions(node.status.conditions),
        unschedulable=bool(node.spec.unschedulable),
    )


def decode_log(data: bytes | str | None) -> str:
    """Decode a raw log body into text, tolerating invalid UTF-8."""
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


def load_kube_config(context: str | None = None) -> None:
    """Load in-cluster configuration when available, otherwise the local kubeconfig."""
    from kubernetes import config  # noqa: PLC0415 - keep import time low for --help

    try:
        config.load_incluster_config()
        log.debug("using in-cluster configuration")
    except config.ConfigException:
        config.load_kube_config(context=context)


def collect(
    namespace: str,
    *,
    label_selector: str | None = None,
    include_logs: bool = True,
    log_tail_lines: int = 40,
    context: str | None = None,
) -> ClusterSnapshot:
    """Collect pods, events, deployments, nodes and failing container logs."""
    from kubernetes import client  # noqa: PLC0415
    from kubernetes.client.exceptions import ApiException  # noqa: PLC0415

    load_kube_config(context)
    core = client.CoreV1Api()
    apps = client.AppsV1Api()
    snapshot = ClusterSnapshot(namespace=namespace)

    pods = core.list_namespaced_pod(namespace, label_selector=label_selector or "").items
    snapshot.pods = [pod_to_snapshot(p) for p in pods]
    snapshot.events = [
        event_to_snapshot(e)
        for e in core.list_namespaced_event(namespace).items
        if (e.type or "") == "Warning"
    ]
    snapshot.deployments = [
        deployment_to_snapshot(d)
        for d in apps.list_namespaced_deployment(
            namespace, label_selector=label_selector or ""
        ).items
    ]

    try:
        snapshot.nodes = [node_to_snapshot(n) for n in core.list_node().items]
    except ApiException as exc:
        if exc.status != HTTP_FORBIDDEN:
            raise
        snapshot.warnings.append("nodes: forbidden (node checks skipped)")

    if include_logs:
        for pod in snapshot.pods:
            for container in pod.containers:
                if container.ready and container.restart_count == 0:
                    continue
                try:
                    # Read the raw body: depending on the client version the preloaded
                    # content can be the repr of a bytes object instead of text.
                    response = core.read_namespaced_pod_log(
                        pod.name,
                        namespace,
                        container=container.name,
                        tail_lines=log_tail_lines,
                        previous=container.restart_count > 0,
                        _preload_content=False,
                    )
                    text = decode_log(response.data)
                except ApiException as exc:
                    log.debug("no logs for %s/%s: %s", pod.name, container.name, exc.reason)
                    continue
                if text:
                    snapshot.logs[f"{pod.name}/{container.name}"] = text
    return snapshot
