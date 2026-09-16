<!-- Reference output of ./demo/run-demo.sh on a single-node kind cluster (kind v0.23, Kubernetes v1.30).
     Pod name suffixes and restart counts differ on every run. -->

# Diagnosis for namespace `sre-demo`

**6 finding(s)**: 5 critical, 1 warning

## 1. [CRITICAL] Deployment/cache: Container 'cache' is being OOMKilled

- **Detector**: `oom-killed`
- **Probable cause**: The container exceeds its memory limit (24Mi) and is killed by the kernel.
- **Resources**: Pod/cache-668b79d595-qwmb4

**Evidence**

- 1 pod affected
- lastState.terminated.reason=OOMKilled, exitCode=137
- restartCount=5
- resources.limits.memory=24Mi
- rollout: Deployment has 0/1 available replicas

**Remediation**

1. Check real usage: kubectl top pod -n sre-demo --containers
2. Raise the memory limit (and request) of the container, or fix the memory leak / unbounded cache in the application
3. For JVM or Node.js workloads, align heap size with the container limit

## 2. [CRITICAL] Deployment/checkout: Container 'app' is in CrashLoopBackOff

- **Detector**: `crash-loop`
- **Probable cause**: Exit code 1: the application exited with a generic error; the logs usually show the exception.
- **Resources**: Pod/checkout-6458c4b797-48dhq

**Evidence**

- 1 pod affected
- state.waiting.reason=CrashLoopBackOff, restartCount=5
- lastState.terminated.exitCode=1 (reason=Error)
- log: starting checkout service
- log: FATAL: environment variable PAYMENT_API_URL is not set
- rollout: Deployment has 0/1 available replicas

**Remediation**

1. kubectl logs -n sre-demo checkout-6458c4b797-48dhq -c app --previous
2. Fix the startup error shown in the logs (configuration, missing dependency, wrong command) and roll out a new version
3. kubectl rollout undo -n sre-demo deployment/checkout (if a recent rollout introduced the problem)

## 3. [CRITICAL] Deployment/frontend: Image 'registry.k8s.io/pause:0.0.0-does-not-exist' cannot be pulled

- **Detector**: `image-pull`
- **Probable cause**: The image or tag does not exist in the registry (typo or tag never pushed).
- **Resources**: Pod/frontend-5f8559b668-lz47f

**Evidence**

- 1 pod affected
- state.waiting.reason=ErrImagePull
- event: Failed to pull image "registry.k8s.io/pause:0.0.0-does-not-exist": rpc error: code = NotFound desc = failed to pull and unpack image "registry.k8s.io/pause:0.0.0-does-not-exist": failed to resolve reference "registry.k8s.io/pause:0.0.0-does-not-exist": registry.k8s.io/pause:0.0.0-does-not-exist: not found
- event: Error: ErrImagePull
- rollout: Deployment has 0/1 available replicas

**Remediation**

1. Verify the image exists: docker manifest inspect registry.k8s.io/pause:0.0.0-does-not-exist
2. Fix the image name or tag in the workload spec
3. For private registries, check the imagePullSecrets of the pod or its ServiceAccount

## 4. [CRITICAL] Deployment/reports: Pods are stuck in Pending: the scheduler cannot place them

- **Detector**: `unschedulable`
- **Probable cause**: No node has enough allocatable CPU or memory for the pod's resource requests.
- **Resources**: Pod/reports-6c9b54cf65-86hz2

**Evidence**

- 1 pod affected
- condition PodScheduled=False (Unschedulable)
- scheduler: 0/1 nodes are available: 1 Insufficient memory. preemption: 0/1 nodes are available: 1 No preemption victims found for incoming pod.
- rollout: Deployment has 0/1 available replicas

**Remediation**

1. Lower the resource requests, scale down other workloads, or add nodes (check the cluster autoscaler)
2. kubectl describe pod -n sre-demo reports-6c9b54cf65-86hz2

## 5. [CRITICAL] Deployment/worker: Container 'worker' cannot start: CreateContainerConfigError

- **Detector**: `config-error`
- **Probable cause**: The configmap 'worker-config' referenced by the container does not exist.
- **Resources**: Pod/worker-8fcf984d7-f2dxd

**Evidence**

- 1 pod affected
- state.waiting.reason=CreateContainerConfigError
- message: configmap "worker-config" not found
- rollout: Deployment has 0/1 available replicas

**Remediation**

1. Create the configmap 'worker-config' in namespace 'sre-demo' or fix the reference
2. kubectl describe pod -n sre-demo worker-8fcf984d7-f2dxd

## 6. [WARNING] Deployment/api: Readiness probe is failing

- **Detector**: `probe-failure`
- **Probable cause**: The readiness probe does not succeed: wrong path/port, the application is slow to start, or a dependency is unavailable.
- **Resources**: Pod/api-9dc9cf68-rqkxr

**Evidence**

- 1 pod affected
- event Unhealthy x21: Readiness probe failed: HTTP probe failed with statuscode: 404
- rollout: Deployment has 0/1 available replicas

**Remediation**

1. Check that the probe path and port match the application
2. Increase initialDelaySeconds / failureThreshold or add a startupProbe for slow-starting applications

