# Contributing to k8s-sre-agent

Thanks for your interest in making Kubernetes incidents shorter! Bug reports, new detectors, documentation and demo scenarios are all welcome.

## Ground rules

- Follow the [Code of Conduct](CODE_OF_CONDUCT.md).
- Open an issue before starting a large change so we can agree on the approach.
- The agent must stay **read-only**: no pull request may add a write, patch or delete call to the Kubernetes API.
- Never commit credentials, kubeconfigs, real cluster snapshots or unredacted logs.
- Report security problems privately (see [SECURITY.md](SECURITY.md)).

## Development setup

Requirements: Python 3.10+, and for the end-to-end demo Docker, [kind](https://kind.sigs.k8s.io/) and `kubectl`.

```bash
git clone https://github.com/Sudo-oy/k8s-sre-agent.git && cd k8s-sre-agent
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

ruff format . && ruff check .
mypy
pytest
./demo/run-demo.sh          # optional end-to-end check on kind
```

No LLM account is needed: unit tests use fake providers and HTTP mock transports.

## Adding a detector

1. Write a function `detect_<name>(snapshot: ClusterSnapshot) -> list[Finding]` in `src/k8s_sre_agent/detectors.py` and register it in `DETECTORS`.
2. Keep it pure: read only the snapshot, never call the API or an LLM.
3. Group per workload with `_Accumulator` so replicas produce a single finding.
4. Give a concrete `probable_cause` and ordered `remediation` steps (safest first).
5. Add fixture data to `tests/fixtures/` and tests in `tests/test_detectors.py`.
6. If the failure can be reproduced on kind, add it to `demo/scenarios/` and `demo/expected-findings.json`.

## Commits and pull requests

- Branch from `main`: `git checkout -b feat/short-description`.
- Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `ci:`, `chore:`.
- Fill in the pull request template. CI (lint, types, tests on Python 3.10-3.13, build, security scan, kind e2e) must be green.

## Releases

Versions follow [Semantic Versioning](https://semver.org/). The version lives in `src/k8s_sre_agent/__init__.py` and changes are recorded in [CHANGELOG.md](CHANGELOG.md).
