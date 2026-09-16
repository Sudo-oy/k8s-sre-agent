"""Assert that a JSON diagnosis contains every expected finding.

Usage: python demo/check_findings.py diagnosis.json demo/expected-findings.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    diagnosis = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    expected = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    actual = {(f["detector"], f["severity"], f["workload"]) for f in diagnosis["findings"]}
    missing = [e for e in expected if (e["detector"], e["severity"], e["workload"]) not in actual]
    for e in expected:
        status = "MISSING" if e in missing else "ok"
        print(f"  [{status:>7}] {e['severity']:<8} {e['detector']:<14} {e['workload']}")
    if missing:
        print(f"{len(missing)} expected finding(s) missing", file=sys.stderr)
        return 1
    print(f"all {len(expected)} expected findings detected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
