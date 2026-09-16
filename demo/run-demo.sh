#!/usr/bin/env bash
# Reproducible demo: create a kind cluster, break six workloads, diagnose them.
#
#   ./demo/run-demo.sh            # create the cluster (if needed), run the scenario, diagnose
#   ./demo/run-demo.sh --cleanup  # delete the demo cluster
#
# Requirements: docker, kind, kubectl, and k8s-sre-agent installed (pip install -e .).
# No LLM is needed: the deterministic report is produced with SRE_AGENT_LLM_PROVIDER=none.
set -euo pipefail

CLUSTER="${CLUSTER:-k8s-sre-agent-demo}"
NAMESPACE="sre-demo"
DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${OUT_DIR:-${DEMO_DIR}/out}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-240}"

if [[ "${1:-}" == "--cleanup" ]]; then
  kind delete cluster --name "${CLUSTER}"
  exit 0
fi

for bin in kind kubectl k8s-sre-agent; do
  command -v "${bin}" >/dev/null || { echo "error: ${bin} not found in PATH" >&2; exit 1; }
done

if ! kind get clusters | grep -qx "${CLUSTER}"; then
  echo "==> Creating kind cluster ${CLUSTER}"
  kind create cluster --name "${CLUSTER}" --wait 120s
fi
kubectl config use-context "kind-${CLUSTER}" >/dev/null

echo "==> Deploying the failure scenario"
kubectl apply -f "${DEMO_DIR}/scenarios/"

echo "==> Waiting for the failures to surface (up to ${TIMEOUT_SECONDS}s)"
mkdir -p "${OUT_DIR}"
deadline=$((SECONDS + TIMEOUT_SECONDS))
until SRE_AGENT_LLM_PROVIDER=none k8s-sre-agent diagnose -n "${NAMESPACE}" -o json \
        > "${OUT_DIR}/diagnosis.json" \
      && python "${DEMO_DIR}/check_findings.py" "${OUT_DIR}/diagnosis.json" \
        "${DEMO_DIR}/expected-findings.json" > "${OUT_DIR}/check.txt" 2>&1; do
  if (( SECONDS >= deadline )); then
    cat "${OUT_DIR}/check.txt"
    echo "error: expected findings not detected in time" >&2
    exit 1
  fi
  sleep 10
done
cat "${OUT_DIR}/check.txt"

echo "==> Diagnosis report"
SRE_AGENT_LLM_PROVIDER="${SRE_AGENT_LLM_PROVIDER:-none}" \
  k8s-sre-agent diagnose -n "${NAMESPACE}" | tee "${OUT_DIR}/diagnosis.md"

echo
echo "Report written to ${OUT_DIR}/diagnosis.md"
echo "Set SRE_AGENT_LLM_PROVIDER=anthropic|openai|ollama to add a root cause analysis."
echo "Delete the cluster with: $0 --cleanup"
