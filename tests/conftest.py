from __future__ import annotations

import json
from pathlib import Path

import pytest

from k8s_sre_agent.models import ClusterSnapshot

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def incident_snapshot() -> ClusterSnapshot:
    data = json.loads((FIXTURES / "incident-snapshot.json").read_text(encoding="utf-8"))
    return ClusterSnapshot.from_dict(data)


@pytest.fixture
def healthy_snapshot() -> ClusterSnapshot:
    data = json.loads((FIXTURES / "healthy-snapshot.json").read_text(encoding="utf-8"))
    return ClusterSnapshot.from_dict(data)
