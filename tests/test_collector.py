from __future__ import annotations

from kubernetes import client

from k8s_sre_agent.collector import decode_log, pod_to_snapshot, workload_of


def _pod(owner_kind: str | None, owner_name: str = "", labels: dict[str, str] | None = None):
    owners = (
        [
            client.V1OwnerReference(
                api_version="v1", kind=owner_kind, name=owner_name, uid="u", controller=True
            )
        ]
        if owner_kind
        else None
    )
    return client.V1Pod(
        metadata=client.V1ObjectMeta(
            name="web-5d9c8b7f6-x2x2x", namespace="shop", labels=labels, owner_references=owners
        ),
        spec=client.V1PodSpec(
            node_name="node-1",
            containers=[
                client.V1Container(
                    name="app",
                    image="nginx:1.27",
                    resources=client.V1ResourceRequirements(limits={"memory": "128Mi"}),
                )
            ],
        ),
        status=client.V1PodStatus(
            phase="Running",
            container_statuses=[
                client.V1ContainerStatus(
                    name="app",
                    image="nginx:1.27",
                    image_id="",
                    ready=False,
                    restart_count=3,
                    state=client.V1ContainerState(
                        waiting=client.V1ContainerStateWaiting(reason="CrashLoopBackOff")
                    ),
                    last_state=client.V1ContainerState(
                        terminated=client.V1ContainerStateTerminated(
                            exit_code=137, reason="OOMKilled"
                        )
                    ),
                )
            ],
        ),
    )


def test_workload_of_deployment_pod() -> None:
    pod = _pod("ReplicaSet", "web-5d9c8b7f6", {"pod-template-hash": "5d9c8b7f6"})
    assert workload_of(pod) == "Deployment/web"


def test_workload_of_statefulset_and_bare_pod() -> None:
    assert workload_of(_pod("StatefulSet", "db")) == "StatefulSet/db"
    assert workload_of(_pod(None)) == "Pod/web-5d9c8b7f6-x2x2x"


def test_pod_to_snapshot_maps_container_state() -> None:
    snap = pod_to_snapshot(_pod("ReplicaSet", "web-5d9c8b7f6", {"pod-template-hash": "5d9c8b7f6"}))
    (container,) = snap.containers
    assert snap.workload == "Deployment/web"
    assert container.state == "waiting"
    assert container.reason == "CrashLoopBackOff"
    assert container.last_state_reason == "OOMKilled"
    assert container.last_exit_code == 137
    assert container.memory_limit == "128Mi"
    assert container.restart_count == 3


def test_decode_log_handles_bytes_and_invalid_utf8() -> None:
    assert decode_log(b"line 1\nline 2\n") == "line 1\nline 2\n"
    assert decode_log(b"bad \xff byte") == "bad � byte"
    assert decode_log(None) == ""
    assert decode_log("already text") == "already text"
